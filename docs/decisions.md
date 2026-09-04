# Architectural decision records

---

## ADR-001 — AI is separated from payment execution

**Decision.** The model can call read-only tools and emit one structured proposal. It has
no path to any function that moves money or contacts a customer.

**Why.** The failure mode that matters is not bad advice, it is bad advice becoming an
irreversible action. Charging a cancelled order, double-charging a captured one, or
messaging someone six times cannot be undone by noticing afterwards. Prompt instructions
reduce the probability of a mistake; they do not bound its consequence. Removing the
capability bounds it at zero.

**Consequence.** The model cannot act even when it is right and the gate is wrong. That
is the correct trade for money.

---

## ADR-002 — Policy is deterministic and pure

**Decision.** `policy.evaluate()` is a pure function: no I/O, no clock reads, no model
calls. Elapsed time arrives on the context.

**Why.** Three things follow from purity and nothing else provides them:

1. **Auditability.** A verdict written into an audit log can be re-derived and defended
   six months later.
2. **Testability.** 35 tests, no mocks, sub-millisecond.
3. **Explainability.** "Why didn't it retry?" has exactly one answer — a code the UI, the
   log and the tests all share.

**Rejected alternative.** An LLM judge for policy. Slower, more expensive, untestable, and
it would make the answer to "may we charge this customer?" probabilistic.

---

## ADR-003 — Synthetic data for evaluation

**Decision.** 10,000 seeded synthetic events with a latent ground-truth model, rather than
a small hand-labelled set or no evaluation at all.

**Why.** Real payment-failure data with recovery outcomes is not publicly available, and
would be the merchant's confidential data if it were. The alternative — reporting no
numbers — makes "our AI recovers revenue" unfalsifiable.

**How the honesty is protected.** Ground truth is structurally independent from the
system's scorer and uses different coefficients. Observation noise means the observed
record can disagree with the world, so detection metrics are not trivially 1.0. An
explicit allow-list stops latent fields reaching the model, with tests.

**Consequence, stated plainly.** These numbers describe behaviour under designed
assumptions, not production performance. Recovered revenue is simulated. Said in the
README, in `docs/evaluation.md`, on the Evaluation screen, and in the CLI output.

---

## ADR-004 — Razorpay Test Mode, and a refusal to run on live keys

**Decision.** Test Mode only. The API **refuses to start** if `RAZORPAY_KEY_ID` does not
begin with `rzp_test_`.

