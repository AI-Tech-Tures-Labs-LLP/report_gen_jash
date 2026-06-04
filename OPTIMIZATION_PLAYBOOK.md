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

## Test Query Set — LOCKED for this measurement round (5 queries)

Agreed 2026-06-04. Process: run all 5, log each below (NO fixes yet), then build the plan of action.

1. **Inventory dashboard:** "Show me a dashboard of inventory health across all warehouses." *(inventory/fulfillment layer, multi-warehouse grouping)*
2. **Simple time-series:** "Create a report on monthly revenue trends for the last 12 months" *(baseline, Run #1 — DONE)*
3. **Category breakdown:** "Create a report on product category performance comparing sales volume and revenue" *(category grouping + fan-out to line items)*
4. **Worst-case scope:** "Show me a comprehensive dashboard of everything — sales, inventory, customers, and logistics" *(huge/vague scope — max tokens/time stress test)*
5. **Accuracy stress (added):** "Gross margin % by product category" *(fan-out + component-split money: gold+diamond+making−discount. WATCH: discount_amount is all-zero → must use discount_exceptions. Most likely to be SILENTLY WRONG.)*

Coverage: #1-4 give breadth across data domains + a worst-case; #5 adds the accuracy depth the dashboards miss.
Each stresses something different: inventory grouping, simple aggregate, category fan-out, unbounded scope, margin math + data-quality landmine.

(Later/separate round — not this batch: discount-trend landmine, cross-layer vendor attribution, period-over-period drift.)

---

## Run Log (append a block per run)

### Run #1 — baseline (see table above)
- Query: monthly revenue trends 12mo | 265.9s | $0.4341 | cache 46.2% | QA 7/12
- Issues: P1 latency, P2 lopsided cache, P3 64k context, P4 KPI6=None + QA 7/12, P5 np.float64 bug
- Changes applied: none yet (baseline)

### Run #2 — Query #1 (Inventory dashboard) — "Show me a dashboard of inventory health across all warehouses."
**Result:** 279.5s pipeline / 299.7s total · **$0.4220** · 131,554 in / 36,608 out · 48.4% cache · 6 agents · QA **9/12**
trace_id 1879ced3. total_tokens 428,938. cache_read 190,065, cache_creation 70,711.

| Agent | Time | Input | Output | Cache read | Notes |
|---|---:|---:|---:|---:|---|
| Context + Signal | **30.1s** | 64,504 | 1,089 | 0 | ⚠ 64k in, cache=0 (SAME as Run#1). **get_data_profile tool took 17.9s** — profiler is slow/uncached on first hit |
| Business Analyst | 14.0s | 17,069 | 2,168 | 4,400 | partial cache |
| SQL Agent | 54.9s | 15,271 | 9,061 | 185,665 | ✅ cache working; 13 tool calls over 3 rounds, no SQL errors |
| Data Analyst | 48.4s | 8,757 | 8,080 | 0 | cache=0 |
| Report Writer | **97.4s** | 10,723 | 12,998 | 0 | ⚠ SLOWEST again (~98s, like Run#1). cache=0. 13k output |
| QA Agent | 34.6s | 15,230 | 3,212 | 0 | cache=0; scored 9/12 |

**Confirmed patterns (now 2 runs):**
- Context agent = ~64k input tokens, cache=0, EVERY run (whole schema+profile dumped). Universal P3.
- Report Writer ≈ 97-98s EVERY run — the dominant latency sink, independent of query. Universal P1.
- Cache=0 on the SAME 4 agents (Context, Data Analyst, Report Writer, QA) both runs. Universal P2.
- ~6 agents, ~266-280s, ~$0.42 per report — remarkably STABLE across query types. Cost/time is ~constant regardless of question complexity → the OVERHEAD (agents+context) dominates, not the query.

**NEW finding — Context agent latency varies via the data profiler:** Run#1 Context=7s, Run#2 Context=30s. The difference = `get_data_profile` took 17.9s here (cold). The profiler is a latency wildcard; caching/warming it matters.

**🔴 NEW ACCURACY BUG (P6) — "Warehouse Locations Active = 1" / all inventory in 1 location:**
Query asks for inventory "across all warehouses" but KPI3 returns **1** and Chart 1 (Inventory Value by Warehouse) has only **1 row**. Either (a) the data genuinely has one `to_location`, or (b) the SQL is grouping on the wrong column (`finished_goods_inventory.to_location` may not be the warehouse dimension). A dashboard titled "across all warehouses" showing ONE warehouse is a silent correctness/coverage problem — the model didn't flag that the premise (multiple warehouses) isn't met. WATCH whether other location/dimension queries do this.

**Stock Turnover Ratio = 0.7986** computed as AVG(quantity_available/quantity_received) — that's a fill-rate, NOT turnover (turnover = COGS/avg inventory). Likely a mislabeled/incorrect metric. Another silent-accuracy flag.

**P5 (np.float64 logging bug) — still firing**, ~18 failed signal inserts this run. Same root cause. Non-fatal, log spam.

**Cost note:** estimated_cost now uses CORRECTED pricing (Opus updated; Sonnet/Haiku verified vs screenshot). All agents on claude-haiku-4-5.

### Run #3 — Query #3 (Category performance) — "Create a report on product category performance comparing sales volume and revenue"
**Result:** 260.9s pipeline / 261.1s total · **$0.4028** · 128,927 in / 33,207 out · 48.7% cache · 6 agents · QA **8/12**
trace_id 2004b202. cache_read 189,569, cache_creation 71,126.

| Agent | Time | Input | Output | Cache read | Notes |
|---|---:|---:|---:|---:|---|
| Context + Signal | 10.3s | 64,510 | 914 | 0 | profiler fast this run (cached); still 64k in, cache=0 |
| Business Analyst | 20.4s | 16,891 | 2,534 | 4,400 | |
| SQL Agent | 59.4s | 16,651 | 9,224 | 185,169 | ✅ cache working; 13 tool calls / 3 rounds, NO errors |
| Data Analyst | 48.5s | 8,409 | 7,733 | 0 | cache=0 |
| Report Writer | **86.5s** | 10,448 | 9,568 | 0 | slowest again (~87s). cache=0 |
| QA Agent | 35.8s | 12,018 | 3,234 | 0 | cache=0; scored 8/12 |

**Patterns hold (now 3 runs): ~$0.40-0.42, ~261-280s, 6 agents, ~48% cache, QA 7-9/12. Overhead dominates, not query.**
- Context = 64.5k in / cache=0 EVERY run. Report Writer = slowest EVERY run (87-98s). Same 4 agents cache=0 EVERY run.
- Confirms profiler latency variance: Context was 30s (Run#2 cold profiler) vs 10s here (warm). Tool times this run all <1s.

**✅ GOOD — fan-out handled correctly here (the accuracy stress this query was meant to test):**
SQL joins product_master → sales_order_line → sales_order_line_pricing and uses `SUM(solp.line_total)` for revenue and `SUM(sol.quantity)` for volume — separate aggregates, NOT a single multiplied join. Revenue (₹2.49B top category RING) and volume (52,871 units EARRINGS) computed on the right grains. No obvious double-counting. The pattern_checker + SQL agent prompt appear to be doing their job on category-level money. (Still worth confirming totals vs ground truth, but the SQL SHAPE is correct.)

**Minor accuracy watch:** "Number of Closed Orders = 15,884" is a GLOBAL count (no category dimension) sitting in a *category-performance* report — slightly off-subject (matches Run#1 revenue total). Also AOV uses so.total_amount (order-level) while category revenue uses solp.line_total (line-level) — two different revenue definitions in one report (₹10.41Cr narrative total vs per-category sums). Possible internal inconsistency to verify.

**P5 np.float64 bug:** not shown in this paste but almost certainly still firing (signals enabled).

### Run #4 — Query #4 (Everything dashboard / worst-case) — "Show me a comprehensive dashboard of everything — sales, inventory, customers, and logistics"
**Result:** 284.6s pipeline / 284.9s total · **$0.4190** · 127,468 in / 36,249 out · 49.1% cache · 6 agents · QA **8/12**
trace_id 9f121746. cache_read 192,875, cache_creation 72,803. (Full per-agent JSON captured.)

| Agent | Time | Input | Output | Cache read | Cost | Notes |
|---|---:|---:|---:|---:|---:|---|
| Context + Signal | 22.4s | 64,505 | 1,563 | 0 | $0.072 | profiler 8.6s (warm-ish). 64k in, cache=0 |
| Business Analyst | 19.3s | 17,771 | 2,673 | 4,400 | $0.039 | |
| SQL Agent | 60.1s | 12,053 | 9,640 | 188,475 | $0.163 | ✅ cache; 18 tool calls / 3 rounds, no errors |
| Data Analyst | 57.0s | 8,632 | 8,331 | 0 | $0.050 | cache=0 |
| Report Writer | **95.1s** | 11,074 | 11,140 | 0 | $0.067 | slowest again. cache=0 |
| QA Agent | 30.8s | 13,433 | 2,902 | 0 | $0.028 | cache=0; 8/12 |

**KEY FINDING — the worst-case did NOT break the pattern.** "Everything" still produced exactly 6 KPIs / 6 charts / 8 insights, ~$0.42, ~285s — same as a simple query. This CONFIRMS the pipeline is HARD-CAPPED at 6/6/8 regardless of scope. Implication two ways:
- GOOD for cost/latency predictability (no runaway).
- ⚠️ BAD for accuracy on broad asks: a request for "sales, inventory, customers, AND logistics" got squeezed into the same 6 KPIs — **logistics/customers under-covered** (only 1 customer chart, logistics basically absent except a vendor query that didn't even make the final 6). The fixed 6/6/8 structure can't satisfy genuinely multi-domain asks. Universal structural limit, not a per-query bug.

**🔴 ACCURACY (P7) — TWO different revenue definitions again, now starker:**
- KPI "Total Revenue YTD = ₹1.24B" uses `sales_order.total_amount` (order-level).
- Chart "Revenue by Category" uses `SUM(so.total_amount)` JOINed through sales_order_line → **this DOUBLE-COUNTS**: order total_amount is repeated for every line in the order, so category revenue is inflated/fan-out. Compare to Run#3 which correctly used `solp.line_total` for category revenue. **Same metric, different SQL, inconsistent across runs.** This is the fan-out bug appearing when the agent picks total_amount instead of line_total. CONFIRMS fan-out is NOT reliably handled — it depends on which column the agent grabs.
- "Outstanding Purchase Orders (Open) = ₹2.3B" vs "Current Inventory Value = ₹4.47Cr" — wildly different magnitudes, plausible but unverified.

**Confirms across 4 runs:** Context 64.5k/cache=0 (×4), Report Writer slowest 86-98s (×4), same 4 agents cache=0 (×4), ~$0.40-0.42 & ~261-285s & 48-49% cache & QA 8/12-ish (×4). ROCK SOLID universal patterns.

**P5 np.float64 bug:** still firing (visible in this run's tail).

### Run #5 — Query #5 (Gross margin %) — "show me Gross margin % by product category"
**⚠️ RAN VIA CHAT (/chat/stream), NOT the report pipeline** — so it's the 2-agent chat path, not 6-agent report. Not apples-to-apples with #1-4, but EXTREMELY informative.
**Result:** 33.6s · **$0.326** · 4,064 in / 2,059 out · 73.4% cache · **2 agents** · model **claude-sonnet-4-6**

| Agent | Time | Input | Output | Cache read | Cost | Notes |
|---|---:|---:|---:|---:|---:|---|
| Chat SQL Agent | 26.2s | 3,189 | 1,752 | 179,851 | $0.319 | Sonnet. 4 rounds (2 failed validations + execute). cache hit 179k |
| Chat Interpreter | 7.3s | 875 | 307 | 0 | $0.007 | Sonnet |

**HUGE FINDINGS:**

**A) CHAT IS 8× FASTER THAN REPORT (33.6s vs ~280s) for the SAME analytical question.** 2 agents vs 6. This is the single biggest lever discovered: most of the 280s/`$0.42` report cost is the 4 NON-SQL agents (BA, Data Analyst, Report Writer, QA) — i.e. the "make it pretty" layer, not the "get the answer" layer.

**B) CHAT RUNS ON SONNET, REPORTS RUN ON HAIKU.** claude_report_llm.py (chat) uses config.CLAUDE_MODEL=sonnet; claude_multi_agent.py (reports) forces _HAIKU on every agent. So chat costs MORE per token but is a stronger model. This is an INCONSISTENCY we introduced/inherited — same product, two model tiers. Note: chat's $0.326 is dominated by 61k cache-WRITE (first call this session); steady-state chat would be far cheaper.

**C) ✅ MARGIN SQL WAS CORRECT — and the validator's self-repair WORKED.** The accuracy stress query actually came out RIGHT:
- Revenue = SUM(solp.line_total); Cost = SUM(solp.base_price_per_unit * sol.quantity); margin% = (rev-cost)/rev. Correct component logic, no fan-out double-count (used line-level pricing, multiplied cost by quantity correctly).
- The validator caught `round_missing_numeric_cast` TWICE and the agent fixed it (added ::numeric) before executing — the sql_pattern_checker / validate tool loop did its job. This is the Claude-side repair working (vs the removed Groq repair).
- It did NOT touch the discount_amount=0 landmine because it computed cost from base_price, not discount — so margin is "gross margin over base cost," a reasonable interpretation. WATCH: but is base_price_per_unit the right cost basis? (vs actual gold+diamond+making cost from PO side). Plausible but worth a ground-truth check.

**D) RESULT LOOKS SUSPICIOUS (accuracy watch):** all 11 categories have gross margin 25.70%–26.07% — a razor-thin 0.37-point spread across totally different product types (ankle, nose_pin, necklace...). Real jewelry margins vary far more by category. This near-uniform ~26% suggests margin may be derived from a near-constant markup in the seed data, OR the cost basis (base_price_per_unit) is a fixed % of selling price in the data → margin is artificially flat. Likely a DATA artifact, not a SQL bug, but it means "margin by category" is not analytically meaningful on this dataset. Flag for the discount/cost-basis discussion.

**Chat vs Report comparison (the strategic insight):**
| | Report (#1-4) | Chat (#5) |
|---|---|---|
| Agents | 6 | 2 |
| Time | ~280s | ~34s |
| Model | Haiku | Sonnet |
| Output | full dashboard (6 KPI/6 chart/8 insight) | sql + answer + insights text |
| Per-query cost | ~$0.42 | ~$0.33 (inflated by 1st-call cache write) |

---

## Decision order (proposed — adjust as data dictates)
1. **L1 (caching everywhere)** first — cheapest, helps cost + latency, addresses P2/P3. Re-measure Run #1.
2. **P5 bug** — quick cleanup so logs are readable while we work (or delete logging_agent).
3. **L2 (parallelize)** — biggest latency win for P1.
4. Then escalate to queries 4 & 6 to expose **accuracy** issues → apply L5/L6.
5. Revisit L4 (schema-RAG) once caching shows whether context size is still the bottleneck.

> Keep [[JoelOptimization.md]] (the high-level roadmap) and memory in sync as each lever lands.
