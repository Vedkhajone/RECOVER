"""The agent's tool registry.

Two tiers, and the split is the security model:

  READ TIER  (this module) - the only tools the model can call. Every one is a
             pure lookup. None of them moves money, contacts anyone, or mutates
             state. The worst a compromised or confused model can do with the
             entire read tier is read.

  ACTION TIER (recover/executor.py) - create_payment_retry_request,
             create_payment_link, send_customer_recovery_message, escalate_case.
             These are never exposed to the model. They are invoked by the
             executor *after* policy.evaluate() returns ALLOW, and each one
             re-checks policy itself before touching a provider.

There is deliberately no execute_anything(), no admin_action(), no
charge_any_amount(). The action vocabulary is closed at seven members.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..context import RecoveryContext
from ..enums import FailureReason
from ..policy import action_menu
from ..scoring import score_case

#: Human-readable notes on what each failure class usually means. Supplied to
#: the model as reference material so it is interpreting a documented taxonomy
#: rather than guessing at an opaque error string.
FAILURE_NOTES: dict[FailureReason, str] = {
    FailureReason.BANK_TRANSIENT: (
        "Issuer or gateway was temporarily unavailable. The instrument itself is fine. "
        "Usually resolves on its own within minutes."
    ),
    FailureReason.NETWORK_ERROR: (
        "Connection dropped mid-authorisation. Outcome was never returned to us. "
        "The customer may not even know it failed."
    ),
    FailureReason.INSUFFICIENT_FUNDS: (
        "Account did not have the balance. Retrying immediately fails again; retrying "
        "after payday or with a smaller amount sometimes works."
    ),
    FailureReason.CARD_EXPIRED: (
        "The stored card is past its expiry date. No retry on this instrument can ever "
        "succeed - the customer must supply a new one."
    ),
    FailureReason.INCORRECT_CVV: "Customer mistyped the CVV. A fresh checkout usually succeeds.",
    FailureReason.DO_NOT_HONOUR: (
        "Issuer declined without giving a reason. Ambiguous: could be a limit, a block, "
        "or a risk rule on the issuer side."
    ),
    FailureReason.AUTHENTICATION_FAILED: (
        "3DS/OTP was not completed. Often the customer walked away or the OTP SMS was slow."
    ),
    FailureReason.RISK_DECLINED: "Declined by risk screening. Never retried automatically.",
    FailureReason.MANDATE_INACTIVE: (
        "The subscription mandate has been revoked or expired. No debit is authorised."
    ),
    FailureReason.CUSTOMER_CANCELLED: "The customer deliberately abandoned or cancelled.",
    FailureReason.NONE: "No payment error was recorded for this event.",
}


@dataclass
class ToolContext:
    """What the read tools are allowed to see."""

    ctx: RecoveryContext
    db: Session | None = None
    calls: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Read-tier implementations
# --------------------------------------------------------------------------
def get_payment(tc: ToolContext, payment_id: str | None = None) -> dict:
    c = tc.ctx
    return {
        "payment_id": c.payment_id,
        "status": c.payment_status.value if c.payment_status else None,
        "amount_inr": round(c.amount_rupees, 2),
        "currency": c.currency,
        "method": c.payment_method,
        "failure_reason": c.failure_reason.value,
        "provider_error_code": c.provider_error_code,
        "provider_error_description": c.provider_error_description,
        "attempt_number": c.retry_count + 1,
    }


def get_order(tc: ToolContext, order_id: str | None = None) -> dict:
    c = tc.ctx
    return {
        "order_id": c.order_id,
        "reference": c.order_reference,
        "description": c.order_description,
        "amount_inr": round(c.amount_rupees, 2),
        "state": c.order_state.value if c.order_state else None,
        "already_paid": c.order_already_paid,
        "cancelled": c.order_cancelled,
        "refunded": c.order_refunded,
    }


def get_customer_history(tc: ToolContext, customer_id: str | None = None) -> dict:
    cust = tc.ctx.customer
    return {
        "customer_id": cust.customer_id,
        "segment": cust.segment.value,
        "successful_payments": cust.successful_payments,
        "failed_payments": cust.failed_payments,
        "previous_success_rate": round(cust.previous_success_rate, 3),
        "lifetime_value_inr": round(cust.lifetime_value_paise / 100, 2),
        "risk_flagged": cust.risk_flagged,
    }


def get_failure_context(tc: ToolContext, payment_id: str | None = None) -> dict:
    c = tc.ctx
    return {
        "failure_reason": c.failure_reason.value,
        "what_this_usually_means": FAILURE_NOTES.get(c.failure_reason, "Unknown failure class."),
        "provider_error_code": c.provider_error_code,
        "provider_error_description": c.provider_error_description,
        "minutes_since_event": c.minutes_since_event,
        "minutes_since_last_attempt": c.minutes_since_last_attempt,
        "risk_flags": c.risk_flags,
        "subscription_active": c.subscription_active,
        "invoice_days_overdue": c.invoice_days_overdue,
        "checkout_started": c.checkout_started,
        "checkout_completed": c.checkout_completed,
    }


def get_recovery_policy(tc: ToolContext, merchant_id: str | None = None) -> dict:
    p = tc.ctx.policy
    return {
        "max_auto_retries": p.max_auto_retries,
        "min_retry_interval_minutes": p.min_retry_interval_minutes,
        "max_auto_recovery_amount_inr": p.max_auto_recovery_amount_paise / 100,
        "approval_threshold_inr": p.approval_threshold_paise / 100,
        "max_customer_contacts_24h": p.max_customer_contacts_24h,
        "allow_payment_link_recovery": p.allow_payment_link_recovery,
        "allow_alternative_method": p.allow_alternative_method,
        "case_expiry_hours": p.case_expiry_hours,
        "note": (
            "This policy is enforced deterministically after you answer. You cannot "
            "override it and you should not propose an action it forbids."
        ),
    }


def get_recovery_history(tc: ToolContext, customer_id: str | None = None) -> dict:
    """Prior recovery activity on this case, and across this customer if we have a DB."""
    c = tc.ctx
    out: dict[str, Any] = {
        "this_case_retry_count": c.retry_count,
        "this_case_contacts_last_24h": c.contacts_last_24h,
    }
    if tc.db is not None:
        from ..models import RecoveryAttempt, RecoveryCase

        rows = tc.db.execute(
            select(RecoveryCase.id, RecoveryCase.status, RecoveryCase.executed_action,
                   RecoveryCase.recovered_amount_paise)
            .where(RecoveryCase.customer_id == c.customer.customer_id)
            .order_by(RecoveryCase.opened_at.desc())
            .limit(10)
        ).all()
        out["recent_cases"] = [
            {"case_id": r[0], "status": r[1], "action": r[2],
             "recovered_inr": (r[3] or 0) / 100}
            for r in rows
        ]
        attempts = tc.db.execute(
            select(RecoveryAttempt.action, RecoveryAttempt.succeeded)
            .where(RecoveryAttempt.case_id == c.case_id)
            .order_by(RecoveryAttempt.created_at)
        ).all()
        out["attempts_on_this_case"] = [
            {"action": a[0], "succeeded": a[1]} for a in attempts
        ]
    return out


def calculate_recovery_score(tc: ToolContext) -> dict:
    """The deterministic analytics. The model reads this; it does not compute it."""
    return score_case(tc.ctx)


def propose_recovery_action(tc: ToolContext) -> dict:
    """The menu of actions with each one's precomputed policy verdict."""
    return {
        "actions": action_menu(tc.ctx),
        "note": (
            "Choosing an action marked BLOCK will be rejected by the policy engine and the "
            "case will fall back to a safe default. Choose the best action that policy permits, "
            "or STOP if none is worth taking."
        ),
    }


