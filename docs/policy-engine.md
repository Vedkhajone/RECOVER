# Policy engine

`apps/api/recover/policy.py`. One function is the entire security boundary:

```python
def evaluate(ctx: RecoveryContext, action: RecoveryAction | str) -> PolicyVerdict
```

Every recovery action — proposed by the AI, clicked by a merchant, produced by the batch
runner — passes through it before anything happens. There is no second path to execution.

## Design rules

**Pure.** No I/O, no clock reads, no model calls, no database access. Elapsed time is
supplied on the context by the caller. The same context always yields the same verdict,
which is what lets a verdict be written into an audit log and defended six months later.
There is a test that runs one evaluation twenty times and asserts a single distinct
result.

**Default deny.** An action outside the closed vocabulary is `BLOCK` / `UNKNOWN_ACTION`.
The system fails closed on anything it does not recognise.

**Machine-readable reasons.** Every verdict carries a `code`. The UI, the audit log, the
timeline and the tests all refer to the same reason by the same name, so "why didn't it
retry?" has exactly one answer everywhere.

**Explains itself in the merchant's terms.** Alongside the code, a sentence: *"₹12,499.00
exceeds the merchant approval threshold of ₹10,000.00. Merchant approval is required
before this action runs."*

## The three verdicts

| Verdict | Meaning |
|---|---|
| `ALLOW` | Execute now. |
| `REQUIRE_APPROVAL` | A human must say yes. Nothing runs meanwhile. |
| `BLOCK` | Never, for this case as it currently stands. |

**`REQUIRE_APPROVAL` and `BLOCK` are not degrees of the same thing.** Merchant approval
satisfies the first and cannot touch the second:

```python
acceptable = {PolicyDecision.ALLOW}
if merchant_approved:
    acceptable.add(PolicyDecision.REQUIRE_APPROVAL)
if verdict.decision not in acceptable:
    raise PolicyViolation(...)
```

A merchant can approve a ₹12,499 retry. No one can approve retrying a cancelled order.

## Evaluation order

```
1. Coerce the action        unknown → BLOCK / UNKNOWN_ACTION
2. Inert actions            WAIT, STOP, ESCALATE_TO_MERCHANT → ALLOW, always
3. Universal guards         conditions under which nothing outward is permitted
4. Contact budget           for the three actions that message a customer
5. Action-specific rules    retry limits, intervals, non-retryable failures, channels
6. Amount gates             for the three money-moving actions
7. Expected-value floor     refuse to spend more chasing than the money is worth
8. ALLOW
```

**Step 2 matters more than it looks.** `STOP` is unconditionally permitted — on any case,
in any state, including ones where every other action is refused. A system that could be
prevented from doing nothing would have no safe fallback. There is a test asserting `STOP`,
`WAIT` and `ESCALATE_TO_MERCHANT` are allowed across cancelled, already-paid, exhausted,
expired and zero-amount cases.

## Every rule

### Universal guards — no outward action, whatever was proposed

| Code | Condition | Why |
|---|---|---|
| `INVALID_ORDER` | amount ≤ 0, or no order on a non-abandonment case | Nothing to recover. |
| `ORDER_ALREADY_PAID` | order captured or completed | Retrying would double-charge. The most expensive possible bug. |
| `ORDER_REFUNDED` | order refunded | The balance is settled. |
| `ORDER_CANCELLED` | order cancelled | The customer said no. |
| `RISK_FLAG_PRESENT` | customer flagged, or `fraud_suspected` | Automated recovery is off for flagged accounts, full stop. |
| `CASE_EXPIRED` | older than `case_expiry_hours` (72) | Chasing indefinitely is not a strategy. |
| `DUPLICATE_EVENT` | event class is `DUPLICATE_PAYMENT` | Already collected once. |
| `REQUIRES_RECONCILIATION` | event class is `STATE_MISMATCH` | Needs a human, not a recovery action. |

### Contact budget — `SEND_PAYMENT_LINK`, `SEND_REMINDER`, `OFFER_ALLOWED_ALTERNATIVE`

`CONTACT_LIMIT_EXCEEDED` when contacts in 24h ≥ `max_customer_contacts_24h` (default 2).