**Why.** A prototype that retries payments is exactly the kind of software that must never
be pointed at live money by an environment-variable mistake. A convention ("please use
test keys") is not a control. A startup assertion is.

**Consequence.** Someone with a legitimate reason to run against live keys cannot, without
editing the code. Correct for a hackathon prototype.

---

## ADR-005 — A payment provider port

**Decision.** Everything talks to `PaymentProvider`. Razorpay and the local simulator both
sit behind it.

**Why.** Two reasons, one obvious and one that turned out to matter more:

1. The recovery engine is not coupled to one gateway's API shape.
2. **A clean clone runs end to end with no account.** The simulator emits webhooks in
   Razorpay's documented envelope shape and signs them with HMAC-SHA256 over the raw body,
   so verification, idempotency, state transitions and recovery accounting are genuinely
   exercised on the credential-free path. Only Razorpay's own API surface is not.

**Guard against dishonesty.** The provider name and mode appear in the UI header at all
times, and `/api/recovery/{token}/simulate-payment` **refuses to run** when Razorpay is
configured. There is no code path that fabricates a Razorpay payment.

---

## ADR-006 — Webhook processing is idempotent

**Decision.** Verify signature over raw bytes → deduplicate on `X-Razorpay-Event-Id` →
apply. In that order. A duplicate returns **200**.

**Why.** Razorpay retries. A system that double-applies retries double-counts revenue,
which is the worst possible bug in a product whose headline metric is recovered revenue.

**Three details that are easy to get wrong.**

- The body must be verified as received. Parsing and re-serialising changes the bytes and
  every signature fails.
- The duplicate must return 200. A 4xx makes the provider retry forever.
- The ledger row is inserted and flushed *before* the side effect, so the unique
  constraint settles a concurrent double delivery rather than a read-then-write race.

---

## ADR-007 — The system can stop

**Decision.** `STOP` is a first-class action, is unconditionally permitted by policy, and
is a correct answer in the evaluation.

**Why.** This is the product thesis. Retrying an expired card cannot succeed. Messaging a
customer four times about ₹99 costs more than ₹99. Chasing a cancelled order is an
unwanted charge. A recovery system without a stop condition is a spam machine with extra
steps.

**Implementation notes.** `STOP`, `WAIT` and `ESCALATE_TO_MERCHANT` are in `INERT_ACTIONS`
and short-circuit every guard — a system that could be prevented from doing nothing would
have no safe fallback. There is a test asserting this across six hostile contexts. On the
held-out split, 301 of 1,974 cases stop.

---

## ADR-008 — AI output is untrusted input

**Decision.** Model output is parsed, schema-validated, case-id-pinned, sanitised and
length-capped before anything reads it — and then re-validated by policy anyway.

**Why.** The model is a component that can fail, and it is also the component most exposed
to whatever is in the data it reads. Treating its output like a trusted internal call is
how prompt injection becomes a financial transaction.

**What the schema cannot express.** No amount, no order state, no recovered figure, no
policy override. Those fields do not exist, so the model cannot assert them, and
`extra="forbid"` makes inventing one a validation error rather than a silently ignored key.

**The property that matters.** Valid JSON is not authorisation. Tested directly.

---

## ADR-009 — SQLite by default, Postgres in Docker

**Decision.** `DATABASE_URL` drives everything; the default is SQLite. Docker Compose runs
Postgres. Column types are chosen to work on both — `JSON` rather than `JSONB`, `String`
rather than native enums.

**Why.** Two audiences. A judge cloning the repo should reach a working dashboard without
installing a database; a reviewer assessing production-readiness should see the Postgres
path is real and one environment variable away.

**Cost, acknowledged.** Two supported backends is more surface than one, and SQLite's
concurrency story is not Postgres's. For a prototype, a clean clone that runs in under a
minute is worth more.

---

## ADR-010 — Simple background processing

**Decision.** FastAPI `BackgroundTasks` plus a synchronous batch endpoint. No Celery, no
Redis, no queue.

**Why.** The work is short, and every recovery action is already idempotent and
policy-gated, so a lost task means a case is processed on the next run rather than money
moving twice. A broker would add two services, a deployment story and a new class of
failure, for no capability at this scale.

**Limitation, acknowledged.** In-process, lost on restart, no retries, no visibility. In
the README's limitations section.

---

## ADR-011 — Exceptions are exposed, not hidden

**Decision.** Anything the system cannot confidently resolve becomes a
`ReconciliationException` or an `AWAITING_APPROVAL` case, surfaced on the Audit and
Evaluation screens and counted in the metrics.

**Why.** The tempting alternative is to auto-close ambiguous cases so the dashboard looks
clean. That turns an unknown into a false success — a payment quietly written off, an
order left in a state nobody looks at. In payments an unresolved exception is a normal
operating output, and the number of them is a quality signal.

**Consequence.** The reported recovery rate is *lower* than it could be: 508 held-out
cases sit in approval and are counted as unresolved rather than as wins.

---

## ADR-012 — Recovery is credited in exactly one place

**Decision.** `recovered_amount_paise` is written in `webhooks._apply_capture()` and
nowhere else.

**Why.** Multiple write paths to a money field means multiple ways to be wrong, and
whichever is wrong will be found by an accountant rather than a test. Not the checkout
callback (a browser can lie), not the executor (it only opens an attempt), not the model.

**Consequence.** Recovery is asynchronous: the customer sees success from a verified
signature, and the merchant dashboard updates when the webhook lands. The customer page
says exactly that.

---

## ADR-013 — One context type for production and evaluation

**Decision.** `RecoveryContext` is projected from both a live case and a synthetic record,
and both feed the same detector, scorer, agent and policy engine.

**Why.** The alternative — an evaluation harness that re-implements the decision logic —
produces numbers about a system that was never shipped, and drifts silently the first time
production changes.

**Cost.** The projection layer must be careful: `OBSERVABLE_KEYS` is an explicit allow-list
with tests, because a leaked ground-truth field would inflate every metric.

---

## ADR-014 — The deterministic fallback is labelled, never disguised

**Decision.** When no API key is configured or a model call fails, a documented rule tree
answers, and every surface says so: `path="fallback-heuristic"` on the case, a "Deterministic
fallback" badge in the decision panel, a badge in the global header, and an amber panel
naming the error when a model call was attempted and failed.

**Why.** The single most dishonest thing a project like this can do is run an `if/else`
tree and call it AI. If the fallback were presented as a model decision, every claim in
this repository would be worth less.

**Secondary benefit.** It is the baseline the model is measured against, and
"the model beat a rule tree" is only worth saying if the rule tree is written down and
actually run. It is, in `agent/fallback.py`, and it is what produces the headline
evaluation numbers.
