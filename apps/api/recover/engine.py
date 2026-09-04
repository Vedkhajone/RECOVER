"""Recovery case orchestration.

This is the lifecycle described in the README:

    EVENT -> CASE -> CONTEXT -> AI DIAGNOSIS -> AI PROPOSAL
          -> DETERMINISTIC POLICY VALIDATION -> ALLOW / APPROVAL / BLOCK
          -> EXECUTION -> OBSERVE -> UPDATE -> STOP OR CONTINUE -> AUDIT

The ordering is the whole design. The AI's proposal is written to the case
*before* the policy engine runs, and the policy verdict is written next to it.
That means a case row always shows both what the model wanted and what the
system actually permitted, including the cases where those disagree.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit, executor
from .agent import llm
from .agent.schemas import AIDecisionEnvelope
from .context import RecoveryContext, context_from_case
from .enums import (
    ActorType,
    CaseStatus,
    EventType,
    FailureReason,
    OrderState,
    PolicyDecision,
    RecoveryAction,
    TERMINAL_CASE_STATUSES,
)
from .models import (
    Customer,
    MerchantPolicy,
    Order,
    Payment,
    RecoveryCase,
)
from .policy import evaluate, permitted_actions
from .scoring import score_case

log = logging.getLogger("recover.engine")


class CaseProcessingError(Exception):
    pass


def get_policy(db: Session, merchant_id: str) -> MerchantPolicy:
    policy = db.get(MerchantPolicy, merchant_id)
    if policy is None:
        raise CaseProcessingError(f"merchant {merchant_id} has no policy configured")
    return policy


# --------------------------------------------------------------------------
# Case creation
# --------------------------------------------------------------------------
def open_case(
    db: Session,
    *,
    merchant_id: str,
    customer_id: str,
    event_type: EventType,
    amount_paise: int,
    order_id: str | None = None,
    payment_id: str | None = None,
    detail: str | None = None,
    synthetic_ref: str | None = None,
) -> RecoveryCase:
    """Open a recovery case. Idempotent per (order, event_type) while one is live."""
    if order_id is not None:
        existing = db.execute(
            select(RecoveryCase).where(
                RecoveryCase.order_id == order_id,
                RecoveryCase.event_type == event_type,
                RecoveryCase.status.notin_([s.value for s in TERMINAL_CASE_STATUSES]),
            )
        ).scalars().first()
        if existing is not None:
            return existing

    case = RecoveryCase(
        merchant_id=merchant_id, customer_id=customer_id, order_id=order_id,
        payment_id=payment_id, event_type=event_type, amount_at_risk_paise=amount_paise,
        status=CaseStatus.OPEN, synthetic_ref=synthetic_ref,
    )
    db.add(case)
    db.flush()
    audit.record(db, actor=ActorType.SYSTEM, action="CASE_OPENED", case_id=case.id,
                 customer_id=customer_id, order_id=order_id, payment_id=payment_id,
                 input_summary={"event_type": event_type.value, "amount_paise": amount_paise})
    audit.timeline(db, case.id, actor=ActorType.SYSTEM,
                   title="RECOVER detected revenue at risk",
                   detail=detail or f"{event_type.value.replace('_', ' ').title()} "
                                    f"worth Rs {amount_paise / 100:,.2f}")
    return case


def open_case_for_failed_payment(db: Session, payment: Payment) -> RecoveryCase:
    order = db.get(Order, payment.order_id)
    if order is None:
        raise CaseProcessingError(f"payment {payment.id} has no order")
    return open_case(
        db, merchant_id=order.merchant_id, customer_id=order.customer_id,
        event_type=EventType.PAYMENT_FAILURE, amount_paise=order.amount_paise,
        order_id=order.id, payment_id=payment.id,
        detail=f"Payment of Rs {order.amount_paise / 100:,.2f} failed "
               f"({payment.failure_reason.value}).",
    )


# --------------------------------------------------------------------------
# Case processing
# --------------------------------------------------------------------------
def _record_diagnosis(db: Session, case: RecoveryCase, envelope: AIDecisionEnvelope,
                      analytics: dict) -> None:
    d = envelope.decision
    case.root_cause = d.root_cause
    case.recoverability = d.recoverability.value
    case.ai_recommended_action = d.recommended_action.value
    case.ai_reason = d.reason
    case.ai_confidence = d.confidence
    case.ai_path = envelope.path
    case.ai_model = envelope.model
    case.ai_evidence = {
        "evidence_used": d.evidence_used,
        "tool_calls": envelope.tool_calls,
        "latency_ms": envelope.latency_ms,
        "degraded": envelope.degraded,
        "validation_error": envelope.validation_error,
        "analytics": analytics,
    }
    case.recovery_probability = analytics["recovery_probability"]
    case.status = CaseStatus.INVESTIGATING

    label = "AI diagnosis" if envelope.path == "llm" else "Deterministic fallback diagnosis"
    audit.record(
        db, actor=ActorType.AI_AGENT, action="AI_DIAGNOSIS", case_id=case.id,
        tool=",".join(envelope.tool_calls) or None,
        input_summary={"path": envelope.path, "model": envelope.model},
        result=d.model_dump(mode="json"),
        reason=d.reason,
        status="degraded" if envelope.degraded else "ok",
        error=envelope.validation_error,
    )
    audit.timeline(
        db, case.id, actor=ActorType.AI_AGENT,
        title=f"{label}: {d.root_cause}",
        detail=f"Recommends {d.recommended_action.value} "
               f"(recoverability {d.recoverability.value}, confidence {d.confidence:.0%}). "
               f"{d.reason}",
    )
    if envelope.degraded:
        audit.timeline(
            db, case.id, actor=ActorType.SYSTEM,
            title="AI path degraded to deterministic fallback",
            detail=envelope.validation_error or "Model call did not return a valid decision.",
        )


def _execute(db: Session, case: RecoveryCase, ctx: RecoveryContext,
             action: RecoveryAction, *, merchant_approved: bool = False) -> dict:
    """Dispatch an ALLOWed action to the action tier."""
    if action == RecoveryAction.RETRY_PAYMENT:
        return executor.create_payment_retry_request(db, case, ctx,
                                                     merchant_approved=merchant_approved)
    if action == RecoveryAction.SEND_PAYMENT_LINK:
        return executor.create_payment_link(db, case, ctx,
                                            merchant_approved=merchant_approved)
    if action in (RecoveryAction.SEND_REMINDER, RecoveryAction.OFFER_ALLOWED_ALTERNATIVE):
        message = _compose_message(case, ctx, action)
        return executor.send_customer_recovery_message(
            db, case, ctx, action, message, merchant_approved=merchant_approved)
    if action == RecoveryAction.ESCALATE_TO_MERCHANT:
        reconciliation = case.event_type == EventType.STATE_MISMATCH
        # A reconciliation exception is read by whoever has to fix the ledger,
        # so it must describe the discrepancy. The agent's rationale for
        # escalating is already on the timeline and is not useful here.
        reason = (
            _describe_mismatch(case, ctx) if reconciliation
            else (case.ai_reason or "Escalated for merchant review.")
        )
        return executor.escalate_case(db, case, reason, reconciliation=reconciliation)
    if action == RecoveryAction.STOP:
        return executor.stop_case(db, case, case.ai_reason or "No worthwhile recovery action.")
    if action == RecoveryAction.WAIT:
        case.status = CaseStatus.OPEN
        case.last_action_at = datetime.now(UTC)
        audit.timeline(db, case.id, actor=ActorType.SYSTEM, title="Waiting before next attempt",
                       detail=f"Policy requires {ctx.policy.min_retry_interval_minutes} minutes "
                              f"between attempts.")
        return {"status": "waiting"}
    raise CaseProcessingError(f"no executor for {action}")


def _describe_mismatch(case: RecoveryCase, ctx: RecoveryContext) -> str:
    """What an operator needs to reconcile this by hand."""
    return (
        f"Payment on order {ctx.order_reference or ctx.order_id} is captured for "
        f"Rs {ctx.amount_paise / 100:,.2f}, but the order is still "
        f"{ctx.order_state.value if ctx.order_state else 'unknown'}. The money has been "
        f"collected and the ledger does not reflect it. RECOVER has not changed the "
        f"order state: confirm the payment in the Razorpay dashboard, then settle the "
        f"order manually."
    )


def _compose_message(case: RecoveryCase, ctx: RecoveryContext,
                     action: RecoveryAction) -> str:
    amount = f"Rs {ctx.amount_paise / 100:,.2f}"
    name = case.customer.name.split()[0] if case.customer.name else "there"
    if action == RecoveryAction.OFFER_ALLOWED_ALTERNATIVE:
        return (f"Hi {name}, the card saved for order {ctx.order_reference} could not be used "
                f"({ctx.failure_reason.value.replace('_', ' ').lower()}). You can complete the "
                f"{amount} payment with any other method here: {case.payment_link_url or ''}")
    if ctx.event_type == EventType.OVERDUE_INVOICE:
        return (f"Hi {name}, invoice {ctx.order_reference} for {amount} is "
                f"{ctx.invoice_days_overdue or 0} days overdue. You can settle it here.")
    return (f"Hi {name}, your {amount} payment for {ctx.order_description or 'your order'} "
            f"did not go through. You can finish it here whenever suits you.")


def _handle_block(db: Session, case: RecoveryCase, ctx: RecoveryContext, verdict) -> str:
    """A blocked proposal never becomes a no-op. It becomes an explicit outcome.

    Reconciliation cases go to a human. Everything else is stopped, which is the
    safe default: doing nothing is always permitted.
    """
    audit.timeline(
        db, case.id, actor=ActorType.POLICY_ENGINE,
        title=f"Policy blocked {verdict.action.value}",
        detail=f"{verdict.code}: {verdict.reason}",
    )
    if verdict.code == "REQUIRES_RECONCILIATION":
        executor.escalate_case(db, case, verdict.reason, reconciliation=True)
        return CaseStatus.RECONCILIATION.value
    executor.stop_case(
        db, case,
        f"Blocked by policy ({verdict.code}). No further recovery attempted. {verdict.reason}")
    case.status = CaseStatus.BLOCKED
    case.closed_at = datetime.now(UTC)
    return CaseStatus.BLOCKED.value


def process_case(db: Session, case_id: str, *, force_fallback: bool = False) -> dict:
    """Run one full detect->diagnose->decide->act cycle on a case."""
    case = db.get(RecoveryCase, case_id)
    if case is None:
        raise CaseProcessingError(f"case {case_id} not found")
    if CaseStatus(case.status) in TERMINAL_CASE_STATUSES:
        return {"case_id": case.id, "status": case.status, "skipped": "case is closed"}

    policy = get_policy(db, case.merchant_id)
    ctx = context_from_case(case, policy)
    analytics = score_case(ctx)

    # 1. AI diagnosis and proposal (or the labelled deterministic fallback).
    envelope = llm.diagnose(ctx, db=db, force_fallback=force_fallback)
    _record_diagnosis(db, case, envelope, analytics)
    proposed = envelope.decision.recommended_action

    # 2. Deterministic validation. Runs on whatever the model proposed, always.
    verdict = evaluate(ctx, proposed)
    case.policy_decision = verdict.decision.value
    case.policy_reason = verdict.reason
    case.policy_code = verdict.code
    case.expected_value_paise = verdict.expected_value_paise

    # Persist the verdict for every action, not just the proposed one. This is
    # what the decision panel renders under "what policy allows", and it is the
    # only way a refusal is visible when the engine quietly picked its second
    # choice instead.
    matrix = permitted_actions(ctx)
    case.ai_evidence = {
        **(case.ai_evidence or {}),
        "policy_matrix": {
            name: {"decision": v.decision.value, "code": v.code, "reason": v.reason,
                   "expected_value_paise": v.expected_value_paise}
            for name, v in matrix.items()
        },
    }
    refused = [f"{name} ({v.code})" for name, v in matrix.items()
               if v.decision == PolicyDecision.BLOCK]
    if refused:
        audit.timeline(
            db, case.id, actor=ActorType.POLICY_ENGINE,
            title=f"Policy refused {len(refused)} of 7 actions",
            detail="; ".join(refused))

    audit.record(db, actor=ActorType.POLICY_ENGINE, action="POLICY_EVALUATION",
                 case_id=case.id, policy_decision=verdict.decision.value,
                 input_summary={"proposed_action": proposed.value},
                 result={"code": verdict.code,
                         "expected_value_paise": verdict.expected_value_paise},
                 reason=verdict.reason)
    audit.timeline(db, case.id, actor=ActorType.POLICY_ENGINE,
                   title=f"Policy evaluated: {verdict.decision.value}",
                   detail=f"{verdict.code}: {verdict.reason}")

    # 3. Act on the verdict.
    result: dict
    if verdict.decision == PolicyDecision.BLOCK:
        status = _handle_block(db, case, ctx, verdict)
        case.executed_action = None
        result = {"executed": None, "status": status}
    elif verdict.decision == PolicyDecision.REQUIRE_APPROVAL:
        case.status = CaseStatus.AWAITING_APPROVAL
        case.executed_action = None
        audit.timeline(db, case.id, actor=ActorType.POLICY_ENGINE,
                       title="Merchant approval required",
                       detail=f"{verdict.reason} The action is held until a merchant approves it.")
        result = {"executed": None, "status": CaseStatus.AWAITING_APPROVAL.value}
    else:
        try:
            outcome = _execute(db, case, ctx, proposed)
            case.executed_action = proposed.value
            result = {"executed": proposed.value, "outcome": outcome, "status": case.status}
        except executor.PolicyViolation as exc:
            # The action tier disagreed with the orchestrator. That is a bug or
            # a race; either way the case stops rather than proceeding.
            status = _handle_block(db, case, ctx, verdict)
            audit.record(db, actor=ActorType.POLICY_ENGINE, action="ACTION_TIER_DISAGREEMENT",
                         case_id=case.id, status="error", error=str(exc))
            result = {"executed": None, "status": status, "error": str(exc)}
        except Exception as exc:
            log.exception("execution failed for case %s", case.id)
            case.status = CaseStatus.ESCALATED
            audit.record(db, actor=ActorType.SYSTEM, action="EXECUTION_FAILED",
                         case_id=case.id, status="error", error=f"{type(exc).__name__}: {exc}")
            audit.timeline(db, case.id, actor=ActorType.SYSTEM,
                           title="Recovery action failed",
                           detail=f"{type(exc).__name__}: {exc}. Case escalated for review; "
                                  f"no money moved.")
            result = {"executed": None, "status": case.status, "error": str(exc)}

    db.commit()
    return {
        "case_id": case.id,
        "ai_path": envelope.path,
        "ai_action": proposed.value,
        "policy_decision": verdict.decision.value,
        "policy_code": verdict.code,
        **result,
    }


def approve_case(db: Session, case_id: str, *, approver: str = "merchant") -> dict:
    """Merchant approves a held action. Satisfies REQUIRE_APPROVAL, nothing else."""
    case = db.get(RecoveryCase, case_id)
    if case is None:
        raise CaseProcessingError(f"case {case_id} not found")
    if CaseStatus(case.status) != CaseStatus.AWAITING_APPROVAL:
        raise CaseProcessingError(f"case {case_id} is not awaiting approval "
                                  f"(status {case.status})")
    if not case.ai_recommended_action:
        raise CaseProcessingError("case has no proposed action to approve")

    policy = get_policy(db, case.merchant_id)
    ctx = context_from_case(case, policy)
    action = RecoveryAction(case.ai_recommended_action)
    verdict = evaluate(ctx, action)

    if verdict.decision == PolicyDecision.BLOCK:
        # Circumstances changed while the case waited. Approval cannot revive it.
        status = _handle_block(db, case, ctx, verdict)
        db.commit()
        return {"case_id": case.id, "approved": False, "status": status,
                "reason": verdict.reason}

    audit.record(db, actor=ActorType.MERCHANT, action="APPROVAL_GRANTED", case_id=case.id,
                 input_summary={"action": action.value, "approver": approver},
                 policy_decision=verdict.decision.value)
    audit.timeline(db, case.id, actor=ActorType.MERCHANT, title="Merchant approved the action",
                   detail=f"{action.value} approved by {approver}.")
    outcome = _execute(db, case, ctx, action, merchant_approved=True)
    case.executed_action = action.value
    case.policy_decision = PolicyDecision.ALLOW.value
    case.policy_reason = f"Merchant approval granted for {verdict.code}."
    db.commit()
    return {"case_id": case.id, "approved": True, "executed": action.value,
            "status": case.status, "outcome": outcome}


def reject_case(db: Session, case_id: str, *, reason: str = "Merchant declined recovery.") -> dict:
    case = db.get(RecoveryCase, case_id)
    if case is None:
        raise CaseProcessingError(f"case {case_id} not found")
    audit.record(db, actor=ActorType.MERCHANT, action="APPROVAL_DECLINED", case_id=case.id,
                 reason=reason)
    executor.stop_case(db, case, reason)
    db.commit()
    return {"case_id": case.id, "status": case.status}


def run_recovery_batch(db: Session, *, merchant_id: str | None = None, limit: int = 50,
                       force_fallback: bool = False) -> dict:
    """Process every open case. This is what the dashboard's Run Recovery Batch does."""
    query = select(RecoveryCase.id).where(
        RecoveryCase.status.in_([CaseStatus.OPEN.value, CaseStatus.INVESTIGATING.value])
    )
    if merchant_id:
        query = query.where(RecoveryCase.merchant_id == merchant_id)
    case_ids = list(db.execute(query.order_by(RecoveryCase.opened_at).limit(limit)).scalars())

    results = []
    for case_id in case_ids:
        try:
            results.append(process_case(db, case_id, force_fallback=force_fallback))
        except Exception as exc:
            log.exception("batch: case %s failed", case_id)
            db.rollback()
            results.append({"case_id": case_id, "error": f"{type(exc).__name__}: {exc}"})

    summary = {
        "processed": len(results),
        "executed": sum(1 for r in results if r.get("executed")),
        "blocked": sum(1 for r in results if r.get("policy_decision") == "BLOCK"),
        "awaiting_approval": sum(1 for r in results
                                 if r.get("policy_decision") == "REQUIRE_APPROVAL"),
        "errors": sum(1 for r in results if r.get("error")),
        "results": results,
    }
    audit.record(db, actor=ActorType.SYSTEM, action="RECOVERY_BATCH_RUN",
                 input_summary={"merchant_id": merchant_id, "limit": limit},
                 result={k: v for k, v in summary.items() if k != "results"})
    db.commit()
    return summary


