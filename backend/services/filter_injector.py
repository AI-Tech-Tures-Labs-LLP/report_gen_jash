"""Server-side filter injection for report SQL.

Domain-aware and multi-value: reads filter definitions from
services.domain_filters.DOMAINS. Each filter value may be a single scalar OR a
list of values — a list becomes an Excel-style `IN (...)` (OR within a filter);
different filters combine with AND (narrowing).

Injects WHERE clauses (and any required JOINs) into an existing working SQL
query instead of asking the LLM to regenerate SQL. Pure regex/string
rewriting — no LLM involved.

Key invariant: every table this module touches ends up with an EXPLICIT
ALIAS. An unaliased anchor table (`FROM finished_goods_inventory ...`) is
given one the first time a filter needs to reference it, and every column
reference this module writes or rewrites uses that alias. This guarantees no
ambiguous-column errors once a JOIN is added to a second table that happens
to share a column name (e.g. both finished_goods_inventory and
product_master have a `status` column).
"""

import re

from services.domain_filters import DOMAINS, detect_domain

# Words that must never be captured as a table alias (an unaliased table
# `FROM sales_order WHERE ...` would otherwise treat WHERE as the alias).
_SQL_KEYWORDS = {
    "WHERE", "ORDER", "GROUP", "HAVING", "LIMIT", "UNION", "INTERSECT",
    "EXCEPT", "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "ON",
    "AS", "SET", "VALUES", "OFFSET", "FETCH", "WITH", "SELECT", "FROM",
    "AND", "OR", "NOT", "IS", "NULL", "CROSS", "USING",
}


def _strip_parens(sql: str) -> str:
    """Blank out the contents of every genuine SUBQUERY — a parenthesized
    group whose content starts with SELECT/WITH (covers `(SELECT ...)` and
    `EXISTS (SELECT ...)`) — while preserving length/positions, so alias
    detection never reuses an alias that's scoped inside a subquery.

    Deliberately does NOT blank ordinary function-call parens like
    `COUNT(DISTINCT so_id)` or `SUM(x)`: those are part of the OUTER query
    and their column references (e.g. `so_id`) must stay visible so this
    module can find and qualify them before a JOIN makes them ambiguous.
    Blanking them was a real bug — it hid `so_id` inside
    `COUNT(DISTINCT so_id)` from the qualification pass, leaving it
    unqualified and making it ambiguous once a same-keyed table was joined.
    """
    out = list(sql)
    # Find every top-level-or-nested '(' whose content starts with a
    # subquery keyword, and blank out through its matching ')'.
    i = 0
    n = len(sql)
    while i < n:
        if sql[i] == '(':
            # Peek at what follows the '(' (skipping whitespace) to decide
            # if this is a subquery vs. a plain function-call paren.
            j = i + 1
            while j < n and sql[j].isspace():
                j += 1
            is_subquery = sql[j:j + 6].upper() == 'SELECT' or sql[j:j + 4].upper() == 'WITH'
            if is_subquery:
                depth = 1
                k = i + 1
                while k < n and depth > 0:
                    if sql[k] == '(':
                        depth += 1
                    elif sql[k] == ')':
                        depth -= 1
                    if depth > 0:
                        out[k] = ' '
                    k += 1
                i = k
                continue
        i += 1
    return ''.join(out)


def _alias_in(table: str, scope: str) -> str | None:
    """Return the alias a table is given in `scope`, or None if it has no
    explicit alias (never mistakes a trailing SQL keyword for an alias)."""
    m = re.search(rf'\b{table}\b(?!_)\s+(\w+)', scope, re.IGNORECASE)
    if not m:
        return None
    cand = m.group(1)
    return None if cand.upper() in _SQL_KEYWORDS else cand


def _has_table(table: str, scope: str) -> bool:
    return bool(re.search(rf'\b{table}\b(?!_)', scope, re.IGNORECASE))


