"""Revenue-at-risk detection.

The first question in the pipeline, and the cheapest place to be wrong in an
expensive way: every case that gets past here costs money to investigate, and
every case wrongly waved through risks contacting a customer who owes nothing.

Deliberately conservative. Anything that looks collected, cancelled, refunded or
duplicated is not revenue at risk, even when the event that reached us is
labelled as a failure.
"""
from __future__ import annotations

from .context import RecoveryContext
from .enums import EventType


def is_revenue_at_risk(ctx: RecoveryContext) -> tuple[bool, str]:
    """Return (at_risk, reason)."""
    if ctx.amount_paise <= 0:
        return False, "Amount is zero; nothing is outstanding."
    if ctx.order_already_paid:
        return False, "Order is already captured; the money was collected."
    if ctx.order_refunded:
        return False, "Order was refunded; the balance is settled."
    if ctx.order_cancelled:
        return False, "Customer cancelled the order; there is no revenue to recover."
    if ctx.event_type == EventType.DUPLICATE_PAYMENT:
        return False, "Duplicate payment event; the amount was already collected once."
    if ctx.event_type == EventType.STATE_MISMATCH:
        return (False,
                "Payment and order records disagree. The money is in hand - this is a "
                "reconciliation exception, not revenue at risk.")
    return True, "Uncollected revenue on a live order."
