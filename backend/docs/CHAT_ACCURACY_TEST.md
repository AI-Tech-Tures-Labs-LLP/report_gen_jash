# Chat Accuracy Test — Expanded Golden Set

Live-DB-verified accuracy battery for the **chat** path (`/ask`), run 2026-06-15.
Each question graded against ground truth pulled directly from the live database.
This doubles as the chat regression suite — re-run after any change to chat/SQL/semantic-layer.

Legend: ✅ pass · ❌ fail · ⚠️ partial/needs-attention

## FINAL SCORE: **19 / 19 chat-path questions CORRECT** ✅
(A, C, D, E, F, G, H all pass. B routed to report — valid behavior, not a failure.)
All adversarial fan-out traps (H) avoided. All metric-substitution traps (consumed-vs-purchased,
inventory total_amount, avg-order-value denominator) avoided. 0 wrong numbers.

Caveats observed (not accuracy failures): latency 8-23s per answer (3 sequential LLM calls +
occasional self-repair rounds); the agent sometimes skips get_metric_sql and self-derives — H20
did this but landed on a fan-out-safe equivalent that reconciled to the penny.

---


---

## B — Multi-metric in one answer

### B3 — "Show revenue and units sold by product category"  ↪️ ROUTED TO REPORT (not a chat failure)
- **Behavior:** the intent router classified this as `report`, not `data`. By design — its rules
  treat a multi-metric ("revenue AND units") breakdown across a dimension as report-shaped.
- **Not a bug:** this is a deliberate routing boundary. KNOWN BEHAVIOR: *multi-metric breakdowns
  route to the report pipeline, single-metric facts stay in chat.* (Matches typical BI tools.)
- **To test the chat SQL for multi-metric:** ask as two single-metric chat turns instead
  ("revenue by category" → "now units by category").
- **Ground truth (for whoever verifies the report):** RING rev 2.71B/units 47,406 ·
  EARRINGS 2.61B/56,218 · BRACELET 1.52B/18,371 · BANGLE 1.31B/10,536 · NECKLACE 1.16B/8,847.

---

## A — Comparisons / time

### A1 — "Revenue by year"  ✅ PASS
- **Expected:** 2024 ₹3.67B · 2025 ₹6.09B · 2026 ₹1.50B
- **Got:** 2024 3,670,226,622 · 2025 6,094,694,732 · 2026 1,503,052,703 — exact.
- **SQL:** `SUM(total_amount) FROM sales_order WHERE status='closed' GROUP BY TO_CHAR(order_date,'YYYY')` — correct grand-total path, no fan-out.
- Notes: ₹ symbol correct. Routed complex→Sonnet.

### A2 — "Compare 2025 revenue to 2024"  ✅ PASS
- **Expected:** 2025 6.09B vs 2024 3.67B, +2.42B, ~+66%
- **Got:** +₹2.42B, **+66.06%** YoY — exact.
- **SQL:** LAG() window over yearly totals; computed both years + delta + pct.
- Notes: Self-healed a validation error (mis-qualified `order_date`) in one retry. Follow-up context resolved correctly (knew "2025 vs 2024" from prior turn).

---

## C — Counts / operational

### C4 — "How many sales orders are there in total?"  ✅ PASS
- Expected **18,500** · Got 18,500. SQL: `COUNT(*) FROM sales_order`. Routed simple→Haiku.

### C5 — "How many orders are closed?"  ✅ PASS
- Expected **16,941** · Got 16,941. SQL: `COUNT(*) ... WHERE status='closed'`.

### C6 — "Break down orders by status"  ✅ PASS
- Expected closed 16,941 / cancelled 1,185 / open 374 · Got exact.
- SQL: `GROUP BY status ORDER BY count DESC`.

### C7 — "What is the average order value?"  ✅ PASS  (trap avoided)
- Expected **₹665,130** · Got ₹665,130.40.
- SQL: `AVG(total_amount) FROM sales_order WHERE status='closed'` — used the ORDER-level
  average (1 row per order), NOT a line-joined revenue÷count that would skew the denominator.
  This was the watch-point; it got it right.

### C8 — "How many distinct products have we sold?"  ✅ PASS
- Expected **953** · Got 953. SQL: `COUNT(DISTINCT sol.product_id)` joined to closed orders.

