# Architecture

## The one-sentence version

AI proposes, deterministic code validates, policy controls, payment services execute,
verified events update state, and everything is audited.

## Why the split exists

Revenue recovery contains two very different kinds of problem in the same workflow.

*Was this failure transient, and is this customer likely to come back?* is genuinely
ambiguous. It needs a failure taxonomy, a customer's history, timing, and a sense of when
chasing stops being worth it. That is what language models are good at.

*May we charge this customer ₹4,999 right now?* is not ambiguous at all. It is a
conjunction of six boolean conditions that a merchant can state in a sentence. Delegating
it to a probabilistic system would be indefensible, and would also be *worse* — slower,
more expensive, and untestable.

So the system is built as a pipeline where the second question is answered by code that
cannot be talked out of its answer.

## Components

```
apps/api/recover/
├── config.py            Settings from environment. Refuses live Razorpay keys.
├── db.py                Engine/session. Portable across SQLite and Postgres.
├── enums.py             Closed vocabularies. The seven-action set lives here.
├── models.py            SQLAlchemy domain model. Money is integer paise, always.
├── context.py           RecoveryContext — the single structured view of a case.
├── detection.py         Is this genuinely revenue at risk?
├── scoring.py           Deterministic probability and expected value.
├── policy.py            ★ The gate. Pure functions, default deny.
├── state_machine.py     ★ Order states. The authoritative "were we paid?" record.
├── executor.py          ★ The four functions that can touch money or customers.
├── engine.py            Orchestration: the lifecycle, approvals, batch, reconciliation.
├── webhooks.py          ★ Verify → deduplicate → apply. The only recovery credit path.
├── audit.py             Append-only trail with credential redaction.
├── seed.py              Deterministic demo data covering all seven scenarios.
├── bootstrap.py         First-run schema + seed (used by Docker).
├── agent/
│   ├── schemas.py       AIDecision. extra="forbid", closed enums, case-id pinning.
│   ├── tools.py         Read-only tool registry. The security boundary.
│   ├── llm.py           Anthropic tool-use loop. Never raises.
│   └── fallback.py      Labelled deterministic reasoner. NOT the AI.
├── providers/
│   ├── base.py          PaymentProvider port.
│   ├── razorpay_provider.py   Test Mode adapter, per official docs.
│   └── simulator.py     Local simulator. Signs real webhooks.
├── dataset/
│   ├── generator.py     10,000 correlated synthetic events + observation noise.
│   ├── ground_truth.py  Independent latent world model.
│   ├── projection.py    Record → context. Enforces the no-leakage boundary.
│   ├── evaluate.py      Runs the shipped path; scores against ground truth.
│   └── persist.py       Stores a run for the dashboard.
└── routers/             HTTP surface.
```

★ marks the modules where a bug costs money.

## Data model

```
Merchant ──1:1── MerchantPolicy          every field read on every decision
    │
    ├──1:N── Customer                    history, segment, risk flag
    │
    └──1:N── Order ──1:N── Payment       amount_paise; state machine on Order.state
                 │
                 └──1:N── RecoveryCase
                              ├── AI diagnosis      (advisory, never authoritative)
                              ├── Policy verdict    (deterministic)
                              ├── Outcome           (only from verified events)
                              ├──1:N── CaseEvent        the timeline
                              └──1:N── RecoveryAttempt  what was tried, and its cost

AuditLog                    append-only, redacted, indexed by case
WebhookEvent                idempotency ledger, unique on event_id
ReconciliationException     what the system could not confidently resolve
EvaluationRun               stored metrics — never hand-written
```

**Money is integer paise everywhere.** No float touches an amount, in the database, in
the API, or in the browser. The API sends both `paise` and a preformatted `display`
string so the frontend never does currency arithmetic — one authoritative implementation
instead of two that drift.

## The central abstraction: RecoveryContext

Both halves of the system consume the same type.

```
  production:  RecoveryCase + MerchantPolicy  ──▶ context_from_case()   ──┐
                                                                          ├──▶ RecoveryContext
  evaluation:  synthetic record               ──▶ context_from_record() ──┘
```

This is why the evaluation numbers mean something. The harness exercises the same
detector, the same scoring functions, the same agent and the same policy engine that
serve live traffic — not a parallel re-implementation that could quietly diverge.

