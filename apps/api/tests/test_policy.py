"""Policy engine tests.

These are the tests that matter most. Every one of them corresponds to a rule a
merchant would state in a sentence, and if one of them regresses the system
either loses money or annoys a customer.
"""
from __future__ import annotations

import pytest

from recover.enums import (
    CustomerSegment,
    EventType,
    FailureReason,
    OrderState,
    PolicyDecision,
    RecoveryAction,
)
from recover.policy import evaluate, permitted_actions
from tests.conftest import make_context


# --------------------------------------------------------------------------
# The worked example from the spec
# --------------------------------------------------------------------------
def test_transient_failure_under_limit_is_allowed():
    """Rs 4,999 transient failure, reliable customer -> retry allowed."""
    verdict = evaluate(make_context(), RecoveryAction.RETRY_PAYMENT)
    assert verdict.decision == PolicyDecision.ALLOW
    assert verdict.code == "WITHIN_POLICY"
    assert verdict.expected_value_paise > 0


def test_amount_just_above_auto_limit_is_not_automatic():
    """Rs 5,001 -> automatic retry is refused; a human decides."""
    verdict = evaluate(make_context(amount_paise=500_100), RecoveryAction.RETRY_PAYMENT)
    assert verdict.decision == PolicyDecision.REQUIRE_APPROVAL
    assert verdict.code == "ABOVE_AUTO_RECOVERY_LIMIT"
    assert not verdict.is_executable


def test_amount_at_exactly_the_auto_limit_is_allowed():
    """Rs 5,000 exactly is inside the limit - boundaries are inclusive."""
    verdict = evaluate(make_context(amount_paise=500_000), RecoveryAction.RETRY_PAYMENT)
    assert verdict.decision == PolicyDecision.ALLOW


def test_amount_above_approval_threshold_requires_approval():
    verdict = evaluate(make_context(amount_paise=1_000_100), RecoveryAction.RETRY_PAYMENT)
    assert verdict.decision == PolicyDecision.REQUIRE_APPROVAL
    assert verdict.code == "ABOVE_APPROVAL_THRESHOLD"


# --------------------------------------------------------------------------
# Retry limits
# --------------------------------------------------------------------------
@pytest.mark.parametrize("retry_count,expected", [
    (0, PolicyDecision.ALLOW),
    (1, PolicyDecision.ALLOW),
    (2, PolicyDecision.BLOCK),
    (3, PolicyDecision.BLOCK),
])
def test_third_retry_is_blocked(retry_count, expected):
    """Policy allows 2 automatic retries. The third is refused."""
    ctx = make_context(retry_count=retry_count, minutes_since_last_attempt=120)
    verdict = evaluate(ctx, RecoveryAction.RETRY_PAYMENT)
    assert verdict.decision == expected
    if expected == PolicyDecision.BLOCK:
        assert verdict.code == "RETRY_LIMIT_EXCEEDED"


def test_retry_inside_the_minimum_interval_is_blocked():
    ctx = make_context(retry_count=1, minutes_since_last_attempt=5)
    verdict = evaluate(ctx, RecoveryAction.RETRY_PAYMENT)
    assert verdict.decision == PolicyDecision.BLOCK
    assert verdict.code == "RETRY_INTERVAL_NOT_ELAPSED"


def test_retry_after_the_interval_elapses_is_allowed():
    ctx = make_context(retry_count=1, minutes_since_last_attempt=31)
    assert evaluate(ctx, RecoveryAction.RETRY_PAYMENT).decision == PolicyDecision.ALLOW


# --------------------------------------------------------------------------
# States in which recovery must never happen
# --------------------------------------------------------------------------
def test_cancelled_order_blocks_recovery():
    ctx = make_context(order_cancelled=True, order_state=OrderState.CANCELLED)
    verdict = evaluate(ctx, RecoveryAction.RETRY_PAYMENT)
    assert verdict.decision == PolicyDecision.BLOCK
    assert verdict.code == "ORDER_CANCELLED"


