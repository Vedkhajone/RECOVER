"""Dataset, ground truth and evaluation metrics.

These guard the honesty of the reported numbers. A bug here would not crash
anything - it would quietly produce a better-looking result than the system
deserves, which is the worst kind of bug in an evaluation harness.
"""
from __future__ import annotations

import pytest

from recover.context import PolicySnapshot
from recover.dataset.evaluate import CaseOutcome, run_case, score
from recover.dataset.generator import generate_records, split
from recover.dataset.ground_truth import truly_at_risk
from recover.dataset.projection import OBSERVABLE_KEYS, context_from_record
from recover.enums import EventType, FailureReason, PolicyDecision, RecoveryAction


# --------------------------------------------------------------------------
# Determinism and shape
# --------------------------------------------------------------------------
def test_generation_is_reproducible():
    a = generate_records(seed=42, size=300)
    b = generate_records(seed=42, size=300)
    assert a == b


def test_a_different_seed_gives_different_data():
    a = generate_records(seed=1, size=200)
    b = generate_records(seed=2, size=200)
    assert a != b


def test_the_split_is_deterministic_and_disjoint():
    records = generate_records(seed=42, size=1000)
    dev1, hold1 = split(records)
    dev2, hold2 = split(records)
    assert [r["transaction_id"] for r in dev1] == [r["transaction_id"] for r in dev2]
    assert [r["transaction_id"] for r in hold1] == [r["transaction_id"] for r in hold2]
    dev_ids = {r["transaction_id"] for r in dev1}
    hold_ids = {r["transaction_id"] for r in hold1}
    assert dev_ids.isdisjoint(hold_ids)
    assert len(dev_ids) + len(hold_ids) == 1000


def test_the_split_is_roughly_eighty_twenty():
    _, holdout = split(generate_records(seed=42, size=4000))
    assert 0.17 < len(holdout) / 4000 < 0.23


def test_the_split_survives_a_change_of_ordering():
    """Assignment is by hash of the id, not list position."""
    records = generate_records(seed=42, size=500)
    _, hold_a = split(records)
    _, hold_b = split(list(reversed(records)))
    assert {r["transaction_id"] for r in hold_a} == {r["transaction_id"] for r in hold_b}


def test_every_record_carries_its_ground_truth():
    for record in generate_records(seed=7, size=200):
        for key in ("ground_truth_at_risk", "ground_truth_recoverable",
                    "ground_truth_best_action", "ground_truth_recoverable_amount_paise",
                    "ground_truth_max_attempts", "ground_truth_effective_actions"):
            assert key in record
        assert record["ground_truth_best_action"] in {a.value for a in RecoveryAction}


# --------------------------------------------------------------------------
# Leakage: the system must never see ground truth
# --------------------------------------------------------------------------
def test_ground_truth_never_reaches_the_context():
    """If a latent field leaked into the context, every metric would be inflated."""
    forbidden = {"ground_truth_at_risk", "ground_truth_recoverable",
                 "ground_truth_best_action", "ground_truth_collectability",
                 "ground_truth_effective_actions", "ground_truth_max_attempts",
                 "true_event_type", "stale_snapshot"}
    assert forbidden.isdisjoint(OBSERVABLE_KEYS)

    record = generate_records(seed=3, size=1)[0]
    ctx = context_from_record(record)
    serialised = ctx.model_dump_json()
    for key in forbidden:
        assert key not in serialised


def test_the_evidence_block_leaks_nothing_either():
    """The evidence dict is what the model literally sees."""
    for record in generate_records(seed=11, size=50):
        evidence = str(context_from_record(record).evidence())
        assert "ground_truth" not in evidence
        assert "stale_snapshot" not in evidence


# --------------------------------------------------------------------------
# Ground-truth correctness
# --------------------------------------------------------------------------
def test_cancelled_and_paid_money_is_never_at_risk():
    for record in generate_records(seed=5, size=2000):
        if record["ground_truth_at_risk"]:
            continue
        # Anything not at risk must have a reason in the latent world.
        assert (
            record["true_event_type"] in (EventType.DUPLICATE_PAYMENT.value,
                                          EventType.STATE_MISMATCH.value)
            or record["ground_truth_recoverable"] is False
        )


