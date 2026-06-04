# Joel's Optimization & Restructure Roadmap

> **Read this first.** This is the working plan for modernizing the StackHunter report-generation
> project. It captures the decisions, the verified architecture, and the ordered priority list.
> Written 2026-06-04 by Joel (incoming tech lead) + Claude after a full codebase audit.
> Keep this updated as work progresses — check off items and add notes.

---

## TL;DR — Where things stand

- **Project:** FastAPI app that generates analytics reports / answers NL questions over a jewelry-ERP
  PostgreSQL database ("StackHunter" — sales orders, gold/diamond pricing, vendors, job cards, invoices).
- **Built by:** interns. Inherited by Joel. **Verdict: ~6/10, RESTRUCTURE not redesign.** Core engine works;
  ~30-40% of the AI code is dead "enterprise" scaffolding that compiles but never runs.
- **Domain difficulty:** accuracy is genuinely HARD (multi-table fan-out, component-split money, period
  comparisons). Not a toy text-to-SQL. The interns built the *right* defenses for it.
- **Decision:** ditch all Groq/DSPy, standardize on **Anthropic (Claude) only**.
- **Status:** app runs locally (venv on Python 3.14.5 in repo root, all deps installed, `.env` filled).
  Internet-facing in production.
- **Priority 1 (Groq removal) = DONE** (uncommitted on `joelsrgv1exp1`). Telemetry added. 5-query
  measurement study done. Architecture decided: **Option T (complexity-tiered routing)**.
  See companion docs: **`ARCHITECTURE.md`** (target design + sequenced plan) and
  **`OPTIMIZATION_PLAYBOOK.md`** (5 run logs + measured findings).

---

## Verified architecture (confirmed by import-tracing, not guessed)

There are **THREE LLM patterns** in the code today, not two:

| Pattern | Engine | Powers | Keep? |
|---|---|---|---|
| `claude_multi_agent.py` (via `enhanced_pipeline.py`) | **Claude** | `/report` when `provider=claude` | ✅ **KEEP** — the real product |
| `ReportPipeline` in `report_generator.py` (`provider=groq` default) | DSPy/Groq | `/report` `else` branch — code comment literally says "Legacy DSPy pipeline" (app.py:394) | 🗑️ DITCH |
| `SQLAnalystPipeline` in `pipeline.py` | DSPy/Groq | `/chat`, `/chat/stream`, `/generate-sql`, `/execute-sql` | 🗑️ DITCH (re-point /chat/stream) |

**Endpoint disposition** (frontend only calls `/chat/stream` — verified at `frontend/script.js:622`):

| Endpoint | Frontend uses? | Action |
|---|---|---|
| `/report` (provider=claude) | ✅ | KEEP |
| `/report` else/legacy branch | ❌ | DELETE the `else` branch (app.py:393-397) |
| `/chat/stream` | ✅ | **RE-POINT to Claude** (keep feature) |
| `/chat` | ❌ | DELETE |
| `/generate-sql` | ❌ | DELETE |
| `/execute-sql` | ❌ | DELETE (also shrinks attack surface — runs arbitrary SQL) |

**What's ALIVE vs DEAD theater** (from audit):
- ALIVE: `claude_multi_agent.py`, `claude_client.py`, `claude_tools.py`, `claude_prompts.py`,
  `sql_pattern_checker.py` (11 hallucination guards — domain-appropriate, KEEP), `validator.py`,
  the whole `db/` layer.
- DEAD/THEATER (built, never called in request path — safe to delete after verifying):
  `ai/agents/orchestrator.py`, `ai/agents/event_bus.py`, `ai/optimization/*`
  (query_cache, query_batcher, context_compression — instantiated but NEVER invoked, $0 savings),
  `ai/intelligence/drift_detector.py` (dead), `signal_detector.py` (half-wired, runs post-hoc),
  `claude_prompts_backup.py`.
- STALE DOCS: `implementation.md`, `stacklogix_enhanced_agents.md` describe aspirational features
  (37 signals, 11-tab drift cards) that are NOT implemented. Code moved past the docs.

