# Optimization Playbook — Query-by-Query, Universal Fixes

> **Method:** Run real queries → read the telemetry → find the UNIVERSAL root cause (not a
> one-off patch) → apply → re-measure the same query → confirm it improved without breaking others.
> Goal: best achievable **accuracy + speed + cost** across ALL query types, via changes that
> generalize. Written 2026-06-04 from the first real measured run.

---

## How to use this file
1. Pick a query from the **Test Query Set** below (start simple, escalate).
2. Run it, open the metrics modal, paste the per-agent numbers into a new row of the **Run Log**.
3. Diagnose against the **Universal Levers** list — is this query slow/expensive/wrong for a
   reason that affects *every* query, or just this one?
4. Apply the smallest universal change. Re-run the SAME query. Record before/after.
5. Re-run 1–2 OTHER query types to confirm no regression. Then move on.

Rule: prefer fixes that help the whole pipeline (caching, context size, agent design) over
fixes that only help one question. Record every change so we can attribute gains.

---

## Baseline — Run #1 (the measured reference)

**Query:** "Create a report on monthly revenue trends for the last 12 months" (a SIMPLE query)
**Result:** 265.9s · $0.4341 · 128,315 in / 36,076 out · 46.2% cache hit · 6 agent calls · QA 7/12

| Agent | Time | Input | Output | Cache read | Cost | Notes |
|---|---:|---:|---:|---:|---:|---|
| Context + Signal | 7.0s | 64,520 | 565 | 0 | $0.067 | ⚠ 64k tokens, cache=0 — whole schema/profile dumped uncached |
| Business Analyst | 20.5s | 16,772 | 3,065 | 4,400 | $0.039 | partial cache |
| SQL Agent | 51.7s | 15,557 | 8,680 | 179,139 | $0.178 | ✅ cache working; 13 tool calls + 1 self-repaired SQL error |
| Data Analyst | 40.1s | 7,269 | 6,812 | 0 | $0.041 | cache=0 |
| Report Writer | 98.2s | 9,436 | 12,349 | 0 | $0.071 | ⚠ SLOWEST. cache=0. 12k output tokens (narrative) |
| QA Agent | 48.4s | 14,761 | 4,605 | 0 | $0.038 | cache=0; scored report 7/12 |

All agents run on **claude-haiku-4-5**, sequentially.

---

## What Run #1 proved (problems ranked by impact)

### P1 — Latency is the worst problem: 266s for a SIMPLE query
- Sequential 6-agent chain. Report Writer (98s) + SQL (52s) + QA (48s) + Data Analyst (40s) dominate.
- A user will not wait ~4.5 min. This is the #1 product issue, bigger than cost.

### P2 — Caching only works on 1 of 6 agents (46% overall, but lopsided)
- SQL Agent: 179k cache reads ✅. Context, Data Analyst, Report Writer, QA: **cache=0**.
- The big static context (schema + profile + system prompts) is being RE-SENT uncached on 4 agents.
- Root cause to investigate: cache_control breakpoints / whether each agent's system prompt is
  structured so the static prefix is cacheable, and whether per-request dynamic data busts the cache.
- This is the SINGLE highest-ROI lever — fixes BOTH cost and latency at once.

### P3 — 64k uncached tokens in the Context agent = the "whole schema in every prompt" problem
- Measured proof of the original audit concern. Context agent dumps full 40-table schema + profile.
- Universal fixes: (a) prompt-cache that static block, (b) schema-RAG to send only relevant tables,
  (c) trim the data profile. Likely do (a) first (cheap), then (b) for the real win.

### P4 — Accuracy gaps even on a simple query
- KPI 6 "Year-over-Year Growth" = **None** (SQL failed; we removed Groq auto-repair so it no longer
  retries → degrades to None). Decide: add a CLAUDE-based KPI/SQL repair, or accept graceful N/A.
- QA self-scored **7/12** ("conditional"). Claude thinks its own output is mediocre — room to improve
  prompts / blueprint quality.
