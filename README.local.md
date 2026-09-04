# RECOVER

**AI that finds slipping revenue, recovers what it can, and knows when to stop.**

RECOVER is an AI revenue-recovery agent: it detects revenue at risk, diagnoses why
it slipped, decides whether chasing it is worth the cost, acts inside merchant-defined
limits, observes what actually happened, and stops when recovery is no longer justified —
recording the whole chain as an audit trail.

*A prototype built for the Razorpay Buildathon (Track 03: AI Revenue Recovery) using
Razorpay Test Mode. Not an official Razorpay product.*

---

## Problem

Revenue does not only leave through refunds. It leaks, quietly:

- a payment fails on a temporary bank timeout and nobody follows up;
- a customer reaches checkout and abandons it two clicks from done;
- a subscription debit fails because a card expired last month;
- an invoice goes 40 days past due and drifts down the queue;
- a payment succeeds at the gateway while the order row never advances, so the
  merchant has the money and does not know it;
- the same webhook arrives twice and the revenue is counted twice.

Each one is small. In aggregate they are the difference between a business's gross and
net collected revenue, and almost none of them are anyone's job.

## Why this matters

The obvious fix — retry everything — is worse than doing nothing.

Retrying a card that expired cannot succeed. Retrying an order the customer deliberately
cancelled is an unwanted charge. Retrying a payment already captured is a double charge.
Messaging a customer four times about ₹99 costs more in goodwill than the ₹99 is worth.
A failed payment is not a work item; it is a *judgement call*, and the judgement needs
both the failure's context and a firm sense of when to give up.

That judgement is genuinely ambiguous, which is what makes it a real AI problem. Moving
money is not ambiguous at all, which is what makes it a problem AI must be kept away
from.

## Solution

RECOVER splits those two things and never lets them touch.

```
Detect  →  Diagnose  →  Decide  →  Act  →  Observe  →  Recover or Stop
```

Every revenue-risk event opens a **case**. The case gathers structured context, the AI
diagnoses it and *proposes* one of seven actions, a deterministic policy engine
independently decides whether that action is permitted, and only then — if it is —
does an execution tier touch a payment provider. What actually happened is learned from
a signature-verified webhook, never from the model and never from the browser.

## What AI does

The model does the part that is genuinely fuzzy:

- **Interprets the event** — reads the failure code, the customer's history, the timing,
  and works out what actually happened.
- **Identifies root cause** in language a support lead can read.
- **Judges recoverability** — is this money realistically still collectable?
- **Selects an action** from a closed, seven-member vocabulary.
- **Explains itself** in two or three sentences, citing which evidence it used.
- **Decides when to stop.** Recommending STOP is a correct answer, not a failure.

It works through a **read-only tool set** (`apps/api/recover/agent/tools.py`) —
`get_payment`, `get_order`, `get_customer_history`, `get_failure_context`,
`get_recovery_policy`, `get_recovery_history`, `calculate_recovery_score`,
`propose_recovery_action`, `get_case_status` — rather than receiving one enormous prompt.

## What AI does NOT do

Not by instruction. By construction.

| The model cannot… | Because… |
|---|---|
| Move money or capture a payment | The four action-tier functions are never exposed as tools. |
| Authorise a financial action | `policy.evaluate()` runs on its proposal afterwards, independently. |
| Bypass or edit merchant policy | Policy is pure Python reading a database row; the model has no write path. |
| Change an order's state | Only verified provider events advance the state machine. |
| Mark a payment successful | Recovery is credited in exactly one place: the webhook handler. |
| Calculate an authoritative amount | All money maths is integer paise in deterministic code. |
| Emit an action outside the vocabulary | The output schema is a closed enum; anything else fails validation. |
| Assert an amount, state or override | Those fields do not exist in the schema (`extra="forbid"`). |
| Claim revenue was recovered | The recovered figure is written from a webhook, not from prose. |

The model's output is treated as untrusted input throughout: parsed, schema-validated,
case-id-pinned, and scrubbed of control characters before it is rendered.

**Valid JSON is not authorisation.** A perfectly-formed proposal to retry a cancelled
order is still refused. There is a test for exactly that
(`test_valid_json_alone_does_not_authorise_an_action`).

## Architecture

