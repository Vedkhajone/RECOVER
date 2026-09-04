"""Event ingestion — how real revenue-risk events enter RECOVER.

This is the front door of the pipeline. A merchant's system reports "this
payment failed" or "this checkout was abandoned", and RECOVER creates or
updates the customer, records the order and payment, opens a case, and
optionally runs the first recovery cycle immediately.

Everything downstream is unchanged: the case goes through the same detection,
the same agent, the same policy engine. There is no privileged path in here —
ingesting an event cannot make a recovery action happen that policy would
otherwise refuse.

Amounts are accepted in rupees as a *Decimal* and converted to integer paise
exactly once, here. Decimal rather than float, because 4999.99 is not
representable in binary floating point and the project's money invariant is
that no float ever touches an amount.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import engine, serializers
from ..api_schemas import CaseDetail, IngestEventRequest, IngestEventResponse, Money
from ..db import get_db
from ..enums import (
    CustomerSegment,
    EventType,
    FailureReason,
    OrderState,
    PaymentStatus,
)
from ..models import Customer, Merchant, Order, Payment
from ..seed import DEMO_MERCHANT_ID

router = APIRouter(prefix="/api/ingest", tags=["ingest"])

#: The order state an incoming event implies, before any recovery runs.
EVENT_ORDER_STATE = {
    EventType.PAYMENT_FAILURE: OrderState.PAYMENT_FAILED,
    EventType.SUBSCRIPTION_PAYMENT_FAILURE: OrderState.PAYMENT_FAILED,
    EventType.CHECKOUT_ABANDONMENT: OrderState.CREATED,
    EventType.OVERDUE_INVOICE: OrderState.PAYMENT_PENDING,
}

EVENT_PAYMENT_STATUS = {
    EventType.PAYMENT_FAILURE: PaymentStatus.FAILED,
    EventType.SUBSCRIPTION_PAYMENT_FAILURE: PaymentStatus.FAILED,
    EventType.CHECKOUT_ABANDONMENT: PaymentStatus.CREATED,
    EventType.OVERDUE_INVOICE: PaymentStatus.PENDING,
}


def _to_paise(amount_inr: Decimal | None, amount_paise: int | None) -> int:
    """Exactly one of the two must be given. Decimal in, integer paise out."""
    if (amount_inr is None) == (amount_paise is None):
        raise HTTPException(
            status_code=422,
            detail="Provide exactly one of amount_inr or amount_paise.",
        )
    if amount_paise is not None:
        value = amount_paise
    else:
        # Decimal arithmetic, then a single integer conversion. quantize first
        # so 4999.999 is rejected rather than silently truncated.
        scaled = (amount_inr * 100).normalize()
        if scaled != scaled.to_integral_value():
            raise HTTPException(
                status_code=422,
                detail=f"amount_inr {amount_inr} is finer than one paise.",
            )
        value = int(scaled)
    if value <= 0:
        raise HTTPException(status_code=422, detail="Amount must be greater than zero.")
    return value


def _upsert_customer(db: Session, merchant_id: str, payload) -> Customer:
    """Customers are keyed on email within a merchant.

    An existing customer keeps their payment history — that history is exactly
    what the recovery decision depends on, so silently resetting it on every
    ingest would quietly degrade every decision about them.
    """
    customer = db.execute(
        select(Customer).where(
            Customer.merchant_id == merchant_id,
            Customer.email == payload.email,
        )
    ).scalars().first()

    if customer is None:
        customer = Customer(
            merchant_id=merchant_id,
            name=payload.name,
            email=payload.email,
            contact=payload.contact or "",
            segment=payload.segment or CustomerSegment.NEW,
            successful_payments=payload.successful_payments or 0,
            failed_payments=payload.failed_payments or 0,
            lifetime_value_paise=payload.lifetime_value_paise or 0,
            risk_flagged=bool(payload.risk_flagged),
        )
        db.add(customer)
        db.flush()
        return customer

    # Update only what was explicitly supplied.
    customer.name = payload.name or customer.name
    if payload.contact is not None:
        customer.contact = payload.contact
    if payload.segment is not None:
        customer.segment = payload.segment
    if payload.successful_payments is not None:
        customer.successful_payments = payload.successful_payments
    if payload.failed_payments is not None:
        customer.failed_payments = payload.failed_payments
    if payload.lifetime_value_paise is not None:
        customer.lifetime_value_paise = payload.lifetime_value_paise
    if payload.risk_flagged is not None:
        customer.risk_flagged = payload.risk_flagged
    return customer


@router.post("/event", response_model=IngestEventResponse, status_code=201)
def ingest_event(
    payload: IngestEventRequest,
    merchant_id: str = DEMO_MERCHANT_ID,
    db: Session = Depends(get_db),
) -> IngestEventResponse:
    """Report a revenue-risk event and open a recovery case for it."""
    if db.get(Merchant, merchant_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Merchant {merchant_id} does not exist. Run scripts/seed_demo.py first.",
        )

    amount_paise = _to_paise(payload.amount_inr, payload.amount_paise)

    # Order references are unique. A repeat is a duplicate report, not a new
    # case, so return what already exists rather than creating a second one.
    existing = db.execute(
        select(Order).where(Order.reference == payload.order_reference)
    ).scalars().first()
    if existing is not None:
        case = engine.open_case(
            db, merchant_id=existing.merchant_id, customer_id=existing.customer_id,
            event_type=payload.event_type, amount_paise=existing.amount_paise,
            order_id=existing.id,
        )
        db.commit()
        return IngestEventResponse(
            created=False,
            case_id=case.id,
            customer_id=existing.customer_id,
            order_id=existing.id,
            amount=Money.of(existing.amount_paise),
            status=case.status,
            recovery_url=case.payment_link_url,
            message=(f"Order {payload.order_reference} already exists; returning its "
                     f"open case rather than creating a duplicate."),
        )

    customer = _upsert_customer(db, merchant_id, payload.customer)

    order = Order(
        merchant_id=merchant_id,
        customer_id=customer.id,
        reference=payload.order_reference,
        description=payload.description,
        amount_paise=amount_paise,
        currency=payload.currency,
        state=EVENT_ORDER_STATE.get(payload.event_type, OrderState.PAYMENT_FAILED),
    )
    db.add(order)
    db.flush()

    payment = None
    if payload.event_type != EventType.CHECKOUT_ABANDONMENT:
        payment = Payment(
            order_id=order.id,
            amount_paise=amount_paise,
            currency=payload.currency,
            status=EVENT_PAYMENT_STATUS.get(payload.event_type, PaymentStatus.FAILED),
            method=payload.payment_method,
            failure_reason=payload.failure_reason,
            provider_error_code=payload.provider_error_code,
            provider_error_description=payload.provider_error_description,
        )
        db.add(payment)
        db.flush()

    case = engine.open_case(
        db,
        merchant_id=merchant_id,
        customer_id=customer.id,
        event_type=payload.event_type,
        amount_paise=amount_paise,
        order_id=order.id,
        payment_id=payment.id if payment else None,
        detail=(f"{payload.event_type.value.replace('_', ' ').title()} reported for "
                f"{payload.order_reference} — Rs {amount_paise / 100:,.2f}."),
    )
    db.commit()

    message = "Case opened. Run the recovery cycle to diagnose and act on it."
    if payload.process_immediately:
        result = engine.process_case(db, case.id,
                                     force_fallback=payload.use_deterministic_engine)
        db.refresh(case)
        message = (f"Case opened and processed: proposed {result.get('ai_action')}, "
                   f"policy said {result.get('policy_decision')} "
                   f"({result.get('policy_code')}).")

    return IngestEventResponse(
        created=True,
        case_id=case.id,
        customer_id=customer.id,
        order_id=order.id,
        amount=Money.of(amount_paise),
        status=case.status,
        recovery_url=case.payment_link_url,
        message=message,
    )


@router.get("/event/{case_id}", response_model=CaseDetail)
def ingested_case(case_id: str, db: Session = Depends(get_db)) -> CaseDetail:
    """Convenience read-back so an integration can confirm what happened."""
    from ..models import RecoveryCase

    case = db.get(RecoveryCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"case {case_id} not found")
    return serializers.case_detail(db, case)
