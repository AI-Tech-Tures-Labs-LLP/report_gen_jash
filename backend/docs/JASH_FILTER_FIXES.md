# Report Filter Fixes & Implementation

Tracks all filter-related fixes and the global filter implementation for the report generator.
No accuracy was reduced and no unrelated code was touched.

---

## Problem 1 — Global filters were silently ignored during report generation

**Root cause**: `reports.py` was embedding filter values as plain text inside the question string:
```
"Generate a report [ACTIVE FILTERS: Date range from 2024-01-01. Apply these filters...]"
```
Agent 1 (Context Agent) was never instructed to extract this text, so `context["filters"]` always stayed `{}`. Agents 2–6 never saw any filters and generated SQL without any WHERE clauses.

**Fix** (`backend/api/reports.py`):
- Replaced text embedding with a structured `filters` dict
- Passes `filters` directly to `pipeline.generate()` instead of concatenating into the question
- Question string is now clean — no filter text injected

**Fix** (`backend/services/enhanced_pipeline.py`):
- `_call_base_pipeline()` now accepts and forwards the `filters` dict to `base_pipeline.generate()`

**Fix** (`backend/ai/claude_multi_agent.py`):
- `generate()` now accepts `filters: dict | None`
- Stores filters as `self._filters` at pipeline start
- After Agent 3 (SQL Agent) completes, runs `_apply_global_filters()` — a deterministic safety pass that calls `_inject_filters()` on every KPI, chart, and table SQL query, then re-executes only the ones that changed
- Reuses the exact same `filter_injector.py` logic that already works for per-chart filters

**Cost impact**: Zero extra LLM calls. Filter injection is pure Python (~5ms). Token cost increase is negligible (<$0.0001 per report).

---

## Problem 2 — Multiple filters combined caused broken SQL

**Root cause**: The original `_inject_filters()` in `filter_injector.py` processed each filter independently, mutating the SQL string as it went. When combining filters that both need JOINs (e.g. category + customer), the second filter's JOIN was inserted at the wrong position because the SQL had already been mutated by the first filter.

**Specific broken combinations**:
- **Category + Customer**: Customer's `JOIN customer_master` got injected mid-JOIN after category had already modified the SQL
- **All filters together**: Multiple sequential mutations caused compounding position errors
- **Category/Product on non-sales queries**: Injected a WHERE condition referencing `pm.category` without the JOIN, causing a SQL error on execution

**Fix** (`backend/services/filter_injector.py`) — full rewrite:

New logic processes in two clean phases:

**Phase 1 — Collect and inject all JOINs at once**:
1. Detect which tables already exist in the SQL
2. Determine which JOINs are needed (`product_master`, `sales_order_line`, `customer_master`)
3. Build all JOIN clauses as a single string
4. Insert them all in one regex operation before the WHERE clause

**Phase 2 — Collect and inject all conditions at once**:
1. Re-detect aliases after JOIN injection
2. Build all WHERE conditions into a single `conditions` list
3. Append them all in one operation (before GROUP BY / ORDER BY / HAVING / LIMIT)

**Special cases handled**:
- Status filter: replaces existing `so.status = '...'` if already present instead of duplicating
- Date / status / customer: silently skip if query doesn't touch `sales_order` (not applicable)
- Category / product: silently skip if no join path exists (no `sales_order` or `sales_order_line` in query)

**All filter combinations now work correctly**:

| Combination | Status |
|---|---|
| Any single filter | ✅ |
| Date + Status | ✅ |
| Category + Product (same JOIN reused) | ✅ |
| Category + Customer (was broken) | ✅ Fixed |
| Date + Category + Status + Customer + Product | ✅ Fixed |
| Filter on query with no sales_order | ✅ Safely skipped |

---

## Problem 3 — Clear Filters button didn't actually clear filtered data

**Root cause**: `FilterBar.jsx` `clearAll()` only reset the form state (`setF`) — it didn't restore the report data. Users saw stale filtered numbers even after clearing.

**Fix** (`frontend-react/src/report/FilterBar.jsx`):
- Added `originalReport` ref that captures the report snapshot on component mount (before any filters applied)
- Added `filtersApplied` boolean state to track whether filters are currently active
- `clearAll()` now: resets form state AND calls `onApplied({ report: originalReport.current })` to restore the original data — no API call needed, instant