`projection.py` enforces the boundary with an explicit `OBSERVABLE_KEYS` allow-list, and
a test asserts no `ground_truth_*` field can reach a context or the model's evidence
block.

## Request flow: processing a case

```
POST /api/cases/{id}/process
  │
  ├─ load case + merchant policy
  ├─ context_from_case()                       coerce ORM strings → closed enums
  ├─ score_case()                              deterministic probability + EV
  │
  ├─ llm.diagnose(ctx)
  │    ├─ tools: get_failure_context, get_customer_history, calculate_recovery_score…
  │    ├─ submit_recovery_decision → parse_ai_decision() → AIDecision
  │    └─ on ANY failure → fallback.decide(), envelope.degraded = True
  │
  ├─ WRITE the proposal to the case            before policy runs, so the row always
  │                                            shows what the model wanted
  ├─ policy.evaluate(ctx, proposal)            independent, deterministic
  ├─ policy.permitted_actions(ctx)             full matrix stored for the decision panel
  │
  └─ ALLOW            → executor.* (which re-checks policy for itself)
     REQUIRE_APPROVAL → status AWAITING_APPROVAL, nothing executed
     BLOCK            → timeline entry naming the code, then stop or reconcile
```

Writing the AI proposal *before* the policy verdict is deliberate: every case row shows
both what the model wanted and what the system permitted, including where they disagree.

## Request flow: a webhook

```
POST /api/webhooks/razorpay
  │
  ├─ read RAW body                      never await request.json() first —
  │                                     re-serialising changes the bytes
  ├─ verify HMAC-SHA256                 fail → audit, 400, business logic never runs
  ├─ parse JSON
  ├─ event_id = X-Razorpay-Event-Id  ||  "sha256:" + digest(body)
  │
  ├─ already in the ledger?  → count the delivery, return 200 "duplicate", apply NOTHING
  │                            (200 matters: Razorpay retries on anything else)
  ├─ INSERT + flush                     the unique constraint settles concurrent deliveries
  │                                     *before* the side effect, not after
  └─ apply:
       payment.captured → state machine → COMPLETED → credit recovered revenue ★
       payment.failed   → state machine → PAYMENT_FAILED → reopen for re-evaluation
       unknown order    → ReconciliationException, never a guess
```

★ is the only place in the codebase that writes `recovered_amount_paise`.

## Trust boundaries

| Boundary | What crosses it | How it is checked |
|---|---|---|
| Model → system | `AIDecision` | Pydantic, closed enums, `extra="forbid"`, case-id pinned, prose scrubbed |
| System → provider | Action-tier calls | `policy.evaluate()` immediately before, again inside |
| Provider → system | Webhook body | HMAC-SHA256 over raw bytes, then event-id deduplication |
| Browser → system | Checkout callback | Signature verified, but **not** treated as financial authority |
| Customer → system | Recovery token | Single-use, scoped to exactly one case |
| Merchant → system | Policy edits | Pydantic bounds + a cross-field consistency check |

## Frontend

Next.js App Router, TypeScript, Tailwind v4, client components fetching a typed API
client (`lib/api.ts`). UI primitives are authored in-repo in the shadcn/ui style — own
your components, style with tokens — because the shadcn CLI needs an interactive init.

Screens: **Dashboard** (metrics, 7-day trend, recent cases, demo scenarios, batch
controls), **Cases** (filterable list showing AI proposal *beside* policy verdict),
**Case detail** (decision panel, policy matrix, timeline, attempts, policy probe),
**Policy** (every control load-bearing), **Evaluation** (dev vs held-out, exceptions),
**Audit** (trail, webhook ledger, exceptions), and the customer **Recovery page**.

A provenance bar in the header states, at all times, whether Razorpay or the simulator is
live and whether the model or the fallback is deciding.

## What was deliberately not built

- **No message broker.** FastAPI `BackgroundTasks` plus a synchronous batch endpoint.
  Adding Celery and Redis would add two services and no capability at this scale.
- **No vector database, no RAG.** Nothing here is a retrieval problem; the context fits
  in a few hundred tokens of structured JSON.
- **No multi-agent system.** One diagnosis step, one decision. A second agent would add
  latency and failure modes to a problem that does not have sub-problems.
- **No fine-tuning.** The task is reading a documented failure taxonomy, which a
  general model does well with the taxonomy in the tool output.
- **No microservices, no Kubernetes.** Two processes and a database.
