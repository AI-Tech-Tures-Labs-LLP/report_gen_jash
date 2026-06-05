# Dead-Code Removal Plan — Review Before Execution

> Written 2026-06-04. Branch: `joelsrgv1exp1`. App is internet-facing — every step ends with a boot+smoke test.
> Decisions locked: delete `query_cache` now (rebuild caching properly at Priority 2); write plan first (this doc).
> Evidence behind every claim is in the deep import-trace audit (3 agents, zero-false-positive standard).

---

## Guiding rules
- **Salvage before delete.** Move engine-agnostic helpers out of legacy classes before removing the class.
- **Every Tier ends with:** `.\venv\Scripts\python.exe app.py` boots clean + smoke test `/report` (provider=claude) and `/chat/stream`.
- **One commit per Tier** so anything can be reverted cleanly.
- Salvaged code keeps its current behavior — no rewrites mixed into a deletion commit.

---

## TIER 1 — Zero-risk deletions (no migration, no code depends on these)

**1.1 Delete file:** `ai/claude_prompts_backup.py` — 0 importers anywhere (verified grep). Pure backup.

**1.2 `requirements.txt` — remove 3 never-imported packages:**
- `litellm` — 0 `import` statements in repo.
- `groq` — 0 `import` statements (groq is used only *through* dspy, not directly).
- `openai` — 0 `import` statements.
- ⚠️ KEEP `dspy` for now (still imported by the legacy stack — removed in Tier 2).

