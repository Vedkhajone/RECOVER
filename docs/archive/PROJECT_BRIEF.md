# Haqdaar — Track 05 (Open Track) submission brief

*"Haqdaar" = one who has a rightful claim. Fallback name if you prefer English: **ScholarScan**.*

---

## The one sentence

> Indian students miss scholarships they are legally entitled to, because eligibility is buried in 20-page government PDFs. Haqdaar reads those PDFs once, turns them into machine-checkable rules, and screens a student against every scheme in seconds — returning only decisions it can cite a clause for.

That sentence is the whole pitch. A non-engineer gets it in one pass. That is why this idea wins the video.

---

## Why this beats the alternatives on their exact rubric

**Problem taste.** Every scholarship in India has published, objective eligibility rules — income ceiling, category, state, course, marks, age. The rules are public. The money is allocated. Students still miss out, purely because nobody reads 40 PDFs to find the 6 that apply to them. That is a distribution failure, not a policy failure, and it is exactly the kind of thing software fixes. You are a student. You have lived this. You can talk about it for 5 minutes without notes.

**Build quality.** The runtime is a deterministic rule engine over a committed rule corpus. No network call in the hot path. It is fast, testable, and reproducible on a clean clone — the three things a judge checks.

**AI judgment.** This is the strongest card in the deck. The LLM does the one job it is genuinely best at — reading messy unstructured legalese and emitting structure — and is then *architecturally forbidden* from the decision. You get to say, on camera:

> "The model never decides whether a student is eligible. It extracts rules offline, I review them by hand, and they get committed to the repo. At runtime a pure-function rule engine decides, and every verdict cites the clause it came from. I did that because a hallucinated 'you're eligible' costs a student a wasted application and a real deadline — that's an irreversible harm, and irreversible actions don't go behind a probabilistic system."

That is the sentence that gets you the callback. It is Razorpay's own engineering philosophy — bounded, gated, explainable actions — applied outside payments.

**Failure recovery.** This build *will* break in interesting, documentable ways (see §6). You will have real FAILURES.md entries, not invented ones.

---

## Architecture

Two pipelines. The split is the whole design.

```
PIPELINE A — offline, run once, output committed to the repo
────────────────────────────────────────────────────────────
  scheme PDF ──► LLM extraction ──► RuleSpec JSON ──► YOU review by hand ──► rules/*.json
                 (the hard, fuzzy,       (strict            (human gate)        (committed,
                  genuinely-AI job)      schema)                                 versioned)

PIPELINE B — runtime, deterministic, no LLM in the decision path
────────────────────────────────────────────────────────────────
  student profile ──► [LLM: normalise free text] ──► typed profile
                            │                             │
                       (schema-validated,                 ▼
                        falls back to a form)      ┌──────────────────┐
                                                   │   RULE ENGINE    │  pure functions
                                                   │  pure, tested    │  zero I/O
                                                   └──────────────────┘
                                                            │
                              ┌─────────────────────────────┼──────────────────────────┐
                              ▼                             ▼                          ▼
                          ELIGIBLE                    NOT ELIGIBLE                NEEDS INFO
                       + cited clause              + the rule that failed     + the exact question
                       + doc checklist                                          to ask next
                              │
                              ▼
                    [LLM: plain-language explanation of a verdict already made]
```

**The three outcomes matter.** `NEEDS_INFO` is not a cop-out, it is the judgment call. The system never guesses eligibility when a field is missing — it names the one question that would resolve it. Say this in the video; abstention is a maturity signal most hackathon projects lack.

### Where AI is used, and where it is banned

| Job | AI? | Why |
|---|---|---|
| PDF → structured eligibility rules | **Yes** | Messy, unstructured, high-variance. Nothing else does this well. Offline, so latency and cost don't matter, and a human reviews every output. |
| Free-text profile → typed fields ("dad earns around 3 lakh" → `income: 300000`) | **Yes, gated** | Genuine NLP. Schema-validated; on failure, fall back to a plain form. Worst case is a UX downgrade, not a wrong verdict. |
| Explaining a verdict in plain language / Hindi | **Yes** | Verdict is already decided. The LLM is a translator, not a judge. |
| **Deciding eligibility** | **NO** | Irreversible harm, and the rules are fully expressible as code. A probabilistic system here would be indefensible. |
| **Ranking / deadline maths / document checklists** | **NO** | Deterministic. Using an LLM would be worse *and* slower. |

That last block is literally a section heading in your ARCHITECTURE.md: **"Where we deliberately did not use an LLM."**

---

## v1 scope — build exactly this

**In:**
1. Corpus of **25–40 real scheme documents** (National Scholarship Portal + 2–3 state portals + a few private/CSR scholarships). Publicly downloadable PDFs.
2. Pipeline A extractor + the committed `rules/*.json` corpus, each with a `source_url` and `clause_quote`.
3. Rule engine supporting: income ceiling, category, domicile/state, course & year, minimum marks, age range, gender, disability status, family occupation. Composable AND/OR.
4. Runtime API: `POST /screen` → ranked eligible list, ineligible list with the failing rule, needs-info list with the question.
5. Eval harness with **60+ hand-labelled (profile, scheme) → verdict** pairs. Reports precision, recall, abstention rate, and per-case failures.
6. One-page web UI: fill profile → results with cited clauses, deadlines, document checklist.

