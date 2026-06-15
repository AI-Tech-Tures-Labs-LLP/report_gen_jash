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
        "sql": f"SELECT ROUND((SUM(solp.line_total) - SUM(solp.base_price_per_unit * solp.quantity))::numeric, 2) AS value {_CLOSED_JOIN}",
        "note": "Revenue − on-row COGS, same row. NEVER revenue − total PO spend.",
    },
    "gross_margin_pct": {
        "sql": f"SELECT ROUND(AVG(solp.margin_pct)::numeric, 2) AS value {_CLOSED_JOIN}",
        "note": "margin_pct already encodes (selling−base)/selling. Use it directly.",
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
}


def get_metric_sql(metric: str) -> dict:
    """Return the canonical SQL fragment + note for a core metric, by exact key or keyword.
    Returns {found, metric, sql, note} (found=False with the list of keys if no match)."""
    if not metric:
        return {"found": False, "available": sorted(METRIC_LIBRARY), "note": "Pass a metric name."}
    key = metric.strip().lower()
    if key in METRIC_LIBRARY:
        entry = METRIC_LIBRARY[key]
        return {"found": True, "metric": key, "sql": entry["sql"], "note": entry["note"]}
    for kw, mapped in _ALIASES.items():
        if kw in key:
            entry = METRIC_LIBRARY[mapped]
            return {"found": True, "metric": mapped, "sql": entry["sql"], "note": entry["note"]}
    return {"found": False, "available": sorted(METRIC_LIBRARY),
            "note": f"No canonical metric matches '{metric}'. Write SQL directly using the schema, "
                    f"or pick one of the available metrics."}