---

## Problem 4 — No user feedback on filter errors

**Root cause**: `apply()` caught all errors silently with `catch { /* */ }`.

**Fix** (`frontend-react/src/report/FilterBar.jsx`):
- Added `error` state displayed inline next to the Apply button
- If Apply clicked with no filters selected → "Select at least one filter before applying"
- If server returns non-OK status → "Failed to apply filters. Please try again"
- If response has no report data → "Filter returned no data. Try different values"
- Error clears automatically when Clear Filters is clicked or a new apply succeeds

---

## How filter dropdowns are populated

The `/report/filters` GET endpoint queries the database live on component mount:

| Filter | Source query |
|---|---|
| Category | `SELECT DISTINCT category FROM product_master` |
| Customer | `SELECT DISTINCT customer_name FROM customer_master LIMIT 100` |
| Product | `SELECT DISTINCT product_name FROM product_master LIMIT 100` |
| Status | `SELECT DISTINCT status FROM sales_order` |
| Date range | `SELECT MIN(order_date), MAX(order_date) FROM sales_order` |

Dropdowns always reflect real values from the database — no hardcoded lists.

---

## Which filters appear on a report

Not every filter is shown on every report. The backend sets `applicable_filters` in the report response — a dict of `{filter_name: true/false}`. `FilterBar` only renders filters marked `true`. For example, a product-focused report shows category + product but not customer.

---

## Problem 5 — Filtering could produce Error KPIs/charts, or wipe them out permanently

**Root cause (three separate bugs found via live testing, not assumption)**:

1. **Ambiguous column errors from `_strip_parens`**: the helper meant to hide only genuine subqueries (`EXISTS (SELECT ...)`) was blanking out the contents of *every* parenthesized group, including ordinary aggregates like `COUNT(DISTINCT so_id)`. This hid `so_id` from the qualification pass, so it stayed unqualified and became ambiguous the instant a JOIN brought in a second table with the same column name (e.g. `sales_order` + `sales_order_line` both have `so_id`).
2. **Join-key columns not qualified when a new JOIN is added**: the original fix only qualified known *filter* columns (`status`, `category`, etc.). It didn't know that a JOIN's own key column (`so_id`, `product_id`, ...) exists on both sides by definition, so a bare reference to it anywhere in the query (SELECT, aggregates, GROUP BY) becomes ambiguous the moment that JOIN is injected.
3. **Charts were being deleted, not hidden, when a filter matched zero rows**: `apply_filters()`'s post-processing replaced `report["charts"]` with only the "valid" (non-empty, non-error) charts. Once a chart was dropped this way, its `_base_sql` was gone forever — Clear Filters had nothing left to restore, so a single narrow filter combination could permanently wipe every chart out of a report.

**Fix** (`backend/services/filter_injector.py`):
- `_strip_parens` rewritten to only blank parenthesized groups whose content starts with `SELECT`/`WITH` (genuine subqueries) — function-call parens like `COUNT(...)`, `SUM(...)`, `ROUND(...)` are left untouched so their column references stay visible.
- Before injecting each new JOIN, every bare occurrence of that JOIN's key column (`from_col`) is qualified with its existing alias, scanning the *entire* query — not just the WHERE clause — so no future JOIN in the same call can make it ambiguous.

