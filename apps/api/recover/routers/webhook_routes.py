"""Webhook endpoint.

Reads the raw body. Never `await request.json()` before verification - parsing
and re-serialising changes the bytes and the HMAC will not match.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import webhooks
from ..db import get_db
from ..models import WebhookEvent
from ..providers.simulator import get_payment_provider

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(default="", alias="X-Razorpay-Signature"),
    db: Session = Depends(get_db),
):
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    provider = get_payment_provider()

    try:
        result = webhooks.handle_delivery(db, body=body, signature=x_razorpay_signature,
                                          headers=headers, provider=provider)
    except webhooks.WebhookRejected as exc:
        # 400, not 500: the delivery was understood and refused.
        return JSONResponse(status_code=400, content={"status": "rejected", "reason": str(exc)})

    # A duplicate is acknowledged with 200 so the provider stops retrying.
    return JSONResponse(status_code=200, content=result)


@router.get("/events")
def list_events(limit: int = 50, db: Session = Depends(get_db)) -> list[dict]:
    """The idempotency ledger, for the audit UI."""
    rows = db.execute(
        select(WebhookEvent).order_by(WebhookEvent.first_received_at.desc()).limit(limit)
    ).scalars()
    return [
        {
            "event_id": row.event_id,
            "event": row.event_type,
            "signature_verified": row.signature_verified,
            "delivery_count": row.delivery_count,
            "duplicate": row.delivery_count > 1,
            "processed": row.processed,
            "effect": row.effect_summary,
            "first_received_at": row.first_received_at.isoformat(),
            "last_received_at": row.last_received_at.isoformat(),
        }
        for row in rows
    ]