# --------------------------------------------------------------------------
# Reconciliation sweep
# --------------------------------------------------------------------------
def detect_state_mismatches(db: Session, merchant_id: str | None = None) -> list[dict]:
    """Find orders whose payment says paid but whose order state does not.

    This is the sixth demo scenario, and it is the one that catches real money:
    a captured payment against a stale order is revenue the merchant already has
    but has not recognised.
    """
    query = select(Order, Payment).join(Payment, Payment.order_id == Order.id).where(
        Payment.status == "captured",
        Order.state.notin_([OrderState.PAYMENT_CAPTURED.value, OrderState.COMPLETED.value,
                            OrderState.REFUNDED.value]),
    )
    if merchant_id:
        query = query.where(Order.merchant_id == merchant_id)

    found = []
    for order, payment in db.execute(query).all():
        case = open_case(
            db, merchant_id=order.merchant_id, customer_id=order.customer_id,
            event_type=EventType.STATE_MISMATCH, amount_paise=order.amount_paise,
            order_id=order.id, payment_id=payment.id,
            detail=f"Payment {payment.provider_payment_id or payment.id} is captured but order "
                   f"{order.reference} is still {order.state}.",
        )
        found.append({"case_id": case.id, "order_id": order.id, "payment_id": payment.id,
                      "amount_paise": order.amount_paise})
    db.commit()
    return found