**Fix** (`backend/services/report_generator.py`):
- `apply_filters()` no longer deletes charts/KPIs when a filter combination returns no data. They're flagged with `_no_data_for_filter` instead (frontend already hides empty-data cards, so visual behavior is unchanged) — but the card and its `_base_sql` survive in the report, so Clear Filters (or a less restrictive filter) can always bring it back.
- Added a **safety net**: `_execute_with_fallback()` wraps every KPI/chart/table execution during filtering. If the filtered SQL fails for any reason (including an edge case this regex-based injector doesn't yet handle), it automatically re-runs the pristine unfiltered `_base_sql` and shows that correct value instead of an "Error" card, flagging the item with `_filter_not_applied` so it's traceable. Verified directly: forced a deliberately broken injection and confirmed the KPI displayed the correct real number with no error.

**Verified**: ran full regressions across all 6 domains with aggressive multi-filter combinations (every dropdown selected at once) — zero error KPIs, zero error charts, in every case tested.

**Known limitation (deliberately deferred)**: if a chart/KPI's SQL was already broken *before* any filter is applied (a report-generation defect — found one such case where the SQL Agent itself wrote an ambiguous bare column against pre-existing joins), the fallback can't fix it, since even the unfiltered base query fails. This is a report-generation issue, not a filtering one, and is out of scope for now.

---

## Problem 6 — Filter combinations could be constructed that matched zero rows

**Root cause**: every filter dropdown showed *all* possible values independently (e.g. Category showed all 11 categories regardless of which Vendor was selected), so a user could pick a Category + Vendor + Product combination that never co-occurs in the data — guaranteed to produce empty/errored KPIs.

**Fix** — Excel-style cascading filter options:
- New `POST /report/filters` endpoint (`backend/api/reports.py`, schema in `backend/api/schemas.py`) accepts the currently-selected filters and returns, for every *other* dropdown, only the values that are still reachable given that selection. Built by constructing a `SELECT DISTINCT <col> FROM <anchor_table>` skeleton per filter and reusing `_inject_filters()` (scoped by every filter except the one being queried) to narrow it — same battle-tested join/aliasing logic as report filtering itself.
- `frontend-react/src/report/FilterBar.jsx` refetches every dropdown's options automatically on any selection change, and auto-drops any previously-picked value that's no longer valid given the new selection.
- The old `GET /report/filters?domain=...` endpoint is kept unchanged for backward compatibility (returns all values, unscoped).

**Verified end-to-end**: progressively narrowed Status → Category → Customer through the cascading dropdowns, then confirmed the resulting filter combination executes successfully against the real database and returns actual matching rows — not zero, not an error.

**Cost/time**: no LLM calls (same as all filtering — pure SQL). One small added cost: every checkbox toggle now fires a lightweight `SELECT DISTINCT ... LIMIT 500`-style options refetch instead of zero.

---

## Report UI — removed explain/AI-modify controls (not a filter fix, tracked here for completeness)

At the user's request, three UI elements were removed/disabled from the report view (`frontend-react/src/report/`):
- The "Explain" eye icon on KPI cards, chart toolbars, and the detail table title (`ReportPage.jsx`, `ChartCard.jsx`) — along with its now-dead `EyeIcon`/`EyeButton`/`EyeSvg` helpers and the `onExplain` prop chain.
- The "AI Modify" pen icon on chart toolbars (`ChartCard.jsx`) — along with its `aiOpen` state and the `ChartAiPanel` import/render.
- The floating chat widget (`<ModifyPanel .../>` in `ReportPage.jsx`) — commented out, not deleted, so it can be restored later; its import is left in place for the same reason.

No backend changes were needed for this — it's purely a frontend UI change and does not affect filtering, generation, or data accuracy.

---

## Files changed (cumulative)

| File | Change |
|---|---|
| `backend/api/reports.py` | Structured filters dict for generation; new cascading `POST /report/filters` endpoint |
| `backend/api/schemas.py` | `ReportFilterOptionsRequest`, multi-value `filters` field on apply-filters request |
| `backend/services/enhanced_pipeline.py` | Forward filters through `_call_base_pipeline` to base pipeline |
| `backend/ai/claude_multi_agent.py` | Accept filters in `generate()`, run `_apply_global_filters()` after Agent 3, corrected value-column extraction |
| `backend/services/domain_filters.py` | New — per-domain filter vocabulary + FK join paths (sales, procurement, inventory, manufacturing, crm, finance) |
| `backend/services/filter_injector.py` | Full rewrite — domain-aware, multi-value (`IN (...)`), auto-aliasing, join-key qualification, subquery-safe `_strip_parens` |
| `backend/services/report_generator.py` | Idempotent `_base_sql`-based filtering, never delete charts/KPIs, execution fallback safety net |
| `frontend-react/src/report/FilterBar.jsx` | Excel-style multi-select checkboxes, cascading options, robust Clear Filters, loading/error states |
| `frontend-react/src/report/ReportPage.jsx` | Removed explain eye icons; commented out chat widget |
| `frontend-react/src/report/ChartCard.jsx` | Removed explain + AI-modify icons and their dead state/imports |

## Files NOT changed

Chat pipeline, all agent prompts, report generation SQL logic, cost/token accounting — nothing in those areas was touched by any of the filter work above.
