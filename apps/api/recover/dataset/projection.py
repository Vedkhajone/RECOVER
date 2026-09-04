"""Synthetic record -> RecoveryContext.

Only *observed* fields cross this boundary. The `ground_truth_*` keys and the
latent `true_event_type` / `stale_snapshot` markers stay behind - if they leaked
into the context the system would be evaluated on information it will never
have in production, and every metric would be inflated.
"""
from __future__ import annotations

from ..context import CustomerContext, PolicySnapshot, RecoveryContext
from ..enums import (
    CustomerSegment,
    EventType,
    FailureReason,
    OrderState,
    PaymentStatus,
)

#: Keys the system is allowed to see. Enforced, not just documented.
OBSERVABLE_KEYS = frozenset({
    "transaction_id", "customer_id", "merchant_id", "order_id", "order_reference",
    "description", "amount_paise", "currency", "event_type", "order_state",
    "payment_status", "failure_reason", "provider_error_code", "provider_error_reason",
    "provider_error_description", "payment_method", "customer_segment",
    "previous_success_count", "previous_failure_count", "previous_success_rate",
    "lifetime_value_paise", "risk_flags", "risk_flagged", "retry_count",
    "contacts_last_24h", "minutes_since_event", "minutes_since_last_attempt",
    "checkout_started", "checkout_completed", "subscription_active",
    "invoice_days_overdue", "invoice_status", "order_already_paid", "order_cancelled",
    "order_refunded", "duplicate_of_payment_id", "created_at", "observed_at",
})


def context_from_record(record: dict, policy: PolicySnapshot | None = None) -> RecoveryContext:
    r = {k: v for k, v in record.items() if k in OBSERVABLE_KEYS}
    return RecoveryContext(
        case_id=r["transaction_id"],
        merchant_id=r["merchant_id"],
        event_type=EventType(r["event_type"]),
        amount_paise=r["amount_paise"],
        currency=r["currency"],
        order_id=r["order_id"],
        order_reference=r["order_reference"],
        order_description=r["description"],
        order_state=OrderState(r["order_state"]),
        payment_id=None,
        payment_status=PaymentStatus(r["payment_status"]),
        failure_reason=FailureReason(r["failure_reason"]),
        provider_error_code=r["provider_error_code"],
        provider_error_description=r["provider_error_description"],
        payment_method=r["payment_method"],
        customer=CustomerContext(
            customer_id=r["customer_id"],
            segment=CustomerSegment(r["customer_segment"]),
            successful_payments=r["previous_success_count"],
            failed_payments=r["previous_failure_count"],
            lifetime_value_paise=r["lifetime_value_paise"],
            risk_flagged=r["risk_flagged"],
        ),
        retry_count=r["retry_count"],
        contacts_last_24h=r["contacts_last_24h"],
        minutes_since_event=r["minutes_since_event"],
        minutes_since_last_attempt=r["minutes_since_last_attempt"],
        checkout_started=r["checkout_started"],
        checkout_completed=r["checkout_completed"],
        subscription_active=r["subscription_active"],
        invoice_days_overdue=r["invoice_days_overdue"],
        order_already_paid=r["order_already_paid"],
        order_refunded=r["order_refunded"],
        order_cancelled=r["order_cancelled"],
        duplicate_of_payment_id=r["duplicate_of_payment_id"],
        risk_flags=list(r["risk_flags"]),
        policy=policy or PolicySnapshot(),
    )
