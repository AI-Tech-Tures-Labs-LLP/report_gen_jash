"""Tool definitions for the Claude multi-agent report pipeline.

Each tool has:
1. A JSON schema (for Claude's tool definition format)
2. A Python handler function (that actually runs the tool)

These tools are the "skills" that agents can call during their execution.
"""

import json
import logging
import re
from typing import Any

from ai.validator import validate_sql, check_sql_against_schema
from ai.sql_pattern_checker import check_sql_patterns
from db.schema import format_schema, get_schema
from db.relationships import format_relationships
from db.profiler import get_data_profile
from db.executor import execute_sql

logger = logging.getLogger(__name__)

# SQL clause keywords that can follow a table name in FROM/JOIN — these are NEVER aliases.
# Used to stop the `FROM <tbl> <word>` alias regex from reading `FROM t WHERE`/`JOIN t ON`/
# `FROM t GROUP` as an alias `WHERE`/`ON`/`GROUP` (RT-033 false-positive that spiraled the agent).
_SQL_KEYWORDS = {
    "where", "group", "order", "having", "limit", "offset", "join", "inner", "left", "right",
    "full", "outer", "cross", "on", "using", "union", "intersect", "except", "as", "and", "or",
    "window", "fetch", "for", "lateral", "natural", "tablesample", "with",
}

# ── Lazy import to avoid circular dependency ────────────────────────────────
# _fix_report_sql is defined in report_generator.py; import at call time.


def _get_fix_report_sql():
    """Lazy import of _fix_report_sql to avoid circular imports."""
    from services.report_generator import _fix_report_sql
    return _fix_report_sql


# ═══════════════════════════════════════════════════════════════════════════
# TOOL SCHEMAS — Claude API format
# ═══════════════════════════════════════════════════════════════════════════

