"""Report generation pipeline — intent classification + LLM-based report builder.

This module adds report generation capability to the SQL chatbot without
modifying any existing chat behaviour.
"""

import hashlib
import json
import logging
import re
from datetime import date
from typing import Any

from ai.validator import validate_sql, check_sql_against_schema
from ai.sql_pattern_checker import check_sql_patterns, format_issues_for_repair
from db.schema import format_schema, get_schema
from db.relationships import format_relationships
from db.profiler import get_data_profile
from db.executor import execute_sql

# Re-export the pure-function clusters that were extracted into dedicated
# modules. These imports keep the public surface of this module identical:
# `from services.report_generator import classify_intent / _fix_report_sql /
# _inject_filters / ...` continues to work for all external importers, and
# all internal uses below resolve through this module's namespace.
from services.intent_classifier import (
    classify_intent,
    _REPORT_KEYWORDS,
    _REPORT_PATTERNS,
)
from services.sql_fixer import (
    _fix_report_sql,
    _SO_ONLY_COLS,
    _SOL_ONLY_COLS,
    _SUB_TABLES,
)
from services.filter_injector import _inject_filters

logger = logging.getLogger(__name__)

MAX_REPAIR_RETRIES = 2

# ── Blueprint Cache ─────────────────────────────────────────────────────────
# Caches the LLM-generated report blueprint (JSON structure with SQL queries,
# chart types, KPI labels, etc.) keyed by a normalized hash of the user's
# question. Same question → identical report structure every time.
# Data values are still executed fresh from the DB.
# Cache is persisted to disk so it survives server restarts.
import pathlib as _pathlib

_CACHE_DIR = _pathlib.Path(__file__).resolve().parent.parent / ".report_cache"
_CACHE_FILE = _CACHE_DIR / "blueprints.json"


def _load_blueprint_cache() -> dict[str, dict]:
    """Load cached blueprints from disk."""
    try:
        if _CACHE_FILE.exists():
            data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            logger.info("Loaded %d cached report blueprints from disk", len(data))
            return data
    except Exception as exc:
        logger.warning("Failed to load blueprint cache: %s", exc)
    return {}


