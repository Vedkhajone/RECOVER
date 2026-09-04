"""Deterministic synthetic event generator.

Seeded, so `--seed 20260901` always produces byte-identical records and the
evaluation is reproducible on any machine.

Two things this generator does that a naive one would not:

  1. **Correlated fields.** A customer's segment drives their history, which
     drives which failures they hit and how likely they are to come back. An
     at-risk customer with eleven prior failures does not get a transient
     gateway error and a 90% recovery chance.

  2. **Observation noise.** The record the system sees is not always the truth.
     A customer can cancel after the failure event but before we look; a
     payment can succeed at the provider while our order row goes stale. So the
     generator maintains a latent world and then *derives* an observed record
     from it, sometimes lossily. This is what makes the detection precision and
     recall numbers non-trivial - a system that trusts every field it is given
     will chase money that was never at risk.
"""
from __future__ import annotations

import hashlib
import random
from datetime import UTC, datetime, timedelta

from ..enums import CustomerSegment, EventType, FailureReason, OrderState, PaymentStatus
from . import ground_truth

DEFAULT_SEED = 20260901
DEFAULT_SIZE = 10_000

EVENT_WEIGHTS = {
    EventType.PAYMENT_FAILURE: 0.44,
    EventType.CHECKOUT_ABANDONMENT: 0.22,
    EventType.SUBSCRIPTION_PAYMENT_FAILURE: 0.13,
    EventType.OVERDUE_INVOICE: 0.12,
    EventType.STATE_MISMATCH: 0.06,
    EventType.DUPLICATE_PAYMENT: 0.03,
}

SEGMENT_WEIGHTS = {
    CustomerSegment.LOYAL: 0.22,
    CustomerSegment.REGULAR: 0.40,
    CustomerSegment.NEW: 0.26,
    CustomerSegment.AT_RISK: 0.12,
}

#: (successes range, failures range) per segment.
SEGMENT_HISTORY = {
    CustomerSegment.LOYAL: ((8, 42), (0, 3)),
    CustomerSegment.REGULAR: ((3, 14), (0, 5)),
    CustomerSegment.NEW: ((0, 2), (0, 2)),
    CustomerSegment.AT_RISK: ((0, 6), (4, 16)),
}

#: Failure mix for one-off card/UPI payments, per segment. At-risk customers hit
#: balance problems far more often; loyal ones mostly hit infrastructure.
FAILURE_MIX = {
    CustomerSegment.LOYAL: {
        FailureReason.BANK_TRANSIENT: 0.34, FailureReason.NETWORK_ERROR: 0.16,
        FailureReason.AUTHENTICATION_FAILED: 0.12, FailureReason.INCORRECT_CVV: 0.07,
        FailureReason.INSUFFICIENT_FUNDS: 0.10, FailureReason.DO_NOT_HONOUR: 0.11,
        FailureReason.CARD_EXPIRED: 0.08, FailureReason.RISK_DECLINED: 0.005,
        FailureReason.CUSTOMER_CANCELLED: 0.015,
    },
    CustomerSegment.REGULAR: {
        FailureReason.BANK_TRANSIENT: 0.27, FailureReason.NETWORK_ERROR: 0.13,
        FailureReason.AUTHENTICATION_FAILED: 0.12, FailureReason.INCORRECT_CVV: 0.07,
        FailureReason.INSUFFICIENT_FUNDS: 0.18, FailureReason.DO_NOT_HONOUR: 0.13,
        FailureReason.CARD_EXPIRED: 0.07, FailureReason.RISK_DECLINED: 0.01,
        FailureReason.CUSTOMER_CANCELLED: 0.02,
    },
    CustomerSegment.NEW: {
        FailureReason.BANK_TRANSIENT: 0.22, FailureReason.NETWORK_ERROR: 0.11,
        FailureReason.AUTHENTICATION_FAILED: 0.18, FailureReason.INCORRECT_CVV: 0.12,
        FailureReason.INSUFFICIENT_FUNDS: 0.15, FailureReason.DO_NOT_HONOUR: 0.13,
        FailureReason.CARD_EXPIRED: 0.04, FailureReason.RISK_DECLINED: 0.02,
        FailureReason.CUSTOMER_CANCELLED: 0.03,
    },
    CustomerSegment.AT_RISK: {
        FailureReason.BANK_TRANSIENT: 0.12, FailureReason.NETWORK_ERROR: 0.06,
        FailureReason.AUTHENTICATION_FAILED: 0.10, FailureReason.INCORRECT_CVV: 0.06,
        FailureReason.INSUFFICIENT_FUNDS: 0.38, FailureReason.DO_NOT_HONOUR: 0.18,
        FailureReason.CARD_EXPIRED: 0.05, FailureReason.RISK_DECLINED: 0.03,
        FailureReason.CUSTOMER_CANCELLED: 0.02,
    },
}

