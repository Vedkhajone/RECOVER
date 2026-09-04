# Razorpay AI Buildathon — Track 05 (Open) — Build Playbook

**Deadline: 5 September 2026.** Written 31 Aug 2026. You have ~5 days.
Deliverables: public GitHub repo + 5-min pitch video (unlisted ok) + architecture doc.
Prize: 6/12-month AI Builder Internship, Bengaluru, in-person, ₹75,000/month.
Selection: Round 1 build → Round 2 submit → Round 3 panel review. **No resume screening, no aptitude test.**

---

## 0. The one law of this build

You are not judged on ambition. You are judged on four things, in this order:

| Criterion | What it actually means | How you prove it |
|---|---|---|
| Problem taste | Did you pick something that matters | PROBLEM.md: who hurts, how much, why now |
| Build quality | Does it run, is it structured, would I trust it | Clean clone → `make demo` works in <5 min. Tests. Error handling. |
| AI judgment | Right tool, right place — **and where you chose NOT to use one** | An architecture section titled "Where we deliberately did not use an LLM" |
| Failure recovery | What broke, what you did | FAILURES.md, written as you go, with commit SHAs |

**Corollary: a small thing that works beats a big thing that demos.** Open Track is not easier — a half-built ambitious entry loses to a tight, measured one.

**The differentiator almost nobody will have: honest numbers.** Every other track demands metrics (precision/recall, match rates, money recovered, exception lists). Open Track says "evidence that it creates value." So build an **eval harness with ground truth** and report real numbers in the README — including where it fails. That alone puts you in the top decile.

---

## 1. Scope law: the 5-day shape

Whatever the idea, it must decompose into this shape. If it can't, the idea is wrong for 5 days.

```
[ Deterministic core ]   <- the part that must never hallucinate. Plain code. Tested.
        ^
        | proposes candidates
[ LLM layer ]            <- fuzzy extraction / ranking / natural language only
        ^
        | gated by
[ Verifier / rule engine ] <- machine-checkable oracle. Accept/reject is NEVER the LLM's call.
        ^
[ Eval harness ]         <- 40-100 labelled cases, `make eval` prints a table
        ^
[ Thin UI ]              <- last. One screen. Do not gold-plate.
```

If an LLM's output can cause a wrong irreversible outcome with no deterministic gate in front of it, you have lost the "AI judgment" criterion. Razorpay is a payments company; **bounded, gated, explainable actions** is their entire religion. Mirror it.

---

## 2. Day-by-day

**Day 0 — tonight (Aug 31).** Lock the idea. Write `PROBLEM.md` (one page, your own words, no AI). Write `SPEC.md`: exactly what v1 does, plus a "Not in v1" list. Scaffold repo, first commit. Decide the eval ground-truth format.

**Day 1 (Sep 1).** Deterministic core + unit tests. No LLM yet. No UI. `make test` green.

**Day 2 (Sep 2).** LLM layer behind an interface. Eval harness + 40–100 labelled cases. First `make eval` numbers — they will be bad. Good. That's the story.

**Day 3 (Sep 3).** Reliability pass: timeouts, retries with backoff, schema validation on every model output, graceful degradation when the API is down, token/latency/cost logging. Then improve eval numbers. Then thin UI.

**Day 4 (Sep 4).** Feature freeze. Final eval run → numbers into README. Write ARCHITECTURE.md + FAILURES.md. Clean-clone test in a fresh folder. **Record the video.**

**Day 5 (Sep 5).** Buffer. Submit by midday — never at the deadline.

Feature freeze on Day 4 morning is non-negotiable.

---

## 3. Claude Code setup (Claude Pro)

### Model
- Run `/status` and `/usage` first — see what your plan offers and what your limit window looks like. **Pro limits reset every 5 hours**; with 5 days you must budget, not burn.
- Set your workhorse: `/model` → **Sonnet 5** for all implementation, refactors, tests, boilerplate. Fast and cheap on your quota.
- If Opus is offered on your plan, spend it *only* on (a) initial architecture in plan mode, (b) a bug you've failed to fix twice. Never on boilerplate.
- Budget rule: if you hit a limit at hour 3 of a 5-hour window, you lose an evening.

