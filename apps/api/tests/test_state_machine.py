"""Order state machine tests.

The order state is the authoritative record of whether the merchant was paid.
A wrong transition here is a wrong number on an accounting report.
"""
from __future__ import annotations

import pytest

from recover.enums import OrderState
from recover.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidStateTransition,
    assert_transition,
    can_transition,
    is_recoverable_state,
    transition,
)


class FakeOrder:
    def __init__(self, state: OrderState) -> None:
        self.state = state


def test_happy_path_to_completion():
    order = FakeOrder(OrderState.CREATED)
    for target in (OrderState.PAYMENT_PENDING, OrderState.PAYMENT_CAPTURED,
                   OrderState.COMPLETED):
        transition(order, target)
    assert order.state == OrderState.COMPLETED


def test_failed_payment_can_be_retried():
    order = FakeOrder(OrderState.PAYMENT_FAILED)
    transition(order, OrderState.PAYMENT_PENDING)
    assert order.state == OrderState.PAYMENT_PENDING


@pytest.mark.parametrize("current,target", [
    (OrderState.COMPLETED, OrderState.PAYMENT_PENDING),
    (OrderState.REFUNDED, OrderState.PAYMENT_CAPTURED),
    (OrderState.CANCELLED, OrderState.PAYMENT_CAPTURED),
    (OrderState.CANCELLED, OrderState.PAYMENT_PENDING),
    (OrderState.CREATED, OrderState.COMPLETED),
    (OrderState.REFUNDED, OrderState.COMPLETED),
])
def test_invalid_transitions_are_refused(current, target):
    assert not can_transition(current, target)
    with pytest.raises(InvalidStateTransition):
        assert_transition(current, target)


def test_transition_to_the_same_state_is_a_no_op():
    """A duplicate webhook must not raise. Idempotency depends on this."""
    order = FakeOrder(OrderState.PAYMENT_CAPTURED)
    assert transition(order, OrderState.PAYMENT_CAPTURED) == OrderState.PAYMENT_CAPTURED
    assert order.state == OrderState.PAYMENT_CAPTURED


def test_terminal_states_have_no_way_back():
    assert ALLOWED_TRANSITIONS[OrderState.CANCELLED] == frozenset()
    assert ALLOWED_TRANSITIONS[OrderState.REFUNDED] == frozenset()


def test_error_message_names_the_allowed_transitions():
    """An operator reading a 409 should be told what *would* have been legal."""
    with pytest.raises(InvalidStateTransition) as excinfo:
        assert_transition(OrderState.COMPLETED, OrderState.PAYMENT_PENDING)
    message = str(excinfo.value)
    assert "COMPLETED -> PAYMENT_PENDING" in message
    assert "REFUNDED" in message


def test_every_state_appears_in_the_transition_table():
    """A state missing from the table would silently deny every transition."""
    assert set(ALLOWED_TRANSITIONS) == set(OrderState)


@pytest.mark.parametrize("state,recoverable", [
    (OrderState.CREATED, True),
    (OrderState.PAYMENT_PENDING, True),
    (OrderState.PAYMENT_FAILED, True),
    (OrderState.PAYMENT_CAPTURED, False),
    (OrderState.COMPLETED, False),
    (OrderState.CANCELLED, False),
    (OrderState.REFUNDED, False),
])
def test_recoverable_states(state, recoverable):
    assert is_recoverable_state(state) is recoverable
