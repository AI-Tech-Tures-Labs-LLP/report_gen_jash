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

### Priority 1 — 🗑️ Dead code removal: ditch all Groq/DSPi, Anthropic only
**Goal:** one LLM stack (Claude), smaller codebase, legible token path.
Steps:
1. Salvage engine-agnostic logic from `report_generator.py` (SQL fixers, validation, `classify_intent`).
2. Re-point `/chat/stream` to a thin Claude call.
3. Delete unused endpoints: `/chat`, `/generate-sql`, `/execute-sql`, and the `/report` legacy `else` branch.
4. Delete DSPy files: `pipeline.py`, `signatures.py`, `groq_setup.py`, `report_signatures.py`, legacy `ReportPipeline` class.
5. Clean `requirements.txt` (drop `dspy`, `litellm`, `groq`) and `config.py` (drop `GROQ_*`, `OPENAI_*` if unused).
6. Test: app boots, `/report` + `/chat/stream` work.
**Scope:** ~1 day. Tangle is shallow. Do it on branch `joelsrgv1exp1`, review diff before commit.

### Priority 2 — ⚡ Token optimization
- Turn on **Claude prompt caching** (`cache_control`) for the schema/relationships/profile context.
  Biggest easy win: ~90% input-token savings on repeated context. Easy once #1 collapses to one stack.
- Stop re-sending full schema in repair loops.
- Right-size model tiers (Haiku for light agents, Sonnet for heavy).

### Priority 3 — 🔍 Schema-RAG (Joel's idea — do it the RIGHT way)
- **YES:** RAG for SCHEMA/CONTEXT retrieval. Embed each table's schema + description + the 8 canonical
  join paths; at query time retrieve only the 5-8 relevant tables instead of dumping all ~40.
  Big token AND accuracy win.
- **NO:** RAG for ANSWERING the question. Analytical questions need precise aggregation over fact tables
  (e.g. summing 491k diamond lines, period-over-period margin). RAG retrieves similar text, can't do the
  math — would give confident WRONG numbers. **Keep SQL generation for the actual computation.**

### Priority 4 — ✅ Accuracy hardening (Joel chose to do this last)
- Verify the 8 canonical join paths and data-quality landmines are encoded in prompts/guards:
  - `sales_invoices.discount_amount` is ALL ZERO → must use `discount_exceptions` instead.
  - Negative fulfillment lead-times exist → filter before any avg/threshold.
  - Use non-null key counts, not Excel used-range row counts.
- Build a **golden test set** (~20-30 question → expected-SQL / expected-number) across easy/medium/hard,
  ESPECIALLY fan-out cases. There are currently ZERO tests — biggest accuracy risk for a fan-out domain.
- Check generated SQL uses the documented join paths.

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
  Next action: begin Priority 1 (dead code removal) on branch `joelsrgv1exp1`.
