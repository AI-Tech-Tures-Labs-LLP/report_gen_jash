# Latency Reduction — Definitive Analysis & Committed Plan

> Written 2026-06-04 after reading the FULL pipeline control flow + all 5 measured runs.
> This is the COMMITTED plan for cutting report latency. No more re-deciding — if facts change
> during implementation, we note it here and adjust deliberately, not mid-conversation.

---

## The complete picture (measured, all 5 runs)

Pipeline is 6 STRICTLY SEQUENTIAL agents (each waits for the previous). Order + measured times:

```
Context → Business Analyst → SQL Agent → Data Analyst → Report Writer → QA
  ~5-30s      ~14-20s         ~53-64s      ~46-57s        ~75-101s      ~18-48s
```

Total ~217-285s. Cross-run averages (the stable signal):
| Agent | Typical | Tools? | Position | What it does |
|---|---|---|---|---|
| Context | ~6-22s | (was tools, now cached prefix) | 1st | classify intent, pick tables |
| Business Analyst | ~14-20s | (cached prefix) | 2nd | design 6 KPI / 6 chart blueprint |
| SQL Agent | ~53-64s | execute_sql (multi-round) | 3rd | write+run all SQL, get data |
| Data Analyst | ~46-57s | none (pure LLM) | 4th | clean/validate DATA (pre-narrative) |
| Report Writer | ~75-101s | none (pure LLM) | 5th | write ALL narrative (~12k output tokens) |
| QA | ~18-48s | none (pure LLM) | 6th | score the FINISHED narrative |

**Retry logic (claude_multi_agent.py:302):** if QA score < 4/12 → re-run BA+SQL+DA+Writer (≈ +200s).
Max 1 retry. In our 5 runs QA scored 8-12, so retry never fired — but when it does, it ~doubles latency.

## The honest root cause of latency (not what the earlier sub-agent claimed)

1. **It's sequential.** 6 LLM calls back-to-back. Wall-clock = sum of all 6. Nothing runs in parallel.
2. **Report Writer is the single biggest sink** (~75-101s) because it generates ~12k output tokens
   (summary + per-KPI + per-chart + insights) in one call. Output tokens are the slow part of any LLM call.
3. **Data Analyst (~50s) + QA (~30s) are pure-LLM passes** that add ~80s combined.

## What is / isn't true about "merging" agents (definitive — was wrong before)

- **DA and QA CANNOT be merged into one call.** They sit on OPPOSITE sides of the Report Writer:
  DA validates raw DATA *before* the writer; QA checks the NARRATIVE *after* it. QA's narrative checks
  literally can't run before the narrative exists. (claude_multi_agent.py order: DA=agent4, Writer=5, QA=6.)
- **The only real overlap** = 4 data-math checks QA repeats that DA already did. That's a ~small dedup, not
  a whole-agent removal. Earlier "merge DA+QA, save 40-50s" was WRONG — corrected here.

## What actually moves latency (ranked, committed order)

### MOVE 1 — Parallelize independent agents  ⭐ biggest structural win
The pipeline is needlessly sequential in places. Real dependencies:
- Context → BA → SQL  (hard chain: each needs the previous) — must stay sequential.
- **Data Analyst** needs SQL's data. **Report Writer** needs cleaned data. **QA** needs the narrative.
- BUT: Data Analyst (data cleaning) and parts of narrative writing don't all need to be strictly serial.
  More importantly: **DA + QA are both validation; we can run DA's data-checks CONCURRENTLY with the
  Report Writer** (writer narrates while DA validates the same data in parallel), then a slim QA at the end.
- Est. save: overlapping DA (~50s) with Writer (~90s) hides DA entirely → ~50s off. **Highest ROI.**

### MOVE 2 — Shrink the Report Writer (the 90s sink)
~12k output tokens in ONE call is the slowest single thing. Options (pick during impl):
- Split per-section? NO — more calls = more overhead. Instead: tighten the prompt to produce TIGHTER
  narratives (fewer tokens = faster) without losing the per-KPI/chart explanations the frontend uses.
- Est. save: 20-40s if output shrinks ~30%, with a quality eye.

### MOVE 3 — Slim QA + make retry cheaper
- Remove QA's 4 redundant data-math checks (DA owns those) → smaller/faster QA (~10-15s off).
- Keep QA's score→retry, but the retry currently re-runs 4 agents (~200s). Consider: on low score, retry
  ONLY the Report Writer (the usual culprit is narrative quality, not data) instead of BA+SQL+DA+Writer.
  Saves the occasional huge retry. Low risk since retry rarely fires.

### MOVE 4 — Tiered routing (Option T) — the OTHER big win, separate workstream
Simple/factual queries skip the whole 6-agent pipeline → fast path ~30s. Already its own planned step.
This is the biggest *user-facing* win for common queries. Tracked in ARCHITECTURE.md / JoelOptimization P2.

## What I will NOT do (decided, to avoid churn)
- Will NOT try to merge DA+QA into one call (impossible — opposite sides of the writer).
- Will NOT split Report Writer into many calls (more overhead, not less).
- Will NOT touch the hard Context→BA→SQL chain (real dependencies).

## COMMITTED ORDER (decided 2026-06-04)
1. **MOVE 3 (DONE)** — cheaper retry only. On QA score <4, re-run ONLY the Report Writer (with QA feedback)
   instead of BA→SQL→DA→Writer. Saves ~110s on the (rare) retry; data stays stable.
   ⚠️ DROPPED the "slim QA prompt" half: on close reading, STANDARD-mode QA checks are mostly relevance +
   narrative/insight quality (NOT redundant with DA — only DRIFT mode has the 4-check overlap). Removing 2
   data checks would save negligible time (QA cost = reading+reasoning, not check count) and risks weakening
   quality. Not worth it. Honest call: MOVE 3 = cheaper retry, nothing else.
2. **MOVE 1 (in progress)** — parallelize Data Analyst ∥ Report Writer. CHOSEN VARIANT (decided after
   tracing the merge hazard): in STANDARD mode, DA is VALIDATE-ONLY (emits data_quality_notes, does NOT
   silently delete/rewrite KPI/chart data) — so DA and Writer can both run from the SQL output concurrently
   with NO orphaned-narrative risk (data is stable, narrative always matches). DRIFT mode keeps DA's full
   causal math (drift runs are rare + structurally different). Implementation: ThreadPoolExecutor runs the
   two agents; merge = validated data + writer narrative; usage_log made thread-safe. Est ~50s saved.
   → 5-query test (expect ~217s → ~165s, reports unchanged).
3. **MOVE 2** — shrink Report Writer output (tighter narrative, ~20-40s). → test.
4. **MOVE 4** — tiered routing, into a now-lean pipeline (separate big workstream). → test.

Rationale: do the safe redundancy-removal first; tackle the risky data-flow parallelization on top of clean
validation; route LAST so the "full path" routing falls back to is already fast. One move at a time, test between.
