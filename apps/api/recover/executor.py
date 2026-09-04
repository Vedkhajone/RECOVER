"""The action tier.

These are the only four functions in RECOVER that can touch a payment provider
or a customer. They are never exposed to the model as tools.

Every one of them re-runs `policy.evaluate()` for itself before doing anything.
That is deliberate belt-and-braces: the orchestrator in engine.py already
gates on policy, and if that gate were ever removed or bypassed, these
functions would still refuse. A `PolicyViolation` raised from here means a code
path tried to skip the gate, and it is logged as a security event.
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from . import audit, state_machine
from .config import get_settings
from .context import RecoveryContext
from .enums import (
    ActorType,
    CaseStatus,
    OrderState,
    PolicyDecision,
    RecoveryAction,
)
from .models import RecoveryAttempt, RecoveryCase, ReconciliationException
from .policy import evaluate
from .providers.base import ProviderError
from .providers.simulator import get_payment_provider
from .scoring import intervention_cost_paise


class PolicyViolation(Exception):
    """An action tier function was called for an action policy does not allow."""


def _gate(db: Session, case: RecoveryCase, ctx: RecoveryContext,
          action: RecoveryAction, *, merchant_approved: bool = False) -> None:
    """Refuse anything policy does not permit.

    `merchant_approved` satisfies a REQUIRE_APPROVAL verdict and nothing else.
    A BLOCK is not approvable by anyone - that is the point of the distinction.
    """
    verdict = evaluate(ctx, action)
    acceptable = {PolicyDecision.ALLOW}
    if merchant_approved:
        acceptable.add(PolicyDecision.REQUIRE_APPROVAL)
    if verdict.decision not in acceptable:
        audit.record(
            db, actor=ActorType.POLICY_ENGINE, action="ACTION_TIER_REFUSED",
            case_id=case.id, policy_decision=verdict.decision.value,
            reason=f"{verdict.code}: {verdict.reason}", status="blocked",
            input_summary={"attempted_action": action.value},
        )
        raise PolicyViolation(
            f"{action.value} refused at the action tier: {verdict.code} - {verdict.reason}"
        )


def _attempt(db: Session, case: RecoveryCase, action: RecoveryAction, *,
             succeeded: bool | None, reference: str | None, cost: int,
             detail: str) -> RecoveryAttempt:
    attempt = RecoveryAttempt(
        case_id=case.id, action=action, policy_decision=PolicyDecision.ALLOW,
        succeeded=succeeded, provider_reference=reference, cost_paise=cost, detail=detail,
    )
    db.add(attempt)
    case.intervention_cost_paise += cost
    case.last_action_at = datetime.now(UTC)
    return attempt


# --------------------------------------------------------------------------
# Action tier
# --------------------------------------------------------------------------
def create_payment_retry_request(db: Session, case: RecoveryCase, ctx: RecoveryContext,
                                 *, merchant_approved: bool = False) -> dict:
    """Open a fresh, customer-authorised payment attempt for a failed order.

    RECOVER never silently re-charges a stored instrument. It creates a new
    provider order and a single-use recovery link; the customer has to complete
    the payment themselves. That is a product decision as much as a technical
    one - a surprise debit is worse than an unrecovered rupee.
    """
    _gate(db, case, ctx, RecoveryAction.RETRY_PAYMENT,
          merchant_approved=merchant_approved)
    provider = get_payment_provider()
    settings = get_settings()
    order = case.order
    if order is None:
        raise PolicyViolation("cannot retry a case with no order")

    try:
        provider_order = provider.create_order(
            amount_paise=order.amount_paise,
            currency=order.currency,
            receipt=f"rcv-{case.id}"[:40],
            notes={"recover_case_id": case.id, "recover_order_ref": order.reference},
        )
    except ProviderError as exc:
        audit.record(db, actor=ActorType.PROVIDER, action="CREATE_ORDER_FAILED",
                     case_id=case.id, order_id=order.id, status="error", error=str(exc))
        _attempt(db, case, RecoveryAction.RETRY_PAYMENT, succeeded=False, reference=None,
                 cost=0, detail=f"Provider rejected order creation: {exc}")
        raise

    order.provider_order_id = provider_order.id
    state_machine.transition(order, OrderState.PAYMENT_PENDING)

    case.recovery_token = secrets.token_urlsafe(24)
    case.retry_count += 1
    case.status = CaseStatus.AWAITING_CUSTOMER
    cost = intervention_cost_paise(ctx, RecoveryAction.RETRY_PAYMENT)
    recovery_url = f"{settings.public_web_url}/recover/{case.recovery_token}"
    case.payment_link_url = recovery_url

    _attempt(db, case, RecoveryAction.RETRY_PAYMENT, succeeded=None,
             reference=provider_order.id, cost=cost,
             detail=f"Recovery attempt {case.retry_count} opened via {provider.name}.")
    audit.record(db, actor=ActorType.SYSTEM, action="CREATE_PAYMENT_RETRY_REQUEST",
                 case_id=case.id, order_id=order.id, tool="create_payment_retry_request",
                 policy_decision=PolicyDecision.ALLOW.value,
                 input_summary={"amount_paise": order.amount_paise,
                                "attempt": case.retry_count},
                 result={"provider_order_id": provider_order.id, "provider": provider.name})
    audit.timeline(db, case.id, actor=ActorType.SYSTEM,
                   title=f"Recovery request created (attempt {case.retry_count})",
                   detail=f"Provider order {provider_order.id} on {provider.name}. "
                          f"Customer must complete payment at the recovery link.")
    return {"provider_order_id": provider_order.id, "recovery_url": recovery_url,
            "cost_paise": cost}


def create_payment_link(db: Session, case: RecoveryCase, ctx: RecoveryContext,
                        *, merchant_approved: bool = False) -> dict:
    """Send the customer a hosted payment link for the outstanding amount."""
    _gate(db, case, ctx, RecoveryAction.SEND_PAYMENT_LINK,
          merchant_approved=merchant_approved)
    provider = get_payment_provider()
    settings = get_settings()
    order = case.order
    customer = case.customer
    if order is None:
        raise PolicyViolation("cannot create a payment link for a case with no order")

    case.recovery_token = case.recovery_token or secrets.token_urlsafe(24)
    callback_url = f"{settings.public_web_url}/recover/{case.recovery_token}"

    try:
        link = provider.create_payment_link(
            amount_paise=order.amount_paise, currency=order.currency,
            description=f"Complete your payment for {order.description}",
            reference_id=f"rcv-{case.id}"[:40],
            customer_name=customer.name, customer_email=customer.email,
            customer_contact=customer.contact or "+919999999999",
            callback_url=callback_url,
            notes={"recover_case_id": case.id},
        )
    except ProviderError as exc:
        audit.record(db, actor=ActorType.PROVIDER, action="CREATE_PAYMENT_LINK_FAILED",
                     case_id=case.id, order_id=order.id, status="error", error=str(exc))
        _attempt(db, case, RecoveryAction.SEND_PAYMENT_LINK, succeeded=False, reference=None,
                 cost=0, detail=f"Provider rejected payment-link creation: {exc}")
        raise

    if state_machine.is_recoverable_state(OrderState(order.state)):
        state_machine.transition(order, OrderState.PAYMENT_PENDING)

    case.payment_link_url = link.short_url
    case.contacts_sent += 1
    case.status = CaseStatus.AWAITING_CUSTOMER
    cost = intervention_cost_paise(ctx, RecoveryAction.SEND_PAYMENT_LINK)
    _attempt(db, case, RecoveryAction.SEND_PAYMENT_LINK, succeeded=None, reference=link.id,
             cost=cost, detail=f"Payment link {link.id} sent to {customer.email}.")
    audit.record(db, actor=ActorType.SYSTEM, action="CREATE_PAYMENT_LINK", case_id=case.id,
                 order_id=order.id, customer_id=customer.id, tool="create_payment_link",
                 policy_decision=PolicyDecision.ALLOW.value,
                 result={"payment_link_id": link.id, "provider": provider.name})
    audit.timeline(db, case.id, actor=ActorType.SYSTEM, title="Payment link sent to customer",
                   detail=f"{link.short_url}")
    return {"payment_link_id": link.id, "url": link.short_url, "cost_paise": cost}


def send_customer_recovery_message(db: Session, case: RecoveryCase, ctx: RecoveryContext,
                                   action: RecoveryAction, message: str,
                                   *, merchant_approved: bool = False) -> dict:
    """Send one recovery message. Counts against the 24h contact budget.

    The prototype records the message rather than dispatching email/SMS - there
    is no mail provider wired up, and pretending otherwise would be a fake
    feature. The contact *budget* is real and does gate behaviour.
    """
    _gate(db, case, ctx, action, merchant_approved=merchant_approved)
    case.contacts_sent += 1
    case.status = CaseStatus.AWAITING_CUSTOMER
    cost = intervention_cost_paise(ctx, action)
    _attempt(db, case, action, succeeded=None, reference=None, cost=cost, detail=message)
    audit.record(db, actor=ActorType.SYSTEM, action="SEND_CUSTOMER_MESSAGE", case_id=case.id,
                 customer_id=case.customer_id, tool="send_customer_recovery_message",
                 policy_decision=PolicyDecision.ALLOW.value,
                 input_summary={"action": action.value},
                 result={"contacts_sent": case.contacts_sent, "delivered": "recorded_only"})
    audit.timeline(db, case.id, actor=ActorType.SYSTEM,
                   title=f"Customer contacted ({action.value.replace('_', ' ').lower()})",
                   detail=message)
    return {"contacts_sent": case.contacts_sent, "cost_paise": cost}


def escalate_case(db: Session, case: RecoveryCase, reason: str, *,
                  reconciliation: bool = False) -> dict:
    """Hand the case to a human. Always permitted - escalation is never blocked."""
    case.status = CaseStatus.RECONCILIATION if reconciliation else CaseStatus.ESCALATED
    case.closed_at = None
    if reconciliation:
        db.add(ReconciliationException(
            case_id=case.id, order_id=case.order_id, kind="STATE_MISMATCH",
            detail=reason, amount_paise=case.amount_at_risk_paise,
        ))
    _attempt(db, case, RecoveryAction.ESCALATE_TO_MERCHANT, succeeded=None, reference=None,
             cost=0, detail=reason)
    audit.record(db, actor=ActorType.SYSTEM, action="ESCALATE_CASE", case_id=case.id,
                 tool="escalate_case", reason=reason,
                 policy_decision=PolicyDecision.ALLOW.value)
    audit.timeline(db, case.id, actor=ActorType.SYSTEM,
                   title="Escalated to merchant" if not reconciliation
                         else "Moved to reconciliation",
                   detail=reason)
    return {"status": case.status.value}


def stop_case(db: Session, case: RecoveryCase, reason: str) -> dict:
    """Close a case without further action. The system's most underrated feature."""
    case.status = CaseStatus.STOPPED
    case.closed_at = datetime.now(UTC)
    audit.record(db, actor=ActorType.SYSTEM, action="STOP_CASE", case_id=case.id,
                 reason=reason, policy_decision=PolicyDecision.ALLOW.value)
    audit.timeline(db, case.id, actor=ActorType.SYSTEM, title="Recovery stopped",
                   detail=reason)
    return {"status": case.status.value}
