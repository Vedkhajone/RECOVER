"""Merchant policy configuration.

Everything writable here changes system behaviour on the next decision. There
are no cosmetic switches on this screen.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import audit
from ..api_schemas import PolicyPayload
from ..db import get_db
from ..enums import ActorType
from ..models import MerchantPolicy
from ..seed import DEMO_MERCHANT_ID

router = APIRouter(prefix="/api/policy", tags=["policy"])


def _payload(policy: MerchantPolicy) -> PolicyPayload:
    return PolicyPayload(
        max_auto_retries=policy.max_auto_retries,
        min_retry_interval_minutes=policy.min_retry_interval_minutes,
        max_auto_recovery_amount_paise=policy.max_auto_recovery_amount_paise,
        approval_threshold_paise=policy.approval_threshold_paise,
        max_customer_contacts_24h=policy.max_customer_contacts_24h,
        allow_payment_link_recovery=policy.allow_payment_link_recovery,
        allow_alternative_method=policy.allow_alternative_method,
        case_expiry_hours=policy.case_expiry_hours,
        min_expected_value_paise=policy.min_expected_value_paise,
        contact_cost_paise=policy.contact_cost_paise,
        retry_cost_paise=policy.retry_cost_paise,
    )


@router.get("", response_model=PolicyPayload)
def get_policy(merchant_id: str = DEMO_MERCHANT_ID,
               db: Session = Depends(get_db)) -> PolicyPayload:
    policy = db.get(MerchantPolicy, merchant_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="policy not found")
    return _payload(policy)


@router.put("", response_model=PolicyPayload)
def update_policy(payload: PolicyPayload, merchant_id: str = DEMO_MERCHANT_ID,
                  db: Session = Depends(get_db)) -> PolicyPayload:
    policy = db.get(MerchantPolicy, merchant_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="policy not found")

    if payload.approval_threshold_paise < payload.max_auto_recovery_amount_paise:
        raise HTTPException(
            status_code=422,
            detail=("Approval threshold must be at least the automatic recovery limit, "
                    "otherwise the two rules contradict each other."),
        )

    before = _payload(policy).model_dump()
    for field, value in payload.model_dump().items():
        setattr(policy, field, value)

    audit.record(db, actor=ActorType.MERCHANT, action="POLICY_UPDATED",
                 input_summary=before, result=payload.model_dump(),
                 reason="Merchant changed recovery policy.")
    db.commit()
    return _payload(policy)
