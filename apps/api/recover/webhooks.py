"""Webhook ingestion.

Three properties matter here and each is enforced separately:

  1. AUTHENTICITY  - the raw body is HMAC-SHA256 verified against the webhook
     secret before it is parsed. An unverified delivery is recorded and rejected;
     it never reaches the business logic. Bytes are verified exactly as received,
     never re-serialised, because re-serialising changes the signature.

  2. IDEMPOTENCY   - a delivery is keyed on Razorpay's event id. A repeat is
     counted and acknowledged with 200, and the business effect runs exactly
     once. Razorpay retries; a system that double-applies retries will
     double-count revenue.

  3. AUTHORITY     - this is the *only* place a payment is allowed to become
     successful. Not the browser callback, not the AI, not an operator button.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import audit, state_machine
from .enums import (
    ActorType,
    CaseStatus,
    FailureReason,
    OrderState,
    PaymentStatus,
)
from .models import Order, Payment, RecoveryCase, ReconciliationException, WebhookEvent
from .providers.razorpay_provider import normalise_failure

log = logging.getLogger("recover.webhooks")

HANDLED_EVENTS = {"payment.captured", "payment.authorized", "payment.failed", "order.paid"}


class WebhookRejected(Exception):
    """Signature verification failed. Never processed, always recorded."""


def _event_id(headers: dict, body: bytes, payload: dict) -> str:
    """Razorpay sends X-Razorpay-Event-Id. Fall back to a body digest so a
    delivery without the header is still deduplicated rather than replayed."""
    header_id = headers.get("x-razorpay-event-id") or headers.get("X-Razorpay-Event-Id")
    if header_id:
        return header_id
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _payment_entity(payload: dict) -> dict | None:
    return (payload.get("payload", {}).get("payment", {}) or {}).get("entity")


def _order_entity(payload: dict) -> dict | None:
    return (payload.get("payload", {}).get("order", {}) or {}).get("entity")


def _find_order(db: Session, entity: dict) -> Order | None:
    provider_order_id = entity.get("order_id")
    if provider_order_id:
        order = db.execute(
            select(Order).where(Order.provider_order_id == provider_order_id)
        ).scalars().first()
        if order is not None:
            return order
    # Notes carry our own case id, which survives even if the order id does not.
    case_id = (entity.get("notes") or {}).get("recover_case_id")
    if case_id:
        case = db.get(RecoveryCase, case_id)
        if case is not None and case.order_id:
            return db.get(Order, case.order_id)
    return None


def _case_for_order(db: Session, order_id: str) -> RecoveryCase | None:
    return db.execute(
        select(RecoveryCase)
        .where(RecoveryCase.order_id == order_id)
        .order_by(RecoveryCase.opened_at.desc())
    ).scalars().first()


# --------------------------------------------------------------------------
# Effects. Each one is written to be safe to *attempt* twice even though
# idempotency should mean it never is - defence in depth around money.
# --------------------------------------------------------------------------
def _apply_capture(db: Session, entity: dict) -> str:
    provider_payment_id = entity.get("id")
    amount = int(entity.get("amount") or 0)

    existing = db.execute(
        select(Payment).where(Payment.provider_payment_id == provider_payment_id)
    ).scalars().first()
    if existing is not None and existing.status == PaymentStatus.CAPTURED:
        return f"payment {provider_payment_id} already captured; no change"

    order = _find_order(db, entity)
    if order is None:
        db.add(ReconciliationException(
            kind="ORPHAN_PAYMENT",
            detail=f"Captured payment {provider_payment_id} for Rs {amount / 100:,.2f} does not "
                   f"match any known order. Held for manual reconciliation.",
            amount_paise=amount,
        ))
        return "no matching order; raised ORPHAN_PAYMENT exception"

    payment = existing or Payment(
        order_id=order.id, amount_paise=amount, currency=entity.get("currency", "INR"),
        method=entity.get("method") or "card", is_recovery_attempt=True,
    )
    payment.provider_payment_id = provider_payment_id
    payment.status = PaymentStatus.CAPTURED
    payment.failure_reason = FailureReason.NONE
    db.add(payment)
    db.flush()

    try:
        state_machine.transition(order, OrderState.PAYMENT_CAPTURED)
        state_machine.transition(order, OrderState.COMPLETED)
    except state_machine.InvalidStateTransition as exc:
        # Money arrived for an order that cannot accept it (cancelled, refunded).
        # We never force the state; a human decides.
        db.add(ReconciliationException(
            order_id=order.id, kind="CAPTURE_ON_INVALID_STATE", detail=str(exc),
            amount_paise=amount,
        ))
        return f"capture recorded but order state left untouched: {exc}"

    order.customer.successful_payments += 1
    order.customer.lifetime_value_paise += amount

    case = _case_for_order(db, order.id)
    if case is None:
        return f"order {order.reference} marked paid (no recovery case)"

    # This assignment is the single source of the "recovered revenue" figure on
    # the dashboard. It happens here and nowhere else.
    case.recovered_amount_paise = amount
    case.status = CaseStatus.RECOVERED
    case.closed_at = datetime.now(UTC)
    case.payment_id = payment.id
    for attempt in case.attempts:
        if attempt.succeeded is None:
            attempt.succeeded = True
    audit.timeline(db, case.id, actor=ActorType.PROVIDER, title="Payment verified by webhook",
                   detail=f"Provider payment {provider_payment_id} captured.")
    audit.timeline(db, case.id, actor=ActorType.SYSTEM,
                   title=f"Rs {amount / 100:,.2f} recovered",
                   detail=f"Order {order.reference} completed after "
                          f"{case.retry_count} recovery attempt(s).")
    return f"case {case.id} recovered Rs {amount / 100:,.2f}"


def _apply_failure(db: Session, entity: dict) -> str:
    order = _find_order(db, entity)
    reason = normalise_failure(entity.get("error_code"), entity.get("error_reason"),
                               entity.get("error_description"))
    if order is None:
        return "payment failed for an unknown order; ignored"

    payment = Payment(
        order_id=order.id, provider_payment_id=entity.get("id"),
        amount_paise=int(entity.get("amount") or order.amount_paise),
        currency=entity.get("currency", "INR"), status=PaymentStatus.FAILED,
        method=entity.get("method") or "card", failure_reason=FailureReason(reason),
        provider_error_code=entity.get("error_code"),
        provider_error_description=entity.get("error_description"),
        is_recovery_attempt=True,
    )
    db.add(payment)
    order.customer.failed_payments += 1
    try:
        state_machine.transition(order, OrderState.PAYMENT_FAILED)
    except state_machine.InvalidStateTransition as exc:
        return f"failure recorded; order state unchanged ({exc})"

    case = _case_for_order(db, order.id)
    if case is None:
        return f"order {order.reference} marked failed (no recovery case)"

    for attempt in case.attempts:
        if attempt.succeeded is None:
            attempt.succeeded = False
    payment.attempt_number = case.retry_count + 1
    # Reopen so the next batch run re-evaluates. The retry counter has already
    # been incremented, so policy will stop the case once the limit is reached.
    case.status = CaseStatus.OPEN
    case.payment_id = payment.id
    audit.timeline(db, case.id, actor=ActorType.PROVIDER,
                   title=f"Recovery attempt {case.retry_count} failed",
                   detail=f"{reason}: {entity.get('error_description') or 'no description'}")
    return f"case {case.id} recovery attempt failed ({reason})"


def _apply(db: Session, event_type: str, payload: dict) -> str:
    if event_type in ("payment.captured", "order.paid"):
        entity = _payment_entity(payload)
        if entity is None and event_type == "order.paid":
            return "order.paid without a payment entity; ignored"
        return _apply_capture(db, entity)
    if event_type == "payment.authorized":
        return "payment authorized; awaiting capture"
    if event_type == "payment.failed":
        entity = _payment_entity(payload)
        return _apply_failure(db, entity) if entity else "no payment entity"
    return f"event {event_type} not handled"


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def handle_delivery(db: Session, *, body: bytes, signature: str, headers: dict,
                    provider) -> dict:
    """Verify, deduplicate, then apply. In that order, always."""
    verified = False
    try:
        verified = provider.verify_webhook_signature(body=body, signature=signature)
    except Exception as exc:
        audit.record(db, actor=ActorType.PROVIDER, action="WEBHOOK_VERIFY_ERROR",
                     status="error", error=str(exc))
        db.commit()
        raise WebhookRejected(str(exc)) from exc

    if not verified:
        audit.record(db, actor=ActorType.PROVIDER, action="WEBHOOK_REJECTED",
                     status="rejected", error="signature verification failed",
                     input_summary={"bytes": len(body)})
        db.commit()
        raise WebhookRejected("signature verification failed")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        audit.record(db, actor=ActorType.PROVIDER, action="WEBHOOK_MALFORMED",
                     status="error", error=str(exc))
        db.commit()
        raise WebhookRejected(f"malformed JSON: {exc}") from exc

    event_type = payload.get("event", "unknown")
    event_id = _event_id(headers, body, payload)
    digest = hashlib.sha256(body).hexdigest()

    existing = db.execute(
        select(WebhookEvent).where(WebhookEvent.event_id == event_id)
    ).scalars().first()
    if existing is not None:
        existing.delivery_count += 1
        existing.last_received_at = datetime.now(UTC)
        audit.record(db, actor=ActorType.PROVIDER, action="WEBHOOK_DUPLICATE",
                     input_summary={"event_id": event_id, "event": event_type},
                     result={"delivery_count": existing.delivery_count},
                     reason="Duplicate event detected - safely ignored.",
                     status="duplicate")
        db.commit()
        return {
            "status": "duplicate",
            "duplicate": True,
            "event_id": event_id,
            "event": event_type,
            "delivery_count": existing.delivery_count,
            "effect": existing.effect_summary,
            "message": "Duplicate event detected - safely ignored.",
        }

    record = WebhookEvent(event_id=event_id, event_type=event_type, body_sha256=digest,
                          signature_verified=True, payload=payload, processed=False)
    db.add(record)
    try:
        # Flush now so the unique constraint settles a concurrent double delivery
        # here rather than after the side effect has been applied.
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"status": "duplicate", "duplicate": True, "event_id": event_id,
                "event": event_type, "message": "Duplicate event detected - safely ignored."}

    effect = _apply(db, event_type, payload)
    record.processed = True
    record.effect_summary = effect
    audit.record(db, actor=ActorType.PROVIDER, action="WEBHOOK_PROCESSED",
                 input_summary={"event_id": event_id, "event": event_type},
                 result={"effect": effect})
    db.commit()
    return {"status": "processed", "duplicate": False, "event_id": event_id,
            "event": event_type, "delivery_count": 1, "effect": effect}
