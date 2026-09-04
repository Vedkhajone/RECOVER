# AI design

## The job

One question, asked once per case:

> Given this revenue-risk event, what happened, is it worth recovering, which permitted
> action is best, and when should we stop?

Not: how much to charge. Not: whether charging is allowed. Not: whether the money came
back. Those are answered by code.

## Why a model belongs here at all

Consider two failed payments, identical in amount:

- `GATEWAY_ERROR` / "Payment processing failed at the bank", customer has paid 8 of 9
  times, first attempt, 2 minutes ago.
- `BAD_REQUEST_ERROR` / "Your account does not have sufficient balance", customer has
  paid 1 of 8 times, second attempt, 12 minutes after the first.

A human operator reads those in three seconds and reaches opposite conclusions. Encoding
that judgement exhaustively in rules is possible but brittle — the interesting part is
the *interaction* between failure class, history, timing and attempt count, and there are
more combinations than anyone wants to enumerate. Reading a messy signal and producing a
structured judgement is exactly what the model is for.

## Why the model is kept away from everything else

The failure mode that matters is not "the model gives bad advice". It is "the model's
advice becomes an irreversible action". Charging a customer who cancelled, double-charging
a captured order, or messaging someone six times cannot be undone by noticing the mistake
afterwards.

So the model has no path to any of those, structurally rather than by instruction.

## The tool set

Two tiers, and the split *is* the security model.

**Read tier** (`agent/tools.py`) — the only tools the model can call. Every one is a pure
lookup:

| Tool | Returns |
|---|---|
| `get_payment` | status, amount, method, normalised failure reason, gateway error |
| `get_order` | reference, description, state, already-paid/cancelled/refunded flags |
| `get_customer_history` | segment, successes, failures, success rate, risk flag |
| `get_failure_context` | the failure class *plus a documented note on what it means* |
| `get_recovery_policy` | the merchant's limits, with a note that they are enforced |
| `get_recovery_history` | attempts on this case, recent cases for this customer |
| `calculate_recovery_score` | **authoritative** probability and per-action expected value |
| `propose_recovery_action` | every action with its policy verdict precomputed |
| `get_case_status` | current counters |

The worst thing the entire read tier can do is read.

**Action tier** (`executor.py`) — `create_payment_retry_request`, `create_payment_link`,
`send_customer_recovery_message`, `escalate_case`. Never exposed to the model. Invoked by
the orchestrator only after `policy.evaluate()` returns ALLOW, and each re-checks policy
itself.

There is deliberately no `execute_anything()`, no `admin_action()`, no
`charge_any_amount()`. The action vocabulary is closed at seven members.

### `get_failure_context` is the highest-leverage tool

It returns not just `INSUFFICIENT_FUNDS` but:

> "Account did not have the balance. Retrying immediately fails again; retrying after
> payday or with a smaller amount sometimes works."

The model is interpreting a documented taxonomy rather than guessing what an opaque
gateway string implies. That is the difference between judgement and vibes.

### `calculate_recovery_score` exists so the model does not do arithmetic

Recovery probability and expected value are computed in `scoring.py` — deterministic,
tested, auditable. The prompt tells the model to use those numbers and not to compute its
own. Even if it ignores that, its own numbers have nowhere to go: the schema has no field
for them.

## The output schema

```python
class AIDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id:            str
    root_cause:         str            # sanitised, truncated
    recoverability:     Recoverability # high | medium | low
    recommended_action: RecoveryAction # closed 7-member enum
    reason:             str            # sanitised, truncated
    confidence:         float          # 0.0–1.0
    requires_approval:  bool
    evidence_used:      list[str]      # advisory, shown in the decision panel
```

Note what it **cannot express**: an amount, an order state, a recovered figure, a policy
override, a customer identifier to charge. Those fields do not exist, so the model cannot
assert them even if it tries — and `extra="forbid"` means inventing one is a validation
error, not a silently ignored key.

Delivery is via a `submit_recovery_decision` tool whose `input_schema` mirrors the model
above, so the model returns structured data rather than JSON-in-prose. Prose output is
still tolerated as a fallback path and parsed, fences and all.

## Treating output as untrusted input

Every defence in `agent/schemas.py`, and what each one stops:

