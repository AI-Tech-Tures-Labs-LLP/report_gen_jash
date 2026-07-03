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
    """Apply filters to an existing report without re-generating via LLM.

    date_from/date_to are single date strings. All other filters are
    multi-select (Excel-style) and arrive in `filters` as
    {filter_name: [value, ...]} — a list of chosen values that becomes
    IN (...) in the SQL. The legacy scalar named fields are still accepted for
    backward compatibility and are merged into `filters` as single-item lists.
    """
    report: dict  # The current report JSON
    date_from: str | None = None
    date_to: str | None = None
    filters: dict[str, list[str]] | None = None  # {name: [values]} multi-select
    # Legacy scalar fields (backward compatible)
    category: str | None = None
    customer: str | None = None
    status: str | None = None
    product: str | None = None
    provider: str = "claude"


class ReportModifyRequest(BaseModel):
    report_json: str
    modification: str
    provider: str = "claude"


class ReportFilterOptionsRequest(BaseModel):
    """Fetch filter dropdown options, optionally CASCADED by whichever filters
    are already selected — e.g. if category=RING is selected, the vendor
    dropdown only returns vendors that actually have RING inventory. This
    guarantees the user can never construct a filter combination that matches
    zero rows, since every dropdown only ever shows values reachable given the
    others already picked.
    """
    domain: str = "sales"
    date_from: str | None = None
    date_to: str | None = None
    filters: dict[str, list[str]] | None = None  # {name: [selected values]}
