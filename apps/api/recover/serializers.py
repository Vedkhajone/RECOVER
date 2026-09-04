"""ORM -> API projection."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .api_schemas import (
    AttemptEntry,
    AuditEntry,
    CaseDetail,
    CaseSummary,
    DecisionPanel,
    ExceptionEntry,
    Money,
    PolicyMatrixEntry,
    TimelineEntry,
)
from .context import context_from_case
from .enums import CaseStatus, EventType, PolicyDecision
from .models import AuditLog, Order, Payment, ReconciliationException, RecoveryCase
from .policy import permitted_actions

EVENT_LABEL = {
    EventType.PAYMENT_FAILURE: "Payment failed",
    EventType.CHECKOUT_ABANDONMENT: "Checkout abandoned",
    EventType.SUBSCRIPTION_PAYMENT_FAILURE: "Subscription payment failed",
    EventType.OVERDUE_INVOICE: "Invoice overdue",
    EventType.STATE_MISMATCH: "Payment / order state mismatch",
    EventType.DUPLICATE_PAYMENT: "Duplicate payment event",
}

RESULT_LABEL = {
    CaseStatus.RECOVERED: "Recovered",
    CaseStatus.STOPPED: "Stopped - no further action",
    CaseStatus.BLOCKED: "Blocked by policy",
    CaseStatus.ESCALATED: "Escalated to merchant",
    CaseStatus.RECONCILIATION: "Held for reconciliation",
    CaseStatus.AWAITING_APPROVAL: "Awaiting merchant approval",
    CaseStatus.AWAITING_CUSTOMER: "Awaiting customer",
    CaseStatus.OPEN: "Open - not yet processed",
    CaseStatus.INVESTIGATING: "Investigating",
    CaseStatus.ACTION_PENDING: "Action pending",
}


def _latest_payment(db: Session, case: RecoveryCase) -> Payment | None:
    if not case.order_id:
        return None
    order = db.get(Order, case.order_id)
    if order is None or not order.payments:
        return None
    return max(order.payments, key=lambda p: (p.created_at, p.id))


def case_summary(db: Session, case: RecoveryCase) -> CaseSummary:
    order = db.get(Order, case.order_id) if case.order_id else None
    return CaseSummary(
        id=case.id,
        customer_name=case.customer.name,
        customer_id=case.customer_id,
        order_reference=order.reference if order else None,
        description=order.description if order else None,
        amount=Money.of(case.amount_at_risk_paise),
        event_type=case.event_type,
        status=case.status,
        root_cause=case.root_cause,
        recoverability=case.recoverability,
        ai_recommended_action=case.ai_recommended_action,
        ai_path=case.ai_path,
        policy_decision=case.policy_decision,
        policy_code=case.policy_code,
        executed_action=case.executed_action,
        recovered_amount=Money.of(case.recovered_amount_paise),
        recovery_probability=case.recovery_probability,
        expected_value=Money.of(case.expected_value_paise),
        retry_count=case.retry_count,
        contacts_sent=case.contacts_sent,
        opened_at=case.opened_at,
        closed_at=case.closed_at,
    )


def _what_happened(case: RecoveryCase, order: Order | None) -> str:
    label = EVENT_LABEL.get(EventType(case.event_type), str(case.event_type))
    amount = f"₹{case.amount_at_risk_paise / 100:,.2f}"
    reference = f" on {order.reference}" if order else ""
    return f"{label}{reference} - {amount} at risk."


def _policy_matrix(db: Session, case: RecoveryCase) -> list[PolicyMatrixEntry]:
    """Live policy verdicts for all seven actions.

    Recomputed on read rather than served from the stored snapshot, so that
    editing merchant policy immediately changes what this panel says. The
    stored snapshot on `ai_evidence` remains the record of what was true at
    decision time.
    """
    stored = (case.ai_evidence or {}).get("policy_matrix")
    try:
        from .engine import get_policy

        ctx = context_from_case(case, get_policy(db, case.merchant_id))
        verdicts = permitted_actions(ctx)
        return [
            PolicyMatrixEntry(action=name, decision=v.decision.value, code=v.code,
                              reason=v.reason, expected_value=Money.of(v.expected_value_paise))
            for name, v in verdicts.items()
        ]
    except Exception:
        if not stored:
            return []
        return [
            PolicyMatrixEntry(action=name, decision=v["decision"], code=v["code"],
                              reason=v["reason"],
                              expected_value=Money.of(v.get("expected_value_paise", 0)))
            for name, v in stored.items()
        ]


def case_detail(db: Session, case: RecoveryCase) -> CaseDetail:
    order = db.get(Order, case.order_id) if case.order_id else None
    payment = _latest_payment(db, case)
    evidence = case.ai_evidence or {}
    status = CaseStatus(case.status)

    if status == CaseStatus.RECOVERED:
        result = f"Recovered ₹{case.recovered_amount_paise / 100:,.2f}"
    else:
        result = RESULT_LABEL.get(status, str(case.status))

    panel = DecisionPanel(
        what_happened=_what_happened(case, order),
        why_it_happened=case.root_cause,
        what_ai_recommends=case.ai_recommended_action,
        why_ai_recommends_it=case.ai_reason,
        what_policy_allows=_policy_matrix(db, case),
        what_action_was_taken=case.executed_action,
        result=result,
        recovered_amount=Money.of(case.recovered_amount_paise),
        ai_path=case.ai_path,
        ai_model=case.ai_model,
        ai_confidence=case.ai_confidence,
        ai_degraded=bool(evidence.get("degraded")),
        ai_tool_calls=list(evidence.get("tool_calls") or []),
        ai_validation_error=evidence.get("validation_error"),
        recovery_probability=case.recovery_probability,
        analytics=evidence.get("analytics"),
    )

    base = case_summary(db, case).model_dump()
    return CaseDetail(
        **base,
        customer_email=case.customer.email,
        customer_segment=case.customer.segment,
        customer_success_rate=round(case.customer.previous_success_rate, 3),
        customer_successful_payments=case.customer.successful_payments,
        customer_failed_payments=case.customer.failed_payments,
        customer_risk_flagged=case.customer.risk_flagged,
        order_state=order.state if order else None,
        failure_reason=payment.failure_reason if payment else None,
        provider_error_code=payment.provider_error_code if payment else None,
        provider_error_description=payment.provider_error_description if payment else None,
        payment_link_url=case.payment_link_url,
        recovery_token=case.recovery_token,
        decision_panel=panel,
        timeline=[
            TimelineEntry(actor=e.actor, title=e.title, detail=e.detail, at=e.created_at)
            for e in case.events
        ],
        attempts=[
            AttemptEntry(action=a.action, policy_decision=a.policy_decision,
                         succeeded=a.succeeded, provider_reference=a.provider_reference,
                         cost=Money.of(a.cost_paise), detail=a.detail, at=a.created_at)
            for a in sorted(case.attempts, key=lambda a: a.created_at)
        ],
    )


def audit_entry(row: AuditLog) -> AuditEntry:
    return AuditEntry(
        id=row.id, at=row.created_at, actor=row.actor, action=row.action,
        case_id=row.case_id, order_id=row.order_id, payment_id=row.payment_id,
        tool=row.tool, input_summary=row.input_summary, result=row.result,
        policy_decision=row.policy_decision, reason=row.reason, status=row.status,
        error=row.error,
    )


def exception_entry(row: ReconciliationException) -> ExceptionEntry:
    return ExceptionEntry(
        id=row.id, kind=row.kind, detail=row.detail, amount=Money.of(row.amount_paise),
        case_id=row.case_id, order_id=row.order_id, resolved=row.resolved,
        at=row.created_at,
    )