TOOL_GET_DB_SCHEMA = {
    "name": "get_db_schema",
    "description": (
        "Returns the complete database schema with all tables and their "
        "columns (name, data type, nullability). Use this to understand "
        "what data is available."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

TOOL_GET_RELATIONSHIPS = {
    "name": "get_relationships",
    "description": (
        "Returns discovered relationships between database tables including "
        "foreign keys, exact column matches, and inferred joins with "
        "confidence scores."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

TOOL_GET_DATA_PROFILE = {
    "name": "get_data_profile",
    "description": (
        "Returns a data profile of key business tables including row counts, "
        "categorical column values with frequencies, numeric column ranges "
        "(min/max/avg), date ranges, and business intelligence rules."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

TOOL_EXECUTE_SQL = {
    "name": "execute_sql_query",
    "description": (
        "Execute a PostgreSQL SELECT query against the database and return "
        "the results. The query is auto-corrected for common mistakes, "
        "validated for safety, and executed. Returns the data rows or an "
        "error message if the query fails. If you get an error, read it "
        "carefully and rewrite the query."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "The PostgreSQL SELECT query to execute.",
            },
            "purpose": {
                "type": "string",
                "description": (
                    "Brief description of what this query is for "
                    "(e.g., 'KPI: Total Revenue', 'Chart: Revenue by Month')."
                ),
            },
        },
        "required": ["sql"],
    },
}

TOOL_GET_METRIC_SQL = {
    "name": "get_metric_sql",
    "description": (
        "Get the CANONICAL, DB-verified SQL for a core business metric (revenue, cogs, "
        "gross_profit, gross_profit_base, gross_margin_pct, units, diamond/gold/making component "
        "value, gold_cost, vendor_cogs, raw_material gold/diamond consumed, leftover_inventory_value, "
        "leftover_units, discount_pct, dso). Call this BEFORE writing SQL for any cost/profit/margin/"
        "component/consumed/inventory/discount metric — it returns the correct tables and joins so you "
        "don't invent a wrong one (e.g. summing across a fan-out child, comparing sales vs all-PO "
        "spend, or using a wrong/empty column). Accepts an exact metric name OR an intent keyword "
        "(e.g. 'vendor cost', 'gold consumed', 'leftover value', 'dso'). If the result has "
        "\"unavailable\": true, that metric has NO data in this DB — tell the user it's unavailable, "
        "do NOT fabricate it. Adapt the returned fragment (add GROUP BY / filters) as needed, but "
        "keep its join structure."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "metric": {
                "type": "string",
                "description": "Metric name or intent keyword (e.g. 'cogs', 'vendor cost', 'gold consumed').",
            },
        },
        "required": ["metric"],
    },
}

TOOL_VALIDATE_SQL = {
    "name": "validate_sql_query",
    "description": (
        "Validate a SQL query against the actual database schema to check "
        "if all referenced tables and columns exist. Also checks for "
        "structural anti-patterns. Returns validation issues or 'valid'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "The SQL query to validate.",
            },
        },
        "required": ["sql"],
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# TOOL HANDLERS — Python functions that execute the tools
# ═══════════════════════════════════════════════════════════════════════════

def handle_get_db_schema() -> str:
    """Return formatted database schema."""
    try:
        return format_schema()
    except Exception as exc:
        logger.error("get_db_schema failed: %s", exc)
        return json.dumps({"error": str(exc)})


def handle_get_relationships() -> str:
    """Return formatted table relationships."""
    try:
        return format_relationships()
    except Exception as exc:
        logger.error("get_relationships failed: %s", exc)
        return json.dumps({"error": str(exc)})


def handle_get_data_profile() -> str:
    """Return data profile for business context."""
    try:
        return get_data_profile()
    except Exception as exc:
        logger.error("get_data_profile failed: %s", exc)
        return json.dumps({"error": str(exc)})


# ── FAN-OUT REGISTRY (Phase 2) ────────────────────────────────────────────────
# DATA-DRIVEN, measured from the live DB (rows-per-parent > 1 = multiplies the parent
# when joined). Replaces the old 3-table hardcode. Each entry: table -> (fanout_factor,
# own_amount_cols). own_amount_cols are this table's OWN per-row values, which ARE correct
# to SUM (e.g. po_line_diamond.amount is per-diamond — verified). Summing a PARENT-level
# amount (line_total/total_amount/pricing-per-unit) ACROSS the fanned join is the bug.
# To extend coverage to a new table, add one line here — no detector changes needed.
_FANOUT_REGISTRY = {
    # table                          factor  own per-row amount cols (safe to SUM)
    "sales_order_line_diamond":     (2.56, ("diamond_amount_per_unit",)),
    "po_line_diamond":              (2.56, ("amount", "carats_total")),
    "job_card_diamond_lines":       (2.40, ("amount",)),
    # sales_order_line fans out 2.07× vs the ORDER, but line_total / pricing per-unit cols live at
    # the LINE grain (sales_order_line_pricing is 1:1 with the line) — summing THOSE while joining
    # sol is NOT double-counting. Only an ORDER-level amount (total_amount) summed across sol is the
    # bug. So line_total + pricing cols are "own" (safe); total_amount stays a blocked parent amount.
    "sales_order_line":             (2.07, ("line_total", "selling_price_per_unit", "base_price_per_unit",
                                            "gold_amount_per_unit", "diamond_amount_per_unit",
                                            "making_charges_per_unit", "quantity")),
    "po_line_items":                (3.42, ("quantity",)),
    "job_card":                     (5.84, ("gold_amount", "ds_total_amount", "gold_weight")),
    "raw_material_lot_usage_ledger": (3.81, ("qty_used_gm", "carats_used", "pieces_used")),
    "finished_goods_inventory":     (36.71, ("quantity_available", "quantity_received", "total_amount", "unit_cost")),
    "inventory_movements":          (2.0,  ("quantity",)),
}
# Parent-level amount columns that are double-counted when summed across ANY fanned join.
_PARENT_AMOUNT_COLS = ("line_total", "total_amount", "final_amount")
# Pricing-table (1:1) per-unit cols — summing these across a fanned child also inflates.
_PRICING_AMOUNT_RE = r'\b(solp|p|pr|pricing|sales_order_line_pricing)\.(diamond|gold|making|selling|base|line)\w*'


def _detect_line_child_fanout(sql: str) -> str:
    """Detect parent→child fan-out double-counting for ANY registered fan-out table
    (Phase 2 — was RT-007's 3-table hardcode, now the data-driven _FANOUT_REGISTRY).

    Returns a repair instruction if the query JOINs a fan-out table (MANY rows per parent)
    AND sums a PARENT-level amount across it (line_total/total_amount/pricing-per-unit) —
    which repeats the parent value once per child row, inflating it. Summing the fan-out
    table's OWN per-row amount is CORRECT and is NOT blocked. Returns "" if no fan-out.

    HARD gate (block + force repair) — prompts alone didn't stop the model (RT-007b). Code enforces.
    """
    s = ' '.join(sql.split()).lower()
    joined = [t for t in _FANOUT_REGISTRY if re.search(rf'\b(?:from|join)\s+{t}\b', s)]
    if not joined:
        return ""
    # GRAIN DISTINCTION (RT-029 fix): sales_order_line is the LINE grain — line_total and the 1:1
    # pricing per-unit cols live AT that grain, so summing them while joining sol is NOT fan-out.
    # The genuine multipliers are the SUB-LINE children (diamond/job_card/inventory/po_items/rm_usage),
    # which multiply rows BELOW the line. Only those make a line/pricing amount double-count.
    _LINE_GRAIN = {"sales_order_line"}
    true_children = [t for t in joined if t not in _LINE_GRAIN]
    own_cols = set()
    for t in joined:
        own_cols.update(_FANOUT_REGISTRY[t][1])
    # ORDER-level amount (total_amount/final_amount) summed across sales_order_line IS double-counting
    # (2.07 lines per order) — always dangerous, even with no sub-line child.
    _order_re = r'\bsum\s*\(\s*[^)]*\b(total_amount|final_amount)\b'
    if re.search(_order_re, s) and "sales_order_line" in joined:
        return _fanout_block_msg(joined)
    # If NO genuine sub-line child is joined, a line_total / pricing-col sum is at the line grain — SAFE.
    if not true_children:
        return ""
    # A true sub-line child IS joined → a PARENT (line/order) amount or a pricing per-unit col summed
    # across it double-counts. Summing the child's OWN per-row col is the correct method (allow).
    _parent_re = r'\bsum\s*\(\s*[^)]*\b(' + '|'.join(_PARENT_AMOUNT_COLS) + r')\b'
    sums_parent_amount = bool(re.search(_parent_re, s) or re.search(r'\bsum\s*\(\s*[^)]*' + _PRICING_AMOUNT_RE, s))
    if not sums_parent_amount:
        return ""
    child_own = set()
    for t in true_children:
        child_own.update(_FANOUT_REGISTRY[t][1])
    sums_only_own = bool(child_own) and not re.search(_parent_re, s) and \
        not re.search(r'\bsum\s*\(\s*[^)]*' + _PRICING_AMOUNT_RE, s)
    if sums_only_own:
        return ""  # correct own-amount method — allow
    # Otherwise block the common flat case and tell the agent the correct alternatives.
    return _fanout_block_msg(true_children)


def _fanout_block_msg(joined: list) -> str:
    """The fan-out repair instruction, listing the offending table(s) + their measured factor."""
    factors = ", ".join(f"{t} ~{_FANOUT_REGISTRY[t][0]}×" for t in joined)
    return (
        f"FAN-OUT DOUBLE-COUNTING BLOCKED: this query JOINs a fan-out table ({factors}) — MANY "
        "rows per parent — AND SUMs a PARENT-level amount (line_total / total_amount / a "
        "pricing per-unit column) across it. That repeats the parent value once per child row → "
        "inflated by the fan-out factor (e.g. a diamond query summing line_total returns ~₹30B vs "
        "the true ~₹1.9B; an inventory query over finished_goods can inflate ~37×). REWRITE:\n"
        "  (A) BEST for gold/diamond/making COMPONENT VALUE: do NOT join the child table at "
        "all — sales_order_line_pricing ALREADY has 1:1 (no fan-out) per-unit columns:\n"
        "      gold value    = SUM(solp.gold_amount_per_unit    * solp.quantity)\n"
        "      diamond value = SUM(solp.diamond_amount_per_unit * solp.quantity)\n"
        "      making value  = SUM(solp.making_charges_per_unit * solp.quantity)\n"
        "      (and component % of selling = that / SUM(solp.selling_price_per_unit*solp.quantity)).\n"
        "      Use these for ANY gold/diamond/making revenue/margin/% breakdown.\n"
        "  (B) ONLY if you need an ATTRIBUTE on the fan-out table (shape/quality/carats, or "
        "consumed qty): SUM the fan-out table's OWN per-row column — e.g. "
        "SUM(sales_order_line_diamond.diamond_amount_per_unit * sales_order_line.quantity), "
        "SUM(po_line_diamond.amount), SUM(raw_material_lot_usage_ledger.qty_used_gm) — NEVER a "
        "parent amount like line_total.\n"
        "  (C) If you need a parent amount grouped by a fan-out attribute, FIRST pre-aggregate the "
        "parent to ONE row per parent in a CTE, THEN join the fan-out table for the GROUP BY label only.\n"
        "NEVER SUM line_total/total_amount across a fan-out join."
    )


def _autofix_mechanical_sql(sql: str) -> tuple[str, list[str]]:
    """Deterministically fix UNAMBIGUOUS mechanical SQL errors the model repeatedly
    emits, so they don't waste a repair round (SQL-robustness Step 1).

    Only fixes things that are NEVER valid SQL (pure typos / required casts) — never
    touches semantics. Returns (fixed_sql, list_of_fixes_applied) for audit logging.
    """
    fixes: list[str] = []
    out = sql

    # 1) Bogus tokens: WHERE2 / GROUP2 BY / ORDER2 BY / HAVING2 — never valid.
    for bad, good in (
        (r'\bWHERE2\b', 'WHERE'),
        (r'\bGROUP2\s+BY\b', 'GROUP BY'),
        (r'\bORDER2\s+BY\b', 'ORDER BY'),
        (r'\bHAVING2\b', 'HAVING'),
    ):
        new = re.sub(bad, good, out, flags=re.IGNORECASE)
        if new != out:
            fixes.append(f"{bad} -> {good}")
            out = new

    # 2) ROUND(<expr>, n) where expr lacks a ::numeric/::decimal cast → add it.
    #    Postgres ROUND(double precision, int) errors; ROUND(numeric, int) is fine.
    def _fix_round(m):
        inner, ndigits = m.group(1), m.group(2)
        if '::numeric' in inner.lower() or '::decimal' in inner.lower():
            return m.group(0)
        # Only cast when it looks like an expression (has an operator/paren), not a bare column
        # that might already be numeric — safe superset: always cast, it's a no-op on numeric.
        return f"ROUND(({inner})::numeric, {ndigits})"
    new = re.sub(r'ROUND\s*\(\s*([^,()]+(?:\([^()]*\)[^,()]*)*)\s*,\s*(\d+)\s*\)',
                 _fix_round, out, flags=re.IGNORECASE)
    if new != out:
        fixes.append("added ::numeric cast inside ROUND(...)")
        out = new

    return out, fixes


def _enrich_sql_error(error: str, sql: str) -> str:
    """Append a SPECIFIC fix hint to a raw Postgres error so the SQL agent repairs
    it in ONE retry instead of flailing (SQL-robustness Step 2)."""
    e = (error or "").lower()
    hint = ""
    m = re.search(r'missing from-clause entry for table "(\w+)"', e)
    if m:
        a = m.group(1)
        hint = (f" FIX: you referenced `{a}.<col>` but never joined a table aliased `{a}`. "
                f"Either add the JOIN (e.g. JOIN sales_order {a} ON ...) or remove the `{a}.` reference. "
                f"Common cause: using `so.` after only joining sales_order_line — join sales_order too.")
    elif 'specified more than once' in e or ('table name' in e and 'more than once' in e):
        md = re.search(r'table name "(\w+)" specified more than once', e)
        who = md.group(1) if md else "an alias"
        hint = (f" FIX: '{who}' is used as a table name/alias more than once in FROM/JOIN. Most likely "
                f"you wrote the SAME JOIN twice — remove the duplicate. If both joins are needed, give "
                f"the second a DIFFERENT alias (e.g. '{who}2') and update only that table's '{who}.<col>' "
                f"references. Do NOT blindly rename every '{who}.' — only the ones for the duplicated table.")
    elif re.search(r'column reference "?(\w+)"? is ambiguous', e):
        m3 = re.search(r'column reference "?(\w+)"? is ambiguous', e)
        col = m3.group(1)
        hint = (f" FIX: '{col}' exists in MORE THAN ONE joined table, so you must QUALIFY it with a "
                f"table alias — e.g. write `<alias>.{col}` (sol.{col} / so.{col} / pm.{col}) everywhere "
                f"it appears, including in WHERE / GROUP BY / subquery IN (...) clauses. Pick the table you "
                f"actually mean. Do NOT remove the column — just prefix it.")
    elif re.search(r'column "?(\w+)"? does not exist', e):
        m2 = re.search(r'column "?(\w+)"? does not exist', e)
        hint = (f" FIX: column '{m2.group(1)}' is NOT in the schema — do NOT guess column names. "
                f"Use only columns shown in the DATABASE SCHEMA / metric dictionary in your system prompt.")
    elif 'function round' in e and 'does not exist' in e:
        hint = " FIX: cast the first arg of ROUND to numeric — ROUND(expr::numeric, n)."
    elif 'must appear in the group by' in e:
        hint = (" FIX: every non-aggregated SELECT column must be in GROUP BY. Use raw columns or "
                "positional numbers (GROUP BY 1,2) — never aggregates or aliases in GROUP BY.")
    return error + hint


def _prevalidate_sql(sql: str) -> tuple[bool, str, str]:
    """Pre-validate SQL syntax before execution to catch common errors.

    Returns (is_valid, corrected_sql, error_message)
    """
    if not sql or not sql.strip():
        return False, sql, "Empty SQL query"

    sql_clean = ' '.join(sql.split())
    sql_upper = sql_clean.upper()
    errors = []

    # Check 2: Duplicate table aliases
    # Skip for CTEs — the same alias is routinely reused in different CTE scopes
    # and a flat regex scan across the whole SQL produces false positives.
    has_cte = bool(re.match(r'\s*WITH\b', sql_clean, re.IGNORECASE))
    if not has_cte:
        from_matches = re.findall(r'\bFROM\s+(\w+)\s+(\w+)', sql_clean, re.IGNORECASE)
        join_matches = re.findall(r'\bJOIN\s+(\w+)\s+(\w+)', sql_clean, re.IGNORECASE)

        all_aliases = {}
        for table, alias in from_matches + join_matches:
            alias_lower = alias.lower()
            # CRITICAL (RT-033): the regex `FROM <tbl> <word>` also matches `FROM tbl WHERE`,
            # `JOIN tbl ON`, `FROM tbl GROUP`, etc — where the 2nd word is a CLAUSE KEYWORD, not an
            # alias. Treating those as aliases produced bogus "duplicate alias 'WHERE'" errors that
            # sent the SQL agent into a death-spiral on garbage fix hints. A real alias is NEVER a
            # SQL keyword — skip them. (Also skip when the "table" word is itself a keyword.)
            if alias_lower in _SQL_KEYWORDS or table.lower() in _SQL_KEYWORDS:
                continue
            if alias_lower in all_aliases:
                prior = all_aliases[alias_lower]
                if prior.lower() != table.lower():
                    # same alias, two DIFFERENT tables → rename the second + its column refs
                    errors.append(
                        f"Duplicate alias '{alias}' used for both {prior} and {table}. "
                        f"FIX: give {table} a UNIQUE alias (e.g. '{alias}2') and update every "
                        f"'{alias}.<col>' that refers to {table} (NOT the ones referring to {prior})."
                    )
                else:
                    # SAME table joined twice with the SAME alias → the second JOIN is redundant
                    errors.append(
                        f"Table {table} is joined twice with the same alias '{alias}' "
                        f"('specified more than once'). FIX: you almost certainly need only ONE "
                        f"'{table} {alias}' — remove the duplicate JOIN. If you truly need it twice "
                        f"(self-join), the second one MUST have a different alias (e.g. '{alias}2')."
                    )
            else:
                all_aliases[alias_lower] = table

    # Check 3: Window function inside aggregate (common error)
    if re.search(r'(SUM|COUNT|AVG|MIN|MAX)\s*\([^)]*?(OVER|RANK|ROW_NUMBER|LAG|LEAD)\s*\(', sql_clean, re.IGNORECASE):
        errors.append("Aggregate function cannot contain window function calls - restructure query")

    # Check 4: CTE (WITH clause) reference errors
    cte_matches = re.findall(r'WITH\s+(\w+)\s+AS', sql_clean, re.IGNORECASE)
    cte_names = {cte.lower() for cte in cte_matches}

    # Check for undefined table references (basic check)
    table_refs = re.findall(r'\bFROM\s+(\w+)|JOIN\s+(\w+)', sql_clean, re.IGNORECASE)
    for match in table_refs:
        table = match[0] or match[1]
        table_lower = table.lower()
        # Skip if it's a known CTE or common table
        if table_lower in cte_names:
            continue

    if errors:
        return False, sql, "; ".join(errors)

    return True, sql, ""


def handle_execute_sql_query(sql: str, purpose: str = "") -> str:
    """Execute SQL with auto-correction and safety validation.

    Applies the full validation chain:
    1. Auto-correct known LLM mistakes (_fix_report_sql)
    2. Pre-validation (syntax checks before DB execution)
    3. Safety validation (validate_sql)
    4. Execute and return results or error
    """
    try:
        # Step 1: Auto-correct common mistakes
        fix_sql = _get_fix_report_sql()
        corrected_sql = fix_sql(sql)

        if corrected_sql != sql:
            logger.info(
                "[SQL Tool] Auto-corrected SQL for '%s':\n  BEFORE: %s\n  AFTER:  %s",
                purpose,
                sql[:200],
                corrected_sql[:200],
            )

        # Step 1a: Deterministic mechanical auto-fixes (typos/casts the model repeats),
        # so they don't burn a repair round (SQL-robustness Step 1). Unambiguous only.
        corrected_sql, _mech_fixes = _autofix_mechanical_sql(corrected_sql)
        if _mech_fixes:
            logger.info("[SQL Tool] Mechanical auto-fix for '%s': %s", purpose, _mech_fixes)

        # Step 1b: Non-blocking accuracy guard — surface known correctness
        # anti-patterns (esp. revenue fan-out / double-counting) WITH the result so
        # the agent + downstream validator are warned even if they skipped the
        # validate_sql_query tool. We warn, not block (heuristic shouldn't hard-fail).
        try:
            _pattern_issues = check_sql_patterns(corrected_sql)
        except Exception:
            _pattern_issues = []
        _accuracy_warnings = [
            f"{pi['pattern_name']}: {pi.get('description', '')}"
            for pi in _pattern_issues
        ]

        # Step 1c: HARD GATE — block line-child fan-out (diamond/gold) and force a
        # rewrite. Unlike the warn-only pattern check above, this returns success:False
        # so the SQL agent's repair loop MUST fix it before the wrong number can ship
        # (RT-007/007b: prompt rule alone didn't stop it; code enforces).
        _fanout_fix = _detect_line_child_fanout(corrected_sql)
        if _fanout_fix:
            logger.warning("[SQL Tool] BLOCKED line-child fan-out for '%s'", purpose)
            return json.dumps({
                "success": False,
                "error": _fanout_fix,
                "blocked_reason": "line_child_fanout",
                "rejected_sql": corrected_sql,
            })

        # Step 2: Pre-validation (catch errors before DB execution)
        is_valid, corrected_sql, validation_error = _prevalidate_sql(corrected_sql)
        if not is_valid:
            return json.dumps({
                "success": False,
                "error": f"SQL validation error: {validation_error}",
                "corrected_sql": corrected_sql,
            })

        # Step 3: Safety validation
        is_safe, reason = validate_sql(corrected_sql)
        if not is_safe:
            return json.dumps({
                "success": False,
                "error": f"Query rejected: {reason}",
                "corrected_sql": corrected_sql,
            })

        # Step 4: Execute
        result = execute_sql(corrected_sql)

        if result["success"]:
            data = result["data"]
            # Truncate large results to keep context manageable
            if len(data) > 50:
                data = data[:50]
                truncated = True
            else:
                truncated = False

            _payload = {
                "success": True,
                "data": data,
                "row_count": len(result["data"]),
                "columns": result.get("columns", []),
                "truncated": truncated,
                "executed_sql": corrected_sql,
            }
            if _accuracy_warnings:
                _payload["accuracy_warnings"] = _accuracy_warnings
                _payload["warning"] = (
                    "⚠️ This query matched a known correctness anti-pattern "
                    "(see accuracy_warnings) — the result may be WRONG (e.g. "
                    "double-counted revenue). Review and fix before trusting it."
                )
            return json.dumps(_payload, default=str)
        else:
            return json.dumps({
                "success": False,
                "error": _enrich_sql_error(result["error"], corrected_sql),
                "executed_sql": corrected_sql,
            })

    except Exception as exc:
        logger.error("[SQL Tool] Execution error: %s", exc)
        return json.dumps({
            "success": False,
            "error": str(exc),
        })


def handle_validate_sql_query(sql: str) -> str:
    """Validate SQL against schema and check for anti-patterns.

    Patterns are split into two tiers:
    - HARD issues  → valid: false  — always wrong, Claude must rewrite
    - SOFT warnings → valid: true  — heuristic/context-dependent, Claude is
                                     informed but NOT forced into a rewrite round
    """
    # Patterns that are heuristic / context-dependent and produce too many
    # false positives when treated as hard failures. Claude still sees the
    # warning text, but the query is not blocked.
    _WARN_ONLY_PATTERNS = {
        "top_per_group_missing_partition_by",  # fires on correct multi-col GROUP BY without LIMIT
        "dual_metric_limit_not_dual_rank",     # ORDER BY multi-col + LIMIT is often intentional
        "per_unit_instead_of_per_order",       # question context decides which is correct
        "case_when_status_with_where_filter",  # not always wrong — depends on intent
    }

    issues:   list[str] = []
    warnings: list[str] = []

    try:
        # Schema validation — always hard
        schema = get_schema()
        schema_valid, schema_issues = check_sql_against_schema(sql, schema)
        if not schema_valid:
            issues.extend(schema_issues)

        # Pattern checker — split into hard vs soft by pattern name
        pattern_issues = check_sql_patterns(sql)
        for pi in pattern_issues:
            msg = f"{pi['pattern_name']}: {pi.get('description', pi.get('fix', ''))}"
            if pi.get('correction'):
                msg += f"\nHOW TO FIX: {pi['correction']}"
            if pi["pattern_name"] in _WARN_ONLY_PATTERNS:
                warnings.append(msg)
            else:
                issues.append(msg)

        # Safety check — always hard
        is_safe, reason = validate_sql(sql)
        if not is_safe:
            issues.append(f"Safety: {reason}")

    except Exception as exc:
        issues.append(f"Validation error: {str(exc)}")

    if issues:
        payload = {"valid": False, "issues": issues}
        if warnings:
            payload["warnings"] = warnings
        return json.dumps(payload)

    payload = {"valid": True, "message": "SQL is valid"}
    if warnings:
        payload["warnings"] = warnings
    return json.dumps(payload)


def handle_get_metric_sql(metric: str = "") -> str:
    """Return the canonical DB-verified SQL fragment for a core metric (Phase 3 semantic layer)."""
    from ai.metric_library import get_metric_sql
    return json.dumps(get_metric_sql(metric))


# ═══════════════════════════════════════════════════════════════════════════
# TOOL COLLECTIONS — grouped by agent
# ═══════════════════════════════════════════════════════════════════════════

# Tool schemas grouped by which agents use them
CONTEXT_AGENT_TOOLS = [TOOL_GET_DB_SCHEMA, TOOL_GET_RELATIONSHIPS, TOOL_GET_DATA_PROFILE]
BA_AGENT_TOOLS = [TOOL_GET_DB_SCHEMA, TOOL_GET_DATA_PROFILE]
SQL_AGENT_TOOLS = [TOOL_GET_METRIC_SQL, TOOL_EXECUTE_SQL, TOOL_VALIDATE_SQL]
# Data Analyst, Report Writer, QA — no tools (pure reasoning)

# Handler registry mapping tool names to functions
TOOL_HANDLERS: dict[str, Any] = {
    "get_db_schema": handle_get_db_schema,
    "get_relationships": handle_get_relationships,
    "get_data_profile": handle_get_data_profile,
    "get_metric_sql": handle_get_metric_sql,
    "execute_sql_query": handle_execute_sql_query,
    "validate_sql_query": handle_validate_sql_query,
}
