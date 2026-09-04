"""Run the held-out evaluation and print the honest table.

Run:  python scripts/run_evaluation.py [--seed 20260901] [--size 10000]
      python scripts/run_evaluation.py --engine llm --limit 40   # LLM sample

Writes the result to the database so the evaluation dashboard can read it.
Nothing in the reported numbers is hardcoded; re-running with the same seed
reproduces them exactly.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

API = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API))

from recover.dataset.evaluate import run_evaluation  # noqa: E402
from recover.dataset.generator import DEFAULT_SEED, DEFAULT_SIZE  # noqa: E402
from recover.dataset.persist import store_run  # noqa: E402
from recover.db import init_db, session_scope  # noqa: E402
from recover.scoring import tuning_source  # noqa: E402


def _table(title: str, block: dict) -> str:
    d = block["detection"]
    e = block["recovery_eligibility"]
    a = block["action_selection"]
    m = block["money_inr"]
    s = block["safety"]
    lines = [f"\n{title}", "=" * len(title)]
    lines.append(f"  Records                            {block['dataset']['records']:>16,}")
    lines.append("")
    lines.append("  DETECTION (predicted at-risk vs actual)")
    lines.append(f"    Precision                        {d['precision']:>16.4f}")
    lines.append(f"    Recall                           {d['recall']:>16.4f}")
    lines.append(f"    TP / FP / FN                     {d['true_positives']:>8,} / "
                 f"{d['false_positives']:,} / {d['false_negatives']:,}")
    lines.append("")
    lines.append("  RECOVERY ELIGIBILITY")
    lines.append(f"    Precision                        {e['precision']:>16.4f}")
    lines.append(f"    Recall                           {e['recall']:>16.4f}")
    lines.append("")
    lines.append("  ACTION SELECTION")
    lines.append(f"    Accuracy (all records)           {a['accuracy_all_records']:>16.4f}")
    lines.append(f"    Accuracy (detected only)         {a['accuracy_detected_only']:>16.4f}")
    lines.append("")
    lines.append("  MONEY (INR)")
    lines.append(f"    Total revenue at risk      {m['total_revenue_at_risk']:>22,.2f}")
    lines.append(f"    Eligible for recovery      {m['revenue_eligible_for_recovery']:>22,.2f}")
    lines.append(f"    Actually recovered         {m['revenue_actually_recovered']:>22,.2f}")
    lines.append(f"    Intervention cost          {m['intervention_cost']:>22,.2f}")
    lines.append(f"    Net recovered revenue      {m['net_recovered_revenue']:>22,.2f}")
    lines.append(f"    False-positive cost        "
                 f"{m['false_positive_intervention_cost']:>22,.2f}")
    lines.append(f"    Recovery rate              {block['recovery_rate']:>22.4f}")
    lines.append("")
    lines.append("  SAFETY")
    lines.append(f"    Interventions made               {s['interventions']:>16,}")
    lines.append(f"    False-positive interventions     "
                 f"{s['false_positive_interventions']:>16,}")
    lines.append(f"    Unnecessary customer contacts    "
                 f"{s['unnecessary_customer_contacts']:>16,}")
    lines.append(f"    Total customer contacts          {s['total_customer_contacts']:>16,}")
    lines.append(f"    Unsafe actions blocked           {s['blocked_unsafe_actions']:>16,}")
    lines.append(f"    Policy refusals (all)            {s['policy_refusals']:>16,}")
    lines.append(f"    Cases stopped                    {s['cases_stopped']:>16,}")
    lines.append(f"    Cases escalated                  {s['cases_escalated']:>16,}")
    lines.append(f"    Cases awaiting approval          {s['cases_awaiting_approval']:>16,}")
    lines.append(f"    Unresolved exceptions            {s['unresolved_exceptions']:>16,}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE)
    parser.add_argument("--engine", choices=["deterministic", "llm"], default="deterministic")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap records per split. Required in practice for --engine llm.")
    parser.add_argument("--json", action="store_true", help="Dump raw JSON instead of a table.")
    parser.add_argument("--no-store", action="store_true")
    args = parser.parse_args()

    if args.engine == "llm" and args.limit is None:
        parser.error("--engine llm needs --limit (it makes one model call per record).")

    result = run_evaluation(seed=args.seed, size=args.size,
                            force_fallback=args.engine == "deterministic",
                            limit_records=args.limit)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("RECOVER - evaluation")
        print("=" * 62)
        print(f"  Seed                 {result['seed']}")
        print(f"  Dataset              {result['dataset_size']:,} synthetic events")
        print(f"  Development split    {result['dev_size']:,}")
        print(f"  Held-out split       {result['holdout_size']:,}")
        print(f"  Decision engine      {result['decision_engine']}")
        print(f"  Thresholds           {tuning_source()}")
        print(f"  Elapsed              {result['elapsed_seconds']}s")
        print(_table("DEVELOPMENT SPLIT (used for tuning)", result["development"]))
        print(_table("HELD-OUT SPLIT (never used for tuning)", result["holdout"]))
        print("\nEXCEPTIONS (held-out, largest first)")
        print("=" * 62)
        for row in result["holdout_exceptions"][:10]:
            print(f"  {row['transaction_id']}  Rs {row['amount_inr']:>10,.2f}  "
                  f"proposed={row['proposed_action']:<26} "
                  f"truth={row['ground_truth_best_action']:<26} {row['issue']}")
        print(f"\n{result['note']}")

    if not args.no_store:
        init_db()
        with session_scope() as db:
            row = store_run(db, result)
            run_id = row.id
        print(f"\nStored as evaluation run #{run_id}. The dashboard will show it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