### Settings that actually matter
1. **Plan mode before every non-trivial change.** `Shift+Tab` twice. Make Claude produce a plan, read it, correct it, *then* let it write. Highest-leverage habit there is.
2. **`/init`** once you have a scaffold — generates `CLAUDE.md`. Then edit it by hand (template below).
3. **Kill permission fatigue:** run the `/fewer-permission-prompts` skill, or add an allowlist to `.claude/settings.json`.
4. **Hooks** to auto-run tests after edits — ask Claude: *"use the update-config skill to add a PostToolUse hook that runs `npm test -- --run` after any edit to src/**"*. Catches breakage instantly.
5. **`/clear` between tasks.** A polluted context makes Claude dumber and burns quota. One task, one context.
6. **`/code-review high`** on Day 4 before you submit. Free senior review.
7. **Commit constantly.** Judges read commit history. Forty commits like `fix: matcher dropped ties on equal scores` reads as a builder. Three commits named `update` reads as copy-paste.

### CLAUDE.md template (repo root, edit by hand)

```markdown
# <Project>

## What this is
One paragraph. The problem, the user, the guarantee we make.

## Non-negotiable invariants
- The LLM never makes the final accept/reject decision. <verifier> does.
- Every model output is validated against a schema before use.
  Invalid -> retry once -> fall back to <deterministic path> and log it.
- No network call without a timeout.
- Every accepted result carries an explanation trace (inputs -> rule fired -> outcome).

## Commands
- `make test`   unit tests
- `make eval`   run the labelled eval set, print the metrics table
- `make demo`   run the app end to end on sample data

## Conventions
- <language/framework>, <test runner>
- Business rules live in `src/rules/` as pure functions with tests.
  Never inline them into prompts.
- Prompts live in `src/prompts/` as versioned files, not string literals in logic.

## Do not
- Do not add dependencies without asking.
- Do not touch `evals/goldens/` — that is ground truth.
- Do not widen scope. See SPEC.md "Not in v1".
```

---

## 4. Copy-paste Claude Code prompts, in order

**P1 — Architecture (plan mode, best model available)**

```
Read PROBLEM.md and SPEC.md. Do not write code yet.

Design a 5-day-buildable architecture with this constraint: a deterministic core
that must never hallucinate, an LLM layer that only does fuzzy extraction/ranking,
and a verifier that gates every LLM output. The LLM must never make the final
accept/reject decision.

Give me: module boundaries, the data contract between layers, the exact interface
the LLM sits behind so I can swap or mock it, and the eval harness design
(ground-truth format + which metrics to report).

Then list the 3 riskiest parts and what I should build first to de-risk them.
Push back on anything in SPEC.md that cannot ship by Sep 4.
```

**P2 — Scaffold**

```
Scaffold the repo per the approved plan. Include: Makefile with test/eval/demo,
CI on push running lint+test, .env.example, README skeleton, and a failing
placeholder test in each module. No business logic yet. Commit.
```

**P3 — Deterministic core (repeat per module)**

```
Implement <module> per the plan. TDD: write the tests first from the spec,
show me the test list, wait for my approval, then implement until green.
Pure functions, no I/O, no LLM. Handle these edge cases explicitly: <list>.
```

**P4 — LLM layer**

```
Implement the LLM layer behind the <Interface> from the plan.
Requirements:
- Prompt lives in src/prompts/<name>.md, loaded at runtime, not inlined.
- Output validated against a strict schema; on validation failure retry once with
  the error appended, then fall back to <deterministic path> and log it.
- Timeout, exponential backoff, hard cap on retries.
- A FakeLLM implementation so `make test` never hits the network.
- Log tokens, latency and cost per call to a structured log.
Use the claude-api skill for the correct model IDs and parameters.
```

**P5 — Eval harness** (the money prompt)

```
Build `make eval`: loads evals/goldens/*.json, runs the full pipeline on each case,
compares to ground truth, prints a metrics table plus a per-case failure list with
reasons, and writes evals/results/<timestamp>.json.

It must be honest: no case is skipped, failures are counted and shown.
Then run it and show me the table.
```

**P6 — Reliability pass**

```
Adversarial review of the whole pipeline. For each external call and each LLM
output, tell me what happens on: timeout, malformed JSON, empty response, rate
limit, and a plausible-but-wrong answer. Where the answer is "we break", fix it.
Then write the tests that prove the fix.
```

**P7 — Day 4 docs (Claude drafts, you rewrite in your voice)**

```
Draft ARCHITECTURE.md: system diagram (mermaid), data flow, module
responsibilities, the trust boundary between deterministic and LLM code, and a
section titled "Where we deliberately did not use an LLM, and why".
Draft from the actual code, not from the spec. Flag anything the code does that
the docs would have to lie about.
```

**P8 — final gate**

```
/code-review high
```

