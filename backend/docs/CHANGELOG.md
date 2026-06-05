# Changelog — Bugs Fixed & Improvements Made

> Running record of every bug fix and improvement, in plain terms, so they can be highlighted
> in a final report later. Each entry: WHAT was wrong/missing → WHAT changed → IMPACT.
> Append a new entry every time something is fixed or improved. (Raw material, not the final doc.)
> All on branch `joelsrgv1exp1`. Baseline = intern-built version (~6/10 audit, ~280s/report, ~$0.42).

---

## Architecture / cleanup

### 1. Removed dead "two-engine" stack — standardized on Anthropic (Claude) only
- **Was:** project ran THREE LLM patterns — DSPy+Groq chat, DSPy+Groq legacy report generator, and the
  Claude multi-agent pipeline. ~30-40% of the AI code was dead "enterprise" scaffolding that compiled but
  never ran (orchestrator, optimization layer, drift detector, etc.).
- **Did:** deleted the Groq/DSPy stack (pipeline.py, signatures.py, groq_setup.py, report_signatures.py),
  the legacy /api/v2 router, a backup prompts file; re-pointed the chat endpoint to Claude; cleaned
  requirements + config. Salvaged the valuable plain-Python helpers (SQL fixers, validators).
- **Impact:** one clean LLM stack, smaller/legible codebase, removed confusion + maintenance burden.

### 2. Added cost/performance telemetry (was none)
- **Was:** no visibility into tokens, cost, or per-agent timing.
- **Did:** per-agent token/cost/timing capture surfaced to the DevTools console ([Report/Chat/Modify Metrics]),
  with a model-pricing table for $ estimates.
- **Impact:** every optimization decision is now data-driven, not guessed.

---

## Bugs found & fixed

### B1. Signal logging crashed on EVERY report (np.float64)
- **Bug:** the signal/drift logger interpolated numpy floats into SQL, producing `schema "np" does not exist`
  errors — every signal insert failed, spamming logs. Feature was 100% broken.
- **Fix:** coerce numpy/Decimal values to plain Python floats before the DB insert.
- **Impact:** log spam gone; signal logging actually works now.

### B2. Logging could crash ANY agent call on a Windows console (Unicode)
- **Bug:** the terminal logging function printed Unicode decorations (◉ ✓ box-drawing). On a non-UTF-8
  console (Windows cp1252) this threw UnicodeEncodeError that propagated up and KILLED the agent call.
  Discovered when it silently broke the intent classifier.
- **Fix:** made the print function bulletproof — degrades to ASCII-safe output, never raises.
- **Impact:** removed a real production-crash risk affecting any agent in certain terminal encodings.

### B3. Revenue fan-out / double-counting (SILENT wrong numbers) — ACCURACY
- **Bug:** when computing revenue across joined tables (e.g. by category), the SQL agent sometimes used
  `SUM(sales_order.total_amount)` across a sales_order_line join — which repeats the order total per line
  and INFLATES revenue. Intermittent (luck-dependent on which column the model grabbed). Confirmed live in
  the 5-query study: same metric, correct one run, double-counted another.
- **Fix:** (a) explicit prompt rule — total_amount only on sales_order alone; breakdowns MUST use
  sales_order_line_pricing.line_total; (b) a precise validator detector (fires only on the bad join+SUM
  pattern, zero false positives — unit tested); (c) a non-blocking accuracy warning attached to query
  results so the agent + downstream validator are warned even if they skip the validate tool.
- **Impact:** prevents confidently-wrong revenue/margin figures — the worst failure mode for a finance tool.

### B4. Frontend cache-buster not bumped → browser ran stale JS
- **Bug:** after editing script.js, the `?v=` version string in index.html wasn't changed, so browsers
  (even on hard-refresh) served the OLD cached script — making it look like new features (the intent router)
  weren't working. Diagnosed via the Network tab showing the old `/chat/stream` endpoint instead of `/ask`.
