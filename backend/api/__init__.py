"""API layer — FastAPI routers grouped by concern.

app.py assembles these via include_router(). Each router owns one functional area:
- chat.py      : /ask  (login-only; chat memory in the app Postgres DB per user)
- reports.py   : /report, /report/apply-filters, /report/modify, /report/filters
- conversations.py : /conversations*  (Postgres-backed chat history, per user)
- meta.py      : /health, /schema, /relationships
schemas.py holds the shared Pydantic request models.
"""
