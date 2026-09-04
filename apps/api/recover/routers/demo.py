"""Demo controls.

Everything here drives a real code path. There is no endpoint that fakes a
result, and none of these bypass policy, signature verification or idempotency.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import engine, webhooks
from ..api_schemas import AIInfo, ProviderInfo, SystemConfig
from ..config import get_settings
from ..db import get_db
from ..models import RecoveryCase, WebhookEvent
from ..providers.simulator import SimulatedPaymentProvider, get_payment_provider
from ..seed import DEMO_MERCHANT_ID, seed

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.post("/seed")
def reseed(db: Session = Depends(get_db)) -> dict:
    """Reset to the deterministic demo state."""
    result = seed(db)
    db.commit()
    return result


@router.post("/reconcile")
def reconcile(db: Session = Depends(get_db)) -> dict:
    found = engine.detect_state_mismatches(db, DEMO_MERCHANT_ID)
    return {"mismatches_found": len(found), "cases": found}


class ReplayRequest(BaseModel):
    """Re-deliver a webhook that has already been processed."""

    event_id: str


@router.post("/replay-webhook")
def replay_webhook(payload: ReplayRequest, db: Session = Depends(get_db)) -> dict:
    """Re-send a stored webhook delivery byte-for-byte.

    This is the duplicate-webhook demonstration. The stored raw payload is
    re-signed and pushed through the real handler, so the second delivery hits
    genuine signature verification and genuine idempotency - it is not a UI
    trick that prints "duplicate".
    """
    provider = get_payment_provider()
    if not isinstance(provider, SimulatedPaymentProvider):
        raise HTTPException(
            status_code=409,
            detail=("Replay is only available on the simulator. With Razorpay Test Mode, "
                    "trigger a redelivery from the Razorpay dashboard instead."),
        )

    record = db.execute(
        select(WebhookEvent).where(WebhookEvent.event_id == payload.event_id)
    ).scalars().first()
    if record is None:
        raise HTTPException(status_code=404, detail=f"no webhook event {payload.event_id}")

    body = json.dumps(record.payload, separators=(",", ":")).encode()
    signature = provider._sign(body)  # same key the original delivery was signed with
    result = webhooks.handle_delivery(
        db, body=body, signature=signature,
        headers={"x-razorpay-event-id": record.event_id}, provider=provider,
    )
    return result


@router.get("/config", response_model=SystemConfig)
def config() -> SystemConfig:
    """What is actually wired up right now.

    Surfaced in the UI header so nobody watching a demo can mistake the
    simulator for Razorpay, or the deterministic fallback for the model.
    """
    settings = get_settings()
    provider = get_payment_provider()
    is_razorpay = provider.name == "razorpay"

    return SystemConfig(
        provider=ProviderInfo(
            name=provider.name,
            mode=provider.mode,
            is_razorpay=is_razorpay,
            key_id_present=bool(settings.razorpay_key_id),
            webhook_secret_configured=bool(settings.razorpay_webhook_secret),
            note=("Razorpay Test Mode. Orders, payment links and webhook signatures are "
                  "handled by Razorpay." if is_razorpay else
                  "Local simulator - no Razorpay credentials configured. Webhook signing, "
                  "verification and idempotency are real; the Razorpay API is not called."),
        ),
        ai=AIInfo(
            enabled=settings.ai_enabled,
            model=settings.anthropic_model if settings.ai_enabled else None,
            path="llm" if settings.ai_enabled else "fallback-heuristic",
            note=(f"Diagnosis runs on {settings.anthropic_model} with a read-only tool set."
                  if settings.ai_enabled else
                  "No ANTHROPIC_API_KEY set. Diagnosis uses the documented deterministic "
                  "fallback in recover/agent/fallback.py, which is a rule tree, not a model."),
        ),
        database=("postgresql" if settings.database_url.startswith("postgres") else "sqlite"),
        merchant_id=DEMO_MERCHANT_ID,
    )


@router.get("/scenarios")
def scenarios(db: Session = Depends(get_db)) -> list[dict]:
    """The seven demo scenarios, with the case each one lives on."""
    definitions = [
        ("1", "Payment retry succeeds", "case_demo_retry",
         "Rs 4,999 transient bank failure, loyal customer. Retry is within every limit."),
        ("2", "Retry fails again, system stops", "case_demo_retrylimit",
         "Two retries and two contacts already spent. Every outward action is refused."),
        ("3", "Above the automatic limit", "case_demo_approval",
         "Rs 12,499 exceeds the approval threshold. Held for a human."),
        ("4", "Customer cancelled", "case_demo_cancelled",
         "Order is cancelled. Recovery is blocked outright."),
        ("5", "Duplicate webhook", None,
         "Replay a delivered webhook; the second is detected and ignored."),
        ("6", "Payment captured, order stale", None,
         "Run reconciliation to find money already collected against a stale order."),
        ("7", "Forbidden action proposed", "case_demo_cancelled",
         "Probe the policy engine with RETRY_PAYMENT on a cancelled order."),
    ]
    out = []
    for number, title, case_id, detail in definitions:
        status = None
        if case_id:
            case = db.get(RecoveryCase, case_id)
            status = case.status if case else "missing (re-seed required)"
        out.append({"scenario": number, "title": title, "case_id": case_id,
                    "detail": detail, "case_status": status})
    return out
