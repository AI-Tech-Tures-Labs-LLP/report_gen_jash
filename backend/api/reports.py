"""Report endpoints: /report, /report/apply-filters, /report/modify, /report/filters, /reports/save."""

import json as _json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.schemas import ReportRequest, ReportApplyFiltersRequest, ReportModifyRequest, ReportFilterOptionsRequest
from api.auth import get_current_user

logger = logging.getLogger("api")
router = APIRouter()


class SaveReportRequest(BaseModel):
    report_id: str
    conv_id: Optional[str] = None
    question: str
    report_data: Dict[str, Any]


@router.post("/reports/save")
def save_report_endpoint(req: SaveReportRequest, current_user: dict = Depends(get_current_user)):
    """Persist a generated report to MongoDB (user-scoped via JWT)."""
    from db.user_data import save_report

    save_report(
        user_id=current_user["user_id"],
        conv_id=req.conv_id or "",
        report_id=req.report_id,
        question=req.question,
        report_data=req.report_data,
    )
    return {"ok": True}


@router.post("/report")
def report_endpoint(req: ReportRequest):
    """Generate a full analytics report from a natural-language question."""
    import asyncio

    # Build structured filters dict (passed directly to pipeline, not embedded as text)
    filters = {}
    if req.date_from:
        filters["date_from"] = req.date_from
    if req.date_to:
        filters["date_to"] = req.date_to
    if req.category:
        filters["category"] = req.category
    if req.customer:
        filters["customer"] = req.customer
    if req.status:
        filters["status"] = req.status
    if req.product:
        filters["product"] = req.product

    logger.info("REPORT request | question=%s | filters=%s",
                req.question, filters or "none")

    from services.enhanced_pipeline import EnhancedReportPipeline
    pipeline = EnhancedReportPipeline(
        # DB logging (audit_trail / signal_detection_logs / graph_sql_mappings) is
        # write-only — nothing reads it back, and it was producing INSERT-error spam.
        # Disabled. Re-enable + run db/migrations/001 if an audit dashboard is built.
        enable_logging=False,
        enable_signals=True,
        enable_optimization=True,
    )
    result = asyncio.run(pipeline.generate(
        question=req.question,
        filters=filters or None,
        provider="claude",
        force_refresh=req.force_refresh,
    ))
    if result.get("status") == "failed":
        raise HTTPException(status_code=500, detail=result.get("error", "Report generation failed"))
    # Return via JSONResponse with default=str to handle numpy/datetime types
    # from signal detection that would otherwise break FastAPI's serializer.
    return JSONResponse(
        content=_json.loads(_json.dumps(result, default=str))
    )


@router.post("/report/apply-filters")
def report_apply_filters_endpoint(req: ReportApplyFiltersRequest):
    """Apply filters to an existing report by injecting WHERE clauses into SQL.

    This does NOT call the LLM — it modifies the existing SQL queries directly,
    making it much faster and more reliable than regenerating the entire report.
    """
    from services.report_generator import apply_filters

    filters: dict = {}
    # Dates are single-valued.
    if req.date_from:
        filters["date_from"] = req.date_from
    if req.date_to:
        filters["date_to"] = req.date_to

    # Multi-select filters: {name: [values]} — the primary path.
    if req.filters:
        for name, values in req.filters.items():
            vals = [v for v in (values or []) if v is not None and str(v) != ""]
            if vals:
                filters[name] = vals

    # Legacy scalar named fields (backward compatible) — only if not already
    # provided via `filters`. Wrapped as single-item lists so the injector
    # treats them uniformly.
    for legacy_name, legacy_val in (
        ("category", req.category), ("customer", req.customer),
        ("status", req.status), ("product", req.product),
    ):
        if legacy_val and legacy_name not in filters:
            filters[legacy_name] = [legacy_val]

    logger.info("REPORT APPLY-FILTERS | filters=%s", filters)
    return apply_filters(req.report, filters)


@router.post("/report/modify")
def report_modify_endpoint(req: ReportModifyRequest):
    """Modify an existing report based on a natural-language command (Claude)."""
    from services.report_generator import modify_report

    logger.info("REPORT MODIFY | command=%s", req.modification)
    result = modify_report(req.report_json, req.modification)
    # Safe serialization — SQL results may contain numpy/Decimal/datetime types
    return JSONResponse(content=_json.loads(_json.dumps(result, default=str)))


