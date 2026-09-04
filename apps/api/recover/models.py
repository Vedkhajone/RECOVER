"""SQLAlchemy domain model.

Money is stored exclusively as integer paise. No float ever touches an amount.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .enums import (
    ActorType,
    CaseStatus,
    CustomerSegment,
    EventType,
    FailureReason,
    OrderState,
    PaymentStatus,
    PolicyDecision,
    RecoveryAction,
)


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:14]}"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Merchant(Base):
    __tablename__ = "merchants"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _uid("mer"))
    name: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    policy: Mapped["MerchantPolicy"] = relationship(back_populates="merchant", uselist=False)


class MerchantPolicy(Base):
    """Merchant-configurable recovery policy.

    Every field here is load-bearing: policy.py reads these values on every
    single decision. There are no decorative settings in this table.
    """

    __tablename__ = "merchant_policies"

    merchant_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("merchants.id", ondelete="CASCADE"), primary_key=True
    )
    max_auto_retries: Mapped[int] = mapped_column(Integer, default=2)
    min_retry_interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    max_auto_recovery_amount_paise: Mapped[int] = mapped_column(Integer, default=500_000)
    approval_threshold_paise: Mapped[int] = mapped_column(Integer, default=1_000_000)
    max_customer_contacts_24h: Mapped[int] = mapped_column(Integer, default=2)
    allow_payment_link_recovery: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_alternative_method: Mapped[bool] = mapped_column(Boolean, default=True)
    case_expiry_hours: Mapped[int] = mapped_column(Integer, default=72)
    min_expected_value_paise: Mapped[int] = mapped_column(Integer, default=0)
    contact_cost_paise: Mapped[int] = mapped_column(Integer, default=50)
    retry_cost_paise: Mapped[int] = mapped_column(Integer, default=200)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    merchant: Mapped[Merchant] = relationship(back_populates="policy")


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _uid("cus"))
    merchant_id: Mapped[str] = mapped_column(String(40), ForeignKey("merchants.id"))
    name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(200))
    contact: Mapped[str] = mapped_column(String(32), default="")
    segment: Mapped[CustomerSegment] = mapped_column(String(20), default=CustomerSegment.NEW)
    successful_payments: Mapped[int] = mapped_column(Integer, default=0)
    failed_payments: Mapped[int] = mapped_column(Integer, default=0)
    lifetime_value_paise: Mapped[int] = mapped_column(Integer, default=0)
    risk_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def previous_success_rate(self) -> float:
        total = self.successful_payments + self.failed_payments
        return self.successful_payments / total if total else 0.0


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _uid("ord"))
    merchant_id: Mapped[str] = mapped_column(String(40), ForeignKey("merchants.id"))
    customer_id: Mapped[str] = mapped_column(String(40), ForeignKey("customers.id"))
    reference: Mapped[str] = mapped_column(String(40), unique=True)
    description: Mapped[str] = mapped_column(String(200))
    amount_paise: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    state: Mapped[OrderState] = mapped_column(String(24), default=OrderState.CREATED)
    provider_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    customer: Mapped[Customer] = relationship()
    payments: Mapped[list["Payment"]] = relationship(back_populates="order")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _uid("pay"))
    order_id: Mapped[str] = mapped_column(String(40), ForeignKey("orders.id"))
    provider_payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[PaymentStatus] = mapped_column(String(20), default=PaymentStatus.CREATED)
    method: Mapped[str] = mapped_column(String(24), default="card")
    failure_reason: Mapped[FailureReason] = mapped_column(String(32), default=FailureReason.NONE)
    provider_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_error_description: Mapped[str | None] = mapped_column(String(400), nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    is_recovery_attempt: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order: Mapped[Order] = relationship(back_populates="payments")


class RecoveryCase(Base):
    __tablename__ = "recovery_cases"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _uid("case"))
    merchant_id: Mapped[str] = mapped_column(String(40), ForeignKey("merchants.id"), index=True)
    customer_id: Mapped[str] = mapped_column(String(40), ForeignKey("customers.id"))
    order_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("orders.id"), nullable=True
    )
    payment_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("payments.id"), nullable=True
    )
    event_type: Mapped[EventType] = mapped_column(String(40))
    amount_at_risk_paise: Mapped[int] = mapped_column(Integer)
    status: Mapped[CaseStatus] = mapped_column(String(24), default=CaseStatus.OPEN, index=True)

    # --- AI diagnosis (advisory only; never authoritative) -------------------
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    recoverability: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ai_recommended_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_path: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    ai_evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- Deterministic verdict ----------------------------------------------
    policy_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    policy_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    executed_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expected_value_paise: Mapped[int] = mapped_column(Integer, default=0)
    recovery_probability: Mapped[float] = mapped_column(Float, default=0.0)

    # --- Outcome (only ever written from verified provider events) ----------
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    contacts_sent: Mapped[int] = mapped_column(Integer, default=0)
    recovered_amount_paise: Mapped[int] = mapped_column(Integer, default=0)
    intervention_cost_paise: Mapped[int] = mapped_column(Integer, default=0)

    recovery_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    payment_link_url: Mapped[str | None] = mapped_column(String(400), nullable=True)
    last_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synthetic_ref: Mapped[str | None] = mapped_column(String(40), nullable=True)

    customer: Mapped[Customer] = relationship()
    order: Mapped[Order | None] = relationship()
    events: Mapped[list["CaseEvent"]] = relationship(
        back_populates="case",
        order_by="CaseEvent.created_at, CaseEvent.id",
        cascade="all, delete-orphan",
    )
    attempts: Mapped[list["RecoveryAttempt"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )


class CaseEvent(Base):
    """Ordered, human-readable timeline. Written only after the fact it records."""

    __tablename__ = "case_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("recovery_cases.id", ondelete="CASCADE"), index=True
    )
    actor: Mapped[ActorType] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped[RecoveryCase] = relationship(back_populates="events")


class RecoveryAttempt(Base):
    __tablename__ = "recovery_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("recovery_cases.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[RecoveryAction] = mapped_column(String(32))
    policy_decision: Mapped[PolicyDecision] = mapped_column(String(20))
    succeeded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cost_paise: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped[RecoveryCase] = relationship(back_populates="attempts")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    actor: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(64), index=True)
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    customer_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    payment_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    tool: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class WebhookEvent(Base):
    """Idempotency ledger. A repeated event_id is recorded but never re-applied."""

    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("event_id", name="uq_webhook_event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(120), index=True)
    event_type: Mapped[str] = mapped_column(String(60))
    body_sha256: Mapped[str] = mapped_column(String(64))
    signature_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    delivery_count: Mapped[int] = mapped_column(Integer, default=1)
    first_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    effect_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReconciliationException(Base):
    """Cases the system could not confidently resolve. Surfaced, never hidden."""

    __tablename__ = "reconciliation_exceptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(60))
    detail: Mapped[str] = mapped_column(Text)
    amount_paise: Mapped[int] = mapped_column(Integer, default=0)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvaluationRun(Base):
    """Persisted output of scripts/run_evaluation.py. Never hand-written."""

    __tablename__ = "evaluation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    dataset_seed: Mapped[int] = mapped_column(Integer)
    dataset_size: Mapped[int] = mapped_column(Integer)
    dev_size: Mapped[int] = mapped_column(Integer)
    holdout_size: Mapped[int] = mapped_column(Integer)
    decision_engine: Mapped[str] = mapped_column(String(40))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
