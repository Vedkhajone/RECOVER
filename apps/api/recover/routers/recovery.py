"""Customer-facing recovery flow.

Deliberately thin. A customer who followed a recovery link sees the order, the
amount, a plain explanation and a pay button. They never see case internals,
AI reasoning, policy codes or anything about other customers.

Authorisation is the single-use recovery token on the case. There is no
customer login, and the token grants access to exactly one case.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, webhooks
from ..api_schemas import Money, RecoveryPageResponse, SimulatePaymentRequest
from ..config import get_settings
from ..db import get_db
from ..enums import ActorType, CaseStatus, FailureReason, OrderState
from ..models import Order, RecoveryCase
from ..providers.simulator import SimulatedPaymentProvider, get_payment_provider

router = APIRouter(prefix="/api/recovery", tags=["customer-recovery"])

REASON_MESSAGE = {
    FailureReason.BANK_TRANSIENT: "Your bank could not be reached when you last tried.",
    FailureReason.NETWORK_ERROR: "The connection dropped before your payment finished.",
    FailureReason.INSUFFICIENT_FUNDS: "Your previous payment attempt was declined.",
    FailureReason.CARD_EXPIRED: "The card you used has expired.",
    FailureReason.INCORRECT_CVV: "The security code entered did not match.",
    FailureReason.DO_NOT_HONOUR: "Your bank declined the previous attempt.",
    FailureReason.AUTHENTICATION_FAILED: "The verification step was not completed.",
    FailureReason.MANDATE_INACTIVE: "The saved payment mandate is no longer active.",
}
DEFAULT_MESSAGE = "Your previous payment attempt was unsuccessful."


def _case_by_token(db: Session, token: str) -> RecoveryCase:
    case = db.execute(
        select(RecoveryCase).where(RecoveryCase.recovery_token == token)
    ).scalars().first()
    if case is None:
        raise HTTPException(status_code=404, detail="This recovery link is not valid.")
    return case


@router.get("/{token}", response_model=RecoveryPageResponse)
def recovery_page(token: str, db: Session = Depends(get_db)) -> RecoveryPageResponse:
    case = _case_by_token(db, token)
    order = db.get(Order, case.order_id) if case.order_id else None
    if order is None:
        raise HTTPException(status_code=404, detail="This recovery link is not valid.")

    provider = get_payment_provider()
    settings = get_settings()
    payment = max(order.payments, key=lambda p: (p.created_at, p.id)) if order.payments else None
    reason = FailureReason(payment.failure_reason) if payment else FailureReason.NONE
    paid = OrderState(order.state) in (OrderState.PAYMENT_CAPTURED, OrderState.COMPLETED)

    return RecoveryPageResponse(
        case_id=case.id,
        order_reference=order.reference,
        description=order.description,
        amount=Money.of(order.amount_paise),
        customer_name=case.customer.name,
        status=case.status,
        paid=paid,
        reason_message=REASON_MESSAGE.get(reason, DEFAULT_MESSAGE),
        provider=provider.name,
        provider_mode=provider.mode,
        razorpay_key_id=provider.public_key_id,
        razorpay_order_id=order.provider_order_id,
        payment_link_url=case.payment_link_url,
    )


@router.post("/{token}/simulate-payment")
def simulate_payment(token: str, payload: SimulatePaymentRequest,
                     db: Session = Depends(get_db)) -> dict:
    """Complete the payment against the local simulator.

    Only available when the simulator is the active provider. With Razorpay
    Test Mode configured, the customer pays through Razorpay Checkout instead
    and this endpoint refuses - there is no code path that fabricates a
    Razorpay payment.

    The simulated outcome is fed through the *real* webhook endpoint, signed,
    so signature verification and idempotency are genuinely exercised.
    """
    provider = get_payment_provider()
    if not isinstance(provider, SimulatedPaymentProvider):
        raise HTTPException(
            status_code=409,
            detail=("Razorpay Test Mode is active. Complete the payment through Razorpay "
                    "Checkout; simulated payments are disabled."),
        )

    case = _case_by_token(db, token)
    order = db.get(Order, case.order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="This recovery link is not valid.")
    if OrderState(order.state) in (OrderState.PAYMENT_CAPTURED, OrderState.COMPLETED):
        return {"already_paid": True, "status": order.state}

    try:
        reason = FailureReason(payload.failure_reason)
    except ValueError:
        raise HTTPException(status_code=422,
                            detail=f"unknown failure reason {payload.failure_reason!r}") from None

    from ..dataset.generator import PROVIDER_ERROR_CODES

    code, error_reason, description = PROVIDER_ERROR_CODES[reason]
    provider_payment = provider.record_payment(
        order_id=order.provider_order_id, amount_paise=order.amount_paise,
        succeeded=payload.succeed,
        failure=None if payload.succeed else (code or "", description or "", error_reason or ""),
    )
    body, signature, event_id = provider.build_webhook(
        event="payment.captured" if payload.succeed else "payment.failed",
        payment=provider_payment,
    )
    audit.record(db, actor=ActorType.CUSTOMER, action="CUSTOMER_COMPLETED_PAYMENT",
                 case_id=case.id, order_id=order.id,
                 input_summary={"succeeded": payload.succeed, "provider": provider.name})
    db.commit()

    result = webhooks.handle_delivery(
        db, body=body, signature=signature,
        headers={"x-razorpay-event-id": event_id}, provider=provider,
    )
    return {"succeeded": payload.succeed, "webhook": result,
            "replay": {"body": body.decode(), "signature": signature, "event_id": event_id}}


class CheckoutCallback(BaseModel):
    """Fields Razorpay Checkout hands back to the browser on success."""

    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@router.post("/{token}/verify")
def verify_checkout(token: str, payload: CheckoutCallback,
                    db: Session = Depends(get_db)) -> dict:
    """Verify a Razorpay Checkout callback signature.

    A valid signature proves the callback came from Razorpay. It is *not* what
    marks the money as recovered - the webhook does that. This endpoint only
    confirms to the customer that their payment was accepted, and records the
    fact for audit. Treating a browser callback as financial authority is how
    payment integrations get defrauded.
    """
    case = _case_by_token(db, token)
    provider = get_payment_provider()
    verified = provider.verify_checkout_signature(
        order_id=payload.razorpay_order_id,
        payment_id=payload.razorpay_payment_id,
        signature=payload.razorpay_signature,
    )
    audit.record(db, actor=ActorType.CUSTOMER, action="CHECKOUT_SIGNATURE_VERIFIED",
                 case_id=case.id, payment_id=payload.razorpay_payment_id,
                 result={"verified": verified},
                 status="ok" if verified else "rejected")
    if verified:
        audit.timeline(db, case.id, actor=ActorType.CUSTOMER,
                       title="Customer completed payment",
                       detail=f"Checkout signature verified for "
                              f"{payload.razorpay_payment_id}. Awaiting webhook confirmation "
                              f"before the amount is recorded as recovered.")
    db.commit()
    if not verified:
        raise HTTPException(status_code=400, detail="Payment signature could not be verified.")
    return {
        "verified": True,
        "case_status": case.status,
        "message": ("Payment accepted. Recovery is confirmed once Razorpay's webhook "
                    "is received and verified."),
    }