SUBSCRIPTION_FAILURE_MIX = {
    FailureReason.INSUFFICIENT_FUNDS: 0.34,
    FailureReason.MANDATE_INACTIVE: 0.24,
    FailureReason.BANK_TRANSIENT: 0.22,
    FailureReason.CARD_EXPIRED: 0.14,
    FailureReason.DO_NOT_HONOUR: 0.06,
}

PROVIDER_ERROR_CODES = {
    FailureReason.BANK_TRANSIENT: ("GATEWAY_ERROR", "payment_failed",
                                   "Payment processing failed at the bank"),
    FailureReason.NETWORK_ERROR: ("GATEWAY_ERROR", "payment_failed",
                                  "Connection to the issuer was interrupted"),
    FailureReason.INSUFFICIENT_FUNDS: ("BAD_REQUEST_ERROR", "insufficient_funds",
                                       "Your account does not have sufficient balance"),
    FailureReason.CARD_EXPIRED: ("BAD_REQUEST_ERROR", "card_expired",
                                 "The card used has expired"),
    FailureReason.INCORRECT_CVV: ("BAD_REQUEST_ERROR", "incorrect_cvv",
                                  "The CVV entered is incorrect"),
    FailureReason.DO_NOT_HONOUR: ("BAD_REQUEST_ERROR", "payment_declined_by_bank",
                                  "Your payment was declined by the bank"),
    FailureReason.AUTHENTICATION_FAILED: ("BAD_REQUEST_ERROR", "authentication_failed",
                                          "Authentication could not be completed"),
    FailureReason.RISK_DECLINED: ("BAD_REQUEST_ERROR", "payment_risk_check_failed",
                                  "Payment blocked by risk checks"),
    FailureReason.MANDATE_INACTIVE: ("BAD_REQUEST_ERROR", "invalid_mandate",
                                     "The mandate for this subscription is not active"),
    FailureReason.CUSTOMER_CANCELLED: ("BAD_REQUEST_ERROR", "payment_cancelled",
                                       "Payment was cancelled by the customer"),
    FailureReason.NONE: (None, None, None),
}

PRODUCTS = [
    ("Wireless Headphones", 499_900), ("Annual SaaS Plan", 1_199_900),
    ("Smart Watch", 899_900), ("Monthly Subscription", 49_900),
    ("Running Shoes", 349_900), ("Coffee Subscription", 89_900),
    ("Laptop Stand", 249_900), ("Cloud Storage 1TB", 79_900),
    ("Fitness Membership", 599_900), ("Ergonomic Chair", 1_849_900),
    ("Phone Case", 99_900), ("Team Seat Add-on", 2_499_900),
]


def _weighted(rng: random.Random, weights: dict):
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]


def _amount_for(rng: random.Random, segment: CustomerSegment) -> tuple[str, int]:
    name, base = rng.choice(PRODUCTS)
    # Spread around the catalogue price so the amount distribution straddles the
    # Rs 5,000 auto-recovery limit and the Rs 10,000 approval threshold.
    jitter = rng.uniform(0.55, 1.6 if segment != CustomerSegment.NEW else 1.15)
    amount = int(base * jitter)
    amount = max(9_900, round(amount, -2))
    return name, amount