def test_already_paid_order_blocks_recovery():
    """The most expensive possible bug: retrying a captured order."""
    ctx = make_context(order_already_paid=True, order_state=OrderState.PAYMENT_CAPTURED)
    verdict = evaluate(ctx, RecoveryAction.RETRY_PAYMENT)
    assert verdict.decision == PolicyDecision.BLOCK
    assert verdict.code == "ORDER_ALREADY_PAID"


def test_refunded_order_blocks_recovery():
    ctx = make_context(order_refunded=True, order_state=OrderState.REFUNDED)
    assert evaluate(ctx, RecoveryAction.RETRY_PAYMENT).code == "ORDER_REFUNDED"


def test_risk_flag_blocks_every_outward_action():
    customer = make_context().customer.model_copy(update={"risk_flagged": True})
    ctx = make_context(customer=customer)
    for action in (RecoveryAction.RETRY_PAYMENT, RecoveryAction.SEND_PAYMENT_LINK,
                   RecoveryAction.SEND_REMINDER, RecoveryAction.OFFER_ALLOWED_ALTERNATIVE):
        verdict = evaluate(ctx, action)
        assert verdict.decision == PolicyDecision.BLOCK, action
        assert verdict.code == "RISK_FLAG_PRESENT"


def test_expired_case_blocks_recovery():
    ctx = make_context(minutes_since_event=73 * 60)
    assert evaluate(ctx, RecoveryAction.RETRY_PAYMENT).code == "CASE_EXPIRED"


def test_zero_amount_is_invalid():
    ctx = make_context(amount_paise=0)
    assert evaluate(ctx, RecoveryAction.RETRY_PAYMENT).code == "INVALID_ORDER"


# --------------------------------------------------------------------------
# Failure classes a retry can never fix
# --------------------------------------------------------------------------
@pytest.mark.parametrize("reason", [
    FailureReason.CARD_EXPIRED,
    FailureReason.RISK_DECLINED,
    FailureReason.CUSTOMER_CANCELLED,
    FailureReason.MANDATE_INACTIVE,
])
def test_non_retryable_failures_are_blocked(reason):
    ctx = make_context(failure_reason=reason)
    verdict = evaluate(ctx, RecoveryAction.RETRY_PAYMENT)
    assert verdict.decision == PolicyDecision.BLOCK
    assert verdict.code in {"NON_RETRYABLE_FAILURE", "RISK_FLAG_PRESENT"}


def test_expired_card_still_allows_an_alternative_method():
    """The money is recoverable - just not on that card."""
    ctx = make_context(failure_reason=FailureReason.CARD_EXPIRED)
    assert evaluate(ctx, RecoveryAction.OFFER_ALLOWED_ALTERNATIVE).decision == (
        PolicyDecision.ALLOW
    )


def test_inactive_mandate_blocks_subscription_retry():
    ctx = make_context(event_type=EventType.SUBSCRIPTION_PAYMENT_FAILURE,
                       failure_reason=FailureReason.INSUFFICIENT_FUNDS,
                       subscription_active=False)
    verdict = evaluate(ctx, RecoveryAction.RETRY_PAYMENT)
    assert verdict.decision == PolicyDecision.BLOCK
    assert verdict.code == "MANDATE_INACTIVE"


# --------------------------------------------------------------------------
# Contact budget
# --------------------------------------------------------------------------
@pytest.mark.parametrize("contacts,expected", [
    (0, PolicyDecision.ALLOW),
    (1, PolicyDecision.ALLOW),
    (2, PolicyDecision.BLOCK),
])
def test_contact_limit_is_enforced(contacts, expected):
    ctx = make_context(contacts_last_24h=contacts)
    verdict = evaluate(ctx, RecoveryAction.SEND_REMINDER)
    assert verdict.decision == expected
    if expected == PolicyDecision.BLOCK:
        assert verdict.code == "CONTACT_LIMIT_EXCEEDED"


