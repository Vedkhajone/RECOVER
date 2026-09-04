# Failure recovery

A recovery system that only demonstrates recoveries is demonstrating half a product. The
harder half is knowing when *not* to act, and being able to explain the refusal.

Every failure surface in RECOVER answers the same five questions:

1. What happened
2. Why it happened
3. What the system did
4. Why that was safe
5. What the next step is

## The seven scenarios

All seven ship seeded (`recover/seed.py`) and are listed on the dashboard with their live
case status. They are deterministic — the demo does not depend on a random batch happening
to produce an interesting case.

---

### 1 · Payment retry succeeds

**Case** `case_demo_retry` — Priya Sharma, ₹4,999, Wireless Headphones, `BANK_TRANSIENT`,
8 of 9 prior payments successful.

Diagnosis: the issuer was temporarily unavailable; the instrument is fine. Recommends
`RETRY_PAYMENT`. Policy: **ALLOW** / `WITHIN_POLICY` — ₹4,999 ≤ ₹5,000, 0 of 2 retries
used, no risk flag. A provider order and a single-use recovery link are created, the order
moves `PAYMENT_FAILED → PAYMENT_PENDING`, the customer pays, the webhook is verified, and
₹4,999 is credited.

**What it proves.** The full lifecycle, and that the recovered figure comes from a
signature-verified event rather than an optimistic UI update.

---

### 2 · Retry fails again, and the system stops

**Case** `case_demo_retrylimit` — Rahul Verma, ₹5,999, `INSUFFICIENT_FUNDS`, 2 of 2
retries used, 2 of 2 contacts used.

Both budgets are spent. `RETRY_PAYMENT` → `RETRY_LIMIT_EXCEEDED`. `SEND_PAYMENT_LINK` and
`SEND_REMINDER` → `CONTACT_LIMIT_EXCEEDED`. Four of seven actions refused. The engine
settles on `STOP` and closes the case.

You can also reach this live: complete a recovery on scenario 1 using *"Simulate a second
failure"*. The webhook records the failure, reopens the case, and the next cycle finds the
retry budget spent.

**What it proves.** Stopping is a designed outcome, not an absence of one. The decision
panel shows all four refusals with their codes.

---

### 3 · Amount exceeds the automatic limit

**Case** `case_demo_approval` — Aditya Nair, ₹12,499, `BANK_TRANSIENT`, a loyal customer.

The diagnosis is the same as scenario 1 and the recommendation is the same —
`RETRY_PAYMENT`. Policy: **REQUIRE_APPROVAL** / `ABOVE_APPROVAL_THRESHOLD`.

The case goes to `AWAITING_APPROVAL` and **nothing runs**: no provider order, no retry
counter increment, no attempt row. Approve on the case page and the held action executes;
decline and the case stops.

**What it proves.** Automation has a ceiling, and above it a human decides. Also that
`REQUIRE_APPROVAL` is a real hold rather than a warning label — there is a test asserting
zero side effects while a case waits.

---

### 4 · Customer cancelled

**Case** `case_demo_cancelled` — Meera Iyer, ₹18,499, order `CANCELLED`.

Every outward action returns **BLOCK** / `ORDER_CANCELLED`. No retry, no message, no
provider call. The case is closed with the reason on the timeline.

**What it proves.** A cancellation is respected. This is the scenario where a naive
"retry everything" system charges someone who explicitly said no.

---

### 5 · Duplicate webhook

**Audit → Webhook events → Replay.**

The stored delivery is re-signed and pushed through the real handler. The second delivery
hits genuine signature verification and genuine idempotency:

```
delivery 1  →  200  processed   "case case_demo_retry recovered Rs 4,999.00"
delivery 2  →  200  duplicate   "Duplicate event detected — safely ignored."
delivery 3  →  200  duplicate   delivery_count: 3
```

One ledger row, one payment row, one credited amount.