def _save_blueprint_cache(cache: dict[str, dict]) -> None:
    """Save cached blueprints to disk."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to save blueprint cache: %s", exc)


_blueprint_cache: dict[str, dict] = _load_blueprint_cache()

# Per-key locks to prevent duplicate LLM calls when two users request
# the same report simultaneously.  The second request waits for the
# first LLM call to finish and then gets the cached result.
import threading as _threading
_cache_locks: dict[str, _threading.Lock] = {}
_cache_locks_guard = _threading.Lock()  # protects _cache_locks dict itself


def _get_cache_lock(key: str) -> _threading.Lock:
    """Get or create a per-key lock for blueprint generation."""
    with _cache_locks_guard:
        if key not in _cache_locks:
            _cache_locks[key] = _threading.Lock()
        return _cache_locks[key]


# ── Report generation ──────────────────────────────────────────────────────

class ReportPipeline:
    """Helper for report filtering/modification post-processing.

    NOTE: This was the legacy DSPy/Groq report generator. The main report
    pipeline is now Claude-based (see ai/claude_multi_agent.py via
    ai/enhanced_pipeline.py). What remains here are the engine-agnostic
    plain-Python helpers used by `/report/apply-filters` and `/report/modify`
    (SQL filter injection, KPI/chart execution, dedup, formatting). No LLM is
    used in this class; SQL repair/regeneration fallbacks have been removed
    along with the Groq stack.
    """

    def __init__(self):
        # Per-report regeneration counter (retained for method compatibility).
        self._regen_count = 0
        self._MAX_REGEN_PER_REPORT = 3

    # ── Shared SQL validation + execution (mirrors SQLAnalystPipeline) ──

    @staticmethod
    def _clean_sql(raw: str) -> str:
        """Strip markdown fences, trailing prose, and whitespace from LLM SQL."""
        sql = raw.strip()
        if sql.startswith("```"):
            lines = sql.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            sql = "\n".join(lines).strip()
        match = re.search(
            r"((?:SELECT|WITH)\b[\s\S]*?)(;|\n\n(?=[A-Z][a-z])|$)",
            sql, re.IGNORECASE,
        )
        if match:
            sql = match.group(1).strip()
        sql = sql.rstrip(";")
        return sql

    def _validate_and_execute_sql(self, sql: str, context: str = "report", sql_description: str = "") -> tuple:
        """Validate and execute SQL using the same pipeline as SQL Chat.

        Applies the full validation chain:
        1. Auto-correct known LLM mistakes (_fix_report_sql)
        2. Schema validation (check_sql_against_schema)
        3. Structural pattern checker (check_sql_patterns)
        4. Safety validation (validate_sql)
        5. Execute with repair loop (up to 2 retries on DB errors)

        Returns (corrected_sql, result_dict) where result_dict has
        'success', 'data', 'error' keys.
        """
        # Step 1: Auto-correct known regex patterns
        sql = _fix_report_sql(sql)

        # Step 2: Schema validation
        try:
            schema = get_schema()
            schema_valid, schema_issues = check_sql_against_schema(sql, schema)
            if not schema_valid:
                logger.warning("[%s] Schema issues: %s", context, schema_issues)
        except Exception as exc:
            logger.warning("Schema check failed: %s", exc)

        # Step 3: Structural pattern checker
        try:
            pattern_issues = check_sql_patterns(sql)
            if pattern_issues:
                logger.warning(
                    "[%s] Pattern issues: %s",
                    context,
                    [i["pattern_name"] for i in pattern_issues],
                )
        except Exception as exc:
            logger.warning("Pattern check failed: %s", exc)

        # Step 4: Safety validation
        is_safe, reason = validate_sql(sql)
        if not is_safe:
            return sql, {"success": False, "data": [], "error": f"Query rejected: {reason}"}

        # Step 5: Execute. (LLM-based SQL repair/regeneration was removed with the
        # Groq/DSPy stack. SQL still goes through the full regex auto-fix + schema
        # + pattern + safety validation above; on failure it degrades gracefully
        # rather than attempting an LLM repair. A Claude-based repair can be added
        # later if needed.)
        result = execute_sql(sql)
        if not result["success"]:
            logger.warning("[%s] SQL execution failed (no LLM repair): %s", context, result["error"])

        return sql, result

    @staticmethod
    def _build_question_with_context(question: str) -> str:
        today = date.today()
        current_year = today.year
        last_year = current_year - 1
        return (
            f"[CONTEXT: Today is {today.isoformat()}. "
            f"Current year = {current_year}. "
            f"'Last year' = {last_year} ({last_year}-01-01 to {last_year}-12-31). "
            f"'This year' = {current_year} ({current_year}-01-01 to {current_year}-12-31).]\n\n"
            f"{question}"
        )

    @staticmethod
    def _get_analytical_framework(question: str) -> str:
        """Provide high-level analytical guidance based on the report theme.

        Tells the report LLM WHAT to analyze (conceptual metrics/dimensions)
        without prescribing HOW (no SQL examples — the schema and pre-analysis
        handle that). This prevents hallucination while ensuring relevant metrics.
        """
        q = question.lower().strip()
        q = re.sub(r'\[active filters:.*?\]', '', q, flags=re.IGNORECASE)
        q = re.sub(r'\[context:.*?\]', '', q, flags=re.IGNORECASE).strip()

        # Theme → (keywords, focus area, metric hints, anti-patterns)
        themes = {
            "customer": {
                "kw": ["customer", "buyer", "buying pattern", "purchase pattern",
                       "customer behavior", "customer analysis", "retention",
                       "churn", "loyalty", "repeat", "rfm", "customer value",
                       "top 10 customer", "top 5 customer", "top 20 customer",
                       "top customer", "best customer", "biggest customer",
                       "customer domain", "customer segment",
                       "customer spending", "spending", "spend", "spender",
                       "top spender", "biggest spender", "customer spend"],
                "focus": "CUSTOMER behavior, segments, spending patterns, and customer-level metrics",
                "metrics": [
                    "Total number of unique customers",
                    "Total customer revenue / spending (SUM of closed order totals)",
                    "Average order value (AOV) — total revenue ÷ total orders",
                    "Average number of orders per customer",
                    "Average spend per customer (total revenue ÷ unique customers)",
                    "Customer repeat purchase rate (% of customers with 2+ orders)",
                    "Customer revenue ranking (horizontalBar — top 10 customer names vs total spend)",
                    "Customer spending trend over time (line chart — monthly revenue)",
                    "Customer product category preferences (bar — category vs total spend)",
                    "New vs returning customer split (doughnut — NEW=1 order, RETURNING=2+ orders)",
                    "Customer concentration — top 10 vs rest (pie — top 10 share vs others)",
                    "Customer order frequency distribution (bar — orders-per-customer buckets)",
                ],
                "sql_notes": (
                    "CRITICAL SQL NOTES for this report:\n"
                    "Base table: sales_order (alias: so), JOIN sales_order_line (alias: sol) ON so.so_id = sol.so_id\n"
                    "Always filter: WHERE so.status = 'closed'\n\n"
                    "• Total Unique Customers: SELECT COUNT(DISTINCT so.customer_id) AS value FROM sales_order so WHERE so.status = 'closed'\n"
                    "• Total Customer Revenue: SELECT SUM(so.total_amount) AS value FROM sales_order so WHERE so.status = 'closed'\n"
                    "• Average Order Value (AOV): SELECT ROUND(AVG(so.total_amount), 2) AS value FROM sales_order so WHERE so.status = 'closed'\n"
                    "• Average Orders per Customer: SELECT ROUND(COUNT(*)::numeric / NULLIF(COUNT(DISTINCT so.customer_id), 0), 2) AS value FROM sales_order so WHERE so.status = 'closed'\n"
                    "• Average Spend per Customer: SELECT ROUND(SUM(so.total_amount) / NULLIF(COUNT(DISTINCT so.customer_id), 0), 2) AS value FROM sales_order so WHERE so.status = 'closed'\n"
                    "• Customer Repeat Purchase Rate (%): "
                    "SELECT ROUND(100.0 * COUNT(DISTINCT CASE WHEN order_count > 1 THEN customer_id END) "
                    "/ NULLIF(COUNT(DISTINCT customer_id), 0), 2) AS value "
                    "FROM (SELECT customer_id, COUNT(*) AS order_count FROM sales_order WHERE status = 'closed' GROUP BY customer_id) t\n"
                    "• New vs Returning split (for CHART only — DO NOT use as KPI): "
                    "SELECT CASE WHEN order_count = 1 THEN 'New' ELSE 'Returning' END AS customer_type, "
                    "COUNT(*) AS customer_count "
                    "FROM (SELECT customer_id, COUNT(*) AS order_count FROM sales_order WHERE status = 'closed' GROUP BY customer_id) t "
                    "GROUP BY customer_type ORDER BY customer_type\n"
                    "• Top customers by spend (for CHART): "
                    "SELECT cm.customer_name, SUM(so.total_amount) AS total_spend "
                    "FROM sales_order so JOIN customer_master cm ON so.customer_id = cm.customer_id "
                    "WHERE so.status = 'closed' GROUP BY cm.customer_name ORDER BY total_spend DESC LIMIT 10\n"
                    "• NEVER use customer_id alone — always JOIN customer_master for the name column in charts."
                ),
                "avoid": "generic Total Revenue, generic Top Products, generic Category Distribution, generic Revenue by Region — EVERY KPI label and chart title MUST include the word 'Customer'. Charts MUST show customer-level data on the X-axis (customer names, customer segments, etc.), NOT product names or regions",
            },
            "sales": {
                "kw": ["sales report", "revenue report", "sales analysis", "revenue analysis",
                       "sales performance", "revenue performance", "total sales", "total revenue",
                       "sales overview", "revenue overview", "sales dashboard", "revenue dashboard",
                       "sales summary", "revenue summary", "monthly sales", "monthly revenue",
                       "yearly sales", "yearly revenue", "quarterly sales"],
                "focus": "SALES and REVENUE performance, trends, and key business metrics",
                "metrics": [
                    "Total revenue (sum of all completed orders)",
                    "Total number of orders placed",
                    "Average order value (AOV)",
                    "Total units sold",
                    "Revenue trend over time (monthly line chart)",
                    "Revenue by product category (bar/pie chart)",
                    "Top products by revenue (horizontal bar chart)",
                    "Top customers by revenue (horizontal bar chart)",
                    "Order volume trend (line chart — orders per month)",
                    "Revenue vs order count correlation (dual-axis or stacked bar)",
                    "Sales status distribution (pie/doughnut — completed vs cancelled etc.)",
                    "Revenue by sales channel or region (bar chart)",
                ],
                "avoid": "inventory, procurement, or vendor metrics that are unrelated to sales — every KPI and chart must be SALES/REVENUE-centric",
            },
            "product": {
                "kw": ["product analysis", "product performance", "product report",
                       "best selling", "product mix", "sku analysis", "variant",
                       "top 10 product", "top 5 product", "top product"],
                "focus": "PRODUCT-level performance and comparison",
                "metrics": [
                    "Total products sold", "Best/worst selling products",
                    "Average revenue per product", "Product category breakdown",
                    "Price point distribution", "Product sales trend",
                    "Top products by volume vs revenue", "Product margin analysis",
                ],
                "avoid": "generic order/customer metrics — every KPI and chart must be PRODUCT-centric",
            },
            "inventory": {
                "kw": ["inventory", "stock", "overstock", "understock",
                       "stock level", "warehouse", "finished goods"],
                "focus": "INVENTORY levels, stock health, and turnover",
                "metrics": [
                    "Total SKUs in stock", "Overstocked items count",
                    "Low/out-of-stock items", "Stock value by category",
                    "Stock health distribution", "Top overstocked SKUs",
                    "Stock turnover indicators", "Category-wise stock levels",
                ],
                "avoid": "revenue or customer metrics — every KPI and chart must be INVENTORY-centric",
            },
            "vendor": {
                "kw": ["vendor", "purchase order", "supplier", "procurement",
                       "vendor analysis", "po analysis",
                       "top 10 vendor", "top vendor", "best vendor"],
                "focus": "VENDOR performance and procurement analysis",
                "metrics": [
                    "Total active vendors", "Total PO value", "Average PO value",
                    "Top vendors by value", "PO volume trend",
                    "Vendor concentration", "PO status distribution",
                ],
                "avoid": "sales order or customer metrics — every KPI and chart must be VENDOR/PROCUREMENT-centric",
            },
            "material": {
                "kw": ["gold", "diamond", "karat", "carat", "material",
                       "making charges", "material cost", "component cost"],
                "focus": "MATERIAL and component cost breakdown",
                "metrics": [
                    "Total gold cost (SUM of gold_amount_per_unit × quantity)",
                    "Total diamond cost (SUM of diamond_amount_per_unit × quantity)",
                    "Total making charges (SUM of making_charges_per_unit × quantity)",
                    "Gold cost by karat type (gold_kt from sales_order_line_gold)",
                    "Material cost trend over time (monthly line chart)",
                    "Top products by gold cost (horizontal bar)",
                    "Cost component ratio — gold vs diamond vs making charges (pie/doughnut)",
                    "Gold weight distribution by karat (bar chart)",
                ],
                "sql_notes": (
                    "CRITICAL SQL NOTES for this report:\n"
                    "• gold_amount_per_unit, diamond_amount_per_unit, making_charges_per_unit\n"
                    "  are columns on sales_order_line_pricing (alias: lp) — use them directly.\n"
                    "• Total Gold Cost KPI: SELECT SUM(lp.gold_amount_per_unit * sol.quantity) AS value\n"
                    "  FROM sales_order so JOIN sales_order_line sol ON so.so_id = sol.so_id\n"
                    "  JOIN sales_order_line_pricing lp ON sol.sol_id = lp.sol_id WHERE so.status = 'closed'\n"
                    "• Total Diamond Cost KPI: same structure but SUM(lp.diamond_amount_per_unit * sol.quantity)\n"
                    "• Total Making Charges KPI: same structure but SUM(lp.making_charges_per_unit * sol.quantity)\n"
                    "• NEVER use SUM(lp.line_total) for a specific component — line_total = ALL costs combined.\n"
                    "• gold_kt (karat type) lives on sales_order_line_gold (alias: solg) — join it for karat charts.\n"
                    "• diamond_type lives on sales_order_line_diamond (alias: sold) — join for diamond type charts."
                ),
                "avoid": "SUM(line_total) for any single component cost; generic revenue metrics unrelated to materials",
            },
            "order": {
                "kw": ["order analysis", "order pattern", "order report",
                       "order trend", "order frequency", "order status"],
                "focus": "ORDER-level patterns and fulfillment",
                "metrics": [
                    "Total orders", "Average order value", "Order frequency trend",
                    "Cancellation rate", "Average items per order",
                    "Order status distribution", "Order value distribution",
                    "Peak ordering periods",
                ],
                "avoid": "product-level or customer-level detail — focus on ORDER metrics",
            },
            "aov": {
                "kw": ["aov", "average order value", "order value", "basket size",
                       "basket value", "avg order", "average order", "per order"],
                "focus": "AVERAGE ORDER VALUE (AOV) analysis, trends, and segmentation",
                "metrics": [
                    "Overall AOV (total revenue ÷ total orders)",
                    "Median Order Value (use PERCENTILE_CONT(0.5) WITHIN GROUP)",
                    "Highest Single Order Value (MAX of total_amount)",
                    "Orders Above Average (count of orders > overall AOV)",
                    "AOV Year-over-Year Change % (calculate actual percentage)",
                    "Top Product by AOV Contribution (product NAME, not count)",
                    "AOV by product category (bar chart — category vs avg order value)",
                    "AOV by customer tier (horizontalBar — top/mid/low spending groups)",
                    "AOV trend over time (line — monthly AOV, ONLY if 6+ months)",
                    "Order value distribution (doughnut — order size buckets: <1L, 1-5L, 5-10L, >10L)",
                    "Top 10 products by AOV contribution (horizontalBar)",
                    "AOV comparison by order status (bar — closed vs open vs processing)",
                ],
                "sql_notes": (
                    "CRITICAL SQL NOTES for AOV reports:\n"
                    "• AOV = ROUND(SUM(so.total_amount) / NULLIF(COUNT(*), 0), 2) AS value\n"
                    "  FROM sales_order so WHERE so.status = 'closed'\n"
                    "• AOV by category: SELECT pm.category, ROUND(AVG(so.total_amount), 2) AS avg_order_value\n"
                    "  FROM sales_order so JOIN sales_order_line sol ON so.so_id = sol.so_id\n"
                    "  JOIN product_master pm ON sol.product_id = pm.product_id\n"
                    "  WHERE so.status = 'closed' GROUP BY pm.category ORDER BY avg_order_value DESC\n"
                    "• Order value buckets: SELECT CASE\n"
                    "    WHEN so.total_amount < 100000 THEN 'Below ₹1L'\n"
                    "    WHEN so.total_amount < 500000 THEN '₹1L-5L'\n"
                    "    WHEN so.total_amount < 1000000 THEN '₹5L-10L'\n"
                    "    ELSE 'Above ₹10L' END AS order_bucket, COUNT(*) AS order_count\n"
                    "  FROM sales_order so WHERE so.status = 'closed' GROUP BY order_bucket ORDER BY order_count DESC\n"
                    "• YoY AOV change: Use subqueries for current vs previous year, calculate % difference\n"
                    "• For February-specific queries: add WHERE EXTRACT(MONTH FROM so.order_date) = 2\n"
                    "• NEVER return raw counts as KPI values for 'Top Product' — return the NAME"
                ),
                "avoid": "generic revenue/order count metrics that ignore AOV — every KPI and chart must be about ORDER VALUE, not just counts or totals. Do NOT make all charts 'by year' — show different dimensions",
            },
            "comparison": {
                "kw": ["compare", "comparison", "versus", "vs", "across",
                       "between", "difference", "contrast", "benchmark"],
                "focus": "COMPARATIVE analysis across multiple dimensions, periods, or segments",
                "metrics": [
                    "Metric value for period/segment A vs B",
                    "Percentage difference between compared items",
                    "Absolute change (delta) between segments",
                    "Best performing segment/period (return NAME, not count)",
                    "Worst performing segment/period (return NAME, not count)",
                    "Growth rate % between compared periods",
                    "Side-by-side comparison (bar chart — grouped by dimension)",
                    "Trend comparison (line chart — multiple series, only if 6+ points)",
                    "Share breakdown (pie/doughnut — proportion of each segment)",
                    "Ranking of compared items (horizontalBar)",
                    "Category-level comparison (stackedBar — multi-series)",
                    "Distribution across segments (doughnut)",
                ],
                "sql_notes": (
                    "CRITICAL SQL NOTES for comparison reports:\n"
                    "• Use GROUP BY for the compared dimension (year, month, category, etc.)\n"
                    "• For year-over-year: EXTRACT(YEAR FROM so.order_date) AS year\n"
                    "• For month filtering: EXTRACT(MONTH FROM so.order_date) = N\n"
                    "• For growth %: ROUND(100.0 * (new_val - old_val) / NULLIF(old_val, 0), 2)\n"
                    "• For best/worst KPIs: use ORDER BY + LIMIT 1 and return the NAME/label\n"
                    "• IMPORTANT: Do NOT make all 6 charts show the same dimension (e.g. all 'by year').\n"
                    "  Instead, compare across DIFFERENT angles: by year, by category, by customer,\n"
                    "  by product, by order size, etc.\n"
                    "• Use bar charts for ≤5 comparison items, NOT line charts"
                ),
                "avoid": "making all charts identical (all 'by year') — each chart must compare a DIFFERENT dimension. Do NOT use line/area for ≤5 data points.",
            },
        }

        # Score all themes and pick the top 2 matching ones
        scored = []
        for tid, t in themes.items():
            score = sum(1 for kw in t["kw"] if kw in q)
            if score > 0:
                scored.append((score, tid))
        scored.sort(reverse=True)

        if not scored:
            return ""

        # Combine top 2 frameworks (e.g., "compare AOV" → aov + comparison)
        matched_ids = [s[1] for s in scored[:2]]

        result_parts = []
        for mid in matched_ids:
            t = themes[mid]
            metrics_list = "\n".join(f"  • {m}" for m in t["metrics"])
            sql_notes_block = ""
            if t.get("sql_notes"):
                sql_notes_block = f"\n📌 SQL FORMULAS — FOLLOW EXACTLY:\n{t['sql_notes']}\n"
            part = (
                f"\n══════════════════════════════════════════════════\n"
                f"🚨 MANDATORY REPORT FOCUS: {t['focus']}\n"
                f"══════════════════════════════════════════════════\n"
                f"YOU MUST generate KPIs and charts from this list:\n"
                f"{metrics_list}\n"
                f"{sql_notes_block}\n"
                f"🚫 STRICTLY FORBIDDEN: {t['avoid']}\n"
                f"🚨 Every KPI label and chart title MUST relate to: {t['focus']}\n"
                f"🚨 If a KPI or chart does NOT directly measure {mid.upper()}-level data, DELETE it and replace with one from the list above.\n"
                f"══════════════════════════════════════════════════"
            )
            result_parts.append(part)

        logger.info("Analytical framework(s): %s", ", ".join(f"{mid}(score={s})" for s, mid in scored[:2]))

        # ── Universal KPI quality enforcement (appended to ALL frameworks) ──
        universal_kpi_rules = (
            "\n══════════════════════════════════════════════════"
            "\n⛔ MANDATORY KPI QUALITY RULES (APPLY TO ALL REPORTS)"
            "\n══════════════════════════════════════════════════"
            "\nThe following KPI labels are PERMANENTLY BANNED:"
            "\n  ✗ 'Revenue Growth' — growth requires a baseline comparison; use 'Total Revenue' instead"
            "\n  ✗ 'Sales Growth' — same reason; use 'Total Sales Value' instead"
            "\n  ✗ 'Top-Selling Product Category' — this is a NAME, not a scalar; use 'Product Categories Count' instead"
            "\n  ✗ 'Top-Selling Product' — this is a list/ranking metric, not a KPI"
            "\n  ✗ Any KPI with 'Growth' in the label UNLESS the SQL calculates an actual % change"
            "\n  ✗ Any KPI with 'Trend' in the label — trends are charts, not single values"
            "\n  ✗ Any KPI with 'Distribution' or 'Breakdown' — these are chart metrics"
            "\n"
            "\nEACH of the 6 KPIs MUST:"
            "\n  ✓ Return a UNIQUE numeric value (no two KPIs may share the same number)"
            "\n  ✓ Use a DIFFERENT SQL query (different aggregate function or different WHERE clause)"
            "\n  ✓ Be a meaningful scalar: COUNT, SUM, AVG, MAX, MIN, or a calculated RATIO"
            "\n  ✓ Have a label that clearly describes what the NUMBER represents"
            "\n"
            "\nGOOD KPI examples: Total Revenue, Total Orders, Average Order Value, "
            "Unique Customers, Total Quantity Sold, Order Fulfillment Rate (%)"
            "\nBAD KPI examples: Revenue Growth, Sales Growth, Top-Selling Product, "
            "Revenue Trend, Category Distribution"
            "\n══════════════════════════════════════════════════"
        )
        result_parts.append(universal_kpi_rules)

        return "\n".join(result_parts)

    @staticmethod
    def _repair_json(text: str) -> str:
        """Best-effort repair of common LLM JSON generation errors.

        Handles:
        1. Single-quoted keys/values -> double-quoted
        2. Literal newlines / tabs / carriage-returns inside string values
        3. Trailing commas before } or ]
        4. Truncated JSON (missing closing braces/brackets)
        """
        # ── Pass 0: if the text looks like Python dict (single quotes), convert ──
        # Only attempt if there is no " in the text but there are '
        if "'" in text and '"' not in text:
            import re as _re0
            # Replace 'key': pattern
            text = _re0.sub(r"(?<=[{,\[]\s*)'", '"', text)
            text = _re0.sub(r"'(?=\s*[:\}\],])", '"', text)

        # ── Pass 1: escape unescaped control chars inside string literals ──
        result: list[str] = []
        in_string = False
        escape_next = False

        for ch in text:
            if escape_next:
                result.append(ch)
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                result.append(ch)
                continue
            if ch == '"':
                in_string = not in_string
                result.append(ch)
                continue
            if in_string:
                if ch == "\n":
                    result.append("\\n")
                elif ch == "\r":
                    result.append("\\r")
                elif ch == "\t":
                    result.append("\\t")
                else:
                    result.append(ch)
            else:
                result.append(ch)

        text = "".join(result)

        # ── Pass 2: remove trailing commas before } or ] ───────────────────
        import re as _re
        text = _re.sub(r",(\s*[}\]])", r"\1", text)

        # ── Pass 3: close any truncated JSON ──────────────────────────────
        # Count unmatched { and [
        depth_brace = 0
        depth_bracket = 0
        in_str = False
        esc = False
        for ch in text:
            if esc:
                esc = False
                continue
            if ch == "\\" and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if not in_str:
                if ch == "{":
                    depth_brace += 1
                elif ch == "}":
                    depth_brace -= 1
                elif ch == "[":
                    depth_bracket += 1
                elif ch == "]":
                    depth_bracket -= 1

        # If we ended mid-string, close it first
        if in_str:
            text += '"'
        # Close any open arrays before open objects
        if depth_bracket > 0:
            text += "]" * depth_bracket
        if depth_brace > 0:
            text += "}" * depth_brace

        return text

    @staticmethod
    def _extract_json(raw: str) -> dict:
        """Extract and parse JSON from LLM output with multi-stage repair."""
        text = raw.strip()

        # ── Strip markdown code fences ────────────────────────────────────
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines).strip()

        # ── Find JSON object boundaries ───────────────────────────────────
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]

        # ── Stage 1: direct parse ─────────────────────────────────────────
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # ── Stage 2: repair then parse ────────────────────────────────────
        repaired = ReportPipeline._repair_json(text)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

        # ── Stage 3: re-extract boundaries after repair and retry ─────────
        start = repaired.find("{")
        end = repaired.rfind("}")
        if start != -1 and end != -1 and end > start:
            repaired = repaired[start:end + 1]

        return json.loads(repaired)  # let the caller handle any final exception

    @staticmethod
    def _fix_kpi_sql(sql: str) -> str:
        """Detect and fix KPI SQL that returns multiple rows instead of a single aggregate.

        Common LLM mistakes:
        - SELECT name, SUM(x) ... GROUP BY name ORDER BY ... LIMIT 10  (ranking, not KPI)
        - SELECT col FROM ... LIMIT 5  (list, not aggregate)

        Fix strategy: wrap multi-row queries into a COUNT or SUM aggregate.
        """
        if not sql:
            return sql

        sql_upper = ' '.join(sql.upper().split())

        # Detect multi-row patterns: GROUP BY with multiple result rows
        has_group_by = 'GROUP BY' in sql_upper

        # Check for LIMIT > 1 (LIMIT 10, LIMIT 5, etc.)
        limit_match = re.search(r'LIMIT\s+(\d+)', sql_upper)
        has_multi_limit = limit_match and int(limit_match.group(1)) > 1

        # Check if the SELECT already looks like a single aggregate (no GROUP BY)
        # e.g., SELECT COUNT(*), SELECT SUM(x) — these are fine
        select_match = re.search(r'SELECT\s+(.*?)\s+FROM', sql_upper, re.DOTALL)
        if select_match:
            select_clause = select_match.group(1).strip()
            # If it's a pure aggregate (no comma-separated non-agg columns), it's fine
            agg_funcs = ['COUNT(', 'SUM(', 'AVG(', 'MIN(', 'MAX(', 'ROUND(']
            is_pure_aggregate = (
                any(select_clause.startswith(f) for f in agg_funcs)
                and ',' not in select_clause
                and not has_group_by
            )
            if is_pure_aggregate:
                return sql  # Already a proper KPI query

        # ── Detect correlated subquery (GroupingError source) ────────────────
        # Pattern: the SQL itself contains a nested SELECT that references outer
        # table aliases — PostgreSQL rejects this in a scalar context.
        # Fix: strip the inner subquery and rewrite as a flat aggregate.
        correlated_subq = re.search(
            r'\(\s*SELECT\b.*?\bWHERE\b.*?\b(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)',
            sql, re.IGNORECASE | re.DOTALL
        )
        if correlated_subq:
            # Best-effort: remove the problematic subquery clause entirely,
            # keeping the outer aggregate structure. The repair loop will
            # regenerate a clean version via LLM if this produces bad SQL.
            sql = re.sub(
                r'/\s*\(\s*SELECT\b[^)]+\)\s*/',
                '',
                sql, flags=re.IGNORECASE | re.DOTALL
            )
            sql_upper = ' '.join(sql.upper().split())
            has_group_by = 'GROUP BY' in sql_upper
            logger.info("KPI SQL auto-fix: stripped correlated subquery")

        if has_group_by or has_multi_limit:
            # This is a ranking/list query, not a KPI
            # Wrap it: SELECT COUNT(*) AS value FROM (original) sub
            # But first, try to detect the value column to SUM it instead
            if has_group_by and select_match:
                # Find the aggregate column (last column in SELECT)
                cols = select_match.group(1).strip()
                # Try to find the aggregate expression
                agg_match = re.search(
                    r'(SUM|COUNT|AVG|MIN|MAX)\s*\([^)]+\)',
                    cols, re.IGNORECASE
                )
                if agg_match:
                    agg_expr = agg_match.group(0)
                    # Remove the GROUP BY and everything after, replace SELECT
                    # to get a total aggregate
                    fixed = re.sub(
                        r'SELECT\s+.*?\s+FROM',
                        f'SELECT {agg_expr} AS value FROM',
                        sql, count=1, flags=re.IGNORECASE | re.DOTALL
                    )
                    fixed = re.sub(
                        r'\s+GROUP\s+BY\s+.*$',
                        '', fixed, flags=re.IGNORECASE
                    )
                    fixed = re.sub(
                        r'\s+ORDER\s+BY\s+.*$',
                        '', fixed, flags=re.IGNORECASE
                    )
                    fixed = re.sub(
                        r'\s+LIMIT\s+\d+',
                        '', fixed, flags=re.IGNORECASE
                    )
                    logger.info("KPI SQL auto-fix: converted ranking query to aggregate")
                    return fixed.strip()

            # Fallback: wrap the entire query in a COUNT
            clean_sql = sql.rstrip(';').strip()
            wrapped = f"SELECT COUNT(*) AS value FROM ({clean_sql}) _kpi_sub"
            logger.info("KPI SQL auto-fix: wrapped multi-row query in COUNT(*)")
            return wrapped

        return sql

    @staticmethod
    def _format_indian(num: float) -> str:
        """Format a numeric value into Indian notation (crores/lakhs)."""
        abs_num = abs(num)
        if abs_num >= 1e7:
            return f"₹{num / 1e7:.2f} crores"
        if abs_num >= 1e5:
            return f"₹{num / 1e5:.2f} lakhs"
        return f"₹{num:,.2f}"

    # ── Bad KPI label patterns (chart-type metrics, not single values) ──
    _BAD_KPI_RE = re.compile(
        r'\btop[\s-]+\d+\b'          # "Top 5 Vendors", "Top-10 Products"
        r'|\btop[\s-]+selling\b'     # "Top-Selling Products" — list metric
        r'|\bbest[\s-]+selling\b'    # "Best Selling" — list metric
        r'|\btrend\b'               # "Revenue Trend" — chart metric
        r'|\bgrowth\s+(?:trend|over|chart|timeline)\b'  # "Growth Trend/Over Time" — chart metric
        r'|\b(?:revenue|sales|order|customer|product|vendor|purchase)\s+growth\b'  # standalone "Revenue Growth" etc.
        r'|\bgrowth\s+rate\b'        # "Growth Rate" — unmeasurable as single scalar
        r'|\byear[- ]over[- ]year\b' # "Year-over-Year" without actual % calculation
        r'|\bmonth[- ]over[- ]month\b' # "Month-over-Month" without actual % calculation
        r'|\b(?:yoy|mom)\s+(?:growth|change|variance)\b'  # abbreviated growth patterns
        r'|\bdistribution\b'        # "Customer Distribution" — chart metric
        r'|\bbreakdown\b'           # "Category Breakdown" — chart metric
        r'|\bconcentration\b'       # "Vendor Concentration" — not a scalar
        r'|\bcomposition\b'         # "Revenue Composition" — chart metric
        r'|\branking\b'             # "Product Ranking" — list metric
        r'|\blist\b'                # "Product List" — not a KPI
        r'|\boverview\b'            # "Sales Overview" — too vague
        r'|\bby\s+\w+\b'            # "Revenue By Month" — chart metric
        r'|\bover\s+time\b'         # "Sales Over Time" — chart metric
        r'|\bmost\s+\w+\b'          # "Most Popular" — list metric
        r'|\bbottom\s+\d+\b',       # "Bottom 5" — list metric
        re.IGNORECASE,
    )

    # ── Semantic KPI label normalization (for synonym detection) ──
    _KPI_SYNONYMS = {
        'total': '', 'overall': '', 'aggregate': '', 'cumulative': '',
        'net': '', 'gross': '', 'all': '', 'entire': '',
    }

    @staticmethod
    def _normalize_kpi_label(label: str) -> str:
        """Normalize a KPI label to a canonical form for semantic comparison.

        Strips filler words so 'Total Revenue' == 'Overall Revenue' == 'Revenue'.
        """
        words = label.lower().strip().split()
        significant = [w for w in words if w not in ReportPipeline._KPI_SYNONYMS]
        return ' '.join(significant) if significant else label.lower().strip()

    @staticmethod
    def _sql_signature(sql: str) -> str:
        """Extract a signature from a SQL query for similarity comparison.

        Normalizes whitespace, removes aliases, and extracts key FROM/WHERE/GROUP.
        Two KPIs with the same signature are semantically redundant.
        """
        if not sql:
            return ''
        s = ' '.join(sql.upper().split())
        # Extract the core structure: FROM tables + WHERE conditions + aggregation
        parts = []
        from_match = re.search(r'FROM\s+(.+?)\s*(?:WHERE|GROUP|ORDER|LIMIT|$)', s)
        if from_match:
            parts.append('FROM:' + from_match.group(1).strip()[:80])
        where_match = re.search(r'WHERE\s+(.+?)\s*(?:GROUP|ORDER|LIMIT|$)', s)
        if where_match:
            parts.append('WHERE:' + where_match.group(1).strip()[:80])
        # Extract the aggregate function
        agg_match = re.search(r'(SUM|COUNT|AVG|MIN|MAX)\s*\([^)]+\)', s)
        if agg_match:
            parts.append('AGG:' + agg_match.group(0))
        return '|'.join(parts)

    def _clean_kpis(self, kpis: list) -> list:
        """Clean, validate and deduplicate KPIs.

        1. Remove KPIs with N/A, None or error values (zero-valued are held as fallback)
        2. Remove KPIs whose labels indicate chart-type metrics (top N, trend, etc.)
        3. Deduplicate KPIs with identical numeric values
        4. Guarantee at least MIN_KPIS=6 survive by restoring filtered KPIs in order:
           value-duplicates → bad-pattern KPIs → zero-valued KPIs → error KPIs (last resort)
        """
        MIN_KPIS = 6
        MAX_KPIS = 6
        original_count = len(kpis)

        _BAD_VALUES = {None, "", "N/A", "null", "None", "none", "n/a", "NaN", "nan"}

        # Step 1: separate non-zero, zero-valued, broken, and error KPIs
        non_zero: list = []
        zero_kpis: list = []
        error_kpis: list = []  # KPIs with SQL errors — last resort
        for k in kpis:
            v = k.get("value")
            if k.get("error"):
                error_kpis.append(k)
                continue
            if v is None:
                error_kpis.append(k)
                continue
            s = str(v).strip()
            if s in _BAD_VALUES:
                error_kpis.append(k)
                continue
            try:
                if float(v) == 0:
                    zero_kpis.append(k)
                    continue
            except (ValueError, TypeError):
                pass
            non_zero.append(k)

        # Step 2: reject bad-pattern labels; keep rejects as fallback candidates
        pattern_ok: list = []
        pattern_bad: list = []
        for k in non_zero:
            if self._BAD_KPI_RE.search(k.get("label", "")):
                pattern_bad.append(k)
            else:
                pattern_ok.append(k)

        if pattern_bad:
            logger.info("Removed %d bad-pattern KPIs (top-N/trend/distribution/breakdown)",
                        len(pattern_bad))

        # Step 3: deduplicate identical numeric values; keep rejects as fallback
        seen: dict = {}
        deduped: list = []
        value_dupes: list = []
        for k in pattern_ok:
            try:
                norm = round(float(k.get("value", 0)), 2)
            except (ValueError, TypeError):
                norm = k.get("value")
            if norm not in seen:
                seen[norm] = k.get("label", "?")
                deduped.append(k)
            else:
                logger.info("Duplicate KPI '%s' (same value as '%s')",
                            k.get("label", "?"), seen[norm])
                value_dupes.append(k)

        # Step 3b: semantic deduplication — detect synonym labels or similar SQL
        label_seen: dict = {}   # normalized_label → index in deduped
        sql_seen: dict = {}     # sql_signature → index in deduped
        semantic_dupes: list = []
        clean_deduped: list = []
        for k in deduped:
            norm_label = self._normalize_kpi_label(k.get("label", ""))
            sql_sig = self._sql_signature(k.get("sql", ""))

            # Check if a KPI with a very similar label already exists
            is_label_dupe = norm_label in label_seen and len(norm_label) > 3
            # Check if a KPI with the same SQL signature exists
            is_sql_dupe = sql_sig in sql_seen and len(sql_sig) > 10

            if is_label_dupe:
                logger.info("Semantic dedup: '%s' is synonym of '%s' (label match)",
                            k.get("label", "?"), deduped[label_seen[norm_label]].get("label", "?"))
                semantic_dupes.append(k)
            elif is_sql_dupe:
                logger.info("Semantic dedup: '%s' has same SQL signature as '%s'",
                            k.get("label", "?"), deduped[sql_seen[sql_sig]].get("label", "?"))
                semantic_dupes.append(k)
            else:
                if norm_label and len(norm_label) > 3:
                    label_seen[norm_label] = len(clean_deduped)
                if sql_sig and len(sql_sig) > 10:
                    sql_seen[sql_sig] = len(clean_deduped)
                clean_deduped.append(k)

        if semantic_dupes:
            logger.info("Removed %d semantically duplicate KPIs", len(semantic_dupes))
            # Add semantic dupes to value_dupes pool for fallback restoration
            value_dupes.extend(semantic_dupes)

        deduped = clean_deduped

        # Cap at MAX_KPIS to keep the report focused
        deduped = deduped[:MAX_KPIS]

        # ── Priority 0: Generate FRESH replacement KPIs via chat pipeline ─────
        # When bad/duplicate KPIs are removed, try to generate genuinely new ones
        # instead of recycling the same bad pool.
        if len(deduped) < MIN_KPIS:
            _replacement_ideas = [
                ("Total Quantity Sold", "SELECT SUM(sol.quantity) AS value FROM sales_order so JOIN sales_order_line sol ON so.so_id = sol.so_id WHERE so.status = 'closed'"),
                ("Unique Products Sold", "SELECT COUNT(DISTINCT sol.product_id) AS value FROM sales_order so JOIN sales_order_line sol ON so.so_id = sol.so_id WHERE so.status = 'closed'"),
                ("Unique Active Customers", "SELECT COUNT(DISTINCT so.customer_id) AS value FROM sales_order so WHERE so.status = 'closed'"),
                ("Average Line Total", "SELECT ROUND(AVG(solp.line_total)::numeric, 2) AS value FROM sales_order so JOIN sales_order_line sol ON so.so_id = sol.so_id JOIN sales_order_line_pricing solp ON sol.sol_id = solp.sol_id WHERE so.status = 'closed'"),
                ("Highest Single Order Value", "SELECT MAX(so.total_amount) AS value FROM sales_order so WHERE so.status = 'closed'"),
                ("Order Fulfillment Rate (%)", "SELECT ROUND(100.0 * COUNT(CASE WHEN status = 'closed' THEN 1 END) / NULLIF(COUNT(*), 0), 2) AS value FROM sales_order"),
                ("Total Product Categories", "SELECT COUNT(DISTINCT pm.category) AS value FROM product_master pm"),
                ("Average Items Per Order", "SELECT ROUND(AVG(item_count)::numeric, 2) AS value FROM (SELECT so.so_id, COUNT(sol.sol_id) AS item_count FROM sales_order so JOIN sales_order_line sol ON so.so_id = sol.so_id WHERE so.status = 'closed' GROUP BY so.so_id) sub"),
            ]

            # Collect existing label signatures to avoid duplicating
            _existing_labels = {self._normalize_kpi_label(k.get("label", "")) for k in deduped}
            _existing_values = set()
            for k in deduped:
                try:
                    _existing_values.add(round(float(k.get("value", 0)), 2))
                except (ValueError, TypeError):
                    pass

            _colors = ["blue", "green", "purple", "orange", "red", "teal"]
            _icons = ["revenue", "orders", "customers", "products", "growth", "average"]

            for idea_label, idea_sql in _replacement_ideas:
                if len(deduped) >= MIN_KPIS:
                    break
                # Skip if a similar label already exists
                norm = self._normalize_kpi_label(idea_label)
                if norm in _existing_labels:
                    continue

                # Execute the replacement SQL
                try:
                    idea_sql = _fix_report_sql(idea_sql)
                    result = execute_sql(idea_sql)
                    if not result["success"] or not result["data"]:
                        continue
                    row = result["data"][0]
                    val = list(row.values())[0] if row else None
                    if val is None:
                        continue
                    try:
                        float_val = round(float(val), 2)
                    except (ValueError, TypeError):
                        float_val = None

                    # Skip if this value already exists
                    if float_val is not None and float_val in _existing_values:
                        continue

                    idx = len(deduped)
                    new_kpi = {
                        "id": f"kpi_gen_{idx}",
                        "label": idea_label,
                        "sql": idea_sql,
                        "value": val,
                        "format": "percent" if "%" in idea_label or "rate" in idea_label.lower() else "number",
                        "icon": _icons[idx % len(_icons)],
                        "color": _colors[idx % len(_colors)],
                        "explanation": {
                            "what": f"Measures {idea_label.lower()}",
                            "how": "Calculated from sales data",
                            "why": "Provides additional business context",
                            "insight": f"Current value: {val}"
                        }
                    }
                    deduped.append(new_kpi)
                    _existing_labels.add(norm)
                    if float_val is not None:
                        _existing_values.add(float_val)
                    logger.info("Generated replacement KPI '%s' = %s", idea_label, val)
                except Exception as exc:
                    logger.warning("Replacement KPI '%s' failed: %s", idea_label, exc)

        # ── Guarantee MIN_KPIS — restore in priority order ────────────────
        if len(deduped) < MIN_KPIS:
            # Priority 1: value-duplicates that DON'T also have bad-pattern labels
            # (avoids restoring doubly-bad KPIs: duplicate value AND bad label)
            for k in value_dupes:
                if len(deduped) >= MIN_KPIS:
                    break
                if not self._BAD_KPI_RE.search(k.get("label", "")):
                    deduped.append(k)
                    logger.info("Restored value-duplicate KPI '%s' to meet minimum count",
                                k.get("label", "?"))

        if len(deduped) < MIN_KPIS:
            # Priority 2: bad-pattern KPIs — ONLY if their value is not already present
            # (prevents e.g. 'Top-Selling Products: ₹1031Cr' when 'Total Revenue: ₹1031Cr' exists)
            _existing_vals = set()
            for k in deduped:
                try:
                    _existing_vals.add(round(float(k.get("value", 0)), 2))
                except (ValueError, TypeError):
                    pass
            for k in pattern_bad:
                if len(deduped) >= MIN_KPIS:
                    break
                try:
                    kv = round(float(k.get("value", 0)), 2)
                except (ValueError, TypeError):
                    kv = None
                if kv not in _existing_vals:
                    deduped.append(k)
                    if kv is not None:
                        _existing_vals.add(kv)
                    logger.info("Restored bad-pattern KPI '%s' (unique value) to meet minimum count",
                                k.get("label", "?"))

        if len(deduped) < MIN_KPIS:
            # Priority 3: last resort — zero-valued KPIs with clean labels
            for k in zero_kpis:
                if len(deduped) >= MIN_KPIS:
                    break
                if not self._BAD_KPI_RE.search(k.get("label", "")):
                    deduped.append(k)
                    logger.info("Restored zero-valued KPI '%s' as last resort",
                                k.get("label", "?"))

        if len(deduped) < MIN_KPIS:
            # Priority 4: absolute last resort — any zero-valued KPI (even bad-pattern labels)
            for k in zero_kpis:
                if len(deduped) >= MIN_KPIS:
                    break
                if k not in deduped:
                    deduped.append(k)
                    logger.info("Restored zero-valued KPI '%s' (any label) as absolute last resort",
                                k.get("label", "?"))

        if len(deduped) < MIN_KPIS:
            # Priority 5: error KPIs — shown as 'Error' card in UI; better than a missing slot
            for k in error_kpis:
                if len(deduped) >= MIN_KPIS:
                    break
                deduped.append(k)
                logger.info("Restored error KPI '%s' to fill missing slot",
                            k.get("label", "?"))

        # ── Final pass: remove any duplicate-value KPIs that slipped through ──
        # Prefer the clean-label KPI when two share the same numeric value.
        final_seen: dict = {}   # norm_value → (list_index, is_bad_label)
        final_deduped: list = []
        for k in deduped:
            try:
                norm = round(float(k.get("value", 0)), 2)
            except (ValueError, TypeError):
                final_deduped.append(k)
                continue
            is_bad = bool(self._BAD_KPI_RE.search(k.get("label", "")))
            if norm not in final_seen:
                final_seen[norm] = (len(final_deduped), is_bad)
                final_deduped.append(k)
            elif is_bad:
                logger.info(
                    "Final dedup: dropped bad-label KPI '%s' (duplicate value of existing)",
                    k.get("label", "?"),
                )
            else:
                existing_idx, existing_is_bad = final_seen[norm]
                if existing_is_bad:
                    # Replace the earlier bad-label KPI with this cleaner one
                    final_deduped[existing_idx] = k
                    final_seen[norm] = (existing_idx, False)
                    logger.info(
                        "Final dedup: replaced bad-label KPI with cleaner '%s'",
                        k.get("label", "?"),
                    )
                else:
                    logger.info(
                        "Final dedup: dropped duplicate-value KPI '%s'",
                        k.get("label", "?"),
                    )
        # Floor: if final dedup left too few KPIs, restore clean-label KPIs
        # (e.g. all 6 KPIs computed identical values due to LLM SQL mistakes —
        # showing 2-3 "same value" KPIs is better than showing 1)
        _FINAL_DEDUP_FLOOR = 3
        if len(final_deduped) < _FINAL_DEDUP_FLOOR:
            for k in deduped:
                if len(final_deduped) >= _FINAL_DEDUP_FLOOR:
                    break
                if k not in final_deduped:
                    if not self._BAD_KPI_RE.search(k.get("label", "")):
                        final_deduped.append(k)
                        logger.info(
                            "Final dedup floor: restored clean-label KPI '%s' to reach minimum %d",
                            k.get("label", "?"), _FINAL_DEDUP_FLOOR,
                        )

        deduped = final_deduped

        if len(deduped) != original_count:
            logger.info("KPIs after cleanup: %d of %d valid", len(deduped), original_count)
        return deduped

    def _execute_kpi_sql(self, kpi: dict) -> dict:
        """Execute a KPI's SQL and populate its value.

        If the initial SQL returns zero/null/error, attempts regeneration
        via the chat pipeline's full chain for data accuracy.
        """
        sql = kpi.get("sql", "")
        if not sql:
            kpi["value"] = "N/A"
            kpi["error"] = "No SQL provided"
            return kpi

        # ── Fix column aliases and table references first (all 7 passes) ──
        sql = _fix_report_sql(sql)
        # ── Then convert ranking queries to scalar aggregates ───────────
        sql = self._fix_kpi_sql(sql)

        kpi_label = kpi.get("label", kpi.get("id", "?"))
        sql, result = self._validate_and_execute_sql(
            sql, context=f"KPI:{kpi_label}",
            sql_description=f"Calculate a single numeric value for: {kpi_label}. The SQL MUST return exactly ONE row with ONE numeric value. Do NOT use GROUP BY or LIMIT > 1.",
        )
        kpi["sql"] = sql  # store corrected SQL

        if not result["success"]:
            kpi["value"] = "N/A"
            kpi["error"] = result["error"]
            # ── Attempt regeneration via chat pipeline ──────────────────
            kpi = self._try_regenerate_kpi(kpi)
            return kpi

        data = result["data"]
        if data and len(data) > 0:
            # ── If result has multiple rows, auto-aggregate ────────────
            if len(data) > 1:
                logger.warning(
                    "KPI '%s' returned %d rows — expected 1. Auto-aggregating.",
                    kpi_label, len(data)
                )
                # Try to sum all numeric values across rows
                first_row = data[0]
                value_cols = [k for k, v in first_row.items()
                              if isinstance(v, (int, float)) or
                              (v is not None and str(v).replace('.', '').replace('-', '').isdigit())]
                if value_cols:
                    # Use the last numeric column (usually the aggregate)
                    val_col = value_cols[-1]
                    total = 0
                    for row in data:
                        try:
                            total += float(row.get(val_col, 0) or 0)
                        except (ValueError, TypeError):
                            pass
                    kpi["value"] = total
                    return kpi
                else:
                    # No numeric column — just count the rows
                    kpi["value"] = len(data)
                    return kpi

            first_row = data[0]
            if not first_row:
                kpi["value"] = "N/A"
                return kpi

            values = list(first_row.values())
            numeric_val = None
            for v in reversed(values):
                if v is not None and isinstance(v, (int, float)):
                    numeric_val = v
                    break
                try:
                    numeric_val = float(v)
                    break
                except (TypeError, ValueError):
                    continue

            if numeric_val is not None:
                kpi["value"] = numeric_val
            else:
                kpi["value"] = values[0] if values else "N/A"
        else:
            kpi["value"] = "N/A"

        # ── If the value is zero or N/A, try regeneration ──────────────
        try:
            val = kpi.get("value")
            if val == "N/A" or val is None or (isinstance(val, (int, float)) and val == 0):
                kpi = self._try_regenerate_kpi(kpi)
        except Exception:
            pass

        return kpi

    def _try_regenerate_kpi(self, kpi: dict) -> dict:
        """No-op: LLM-based KPI regeneration was removed with the Groq/DSPy stack."""
        return kpi

    def _execute_chart_sql(self, chart: dict) -> dict:
        """Execute a chart's SQL and populate its data.

        If the SQL fails or returns empty/bad data, attempts regeneration
        via the chat pipeline for data accuracy.
        """
        sql = chart.get("sql", "")
        if not sql:
            chart["data"] = []
            chart["error"] = "No SQL provided"
            return chart

        chart_title = chart.get("title", chart.get("id", "?"))
        chart_type = chart.get("type", "bar")
        sql, result = self._validate_and_execute_sql(
            sql, context=f"Chart:{chart_title}",
            sql_description=f"Query data for chart: {chart_title}. Return rows with a label column and a value column.",
        )
        chart["sql"] = sql  # store corrected SQL

        if not result["success"]:
            chart["data"] = []
            chart["error"] = result["error"]
            # ── Attempt regeneration via chat pipeline ──────────────────
            chart = self._try_regenerate_chart(chart)
            return chart

        chart["data"] = result["data"]

        # ── Validate chart data quality ─────────────────────────────────
        chart = self._validate_chart_data(chart)

        return chart

    def _validate_chart_data(self, chart: dict) -> dict:
        """Check chart data for quality issues and attempt regeneration if bad.

        Detects:
        - Empty data (0 rows)
        - Single-column data (missing label or value)
        - All-zero or all-null value columns
        - Single row (KPI-style result, not chart-worthy)
        """
        data = chart.get("data", [])
        chart_title = chart.get("title", "?")

        if not data or len(data) == 0:
            logger.info("Chart '%s' — empty data, attempting regeneration", chart_title)
            return self._try_regenerate_chart(chart)

        # Check column count
        keys = list(data[0].keys())
        if len(keys) < 2:
            logger.info("Chart '%s' — only %d column(s), attempting regeneration", chart_title, len(keys))
            return self._try_regenerate_chart(chart)

        # Check if all numeric values are zero/null
        value_keys = keys[1:]
        _bad_vals = {None, 0, "", "0", 0.0}
        all_bad = all(
            all(row.get(k) in _bad_vals for k in value_keys)
            for row in data
        )
        if all_bad:
            logger.info("Chart '%s' — all values are zero/null, attempting regeneration", chart_title)
            return self._try_regenerate_chart(chart)

        return chart

    def _try_regenerate_chart(self, chart: dict) -> dict:
        """No-op: LLM-based chart regeneration was removed with the Groq/DSPy stack."""
        return chart

    def _execute_table_sql(self, table: dict) -> dict:
        """Execute the detail table's SQL and populate data."""
        sql = table.get("sql", "")
        if not sql:
            table["data"] = []
            return table

        table_title = table.get("title", "Detail table")
        sql, result = self._validate_and_execute_sql(
            sql, context="DetailTable",
            sql_description=f"Query detail data for: {table_title}",
        )
        table["sql"] = sql  # store corrected SQL

        if not result["success"]:
            table["data"] = []
            table["error"] = result["error"]
            return table

        table["data"] = result["data"][:200]  # Limit rows for display
        return table

    def _extract_subject_lock(self, question: str, schema_str: str, profile_str: str) -> str:
        """Dynamically extract the report subject and build a subject-locking
        instruction that gets prepended to the LLM question.

        Scans the actual schema for tables/columns matching the user's keywords
        so the LLM knows exactly which tables to JOIN/filter.
        """
        q = question.lower().strip()

        # Remove filter context injected by app.py (e.g. "[ACTIVE FILTERS: ...]")
        q = re.sub(r'\[active filters:.*?\]', '', q, flags=re.IGNORECASE).strip()
        # Remove date context injected by _build_question_with_context
        q = re.sub(r'\[context:.*?\]', '', q, flags=re.IGNORECASE).strip()

        if len(q) < 5:
            return ""

        # ── Find schema tables whose names match words in the question ──
        # Schema format is "TABLE: table_name\n    col_name  type  nullable"
        all_tables = re.findall(r'TABLE:\s*(\S+)', schema_str, re.IGNORECASE)
        matching_tables = []
        for table in all_tables:
            # Check if any word from the table name appears in the question
            table_words = table.lower().replace('_', ' ').split()
            for tw in table_words:
                if len(tw) >= 3 and tw in q and tw not in ('line', 'order', 'master', 'sales'):
                    if table not in matching_tables:
                        matching_tables.append(table)
                        break

        # ── Find matching column values from data profile ──
        matching_values = []
        if profile_str:
            for line in profile_str.split('\n'):
                line_lower = line.lower()
                if 'distinct values' not in line_lower:
                    continue
                # Check if any significant word from the question appears
                q_words = [w for w in q.split() if len(w) >= 3]
                for w in q_words:
                    if w in line_lower and w not in ('the', 'and', 'for', 'report', 'analysis', 'top'):
                        matching_values.append(line.strip())
                        break

        # ── Build the subject lock instruction ──
        lock = [
            "══════════════════════════════════════════════════",
            f"⚠ SUBJECT LOCK: \"{question.strip()}\"",
            "══════════════════════════════════════════════════",
            f"The user's EXACT request is: \"{question.strip()}\"",
            "",
            "ALL 6 KPIs, ALL 6 charts, ALL insights, and the detail table",
            "must be EXCLUSIVELY about this subject. Every KPI label and",
            "chart title must reference the subject. Every SQL query must",
            "filter/scope data to ONLY this subject using appropriate",
            "JOINs and WHERE clauses derived from the schema.",
            "",
            "Generic/unscoped components are FORBIDDEN. Each metric must",
            "be qualified with the subject (e.g., 'Subject Revenue' not",
            "'Total Revenue').",
        ]

        if matching_tables:
            lock.append("")
            lock.append("SCHEMA TABLES matching this subject:")
            for t in matching_tables[:8]:
                # Get columns for this table from schema
                table_section = re.search(
                    rf'TABLE:\s*{re.escape(t)}\n((?:\s+\S+.*\n)*)',
                    schema_str, re.IGNORECASE
                )
                cols = ""
                if table_section:
                    col_lines = table_section.group(1).strip().split('\n')
                    col_names = [cl.strip().split()[0] for cl in col_lines if cl.strip()]
                    cols = f" → columns: {', '.join(col_names[:8])}"
                lock.append(f"  • {t}{cols}")
            lock.append("JOIN these tables to scope queries to the subject.")

        if matching_values:
            lock.append("")
            lock.append("MATCHING DATA VALUES from profile:")
            for v in matching_values[:5]:
                lock.append(f"  • {v[:150]}")

        lock.append("══════════════════════════════════════════════════")

        result = "\n".join(lock)
        logger.info("Subject lock: matching_tables=%s", matching_tables[:5])
        return result

    @staticmethod
    def _cache_key(question: str) -> str:
        """Generate a deterministic cache key from the user's question.

        Normalises whitespace, lowercases, strips common filler words so
        that 'Gold product analysis' and 'gold product   analysis' hit
        the same cache entry.
        """
        q = question.lower().strip()
        # Remove injected context/filters — they change per call
        q = re.sub(r'\[active filters:.*?\]', '', q, flags=re.IGNORECASE)
        q = re.sub(r'\[context:.*?\]', '', q, flags=re.IGNORECASE)
        q = re.sub(r'\s+', ' ', q).strip()
        return hashlib.md5(q.encode()).hexdigest()

    def _smart_fix_chart_type(self, chart: dict) -> None:
        """Auto-correct chart type based on actual data patterns.

        Analyzes the first column (labels) and row count to pick the best
        chart type for the data, overriding the LLM's choice when wrong.
        Also trims excessively large datasets to keep charts readable.
        """
        data = chart.get("data")
        if not data or len(data) == 0:
            return

        chart_type = chart.get("type", "bar").lower()
        row_count = len(data)
        keys = list(data[0].keys())
        label_key = keys[0]
        value_keys = keys[1:]
        labels = [str(row.get(label_key, "")) for row in data]

        # ── Detect time-series labels (dates, months, years) ──────────
        time_patterns = [
            r"^\d{4}-\d{2}$",        # 2024-01
            r"^\d{4}-\d{2}-\d{2}$",  # 2024-01-15
            r"^\d{4}-\d{2}-\d{2}T",  # 2024-01-15T00:00:00 (ISO timestamp)
            r"^\d{4}-\d{2}-\d{2}\s", # 2024-01-15 00:00:00
            r"^\d{4}$",              # 2024
            r"^Q[1-4]\s?\d{4}$",     # Q1 2024
            r"^\w{3,9}\s?\d{4}$",    # Jan 2024 / January 2024
        ]
        import re as _re
        is_time_series = False
        if row_count >= 3:
            match_count = sum(
                1 for lbl in labels[:5]
                if any(_re.match(p, lbl.strip()) for p in time_patterns)
            )
            if match_count >= min(3, len(labels[:5])):
                is_time_series = True

        # ── Auto-clean raw timestamp labels to YYYY-MM format ─────────
        # If labels are raw timestamps (e.g., 2025-10-01T00:00:00+00:00),
        # clean them to YYYY-MM for readable chart axes
        if is_time_series:
            iso_pattern = r"^\d{4}-\d{2}-\d{2}[T\s]"
            has_raw_timestamps = any(_re.match(iso_pattern, lbl.strip()) for lbl in labels[:3])
            if has_raw_timestamps:
                # Aggregate to monthly if labels are daily timestamps
                from collections import OrderedDict
                monthly = OrderedDict()
                for row in data:
                    lbl = str(row.get(label_key, ""))
                    month_key = lbl[:7]  # "2025-10-01T..." → "2025-10"
                    if month_key not in monthly:
                        monthly[month_key] = {label_key: month_key}
                        for vk in value_keys:
                            monthly[month_key][vk] = 0
                    for vk in value_keys:
                        try:
                            monthly[month_key][vk] += float(row.get(vk, 0) or 0)
                        except (ValueError, TypeError):
                            pass
                chart["data"] = list(monthly.values())
                data = chart["data"]
                row_count = len(data)
                labels = [str(row.get(label_key, "")) for row in data]
                logger.info("Auto-clean chart '%s': cleaned timestamp labels → YYYY-MM (%d points)",
                            chart.get("title", "?"), row_count)

        # ── Fix raw numeric labels ────────────────────────────────────
        # If the first column (labels) contains large numeric values, columns
        # might be swapped. Try to detect and fix or format them.
        if not is_time_series and data and len(keys) >= 2:
            import re as _re2
            # Check if labels are all large numbers (>1000) — likely wrong column as label
            numeric_labels = 0
            for lbl in labels[:5]:
                try:
                    val = float(lbl.replace(",", ""))
                    if abs(val) > 1000:
                        numeric_labels += 1
                except (ValueError, TypeError):
                    pass

            if numeric_labels >= min(3, len(labels[:5])):
                # Check if second column has string/name values that should be labels
                second_key = keys[1] if len(keys) > 1 else None
                if second_key:
                    second_vals = [str(row.get(second_key, "")) for row in data[:5]]
                    non_numeric_count = sum(
                        1 for v in second_vals
                        if v and not v.replace(".", "").replace(",", "").replace("-", "").isdigit()
                    )
                    if non_numeric_count >= min(3, len(second_vals)):
                        # Swap columns — second column has the real labels
                        logger.info("Auto-fix chart '%s': swapping label column '%s' ↔ '%s'",
                                    chart.get("title", "?"), label_key, second_key)
                        for row in data:
                            row[label_key], row[second_key] = row[second_key], row[label_key]
                        labels = [str(row.get(label_key, "")) for row in data]
                    else:
                        # Both columns numeric — format labels for readability
                        logger.info("Auto-fix chart '%s': formatting numeric labels for readability",
                                    chart.get("title", "?"))
                        for row in data:
                            try:
                                val = float(str(row.get(label_key, 0)).replace(",", ""))
                                if abs(val) >= 1_00_00_000:
                                    row[label_key] = f"₹{val/1_00_00_000:.1f}Cr"
                                elif abs(val) >= 1_00_000:
                                    row[label_key] = f"₹{val/1_00_000:.1f}L"
                                elif abs(val) >= 1000:
                                    row[label_key] = f"₹{val/1000:.1f}K"
                                else:
                                    row[label_key] = f"₹{val:,.0f}"
                            except (ValueError, TypeError):
                                pass
                        labels = [str(row.get(label_key, "")) for row in data]

        original_type = chart_type

        # Rule 1: Time-series data → line or area (never bar/horizontalBar)
        # BUT only for dense time-series (6+ points) — ≤5 points look bad as line/area
        if is_time_series and row_count >= 6 and chart_type in ("bar", "horizontalBar", "pie", "doughnut"):
            chart["type"] = "line"
            logger.info("Auto-fix chart '%s': %s → line (dense time-series, %d points)",
                        chart.get("title", "?"), original_type, row_count)

        # Rule 2: Too many slices for pie/doughnut → trim to top 6 + "Others"
        elif chart_type in ("pie", "doughnut") and row_count > 8 and value_keys:
            first_val_key = value_keys[0]
            try:
                sorted_data = sorted(
                    data,
                    key=lambda r: float(r.get(first_val_key, 0) or 0),
                    reverse=True
                )
                top_slices = sorted_data[:6]
                others_sum = sum(float(r.get(first_val_key, 0) or 0) for r in sorted_data[6:])
                if others_sum > 0:
                    others_row = {label_key: "Others", first_val_key: others_sum}
                    top_slices.append(others_row)
                chart["data"] = top_slices
                logger.info("Auto-fix chart '%s': trimmed %d → %d slices (kept %s)",
                            chart.get("title", "?"), row_count, len(top_slices), chart_type)
            except (ValueError, TypeError):
                chart["type"] = "bar"  # fallback
                logger.info("Auto-fix chart '%s': %s → bar (trim failed)",
                            chart.get("title", "?"), original_type)

        # Rule 3: Too many categories for bar → horizontalBar
        elif chart_type == "bar" and row_count > 12 and not is_time_series:
            chart["type"] = "horizontalBar"
            logger.info("Auto-fix chart '%s': bar → horizontalBar (%d categories)",
                        chart.get("title", "?"), row_count)

        # Rule 4: Few categories in horizontalBar → regular bar
        elif chart_type == "horizontalBar" and row_count <= 6:
            chart["type"] = "bar"
            logger.info("Auto-fix chart '%s': horizontalBar → bar (only %d categories)",
                        chart.get("title", "?"), row_count)

        # ── Rule 5: Trim excessive NON-time-series data (>20) to Top 15 ──
        # Only for categorical charts, never for time-series
        updated_type = chart.get("type", chart_type).lower()
        updated_count = len(chart.get("data", data))
        if (not is_time_series
                and updated_type not in ("pie", "doughnut", "line", "area")
                and updated_count > 20
                and value_keys):
            first_val_key = value_keys[0]
            try:
                current_data = chart.get("data", data)
                sorted_data = sorted(
                    current_data,
                    key=lambda r: float(r.get(first_val_key, 0) or 0),
                    reverse=True
                )
                chart["data"] = sorted_data[:15]
                logger.info("Auto-fix chart '%s': trimmed %d → 15 rows",
                            chart.get("title", "?"), updated_count)
            except (ValueError, TypeError):
                pass

        # ── Rule 6: Remove incompatible secondary series (scale ratio > 1000x) ──
        # e.g. Revenue (crores) + Order Count (hundreds) can NOT share a Y-axis.
        # The small series becomes invisible and its tooltip shows ₹ which is wrong.
        # Fix: keep only the primary (largest magnitude) series.
        current_data = chart.get("data", data)
        if current_data:
            current_keys = list(current_data[0].keys())
            current_vkeys = current_keys[1:]
            if (len(current_vkeys) > 1
                    and chart.get("type", "bar").lower() not in ("pie", "doughnut", "stackedbar")):
                max_by_key = {}
                for vk in current_vkeys:
                    try:
                        mv = max(abs(float(row.get(vk, 0) or 0)) for row in current_data)
                        max_by_key[vk] = mv
                    except (ValueError, TypeError):
                        max_by_key[vk] = 0
                positive_maxes = {k: v for k, v in max_by_key.items() if v > 0}
                if len(positive_maxes) >= 2:
                    max_v = max(positive_maxes.values())
                    min_v = min(positive_maxes.values())
                    if min_v > 0 and max_v / min_v > 1000:
                        primary_key = max(positive_maxes, key=lambda k: positive_maxes[k])
                        lk = current_keys[0]
                        for row in current_data:
                            for vk in list(row.keys()):
                                if vk != lk and vk != primary_key:
                                    del row[vk]
                        chart["data"] = current_data
                        logger.info(
                            "Auto-fix chart '%s': removed incompatible series "
                            "(scale ratio %.0fx, kept '%s')",
                            chart.get("title", "?"), max_v / min_v, primary_key,
                        )

    @staticmethod
    def _enforce_chart_diversity(charts: list) -> list:
        """Ensure chart types are diverse — no type used more than 2 times.

        Rules:
        - Never use polarArea or radar (unreadable with business data)
        - No chart type may appear more than MAX_PER_TYPE times
        - Time-series data (dates in labels) → line or area preferred,
          but excess time-series get converted to bar/stackedBar
        - Proportions/shares (≤8 items) → pie or doughnut
        - Comparisons (>8 items) → horizontalBar
        - Comparisons (≤8 items) → bar
        """
        MAX_PER_TYPE = 2  # hard cap: no type more than 2 times

        GOOD_TYPES = ["bar", "line", "pie", "doughnut", "horizontalBar", "stackedBar", "area"]

        TIME_KEYWORDS = ["trend", "growth", "over time", "monthly", "weekly", "daily",
                         "quarterly", "yearly", "timeline", "history", "date", "period"]
        PROPORTION_KEYWORDS = ["distribution", "share", "breakdown", "composition",
                               "by category", "by type", "proportion", "split", "mix"]

        def _has_date_labels(chart):
            """Check if the chart's data labels look like dates."""
            data = chart.get("data", [])
            if not data:
                return False
            keys = list(data[0].keys())
            if not keys:
                return False
            label_key = keys[0]
            sample_labels = [str(row.get(label_key, "")) for row in data[:5]]
            date_patterns = [r"\d{4}-\d{2}", r"\d{2}/\d{2}", r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"]
            import re as _re
            for label in sample_labels:
                for pat in date_patterns:
                    if _re.search(pat, label, _re.IGNORECASE):
                        return True
            return False

        def _type_available(t, type_counts):
            """Check if a chart type hasn't reached the max limit."""
            return type_counts.get(t, 0) < MAX_PER_TYPE

        def _infer_best_type(chart, type_counts):
            """Pick the best chart type based on title, data shape, and usage counts."""
            title = (chart.get("title") or "").lower()
            data = chart.get("data", [])
            num_rows = len(data)
            num_cols = len(data[0].keys()) if data else 0

            # Time-series → prefer line or area, but fall through to others if maxed
            if _has_date_labels(chart) or any(kw in title for kw in TIME_KEYWORDS):
                for t in ["line", "area", "bar", "stackedBar"]:
                    if _type_available(t, type_counts):
                        return t

            # Proportions → pie or doughnut
            if any(kw in title for kw in PROPORTION_KEYWORDS) and num_rows <= 10:
                for t in ["pie", "doughnut", "bar", "horizontalBar"]:
                    if _type_available(t, type_counts):
                        return t

            # Many categories → horizontalBar or bar
            if num_rows > 8:
                for t in ["horizontalBar", "bar", "stackedBar"]:
                    if _type_available(t, type_counts):
                        return t

            # Multiple value columns → stackedBar
            if num_cols >= 3:
                if _type_available("stackedBar", type_counts):
                    return "stackedBar"

            # Default: pick first available good type
            for t in GOOD_TYPES:
                if _type_available(t, type_counts):
                    return t

            # Absolute fallback (all types at max — very unlikely with 7 types × 2 = 14 slots)
            return "bar"

        from collections import Counter
        type_counts = Counter()
        result = []

        for chart in charts:
            original_type = (chart.get("type") or "bar").lower()

            # Force-replace banned chart types
            if original_type in ("polararea", "polarArea", "radar"):
                new_type = _infer_best_type(chart, type_counts)
                chart["type"] = new_type
                type_counts[new_type] += 1
                logger.info(
                    "Chart fix: replaced '%s' with '%s' for '%s'",
                    original_type, new_type, chart.get("title", "?"),
                )
                result.append(chart)
            elif not _type_available(original_type, type_counts):
                # Type has reached the max — must reassign
                if original_type == "pie" and _type_available("doughnut", type_counts):
                    chart["type"] = "doughnut"
                    type_counts["doughnut"] += 1
                    logger.info("Chart diversity: pie → doughnut for '%s'", chart.get("title", "?"))
                elif original_type == "doughnut" and _type_available("pie", type_counts):
                    chart["type"] = "pie"
                    type_counts["pie"] += 1
                    logger.info("Chart diversity: doughnut → pie for '%s'", chart.get("title", "?"))
                elif original_type in ("line", "area") and _type_available("area" if original_type == "line" else "line", type_counts):
                    swap = "area" if original_type == "line" else "line"
                    chart["type"] = swap
                    type_counts[swap] += 1
                    logger.info("Chart diversity: %s → %s for '%s'", original_type, swap, chart.get("title", "?"))
                else:
                    new_type = _infer_best_type(chart, type_counts)
                    chart["type"] = new_type
                    type_counts[new_type] += 1
                    logger.info(
                        "Chart diversity: changed '%s' (at max %d) to '%s' for '%s'",
                        original_type, MAX_PER_TYPE, new_type, chart.get("title", "?"),
                    )
                result.append(chart)
            else:
                type_counts[original_type] += 1
                result.append(chart)

        return result

    @staticmethod
    def _detect_applicable_filters(report: dict) -> dict:
        """Analyze all SQL in the report to determine which filters are applicable.

        Returns a dict like:
        {
            "date_range": True,   # has sales_order with order_date
            "category": True,     # has product_master
            "product": True,      # has product_master
            "customer": True,     # has customer_master
            "status": True,       # has sales_order with status
        }
        """
        # Collect all SQL from KPIs, charts, and table
        all_sql = []
        for kpi in report.get("kpis", []):
            if kpi.get("sql"):
                all_sql.append(kpi["sql"])
        for chart in report.get("charts", []):
            if chart.get("sql"):
                all_sql.append(chart["sql"])
        if report.get("table", {}).get("sql"):
            all_sql.append(report["table"]["sql"])

        combined = " ".join(all_sql).lower()

        has_sales_order = bool(re.search(r'\bsales_order\b(?!_)', combined))
        has_product_master = bool(re.search(r'\bproduct_master\b', combined))
        has_customer_master = bool(re.search(r'\bcustomer_master\b', combined))
        has_order_date = bool(re.search(r'\border_date\b', combined))

        filters = {}

        # Date range filter — applicable if sales_order is referenced
        if has_sales_order and has_order_date:
            filters["date_range"] = True

        # Category & Product — applicable if product_master is referenced
        if has_product_master:
            filters["category"] = True
            filters["product"] = True

        # Customer — applicable if customer_master is referenced
        if has_customer_master:
            filters["customer"] = True

        # Status — applicable if sales_order is referenced
        if has_sales_order:
            filters["status"] = True

        logger.info("Detected applicable filters: %s", filters)
        return filters

    def apply_filters(self, report: dict, filters: dict) -> dict[str, Any]:
        """Apply filters to an existing report by injecting WHERE clauses.

        This does NOT call the LLM — it modifies existing SQL directly.
        Much faster and more reliable than re-generating.
        """
        import copy
        report = copy.deepcopy(report)

        logger.info("Applying filters to existing report: %s", filters)

        # Apply filters to all KPI SQLs and re-execute
        for kpi in report.get("kpis", []):
            original_sql = kpi.get("sql", "")
            if original_sql:
                filtered_sql = _inject_filters(original_sql, filters)
                filtered_sql = _fix_report_sql(filtered_sql)
                kpi["sql"] = filtered_sql
                # Clear previous error/value
                kpi.pop("error", None)
                kpi.pop("value", None)
            self._execute_kpi_sql(kpi)

        # Apply filters to all chart SQLs and re-execute
        for chart in report.get("charts", []):
            original_sql = chart.get("sql", "")
            if original_sql:
                filtered_sql = _inject_filters(original_sql, filters)
                filtered_sql = _fix_report_sql(filtered_sql)
                chart["sql"] = filtered_sql
                # Clear previous error/data
                chart.pop("error", None)
                chart["data"] = []
            self._execute_chart_sql(chart)

        # Apply filters to table SQL and re-execute
        if "table" in report and report["table"]:
            original_sql = report["table"].get("sql", "")
            if original_sql:
                filtered_sql = _inject_filters(original_sql, filters)
                filtered_sql = _fix_report_sql(filtered_sql)
                report["table"]["sql"] = filtered_sql
                report["table"].pop("error", None)
                report["table"]["data"] = []
            self._execute_table_sql(report["table"])

        logger.info("Filter application complete — all SQL re-executed")

        # ── Post-processing: clean up after filter application ────────
        if "kpis" in report:
            report["kpis"] = self._clean_kpis(report["kpis"])

        if "charts" in report:
            valid_charts = []
            for chart in report["charts"]:
                if chart.get("error"):
                    continue
                if not chart.get("data") or len(chart["data"]) == 0:
                    continue
                row_keys = list(chart["data"][0].keys()) if chart["data"] else []
                if len(row_keys) < 2:
                    continue
                value_keys = row_keys[1:]
                all_zero = all(
                    all((v := row.get(k)) is None or v == 0 or v == "" for k in value_keys)
                    for row in chart["data"]
                )
                if all_zero:
                    continue
                valid_charts.append(chart)
            # Always update — even if empty — so stale pre-filter data is never shown
            report["charts"] = valid_charts

        # ── Smart chart-type auto-correction (same as generate) ──────────
        if "charts" in report:
            for chart in report["charts"]:
                self._smart_fix_chart_type(chart)

        if "charts" in report and len(report["charts"]) > 1:
            report["charts"] = self._enforce_chart_diversity(report["charts"])

        return {
            "mode": "report",
            "report": report,
            "applicable_filters": self._detect_applicable_filters(report),
            "ui_instructions": {
                "create_new_section": True,
                "open_in_new_tab": True,
                "enable_streaming": False,
                "stream_once": False,
            },
        }

    def modify(self, current_report_json: str, modification: str) -> dict[str, Any]:
        """Modify an existing report based on a natural-language command."""
        schema_str = format_schema()

        # ── Step 1: Record original chart types BEFORE the LLM call ──────────
        # Any chart whose type changes in the LLM response was explicitly requested
        # by the user — we must protect those from being overridden by auto-correction.
        _original_types: dict[str, str] = {}
        try:
            _current = (
                json.loads(current_report_json)
                if isinstance(current_report_json, str)
                else current_report_json
            )
            for c in _current.get("charts", []):
                cid = c.get("id") or c.get("title", "")
                if cid:
                    _original_types[cid] = (c.get("type") or "bar").lower()
        except Exception:
            pass

        logger.info("Report modification — command: %s", modification)

        # Strip data arrays before sending to LLM — the model only needs structure
        # and SQL queries, not hundreds of result rows. This prevents token truncation.
        try:
            _lean = json.loads(current_report_json) if isinstance(current_report_json, str) else current_report_json
            _lean_copy = json.loads(json.dumps(_lean))  # deep copy
            for kpi in _lean_copy.get("kpis", []):
                kpi.pop("value", None); kpi.pop("error", None)
            for chart in _lean_copy.get("charts", []):
                chart.pop("data", None); chart.pop("error", None)
            if _lean_copy.get("table"):
                _lean_copy["table"].pop("data", None)
            lean_json = json.dumps(_lean_copy)
        except Exception:
            lean_json = current_report_json  # fallback to original if stripping fails

        from services.claude_report_llm import modify_report as _claude_modify_report
        from ai.claude_client import ClaudeClient
        import time as _time

        # Shared client so all LLM calls in this modify (main + any retry) accumulate
        # into one usage_log we can surface as telemetry to the frontend console.
        _mod_client = ClaudeClient()
        _mod_client.reset_usage()
        _mod_start = _time.time()

        updated_json_str = _claude_modify_report(lean_json, modification, schema_str, client=_mod_client)

        try:
            report = self._extract_json(updated_json_str)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Failed to parse modified report JSON (attempt 1): %s — retrying", exc)
            # ── Retry: ask the LLM to fix its own output ──────────────────
            try:
                retry_json_str = _claude_modify_report(
                    lean_json,
                    (
                        f"{modification}\n\n"
                        "CRITICAL: Your previous response was not valid JSON. "
                        "Output ONLY a raw JSON object. "
                        "No markdown, no code fences, no single quotes, no trailing commas. "
                        "Every key and string value MUST use double quotes."
                    ),
                    schema_str,
                    client=_mod_client,
                )
                report = self._extract_json(retry_json_str)
            except (json.JSONDecodeError, ValueError) as exc2:
                logger.error("Failed to parse modified report JSON (attempt 2): %s", exc2)
                return {
                    "mode": "report",
                    "error": f"Failed to modify report: {str(exc2)}",
                    "report": None,
                }

        # ── Step 2: Detect which chart types were explicitly changed ──────────
        # These are "user-locked" — auto-correction must NOT touch them.
        _user_locked: dict[str, str] = {}   # {chart_id_or_title: new_type_as_given}
        for chart in report.get("charts", []):
            cid = chart.get("id") or chart.get("title", "")
            new_type = (chart.get("type") or "bar").lower()
            if cid and cid in _original_types and _original_types[cid] != new_type:
                _user_locked[cid] = chart.get("type") or new_type
                logger.info(
                    "Modification: chart '%s' type locked as '%s' (changed from '%s' — user intent)",
                    cid, new_type, _original_types[cid],
                )

        # Re-execute all SQL queries on the modified report
        for kpi in report.get("kpis", []):
            self._execute_kpi_sql(kpi)

        for chart in report.get("charts", []):
            self._execute_chart_sql(chart)

        if "table" in report and report["table"]:
            self._execute_table_sql(report["table"])

        # ── Post-processing (same as generate, but lighter for add operations) ──
        _is_add_kpi = bool(re.search(r'\badd\b.*\bkpi\b', modification, re.IGNORECASE))
        if "kpis" in report:
            if _is_add_kpi:
                logger.info("Skipping _clean_kpis — user explicitly added a KPI")
            else:
                report["kpis"] = self._clean_kpis(report["kpis"])

        if "charts" in report:
            valid_charts = []
            for chart in report["charts"]:
                if chart.get("error"):
                    continue
                if not chart.get("data") or len(chart["data"]) == 0:
                    continue
                row_keys = list(chart["data"][0].keys()) if chart["data"] else []
                if len(row_keys) < 2:
                    continue
                value_keys = row_keys[1:]
                all_zero = all(
                    all((v := row.get(k)) is None or v == 0 or v == "" for k in value_keys)
                    for row in chart["data"]
                )
                if all_zero:
                    continue
                valid_charts.append(chart)
            if valid_charts:
                report["charts"] = valid_charts

        # ── Smart chart-type auto-correction (data-shape fixes only) ──────────
        # Run _smart_fix_chart_type for data-aggregation benefits (e.g. daily→monthly),
        # but immediately restore any type that was explicitly set by the user.
        if "charts" in report:
            for chart in report["charts"]:
                self._smart_fix_chart_type(chart)

        # ── Step 3: Restore user-locked chart types after auto-correction ─────
        # _smart_fix_chart_type may have re-changed the type — undo that for locked charts.
        # _enforce_chart_diversity is intentionally SKIPPED in modify() — it would override
        # the user's explicit request (e.g. "change pie to bar").
        if _user_locked and "charts" in report:
            for chart in report["charts"]:
                cid = chart.get("id") or chart.get("title", "")
                if cid in _user_locked:
                    chart["type"] = _user_locked[cid]
                    logger.info(
                        "Modification: enforced user-requested type '%s' for chart '%s'",
                        _user_locked[cid], cid,
                    )

        applicable_filters = self._detect_applicable_filters(report)

        # Telemetry — surface token/cost/timing of the modify LLM call(s) to the
        # frontend console, same shape as report/chat metrics.
        try:
            from ai.claude_multi_agent import _build_metrics
            _mod_metrics = _build_metrics(_mod_client.usage_log, _time.time() - _mod_start)
        except Exception:
            _mod_metrics = None

        return {
            "mode": "report",
            "report": report,
            "metrics": _mod_metrics,
            "applicable_filters": applicable_filters,
            "ui_instructions": {
                "create_new_section": True,
                "open_in_new_tab": False,
                "enable_streaming": False,
                "stream_once": False,
                "include_report_ai": True,
                "report_ai": {
                    "type": "chat_like",
                    "position": "below_report",
                },
                "explanation_feature": {
                    "enabled": True,
                    "trigger": "eye_button",
                },
            },
        }


# ── Module-level entry points for the report endpoints ──────────────────────
def modify_report(report_json: str, modification: str) -> dict:
    """Modify an existing report via natural language (Claude-backed)."""
    return ReportPipeline().modify(report_json, modification)


def apply_filters(report: dict, filters: dict) -> dict:
    """Apply filters to an existing report by re-injecting WHERE clauses (no LLM)."""
    return ReportPipeline().apply_filters(report, filters)
