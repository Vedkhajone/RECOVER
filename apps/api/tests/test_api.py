"""HTTP surface: dashboard, cases, policy, customer recovery, webhooks."""
from __future__ import annotations

import json

from recover.enums import CaseStatus, OrderState
from recover.models import Order, RecoveryCase


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_config_states_exactly_what_is_wired_up(client):
    """Without this, a demo could pass off the simulator as Razorpay."""
    body = client.get("/api/demo/config").json()
    assert body["provider"]["is_razorpay"] is False
    assert body["provider"]["name"] == "simulator"
    assert body["ai"]["enabled"] is False
    assert body["ai"]["path"] == "fallback-heuristic"
    assert "simulator" in body["provider"]["note"].lower()


def test_dashboard_metrics_start_at_zero(client, merchant):
    body = client.get("/api/dashboard/metrics?merchant_id=mer_test").json()
    assert body["revenue_at_risk"]["paise"] == 0
    assert body["recovered_revenue"]["paise"] == 0
    assert body["recovery_rate"] == 0.0
    assert len(body["trend"]) == 7


def test_dashboard_reflects_a_real_case(client, seeded_case):
    body = client.get("/api/dashboard/metrics?merchant_id=mer_test").json()
    assert body["revenue_at_risk"]["paise"] == 499_900
    assert body["revenue_at_risk"]["display"] == "₹4,999.00"
    assert body["total_cases"] == 1


def test_case_list_and_filters(client, seeded_case):
    rows = client.get("/api/cases?merchant_id=mer_test").json()
    assert len(rows) == 1
    assert rows[0]["customer_name"] == "Priya Sharma"

    assert client.get("/api/cases?merchant_id=mer_test&status=RECOVERED").json() == []
    found = client.get("/api/cases?merchant_id=mer_test&search=ORD-18392").json()
    assert len(found) == 1


def test_case_detail_includes_the_full_decision_panel(client, seeded_case):
    body = client.get("/api/cases/case_test").json()
    panel = body["decision_panel"]
    for key in ("what_happened", "why_it_happened", "what_ai_recommends",
                "why_ai_recommends_it", "what_policy_allows", "what_action_was_taken",
                "result", "recovered_amount"):
        assert key in panel
    assert len(panel["what_policy_allows"]) == 7


def test_a_missing_case_is_a_404(client, merchant):
    assert client.get("/api/cases/case_nope").status_code == 404


def test_processing_a_case_over_http(client, seeded_case):
    body = client.post(
        "/api/cases/case_test/process?use_deterministic_engine=true").json()
    assert body["policy_decision"] == "ALLOW"
    assert body["executed"] == "RETRY_PAYMENT"


def test_the_policy_probe_reports_a_block_without_executing(client, seeded_case, db):
    order = db.get(Order, "ord_test")
    order.state = OrderState.CANCELLED
    db.commit()

    body = client.post("/api/cases/case_test/policy-probe",
                       json={"action": "RETRY_PAYMENT"}).json()
    assert body["decision"] == "BLOCK"
    assert body["code"] == "ORDER_CANCELLED"
    assert body["would_execute"] is False
    # Nothing happened as a side effect of asking.
    assert db.get(RecoveryCase, "case_test").retry_count == 0


def test_the_policy_probe_refuses_an_unknown_action(client, seeded_case):
    body = client.post("/api/cases/case_test/policy-probe",
                       json={"action": "TRANSFER_ALL_FUNDS"}).json()
    assert body["decision"] == "BLOCK"
    assert body["code"] == "UNKNOWN_ACTION"


# --------------------------------------------------------------------------
# Policy screen
# --------------------------------------------------------------------------
def test_policy_round_trips(client, merchant):
    policy = client.get("/api/policy?merchant_id=mer_test").json()
    assert policy["max_auto_retries"] == 2

    policy["max_auto_retries"] = 1
    saved = client.put("/api/policy?merchant_id=mer_test", json=policy).json()
    assert saved["max_auto_retries"] == 1
    assert client.get("/api/policy?merchant_id=mer_test").json()["max_auto_retries"] == 1


def test_a_contradictory_policy_is_rejected(client, merchant):
    """Approval threshold below the automatic limit means the two rules
    disagree; refusing is better than silently picking one."""
    policy = client.get("/api/policy?merchant_id=mer_test").json()
    policy["approval_threshold_paise"] = 100
    response = client.put("/api/policy?merchant_id=mer_test", json=policy)
    assert response.status_code == 422


def test_policy_changes_take_effect_immediately(client, seeded_case):
    """The Policy screen is not decorative: turning the retry limit to zero
    must change the very next decision."""
    policy = client.get("/api/policy?merchant_id=mer_test").json()
    policy["max_auto_retries"] = 0
    client.put("/api/policy?merchant_id=mer_test", json=policy)

    probe = client.post("/api/cases/case_test/policy-probe",
                        json={"action": "RETRY_PAYMENT"}).json()
    assert probe["decision"] == "BLOCK"
    assert probe["code"] == "RETRY_LIMIT_EXCEEDED"


def test_turning_off_payment_links_blocks_them_immediately(client, seeded_case):
    policy = client.get("/api/policy?merchant_id=mer_test").json()
    policy["allow_payment_link_recovery"] = False
    client.put("/api/policy?merchant_id=mer_test", json=policy)
    probe = client.post("/api/cases/case_test/policy-probe",
                        json={"action": "SEND_PAYMENT_LINK"}).json()
    assert probe["code"] == "PAYMENT_LINK_DISABLED"


