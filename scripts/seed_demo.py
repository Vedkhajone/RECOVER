"""Reset the database to the deterministic demo state.

Run:  python scripts/seed_demo.py [--process] [--use-llm]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from recover.db import session_scope  # noqa: E402
from recover.engine import detect_state_mismatches, run_recovery_batch  # noqa: E402
from recover.seed import DEMO_MERCHANT_ID, reset_and_seed  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--process", action="store_true",
                        help="Also run the recovery batch over the seeded cases.")
    parser.add_argument("--use-llm", action="store_true",
                        help="Use the model path when ANTHROPIC_API_KEY is set.")
    args = parser.parse_args()

    result = reset_and_seed()
    print(f"Seeded merchant {result['merchant_id']}: "
          f"{result['scenario_cases']} scenario cases + {result['bulk_cases']} bulk cases.")
    print(f"Live demo case: {result['live_demo_case_id']}")

    if args.process:
        with session_scope() as db:
            mismatches = detect_state_mismatches(db, DEMO_MERCHANT_ID)
        print(f"Reconciliation sweep found {len(mismatches)} state mismatch(es).")
        with session_scope() as db:
            summary = run_recovery_batch(db, merchant_id=DEMO_MERCHANT_ID, limit=200,
                                         force_fallback=not args.use_llm)
        print(f"Batch processed {summary['processed']} cases: "
              f"{summary['executed']} executed, {summary['blocked']} blocked, "
              f"{summary['awaiting_approval']} awaiting approval, "
              f"{summary['errors']} errors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
