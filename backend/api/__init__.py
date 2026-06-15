"""API layer — FastAPI routers grouped by concern.

app.py assembles these via include_router(). Each router owns one functional area:
- chat.py      : /ask
- reports.py   : /report, /report/apply-filters, /report/modify, /report/filters
- history.py   : /history and /history/{turn_id}/*
- meta.py      : /schema, /relationships
- frontend.py  : /, /report-view, /static mount
schemas.py holds the shared Pydantic request models.
"""
