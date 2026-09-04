"""Closed vocabularies shared by the domain, the policy engine and the AI schema.

Every one of these is a *closed set*. The LLM can only ever emit a member of
RecoveryAction; anything else is rejected before it reaches the policy engine.
"""
from __future__ import annotations

from enum import StrEnum


class OrderState(StrEnum):
    CREATED = "CREATED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_CAPTURED = "PAYMENT_CAPTURED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class PaymentStatus(StrEnum):
    CREATED = "created"
    PENDING = "pending"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    REFUNDED = "refunded"


class EventType(StrEnum):
    PAYMENT_FAILURE = "PAYMENT_FAILURE"
    CHECKOUT_ABANDONMENT = "CHECKOUT_ABANDONMENT"
    SUBSCRIPTION_PAYMENT_FAILURE = "SUBSCRIPTION_PAYMENT_FAILURE"
    OVERDUE_INVOICE = "OVERDUE_INVOICE"
    STATE_MISMATCH = "STATE_MISMATCH"
    DUPLICATE_PAYMENT = "DUPLICATE_PAYMENT"


class RecoveryAction(StrEnum):
    WAIT = "WAIT"
    RETRY_PAYMENT = "RETRY_PAYMENT"
    SEND_PAYMENT_LINK = "SEND_PAYMENT_LINK"
    SEND_REMINDER = "SEND_REMINDER"
    OFFER_ALLOWED_ALTERNATIVE = "OFFER_ALLOWED_ALTERNATIVE"
    ESCALATE_TO_MERCHANT = "ESCALATE_TO_MERCHANT"
    STOP = "STOP"


#: Actions that move money or contact a customer. These require the strictest gate.
MONEY_ACTIONS = frozenset({RecoveryAction.RETRY_PAYMENT, RecoveryAction.SEND_PAYMENT_LINK,
                           RecoveryAction.OFFER_ALLOWED_ALTERNATIVE})
CONTACT_ACTIONS = frozenset({RecoveryAction.SEND_PAYMENT_LINK, RecoveryAction.SEND_REMINDER,
                             RecoveryAction.OFFER_ALLOWED_ALTERNATIVE})
#: Actions with no external side effect at all.
INERT_ACTIONS = frozenset({RecoveryAction.WAIT, RecoveryAction.STOP,
                           RecoveryAction.ESCALATE_TO_MERCHANT})


class PolicyDecision(StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    BLOCK = "BLOCK"


class CaseStatus(StrEnum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    ACTION_PENDING = "ACTION_PENDING"
    AWAITING_CUSTOMER = "AWAITING_CUSTOMER"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    RECOVERED = "RECOVERED"
    STOPPED = "STOPPED"
    BLOCKED = "BLOCKED"
    ESCALATED = "ESCALATED"
    RECONCILIATION = "RECONCILIATION"


TERMINAL_CASE_STATUSES = frozenset(
    {CaseStatus.RECOVERED, CaseStatus.STOPPED, CaseStatus.BLOCKED}
)


class Recoverability(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FailureReason(StrEnum):
    """Normalised failure taxonomy mapped from provider error codes."""
    BANK_TRANSIENT = "BANK_TRANSIENT"          # issuer/gateway timeout, usually retryable
    NETWORK_ERROR = "NETWORK_ERROR"            # dropped connection mid-authorisation
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"  # retry only helps after time passes
    CARD_EXPIRED = "CARD_EXPIRED"              # retry is pointless; needs new instrument
    INCORRECT_CVV = "INCORRECT_CVV"            # customer input error
    DO_NOT_HONOUR = "DO_NOT_HONOUR"            # issuer declined, opaque
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"  # OTP/3DS not completed
    RISK_DECLINED = "RISK_DECLINED"            # never auto-retry
    MANDATE_INACTIVE = "MANDATE_INACTIVE"      # subscription mandate revoked
    CUSTOMER_CANCELLED = "CUSTOMER_CANCELLED"  # deliberate abandonment
    NONE = "NONE"


#: Failure classes where an automatic retry can never be justified, regardless
#: of what the model proposes. Enforced deterministically in policy.py.
NEVER_AUTO_RETRY = frozenset({
    FailureReason.RISK_DECLINED,
    FailureReason.CUSTOMER_CANCELLED,
    FailureReason.MANDATE_INACTIVE,
    FailureReason.CARD_EXPIRED,
})


class CustomerSegment(StrEnum):
    LOYAL = "LOYAL"
    REGULAR = "REGULAR"
    NEW = "NEW"
    AT_RISK = "AT_RISK"


class ActorType(StrEnum):
    SYSTEM = "system"
    AI_AGENT = "ai_agent"
    POLICY_ENGINE = "policy_engine"
    MERCHANT = "merchant"
    CUSTOMER = "customer"
    PROVIDER = "provider"