```
                            ┌──────────────────────────────────────┐
   revenue-risk event ─────▶│  DETECTION      detection.py          │
   (failure, abandonment,   │  is this money genuinely at risk?     │
    subscription, invoice,  └───────────────┬──────────────────────┘
    state mismatch,                         │  yes
    duplicate)                              ▼
                            ┌──────────────────────────────────────┐
                            │  CASE           engine.open_case()    │
                            └───────────────┬──────────────────────┘
                                            ▼
                            ┌──────────────────────────────────────┐
                            │  CONTEXT        context.py            │
                            │  one structured view, no raw rows     │
                            └───────────────┬──────────────────────┘
                                            ▼
             ┌──────────────────────────────────────────────────────────┐
             │  AI DIAGNOSIS            agent/llm.py                     │
             │  ┌────────────────────────────────────────────────────┐  │
             │  │  read-only tools ──▶ Claude ──▶ submit_decision     │  │
             │  └────────────────────────────────────────────────────┘  │
             │  no API key or a bad response ──▶ agent/fallback.py       │
             │                                   (labelled, not "AI")    │
             └───────────────────────────┬──────────────────────────────┘
                                         │  PROPOSAL (untrusted)
                                         ▼
             ╔══════════════════════════════════════════════════════════╗
             ║  DETERMINISTIC POLICY ENGINE        policy.py             ║
             ║  pure functions · default deny · no I/O · no model        ║
             ║          ALLOW    │    REQUIRE_APPROVAL    │    BLOCK     ║
             ╚═══════════╤═══════╧════════════╤═══════════╧══════╤══════╝
                         │                    │                  │
                         ▼                    ▼                  ▼
             ┌───────────────────┐   ┌────────────────┐  ┌──────────────┐
             │  ACTION TIER      │   │  merchant queue│  │  stop, and   │
             │  executor.py      │   │  human decides │  │  say why     │
             │  re-checks policy │   └────────────────┘  └──────────────┘
             └─────────┬─────────┘
                       ▼
             ┌───────────────────────────┐
             │  PaymentProvider (port)   │
             │  ├─ RazorpayPaymentProvider  (Test Mode)
             │  └─ SimulatedPaymentProvider (no credentials)
             └─────────┬─────────────────┘
                       │  customer pays
                       ▼
             ┌──────────────────────────────────────────────────────────┐
             │  WEBHOOK        webhooks.py                               │
             │  1. verify HMAC-SHA256 over the RAW body                  │
             │  2. deduplicate on X-Razorpay-Event-Id                    │
             │  3. advance the order state machine                       │
             │  4. credit recovered revenue  ← the ONLY place this happens│
             └─────────┬────────────────────────────────────────────────┘
                       ▼
             ┌───────────────────────────┐   ┌──────────────────────────┐
             │  merchant dashboard       │   │  audit trail  audit.py   │
             │  apps/web                 │   │  append-only, redacted   │
             └───────────────────────────┘   └──────────────────────────┘
```

## Recovery lifecycle

**Detect → Diagnose → Decide → Act → Observe → Recover / Stop**

A worked example, exactly as the timeline renders it:

```
10:31  Payment of ₹4,999 failed (BANK_TRANSIENT)
10:31  RECOVER detected revenue at risk
10:31  Diagnosis: issuer was temporarily unavailable; the instrument is fine
       → recommends RETRY_PAYMENT, recoverability high
10:31  Policy evaluated: ALLOW (WITHIN_POLICY)
       ₹4,999 ≤ ₹5,000 auto limit · 0 of 2 retries used · customer 89% reliable
10:31  Recovery request created (attempt 1)
10:33  Customer completed payment
10:33  Razorpay webhook received and signature verified
10:33  ₹4,999 recovered — order ORD-18392 completed
```

## Policy engine

`apps/api/recover/policy.py` is the hard boundary. It is pure — no I/O, no clock reads,
no model calls — so the same case always produces the same verdict, and that verdict can
be written into an audit log and defended later.

Merchant-configurable (all live on the Policy screen, all load-bearing):

| Setting | Default |
|---|---|
| Automatic retry limit | 2 |
| Minimum retry interval | 30 minutes |
| Maximum automatic recovery amount | ₹5,000 |
| Merchant approval threshold | ₹10,000 |
| Customer contact limit | 2 per 24 hours |
| Case expiry | 72 hours |
| Payment-link recovery | on |
| Alternative payment method | on |
| Minimum expected value | ₹0 |

