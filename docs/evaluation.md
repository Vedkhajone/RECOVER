# Evaluation

## What is being measured

Whether RECOVER makes good decisions on revenue-risk events it has never seen, and what
those decisions cost when they are wrong.

Two words appear throughout, and they mean different things:

- **PREDICTED** — what RECOVER decided from observable evidence.
- **ACTUAL** — what the simulated world says would really have happened.

Recovered revenue is an ACTUAL figure. Detection and eligibility are PREDICTED. The
Evaluation screen and the CLI both label them.

## The dataset

10,000 synthetic events, generated deterministically from a seed
(`dataset/generator.py`). Regenerate byte-identically with `--seed 20260901`.

**Fields are correlated, not independently random.** A customer's segment drives their
history, which drives which failures they hit and how likely they are to return. An
at-risk customer with eleven prior failures does not draw a transient gateway error and a
90% recovery chance.

| Event class | Share |
|---|---|
| Payment failure | 44% |
| Checkout abandonment | 22% |
| Subscription payment failure | 13% |
| Overdue invoice | 12% |
| Payment/order state mismatch | 6% |
| Duplicate payment event | 3% |

Amounts straddle both policy thresholds deliberately — there is a test asserting the
dataset contains records below ₹5,000, between ₹5,000 and ₹10,000, and above ₹10,000, so
the approval rules actually fire.

### Observation noise, and why it is the point

The generator maintains a **latent world** and then *derives* an observed record from it,
sometimes lossily. About 6% of records carry a stale snapshot: the customer cancelled
after the failure event but before we looked; a payment succeeded at the provider while
our order row went stale; a duplicate event presents as an ordinary failure.

Without this, detection precision would be trivially 1.0 and would be measuring nothing.
With it, the detector chases 15 held-out cases where the money was never actually at
risk — which is exactly the class of mistake the product exists to reduce, so it needs to
be measurable.

## Ground truth

`dataset/ground_truth.py` is structurally independent from the system's own scorer, and
that separation is load-bearing.

| | `scoring.py` (the system) | `ground_truth.py` (the world) |
|---|---|---|
| Question | Given what we observe, how likely is recovery? | What was actually true? |
| Sees | Observable fields only | Latent intent, real order state |
| `BANK_TRANSIENT` prior | 0.62 | 0.78 |
| `INSUFFICIENT_FUNDS` prior | 0.26 | 0.34 |
| History effect | `0.70 + 0.62 × rate` | `0.60 + 0.75 × rate` |
| Attempt decay | `0.55ⁿ` | `0.62ⁿ` |

If ground truth were derived from the same coefficients the system decides with, the
evaluation would be measuring the system against its own beliefs and every number would
be meaningless.

Ground truth also decides **which actions could have worked**. Retrying an expired card
never recovers the money, however reasonable the proposal looked — so an action outside
`ground_truth_effective_actions` earns nothing, and the case is reported as
`ACTION_INEFFECTIVE_FOR_FAILURE_CLASS`.

## No leakage

`dataset/projection.py` has an explicit `OBSERVABLE_KEYS` allow-list. Every
`ground_truth_*` field, plus `true_event_type` and `stale_snapshot`, is left behind.

Two tests enforce it: one asserts the forbidden keys are disjoint from the allow-list and
absent from a serialised context; the other asserts they never appear in the evidence
block the model literally sees.

## The split

80/20, assigned by **hash of the transaction id** rather than list position — so the split
is stable if the generator's ordering ever changes, and a record cannot silently migrate
between dev and held-out between runs. Tested for determinism, disjointness, and
robustness to reordering.

Seed 20260901 gives 8,026 development and 1,974 held-out records.

## Tuning honesty

`scripts/tune_thresholds.py` grid-searches two decision thresholds against the
**development split only**. The held-out split is generated, then immediately `del`-eted
so it cannot be read by accident. `scripts/run_evaluation.py` is the only thing that
touches it, and it runs afterwards.

### What the tuner found, and why we did not hide it

The first objective was net recovered revenue. It selected the most aggressive operating
point on the grid (act threshold 0.15):

```
  act=0.15 retry=0.20   net=Rs 4,442,656   action_acc=0.490
  act=0.35 retry=0.35   net=Rs 4,215,441   action_acc=0.538
```

Of course it did. A message costs ₹0.50 and a recovered order is worth thousands, so a
pure-revenue objective will always say "contact everyone" — which is precisely the blind
retry behaviour RECOVER exists to avoid, arrived at honestly by an objective function
that was wrong.

We added a goodwill penalty of ₹25 per contact that ground truth says was unnecessary.
**It did not change the answer.** 1,310 bad contacts × ₹25 = ₹32,750 against ₹4.4M of
recovered revenue: still two orders of magnitude apart.

We did not keep raising the penalty until the optimiser agreed with our thesis. The
committed thresholds are the ones the stated objective actually selects, and both
operating points are recorded in `recover/tuning.json`. Run
`--objective net_revenue` to reproduce the degenerate one.

**What this taught us about the system.** The restraint in RECOVER does not come from the
expected-value threshold. It comes from the policy engine — retry limits, contact budgets,
non-retryable failure classes, risk flags, cancelled and captured orders. On the held-out
split those refuse 249 actions and stop 301 cases. The economics of this synthetic world
simply do not punish over-intervention enough for a threshold to matter, and saying so is
more useful than tuning until the graph looks virtuous.

