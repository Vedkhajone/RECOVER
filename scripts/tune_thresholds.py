"""Fit decision thresholds on the DEVELOPMENT split only.

Run:  python scripts/tune_thresholds.py [--seed 20260901] [--size 10000]

Grid-searches the two decision thresholds in recover/scoring.py against the 80%
development split and writes the winner to recover/tuning.json.

The held-out split is not loaded by this script at all - not read, not scored,
not peeked at. That is the point. `scripts/run_evaluation.py` is the only thing
that touches it, and it runs after tuning is finished.

Objective: net recovered revenue, minus a goodwill penalty for every customer
contact that ground truth says was unnecessary.

The goodwill term is not decoration. Tuning on net revenue alone degenerates:
the marginal cost of a message is Rs 0.50 while a recovered order is worth
thousands, so the optimiser always chooses "contact everyone", which is exactly
the blind-retry behaviour RECOVER exists to avoid. Rs 0.50 is the *send* cost,
not the cost of annoying a customer who owed nothing.

GOODWILL_COST_INR below is therefore an explicit, arguable assumption rather
than a measured quantity, and it is stated here so a reader can disagree with
it. Run with --objective net_revenue to see the degenerate operating point for
yourself; docs/evaluation.md records both.
"""
from __future__ import annotations

import argparse
import json
import sys
from itertools import product
from pathlib import Path

API = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API))

from recover.context import PolicySnapshot  # noqa: E402
from recover.dataset.evaluate import run_case, score  # noqa: E402
from recover.dataset.generator import DEFAULT_SEED, DEFAULT_SIZE, generate_records, split  # noqa: E402
from recover.scoring import DEFAULT_TUNING, TUNING_PATH, tuning  # noqa: E402

#: Assumed cost of one unnecessary customer contact, in rupees. An assumption,
#: not a measurement - see the module docstring.
GOODWILL_COST_INR = 25.0

ACT_GRID = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40]
RETRY_GRID = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE)
    parser.add_argument("--objective", choices=["net_revenue_with_goodwill", "net_revenue"],
                        default="net_revenue_with_goodwill")
    parser.add_argument("--goodwill-cost-inr", type=float, default=GOODWILL_COST_INR)
    args = parser.parse_args()

    records = generate_records(seed=args.seed, size=args.size)
    dev, holdout = split(records)
    del holdout  # never used here, and deleted so it cannot be
    print(f"Tuning on {len(dev)} development records "
          f"({args.size - len(dev)} held-out records untouched).\n")

    policy = PolicySnapshot()
    results = []
    for act, retry in product(ACT_GRID, RETRY_GRID):
        if retry < act:
            continue  # a retry threshold below the act threshold is meaningless
        TUNING_PATH.write_text(json.dumps({
            "params": {**DEFAULT_TUNING,
                       "act_probability_threshold": act,
                       "retry_probability_threshold": retry},
            "fitted_on": "development split",
        }, indent=2))
        tuning.cache_clear()

        outcomes = [run_case(r, policy, force_fallback=True) for r in dev]
        s = score(outcomes)
        net = s["money_inr"]["net_recovered_revenue"]
        unnecessary = s["safety"]["unnecessary_customer_contacts"]
        objective = (net if args.objective == "net_revenue"
                     else net - args.goodwill_cost_inr * unnecessary)
        results.append({
            "objective_value_inr": round(objective, 2),
            "act_probability_threshold": act,
            "retry_probability_threshold": retry,
            "net_recovered_inr": s["money_inr"]["net_recovered_revenue"],
            "recovery_rate": s["recovery_rate"],
            "action_accuracy": s["action_selection"]["accuracy_detected_only"],
            "eligibility_precision": s["recovery_eligibility"]["precision"],
            "unnecessary_contacts": unnecessary,
        })
        print(f"  act={act:.2f} retry={retry:.2f}  "
              f"objective=Rs {objective:>12,.0f}  "
              f"net=Rs {net:>12,.0f}  "
              f"bad_contacts={unnecessary:>5}  "
              f"action_acc={s['action_selection']['accuracy_detected_only']:.3f}")

    best = max(results, key=lambda r: (r["objective_value_inr"], r["action_accuracy"]))
    TUNING_PATH.write_text(json.dumps({
        "params": {**DEFAULT_TUNING,
                   "act_probability_threshold": best["act_probability_threshold"],
                   "retry_probability_threshold": best["retry_probability_threshold"]},
        "fitted_on": "development split (80%)",
        "seed": args.seed,
        "dataset_size": args.size,
        "dev_records": len(dev),
        "objective": args.objective,
        "goodwill_cost_inr_per_unnecessary_contact": args.goodwill_cost_inr,
        "selected": best,
        "grid": results,
    }, indent=2))

    print(f"\nSelected act={best['act_probability_threshold']} "
          f"retry={best['retry_probability_threshold']}")
    print(f"Written to {TUNING_PATH}")
    print("Now run: python scripts/run_evaluation.py  (held-out numbers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
