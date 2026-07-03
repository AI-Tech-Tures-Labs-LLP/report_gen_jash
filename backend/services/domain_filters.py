"""Domain-aware filter configuration for the report filter bar.

Each generated report belongs to ONE business domain (sales, procurement,
inventory, manufacturing, crm, finance), detected from which core tables its
SQL touches. Each domain defines its own filter vocabulary. The filter bar,
the /report/filters options endpoint, and the SQL injector all read from this
single config — so adding a domain or filter is a config edit here only, never
a change to injection logic.

Every join_via chain below is a real foreign key (verified against
db/relationships.py and the live schema), never a guess.

Filter definition fields
-------------------------
  table        : table the filtered column lives on
  alias_hint   : alias to use if we must inject a JOIN to reach `table`
  column       : column to filter on
  kind         : 'text'  -> equality / IN (...) on a categorical column
                 'date'  -> range comparison (uses date_from / date_to values)
  from_anchor  : which of the domain's anchor_tables this filter's join path
                 starts from
  join_via     : ordered list of (table, alias_hint, from_col, to_col) FK hops
                 from `from_anchor` to `table`. Empty = the column lives on the
                 anchor table itself (no join needed).

The UI shows one control per filter key (except date_from/date_to which render
as a single DATE RANGE pair). Text filters render as Excel-style multi-select
checkbox dropdowns; the injector turns a list of picked values into IN (...).
"""

