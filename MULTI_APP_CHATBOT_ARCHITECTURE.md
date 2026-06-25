# Multi-App Chatbot Architecture — Master Design Doc

**Author context:** Joel (tech lead). The central **Report+Chat** project (governed text-to-SQL
over the full StackHunter jewelry-ERP Postgres DB) is built, accuracy-tested (19/19 on the
expanded golden set), and serves as the **proven reference implementation**. This doc is the
blueprint for replicating that chatbot across the other applications with **scoped, per-app
data access**.

> **One-line thesis:** The central chatbot is the proof of concept. Each app's chatbot is the
> SAME engine, narrowed to that app's tables + metrics, connecting through a DB role that can
> physically only read what that app is allowed to see.

---

## 0. The applications (subsets of the central system)

| App | Platform | Audience | Role in the business |
|---|---|---|---|
| **Hunter app** | React Native | Sales reps ("hunters") | Field CRM — leads, visits, their own orders |
| **Sales Manager** | Web | Sales managers | Sales performance, customers, territories |
| **Procurement Admin** | Web | Procurement team | Purchase orders, vendors, raw materials |
| **Inventory Admin** | Web | Inventory team | Finished goods, stock movements, fulfillment |
| **Central (Report+Chat)** | Web | (this project) | FULL DB access — the reference build |

Each of the first four needs its own chatbot — a **narrowed clone** of the central one.

---

## 1. Core principle — build the engine ONCE, configure per app

The hard part is already solved in the central project: **governed text-to-SQL** =
intent router (Haiku) → SQL agent w/ tool loop (Sonnet) → interpreter, wrapped in deterministic
guards (SELECT-only validator, fan-out double-counting gate, canonical metric/semantic layer).

**Do NOT rebuild this per app.** Each app's chatbot is the same engine with two things swapped:
1. A **smaller schema** (only its allowed tables) injected into the prompt.
2. A **smaller semantic layer** (only its relevant metrics).

Plus it **connects to Postgres as a restricted role** (see §2).

---

## 2. Data access scoping — ENFORCE AT THE DB, not just the prompt

> ⚠️ **The schema you feed the LLM is NOT a security boundary — it is guidance.** Real access
> control must be enforced by Postgres itself. Use BOTH layers below (defense in depth).

### Layer A — Dedicated read-only Postgres role per app (the REAL enforcement)

Create one role per app that can only `SELECT` its allowed tables. The app's chat backend
connects with that role's credentials. If the LLM hallucinates or is prompt-injected into
querying a forbidden table, **Postgres refuses it** — the role physically cannot read it.

```sql
-- Template: one per app. Replace <app> and the table list.
CREATE ROLE <app>_app_ro LOGIN PASSWORD '<strong-secret>';
GRANT CONNECT ON DATABASE stackhunter TO <app>_app_ro;
GRANT USAGE ON SCHEMA public TO <app>_app_ro;

-- Grant ONLY this app's allowed tables (everything else is implicitly denied):
GRANT SELECT ON table_a, table_b, table_c TO <app>_app_ro;

-- Optional hardening: make sure future tables aren't auto-granted
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM <app>_app_ro;
```

**Why this is non-negotiable for multi-app:** the central project uses ONE "see-everything"
connection. The moment data is scoped per app, a hallucinated/injected query on the shared
connection could touch any table. A per-app read-only role makes "the hunter app cannot read
vendor costs" *true*, not just *intended*.

### Layer B — Schema subset fed to the LLM (guidance + token savings)

Each app's chat config exposes only its allowed tables in the system prompt. Benefits:
- The model can't even *attempt* tables it doesn't know exist.
- Smaller prompt = fewer tokens = cheaper + faster.

**Both layers together:** the LLM is *told* a small world (Layer B) and the DB *enforces* that
small world (Layer A).