Deliberately **not** applied to `RETRY_PAYMENT` — a retry is not a message, and confusing
the two would make the contact budget mean nothing. There is a test for that.

### Retry rules — `RETRY_PAYMENT`

| Code | Condition |
|---|---|
| `NON_RETRYABLE_FAILURE` | failure class in `NEVER_AUTO_RETRY`: expired card, revoked mandate, deliberate cancellation, risk decline |
| `MANDATE_INACTIVE` | subscription mandate inactive — no debit is authorised |
| `RETRY_LIMIT_EXCEEDED` | attempts ≥ `max_auto_retries` (default 2) |
| `RETRY_INTERVAL_NOT_ELAPSED` | last attempt < `min_retry_interval_minutes` (default 30) ago |

`NEVER_AUTO_RETRY` is the rule that stops the product being a blind retry loop. Those four
classes cannot succeed on a retry, so retrying only annoys the customer. An expired card
is still *recoverable* — via `OFFER_ALLOWED_ALTERNATIVE`, which policy allows.

### Channel switches

`PAYMENT_LINK_DISABLED` and `ALTERNATIVE_METHOD_DISABLED` when the merchant has turned
that channel off. Toggling either on the Policy screen changes the next decision
immediately; there is an e2e test that flips a switch and asserts the verdict changes.

### Amount gates — money-moving actions only

| Code | Condition | Verdict |
|---|---|---|
| `ABOVE_APPROVAL_THRESHOLD` | amount > ₹10,000 | `REQUIRE_APPROVAL` |
| `ABOVE_AUTO_RECOVERY_LIMIT` | amount > ₹5,000 | `REQUIRE_APPROVAL` |

Boundaries are inclusive: ₹5,000 exactly is allowed, ₹5,001 is not. Tested at the boundary,
because that is where this kind of rule is always wrong.

### Expected value

`NEGATIVE_EXPECTED_VALUE` when `amount × P(recovery) − intervention cost` falls below the
merchant's `min_expected_value_paise` floor.

Default floor is ₹0, so out of the box this gate rarely fires — see
[evaluation.md](evaluation.md) for why, and why we did not quietly raise it to make the
system look more restrained than it is. Raising it on the Policy screen genuinely stops
the system chasing low-value long shots.

## Defence in depth

The orchestrator gates on policy. Each action-tier function *also* gates, for itself:

```python
def create_payment_retry_request(db, case, ctx, *, merchant_approved=False):
    _gate(db, case, ctx, RecoveryAction.RETRY_PAYMENT, merchant_approved=merchant_approved)
    ...
```

If the orchestrator's gate were ever removed or bypassed, this one still refuses. A
`PolicyViolation` raised here means a code path tried to skip the gate, so it is logged as
`ACTION_TIER_REFUSED` — a security event, not a business one. Tested by calling the
action tier directly on a cancelled order and asserting both the refusal and the audit
row.

## Making refusals visible

A subtlety worth naming. The deterministic engine consults `permitted_actions()` while
choosing, so it almost never *submits* a blocked action — it quietly picks its second
choice. A naive "actions blocked" counter would read zero and imply the policy engine was
doing nothing.

Two fixes, both about honesty:

1. **Every case stores the full 7-action verdict matrix**, rendered in the decision panel
   under "What policy allows". A refusal is visible even when the engine routed around it.
2. **The evaluation scores the engine's unconstrained first choice separately**, so
   "unsafe actions blocked by policy" counts what policy actually prevented (166 on the
   held-out split) rather than what happened to reach it.

## Testing

35 tests in `apps/api/tests/test_policy.py`, one per rule a merchant would state in a
sentence, including:

- ₹4,999 transient failure → allowed
- ₹5,001 → not automatic
- third retry → blocked
- cancelled / already paid / refunded → blocked
- risk-flagged → every outward action blocked
- contact limit at 0, 1, 2
- unknown action → denied by default
- `STOP` never blocked, across six hostile contexts
- policy is pure

Plus the end-to-end proof that a schema-valid AI proposal for a forbidden action is still
refused.
