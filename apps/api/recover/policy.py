"""The merchant policy engine.

This module is the hard boundary of the system. Every recovery action, however
it was proposed - by the AI agent, by a merchant clicking a button, by the
batch runner - passes through `evaluate()` before anything happens. There is no
second path to execution.

Design rules, in force everywhere below:

  * Pure functions. No I/O, no clock reads (the caller supplies elapsed time on
    the context), no model calls. Same context in, same verdict out.
  * Default deny. An action this module does not recognise is BLOCKed.
  * The verdict carries a machine-readable `code` so the UI, the audit log and
    the tests all refer to the same reason by the same name.
"""
from __future__ import annotations

from pydantic import BaseModel

from .context import RecoveryContext
from .enums import (
    CONTACT_ACTIONS,
    INERT_ACTIONS,
    MONEY_ACTIONS,
    NEVER_AUTO_RETRY,
    EventType,
    PolicyDecision,
    RecoveryAction,
)
from .scoring import expected_recovery_value_paise, recovery_probability


class PolicyVerdict(BaseModel):
    """The deterministic answer. `decision` is the only thing the executor reads."""

    action: RecoveryAction
    decision: PolicyDecision
    code: str
    reason: str
    expected_value_paise: int = 0
    recovery_probability: float = 0.0

    @property
    def is_executable(self) -> bool:
        return self.decision == PolicyDecision.ALLOW

    @property
    def blocked(self) -> bool:
        return self.decision == PolicyDecision.BLOCK


def _verdict(
    action: RecoveryAction,
    decision: PolicyDecision,
    code: str,
    reason: str,
    ctx: RecoveryContext,
) -> PolicyVerdict:
    return PolicyVerdict(
        action=action,
        decision=decision,
        code=code,
        reason=reason,
        expected_value_paise=expected_recovery_value_paise(ctx, action),
        recovery_probability=recovery_probability(ctx),
    )


def _rupees(paise: int) -> str:
    return f"Rs {paise / 100:,.2f}"


# --------------------------------------------------------------------------
# Universal guards: conditions under which *no* outward action is permitted,
# whatever the action and whatever the model believes.
# --------------------------------------------------------------------------
def _universal_block(ctx: RecoveryContext) -> tuple[str, str] | None:
    if ctx.amount_paise <= 0:
        return ("INVALID_ORDER", "Order amount is zero or negative; there is nothing to recover.")
    if ctx.order_id is None and ctx.event_type != EventType.CHECKOUT_ABANDONMENT:
        return ("INVALID_ORDER", "Case has no associated order.")
    if ctx.order_already_paid:
        return ("ORDER_ALREADY_PAID",
                "This order is already captured. Retrying would double-charge the customer.")
    if ctx.order_refunded:
        return ("ORDER_REFUNDED", "This order was refunded; collecting again is not permitted.")
    if ctx.order_cancelled:
        return ("ORDER_CANCELLED", "The customer cancelled this order. Recovery is not attempted.")
    if ctx.customer.risk_flagged or "fraud_suspected" in ctx.risk_flags:
        return ("RISK_FLAG_PRESENT",
                "A risk or fraud flag is present on this customer. Automated recovery is disabled.")
    if ctx.minutes_since_event > ctx.policy.case_expiry_hours * 60:
        return ("CASE_EXPIRED",
                f"Case is older than the {ctx.policy.case_expiry_hours}h recovery window.")
    if ctx.event_type == EventType.DUPLICATE_PAYMENT:
        return ("DUPLICATE_EVENT",
                "This is a duplicate payment event; the amount was already collected once.")
    if ctx.event_type == EventType.STATE_MISMATCH:
        return ("REQUIRES_RECONCILIATION",
                "Payment and order state disagree. This needs reconciliation, not a recovery "
                "action.")
    return None


def _contact_block(ctx: RecoveryContext) -> tuple[str, str] | None:
    limit = ctx.policy.max_customer_contacts_24h
    if ctx.contacts_last_24h >= limit:
        return ("CONTACT_LIMIT_EXCEEDED",
                f"Customer already contacted {ctx.contacts_last_24h} times in 24h "
                f"(policy limit {limit}).")
    return None


def _amount_gate(ctx: RecoveryContext, action: RecoveryAction) -> tuple[str, str] | None:
    """Returns an approval requirement, or None if the amount is within limits."""
    if ctx.amount_paise > ctx.policy.approval_threshold_paise:
        return ("ABOVE_APPROVAL_THRESHOLD",
                f"{_rupees(ctx.amount_paise)} exceeds the merchant approval threshold of "
                f"{_rupees(ctx.policy.approval_threshold_paise)}.")
    if ctx.amount_paise > ctx.policy.max_auto_recovery_amount_paise:
        return ("ABOVE_AUTO_RECOVERY_LIMIT",
                f"{_rupees(ctx.amount_paise)} exceeds the automatic recovery limit of "
                f"{_rupees(ctx.policy.max_auto_recovery_amount_paise)}.")
    return None


