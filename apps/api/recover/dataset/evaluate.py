"""Evaluation harness.

Runs the *shipped* decision path - the same detector, the same agent, the same
policy engine - over synthetic records, then scores the result against ground
truth the system never saw.

Two words appear throughout the output and they mean different things:

  PREDICTED - what RECOVER decided from observable evidence.
  ACTUAL    - what the simulated world says would really have happened.

Recovered revenue is an ACTUAL figure. It is credited only when the system took
an action that ground truth says would genuinely have collected the money, on a
case ground truth says was genuinely recoverable, within the number of attempts
ground truth says would ever have worked. An action that looked sensible but
could not have worked earns nothing.
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field

from ..agent import llm
from ..agent.fallback import _preference_order
from ..context import PolicySnapshot
from ..detection import is_revenue_at_risk
from ..enums import CONTACT_ACTIONS, PolicyDecision, RecoveryAction
from ..policy import evaluate as evaluate_policy
from ..scoring import intervention_cost_paise
from .generator import DEFAULT_SEED, DEFAULT_SIZE, generate_records, split
from .ground_truth import ACTIVE_ACTIONS
from .projection import context_from_record


@dataclass
class CaseOutcome:
    transaction_id: str
    amount_paise: int
    detected: bool
    proposed_action: str
    policy_decision: str
    policy_code: str
    executed_action: str | None
    recovered_paise: int
    cost_paise: int
    contacts: int
    ai_path: str
    gt_at_risk: bool
    gt_recoverable: bool
    gt_best_action: str
    note: str = ""
    #: First-preference action for this case, and whether policy refused it.
    first_choice_action: str = ""
    first_choice_blocked: bool = False


@dataclass
class Metrics:
    counts: Counter = field(default_factory=Counter)
    money: Counter = field(default_factory=Counter)


def fallback_preference(ctx) -> RecoveryAction:
    """The engine's unconstrained first choice, ignoring policy."""
    from ..scoring import recovery_probability

    order = _preference_order(ctx, recovery_probability(ctx))
    return order[0] if order else RecoveryAction.STOP


