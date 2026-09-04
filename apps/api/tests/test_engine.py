"""Case lifecycle: diagnose -> validate -> act -> observe -> recover or stop.

These run the real orchestrator against a real database and the payment
simulator, so they cover the wiring between the pieces, not just the pieces.
"""
from __future__ import annotations

import pytest

from recover import engine, executor
from recover.context import context_from_case
from recover.enums import (
    CaseStatus,
    EventType,
    FailureReason,
    OrderState,
    PaymentStatus,
    PolicyDecision,
    RecoveryAction,
)
from recover.models import (
    AuditLog,
    Customer,
    Order,
    Payment,
    ReconciliationException,
    RecoveryAttempt,
    RecoveryCase,
)


def process(db, case_id="case_test"):
    return engine.process_case(db, case_id, force_fallback=True)


# --------------------------------------------------------------------------
# Scenario 1: a recoverable case is acted on
# --------------------------------------------------------------------------
def test_recoverable_case_opens_a_retry_request(db, seeded_case):
    result = process(db)
    assert result["ai_action"] == RecoveryAction.RETRY_PAYMENT.value
    assert result["policy_decision"] == PolicyDecision.ALLOW.value
    assert result["executed"] == RecoveryAction.RETRY_PAYMENT.value

    case = db.get(RecoveryCase, "case_test")
    assert case.status == CaseStatus.AWAITING_CUSTOMER
    assert case.retry_count == 1
    assert case.recovery_token is not None
    # The order is back in flight, and a provider order exists.
    order = db.get(Order, "ord_test")
    assert order.state == OrderState.PAYMENT_PENDING
    assert order.provider_order_id is not None


def test_processing_writes_a_full_timeline_and_audit_trail(db, seeded_case):
    process(db)
    case = db.get(RecoveryCase, "case_test")
    titles = [event.title for event in case.events]
    assert any("detected revenue at risk" in t for t in titles)
    assert any("diagnosis" in t.lower() for t in titles)
    assert any("Policy evaluated" in t for t in titles)
    assert any("Recovery request created" in t for t in titles)

    actions = {row.action for row in db.query(AuditLog).all()}
    assert {"AI_DIAGNOSIS", "POLICY_EVALUATION", "CREATE_PAYMENT_RETRY_REQUEST"} <= actions


def test_the_diagnosis_records_which_path_produced_it(db, seeded_case):
    process(db)
    case = db.get(RecoveryCase, "case_test")
    assert case.ai_path == "fallback-heuristic"
    assert case.ai_model is None
    assert case.root_cause
    assert case.ai_reason
    assert case.recovery_probability > 0


def test_the_policy_matrix_is_stored_for_every_action(db, seeded_case):
    process(db)
    case = db.get(RecoveryCase, "case_test")
    matrix = case.ai_evidence["policy_matrix"]
    assert set(matrix) == {a.value for a in RecoveryAction}


# --------------------------------------------------------------------------
# Scenario 2: the system stops
# --------------------------------------------------------------------------
def test_case_stops_once_every_budget_is_spent(db, seeded_case):
    seeded_case.retry_count = 2
    seeded_case.contacts_sent = 2
    db.commit()

    result = process(db)
    assert result["ai_action"] == RecoveryAction.STOP.value
    case = db.get(RecoveryCase, "case_test")
    assert case.status == CaseStatus.STOPPED
    assert case.closed_at is not None


def test_a_closed_case_is_not_reprocessed(db, seeded_case):
    seeded_case.status = CaseStatus.RECOVERED
    db.commit()
    result = process(db)
    assert "skipped" in result


# --------------------------------------------------------------------------
# Scenario 3: approval
# --------------------------------------------------------------------------
def test_a_large_amount_is_held_for_merchant_approval(db, seeded_case):
    order = db.get(Order, "ord_test")
    order.amount_paise = 1_249_900
    seeded_case.amount_at_risk_paise = 1_249_900
    db.commit()

    result = process(db)
    assert result["policy_decision"] == PolicyDecision.REQUIRE_APPROVAL.value
    case = db.get(RecoveryCase, "case_test")
    assert case.status == CaseStatus.AWAITING_APPROVAL
    # Crucially: nothing was executed while it waits.
    assert case.executed_action is None
    assert case.retry_count == 0
    assert db.query(RecoveryAttempt).count() == 0