DOMAINS = {
    "sales": {
        "label": "Sales",
        "anchor_tables": ["sales_order", "sales_order_line"],
        "filters": {
            "date_from": {"table": "sales_order", "alias_hint": "so", "column": "order_date", "kind": "date", "from_anchor": "sales_order", "join_via": []},
            "date_to":   {"table": "sales_order", "alias_hint": "so", "column": "order_date", "kind": "date", "from_anchor": "sales_order", "join_via": []},
            "status":    {"table": "sales_order", "alias_hint": "so", "column": "status", "kind": "text", "from_anchor": "sales_order", "join_via": []},
            "category":  {"table": "product_master", "alias_hint": "pm", "column": "category", "kind": "text", "from_anchor": "sales_order",
                          "join_via": [("sales_order_line", "sol", "so_id", "so_id"), ("product_master", "pm", "product_id", "product_id")]},
            "product":   {"table": "product_master", "alias_hint": "pm", "column": "product_name", "kind": "text", "from_anchor": "sales_order",
                          "join_via": [("sales_order_line", "sol", "so_id", "so_id"), ("product_master", "pm", "product_id", "product_id")]},
            "customer":  {"table": "customer_master", "alias_hint": "cm", "column": "customer_name", "kind": "text", "from_anchor": "sales_order",
                          "join_via": [("customer_master", "cm", "customer_id", "customer_id")]},
        },
    },
    "procurement": {
        "label": "Procurement",
        "anchor_tables": ["purchase_order", "po_line_items"],
        "filters": {
            "date_from": {"table": "purchase_order", "alias_hint": "po", "column": "created_at", "kind": "date", "from_anchor": "purchase_order", "join_via": []},
            "date_to":   {"table": "purchase_order", "alias_hint": "po", "column": "created_at", "kind": "date", "from_anchor": "purchase_order", "join_via": []},
            "status":    {"table": "purchase_order", "alias_hint": "po", "column": "status", "kind": "text", "from_anchor": "purchase_order", "join_via": []},
            "po_type":   {"table": "purchase_order", "alias_hint": "po", "column": "po_type", "kind": "text", "from_anchor": "purchase_order", "join_via": []},
            "vendor":    {"table": "vendor_master", "alias_hint": "vm", "column": "vendor_name", "kind": "text", "from_anchor": "purchase_order",
                          "join_via": [("vendor_master", "vm", "vendor_id", "vendor_id")]},
        },
    },
    "inventory": {
        "label": "Inventory",
        "anchor_tables": ["finished_goods_inventory", "inventory_movements"],
        "filters": {
            "date_from": {"table": "finished_goods_inventory", "alias_hint": "fgi", "column": "received_date", "kind": "date", "from_anchor": "finished_goods_inventory", "join_via": []},
            "date_to":   {"table": "finished_goods_inventory", "alias_hint": "fgi", "column": "received_date", "kind": "date", "from_anchor": "finished_goods_inventory", "join_via": []},
            "status":    {"table": "finished_goods_inventory", "alias_hint": "fgi", "column": "status", "kind": "text", "from_anchor": "finished_goods_inventory", "join_via": []},
            "vendor":    {"table": "finished_goods_inventory", "alias_hint": "fgi", "column": "vendor", "kind": "text", "from_anchor": "finished_goods_inventory", "join_via": []},
            "product":   {"table": "product_master", "alias_hint": "pm", "column": "product_name", "kind": "text", "from_anchor": "finished_goods_inventory",
                          "join_via": [("product_master", "pm", "product_id", "product_id")]},
        },
    },
    "manufacturing": {
        "label": "Manufacturing",
        "anchor_tables": ["job_card"],
        "filters": {
            "date_from": {"table": "job_card", "alias_hint": "jc", "column": "created_at", "kind": "date", "from_anchor": "job_card", "join_via": []},
            "date_to":   {"table": "job_card", "alias_hint": "jc", "column": "created_at", "kind": "date", "from_anchor": "job_card", "join_via": []},
            "status":    {"table": "job_card", "alias_hint": "jc", "column": "status", "kind": "text", "from_anchor": "job_card", "join_via": []},
            "gold_kt":   {"table": "job_card", "alias_hint": "jc", "column": "gold_kt", "kind": "text", "from_anchor": "job_card", "join_via": []},
        },
    },
    "crm": {
        "label": "CRM",
        "anchor_tables": ["party_master", "visit_log"],
        "filters": {
            "date_from": {"table": "party_master", "alias_hint": "party", "column": "created_at", "kind": "date", "from_anchor": "party_master", "join_via": []},
            "date_to":   {"table": "party_master", "alias_hint": "party", "column": "created_at", "kind": "date", "from_anchor": "party_master", "join_via": []},
            "stage":     {"table": "party_master", "alias_hint": "party", "column": "stage", "kind": "text", "from_anchor": "party_master", "join_via": []},
            "territory": {"table": "territories", "alias_hint": "terr", "column": "name", "kind": "text", "from_anchor": "party_master",
                          "join_via": [("territories", "terr", "territory_id", "territory_id")]},
            "hunter":    {"table": "hunters", "alias_hint": "hnt", "column": "name", "kind": "text", "from_anchor": "party_master",
                          "join_via": [("hunters", "hnt", "assigned_hunter_id", "hunter_id")]},
        },
    },
    "finance": {
        "label": "Finance",
        "anchor_tables": ["sales_invoices", "purchase_invoices"],
        "filters": {
            "date_from":      {"table": "sales_invoices", "alias_hint": "sinv", "column": "invoice_date", "kind": "date", "from_anchor": "sales_invoices", "join_via": []},
            "date_to":        {"table": "sales_invoices", "alias_hint": "sinv", "column": "invoice_date", "kind": "date", "from_anchor": "sales_invoices", "join_via": []},
            "payment_status": {"table": "sales_invoices", "alias_hint": "sinv", "column": "payment_status", "kind": "text", "from_anchor": "sales_invoices", "join_via": []},
            "customer":       {"table": "sales_invoices", "alias_hint": "sinv", "column": "customer_name", "kind": "text", "from_anchor": "sales_invoices", "join_via": []},
        },
    },
}


def detect_domain(sql_combined: str) -> str | None:
    """Return the domain whose anchor tables appear most often in the report's
    combined SQL, or None if no domain's anchor tables are present. Ties break
    by DOMAINS insertion order (sales first)."""
    import re
    best_domain, best_score = None, 0
    for name, cfg in DOMAINS.items():
        score = sum(
            1 for tbl in cfg["anchor_tables"]
            if re.search(rf'\b{tbl}\b(?!_)', sql_combined, re.IGNORECASE)
        )
        if score > best_score:
            best_domain, best_score = name, score
    return best_domain
