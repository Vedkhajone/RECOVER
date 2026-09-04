"""Append-only audit trail.

Every decision, tool call, policy verdict and provider interaction lands here.
The rule is: if it moved money, contacted a customer, or explains why we did
either, it is auditable.

Secrets never reach this table. `input_summary` is a short rendered summary, not
a raw request body.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from .enums import ActorType
from .models import AuditLog, CaseEvent

log = logging.getLogger("recover.audit")

MAX_SUMMARY = 2000
#: Keys that must never be persisted or logged, at any nesting depth.
REDACT_KEYS = {"key_secret", "razorpay_key_secret", "webhook_secret", "signature",
               "api_key", "anthropic_api_key", "authorization", "password", "token"}


def redact(value):
    """Recursively drop anything that looks like a credential."""
    if isinstance(value, dict):
        return {k: ("[redacted]" if k.lower() in REDACT_KEYS else redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def summarise(payload) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(redact(payload), default=str)
    return text[:MAX_SUMMARY]


def record(
    db: Session,
    *,
    actor: ActorType | str,
    action: str,
    case_id: str | None = None,
    customer_id: str | None = None,
    order_id: str | None = None,
    payment_id: str | None = None,
    tool: str | None = None,
    input_summary=None,
    result=None,
    policy_decision: str | None = None,
    reason: str | None = None,
    status: str = "ok",
    error: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor=str(actor),
        action=action,
        case_id=case_id,
        customer_id=customer_id,
        order_id=order_id,
        payment_id=payment_id,
        tool=tool,
        input_summary=summarise(input_summary),
        result=summarise(result),
        policy_decision=policy_decision,
        reason=(reason or "")[:MAX_SUMMARY] or None,
        status=status,
        error=(error or "")[:MAX_SUMMARY] or None,
    )
    db.add(entry)
    return entry


def timeline(db: Session, case_id: str, *, actor: ActorType, title: str,
             detail: str | None = None) -> CaseEvent:
    """Add a human-readable entry to a case's timeline."""
    event = CaseEvent(case_id=case_id, actor=actor, title=title, detail=detail)
    db.add(event)
    return event
