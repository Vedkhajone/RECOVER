"""Event ingestion — the front door for real merchant data."""
from __future__ import annotations

from recover.enums import CaseStatus, OrderState
from recover.models import Customer, Order, Payment, RecoveryCase


def event(**overrides) -> dict:
    """A Rs 4,999 transient failure on a reliable customer."""
    payload = {
        "customer": {
            "name": "Anita Desai",
            "email": "anita@example.com",
            "contact": "+919812345678",
            "segment": "LOYAL",
            "successful_payments": 9,
            "failed_payments": 1,
        },
        "order_reference": "ORD-90001",
        "description": "Noise Cancelling Earbuds",
        "amount_inr": "4999.00",
        "event_type": "PAYMENT_FAILURE",
        "failure_reason": "BANK_TRANSIENT",
        "process_immediately": True,
        "use_deterministic_engine": True,
    }
    payload.update(overrides)
    return payload


def post(client, **overrides):
    return client.post("/api/ingest/event?merchant_id=mer_test", json=event(**overrides))


def test_ingesting_an_event_creates_customer_order_and_case(client, merchant, db):
    response = post(client)
    assert response.status_code == 201
    body = response.json()

    assert body["created"] is True
    assert body["amount"]["paise"] == 499_900
    assert body["amount"]["display"] == "₹4,999.00"

    customer = db.get(Customer, body["customer_id"])
    assert customer.email == "anita@example.com"
    assert customer.successful_payments == 9

    order = db.get(Order, body["order_id"])
    assert order.reference == "ORD-90001"
    assert order.amount_paise == 499_900

    case = db.get(RecoveryCase, body["case_id"])
    assert case.amount_at_risk_paise == 499_900


def test_an_ingested_case_runs_the_normal_recovery_pipeline(client, merchant, db):
    body = post(client).json()
    case = db.get(RecoveryCase, body["case_id"])
    db.refresh(case)

    # Same engine, same policy, same outcome as a seeded case.
    assert case.ai_recommended_action == "RETRY_PAYMENT"
    assert case.policy_decision == "ALLOW"
    assert case.status == CaseStatus.AWAITING_CUSTOMER
    assert case.recovery_token is not None
    assert "proposed RETRY_PAYMENT" in body["message"]


def test_ingestion_cannot_bypass_policy(client, merchant, db):
    """An ingested event gets no privileges. A risk-flagged customer is refused
    exactly as a seeded one would be."""
    body = post(
        client,
        customer={
            "name": "Flagged Person",
            "email": "flagged@example.com",
            "successful_payments": 1,
            "failed_payments": 9,
            "risk_flagged": True,
        },
        order_reference="ORD-90002",
    ).json()

    case = db.get(RecoveryCase, body["case_id"])
    db.refresh(case)
    assert case.status == CaseStatus.STOPPED
    assert case.retry_count == 0
    assert case.contacts_sent == 0


def test_a_large_amount_is_held_for_approval(client, merchant, db):
    body = post(client, amount_inr="24999.00", order_reference="ORD-90003").json()
    case = db.get(RecoveryCase, body["case_id"])
    db.refresh(case)
    assert case.status == CaseStatus.AWAITING_APPROVAL
    assert case.executed_action is None


def test_reporting_the_same_order_twice_does_not_duplicate_it(client, merchant, db):
    first = post(client).json()
    second = post(client).json()

    assert first["created"] is True
    assert second["created"] is False
    assert second["case_id"] == first["case_id"]
    assert db.query(Order).count() == 1
    assert db.query(RecoveryCase).count() == 1


def test_an_existing_customer_keeps_their_history(client, merchant, db):
    """History is the strongest signal the decision has. Re-reporting a
    customer without it must not silently wipe it."""
    post(client)
    post(client, order_reference="ORD-90004",
         customer={"name": "Anita Desai", "email": "anita@example.com"})

    customers = db.query(Customer).all()
    assert len(customers) == 1
    assert customers[0].successful_payments == 9
    assert customers[0].failed_payments == 1


def test_amounts_are_exact_paise(client, merchant, db):
    """4999.99 is not representable as a float. It must survive intact."""
    body = post(client, amount_inr="4999.99", order_reference="ORD-90005").json()
    assert body["amount"]["paise"] == 499_999


def test_an_amount_finer_than_a_paise_is_rejected(client, merchant):
    response = post(client, amount_inr="4999.999", order_reference="ORD-90006")
    assert response.status_code == 422
    assert "finer than one paise" in response.json()["detail"]


def test_amount_must_be_given_exactly_once(client, merchant):
    both = client.post("/api/ingest/event?merchant_id=mer_test",
                       json={**event(), "amount_paise": 499_900})
    assert both.status_code == 422

    payload = event()
    payload.pop("amount_inr")
    neither = client.post("/api/ingest/event?merchant_id=mer_test", json=payload)
    assert neither.status_code == 422


def test_amount_in_paise_is_accepted(client, merchant, db):
    payload = event(order_reference="ORD-90007")
    payload.pop("amount_inr")
    payload["amount_paise"] = 250_000
    body = client.post("/api/ingest/event?merchant_id=mer_test", json=payload).json()
    assert body["amount"]["paise"] == 250_000


def test_checkout_abandonment_creates_no_payment_row(client, merchant, db):
    """There was no payment attempt, so there must be no payment row - the
    failure taxonomy has nothing to say about a checkout nobody completed."""
    body = post(client, event_type="CHECKOUT_ABANDONMENT", failure_reason="NONE",
                order_reference="ORD-90008", process_immediately=False).json()
    order = db.get(Order, body["order_id"])
    assert order.state == OrderState.CREATED
    assert db.query(Payment).filter_by(order_id=order.id).count() == 0


def test_an_abandoned_checkout_is_recovered_with_a_payment_link(client, merchant, db):
    body = post(client, event_type="CHECKOUT_ABANDONMENT", failure_reason="NONE",
                order_reference="ORD-90009").json()
    case = db.get(RecoveryCase, body["case_id"])
    db.refresh(case)
    # A retry is meaningless here - nothing was ever attempted. A link is the
    # only sensible route, and it puts the order back in flight.
    assert case.ai_recommended_action == "SEND_PAYMENT_LINK"
    assert db.get(Order, body["order_id"]).state == OrderState.PAYMENT_PENDING


def test_an_unknown_merchant_is_a_404(client, merchant):
    response = client.post("/api/ingest/event?merchant_id=mer_nope", json=event())
    assert response.status_code == 404
    assert "seed_demo" in response.json()["detail"]


def test_an_invented_failure_reason_is_rejected(client, merchant):
    response = post(client, failure_reason="THE_VIBES_WERE_OFF")
    assert response.status_code == 422


def test_process_can_be_deferred(client, merchant, db):
    body = post(client, process_immediately=False).json()
    case = db.get(RecoveryCase, body["case_id"])
    assert case.status == CaseStatus.OPEN
    assert case.ai_recommended_action is None


def test_the_ingested_case_is_readable_back(client, merchant):
    body = post(client).json()
    detail = client.get(f"/api/ingest/event/{body['case_id']}").json()
    assert detail["customer_name"] == "Anita Desai"
    assert detail["order_reference"] == "ORD-90001"
    assert len(detail["decision_panel"]["what_policy_allows"]) == 7


def test_an_ingested_case_appears_on_the_dashboard(client, merchant):
    post(client)
    metrics = client.get("/api/dashboard/metrics?merchant_id=mer_test").json()
    assert metrics["total_cases"] == 1
    assert metrics["revenue_at_risk"]["paise"] == 499_900