| Defence | Stops |
|---|---|
| `RecoveryAction` enum | `"DELETE_ALL_ORDERS"`, `"refund_everything"` |
| `extra="forbid"` | `{"recovered_amount_paise": 999999}`, `{"override_policy": true}` |
| `case_id` pinning | a decision addressed to a different customer's case |
| `confidence` bounds | `1.5`, `-0.2`, `"high"` |
| `sanitize_text()` | NUL bytes, ANSI escapes, markup smuggled into an operator's screen |
| Length caps | a 5,000-character "reason" wrecking the UI |
| `frozen=True` | anything downstream quietly editing what the model said |

And then, after all of that succeeds:

```python
def test_valid_json_alone_does_not_authorise_an_action():
    ctx = make_context(order_cancelled=True)
    decision = parse_ai_decision(VALID_JSON, expected_case_id="case_test")
    assert decision.recommended_action == RecoveryAction.RETRY_PAYMENT   # schema-valid
    verdict = evaluate(ctx, decision.recommended_action)
    assert verdict.decision == PolicyDecision.BLOCK                      # still refused
```

Schema validity is a parsing result, not permission.

## The prompt

Full text in `agent/llm.py`. Its structure:

1. **Role** — the diagnosis and triage engine inside a revenue-recovery system.
2. **The four-step job** — what happened, how recoverable, which permitted action, explain.
3. **How to work** — call the read tools; the deterministic score is authoritative; the
   policy verdicts are precomputed; finish with `submit_recovery_decision`.
4. **Judgement expected** — *not every failure is worth chasing*; distinguish failures a
   retry can fix from ones it cannot; a reliable customer earns benefit of the doubt;
   **stopping is a correct answer, not a failure**.
5. **Constraints** — cannot move money, cannot mark anything recovered, cannot invent
   data. Stated for clarity, enforced elsewhere.

The prompt is guidance, not control. If the model ignored every word, the worst outcome
is a bad proposal that the policy engine rejects.

## The deterministic fallback

`agent/fallback.py` is a hand-written decision tree that emits an `AIDecision`-shaped
answer. It exists so that:

1. a clean clone with no API key runs end to end;
2. an API outage degrades the product instead of breaking it;
3. there is an honest baseline the model is measured against.

**It is never presented as AI.** Every decision carries `path`, and the UI renders
`fallback-heuristic` as "Deterministic fallback" in a neutral badge, next to a header
badge stating the same thing globally. Its `confidence` is a flat 0.5 — a rule tree has no
calibrated uncertainty, and inventing a number that looks fitted would be a lie.

Its preference order encodes the same domain knowledge the prompt describes: expired card
and revoked mandate route to `OFFER_ALLOWED_ALTERNATIVE` (a retry cannot work);
abandonment routes to a payment link; overdue invoices to a reminder; insufficient funds
waits if the retry interval has not elapsed; otherwise it picks by probability threshold.
It walks the policy menu and takes the best action policy permits, or `STOP`.

## Degradation

`diagnose()` never raises. Network error, auth failure, rate limit, malformed JSON, schema
violation, or a model that browses tools until the budget runs out — all land in the same
place:

```python
except Exception as exc:
    validation_error = f"{type(exc).__name__}: {exc}"
...
return AIDecisionEnvelope(
    decision=fallback.decide(ctx),
    path="fallback-heuristic",
    validation_error=validation_error,
    degraded=True,
)
```

`degraded=True` surfaces as an amber panel on the case: *"The model call did not return a
valid decision, so the deterministic fallback answered"*, with the error. A degraded
decision is never silently dressed up as an AI decision.

The tool loop is capped at six rounds, and on the final round `tool_choice` forces
`submit_recovery_decision`, so a model that keeps browsing still terminates with an answer
instead of timing out.

## Cost and reproducibility

The headline evaluation numbers come from the deterministic engine, because 10,000 model
calls are neither affordable nor reproducible, and a metric nobody can re-run is not a
metric. The LLM path is real, runs on live cases, and can be scored on a sample:

```bash
python scripts/run_evaluation.py --engine llm --limit 40
```

The `decision_engine` field is stored with every run and shown on the Evaluation screen,
so a reader always knows which path produced the numbers they are looking at.
