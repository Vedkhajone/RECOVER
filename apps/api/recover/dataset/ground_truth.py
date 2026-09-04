"""Ground truth for the synthetic evaluation set.

This module answers a different question from the rest of the system, and it is
important that it stays that way.

`recover/scoring.py` asks: *given what we can observe, how likely is recovery?*
This module asks: *what was actually true in the simulated world?* It draws on
latent state the system never sees - the customer's real intent, whether the
instrument would really have authorised, whether the order was already
cancelled at the moment we looked.

The two are kept structurally independent on purpose. If ground truth were
derived from the same coefficients the system uses to decide, the evaluation
would be measuring the system against itself and every number would be
meaningless.
"""
from __future__ import annotations

from ..enums import EventType, FailureReason, RecoveryAction

#: True probability that the money is still collectable at all, by failure
#: class. These are the "physics" of the simulated world - deliberately not the
#: same numbers as BASE_PROBABILITY in scoring.py.
TRUE_COLLECTABILITY: dict[FailureReason, float] = {
    FailureReason.BANK_TRANSIENT: 0.78,
    FailureReason.NETWORK_ERROR: 0.74,
    FailureReason.AUTHENTICATION_FAILED: 0.55,
    FailureReason.INCORRECT_CVV: 0.60,
    FailureReason.INSUFFICIENT_FUNDS: 0.34,
    FailureReason.DO_NOT_HONOUR: 0.26,
    FailureReason.CARD_EXPIRED: 0.48,   # collectable, but only on a NEW instrument
    FailureReason.MANDATE_INACTIVE: 0.30,  # collectable only via re-authorisation
    FailureReason.RISK_DECLINED: 0.0,
    FailureReason.CUSTOMER_CANCELLED: 0.0,
    FailureReason.NONE: 0.40,
}

TRUE_EVENT_COLLECTABILITY: dict[EventType, float] = {
    EventType.CHECKOUT_ABANDONMENT: 0.38,
    EventType.OVERDUE_INVOICE: 0.62,
    EventType.STATE_MISMATCH: 0.0,
    EventType.DUPLICATE_PAYMENT: 0.0,
}

#: Which action classes actually work for a given failure. Taking an action
#: outside this set never recovers the money, however reasonable it looked.
EFFECTIVE_ACTIONS: dict[FailureReason, set[RecoveryAction]] = {
    FailureReason.CARD_EXPIRED: {RecoveryAction.OFFER_ALLOWED_ALTERNATIVE,
                                 RecoveryAction.SEND_PAYMENT_LINK},
    FailureReason.MANDATE_INACTIVE: {RecoveryAction.OFFER_ALLOWED_ALTERNATIVE,
                                     RecoveryAction.SEND_PAYMENT_LINK},
    FailureReason.INCORRECT_CVV: {RecoveryAction.RETRY_PAYMENT,
                                  RecoveryAction.SEND_PAYMENT_LINK,
                                  RecoveryAction.OFFER_ALLOWED_ALTERNATIVE},
}

#: Default set for everything else: any active recovery route can work.
DEFAULT_EFFECTIVE = {
    RecoveryAction.RETRY_PAYMENT,
    RecoveryAction.SEND_PAYMENT_LINK,
    RecoveryAction.SEND_REMINDER,
    RecoveryAction.OFFER_ALLOWED_ALTERNATIVE,
}

ACTIVE_ACTIONS = frozenset(DEFAULT_EFFECTIVE)


def effective_actions(failure_reason: FailureReason,
                      event_type: EventType) -> set[RecoveryAction]:
    if event_type == EventType.OVERDUE_INVOICE:
        return {RecoveryAction.SEND_REMINDER, RecoveryAction.SEND_PAYMENT_LINK}
    if event_type == EventType.CHECKOUT_ABANDONMENT:
        return {RecoveryAction.SEND_PAYMENT_LINK, RecoveryAction.SEND_REMINDER}
    return EFFECTIVE_ACTIONS.get(failure_reason, set(DEFAULT_EFFECTIVE))


