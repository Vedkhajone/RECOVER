"""Evaluation results.

The API serves the *stored* result of an evaluation run. It does not compute
metrics on request, because a metric computed in an HTTP handler is a metric
nobody can reproduce. `scripts/run_evaluation.py` writes the row; this endpoint
reads it back.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..models import EvaluationRun

router = APIRouter(prefix="/api/evaluation", tags=["evaluation"])


@router.get("/latest")
def latest(db: Session = Depends(get_db)) -> dict:
    row = db.execute(
        select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(1)
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=("No evaluation has been run yet. Run "
                    "`python scripts/run_evaluation.py` and reload."),
        )
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat(),
        "dataset_seed": row.dataset_seed,
        "dataset_size": row.dataset_size,
        "dev_size": row.dev_size,
        "holdout_size": row.holdout_size,
        "decision_engine": row.decision_engine,
        **row.metrics,
    }


@router.get("/runs")
def runs(limit: int = 10, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(
        select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(limit)
    ).scalars()
    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat(),
            "dataset_size": r.dataset_size,
            "holdout_size": r.holdout_size,
            "decision_engine": r.decision_engine,
            "holdout_recovery_rate": r.metrics.get("holdout", {}).get("recovery_rate"),
            "holdout_action_accuracy": (r.metrics.get("holdout", {})
                                        .get("action_selection", {})
                                        .get("accuracy_detected_only")),
        }
        for r in rows
    ]


def _run(seed: int, size: int) -> None:
    from ..dataset.evaluate import run_evaluation
    from ..dataset.persist import store_run

    result = run_evaluation(seed=seed, size=size, force_fallback=True)
    db = SessionLocal()
    try:
        store_run(db, result)
        db.commit()
    finally:
        db.close()


@router.post("/run")
def run(background: BackgroundTasks, seed: int = 20260901, size: int = 10000) -> dict:
    """Kick off an evaluation in the background.

    Provided so a judge can regenerate the numbers from the UI. The CLI script
    is the canonical path and prints the full table.
    """
    background.add_task(_run, seed, size)
    return {"queued": True, "seed": seed, "size": size,
            "note": "Poll /api/evaluation/latest; a 10,000-record run takes a few seconds."}
