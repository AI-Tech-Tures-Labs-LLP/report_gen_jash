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


def _detect_line_child_fanout(sql: str) -> str:
    """Detect the line→diamond/gold second-level fan-out (RT-007).

    Returns a non-empty repair instruction if the query JOINs a *_diamond / *_gold
    child table (MANY rows per order line) AND sums a line-level / order-level amount
    (line_total, total_amount) — which repeats the line revenue once per child row,
    inflating it 2-3×. Returns "" if no fan-out detected.

    This is a HARD gate (block + force repair), not a warning — the prompt rule alone
    did not stop the model (RT-007b). Code enforces.
    """
    s = ' '.join(sql.split()).lower()
    # AUDITED fan-out surface (rows-per-parent verified > 1): ONLY the diamond line/PO/job-card
    # children multiply (~2.4-2.6×). gold / pricing children are 1:1 and SAFE — do NOT gate them.
    _fanout_tables = (
        "sales_order_line_diamond",   # 2.56× per sales_order_line
        "po_line_diamond",            # 2.56× per po_line
        "job_card_diamond_lines",     # 2.40× per job_card
    )
    joins_fanout_child = any(
        re.search(rf'\b(?:from|join)\s+{t}\b', s) for t in _fanout_tables
    )
    if not joins_fanout_child:
        return ""
    # UNIVERSAL rule (value-independent): a line/order-level amount summed ACROSS the fanned
    # child join is double-counted, regardless of WHICH column. This covers line_total,
    # total_amount, AND the pricing per-unit columns (solp.diamond_amount_per_unit, etc.) —
    # the RT-019 variant where the model used the right column but still joined the child.
    # Identify line-level/pricing/order aliases vs the child's OWN amount (which is correct).
    sums_line_amount = bool(
        re.search(r'\bsum\s*\(\s*[^)]*\bline_total\b', s)
        or re.search(r'\bsum\s*\(\s*[^)]*\btotal_amount\b', s)
        or re.search(r'\bsum\s*\(\s*[^)]*\bfinal_amount\b', s)
        # pricing-table per-unit columns summed across the diamond join = fan-out too
        or re.search(r'\bsum\s*\(\s*[^)]*\b(solp|p|pr|pricing)\.(diamond|gold|making|selling|base|line)\w*', s)
        or re.search(r'\bsum\s*\(\s*[^)]*\bsales_order_line_pricing\b', s)
    )
    # EXCEPTION: summing the child's OWN per-row amount (sold.diamond_amount_per_unit) is the
    # CORRECT attribution method — do NOT block that. Only block when a NON-child (line/pricing)
    # amount is summed across the join.
    sums_child_own = bool(
        re.search(r'\bsum\s*\(\s*[^)]*\b(sold|sodl?|d|dia|diamond)\.(diamond_amount_per_unit|amount_per_unit)\b', s)
    )
    if not sums_line_amount:
        return ""
    if sums_child_own and not re.search(r'\bsum\s*\(\s*[^)]*\b(line_total|total_amount|final_amount)\b', s) \
       and not re.search(r'\bsum\s*\(\s*[^)]*\b(solp|pricing)\.', s):
        return ""  # correct child-own-amount method — allow
    # Allow the correct pattern: pre-aggregating line revenue to one row per line in a
    # CTE/subquery, then joining the child only for grouping. Heuristic: if the SUM and
    # the child join are NOT in the same SELECT scope it's likely safe — but to stay safe
    # we still block the common flat case and tell the agent the correct alternatives.
    return (
        "FAN-OUT DOUBLE-COUNTING BLOCKED: this query JOINs a line-child table "
        "(sales_order_line_diamond / _gold, which has MANY rows per order line) AND "
        "SUMs a line/order-level amount (line_total / total_amount). That repeats the "
        "line revenue once per child row → 2-3x inflated (a diamond query summing "
        "line_total returns ~₹30B vs the true ~₹1.9B). REWRITE using the BEST option:\n"
        "  (A) BEST for gold/diamond/making COMPONENT VALUE: do NOT join the child table at "
        "all — sales_order_line_pricing ALREADY has 1:1 (no fan-out) per-unit columns:\n"
        "      gold value    = SUM(solp.gold_amount_per_unit    * solp.quantity)\n"
        "      diamond value = SUM(solp.diamond_amount_per_unit * solp.quantity)\n"
        "      making value  = SUM(solp.making_charges_per_unit * solp.quantity)\n"
        "      (and component % of selling = that / SUM(solp.selling_price_per_unit*solp.quantity)).\n"
        "      Use these for ANY gold/diamond/making revenue/margin/% breakdown.\n"
        "  (B) ONLY if you need a DIAMOND ATTRIBUTE not on pricing (shape/quality/carats): SUM the "
        "CHILD's own amount — SUM(sales_order_line_diamond.diamond_amount_per_unit * "
        "sales_order_line.quantity) — NEVER line_total.\n"
        "  (C) If you need line_total grouped by a child attribute, FIRST pre-aggregate line revenue "
        "to ONE row per line in a CTE, THEN join the child for the GROUP BY label only.\n"
        "NEVER SUM line_total/total_amount across the child join."
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
    """Validate SQL against schema and check for anti-patterns."""
    issues: list[str] = []

    try:
        # Schema validation
        schema = get_schema()
        schema_valid, schema_issues = check_sql_against_schema(sql, schema)
        if not schema_valid:
            issues.extend(schema_issues)

        # Pattern checker (includes GROUP BY aggregate/alias contamination check)
        pattern_issues = check_sql_patterns(sql)
        if pattern_issues:
            for pi in pattern_issues:
                msg = f"{pi['pattern_name']}: {pi.get('description', pi.get('fix', ''))}"
                if pi.get('correction'):
                    msg += f"\nHOW TO FIX: {pi['correction']}"
                issues.append(msg)

        # Safety check
        is_safe, reason = validate_sql(sql)
        if not is_safe:
            issues.append(f"Safety: {reason}")

    except Exception as exc:
        issues.append(f"Validation error: {str(exc)}")

    if issues:
        return json.dumps({"valid": False, "issues": issues})
    return json.dumps({"valid": True, "message": "SQL is valid"})


# ═══════════════════════════════════════════════════════════════════════════
# TOOL COLLECTIONS — grouped by agent
# ═══════════════════════════════════════════════════════════════════════════

# Tool schemas grouped by which agents use them
CONTEXT_AGENT_TOOLS = [TOOL_GET_DB_SCHEMA, TOOL_GET_RELATIONSHIPS, TOOL_GET_DATA_PROFILE]
BA_AGENT_TOOLS = [TOOL_GET_DB_SCHEMA, TOOL_GET_DATA_PROFILE]
SQL_AGENT_TOOLS = [TOOL_EXECUTE_SQL, TOOL_VALIDATE_SQL]
# Data Analyst, Report Writer, QA — no tools (pure reasoning)

# Handler registry mapping tool names to functions
TOOL_HANDLERS: dict[str, Any] = {
    "get_db_schema": handle_get_db_schema,
    "get_relationships": handle_get_relationships,
    "get_data_profile": handle_get_data_profile,
    "execute_sql_query": handle_execute_sql_query,
    "validate_sql_query": handle_validate_sql_query,
}