**1.3 `config.py` — remove dead OpenAI config:**
- `OPENAI_API_KEY` (line 16) and `OPENAI_MODEL` (line 17) — read NOWHERE outside config.py.
- (Leave `.env`'s OpenAI key alone; just stop referencing it in code. Note: rotate exposed keys later.)

**Test:** boot app, hit `/report` + `/chat/stream`. Commit: `chore: remove backup file, unused deps (litellm/groq/openai), dead OpenAI config`.

---

## TIER 2 — Ditch Groq/DSPy (Priority 1 proper). The big one.

This is a PORT, not a pure delete, because the live `/chat/stream` and the salvageable report helpers
currently live in DSPy-bound files. Order matters.

### 2.1 SALVAGE engine-agnostic helpers out of `report_generator.py`
These ~18 helpers are plain Python (no dspy/LLM) and several are used by the LIVE Claude path or by
endpoints we keep. Move them to a new module `ai/report_utils.py` (or keep them as module-level funcs in
report_generator.py and only delete the `ReportPipeline` *class*). Salvage list (file:line in report_generator.py):
- `_inject_filters()` :801 — SQL WHERE injection (used by apply_filters)
- `_clean_sql()` :990
- `_build_question_with_context()` :1108
- `_get_analytical_framework()` :1165
- `_repair_json()` :1515, `_extract_json()` :1606
- `_fix_kpi_sql()` :1644
- `_format_indian()` :1746, `_normalize_kpi_label()` :1816, `_sql_signature()` :1826, `_clean_kpis()` :1849
- `_execute_kpi_sql()` :2155, `_execute_chart_sql()` :2308, `_validate_chart_data()` :2342, `_execute_table_sql()` :2423
- `_extract_subject_lock()` :2445, `_cache_key()` :2537
- `_detect_applicable_filters()` :3168, `apply_filters()` :3220
- **`_enforce_chart_diversity()` :3039 — CRITICAL: also called by live `claude_multi_agent.py:598`. Must survive.**
- ALSO KEEP module-level `_fix_report_sql()` :166 and `classify_intent()` — `classify_intent` is imported by the
  chat endpoints (app.py:182, 266); `_fix_report_sql` is referenced from the live tool path.

### 2.2 Re-point `/chat/stream` to Claude
- Currently `app.py:257` `/chat/stream` uses `SQLAnalystPipeline` (DSPy/Groq). Frontend calls it (script.js:622).
- Rewrite as a thin call through `ai/claude_client.py` (the existing Anthropic client). Reuse salvaged
  `classify_intent` + SQL validation/execution + `sql_pattern_checker`. Keep the SSE streaming response shape
  the frontend expects.

### 2.3 DELETE unused chat/SQL endpoints (frontend calls NONE of these — only /chat/stream)
- `/chat` (app.py:179) — DELETE
- `/generate-sql` (app.py:137) — DELETE
- `/execute-sql` (app.py:147) — DELETE (bonus: removes an unauthenticated arbitrary-SQL endpoint = security win)
- Remove their Pydantic models if unused after (e.g. `GenerateSQLResponse`, `ExecuteSQLResponse`).

### 2.4 DELETE the legacy report branch + class
- `app.py:393-397` — the `else:` "Legacy DSPy pipeline" branch in `/report`. Make Claude the only path
  (drop the `if provider == "claude"` conditional, always use `EnhancedReportPipeline`).
- `/report/apply-filters` (app.py:400) and `/report/modify` (app.py:428) currently use `ReportPipeline`
  (DSPy). ⚠️ These ARE used by the frontend (report.js). **Decision needed** — see Open Questions Q1.
- Delete the `ReportPipeline` class from report_generator.py once its salvageable parts are extracted (2.1).

### 2.5 DELETE DSPy files + config + dep
- Files: `ai/pipeline.py`, `ai/signatures.py`, `ai/groq_setup.py`, `ai/report_signatures.py`.
- `requirements.txt`: remove `dspy`.
- `config.py`: remove `GROQ_API_KEY` (line 12), `GROQ_MODEL` (line 13).

**Test:** boot, `/report` (claude), `/chat/stream`, `/report/apply-filters`, `/report/modify`.
Commit: `refactor: remove Groq/DSPy stack, standardize on Anthropic (salvage report helpers, re-point /chat/stream)`.

---

## TIER 3 — Remove inert "enterprise" scaffolding

These are imported + instantiated in `enhanced_pipeline.py` but their work-methods are never called.
Requires editing enhanced_pipeline.py + the package `__init__.py` re-exports, NOT just `rm`.

### 3.1 DELETE files
- `ai/agents/orchestrator.py` (AgentOrchestrator never instantiated; only imported)
- `ai/intelligence/drift_detector.py` (drift_engine instantiated at enhanced_pipeline.py:62, `.analyze()` never called)
- `ai/optimization/query_cache.py` (per your decision — rebuild at Priority 2)
- `ai/optimization/query_batcher.py` (batch_engine instantiated :66, `execute_batch()` never called)
- `ai/optimization/context_compression.py` (LLMContextCompressor never instantiated)

### 3.2 EDIT `enhanced_pipeline.py` (remove instantiations + stat references)
- Line 17: drop `AgentOrchestrator` from import (KEEP `AgentEventBus, LoggingAgent, AgentStep`; and `AgentContext` used at :167).
- Line 18: drop `DriftDetectionEngine` (KEEP `SignalDetectionEngine`).
- Line 19: drop `QueryCacheManager, QueryBatchingEngine` (KEEP `GraphOptimizationEngine` — it IS called at :330).
- Line 62: remove `self.drift_engine = ...`
- Lines 65-66: remove `self.cache_manager` + `self.batch_engine`.
- Lines 156, 426: `'cache_enabled': self.cache_manager is not None` → remove or hardcode False.
- Lines 430-431, 433-434: remove the `cache`/`batch` stats blocks.
- ⚠️ `enable_caching` / `enable_optimization` constructor flags + app.py:374-379 pass them → clean up the now-dead flags.

### 3.3 EDIT package `__init__.py` re-exports (or imports break)
- `ai/optimization/__init__.py`: keep only `GraphOptimizationEngine`. Remove the other 3 imports + `__all__` entries.
- `ai/intelligence/__init__.py`: keep only `SignalDetectionEngine` (+ its DetectedSignal/SignalType/SignalSeverity).
  Remove DriftDetectionEngine/DriftResult/DriftType.
- `ai/agents/__init__.py`: remove `AgentOrchestrator, AgentStep, AgentPipelineError`?? — ⚠️ `AgentStep` is imported by
  enhanced_pipeline.py:17. CHECK if AgentStep is actually used; if not, drop it too. Keep base_agent exports + EventBus + LoggingAgent.

### 3.4 KEEP (do NOT delete — verified alive or load-bearing)
- `ai/agents/base_agent.py` (defines AgentContext, used at enhanced_pipeline.py:167)
- `ai/agents/event_bus.py`, `ai/agents/logging_agent.py` (logging fires in request path)
- `ai/intelligence/signal_detector.py` (`.analyze()` called per-chart at :246)
- `ai/optimization/graph_optimization.py` (`.optimize_all_graphs()` called at :330)
- `db/migrations/001_add_logging_tables.sql` (logging_agent writes to these tables)

**Test:** boot, full `/report` run, confirm logging + signals + graph-opt still work.
Commit: `chore: remove inert scaffolding (orchestrator, drift, query cache/batcher, context compression)`.

---

## Resolved questions (investigated 2026-06-04)

**Q1 — RESOLVED: PORT `/report/modify` + `/report/apply-filters` to Claude.**
Investigation: `/report/modify` is heavily used by report.js (4 call sites — it's the live "edit report"/floating-chat
feature); `/report/filters` + `/report/apply-filters` also used. The Claude pipeline has NO modify/apply-filters
capability (only `_detect_applicable_filters` at claude_multi_agent.py:908), so we can't just route there. BUT
reading `modify()` (report_generator.py:3313): the ENTIRE method is salvageable plain Python (chart-type lock,
lean-JSON strip, JSON repair, SQL re-execution, KPI cleaning) — the ONLY DSPy touchpoint is the single LLM call
`self.report_mod(...)` at line 3351 returning `.updated_report_json`. Same shape for apply_filters (mostly SQL re-run).
→ Port = replace that ONE dspy.Predict call with a Claude call (via claude_client.py) taking the same inputs
(current_report, modification, schema_info) → JSON. ~2-3 hrs, mechanical, features preserved. LOW RISK.

**Q2 — RESOLVED: DELETE the `/api/v2/*` router (api_enhanced.py).** Confirmed: the ONLY `/api/v2` references in the
repo are inside `venv/.../litellm/` (third-party noise). Zero references in our code/frontend. Delete the whole router
+ its `app.include_router` line in app.py.

**Q3 — Secrets (still open, gates committing):** rotate exposed `.env` keys (Anthropic/OpenAI/Groq + AWS RDS password)
and confirm `.env` is gitignored before the first commit.

---

## Suggested execution order
1. Tier 1 (safe, fast, builds confidence) → commit.
2. Resolve Q1 (read apply-filters/modify + report.js) → decide port vs route vs drop.
3. Tier 2 (Groq removal + /chat/stream re-point + report endpoints) → commit.
4. Tier 3 (inert scaffolding) → commit.
5. Update JoelOptimization.md progress log after each tier.
```