### Prompting rules that keep quality up
- Feed it the spec file, not a vibe. `Read SPEC.md` beats a paragraph of description.
- Never accept a big diff you didn't read. If you can't explain it in the panel review, delete it.
- When it's wrong twice, stop re-prompting. Read the code yourself, then tell it the *cause*.
- Make it write tests before implementation. Enforce it.
- Your README, PROBLEM.md, FAILURES.md and video script: **write these yourself.** The panel will probe. AI-flavoured prose in the human-voice documents is the tell that sinks candidates in this exact program.

---

## 5. Repo structure judges want to see

```
README.md            <- hook, demo GIF, results table, quickstart, honest limitations
PROBLEM.md           <- who hurts, how much, evidence. Your voice.
ARCHITECTURE.md      <- diagram, trust boundary, "where we did NOT use an LLM"
FAILURES.md          <- required by the brief. Write it live.
SPEC.md              <- v1 scope + "Not in v1"
CLAUDE.md
Makefile             <- test / eval / demo
.github/workflows/   <- CI green badge
src/
  core/              <- deterministic, pure, tested
  rules/             <- business rules as pure functions
  llm/               <- provider behind an interface + FakeLLM
  prompts/           <- versioned prompt files
evals/
  goldens/           <- ground truth, hand-labelled
  results/           <- committed run outputs (proves you actually ran it)
tests/
```

README opens in this order: one-sentence what it does → demo GIF → **results table** → quickstart → limitations. Not a wall of text.

---

## 6. FAILURES.md — do not fake this

An explicit judging criterion, and most entries will fabricate it on the last night. Write it live, one entry per genuine breakage:

```markdown
### 2026-09-02 — Model returned valid JSON with invented IDs
**Symptom:** eval accuracy 71%, but 9 "matches" referenced IDs absent from the input.
**Root cause:** schema validated shape, not membership. Structurally valid,
semantically fabricated.
**Fix:** validator now asserts every referenced ID exists in the input set;
violations route to the exception queue instead of the accepted set. (commit a3f91c2)
**Cost:** ~3h. **Lesson:** schema validity is not grounding. Constrain the model to
the input's own vocabulary.
```

Three to five entries of that quality beat any feature you could add in the same time.

---

## 7. The 5-minute video (record Day 4, not Day 5)

Unlisted YouTube. Face on camera for the first and last 30 seconds, screen share the rest. Script it, three takes, don't read it robotically.

- **0:00–0:40 Problem.** Who, how much it costs them, how you know. Specific and personal beats statistics.
- **0:40–1:00 What you built.** One sentence + the guarantee it makes.
- **1:00–3:00 Live demo.** Real run, real data, no slides. Include one case it gets *wrong* and show it landing in the exception queue rather than silently failing. This buys more trust than a flawless demo.
- **3:00–4:00 Architecture + AI judgment.** The diagram. Then say out loud: *"Here is where I chose not to use an LLM, and why."* That sentence is aimed straight at their rubric.
- **4:00–4:40 Numbers + what broke.** The eval table. One real failure and the fix.
- **4:40–5:00 What's next + why you.**

Hard rule: **do not exceed 5:00.** Cut the demo, not the numbers.

---

## 8. About your resume

The brief says it outright: **"No resume screening. No long application."** Do not spend Day 3 rewriting a resume — that time buys nothing here. Two things replace it:

1. **Your GitHub profile.** Panel reviewers open it. Pin this repo. Real name, one-line bio, excellent README. One deep repo beats twelve tutorial forks — consider un-pinning the forks.
2. **The Google Form's free-text fields.** Answer the way the rubric reads: *problem → what works today → the number → what broke and what you did.* Concrete, first person, no adjectives.

If a resume is requested anywhere: one page, and rewrite project bullets to this shape —

> Built &lt;thing&gt; that &lt;verb&gt; &lt;specific outcome&gt;. &lt;Metric&gt; across &lt;N&gt; cases, measured with a ground-truth eval harness. Deterministic rule engine gates every model output; LLM used only for &lt;narrow task&gt;.

Result first, number inside the sentence, architectural judgment visible. Cut every "passionate", "keen learner", "worked on".

---

## 9. Traps that kill entries in this exact program

- Building for 4 days and demoing for 0. Reserve Day 4 entirely.
- No metrics. "It works well" is invisible to this rubric.
- LLM in the decision path with no gate. Instant fail on AI judgment.
- Repo that doesn't run on a clean clone. Test it in a fresh folder on Day 4.
- Secrets committed. `.env.example` only — check before pushing.
- Submitting at 11:58pm on Sep 5.
- Letting Claude write your human-voice docs. The panel will hear it.