def evaluate(ctx: RecoveryContext, action: RecoveryAction | str) -> PolicyVerdict:
    """Decide whether `action` may be taken on `ctx`. The only gate in the system."""

    # Default deny: an action outside the closed vocabulary never executes.
    try:
        action = RecoveryAction(action)
    except ValueError:
        return PolicyVerdict(
            action=RecoveryAction.STOP,
            decision=PolicyDecision.BLOCK,
            code="UNKNOWN_ACTION",
            reason=f"Proposed action {action!r} is not in the permitted action set.",
        )

    # Inert actions have no external side effect, so they are always available.
    # STOP in particular must never be blocked - the system must always be able
    # to decide to do nothing.
    if action in INERT_ACTIONS:
        return _verdict(action, PolicyDecision.ALLOW, "NO_SIDE_EFFECT",
                        "Action has no financial or customer-facing side effect.", ctx)

    if (blocked := _universal_block(ctx)) is not None:
        code, reason = blocked
        return _verdict(action, PolicyDecision.BLOCK, code, reason, ctx)

    if action in CONTACT_ACTIONS and (blocked := _contact_block(ctx)) is not None:
        code, reason = blocked
        return _verdict(action, PolicyDecision.BLOCK, code, reason, ctx)

    if action == RecoveryAction.RETRY_PAYMENT:
        if ctx.failure_reason in NEVER_AUTO_RETRY:
            return _verdict(
                action, PolicyDecision.BLOCK, "NON_RETRYABLE_FAILURE",
                f"Failure class {ctx.failure_reason.value} is never automatically retried; "
                "retrying cannot succeed and would annoy the customer.", ctx)
        if ctx.subscription_active is False:
            return _verdict(action, PolicyDecision.BLOCK, "MANDATE_INACTIVE",
                            "The subscription mandate is inactive; no debit is authorised.", ctx)
        if ctx.retry_count >= ctx.policy.max_auto_retries:
            return _verdict(
                action, PolicyDecision.BLOCK, "RETRY_LIMIT_EXCEEDED",
                f"Already attempted {ctx.retry_count} automatic retries "
                f"(policy limit {ctx.policy.max_auto_retries}).", ctx)
        if (ctx.minutes_since_last_attempt is not None
                and ctx.minutes_since_last_attempt < ctx.policy.min_retry_interval_minutes):
            return _verdict(
                action, PolicyDecision.BLOCK, "RETRY_INTERVAL_NOT_ELAPSED",
                f"Last attempt was {ctx.minutes_since_last_attempt} min ago; policy requires "
                f"{ctx.policy.min_retry_interval_minutes} min between retries.", ctx)

    if action == RecoveryAction.SEND_PAYMENT_LINK and not ctx.policy.allow_payment_link_recovery:
        return _verdict(action, PolicyDecision.BLOCK, "PAYMENT_LINK_DISABLED",
                        "Payment-link recovery is switched off in merchant policy.", ctx)

    if action == RecoveryAction.OFFER_ALLOWED_ALTERNATIVE and not ctx.policy.allow_alternative_method:
        return _verdict(action, PolicyDecision.BLOCK, "ALTERNATIVE_METHOD_DISABLED",
                        "Alternative payment methods are switched off in merchant policy.", ctx)

    # Money-moving actions are gated on amount.
    if action in MONEY_ACTIONS and (gate := _amount_gate(ctx, action)) is not None:
        code, reason = gate
        return _verdict(action, PolicyDecision.REQUIRE_APPROVAL, code,
                        reason + " Merchant approval is required before this action runs.", ctx)

    # Finally, refuse to spend more chasing the money than it is worth.
    ev = expected_recovery_value_paise(ctx, action)
    if ev < ctx.policy.min_expected_value_paise:
        return _verdict(
            action, PolicyDecision.BLOCK, "NEGATIVE_EXPECTED_VALUE",
            f"Expected recovery value {_rupees(ev)} is below the merchant floor of "
            f"{_rupees(ctx.policy.min_expected_value_paise)}; chasing this case loses money.",
            ctx)

    return _verdict(action, PolicyDecision.ALLOW, "WITHIN_POLICY",
                    "Action is within all merchant policy limits.", ctx)


def permitted_actions(ctx: RecoveryContext) -> dict[str, PolicyVerdict]:
    """Pre-compute the verdict for every action.

    Handed to the agent so it chooses from a menu that is already known-valid.
    This is a convenience for the model, not a substitute for re-validation:
    `evaluate()` still runs on whatever the model actually returns.
    """
    return {action.value: evaluate(ctx, action) for action in RecoveryAction}


def action_menu(ctx: RecoveryContext) -> list[dict]:
    """Compact serialisation of the menu for the model prompt."""
    return [
        {
            "action": v.action.value,
            "policy_decision": v.decision.value,
            "policy_code": v.code,
            "policy_reason": v.reason,
            "expected_value_inr": round(v.expected_value_paise / 100, 2),
        }
        for v in permitted_actions(ctx).values()
    ]