**Two details that are easy to get wrong.** The duplicate returns **200**, not 4xx —
Razorpay retries on anything else, so rejecting a duplicate creates an infinite redelivery
loop. And the ledger row is inserted and flushed *before* the side effect, so the unique
constraint settles a concurrent double delivery rather than a read-then-write race.

**What it proves.** Revenue cannot be double-counted by a retried webhook.

---

### 6 · Payment captured, order left stale

**Dashboard → Reconcile.** Seeded as `ord_demo_0005` — a ₹11,999 payment in `captured`
while the order sits in `PAYMENT_PENDING`.

The sweep opens a `STATE_MISMATCH` case. Policy refuses every recovery action with
`REQUIRES_RECONCILIATION` — this is not revenue at risk, the money is already in hand and
the ledger is wrong. The case routes to `RECONCILIATION` and raises an exception on the
Audit screen.

The webhook handler has the same instinct in two other places: a captured payment matching
no known order raises `ORPHAN_PAYMENT` rather than guessing; a capture against a cancelled
order raises `CAPTURE_ON_INVALID_STATE` and **leaves the order state untouched**.

**What it proves.** The system does not paper over an inconsistency by mutating state
until it looks tidy. It names the problem and gives it to a human.

---

### 7 · A forbidden action is proposed

**Case page → Policy probe.** Choose `RETRY_PAYMENT` on the cancelled case:

```
BLOCK   ORDER_CANCELLED
The customer cancelled this order. Recovery is not attempted.
```

The probe evaluates and never executes, so it is safe to expose. It is also honest: the
operator chose the action, and nothing is attributed to the model.

The same guarantee is tested directly, without the UI:

- `test_valid_json_alone_does_not_authorise_an_action` — schema-valid model output
  proposing a retry on a cancelled order is refused by policy.
- `test_the_action_tier_refuses_a_forbidden_action_even_if_called_directly` — bypassing
  the orchestrator and calling `create_payment_retry_request` still raises
  `PolicyViolation`, logs `ACTION_TIER_REFUSED`, and leaves the retry counter at zero.
- `test_the_model_cannot_grant_itself_approval` — `requires_approval: false` in the
  model's output does not make an over-limit action executable.

**What it proves.** The gate is structural. It holds even if the model is wrong, and even
if a future code path forgets to check.

---

## Failures the system handles beyond the seven

| Failure | Behaviour |
|---|---|
| Anthropic API down, rate-limited, or returning nonsense | Degrade to the labelled deterministic fallback; case shows an amber "AI path degraded" panel with the error |
| Model returns an invented action or extra fields | Schema validation fails → fallback; nothing executes |
| Model answers about a different case | Case-id pinning rejects it → fallback |
| Razorpay API error while creating an order | Attempt recorded as failed, case escalated, **no money moved** |
| Webhook with a bad signature | Recorded, rejected with 400, business logic never runs |
| Webhook with a tampered body | Same — the HMAC is over raw bytes |
| Malformed webhook JSON with a valid signature | Rejected as malformed |
| Invalid state transition | `InvalidStateTransition` → 409 naming the transitions that *would* have been legal |
| One case explodes mid-batch | Rolled back and counted in `errors`; the other cases still run |
| Backend unreachable from the browser | Every screen shows a specific error with a retry, not a blank page |
| No evaluation run yet | The Evaluation screen explains which command to run |

## Where failures are visible

- **Case timeline** — every refusal, degradation and provider error, in order.
- **Decision panel** — the full 7-action policy matrix with reasons, so a refusal is
  visible even when the engine quietly routed around it.
- **Audit trail** — `ACTION_TIER_REFUSED`, `WEBHOOK_REJECTED`, `EXECUTION_FAILED`,
  `WEBHOOK_DUPLICATE`, all filterable by status.
- **Exceptions list** — everything unresolved, with amounts.
- **Evaluation screen** — held-out cases the system got wrong, largest first.

Nothing is swallowed. The Exceptions list existing at all is the point: an exception you
cannot see is worse than one you can.
