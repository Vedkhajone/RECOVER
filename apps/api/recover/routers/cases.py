"""Recovery case management."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import engine, serializers
from ..api_schemas import (
    BatchRequest,
    CaseDetail,
    CaseSummary,
    Money,
    PolicyProbeRequest,
    PolicyProbeResponse,
)
from ..config import get_settings
from ..context import context_from_case
from ..db import SessionLocal, get_db
from ..enums import PolicyDecision, RecoveryAction
from ..models import Customer, Order, RecoveryCase
from ..policy import evaluate
from ..seed import DEMO_MERCHANT_ID

router = APIRouter(prefix="/api/cases", tags=["cases"])


def _get(db: Session, case_id: str) -> RecoveryCase:
    case = db.get(RecoveryCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"case {case_id} not found")
    return case


@router.get("", response_model=list[CaseSummary])
def list_cases(
    merchant_id: str = DEMO_MERCHANT_ID,
    status: str | None = None,
    event_type: str | None = None,
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[CaseSummary]:
    query = select(RecoveryCase).where(RecoveryCase.merchant_id == merchant_id)
    if status:
        query = query.where(RecoveryCase.status == status)
    if event_type:
        query = query.where(RecoveryCase.event_type == event_type)
    if search:
        term = f"%{search.strip()}%"
        query = (query.join(Customer, Customer.id == RecoveryCase.customer_id)
                 .outerjoin(Order, Order.id == RecoveryCase.order_id)
                 .where(or_(Customer.name.ilike(term), Order.reference.ilike(term),
                            RecoveryCase.id.ilike(term))))
    query = query.order_by(RecoveryCase.opened_at.desc()).limit(limit).offset(offset)
    return [serializers.case_summary(db, c) for c in db.execute(query).scalars()]


@router.get("/{case_id}", response_model=CaseDetail)
def get_case(case_id: str, db: Session = Depends(get_db)) -> CaseDetail:
    return serializers.case_detail(db, _get(db, case_id))


@router.post("/{case_id}/process")
def process(case_id: str, use_deterministic_engine: bool = False,
            db: Session = Depends(get_db)) -> dict:
    """Run one detect -> diagnose -> decide -> act cycle."""
    _get(db, case_id)
    try:
        return engine.process_case(db, case_id, force_fallback=use_deterministic_engine)
    except engine.CaseProcessingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{case_id}/approve")
def approve(case_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return engine.approve_case(db, case_id)
    except engine.CaseProcessingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{case_id}/reject")
def reject(case_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return engine.reject_case(db, case_id)
    except engine.CaseProcessingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{case_id}/policy-probe", response_model=PolicyProbeResponse)
def policy_probe(case_id: str, payload: PolicyProbeRequest,
                 db: Session = Depends(get_db)) -> PolicyProbeResponse:
    """Ask the policy engine about an arbitrary action without executing it.

    This is how the UI demonstrates "the AI proposes something forbidden and
    policy blocks it" honestly: the operator picks the action, the real policy
    engine answers, and nothing is executed or attributed to the model.
    """
    case = _get(db, case_id)
    ctx = context_from_case(case, engine.get_policy(db, case.merchant_id))
    verdict = evaluate(ctx, payload.action)
    return PolicyProbeResponse(
        action=verdict.action.value,
        decision=verdict.decision.value,
        code=verdict.code,
        reason=verdict.reason,
        expected_value=Money.of(verdict.expected_value_paise),
        recovery_probability=verdict.recovery_probability,
        would_execute=verdict.decision == PolicyDecision.ALLOW,
    )


def _run_batch(merchant_id: str, limit: int, force_fallback: bool) -> None:
    """Background worker. Owns its own session - a request-scoped one would be
    closed out from under it the moment the response is returned."""
    db = SessionLocal()
    try:
        engine.run_recovery_batch(db, merchant_id=merchant_id, limit=limit,
                                  force_fallback=force_fallback)
    finally:
        db.close()


@router.post("/batch/run")
def run_batch(payload: BatchRequest, background: BackgroundTasks,
              merchant_id: str = DEMO_MERCHANT_ID, wait: bool = True,
              db: Session = Depends(get_db)) -> dict:
    """Process every open case.

    `wait=true` (the default) runs inline and returns the full result, which is
    what the demo uses so the judge sees the numbers move immediately.
    `wait=false` queues it on FastAPI's background task runner - adequate for a
    prototype, and deliberately not a message broker we would then have to run.
    """
    settings = get_settings()
    force_fallback = payload.use_deterministic_engine or not settings.ai_enabled
    if not wait:
        background.add_task(_run_batch, merchant_id, payload.limit, force_fallback)
        return {"queued": True, "limit": payload.limit,
                "engine": "deterministic-fallback" if force_fallback else "llm"}
    summary = engine.run_recovery_batch(db, merchant_id=merchant_id, limit=payload.limit,
                                        force_fallback=force_fallback)
    summary["engine"] = "deterministic-fallback" if force_fallback else "llm"
    return summary


@router.post("/reconcile")
def reconcile(merchant_id: str = DEMO_MERCHANT_ID, db: Session = Depends(get_db)) -> dict:
    """Sweep for payments that succeeded against orders that never advanced."""
    found = engine.detect_state_mismatches(db, merchant_id)
    return {"mismatches_found": len(found), "cases": found}
