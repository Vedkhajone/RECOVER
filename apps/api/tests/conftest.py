"""Test fixtures.

The database URL is set before `recover` is imported for the first time,
because recover.db builds its engine at import time. Every test gets a fresh
schema in a temporary file so tests never see each other's rows.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="recover-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
# Force the deterministic paths: tests must never call a real API.
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["RAZORPAY_KEY_ID"] = ""
os.environ["RAZORPAY_KEY_SECRET"] = ""

from recover import models  # noqa: E402,F401
from recover.context import (  # noqa: E402
    CustomerContext,
    PolicySnapshot,
    RecoveryContext,
)
from recover.db import Base, SessionLocal, engine  # noqa: E402
from recover.enums import (  # noqa: E402
    CustomerSegment,
    EventType,
    FailureReason,
    OrderState,
    PaymentStatus,
)
from recover.providers.simulator import reset_provider  # noqa: E402


@pytest.fixture()
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    reset_provider()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def merchant(db):
    merchant = models.Merchant(id="mer_test", name="Test Merchant")
    db.add(merchant)
    db.add(models.MerchantPolicy(merchant_id="mer_test"))
    db.commit()
    return merchant


@pytest.fixture()
def policy(db, merchant):
    return db.get(models.MerchantPolicy, "mer_test")


def make_context(**overrides) -> RecoveryContext:
    """A Rs 4,999 transient bank failure on a reliable customer.

    This is the canonical happy case: everything in the spec's worked example
    holds, so a test that changes one field is testing exactly that field.
    """
    customer = CustomerContext(
        customer_id="cus_test",
        segment=CustomerSegment.LOYAL,
        successful_payments=8,
        failed_payments=1,
        lifetime_value_paise=2_000_000,
        risk_flagged=False,
    )
    base = {
        "case_id": "case_test",
        "merchant_id": "mer_test",
        "event_type": EventType.PAYMENT_FAILURE,
        "amount_paise": 499_900,
        "order_id": "ord_test",
        "order_reference": "ORD-18392",
        "order_description": "Wireless Headphones",
        "order_state": OrderState.PAYMENT_FAILED,
        "payment_id": "pay_test",
        "payment_status": PaymentStatus.FAILED,
        "failure_reason": FailureReason.BANK_TRANSIENT,
        "customer": customer,
        "retry_count": 0,
        "contacts_last_24h": 0,
        "minutes_since_event": 2,
        "minutes_since_last_attempt": None,
        "policy": PolicySnapshot(),
    }
    if "customer" in overrides:
        base["customer"] = overrides.pop("customer")
    base.update(overrides)
    return RecoveryContext(**base)


@pytest.fixture()
def ctx():
    return make_context()


@pytest.fixture()
def seeded_case(db, merchant):
    """A live case in the database, ready to be processed."""
    customer = models.Customer(
        id="cus_test", merchant_id="mer_test", name="Priya Sharma",
        email="priya@example.com", contact="+919876543210",
        segment=CustomerSegment.LOYAL, successful_payments=8, failed_payments=1,
    )
    order = models.Order(
        id="ord_test", merchant_id="mer_test", customer_id="cus_test",
        reference="ORD-18392", description="Wireless Headphones",
        amount_paise=499_900, state=OrderState.PAYMENT_FAILED,
    )
    payment = models.Payment(
        id="pay_test", order_id="ord_test", amount_paise=499_900,
        status=PaymentStatus.FAILED, failure_reason=FailureReason.BANK_TRANSIENT,
        provider_error_code="GATEWAY_ERROR",
        provider_error_description="Payment processing failed at the bank",
    )
    case = models.RecoveryCase(
        id="case_test", merchant_id="mer_test", customer_id="cus_test",
        order_id="ord_test", payment_id="pay_test",
        event_type=EventType.PAYMENT_FAILURE, amount_at_risk_paise=499_900,
    )
    db.add_all([customer, order, payment, case])
    db.flush()
    # engine.open_case() always writes this first timeline entry, so the
    # fixture writes it too - otherwise tests exercise a shape the real system
    # never produces.
    db.add(models.CaseEvent(
        case_id="case_test", actor="system",
        title="RECOVER detected revenue at risk",
        detail="Payment of Rs 4,999.00 failed (BANK_TRANSIENT)."))
    db.commit()
    return case


@pytest.fixture()
def client(db):
    """FastAPI test client sharing the test session."""
    from fastapi.testclient import TestClient

    from recover.db import get_db
    from recover.main import app

    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