**Not in v1** (write this list in SPEC.md and defend it in the video — scope discipline is a signal):
- Auto-submitting applications
- Live scraping / scheme auto-discovery
- Login, accounts, saved profiles
- Document upload and OCR verification
- More than 2 languages in the explanation layer

### Stack
Python 3.11 + FastAPI + Pydantic (schema validation is core to the design, so Pydantic earns its place) + SQLite + one HTML page with plain fetch or HTMX. `pytest` for tests. Fewest moving parts, cleanest clean-clone story on Windows. **Swap to TypeScript/Next.js only if TS is genuinely your stronger language** — do not learn a stack this week.

---

## The numbers you will report

Put this table at the top of the README. These are the slots; you fill them with what you actually measure.

| Metric | What it proves |
|---|---|
| Rule-extraction accuracy: `N/M` rules correct vs. your manual review | Pipeline A works, and you checked |
| Eligibility precision / recall over 60+ labelled cases | The core is correct, honestly measured |
| Abstention rate (`NEEDS_INFO` %) | You'd rather ask than guess |
| False-positive count, stated plainly | You are honest about the failure that costs a student most |
| Screening latency (expect: milliseconds — no LLM in the path) | The architecture pays off |
| Schemes surfaced per profile vs. the ~1–2 a student typically knows | **The value number.** This is your headline. |

That last row is your money slide. Something in the shape of *"a median test profile matched N schemes; a student manually would have found 1–2."* Measure it, don't estimate it.

---

## Demo beats for the video (§7 of the playbook has the timings)

1. Show a real 18-page scheme PDF. Scroll it. Let it feel awful. **This is the problem, felt, in 8 seconds.**
2. Show the extracted `rules/*.json` next to it, with the clause quote highlighted.
3. Fill a profile in the UI. Hit screen. Results in milliseconds.
4. Click one result → the cited clause. **"Every verdict traces to a line in a government PDF."**
5. Show one `NEEDS_INFO` case. *"It doesn't guess. It asks."*
6. **Show a case it got wrong in eval**, and where that lands. Judges trust you more after this, not less.
7. Terminal: `make eval`, the table appears.

---

## What will break (pre-write these FAILURES.md entries as they happen)

You will genuinely hit most of these. Log them the moment they occur, with commit SHAs:

- The model extracts rules that are **structurally valid but not in the document** — invented income ceilings. Fix: validator requires a verbatim `clause_quote` that must be found in the source text, else the rule is rejected.
- Income limits written as "₹2.5 lakh", "2,50,000", "Rs. 2.5L p.a." — normalisation hell. Fix: deterministic parser with a test table, not a prompt.
- A scheme with "OR" eligibility (SC/ST **or** income below X) silently evaluated as AND. Fix: explicit rule-tree type, plus tests.
- PDFs that are scanned images and yield no text. Fix: detect and route to a manual queue rather than emitting empty rules that would wrongly mark everyone eligible.
- Free-text normaliser reads "no income" as `0` for a student whose *family* income is the actual criterion. Fix: field-level disambiguation question.

Each of these is a real "AI judgment" data point. Do not smooth them over in the writeup.

---

## Your first Claude Code prompt

Write `PROBLEM.md` yourself first, in your own words, tonight — one page, no AI. Then open Claude Code in the repo, press `Shift+Tab` twice for plan mode, and paste:

```
Read PROBLEM.md and PROJECT_BRIEF.md. Do not write code yet.

Design the implementation for the v1 scope in PROJECT_BRIEF.md §"v1 scope".
Hard constraints:
- Two pipelines: offline rule extraction (LLM + human review, output committed)
  and a runtime path with NO LLM in the eligibility decision.
- The rule engine is pure functions, zero I/O, fully unit-testable.
- Three verdicts: ELIGIBLE / NOT_ELIGIBLE / NEEDS_INFO. Never guess.
- Every verdict carries a citation: scheme id, rule id, verbatim clause quote.

Give me:
1. The RuleSpec JSON schema, expressive enough for AND/OR trees over the nine
   criteria listed in the brief. Show it evaluated against two worked examples.
2. Module boundaries and the interface the LLM sits behind so I can mock it.
3. The eval harness design: golden-file format for (profile, scheme) -> verdict,
   and which metrics to print.
4. The 3 riskiest parts, and what to build first to de-risk them.

Then tell me honestly what in this scope will not ship by Sep 4, and what you'd cut.
```

Then follow prompts P2–P8 in [BUILDATHON_PLAN.md](BUILDATHON_PLAN.md) §4.

---

## Tonight's checklist (Aug 31)

- [ ] Create the GitHub repo, public, named `haqdaar`
- [ ] Write `PROBLEM.md` in your own words — one page. Who, how much, why now.
- [ ] Copy §"v1 scope" into `SPEC.md`, including the "Not in v1" list
- [ ] Download 25–40 scheme PDFs into `data/schemes_raw/` and commit them
- [ ] First commit
- [ ] Run the P1 prompt above in plan mode

The corpus is your critical path. If you have the PDFs by tonight, the rest is engineering. Get the PDFs.
