"""The single structured view of a revenue-risk case.

Both halves of the system consume this exact type:

  * production  - projected from the database (`context_from_case`)
  * evaluation  - projected from a synthetic record (`context_from_record`)

That is deliberate. The evaluation harness exercises the *same* policy engine,
the same scoring functions and the same agent that serve live traffic, so the
held-out numbers describe the shipped system rather than a parallel re-write.
"""
from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from .enums import (
    CustomerSegment,
    EventType,
    FailureReason,
    OrderState,
    PaymentStatus,
)


class PolicySnapshot(BaseModel):
    """Immutable copy of the merchant policy at decision time.

    Snapshotting matters for audit: a case decided last Tuesday must be
    explainable against the policy that was in force last Tuesday.
    """

    max_auto_retries: int = 2
    min_retry_interval_minutes: int = 30
    max_auto_recovery_amount_paise: int = 500_000
    approval_threshold_paise: int = 1_000_000
    max_customer_contacts_24h: int = 2
    allow_payment_link_recovery: bool = True
    allow_alternative_method: bool = True
    case_expiry_hours: int = 72
    min_expected_value_paise: int = 0
    contact_cost_paise: int = 50
    retry_cost_paise: int = 200


class CustomerContext(BaseModel):
    customer_id: str
    segment: CustomerSegment = CustomerSegment.NEW
    successful_payments: int = 0
    failed_payments: int = 0
    lifetime_value_paise: int = 0
    risk_flagged: bool = False

    @property
    def previous_success_rate(self) -> float:
        total = self.successful_payments + self.failed_payments
        return self.successful_payments / total if total else 0.0


class RecoveryContext(BaseModel):
    """Everything the system knows about one revenue-risk case."""

    case_id: str
    merchant_id: str
    event_type: EventType
    amount_paise: int = Field(ge=0)
    currency: str = "INR"

    order_id: str | None = None
    order_reference: str | None = None
    order_description: str | None = None
    order_state: OrderState | None = None

    payment_id: str | None = None
    payment_status: PaymentStatus | None = None
    failure_reason: FailureReason = FailureReason.NONE
    provider_error_code: str | None = None
    provider_error_description: str | None = None
    payment_method: str = "card"

    customer: CustomerContext

    retry_count: int = 0
    contacts_last_24h: int = 0
    minutes_since_event: int = 0
    minutes_since_last_attempt: int | None = None

    checkout_started: bool = False
    checkout_completed: bool = False
    subscription_active: bool | None = None
    invoice_days_overdue: int | None = None
    order_already_paid: bool = False
    order_refunded: bool = False
    order_cancelled: bool = False
    duplicate_of_payment_id: str | None = None

    risk_flags: list[str] = Field(default_factory=list)
    policy: PolicySnapshot = Field(default_factory=PolicySnapshot)

    @property
    def amount_rupees(self) -> float:
        return self.amount_paise / 100

    def evidence(self) -> dict:
        """Compact, structured evidence block. This is what the model sees -
        never a raw database row, never free-form narrative."""
        return {
            "event_type": self.event_type.value,
            "amount_inr": round(self.amount_rupees, 2),
            "order_state": self.order_state.value if self.order_state else None,
            "payment_status": self.payment_status.value if self.payment_status else None,
            "failure_reason": self.failure_reason.value,
            "provider_error_code": self.provider_error_code,
            "provider_error_description": self.provider_error_description,
            "payment_method": self.payment_method,
            "customer": {
                "segment": self.customer.segment.value,
                "successful_payments": self.customer.successful_payments,
                "failed_payments": self.customer.failed_payments,
                "previous_success_rate": round(self.customer.previous_success_rate, 3),
                "risk_flagged": self.customer.risk_flagged,
            },
            "retry_count": self.retry_count,
            "contacts_last_24h": self.contacts_last_24h,
            "minutes_since_event": self.minutes_since_event,
            "minutes_since_last_attempt": self.minutes_since_last_attempt,
            "checkout_started": self.checkout_started,
            "checkout_completed": self.checkout_completed,
            "subscription_active": self.subscription_active,
            "invoice_days_overdue": self.invoice_days_overdue,
            "order_already_paid": self.order_already_paid,
            "order_refunded": self.order_refunded,
            "order_cancelled": self.order_cancelled,
            "duplicate_of_payment_id": self.duplicate_of_payment_id,
            "risk_flags": self.risk_flags,
        }