- **Fix:** bumped the version string (script.js + style.css) so browsers fetch fresh.
- **Impact:** new frontend code actually loads. PROCESS RULE going forward: bump `?v=` whenever JS/CSS changes.

---

## Performance improvements (detailed before/after in OPTIMIZATION_PLAYBOOK.md)

### P1. Shared cached context (schema/profile)
- **Was:** each agent re-fetched the full ~40-table schema via uncached tool calls every report (~64k tokens
  on the Context agent alone, cache=0).
- **Did:** build the schema/relationships/profile once, inject as ONE cached block shared across agents;
  removed the redundant fetch tools.
- **Impact:** input tokens **−58-64%**; Context agent 64k→54 tokens; report ~280s→~217s.

### P2. Parallelize Data Analyst ∥ Report Writer
- **Was:** these two agents ran serially (~50s + ~90s).
- **Did:** run them concurrently (Data Analyst made validate-only in standard mode so data stays stable);
  thread-safe telemetry.
- **Impact:** ~34s reliably saved; report ~217s→~140-165s; QA scores held/improved (up to 11-12/12).

### P3. Parallelize the Report Writer internally
- **Was:** the Writer generated ~12k tokens (summary + all explanations + insights) in one ~60-90s call.
- **Did:** split into 2 concurrent calls (explanations ∥ summary+insights), IDENTICAL output (no content cut).
- **Impact:** writer wall-clock ~halved; output equivalent.

### P4. Cheaper QA retry
- **Was:** a low QA score re-ran the WHOLE pipeline (BA→SQL→DA→Writer, ~+200s).
- **Did:** on low score, re-run ONLY the Report Writer with QA feedback (data already validated).
- **Impact:** ~110s saved on the (rare) retry; data stays stable.

---

## New capabilities

### C1. Intent router — backend decides report vs fast chat (was crude/frontend)
- **Was:** a primitive KEYWORD check in the FRONTEND JS decided report-vs-chat (brittle; AI logic in the
  client; missed natural phrasing). The "offer a report" only showed on ≥3-row answers.
- **Did:** new backend `/ask` endpoint with an LLM intent classifier (cheap Haiku, 10/10 on test phrasings) —
  report-intent → full pipeline, chat-intent → fast answer, and ALWAYS offers "Generate Report" on chat.
  All AI logic now backend-owned; frontend just renders based on a `mode` flag.
- **Impact:** smart routing — quick questions get ~30s answers, dashboard requests get the full report,
  the user is never trapped (always-on report offer). Replaced brittle frontend keyword logic with real
  understanding in the right layer.

---

## Cumulative headline
**~280s → ~140-165s per report (≈2× faster), cost ~$0.42 → ~$0.33, QA 8-9/12 → 11-12/12, plus 4 real bugs
fixed (2 of them crash-class), accuracy hardened against revenue double-counting, and a backend intent router.**

### (Accuracy / testing — DEFERRED to Joel + a data engineer, handled manually later. No test files in repo.)
Note for the record (facts confirmed while exploring, even though no test files were kept):
- The fan-out bug inflates category revenue ~2.7-3x and flips the ranking (RING ₹2.49B correct via
  line_total vs EARRINGS ₹7.26B wrong via total_amount fan-out) — hard proof the fan-out fix matters.
- "1 warehouse" is a DATA truth (only location W001 exists), NOT a bug.
- `discount_exceptions` table does NOT exist in this DB — discount lives in sales_invoices / pricing.
  (Corrects a stale assumption from the data doc.)

## Still open / flagged (not yet done)
- Accuracy hardening: data-quality landmines (discount_amount all-zero → use discount_exceptions; negative
  lead-times), golden test set (zero tests today), flat-26%-margin data question (needs data-team input).
- Security (deferred): hardcoded DB password fallback, no auth, open CORS, no rate limiting, rotate exposed
  .env secrets.
