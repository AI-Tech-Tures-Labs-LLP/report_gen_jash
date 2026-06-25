"""Phase 3 — lightweight CODE semantic layer for the core metrics.

The root cause of the RT-025 / consumption (RT-028) bug CLASS is that the LLM freely
INVENTS joins for a metric and sometimes connects tables that aren't actually related
(or summs a fan-out column). Prompts steer but don't guarantee. This module makes the
canonical, DB-VERIFIED definition of each core metric available as a tool, so the agent
can fetch the trusted SQL fragment instead of guessing the join.

Each fragment was validated against the live DB (see RT-028 audit):
  total_revenue        = 11,267,974,059.01   gross_margin_pct = 35.02
  cogs (base_price)    =  8,347,150,500.01   units            = 204,020
  diamond_component    =  1,915,770,280.20   gold_component   = 5,653,736,990.82
  vendor_cogs (bridge) =  7,588,274,007.37   rm_gold_consumed = 245,960.35 gm

This is ADVISORY (a tool the agent calls), not a hard gate — the fan-out gate
(_detect_line_child_fanout) and report guards remain the enforcement layer. The point
here is to PREVENT the wrong join from being written in the first place.
"""

# Canonical closed-orders join used by all sales-side value metrics (sol→so, status=closed).
_CLOSED_JOIN = (
    "FROM sales_order_line_pricing solp "
    "JOIN sales_order_line sol ON solp.sol_id = sol.sol_id "
    "JOIN sales_order so ON sol.so_id = so.so_id "
    "WHERE so.status = 'closed'"
)

