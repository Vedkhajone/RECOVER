"""Deterministic recovery analytics.

Nothing in this module is probabilistic at runtime and nothing here calls a
model. Given the same context it returns the same number, every time - which is
what lets the expected-value figure appear in an audit log and be defended.

The coefficients below are *fitted*, not invented: `scripts/tune_thresholds.py`
grid-searches them against the **development split only** and writes the result
to `recover/tuning.json`. The held-out split is never used for tuning.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .context import RecoveryContext
from .enums import CONTACT_ACTIONS, EventType, FailureReason, RecoveryAction

TUNING_PATH = Path(__file__).with_name("tuning.json")

#: Prior probability that a *first* recovery attempt succeeds, per failure class.
#: Ordered by how much signal the error code carries about the customer's intent.
BASE_PROBABILITY: dict[FailureReason, float] = {
    FailureReason.BANK_TRANSIENT: 0.62,
    FailureReason.NETWORK_ERROR: 0.58,
    FailureReason.AUTHENTICATION_FAILED: 0.44,
    FailureReason.INCORRECT_CVV: 0.42,
    FailureReason.INSUFFICIENT_FUNDS: 0.26,
    FailureReason.DO_NOT_HONOUR: 0.20,
    FailureReason.CARD_EXPIRED: 0.10,
    FailureReason.MANDATE_INACTIVE: 0.08,
    FailureReason.RISK_DECLINED: 0.01,
    FailureReason.CUSTOMER_CANCELLED: 0.01,
    FailureReason.NONE: 0.30,
}

#: Prior for event classes where there is no payment error code to read.
EVENT_BASE_PROBABILITY: dict[EventType, float] = {
    EventType.CHECKOUT_ABANDONMENT: 0.32,
    EventType.OVERDUE_INVOICE: 0.46,
    EventType.STATE_MISMATCH: 0.0,      # nothing to recover; needs reconciliation
    EventType.DUPLICATE_PAYMENT: 0.0,   # money already collected once
}

DEFAULT_TUNING: dict[str, float] = {
    # Multiplier applied per prior failed recovery attempt on the same case.
    "retry_decay": 0.55,
    # Customer reliability term: p *= (floor + span * previous_success_rate).
    "history_floor": 0.70,
    "history_span": 0.62,
    # A customer with no history at all gets this neutral reliability value.
    "unknown_history_rate": 0.45,
    # Probability at or above which an active recovery attempt is worthwhile.
    "act_probability_threshold": 0.30,
    # Probability at or above which a *payment retry* (vs a softer nudge) is used.
    "retry_probability_threshold": 0.45,
    # Overdue invoices decay in recoverability the longer they sit.
    "invoice_daily_decay": 0.015,
}


@lru_cache(maxsize=1)
def tuning() -> dict[str, float]:
    """Fitted parameters, falling back to the committed defaults."""
    params = dict(DEFAULT_TUNING)
    if TUNING_PATH.exists():
        try:
            loaded = json.loads(TUNING_PATH.read_text())
            params.update({k: float(v) for k, v in loaded.get("params", {}).items()
                           if k in DEFAULT_TUNING})
        except (json.JSONDecodeError, TypeError, ValueError):
            # A corrupt tuning file must never take the service down; the
            # committed defaults are always a valid operating point.
            pass
    return params


def tuning_source() -> str:
    return "fitted (recover/tuning.json)" if TUNING_PATH.exists() else "built-in defaults"


def recovery_probability(ctx: RecoveryContext) -> float:
    """Deterministic estimate that this case can still be collected.

    This is the authoritative number. The AI is shown it; the AI does not
    produce it.
    """
    t = tuning()

    # 1. Hard zeros - money that cannot be recovered because it is not at risk,
    #    or because recovering it would be wrong.
    if ctx.order_already_paid or ctx.order_refunded or ctx.order_cancelled:
        return 0.0
    if ctx.customer.risk_flagged or ctx.failure_reason == FailureReason.RISK_DECLINED:
        return 0.0
    if ctx.event_type in (EventType.STATE_MISMATCH, EventType.DUPLICATE_PAYMENT):
        return 0.0
    if ctx.subscription_active is False:
        return 0.0

    # 2. Base prior: error code if we have one, otherwise the event class.
    if ctx.failure_reason != FailureReason.NONE:
        p = BASE_PROBABILITY[ctx.failure_reason]
    else:
        p = EVENT_BASE_PROBABILITY.get(ctx.event_type, BASE_PROBABILITY[FailureReason.NONE])

    # 3. Customer reliability.
    total_history = ctx.customer.successful_payments + ctx.customer.failed_payments
    rate = (ctx.customer.previous_success_rate if total_history >= 3
            else t["unknown_history_rate"])
    p *= t["history_floor"] + t["history_span"] * rate

    # 4. Each failed attempt is evidence the next one also fails.
    p *= t["retry_decay"] ** max(0, ctx.retry_count)

    # 5. Ageing invoices get harder to collect.
    if ctx.invoice_days_overdue:
        p *= max(0.2, 1.0 - t["invoice_daily_decay"] * ctx.invoice_days_overdue)

    # 6. Repeated insufficient-funds is a strong negative signal beyond decay.
    if ctx.failure_reason == FailureReason.INSUFFICIENT_FUNDS and ctx.retry_count >= 1:
        p *= 0.6

    return max(0.0, min(1.0, round(p, 4)))


def intervention_cost_paise(ctx: RecoveryContext, action: RecoveryAction) -> int:
    """What it costs us to attempt this action, in paise."""
    policy = ctx.policy
    cost = 0
    if action == RecoveryAction.RETRY_PAYMENT:
        cost += policy.retry_cost_paise
    if action in CONTACT_ACTIONS:
        cost += policy.contact_cost_paise
    return cost


def expected_recovery_value_paise(ctx: RecoveryContext, action: RecoveryAction) -> int:
    """expected_value = amount x P(recovery) - intervention cost - contact cost.

    Returned in integer paise. Negative values are meaningful: they are the
    reason the system declines to act on low-value, low-probability cases.
    """
    p = recovery_probability(ctx)
    gross = int(round(ctx.amount_paise * p))
    return gross - intervention_cost_paise(ctx, action)


def score_case(ctx: RecoveryContext) -> dict:
    """Full analytics block handed to the agent and stored on the case."""
    p = recovery_probability(ctx)
    t = tuning()
    per_action = {
        action.value: {
            "expected_value_paise": expected_recovery_value_paise(ctx, action),
            "intervention_cost_paise": intervention_cost_paise(ctx, action),
        }
        for action in RecoveryAction
    }
    return {
        "recovery_probability": p,
        "amount_paise": ctx.amount_paise,
        "gross_expected_recovery_paise": int(round(ctx.amount_paise * p)),
        "act_probability_threshold": t["act_probability_threshold"],
        "retry_probability_threshold": t["retry_probability_threshold"],
        "above_act_threshold": p >= t["act_probability_threshold"],
        "above_retry_threshold": p >= t["retry_probability_threshold"],
        "per_action": per_action,
        "tuning_source": tuning_source(),
    }