def _quote(v) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def _condition(alias: str, column: str, kind: str, value, name: str) -> str | None:
    """Build a single WHERE condition string for one filter, or None to skip."""
    if kind == "date":
        op = ">=" if name == "date_from" else "<="
        return f"{alias}.{column} {op} {_quote(value)}"
    # text — single value → '=', list → IN (...)
    if isinstance(value, (list, tuple, set)):
        vals = [v for v in value if v is not None and str(v) != ""]
        if not vals:
            return None
        if len(vals) == 1:
            return f"{alias}.{column} = {_quote(vals[0])}"
        return f"{alias}.{column} IN ({', '.join(_quote(v) for v in vals)})"
    return f"{alias}.{column} = {_quote(value)}"


def _ensure_alias(table: str, alias_hint: str, sql: str, outer: str) -> tuple[str, str, str]:
    """Make sure `table` has an explicit alias in the OUTER scope of `sql`.

    Returns (new_sql, new_outer, alias). If the table already has an alias,
    returns it unchanged. If the table is present but unaliased, rewrites its
    FIRST occurrence in the outer query to `table alias_hint` (one-time), so
    every subsequent reference in this module can safely use `alias_hint`.
    """
    existing = _alias_in(table, outer)
    if existing:
        return sql, outer, existing
    if not _has_table(table, outer):
        return sql, outer, None

    m = re.search(rf'\b{table}\b(?!_)', outer, re.IGNORECASE)
    pos, end = m.start(), m.end()
    matched_text = sql[pos:end]  # preserve original casing
    insertion = f'{matched_text} {alias_hint}'
    new_sql = sql[:pos] + insertion + sql[end:]
    new_outer = outer[:pos] + insertion + outer[end:]
    return new_sql, new_outer, alias_hint


def _replace_unqualified_column(sql: str, outer: str, column: str, new_cond: str) -> tuple[str, str, bool]:
    """If `outer` contains an UNQUALIFIED equality on `column` (e.g. bare
    `status = 'x'`, not `alias.status = 'x'`), replace it with `new_cond`.
    Returns (new_sql, new_outer, replaced?)."""
    pat = rf"(?<![.\w]){re.escape(column)}\s*=\s*'[^']*'"
    m = re.search(pat, outer, re.IGNORECASE)
    if not m:
        return sql, outer, False
    pos, end = m.start(), m.end()
    return sql[:pos] + new_cond + sql[end:], outer[:pos] + new_cond + outer[end:], True


def _replace_qualified_column(sql: str, outer: str, alias: str, column: str, new_cond: str) -> tuple[str, str, bool]:
    """If `outer` contains `alias.column = 'x'`, replace it with `new_cond`."""
    pat = rf"\b{re.escape(alias)}\.{re.escape(column)}\s*=\s*'[^']*'"
    m = re.search(pat, outer, re.IGNORECASE)
    if not m:
        return sql, outer, False
    pos, end = m.start(), m.end()
    return sql[:pos] + new_cond + sql[end:], outer[:pos] + new_cond + outer[end:], True