# name -> {sql, note}. `sql` is a ready-to-run/adapt fragment; `note` states the trap it avoids.
METRIC_LIBRARY: dict[str, dict] = {
    "total_revenue": {
        "sql": f"SELECT ROUND(SUM(solp.line_total)::numeric, 2) AS value {_CLOSED_JOIN}",
        "note": "Closed-order revenue from the 1:1 pricing table. NEVER sum line_total across a "
                "diamond/child join (fan-out → ~3× inflation).",
    },
    "cogs": {
        "sql": f"SELECT ROUND(SUM(solp.base_price_per_unit * solp.quantity)::numeric, 2) AS value {_CLOSED_JOIN}",
        "note": "A SALE's cost is the on-row base_price_per_unit (= gold+diamond+making). Do NOT "
                "aggregate PO tables separately and subtract (RT-025 fabrication).",
    },
    "gross_profit": {
        # VENDOR-cost gross profit — consistent with gross_margin_pct (both use vendor cost via the
        # 1:1 allocation bridge). This is "what we sold for − what the vendor charged us".
        "sql": "SELECT ROUND((SUM(solp.line_total) - SUM(plp.unit_price * sol.quantity))::numeric, 2) AS value "
               "FROM sales_order_line sol "
               "JOIN sales_order so ON sol.so_id = so.so_id AND so.status = 'closed' "
               "JOIN sales_order_line_pricing solp ON solp.sol_id = sol.sol_id "
               "JOIN sales_allocation sa ON sa.sol_id = sol.sol_id "
               "JOIN po_line_pricing plp ON plp.pol_id = sa.pol_id",
        "note": "VENDOR-cost gross profit = revenue − vendor COGS (allocation bridge). Pairs with "
                "gross_margin_pct (same basis). For the INTERNAL landed-cost version use "
                "gross_profit_base. NEVER revenue − total PO spend (unrelated populations).",
    },
    "gross_profit_base": {
        "sql": f"SELECT ROUND((SUM(solp.line_total) - SUM(solp.base_price_per_unit * solp.quantity))::numeric, 2) AS value {_CLOSED_JOIN}",
        "note": "INTERNAL landed-cost gross profit = revenue − on-row base_price (gold+diamond+making). "
                "Same row, no bridge. Use when the question asks profit vs our COST TO MAKE, not vs "
                "what the vendor charged. (Vendor-cost version is the default 'gross_profit'.)",
    },
    "gross_margin_pct": {
        # VENDOR-COST WEIGHTED margin (Joel's definition: price to customer vs cost from
        # vendor), revenue-weighted so large bulk orders pull the company figure correctly.
        # NOT AVG(margin_pct): a simple average weights a ₹500 line == a ₹5M line and
        # overstates the blended margin (35.0% vs the true 32.66%, DB-verified). The
        # vendor cost comes through the 1:1 allocation bridge (sol→sales_allocation→
        # po_line_pricing), same path as vendor_cogs.
        "sql": "SELECT ROUND((100.0 * (SUM(solp.line_total) - SUM(plp.unit_price * sol.quantity)) "
               "/ NULLIF(SUM(solp.line_total), 0))::numeric, 2) AS value "
               "FROM sales_order_line sol "
               "JOIN sales_order so ON sol.so_id = so.so_id AND so.status = 'closed' "
               "JOIN sales_order_line_pricing solp ON solp.sol_id = sol.sol_id "
               "JOIN sales_allocation sa ON sa.sol_id = sol.sol_id "
               "JOIN po_line_pricing plp ON plp.pol_id = sa.pol_id",
        "note": "VENDOR-cost gross margin %, revenue-weighted: (Σ line_total − Σ vendor "
                "unit_price×qty) / Σ line_total. Vendor cost via the 1:1 allocation bridge "
                "(safe, no fan-out). NEVER AVG(margin_pct) — that is an unweighted per-line "
                "average and overstates the blended company margin.",
    },
    "units": {
        "sql": "SELECT SUM(sol.quantity) AS value FROM sales_order_line sol "
               "JOIN sales_order so ON sol.so_id = so.so_id WHERE so.status = 'closed'",
        "note": "SUM(quantity), NOT COUNT(lines). Join sales_order for the closed filter.",
    },
    "diamond_component_value": {
        "sql": f"SELECT ROUND(SUM(solp.diamond_amount_per_unit * solp.quantity)::numeric, 2) AS value {_CLOSED_JOIN}",
        "note": "Diamond COMPONENT value from the 1:1 pricing table — do NOT join "
                "sales_order_line_diamond (2.56× fan-out).",
    },
    "gold_component_value": {
        "sql": f"SELECT ROUND(SUM(solp.gold_amount_per_unit * solp.quantity)::numeric, 2) AS value {_CLOSED_JOIN}",
        "note": "Gold COMPONENT value from the 1:1 pricing table (gold child is 1:1 but pricing is simpler).",
    },
    "making_component_value": {
        "sql": f"SELECT ROUND(SUM(solp.making_charges_per_unit * solp.quantity)::numeric, 2) AS value {_CLOSED_JOIN}",
        "note": "Making-charge component from the 1:1 pricing table.",
    },
    "vendor_cogs": {
        "sql": "SELECT ROUND(SUM(plp.unit_price * sol.quantity)::numeric, 2) AS value "
               "FROM sales_order_line sol "
               "JOIN sales_order so ON sol.so_id = so.so_id AND so.status = 'closed' "
               "JOIN sales_allocation sa ON sol.sol_id = sa.sol_id "
               "JOIN po_line_pricing plp ON sa.pol_id = plp.pol_id",
        "note": "VENDOR cost of the items SOLD — ONLY via the allocation bridge "
                "(sol→sales_allocation.pol_id→po_line_pricing). NEVER aggregate all POs separately.",
    },
    "raw_material_gold_consumed": {
        "sql": "SELECT ROUND(SUM(qty_used_gm)::numeric, 2) AS value "
               "FROM raw_material_lot_usage_ledger WHERE material_type = 'gold'",
        "note": "CONSUMED in production = the usage ledger (qty_used_gm grams). NOT po_line_gold "
                "(=purchased) and NOT sales pricing (=charged). 3.81×/sol — aggregate before joining sales.",
    },
    "raw_material_diamond_consumed": {
        "sql": "SELECT ROUND(SUM(carats_used)::numeric, 2) AS value "
               "FROM raw_material_lot_usage_ledger WHERE material_type = 'diamond'",
        "note": "Diamond CONSUMED = usage ledger carats_used. NOT po_line_diamond (=purchased).",
    },
    "gold_cost": {
        "sql": "SELECT ROUND(SUM(solg.gold_rate_per_gm * solg.total_gold_weight_per_unit * sol.quantity)::numeric, 2) AS value "
               "FROM sales_order_line_gold solg "
               "JOIN sales_order_line sol ON solg.sol_id = sol.sol_id "
               "JOIN sales_order so ON sol.so_id = so.so_id WHERE so.status = 'closed'",
        "note": "Gold metal value of sold items. Column is total_gold_weight_per_unit (grams/unit) — "
                "there is NO 'gold_weight_grams'. solg is 1:1 with sol (no fan-out). Equivalent: "
                "SUM(solg.gold_amount_per_unit * sol.quantity). Agrees with gold_component_value.",
    },
    "leftover_inventory_value": {
        "sql": "SELECT ROUND(SUM(quantity_available * unit_cost)::numeric, 2) AS value "
               "FROM finished_goods_inventory",
        "note": "On-hand/remaining stock VALUE = quantity_available × unit_cost. NEVER SUM(total_amount) "
                "(= full original receipt value, overstates ~16× once any units are consumed). "
                "Filter material_mode='RM_PROVIDED' for RM-provided stock only.",
    },
    "leftover_units": {
        "sql": "SELECT SUM(quantity_available) AS value FROM finished_goods_inventory",
        "note": "On-hand units = SUM(quantity_available). (Value version = leftover_inventory_value.)",
    },
    "discount_pct": {
        "sql": "SELECT ROUND(AVG(approved_discount_pct)::numeric, 2) AS value FROM discount_exceptions",
        "note": "Discount lives in the governance table discount_exceptions.approved_discount_pct "
                "(also requested_/allowed_discount_pct; trend by created_at). ⚠️ "
                "sales_invoices.discount_amount is ALL ZERO — never use it. DISCOUNT ≠ MARGIN. "
                "If discount_exceptions has no rows for the asked period, say discount data is "
                "unavailable — do NOT substitute margin.",
    },
    "dso": {
        # Intentionally returns no SQL — the underlying data is all-zero, so any computed DSO is a
        # false 0. The agent must surface unavailability, not a number.
        "sql": "",
        "note": "UNAVAILABLE: customer_master.outstanding_balance is ALL ZERO in this DB — there is no "
                "receivables/AR data, so DSO cannot be computed. Do NOT return 0 days; STATE that "
                "outstanding-balance data is unavailable. (If AR is added later: "
                "outstanding_balance / annual_revenue × 365, annual_revenue = "
                "SUM(sales_order.total_amount) per customer_id.)",
    },
}

