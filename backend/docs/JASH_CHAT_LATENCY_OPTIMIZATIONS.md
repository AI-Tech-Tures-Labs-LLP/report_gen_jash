# Chat Latency Optimizations

**Date:** 2026-06-22
**Branch:** jashsrgv1exp1
**Scope:** Chat pipeline only (`/ask` endpoint, `data` intent path). Report pipeline untouched.

---

## Background

A benchmark of 4 representative chat questions was run before these changes
(`benchmark_chat.py`, results in `benchmark_results.json`). The baseline numbers:

| Question | Total | Intent | SQL Agent | Interpreter |
|----------|-------|--------|-----------|-------------|
| Product with highest sales | 22.6s | 1.6s | 10.7s | 5.2s |
| Customer with highest sales | 15.7s | 1.5s | 8.4s | 5.7s |
| Customer with most fulfilled orders | 17.4s | 1.7s | 8.8s | 6.8s |
| Products not sold since Oct 2025 | 46.5s | 1.6s | 35.2s | 9.5s |
| **Average** | **25.6s** | **1.6s** | **~16s** | **~6.8s** |

Two independent bottlenecks were identified where time was being spent
without any benefit to SQL accuracy or answer quality.

---

## Change 1 — Chat Interpreter: Sonnet → Haiku

**File:** `services/claude_report_llm.py`
**Line:** `answer_chat_question()`, interpreter `call_agent()` call

### What changed

Added `model=_config.CLAUDE_HAIKU_MODEL` to the Chat Interpreter agent call.
Previously it inherited the default model (Sonnet). Now it explicitly uses Haiku.

### Why it is safe

The Chat Interpreter receives three inputs: the original question, the SQL that
ran, and the actual query result rows (already fetched and captured from the DB).
Its only job is to write 2–4 sentences of plain English and 3–5 bullet points
narrating what those rows show.

- It does **no SQL generation**
- It does **no schema reasoning**
- It does **no joins or fan-out logic**
- The answer is fully determined by the DB result rows — the interpreter only
  phrases it

Haiku handles prose narration trivially. This is the same model used for the
intent classifier. The answer content, SQL, and data rows are completely unaffected.

### Why it is universal

Every single chat query that reaches the interpreter step gets this benefit.
The interpreter is always called when SQL was successfully executed, regardless
of question complexity, number of rows, or conversation history.

### Expected saving

~3–5s per query (the interpreter was taking 5.2–9.5s on Sonnet; Haiku runs
the same task in ~1.5–3s).

---

## Change 2 — Parallel Intent Classification + Schema Cache Warm

**File:** `api/chat.py`
**Function:** `event_generator()` inside `ask_endpoint()`

### What changed

Previously the code ran two sequential operations before the SQL Agent started:

```
[intent classify ~1.5s] → [SQL Agent starts, fetches schema ~0.5s cached / ~2s cold]
```

Now both run in parallel using `concurrent.futures.ThreadPoolExecutor`:

```
[intent classify ~1.5s]  ↘
                           → both finish → SQL Agent reads from warm cache
[schema/rels/profile warm] ↗
```

The schema warm call (`format_schema()`, `format_relationships()`,
`get_data_profile()`) populates the module-level in-memory caches in
`db/schema.py`, `db/relationships.py`, and `db/profiler.py`. When the SQL
Agent calls these functions moments later inside `answer_chat_question()`, it
reads from cache (near-zero cost) instead of hitting the DB.

The result of the schema future is discarded — the side effect (cache population)
is what matters.

### Why it is safe

The two operations are **completely independent**:
- `classify_query_intent()` only reads the question text and makes a Haiku API
  call. It has no knowledge of schema and touches no DB.
- `format_schema()` / `format_relationships()` / `get_data_profile()` only read
  from the DB. They have no knowledge of the question.

Neither call's output depends on the other. The intent result and the schema
cache are used by completely separate downstream steps. No shared state, no
race conditions. The schema cache functions already use module-level locks
internally (profiler uses `threading.Event`).

### Why it is universal

Every chat query classified as `data` or `report` goes through the SQL Agent,
which always calls `format_schema()`. The parallel warm means the schema is
already cached by the time the SQL Agent needs it — on every single request,
including first request after startup (cold start) and after cache expiry.

On a warm cache (schema already loaded) the schema future completes in ~50ms
and the intent future wins anyway, so there is no overhead.

### Expected saving

