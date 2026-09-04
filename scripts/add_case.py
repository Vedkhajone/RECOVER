"""Create a single recovery case from the command line.

Run:  python scripts/add_case.py --name "Kavya Menon" --amount 3499
      python scripts/add_case.py --amount 22000            # forces approval
      python scripts/add_case.py --risk-flagged            # forces a stop
      python scripts/add_case.py --event CHECKOUT_ABANDONMENT --reason NONE

This is a thin wrapper over POST /api/ingest/event, so a case made here goes
through the same detection, agent and policy engine as a seeded one. It gets no
privileges: an amount above the merchant's approval threshold is held for a
human here exactly as it would be anywhere else.

The API must already be running (uvicorn on --api, default localhost:8000).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

EVENTS = [
    "PAYMENT_FAILURE",
    "CHECKOUT_ABANDONMENT",
    "SUBSCRIPTION_PAYMENT_FAILURE",
    "OVERDUE_INVOICE",
]
REASONS = [
    "BANK_TRANSIENT", "NETWORK_ERROR", "INSUFFICIENT_FUNDS", "CARD_EXPIRED",
    "INCORRECT_CVV", "DO_NOT_HONOUR", "AUTHENTICATION_FAILED", "RISK_DECLINED",
    "MANDATE_INACTIVE", "CUSTOMER_CANCELLED", "NONE",
]
SEGMENTS = ["LOYAL", "REGULAR", "NEW", "AT_RISK"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--merchant", default="mer_demo")

    p.add_argument("--name", default="Test Customer")
    p.add_argument("--email", help="defaults to a slug of --name")
    p.add_argument("--contact", default="+919800000000")
    p.add_argument("--segment", choices=SEGMENTS, default="REGULAR")
    p.add_argument("--successful", type=int, default=6,
                   help="prior successful payments - the strongest recovery signal")
    p.add_argument("--failed", type=int, default=1, help="prior failed payments")
    p.add_argument("--risk-flagged", action="store_true",
                   help="fraud flag; policy blocks every outward action")

    p.add_argument("--amount", default="4999.00",
                   help="rupees, e.g. 4999.00. Above the approval threshold "
                        "the case is held for a human.")
    p.add_argument("--order", help="order reference; defaults to a timestamped one")
    p.add_argument("--description", default="Test Order")
    p.add_argument("--event", choices=EVENTS, default="PAYMENT_FAILURE")
    p.add_argument("--reason", choices=REASONS, default="BANK_TRANSIENT")

    p.add_argument("--defer", action="store_true",
                   help="open the case but do not run the recovery cycle yet")
    p.add_argument("--llm", action="store_true",
                   help="use the model path instead of the deterministic fallback "
                        "(needs ANTHROPIC_API_KEY)")
    p.add_argument("--json", action="store_true", help="print the raw response")
    return p.parse_args(argv)


def build_payload(args: argparse.Namespace) -> dict:
    try:
        Decimal(args.amount)
    except InvalidOperation:
        raise SystemExit(f"--amount {args.amount!r} is not a number.")

    slug = "".join(c for c in args.name.lower() if c.isalnum() or c == " ").replace(" ", ".")
    # A timestamped reference by default, because a repeated reference is
    # treated as a duplicate report rather than a new case.
    import time
    reference = args.order or f"ORD-CLI-{int(time.time())}"

    # CHECKOUT_ABANDONMENT means nothing was ever attempted, so there is no
    # failure to name.
    reason = "NONE" if args.event == "CHECKOUT_ABANDONMENT" else args.reason

    return {
        "customer": {
            "name": args.name,
            "email": args.email or f"{slug or 'test'}@example.com",
            "contact": args.contact,
            "segment": args.segment,
            "successful_payments": args.successful,
            "failed_payments": args.failed,
            "risk_flagged": args.risk_flagged,
        },
        "order_reference": reference,
        "description": args.description,
        "amount_inr": args.amount,
        "event_type": args.event,
        "failure_reason": reason,
        "process_immediately": not args.defer,
        "use_deterministic_engine": not args.llm,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build_payload(args)
    url = f"{args.api}/api/ingest/event?merchant_id={args.merchant}"
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )

    try:
        with urllib.request.urlopen(request) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        print(f"HTTP {exc.code}: {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Cannot reach {args.api} ({exc.reason}). Is uvicorn running?",
              file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(body, indent=2))
        return 0

    print(f"case      {body['case_id']}")
    print(f"amount    {body['amount']['inr']:,.2f} INR ({body['amount']['paise']} paise)")
    print(f"status    {body['status']}")
    print(f"outcome   {body['message']}")
    if body.get("recovery_url"):
        print(f"pay here  {body['recovery_url']}")
    print(f"open      http://localhost:3000/cases/{body['case_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