def test_unrecoverable_cases_carry_no_recoverable_amount():
    for record in generate_records(seed=5, size=2000):
        if not record["ground_truth_recoverable"]:
            assert record["ground_truth_recoverable_amount_paise"] == 0
        else:
            assert record["ground_truth_recoverable_amount_paise"] == record["amount_paise"]


def test_risk_flagged_cases_are_never_recoverable():
    records = [r for r in generate_records(seed=5, size=3000) if r["risk_flagged"]]
    assert records, "expected some risk-flagged records in the sample"
    assert all(not r["ground_truth_recoverable"] for r in records)


def test_ground_truth_says_stop_when_nothing_is_recoverable():
    for record in generate_records(seed=9, size=1500):
        if not record["ground_truth_recoverable"] and \
                record["true_event_type"] != EventType.STATE_MISMATCH.value:
            assert record["ground_truth_best_action"] == RecoveryAction.STOP.value


def test_a_dead_instrument_is_never_fixed_by_a_retry():
    for record in generate_records(seed=13, size=2000):
        if record["failure_reason"] in (FailureReason.CARD_EXPIRED.value,
                                        FailureReason.MANDATE_INACTIVE.value):
            assert RecoveryAction.RETRY_PAYMENT.value not in \
                record["ground_truth_effective_actions"]


def test_observation_noise_actually_occurs():
    """Detection precision is only meaningful because the observed record can
    disagree with the world. If this stopped happening the metric would be
    trivially 1.0 and would be telling us nothing."""
    records = generate_records(seed=5, size=3000)
    noisy = [r for r in records if r["stale_snapshot"]]
    assert 0.03 < len(noisy) / len(records) < 0.10
    disagreements = [
        r for r in records
        if not r["ground_truth_at_risk"]
        and not (r["order_cancelled"] or r["order_already_paid"] or r["order_refunded"])
        and r["event_type"] not in (EventType.DUPLICATE_PAYMENT.value,
                                    EventType.STATE_MISMATCH.value)
    ]
    assert disagreements, "expected some records where the snapshot hides the truth"


def test_the_dataset_spans_the_policy_thresholds():
    """The evaluation is only interesting if amounts straddle Rs 5,000 and
    Rs 10,000, otherwise the approval rules never fire."""
    records = generate_records(seed=5, size=2000)
    assert any(r["amount_paise"] <= 500_000 for r in records)
    assert any(500_000 < r["amount_paise"] <= 1_000_000 for r in records)
    assert any(r["amount_paise"] > 1_000_000 for r in records)


def test_every_event_class_is_represented():
    records = generate_records(seed=5, size=3000)
    seen = {r["true_event_type"] for r in records}
    assert seen == {e.value for e in EventType}


# --------------------------------------------------------------------------
# Metric arithmetic
# --------------------------------------------------------------------------
def outcome(**kwargs) -> CaseOutcome:
    base = dict(
        transaction_id="txn_1", amount_paise=100_000, detected=True,
        proposed_action=RecoveryAction.RETRY_PAYMENT.value,
        policy_decision=PolicyDecision.ALLOW.value, policy_code="WITHIN_POLICY",
        executed_action=RecoveryAction.RETRY_PAYMENT.value, recovered_paise=0,
        cost_paise=200, contacts=0, ai_path="fallback-heuristic", gt_at_risk=True,
        gt_recoverable=True, gt_best_action=RecoveryAction.RETRY_PAYMENT.value,
    )
    base.update(kwargs)
    return CaseOutcome(**base)


def test_detection_precision_and_recall_arithmetic():
    outcomes = [
        outcome(detected=True, gt_at_risk=True),    # TP
        outcome(detected=True, gt_at_risk=True),    # TP
        outcome(detected=True, gt_at_risk=False),   # FP
        outcome(detected=False, gt_at_risk=True),   # FN
        outcome(detected=False, gt_at_risk=False),  # TN
    ]
    result = score(outcomes)["detection"]
    assert result["true_positives"] == 2
    assert result["false_positives"] == 1
    assert result["false_negatives"] == 1
    assert result["precision"] == pytest.approx(2 / 3, abs=1e-4)
    assert result["recall"] == pytest.approx(2 / 3, abs=1e-4)