Not configurable, because they are correctness constraints rather than preferences —
recovery is refused on every merchant when the order is already captured, refunded or
cancelled; when a risk or fraud flag is present; when the failure class can never be
fixed by a retry (expired card, revoked mandate, deliberate cancellation, risk decline);
and when the event is a duplicate payment.

Three verdicts: **ALLOW**, **REQUIRE_APPROVAL**, **BLOCK**. Merchant approval satisfies
`REQUIRE_APPROVAL` and nothing else — a `BLOCK` is not approvable by anyone, which is
the entire point of separating them.

**Defence in depth.** The orchestrator gates on policy, and each action-tier function
re-checks policy for itself. If the first gate were ever removed, the second still
refuses and logs a security event.

## Razorpay integration

Test Mode only. `apps/api/recover/providers/razorpay_provider.py`, implemented against
the official documentation and the official `razorpay-python` SDK:

| Concern | Implementation |
|---|---|
| Orders | `POST /v1/orders`, amount in paise, receipt ≤ 40 chars |
| Payment links | `POST /v1/payment_links`, returns `short_url`, `callback_method: "get"` |
| Checkout signature | HMAC-SHA256 over `order_id\|payment_id`, via `utility.verify_payment_signature` |
| Webhook signature | HMAC-SHA256 over the **raw body**, header `X-Razorpay-Signature`, via `utility.verify_webhook_signature` |
| Idempotency key | `X-Razorpay-Event-Id`, falling back to a SHA-256 of the body |

`RAZORPAY_KEY_SECRET` never leaves the server. Only the key id reaches the browser, which
is what it is for. **The API refuses to start if `RAZORPAY_KEY_ID` is not an
`rzp_test_` key** — this prototype cannot be pointed at live money by accident.

**Without credentials** it runs on `SimulatedPaymentProvider`, which emits webhooks in
Razorpay's documented envelope shape and signs them with HMAC-SHA256 over the raw body,
so signature verification, idempotency, state transitions and recovery accounting are all
genuinely exercised. What is *not* exercised is Razorpay's own API surface. The UI header
always states which provider is live; nothing is ever presented as a real Razorpay
transaction when it is not.

## Evaluation

10,000 synthetic revenue-risk events, generated deterministically from a seed, split
80/20 by hash of the transaction id. **Thresholds are fitted on the development split
only** (`scripts/tune_thresholds.py`); the held-out split is never used for tuning — that
script does not even keep it in memory.

The harness runs the *shipped* decision path — same detector, same agent, same policy
engine — and scores it against ground truth the system never sees. A separate latent
world model (`dataset/ground_truth.py`) decides what was actually true, using different
coefficients from the ones the system decides with; if ground truth were derived from the
system's own scorer, every number would be meaningless.

**Held-out results** (10,000 events, seed 20260901, deterministic engine, 1,974 held-out
records — reproduce with `python scripts/run_evaluation.py`):

| Metric | Held out |
|---|---|
| Revenue-at-risk detection precision | 0.9912 |
| Revenue-at-risk detection recall | 1.0000 |
| Recovery eligibility precision | 0.4558 |
| Recovery eligibility recall | 0.8741 |
| Action-selection accuracy (detected cases) | 0.4652 |
| Total revenue at risk | ₹12,240,050 |
| Revenue eligible for recovery | ₹4,910,380 |
| Revenue actually recovered | ₹1,006,018 |
| Intervention cost | ₹944.50 |
| **Net recovered revenue** | **₹1,005,073.50** |
| Recovery rate | 0.2049 |
| False-positive interventions | 458 |
| False-positive intervention cost | ₹461.50 |
| Unnecessary customer contacts | 303 |
| Unsafe actions blocked by policy | 166 |
| Cases stopped | 301 |
| Cases held for merchant approval | 508 |
| Unresolved exceptions | 508 |

**Read those numbers honestly.**

- *Recovered revenue is simulated*, not collected from a bank. It is credited only when
  the system took an action ground truth says would genuinely have worked, on a case
  ground truth says was genuinely recoverable, within the attempt budget that would ever
  have helped.
- *Eligibility precision is 0.46.* Roughly half the cases the system judges worth
  pursuing turn out not to have been recoverable. That is the honest cost of deciding
  from observable evidence — latent customer intent is not in the data — and it is why
  the false-positive intervention cost is reported next to it rather than buried.