The merchant-configurable `min_expected_value_paise` floor exists so a real merchant, with
real churn costs, can impose the restraint their economics justify. It defaults to ₹0 —
we did not set it to a number that flatters the demo.

## Results

10,000 events, seed 20260901, deterministic decision engine. Reproduce with
`python scripts/run_evaluation.py`.

| Metric | Development (8,026) | **Held out (1,974)** |
|---|---|---|
| Detection precision | 0.9908 | **0.9912** |
| Detection recall | 1.0000 | **1.0000** |
| Detection TP / FP / FN | 6,866 / 64 / 0 | **1,696 / 15 / 0** |
| Eligibility precision | 0.4774 | **0.4558** |
| Eligibility recall | 0.8844 | **0.8741** |
| Action accuracy (detected) | 0.4896 | **0.4652** |
| Action accuracy (all records) | 0.5001 | **0.4762** |
| Revenue at risk | ₹48,459,990.00 | **₹12,240,050.00** |
| Eligible for recovery | ₹21,766,816.00 | **₹4,910,380.00** |
| Actually recovered | ₹4,446,559.00 | **₹1,006,018.00** |
| Intervention cost | ₹3,902.50 | **₹944.50** |
| Net recovered | ₹4,442,656.50 | **₹1,005,073.50** |
| Recovery rate | 0.2043 | **0.2049** |
| Interventions made | 3,500 | **848** |
| False-positive interventions | 1,862 | **458** |
| False-positive cost | ₹1,759.00 | **₹461.50** |
| Unnecessary contacts | 1,310 | **303** |
| Total customer contacts | 2,065 | **501** |
| Unsafe actions blocked | 568 | **166** |
| Policy refusals (all) | 898 | **249** |
| Cases stopped | 1,222 | **301** |
| Held for approval | 2,045 | **508** |
| Unresolved exceptions | 2,045 | **508** |

Development and held-out track closely, which is the main thing a held-out split is for:
no meaningful overfitting on two thresholds fitted over an 8,026-record grid.

## Reading the numbers honestly

**Detection recall is 1.0 and precision is 0.99.** The detector is deliberately
conservative — it flags anything uncollected. The 15 false positives are the stale-snapshot
records where the customer had already cancelled. This metric is *easy* and we are not
claiming otherwise; the hard decisions come later in the pipeline.

**Eligibility precision is 0.46.** Roughly half the cases judged worth pursuing turn out
not to have been recoverable. This is the honest cost of deciding from observable
evidence: whether a customer *will* come back is latent, and no amount of feature
engineering over a failure code recovers it. What matters is that the cost is bounded and
reported — ₹461.50 of false-positive intervention cost against ₹1,006,018 recovered — and
that policy caps how far a wrong judgement can go.

**Action accuracy is 0.47.** Measured against a ground truth that *knows* whether each
customer would have paid. A large share of the gap is the system proposing a reasonable
active recovery on a case ground truth marks `STOP` because the customer was never going
to pay. The ceiling here is far below 1.0. A project reporting 0.95 on this metric is
either scoring against its own beliefs or leaking ground truth.

**Recovery rate is 0.20.** Denominator is all money genuinely recoverable, including the
508 cases routed to a human and never auto-resolved. Measuring against successfully
recovered cases only would produce 100% and mean nothing.

**508 unresolved exceptions.** Cases held for merchant approval, above the automatic
limit. They are counted as unresolved, not quietly as wins. The Evaluation screen lists
the largest of them by name.

## Exceptions

Every run surfaces the held-out cases it could not confidently resolve, largest first:
held for approval, blocked, detected-but-not-at-risk, at-risk-but-missed, and actions that
could not have worked. Shown on the Evaluation screen and printed by the CLI.

At the current operating point the largest are all `ABOVE_APPROVAL_THRESHOLD` — high-value
cases correctly routed to a human. Some of those, ground truth says, were never
recoverable anyway; the system had no way to know that, and asking a human was the right
call.

## Commands

```bash
python scripts/generate_dataset.py                  # write records to data/ and inspect
python scripts/tune_thresholds.py                   # fit on DEV ONLY
python scripts/tune_thresholds.py --objective net_revenue   # the degenerate point
python scripts/run_evaluation.py                    # held-out metrics, stored
python scripts/run_evaluation.py --json             # raw output
python scripts/run_evaluation.py --engine llm --limit 40    # score an LLM sample
```

## Threats to validity

1. **Recovered revenue is simulated.** Ground truth decides whether a customer "would have
   paid". Real recovery rates would differ, probably substantially.
2. **The dataset is a model of reality, not a sample of it.** Correlations are
   hand-designed to be plausible. These numbers say the system behaves sensibly under
   those assumptions — not that it will hit them in production.
3. **Ground truth and the generator share an author.** They are structurally independent
   and use different coefficients, but they are not independent evidence in the way a
   real labelled dataset would be.
4. **The headline numbers are the deterministic engine.** The LLM path is scored on
   samples only, for cost and reproducibility. Every run records which engine produced it.
5. **A single operating point.** No confidence intervals, no seed sweep. One seed, one
   configuration, reported exactly as it comes out.
