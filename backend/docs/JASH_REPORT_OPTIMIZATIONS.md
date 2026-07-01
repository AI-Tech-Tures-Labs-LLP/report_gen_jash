# Report Pipeline Optimization Plan

Tracks the research, cost analysis, and planned optimizations for the report generator pipeline.
All planned changes maintain full accuracy — no reduction in output quality.

---

## Current Architecture (baseline)

A 6-agent serial chain. All agents run on Haiku except the SQL agent which escalates to Sonnet for complex/drift reports.

| Agent | Model | Typical Time | What it does |
|---|---|---|---|
| 1. Context Agent | Haiku | ~5s | Classifies intent, identifies tables, routes STANDARD vs DRIFT |
| 2. Business Analyst | Haiku | ~8s | Builds blueprint: 6 KPIs, 6 charts, 1 table, insight topics |
| 3. SQL Agent | Haiku or Sonnet | **2–8 min** | Writes + executes all queries in a tool loop (25–40 rounds) |
| 4. Data Analyst | Haiku | ~10s | Quality checks (concurrent with Writer in STANDARD mode) |
| 5. Report Writer | Haiku | ~20s | Writes narrative: summary, KPI/chart explanations, insights |
| 6. QA Agent | Haiku | ~10s | Validates output, scores 0–12, triggers Writer retry if score < 4 |

**Total time today:**
- Simple reports: 4–5 minutes
- Complex / drift reports: 8–10 minutes

**Cost today:**
- Simple reports: ~$0.17–0.25
- Complex / drift reports: ~$0.57–0.92

### Why it's slow

Agent 3 (SQL) is a single-threaded tool loop. It writes one SQL query, validates it, executes it, sees the result, then writes the next query. For a standard report with 13 queries (6 KPIs + 6 charts + 1 table), this means:

```
Round 1:  write KPI-1 SQL → validate → execute → see result
Round 2:  write KPI-2 SQL → validate → execute → see result
...
Round 13: write table SQL → validate → execute → see result
```

Each round = 1 LLM call with the full 50k-token schema in context. 13 rounds × ~8–15s each = 2–3 minutes minimum for simple reports. Drift investigations run 18–25 queries = 6–8 minutes just for the SQL agent.

---

## Why parallel agents cost $6 (attempted and reverted)

The first attempt at optimization launched 13 separate Claude agents simultaneously, one per query. This destroyed the prompt cache:

- Claude's cache is keyed per-request, populated after the first call completes
- 13 agents launching at the same time = none can read from another's cache
- Every agent paid full price to write its own 50k-token cache entry
- 13 × 50k tokens × $3.75/1M (Sonnet cache write) = $2.44 just for cache writes
- Total came out to ~$6.73 per report vs ~$0.30 serial

**This approach was reverted.** The correct solution keeps one SQL agent but changes how it generates SQL.

---

## Planned Optimizations

### Opt A — SQL Batching with Guardrails + Fallback

**The core idea**: Instead of Agent 3 writing and running queries one by one in a tool loop, have it generate all SQL upfront in a single LLM call, then execute all queries in parallel via Python (zero LLM cost for execution), then do a single retry pass for any failures.

**Why this doesn't cost $6**: The parallel part is Python executing PostgreSQL queries — no LLM calls, no token cost. There is still only one Claude agent, one schema prompt, one cache entry.

```
Before:  1 agent × 13 LLM rounds × 50k token schema = 13 schema reads
After:   1 agent × 2 LLM calls × 50k token schema   = 2 schema reads
         + Python executes 13 queries in parallel     = $0 LLM cost
```

**Guardrail Layer 1 — Pre-execution validation (Python)**
Before running any query:
- Block any non-SELECT statement
- Check all table names exist in schema
- Check for obvious syntax issues (unmatched parens, missing FROM)
- Any failing query is removed from batch and flagged for retry

**Guardrail Layer 2 — Post-execution validation (Python)**
After all queries run:
- Flag any query that returned 0 rows
- Flag any query that errored
- Flag any metric value that is NULL or negative when it should be positive
- All flagged queries collected into a single retry call to Agent 3

**Guardrail Layer 3 — Fallback to tool loop**
If after the retry pass there are still failed/empty queries:
- Those specific queries fall back to the original interactive tool loop
- Agent 3 runs interactively only for the broken ones, sees live results, self-corrects
- The other queries that already succeeded are preserved

