"""Drive the demo end to end so the dashboard actually moves.

Run:  python scripts/demo_live.py            # reset, batch, let customers pay
      python scripts/demo_live.py --pay-rate 0.7
      python scripts/demo_live.py --no-reset  # just pay the open cases

Why this exists. After a recovery batch every eligible case is sitting at
AWAITING_CUSTOMER with a payment link, and recovered revenue is still zero -
because RECOVER only credits money when a signed webhook confirms it. For a
demo that means someone has to open each link and pay, one at a time, and the
headline number never moves.

This script plays the part of those customers. Each payment goes through
POST /api/recovery/{token}/simulate-payment, which feeds a *signed* webhook
into the real handler - the same verification, idempotency and state machine a
Razorpay delivery would hit. Nothing here writes recovered_amount_paise
directly, and nothing bypasses policy. It is the demo's customers, not a
shortcut past the system.

Which cases pay is chosen by a seeded RNG, so a recorded run is reproducible.

A customer *choosing* to pay is not the same as money arriving. This script
reports PAID only when the webhook handler says it credited the case, never
from the RNG's intent - the same rule the rest of the project holds itself to.
Abandoned checkouts are skipped: no payment was ever attempted on them, so
there is no provider order to capture and a simulated capture would only file
an orphan-payment exception.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request

DEFAULT_API = "http://localhost:8000"
MERCHANT = "mer_demo"


def call(api: str, path: str, payload: dict | None = None) -> dict | list:
    url = f"{api}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method="POST" if payload is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"HTTP {exc.code} on {path}: {exc.read().decode(errors='replace')}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"Cannot reach {api} ({exc.reason}). Is uvicorn running?")


def rupees(paise: int) -> str:
    return f"Rs {paise / 100:,.2f}"


def metrics(api: str) -> dict:
    return call(api, f"/api/dashboard/metrics?merchant_id={MERCHANT}")  # type: ignore[return-value]


def settle(api: str, quiet_rounds: int = 3, timeout: float = 30.0) -> list:
    """Wait for the batch to finish writing before reading its results.

    The batch endpoint returns as soon as the work is queued (BackgroundTasks,
    ADR-010), so recovery tokens keep landing after the response. Reading too
    early gives a short, racy list - or a token that is replaced under us.
    Poll until the count of payable cases holds still.
    """
    deadline = time.monotonic() + timeout
    stable = 0
    previous = -1
    cases: list = []
    while time.monotonic() < deadline:
        cases = call(api, "/api/cases?limit=200")  # type: ignore[assignment]
        count = sum(1 for c in cases if c.get("status") == "AWAITING_CUSTOMER")
        stable = stable + 1 if count == previous else 0
        previous = count
        if stable >= quiet_rounds and count:
            return cases
        time.sleep(0.4)
    return cases


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default=DEFAULT_API)
    p.add_argument("--pay-rate", type=float, default=0.6,
                   help="share of contacted customers who pay (default 0.6). "
                        "The rest fail again, which is the more interesting half.")
    p.add_argument("--seed", type=int, default=7, help="which customers pay; reproducible")
    p.add_argument("--delay", type=float, default=0.35,
                   help="seconds between payments, so the number climbs on camera")
    p.add_argument("--no-reset", action="store_true", help="skip the reset and batch")
    p.add_argument("--llm", action="store_true",
                   help="run the batch on the model path (needs ANTHROPIC_API_KEY)")
    args = p.parse_args(argv)

    if not args.no_reset:
        call(args.api, "/api/demo/seed", {})
        before = metrics(args.api)
        print(f"reset      {before['total_cases']} cases, "
              f"{rupees(before['revenue_at_risk']['paise'])} at risk, nothing recovered")

        batch = call(args.api, "/api/cases/batch/run",
                     {"limit": 200, "use_deterministic_engine": not args.llm})
        print(f"batch      {batch['processed']} processed - "  # type: ignore[index]
              f"{batch['executed']} acted on, "  # type: ignore[index]
              f"{batch['awaiting_approval']} held for a human")  # type: ignore[index]
        print()

    cases = settle(args.api)
    payable = []
    unsettleable = 0
    for summary in cases:  # type: ignore[union-attr]
        if summary.get("status") != "AWAITING_CUSTOMER":
            continue
        detail = call(args.api, f"/api/cases/{summary['id']}")
        token = detail.get("recovery_token")  # type: ignore[union-attr]
        if not token:
            continue
        if summary.get("event_type") == "CHECKOUT_ABANDONMENT":
            # Never had a payment attempt, so the provider has no order to
            # capture against. Pushing a capture through would raise an
            # ORPHAN_PAYMENT exception rather than recover anything.
            unsettleable += 1
            continue
        payable.append((detail, token))

    if not payable:
        print("No cases are awaiting a customer. Run without --no-reset first.")
        return 1

    rng = random.Random(args.seed)
    print(f"{len(payable)} customers were contacted. Watching them respond:\n")

    recovered = failed = uncredited = 0
    for detail, token in payable:
        pays = rng.random() < args.pay_rate
        result = call(args.api, f"/api/recovery/{token}/simulate-payment",
                      {"succeed": pays, "failure_reason": "INSUFFICIENT_FUNDS"})
        name = detail.get("customer_name", "?")  # type: ignore[union-attr]
        amount = detail["amount"]["paise"]  # type: ignore[index]
        effect = result.get("webhook", {}).get("effect", "")  # type: ignore[union-attr]
        if "recovered Rs" in effect:
            recovered += 1
            print(f"  PAID     {name:<20} {rupees(amount):>14}")
        elif not pays:
            failed += 1
            print(f"  failed   {name:<20} {rupees(amount):>14}   (retry budget spent)")
        else:
            # The customer paid but nothing was credited. Say so - a silent
            # PAID here would be exactly the overclaim the project forbids.
            uncredited += 1
            print(f"  no credit {name:<19} {rupees(amount):>14}   ({effect})")
        time.sleep(args.delay)

    after = metrics(args.api)
    print()
    print(f"{recovered} paid, {failed} failed again.")
    if uncredited:
        print(f"{uncredited} paid but were not credited - see the lines above.")
    if unsettleable:
        print(f"{unsettleable} abandoned checkouts were skipped: no payment was ever "
              f"attempted on them, so there is nothing to capture.")
    print()
    print(f"  revenue recovered   {rupees(after['recovered_revenue']['paise'])}")
    print(f"  intervention cost   {rupees(after['intervention_cost']['paise'])}")
    print(f"  net recovered       {rupees(after['net_recovered_revenue']['paise'])}")
    print(f"  still at risk       {rupees(after['revenue_at_risk']['paise'])}")
    print(f"  recovery rate       {after['recovery_rate']:.1%}")
    print(f"  stopped             {after['stopped_cases']} cases")
    print(f"  held for a human    {after['awaiting_approval']} cases")
    print()
    print("Every rupee above was credited by a signature-verified webhook.")
    print("Refresh the dashboard at http://localhost:3000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
