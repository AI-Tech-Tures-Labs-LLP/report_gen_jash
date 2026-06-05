# Backend Restructure Plan (phased, layer-based)

> Goal: reorganize the repo into clean `backend/` and `frontend/` trees, backend organized by
> ARCHITECTURE LAYERS (api / services / ai / db / core), important files only at root, working .md
> docs moved into `backend/docs/`. Env files live in `backend/.env` and `frontend/.env` — NONE at root.
> Safety net: every phase ends with a boot test; if broken, revert to previous commit. Commit per phase.
> RULE: before executing each phase, RE-ANALYZE that phase's exact files/imports, then move. Accuracy > speed.

---

## Grounding facts (from deep import-graph analysis)

- **~15.5 KLOC Python**: app.py (488), config.py (47), data_sync.py (101), ai/ (~6.6k + agents/intelligence/
  optimization subpkgs), db/ (867).
- **Entrypoint:** `uvicorn app:app` — hardcoded in Dockerfile AND app.py `__main__`. Changes when app.py moves.
- **2 Path(__file__) gotchas:** app.py:468 `FRONTEND_DIR = Path(__file__).parent/"frontend"`;
  report_generator.py:33 `_CACHE_DIR = .../parent.parent/".report_cache"`. Both break on move → fix explicitly.
- **~29 lazy imports** inside app.py route handlers (easy to miss) + lazy imports in report_generator.py
  (lines ~2814, ~2931) that EXIST ON PURPOSE to break a circular dependency.
- **⚠ Real circular dependency (already present):** app → ai.report_generator → ai.claude_tools →
  ai.report_generator. Survives today ONLY because claude_tools imports report_generator lazily / report_gen
  imports claude_* lazily. MUST keep these lazy through the move (do not "tidy" them to top-level).
- **All imports are ABSOLUTE** (`from ai.x import y`, `from db.x import y`, `import config`). Strategy decision
  below (Option A vs B) determines whether they stay the same or get a prefix.
- **Frontend:** index.html, report.html, script.js, report.js, style.css. Served via StaticFiles mount +
  FileResponse using FRONTEND_DIR. `?v=` cache-buster in HTML.
- **Layer map:** API = app.py (routes+models+inline logic, MIXED). SERVICES = report_generator (god-file),
  claude_report_llm, enhanced_pipeline, data_sync. AI = claude_client, claude_multi_agent, claude_prompts,
  claude_tools, validator, sql_pattern_checker, agents/, intelligence/, optimization/. DB = connection,
  schema, executor, profiler, relationships, memory, migrations. CORE = config.

---

## ✅ STATUS — Backend move DONE & verified (2026-06-05)
Decisions locked: **Option A** (run from inside backend/, imports unchanged), god-file split = LATER phase,
frontend stays at root + backend moves down (Phase 1 merged into the move).

DONE in one phase:
- Created `backend/` + `backend/docs/`. `git mv` (history preserved as renames) of app.py, config.py,
  data_sync.py, requirements.txt, Dockerfile, space.yaml, ai/, db/ → backend/. All working .md docs → backend/docs/.
- `.env` moved to `backend/.env` (verified still gitignored). frontend/ stays at repo root.
- Fixed: (1) FRONTEND_DIR → `../frontend` from backend, env-overridable; (2) app.py __main__ uses app_dir=backend
  + sys.path so `python backend/app.py` works from root; (3) Dockerfile → build context = repo root, copies
  backend/ + frontend/, runs `uvicorn app:app --app-dir backend`.
- Imports UNCHANGED (Option A) — `import config`, `from ai.x`, `from db.x` all still resolve.

BOOT TEST PASSED: app imports from backend/, GET / =200, /report-view=200, /static/script.js=200,
/report/filters=200 (live DB), FRONTEND_DIR resolves to root frontend/ (exists). Git tracked all as renames.

ROOT now: README.md, RESTRUCTURE.md, backend/, frontend/. Clean.

