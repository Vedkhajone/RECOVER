"""Deterministic fallback reasoner.

READ THIS BEFORE COMPARING IT TO THE AI PATH.

This is **not** the AI. It is a hand-written decision tree that produces an
AIDecision-shaped answer so the system keeps working when no Anthropic API key
is configured or the model call fails. Anything decided here is stamped
`path="fallback-heuristic"` on the case and rendered in the UI as
"Deterministic fallback", never as an AI decision.

It exists for three reasons:
  1. a clean clone with no API key still runs end to end;
  2. an API outage degrades the product instead of breaking it;
  3. it is the honest baseline the LLM is measured against in the evaluation -
     "the model beat a rule tree" is only a claim worth making if the rule tree
     is actually written down and actually run.
"""
from __future__ import annotations

from ..context import RecoveryContext
from ..enums import (
    EventType,
    FailureReason,
    PolicyDecision,
    Recoverability,
    RecoveryAction,
)
from ..policy import permitted_actions
from ..scoring import recovery_probability, tuning
from .schemas import AIDecision
from .tools import FAILURE_NOTES


def _root_cause(ctx: RecoveryContext) -> str:
    if ctx.event_type == EventType.CHECKOUT_ABANDONMENT:
        return "Customer began checkout and left before authorising payment."
    if ctx.event_type == EventType.OVERDUE_INVOICE:
        days = ctx.invoice_days_overdue or 0
        return f"Invoice is {days} days past its due date with no payment recorded."
    if ctx.event_type == EventType.STATE_MISMATCH:
        return "Payment and order records disagree; the order did not advance after capture."
    if ctx.event_type == EventType.DUPLICATE_PAYMENT:
        return "A second payment event arrived for an amount already collected."
    if ctx.event_type == EventType.SUBSCRIPTION_PAYMENT_FAILURE:
        base = "Scheduled subscription debit failed"
        if ctx.subscription_active is False:
            return base + " because the mandate is no longer active."
        return f"{base}: {FAILURE_NOTES.get(ctx.failure_reason, '')}".strip()
    return FAILURE_NOTES.get(ctx.failure_reason, "Payment failed for an unclassified reason.")


def _recoverability(p: float) -> Recoverability:
    if p >= 0.50:
        return Recoverability.HIGH
    if p >= 0.25:
        return Recoverability.MEDIUM
    return Recoverability.LOW


def _preference_order(ctx: RecoveryContext, p: float) -> list[RecoveryAction]:
    """Which actions to consider, best first, for this kind of case."""
    t = tuning()

    if ctx.event_type == EventType.STATE_MISMATCH:
        return [RecoveryAction.ESCALATE_TO_MERCHANT, RecoveryAction.STOP]
    if ctx.event_type == EventType.DUPLICATE_PAYMENT:
        return [RecoveryAction.STOP]

    # A dead instrument cannot be retried; the only route is a new one.
    if ctx.failure_reason in (FailureReason.CARD_EXPIRED,):
        return [RecoveryAction.OFFER_ALLOWED_ALTERNATIVE,
                RecoveryAction.SEND_PAYMENT_LINK,
                RecoveryAction.STOP]

    if ctx.event_type == EventType.CHECKOUT_ABANDONMENT:
        return [RecoveryAction.SEND_PAYMENT_LINK, RecoveryAction.SEND_REMINDER,
                RecoveryAction.STOP]

    if ctx.event_type == EventType.OVERDUE_INVOICE:
        return [RecoveryAction.SEND_REMINDER, RecoveryAction.SEND_PAYMENT_LINK,
                RecoveryAction.STOP]

    # Insufficient funds: a retry inside the same hour just fails again. Wait
    # if the interval has not elapsed, otherwise one retry is reasonable.
    if ctx.failure_reason == FailureReason.INSUFFICIENT_FUNDS:
        if (ctx.minutes_since_last_attempt is not None
                and ctx.minutes_since_last_attempt < ctx.policy.min_retry_interval_minutes):
            return [RecoveryAction.WAIT]
        return [RecoveryAction.RETRY_PAYMENT, RecoveryAction.SEND_PAYMENT_LINK,
                RecoveryAction.STOP]

    order: list[RecoveryAction] = []
    if p >= t["retry_probability_threshold"]:
        order.append(RecoveryAction.RETRY_PAYMENT)
    if p >= t["act_probability_threshold"]:
        order.extend([RecoveryAction.SEND_PAYMENT_LINK, RecoveryAction.SEND_REMINDER])
    order.append(RecoveryAction.STOP)
    return order


def decide(ctx: RecoveryContext) -> AIDecision:
    """Produce a decision without calling any model."""
    p = recovery_probability(ctx)
    verdicts = permitted_actions(ctx)
    chosen = RecoveryAction.STOP
    requires_approval = False
    note = "No permitted action has positive expected value."

    for candidate in _preference_order(ctx, p):
        verdict = verdicts[candidate.value]
        if verdict.decision == PolicyDecision.ALLOW:
            chosen = candidate
            note = verdict.reason
            break
        if verdict.decision == PolicyDecision.REQUIRE_APPROVAL:
            chosen = candidate
            requires_approval = True
            note = verdict.reason
            break
        note = verdict.reason  # remember why the best candidate was refused

    if chosen == RecoveryAction.STOP and ctx.event_type == EventType.STATE_MISMATCH:
        chosen = RecoveryAction.ESCALATE_TO_MERCHANT

    reason_bits = [
        f"Deterministic recovery probability {p:.0%}.",
        f"Selected {chosen.value}.",
        note,
    ]

    return AIDecision(
        case_id=ctx.case_id,
        root_cause=_root_cause(ctx),
        recoverability=_recoverability(p),
        recommended_action=chosen,
        reason=" ".join(b for b in reason_bits if b),
        # A rule tree has no calibrated uncertainty. Reporting a flat, modest
        # confidence is more honest than inventing a number that looks fitted.
        confidence=0.5,
        requires_approval=requires_approval,
        evidence_used=["failure_reason", "previous_success_rate", "retry_count",
                       "recovery_probability", "policy_limits"],
    )
