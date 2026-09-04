"""Store an evaluation result so the dashboard can read it back."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import EvaluationRun


def store_run(db: Session, result: dict) -> EvaluationRun:
    row = EvaluationRun(
        dataset_seed=result["seed"],
        dataset_size=result["dataset_size"],
        dev_size=result["dev_size"],
        holdout_size=result["holdout_size"],
        decision_engine=result["decision_engine"],
        metrics={
            "development": result["development"],
            "holdout": result["holdout"],
            "holdout_exceptions": result["holdout_exceptions"],
            "policy": result["policy"],
            "elapsed_seconds": result["elapsed_seconds"],
            "note": result["note"],
        },
    )
    db.add(row)
    db.flush()
    return row