- *Action accuracy is 0.47*, against a ground truth that knows whether each customer
  would really have paid. The system does not, and cannot. The ceiling here is well
  below 1.0 and any project claiming otherwise is scoring itself against its own beliefs.
- *Detection recall is 1.0* because the detector is deliberately conservative: it flags
  anything uncollected. Precision is 0.99, and the 15 false positives are real — stale
  snapshots where the customer had already cancelled.
- *508 cases were handed to a human*, not resolved. They are counted as unresolved
  exceptions, not quietly as successes.

Full detail, including the tuning grid and a discussion of what the objective function
gets wrong, is in [docs/evaluation.md](docs/evaluation.md).

## Failure recovery

The demo deliberately includes the cases where the system refuses to act. Seven scenarios
ship seeded and are listed on the dashboard:

| # | Scenario | What you see |
|---|---|---|
| 1 | Retry succeeds | ₹4,999 recovered end to end through a verified webhook |
| 2 | Retry budget exhausted | 2/2 retries and 2/2 contacts spent → every action refused, case stopped |
| 3 | Above the automatic limit | ₹12,499 → `REQUIRE_APPROVAL`, nothing executed until a human approves |
| 4 | Customer cancelled | `BLOCK` / `ORDER_CANCELLED`, no contact, no charge |
| 5 | Duplicate webhook | Second delivery counted, acknowledged 200, business effect runs once |
| 6 | Payment captured, order stale | Reconciliation sweep raises an exception rather than guessing |
| 7 | Forbidden action proposed | Policy probe: ask the real engine for `RETRY_PAYMENT` on a cancelled order and watch it refuse |

Every failure surface answers the same five questions: what happened, why, what the
system did, why that was safe, and what happens next. Cases the system cannot confidently
resolve go to an **Exceptions** list on the Audit screen — shown, never quietly closed.

## What broke and how we fixed it

Real problems hit while building this, not invented ones.

**1. Optimising net revenue produced a system that spams everyone.**
`tune_thresholds.py` maximised net recovered revenue on the dev split, and picked the most
aggressive operating point available (act threshold 0.15) — because a message costs ₹0.50
while a recovered order is worth thousands, so the optimiser will always contact everyone.
That is the exact "retry everything" behaviour the product exists to avoid, arrived at
honestly by an objective function that was wrong. Adding a goodwill penalty for
unnecessary contacts did not change the answer either: at ₹25 per bad contact the
aggressive point still wins by two orders of magnitude. **We did not fudge the cost until
the answer looked better.** The tuner reports both operating points, `docs/evaluation.md`
records the tension, and the real restraint in the system turns out to come from the
policy engine — retry limits, contact budgets, non-retryable failure classes — not from
the expected-value threshold. Reporting that is more useful than hiding it.

**2. `models.py` silently never got written.** A shell heredoc containing the file failed
to parse, taking the whole command with it, and the next step failed with a confusing
`ImportError: cannot import name 'models'`. Diagnosed by listing the package directory
rather than trusting that the write had happened.

**3. SQLAlchemy returns enum columns as plain strings.** `context_from_case` did
`case.event_type.value` and crashed with `'str' object has no attribute 'value'` the
first time a case was processed from the database — the type annotation says `EventType`,
but the `String` column hands back `str`. Fixed by coercing back into the closed
vocabularies at the projection boundary, which is now the single place that conversion
happens.

**4. Seeded demo timestamps were anchored to a hardcoded date.** Cases seeded "95 minutes
ago" relative to a literal `2026-09-01 10:30` were in the *future* relative to the real
clock, so elapsed-time clamped to zero and the retry-interval rule misfired — the
retry-limit scenario proposed `WAIT` instead of stopping. Seed timestamps now anchor to
the real clock; only the timestamps move, the content stays deterministic.

**5. Re-seeding the demo deleted the evaluation results.** `wipe()` truncated every table
including `evaluation_runs`, so resetting the demo between takes blanked the Evaluation
screen. Evaluation results are evidence about the system, not demo furniture; that table
is now excluded.

**6. A test asserted on a hidden `<option>` and would have passed with zero rows.**
`getByText("Stopped")` in the cases-list e2e matched the filter dropdown, not the table.
It now asserts on `tbody` cells, so an empty table fails the test.