- Data-quality landmines NOT yet enforced (discount_amount=0, negative lead-times, fan-out) — these
  will bite on margin/fulfillment queries. (See database-domain notes.)

### P5 — BUG: signal logging is 100% broken (np.float64)
- `logging_agent` interpolates numpy floats into raw SQL → `schema "np" does not exist`, every signal.
- Non-fatal (try/except) but spams logs and the signal-logging feature never persists.
- Fix: cast `float(...)` on metric values before insert (and ensure params are plain Python types).
- OR: delete logging_agent (it was flagged inert scaffolding in the removal plan).

---

## Universal Levers (the toolbox — each should help ALL queries)

| Lever | Targets | Effort | Expected gain |
|---|---|---|---|
| **L1. Prompt caching on all agents** | P2, P3, cost, latency | M | ~50-80% input-token cost ↓ after warm cache; some latency ↓ |
| **L2. Parallelize independent agents** | P1 latency | M-H | Data Analyst/Report Writer/QA partly parallelizable → big wall-clock ↓ |
| **L3. Trim/shrink static context** (smaller profile, lean schema) | P3, cost | M | fewer input tokens everywhere |
| **L4. Schema-RAG: send only relevant tables** | P3, accuracy, cost | H | big token ↓ + fewer wrong-table hallucinations |
| **L5. Claude SQL/KPI repair** (replace removed Groq repair) | P4 accuracy | M | fewer N/A KPIs |
| **L6. Encode data-quality business rules** in prompts | P4 accuracy | M | correct margins/lead-times/discounts |
| **L7. Reduce agent count / merge agents** | P1 latency, cost | M | e.g. fold QA into Report Writer, or skip Data Analyst when data is clean |
| **L8. Smaller output where possible** (Report Writer 12k out) | P1, cost | L-M | shorter narratives = faster + cheaper |
| **L9. Right-size models** (already all Haiku — maybe Sonnet only where needed) | accuracy vs cost | L | targeted quality bumps |

---

## Test Query Set (escalating difficulty — covers the domain)

Run these as we optimize, to ensure fixes generalize:

1. **Simple aggregate:** "Monthly revenue trends for the last 12 months" *(baseline, Run #1)*
2. **Single-dimension breakdown:** "Top 10 customers by total order value this year"
3. **Channel/category mix:** "Sales by channel and order type, last 6 months"
4. **Margin (component-split money):** "Gross margin % by product category" *(tests fan-out + margin math)*
5. **Cross-layer attribution:** "Which vendor caused the most fulfillment delay last quarter"
6. **Discount landmine:** "Discount trends over the last year" *(must use discount_exceptions, NOT discount_amount=0)*
7. **Period-over-period drift:** "Why did revenue drop in [month] vs prior month"

Each tests a different stress: fan-out, component math, attribution, data-quality landmines, drift.

---

## Run Log (append a block per run)

### Run #1 — baseline (see table above)
- Query: monthly revenue trends 12mo | 265.9s | $0.4341 | cache 46.2% | QA 7/12
- Issues: P1 latency, P2 lopsided cache, P3 64k context, P4 KPI6=None + QA 7/12, P5 np.float64 bug
- Changes applied: none yet (baseline)

### Run #2 — (after first optimization)
- _to be filled_

---

## Decision order (proposed — adjust as data dictates)
1. **L1 (caching everywhere)** first — cheapest, helps cost + latency, addresses P2/P3. Re-measure Run #1.
2. **P5 bug** — quick cleanup so logs are readable while we work (or delete logging_agent).
3. **L2 (parallelize)** — biggest latency win for P1.
4. Then escalate to queries 4 & 6 to expose **accuracy** issues → apply L5/L6.
5. Revisit L4 (schema-RAG) once caching shows whether context size is still the bottleneck.

> Keep [[JoelOptimization.md]] (the high-level roadmap) and memory in sync as each lever lands.
