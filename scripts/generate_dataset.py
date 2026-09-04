"""Generate the synthetic dataset to disk for inspection.

Run:  python scripts/generate_dataset.py --out data/synthetic.jsonl

The evaluation does not read this file - it regenerates from the seed, so the
reported numbers can never drift from a stale export. This exists so a reviewer
can read the records with their own eyes.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from recover.dataset.generator import (  # noqa: E402
    DEFAULT_SEED,
    DEFAULT_SIZE,
    generate_records,
    split,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE)
    parser.add_argument("--out", default=str(ROOT / "data" / "synthetic.jsonl"))
    args = parser.parse_args()

    records = generate_records(seed=args.seed, size=args.size)
    dev, holdout = split(records)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")

    events = Counter(r["event_type"] for r in records)
    reasons = Counter(r["failure_reason"] for r in records)
    at_risk = sum(1 for r in records if r["ground_truth_at_risk"])
    recoverable = sum(1 for r in records if r["ground_truth_recoverable"])
    total = sum(r["amount_paise"] for r in records if r["ground_truth_at_risk"])

    print(f"Wrote {len(records):,} records to {out}")
    print(f"  Development split      {len(dev):,}")
    print(f"  Held-out split         {len(holdout):,}")
    print(f"  Genuinely at risk      {at_risk:,} ({at_risk / len(records):.1%})")
    print(f"  Genuinely recoverable  {recoverable:,} ({recoverable / len(records):.1%})")
    print(f"  Revenue at risk        Rs {total / 100:,.2f}")
    print("\n  Event mix:")
    for name, count in events.most_common():
        print(f"    {name:<34} {count:>7,}  {count / len(records):>6.1%}")
    print("\n  Failure mix:")
    for name, count in reasons.most_common():
        print(f"    {name:<34} {count:>7,}  {count / len(records):>6.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
