"""Webhook verification, idempotency and recovery accounting."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from recover import webhooks
from recover.enums import CaseStatus, OrderState, PaymentStatus
from recover.models import Order, Payment, ReconciliationException, RecoveryCase, WebhookEvent
from recover.providers.simulator import SIMULATED_SECRET, SimulatedPaymentProvider


@pytest.fixture()
def provider():
    return SimulatedPaymentProvider()


def build_event(*, event: str, payment_id: str, order_id: str | None,
                amount: int = 499_900, status: str = "captured",
                error: dict | None = None) -> tuple[bytes, str, str]:
    envelope = {
        "entity": "event",
        "account_id": "acc_test",
        "event": event,
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id, "entity": "payment", "amount": amount,
                    "currency": "INR", "status": status, "order_id": order_id,
                    "method": "card", **(error or {}),
                }
            }
        },
        "created_at": 1756455561,
    }
    body = json.dumps(envelope, separators=(",", ":")).encode()
    signature = hmac.new(SIMULATED_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, signature, f"evt_{payment_id}"


# --------------------------------------------------------------------------
# Signature verification
# --------------------------------------------------------------------------
def test_a_bad_signature_is_rejected_and_never_applied(db, seeded_case, provider):
    body, _, event_id = build_event(event="payment.captured", payment_id="pay_x",
                                    order_id="order_provider_1")
    with pytest.raises(webhooks.WebhookRejected):
        webhooks.handle_delivery(db, body=body, signature="deadbeef",
                                 headers={"x-razorpay-event-id": event_id},
                                 provider=provider)
    # Nothing was recorded as processed, and no money was credited.
    assert db.query(WebhookEvent).count() == 0
    assert db.get(RecoveryCase, "case_test").recovered_amount_paise == 0


def test_a_tampered_body_fails_verification(db, seeded_case, provider):
    body, signature, event_id = build_event(event="payment.captured", payment_id="pay_x",
                                            order_id=None)
    tampered = body.replace(b'"amount":499900', b'"amount":1')
    with pytest.raises(webhooks.WebhookRejected):
        webhooks.handle_delivery(db, body=tampered, signature=signature,
                                 headers={"x-razorpay-event-id": event_id},
                                 provider=provider)


def test_malformed_json_with_a_valid_signature_is_rejected(db, provider):
    body = b"{not json"
    signature = hmac.new(SIMULATED_SECRET.encode(), body, hashlib.sha256).hexdigest()
    with pytest.raises(webhooks.WebhookRejected):
        webhooks.handle_delivery(db, body=body, signature=signature, headers={},
                                 provider=provider)


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------
def test_duplicate_delivery_has_one_business_effect(db, seeded_case, provider):
    order = db.get(Order, "ord_test")
    order.provider_order_id = "order_provider_1"
    seeded_case.retry_count = 1
    db.commit()

    body, signature, event_id = build_event(
        event="payment.captured", payment_id="pay_provider_1",
        order_id="order_provider_1")
    headers = {"x-razorpay-event-id": event_id}

    first = webhooks.handle_delivery(db, body=body, signature=signature,
                                     headers=headers, provider=provider)
    assert first["duplicate"] is False
    assert first["status"] == "processed"

    second = webhooks.handle_delivery(db, body=body, signature=signature,
                                      headers=headers, provider=provider)
    assert second["duplicate"] is True
    assert second["delivery_count"] == 2
    assert "safely ignored" in second["message"]

    third = webhooks.handle_delivery(db, body=body, signature=signature,
                                     headers=headers, provider=provider)
    assert third["delivery_count"] == 3

    # One ledger row, one payment, one credited amount.
    assert db.query(WebhookEvent).count() == 1
    case = db.get(RecoveryCase, "case_test")
    assert case.recovered_amount_paise == 499_900
    assert case.status == CaseStatus.RECOVERED
    captured = [p for p in db.query(Payment).all() if p.status == PaymentStatus.CAPTURED]
    assert len(captured) == 1


def test_deliveries_without_an_event_id_are_deduplicated_by_body_digest(
    db, seeded_case, provider
):
    """Razorpay sends X-Razorpay-Event-Id, but a delivery without it must still
    not be replayed."""
    order = db.get(Order, "ord_test")
    order.provider_order_id = "order_provider_1"
    db.commit()

    body, signature, _ = build_event(event="payment.captured",
                                     payment_id="pay_provider_2",
                                     order_id="order_provider_1")
    first = webhooks.handle_delivery(db, body=body, signature=signature, headers={},
                                     provider=provider)
    second = webhooks.handle_delivery(db, body=body, signature=signature, headers={},
                                      provider=provider)
    assert first["event_id"].startswith("sha256:")
    assert second["duplicate"] is True


# --------------------------------------------------------------------------
# Effects
# --------------------------------------------------------------------------
def test_capture_advances_the_order_and_credits_the_case(db, seeded_case, provider):
    order = db.get(Order, "ord_test")
    order.provider_order_id = "order_provider_1"
    db.commit()

    body, signature, event_id = build_event(event="payment.captured",
                                            payment_id="pay_provider_3",
                                            order_id="order_provider_1")
    result = webhooks.handle_delivery(db, body=body, signature=signature,
                                      headers={"x-razorpay-event-id": event_id},
                                      provider=provider)
    assert "recovered" in result["effect"]
    db.refresh(order)
    assert order.state == OrderState.COMPLETED
    case = db.get(RecoveryCase, "case_test")
    assert case.status == CaseStatus.RECOVERED
    assert case.closed_at is not None


def test_failure_reopens_the_case_for_re_evaluation(db, seeded_case, provider):
    order = db.get(Order, "ord_test")
    order.provider_order_id = "order_provider_1"
    seeded_case.retry_count = 1
    seeded_case.status = CaseStatus.AWAITING_CUSTOMER
    db.commit()

    body, signature, event_id = build_event(
        event="payment.failed", payment_id="pay_provider_4",
        order_id="order_provider_1", status="failed",
        error={"error_code": "BAD_REQUEST_ERROR", "error_reason": "insufficient_funds",
               "error_description": "Your account does not have sufficient balance"})
    webhooks.handle_delivery(db, body=body, signature=signature,
                             headers={"x-razorpay-event-id": event_id}, provider=provider)

    case = db.get(RecoveryCase, "case_test")
    assert case.status == CaseStatus.OPEN
    assert case.recovered_amount_paise == 0
    db.refresh(order)
    assert order.state == OrderState.PAYMENT_FAILED


def test_capture_for_an_unknown_order_raises_an_exception_rather_than_guessing(
    db, provider
):
    body, signature, event_id = build_event(event="payment.captured",
                                            payment_id="pay_orphan",
                                            order_id="order_nobody_knows")
    result = webhooks.handle_delivery(db, body=body, signature=signature,
                                      headers={"x-razorpay-event-id": event_id},
                                      provider=provider)
    assert "ORPHAN_PAYMENT" in result["effect"]
    exception = db.query(ReconciliationException).one()
    assert exception.kind == "ORPHAN_PAYMENT"
    assert exception.amount_paise == 499_900


def test_capture_on_a_cancelled_order_does_not_force_the_state(db, seeded_case, provider):
    """Money arriving for a cancelled order is a human's problem, not something
    the system should paper over by mutating state."""
    order = db.get(Order, "ord_test")
    order.provider_order_id = "order_provider_1"
    order.state = OrderState.CANCELLED
    db.commit()

    body, signature, event_id = build_event(event="payment.captured",
                                            payment_id="pay_provider_5",
                                            order_id="order_provider_1")
    result = webhooks.handle_delivery(db, body=body, signature=signature,
                                      headers={"x-razorpay-event-id": event_id},
                                      provider=provider)
    assert "state left untouched" in result["effect"]
    db.refresh(order)
    assert order.state == OrderState.CANCELLED
    assert db.query(ReconciliationException).filter_by(
        kind="CAPTURE_ON_INVALID_STATE").count() == 1


def test_the_notes_field_links_a_payment_back_to_its_case(db, seeded_case, provider):
    """Even when the provider order id does not match, our own case id in
    `notes` finds the right order."""
    envelope = {
        "entity": "event", "event": "payment.captured", "contains": ["payment"],
        "payload": {"payment": {"entity": {
            "id": "pay_via_notes", "entity": "payment", "amount": 499_900,
            "currency": "INR", "status": "captured", "order_id": "order_unknown",
            "method": "upi", "notes": {"recover_case_id": "case_test"},
        }}},
        "created_at": 1756455561,
    }
    body = json.dumps(envelope, separators=(",", ":")).encode()
    signature = hmac.new(SIMULATED_SECRET.encode(), body, hashlib.sha256).hexdigest()
    result = webhooks.handle_delivery(db, body=body, signature=signature,
                                      headers={"x-razorpay-event-id": "evt_notes"},
                                      provider=provider)
    assert "recovered" in result["effect"]
