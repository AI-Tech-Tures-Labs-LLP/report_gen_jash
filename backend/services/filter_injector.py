"""Server-side filter injection for report SQL.

Extracted from services.report_generator. Programmatically injects WHERE
clauses (and any required JOINs) into an existing working SQL query, instead
of asking the LLM to regenerate SQL. Pure regex/string rewriting.
"""

import re

# ── Server-side filter injection ───────────────────────────────────────────
# Instead of asking the LLM to regenerate SQL with filters, we inject
# WHERE clauses programmatically into the existing working SQL.

def _inject_filters(sql: str, filters: dict) -> str:
    """Inject WHERE conditions into an existing SQL query for applied filters.

    This is the reliable alternative to asking the LLM to rewrite queries.
    It modifies the existing (working) SQL by adding/extending WHERE clauses
    and injecting required JOINs if needed.

    Args:
        sql: Original SQL query string
        filters: Dict with keys: date_from, date_to, category, status, customer, product
    """
    if not sql or not filters:
        return sql

    sql = ' '.join(sql.split())  # normalize whitespace
    conditions = []

    # ── Date filters ──────────────────────────────────────────────────
    # Only apply if the query references sales_order
    if filters.get('date_from') and re.search(r'\bsales_order\b(?!_)', sql, re.IGNORECASE):
        # Find the alias for sales_order
        so_alias_m = re.search(r'\bsales_order\b(?!_)\s+(\w+)', sql, re.IGNORECASE)
        so_alias = so_alias_m.group(1) if so_alias_m else 'so'
        conditions.append(f"{so_alias}.order_date >= '{filters['date_from']}'")

    if filters.get('date_to') and re.search(r'\bsales_order\b(?!_)', sql, re.IGNORECASE):
        so_alias_m = re.search(r'\bsales_order\b(?!_)\s+(\w+)', sql, re.IGNORECASE)
        so_alias = so_alias_m.group(1) if so_alias_m else 'so'
        conditions.append(f"{so_alias}.order_date <= '{filters['date_to']}'")

    # ── Status filter ─────────────────────────────────────────────────
    if filters.get('status') and re.search(r'\bsales_order\b(?!_)', sql, re.IGNORECASE):
        so_alias_m = re.search(r'\bsales_order\b(?!_)\s+(\w+)', sql, re.IGNORECASE)
        so_alias = so_alias_m.group(1) if so_alias_m else 'so'
        status_val = filters['status'].replace("'", "''")
        # Remove any existing status condition and replace
        sql = re.sub(
            rf"\b{re.escape(so_alias)}\.status\s*=\s*'[^']*'",
            f"{so_alias}.status = '{status_val}'",
            sql, flags=re.IGNORECASE
        )
        # If no existing status condition was replaced, add one
        if not re.search(rf"\b{re.escape(so_alias)}\.status\s*=", sql, re.IGNORECASE):
            conditions.append(f"{so_alias}.status = '{status_val}'")

    # ── Category filter ───────────────────────────────────────────────
    if filters.get('category'):
        cat_val = filters['category'].replace("'", "''")
        # Check if product_master is already in the query
        pm_match = re.search(r'\bproduct_master\s+(\w+)', sql, re.IGNORECASE)
        if pm_match:
            pm_alias = pm_match.group(1)
        else:
            # Need to inject the join chain: sales_order_line + product_master
            pm_alias = 'pm'
            sol_match = re.search(r'\bsales_order_line\b(?!_)\s+(\w+)', sql, re.IGNORECASE)
            so_alias_m = re.search(r'\bsales_order\b(?!_)\s+(\w+)', sql, re.IGNORECASE)

            if sol_match:
                sol_alias = sol_match.group(1)
                # sales_order_line exists, just add product_master join
                sql = re.sub(
                    r'(\bWHERE\b)',
                    f'JOIN product_master {pm_alias} ON {sol_alias}.product_id = {pm_alias}.product_id WHERE',
                    sql, count=1, flags=re.IGNORECASE
                )
                if 'WHERE' not in sql.upper():
                    sql += f' JOIN product_master {pm_alias} ON {sol_alias}.product_id = {pm_alias}.product_id'
            elif so_alias_m:
                so_alias = so_alias_m.group(1)
                sol_alias = 'sol'
                # Need both sales_order_line and product_master
                join_clause = (f'JOIN sales_order_line {sol_alias} ON {so_alias}.so_id = {sol_alias}.so_id '
                               f'JOIN product_master {pm_alias} ON {sol_alias}.product_id = {pm_alias}.product_id')
                if 'WHERE' in sql.upper():
                    sql = re.sub(r'(\bWHERE\b)', f'{join_clause} WHERE', sql, count=1, flags=re.IGNORECASE)
                else:
                    sql += f' {join_clause}'

        conditions.append(f"{pm_alias}.category = '{cat_val}'")

    # ── Product filter ────────────────────────────────────────────────
    if filters.get('product'):
        prod_val = filters['product'].replace("'", "''")
        pm_match = re.search(r'\bproduct_master\s+(\w+)', sql, re.IGNORECASE)
        if pm_match:
            pm_alias = pm_match.group(1)
        else:
            # Inject join chain (same logic as category)
            pm_alias = 'pm'
            sol_match = re.search(r'\bsales_order_line\b(?!_)\s+(\w+)', sql, re.IGNORECASE)
            so_alias_m = re.search(r'\bsales_order\b(?!_)\s+(\w+)', sql, re.IGNORECASE)

            if sol_match:
                sol_alias = sol_match.group(1)
                if 'WHERE' in sql.upper():
                    sql = re.sub(
                        r'(\bWHERE\b)',
                        f'JOIN product_master {pm_alias} ON {sol_alias}.product_id = {pm_alias}.product_id WHERE',
                        sql, count=1, flags=re.IGNORECASE
                    )
                else:
                    sql += f' JOIN product_master {pm_alias} ON {sol_alias}.product_id = {pm_alias}.product_id'
            elif so_alias_m:
                so_alias = so_alias_m.group(1)
                sol_alias = 'sol'
                join_clause = (f'JOIN sales_order_line {sol_alias} ON {so_alias}.so_id = {sol_alias}.so_id '
                               f'JOIN product_master {pm_alias} ON {sol_alias}.product_id = {pm_alias}.product_id')
                if 'WHERE' in sql.upper():
                    sql = re.sub(r'(\bWHERE\b)', f'{join_clause} WHERE', sql, count=1, flags=re.IGNORECASE)
                else:
                    sql += f' {join_clause}'

        conditions.append(f"{pm_alias}.product_name = '{prod_val}'")

    # ── Customer filter ───────────────────────────────────────────────
    if filters.get('customer'):
        cust_val = filters['customer'].replace("'", "''")
        cm_match = re.search(r'\bcustomer_master\s+(\w+)', sql, re.IGNORECASE)
        if cm_match:
            cm_alias = cm_match.group(1)
        else:
            cm_alias = 'cm'
            so_alias_m = re.search(r'\bsales_order\b(?!_)\s+(\w+)', sql, re.IGNORECASE)
            if so_alias_m:
                so_alias = so_alias_m.group(1)
                if 'WHERE' in sql.upper():
                    sql = re.sub(
                        r'(\bWHERE\b)',
                        f'JOIN customer_master {cm_alias} ON {so_alias}.customer_id = {cm_alias}.customer_id WHERE',
                        sql, count=1, flags=re.IGNORECASE
                    )
                else:
                    sql += f' JOIN customer_master {cm_alias} ON {so_alias}.customer_id = {cm_alias}.customer_id'

        conditions.append(f"{cm_alias}.customer_name = '{cust_val}'")

    # ── Apply collected conditions ────────────────────────────────────
    if conditions:
        cond_str = ' AND '.join(conditions)
        if re.search(r'\bWHERE\b', sql, re.IGNORECASE):
            # Find the position right after WHERE and its existing conditions
            # Insert before GROUP BY / ORDER BY / LIMIT if present
            for keyword in ['GROUP BY', 'ORDER BY', 'LIMIT', 'HAVING']:
                pattern = re.compile(rf'\b{keyword}\b', re.IGNORECASE)
                match = pattern.search(sql)
                if match:
                    insert_pos = match.start()
                    sql = sql[:insert_pos] + f'AND {cond_str} ' + sql[insert_pos:]
                    break
            else:
                # No GROUP BY/ORDER BY/LIMIT — just append
                sql += f' AND {cond_str}'
        else:
            # No WHERE clause at all — insert before GROUP BY etc. or append
            for keyword in ['GROUP BY', 'ORDER BY', 'LIMIT', 'HAVING']:
                pattern = re.compile(rf'\b{keyword}\b', re.IGNORECASE)
                match = pattern.search(sql)
                if match:
                    insert_pos = match.start()
                    sql = sql[:insert_pos] + f'WHERE {cond_str} ' + sql[insert_pos:]
                    break
            else:
                sql += f' WHERE {cond_str}'

    return sql
