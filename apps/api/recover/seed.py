"""Deterministic demo seed.

Judges get the same data every time. Nothing here is random, and the seven
scenarios in the demo script are each seeded as a real, individually named case
rather than being hoped for out of a random batch.

The bulk cases come from the same synthetic generator the evaluation uses, so
the dashboard is populated by the identical distribution the metrics describe -
not by a separate, prettier fixture set.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .db import Base, engine, session_scope
from .enums import (
    CustomerSegment,
    EventType,
    FailureReason,
    OrderState,
    PaymentStatus,
)
from .models import (
    AuditLog,
    CaseEvent,
    Customer,
    Merchant,
    MerchantPolicy,
    Order,
    Payment,
    ReconciliationException,
    RecoveryAttempt,
    RecoveryCase,
    WebhookEvent,
)

DEMO_MERCHANT_ID = "mer_demo"

#: Seed timestamps are anchored to the real clock, not to a literal date.
#: The *content* of the seed is fully deterministic; only the timestamps move,
#: and they have to, because policy decisions depend on elapsed time. A fixed
#: literal here meant every seeded case looked either zero or hundreds of hours
#: old depending on when the demo ran, and the retry-interval rule misfired.
NOW = datetime.now(UTC)


def _customer(db: Session, cid: str, name: str, email: str, segment: CustomerSegment,
              successes: int, failures: int, *, risk: bool = False) -> Customer:
    customer = Customer(
        id=cid, merchant_id=DEMO_MERCHANT_ID, name=name, email=email,
        contact="+919876543210", segment=segment, successful_payments=successes,
        failed_payments=failures, lifetime_value_paise=successes * 250_000,
        risk_flagged=risk, created_at=NOW - timedelta(days=400),
    )
    db.add(customer)
    return customer


def _order(db: Session, oid: str, customer: Customer, reference: str, description: str,
           amount_paise: int, state: OrderState, *, minutes_ago: int = 12) -> Order:
    order = Order(
        id=oid, merchant_id=DEMO_MERCHANT_ID, customer_id=customer.id, reference=reference,
        description=description, amount_paise=amount_paise, state=state,
        created_at=NOW - timedelta(minutes=minutes_ago),
    )
    db.add(order)
    return order


def _payment(db: Session, pid: str, order: Order, status: PaymentStatus,
             reason: FailureReason, *, minutes_ago: int = 12,
             method: str = "card") -> Payment:
    from .dataset.generator import PROVIDER_ERROR_CODES

    code, error_reason, description = PROVIDER_ERROR_CODES[reason]
    payment = Payment(
        id=pid, order_id=order.id, provider_payment_id=f"pay_demo_{pid[-8:]}",
        amount_paise=order.amount_paise, status=status, method=method,
        failure_reason=reason, provider_error_code=code,
        provider_error_description=description,
        created_at=NOW - timedelta(minutes=minutes_ago),
    )
    db.add(payment)
    return payment


def _case(db: Session, case_id: str, customer: Customer, order: Order | None,
          payment: Payment | None, event_type: EventType, *, minutes_ago: int = 12,
          retry_count: int = 0, contacts: int = 0,
          last_action_minutes_ago: int | None = None) -> RecoveryCase:
    case = RecoveryCase(
        id=case_id, merchant_id=DEMO_MERCHANT_ID, customer_id=customer.id,
        order_id=order.id if order else None, payment_id=payment.id if payment else None,
        event_type=event_type,
        amount_at_risk_paise=order.amount_paise if order else 0,
        retry_count=retry_count, contacts_sent=contacts,
        opened_at=NOW - timedelta(minutes=minutes_ago),
        last_action_at=(NOW - timedelta(minutes=last_action_minutes_ago)
                        if last_action_minutes_ago is not None else None),
    )
    db.add(case)
    db.add(CaseEvent(case_id=case_id, actor="system",
                     title="RECOVER detected revenue at risk",
                     detail=f"{event_type.value.replace('_', ' ').title()} worth "
                            f"Rs {(order.amount_paise if order else 0) / 100:,.2f}",
                     created_at=NOW - timedelta(minutes=minutes_ago)))
    return case


def wipe(db: Session) -> None:
    """Clear demo state.

    EvaluationRun is deliberately not in this list. Evaluation results are
    evidence about the system, not demo furniture, and re-seeding the demo
    between takes must not quietly delete the numbers on the evaluation screen.
    """
    for model in (CaseEvent, RecoveryAttempt, AuditLog, ReconciliationException,
                  WebhookEvent, RecoveryCase, Payment, Order, Customer,
                  MerchantPolicy, Merchant):
        db.execute(delete(model))


def seed(db: Session, *, bulk_cases: int = 24) -> dict:
    """Reset to the deterministic demo state."""
    wipe(db)
    db.add(Merchant(id=DEMO_MERCHANT_ID, name="Northwind Commerce",
                    created_at=NOW - timedelta(days=800)))
    db.add(MerchantPolicy(merchant_id=DEMO_MERCHANT_ID))
    db.flush()

    # ---- Scenario 1 & 2: the live recovery case ---------------------------
    # A reliable customer, a transient bank failure, Rs 4,999. Retry is within
    # every policy limit, so this is the case the demo actually recovers.
    priya = _customer(db, "cus_demo_priya", "Priya Sharma", "priya@example.com",
                      CustomerSegment.LOYAL, successes=8, failures=1)
    order1 = _order(db, "ord_demo_0001", priya, "ORD-18392", "Wireless Headphones",
                    499_900, OrderState.PAYMENT_FAILED)
    pay1 = _payment(db, "pay_demo_0001", order1, PaymentStatus.FAILED,
                    FailureReason.BANK_TRANSIENT)
    _case(db, "case_demo_retry", priya, order1, pay1, EventType.PAYMENT_FAILURE)

    # ---- Scenario 2b: retry limit already exhausted -----------------------
    # Two attempts already made. Whatever the model proposes, policy stops it.
    rahul = _customer(db, "cus_demo_rahul", "Rahul Verma", "rahul@example.com",
                      CustomerSegment.REGULAR, successes=5, failures=4)
    order2 = _order(db, "ord_demo_0002", rahul, "ORD-18393", "Fitness Membership",
                    599_900, OrderState.PAYMENT_FAILED, minutes_ago=180)
    pay2 = _payment(db, "pay_demo_0002", order2, PaymentStatus.FAILED,
                    FailureReason.INSUFFICIENT_FUNDS, minutes_ago=95)
    # Both budgets are spent: 2 of 2 retries, 2 of 2 contacts in 24h. Whatever
    # is proposed, every outward action is refused and the case must stop.
    _case(db, "case_demo_retrylimit", rahul, order2, pay2, EventType.PAYMENT_FAILURE,
          minutes_ago=180, retry_count=2, contacts=2, last_action_minutes_ago=95)

    # ---- Scenario 3: above the automatic recovery limit -------------------
    aditya = _customer(db, "cus_demo_aditya", "Aditya Nair", "aditya@example.com",
                       CustomerSegment.LOYAL, successes=14, failures=1)
    order3 = _order(db, "ord_demo_0003", aditya, "ORD-18394", "Team Seat Add-on",
                    1_249_900, OrderState.PAYMENT_FAILED, minutes_ago=40)
    pay3 = _payment(db, "pay_demo_0003", order3, PaymentStatus.FAILED,
                    FailureReason.BANK_TRANSIENT, minutes_ago=40)
    _case(db, "case_demo_approval", aditya, order3, pay3, EventType.PAYMENT_FAILURE,
          minutes_ago=40)

    # ---- Scenario 4: customer cancelled -----------------------------------
    meera = _customer(db, "cus_demo_meera", "Meera Iyer", "meera@example.com",
                      CustomerSegment.REGULAR, successes=6, failures=2)
    order4 = _order(db, "ord_demo_0004", meera, "ORD-18395", "Ergonomic Chair",
                    1_849_900, OrderState.CANCELLED, minutes_ago=60)
    pay4 = _payment(db, "pay_demo_0004", order4, PaymentStatus.FAILED,
                    FailureReason.CUSTOMER_CANCELLED, minutes_ago=60)
    _case(db, "case_demo_cancelled", meera, order4, pay4, EventType.PAYMENT_FAILURE,
          minutes_ago=60)

    # ---- Scenario 6: payment captured, order left stale -------------------
    # Detected by the reconciliation sweep, not by a recovery case.
    kabir = _customer(db, "cus_demo_kabir", "Kabir Anand", "kabir@example.com",
                      CustomerSegment.REGULAR, successes=9, failures=0)
    order5 = _order(db, "ord_demo_0005", kabir, "ORD-18396", "Annual SaaS Plan",
                    1_199_900, OrderState.PAYMENT_PENDING, minutes_ago=220)
    _payment(db, "pay_demo_0005", order5, PaymentStatus.CAPTURED, FailureReason.NONE,
             minutes_ago=215)

    # ---- Scenario: a risk-flagged customer --------------------------------
    suspect = _customer(db, "cus_demo_flagged", "Vikram Rao", "vikram@example.com",
                        CustomerSegment.AT_RISK, successes=1, failures=7, risk=True)
    order6 = _order(db, "ord_demo_0006", suspect, "ORD-18397", "Smart Watch",
                    899_900, OrderState.PAYMENT_FAILED, minutes_ago=25)
    pay6 = _payment(db, "pay_demo_0006", order6, PaymentStatus.FAILED,
                    FailureReason.RISK_DECLINED, minutes_ago=25)
    _case(db, "case_demo_risk", suspect, order6, pay6, EventType.PAYMENT_FAILURE,
          minutes_ago=25)

    # ---- Scenario: expired card, retry can never work ---------------------
    nisha = _customer(db, "cus_demo_nisha", "Nisha Gupta", "nisha@example.com",
                      CustomerSegment.LOYAL, successes=22, failures=2)
    order7 = _order(db, "ord_demo_0007", nisha, "ORD-18398", "Coffee Subscription",
                    89_900, OrderState.PAYMENT_FAILED, minutes_ago=75)
    pay7 = _payment(db, "pay_demo_0007", order7, PaymentStatus.FAILED,
                    FailureReason.CARD_EXPIRED, minutes_ago=75)
    _case(db, "case_demo_expired", nisha, order7, pay7,
          EventType.SUBSCRIPTION_PAYMENT_FAILURE, minutes_ago=75)

    # ---- Scenario: abandoned checkout -------------------------------------
    arjun = _customer(db, "cus_demo_arjun", "Arjun Menon", "arjun@example.com",
                      CustomerSegment.NEW, successes=1, failures=0)
    order8 = _order(db, "ord_demo_0008", arjun, "ORD-18399", "Running Shoes",
                    349_900, OrderState.CREATED, minutes_ago=95)
    _case(db, "case_demo_abandon", arjun, order8, None, EventType.CHECKOUT_ABANDONMENT,
          minutes_ago=95)

    # ---- Bulk cases from the synthetic generator --------------------------
    added = _seed_bulk(db, bulk_cases)

    db.flush()
    return {
        "merchant_id": DEMO_MERCHANT_ID,
        "scenario_cases": 7,
        "bulk_cases": added,
        "live_demo_case_id": "case_demo_retry",
    }


def _seed_bulk(db: Session, count: int) -> int:
    """Project synthetic records into real rows so the dashboard has volume."""
    from .dataset.generator import PROVIDER_ERROR_CODES, generate_records

    records = [
        r for r in generate_records(seed=7, size=400)
        if r["event_type"] in (EventType.PAYMENT_FAILURE.value,
                               EventType.CHECKOUT_ABANDONMENT.value,
                               EventType.SUBSCRIPTION_PAYMENT_FAILURE.value,
                               EventType.OVERDUE_INVOICE.value)
        and not r["order_already_paid"] and not r["order_refunded"]
    ][:count]

    seen: dict[str, Customer] = {}
    for i, r in enumerate(records):
        cid = f"cus_bulk_{i:04d}"
        customer = _customer(
            db, cid, f"Customer {i + 1:03d}", f"customer{i + 1:03d}@example.com",
            CustomerSegment(r["customer_segment"]), r["previous_success_count"],
            r["previous_failure_count"], risk=r["risk_flagged"],
        )
        seen[cid] = customer
        order = _order(db, f"ord_bulk_{i:04d}", customer, f"ORD-{30000 + i}",
                       r["description"], r["amount_paise"], OrderState(r["order_state"]),
                       minutes_ago=r["minutes_since_event"])
        payment = None
        if r["failure_reason"] != FailureReason.NONE.value:
            code, _, description = PROVIDER_ERROR_CODES[FailureReason(r["failure_reason"])]
            payment = Payment(
                id=f"pay_bulk_{i:04d}", order_id=order.id,
                provider_payment_id=f"pay_bulk_prov_{i:04d}",
                amount_paise=order.amount_paise, status=PaymentStatus(r["payment_status"]),
                method=r["payment_method"], failure_reason=FailureReason(r["failure_reason"]),
                provider_error_code=code, provider_error_description=description,
                created_at=NOW - timedelta(minutes=r["minutes_since_event"]),
            )
            db.add(payment)
        _case(db, f"case_bulk_{i:04d}", customer, order, payment,
              EventType(r["event_type"]), minutes_ago=r["minutes_since_event"],
              retry_count=r["retry_count"], contacts=r["contacts_last_24h"],
              last_action_minutes_ago=r["minutes_since_last_attempt"])
    return len(records)


def reset_and_seed() -> dict:
    Base.metadata.create_all(bind=engine)
    with session_scope() as db:
        return seed(db)