def policy_snapshot_from_model(policy) -> PolicySnapshot:
    """Project a MerchantPolicy ORM row into an immutable snapshot."""
    return PolicySnapshot(
        max_auto_retries=policy.max_auto_retries,
        min_retry_interval_minutes=policy.min_retry_interval_minutes,
        max_auto_recovery_amount_paise=policy.max_auto_recovery_amount_paise,
        approval_threshold_paise=policy.approval_threshold_paise,
        max_customer_contacts_24h=policy.max_customer_contacts_24h,
        allow_payment_link_recovery=policy.allow_payment_link_recovery,
        allow_alternative_method=policy.allow_alternative_method,
        case_expiry_hours=policy.case_expiry_hours,
        min_expected_value_paise=policy.min_expected_value_paise,
        contact_cost_paise=policy.contact_cost_paise,
        retry_cost_paise=policy.retry_cost_paise,
    )


def _minutes_between(later: datetime, earlier: datetime | None) -> int | None:
    if earlier is None:
        return None
    if earlier.tzinfo is None:
        earlier = earlier.replace(tzinfo=UTC)
    return max(0, int((later - earlier).total_seconds() // 60))


def context_from_case(case, policy, *, now: datetime | None = None) -> RecoveryContext:
    """Project a live RecoveryCase (plus its order/payment/customer) into context."""
    now = now or datetime.now(UTC)
    # SQLAlchemy returns these columns as plain strings, so coerce back into the
    # closed vocabularies before anything downstream relies on enum behaviour.
    event_type = EventType(case.event_type)
    order = case.order
    customer = case.customer
    payment = None
    if order is not None and order.payments:
        payment = max(order.payments, key=lambda p: (p.created_at, p.id))

    return RecoveryContext(
        case_id=case.id,
        merchant_id=case.merchant_id,
        event_type=event_type,
        amount_paise=case.amount_at_risk_paise,
        currency=order.currency if order else "INR",
        order_id=order.id if order else None,
        order_reference=order.reference if order else None,
        order_description=order.description if order else None,
        order_state=OrderState(order.state) if order else None,
        payment_id=payment.id if payment else None,
        payment_status=PaymentStatus(payment.status) if payment else None,
        failure_reason=(FailureReason(payment.failure_reason) if payment
                        else FailureReason.NONE),
        provider_error_code=payment.provider_error_code if payment else None,
        provider_error_description=payment.provider_error_description if payment else None,
        payment_method=payment.method if payment else "card",
        customer=CustomerContext(
            customer_id=customer.id,
            segment=CustomerSegment(customer.segment),
            successful_payments=customer.successful_payments,
            failed_payments=customer.failed_payments,
            lifetime_value_paise=customer.lifetime_value_paise,
            risk_flagged=customer.risk_flagged,
        ),
        retry_count=case.retry_count,
        contacts_last_24h=case.contacts_sent,
        minutes_since_event=_minutes_between(now, case.opened_at) or 0,
        minutes_since_last_attempt=_minutes_between(now, case.last_action_at),
        checkout_started=event_type == EventType.CHECKOUT_ABANDONMENT,
        checkout_completed=False,
        order_already_paid=bool(order and order.state in (OrderState.PAYMENT_CAPTURED,
                                                          OrderState.COMPLETED)),
        order_refunded=bool(order and order.state == OrderState.REFUNDED),
        order_cancelled=bool(order and order.state == OrderState.CANCELLED),
        subscription_active=(
            False if (event_type == EventType.SUBSCRIPTION_PAYMENT_FAILURE and payment
                      and payment.failure_reason == FailureReason.MANDATE_INACTIVE)
            else (True if event_type == EventType.SUBSCRIPTION_PAYMENT_FAILURE else None)
        ),
        risk_flags=["customer_risk_flagged"] if customer.risk_flagged else [],
        policy=policy_snapshot_from_model(policy),
    )
