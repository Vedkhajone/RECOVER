"""Audit trail and reconciliation exceptions."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import serializers
from ..api_schemas import AuditEntry, ExceptionEntry
from ..db import get_db
from ..models import AuditLog, ReconciliationException

router = APIRouter(prefix="/api", tags=["audit"])


@router.get("/audit", response_model=list[AuditEntry])
def list_audit(
    case_id: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[AuditEntry]:
    query = select(AuditLog)
    if case_id:
        query = query.where(AuditLog.case_id == case_id)
    if actor:
        query = query.where(AuditLog.actor == actor)
    if action:
        query = query.where(AuditLog.action == action)
    if status:
        query = query.where(AuditLog.status == status)
    if search:
        term = f"%{search.strip()}%"
        query = query.where(or_(AuditLog.action.ilike(term), AuditLog.reason.ilike(term),
                                AuditLog.result.ilike(term), AuditLog.case_id.ilike(term)))
    query = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    rows = db.execute(query.limit(limit).offset(offset)).scalars()
    return [serializers.audit_entry(r) for r in rows]


@router.get("/exceptions", response_model=list[ExceptionEntry])
def list_exceptions(include_resolved: bool = False,
                    db: Session = Depends(get_db)) -> list[ExceptionEntry]:
    """Cases the system could not confidently resolve. Never hidden."""
    query = select(ReconciliationException)
    if not include_resolved:
        query = query.where(ReconciliationException.resolved.is_(False))
    rows = db.execute(query.order_by(ReconciliationException.created_at.desc())).scalars()
    return [serializers.exception_entry(r) for r in rows]
