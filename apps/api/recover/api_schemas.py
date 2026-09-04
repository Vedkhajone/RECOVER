"""Response and request models for the HTTP API.

Amounts cross the wire as integer paise *and* a preformatted rupee string. The
frontend never does currency arithmetic - it renders what the server computed.
That keeps one authoritative implementation of the money maths instead of two
that can drift.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from .enums import CustomerSegment, EventType, FailureReason, RecoveryAction


class Money(BaseModel):
    paise: int
    inr: float
    display: str

    @classmethod
    def of(cls, paise: int | None) -> "Money":
        paise = int(paise or 0)
        return cls(paise=paise, inr=round(paise / 100, 2), display=f"₹{paise / 100:,.2f}")


class ProviderInfo(BaseModel):
    name: str
    mode: str
    is_razorpay: bool
    key_id_present: bool
    webhook_secret_configured: bool
    note: str


class AIInfo(BaseModel):
    enabled: bool
    model: str | None
    path: str
    note: str


class SystemConfig(BaseModel):
    provider: ProviderInfo
    ai: AIInfo
    database: str
    merchant_id: str


class TrendPoint(BaseModel):
    label: str
    at_risk_paise: int
    recovered_paise: int


class DashboardMetrics(BaseModel):
    revenue_at_risk: Money
    recoverable_revenue: Money
    recovered_revenue: Money
    net_recovered_revenue: Money
    intervention_cost: Money
    recovery_rate: float
    active_cases: int
    successful_interventions: int
    stopped_cases: int
    escalated_cases: int
    blocked_cases: int
    awaiting_approval: int
    reconciliation_cases: int
    customer_contacts: int
    total_cases: int
    open_exceptions: int
    trend: list[TrendPoint]


class CaseSummary(BaseModel):
    id: str
    customer_name: str
    customer_id: str
    order_reference: str | None
    description: str | None
    amount: Money
    event_type: str
    status: str
    root_cause: str | None
    recoverability: str | None
    ai_recommended_action: str | None
    ai_path: str | None
    policy_decision: str | None
    policy_code: str | None
    executed_action: str | None
    recovered_amount: Money
    recovery_probability: float
    expected_value: Money
    retry_count: int
    contacts_sent: int
    opened_at: datetime
    closed_at: datetime | None


class TimelineEntry(BaseModel):
    actor: str
    title: str
    detail: str | None
    at: datetime


class AttemptEntry(BaseModel):
    action: str
    policy_decision: str
    succeeded: bool | None
    provider_reference: str | None
    cost: Money
    detail: str | None
    at: datetime


class PolicyMatrixEntry(BaseModel):
    action: str
    decision: str
    code: str
    reason: str
    expected_value: Money


class DecisionPanel(BaseModel):
    """The eight questions the case detail view has to answer."""

    what_happened: str
    why_it_happened: str | None
    what_ai_recommends: str | None
    why_ai_recommends_it: str | None
    what_policy_allows: list[PolicyMatrixEntry]
    what_action_was_taken: str | None
    result: str
    recovered_amount: Money
    ai_path: str | None
    ai_model: str | None
    ai_confidence: float | None
    ai_degraded: bool
    ai_tool_calls: list[str]
    ai_validation_error: str | None
    recovery_probability: float
    analytics: dict | None


class CaseDetail(CaseSummary):
    customer_email: str
    customer_segment: str
    customer_success_rate: float
    customer_successful_payments: int
    customer_failed_payments: int
    customer_risk_flagged: bool
    order_state: str | None
    failure_reason: str | None
    provider_error_code: str | None
    provider_error_description: str | None
    payment_link_url: str | None
    recovery_token: str | None
    decision_panel: DecisionPanel
    timeline: list[TimelineEntry]
    attempts: list[AttemptEntry]


class PolicyPayload(BaseModel):
    max_auto_retries: int = Field(ge=0, le=10)
    min_retry_interval_minutes: int = Field(ge=0, le=1440)
    max_auto_recovery_amount_paise: int = Field(ge=0)
    approval_threshold_paise: int = Field(ge=0)
    max_customer_contacts_24h: int = Field(ge=0, le=20)
    allow_payment_link_recovery: bool
    allow_alternative_method: bool
    case_expiry_hours: int = Field(ge=1, le=8760)
    min_expected_value_paise: int
    contact_cost_paise: int = Field(ge=0)
    retry_cost_paise: int = Field(ge=0)


class PolicyProbeRequest(BaseModel):
    """Ask the policy engine what it would say about an arbitrary action.

    Used by the UI's "what if the model proposed this?" control. It evaluates
    only - it never executes - so it is safe to expose.
    """

    action: str


class PolicyProbeResponse(BaseModel):
    action: str
    decision: str
    code: str
    reason: str
    expected_value: Money
    recovery_probability: float
    would_execute: bool


class AuditEntry(BaseModel):
    id: int
    at: datetime
    actor: str
    action: str
    case_id: str | None
    order_id: str | None
    payment_id: str | None
    tool: str | None
    input_summary: str | None
    result: str | None
    policy_decision: str | None
    reason: str | None
    status: str
    error: str | None


class RecoveryPageResponse(BaseModel):
    """What the customer-facing recovery page needs. Deliberately minimal -
    no case internals, no AI reasoning, no policy detail."""

    case_id: str
    order_reference: str
    description: str
    amount: Money
    customer_name: str
    status: str
    paid: bool
    reason_message: str
    provider: str
    provider_mode: str
    razorpay_key_id: str | None
    razorpay_order_id: str | None
    payment_link_url: str | None


class SimulatePaymentRequest(BaseModel):
    """Simulator-only. Lets the demo choose the outcome deterministically,
    the same way Razorpay Test Mode lets you pick success or failure."""

    succeed: bool = True
    failure_reason: str = "BANK_TRANSIENT"


class ExceptionEntry(BaseModel):
    id: int
    kind: str
    detail: str
    amount: Money
    case_id: str | None
    order_id: str | None
    resolved: bool
    at: datetime


class BatchRequest(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)
    #: Force the deterministic engine even when an API key is present. Used to
    #: keep the demo reproducible and to compare the two paths side by side.
    use_deterministic_engine: bool = False


class ActionProposal(BaseModel):
    action: RecoveryAction


# --------------------------------------------------------------------------
# Event ingestion
# --------------------------------------------------------------------------
class IngestCustomer(BaseModel):
    """The customer this event belongs to.

    Keyed on email within a merchant. Payment history is optional: supply it if
    your system knows it, because it is the strongest signal the recovery
    decision has. Omitted, the customer is treated as new - which is the
    conservative reading, not a flattering one.
    """

    name: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=3, max_length=200)
    contact: str | None = Field(default=None, max_length=32)
    segment: CustomerSegment | None = None
    successful_payments: int | None = Field(default=None, ge=0)
    failed_payments: int | None = Field(default=None, ge=0)
    lifetime_value_paise: int | None = Field(default=None, ge=0)
    risk_flagged: bool | None = None


class IngestEventRequest(BaseModel):
    """A revenue-risk event reported by a merchant system."""

    customer: IngestCustomer
    order_reference: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=200)

    #: Supply exactly one. `amount_inr` is a Decimal, never a float.
    amount_inr: Decimal | None = Field(default=None, gt=0)
    amount_paise: int | None = Field(default=None, gt=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)

    event_type: EventType = EventType.PAYMENT_FAILURE
    failure_reason: FailureReason = FailureReason.BANK_TRANSIENT
    payment_method: str = Field(default="card", max_length=24)
    provider_error_code: str | None = Field(default=None, max_length=64)
    provider_error_description: str | None = Field(default=None, max_length=400)

    #: Run the first detect/diagnose/decide/act cycle straight away.
    process_immediately: bool = True
    #: Force the deterministic engine even when an API key is configured.
    use_deterministic_engine: bool = False


class IngestEventResponse(BaseModel):
    created: bool
    case_id: str
    customer_id: str
    order_id: str
    amount: Money
    status: str
    recovery_url: str | None
    message: str