**7. The audit screen chose its tab in an effect.** `?tab=exceptions` painted the audit
tab first and switched after mount, which users see as a flash and the e2e suite saw as an
intermittent failure. The tab is now resolved during the first render.

**8. Next 16 blocked the e2e run's own assets.** Driving the app over `127.0.0.1` while
the dev server bound `localhost` tripped the cross-origin dev-resource guard, and every
page loaded without JavaScript. Fixed with `allowedDevOrigins` plus a consistent origin.
Related constraint, documented rather than worked around: Next 16 refuses a second dev
server in the same directory, so `npm run dev` must not be running when the e2e suite is.

## Security

- **Secrets live only in the environment.** `.env` is gitignored; `.env.example` documents
  every variable with no values.
- **`RAZORPAY_KEY_SECRET` and `ANTHROPIC_API_KEY` never reach the browser.** Only the
  Razorpay key id does, which is public by design.
- **Live keys are refused at startup**, not merely discouraged.
- **Webhook signatures are verified over raw bytes** before the body is parsed. An
  unverified delivery is recorded and rejected and never reaches business logic.
- **A browser callback is not authority.** The Checkout callback signature is verified and
  logged, but recovery is credited only by the webhook.
- **The audit log redacts credentials** at any nesting depth before writing, and there is
  a test asserting no secret-shaped string ever appears in it.
- **All input is Pydantic-validated**, including — especially — the model's output.
- **Idempotency is enforced by a unique constraint**, not by a read-then-write race.
- **Customer recovery pages are token-scoped**: one single-use token, one case, no login,
  no access to anything else.
- No payment credentials are ever stored. RECOVER never sees a card number.

## Running locally

Requires Python 3.12+ and Node 20+. No API keys needed — it runs fully without them.

```bash
git clone <repo-url> && cd Razorpay
cp .env.example .env          # optional; every value may stay blank

# --- backend ---------------------------------------------------------------
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\Activate.ps1
pip install -r apps/api/requirements.txt

python scripts/seed_demo.py --process     # deterministic demo data
python scripts/run_evaluation.py          # held-out metrics (~10s)

cd apps/api && python -m uvicorn recover.main:app --reload --port 8000
```

```bash
# --- frontend (second terminal) --------------------------------------------
cd apps/web
npm install
npm run dev
```

- Merchant dashboard — <http://localhost:3000>
- API docs — <http://localhost:8000/docs>

### With Docker

```bash
docker compose up --build
```

Brings up Postgres, the API (schema created and demo data seeded automatically) and the
web app. Run `python scripts/run_evaluation.py` afterwards to populate the Evaluation
screen.

### With Razorpay Test Mode

1. Razorpay Dashboard → **Test Mode** → Settings → API Keys → generate a key.
2. Put `RAZORPAY_KEY_ID` (`rzp_test_…`) and `RAZORPAY_KEY_SECRET` in `.env`.
3. Expose the API publicly (`ngrok http 8000` or similar).
4. Settings → Webhooks → add `https://<host>/api/webhooks/razorpay`, choose a secret, put
   it in `RAZORPAY_WEBHOOK_SECRET`, and subscribe to `payment.captured`,
   `payment.failed`, `order.paid`.
5. Restart the API. The header switches to **Razorpay Test Mode** and the customer
   recovery page opens real Razorpay Checkout.

## Environment variables

| Variable | Purpose | Blank means |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy URL | SQLite at `./recover.db` |
| `RAZORPAY_KEY_ID` | Test Mode key id (public) | use the local simulator |
| `RAZORPAY_KEY_SECRET` | Test Mode secret (**server only**) | use the local simulator |
| `RAZORPAY_WEBHOOK_SECRET` | Webhook signing secret | live webhook verification unavailable |
| `ANTHROPIC_API_KEY` | Enables the LLM diagnosis path | deterministic fallback, labelled as such |
| `ANTHROPIC_MODEL` | Model id | `claude-opus-5` |
| `AI_TIMEOUT_SECONDS` | Per-call timeout | 45 |
| `PUBLIC_WEB_URL` | Base for customer recovery links | `http://localhost:3000` |
| `CORS_ORIGINS` | Comma-separated allowed origins | `http://localhost:3000` |
| `NEXT_PUBLIC_API_URL` | API base for the browser | `http://localhost:8000` |

## Testing