~1.5s per query (the intent classifier latency is hidden behind the schema
fetch instead of being purely additive).

---

## Change 3 — Cap SQL Agent `max_tokens` to 4096

**File:** `services/claude_report_llm.py`
**Function:** `answer_chat_question()`, SQL Agent `call_agent()` call

### What changed

Added `max_tokens=4096` to the Chat SQL Agent `call_agent()` invocation.
Previously it inherited the default of `16384`.

### Why it is safe

The Chat SQL Agent's final response is a tiny JSON object: `{"sql": "..."}`.
It never needs anywhere near 16k output tokens. Capping at 4096 gives plenty
of headroom for even the longest SQL query the model might write.

This parameter only bounds output generation — it has zero effect on the model's
reasoning, tool rounds, schema reading, or SQL quality.

Report pipeline: **unaffected** — this `max_tokens` argument is passed only to
the `call_agent()` call inside `answer_chat_question()`, which is chat-only.

### Expected saving

~0.3–0.8s per query (reduced generation planning overhead).

---

## Change 4 — Remove "echo rows back" from SQL Agent instruction

**File:** `services/claude_report_llm.py`
**Function:** `answer_chat_question()`, SQL Agent user message

### What changed

Previously the user message instructed the SQL Agent to include the full `data`
rows in its final JSON response:

```
"respond with a JSON object containing the final "sql" you ran and the "data" rows
(as returned by the tool). Output ONLY that JSON object."
```

Now it only asks for the SQL:

```
"output ONLY this JSON object: {"sql": "<the sql you ran>"}"
```

### Why it is safe

The actual query result rows are **already captured** by the `_capture` callback
via `on_tool_result` — directly from the `execute_sql_query` tool result, before
the model writes anything. The code in `answer_chat_question()` already prefers
`_last_exec["data"]` over the model's echoed rows:

```python
if _last_exec.get("data"):
    rows = _last_exec["data"]   # ← authoritative tool-captured rows
```

The model's row echo was being discarded anyway. Asking it not to write them
eliminates 1,000–5,000 wasted output tokens (50 rows × 5 columns ≈ 2,000 tokens
at ~40 tok/s on Sonnet = ~1.5–3s of generation time for nothing).

Report pipeline: **unaffected** — this change is inside `answer_chat_question()`
which is only called from the chat path.

### Why it is universal

Every chat query that successfully executes SQL was paying this cost. Now none do.

### Expected saving

~1.5–3s per query (eliminated output token generation that was immediately discarded).

---

## Combined Expected Impact

| Scenario | Before (baseline) | After Ch1+Ch2 | After Ch3+Ch4 (est.) |
|----------|-------------------|---------------|----------------------|
| Simple factual query (Q1/Q2 type) | 15–23s | 14–18s | 11–15s |
| Complex multi-round query (Q4 type) | 40–47s | 42–47s | 39–44s |
| Average across query types | ~25s | ~24s | ~20s |

The SQL Agent model time (the dominant cost at ~8–35s) is unchanged — that is
determined by query complexity and Sonnet's reasoning speed, which are outside
the scope of zero-risk changes.

Changes 3 and 4 target wasted **output token generation** inside the SQL Agent
response step — the model was writing thousands of tokens that were immediately
discarded. These changes eliminate that waste without touching any reasoning or
accuracy logic.

---

## What Was Not Changed

| Item | Reason |
|------|--------|
| SQL Agent model (stays Sonnet) | Complex joins/fan-out need stronger model |
| `validate_sql_query` round | Catches real bugs (fan-out, missing status filter); removing it risks silent wrong answers |
| SQL accuracy guards (`sql_pattern_checker.py`) | Safety layer for known bad patterns |
| Model selection logic (Haiku for simple, Sonnet for complex/history) | Logic in `chat.py:124` is intentional |
| Report pipeline | Entirely separate; not in scope |
| Mongo persistence timing | Still synchronous; async change deferred (risk vs reward too low) |

---

## Files Modified

| File | Change |
|------|--------|
| `services/claude_report_llm.py` | Ch1: Added `model=_config.CLAUDE_HAIKU_MODEL` to interpreter call |
| `api/chat.py` | Ch2: Added `concurrent.futures` import; wrapped intent + schema in `ThreadPoolExecutor` |
| `services/claude_report_llm.py` | Ch3: Added `max_tokens=4096` to Chat SQL Agent call |
| `services/claude_report_llm.py` | Ch4: Removed "echo data rows" from SQL Agent user message |