**Engine-agnostic gold to SALVAGE from report_generator.py** (3,104-line god-file, plain Python, no DSPy needed):
`_fix_report_sql`, `_validate_and_execute_sql`, `classify_intent` (imported by /chat endpoints),
schema validation, filter injection. Only the thin LLM-call layer needs porting to Claude.

---

## PRIORITY LIST (Joel's chosen order)

### Priority 1 — 🗑️ Dead code removal: ditch all Groq/DSPy, Anthropic only ✅ DONE (uncommitted)
**Goal:** one LLM stack (Claude), smaller codebase, legible token path.
DONE 2026-06-04: deleted DSPy files (pipeline/signatures/groq_setup/report_signatures), backup file,
api_enhanced.py; salvaged plain-Python helpers; re-pointed /chat/stream to Claude (reuses the SQL Agent);
deleted /chat,/generate-sql,/execute-sql + legacy /report branch; cleaned requirements + config.
App boots, /report + /chat/stream verified vs live DB. Also added telemetry (token/cost/timing per agent,
logged to DevTools console). On branch `joelsrgv1exp1`, awaiting Joel's commit.

---

> **2026-06-04 — Post-measurement update.** After P1, we ran a 5-query measurement study (logged in
> `OPTIMIZATION_PLAYBOOK.md`) and wrote `ARCHITECTURE.md`. Headline findings:
> - Report cost/time is QUERY-INDEPENDENT (~$0.42 / ~280s for ANY query) → overhead dominates.
> - ~85% of report time = the 4 non-SQL agents (BA/DataAnalyst/ReportWriter/QA), not the data.
> - Caching half-works: SQL agent cached, but Context/DataAnalyst/ReportWriter/QA all cache=0.
> - Fan-out bug is REAL & intermittent: category revenue used line_total (correct) one run, total_amount
>   via line joins (DOUBLE-COUNT) another — luck-dependent.
> - DataAnalyst & QA do redundant validation; QA's verdict is then discarded (1 retry, auto-approve).
> - Chat path answers the SAME analytical question in ~34s (Sonnet) vs report's ~280s (Haiku).
> **DECISION: Option T — complexity-tiered routing.** Simple/factual queries → fast 2-3 agent path (~30-40s);
> dashboard requests → full pipeline (optimized to ~150-180s). Frontend already supports this via the
> "Generate full report" offer card (script.js:669). Results only improve (simple Qs get Sonnet; dashboards
> keep full richness + get caching/fan-out fixes). Only risk = router misroutes, mitigated by the offer-card
> fallback + tuning + reversibility. **Full sequenced plan in `ARCHITECTURE.md` §4-§5.**

### Priority 2 — ⚡ Token optimization + pipeline speed  (now driven by ARCHITECTURE.md)
This priority now ABSORBS the architecture work. Sequenced steps (detail in `ARCHITECTURE.md`):
1. **Caching fix** — fix `cache_control` on the 4 cache=0 agents (Context/DataAnalyst/ReportWriter/QA).
   Lowest-risk pure win; do FIRST as the confidence-builder. Re-run the 5 queries to prove it.
2. **np.float64 logging bug** — cast `float()` before insert (or delete logging_agent). Stops log spam.
3. **Merge DataAnalyst + QA → one Validator** — removes redundant validation, ~1 fewer LLM call (~40-50s).
4. **Model policy** — Sonnet for SQL-gen + Report Writer, Haiku for router/validator (decide after step 3).
5. **Merge Context + BA → one Design agent** — ~25-35s.
6. **Tiered router (Option T)** — route FACTUAL→fast path, DASHBOARD→full pipeline; wire the offer card.
Target: common factual query ~30-40s; dashboards ~150-180s; cheaper; cleaner.

### Priority 3 — 🔍 Schema-RAG (Joel's idea — still valid, RE-EVALUATE after caching)
- **YES:** RAG for SCHEMA/CONTEXT retrieval — retrieve only the 5-8 relevant tables + canonical join paths
  instead of dumping all ~40 (Context agent currently sends 64.5k uncached tokens every run).