def _inject_filters(sql: str, filters: dict, domain: str | None = None) -> str:
    """Inject WHERE conditions (and required JOINs) into an existing SQL query.

    Args:
        sql:     original SQL query string
        filters: {filter_name: value}. value may be a scalar or a list. Filter
                 names must exist in the resolved domain's `filters` config;
                 names/empty values that don't apply are silently ignored.
        domain:  domain from DOMAINS. If None, auto-detected from the SQL's
                 anchor tables (falls back to "sales").

    Correctness properties:
    - OUTER-SCOPE ONLY: aliases/tables inside subqueries are never reused.
    - EVERY TABLE GETS AN ALIAS: an unaliased anchor table is aliased the
      first time it's touched, eliminating ambiguous-column errors once a
      JOIN brings in a second table with a same-named column.
    - JOINS FIRST: all needed JOINs are collected and injected in one pass,
      reusing any table already present; then all conditions are appended.
    - MULTI-VALUE: a list value → IN (...); scalar → '='.
    - REPLACE, NOT STACK: a filter overrides a pre-existing equality on the
      same column baked into the base SQL (qualified or bare), instead of
      appending a second, contradicting condition.
    """
    if not sql or not filters:
        return sql

    sql = ' '.join(sql.split())
    outer = _strip_parens(sql)

    if domain is None:
        domain = detect_domain(outer) or "sales"
    cfg = DOMAINS.get(domain)
    if not cfg:
        return sql

    fdefs = cfg["filters"]

    def _nonempty(v):
        if v is None:
            return False
        if isinstance(v, (list, tuple, set)):
            return any(x is not None and str(x) != "" for x in v)
        return str(v) != ""

    active = {n: v for n, v in filters.items() if n in fdefs and _nonempty(v)}
    if not active:
        return sql

    # Which anchor tables are present at all (before any aliasing)?
    anchors_present = [t for t in cfg["anchor_tables"] if _has_table(t, outer)]
    if not anchors_present:
        return sql  # report's outer query doesn't belong to this domain — skip

    # Ensure every anchor table that's present has an explicit alias. This
    # also rewrites any bare `FROM table` to `FROM table alias` up front, so
    # the ambiguity risk is eliminated before we add any JOINs.
    anchor_alias = {}
    for t in anchors_present:
        hint = re.sub(r'[^a-z]', '', t.lower())[:3] or "t"
        sql, outer, alias = _ensure_alias(t, hint, sql, outer)
        if alias:
            anchor_alias[t] = alias

    # Pre-emptively qualify any bare (unqualified) occurrence of one of THIS
    # anchor's own filterable columns in the WHERE clause — e.g. a pre-existing
    # bare `status = 'active'` on finished_goods_inventory. This must happen
    # BEFORE any JOIN is added below: if we're about to join in a table that
    # also has a `status` column (e.g. product_master), a still-bare `status`
    # would become ambiguous even for filters unrelated to status.
    for t, a in anchor_alias.items():
        this_table_columns = {
            fdef["column"] for fdef in fdefs.values()
            if fdef["table"] == t and fdef["kind"] == "text"
        }
        for col in this_table_columns:
            where_m = re.search(r'\bWHERE\b', outer, re.IGNORECASE)
            if not where_m:
                continue
            search_from = where_m.end()
            pat = rf"(?<![.\w]){re.escape(col)}\b"
            m = re.search(pat, outer[search_from:], re.IGNORECASE)
            if m:
                pos = search_from + m.start()
                end = search_from + m.end()
                sql = sql[:pos] + f"{a}.{col}" + sql[end:]
                outer = outer[:pos] + f"{a}.{col}" + outer[end:]

    # ── Resolve join path + target alias for each active filter ───────────
    target_alias = {}
    joins_needed = []          # (table, alias, from_alias, from_col, to_col)
    joins_seen = set()

    for name in active:
        fdef = fdefs[name]
        from_anchor = fdef["from_anchor"]
        if from_anchor not in anchor_alias:
            continue  # this filter's anchor table isn't in the query — skip it

        existing = _alias_in(fdef["table"], outer)
        if existing:
            target_alias[name] = existing
            continue
        if not fdef["join_via"]:
            # Filter's column lives on the anchor table itself (now aliased).
            target_alias[name] = anchor_alias[from_anchor]
            continue

        # Walk the FK chain from the anchor, reusing existing tables.
        cur = anchor_alias[from_anchor]
        for hop_table, hop_hint, from_col, to_col in fdef["join_via"]:
            hop_existing = _alias_in(hop_table, outer)
            if hop_existing:
                cur = hop_existing
                continue
            key = (hop_table, hop_hint)
            if key not in joins_seen:
                joins_seen.add(key)
                joins_needed.append((hop_table, hop_hint, cur, from_col, to_col))
            cur = hop_hint
        target_alias[name] = cur

    # ── Qualify bare join-key columns BEFORE injecting the JOINs that would
    # make them ambiguous ─────────────────────────────────────────────────
    # A join key (e.g. so_id) exists — by definition — on BOTH sides of a
    # join, so it's exactly the column most likely to already appear bare
    # (unqualified) somewhere in the original query (SELECT, GROUP BY, ORDER
    # BY — anywhere, not just WHERE). If we add `JOIN sales_order_line sol ON
    # sal.so_id = sol.so_id` while a bare `so_id` still sits in
    # `COUNT(DISTINCT so_id)`, Postgres can no longer tell which table's
    # so_id was meant. Qualify every bare occurrence of each join's `from_col`
    # with the alias it already has on the anchor/current side, everywhere in
    # the query, before that join is physically added.
    for _, _, from_alias, from_col, _ in joins_needed:
        pat = rf"(?<![.\w]){re.escape(from_col)}\b"
        while True:
            m = re.search(pat, outer, re.IGNORECASE)
            if not m:
                break
            pos, end = m.start(), m.end()
            sql = sql[:pos] + f"{from_alias}.{from_col}" + sql[end:]
            outer = outer[:pos] + f"{from_alias}.{from_col}" + outer[end:]

    # ── Inject all JOINs in one pass, before the OUTER WHERE ──────────────
    def _insert_before_clause(text_sql, scope_sql, snippet):
        m = re.search(r'\bWHERE\b', scope_sql, re.IGNORECASE)
        if not m:
            for kw in ('GROUP BY', 'ORDER BY', 'HAVING', 'LIMIT'):
                m = re.search(rf'\b{kw}\b', scope_sql, re.IGNORECASE)
                if m:
                    break
        if m:
            pos = m.start()
            return text_sql[:pos] + snippet + text_sql[pos:], scope_sql[:pos] + (' ' * len(snippet)) + scope_sql[pos:]
        return text_sql + ' ' + snippet.rstrip(), scope_sql + ' ' + (' ' * len(snippet.rstrip()))

    if joins_needed:
        join_str = ' '.join(
            f'JOIN {t} {a} ON {fa}.{fc} = {a}.{tc}'
            for t, a, fa, fc, tc in joins_needed
        ) + ' '
        wm = re.search(r'\bWHERE\b', outer, re.IGNORECASE)
        if wm:
            pos = wm.start()
            sql = sql[:pos] + join_str + sql[pos:]
            outer = outer[:pos] + (' ' * len(join_str)) + outer[pos:]
        else:
            sql, outer = _insert_before_clause(sql, outer, join_str)

    # Re-resolve target aliases now the JOINs are physically in the SQL.
    for name in list(target_alias):
        real = _alias_in(fdefs[name]["table"], outer)
        if real:
            target_alias[name] = real

    # ── Build all WHERE conditions ────────────────────────────────────────
    conditions = []
    for name, value in active.items():
        if name not in target_alias:
            continue
        fdef = fdefs[name]
        alias, column, kind = target_alias[name], fdef["column"], fdef["kind"]

        cond = _condition(alias, column, kind, value, name)
        if not cond:
            continue

        # A text filter OVERRIDES a pre-existing equality on the same column
        # baked into the base SQL, instead of stacking a second (possibly
        # contradicting) condition. By this point every anchor table's bare
        # columns were already qualified above, so this only needs to check
        # the qualified form.
        if kind == "text":
            sql, outer, replaced = _replace_qualified_column(sql, outer, alias, column, cond)
            if replaced:
                continue
            sql, outer, replaced = _replace_unqualified_column(sql, outer, column, cond)
            if replaced:
                continue

        conditions.append(cond)

    if conditions:
        cond_str = ' AND '.join(conditions)
        has_where = bool(re.search(r'\bWHERE\b', outer, re.IGNORECASE))
        prefix = 'AND' if has_where else 'WHERE'
        inserted = False
        for kw in ('GROUP BY', 'ORDER BY', 'HAVING', 'LIMIT'):
            m = re.search(rf'\b{kw}\b', outer, re.IGNORECASE)
            if m:
                pos = m.start()
                sql = sql[:pos] + f'{prefix} {cond_str} ' + sql[pos:]
                inserted = True
                break
        if not inserted:
            sql += f' {prefix} {cond_str}'

    return sql
