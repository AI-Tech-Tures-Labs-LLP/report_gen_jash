"""Deterministic intent classification (no LLM).

Extracted from services.report_generator. Classifies a user question as
either 'chat' or 'report' using keyword matching and regex patterns.
"""

import re

# ── Intent classification (deterministic — no LLM) ─────────────────────────

_REPORT_KEYWORDS = [
    # Generic report terms
    "report", "dashboard", "analyze", "analysis", "trend", "trends",
    "summary", "comparison", "compare", "insight", "insights",
    "performance", "overview", "breakdown", "kpi", "kpis",
    "analytics", "metrics", "statistics", "visualize", "visualization",
    "chart", "graph", "show me", "give me a report",
    "top 10", "top 5", "top 20",
    # Sales / revenue
    "sales report", "revenue report", "revenue analysis",
    # Operations / order status
    "open orders", "open order", "inorder", "in-order", "in order",
    "order status", "order fulfillment", "active orders", "pending orders",
    # Backorder
    "backorder", "back order", "back-order", "unfulfilled", "outstanding orders",
    # Procurement / purchasing
    "purchase order", "procurement", "vendor report", "vendor analysis",
    "po report", "supplier report",
    # Customer / product
    "customer report", "customer analysis", "product report", "product analysis",
    "inventory report", "stock report",
    # Financial
    "cost report", "margin report", "profitability", "financial report",
]

_REPORT_PATTERNS = [
    r"\b(?:show|give|create|generate|build|make)\b.*\b(?:report|dashboard|analysis|overview)\b",
    r"\b(?:sales|revenue|order|product|customer|vendor)\s+(?:performance|analysis|breakdown|trend|summary)\b",
    r"\b(?:analyze|analyse)\b",
    r"\btop\s+\d+\b.*\b(?:product|customer|vendor|item|sku)\b",
    # Operational / backorder / procurement report patterns
    r"\b(?:backorder|back-order|inorder|in-order)\b",
    r"\b(?:open|pending|active|processing)\s+orders?\b",
    r"\b(?:order|purchase)\s+(?:status|fulfillment|pipeline)\b",
    r"\b(?:procurement|purchasing)\s+(?:report|analysis|overview|summary|dashboard)\b",
    r"\b(?:vendor|supplier)\s+(?:report|analysis|performance|summary)\b",
    r"\bunfulfilled\s+(?:orders?|lines?)\b",
]


def classify_intent(question: str) -> str:
    """Classify user intent as 'chat' or 'report'.

    Uses keyword matching and regex patterns — fully deterministic.
    """
    q = question.lower().strip()

    # Check for explicit report patterns first
    for pattern in _REPORT_PATTERNS:
        if re.search(pattern, q):
            return "report"

    # Keyword check — at least one keyword must appear
    for kw in _REPORT_KEYWORDS:
        if kw in q:
            return "report"

    return "chat"