def get_case_status(tc: ToolContext, case_id: str | None = None) -> dict:
    c = tc.ctx
    return {
        "case_id": c.case_id,
        "event_type": c.event_type.value,
        "amount_inr": round(c.amount_rupees, 2),
        "retry_count": c.retry_count,
        "contacts_last_24h": c.contacts_last_24h,
        "minutes_since_event": c.minutes_since_event,
    }


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., dict]


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


_ID = {"type": "string", "description": "Identifier from the case summary."}

READ_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("get_payment", "Fetch the payment attached to this case.",
             _obj({"payment_id": _ID}), get_payment),
    ToolSpec("get_order", "Fetch the order attached to this case, including its state.",
             _obj({"order_id": _ID}), get_order),
    ToolSpec("get_customer_history",
             "Fetch this customer's past payment reliability and risk flags.",
             _obj({"customer_id": _ID}), get_customer_history),
    ToolSpec("get_failure_context",
             "Explain the payment failure: normalised class, what it usually means, timing.",
             _obj({"payment_id": _ID}), get_failure_context),
    ToolSpec("get_recovery_policy",
             "Read the merchant's recovery policy limits.",
             _obj({"merchant_id": _ID}), get_recovery_policy),
    ToolSpec("get_recovery_history",
             "What recovery has already been attempted on this case and this customer.",
             _obj({"customer_id": _ID}), get_recovery_history),
    ToolSpec("calculate_recovery_score",
             "Deterministic recovery probability and expected value per action. "
             "You must use these numbers rather than estimating your own.",
             _obj({}), calculate_recovery_score),
    ToolSpec("propose_recovery_action",
             "List every recovery action with the policy verdict already computed for it.",
             _obj({}), propose_recovery_action),
    ToolSpec("get_case_status", "Current status counters for this case.",
             _obj({"case_id": _ID}), get_case_status),
)

READ_TOOLS_BY_NAME = {t.name: t for t in READ_TOOLS}


def anthropic_tool_schemas() -> list[dict]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.parameters}
        for t in READ_TOOLS
    ]


def dispatch(tc: ToolContext, name: str, arguments: dict | None) -> dict:
    """Run a read tool by name. Unknown names are refused, not guessed at."""
    spec = READ_TOOLS_BY_NAME.get(name)
    if spec is None:
        return {"error": f"unknown tool {name!r}",
                "available": sorted(READ_TOOLS_BY_NAME)}
    tc.calls.append(name)
    kwargs = {k: v for k, v in (arguments or {}).items()
              if k in spec.parameters.get("properties", {})}
    try:
        return spec.fn(tc, **kwargs)
    except Exception as exc:  # a tool failure is data, not a crash
        return {"error": f"{type(exc).__name__}: {exc}"}


def dispatch_json(tc: ToolContext, name: str, arguments: dict | None) -> str:
    return json.dumps(dispatch(tc, name, arguments), default=str)