def _rate(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def run_case(record: dict, policy: PolicySnapshot, *, force_fallback: bool) -> CaseOutcome:
    """One record through the real pipeline."""
    ctx = context_from_record(record, policy)
    gt_recoverable = bool(record["ground_truth_recoverable"])
    gt_at_risk = bool(record["ground_truth_at_risk"])
    gt_best = record["ground_truth_best_action"]
    gt_effective = set(record["ground_truth_effective_actions"])
    gt_max_attempts = int(record["ground_truth_max_attempts"])

    detected, detect_reason = is_revenue_at_risk(ctx)
    if not detected:
        # Not flagged means no case, no cost, no contact.
        return CaseOutcome(
            transaction_id=record["transaction_id"], amount_paise=ctx.amount_paise,
            detected=False, proposed_action=RecoveryAction.STOP.value,
            policy_decision="NOT_APPLICABLE", policy_code="NOT_AT_RISK",
            executed_action=None, recovered_paise=0, cost_paise=0, contacts=0,
            ai_path="n/a", gt_at_risk=gt_at_risk, gt_recoverable=gt_recoverable,
            gt_best_action=gt_best, note=detect_reason,
        )

    # The action the engine would reach for first, before policy is consulted.
    # Scoring this separately is the only way to report a meaningful
    # "unsafe actions blocked" figure: the deterministic engine consults the
    # policy menu while choosing, so by construction it almost never *submits*
    # a blocked action. What policy actually prevents is this first choice.
    first_choice = fallback_preference(ctx)
    first_verdict = evaluate_policy(ctx, first_choice)

    envelope = llm.diagnose(ctx, force_fallback=force_fallback)
    proposed = envelope.decision.recommended_action
    verdict = evaluate_policy(ctx, proposed)

    executed: str | None = None
    recovered = cost = contacts = 0
    note = verdict.code

    if verdict.decision == PolicyDecision.ALLOW:
        executed = proposed.value
        cost = intervention_cost_paise(ctx, proposed)
        if proposed in CONTACT_ACTIONS:
            contacts = 1
        if proposed in ACTIVE_ACTIONS:
            # ACTUAL outcome: did the world let this action collect the money?
            if (gt_recoverable and proposed.value in gt_effective
                    and ctx.retry_count < gt_max_attempts):
                recovered = ctx.amount_paise
            elif gt_recoverable and proposed.value not in gt_effective:
                note = "ACTION_INEFFECTIVE_FOR_FAILURE_CLASS"
            elif gt_recoverable and ctx.retry_count >= gt_max_attempts:
                note = "PAST_USEFUL_ATTEMPT_LIMIT"
            else:
                note = "INTERVENED_ON_UNRECOVERABLE_CASE"
    elif verdict.decision == PolicyDecision.REQUIRE_APPROVAL:
        note = f"HELD_FOR_APPROVAL:{verdict.code}"
    else:
        note = f"BLOCKED:{verdict.code}"

    return CaseOutcome(
        transaction_id=record["transaction_id"], amount_paise=ctx.amount_paise,
        detected=True, proposed_action=proposed.value,
        policy_decision=verdict.decision.value, policy_code=verdict.code,
        executed_action=executed, recovered_paise=recovered, cost_paise=cost,
        contacts=contacts, ai_path=envelope.path,
        first_choice_action=first_choice.value,
        first_choice_blocked=first_verdict.decision == PolicyDecision.BLOCK,
        gt_at_risk=gt_at_risk,
        gt_recoverable=gt_recoverable, gt_best_action=gt_best, note=note,
    )


def score(outcomes: list[CaseOutcome]) -> dict:
    """Turn per-case outcomes into the reported metric set."""
    c = Counter()
    m = Counter()

    for o in outcomes:
        c["records"] += 1

        # --- detection (PREDICTED at-risk vs ACTUAL at-risk) ---------------
        if o.detected and o.gt_at_risk:
            c["detect_tp"] += 1
        elif o.detected and not o.gt_at_risk:
            c["detect_fp"] += 1
        elif not o.detected and o.gt_at_risk:
            c["detect_fn"] += 1
        else:
            c["detect_tn"] += 1

        # --- recovery eligibility ------------------------------------------
        # PREDICTED eligible = we judged the case worth an active intervention
        # and policy did not refuse outright.
        predicted_eligible = (
            o.detected
            and o.proposed_action in {a.value for a in ACTIVE_ACTIONS}
            and o.policy_decision in (PolicyDecision.ALLOW.value,
                                      PolicyDecision.REQUIRE_APPROVAL.value)
        )
        if predicted_eligible and o.gt_recoverable:
            c["elig_tp"] += 1
        elif predicted_eligible and not o.gt_recoverable:
            c["elig_fp"] += 1
        elif not predicted_eligible and o.gt_recoverable:
            c["elig_fn"] += 1
        else:
            c["elig_tn"] += 1

        # --- action selection ------------------------------------------------
        if o.proposed_action == o.gt_best_action:
            c["action_correct"] += 1
        if o.detected:
            c["action_scored_detected"] += 1
            if o.proposed_action == o.gt_best_action:
                c["action_correct_detected"] += 1

        # --- money -----------------------------------------------------------
        if o.gt_at_risk:
            m["revenue_at_risk"] += o.amount_paise
        if o.gt_recoverable:
            m["revenue_eligible"] += o.amount_paise
        m["revenue_recovered"] += o.recovered_paise
        m["intervention_cost"] += o.cost_paise
        c["contacts"] += o.contacts

        # --- safety ----------------------------------------------------------
        if o.executed_action and o.executed_action in {a.value for a in ACTIVE_ACTIONS}:
            c["interventions"] += 1
            if not o.gt_recoverable:
                c["false_positive_interventions"] += 1
                m["false_positive_cost"] += o.cost_paise
                if o.contacts:
                    c["unnecessary_contacts"] += o.contacts
        if o.policy_decision == PolicyDecision.BLOCK.value:
            c["blocked_submitted_actions"] += 1
        if o.first_choice_blocked:
            c["blocked"] += 1
            # A block is "unsafe action prevented" when ground truth says the
            # right move was to stop, or the money was never at risk.
            if o.gt_best_action == RecoveryAction.STOP.value or not o.gt_at_risk:
                c["blocked_unsafe_actions"] += 1
        if o.policy_decision == PolicyDecision.REQUIRE_APPROVAL.value:
            c["awaiting_approval"] += 1
        if o.note.startswith("HELD_FOR_APPROVAL") or o.policy_code == "REQUIRES_RECONCILIATION":
            c["unresolved_exceptions"] += 1
        if o.proposed_action == RecoveryAction.STOP.value and o.detected:
            c["stopped"] += 1
        if o.proposed_action == RecoveryAction.ESCALATE_TO_MERCHANT.value:
            c["escalated"] += 1

    recovered = m["revenue_recovered"]
    cost = m["intervention_cost"]

    return {
        "dataset": {"records": c["records"]},
        "detection": {
            "true_positives": c["detect_tp"],
            "false_positives": c["detect_fp"],
            "false_negatives": c["detect_fn"],
            "true_negatives": c["detect_tn"],
            "precision": _rate(c["detect_tp"], c["detect_tp"] + c["detect_fp"]),
            "recall": _rate(c["detect_tp"], c["detect_tp"] + c["detect_fn"]),
        },
        "recovery_eligibility": {
            "true_positives": c["elig_tp"],
            "false_positives": c["elig_fp"],
            "false_negatives": c["elig_fn"],
            "precision": _rate(c["elig_tp"], c["elig_tp"] + c["elig_fp"]),
            "recall": _rate(c["elig_tp"], c["elig_tp"] + c["elig_fn"]),
        },
        "action_selection": {
            "accuracy_all_records": _rate(c["action_correct"], c["records"]),
            "accuracy_detected_only": _rate(c["action_correct_detected"],
                                            c["action_scored_detected"]),
            "correct": c["action_correct"],
        },
        "money_paise": {
            "total_revenue_at_risk": m["revenue_at_risk"],
            "revenue_eligible_for_recovery": m["revenue_eligible"],
            "revenue_actually_recovered": recovered,
            "intervention_cost": cost,
            "net_recovered_revenue": recovered - cost,
            "false_positive_intervention_cost": m["false_positive_cost"],
        },
        "money_inr": {
            "total_revenue_at_risk": round(m["revenue_at_risk"] / 100, 2),
            "revenue_eligible_for_recovery": round(m["revenue_eligible"] / 100, 2),
            "revenue_actually_recovered": round(recovered / 100, 2),
            "intervention_cost": round(cost / 100, 2),
            "net_recovered_revenue": round((recovered - cost) / 100, 2),
            "false_positive_intervention_cost": round(m["false_positive_cost"] / 100, 2),
        },
        "recovery_rate": _rate(recovered, m["revenue_eligible"]),
        "safety": {
            "interventions": c["interventions"],
            "false_positive_interventions": c["false_positive_interventions"],
            "unnecessary_customer_contacts": c["unnecessary_contacts"],
            "total_customer_contacts": c["contacts"],
            "policy_refusals": c["blocked"],
            "blocked_unsafe_actions": c["blocked_unsafe_actions"],
            "blocked_submitted_actions": c["blocked_submitted_actions"],
            "cases_stopped": c["stopped"],
            "cases_escalated": c["escalated"],
            "cases_awaiting_approval": c["awaiting_approval"],
            "unresolved_exceptions": c["unresolved_exceptions"],
        },
    }


def exception_samples(outcomes: list[CaseOutcome], limit: int = 25) -> list[dict]:
    """Cases the system could not confidently resolve. Shown, not hidden."""
    interesting = [
        o for o in outcomes
        if o.note.startswith("HELD_FOR_APPROVAL")
        or o.policy_code == "REQUIRES_RECONCILIATION"
        or o.note == "ACTION_INEFFECTIVE_FOR_FAILURE_CLASS"
        or o.note == "INTERVENED_ON_UNRECOVERABLE_CASE"
        or (o.detected and not o.gt_at_risk)
        or (not o.detected and o.gt_at_risk)
    ]
    interesting.sort(key=lambda o: -o.amount_paise)
    return [
        {
            "transaction_id": o.transaction_id,
            "amount_inr": round(o.amount_paise / 100, 2),
            "detected": o.detected,
            "proposed_action": o.proposed_action,
            "policy_decision": o.policy_decision,
            "policy_code": o.policy_code,
            "ground_truth_best_action": o.gt_best_action,
            "ground_truth_recoverable": o.gt_recoverable,
            "issue": o.note,
        }
        for o in interesting[:limit]
    ]


def run_evaluation(*, seed: int = DEFAULT_SEED, size: int = DEFAULT_SIZE,
                   force_fallback: bool = True, policy: PolicySnapshot | None = None,
                   limit_records: int | None = None) -> dict:
    """Full evaluation over both splits.

    `force_fallback=True` (the default) runs the deterministic decision engine.
    That is what the reported headline numbers use, because scoring 10,000
    records through an LLM is neither affordable nor reproducible. Pass
    `force_fallback=False` with `limit_records` to score an LLM sample.
    """
    policy = policy or PolicySnapshot()
    records = generate_records(seed=seed, size=size)
    dev, holdout = split(records)
    if limit_records:
        dev = dev[:limit_records]
        holdout = holdout[:limit_records]

    started = time.perf_counter()
    dev_outcomes = [run_case(r, policy, force_fallback=force_fallback) for r in dev]
    holdout_outcomes = [run_case(r, policy, force_fallback=force_fallback) for r in holdout]
    elapsed = time.perf_counter() - started

    engine = "deterministic-fallback" if force_fallback else "llm"
    return {
        "seed": seed,
        "dataset_size": len(records),
        "dev_size": len(dev),
        "holdout_size": len(holdout),
        "decision_engine": engine,
        "elapsed_seconds": round(elapsed, 2),
        "policy": policy.model_dump(),
        "development": score(dev_outcomes),
        "holdout": score(holdout_outcomes),
        "holdout_exceptions": exception_samples(holdout_outcomes),
        "note": (
            "Held-out metrics are the honest ones. Thresholds in recover/tuning.json are "
            "fitted on the development split only; the held-out split is never used for "
            "tuning. Recovered revenue is simulated against ground truth, not collected "
            "from a real bank."
        ),
    }
