"""Merchant dashboard metrics.

Every figure here is a live aggregate over the cases table. None of it is
cached, precomputed or hardcoded - if the number moves on screen, a row moved
in the database.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..api_schemas import DashboardMetrics, Money, TrendPoint
from ..db import get_db
from ..enums import CaseStatus
from ..models import ReconciliationException, RecoveryCase
from ..seed import DEMO_MERCHANT_ID

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

#: Statuses in which the merchant has not yet been paid and the case is live.
ACTIVE_STATUSES = (
    CaseStatus.OPEN.value, CaseStatus.INVESTIGATING.value,
    CaseStatus.ACTION_PENDING.value, CaseStatus.AWAITING_CUSTOMER.value,
    CaseStatus.AWAITING_APPROVAL.value,
)


def _count(db: Session, merchant_id: str, *statuses: str) -> int:
    return db.execute(
        select(func.count()).select_from(RecoveryCase)
        .where(RecoveryCase.merchant_id == merchant_id,
               RecoveryCase.status.in_(statuses))
    ).scalar_one()


@router.get("/metrics", response_model=DashboardMetrics)
def metrics(merchant_id: str = DEMO_MERCHANT_ID,
            db: Session = Depends(get_db)) -> DashboardMetrics:
    scope = RecoveryCase.merchant_id == merchant_id

    total_cases = db.execute(
        select(func.count()).select_from(RecoveryCase).where(scope)
    ).scalar_one()

    # Revenue at risk = money on cases that are still open in some form. A case
    # that has been recovered, stopped or blocked is no longer "at risk"; it has
    # an outcome.
    at_risk = db.execute(
        select(func.coalesce(func.sum(RecoveryCase.amount_at_risk_paise), 0))
        .where(scope, RecoveryCase.status.in_(ACTIVE_STATUSES))
    ).scalar_one()

    # Recoverable = at-risk money the deterministic scorer judged worth pursuing
    # (positive expected value). This is a PREDICTION, not a promise.
    recoverable = db.execute(
        select(func.coalesce(func.sum(RecoveryCase.amount_at_risk_paise), 0))
        .where(scope, RecoveryCase.status.in_(ACTIVE_STATUSES),
               RecoveryCase.expected_value_paise > 0)
    ).scalar_one()

    # Recovered = written only from a verified provider payment event.
    recovered = db.execute(
        select(func.coalesce(func.sum(RecoveryCase.recovered_amount_paise), 0)).where(scope)
    ).scalar_one()

    cost = db.execute(
        select(func.coalesce(func.sum(RecoveryCase.intervention_cost_paise), 0)).where(scope)
    ).scalar_one()

    contacts = db.execute(
        select(func.coalesce(func.sum(RecoveryCase.contacts_sent), 0)).where(scope)
    ).scalar_one()

    successful = _count(db, merchant_id, CaseStatus.RECOVERED.value)

    # Recovery rate is measured against everything that reached an outcome plus
    # everything still live - i.e. all money the system was ever responsible
    # for. Measuring against recovered-only would make the rate always 100%.
    denominator = db.execute(
        select(func.coalesce(func.sum(RecoveryCase.amount_at_risk_paise), 0)).where(scope)
    ).scalar_one()

    exceptions = db.execute(
        select(func.count()).select_from(ReconciliationException)
        .where(ReconciliationException.resolved.is_(False))
    ).scalar_one()

    # 7-day trend, bucketed by day of case opening.
    now = datetime.now(UTC)
    trend: list[TrendPoint] = []
    for offset in range(6, -1, -1):
        start = (now - timedelta(days=offset)).replace(hour=0, minute=0, second=0,
                                                       microsecond=0)
        end = start + timedelta(days=1)
        row = db.execute(
            select(func.coalesce(func.sum(RecoveryCase.amount_at_risk_paise), 0),
                   func.coalesce(func.sum(RecoveryCase.recovered_amount_paise), 0))
            .where(scope, RecoveryCase.opened_at >= start, RecoveryCase.opened_at < end)
        ).one()
        trend.append(TrendPoint(label=start.strftime("%d %b"),
                                at_risk_paise=int(row[0]), recovered_paise=int(row[1])))

    return DashboardMetrics(
        revenue_at_risk=Money.of(at_risk),
        recoverable_revenue=Money.of(recoverable),
        recovered_revenue=Money.of(recovered),
        net_recovered_revenue=Money.of(recovered - cost),
        intervention_cost=Money.of(cost),
        recovery_rate=round(recovered / denominator, 4) if denominator else 0.0,
        active_cases=_count(db, merchant_id, *ACTIVE_STATUSES),
        successful_interventions=successful,
        stopped_cases=_count(db, merchant_id, CaseStatus.STOPPED.value),
        escalated_cases=_count(db, merchant_id, CaseStatus.ESCALATED.value),
        blocked_cases=_count(db, merchant_id, CaseStatus.BLOCKED.value),
        awaiting_approval=_count(db, merchant_id, CaseStatus.AWAITING_APPROVAL.value),
        reconciliation_cases=_count(db, merchant_id, CaseStatus.RECONCILIATION.value),
        customer_contacts=int(contacts),
        total_cases=int(total_cases),
        open_exceptions=int(exceptions),
        trend=trend,
    )