def generate_records(seed: int = DEFAULT_SEED, size: int = DEFAULT_SIZE) -> list[dict]:
    """Generate `size` synthetic revenue-risk records deterministically."""
    rng = random.Random(seed)
    # A separate stream for latent outcomes so that changing the observation
    # noise does not reshuffle every ground-truth label.
    truth_rng = random.Random(seed ^ 0x5EED)
    now = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    records: list[dict] = []

    for i in range(size):
        txn_id = f"txn_{seed}_{i:06d}"
        customer_id = f"cus_syn_{rng.randrange(1, max(2, size // 3)):06d}"
        segment = _weighted(rng, SEGMENT_WEIGHTS)
        event_type = _weighted(rng, EVENT_WEIGHTS)

        (smin, smax), (fmin, fmax) = SEGMENT_HISTORY[segment]
        successes = rng.randint(smin, smax)
        failures = rng.randint(fmin, fmax)

        risk_flagged = rng.random() < (0.09 if segment == CustomerSegment.AT_RISK else 0.012)

        description, amount = _amount_for(rng, segment)

        # ---- failure class -------------------------------------------------
        if event_type == EventType.SUBSCRIPTION_PAYMENT_FAILURE:
            failure_reason = _weighted(rng, SUBSCRIPTION_FAILURE_MIX)
        elif event_type in (EventType.PAYMENT_FAILURE, EventType.STATE_MISMATCH,
                            EventType.DUPLICATE_PAYMENT):
            failure_reason = _weighted(rng, FAILURE_MIX[segment])
        else:
            failure_reason = FailureReason.NONE
        if risk_flagged and rng.random() < 0.4:
            failure_reason = FailureReason.RISK_DECLINED

        # ---- attempt history ----------------------------------------------
        retry_count = rng.choices([0, 1, 2, 3], weights=[0.62, 0.24, 0.10, 0.04])[0]
        contacts_last_24h = rng.choices([0, 1, 2, 3], weights=[0.60, 0.26, 0.11, 0.03])[0]
        minutes_since_last_attempt = (
            None if retry_count == 0 else rng.choice([3, 8, 17, 29, 35, 62, 140, 400])
        )
        minutes_since_event = rng.choices(
            [rng.randint(1, 45), rng.randint(46, 600), rng.randint(601, 4400)],
            weights=[0.55, 0.33, 0.12],
        )[0]

        # ---- latent world state --------------------------------------------
        truly_paid = event_type in (EventType.STATE_MISMATCH, EventType.DUPLICATE_PAYMENT)
        truly_cancelled = (
            failure_reason == FailureReason.CUSTOMER_CANCELLED or rng.random() < 0.035
        )
        truly_refunded = (not truly_cancelled) and rng.random() < 0.012
        if truly_paid:
            truly_cancelled = False

        subscription_active = (
            None if event_type != EventType.SUBSCRIPTION_PAYMENT_FAILURE
            else failure_reason != FailureReason.MANDATE_INACTIVE
        )
        invoice_days_overdue = (
            rng.choice([2, 5, 9, 14, 22, 38, 60]) if event_type == EventType.OVERDUE_INVOICE
            else None
        )

        latent = {
            "event_type": event_type,
            "failure_reason": failure_reason,
            "amount_paise": amount,
            "successful_payments": successes,
            "failed_payments": failures,
            "retry_count": retry_count,
            "risk_flagged": risk_flagged,
            "truly_paid": truly_paid,
            "truly_cancelled": truly_cancelled,
            "truly_refunded": truly_refunded,
            "minutes_since_last_attempt": minutes_since_last_attempt,
            "invoice_days_overdue": invoice_days_overdue,
        }
        gt = ground_truth.label(latent, truth_rng)

        # ---- observation ----------------------------------------------------
        # What the system actually gets to see. Most of the time it matches the
        # world; sometimes our snapshot is stale, which is the whole reason
        # detection precision is not 1.0.
        stale_snapshot = rng.random() < 0.06
        observed_cancelled = truly_cancelled and not stale_snapshot
        observed_refunded = truly_refunded and not stale_snapshot
        observed_paid = truly_paid and not stale_snapshot

        if observed_paid:
            order_state = OrderState.PAYMENT_CAPTURED
            payment_status = PaymentStatus.CAPTURED
        elif observed_cancelled:
            order_state = OrderState.CANCELLED
            payment_status = PaymentStatus.FAILED
        elif observed_refunded:
            order_state = OrderState.REFUNDED
            payment_status = PaymentStatus.REFUNDED
        elif event_type == EventType.CHECKOUT_ABANDONMENT:
            order_state = OrderState.CREATED
            payment_status = PaymentStatus.CREATED
        elif event_type == EventType.OVERDUE_INVOICE:
            order_state = OrderState.PAYMENT_PENDING
            payment_status = PaymentStatus.PENDING
        else:
            order_state = OrderState.PAYMENT_FAILED
            payment_status = PaymentStatus.FAILED

        # A duplicate event sometimes presents to us as an ordinary failure.
        observed_event = event_type
        if event_type == EventType.DUPLICATE_PAYMENT and rng.random() < 0.35:
            observed_event = EventType.PAYMENT_FAILURE
        if event_type == EventType.STATE_MISMATCH and stale_snapshot:
            observed_event = EventType.PAYMENT_FAILURE

        code, reason_code, description_text = PROVIDER_ERROR_CODES[failure_reason]
        created = now - timedelta(minutes=minutes_since_event)

        records.append({
            "transaction_id": txn_id,
            "customer_id": customer_id,
            "merchant_id": "mer_demo",
            "order_id": f"ord_syn_{i:06d}",
            "order_reference": f"ORD-{20000 + i}",
            "description": description,
            "amount_paise": amount,
            "currency": "INR",
            # observed
            "event_type": observed_event.value,
            "true_event_type": event_type.value,
            "order_state": order_state.value,
            "payment_status": payment_status.value,
            "failure_reason": failure_reason.value,
            "provider_error_code": code,
            "provider_error_reason": reason_code,
            "provider_error_description": description_text,
            "payment_method": rng.choice(["card", "upi", "netbanking", "wallet"]),
            "customer_segment": segment.value,
            "previous_success_count": successes,
            "previous_failure_count": failures,
            "previous_success_rate": round(successes / (successes + failures), 4)
                                     if (successes + failures) else 0.0,
            "lifetime_value_paise": successes * rng.randint(50_000, 400_000),
            "risk_flags": ["fraud_suspected"] if risk_flagged else [],
            "risk_flagged": risk_flagged,
            "retry_count": retry_count,
            "contacts_last_24h": contacts_last_24h,
            "minutes_since_event": minutes_since_event,
            "minutes_since_last_attempt": minutes_since_last_attempt,
            "checkout_started": observed_event == EventType.CHECKOUT_ABANDONMENT,
            "checkout_completed": False,
            "subscription_active": subscription_active,
            "invoice_days_overdue": invoice_days_overdue,
            "invoice_status": ("overdue" if invoice_days_overdue else None),
            "order_already_paid": observed_paid,
            "order_cancelled": observed_cancelled,
            "order_refunded": observed_refunded,
            "duplicate_of_payment_id": (f"pay_syn_orig_{i:06d}"
                                        if event_type == EventType.DUPLICATE_PAYMENT else None),
            "created_at": created.isoformat(),
            "observed_at": now.isoformat(),
            "stale_snapshot": stale_snapshot,
            **gt,
        })

    return records


def split(records: list[dict], holdout_fraction: float = 0.2) -> tuple[list[dict], list[dict]]:
    """Deterministic 80/20 split.

    Assignment is by hash of the transaction id, not by list position, so the
    split is stable if the generator ever changes ordering, and a record can
    never silently migrate between dev and held-out between runs.
    """
    dev, holdout = [], []
    threshold = int(holdout_fraction * (1 << 20))
    for record in records:
        digest = hashlib.sha256(record["transaction_id"].encode()).digest()
        bucket = int.from_bytes(digest[:3], "big") & 0xFFFFF
        (holdout if bucket < threshold else dev).append(record)
    return dev, holdout