# Cheap keyword → metric routing so the agent can ask by intent word.
_ALIASES = {
    "revenue": "total_revenue", "sales": "total_revenue",
    "cogs": "cogs", "cost of goods": "cogs", "cost of sale": "cogs",
    "profit": "gross_profit", "gross profit": "gross_profit",
    "margin": "gross_margin_pct", "margin %": "gross_margin_pct",
    "units": "units", "quantity sold": "units", "units sold": "units",
    "diamond value": "diamond_component_value", "diamond component": "diamond_component_value",
    "gold value": "gold_component_value", "gold component": "gold_component_value",
    "making": "making_component_value",
    "vendor cost": "vendor_cogs", "what it cost us": "vendor_cogs", "vendor cogs": "vendor_cogs",
    "gold consumed": "raw_material_gold_consumed", "gold used": "raw_material_gold_consumed",
    "diamond consumed": "raw_material_diamond_consumed", "diamond used": "raw_material_diamond_consumed",
    "raw material": "raw_material_gold_consumed",
    "gross profit base": "gross_profit_base", "landed cost profit": "gross_profit_base",
    "gold cost": "gold_cost", "metal cost": "gold_cost",
    "leftover value": "leftover_inventory_value", "on-hand value": "leftover_inventory_value",
    "on hand value": "leftover_inventory_value", "remaining value": "leftover_inventory_value",
    "inventory value": "leftover_inventory_value",
    "leftover units": "leftover_units", "on-hand units": "leftover_units", "remaining units": "leftover_units",
    "discount": "discount_pct", "discount %": "discount_pct", "discount rate": "discount_pct",
    "dso": "dso", "days sales outstanding": "dso", "receivable": "dso", "receivables": "dso",
}


def get_metric_sql(metric: str) -> dict:
    """Return the canonical SQL fragment + note for a core metric, by exact key or keyword.
    Returns {found, metric, sql, note} (found=False with the list of keys if no match)."""
    if not metric:
        return {"found": False, "available": sorted(METRIC_LIBRARY), "note": "Pass a metric name."}
    key = metric.strip().lower()

    def _result(name: str) -> dict:
        entry = METRIC_LIBRARY[name]
        out = {"found": True, "metric": name, "sql": entry["sql"], "note": entry["note"]}
        # A metric with no SQL is intentionally UNAVAILABLE (data missing, e.g. DSO) — flag it
        # so the agent surfaces unavailability instead of trying to run an empty query.
        if not entry["sql"]:
            out["unavailable"] = True
        return out

    if key in METRIC_LIBRARY:
        return _result(key)
    # Match the MOST SPECIFIC alias first: a substring scan in dict order would let a short
    # generic alias ("units", "sales") hijack a longer specific phrase ("leftover units",
    # "days sales outstanding"). Sorting by descending key length fixes that ordering bug.
    for kw in sorted(_ALIASES, key=len, reverse=True):
        if kw in key:
            return _result(_ALIASES[kw])
    return {"found": False, "available": sorted(METRIC_LIBRARY),
            "note": f"No canonical metric matches '{metric}'. Write SQL directly using the schema, "
                    f"or pick one of the available metrics."}
