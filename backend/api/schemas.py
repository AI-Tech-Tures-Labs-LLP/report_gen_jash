"""Shared Pydantic request models for the API layer."""

from pydantic import BaseModel


class QuestionRequest(BaseModel):
    question: str
    conversation_id: str | None = None


class ReportRequest(BaseModel):
    question: str
    conversation_id: str | None = None
    force_refresh: bool = False  # bypass cache for fresh report
    # Filters
    date_from: str | None = None
    date_to: str | None = None
    aggregation: str | None = None  # daily|weekly|monthly|quarterly|yearly
    category: str | None = None
    customer: str | None = None
    status: str | None = None
    product: str | None = None


class ReportApplyFiltersRequest(BaseModel):
    """Apply filters to an existing report without re-generating via LLM."""
    report: dict  # The current report JSON
    date_from: str | None = None
    date_to: str | None = None
    category: str | None = None
    customer: str | None = None
    status: str | None = None
    product: str | None = None
    provider: str = "claude"


class ReportModifyRequest(BaseModel):
    report_json: str
    modification: str
    provider: str = "claude"