@router.get("/report/filters")
def report_filters_endpoint(domain: str = "sales"):
    """Return distinct filter values for the report filter bar, per domain.

    Response shape (uniform across domains):
      {
        "options": { "<filter_name>": [distinct values...], ... },
        "date_range": { "min_date": ..., "max_date": ... }
      }
    `domain` selects which domain's filter vocabulary to fetch. Defaults to
    "sales". Table/column names come only from our own domain_filters config
    (never user input), so the dynamic SQL below is safe.
    """
    from db.executor import execute_sql
    from services.domain_filters import DOMAINS

    cfg = DOMAINS.get(domain)
    if not cfg:
        raise HTTPException(status_code=400, detail=f"Unknown filter domain: {domain}")

    options: dict = {}
    date_range = {"min_date": None, "max_date": None}

    for name, fdef in cfg["filters"].items():
        table, column = fdef["table"], fdef["column"]
        if name in ("date_from", "date_to"):
            # Compute the range once (both share a column) from date_from's def.
            if name == "date_from":
                q = f"SELECT MIN({column})::text AS min_date, MAX({column})::text AS max_date FROM {table}"
                res = execute_sql(q)
                if res["success"] and res["data"]:
                    date_range = res["data"][0]
            continue
        q = (f"SELECT DISTINCT {column} FROM {table} "
             f"WHERE {column} IS NOT NULL ORDER BY {column} LIMIT 500")
        res = execute_sql(q)
        options[name] = [r[column] for r in (res["data"] if res["success"] else [])]

    return {"options": options, "date_range": date_range}


@router.post("/report/filters")
def report_filters_cascading_endpoint(req: ReportFilterOptionsRequest):
    """CASCADING filter options: each dropdown only returns values that are
    still reachable given whichever OTHER filters are already selected.

    Example: if category=RING is selected, the vendor dropdown returns only
    vendors that actually carry RING inventory — never a vendor that would
    combine with RING to match zero rows. This makes it impossible for the UI
    to construct a filter combination with no matching data, which is what
    previously produced empty/errored KPIs and lost charts.

    For each filter X, we build `SELECT DISTINCT <col> FROM <anchor> <alias>`
    and reuse the same _inject_filters() used for report SQL to scope it by
    every OTHER active filter (never filter X by itself, or its own dropdown
    would collapse to only the currently-picked value).
    """
    from db.executor import execute_sql
    from services.domain_filters import DOMAINS
    from services.filter_injector import _inject_filters

    cfg = DOMAINS.get(req.domain)
    if not cfg:
        raise HTTPException(status_code=400, detail=f"Unknown filter domain: {req.domain}")

    fdefs = cfg["filters"]

    # Build the full active-filters dict (as apply-filters does) so we can
    # exclude "this filter's own name" per dropdown below.
    all_active: dict = {}
    if req.date_from:
        all_active["date_from"] = req.date_from
    if req.date_to:
        all_active["date_to"] = req.date_to
    if req.filters:
        for name, values in req.filters.items():
            vals = [v for v in (values or []) if v is not None and str(v) != ""]
            if vals:
                all_active[name] = vals

    options: dict = {}
    date_range = {"min_date": None, "max_date": None}

    for name, fdef in fdefs.items():
        anchor = fdef["from_anchor"]
        anchor_alias = "".join(c for c in anchor if c.isalpha())[:3] or "t"
        # Scope by every OTHER active filter — never by this filter's own
        # selection, or its own dropdown would collapse to just the picked value.
        other_filters = {k: v for k, v in all_active.items() if k != name}

        if name in ("date_from", "date_to"):
            if name != "date_from":
                continue  # date range computed once, from date_from's pass
            q = (f"SELECT MIN({anchor_alias}.{fdef['column']})::text AS min_date, "
                 f"MAX({anchor_alias}.{fdef['column']})::text AS max_date "
                 f"FROM {anchor} {anchor_alias}")
            if other_filters:
                q = _inject_filters(q, other_filters, domain=req.domain)
            res = execute_sql(q)
            if res["success"] and res["data"]:
                date_range = res["data"][0]
            continue

        # Build this filter's OWN join path first (so its column is always
        # reachable, even with no other filters selected) — _inject_filters()
        # only adds joins for filters it's asked to scope BY, not the one
        # being queried, so we construct this join_via chain directly here.
        col_qualifier = anchor_alias
        join_clause = ""
        for hop_table, hop_alias, from_col, to_col in fdef["join_via"]:
            join_clause += f" JOIN {hop_table} {hop_alias} ON {col_qualifier}.{from_col} = {hop_alias}.{to_col}"
            col_qualifier = hop_alias
        col = fdef["column"]
        distinct_sql = f"SELECT DISTINCT {col_qualifier}.{col} AS value FROM {anchor} {anchor_alias}{join_clause}"
        if other_filters:
            distinct_sql = _inject_filters(distinct_sql, other_filters, domain=req.domain)
        has_where = "WHERE" in distinct_sql.upper()
        distinct_sql += (f" AND {col_qualifier}.{col} IS NOT NULL" if has_where
                          else f" WHERE {col_qualifier}.{col} IS NOT NULL")
        distinct_sql += " ORDER BY value LIMIT 500"

        res = execute_sql(distinct_sql)
        if res["success"]:
            options[name] = [r["value"] for r in res["data"]]
        else:
            logger.warning("Cascading filter options failed for '%s': %s", name, res.get("error"))
            options[name] = []

    return {"options": options, "date_range": date_range}
