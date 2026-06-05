# Architecture — Current State, Target Options, and Implementation Plan

> Written 2026-06-04 after measuring 5 real queries (see OPTIMIZATION_PLAYBOOK.md).
> Purpose: decide the target architecture for the report/chat pipeline and sequence the work
> to make this "a proper project" — fast, accurate, cost-predictable.

---

## 1. Current state (measured, not guessed)

Two pipelines that evolved separately but do the same core job (question → SQL → answer):

**Report pipeline** (`ai/claude_multi_agent.py`, 6 agents, all Haiku, sequential):
`Context → Business Analyst → SQL → Data Analyst → Report Writer → QA`
- ~280s, ~$0.42, ~48% cache, QA self-scores 7–9/12. Output: fixed 6 KPIs / 6 charts / 8 insights.

**Chat pipeline** (`ai/claude_report_llm.py`, 2 agents, Sonnet):
`SQL Agent → Interpreter`
- ~34s, answers the SAME analytical question (Run #5 did margin% correctly in 34s).

### Measured truths (5 runs)
- **Cost/time is query-INDEPENDENT** for reports: ~$0.42 / ~280s whether the query is simple or "everything." The 6-agent overhead dominates, not query complexity.
- **~85% of report time is the 4 non-SQL agents** (BA + Data Analyst + Report Writer + QA). SQL (the actual answer) is ~55–60s; Report Writer alone is 86–98s every run.
- **Caching half-works**: SQL agent gets ~185k cached tokens; Context, Data Analyst, Report Writer, QA all show **cache=0** (re-send static context every run).
- **Context agent sends ~64.5k input tokens, uncached, every run** (full schema+profile dump).
- **Two LLM tiers**: chat=Sonnet, reports=Haiku — inherited inconsistency.
- **The frontend ALREADY supports tiering**: chat returns fast, and `report_eligible` triggers an "offer card" (`script.js:669`) letting the user choose to generate the full report.

### Confirmed defects
- **D1 (accuracy) Fan-out unreliable**: category revenue used `solp.line_total` (correct, Run#3) in one run and `so.total_amount` via line joins (DOUBLE-COUNTS, Run#4) in another. Same metric, different SQL, luck-dependent.
- **D2 (redundancy) Data Analyst & QA validate the SAME 4 math checks** (contribution sums, drift math, consecutive periods, impact). And **QA's verdict is thrown away** — not in the response, triggers only 1 retry then auto-approves. ~80–100s spent on overlapping validation.
- **D3 (waste) cache=0 on 4 agents** — pure cost/latency left on the table.
- **D4 (bug) np.float64 signal logging** — every signal insert fails (`schema "np" does not exist`); log spam, feature 100% broken.
- **D5 (data) margin ~26% flat across all 11 categories** — likely a seed-data artifact (constant markup); "margin by category" may not be analytically meaningful on this dataset.

---

## 2. The core decision — Tiered vs Single-optimized

### Option T — Complexity-tiered routing (RECOMMENDED, pending your call)
Use the Context agent's intent classification (already exists) to route:
- **Simple/factual** ("total revenue", "top 10 customers", "margin by category") → **fast path** (chat-style 2–3 agents, ~30–40s). User gets an answer + the "generate full report" offer card (already built).
- **Dashboard/overview** ("inventory health dashboard", "comprehensive everything") → **full pipeline** (the rich 6→4-agent report).

```
                 ┌─ simple ─→ Fast path (SQL + interpret)  ~30-40s ─→ answer + [Generate Report] offer
question → route ┤
                 └─ dashboard ─→ Full pipeline (cached, merged) ~120-180s ─→ full report
```

| | Pros | Cons |
|---|---|---|
| Tiered | Common case ~8× faster (~30s). Cost ~$0.05–0.10 for simple Qs. Matches existing UI (offer card). Full richness preserved WHERE IT'S WANTED. | Routing logic to build + tune (misroutes possible). Two paths to maintain (but they share the SQL core). |

### Option S — Single optimized pipeline (every query = full report)
Keep one pipeline; make it as fast as possible via caching + merging DA/QA + parallelizing.

| | Pros | Cons |
|---|---|---|
| Single | One code path. Every query yields the rich dashboard. Simpler mental model. | Floor is ~150–180s even after all optimizations — a "what's my total revenue" question still takes minutes. Cost stays ~$0.30+/query. |

### Recommendation
**Option T.** The decisive facts: (1) cost/time is query-independent today, so simple questions are massively over-served; (2) the chat path already answers analytical questions correctly in 34s; (3) the frontend already has the offer-card UX for "want the full report?". Tiering turns the common case 8× faster with no loss to genuine dashboards. Option S leaves a 3-minute floor on trivial questions.
**→ DECISION NEEDED FROM JOEL: T or S.** (Everything in §4 below applies to both; §5 is T-specific.)

---

## 3. Target design (under Option T)

```
┌─────────────────────────────────────────────────────────────────┐
│ ROUTER (cheap Haiku call or reuse Context's intent classify)      │
│   classifies: FACTUAL_QUERY | DASHBOARD_REQUEST                   │
└───────────────┬───────────────────────────┬──────────────────────┘
        FACTUAL │                            │ DASHBOARD
                ▼                            ▼
   ┌────────────────────────┐   ┌────────────────────────────────────┐
   │ FAST PATH (~30-40s)     │   │ FULL PIPELINE (~120-180s target)    │
   │  SQL Agent (Sonnet,     │   │  Context+BA (merged) → SQL →         │
   │   tools, fan-out guard) │   │   Validator (DA+QA merged) →         │
   │  → Interpreter          │   │   Report Writer                      │
   │  → answer + offer card  │   │  all with prompt caching ON          │
   └────────────────────────┘   └────────────────────────────────────┘
        shared SQL-generation core + shared fan-out validator
```

Key target properties:
- **One SQL brain** shared by both paths (canonical join paths + fan-out guard baked into the SQL agent prompt + validator).
- **Caching ON for every agent** (fix the 4 cache=0 agents).
- **Model policy**: Sonnet for reasoning/writing agents (SQL gen, Report Writer), Haiku for mechanical (router, validator) — measure the cost delta.
- **6 → 4 agents** in the full pipeline: merge Context+BA, merge DataAnalyst+QA into one Validator.

---

## 4. Sequenced implementation plan (applies to BOTH T and S)

Ordered by ROI / risk. Each step ends with a re-run of the 5-query set + before/after in the playbook.

| # | Step | Targets | Effort | Risk | Expected gain |
|---|---|---|---|---|---|
| **1** | **Fix prompt caching on the 4 cache=0 agents** (Context, DataAnalyst, ReportWriter, QA) — correct `cache_control` breakpoint placement | D3, cost, some latency | S | Low | input-token cost ↓ materially; verify via cache_hit_rate jump |
| **2** | **Fix np.float64 logging bug** (cast `float()` before insert) OR delete logging_agent | D4 | S | Low | clean logs; signal logging works (or gone) |
| **3** | **Fan-out reliability** — bake canonical join paths into SQL agent prompt + add fan-out guard to validator (never SUM(order_total) across line joins) | D1, accuracy (#1 goal) | M | Med | consistent correct revenue/margin across runs |
| **4** | **Merge Data Analyst + QA → one Validator agent** (single pass: data-math checks + narrative/completeness); decide if QA verdict should surface to UI | D2, latency, cost | M | Med | ~1 fewer LLM call (~40-50s), removes redundant validation |
| **5** | **Model policy** — Sonnet for SQL-gen + Report Writer, Haiku for router/validator; measure delta | quality (QA scores), cost | S | Low | better narratives/accuracy; quantified cost change |
| **6** | **Merge Context + BA → one Design agent** (classify + blueprint in one call, with explicit signal validation retained) | latency | M | Med | ~25-35s |

After 1–6 (single-pipeline path): ~280s → ~150-180s, cheaper, more accurate, cleaner.

---

## 5. Tiered-routing work (ONLY if Option T chosen)

| # | Step | Effort | Risk | Notes |
|---|---|---|---|---|
| **7** | **Build the router** — reuse Context's FACTUAL vs DASHBOARD classification to pick fast-path vs full-pipeline | M | Med | the main architectural change |
| **8** | **Wire fast-path to the offer card** — fast answer + "Generate full report" (UI already exists at script.js:669) | S | Low | mostly connecting existing pieces |
| **9** | **Tune routing** — measure misroutes on the 5-query set + a few more; adjust thresholds | M | Med | accept some misroute, make "generate report" always available as fallback |

After 7–9: common factual case ~30-40s; dashboards ~150-180s. This is the "proper project" end state.

---

## 6. Open data question (not code)
**D5 — margin flatness.** Confirm with the data team whether `base_price_per_unit` / cost basis in the seed data is a constant markup (making "margin by category" meaningless), or whether real cost should come from the PO/job-card side (actual gold+diamond+making). This decides whether margin analytics are worth building on. Also re-confirm the discount_amount=0 landmine handling for any discount/net-margin query.

---

## 7. Decisions needed from Joel
1. **Option T (tiered) or S (single)?** — drives whether §5 happens. (My rec: T.)
2. **Model policy** — OK to put SQL-gen + Report Writer on Sonnet (higher quality, more cost)? (Decide after step 4 per earlier call.)
3. **QA verdict** — when we merge DA+QA, should the quality score/feedback be SURFACED to the user (it's currently discarded), or stay internal?
4. **Start order** — proposed: Step 1 (caching) first as a low-risk confidence-builder. Confirm.

> Keeps [[OPTIMIZATION_PLAYBOOK.md]] (run logs + levers) and [[JoelOptimization.md]] (roadmap) in sync as steps land.