### Row-level access (harder — only where needed)
Some apps need a table but only SOME rows (e.g. Hunter app sees `sales_order` but only THAT
hunter's orders). That is **Postgres Row-Level Security (RLS)**, not table grants:

```sql
ALTER TABLE sales_order ENABLE ROW LEVEL SECURITY;
CREATE POLICY hunter_own_orders ON sales_order
  FOR SELECT TO hunter_app_ro
  USING (hunter_id = current_setting('app.current_hunter_id')::int);
```
The app sets `SET app.current_hunter_id = '<id>'` on the connection per request. Then even a
broad `SELECT * FROM sales_order` returns only that hunter's rows. **Identify every
"this table but only their rows" case up front** (see §5 Q2) — these are the risky ones.

---

## 3. Table access per app — FRAMEWORK + starting hypothesis

> ⚠️ The grid below is an **educated hypothesis from the schema map**, NOT authoritative.
> The data engineers own the real answer (see §5). Use this as the strawman to react to.

| App | Likely READ access | Should NOT see |
|---|---|---|
| **Hunter** | hunters, party_master, party_assignment, party_stage_history, visit_log, sales_order*, territories, customer_master | vendor costs, PO pricing, raw materials, OTHER hunters' data |
| **Sales Manager** | sales_order, sales_order_line(+_pricing/_gold/_diamond), customer_master, hunters, managers, territories, sales_invoices(+lines), sales_payments, discount_exceptions, discount_rules | procurement internals, raw material lots, job cards |
| **Procurement Admin** | purchase_order, po_line_items(+_gold/_diamond/_pricing), vendor_master, raw_material_lots, raw_material_* (bags/inventory/ledger/balance), sales_allocation, purchase_invoices(+lines), purchase_payments | customer-facing sales pricing, hunter CRM, end-customer PII |
| **Inventory Admin** | finished_goods_inventory, inventory_movements, product_master, product_variant, product_stone_detail, job_card(+_gold_details/_diamond_lines/_igi_numbers), so_fulfillment_log | financials, customer/vendor pricing, CRM |
| **Central** | EVERYTHING (already built) | — |

`*` = likely needs ROW-LEVEL filtering (only their own rows), not full table access.

**The central DB context (verified):** 58 tables, 63 declared foreign keys. Hub tables:
sales_order (18.5k) / sales_order_line (38k) / purchase_order (10.4k) / po_line_items (35.7k) /
job_card (218k) / finished_goods_inventory (35k). All money lives 1:1 in
`sales_order_line_pricing`. The sale↔purchase bridge is ONLY via `sales_allocation`
(sol_id → pol_id). Diamond/job-card child tables fan out 2.4–37× — the reason the fan-out
gate exists.

---

## 4. Reusable chat-layer architecture

### Option A (RECOMMENDED): Shared `chat-core` library + per-app config

```
chat-core/  (shared package — the proven engine, extracted)
  ├─ intent router (Haiku)            ← reused as-is (incl. complexity rating + follow-up context)
  ├─ SQL agent + tool loop (Sonnet)   ← reused as-is
  ├─ interpreter                      ← reused as-is
  ├─ validator + fan-out guards       ← reused as-is
  ├─ metric/semantic library          ← reused, FILTERED per app
  └─ accepts a CONFIG object:
        {
          app_name,
          db_role_creds,        // connect as THIS app's restricted role
          allowed_tables,       // schema subset injected into the prompt
          semantic_metrics,     // subset of the canonical metric library
          row_level_key?,       // e.g. 'hunter_id' — sets the session var for RLS
          user_pool,            // which user base for auth + chat memory
        }

hunter-app/      → imports chat-core, passes hunter config
salesmgr-app/    → imports chat-core, passes salesmgr config
procurement-app/ → imports chat-core, passes procurement config
inventory-app/   → imports chat-core, passes inventory config
```

**Each app's chatbot = `chat-core` + a config file.** Write the engine ONCE, configure per app.
Improve the engine once (e.g. streaming) → every app benefits. Strongly preferred.

### Option B: Replicate + trim (only if apps can't share a package)
Copy the chat backend into each app, delete unneeded tables/metrics. Simpler to start, but you
maintain 4 copies — every bug fix applied 4×. Avoid unless forced by separate teams/repos.

### Per-app backend shape (identical to the proven central `/ask`)
```
POST /ask   (auth required, scoped to that app's users)
  1. intent router      → uses the app's SMALLER schema; rates complexity; resolves follow-ups
  2. SQL agent          → connects as the app's DB ROLE; sees only the app's tables;
                          sets row-level session var if applicable
  3. interpreter        → rows → natural-language answer
  4. SSE stream back
```
Only differences from central: **DB role**, **schema subset**, **metric subset**,
(optional) **row-level session var**. Everything else carries over unchanged:
fan-out gate, validator, login-only, Mongo-per-user chat memory.

### Auth + memory per app
Each app has its own users (hunters, sales managers, etc.). Reuse the central pattern:
- **Login-only** chat (auth token required; resolve user at the endpoint, before streaming).
- **Chat memory in Mongo, per user**, keyed by (user_id, conversation_id). Sliding window of
  recent turns (~5) replayed for follow-ups; store the query_result rows so follow-ups can
  reference prior numbers. NEVER store chat history in the business Postgres.

---

## 5. EXACTLY what to ask the data engineers (unblock list)

Bring them this. These answers unblock the whole effort.

**Q1 — Table ownership / sensitivity map.**
"For each table, which of our 5 apps should READ it? Are any tables sensitive enough that even
read access is restricted?" → Get back a **table × app grid**.

**Q2 — Row-level vs table-level (CRITICAL).**
"For each app, are there tables where it should see only SOME rows, not all? e.g. Hunter app
only its own sales_order; Sales Manager only their territory?" → Every YES = needs RLS + a
session variable. Get the FULL list up front.

**Q3 — The identity/filter key.**
"When an app sees 'only their own' data, what COLUMN identifies ownership? (hunter_id /
territory_id / manager_id?) Is it reliably populated on every relevant table?"
→ This is what RLS policies filter on. NOTE: some columns in this DB are all-zero
(outstanding_balance, sales_invoices.discount_amount) — confirm ownership keys are actually
populated before relying on them.