def true_collectability(latent: dict) -> float:
    """Latent probability that this money can still be collected."""
    # Hard zeros first: these are facts about the world, not estimates.
    if latent["truly_cancelled"] or latent["truly_paid"] or latent["truly_refunded"]:
        return 0.0
    if latent["risk_flagged"]:
        return 0.0
    if latent["event_type"] in TRUE_EVENT_COLLECTABILITY and \
            latent["failure_reason"] == FailureReason.NONE:
        base = TRUE_EVENT_COLLECTABILITY[latent["event_type"]]
    else:
        base = TRUE_COLLECTABILITY[latent["failure_reason"]]
        if latent["event_type"] in (EventType.STATE_MISMATCH, EventType.DUPLICATE_PAYMENT):
            base = 0.0

    # A customer who has paid you many times before is genuinely more likely to
    # pay you again - a stronger effect in the world than the system assumes.
    successes = latent["successful_payments"]
    failures = latent["failed_payments"]
    total = successes + failures
    if total >= 3:
        rate = successes / total
        base *= 0.60 + 0.75 * rate
    # Each previous failed attempt on the same case is real evidence.
    base *= 0.62 ** latent["retry_count"]
    if latent["invoice_days_overdue"]:
        base *= max(0.15, 1.0 - 0.012 * latent["invoice_days_overdue"])
    return max(0.0, min(1.0, base))


def best_action(latent: dict, *, recoverable: bool) -> RecoveryAction:
    """What a perfectly informed operator would do.

    Note this ignores merchant policy entirely. Policy decides whether an action
    is *permitted*; ground truth says what would have been *right*. Keeping them
    separate is what lets the evaluation report "correct action, blocked by
    policy" as its own outcome rather than silently scoring it as a miss.
    """
    event = latent["event_type"]
    reason = latent["failure_reason"]

    if event == EventType.STATE_MISMATCH:
        return RecoveryAction.ESCALATE_TO_MERCHANT
    if event == EventType.DUPLICATE_PAYMENT:
        return RecoveryAction.STOP
    if latent["truly_cancelled"] or latent["truly_paid"] or latent["truly_refunded"]:
        return RecoveryAction.STOP
    if latent["risk_flagged"] or reason == FailureReason.RISK_DECLINED:
        return RecoveryAction.STOP
    if not recoverable:
        return RecoveryAction.STOP
    if reason in (FailureReason.CARD_EXPIRED, FailureReason.MANDATE_INACTIVE):
        return RecoveryAction.OFFER_ALLOWED_ALTERNATIVE
    if event == EventType.CHECKOUT_ABANDONMENT:
        return RecoveryAction.SEND_PAYMENT_LINK
    if event == EventType.OVERDUE_INVOICE:
        return RecoveryAction.SEND_REMINDER
    if reason == FailureReason.INSUFFICIENT_FUNDS:
        # Retrying inside the hour genuinely fails again; waiting is correct.
        if latent["minutes_since_last_attempt"] is not None and \
                latent["minutes_since_last_attempt"] < 30:
            return RecoveryAction.WAIT
        return RecoveryAction.RETRY_PAYMENT
    return RecoveryAction.RETRY_PAYMENT


def max_useful_attempts(latent: dict) -> int:
    """How many attempts would ever have been worth making."""
    if latent["truly_cancelled"] or latent["truly_paid"] or latent["risk_flagged"]:
        return 0
    reason = latent["failure_reason"]
    if reason in (FailureReason.RISK_DECLINED, FailureReason.CUSTOMER_CANCELLED):
        return 0
    if reason in (FailureReason.CARD_EXPIRED, FailureReason.MANDATE_INACTIVE):
        return 1
    if reason == FailureReason.INSUFFICIENT_FUNDS:
        return 2
    return 3


def truly_at_risk(latent: dict) -> bool:
    """Is this genuinely uncollected revenue that the merchant should chase?

    False for money already collected, deliberately cancelled, refunded, or
    duplicated - even when the observed record still looks like a live failure.
    """
    if latent["truly_paid"] or latent["truly_cancelled"] or latent["truly_refunded"]:
        return False
    if latent["event_type"] == EventType.DUPLICATE_PAYMENT:
        return False
    if latent["event_type"] == EventType.STATE_MISMATCH:
        # The money is in hand; the ledger is wrong. Not revenue at risk.
        return False
    return True


def label(latent: dict, rng) -> dict:
    """Produce the full ground-truth block for one record."""
    p = true_collectability(latent)
    # The latent coin flip. This is the fact of the matter for this record:
    # would this customer have paid, given a correct intervention?
    recoverable = rng.random() < p
    at_risk = truly_at_risk(latent)
    action = best_action(latent, recoverable=recoverable)
    return {
        "ground_truth_at_risk": at_risk,
        "ground_truth_recoverable": bool(at_risk and recoverable),
        "ground_truth_best_action": action.value,
        "ground_truth_recoverable_amount_paise": (
            latent["amount_paise"] if (at_risk and recoverable) else 0
        ),
        "ground_truth_max_attempts": max_useful_attempts(latent),
        "ground_truth_effective_actions": sorted(
            a.value for a in effective_actions(latent["failure_reason"], latent["event_type"])
        ),
        "ground_truth_collectability": round(p, 4),
    }
