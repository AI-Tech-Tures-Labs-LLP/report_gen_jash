"""Report endpoints: /report, /report/apply-filters, /report/modify, /report/filters, /reports/save."""

import json as _json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.schemas import ReportRequest, ReportApplyFiltersRequest, ReportModifyRequest
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

    # Build filter context string for the LLM
    filters = []
    if req.date_from:
        filters.append(f"Date range: from {req.date_from}")
    if req.date_to:
        filters.append(f"to {req.date_to}")
    if req.aggregation:
        filters.append(f"Time aggregation: {req.aggregation}")
    if req.category:
        filters.append(f"Category filter: {req.category}")
    if req.customer:
        filters.append(f"Customer filter: {req.customer}")
    if req.status:
        filters.append(f"Order status filter: {req.status}")
    if req.product:
        filters.append(f"Product filter: {req.product}")

    filter_ctx = ""
    if filters:
        filter_ctx = "\n[ACTIVE FILTERS: " + ", ".join(filters) + ". Apply these filters in ALL SQL WHERE clauses.]"

    question_with_filters = req.question + filter_ctx

    logger.info("REPORT request | question=%s | filters=%s",
                req.question, filter_ctx or "none")

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
        question=question_with_filters,
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
def report_filters_endpoint():
    """Return distinct filter values for the report filter bar."""
    from db.executor import execute_sql

    result = {}

    cat_res = execute_sql("SELECT DISTINCT category FROM product_master WHERE category IS NOT NULL ORDER BY category")
    result["categories"] = [r["category"] for r in (cat_res["data"] if cat_res["success"] else [])]

    cust_res = execute_sql("SELECT DISTINCT customer_name FROM customer_master WHERE customer_name IS NOT NULL ORDER BY customer_name LIMIT 100")
    result["customers"] = [r["customer_name"] for r in (cust_res["data"] if cust_res["success"] else [])]

    prod_res = execute_sql("SELECT DISTINCT product_name FROM product_master WHERE product_name IS NOT NULL ORDER BY product_name LIMIT 100")
    result["products"] = [r["product_name"] for r in (prod_res["data"] if prod_res["success"] else [])]

    stat_res = execute_sql("SELECT DISTINCT status FROM sales_order WHERE status IS NOT NULL ORDER BY status")
    result["statuses"] = [r["status"] for r in (stat_res["data"] if stat_res["success"] else [])]

    date_res = execute_sql("SELECT MIN(order_date)::text AS min_date, MAX(order_date)::text AS max_date FROM sales_order")
    if date_res["success"] and date_res["data"]:
        result["date_range"] = date_res["data"][0]
    else:
        result["date_range"] = {"min_date": None, "max_date": None}

    return result