def test_merchant_approval_executes_the_held_action(db, seeded_case):
    order = db.get(Order, "ord_test")
    order.amount_paise = 1_249_900
    seeded_case.amount_at_risk_paise = 1_249_900
    db.commit()
    process(db)

    result = engine.approve_case(db, "case_test")
    assert result["approved"] is True
    case = db.get(RecoveryCase, "case_test")
    assert case.status == CaseStatus.AWAITING_CUSTOMER
    assert case.executed_action == RecoveryAction.RETRY_PAYMENT.value
    assert case.retry_count == 1


def test_approval_cannot_revive_a_case_that_became_blocked(db, seeded_case):
    """Approval satisfies REQUIRE_APPROVAL. It is not a master key."""
    order = db.get(Order, "ord_test")
    order.amount_paise = 1_249_900
    seeded_case.amount_at_risk_paise = 1_249_900
    db.commit()
    process(db)

    # The customer cancels while the case sits in the approval queue.
    order.state = OrderState.CANCELLED
    db.commit()

    result = engine.approve_case(db, "case_test")
    assert result["approved"] is False
    case = db.get(RecoveryCase, "case_test")
    assert case.status == CaseStatus.BLOCKED
    assert case.executed_action is None


def test_declining_an_approval_stops_the_case(db, seeded_case):
    order = db.get(Order, "ord_test")
    order.amount_paise = 1_249_900
    seeded_case.amount_at_risk_paise = 1_249_900
    db.commit()
    process(db)
    engine.reject_case(db, "case_test")
    assert db.get(RecoveryCase, "case_test").status == CaseStatus.STOPPED


# --------------------------------------------------------------------------
# Scenario 4 and 7: blocked
# --------------------------------------------------------------------------
def test_a_cancelled_order_blocks_recovery_end_to_end(db, seeded_case):
    order = db.get(Order, "ord_test")
    order.state = OrderState.CANCELLED
    db.commit()

    result = process(db)
    # The engine settles on STOP, because stopping is the only thing policy
    # permits here. What matters is that no money moved and nobody was
    # contacted - not the label on the terminal action.
    assert result["executed"] == RecoveryAction.STOP.value
    case = db.get(RecoveryCase, "case_test")
    assert case.status in (CaseStatus.STOPPED, CaseStatus.BLOCKED)
    assert case.retry_count == 0
    assert case.contacts_sent == 0
    assert order.provider_order_id is None
    # Every outward action was refused, and the refusals are on the record.
    matrix = case.ai_evidence["policy_matrix"]
    assert matrix["RETRY_PAYMENT"]["decision"] == "BLOCK"
    assert matrix["RETRY_PAYMENT"]["code"] == "ORDER_CANCELLED"
    assert matrix["SEND_PAYMENT_LINK"]["decision"] == "BLOCK"


def test_the_action_tier_refuses_a_forbidden_action_even_if_called_directly(
    db, seeded_case
):
    """The orchestrator gates on policy. If that gate were removed, the action
    tier must still refuse - which is what this asserts."""
    order = db.get(Order, "ord_test")
    order.state = OrderState.CANCELLED
    db.commit()
    policy = engine.get_policy(db, "mer_test")
    ctx = context_from_case(seeded_case, policy)

    with pytest.raises(executor.PolicyViolation) as excinfo:
        executor.create_payment_retry_request(db, seeded_case, ctx)
    assert "ORDER_CANCELLED" in str(excinfo.value)
    assert seeded_case.retry_count == 0

    # The refusal is auditable as a security event.
    db.flush()
    refusals = db.query(AuditLog).filter_by(action="ACTION_TIER_REFUSED").all()
    assert len(refusals) == 1


def test_a_risk_flagged_customer_is_never_contacted(db, seeded_case):
    customer = db.get(Customer, "cus_test")
    customer.risk_flagged = True
    db.commit()
    process(db)
    case = db.get(RecoveryCase, "case_test")
    assert case.contacts_sent == 0
    assert case.retry_count == 0
    assert case.status == CaseStatus.STOPPED