def test_recovered_revenue_only_counts_actual_recoveries():
    outcomes = [
        outcome(recovered_paise=100_000),
        outcome(recovered_paise=0, gt_recoverable=False),
    ]
    money = score(outcomes)["money_paise"]
    assert money["revenue_actually_recovered"] == 100_000
    assert money["net_recovered_revenue"] == 100_000 - 400


def test_a_false_positive_intervention_is_counted_and_costed():
    outcomes = [outcome(gt_recoverable=False, contacts=1, cost_paise=250,
                        executed_action=RecoveryAction.SEND_PAYMENT_LINK.value,
                        proposed_action=RecoveryAction.SEND_PAYMENT_LINK.value,
                        gt_best_action=RecoveryAction.STOP.value)]
    result = score(outcomes)
    assert result["safety"]["false_positive_interventions"] == 1
    assert result["safety"]["unnecessary_customer_contacts"] == 1
    assert result["money_paise"]["false_positive_intervention_cost"] == 250


def test_stopping_costs_nothing_and_contacts_nobody():
    outcomes = [outcome(proposed_action=RecoveryAction.STOP.value, executed_action=None,
                        cost_paise=0, contacts=0, gt_recoverable=False,
                        gt_best_action=RecoveryAction.STOP.value)]
    result = score(outcomes)
    assert result["safety"]["interventions"] == 0
    assert result["safety"]["total_customer_contacts"] == 0
    assert result["money_paise"]["intervention_cost"] == 0
    assert result["action_selection"]["accuracy_all_records"] == 1.0


def test_undetected_cases_are_scored_as_missed_not_ignored():
    """A case we never opened is still a case we failed to recover."""
    outcomes = [outcome(detected=False, gt_at_risk=True, gt_recoverable=True,
                        recovered_paise=0, cost_paise=0, executed_action=None,
                        proposed_action=RecoveryAction.STOP.value)]
    result = score(outcomes)
    assert result["detection"]["false_negatives"] == 1
    assert result["recovery_eligibility"]["false_negatives"] == 1
    assert result["money_paise"]["revenue_eligible_for_recovery"] == 100_000
    assert result["money_paise"]["revenue_actually_recovered"] == 0


def test_empty_input_does_not_divide_by_zero():
    result = score([])
    assert result["detection"]["precision"] == 0.0
    assert result["recovery_rate"] == 0.0


# --------------------------------------------------------------------------
# End to end through the real pipeline
# --------------------------------------------------------------------------
def test_run_case_never_recovers_money_ground_truth_says_is_gone():
    """The single most important property of the harness: it cannot credit
    revenue on a case that was never recoverable."""
    policy = PolicySnapshot()
    for record in generate_records(seed=17, size=800):
        result = run_case(record, policy, force_fallback=True)
        if not record["ground_truth_recoverable"]:
            assert result.recovered_paise == 0, record["transaction_id"]


def test_run_case_never_recovers_via_an_ineffective_action():
    policy = PolicySnapshot()
    for record in generate_records(seed=19, size=800):
        result = run_case(record, policy, force_fallback=True)
        if result.recovered_paise > 0:
            assert result.executed_action in record["ground_truth_effective_actions"]
            assert record["retry_count"] < record["ground_truth_max_attempts"]


def test_a_case_that_was_never_detected_costs_nothing():
    policy = PolicySnapshot()
    for record in generate_records(seed=23, size=600):
        result = run_case(record, policy, force_fallback=True)
        if not result.detected:
            assert result.cost_paise == 0
            assert result.contacts == 0
            assert result.recovered_paise == 0


def test_evaluation_is_reproducible_end_to_end():
    from recover.dataset.evaluate import run_evaluation

    a = run_evaluation(seed=31, size=400, force_fallback=True)
    b = run_evaluation(seed=31, size=400, force_fallback=True)
    assert a["holdout"] == b["holdout"]
    assert a["development"] == b["development"]
