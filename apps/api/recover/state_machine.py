"""Deterministic order state machine.

An order's state is the authoritative record of whether money was collected.
It is advanced only by verified provider events - never by an LLM, and never
by a request body the browser controls.
"""
from __future__ import annotations

from .enums import OrderState

#: The complete transition table. Anything not listed here is invalid.
ALLOWED_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.CREATED: frozenset(
        {OrderState.PAYMENT_PENDING, OrderState.CANCELLED}
    ),
    OrderState.PAYMENT_PENDING: frozenset(
        {OrderState.PAYMENT_CAPTURED, OrderState.PAYMENT_FAILED, OrderState.CANCELLED}
    ),
    # A failed payment may be retried, which puts the order back in flight.
    OrderState.PAYMENT_FAILED: frozenset(
        {OrderState.PAYMENT_PENDING, OrderState.PAYMENT_CAPTURED, OrderState.CANCELLED}
    ),
    OrderState.PAYMENT_CAPTURED: frozenset(
        {OrderState.COMPLETED, OrderState.REFUNDED}
    ),
    # Terminal states.
    OrderState.COMPLETED: frozenset({OrderState.REFUNDED}),
    OrderState.CANCELLED: frozenset(),
    OrderState.REFUNDED: frozenset(),
}

TERMINAL_STATES = frozenset({OrderState.CANCELLED, OrderState.REFUNDED})

#: States in which the merchant has been paid.
PAID_STATES = frozenset({OrderState.PAYMENT_CAPTURED, OrderState.COMPLETED})


class InvalidStateTransition(Exception):
    """Raised when a transition would corrupt the financial record."""

    def __init__(self, current: OrderState, target: OrderState) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"Invalid order state transition {current.value} -> {target.value}. "
            f"Allowed from {current.value}: "
            f"{sorted(s.value for s in ALLOWED_TRANSITIONS.get(current, frozenset())) or 'none'}"
        )


def can_transition(current: OrderState, target: OrderState) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def assert_transition(current: OrderState, target: OrderState) -> None:
    if not can_transition(current, target):
        raise InvalidStateTransition(current, target)


def transition(order, target: OrderState) -> OrderState:
    """Apply a transition to an Order, or raise. Mutates `order.state` in place."""
    current = OrderState(order.state)
    if current == target:
        # Idempotent no-op: a duplicate webhook must not be an error.
        return current
    assert_transition(current, target)
    order.state = target
    return target


def is_recoverable_state(state: OrderState) -> bool:
    """Whether an order in this state could still legitimately be paid."""
    return state in {OrderState.CREATED, OrderState.PAYMENT_PENDING, OrderState.PAYMENT_FAILED}