# --------------------------------------------------------------------------
# Scenario 6: reconciliation
# --------------------------------------------------------------------------
def test_state_mismatch_is_detected_and_routed_to_reconciliation(db, merchant):
    customer = Customer(id="cus_x", merchant_id="mer_test", name="Kabir",
                        email="kabir@example.com")
    order = Order(id="ord_x", merchant_id="mer_test", customer_id="cus_x",
                  reference="ORD-1", description="Annual plan", amount_paise=1_199_900,
                  state=OrderState.PAYMENT_PENDING)
    payment = Payment(id="pay_x", order_id="ord_x", amount_paise=1_199_900,
                      status=PaymentStatus.CAPTURED, provider_payment_id="pay_prov_x")
    db.add_all([customer, order, payment])
    db.commit()

    found = engine.detect_state_mismatches(db, "mer_test")
    assert len(found) == 1
    case_id = found[0]["case_id"]

    engine.process_case(db, case_id, force_fallback=True)
    case = db.get(RecoveryCase, case_id)
    assert case.status == CaseStatus.RECONCILIATION
    assert db.query(ReconciliationException).count() == 1


def test_the_reconciliation_sweep_is_idempotent(db, merchant):
    customer = Customer(id="cus_x", merchant_id="mer_test", name="Kabir",
                        email="kabir@example.com")
    order = Order(id="ord_x", merchant_id="mer_test", customer_id="cus_x",
                  reference="ORD-1", description="Annual plan", amount_paise=1_199_900,
                  state=OrderState.PAYMENT_PENDING)
    payment = Payment(id="pay_x", order_id="ord_x", amount_paise=1_199_900,
                      status=PaymentStatus.CAPTURED)
    db.add_all([customer, order, payment])
    db.commit()

    first = engine.detect_state_mismatches(db, "mer_test")
    second = engine.detect_state_mismatches(db, "mer_test")
    assert first[0]["case_id"] == second[0]["case_id"]
    assert db.query(RecoveryCase).count() == 1


# --------------------------------------------------------------------------
# Case creation
# --------------------------------------------------------------------------
def test_opening_a_case_twice_for_the_same_order_returns_the_same_case(db, seeded_case):
    again = engine.open_case(
        db, merchant_id="mer_test", customer_id="cus_test",
        event_type=EventType.PAYMENT_FAILURE, amount_paise=499_900, order_id="ord_test")
    assert again.id == "case_test"
    assert db.query(RecoveryCase).count() == 1


def test_a_new_case_opens_once_the_previous_one_is_closed(db, seeded_case):
    seeded_case.status = CaseStatus.STOPPED
    db.commit()
    fresh = engine.open_case(
        db, merchant_id="mer_test", customer_id="cus_test",
        event_type=EventType.PAYMENT_FAILURE, amount_paise=499_900, order_id="ord_test")
    assert fresh.id != "case_test"


# --------------------------------------------------------------------------
# Batch
# --------------------------------------------------------------------------
def test_the_batch_reports_what_it_did(db, seeded_case):
    summary = engine.run_recovery_batch(db, merchant_id="mer_test", force_fallback=True)
    assert summary["processed"] == 1
    assert summary["executed"] == 1
    assert summary["errors"] == 0


def test_a_failing_case_does_not_abort_the_whole_batch(db, seeded_case, monkeypatch):
    """One bad case must not cost the merchant every other recovery in the run."""
    broken = RecoveryCase(
        id="case_broken", merchant_id="mer_test", customer_id="cus_test",
        order_id=None, event_type=EventType.PAYMENT_FAILURE, amount_at_risk_paise=1000)
    db.add(broken)
    db.commit()

    original = engine.process_case
    def flaky(session, case_id, **kwargs):
        if case_id == "case_broken":
            raise RuntimeError("simulated failure")
        return original(session, case_id, **kwargs)

    monkeypatch.setattr(engine, "process_case", flaky)
    summary = engine.run_recovery_batch(db, merchant_id="mer_test", force_fallback=True)
    assert summary["errors"] == 1
    assert summary["executed"] == 1