**Q4 — DB role strategy.**
"Can we create a dedicated read-only Postgres role per app with table-limited GRANTs? Or is
there an existing scheme (per-app views, separate schemas) we should use instead?"
→ Don't reinvent if they have a pattern.

**Q5 — Sensitive columns within allowed tables.**
"Within a table an app CAN see, are there COLUMNS it shouldn't? (customer PII, vendor cost
margins, etc.)" → Handle via column-level grants or app-specific views.

---

## 6. Recommended sequencing

1. **Get the table×app matrix + the row-level list from data engineers** (§5). BLOCKED on this —
   do not guess at security boundaries.
2. **Create per-app read-only DB roles** (table-level GRANTs first; add RLS only where they
   flagged row-level needs).
3. **Extract the chat engine into `chat-core`** and prove it with ONE new app first
   (suggest the simplest — likely Inventory Admin) using a scoped role + trimmed schema.
4. **Roll out to the other apps** by config.

**Non-negotiable:** once multi-app with scoped data, the per-app DB role is required. Prompt-level
schema scoping alone is NOT security — a hallucinated/injected query would still hit the shared
connection. The DB role makes the boundary real.

---

## 7. What carries over from the proven central build (don't re-solve)

- Intent router with complexity rating (Haiku for simple, Sonnet for complex) + follow-up context.
- SQL agent tool loop with self-repair (validate → execute → fix errors).
- **Fan-out double-counting hard gate** (the #1 accuracy defense — blocks summing parent amounts
  across 2.4–37× child joins).
- Canonical metric/semantic layer (maps business concept → blessed SQL; prevents wrong-column /
  purchased-vs-consumed / inventory-total_amount traps). Filter it per app.
- SELECT-only validator (read-only safety).
- Currency = INR (₹), never $.
- Login-only + Mongo-per-user chat memory; business Postgres holds NO chat history.
- Accuracy methodology: a DB-verified golden-set battery that doubles as a regression suite
  (the central project scored 19/19). Build a per-app golden set the same way.

---

## 8. Reference files — the PROVEN chat engine (in the central Report+Chat project)

> Whoever builds a new app's chatbot should READ these files from the central project first —
> they are the working, accuracy-tested reference. Paths are relative to the central project's
> `backend/`. This is the code to extract into `chat-core` (§4 Option A).

### The chat pipeline (the core flow — read these first, in order)
| File | What it does | Reuse as |
|---|---|---|
| `api/chat.py` | The `/ask` endpoint: login gate → intent router → report/chat/conversational branch → SSE stream. The orchestration glue. | Per-app endpoint (swap DB role, schema subset, user pool) |
| `services/claude_report_llm.py` | The brain: `classify_query_intent()` (router + complexity + follow-up context), `answer_chat_question()` (SQL agent → interpreter pipeline), `answer_conversational()`. ALL the prompts (`_INTENT_SYSTEM`, `_CHAT_INTERPRET_SYSTEM`, currency rule). | Core engine — reuse; filter the schema/metrics it injects |
| `ai/claude_client.py` | `ClaudeClient.call_agent()` — the Anthropic wrapper: tool loop, retries, prompt caching, `on_tool_result` capture hook, token telemetry. | Reuse as-is |