---

## D — Raw-material consumption (purchased-vs-consumed trap; 3.81× fan-out table)

### D9 — "How much gold have we consumed in production?"  ✅ PASS  (trap avoided)
- Expected **245,960.35 gm** · Got 245,960.35.
- Path: get_metric_sql("gold consumed") → `raw_material_gold_consumed` → SUM(qty_used_gm)
  FROM raw_material_lot_usage_ledger. Used the USAGE LEDGER (consumed), NOT po_line_gold
  (purchased). Semantic layer steered it correctly.

### D10 — "How many diamond carats consumed in production?"  ✅ PASS  (trap avoided)
- Expected **11,594.63 cts** · Got 11,594.63.
- Path: get_metric_sql("diamond consumed") → SUM(carats_used) FROM usage ledger. Correct
  consumed-not-purchased table again.

---

## E — Vendor / purchase

### E11 — "What is our total purchase order value?"  ✅ PASS
- Expected **₹8.34B** · Got 8,341,508,245.97.
- SQL: `SUM(total_amount) FROM purchase_order`. Self-corrected after 2 wrong table-name
  guesses (validator rejected them) → 5 rounds. Correct.

### E12 — "Which vendor has the highest PO value?"  ✅ PASS  (slow path)
- Expected **Rajput Gold Works ₹2.11B** · Got exact.
- SQL: `SUM(total_amount) GROUP BY vendor_name ORDER BY DESC LIMIT 1`.
- ⚠️ Took 5 rounds / 15.3s — agent introspected information_schema to find tables after
  initial guesses failed. Correct, but the wandering is a latency cost, not an accuracy issue.

## F — Inventory

### F13 — "What is the value of our leftover inventory?"  ✅ PASS  (trap avoided)
- Expected **₹501.6M** · Got 501,578,425.32.
- Path: get_metric_sql("leftover inventory value") → SUM(quantity_available * unit_cost).
  Did NOT use total_amount (which inflates ~16× to ~₹8B). Semantic layer caught the trap.

### F14 — "How many units are in inventory?"  ✅ PASS
- Expected **11,415** · Got 11,415. SQL: `SUM(quantity_available)`.

---

## G — Hunters / people

### G15 — "How many hunters do we have?"  ✅ PASS
- Expected **22** · Got 22. SQL: `COUNT(*) FROM hunters`. Haiku.

### G16 — "Which hunter generated the most revenue?"  ✅ PASS
- Expected **Neha Gupta ₹413.9M** · Got 413,922,809.77.
- SQL: hunter→sales_order→line→pricing join, SUM(solp.line_total), ORDER DESC LIMIT 1.

## H — Adversarial fan-out (the traps)

### H17 — "Total gold component value in sales"  ✅ PASS (trap avoided)
- Expected **₹5.65B** · Got 5,653,736,990.82.
- get_metric_sql → SUM(solp.gold_amount_per_unit * qty). 1:1 pricing path, no fan-out.
  Self-healed an alias-duplicate error.

### H18 — "Total making charges value in sales"  ✅ PASS (trap avoided)
- Expected **₹777.6M** · Got 777,643,229.00. SUM(solp.making_charges_per_unit * qty).

### H19 — "Diamond value by product category"  ✅ PASS (fan-out + grouping)
- Expected per-category summing to **₹1.92B** · Got total 1.92B across 11 cats (Earrings 558.98M lead).
- SUM(solp.diamond_amount_per_unit * qty) GROUP BY category. No fan-out despite the grouping.

### H20 — "Which category has the highest gold value?"  ✅ PASS (verified — see note)
- Expected top = RING, total reconciling to ₹5.65B · Got RING ₹1.41B.
- NOTE: agent SKIPPED get_metric_sql and used sales_order_line_gold (solg) instead of pricing
  (solp). VERIFIED against DB: solg is 1:1 with the line (no fan-out), and the solg-based
  per-category totals reconcile to the canonical solp total to the penny (5,653,736,990.45 vs
  .82). A correct alternate path — passes. Watch-point: self-derivation without the metric tool
  worked here but is the kind of thing to keep an eye on.