def test_contact_limit_does_not_block_a_retry():
    """A retry is not a message; the contact budget must not apply to it."""
    ctx = make_context(contacts_last_24h=5)
    assert evaluate(ctx, RecoveryAction.RETRY_PAYMENT).decision == PolicyDecision.ALLOW


# --------------------------------------------------------------------------
# Configurable channels
# --------------------------------------------------------------------------
def test_disabling_payment_links_blocks_that_action():
    policy = make_context().policy.model_copy(
        update={"allow_payment_link_recovery": False})
    ctx = make_context(policy=policy)
    verdict = evaluate(ctx, RecoveryAction.SEND_PAYMENT_LINK)
    assert verdict.decision == PolicyDecision.BLOCK
    assert verdict.code == "PAYMENT_LINK_DISABLED"


def test_disabling_alternatives_blocks_that_action():
    policy = make_context().policy.model_copy(update={"allow_alternative_method": False})
    ctx = make_context(policy=policy, failure_reason=FailureReason.CARD_EXPIRED)
    assert evaluate(ctx, RecoveryAction.OFFER_ALLOWED_ALTERNATIVE).code == (
        "ALTERNATIVE_METHOD_DISABLED"
    )


def test_expected_value_floor_blocks_unprofitable_cases():
    policy = make_context().policy.model_copy(
        update={"min_expected_value_paise": 400_000})
    ctx = make_context(policy=policy, amount_paise=10_000)
    verdict = evaluate(ctx, RecoveryAction.RETRY_PAYMENT)
    assert verdict.decision == PolicyDecision.BLOCK
    assert verdict.code == "NEGATIVE_EXPECTED_VALUE"


# --------------------------------------------------------------------------
# Structural guarantees
# --------------------------------------------------------------------------
def test_unknown_action_is_denied_by_default():
    """An action outside the closed vocabulary must never execute."""
    verdict = evaluate(make_context(), "DELETE_ALL_ORDERS")
    assert verdict.decision == PolicyDecision.BLOCK
    assert verdict.code == "UNKNOWN_ACTION"


def test_stop_is_never_blocked():
    """The system must always be able to decide to do nothing - on any case,
    in any state, including ones where everything else is refused."""
    contexts = [
        make_context(),
        make_context(order_cancelled=True),
        make_context(order_already_paid=True),
        make_context(retry_count=9, contacts_last_24h=9),
        make_context(minutes_since_event=10_000),
        make_context(amount_paise=0),
    ]
    for ctx in contexts:
        assert evaluate(ctx, RecoveryAction.STOP).decision == PolicyDecision.ALLOW
        assert evaluate(ctx, RecoveryAction.WAIT).decision == PolicyDecision.ALLOW
        assert evaluate(ctx, RecoveryAction.ESCALATE_TO_MERCHANT).decision == (
            PolicyDecision.ALLOW
        )


def test_state_mismatch_routes_to_reconciliation():
    ctx = make_context(event_type=EventType.STATE_MISMATCH)
    verdict = evaluate(ctx, RecoveryAction.RETRY_PAYMENT)
    assert verdict.decision == PolicyDecision.BLOCK
    assert verdict.code == "REQUIRES_RECONCILIATION"


def test_duplicate_payment_event_blocks_recovery():
    ctx = make_context(event_type=EventType.DUPLICATE_PAYMENT)
    assert evaluate(ctx, RecoveryAction.RETRY_PAYMENT).code == "DUPLICATE_EVENT"


def test_policy_is_pure():
    """Same context in, same verdict out - twenty times over."""
    ctx = make_context()
    verdicts = {evaluate(ctx, RecoveryAction.RETRY_PAYMENT).model_dump_json()
                for _ in range(20)}
    assert len(verdicts) == 1


def test_permitted_actions_covers_every_action():
    menu = permitted_actions(make_context())
    assert set(menu) == {action.value for action in RecoveryAction}