### Tools the SQL agent calls (the "skills")
| File | What it does | Reuse as |
|---|---|---|
| `ai/claude_tools.py` | Tool defs + handlers: `execute_sql_query` (autofix → **fan-out hard gate** → run), `validate_sql_query`, `get_metric_sql`. The fan-out double-counting gate (`_detect_line_child_fanout`, `_FANOUT_REGISTRY`) lives here — the #1 accuracy defense. | Reuse; the fan-out registry is DB-specific but carries over since all apps share the DB |
| `ai/validator.py` | SELECT-only safety check (`validate_sql`), schema-existence check (`check_sql_against_schema`). | Reuse as-is |
| `ai/metric_library.py` | Canonical semantic layer: business concept → blessed SQL (`METRIC_LIBRARY`, `get_metric_sql`, alias routing). | Reuse; FILTER to each app's relevant metrics |
| `ai/sql_pattern_checker.py` | Soft anti-pattern warnings (fan-out heuristics, etc.). | Reuse as-is |

### Schema / DB context fed to the LLM
| File | What it does | Reuse as |
|---|---|---|
| `ai/claude_prompts.py` | `get_sql_agent_system()` — builds the SQL-agent system prompt (schema + relationships + profile + the CANONICAL METRIC DICTIONARY + business rules). Large file; the metric dictionary is near the bottom. | Reuse; inject the SCHEMA SUBSET per app |
| `db/schema.py` | `format_schema()` / `get_schema()` — reads table+column structure. | Reuse; FILTER to the app's allowed tables |
| `db/relationships.py` | `format_relationships()` — the FK/join map. | Reuse; filter to allowed tables |
| `db/profiler.py` | `get_data_profile()` — row counts, value ranges, business rules. | Reuse; filter to allowed tables |
| `db/executor.py` | `execute_sql()` — runs the SELECT (this is where you swap in the per-app DB ROLE connection). | Reuse; point at the app's restricted role |
| `db/connection.py` | The SQLAlchemy engine/connection. | **Per-app: connect as the app's read-only role**, set RLS session var here |

### Auth + memory (login-only, Mongo-per-user)
| File | What it does | Reuse as |
|---|---|---|
| `api/auth.py` | JWT auth: `get_current_user()` dependency, login/register. | Reuse; point at the app's user pool |
| `db/user_data.py` | Mongo per-user chat memory: `add_turn()`, `get_recent_turns()`, `get_full_turns()` (+ conversation/report persistence). Chat history lives HERE, NOT in business Postgres. | Reuse as-is |
| `db/mongo.py` | Mongo connection (`get_db()`). | Reuse as-is |
| `api/schemas.py` | `QuestionRequest` (question + conversation_id) request model. | Reuse as-is |
| `core/config.py` | Env config: API keys, model IDs (`CLAUDE_HAIKU_MODEL`, etc.), DB/Mongo URIs. | Reuse; add per-app DB role creds |

### DEPRECATED — do NOT copy
| File | Status |
|---|---|
| `db/memory.py` | DEPRECATED legacy Postgres chat history. Replaced by `db/user_data.py` (Mongo). Do not use in new apps. |

### Accuracy methodology to replicate
| File | What it is |
|---|---|
| `docs/CHAT_ACCURACY_TEST.md` | The DB-verified golden-set battery (central scored 19/19) — doubles as a regression suite. Build a per-app version the same way: list questions, compute ground truth from the DB, run, grade, keep as regression. |

### The minimal extraction (if building `chat-core`)
The smallest set that IS the engine: `claude_client.py` + `claude_report_llm.py` (logic+prompts)
+ `claude_tools.py` + `validator.py` + `metric_library.py` + `claude_prompts.py` + the `db/`
schema/executor/connection files. Wrap them to accept the §4 CONFIG object (db role, allowed
tables, metric subset, row-level key). `api/chat.py` becomes the thin per-app endpoint on top.

---

## 9. Known caveats to carry forward
- **Latency:** ~8–23s per answer (3 sequential LLM calls + occasional self-repair rounds).
  Safe levers (not yet applied): move the INTERPRETER step to Haiku; STREAM the answer token-by-token
  for perceived speed.
- **Soft vs hard guards:** the metric library is ADVISORY (the agent can self-derive and skip it).
  Only the fan-out gate is a hard block. If per-app testing shows the model substituting a wrong
  source, promote that rule to a hard gate (as was done for fan-out).
- **Multi-tenancy:** the central app is single-tenant (one company). If any new app serves
  MULTIPLE customer companies sharing data, row-level isolation becomes the #1 blocker and must
  be enforced via RLS — not the prompt.
```