- **NO:** RAG for ANSWERING — keep SQL generation for the actual math (RAG would give confident wrong numbers).
- **NOTE:** Do this AFTER P2 step 1 (caching). If caching already makes the 64k context cheap, RAG's token
  win shrinks and it becomes mainly an ACCURACY play (fewer tables = fewer wrong-table joins). Decide then.

### Priority 4 — ✅ Accuracy hardening  (partly pulled FORWARD into P2)
- **Fan-out reliability (pulled into ARCHITECTURE.md step 3, do EARLY):** bake the 8 canonical join paths
  into the SQL agent prompt + add a fan-out guard to the validator (never SUM(order_total) across line joins).
  This is your #1 goal and the 5-run study PROVED it's broken intermittently — don't leave it for last.
- Data-quality landmines in prompts/guards:
  - `sales_invoices.discount_amount` is ALL ZERO → must use `discount_exceptions` instead.
  - Negative fulfillment lead-times → filter before any avg/threshold.
  - Use non-null key counts, not Excel used-range row counts.
- Build a **golden test set** (~20-30 question → expected-SQL/number), ESPECIALLY fan-out cases. ZERO tests today.
- **DATA QUESTION (D5):** margin came back ~26% flat across all 11 categories — likely a seed-data constant-markup
  artifact. Confirm cost basis with data team before building margin analytics. (See ARCHITECTURE.md §6.)

### Priority 5 — 🔒 Security (DEFERRED by Joel, but flagged — app is internet-facing NOW)
- 🔴 Hardcoded DB password fallback in `config.py`.
- 🔴 ZERO auth on all endpoints (incl `/admin/*`). Add API-key/JWT.
- 🔴 CORS wide open (`*`). Lock to known origins.
- 🔴 No rate limiting → anyone can run up the LLM bill. Add `slowapi`.
- 🟠 Errors leak raw SQL to clients; no query timeouts.
- **Rotate the secrets in `.env`** (Anthropic, OpenAI, Groq keys + AWS RDS DB password) — they have been
  exposed in plaintext/chat. Confirm `.env` is gitignored before ANY commit.

---

## Key facts for any AI picking this up

- **DB is AWS RDS PostgreSQL** (`database-1...ap-south-1.rds.amazonaws.com`, db `backup_v2_inventory`),
  NOT Neon as docs claim. ~40 tables, jewelry ERP. See `STACKHUNTER_DATA_FAMILIARIZATION.md` for the
  full data model, grains, row counts, 8 join paths, and data-quality landmines.
- **Run on Windows:** `python` is the broken MS-Store stub. Use `py -3.14` or `.\venv\Scripts\python.exe`.
  From repo root: `.\venv\Scripts\python.exe app.py` → serves on http://localhost:8000.
- **Repo layout:** code is in the repo ROOT (was previously nested in `report_gen_jash/`). Branch: `joelsrgv1exp1`.
- **Working style:** Joel wants blunt, honest assessments. Verify claims (trace imports) before deleting on
  a live repo. Review diffs before committing.

---

## Progress log

- 2026-06-04: Full audit done. Architecture verified. Priorities set. Env set up & app running.
- 2026-06-04: **Priority 1 DONE** — Groq/DSPy fully removed, Anthropic-only. Telemetry added
  (per-agent token/cost/timing → DevTools console). All uncommitted on `joelsrgv1exp1`.
- 2026-06-04: **5-query measurement study DONE** (logged in `OPTIMIZATION_PLAYBOOK.md`).
  Architecture decided = **Option T (tiered routing)**, documented in `ARCHITECTURE.md`.
- **NEXT ACTION:** Priority 2, Step 1 — fix prompt caching on the 4 cache=0 agents (lowest-risk pure win),
  then re-run the 5-query set to prove the gain before any structural change.

## Companion docs
- `ARCHITECTURE.md` — current state, T-vs-S tradeoff (chose T), target design, sequenced build plan.
- `OPTIMIZATION_PLAYBOOK.md` — the 5 measured run logs + universal levers + run-by-run findings.
- `STACKHUNTER_DATA_FAMILIARIZATION.md` — the real DB data model, grains, join paths, landmines.