```bash
# Python: policy, state machine, webhooks, AI validation, engine, evaluation, API
cd apps/api && python -m pytest -v          # 180 tests

# End-to-end in a browser (stop `npm run dev` first — see failure note 8)
cd apps/web && npx playwright install chromium
npx playwright test                          # 9 tests, both servers auto-started
```

```bash
# Evaluation
python scripts/tune_thresholds.py    # fits on the development split ONLY
python scripts/run_evaluation.py     # held-out metrics, stored for the dashboard
python scripts/generate_dataset.py   # write the 10,000 records to data/ to inspect
```

## Adding your own data

Demo data comes from `scripts/seed_demo.py`. To feed RECOVER a real customer and
a real failed payment, post the event to the ingest endpoint — this is the front
door of the pipeline:

```bash
curl -X POST http://localhost:8000/api/ingest/event   -H "Content-Type: application/json"   -d '{
    "customer": {
      "name": "Anita Desai",
      "email": "anita@example.com",
      "contact": "+919812345678",
      "segment": "LOYAL",
      "successful_payments": 9,
      "failed_payments": 1
    },
    "order_reference": "ORD-90001",
    "description": "Noise Cancelling Earbuds",
    "amount_inr": "4999.00",
    "event_type": "PAYMENT_FAILURE",
    "failure_reason": "BANK_TRANSIENT",
    "process_immediately": true
  }'
```

The response carries the new `case_id` and a `recovery_url` you can open directly
as the customer. The case then behaves exactly like a seeded one — same
detection, same agent, same policy engine. Ingestion carries no privileges: a
risk-flagged customer is refused and a ₹24,999 order is held for approval, just
as they would be from any other source.

Notes worth knowing:

- Customers are keyed on **email** within a merchant, and re-reporting one
  without history does **not** wipe the history you already gave.
- Order references are unique; reporting the same one twice returns the existing
  case rather than creating a duplicate.
- `amount_inr` is parsed as an exact decimal, never a float — `"4999.99"` becomes
  499999 paise, and anything finer than a paise is rejected with a 422.
- Use `amount_paise` instead if your system already works in paise.
- `event_type` accepts any of the six classes; `failure_reason` any member of the
  taxonomy. An invented value is a 422, not a silent default.
- Set `"process_immediately": false` to stage a batch and run it from the
  dashboard instead.

Full request/response schema at <http://localhost:8000/docs#/ingest>.

## Limitations

Stated plainly, because a prototype that pretends otherwise is worse than one that does not.

1. **Recovered revenue in the evaluation is simulated.** Ground truth decides whether a
   customer "would have paid". Real recovery rates would differ, probably substantially.
2. **The synthetic dataset is a model of reality, not a sample of it.** The correlations
   are hand-designed to be plausible. Metrics on this data say the system behaves
   sensibly under those assumptions — not that it will hit these numbers in production.
3. **Customer messages are recorded, not delivered.** No email or SMS provider is wired
   up. The contact *budget* is real and does gate behaviour; the send is not.
4. **The default headline metrics come from the deterministic engine, not the LLM.**
   Scoring 10,000 records through a model is neither affordable nor reproducible. The LLM
   path is real and runs on live cases; `--engine llm --limit N` scores a sample.
5. **Single merchant, no authentication.** There is no login, no tenancy, no RBAC. Every
   endpoint trusts its caller. This is a prototype, not a deployable service.
6. **Background processing is FastAPI `BackgroundTasks`** — in-process, lost on restart.
   Deliberately not a broker; see ADR-010.
7. **No retry/backoff around provider calls.** A transient Razorpay error fails the
   action, escalates the case, and moves no money — safe, but not resilient.
8. **SQLite by default.** Fine for a prototype; the concurrency story under real load is
   Postgres, which Docker Compose provides.
9. **Only payment-failure recovery is exercised against a live provider.** The other five
   event classes run through the same engine but are driven by the synthetic event
   generator.

## Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Components, data model, request flows |
| [docs/ai-design.md](docs/ai-design.md) | Prompt, tools, schema, untrusted-output handling |
| [docs/policy-engine.md](docs/policy-engine.md) | Every rule, every code, and why |
| [docs/evaluation.md](docs/evaluation.md) | Dataset, ground truth, metrics, tuning honesty |
| [docs/failure-recovery.md](docs/failure-recovery.md) | The seven scenarios and what each proves |
| [docs/decisions.md](docs/decisions.md) | Architectural decision records |
