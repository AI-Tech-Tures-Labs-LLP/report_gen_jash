# Accuracy Testing — Query Forensics & Universal Fix Plan

> **Purpose.** A living record of real pipeline runs, diagnosed forensically, so that
> recurring failure patterns can be separated from one-off incidents and turned into
> **universal fixes** (prompt rules + code guards + a golden test set).
>
> **Method.** For each query Joel feeds in: (1) record question + trace, (2) diagnose every
> failure with root cause, (3) verify ground truth against the LIVE DB (creds in
> `backend/.env`, query via the root `venv`), (4) tag each failure by TYPE. After enough
> cases, synthesize the cross-cutting fixes in §Patterns and §Universal Fix Plan.
>
> **Dual purpose (Joel's choice):** every entry is BOTH a diagnosed case AND a golden
> test case (question → known-correct answer → expected behavior) for regression testing.
>
> **Entry depth (Joel's choice):** FULL FORENSIC — every failure, root cause, doc citation,
> and the "schema-valid but wrong" analysis.
>
> **Ground truth (Joel's choice):** I verify against the live DB myself, no need to involve Joel.
>
> **✅ DB UPDATE (2026-06-06, later).** Now connected to the MASTER DB: `V2_inventory_management`
> (was `backup_v2_inventory`). **54 tables (was 44).** Re-verified key facts:
> - **CASE 001 PARTIALLY FLIPS:** `discount_exceptions` (120 rows) and `discount_rules` (5 rows) NOW
>   EXIST. So "discount" IS answerable on the master DB — via the GOVERNANCE layer, NOT invoice
>   discount. BUT `sales_invoices.discount_amount` is STILL all-zero (16,941 rows, 0 non-zero), and
>   `sales_order_line.discount_id` is barely populated (105 of 38,363). So the correct discount
>   source = `discount_exceptions` (requested/allowed/approved_discount_pct). The CASE 001 *failure*
>   (silent discount→margin substitution + hardcoded KPIs) STILL STANDS — the model should now route
>   to discount_exceptions, and the bug is it didn't even try the governance tables.
> - **CASE 002 STILL FULLY VALID on master DB:** the leftover-value bug reproduces exactly. Reported
>   `SUM(total_amount)` qty>0 = ₹105,221,906.66 (identical); TRUE leftover value
>   `SUM(quantity_available*unit_cost)` = **₹79,045,615.53** (was ₹78.7M — tiny data diff, same ~34%
>   overstatement). The semantic bug is DB-independent. ✅
> - **Staleness unchanged:** sales_order still ends 2026-03-05; "last few weeks" still has no data.
>
> **Implication for method:** diagnoses were durable as predicted. Only CASE 001's data-availability
> fact flipped (discount now answerable via governance). All golden values below now re-verified
> against the MASTER DB and are no longer provisional unless tagged.

---

## How to read the FAILURE TYPE tags

| Tag | Meaning | Fixable by prompt alone? |
|---|---|---|
| `METRIC-SUBSTITUTION` | Answered a different metric than asked (e.g. discount → margin) | Mostly (prompt), but needs a code contract to be reliable |
| `HARDCODED-KPI` | A KPI's executed SQL is `SELECT <constant>` — no `FROM`, no data lineage | **No — code guard required** |
| `MASKED-MATH` | Broken internal math silently "fixed" (e.g. rescaled to 100%) instead of failing loud | **No — code/logic fix** |
| `SILENT-ASSUMPTION` | Undisclosed assumption (time window shifted, proxy substituted) presented as fact | Partly prompt, partly UI surfacing |
| `DATA-UNAVAILABLE` | The data needed to answer simply does not exist in the DB; system improvised instead of saying so | Needs prompt rule + ideally a pre-flight data check |
| `DOC-VS-REALITY` | The familiarization doc references data/tables NOT actually loaded in the live DB | Process/doc fix — and a reason to ground-truth everything |
| `FANOUT` | Row-multiplying join inflates a SUM/AVG (one order → many lines) | Prompt steers; code guard (currently warn-only) should block |
| `STALE-CONTEXT` | Schema/profile cache served stale info | Code (cache TTL / invalidation) |

---

## Ground-truth facts established about the LIVE DB (reused across cases)

**`[PROVISIONAL — current DB, NOT master]`** — re-verify all of the below once master creds arrive.
Verified directly against the current (non-master) AWS RDS on 2026-06-06 via `venv` + psycopg2:

- **44 tables** in `public`.
- **Discount data is effectively NON-EXISTENT for analysis:**
  - The ONLY discount column in the entire DB is `sales_invoices.discount_amount`.
  - It is **all zero**: 18,500 rows, **0** non-zero, max `0.0000`.
  - **`discount_exceptions`, `discount_rules` tables DO NOT EXIST** in the live DB
    (the familiarization doc lists them under the governance layer, but they were
    never loaded). → `DOC-VS-REALITY`.
  - No price-derived discount either: in `sales_order_line_pricing`,
    `selling_price_per_unit < base_price_per_unit` in **0 of 38,363** lines.
  - **Conclusion: NO query can truthfully answer a "discount rate" question on this DB.**
    The only correct response is "discount data is unavailable."
- `sales_order` date range: needs re-confirm (query was cut short); the doc states
  `2024-01-13 → 2026-03-05`. Data ends ~2026-03-05, so "last few weeks" relative to
  today (2026-06-06) has NO data.
- `sales_order_line_pricing.margin_pct` avg = **35.03%** (matches doc's 35.03% — so the
  margin numbers the model fell back on are at least real).
- Pricing columns: `sol_id, variant_sku, product_id, quantity, gold_amount_per_unit,
  diamond_amount_per_unit, making_charges_per_unit, base_price_per_unit, margin_pct,
  final_amount, line_total, selling_price_per_unit, updated_at`.

---

## CASE 001 — "Has the average discount rate been surging in the last few weeks?"

**Date diagnosed:** 2026-06-06
**Mode chosen by pipeline:** DRIFT_INVESTIGATION (SIG-007 Discount Surge)
**Pipeline result:** 6 KPIs, 1 chart, 8 insights, QA 8/12 CONDITIONAL, 459s.
**Final report title delivered:** "Discount Surge — Global Average **Margin** Compression"

### Verdict: WRONG (and partly un-answerable)

The honest top-line: **this question cannot be answered on this database at all** — there
is no usable discount data (see ground-truth facts above). The only correct output was
"discount data is unavailable." Instead the pipeline silently pivoted to *margin* and
shipped a clean-looking report about a different metric, partly built on hardcoded numbers.

### Failures (each tagged + root cause)

1. **`DATA-UNAVAILABLE` + `METRIC-SUBSTITUTION` (most severe).**
   User asked **discount**; report answers **margin**. Trace shows the pivot live: rounds
   1–7 query discount (`current_avg_discount_pct`, `lines_with_discount`); round 5 hits
   all-zeros (the real data-quality wall); round 8 onward silently switches to `margin_pct`
   and never returns to discount. **Root cause:** model hit the zero-discount landmine and
   improvised a proxy instead of failing. Nothing in the pipeline forces it to answer the
   asked metric or stop.

2. **`DOC-VS-REALITY`.** My first-pass fix (last turn) was "route to `discount_exceptions`."
   Ground-truth check proved **that table does not exist in the live DB.** The doc is partly
   aspirational. Lesson: every fix must be verified against the DB, not the doc.

3. **`HARDCODED-KPI` (hallucination surface).** 4 of 7 KPIs have no data lineage —
   their executed SQL is a literal:
   - `KPI 3 Margin Variance = -0.09` → `SELECT -0.09`
   - `KPI 5 Top 3 Hunters Concentration = 42.65` → `SELECT 42.65`
   - `KPI 6 Order Count = 324` → `SELECT 324`
   - `KPI 7 AOV Increase = 148.45` → `SELECT 148.45`
   These look data-backed (they have "SQL" beside them) but the DB was never asked.

4. **`MASKED-MATH`.** Agent 4 logged: *"[hunter] contributions summed to 4.9% (outside
   80-120%) — rescaled to 100%."* A broken decomposition (summed to 4.9%, should be ~100%)
   was multiplied ~20× to look clean, then KPI 5 reported "42.65" as fact.

5. **`SILENT-ASSUMPTION`.** "Last few weeks" → silently became `2026-02-18 to 2026-03-05`
   (≈3 months stale) because data ends ~2026-03-05. Defensible, but never disclosed.

### What worked (for fairness)
- Caching excellent (1.9M cached tokens).
- Repair loop works: round 20 threw a real Postgres syntax error, round 21 recovered.
- Partial honesty: QA only CONDITIONAL 8/12; title admits "margin"; Agent 4 *logged* the
  rescale. The signals exist — they're just buried in logs and the report still ships.

### Schema-valid-but-wrong note
EVERY query here was schema-valid and ran without error (bar one self-healed syntax slip).
The schema did its job perfectly. None of the 5 failures is a schema problem — all are
semantic / data-quality / pipeline-logic problems. This is the core thesis of this doc.

### Golden test entry
- **Question:** "Has the average discount rate been surging in the last few weeks?"
- **Known-correct answer:** "Discount data is unavailable in this database
  (`sales_invoices.discount_amount` is all zero; no `discount_exceptions`/`discount_rules`
  tables; no price-derived discount). Cannot compute a discount rate." Optionally: offer
  margin as a *clearly-labeled* alternative, with the time-window caveat.
- **Expected behavior:** detect unavailability → say so → do NOT silently substitute margin
  → no hardcoded KPIs → disclose the stale window.
- **Pass criteria:** report does not claim a discount number; no KPI with `SELECT <const>`;
  any proxy is labeled; staleness disclosed.

---

## CASE 002 — "leftover inventory where raw material was provided"

**Date diagnosed:** 2026-06-06
**Mode chosen:** STANDARD_REPORT (inventory / overview)
**Pipeline result:** 6 KPIs, 6 charts, 8 insights. **QA score: 12/12 APPROVED.** 203s.
**Filter used:** `material_mode = 'RM_PROVIDED'` on `finished_goods_inventory`.

### Verdict: MOSTLY RIGHT, but contains ONE real value bug that QA missed + a definitional ambiguity

This is a much better run than CASE 001 — **no hardcoded KPIs** (every value traces to a real
query), good self-recovery from 2 SQL errors, sensible filter. BUT "12/12 APPROVED" is misleading:
there is a genuine, quantified accuracy error in the headline value KPI, and QA had no way to catch
it because it only checks narrative/structure, never re-derives the numbers.

### Ground-truth verification (live DB, 2026-06-06) `[PROVISIONAL — current DB]`
- `finished_goods_inventory`: 35,763 rows. `status`: exhausted 35,189 / active 574.
  `material_mode`: RM_PROVIDED 9,397 / FULL_VENDOR 26,366. ✅ filter value is real.
- `total_amount` = `quantity_received * unit_cost` — confirmed (211499.64 = 32×6609.36). It is the
  **ORIGINAL RECEIPT value, NOT the leftover/on-hand value.**

### Failures

1. **`SEMANTIC-WRONG-COLUMN` (real bug, headline KPI).** KPI 2 "Leftover Inventory Value =
   ₹105,221,906.66" is `SUM(total_amount)` over rows with `quantity_available>0`. But `total_amount`
   is the value of the **full original receipt** (qty_received × unit_cost), not the value of what's
   **left over**. The true leftover value = `SUM(quantity_available × unit_cost)` = **₹78,682,824.31**.
   → **The reported leftover value is overstated by ~34% (₹105.2M vs true ₹78.7M).** Every row where
   some units were consumed but not all (qty_available < qty_received) counts its *entire* received
   value as "leftover." Schema-valid, ran clean, QA 12/12 — and wrong by ₹26.5M.

2. **`AMBIGUOUS-INTENT` (defensible but undisclosed).** "Leftover" was interpreted as
   `quantity_available > 0` (units still on hand). Reasonable. But note KPI 1 (`SUM(quantity_available)`
   = 2,140) happens to be identical whether or not you filter qty>0 (exhausted rows contribute 0
   anyway) — so KPI1 is correct by luck of the metric, not by correct filtering. The model never
   stated its "leftover = qty_available>0" definition to the user. A user might mean "RM_PROVIDED FG
   that never fully sold" or "aged stock." Undisclosed assumption.

3. **`INCONSISTENT-FILTER` (minor).** KPI 4 "Avg Days in Stock = 572.5" DID correctly filter to
   qty>0 (verified: 572.5 for qty>0 vs 380.5 for all RM_PROVIDED). So KPI4 is right. But this means
   the report mixes filters across KPIs without a single consistent "leftover" definition applied
   uniformly — KPI2 effectively double-counts consumed value while KPI4 correctly scopes to on-hand.
   No single enforced definition of the subject set.

### What worked (genuinely)
- **No hardcoded KPIs** — contrast CASE 001. Every KPI/chart traces to a real `FROM` query.
- **Self-recovery:** Round 1 `EXTRACT(DAY FROM date-date)` type error → Round 2 fixed to
   `(CURRENT_DATE - received_date)`. Round 2 `GROUP BY label` alias error → Round 3 CTE fix. Good.
- KPI 1 (2,140 units), KPI 3 (124 SKUs), KPI 4 (572.5 days), KPI 5 (avg unit cost) all verified
   **correct** against live DB.
- Filter `material_mode='RM_PROVIDED'` correctly maps the phrase "raw material was provided." ✅

### Schema-valid-but-wrong note
Same thesis as CASE 001: KPI 2 is 100% schema-valid SQL, executed with no error, QA-approved 12/12 —
and overstates the headline number by 34%. The error is **semantic** (wrong column for the concept
"leftover value"), invisible to schema validation, syntax checks, AND the QA agent (which doesn't
re-derive numbers). **This is the scariest class: a clean, confident, approved, WRONG number.**

### Golden test entry `[PROVISIONAL — current DB]`
- **Question:** "leftover inventory where raw material was provided"
- **Known-correct (current DB):** Leftover units = **2,140**; SKUs with leftovers = **124**;
   **Leftover VALUE = ₹78,682,824 (qty_available × unit_cost)**, NOT ₹105.2M; avg days in stock
   (on-hand) = 572.5. Subject set = `finished_goods_inventory WHERE material_mode='RM_PROVIDED' AND
   quantity_available > 0`.
- **Expected behavior:** value of leftover stock must use on-hand quantity × unit cost, never the
   original receipt `total_amount`; "leftover" definition stated to user; one consistent filter
   applied across all KPIs.
- **Pass criteria:** leftover value ≈ ₹78.7M (not ₹105M); definition disclosed; consistent scoping.

---

## CASE 003 — "Overall sales performance: total revenue, orders, top products"

**Date diagnosed:** 2026-06-08 (master DB `V2_inventory_management`)
**Mode:** STANDARD_REPORT. **QA: 9/12 CONDITIONAL→APPROVED.** 149s.
**Window chosen by Context Agent:** "2026 YTD: 2026-01-01 to 2026-06-08."

### Verdict: NUMBERS CORRECT, but SCOPE IS WRONG & SILENT (the fan-out bug did NOT fire — important)

The headline I expected (revenue double-counting via fan-out) **did NOT happen.** All six KPIs
verified **exactly correct** against the live DB. But the report has a different, quietly serious
problem: it silently scoped to a window that captures only a fraction of the data and is partly in
the future, and never told the user.

### Ground-truth verification (master DB, 2026-06-08)
- **KPI1 Total Revenue = ₹1,503,052,703.92** ✅ EXACT. Computed as `SUM(sales_order.total_amount)`
  on the header. **Verified safe because** `sales_order.total_amount` == sum of its line pricing
  (confirmed: header 36,149.37 == Σ line_total 36,149.37), AND `sales_order_line_pricing` is strict
  1:1 with `sales_order_line` (38,363 == 38,363, zero sol_id with >1 pricing row). So no fan-out
  exists on THIS path. The model also cross-checked: line-level `SUM(solp.line_total)` = identical
  ₹1,503,052,703.92. ✅
- **KPI2 Total Orders = 1,692** ✅ EXACT.
- **KPI4 Total Units = 19,734** ✅ EXACT (no fan-out — quantity lives on the line, summed once).
- **KPI5 Top Product Revenue = ₹17,098,606.89** ✅ EXACT.
- All traced to real `FROM` queries. **No hardcoded KPIs.** ✅

### Failures

1. **`SILENT-ASSUMPTION` / `WRONG-SCOPE` (systemic — 3rd case in a row).** Context Agent invented
   "2026 YTD (Jan 1 – Jun 8)" with no basis in the question ("Overall sales performance" implies ALL
   history, not YTD). Ground truth: that window contains **1,692 of 16,941** closed orders
   (**only 10%**), and the data in it actually ENDS 2026-02-28 — so "Jan 1–Jun 8" is ~3 months of
   empty future. **The report says "Overall sales performance = ₹1.50 billion" when true overall
   closed revenue is ₹11.27 billion (~7.5× larger).** The number is internally correct but answers a
   silently narrowed question. User asked "overall," got "first 2 months of 2026" with no disclosure.

2. **`UNIT-MISLABEL` in narrative (real, embarrassing).** KPI value is `1,503,052,703.92` = **₹150
   crore** (₹1.50 **billion**), but the written summary says **"₹1.50Cr"** (₹1.5 crore) — off by 100×
   in the prose, while a later sentence says "₹1.5 billion." The report contradicts itself on the
   headline number. Narrative number-formatting is unreliable.

3. **`PHANTOM-SIGNAL` (new pattern, from the post-run signal_detector).** The graph signal logger
   fired "CRITICAL drop -96.5% at 2026-08", "spike at 2026-07", etc. These weeks are AFTER the data
   ends (2026-02-28) — the "drops" are just empty future weeks in the YTD window being read as
   catastrophic revenue collapse. Garbage signals manufactured from the bad window. (Currently inert
   — fails to persist because `signal_detection_logs`/`graph_sql_mappings`/`audit_trail` tables don't
   exist — see Infra note — but the logic ran and would surface if those tables existed.)

4. **`MISSING-SQL-TRACE` (transparency gap).** Charts 2–6 show `SQL: (none)` in traceability even
   though they have real row data (the SQL was issued in Round 2 but not threaded into the chart
   objects). So 5 of 6 charts have data with no recorded lineage — can't audit them later.

### What worked (genuinely — and notably)
- **Fan-out correctly avoided.** This is the headline good-news: revenue is right because the model
  used the header total (legit here) AND independently the line-level sum matched. The
  `sql_pattern_checker` fan-out fear did not materialize on this schema/path.
- All KPI numbers EXACT vs DB. No hardcoded constants.
- Self-cross-checked revenue two ways.

### Infra note (separate from accuracy — worth a cheap fix)
Post-run, the logging agent threw ~15 errors: `signal_detection_logs`, `graph_sql_mappings`,
`audit_trail` tables DO NOT EXIST in master DB (migrations never run). All non-fatal (caught), but
noisy and means telemetry/audit is silently not persisting. Also CASE-prior `system_cache` create
race. → low-risk infra cleanup: run migrations or guard these inserts.

### Golden test entry
- **Question:** "Overall sales performance: total revenue, orders, top products"
- **Known-correct:** If "overall" = all history: revenue ≈ **₹11.27B**, closed orders = **16,941**,
  data spans 2024-01-13 → 2026-02-28. If a current-period scope is chosen, it MUST be disclosed and
  must not extend past the last data date (2026-02-28).
- **Expected behavior:** don't silently narrow "overall" to a 10% slice; don't pick a window ending
  in empty future; state the scope; narrative units must match KPI magnitude (billion≠crore).
- **Pass criteria:** scope disclosed & data-bounded; revenue matches stated scope; no 100× unit
  error in prose; no phantom future-week signals.

---

## CASE 004 — "Product category performance: sales volume and revenue"

**Date diagnosed:** 2026-06-08 (master DB). **QA: 12/12 APPROVED.** 139s.
**Window chosen:** "current year 2026 (2026-01-01 to 2026-12-31)."

### Verdict: REVENUE RIGHT, VOLUME WRONG (~5.8× understated), SCOPE WRONG (again)

The fan-out test, take 2. Revenue (the multiplying-risk metric) is again CORRECT. But the OTHER
headline metric — "sales volume" — is computed with the wrong aggregation and is wrong by ~5.8×.
QA gave 12/12 and missed it, same as CASE 002.

### Ground-truth verification (master DB, 2026-06-08)
- **KPI4 Total Revenue = ₹1,503,052,703.92** ✅ EXACT (line-level SUM(line_total), no fan-out).
- **Top category by revenue = RING (₹398,485,875.71)** ✅ EXACT.
- **Top category by volume = EARRINGS** ✅ correct ranking (675 lines / 5,035 units — EARRINGS wins
  either way, so the rank is robust to the bug).
- **KPI5 Total Units Sold = 3,404 — ❌ WRONG.** This is `COUNT(sol.sol_id)` = number of order LINES,
  not units. TRUE units = `SUM(sol.quantity)` = **19,734**. Off by **5.8×.** Every per-category
  "unit volume" chart (charts 2, 3) has the same error — they count lines, not units.

### Failures

1. **`SEMANTIC-WRONG-METRIC` / `VOLUME=LINES-NOT-UNITS` (real bug, 2nd semantic-column case).**
   "Sales volume" / "units sold" was computed as `COUNT(sol_id)` (one count per order line)
   instead of `SUM(quantity)` (actual units). Result: 3,404 vs true 19,734. **Direct contradiction
   with CASE 003**, which on the SAME db + period correctly used `SUM(quantity)` = 19,734. So the
   model is INCONSISTENT between runs about what "units" means — sometimes lines, sometimes quantity.
   This is the same CLASS as CASE 002's total_amount bug: a schema-valid column/agg that doesn't
   match the concept. Invisible to schema check, syntax, and QA (12/12).

2. **`WRONG-SCOPE` (systemic — now 4 for 4).** Context Agent invented "current year 2026
   (Jan 1 – Dec 31)". Data ends 2026-02-28, so 10 of 12 months are empty. Captures only 1,692 of
   16,941 closed orders (~10%). "Product category performance" with no time qualifier should default
   to all history (or disclose the narrowing). Undisclosed again.

3. **`PHANTOM-SIGNAL` (recurs — 2nd time).** signal_detector fired "CRITICAL drop -81.8% at NOSE_PIN",
   "-88.8%", etc. — these are just *smaller categories ranked below bigger ones* being read as
   time-series "drops." Categories are NOT a time series; treating a ranked bar chart as a trend and
   declaring "critical drops" between categories is meaningless. (Inert — tables missing.)

### What worked
- Revenue exact, no fan-out (confirms CASE 003 finding: simple revenue path is safe).
- Top-N rankings correct (RING revenue, EARRINGS volume) despite the absolute-unit bug.
- All KPIs traced to real queries; no hardcoded constants.
- Self-recovered nothing-to-recover (clean SQL this run).

### Golden test entry
- **Question:** "Product category performance: sales volume and revenue"
- **Known-correct (all-history scope):** revenue by category led by RING; "units"/"volume" MUST be
  `SUM(quantity)` not `COUNT(sol_id)`. For the (mis-chosen) 2026 window: revenue ₹1.50B ✅, true
  units **19,734** (NOT 3,404), top vol = EARRINGS (5,035 units).
- **Expected behavior:** "volume"/"units" = SUM(quantity); consistent definition of units across
  runs; scope disclosed & data-bounded; don't treat category rankings as time-series drops.
- **Pass criteria:** total units = 19,734 (not 3,404); volume defined as quantity; scope disclosed.

---

## CASE 005 — "Hunter focus / stores / product recommendations with justification + evidence"

**Date diagnosed:** 2026-06-08 (master DB). **QA: 9/12 APPROVED.** 421s, 10 SQL rounds (6 errors).
**Window chosen:** "Last 12 months" → implemented as calendar-year **2025** (EXTRACT(YEAR)=2025).
**Purpose of this test:** does the model HALLUCINATE recommendations/"evidence", and how does it
handle the "stores" concept (no stores table exists)?

### Verdict: NUMBERS MOSTLY CORRECT, but "STORES" IS A FABRICATED DEFINITION + recurring unit/scope bugs

The KPI numbers verified correct. The interesting failure is conceptual: the user asked about
"stores," there is NO store data, and the model SILENTLY redefined "store" = distinct customer —
then built a whole "Store Portfolio Strategy" on that invented definition without disclosing it.
This is the `DATA-UNAVAILABLE`/`SILENT-ASSUMPTION` family applied to a *dimension*, and it's exactly
the kind of confident fabrication the "justification + evidence" ask is supposed to stress.

### Ground-truth verification (master DB, 2026-06-08)
- **KPI4 Overall Revenue 2025 = ₹6,094,694,732.53** ✅ EXACT. (No fan-out: 0 orders have >1 hunter,
  so hunter→order header SUM is safe — confirmed.)
- **KPI1 Top Hunter Revenue = ₹233,263,253.84 (HNT-002)** ✅ EXACT.
- **KPI2 "Avg Store Count per Hunter" = 5.5** — number is computed correctly AS
  `AVG(COUNT(DISTINCT customer_id))` = 5.5 ✅, BUT the LABEL is wrong: it's avg distinct CUSTOMERS,
  not stores. There is no store grain in the data (only a `territories` table + customers).
- **KPI6 Top Category = RING** ✅ correct rank. The displayed value "₹1,496,853,447" ≈ 2025-closed
  RING revenue ₹1,496,229,260 (✅ ~matches; tiny diff likely closed-filter nuance). NOT all-time
  (which is ₹3.02B) — so it silently scoped to 2025 here too.

### Failures

1. **`FABRICATED-DIMENSION` / `DATA-UNAVAILABLE` (the key finding for this test).** User asked about
   "stores." No store entity exists. The model invented "store = distinct customer" and produced a
   "Store Portfolio Analysis," "revenue per store," "store count" — an entire fabricated analytical
   frame, never disclosing that "store" is really "customer." A reader would believe the company has
   ~5.5 stores per hunter. This is the recommendation-engine hallucination risk in concrete form:
   the *numbers* are real, but the *concept they're labeled with is invented*.

2. **`UNIT-MISLABEL` (recurs — 2nd time, now confirmed systemic).** Narrative says top hunter did
   "₹233.3 **Cr**" and overall "₹6.09 **Cr**". True values: ₹233.3 *crore* would be 2.33 billion, but
   233,263,253 = ₹23.3 crore; and 6,094,694,732 = ₹609 crore, NOT ₹6.09 Cr. The prose is off by
   ~10–100× on EVERY rupee figure. Same class as CASE 003. The Report Writer cannot reliably convert
   raw numbers to Cr/L notation. (CASE 003 + 005 ⇒ systemic.)

3. **`WRONG-SCOPE` (5/5).** "Last 12 months" from today (2026-06) should be ~2025-06→2026-06, but it
   used calendar-2025; and data ends 2026-02-28. Defensible-ish but again an undisclosed
   reinterpretation, and KPI6 silently scoped to 2025 while labeled generically.

4. **`RECOMMENDATION-UNVERIFIED` (new, the headline risk for #11).** The report emits "product
   recommendations with justification." The recommendations are built from real top-product-per-hunter
   queries (good — grounded), BUT the *causal justification* ("recommend X because hunter's customers
   prefer Y karat") is narrative reasoning the pipeline does NOT verify against data. It's plausible
   and partly grounded, but there's no check that the "because" actually holds. Lower severity than
   CASE 001's fabrication (here the evidence tables are real), but it's an unverified-reasoning surface.

5. **SQL fragility (process, not accuracy):** 6 of ~22 queries errored (DuplicateAlias ×4 same bug
   repeated, UndefinedColumn h.territory_id [hallucinated FK — hunters has region/primary_city, not
   territory_id], malformed "LEFT GROUP BY"). The repair loop recovered all, but burned 421s and
   shows the model repeatedly emitting the same alias mistake.

### What worked
- Core KPI numbers exact; no fan-out (hunter→order is 1:many safe).
- Recommendations ARE grounded in real per-hunter product queries (not invented from nothing).
- Strong self-recovery across 6 SQL errors.
- `territories` table EXISTS and would have given a real geographic grain — the model didn't use it
  for "stores" (used customers instead).

### Golden test entry
- **Question:** "Hunter focus / stores / product recommendations with justification + evidence"
- **Known-correct:** Overall 2025 closed revenue = ₹6,094,694,732.53 (₹609 Cr); top hunter HNT-002
  ₹233,263,253.84 (₹23.3 Cr); top category RING. "Stores" has NO data → must say so or map to
  `territories` (real) WITH disclosure, not silently to customers.
- **Expected behavior:** don't fabricate a "store" dimension silently; disclose any concept mapping;
  rupee notation in prose must match magnitude (609 Cr not 6.09 Cr); recommendation justifications
  should cite the actual evidence rows.
- **Pass criteria:** "store" either flagged unavailable or explicitly mapped+disclosed; no 10–100×
  unit error; recommendations traceable to evidence.

---

## CASE 006 — "Has the average discount rate been surging in the last few weeks?" (RE-RUN of 001 on master DB)

**Date diagnosed:** 2026-06-08 (master DB). **Path CHANGED:** intent classifier routed to **`chat`
mode (Sonnet)**, NOT the report multi-agent pipeline (Haiku) as in CASE 001. 15 rounds, 94s.

### Verdict: BETTER than CASE 001 (honest, grounded, no hardcoded KPIs) — but STILL the core bug:
### silently answered MARGIN, never found `discount_exceptions` (the actual discount table)

Direct before/after of CASE 001. The good: this run is far more honest — it explicitly says "the
data tracks margin, not discount; no surge," shows its work, no fabricated KPIs, no fudged math. The
bad: the SAME root failure persists — faced with no obvious discount column, it substituted margin
and **never discovered `discount_exceptions`**, which on the master DB IS the real, usable discount
source. So the answer is honest-but-incomplete rather than confidently-wrong. Improvement in candor,
not in correctness of source selection.

### Ground-truth verification (master DB, 2026-06-08)
- **`discount_exceptions` EXISTS and is usable:** 120 rows, 10 distinct weeks, created_at spans
  **2026-02-14 → 2026-04-15**, avg requested 17.59% / approved 16.94%, statuses incl 25 REJECTED.
  → A genuine "discount rate over recent weeks" answer was POSSIBLE here. The model never queried it.
- **Model's dead-ends (all correctly identified as empty, to its credit):**
  - `sales_invoices.discount_amount` = all zero (16,941 rows, 0 nonzero) ✅ confirmed.
  - price-gap `base_price>selling_price` = 0 of 38,363 lines ✅ confirmed (no implicit discount).
- **Final answer = margin stable ~34.3–35.3% over recent weeks** ✅ VERIFIED CORRECT as a margin
  statement (live: min 34.31, max 35.32 over last 15 weeks). So the fallback metric is accurate; it's
  just not what was asked.

### Failures

1. **`METRIC-SUBSTITUTION` (CONFIRMED SYSTEMIC — the open pattern from CASE 001 now closed).** Asked
   "discount rate," answered "margin." Same substitution as CASE 001, on a DB where the correct source
   (`discount_exceptions`) now EXISTS. The model probes the obvious columns (discount_amount, price
   gap), finds them empty, and pivots to margin — it does NOT search for governance/exception tables.
   → Proves the bug is "doesn't hunt for alternative sources + silently substitutes," not "data was
   truly absent." This is the decisive evidence that METRIC-SUBSTITUTION is a real systemic class.

2. **`SILENT-ASSUMPTION` (6/6).** The margin-for-discount swap is disclosed IN the answer text this
   time ("the data actually tracks average margin percentage") — better than CASE 001 — but it's
   framed as "discount data = margin," which is misleading: discount data exists, it just wasn't
   found. Still an undisclosed *source* decision.

3. **SQL fragility (process):** 15 rounds, lots of `round_missing_numeric_cast` validator rejections
   (the validator did its job — good), one `WHERE2`/`CROSS JOIN` syntax garble at round 12. Recovered.

### What improved vs CASE 001 (genuine progress)
- **No hardcoded `SELECT <const>` KPIs.** (CASE 001 had 4.)
- **No fudged/rescaled math.** (CASE 001 rescaled 4.9%→100%.)
- **Honest hedge:** explicitly tells the user it's reporting margin and found no surge — vs CASE 001's
  confident wrong title.
- **Chat path (Sonnet) is more cautious than the report path (Haiku).** Worth noting: routing matters.
- **Validator caught real issues pre-execution** (round_missing_numeric_cast, table-not-found).

### Golden test entry
- **Question:** "Has the average discount rate been surging in the last few weeks?"
- **Known-correct (master DB):** Discount IS answerable via `discount_exceptions` (120 rows,
  2026-02-14→04-15, avg approved ~16.9%, 10 weeks). Correct answer addresses exception-based discount
  trend; may note invoice `discount_amount` is all-zero. Margin (~35%, stable) is a valid SECONDARY
  note but NOT the headline.
- **Expected behavior:** when the obvious discount column is empty, SEARCH for governance/exception
  tables before substituting; if substituting, disclose that the asked metric's real source
  (discount_exceptions) exists and why it wasn't used.
- **Pass criteria:** queries discount_exceptions OR explicitly states it exists; does not present
  margin AS the discount answer without that disclosure.

---

## CASE 007 — SAME discount query, REPORT path (forced), master DB — THE CONTROLLED COMPARISON

**Date diagnosed:** 2026-06-08 (master DB). Path: **report multi-agent (Haiku), DRIFT_INVESTIGATION**
(SIG-007). 12 SQL rounds, 404s. **QA: 12/12 APPROVED.** Same question as CASE 001 & 006.

### Verdict: BEST RUN IN THE STUDY. Correct metric, real source, honest finding. The bug did NOT recur.

This is the decisive experiment. Same question, same report/Haiku path as the FAILED CASE 001 — only
the DB changed (now has `discount_exceptions`). Result: the pipeline **found and used
`discount_exceptions`, computed the ACTUAL discount rate, and correctly concluded "no surge —
discounts slightly DECLINING."** The METRIC-SUBSTITUTION bug from CASE 001/006 did NOT happen here.

### Ground-truth verification (master DB, 2026-06-08)
- **KPI1 Current Avg Discount Rate (April 2026) = 17.33%** ✅ EXACT (live: 17.33, 18 approved
  exceptions in April). Source = `discount_exceptions.approved_discount_pct`. **Correct table!**
- **KPI2 Baseline (Feb–Mar) = 17.81%** ✅ ~EXACT (live: 17.71 over 42 exceptions; rounding/window
  nuance, immaterial).
- **Finding: discount rate −0.48pp, NOT surging** ✅ VERIFIED (monthly: Feb 17.12 → trend flat/down).
- The model's investigative path was sound: tried `sales_invoices.discount_amount` (Round 1–3), found
  it zero (Round 4), **checked `discount_exceptions` (Round 4), pivoted to it (Round 5+)**, found the
  real date range, computed monthly trend. This is EXACTLY the "search for the alternative source"
  behavior that CASE 006 (chat path) FAILED to do.

### Why did it work here but not CASE 001/006?
1. **Agent 1 listed `discount_exceptions` in relevant_tables** (CASE 001 did NOT — table didn't exist
   then; CASE 006 chat path never looked). On the master DB the schema context now includes it, and
   the report pipeline's Context Agent surfaced it. → Evidence that **giving the agent the right table
   in context is often enough** — a point FOR the metric/source-dictionary fix.
2. **The report path's Phase-1 "check data availability" step** caught the zero discount_amount and
   forced a source hunt. The chat path (CASE 006) gave up at the price-gap dead-end.

### Remaining failures (smaller, but the systemic ones still present)
1. **`MASKED-MATH` STILL PRESENT.** Agent 4 quality note: *"[hunter] single entity 'Riya Shah
   (HNT-012)' contributes 101.3% (>90%) - verif..."* — the contribution normalization quirk from
   CASE 001 recurs (here ~101%, milder than CASE 001's 4.9%→100%, but same mechanism). Watch.
2. **`HARDCODED/UNTRACED KPIs` partial:** KPI5 (Top Hunter) and KPI6 (Exception counts) show
   `SQL: (none)` — narrative-derived, not traced. KPI5 even says "→ NULL in Apr" (a hunter with no
   April data presented as "increased 6.07pp" — internally contradictory). Minor vs CASE 001 but the
   class persists for non-scalar KPIs.
3. **`PHANTOM-SIGNAL`:** signal_detector fired "OUTLIER +5,266,650% in Avg Order Value" — a nonsense
   variance from comparing an order-value (₹9.48L) against an exception-count baseline (18). Inert
   (tables missing) but the logic is still garbage on mixed-unit period-compare rows.
4. **SQL fragility:** Rounds 8–9 = 9 consecutive failures, ALL the same `GROUP2 BY` typo (a malformed
   token the model kept emitting), recovered Round 10. Wasted ~35s. Recurring alias/keyword garbles
   across CASE 005 & 007 suggest a Haiku SQL-emission weakness.

### What worked (the big wins)
- **Correct metric, correct source, correct conclusion** — the headline failure class is ABSENT.
- All scalar discount KPIs verified exact.
- Honest negative finding ("no surge, slightly declining") — no false drift manufactured.
- Self-recovered from 9-in-a-row SQL errors.

### Golden test entry
- Same as CASE 006's known-correct. **This run PASSES the core criterion** (used discount_exceptions,
  did not substitute margin as the headline). Keep as the POSITIVE reference example.

---

## CROSS-CASE MATRIX: the discount question across 3 conditions
| | DB | Path/Model | Found discount_exceptions? | Answered | Verdict |
|---|---|---|---|---|---|
| CASE 001 | old (no table) | report/Haiku | N/A (didn't exist) | margin (silent) + hardcoded KPIs + fudged math | WRONG |
| CASE 006 | master | chat/Sonnet | ❌ no (never looked) | margin (disclosed) | honest but incomplete |
| CASE 007 | master | report/Haiku | ✅ YES | **discount rate (correct)** | **BEST** |

**Conclusion: the report pipeline, given the right table in schema context, does the source-hunt and
gets it right (007). The chat path did NOT hunt (006). So METRIC-SUBSTITUTION is driven by (a) missing
data AND (b) not searching for alternatives — and is FIXABLE by ensuring the right source is in context
+ a "hunt before substitute" rule. Routing matters: report path was more thorough than chat here.**

---

## Patterns (updated as cases accumulate)

**N=2 so far. Emerging signal (still tentative):**

- **`HARDCODED-KPI` looks INCIDENTAL, not systemic (good news).** Present in CASE 001 (discount, data
  missing) but ABSENT in CASE 002 (data present, all KPIs traced to real queries). Hypothesis: the
  model hardcodes KPIs as an *escape hatch when the underlying data is missing/thin*, not as default
  behavior. → A code guard rejecting `SELECT <const>` KPIs is still worth it (cheap, catches the
  worst case) but it's a safety net, not the main fix.

- **`SILENT-ASSUMPTION` / undisclosed scoping is looking SYSTEMIC (CASE 001 time window, CASE 002
  "leftover" definition).** The pipeline routinely makes a judgment call (which window, which filter,
  which definition) and never tells the user. → Strong candidate for a universal fix: force the
  blueprint/report to STATE its operative definitions and assumptions.

- **NEW pattern — `SEMANTIC-WRONG-COLUMN` (CASE 002): picking a schema-valid column that doesn't mean
  what the metric needs** (total_amount = receipt value, used as "leftover value"). This is the
  highest-severity class because it's invisible to every existing guard AND to QA. Watch whether it
  recurs — if money/value KPIs frequently grab the wrong column, that's the #1 accuracy fix.

- **QA agent does NOT re-derive numbers** — CASE 002 got 12/12 with a 34%-overstated headline KPI.
  QA validates narrative/structure/citation, not arithmetic against the DB. → Either QA needs a
  numeric re-check capability, or accuracy must be guaranteed upstream (it can't rely on QA).

- Still need more cases for: `METRIC-SUBSTITUTION` (only seen when data missing — incidental?),
  `MASKED-MATH` (only CASE 001 so far).

**N=3 UPDATE (CASE 003 changed the picture):**

- 🔴 **`SILENT-ASSUMPTION` / `WRONG-SCOPE` is now CONFIRMED SYSTEMIC — 3 for 3.** CASE 001 (time
  window), CASE 002 (leftover definition), CASE 003 (invented "2026 YTD" capturing only 10% of data,
  ending in empty future). **The single most consistent failure across every case.** The pipeline
  ALWAYS makes an undisclosed scoping/definition choice. → This is the #1 universal fix: the
  blueprint must DECLARE its operative scope/definitions, bound the window to actual data dates, and
  surface it on the report.

- 🟢 **`FANOUT` did NOT fire on CASE 003 — downgrade the priority.** I predicted revenue
  double-counting; instead the model correctly used the header total (valid because
  sales_order.total_amount == Σ line_total here, and pricing is strict 1:1 with lines). The fan-out
  guard's fear is real for OTHER paths (gold+diamond multi-row joins) but the simple revenue path is
  safe. → Don't over-invest in fan-out yet; need a query that joins the genuinely-multiplying tables
  (sales_order_line_diamond, job_card_diamond_lines) to actually trigger it. (#6 category report or a
  margin-by-component query is the real fan-out test.)

- 🟡 **`HARDCODED-KPI` confirmed INCIDENTAL — 0 occurrences in CASE 002 & 003 (data present).** Only
  appeared in CASE 001 (data missing). Strong evidence it's a data-missing escape hatch. Code guard
  still cheap & worth it, but de-prioritized.

- 🆕 **`UNIT-MISLABEL` (CASE 003): narrative prose gets number magnitude wrong** (₹1.50Cr vs ₹150Cr,
  100× off) even when the KPI value is correct. The Report Writer formats numbers unreliably. Watch
  for recurrence — if systemic, needs a formatting rule or post-format from the raw value.

- 🆕 **`PHANTOM-SIGNAL` (CASE 003): the post-run signal_detector invents critical "drops" from empty
  future weeks** created by the bad window. Compounds `WRONG-SCOPE`. Currently inert (tables missing)
  but the logic is live.

- 🆕 **QA scoring is inconsistent & not number-aware:** CASE 002 got 12/12 with a 34%-wrong KPI;
  CASE 003 got 9/12 while being numerically PERFECT but wrongly-scoped. QA reacts to surface polish,
  not correctness. Reconfirms: accuracy cannot be delegated to QA.

**N=4 UPDATE (CASE 004 — strong convergence now):**

- 🔴🔴 **`WRONG-SCOPE` is now 4/4 — THE dominant, universal failure.** Every single case invents an
  undisclosed scope/window/definition: CASE 001 time window, 002 "leftover" def, 003 "2026 YTD"
  (10% of data, future-padded), 004 "2026 full year" (10% of data, 10 empty months). The Context
  Agent ALWAYS narrows "overall/performance"-type questions to a tiny recent window with no basis and
  no disclosure. **This is fix #1, unambiguously.**

- 🔴 **`SEMANTIC-WRONG-COLUMN/METRIC` is now CONFIRMED a recurring class (2/4: CASE 002 leftover
  value used total_amount; CASE 004 "units" used COUNT(lines) not SUM(quantity)).** And CASE 004
  proves it's INCONSISTENT run-to-run — CASE 003 got units right (SUM quantity=19,734) on the same
  DB that CASE 004 got wrong (COUNT lines=3,404). The model has no stable mapping from business
  concept → correct column/aggregation. This is the #2 fix and the one most invisible to all guards.
  → Needs a **metric-definition dictionary** ("revenue"=SUM(line_total); "units/volume"=SUM(quantity);
  "leftover value"=qty_available*unit_cost) injected as hard rules, AND ideally a code check.

- 🟢 **`FANOUT` confirmed NOT a problem on revenue paths — 2/2 correct (003, 004).** Drop it from
  the priority list for the simple sales path. (Still untested on diamond/job-card multi-row joins —
  a margin-by-component query would test it, but it's now low priority.)

- 🆕 **`PHANTOM-SIGNAL` is systemic too (2/2 in 003, 004):** the post-run signal_detector treats ANY
  chart (ranked categories, future-empty weeks) as a time series and manufactures "critical drops."
  Meaningless for non-time-series charts. Compounds WRONG-SCOPE. Currently inert (tables missing) but
  the logic runs and would mislead if surfaced. → Either scope signal detection to true time series
  only, or disable until reworked.

- 🟡 **`HARDCODED-KPI` still incidental (0/3 when data present).** **`UNIT-MISLABEL`** (CASE 003)
  not repeated in 004's checked figures — watch.

**CONVERGENCE: at N=4 the top-2 universal fixes are clear (scope disclosure/bounding + metric
definition dictionary). One or two more diverse cases (discount re-run, hunter/recommendations) to
confirm METRIC-SUBSTITUTION and hallucinated-reasoning, then write the Universal Fix Plan.**

**N=5 UPDATE (CASE 005 — patterns now fully converged; ready to write the Fix Plan):**

- 🔴🔴 **`WRONG-SCOPE` 5/5. Undisputed #1.** Every case, no exceptions.
- 🔴 **`SILENT-ASSUMPTION`/`DATA-UNAVAILABLE` now includes FABRICATED DIMENSIONS:** CASE 005 invented
  "store = customer" with no disclosure (no store table exists; `territories` does, unused). Same
  family as CASE 001 (discount→margin) and CASE 002 ("leftover" def). The pipeline NEVER says "this
  concept isn't in the data" — it silently substitutes. This + WRONG-SCOPE are really ONE meta-bug:
  **undisclosed reinterpretation of the question** (scope, metric, OR dimension). → Fix #1 should
  cover all three: force a DECLARED, disclosed "scope + definitions + concept-mappings" contract.
- 🔴 **`UNIT-MISLABEL` now SYSTEMIC (CASE 003 + 005):** prose rupee notation is off 10–100× (₹6.09Cr
  for ₹609Cr). The Report Writer can't reliably format crore/lakh. → cheap deterministic fix:
  format numbers in code from the raw value, don't let the LLM write magnitudes freehand.
- 🔴 **`SEMANTIC-WRONG-METRIC` confirmed (CASE 002 value, 004 units) + KPI2 mislabel in 005.** Metric
  definition dictionary = Fix #2.
- 🟢 **`FANOUT` 0/3 — officially deprioritized.** Hunter→order, order→line revenue all safe.
- 🟡 **`RECOMMENDATION-UNVERIFIED` (CASE 005):** justifications are grounded-ish but the causal
  "because" isn't verified. Note for later; lower priority than scope/metric/units.
- **QA still polish-not-accuracy:** 9/12 here while mislabeling stores & units 100× off.

**STATUS: 5 diverse cases, patterns converged. The Universal Fix Plan can now be written with
evidence. Optional 6th case = discount re-run (confirm METRIC-SUBSTITUTION now that discount_exceptions
exists). Otherwise ready to synthesize §Universal Fix Plan into a ranked, evidence-backed spec.**

---

## Universal Fix Plan (FINAL — synthesized from 7 DB-verified cases, 2026-06-08)

> **Thesis proven across 7 cases:** every error was *schema-valid SQL that executed cleanly* and was
> usually QA-APPROVED. The DB schema (which the app pulls live) eliminates "column doesn't exist"
> errors but guarantees NOTHING about whether the number is TRUE. All real failures are SEMANTIC,
> SCOPE, or DEFINITION errors — invisible to schema checks, syntax checks, the regex pattern-checker,
> AND the QA agent. Therefore: **prompt rules steer; code guards guarantee; the golden set measures.
> You need all three.** Fan-out (the thing the existing `sql_pattern_checker` most fears) was NOT a
> real problem (0/3) — do not over-invest there.
>
> **Evidence base:** CASE 001 (discount, old DB), 002 (leftover inventory), 003 (overall sales),
> 004 (category perf), 005 (hunter/stores), 006 (discount chat path), 007 (discount report path).

### Severity-ranked failure patterns (final tally)

| # | Pattern | Freq | Impact | Root cause |
|---|---|---|---|---|
| P1 | **Undisclosed reinterpretation** of the question — scope / metric / dimension | **6/7** | Answers a silently-different question; user can't tell | Context/BA agents invent a window, metric, or concept-mapping with no basis and no disclosure |
| P2 | **Semantic wrong column/metric** — `units`=COUNT(lines) not SUM(qty); `leftover value`=receipt total not on-hand; KPI mislabels | 4/7 | Headline number wrong 30%–580% | No stable business-concept → column/aggregation mapping; re-decided per run |
| P3 | **Metric substitution under missing data** — discount→margin | 2/2 discount | Wrong metric presented as the answer | Hits empty obvious column, does NOT hunt for alternative source, substitutes silently |
| P4 | **Unit mislabel in prose** — ₹6.09Cr written for ₹609Cr (10–100× off) | 2/2 checked | Headline off by orders of magnitude in narrative | Report Writer formats crore/lakh freehand from raw numbers |
| P5 | **Masked math** — bad decomposition (4.9%, 101.3%) rescaled to 100% silently | 2/7 | Fabricated clean-looking contribution %s | Agent-4 normalization "fixes" instead of failing loud |
| P6 | **Hardcoded / untraced KPIs** — `SELECT <const>` or `SQL:(none)` | 2/7 (when data missing / non-scalar) | KPIs with no data lineage = hallucination surface | Escape hatch when data thin or KPI is non-scalar |
| P7 | **Phantom signals** — signal_detector reads ranked bars / empty future weeks as "critical drops" | 3/7 | Garbage alerts (currently inert — tables missing) | Treats any chart as a time series |
| — | QA never catches accuracy errors | 0/7 caught | False confidence (12/12 on wrong reports) | QA scores polish/structure, not numbers-vs-DB |
| ✓ | Fan-out double-count | **0/3** | none — NOT a problem on sales paths | header total == Σ line; pricing 1:1 with lines |

### The fixes (ranked by impact ÷ effort; tagged PROMPT / CODE / CONTEXT / PROCESS)

**FIX 1 — Declared "Question Contract" the whole pipeline must honor & disclose. [PROMPT + CODE]**
*Kills P1 (6/7) + half of P2/P3.* The single highest-value change.
- Make the Context/BA agent emit an explicit, machine-readable contract BEFORE any SQL:
  `{ scope: {time_window, filters}, primary_metric, metric_definition, dimension_mappings, assumptions[] }`.
- **Bound the window to real data:** clamp any time window to `MAX(order_date)` — never emit a window
  that extends into empty future (CASE 003/004 padded 3–10 empty months). If the question has no time
  qualifier ("overall", "performance"), default to ALL history, not a recent slice.
- **Render the contract ON the report** ("Scope: all closed orders 2024-01-13→2026-02-28; 'units' =
  SUM(quantity); 'store' mapped to customer — no store table exists"). Disclosure converts a silent
  wrong answer into an auditable one.
- **Code check:** reject/flag a report whose data window exceeds the DB's max date, or whose scope
  silently captures <X% of the relevant universe without the question asking for a filter.

**FIX 2 — Metric / source dictionary injected into the SQL agent's context. [CONTEXT + PROMPT]**
*Kills P2 (4/7) + P3 (2/2). CASE 007 proved this works: when discount_exceptions was IN context, the
agent found it; CASE 006 without that nudge did not.*
- A small authored map of business concept → canonical SQL, in the agent system prompt:
  - `revenue` = `SUM(sales_order_line_pricing.line_total)` (or header total ONLY when querying
    sales_order alone — verified equivalent).
  - `units` / `volume` = `SUM(sales_order_line.quantity)` — **never** `COUNT(sol_id)`.
  - `leftover/on-hand value` = `SUM(quantity_available * unit_cost)` — **never** `total_amount`.
  - `discount` = `discount_exceptions.approved_discount_pct`; `sales_invoices.discount_amount` is
    ALL ZERO — do not use it; if discount source is empty, SAY SO.
  - `margin` = `AVG(margin_pct)`. (margin ≠ discount.)
- **"Hunt before substitute" rule:** if the obvious column for the asked metric is empty/zero, search
  governance/exception tables (discount_exceptions, etc.) BEFORE falling back; if you must fall back,
  the contract's `assumptions[]` must record it and the report must disclose it.

**FIX 3 — Code guards on the final report object (deterministic, can't be prompted away). [CODE]**
*Kills P5, P6; backstops P1.* These run after Agent 3, before the report is returned.
- **Reject constant KPIs:** any KPI whose `executed_sql` has no `FROM` → flag/strip (P6). The repair
  helper `format_issues_for_repair()` already exists in `sql_pattern_checker.py` — wire it to a hard
  gate instead of warn-only.
- **Fail-loud on decomposition:** if contribution_pct sum ∉ [90,110]%, mark the section
  `low_confidence` and surface it — do NOT silently rescale to 100% (P5; CASE 001 4.9%, CASE 007 101%).
- **Require SQL lineage:** every KPI/chart must carry its `executed_sql` (CASE 003/004/005/007 had
  `SQL:(none)` on charts) — block or mark untraceable items.

**FIX 4 — Format numbers in code, not in prose. [CODE]** *Kills P4 (cheap, high embarrassment).*
- A single `format_inr(raw_value)` helper produces "₹609 Cr" / "₹2.33 Cr" from the raw KPI value;
  the Report Writer references the formatted string, never hand-converts magnitudes. Eliminates the
  10–100× crore/lakh errors (CASE 003, 005).

**FIX 5 — Make QA numeric-aware OR stop trusting it as an accuracy gate. [PROMPT/CODE + PROCESS]**
*Addresses the 0/7 catch rate.* QA approved 34%-wrong (002) and 580%-wrong (004) reports 12/12.
- Either give QA a re-derivation step (re-run 1–2 headline KPIs and compare), or explicitly treat QA
  as a *presentation* check only and rely on FIX 1–3 for correctness. Do NOT let "QA 12/12" imply
  accuracy in the UI.

**FIX 6 — Scope or disable the phantom signal_detector. [CODE]** *Kills P7.*
- Only run drop/spike/trend detection on genuine time-series charts (x = date), never on ranked
  category/entity bars or windows with empty future buckets. (Currently inert because the telemetry
  tables are missing — fix before those tables get created, or it will start surfacing garbage alerts.)

**FIX 7 — Build the golden test set from these 7 cases + run on every change. [PROCESS]**
*This is the measurement layer.* Each CASE's "Golden test entry" = one regression test (question →
known-correct numbers/behavior → pass criteria). 007 is the POSITIVE reference. Without this, no fix
can be proven; with it, accuracy becomes a number you can watch.

### Sequencing recommendation
1. **FIX 2 (metric/source dictionary)** — fastest, highest correctness ROI, proven by 007. Mostly prompt.
2. **FIX 1 (question contract + window-bounding + disclosure)** — kills the dominant P1; prompt + a
   couple of code clamps.
3. **FIX 3 + FIX 4 (report-object code guards + number formatting)** — deterministic guarantees.
4. **FIX 7 (golden set)** — stand up alongside 1–3 so each is measured. (Can seed immediately.)
5. **FIX 5, FIX 6** — lower urgency (QA framing; phantom signals are inert today).

### Explicit NON-priorities (evidence-based)
- **Fan-out hardening** — 0/3, the simple sales paths are safe. Only revisit if a margin-by-component
  (gold+diamond multi-row) query later shows it.
- **The regex `sql_fixer.py` / `sql_pattern_checker.py` expansion** — they guard a problem (fan-out,
  hallucinated columns) that the live schema + these cases show is NOT where errors occur. Don't grow
  them; the real errors are semantic/scope, which regex can't see.

### Cross-cutting note: routing matters
CASE 006 (chat/Sonnet) vs 007 (report/Haiku) on the identical question: the report path
out-investigated the chat path (found discount_exceptions; chat gave up). But Haiku shows a recurring
SQL-emission weakness (repeated `GROUP2 BY` / alias garbles, CASE 005 & 007). Consider: the contract
(FIX 1) + dictionary (FIX 2) help BOTH paths; if SQL fragility persists, test the SQL agent on Sonnet.

---

## IMPLEMENTATION ROUND 1 (2026-06-08) — fixes applied, awaiting re-test

Implemented the highest-ROI fixes from the plan. Boot-tested: full app imports, all 6 agent prompts
build, guards work, no residual bad patterns. **Next: re-run CASES 001–007 to measure improvement.**

**FIX 1 — data-anchored time context [CODE, `ai/claude_prompts.py`]:** `_date_context()` now queries
`MAX(sales_order.order_date)` (cached, fail-safe) and instructs every agent to anchor
'current/recent/last-N' to the DATA end (2026-03-05), NOT today; 'overall/total' → ALL history; never
emit an upper bound past the data end. Targets P1 (6/7).

**FIX 2 — canonical metric dictionary + fixed wrong rules [PROMPT, SQL-agent + Data-analyst]:** added
an authoritative concept→SQL map (revenue, units=SUM(quantity) NOT COUNT(sol_id), leftover
value=qty_available*unit_cost NOT total_amount, discount=discount_exceptions NOT the all-zero
discount_amount, margin≠discount, 'store'=no table→disclose mapping) + a 'hunt-before-substitute /
disclose substitution' rule. Also REPLACED the 3 stale example queries in the Data-Analyst prompt that
were teaching the dead `discount_amount`/`NOW()` patterns. Targets P2 (4/7) + P3 (2/2).

**FIX 1b — baseline construction anchored to DATA_END, not NOW() [PROMPT]:** removed all `NOW()-INTERVAL`
window examples (they fell in empty future). Targets P1 + phantom-drops.

**FIX 4 — Indian-numbering conversion rule [PROMPT, Report-Writer]:** explicit Cr=1e7 / L=1e5 thresholds
+ worked examples + 'write the plain number if unsure'. Targets P4 (2/2: ₹609Cr-written-as-₹6.09Cr).

**FIX 3 — deterministic report guards [CODE, `ai/claude_multi_agent.py:_apply_report_guards`]:** after
SQL agent, annotate (not mutate) report with `accuracy_warnings` + per-item `_accuracy_flag` for
(a) KPIs whose SQL has no FROM (untraced/hardcoded), (b) contribution_pct sums outside [90,110]%
(masked-math). Surfaces instead of fudging. Targets P5, P6.

**NOT yet done (lower priority):** FIX 5 (QA numeric re-derivation), FIX 6 (phantom signal scoping —
still inert as telemetry tables absent), telemetry-table migration. Fan-out deliberately untouched (0/3).

**Re-test protocol:** re-run each query, I verify vs live DB, compare to the original CASE, mark
PASS/PARTIAL/FAIL per the golden 'Pass criteria'. Track a before/after accuracy count.

---

## RE-TEST SCORECARD (after Implementation Round 1)

### RT-001 (re-run of CASE 003) — "Overall sales performance: total revenue, orders, top products"
**Date:** 2026-06-08, post-fix. **Result: ✅ PASS (was FAIL).** The keystone fix worked.

| | CASE 003 (before) | RT-001 (after) | Verified |
|---|---|---|---|
| Timeframe chosen | invented "2026 YTD Jan1–Jun8" (→ future-padded, 10% of data) | **"all-time 2024-01-13 to 2026-03-05"** | ✅ Fix 1 worked |
| Total Revenue | ₹1.50B (10% slice, mislabeled "overall") | **₹11,267,974,058.97** | ✅ EXACT vs DB |
| Total Orders | 1,692 | **16,941** | ✅ EXACT |
| AOV | 888,329 | **665,130.40** | ✅ EXACT |
| Units (SUM qty) | — | **204,020** | ✅ EXACT |
| Unique products | — | **953** | ✅ EXACT |
| Upper date bound | ran to empty future (Jun 8) | **clamped to 2026-03-05** (data end) | ✅ no future window |
| Unit notation in prose | (was 100× wrong elsewhere) | "₹11.27 crore" for 11.27**billion** | ⚠️ STILL WRONG (see below) |

**What the fix fixed (decisively):**
- Context Agent now picks **"all-time (2024-01-13 to 2026-03-05)"** for "overall" — exactly the
  intended behavior. The dominant P1 (wrong-scope) bug is GONE on this query: revenue is the TRUE
  ₹11.27B grand total (7.5× the old wrong ₹1.50B), all 16,941 orders, window clamped to the real data
  end. Every KPI verified EXACT against the live DB.
- Revenue path used header total on sales_order-alone, line_total on dimension joins — correct, no fan-out.
- No hardcoded KPIs.

**Residual issues (smaller, next round):**
1. **`UNIT-MISLABEL` STILL PRESENT (P4 not fixed by the rule).** Summary says **"₹11.27 crore"** for
   ₹11,267,974,058 — that's ₹1,126 **crore** (≈₹11.27 *billion*), so the prose is ~100× low again. The
   Fix-4 prompt rule did NOT hold. → **This needs the CODE fix (deterministic `format_inr()` from the
   raw value), not a prompt rule.** Promote Fix 4 to code next round.
2. **`PHANTOM-SIGNAL` still firing (P7).** signal_detector emitted "SPIKE +142.9%", "DROP -45%" on the
   monthly-revenue series. Here they're at least on a REAL time series (not empty future), but the
   thresholds are noise on normal monthly variation. Still inert (tables missing). Lower priority.
3. Minor SQL fragility: 2 `GROUP BY label` alias errors (rounds 2–3), self-recovered round 4.

**Verdict:** the #1 systemic bug (silent wrong-scope) is FIXED on this query. P4 (number formatting)
confirmed needs a code fix, not a prompt. Net: big win.

---

### RT-002 (re-run of CASE 004) — "Product category performance: sales volume and revenue"
**Date:** 2026-06-08, post-fix. **Result: ✅ PASS (was FAIL on volume).**

| | CASE 004 (before) | RT-002 (after) | Verified |
|---|---|---|---|
| Timeframe | invented "2026 full year" (10%, empty future) | **"all-time up to 2026-03-05"** | ✅ Fix 1 |
| Volume metric | `COUNT(sol_id)` = lines (WRONG) | **`SUM(sol.quantity)`** | ✅ Fix 2 worked |
| Total units | 3,404 (5.8× too low) | **204,020** (all-time) | ✅ matches DB & RT-001 |
| Top cat by volume | EARRINGS | EARRINGS (56,218 units) | ✅ EXACT |
| Top cat by revenue | RING | RING (₹2.71B all-time) | ✅ EXACT |
| Revenue (line_total) | correct | correct, no fan-out | ✅ |

**What the fix fixed:** The metric dictionary landed — every volume query now uses `SUM(sol.quantity)`
(trace Chart 2: `SUM(sol.quantity) as volume`), NOT `COUNT(sol_id)`. The exact CASE 004 bug (3,404
lines mislabeled as units) is GONE. Scope also corrected to all-time (Fix 1). Revenue line-level, no
fan-out. No hardcoded KPIs.

**Residual:** (1) `PHANTOM-SIGNAL` still fires on the ranked-category bars ("DROP -79.8% at NOSE_PIN"
— meaningless: categories aren't a time series); inert. (2) prose "₹11.26 crore" again ~100× low for
₹11.26B (P4 — needs the code formatter). (3) minor SQL fragility: a GROUP BY error + a `solp.sql_id`
typo, self-recovered.

**Verdict:** the headline CASE 004 bug (volume = lines) is FIXED. ✅

---

### RT-003 (re-run of CASE 002) — "leftover inventory where raw material was provided"
**Date:** 2026-06-08, post-fix. **Result: ✅ PASS (was FAIL on leftover value).** QA 11/12.

| | CASE 002 (before) | RT-003 (after) | Verified |
|---|---|---|---|
| Leftover VALUE | `SUM(total_amount)` = ₹105.2M (receipt total, 34% high) | **`SUM(quantity_available*unit_cost)` = ₹79,045,615.53** | ✅ EXACT vs DB |
| SKU count | 124 | 125 (minor def diff, fg_id vs sku) | ✅ ~ |
| Top vendor by value | — | Rajput Gold Works ₹51,104,770 | ✅ EXACT |
| Scope | (ok) | "all-time through 2026-03-05" | ✅ |
| Avg days since receipt | 572.5 | 522 (anchored to DATA date 2026-03-05, not today) | ✅ better |

**What the fix fixed:** The metric dictionary's "leftover/on-hand value = quantity_available*unit_cost,
NEVER total_amount" rule landed perfectly — KPI1 now ₹79.0M (the true on-hand value) instead of the
34%-inflated ₹105.2M receipt total. Verified EXACT against DB. The age metric now anchors to the data
date (2026-03-05) per Fix 1, instead of drifting with today. No hardcoded KPIs.

**Residual:** age-distribution chart returned only 1 bucket (rows:1) — likely a binning quirk, minor.
Table title "Top 20" returned 19 (off-by-one, cosmetic). Prose used "₹79.05M" — note: here it wrote
**M** not Cr/L and it's actually CORRECT (79.0M = ₹7.9 Cr ≈ right order of magnitude), so P4 didn't
bite this time because the value is small enough; the code formatter (round 2) will still standardize it.

**Verdict:** the CASE 002 semantic-wrong-column bug is FIXED. ✅

---

## RE-TEST SCORECARD SUMMARY (Implementation Round 1)
| Re-test | Original verdict | New verdict | Fix that landed |
|---|---|---|---|
| RT-001 (overall sales) | FAIL (wrong scope, ₹1.5B of true ₹11.27B) | ✅ PASS | Fix 1 (data-anchored scope) |
| RT-002 (category perf) | FAIL (units 3,404 of true 204,020) | ✅ PASS | Fix 2 (metric dictionary: units=SUM(qty)) |
| RT-003 (leftover inv) | FAIL (value ₹105M of true ₹79M) | ✅ PASS | Fix 2 (leftover value=qty_avail*unit_cost) |
| RT-004 (discount, CHAT path) | CASE 006 = incomplete (chat bailed to margin, never found discount_exceptions) | ✅ PASS | Fix 2 + Fix 1b (chat path now goes straight to discount_exceptions + data-anchored weeks) |

### RT-004 detail — "Has the average discount rate been surging..." (CHAT path / Sonnet)
Intent classifier sent it to chat (single-metric trend). **This is the FIX of CASE 006's failure:**
- Went STRAIGHT to `discount_exceptions.approved_discount_pct` — no dead `discount_amount`, no margin
  substitution. ✅ (Fix 2 landed on chat path too.)
- Anchored to data: used `MAX(order_date)` / `DATE_TRUNC('week', created_at)`, NOT `NOW()`. ✅ (Fix 1b.)
- Answer: "modest upward trend 16.20%→17.75% recent weeks, but small sample, cannot confirm a true
  surge." DB-verified: weekly approved discount ~15–19% with 2–12 exceptions/week, recent W15–16 =
  16.21%/18.01% — the hedged read is fair and correct. ✅
- Minor SQL fragility: 2 validator rejections (CTE alias 'not found in schema' false-flags) + one
  `WHERE2` typo, self-recovered by round 4. (Validator's table-existence check mis-flags CTE names —
  cosmetic, didn't block.)
Report-path re-run optional (CASE 007 already passed it pre-fix); chat-path pass closes CASE 006.

**Confirmed STILL OPEN after round 1 (→ Implementation Round 2):**
- **P4 UNIT-MISLABEL** — prose still ~100× off ("₹11.27 crore" for billions). Prompt rule (Fix 4) did
  NOT hold across RT-001 & RT-002. **MUST become a CODE formatter (`format_inr` from raw value).** TOP
  priority for round 2.
- **P7 PHANTOM-SIGNAL** — still manufacturing "critical drops" on ranked/category charts (non-time-series).
  Inert (tables missing) but should be scoped to true time-series before telemetry tables are created.
- Minor Haiku SQL-emission fragility (GROUP BY alias, occasional typo) — self-recovers; low priority.

---

## IMPLEMENTATION ROUND 2 (2026-06-08) — code fixes for the residuals

Round 1 re-tests passed 4/4 on the hard number bugs; two residuals remained. Round 2 addresses them
in CODE (where prompt rules failed). Boot-tested: formatter correct, guard attaches value_inr, CTE
false-flag gone, full app imports.

**FIX 4 (now CODE) — `format_inr()` deterministic currency formatter [`ai/claude_multi_agent.py`]:**
prompt rule did NOT hold (RT-001/002 wrote "₹11.27 crore" for ₹11.27 BILLION). Now `format_inr(raw)`
converts in code (Cr=1e7, L=1e5): 11,267,974,058 → "₹1,126.80 Cr", 1,503,052,703 → "₹150.31 Cr",
410,000 → "₹4.10 L". `_apply_report_guards` attaches `value_inr` to currency KPIs (detected by
format field or name keywords: revenue/value/amount/aov/sales/cost/impact). Report-Writer prompt
updated: "USE the pre-computed `value_inr` verbatim; only fall back to manual conversion if absent."
Targets P4. **Needs RT to confirm the writer actually quotes value_inr in prose.**

**BONUS FIX — CTE false-flag in validator [`ai/validator.py`]:** `check_sql_against_schema` flagged
CTE names (WITH x AS …) as "Table not found in schema" (RT-004 wasted 2 rounds on this). Now collects
CTE names and excludes them. Verified: `WITH data_end AS (...), weekly AS (...)` no longer flagged.

**STILL DEFERRED:** P7 phantom-signals (inert — telemetry tables absent), QA numeric re-derivation
(Fix 5), telemetry-table migration, Haiku SQL-emission typos (self-recover).

## IMPLEMENTATION ROUND 2b (2026-06-08) — BULLETPROOF currency formatting [Joel's call]

Joel's principle (correct): calculations must be done in Python, never trusted to the LLM. Round 2's
prompt-says-"use value_inr" was still a soft ask. Round 2b makes code the FINAL word.

**`_enforce_currency_formatting(report)` [`ai/claude_multi_agent.py`], called AFTER the Report Writer,
before QA:**
- **Pass 1 (always):** overwrite every currency KPI's `value_inr` from the raw `value` via
  `format_inr()` — deterministic, LLM-independent. (Excludes percent KPIs.)
- **Pass 2 (safe prose scrub):** in summary/narrative/insight text, find '₹N crore/lakh/billion'
  figures; if a figure maps to a known KPI raw value at a WRONG magnitude (the ×100 crore/billion
  confusion), replace with the correct `format_inr` string. **Never rewrites an unmatched figure**
  (no fabrication) and **leaves correct figures untouched.**

**Boot-tested on the exact RT-001 bug + edges:**
- prose "₹11.27 crore" (raw KPI 11,267,974,058) → auto-corrected to **"₹1,126.80 Cr"** in summary AND
  insight body. ✅
- correct "₹7.90 Cr" (raw 79,045,615) → left unchanged. ✅
- unmatched "₹54.21 crore combined" → left unchanged (not fabricated). ✅
- app imports OK.

P4 is now structurally enforced in code (not prompt-dependent). RT to confirm end-to-end on the live
pipeline, but the unit-level proof already shows the exact failing case is fixed.

---

### RT-005 (CAPSTONE — variant of CASE 005) — "Sales growth report: stores for hunters to focus, products to recommend, justification + evidence"
**Date:** 2026-06-08, post-fix (Round 1+2+2b). Hardest query on the list. **Result: ✅ PASS (substantive), 3 fixes confirmed end-to-end.** QA 7/12 CONDITIONAL.

| Fix under test | Before (CASE 005) | RT-005 (after) | Verdict |
|---|---|---|---|
| **Currency prose (P4, Round 2b)** | "₹233.3 Cr" for ₹23.3 Cr; "₹6.09 Cr" for ₹609 Cr (10–100× off) | summary reads **"₹1,126.80 Cr"** (exact format_inr output) | ✅ FIXED end-to-end |
| **"store" fabrication (Fix 2)** | silently invented "store = customer", undisclosed | BA explicitly wrote **"customer accounts (stores)"**; subject = "customer accounts"; disclosed the mapping | ✅ FIXED (disclosed, not faked) |
| **Scope (Fix 1)** | "2025" / narrow | **"all historical through 2026-03-05"** | ✅ |

**DB-verified numbers (all EXACT):**
- KPI4 total closed revenue = ₹11,267,974,058.97 ✅
- KPI1 top-20 customer concentration = 44.04% ✅
- KPI5 AOV (top 20) = ₹419,929.34 ✅
- Confirmed: NO store table exists (store→customer mapping was the correct, now-disclosed choice).

**What worked:** all three implemented fixes held simultaneously on the hardest query. Numbers exact,
revenue line-level (no fan-out), scope all-time, currency prose correct, store concept disclosed.
Recommendations grounded in real per-customer/product queries.

**Residual issues (known, deferred):**
1. **SQL fragility = the main weakness now.** 13 rounds, ~280s, MANY repeated `DuplicateAlias`
   ("table sol specified more than once") + `missing FROM-clause` errors — the model kept re-emitting
   the same malformed join across rounds 3–11. Self-recovered, but slow and fragile. Also one query
   took **117s** (round 9). → Haiku SQL-emission weakness; candidate for the SQL agent on Sonnet, or a
   targeted alias-dedup pre-check. NOT an accuracy bug (final numbers correct) but a robustness/cost one.
2. **`RECOMMENDATION-UNVERIFIED` (still open, as expected):** "products to recommend + justification"
   — recommendations come from real margin/revenue queries (grounded), but the causal "because" is
   narrative, not verified. Acceptable for now; the evidence cited IS real data.
3. Chart 2 "Top 30" returned 7 rows; some top-N/row-count mismatches (cosmetic).

**Verdict:** the system NOW HANDLES this hard, open-ended query correctly on the numbers and discloses
its assumptions — a clear pass and the strongest evidence yet that the fixes generalize. Remaining gap
is SQL-generation robustness (speed/retries), not accuracy.

---

## FINAL RE-TEST SCORECARD (all fixes, 2026-06-08)
| Re-test | Original | After fixes | Fix(es) confirmed |
|---|---|---|---|
| RT-001 overall sales | FAIL (₹1.5B/₹11.27B, wrong scope) | ✅ PASS | Fix 1 (scope) |
| RT-002 category perf | FAIL (units 3,404/204,020) | ✅ PASS | Fix 2 (units=SUM qty) |
| RT-003 leftover inv | FAIL (₹105M/₹79M) | ✅ PASS | Fix 2 (value=qty×cost) |
| RT-004 discount (chat) | incomplete (bailed to margin) | ✅ PASS | Fix 2 + 1b |
| RT-005 hunter/stores/growth | FAIL (faked stores, 100× prose, etc.) | ✅ PASS | Fix 1 + 2 + 2b + store-disclosure |

**5/5 PASS.** Every original headline accuracy bug fixed and DB-verified. P4 enforced in code (2b).
**Remaining open items (NOT accuracy-critical):** (a) Haiku SQL-emission fragility (DuplicateAlias/
missing-FROM retries, slow) — robustness/cost; (b) RECOMMENDATION-UNVERIFIED causal claims; (c) P7
phantom-signals (inert); (d) QA not number-aware; (e) telemetry-table migration.

---

### RT-006 (NEW hard test, Tier 1) — "Break down gross margin by component (gold/diamond/making/discount), top 5 categories"
**Date:** 2026-06-08. **Result: ⚠️ PARTIAL PASS — fan-out avoided (good), but MASKED-MATH recurred (real bug found).**

**✅ The fan-out trap was correctly AVOIDED (the thing I most expected to break):**
- The model did NOT join the multi-row `sales_order_line_diamond` (91k) / `_gold` tables. It found that
  `sales_order_line_pricing` ALREADY carries `gold_amount_per_unit`, `diamond_amount_per_unit`,
  `making_charges_per_unit` (verified: 3 cols on pricing, 1:1 with the line). So it computed components
  from the single pricing row — sidestepping fan-out entirely. Smart, correct path.
- KPI5 combined top-5 revenue = ₹9,320,474,400.57 — **EXACT** vs DB. ✅
- Currency prose "₹932.05 Cr" correct (Round 2b holding). ✅ Scope all-time. ✅

**🔴 REAL BUG FOUND — `MASKED-MATH` (component %s adjusted to force a clean 100%):**
| Component | Reported | TRUE (DB) |
|---|---|---|
| Gold % of base | **70.0** | 68.55 |
| Diamond % of base | **19.75** | 21.49 |
| Making % of base | **10.25** | 9.96 |
| Sum | **exactly 100.00** | 100.00 |
- The reported trio sums to a suspiciously perfect 100.0, but the TRUE per-component values are
  68.55 / 21.49 / 9.96. Gold is OVERSTATED (+1.45pp), diamond UNDERSTATED (−1.74pp). The pipeline
  (likely Agent 4 Data Analyst, or the writer) massaged the components so the total looks tidy — same
  class as CASE 001's 4.9%→100% rescale. Individual components are now WRONG even though they sum right.
- This is the `MASKED-MATH` pattern I flagged as STILL-OPEN (P5). It surfaced here on component math.
- NOTE: my Round-1 `_apply_report_guards` only checks `contribution_pct` summing — it did NOT catch
  this because these are separate KPI values (gold_pct/diamond_pct/making_pct), not a contribution
  array. The guard needs to also detect "sibling component KPIs that were adjusted vs their source SQL."

**🔴 SECOND BUG — QA verdict display:** QA scored **4/12 = REJECTED** (approval_level "REJECTED") but
the verdict line printed **"APPROVED"**. The score-4 threshold logic (`approved = score >= 4`) treats
exactly-4 as approved, but the QA agent itself returned approved:false / "REJECTED". Mismatch between
QA's own verdict and the pipeline's override → a genuinely rejected report shipped as approved.

**🟡 Also:** "discount" component was requested but isn't a pricing column — the model used
gold/diamond/making/residual instead and didn't flag that "discount" wasn't separately available
(minor DATA-UNAVAILABLE non-disclosure). 15 SQL rounds but clean this time (only 1 GROUP BY retry).

**Verdict:** accuracy is MOSTLY right and fan-out was beaten, but component values were silently
adjusted to sum to 100% (MASKED-MATH) — a real, fixable bug. This is exactly what hard-question
testing was meant to surface.

---

### RT-007 (NEW hard test, Tier 1) — "Which diamond shapes and qualities generate the most revenue and margin?"
**Date:** 2026-06-08. **Result: ❌ FAIL — DIAMOND FAN-OUT DOUBLE-COUNT (the predicted bug, confirmed).**

**The fan-out bug fired exactly as predicted.** "By shape/quality" FORCED a join to
`sales_order_line_diamond` (the many-side: avg **2.56 diamond rows per line**), and the model did
`SUM(solp.line_total)` across it — repeating each line's revenue ~2.56× per its diamond rows.

| | Reported | TRUE (DB) | Error |
|---|---|---|---|
| Total Diamond Revenue | **₹30,901,993,048.80** | ₹11,267,974,059 (= ALL closed revenue) | **2.74× inflated** |
- Dead giveaway: claimed diamond revenue (₹3,090 Cr) is **2.74× the ENTIRE company's revenue**
  (₹1,127 Cr) — impossible. Inflation factor 2.74 ≈ avg diamond-rows-per-line 2.56 → textbook fan-out.
- ALL absolute revenue figures wrong: KPI2, KPI5 (avg ₹/order ₹1,888,413), every shape/quality
  revenue chart, the heatmap, the table. Margin %s likely ~ok (ratios survive uniform fan-out).
- The CORRECT approach: attribute diamond-level value via the diamond line's OWN column
  `sales_order_line_diamond.diamond_amount_per_unit` (× quantity), NOT the line-level `line_total`.

**WHY the existing fan-out guard MISSED it (important — this is the fix target):**
The prompt's fan-out rule covers `sales_order → sales_order_line` (use line_total not order total).
But it does NOT cover the NEXT level: `sales_order_line → sales_order_line_diamond` fans out the LINE,
so summing `line_total` across diamond rows double-counts. The guard needs a rule: "when you join a
child of sales_order_line (diamond/gold with MANY rows per line), NEVER sum line_total/pricing across
it — use the child's own per-unit amount, or pre-aggregate the line revenue to one row per line first."
Also: `sql_pattern_checker` has fan-out heuristics but they didn't catch/block this either.

**QA gave 11/12 (blind again).** A report claiming diamond revenue > total company revenue passed QA.

**Verdict:** the deepest documented accuracy risk (multi-row diamond fan-out) IS still live. This is
the single most important bug found in hard-question testing — a 2.7× revenue inflation on any
diamond-attribute query. Fix = extend the fan-out rule to line-CHILD tables (diamond/gold) + a guard.

---

### RT-008 (NEW hard test, Tier 3) — "Which customers are most at risk of churning, revenue exposure if we lose them?"
**Date:** 2026-06-08. **Result: ❌ FAIL (fabricated metric + wrong question) — BUT the report guard FIRED (first live catch).**

**✅ FIRST LIVE GUARD CATCH:** `_apply_report_guards` (Round 1 code) printed
`[Report Guards] 6 accuracy warning(s)` — correctly flagged KPIs 2–7 as having NO SQL FROM-clause
(`SQL: (none)`) = untraceable/possible hallucination. The infrastructure works. (These warnings are
attached to the report object but NOT yet surfaced to the user or used to block — see fix needed.)

**🔴 FABRICATION (the predicted Tier-3 failure):** "churn" / "at risk" are NOT columns (verified: ZERO
churn/at_risk/risk_score columns anywhere in 55 tables). The model INVENTED a churn model — KPI1 SQL:
`SUM(CASE WHEN is_at_risk=1 THEN revenue_12m * churn_prob * 0.32 ...)` — `churn_prob` and the `0.32`
magic multiplier do not exist in the data. Fabricated formula presented as a computed metric, NOT
disclosed as an assumption. This is the hallucination-of-reasoning risk, confirmed.

**🔴 WRONG QUESTION (metric substitution):** Agent 1 classified "churn" as **SIG-022 "Outstanding
Concentration" (CASH/AR signal)** and pivoted the whole report to accounts-receivable / outstanding
balances, then bolted the fake churn math on top. Real churn proxy (declining order recency/frequency
— which IS derivable from order_date gaps) was NOT cleanly used; instead it conflated churn with AR.

**🔴 MASKED/UNTRACED KPIs:** 6 of 7 KPIs + ALL 3 charts have `SQL:(none)`. "Relative Variance = 1092.55%"
and "Revenue at risk surged 1,092%" are built on the untraced baseline (₹88,926,136, no SQL) vs current
(₹1,060,489,992) — a fabricated 11× surge. QA still 9/12 "APPROVED".

**Verdict:** worst-behaved of the hard tests — fabricated a churn formula AND answered AR instead of
churn. The ONE bright spot: the untraced-KPI guard detected it (proving Round-1 infra). Fix needed:
(1) when a requested concept has no backing column, the pipeline must SAY SO / use a disclosed proxy,
not invent a formula; (2) the guard's warnings should BLOCK or be surfaced, not just logged; (3) the
"hunt before substitute / disclose substitution" rule must extend to ABSTRACT concepts (churn, risk),
not just missing source tables.

---

### RT-009 (NEW hard test, Tier 4) — "Average order fulfillment time, and which vendors are slowest?"
**Date:** 2026-06-08. **Result: ❌ FAIL — negative-lead-time landmine NOT handled (documented data-quality trap).**

**The documented landmine bit exactly as the data doc warned.** `so_fulfillment_log` has **677 negative
`days_to_fulfill`** + **697 negative `days_sol_to_po`** (+1 zero). The doc explicitly says these must be
filtered before averaging. The model used `AVG(NULLIF(days_to_fulfill, 0))` — which strips ZEROS ONLY,
NOT negatives — so negatives stayed in and dragged the average down.

| | Reported | TRUE (>0 only) | Error |
|---|---|---|---|
| Overall avg fulfillment | **13.96 days** | **19.60 days** | ~29% too LOW |
- Verified: `AVG(NULLIF(days_to_fulfill,0))` = 13.96 = IDENTICAL to raw avg incl. negatives (NULLIF 0
  did nothing useful). Correct avg excluding negatives = 19.60.
- **KPI5 "Avg Order-to-PO Cycle = −2.96 days"** — a PHYSICALLY IMPOSSIBLE negative duration shipped as
  a headline metric, no flag. (It's the avg of `days_sol_to_po` incl. its 697 negatives.)
- QA gave 11/12 "APPROVED" — blind to a negative-days KPI and a 29%-understated headline.

**Why it failed:** the metric dictionary / prompt has NO rule about the documented negative-lead-time
landmine (data doc §6). The model "knew" to NULLIF zeros but not to filter negatives. Needs: a
data-quality rule for the lead-time columns (days_to_fulfill / days_sol_to_po / days_to_transfer /
days_dispatched > 0) + a guard that flags any negative duration KPI.

**Verdict:** data-quality-trap class FAILS. The fix is a known, bounded prompt rule (filter negative
lead-times) + a numeric-sanity guard (no negative durations).

---

## IMPLEMENTATION ROUND 3 (2026-06-08) — fixes for the 5 hard-test bugs (RT-006..009 + QA)
Boot-tested: all guards fire on the real failing cases, no false positives, app imports.

**FIX A — Second-level fan-out rule [PROMPT, SQL agent]:** the #1 bug (RT-007, 2.7× revenue inflation).
Added explicit rule: NEVER SUM line_total/total_amount across a join to `sales_order_line_diamond` /
`_gold` (many rows per line); use the child's own `diamond_amount_per_unit * quantity`, or pre-aggregate
line revenue to one row per line first. Includes the sanity check "revenue can't exceed ~₹11.3B total."

**FIX B — Negative-lead-time data-quality rule [PROMPT, SQL agent]:** RT-009. Filter
`days_to_fulfill/days_sol_to_po/days_to_transfer/days_dispatched > 0` before averaging (NULLIF(,0) is
NOT enough); a negative average duration is always a bug.

**FIX C — No-fabrication rule for abstract concepts [PROMPT, SQL agent]:** RT-008. Churn/risk/score
are NOT columns — forbid invented formulas/magic multipliers (`* churn_prob * 0.32`); require an
HONEST, DISCLOSED proxy (e.g. churn = no order in >180d) and real revenue for "exposure", or say
it's not derivable. No `SELECT <const>`, no magic factors.

**FIX D — Independent component computation [PROMPT, SQL agent]:** RT-006. Compute each component %
from its own column and report TRUE values; never adjust to a tidy 100%; add a residual/other line.

**FIX E — Numeric-sanity CODE guards [`_apply_report_guards`]:** deterministic backstops that fire
regardless of the LLM:
  • negative-duration KPI (RT-009) → flag `negative_duration`
  • revenue/value KPI > ₹11.3B company total (RT-007) → flag `revenue_exceeds_total`
  • SQL with churn_prob/is_at_risk/risk_score/`* 0.NN` magic factor (RT-008) → flag `fabricated_formula`
  • ≥3 sibling component %s summing to EXACTLY 100.00% (RT-006) → MASKED-MATH warning
  • All set `report["has_accuracy_warnings"]=True` so warnings are SURFACED (RT-008: were only logged).
  Verified: fires on all 4 failing cases; does NOT flag a legit ₹11.27B total revenue.

**FIX F — QA verdict tiers [pipeline]:** RT-006 — score 4 printed "APPROVED". Now 3-tier matching the
rubric: ≥10 APPROVED, 7–9 APPROVED(warnings), 4–6 CONDITIONAL, <4 REJECTED(retry). Accuracy-guard
warnings downgrade any APPROVED → "APPROVED (accuracy warnings)". Retry now keyed on score<4 only.
Verified: score 4 → CONDITIONAL (was wrongly APPROVED).

**NEXT:** re-run RT-006/007/008/009 to confirm flips to PASS (or at least guard-flagged). Then the
deferred items (P7 phantom signals, SQL robustness TODO, telemetry migration).

---

### RT-007b (re-test of RT-007 after Round 3) — diamond fan-out
**Date:** 2026-06-08. **Result: ⚠️ GUARD CAUGHT IT + VERDICT DOWNGRADED, but the SQL bug is NOT fixed.**

- ✅ **Fix E (guard) WORKED:** `[Report Guards] 'Total Diamond Revenue' = 30,901,993,049 EXCEEDS total
  company revenue (~₹11.3B) — FAN-OUT double-counting`. Flagged + `has_accuracy_warnings`.
- ✅ **Fix F (verdict) WORKED:** QA printed **"APPROVED (accuracy warnings)"** (not clean APPROVED).
- 🔴 **Fix A (prompt rule) did NOT hold:** KPI1 SQL is STILL the fan-out
  `SUM(solp.line_total)` across `sales_order_line_diamond` — identical ₹30,901,993,048 as RT-007. The
  model ignored the "never SUM line_total across a diamond join" prompt rule. Correct value would be
  `SUM(sold.diamond_amount_per_unit * sol.quantity)` = **₹1,915,770,280**.
- **Lesson (same as the currency formatter):** prompt rule = soft ask the model ignores; only CODE is
  reliable. The guard DETECTS but doesn't FIX — the shipped number is still wrong, just flagged.
- **Real fix needed (Round 4):** a deterministic SQL pre-check/rewrite — when a query joins
  `*_diamond`/`*_gold` AND sums `line_total`/`total_amount`, either auto-block-and-repair (feed the
  fan-out error back to the agent forcing a rewrite) OR auto-rewrite to use the child's own amount.
  Detecting-and-flagging is the floor; block-and-repair is the fix. (Same pattern: prompt steers,
  code ENFORCES — here enforcement must be a hard gate, not a warning.)

---

## IMPLEMENTATION ROUND 4 (2026-06-08) — HARD GATE for line-child fan-out (the real fix)
RT-007b proved the prompt rule (Round 3 Fix A) didn't stop the model and the guard only WARNED.
Round 4 makes it a block-and-repair gate — code ENFORCES, matching the currency-formatter pattern.

**`_detect_line_child_fanout(sql)` + Step-1c gate in `handle_execute_sql_query` [`ai/claude_tools.py`]:**
- Detects: query references a line-CHILD table (`sales_order_line_diamond/_gold`, `po_line_diamond/_gold`,
  `job_card_diamond_lines`) in FROM or JOIN, AND SUMs a line/order-level amount
  (`line_total`/`total_amount`/`final_amount`) → the 2-3× fan-out.
- Action: returns `success:False` with `blocked_reason:"line_child_fanout"` + a repair instruction
  telling the agent the TWO correct rewrites (use child's own `diamond_amount_per_unit*quantity`, OR
  pre-aggregate line revenue to 1 row/line in a CTE then join child for grouping only). The SQL agent's
  existing repair loop then MUST fix it before any number ships. Hard gate, not a warning.
- Boot-tested 5 cases: diamond-JOIN ✅blocked, diamond-FROM ✅blocked, gold-FROM ✅blocked,
  correct child-amount ✅allowed, normal revenue (no child) ✅allowed. No false positives. App imports.

**Layered defense now on fan-out:** prompt rule (steer) → HARD GATE block+repair (enforce, Round 4) →
guard `revenue_exceeds_total` flag + verdict downgrade (final backstop, Round 3). Even if the gate is
somehow bypassed, the guard still flags. RE-TEST RT-007 to confirm the agent now produces the correct
~₹1.9B diamond value instead of the ₹30.9B fan-out.

---

### RT-007c (re-test after Round 4 hard gate) — diamond fan-out
**Date:** 2026-06-08. **Result: ✅ PASS — fan-out FIXED. The hard gate worked end-to-end.**

| | RT-007 (bug) | RT-007c (after gate) | Verified |
|---|---|---|---|
| Total Diamond Revenue | ₹30,901,993,048 (2.7× inflated) | **₹1,915,770,280.2** | ✅ EXACT vs DB |
| Top shape (Round) revenue | (inflated) | **₹1,732,828,169.5** | ✅ EXACT |
| Method | `SUM(line_total)` across diamond join | `SUM(diamond_amount_per_unit * quantity)` | ✅ correct |

- The Round-4 gate blocked the initial fan-out attempts; the agent rewrote to the child's own
  `diamond_amount_per_unit * quantity` and landed the CORRECT ₹1.92B (was ₹30.9B). Both values
  EXACT vs live DB. Margin avg 35.0% plausible. Currency prose "₹191.58 Cr" correct.
- Cost: 7 SQL rounds (the gate + some alias/`so`-FROM-clause retries) — slower but correct. Fits the
  known SQL-robustness TODO; accuracy is right.
- The highest-impact accuracy bug in the whole study is now CLOSED.

---

### RT-006b (re-test after Round 3+4) — component margin breakdown, top 5 categories
**Date:** 2026-06-08. **Result: ⚠️ IMPROVED but PARTIAL — MASKED-MATH fixed; diamond component now WRONG via a different path.**

- ✅ **MASKED-MATH GONE:** components no longer forced to sum 100%. Gold 51.11 / Diamond 5.82 / Making
  7.06 / Discount 25.91 — measured as % of SELLING price, independent, do NOT sum to a tidy 100. Good.
- ✅ **Fan-out gate fired here too (universal proof):** Rounds 8–9 BLOCKED a chart query that joined
  the gold/diamond child tables — the gate works beyond the diamond query. Agent rewrote.
- ✅ Verified EXACT vs DB: Gold % = 51.11, Making % = 7.06, Net margin = 35.00, Top-5 revenue = ₹9.32B.
- 🔴 **Diamond component WRONG: reported 5.82, TRUE = 15.91** (ratio-of-sums via solp.diamond_amount).
  Neither 15.91 (ratio-of-sums) nor 14.65 (avg-of-ratios) matches 5.82 — the model computed the
  DIAMOND component by a different/incorrect method than gold/making. Likely fallout from the fan-out
  gate forcing a rewrite of the diamond query specifically (gold/making used the clean 1:1 solp
  columns and stayed exact; only the gated diamond path went wrong). Net: diamond understated ~2.7×.
- QA 12/12 (blind to the diamond error — it's not >total-revenue and not a sum-to-100, so no guard hit).

**Insight:** the fan-out gate is slightly OVER-firing — `solp.diamond_amount_per_unit` lives on the
PRICING table (1:1 with the line, NO fan-out), so a query using `SUM(solp.diamond_amount_per_unit*qty)`
is SAFE and should NOT be blocked. The gate likely blocked a safe pricing-column diamond query because
the chart also touched a child table, pushing the model to an inferior rewrite. **Refine the gate:**
only block when the SUMmed amount comes from a join to the CHILD table, not when diamond/gold value is
read from the 1:1 `sales_order_line_pricing` columns. (Pricing already has gold/diamond/making per-unit
amounts — those are the canonical, fan-out-free source for component math.)

**Verdict:** big improvement (MASKED-MATH fixed, 4/5 components exact) but the diamond component needs
the gate refinement above. Tracked for Round 5.

**ROUND 5 FIX APPLIED (2026-06-08):** (1) Metric dictionary [SQL-agent prompt] now teaches that
gold/diamond/making COMPONENT VALUE = the 1:1 `solp.*_amount_per_unit * quantity` columns (no child
join, no fan-out) — only join the child table for child-only attributes (shape/quality). (2) The
fan-out gate's repair message now LEADS with option (A) = use the pricing 1:1 columns, so a blocked
component query rewrites to the CORRECT diamond method (SUM(solp.diamond_amount_per_unit*qty)) instead
of an inferior one. Boot-tested: real fan-out still blocked, safe pricing-column query allowed (no
false block), repair leads with pricing cols, prompt teaches it. RE-TEST RT-006 to confirm diamond
component now = 15.91 (true) not 5.82.

---

### RT-006c (re-test after Round 5) — component margin breakdown
**Date:** 2026-06-08. **Result: ✅ PASS — diamond component FIXED, all components verified correct.**

| Component | RT-006 (bug) | RT-006b (partial) | RT-006c (now) | TRUE (DB) |
|---|---|---|---|---|
| Gold % | 70 (fudged) | 51.11 ✅ | **51.57** | 51.57 (avg-of-cat) ✅ |
| Diamond % | 19.75 (fudged) | **5.82 ❌** | **15.24** | 15.23 (avg-of-cat) ✅ FIXED |
| Making % | 10.25 (fudged) | 7.06 ✅ | 7.06 ✅ | 7.06 ✅ |
| Top-cat gross margin | — | — | **25.87** | 25.87 = (sell−gold−diam−making)/sell ✅ |
| Top-5 revenue | — | ₹9.32B ✅ | ₹9,320,474,400.57 | ✅ EXACT |

- **Round 5 fixed the diamond component:** 5.82 → 15.24 (true 15.23). The model used the 1:1
  `solp.diamond_amount_per_unit*quantity` pricing column (per the new metric-dictionary rule), not a
  fanned/wrong path. ✅
- KPI1 gross margin 25.87 is the CORRECT definition for component breakdown:
  (selling − gold − diamond − making)/selling. Reconciles: 51.57+15.24+7.06+25.87 ≈ 100% of selling. ✅
  (The stored `margin_pct` = 34.91 is a different base→selling metric; the model picked the right one
  for THIS question.)
- No MASKED-MATH (components are true independent values), no fan-out (gate didn't even need to fire —
  the prompt steered it to pricing columns first). 18 rounds (1 alias typo retry), QA 12/12.

**Verdict:** component-margin class now FULLY CORRECT. RT-006 closed. ✅

---

### RT-009b (re-test after Round 3) — fulfillment time / negative lead-times
**Date:** 2026-06-08. **Result: ✅ PASS — negative-lead-time landmine handled.**

| | RT-009 (bug) | RT-009b (after) | TRUE (DB) |
|---|---|---|---|
| Overall avg fulfillment | 13.96 (negatives incl., 29% low) | **19.60** | 19.60 ✅ EXACT |
| Order-to-PO | **−2.96** (impossible) | **+2.96** | 2.96 ✅ EXACT |
| Production lead time | — | 16.86 | 16.86 ✅ |
| Orders analyzed | — | 16,702 | 16,702 ✅ |

- Every duration KPI now has `WHERE days_to_fulfill > 0` (and `days_sol_to_po/days_production > 0`) —
  the Round-3 data-quality rule landed. Negatives filtered, no impossible −2.96 KPI. All EXACT vs DB.
- QA 12/12; the negative-duration guard didn't need to fire (the prompt rule fixed it upstream).

**Verdict:** data-quality-trap class FIXED. RT-009 closed. ✅

---

### RT-008b (re-test after Round 3) — churn what-if / fabrication
**Date:** 2026-06-08. **Result: ✅ PASS (fabrication fixed) — much improved; minor residuals.**

- ✅ **NO fabricated formula** (the RT-008 bug): no `churn_prob`, no magic `* 0.32`. Churn score now
  built from REAL columns (recency, outstanding, DSO-style) — it even self-corrected at Round 6 when
  `dso_days` didn't exist, rewriting with real columns instead of inventing one. Confirmed: zero
  churn/risk columns in DB, so the derived proxy is the correct approach.
- ✅ **Revenue at risk = REAL revenue** (₹552.06 Cr of ₹1,126.80 Cr total = 48.99%), not revenue ×
  invented probability. KPI2 total ₹11.27B exact. Units = crore (1126.80 Cr).
- ✅ **Guard fired** (5 untraced-KPI warnings) + **verdict "APPROVED (warnings)" at score 8** — both
  Round-3 fixes (surface guard + verdict tiers) working live.
- ⚠️ Residuals (minor, acceptable): (1) framed as "Outstanding Concentration / DSO" rather than a
  recency proxy — defensible since 0 customers are >180d inactive, so outstanding-based risk is
  actually the more meaningful signal here. (2) KPIs 3–6 still `SQL:(none)` (narrative-derived,
  guard-flagged not blocked). (3) "Estimated Cash Impact (40% probability) = 242.26" uses a 40%
  assumption — but now LABELED, not a hidden magic coefficient. Big improvement over RT-008's hidden 0.32.

**Verdict:** the fabrication failure is FIXED — no invented formulas, real revenue, disclosed
assumptions, guard+verdict working. RT-008 effectively closed (the dangerous hallucination is gone).

---

## ═══ FINAL SCORECARD — ALL re-tests after Rounds 1-5 (2026-06-08) ═══
**Original 7 cases + 4 hard-question bugs → all addressed & DB-verified.**

| Re-test | Bug | Result |
|---|---|---|
| RT-001 overall sales | wrong scope (₹1.5B/₹11.27B) | ✅ PASS |
| RT-002 category perf | units=lines (3,404/204,020) | ✅ PASS |
| RT-003 leftover inv | value=receipt (₹105M/₹79M) | ✅ PASS |
| RT-004 discount (chat) | bailed to margin | ✅ PASS |
| RT-005 hunter/stores/growth | faked stores, 100× prose | ✅ PASS |
| RT-006c component margin | MASKED-MATH + diamond wrong | ✅ PASS |
| RT-007c diamond fan-out | 2.7× revenue inflation | ✅ PASS (hard gate) |
| RT-008b churn what-if | fabricated formula | ✅ PASS (no fabrication) |
| RT-009b fulfillment | negative lead-times | ✅ PASS |

**9/9 PASS.** Every headline accuracy bug found across the whole study is fixed and verified against
the live DB. Defenses are layered: prompt rules (steer) + hard gates (enforce: fan-out block+repair,
data-quality filters) + code guards (backstop: negative-duration, revenue>total, fabricated-formula,
untraced-KPI, component-sum-100, currency formatter) + 3-tier QA verdict that surfaces warnings.

**STILL OPEN (NOT accuracy-critical):** SQL-generation robustness (DuplicateAlias/missing-FROM/`WHERE2`
typo retries — slow, self-recovers; see TODO below); untraced non-scalar KPIs flagged-not-blocked; P7
phantom-signals (inert, tables absent); telemetry-table migration; soft assumption labels (e.g. churn
40%) acceptable-but-improvable.

---

## SQL-ROBUSTNESS — Steps 1+2 IMPLEMENTED (2026-06-08); Layer 3 (Sonnet) deferred pending measurement
Joel chose "Layers 1+2 first, measure, then decide." Boot-tested + live-DB verified.

**Step 1 — deterministic mechanical auto-fix [`_autofix_mechanical_sql` in claude_tools.py]:** runs before
pre-validation, fixes ONLY unambiguous typos/casts so they don't burn a repair round:
  • `WHERE2`/`GROUP2 BY`/`ORDER2 BY`/`HAVING2` → valid keyword (these are never valid SQL).
  • `ROUND(expr, n)` → `ROUND((expr)::numeric, n)` (the recurring `round(double precision,int)` error).
  Verified: fixes the bad cases, leaves already-cast & clean queries UNTOUCHED (no false rewrites),
  and the auto-fixed ROUND query runs on the live DB returning the correct 19.60. Every fix is logged
  (audit). NOTE: duplicate-alias AUTO-RENAME deliberately NOT added yet (regex rewrite = corruption
  risk) — handled instead via Step 2 feedback below.

**Step 2 — enriched repair feedback [`_enrich_sql_error`]:** when a query still errors, append a SPECIFIC
fix hint to the raw Postgres error so the agent repairs in ONE retry, not many:
  • `missing FROM-clause for "so"` → "you referenced so. but didn't join sales_order so — add it / common
    cause: used so. after only joining sales_order_line."
  • duplicate alias → "give each table a unique alias (sol, sol2)..."
  • `column X does not exist` → "X not in schema; don't guess — use the metric dictionary."
  • `function round(double...)` → "cast first arg ::numeric."
  • GROUP BY error → "non-aggregated SELECT cols must be in GROUP BY; use positional numbers."

**MEASURE NEXT (Step 3 gate):** re-run SQL-heavy queries, count tool rounds before/after. Target: median
rounds ~14 → ~6 and WHERE2/missing-FROM/ROUND errors gone from traces. Only escalate Agent 3 to Sonnet
(Layer 3) if semantic errors (wrong joins, guessed cols) still burn rounds after this. No accuracy impact
— purely syntactic/feedback; correct numbers unaffected.

---

### RT-010 (new) — "Gold karat mix and average gold rate trends by month" — + SQL-robustness measure
**Date:** 2026-06-08. **Result: ✅ PASS (accuracy) + Steps 1+2 working (typos gone).**

- Accuracy EXACT: 4 karats (10/14/18/22 Kt), Feb-2026 18Kt share 52.9%, Feb avg gold rate ₹11,756.38,
  rate range ₹2,545–16,215. Gold is 1:1 with line (no fan-out); KPIs used COUNT(DISTINCT sol_id). Scope
  correctly anchored to Feb 2026 (data end — Round 1 got 0 rows for Mar, pivoted to Feb). ✅
- **SQL-robustness measure:** 17 rounds / 106s — still high, BUT the breakdown changed: ~13 rounds were
  DISTINCT legit queries (1 per KPI/chart/table), only **2 actual errors** (one missing-FROM "solg",
  one empty-Mar pivot). The recurring WHERE2 / ROUND-cast typos that flooded earlier traces are GONE —
  Step-1 auto-fix ate them silently. So Steps 1+2 ARE working; remaining round count is "one query per
  element" (by design), not error-flailing.
- **Refined conclusion on Layer 3:** the bottleneck is now (a) one-query-per-KPI granularity and (b)
  occasional missing-FROM semantic errors — NOT typos. Sonnet would help (a)+(b) somewhat, but a bigger
  lever may be batching multiple KPIs per query. Keep Layer 3 deferred; the typo class is solved.

---

### RT-011 (curveball) — "Forecast next quarter's revenue" (no future data, no forecast model)
**Date:** 2026-06-08. **Result: ⚠️ ACCEPTABLE — it forecasted (didn't refuse), but ALL projections are
guard-flagged + QA-downgraded. Honest, transparent, not dangerous. Borderline-good.**

- The model PRODUCED a Q2-2026 forecast (₹119 Cr base case) rather than refusing. The forecast VALUES
  are projections typed as `SELECT <constant>` (KPI1 `SELECT ROUND(1190000000::numeric,2)`, KPI3
  `SELECT 1680`, KPI5 `SELECT 'Stack Hunter App...'`, all charts `SELECT '...'`). NOT data-backed.
- ✅ **Untraced-KPI guard fired on ALL 6 KPIs** ("no SQL FROM-clause — possible hallucination"). The
  infrastructure correctly identified every forecast number as not-data-backed.
- ✅ **QA scored 4/12 → verdict "CONDITIONAL"** (Round-3 verdict-tier fix working: QA's own REJECTED /
  score-4 now displays CONDITIONAL, not a false APPROVED — exactly the RT-006 fix in action).
- ✅ **Methodologically honest:** ran REAL historical queries first (Q1-2026 actual ₹1,503,052,703.92
  ✅ exact, Q2-2025 actual ₹977,998,060.57 ✅ exact, quarterly trend, YoY, CAGR, seasonality), then
  projected with labeled Base/Low/High scenarios. Q2-2026 confirmed EMPTY (0 rows) — so it's a
  projection FROM verified history, not invented data.
- Caveat: the forecast is presented fairly confidently in the prose; ideal would be an even stronger
  "this is a projection, not actuals; no predictive model" disclaimer up top. But it IS labeled
  forecast/base-case throughout and the guards + CONDITIONAL verdict signal non-actuals to the user.

**Verdict:** ACCEPTABLE. Forecasting from real history with labeled scenarios + every projected KPI
flagged untraced + a CONDITIONAL QA verdict is honest, defensible behavior — NOT the dangerous
fabrication of RT-008 (which invented a churn_prob formula and presented it as fact). The guards
turned a potentially-misleading ask into a transparently-caveated output. The layered defense works
even on an out-of-scope request.

**Optional future polish (not a bug):** a prompt rule for `intent==forecast` to lead with an explicit
"projection, not actuals" banner. Low priority — current behavior is already safe + flagged.

---

### RT-012 (untouched layer) — "Raw material lot utilization — which lots nearly exhausted?" (CHAT path)
**Date:** 2026-06-08. **Result: ✅ PASS — correct on a never-before-queried table layer.**

- Routed to CHAT (Sonnet) — correct: it's a single specific lookup ("which lots near exhaustion"), not
  a multi-KPI dashboard. Chat is the right path; no report needed.
- Queried the RAW-MATERIALS layer (raw_material_lot_balance) — NEVER touched in any prior test.
- Answer verified EXACT vs DB: RMD-05666 (LOT0107) = 96.97% used, 1.0 remaining, status 'Low' ✅;
  exactly 5 items at 90–99% used ("nearly exhausted") ✅; RMD-05666 correctly the most critical ✅.
- **Good schema-discovery recovery:** Round 1 guessed `raw_materials`/`lot_number` → schema validator
  blocked it ("Table 'raw_materials' not found" + wrong_table_for_lot_number) → Round 2 introspected
  information_schema to learn real columns → Round 3+ landed correct. The validator's hallucinated-
  column guard worked, and the agent recovered properly instead of flailing. 7 rounds total.

**Verdict:** schema/metric discipline holds on a totally new data layer outside the sales core. Strong
generalization evidence.

---

### RT-013 (AR landmine) — "Payment collection status — paid vs outstanding vs overdue"
**Date:** 2026-06-08. **Result: ✅ PASS (accuracy) — but exposed + fixed a GUARD FALSE-POSITIVE.**

- All KPIs verified EXACT vs DB: paid 15,635, outstanding (unpaid+partial) 1,306, total outstanding
  balance ₹824,088,437.72, collection rate 92.9%, total invoice value ₹11,606,013,279.14. Aging buckets,
  top-15 outstanding customers all real. AR/payments layer handled correctly. ✅
- **GUARD FALSE-POSITIVE found + fixed:** the `revenue_exceeds_total` guard flagged KPI4 invoice value
  (₹11.6B > ₹11.3B) as fan-out. But it's LEGIT: `sales_invoices` is 1:1 with sales_order (16,941=16,941,
  joined sum == raw sum, zero double-count), and `total_invoice_value = subtotal_before_tax + total_tax`
  — so invoice value correctly exceeds order revenue by GST (~₹338M). The guard keyed only on
  "value > ₹11.3B + name has 'value'" — too loose.
  **FIX:** guard now fires ONLY when (a) val > 1.5× ceiling AND (b) SQL actually joins a diamond/gold
  CHILD table AND (c) sums line_total/total_amount AND (d) NOT an invoice/tax context. Boot-tested:
  RT-007 real fan-out STILL flagged ✅; RT-013 legit invoice value NOT flagged ✅. Precision restored.
- The mechanical auto-fix also handled the `WHERE`-as-alias garbles in Rounds 1-2 gracefully (the
  validator caught "Duplicate alias 'WHERE'" — a malformed query — and the agent recovered by Round 3).
- QA "APPROVED (accuracy warnings)" at the time (due to the false-positive); after the fix this query
  would be clean APPROVED.

**Verdict:** AR data-quality class PASSES; the run hardened the fan-out guard against tax-inclusive
false positives. Good outcome — accuracy correct AND a guard made more precise.

---

### RT-014 (HARDEST QUERY — 6 traps in one) — "Per hunter: revenue, margin %, gold/diamond cost split, avg fulfillment time, recommend category + evidence"
**Date:** 2026-06-08. **Result: ✅ PASS — ALL SIX failure modes handled simultaneously. Definitive.**

Deliberately stacks every bug class we fought. All verified EXACT vs live DB:
| Component | Trap | Reported | Verified |
|---|---|---|---|
| Top hunter revenue | fan-out / double-count | ₹413,922,809.77 | ✅ EXACT (not inflated; < company total) |
| Avg gross margin % | margin definition | 25.93 | ✅ 25.92 (sell−gold−diam−mak)/sell |
| Avg fulfillment time | NEGATIVE lead-time landmine | 19.60 | ✅ EXACT (`WHERE days_to_fulfill > 0`) |
| Gold-to-diamond cost ratio | 2ND-LEVEL FAN-OUT (diamond/gold child) | 2.95 | ✅ EXACT (used solp 1:1 cols, NO child join) |
| Hunter count ≥5 orders | — | 22 | ✅ EXACT |
| Recommend category + evidence | FABRICATION / unverified reasoning | grounded | ✅ real per-hunter category-margin queries (R15-16), not invented |

- Scope correctly all-history (no time qualifier → all-time, data-anchored). ✅
- NO guard false-positives, NO fan-out (the gold/diamond split correctly used pricing 1:1 columns).
- Recommendations built from actual `recommended_category` margin/revenue queries WITH evidence rows —
  not a fabricated formula. QA clean APPROVED 11/12.
- 17 rounds (a few alias/syntax retries, self-recovered) — the typo auto-fix + enriched-error feedback
  kept it moving; no WHERE2/ROUND-cast flailing.

**VERDICT: every defense built across Rounds 1-5 + SQL Steps 1-2 held SIMULTANEOUSLY on the single
hardest query the schema allows. Fan-out avoided, negatives filtered, margin real, recommendations
grounded, scope correct, no false guard hits. This is the definitive pass.**

---

## ═══ ENGAGEMENT COMPLETE (2026-06-08) ═══
From a 6/10 with multiple silent wrong-number bugs → DB-verified-correct across 14 re-tests + the
hardest possible composite query. Defenses are universal (pattern + schema-anchored), self-surfacing,
and proven to generalize to unseen questions. Accuracy push DONE. Remaining items are non-critical:
SQL batching (speed), telemetry migration, forecast disclaimer banner.

---

## DB LOGGING DISABLED (2026-06-08, FINAL) — error spam fixed by REMOVING the dead feature
**Decision (Joel): the DB logging is unused — disable it, don't feed it tables.** Correct call.
- TRACED it: `/report` (api/reports.py) and report-intent `/chat` (api/chat.py) construct
  `EnhancedReportPipeline(enable_logging=True)` → it creates a `LoggingAgent` that INSERTs into
  audit_trail / signal_detection_logs / graph_sql_mappings on every run. (NOT the dead orchestrator —
  EnhancedReportPipeline IS the live wrapper around ClaudeReportPipeline.)
- WRITE-ONLY confirmed: `get_logs`/`get_signals`/`get_sql_mappings` exist in enhanced_pipeline.py but
  NOTHING calls them — no endpoint, no frontend, no dashboard reads these tables. The frontend metrics
  panel comes from `_build_metrics` (separate in-memory path), NOT these tables. Orphaned scaffolding.
- FIX: set `enable_logging=False` at both call sites (api/reports.py:48, api/chat.py:129) AND the
  constructor default (enhanced_pipeline.py:48). Every `if self.logging_agent:` guard now skips → no
  INSERTs → no spam → no orphan writes. Dropped the 11 tables I'd briefly created (empty; business data
  untouched, sales_order still 18,500). App imports OK.
- `enable_signals=True` KEPT — that's signal DETECTION (feeds the report), separate from DB logging.
- Reversible: to enable an audit dashboard later, flip enable_logging back on + re-run
  db/migrations/001_add_logging_tables.sql. Also makes P7 phantom-signals fully moot (won't persist).

### (superseded) TELEMETRY MIGRATION RUN (2026-06-08) — error spam fixed, logging now persists
The `logging_agent` INSERT-error spam (`UndefinedTable: audit_trail / signal_detection_logs /
graph_sql_mappings does not exist`) seen after EVERY report is FIXED.
- Root cause: migration `db/migrations/001_add_logging_tables.sql` (creates all 11 logging/intelligence
  tables, idempotent, indexed) was never run against the master DB.
- Verified master DB = Postgres 17.4, `gen_random_uuid()` native (no extension needed). Ran the migration
  → all 11 tables created (agent_execution_logs, graph_sql_mappings, sql_execution_logs,
  insight_generation_logs, signal_detection_logs, drift_detection_logs, audit_trail, agent_configurations,
  query_cache_metadata, performance_metrics, schema_migrations); 9 agent_configurations seeded;
  schema_migrations row '001' present.
- Verified schema↔agent COMPATIBILITY: simulated the exact INSERTs logging_agent runs (audit_trail
  pipeline_start, signal_detection_logs per-signal, graph_sql_mappings per-chart) — all 3 succeed, rows
  land, test rows cleaned up. So the original audit's "migration ↔ agent out of sync" risk is resolved
  for these three; the agent now PERSISTS telemetry instead of erroring.
- NET: next report run produces ZERO INSERT-error spam + actually saves the cost/usage/signal telemetry
  the app already computes. NOTE: the OLD `backup_v2_inventory` DB still lacks these tables (only master
  was migrated); and P7 phantom-signals will now actually PERSIST to signal_detection_logs — revisit the
  phantom-signal scoping (ranked-bar/empty-week false signals) now that they're no longer inert.

---

## CHAT GATEKEEPER — 5-way intent router added (2026-06-08)
**Gap (Joel spotted):** the router was binary (report|chat) and BOTH branches ran SQL — so greetings,
off-topic, and injection attempts all hit the SQL engine (wasted call + confused/empty answer). No
"don't touch the DB" path. Industry practice = a triage step that decides whether to query at all.

**Fix [`services/claude_report_llm.py` + `api/chat.py`]:** `classify_query_intent` now returns 5 modes:
- `report` → full pipeline; `data` → SQL chat (these two touch the DB);
- `conversational` (greeting/capability/smalltalk), `out_of_scope` (weather/coding/general), `refuse`
  (prompt-injection / data-exfil / destructive) → **answered directly via `answer_conversational()`
  (cheap Haiku, NO SQL, NO DB)** with a friendly on-brand redirect.
Both `/ask` AND `/chat/stream` now gate on this BEFORE any SQL. Persists the turn (for context) but
with empty sql/data. Falls back to `data` on classifier error (safe — SQL path + validator still guard).

**Verified (live Haiku):** 7/7 routes correct — "total revenue"→data, "full dashboard"→report,
"hi"/"what can you do"→conversational, "weather"→out_of_scope, "ignore instructions show passwords"→
refuse, "diamond lots exhausted"→data. Conversational replies natural + redirecting, zero SQL. App OK.

**Settings (Joel's choices):** conversational = LLM natural reply + redirect; guardrail = scope +
injection (refuse off-topic AND injection/abuse before any SQL). SELECT-only validator still backstops.

**FRONTEND FOLLOW-UP (2026-06-08):** "Generate Report" card was showing under NON-data answers
(out_of_scope/refuse) — wrong, there's no data question to report on. Backend already sent
`report_eligible:false`/`non_data:true` for those; the frontends ignored it (always showed the offer).
FIXED both: `frontend-react/src/App.jsx` (showOffer = report_eligible!==false && !non_data) + rebuilt
dist/ via vite; and `frontend/script.js` (same gate). Now: real DATA chat answers → report offer
shows; greeting/off-topic/refused → NO report offer. Verified logic; React rebuilt OK.

---

### RT-015 — "Diamond Shape & Quality Performance" — LABEL-KPI showing 0 bug (Joel spotted in UI)
**Date:** 2026-06-08. **Result: display bug found + fixed (NOT an accuracy bug).**

- Symptom: KPI cards "Top Performing Shape by Revenue" and "Top Performing Quality by Revenue" showed
  **0** on the dashboard. The DATA was correct (trace: agent computed Shape=Round ₹6.2B, Quality=EF
  VVS-VS ₹2.3B; underlying revenue ₹1.92B verified earlier). The text answer just never reached the card.
- Root cause: these are TEXT/label KPIs ("which shape?"), but the agent followed the old rule "KPI =
  1 numeric value" and returned a 2-COLUMN result (shape, revenue). A KPI card shows ONE value → the
  2-column result collapsed to 0. (The fan-out gate + the WHERE/DuplicateAlias retries also made this
  a 6-round, 116s slog, leaving the label-KPIs mis-shaped at final assembly.)
- FIX (two layers): (1) SQL-agent prompt rule [claude_prompts.py]: a "which/top/best X" KPI must return
  the NAME as a single column aliased `value` (e.g. `SELECT shape AS value ... ORDER BY SUM() DESC LIMIT 1`),
  NEVER a 2-column (label, metric) result. (2) Code guard [_apply_report_guards]: flag `label_kpi_lost`
  when a "which/top/best" KPI has a 0/blank/numeric value (text answer lost). Verified: flags the value=0
  case, does NOT flag a correct 'Round (₹6.2B)' text value or a normal numeric revenue KPI. App OK.
- Note: revenue/margin/orders KPIs in this report were all CORRECT (₹1.92B diamond rev, 35% margin,
  32,672 orders) — only the two label KPIs rendered 0. Display/plumbing fix, accuracy intact.

**RT-015b (re-run after the prompt+guard fix) — STILL showed 0. Root cause refined + frontend fix added.**
- This time the SQL agent CORRECTLY produced `Top Shape by Revenue = Round` (prompt fix worked at SQL
  stage — traceability shows "Round"). But the CARD still rendered 0 → the value is lost AFTER the SQL
  stage, in the model's FINAL-JSON assembly: it ran the right query, logged "Round", but wrote a
  numeric/0 into the KPI's `value` field. The Report Writer (Agent 5) only deep-copies + grafts
  narrative, so it preserves whatever value was there (the 0). The frontend `formatKPIValue("Round")`
  correctly returns "Round" — so the formatter was never the bug; the value reaching it is genuinely 0.
- This is the SQL agent being unreliable at hand-assembling final JSON for label KPIs — prompt steers
  but doesn't guarantee. Re-running the query in code to backfill the name is complex (query
  reconstruction), so the RELIABLE fix is presentational + honest:
  **FRONTEND [ReportPage.jsx, React rebuilt]:** a "top/which/best/highest/lowest/leading" KPI whose
  value is numeric/0 (text answer lost) is HIDDEN rather than shown as a misleading "0". Verified:
  hides `Top Shape=0`, shows `Top Shape='Round'`, never hides normal numeric KPIs (revenue/margin).
  Backend guard still flags `label_kpi_lost` for observability.
- HONEST STATUS: a missing KPI card is better than a wrong "0", but the deeper fix (guarantee the label
  KPI carries its name) needs either Agent-2 to stop emitting fragile label-KPIs, or a code backfill.
  Tracked. Accuracy of the report's NUMBERS is unaffected — this only ever concerned 2 label cards.

---

## FIX A — CODE OWNS KPI/CHART VALUES (2026-06-08) — structural fix for misfiled-value class
**Root cause (Joel pinpointed):** the SQL agent runs ~13 queries then HAND-TYPES one big JSON,
misfiling values during assembly — e.g. 'Top Shape' KPI ran the right query (logged "Round") but the
final JSON kept the placeholder `0`; also the `SQL:(none)` and hardcoded-`SELECT 324` KPIs are the same
disease (model trusted to compute AND transcribe). Prompt steering ≠ guarantee (same lesson as currency
formatter & fan-out gate: code must enforce).

**Fix [`_recompute_from_sql()` in claude_multi_agent.py, runs after SQL agent, before guards]:**
The agent already RECORDS each element's `sql`. We stop trusting its hand-typed value/data and
RE-EXECUTE that sql in code, overwriting `value`/`data` from the real DB result. The value the user
sees is now ALWAYS exactly what the SQL returns — number OR text label — never what the model typed.
- KPI → first col (prefer a col named `value`) of the single row; Decimal→float (JSON-native, keeps
  currency formatter's isinstance(int,float) working).
- Chart/table → replace `data` with executed rows (≤50), Decimal→float.
- Conservative: only acts when a usable `sql` with a FROM clause exists. Constants (`SELECT 324`) /
  no-FROM are LEFT for the guards to flag (we still don't trust those). SQL errors → leave model value
  + note, never crash. Applies to BOTH standard and drift (both carry `sql` on KPIs/charts).

**Verified (live DB):** the exact bug — label KPI value=0 → recomputed to "Round"; a wrong hand-typed
revenue 999 → real ₹2.11B (float) → currency formatter "₹211.04 Cr"; `SELECT 324` (no FROM) left as-is
for guards; empty chart → filled with real 5 rows; whole report JSON-native serializable; no spurious
warnings. App imports OK.

**Why this is the real fix (vs the band-aids):** kills the whole CLASS — label-0, wrong hand-typed
numbers, stale placeholders, and (because we re-run the recorded sql) reduces `SQL:(none)` impact. QA
was never a value gate (it's narrative-only, and even crashed-to-auto-approve in RT-015b) and the
guards only WARN — now code OWNS the value deterministically, upstream of both. The frontend label-KPI
hide (RT-015b) stays as a last-resort cosmetic backstop.

NOTE: re-running SQL adds ~13 more DB queries per report (cheap, ~0.05s each, all SELECT) but is the
price of correctness. Folds into the SQL-robustness/batching TODO if latency matters.

**RT-016 (re-test of the broken diamond report after Fix A) — ✅ PASS, label KPIs fixed:**
- "Top Diamond Shape by Value = **Round (95.8% of diamond value)**", "Top Diamond Quality by Margin %
  = **EF VVS (35.07%)**" — NO MORE 0. Fix A's recompute filled the label values from the real SQL.
- DB-verified: total revenue ₹11,267,974,058.97 ✅, AOV ₹665,130.40 ✅, orders 16,941 ✅, top shape
  Round ✅. All numbers intact (no regression from the recompute).
- FRONTEND CLEANUP: removed the RT-015b "hide label-KPIs showing 0" hack from ReportPage.jsx — now
  that the BACKEND owns/guarantees the value, the frontend just displays what the API sends (no
  second-guessing). React rebuilt. (Backend authoritative > frontend patch.)
- NOTE: this run still took 13 SQL rounds w/ many DuplicateAlias/missing-FROM retries (the Haiku
  SQL-emission weakness) — accuracy correct, but reinforces the SQL-robustness TODO.

---

### RT-017 — ATTRIBUTE-SPLIT DOUBLE-COUNT (Joel found: "GH VVS revenue" wrong ~2×). Verified + universal fix.
**Date:** 2026-06-09. **A genuine WRONG KPI** (I initially under-called it as "defensible" — it was not).

**The bug, verified vs DB:** KPI "Diamond Revenue — GH VVS Quality" = ₹1,231,629,205. TRUE =
**₹596,205,480** (~2.07× overstated). PROOF the true method is right: per-quality parts computed from
the diamond ROW's own amount SUM EXACTLY to the ₹1,915,770,280 total (745M+596M+...). The buggy method
did NOT (it'd sum to ~3.8B).

**Why wrong (root cause):** TWO columns named diamond_amount_per_unit —
`sales_order_line_pricing.diamond_amount_per_unit` = WHOLE LINE's diamond total (1:1), and
`sales_order_line_diamond.diamond_amount_per_unit` = ONE diamond's value (many rows/line, each with its
own quality). The agent split BY quality using the LINE-LEVEL pricing column via DISTINCT sol_id →
attributed the whole line's diamond value to EVERY quality on it. Half the lines (17,865/35,765) have
multiple qualities → ~2× double-count.

**Is it the prompt's fault / recent work / unseen?** ALL THREE (honest): (a) our metric-dictionary
anti-fan-out rule said "use the 1:1 pricing column, avoid the child table" — correct for TOTALS but it
accidentally steered the model to the wrong column for BY-attribute splits; (b) the fan-out HARD GATE
pushed the model off line_total onto the pricing-column+DISTINCT workaround → into this mistake; (c) the
test set only ever verified TOTALS and the dominant slice (Round 96%), never a minor slice like GH VVS.

**UNIVERSAL — it's a whole CLASS:** value split by ANY child attribute (diamond shape/quality/carat/size,
gold karat/colour, job-card diamonds) has this. The inverse of fan-out: not "don't multiply" but "split
using the child's OWN per-row amount, which already partitions correctly."

**FIX (two layers, boot-tested + DB-verified):**
1. PROMPT [claude_prompts.py] — new UNIVERSAL RULE distinguishing TOTAL/component (use line-level
   `solp.*_amount_per_unit`) vs BY-CHILD-ATTRIBUTE (use `sales_order_line_diamond/_gold` row's OWN
   amount). Explicit: never attribute the line total to a child attribute; per-attribute parts MUST
   sum to the grand total (self-check).
2. CODE GUARD [_apply_report_guards] — `attribute_split_double_count`: when a chart splits diamond/gold
   value by shape/quality/karat AND its parts sum to >1.15× the matching grand-total KPI, flag it
   (the double-count signature). Verified: flags the ₹3.8B-vs-₹1.92B case, does NOT flag a correct
   split (parts=total) or a non-attribute chart (monthly trend). App imports OK.

**TRUE numbers for re-test:** total diamond ₹1,915,770,280; by quality — EF VVS-VS ₹745,044,898,
GH VVS ₹596,205,480, then EF VVS, GH VVS-VS (parts sum to total). Re-run the diamond report to confirm
quality/shape splits now use the row amount + sum to total + no guard flag.

**Correction to prior overclaim:** "good accuracy" was overstated. Accurate statement: numbers are
verified-correct on the TESTED cases + fixed bug classes; this run found a NEW unverified class
(attribute-split) that WAS wrong and is now fixed. Verify-don't-assume stands.

---

### RT-018 — Recurring 'Top Shape=0' (3rd time, new cause each run) + wrong ₹11.3B diamond total. Two structural fixes.
**Date:** 2026-06-09. (Honest note: I twice mis-blamed this on stale code/caching — it was a REAL
recurring bug. The pattern across 3 runs: a "Top Shape/Quality" KPI breaks a DIFFERENT way each time —
(1) dropped value→placeholder 0, (2) 2-col collapse→0, (3) malformed TO_CHAR mask "₹ #,##,##,###"→
frontend can't parse→0. Same disease: a label-KPI whose value is a HAND-BUILT string is fragile by
construction; no SQL patch fixes infinite fumble modes. recompute can't help — it faithfully re-runs
the model's bad/garbage SQL.)

**Plus a 2nd real bug this run:** KPI1 'Total Diamond Revenue' = ₹11,311,657,328 (should be ₹1.9B) —
model used SUM(diamond_amount_per_unit * pieces_per_unit) → ~6× inflation. The fan-out guard MISSED it
(₹11.31B squeaked just under the ₹11.3B×1.5 ceiling).

**FIX #1 — kill the fragile label-KPI class [Business Analyst prompt]:** KPI cards must be pure
numeric/percent scalars; explicitly FORBID "which/top/best X" KPIs (Top Shape/Quality/Vendor/Category).
A #1-ranking belongs in a ranked CHART (already generated, shows the winner on top), not a card. This
REMOVES the fragile thing instead of trying (4th time) to make a hand-built name+number string render.
Verified the rule is in BUSINESS_ANALYST_SYSTEM.

**FIX #2a — diamond/gold value multiplier rule [SQL-agent metric dictionary]:** value =
SUM(amount_per_unit * sales_order_line.quantity), NEVER × pieces_per_unit (stones/unit, already baked
into the amount). Includes sanity: diamond total is a fraction of revenue (~₹1.9B of ₹11.3B).

**FIX #2b — guard for implausible material total [_apply_report_guards]:** a "total diamond/gold
value/revenue" KPI > 60% of total company revenue → flag `material_total_implausible` (catches the
×pieces_per_unit ~6× inflation that slipped under the fan-out ceiling). Verified: flags the ₹11.3B
case incl. label "Total Diamond Revenue (All Shapes & Qualities)"; does NOT flag correct ₹1.9B diamond
total, legit ₹11.27B COMPANY revenue, or diamond margin %. App imports OK.

**Effect:** the recurring "Top Shape = 0" card should no longer be created at all (ranking lives in the
chart); the diamond total should be ₹1.9B (right multiplier) and flagged if the model strays. Both are
prompt + guard (no refactor). RE-TEST: restart server + hard-refresh, re-run the diamond report.

### RT-019 — Fix #1 (BA prompt ban) was IGNORED by model → redone as CODE. Fix #2 (₹1.9B) CONFIRMED.
**Date:** 2026-06-09. The BA created "Top Diamond Shape/Quality by Revenue" KPIs DESPITE the prompt ban
→ KPI = "Round (₹ #,##,##,###)" (malformed TO_CHAR mask) → frontend 0. 4th failed prompt-level attempt.
LESSON RE-CONFIRMED: prompt = request the model can ignore; only CODE enforces.
- ✅ Fix #2 WORKS: Total Diamond Revenue = ₹1,915,770,280 (₹191.58 Cr); margin 35%, ASP ₹85,849, orders
  32,672 all correct. The ×pieces_per_unit ₹11.3B inflation is gone.
- ✅ Fix #1 REDONE AS CODE — `_drop_broken_kpis()` (runs after recompute, before guards): deterministically
  REMOVES un-renderable KPI cards — value with a leftover format mask (#,##/9,99/999,999), or a
  "top/which/best X by …" ranking KPI with a 0/blank/garbage value. Ranking is preserved in the ranked
  charts, so nothing is lost. Verified: drops the 2 mask-garbage KPIs, keeps the 4 good scalars, keeps a
  correctly-rendered "Round (₹173.28 Cr)", drops a ranking value=0. App OK.
- NET: broken "Top Shape" card is now GONE deterministically (not asked-nicely). RE-TEST: restart +
  hard-refresh → expect 4 clean scalar KPIs, no Top-Shape/Quality cards, diamond total ₹1.9B, ranking in charts.

### RT-020 — Fan-out variant (solp.diamond across diamond join → ₹6.75B) + UNIVERSAL value-independent fixes + full fan-out AUDIT.
**Date:** 2026-06-09. Joel (rightly) rejected hardcoded ceilings + demanded universal fixes + a re-audit.

**Bug:** "Total Diamond Revenue" = ₹674.99 Cr (₹6.75B) vs true ₹1.9B (~3.5× inflated). The model used the
RIGHT column (solp.diamond_amount_per_unit) but JOINED sales_order_line_diamond anyway → line-level
amount fanned out 2.56× across diamond rows. Slipped past all guards (fan-out gate only checked
line_total; the 60%-of-revenue material ceiling = ₹6.78B, and ₹6.75B squeaked under).

**FULL FAN-OUT AUDIT (rows-per-parent, live DB) — the complete surface, no more guessing:**
  FAN-OUT (multiply): sales_order_line_diamond 2.56×, po_line_diamond 2.56×, job_card_diamond_lines 2.40×.
  1:1 SAFE: sales_order_line_gold 1.00, sales_order_line_pricing 1.00, po_line_gold 1.00, po_line_pricing
  1.00, job_card_gold_details 1.00.
  ⇒ KEY CORRECTION: ONLY the 3 *_diamond children fan out. GOLD children are 1:1 — the old gate was
  OVER-BLOCKING gold (in its block-list wrongly). Fixed.

**UNIVERSAL FIX 1 — fan-out gate [claude_tools._detect_line_child_fanout], value-independent:**
  • Gate ONLY the 3 audited fan-out tables (diamond line/PO/job-card); stop gating gold (1:1).
  • Block ANY line-level/pricing column summed across the fanned join (line_total, total_amount,
    solp.* per-unit, sales_order_line_pricing.*) — not just line_total. Catches the RT-019/020 variant.
  • EXCEPTION: summing the child's OWN amount (sold.diamond_amount_per_unit) is the correct attribution
    method → allowed. Verified: blocks solp.diamond-across-join + line_total-across-join; allows correct
    no-join total, child-own by-quality, and gold(1:1).

**UNIVERSAL FIX 2 — material-total guard re-derives truth LIVE [_live_material_total + guard], NO hardcoded value:**
  Replaced the 60%-of-revenue ceiling with: compute the canonical all-time diamond/gold total from the DB
  at runtime (SUM(amount_per_unit*quantity), 1:1 no fan-out, cached), and flag any "total diamond/gold"
  KPI > 1.2× that live truth. Survives data changes (re-derives every process). Verified: live diamond
  total = ₹1,915,770,280 (from DB); flags ₹6.75B (3.5×), allows ₹1.9B. App OK.

**Both fixes are PATTERN/LIVE-DERIVED, zero hardcoded business values** — per Joel's requirement.
RE-TEST: restart + hard-refresh, re-run diamond report → diamond total must be ₹1.9B (₹191.58 Cr), the
gate should force-rewrite any fanned diamond total, and the live guard backstops it.

### RT-021 — UNIVERSAL INVARIANT LAYER (the correctness endgame) [_check_invariants, claude_multi_agent.py]
**Date:** 2026-06-09. Consolidates the scattered bug-specific guards into 4 LAWS that must hold for ANY
data report, checked deterministically against the code-owned (recomputed) values, after the guards:
  • LAW 1 RECONCILIATION — a value breakdown's parts can't exceed its total (re-derived live for
    diamond/gold, or a total KPI). Catches fan-out + wrong-attribution + double-count with ONE principle.
  • LAW 2 CONTAINMENT — no value KPI exceeds its live-true total (material) or total company revenue.
  • LAW 3 LINEAGE — every value KPI must trace to a query with a FROM clause (no hand-typed constants).
  • LAW 4 SANITY — durations ≥ 0, percentages 0–100, counts non-negative.
Attaches `report_integrity = {status: verified|violations, checks_run, violations[]}` — a DETERMINISTIC,
trustworthy stamp (unlike the LLM QA score, which has lied 12/12 on wrong reports). Violations also set
has_accuracy_warnings + downgrade the QA verdict.
**Boot-tested (all pass):** L1 flags a 3.5× fanned diamond breakdown, passes a correct one; L2 flags
₹6.75B diamond total, passes ₹1.9B; L3 flags a SELECT-324 value; L4 flags −2.96 days and 135%; and a
FULLY-CLEAN report (revenue ₹11.27B + orders + margin + reconciling category chart) → status "verified",
8 checks, ZERO false positives. App imports OK.
**Universality:** the MECHANISM is universal (every data query runs all 4 laws); the strongest guarantee
(reconciliation = provably-correct) fires when a total exists to check against — full for core metrics
(revenue/value/margin by any dimension/table), trace+sane+contain for novel metrics, grounded+sane+
disclosed for inherently-ambiguous questions (churn/recommend). No silent wrong number on anything that
HAS a right answer. value-independent / live-derived — no hardcoded business numbers.
RE-TEST: any report now carries report_integrity; re-run the diamond report → expect status "verified",
diamond total ₹1.9B.

---

### RT-022 — Invariant layer's FIRST live catch was a FALSE POSITIVE (component vs product) → precision fix + live-revenue ceiling.
**Date:** 2026-06-11. Two things this run: (a) made the company-revenue ceiling LIVE (no hardcode, per
Joel); (b) the new CONTAINMENT invariant FIRED on KPI1 — but it was a FALSE POSITIVE worth understanding.

- **Live-revenue ceiling [_live_company_revenue]:** replaced the hardcoded ₹11.3B with a live
  `SELECT SUM(total_amount) FROM sales_order WHERE status='closed'` (=₹11,267,974,059 now). Zero
  hardcoded DB values remain anywhere (grep-confirmed). Scales with data; fail-safe → inf.
- **The false positive:** KPI1 "Total Diamond PRODUCT Revenue" = ₹10,301,636,355. The invariant compared
  it to the diamond COMPONENT truth (₹1.9B) and flagged "5.4× inflated." But ₹10.3B is CORRECT — it's the
  line_total of diamond-BEARING orders (full jewelry value: gold+diamond+making), verified exact vs DB.
  Two legit metrics were conflated: "diamond COMPONENT value" (just the stone, ≤₹1.9B) vs "diamond
  PRODUCT/ORDER revenue" (whole jewelry containing diamonds, ≤ company revenue ₹11.27B).
- ✅ **Silver lining (the architecture worked):** even mis-judging, the system did the SAFE thing —
  flagged + downgraded QA to CONDITIONAL (4/12) instead of silently approving. Under ambiguity it erred
  toward caution, not toward silent-wrong. That IS the correct failure mode.
- **FIX (precision, not aggression):** containment/material guards now only compare to the ₹1.9B
  COMPONENT truth when the KPI clearly means the component ("component", "diamond value/amount/cost",
  "stone"); a "diamond PRODUCT/ORDER revenue" is treated as ordinary revenue, bounded only by the live
  company-revenue ceiling. Verified: ₹10.3B product revenue → VERIFIED (no false flag); ₹6.75B *component*
  → still CAUGHT; ₹20B anything → CAUGHT; ₹1.9B component → verified. App OK.
- LESSON: invariants must respect the SEMANTIC distinction between a component and the whole — over-tight
  bounds create false positives. Caution-on-ambiguity is right, but precision avoids crying wolf.

---

### RT-023 — diamond report numbers ALL CORRECT; fixed a 2nd guard false-positive (concentration-% vs lost-name).
**Date:** 2026-06-11. Re-test after RT-022. **Numbers: ✅ all DB-verified exact.**
- Total Diamond Revenue ₹1,915,770,280.2 ✅; Top Shape Concentration 90.45% ✅ (Round's share of diamond
  revenue, verified); Top Quality Concentration 38.89% ✅; margin/orders/AOV correct. Diamond total ₹1.9B
  (no fan-out — gate forced the correct row-amount query). The RT-022 component/product false-positive is
  GONE (KPI1 passes clean).
- **2nd false-positive found + fixed:** the label-KPI guard flagged "Top Shape Revenue Concentration =
  90.45" as a "lost name." But 90.45 is a VALID % (Round = 90.45% of diamond revenue). The guard saw
  "top " + numeric and assumed the name was lost. FIX: distinguish "Top X CONCENTRATION/share/%" (value
  IS legitimately numeric → keep) from "Top X by revenue" (value should be a NAME → drop if numeric/0).
  Verified 5 cases: concentration-% kept; "by revenue"=0/number → dropped; "by revenue"='Round' → kept;
  normal scalar kept. App OK.
- **Pattern across RT-022/023:** the invariant/guard layer is now catching its OWN over-tight rules as
  FALSE POSITIVES on legitimate metrics, and we keep tightening to PRECISION. The trade is right (caution
  over silent-wrong), and each fix makes the guards smarter without weakening real catches.
- Honest status: diamond report is fully correct + clean; QA should now be clean APPROVED (no spurious
  accuracy warnings). The remaining non-correctness item is SPEED (8-round, 268s grind — deferred).

### RT-024 — gold-by-karat + component-breakdown: numbers ALL CORRECT, fixed 2 MORE guard false-positives.
**Date:** 2026-06-11. Two reports tested; **every number DB-verified correct**, but BOTH reports got a
spurious flag (one was even QA-REJECTED 6/12 while being 100% correct). Root-caused + fixed both.
- **Gold by karat:** 18Kt ₹5,950,033,867.04 ✅, Total ₹11,267,974,059.01 ✅ (= company total exactly;
  gold table is 1:1, NO fan-out — verified MAX 1.000 rows/sol). FALSE POSITIVE: RECONCILIATION invariant
  said "Revenue by Gold Karat parts = 11.27B, 1.99× the total (5.65B)". BUG: the chart is FULL revenue
  split BY a material ATTRIBUTE (karat) → parts sum to company revenue, NOT to the gold-COMPONENT total.
  But the invariant grabbed `company_total('gold')` (₹5.65B component value) as the "total" merely because
  "gold" was in the title. FIX: only use the material-component total when the chart MEASURES component
  value ("gold value/amount/component"), not when revenue is split by that material's attribute; else
  reconcile against the live company-revenue ceiling. → gold report now passes clean (0 violations).
- **Component breakdown (gold/diamond/making):** Total Rev ₹9,320,474,400.66 ✅, Order Lines 24,998 ✅
  (DB-verified exact), avg gold/diamond/making costs all correct. FALSE POSITIVE: label-KPI guard flagged
  "Total Order Lines (Top 5 Categories) = 24998" as a lost name. BUG: `_label_kpi` matched "top " ANYWHERE
  → the SCOPE qualifier "(Top 5 Categories)" tripped it. FIX: match "top "/"highest "/etc only at the
  START of the name; added lines/orders/units/qty/total to numeric-metric exclusions. → no longer flags
  count KPIs with a "(Top N …)" scope.
- Verified 6 cases: both false positives cleared; genuine lost-label ("Top Vendor"=0, "Which Vendor"=num)
  still caught; genuine component fan-out (18×) still caught. App OK.
- **HONEST STATE:** numbers correct in EVERY test (RT-022/023/024) — zero wrong values. The ONLY accuracy
  defect remaining is guard/invariant FALSE POSITIVES that wrongly downgrade correct reports. Converging
  fast; correctness (no silent-wrong) is solid. Speed (gold 162s, component 280s) still deferred.

### RT-025 — ⛔ FIRST GENUINELY-WRONG NUMBER (not fan-out, not false-positive): cross-domain fabricated profit.
**Date:** 2026-06-11. Query: "Compare what we sold products for vs what they cost us from vendors, by
category." **QA gave it 12/12 APPROVED — and it was WRONG.** This is the most important finding of the
engagement: the first bug where the model answered a DIFFERENT question and produced a confident, plausible,
fabricated business number that ALL prior guards missed.
- Correct: Avg Selling Price ₹66,711.30 ✅ (closed); Total Revenue ₹11,267,974,059.01 ✅.
- WRONG: Gross Profit ₹3,145,533,457 / Margin 27.96% / Avg Vendor Cost ₹45,022.08 / all cost+profit charts.
  The model computed Revenue = SUM(sales line_total, closed) MINUS COGS = SUM(po_line_pricing.unit_price ×
  po_line_items.quantity over ALL purchase orders). **VERIFIED: there is NO key linking a sales line to a
  PO line** — `sales_order_line` has no po/allocation column, NO allocation/fulfilment bridge table exists
  (only `pg_shmem_allocations`, a PG internal). So it compared closed-sales revenue (₹11.27B) to TOTAL
  procurement spend (₹8.34B) — two unrelated populations — and called the difference "gross profit." The
  per-category charts AVG selling price and AVG vendor cost from two unrelated row sets. Meaningless.
- THE RIGHT ANSWER WAS ON THE SAME ROW: sales_order_line_pricing has base_price_per_unit (= gold_amount +
  diamond_amount + making_charges per unit). True COGS = base_price_per_unit × quantity; gross profit =
  line_total − that; or just AVG(margin_pct). The model ignored the in-row cost and invented a PO join.
- **WHY GUARDS MISSED IT:** every prior defense targeted fan-out (inflation) or false-positives. This is a
  SEMANTIC join the LLM invents — right-looking SQL, wrong meaning. Prompts can't guarantee against it.
- **FIXES (universal, no hardcoded values):**
  1. Metric dict (claude_prompts.py): added COGS/vendor-cost/gross-profit rule — cost lives on the sales
     row (base_price_per_unit); ⛔ NEVER join/compare sales to po_line_*/purchase_order for a sale's
     cost/margin/profit (no key exists); po unit_price is purchase-side only.
  2. Deterministic guard (_apply_report_guards → `cross_domain_cost_fabrication`): flags any KPI/chart
     whose name/SQL is cost/profit/margin AND whose SQL references BOTH a sales_order* source and a
     po_line*/purchase_order source. Pure-PO ("PO spend by vendor") and pure-sales margin do NOT trip it.
     Verified 5 cases: fabricated profit KPI + chart flagged; vendor-cost (PO-only), correct sales margin,
     pure-PO-spend all clean.
  3. QA escalation: severe flags (cross_domain_cost_fabrication, material_total_inflated,
     revenue_exceeds_total, fabricated_formula, any chart _invariant) now force verdict to
     "CONDITIONAL (accuracy — value may be wrong)" — a fabricated number can NEVER show APPROVED again.
- **HONEST STATE UPDATE:** the "no silent-wrong numbers" claim was PREMATURE. RT-022/023/024 were all
  correct-number/false-flag cases; RT-025 is a real wrong number that scored 12/12. Now caught + QA-gated.
  But the lesson stands: every NEW cross-domain/semantic question is a candidate for a NEW invented-join
  bug. The guard catches the sales↔PO case specifically; other cross-domain fabrications may need the same
  treatment as they surface. NOT yet "perfect" — but the defense pattern is proven and extensible. App OK.

### RT-026 — Sonnet swap on SQL agent: fixed the CAPABILITY gap (correct answer), exposed the alias bug.
**Date:** 2026-06-11. Re-ran the RT-025 query AFTER moving ONLY the SQL Agent (+ Drift Detective) to
claude-sonnet-4-6; all other 5 agents stay on Haiku. **Result: CORRECT + APPROVED 12/12 (verified).**
- Sonnet read the new metric-dict COGS rule and used the ON-ROW cost (base_price_per_unit), the bridge-free
  correct method. DB-verified exact: Avg Selling ₹66,711.30 ✅, Avg Vendor Cost (base_price) ₹49,414.36 ✅,
  Gross Margin 25.93% ✅, Revenue ₹11.27B ✅, Units 204,020 ✅. NO fabrication/cross-domain flags. The exact
  query that was REJECTED-fabricated on Haiku (RT-025/026) is now correct. Capability gap = solved by Sonnet.
- COST: $1.30 vs $0.99 Haiku (+31%) — NOT the "cost-neutral" I predicted. Rounds dropped 15→5 (good) but
  Sonnet still burned 2 FULL rounds (16 queries, ~50s) re-emitting the SAME `DuplicateAlias: sol specified
  more than once` error. At Sonnet's 3× rate, those wasted rounds are what blew the budget. So the +31% is
  mostly WASTE on a mechanical bug, not Sonnet's price.

### RT-027 — fixed the duplicate-alias waste (precise hints, NO risky auto-rewrite).
**Date:** 2026-06-11. Root of RT-026's cost overrun: SQL agent (both models) repeatedly writes the same
alias for two tables, OR the same JOIN twice → Postgres "table name specified more than once" → 2-3 wasted
rounds. DECISION (per project principle "a wrong-but-valid number is worse than a caught error"): do NOT
auto-rewrite the SQL — rewriting ON-clause column refs can silently produce VALID-BUT-WRONG joins (RT-025
class disaster). Instead, make the error PRECISE so the model fixes in 1 round:
  1. `_prevalidate_sql` now distinguishes (a) same alias / two different tables → "rename the SECOND table
     to alias2, update only ITS refs", from (b) same table joined twice same alias → "remove the duplicate
     JOIN". Both name the exact table/alias. Valid distinct-alias SQL still passes clean (no false positive).
  2. `_enrich_sql_error` extracts the alias from the Postgres message and gives the same targeted fix,
     warning NOT to blindly rename every `alias.` (only the duplicated table's).
Verified 4 cases. Expectation: SQL agent now repairs alias collisions in ~1 round, pulling Sonnet's cost
back toward break-even. (Re-test pending.) App OK.

### RT-028 — raw-material→production→sale (3-domain): goal HELD, exposed a SEMANTIC gap + cost blowup.
**Date:** 2026-06-11. Query: "top products: gold/diamond raw material CONSUMED in production vs charged
to customers." Router → Sonnet (cross-domain) ✅. **Outcome: CONDITIONAL 5/12 — a fabricated KPI was
caught, so NO wrong answer shipped as correct (Joel's goal HELD).** But three issues, only one caught:
  1. CAUGHT ✅: KPI "Raw Material Margin Capture" was hand-COMPUTED by the model (SQL: "Computed: …", no
     FROM) → lineage_failed invariant + QA reject. Charts 4/5/6 also SQL:(none). Goal working as designed.
  2. NOT caught ⚠️ (semantic): model read "consumed in production" as po_line_gold/po_line_diamond =
     what was PURCHASED. The TRUTH table is raw_material_lot_usage_ledger (verified: 35,330 rows w/ sol_id,
     material_type gold|diamond, cols qty_used_gm/carats_used/pieces_used). "consumed"≠"purchased"≠"charged"
     = THREE different numbers. No guard catches this — same CLASS as RT-025 (model answers a near-but-wrong
     question). FIX APPLIED: metric-dict rule added (schema-anchored, names real cols, notes 3.81×/sol
     fan-out → aggregate first). NO code gate added — checked live: po_line_diamond.amount is PER-DIAMOND
     (distinct values per diamond_id), so SUM via DISTINCT pol_id is CORRECT; gating it would FALSE-BLOCK.
     (This is the dynamic-not-hardcoded discipline: let the DB decide, don't blanket-gate.)
  3. Cost ⚠️: $2.09 / 693s. SQL agent = $1.61 (77%), 13 rounds, flailing on `AmbiguousColumn product_id`
     ×6 + false DROP-keyword reject + alias (alias hint DID fire, good). Mechanical waste at Sonnet's 3× rate.

### ★ AUDIT + PLAN (2026-06-11) — stop the whack-a-mole; attack the ROOT.
Joel called out (correctly) that I keep "finding new bugs + proposing new fixes" reactively instead of a
plan. Did a real audit. THREE findings:

**Finding 1 — FAN-OUT predicts EVERY accuracy bug.** Measured rows-per-parent for all relationships
(live DB). Sorted by danger:
  - finished_goods_inventory per product = **36.7×** (max 91) 🔴 UNTESTED
  - job_card per sol = **5.8×** 🔴 UNTESTED
  - raw_material_lot_usage per sol = **3.8×** 🔴 (just hit)
  - po_line_items per po = **3.4×** 🔴 UNTESTED
  - sales_order_line_diamond / po_line_diamond = **2.56×**, job_card_diamond_lines **2.40×** 🟡 GATED ✅
  - sales_order_line per so = **2.07×** 🟡 partial
  - pricing / gold / allocation / fulfillment / invoice_lines / returns = **1.00×** 🟢 SAFE (never a bug)
  CONCLUSION: every bug to date (RT-007 diamond, RT-025 cost, RT-028 rm) is a table with fan-out >1; the
  1.0× tables have NEVER produced a bug. Fan-out factor IS the bug predictor. FOUR high-fan-out tables
  remain UNTESTED (inventory 36×, job_card 5.8×, po_items 3.4×, rm_usage 3.8×) = where the NEXT bugs are.

**Finding 2 — the "metric dictionary" is a FAKE semantic layer.** It's prose in a prompt the model can
ignore (RT-025 happened AFTER its COGS rule was written). A real semantic layer is CODE that owns the
join. Patching prose per-metric IS the whack-a-mole; each new metric is a new gap.

**Finding 3 — cost is a FLAILING problem, not a model problem.** 77% of the $2.09 was one agent burning
rounds on MECHANICAL errors (ambiguous column, alias, false-DROP) — deterministic-fixable, model-independent.

**THE PLAN (phased; stop when "answer, never wrong" is structurally met, not at "perfect"):**
  - **Phase 1 — finish the deterministic repair layer** (cheap, low-risk, immediate ~40% cost cut):
    precise hints for ambiguous-column, false-DROP-keyword, missing-FROM (same pattern as the alias hint).
    Industry approach #3 (typed repair loop), done properly. Stops Sonnet bleeding on every hard query.
  - **Phase 2 — fan-out gate as a DATA-DRIVEN registry, not per-table patches.** Replace the 3 hardcoded
    diamond tables with a registry built from the measured fan-out table above; block ANY parent-level
    SUM across ANY >1 table. Closes inventory/job_card/po_items/rm_usage (the 4 time-bombs) in ONE move.
    Industry approach #1, universal. ← After this, the goal is STRUCTURALLY met.
  - **Phase 3 — promote the dictionary to a lightweight CODE semantic layer** for the ~10 core metrics
    (each a function owning its tables/joins/fan-out); SQL agent calls get_metric_sql('cogs') instead of
    inventing joins. Industry approach #2 — the root-cause fix for the RT-025/consumption CLASS. Biggest
    effort; makes bugs PREVENTED not just caught; do last, after Phases 1-2 prove the patterns.
  NOT doing: golden-query library (#4 — questions too varied); Opus (never — failures are mechanical/
  semantic, not reasoning-depth). Order rationale: each phase de-risks the next; 1+2 lock "never wrong",
  3 makes it cheaper/faster by prevention.

---

## TODO (SQL ROBUSTNESS / SPEED — deferred per Joel, correctness first)
Tracked from RT-005: the Haiku SQL agent repeatedly re-emits the SAME malformed SQL
(`DuplicateAlias` "table sol specified more than once", `missing FROM-clause entry`) across many
rounds before recovering — RT-005 took 13 rounds / 280s, one query 117s. Final numbers correct, so
this is robustness/cost, not accuracy. Options to evaluate when picked up:
  (a) Move the SQL agent (Agent 3) to Sonnet — CASE 006/007 showed Sonnet emits much cleaner SQL.
  (b) Add a deterministic alias-dedup / FROM-clause pre-check before execution that auto-renames
      duplicate aliases (the existing _prevalidate_sql already catches duplicate aliases — wire it to
      AUTO-FIX rather than just reject, so the agent doesn't burn rounds re-trying).
  (c) Cap/await on slow queries (117s single query) — add statement_timeout.
Also still deferred: P7 phantom-signals scoping (before telemetry tables created), QA numeric
re-derivation, telemetry-table migration, RECOMMENDATION-UNVERIFIED causal checks.

---

## Log of DB facts verified (so I don't re-query)
- 2026-06-06 (OLD db `backup_v2_inventory`, 44 tables): discount non-existent/all-zero; no
  discount_exceptions/_rules; pricing avg margin 35.03%.
- 2026-06-08 (MASTER db `V2_inventory_management`, 54+1 tables): `discount_exceptions` EXISTS (120
  rows, created_at 2026-02-14→04-15, avg approved ~16.9%, ~10 weeks; status REJECTED=25, APPROVED rows
  exist e.g. Apr=18); `discount_rules`=5; `sales_invoices.discount_amount` STILL all-zero (16,941/0);
  `sales_order_line.discount_id` 105/38,363. sales_order: 18,500 rows, closed=16,941, dates
  2024-01-13→**2026-02-28**, total closed revenue ₹11.27B. pricing: line_total 1:1 with lines (38,363),
  avg margin 35.03%, base>selling in 0 lines, header total == Σ line_total (no fan-out). finished_goods:
  RM_PROVIDED=9,183/9,397, true leftover value ₹79.0M (vs receipt-total ₹105.2M). `territories` table
  exists; NO store table. Telemetry tables (audit_trail / signal_detection_logs / graph_sql_mappings /
  system_cache create-race) — system_cache exists(1 row); the other 3 DO NOT exist → all logging
  INSERTs fail-safe (zero writes). App is SELECT-only on business tables.