**Worst case = today's behavior.** If the entire batch fails, full fallback to tool loop.

**Applies to**: Simple reports and complex reports both. Drift investigations also benefit since their 25 queries are also largely independent.

**Estimated impact:**

| Scenario | Time | Cost |
|---|---|---|
| Happy path (all queries succeed) | 45–60s | ~$0.04 |
| 2–3 queries need retry pass | 60–90s | ~$0.05 |
| Fallback for 1–2 queries | 90–150s | ~$0.08 |
| Full fallback (batch completely fails) | 4–5 min | ~$0.22 (same as today) |

---

### Opt B — Merge Context Agent + Business Analyst into one call

**The core idea**: Context Agent (Agent 1) and Business Analyst (Agent 2) both run on Haiku, both receive the same input (question + schema), and neither calls tools. They are two sequential LLM round-trips that could be one.

A single merged Haiku call outputs:
- The context JSON (intent_mode, subject, relevant_tables, filters, signal_id if drift)
- The blueprint JSON (KPIs, charts, table spec, insight topics)

- Time saved: ~13 seconds (one fewer serial round-trip)
- Cost saved: ~20% on those two agents combined
- Accuracy impact: None — same instructions, same output, one call

---

### Opt C — Replace QA LLM with deterministic Python checks

**The core idea**: 90%+ of QA checks are counting and structure checks that can be done in Python in milliseconds:
- Does every KPI have a non-null value?
- Are there ≥ 6 insights?
- Is the summary ≥ 5 sentences?
- Does every chart have ≥ 1 row of data?
- Are all 11 drift tabs populated?

Only the remaining ~10% are genuine judgment calls (narrative tone, insight depth, driver confidence levels). The QA LLM call can be replaced with a Python checker, with an optional lightweight LLM call only when structural checks pass and subjective judgment is needed.

- Time saved: ~10 seconds
- Cost saved: Entire QA agent cost (~$0.007 per report)
- Accuracy impact: None — deterministic checks are more reliable than LLM checks for structural things

---

### Opt D — Slim payload to Writer and QA

**The core idea**: Agent 5 (Writer) and Agent 6 (QA) currently receive the full report JSON which includes:
- All SQL query strings (~2–5k tokens of SQL text the Writer doesn't need)
- Full raw data rows for every chart/table (Writer only needs labels + values)
- Internal metadata fields

Pre-process the report before passing it to Writer and QA:
- Strip all SQL strings
- For each chart, keep only `{label, value}` pairs (drop raw DB columns)
- For QA, keep only counts + structure (not the full narrative)

Reduces payload from ~8–12k tokens to ~2–3k tokens for Writer and QA.

- Time saved: ~5 seconds
- Cost saved: ~30% on Writer and QA agent token costs
- Accuracy impact: None — Writer only needs values to write about, not the SQL that produced them

---

## Combined Impact

| Metric | Today | After A+B+C+D |
|---|---|---|
| Simple report time | 4–5 min | **45–90 sec** |
| Complex/drift report time | 8–10 min | **90 sec – 3 min** |
| Simple report cost | ~$0.22 | **~$0.04** |
| Complex/drift report cost | ~$0.75 | **~$0.12** |
| Overall time reduction | — | ~80–85% |
| Overall cost reduction | — | ~80–85% |

---

## Implementation Order

1. **Opt A** first — accounts for ~85% of total savings, self-contained change to SQL agent logic
2. **Opt B + C + D** together — all small, low-risk, can be done in one pass after A is verified

---

## Token & Pricing Reference

**Models:**
- Sonnet: `claude-sonnet-4-6`
- Haiku: `claude-haiku-4-6`

**Pricing (USD per 1M tokens):**

| Model | Input | Output | Cache read | Cache write |
|---|---|---|---|---|
| Sonnet | $3.00 | $15.00 | $0.30 | $3.75 |
| Haiku | $1.00 | $5.00 | $0.10 | $1.25 |

**Shared DB context** (schema + relationships + data profile): ~50,000 tokens
- First agent pays cache write: 50k × $1.25/1M = $0.0625 (Haiku)
- Agents 2–6 pay cache read: 50k × $0.10/1M = $0.005 each (Haiku)