# --------------------------------------------------------------------------
# Customer recovery flow
# --------------------------------------------------------------------------
def test_customer_recovery_flow_end_to_end(client, seeded_case, db):
    client.post("/api/cases/case_test/process?use_deterministic_engine=true")
    token = db.get(RecoveryCase, "case_test").recovery_token
    assert token

    page = client.get(f"/api/recovery/{token}").json()
    assert page["order_reference"] == "ORD-18392"
    assert page["amount"]["display"] == "₹4,999.00"
    assert page["paid"] is False
    # The customer page must not leak internals.
    assert "root_cause" not in page
    assert "policy_decision" not in page
    assert "ai_reason" not in page

    paid = client.post(f"/api/recovery/{token}/simulate-payment",
                       json={"succeed": True}).json()
    assert paid["succeeded"] is True
    assert paid["webhook"]["status"] == "processed"

    case = db.get(RecoveryCase, "case_test")
    db.refresh(case)
    assert case.status == CaseStatus.RECOVERED
    assert case.recovered_amount_paise == 499_900

    metrics = client.get("/api/dashboard/metrics?merchant_id=mer_test").json()
    assert metrics["recovered_revenue"]["paise"] == 499_900
    assert metrics["successful_interventions"] == 1


def test_a_failed_retry_reopens_the_case_and_recovers_nothing(client, seeded_case, db):
    client.post("/api/cases/case_test/process?use_deterministic_engine=true")
    token = db.get(RecoveryCase, "case_test").recovery_token

    result = client.post(f"/api/recovery/{token}/simulate-payment",
                         json={"succeed": False,
                               "failure_reason": "INSUFFICIENT_FUNDS"}).json()
    assert result["succeeded"] is False
    case = db.get(RecoveryCase, "case_test")
    db.refresh(case)
    assert case.recovered_amount_paise == 0
    assert case.status == CaseStatus.OPEN


def test_an_invalid_recovery_token_is_a_404(client, merchant):
    assert client.get("/api/recovery/not-a-real-token").status_code == 404


def test_paying_twice_is_a_no_op(client, seeded_case, db):
    client.post("/api/cases/case_test/process?use_deterministic_engine=true")
    token = db.get(RecoveryCase, "case_test").recovery_token
    client.post(f"/api/recovery/{token}/simulate-payment", json={"succeed": True})
    again = client.post(f"/api/recovery/{token}/simulate-payment",
                        json={"succeed": True}).json()
    assert again["already_paid"] is True
    db.refresh(db.get(RecoveryCase, "case_test"))
    assert db.get(RecoveryCase, "case_test").recovered_amount_paise == 499_900


# --------------------------------------------------------------------------
# Webhook endpoint
# --------------------------------------------------------------------------
def test_the_webhook_endpoint_rejects_a_bad_signature(client, merchant):
    response = client.post("/api/webhooks/razorpay",
                           content=json.dumps({"event": "payment.captured"}),
                           headers={"X-Razorpay-Signature": "nope"})
    assert response.status_code == 400
    assert response.json()["status"] == "rejected"


def test_a_duplicate_webhook_returns_200_and_says_so(client, seeded_case, db):
    """Razorpay retries on non-200. A duplicate must be acknowledged, not
    rejected, or the provider will keep resending forever."""
    client.post("/api/cases/case_test/process?use_deterministic_engine=true")
    token = db.get(RecoveryCase, "case_test").recovery_token
    paid = client.post(f"/api/recovery/{token}/simulate-payment",
                       json={"succeed": True}).json()

    replay = paid["replay"]
    response = client.post("/api/webhooks/razorpay", content=replay["body"],
                           headers={"X-Razorpay-Signature": replay["signature"],
                                    "X-Razorpay-Event-Id": replay["event_id"]})
    assert response.status_code == 200
    body = response.json()
    assert body["duplicate"] is True
    assert "safely ignored" in body["message"]

    db.refresh(db.get(RecoveryCase, "case_test"))
    assert db.get(RecoveryCase, "case_test").recovered_amount_paise == 499_900


def test_the_webhook_ledger_is_visible(client, seeded_case, db):
    client.post("/api/cases/case_test/process?use_deterministic_engine=true")
    token = db.get(RecoveryCase, "case_test").recovery_token
    client.post(f"/api/recovery/{token}/simulate-payment", json={"succeed": True})

    rows = client.get("/api/webhooks/events").json()
    assert len(rows) == 1
    assert rows[0]["signature_verified"] is True
    assert rows[0]["processed"] is True


# --------------------------------------------------------------------------
# Audit and exceptions
# --------------------------------------------------------------------------
def test_the_audit_trail_records_the_decision_chain(client, seeded_case):
    client.post("/api/cases/case_test/process?use_deterministic_engine=true")
    rows = client.get("/api/audit?case_id=case_test").json()
    actions = {row["action"] for row in rows}
    assert {"AI_DIAGNOSIS", "POLICY_EVALUATION"} <= actions


def test_the_audit_trail_never_contains_a_secret(client, seeded_case):
    client.post("/api/cases/case_test/process?use_deterministic_engine=true")
    blob = json.dumps(client.get("/api/audit").json()).lower()
    for forbidden in ("key_secret", "webhook_secret", "sk-ant", "rzp_live"):
        assert forbidden not in blob


def test_exceptions_endpoint(client, merchant):
    assert client.get("/api/exceptions").json() == []


def test_evaluation_without_a_run_explains_what_to_do(client, merchant):
    response = client.get("/api/evaluation/latest")
    assert response.status_code == 404
    assert "run_evaluation" in response.json()["detail"]


def test_demo_scenarios_are_listed(client, merchant):
    rows = client.get("/api/demo/scenarios").json()
    assert len(rows) == 7
    assert rows[0]["title"] == "Payment retry succeeds"