## ✅ PHASE 3 — Internal re-layering DONE & verified (2026-06-05)
Scope (Joel's choice): move modules into core/ + services/ only. NOT app.py route extraction (that + god-file
split = Phase 4, same risk class). Executed in two boot-tested sub-steps:
- **3a — services/:** git mv ai/report_generator.py, ai/claude_report_llm.py, ai/enhanced_pipeline.py, and
  data_sync.py → backend/services/. Rewrote all `ai.<mod>` → `services.<mod>` references (app.py routes +
  claude_tools lazy import + report_generator's lazy claude_report_llm import). Boot test PASSED — the
  circular-dep lazy imports (claude_tools↔report_generator) still resolve.
- **3b — core/:** git mv config.py → backend/core/config.py. Rewrote the 4 `import config` sites
  (claude_client, claude_multi_agent, claude_report_llm, db/connection) → `from core import config`.
  .env still loads (cwd=backend). Boot test PASSED — DB + Anthropic keys present.
- Full end-to-end /ask (chat-intent) verified WORKING across core → services → ai → db.

backend/ layer layout now: core/ (config), services/ (report_generator, claude_report_llm,
enhanced_pipeline, data_sync), ai/ (claude_*, validator, sql_pattern_checker, agents/, intelligence/,
optimization/), db/, docs/, app.py (still has routes — see Phase 4).

## ✅ PHASE 4a — Routes extracted into api/ layer DONE & verified (2026-06-05)
app.py (497 lines, mixed routes+models+logic) → thin ~95-line assembler. Routes split via FastAPI APIRouter:
- api/schemas.py — the 4 Pydantic request models.
- api/chat.py — /chat/stream, /ask (SSE streaming preserved verbatim, lazy imports intact).
- api/reports.py — /report, /report/apply-filters, /report/modify, /report/filters.
- api/history.py — /history + /history/{turn_id}/{sql,result,answer}, delete.
- api/meta.py — /schema, /relationships.
- api/frontend.py — /, /report-view, + mount_static(app) for /static (FRONTEND_DIR via parents[2]/frontend).
- app.py now just: create app, CORS, _warm_caches thread, include_router x5, mount_static, __main__.
BOOT TEST PASSED: all 15 routes register (identical paths), /, /report-view, /static, /report/filters,
/schema, /history all 200; end-to-end /ask streaming verified WORKS through api/chat.py.

## ✅ PHASE 4b — God-file pure-function extraction DONE & verified (2026-06-05)
Scope (Joel's choice): extract the SAFE self-contained pure functions only; leave the tangled ReportPipeline
class intact (analysis flagged splitting it HIGH risk + better done with the accuracy test set later).
Extracted via re-export pattern (definitions move out, report_generator.py imports them back) so EVERY
importer (api/chat, api/reports, ai/claude_tools) keeps working UNCHANGED:
- services/intent_classifier.py (68 lines) ← classify_intent + _REPORT_KEYWORDS/_REPORT_PATTERNS.
- services/sql_fixer.py (662 lines) ← _fix_report_sql (the 630-line regex beast) + its column-set constants.
- services/filter_injector.py (179 lines) ← _inject_filters.
report_generator.py: **2967 → 2100 lines** (−867). ReportPipeline class + blueprint cache + module-level
apply_filters/modify_report wrappers untouched. Lazy LLM imports in modify() kept lazy (circular-dep intact).
BOOT TEST PASSED: app boots, / + /report/filters = 200, claude_tools lazy _fix_report_sql resolves, all 3 new
modules import, sql_fixer sanity OK, end-to-end /ask streaming WORKS (returned 2616 open orders).

## 🏁 RESTRUCTURE COMPLETE
Final backend/ layout: app.py (thin assembler ~95 lines) · api/ (routes) · core/ (config) · services/
(report_generator + intent_classifier + sql_fixer + filter_injector + claude_report_llm + enhanced_pipeline +
data_sync) · ai/ (claude_*, validator, sql_pattern_checker, agents/, intelligence/, optimization/) · db/ · docs/.
Root: README.md, RESTRUCTURE.md, backend/, frontend/. All phases boot-tested. Ready for Joel to COMMIT.
Optional future: split the ReportPipeline class itself (deferred — do alongside the accuracy test set).

---

## (historical) KEY DECISION before Phase 1 — import strategy (A vs B)

**Option A — keep `backend/` OFF the import path; run from inside backend/ with absolute imports unchanged.**
- Move all python into `backend/`, set the working dir / uvicorn to run *from* `backend/` so `import config`,
  `from ai.x` still resolve exactly as today. Entrypoint becomes `app:app` run with cwd=backend (or
  `--app-dir backend`). **Imports DON'T change at all** — lowest risk, smallest diff.
- Downside: `backend` isn't a python package; relies on cwd/app-dir.

**Option B — make `backend/` a proper package; imports become `from backend.ai.x import y`.**
- Cleaner/"correct", but rewrites EVERY import in EVERY file (incl. the 29 lazy ones) → big diff, more risk.

**Recommendation: Option A.** It gives you the clean folder layout you want with near-zero import churn and
far less breakage risk. We can always upgrade to B later. (Decision needed from Joel before Phase 1.)

> Phases below are written assuming Option A. If B is chosen, Phase 2/3 grow to "rewrite import prefixes".

---

## Target structure (proposed — adjust before we start)

```
repo-root/
├── backend/
│   ├── app.py                    # entrypoint (run with app-dir=backend)
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── .env                      # backend secrets (gitignored)
│   ├── .report_cache/            # runtime cache (gitignored)
│   ├── core/
│   │   └── config.py             # (was root config.py)
│   ├── api/                      # FastAPI routes extracted from app.py (LATER phase)
│   ├── services/                 # report_generator (split LATER), report_llm, enhanced_pipeline, data_sync
│   ├── ai/                       # claude_client, claude_multi_agent, prompts, tools, validator,
│   │   │                         #   sql_pattern_checker, agents/, intelligence/, optimization/
│   ├── db/                       # connection, schema, executor, profiler, relationships, memory, migrations/
│   └── docs/                     # ALL working .md (JoelOptimization, PLAYBOOK, CHANGELOG, plans, REPORT, etc.)
├── frontend/                     # index.html, report.html, script.js, report.js, style.css (+ later .env)
├── README.md                     # stays at root
├── .gitignore                    # stays at root
└── (no .env at root)
```
NOTE: whether `config.py` moves to `core/` and whether services/api are split are LATER phases — the FIRST
move keeps modules where they are inside `backend/` (just shifts the whole tree down one level) to minimize
risk, THEN we re-layer internally.

---

## PHASES (each: re-analyze → move → fix → boot-test → commit)

### Phase 0 — Decide & prep (no code move)
- Confirm Option A vs B. Confirm target tree. Confirm god-file split is a SEPARATE later phase (not now).
- Ensure current work is committed (clean baseline to revert to).

### Phase 1 — Frontend split (lowest risk, isolated)
- Move `frontend/` to repo-root `frontend/` (it's already named that; if it ends up beside backend, adjust
  FRONTEND_DIR). Actually: frontend stays at root; BACKEND moves down. So Phase 1 = decide frontend's final
  spot and fix the ONE thing that references it.
- Fix `app.py:468` FRONTEND_DIR to resolve to the new relative location (root `frontend/`).
- Boot test: `GET /`, `/report-view`, `/static/...` all 200.
- Re-analyze before executing: confirm the only backend→frontend coupling is FRONTEND_DIR + the 3 serve routes.

### Phase 2 — Create backend/ and move the Python tree (Option A)
- Create `backend/`. Move app.py, config.py, data_sync.py, ai/, db/, requirements.txt, Dockerfile into it.
- Move all working .md docs into `backend/docs/` (keep README at root).
- Because imports are absolute and we run with app-dir=backend, imports DON'T change.
- Fix the 3 path/entrypoint things:
  1. `app.py` uvicorn `__main__` + Dockerfile CMD → run with `--app-dir backend` (or cwd) so `app:app` resolves.
  2. `app.py:468` FRONTEND_DIR → point to `../frontend` from backend.
  3. `report_generator.py:33` `_CACHE_DIR` → verify it still lands at a sane path (backend/.report_cache).
- Move `.env` to `backend/.env`; confirm `config.py` load_dotenv still finds it (cwd-relative).
- Boot test: full app boots, `/`, `/ask` (chat-intent), `/report/filters` (DB hit) all work.
- Re-analyze before executing: re-grep all `Path(__file__)`, all lazy imports, the uvicorn string, Dockerfile.

### Phase 3 — Internal re-layering inside backend/ (optional, after Phase 2 is green)
- Introduce `core/` (config), `services/` (report_generator, claude_report_llm, enhanced_pipeline, data_sync),
  keep `ai/` + `db/`. Update imports for ONLY the moved modules (config, the services).
- ⚠ Watch the circular dep (app↔report_generator↔claude_tools) — keep lazy imports lazy.
- Boot + smoke test after each module group moved.

### Phase 4 — Split the god-file (report_generator.py, ~2967 LOC) — SEPARATE, LATER
- NOT part of the folder restructure. Decompose into focused service modules
  (intent_classifier / sql_repairer / filter_engine / kpi_engine / report_modifier) with a thin orchestrator.
- This is a refactor with real behavior risk → its own plan + its own testing. Do AFTER the move is stable.

---

## Boot-test checklist (run after every phase)
1. `.\backend\...python app.py` (or app-dir) starts with no import error.
2. `GET /` → 200 (frontend serves).
3. `GET /report/filters` → 200 (DB layer reachable).
4. `POST /ask` with a chat question → routes + answers (AI layer reachable).
5. No `Path(__file__)` resolving to a wrong/missing dir (frontend + cache).

## Rollback
Any phase that fails the boot test and can't be fixed in a few minutes → `git checkout .` / revert to the
phase's starting commit. Each phase is one commit, so rollback is clean.
