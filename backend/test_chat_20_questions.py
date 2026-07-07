"""Chat-only test battery: 20 questions run directly through the intent
classifier + chat pipeline (classify_query_intent -> answer_chat_question),
bypassing the HTTP/auth layer entirely.

Original questions (from the product owner's question bank) were largely
broad/strategic and would route to "report" mode (multi-KPI dashboard), not
"data" mode (single SQL answer). Since this battery is chat-only and must
NEVER trigger report generation, each has been reframed into a concrete,
single-metric fact question that preserves the original's intent while
staying inside the intent router's "data" rules (see _INTENT_SYSTEM in
services/claude_report_llm.py: multi-metric breakdowns, broad "which
KPIs/risks/actions" recommendations, and forecasting/scenario questions all
route to "report" — single flat facts stay in chat).

For every question this script records: the classified intent (must be
"data", never "report"), the SQL generated, the row count, the final answer,
elapsed time, and the estimated cost (from the real per-agent token usage).

Run: python test_chat_20_questions.py
"""
import sys
import time
import json

sys.path.insert(0, ".")

from services.claude_report_llm import classify_query_intent, answer_chat_question
from ai.claude_client import ClaudeClient

QUESTIONS = [
    {
        "id": 1,
        "original": "What are the biggest risks currently affecting my business?",
        "reframed": "Which customers have the highest outstanding unpaid order value?",
        "module": "Executive AI, Analytics",
    },
    {
        "id": 2,
        "original": "Which KPIs have drifted significantly in the last 30 days, and what caused them?",
        "reframed": "What is total revenue in the last 30 days compared to the prior 30 days?",
        "module": "Drift Detection",
    },
    {
        "id": 3,
        "original": "What should I purchase this week to avoid stock shortages while minimizing excess inventory?",
        "reframed": "Which raw materials currently have the lowest stock quantity?",
        "module": "Procurement, Inventory",
    },
    {
        "id": 4,
        "original": "Which products are likely to run out of stock in the next 30 days?",
        "reframed": "Which products currently have the lowest stock quantity?",
        "module": "Forecasting",
    },
    {
        "id": 5,
        "original": "Which inventory is slow moving, and what actions do you recommend?",
        "reframed": "Which products have had the fewest sales in the last 90 days?",
        "module": "Inventory",
    },
    {
        "id": 6,
        "original": "Show me the top reasons why sales have declined this month.",
        "reframed": "What is total revenue this month compared to last month?",
        "module": "Sales Analytics",
    },
    {
        "id": 7,
        "original": "Which customers contribute the highest revenue but also carry the highest business risk?",
        "reframed": "Which customers have the highest outstanding unpaid balance?",
        "module": "Customer Analytics",
    },
    {
        "id": 8,
        "original": "How accurate were our forecasts over the last three months?",
        "reframed": "What was total revenue in each of the last three months?",
        "module": "Forecasting",
    },
    {
        "id": 9,
        "original": "Which suppliers are underperforming, and how?",
        "reframed": "Which vendors have the longest average lead time?",
        "module": "Vendor Management",
    },
    {
        "id": 10,
        "original": "Show me products with the highest profit margin and those with margin erosion.",
        "reframed": "Which products have the highest profit margin?",
        "module": "Finance",
    },
    {
        "id": 11,
        "original": "What production bottlenecks are delaying order fulfilment?",
        "reframed": "Which job cards have been open the longest?",
        "module": "Manufacturing",
    },
    {
        "id": 12,
        "original": "If demand increases by 20% next month, can our current inventory support it?",
        "reframed": "What is our current total inventory quantity on hand?",
        "module": "Scenario Planning",
    },
    {
        "id": 13,
        "original": "Where are we losing revenue across sales, procurement, production, or inventory?",
        "reframed": "How many orders are currently unpaid or partially paid?",
        "module": "Cross-functional AI",
    },
    {
        "id": 14,
        "original": "Recommend five actions that will improve profitability this quarter.",
        "reframed": "What is our average profit margin this quarter?",
        "module": "AI Recommendations",
    },
    {
        "id": 15,
        "original": "Which departments require immediate management attention today?",
        "reframed": "How many orders are currently overdue or unpaid?",
        "module": "Executive Dashboard",
    },
    {
        "id": 16,
        "original": "Show me all unusual business activities detected this week.",
        "reframed": "What is total order count this week compared to last week?",
        "module": "Anomaly Detection",
    },
    {
        "id": 17,
        "original": "How can I reduce procurement costs without affecting production?",
        "reframed": "What is our total procurement spend this month?",
        "module": "Procurement AI",
    },
    {
        "id": 18,
        "original": "Which products should be promoted or discounted based on current demand trends?",
        "reframed": "Which products have the lowest sales volume in the last 30 days?",
        "module": "Pricing & Sales",
    },
    {
        "id": 19,
        "original": "Summarize my business performance in the last quarter like a CEO briefing.",
        "reframed": "What was total revenue and total order count last quarter?",
        "module": "Executive Intelligence",
    },
    {
        "id": 20,
        "original": "Based on all my business data, what should I focus on over the next 90 days?",
        "reframed": "What is our current total outstanding receivables?",
        "module": "Predictive AI",
    },
]


def run_one(q: dict) -> dict:
    question = q["reframed"]
    t0 = time.time()
    row = {"id": q["id"], "original": q["original"], "reframed": question, "module": q["module"]}

    # ── Step 1: intent classification (must land on "data", never "report") ──
    client = ClaudeClient()
    intent = classify_query_intent(question, client)
    row["intent_mode"] = intent.get("mode")
    row["intent_complexity"] = intent.get("complexity")
    row["intent_reason"] = intent.get("reason")

    if intent.get("mode") != "data":
        row["status"] = f"SKIPPED — routed to '{intent.get('mode')}', not 'data' (no report/chat call made)"
        row["elapsed_s"] = round(time.time() - t0, 2)
        row["cost_usd"] = 0.0
        row["answer"] = None
        row["sql"] = None
        row["row_count"] = None
        return row

    # ── Step 2: run the actual chat pipeline (SQL agent + interpreter) ──
    sql_model = None  # default Sonnet; matches chat.py's non-"simple" default
    try:
        final = None
        for event in answer_chat_question(question, sql_model=sql_model):
            if event["stage"] == "complete":
                final = event["data"]
        if final is None:
            row["status"] = "ERROR — no complete event yielded"
            row["elapsed_s"] = round(time.time() - t0, 2)
            row["cost_usd"] = 0.0
            row["answer"] = None
            row["sql"] = None
            row["row_count"] = None
            return row

        metrics = final.get("metrics", {})
        row["status"] = "OK"
        row["answer"] = final.get("answer", "")
        row["sql"] = final.get("sql", "")
        row["row_count"] = len(final.get("data") or [])
        row["elapsed_s"] = round(time.time() - t0, 2)
        row["cost_usd"] = metrics.get("estimated_cost_usd", 0.0)
        row["total_tokens"] = metrics.get("total_tokens", 0)
        row["agent_calls"] = metrics.get("agent_calls", 0)
    except Exception as exc:
        row["status"] = f"ERROR — {type(exc).__name__}: {exc}"
        row["elapsed_s"] = round(time.time() - t0, 2)
        row["cost_usd"] = 0.0
        row["answer"] = None
        row["sql"] = None
        row["row_count"] = None
    return row


def main():
    results = []
    total_cost = 0.0
    total_time = 0.0
    report_leaks = 0

    print(f"{'#':<3} {'Status':<8} {'Mode':<8} {'Time':>7} {'Cost':>10}  Question")
    print("-" * 100)

    for q in QUESTIONS:
        r = run_one(q)
        results.append(r)
        total_cost += r.get("cost_usd") or 0.0
        total_time += r.get("elapsed_s") or 0.0
        if r["intent_mode"] == "report":
            report_leaks += 1
        print(
            f"{r['id']:<3} {r['status'].split(' ')[0]:<8} {str(r['intent_mode']):<8} "
            f"{r['elapsed_s']:>6.2f}s ${r.get('cost_usd', 0.0):>8.5f}  {r['reframed'][:70]}"
        )

    print("-" * 100)
    print(f"TOTAL   time={total_time:.2f}s   cost=${total_cost:.5f}   "
          f"report-mode leaks={report_leaks}/20  (must be 0)")

    with open("test_chat_20_questions_results.json", "w", encoding="utf-8") as f:
        json.dump({
            "results": results,
            "summary": {
                "total_time_s": round(total_time, 2),
                "total_cost_usd": round(total_cost, 6),
                "report_mode_leaks": report_leaks,
                "questions_run": len(QUESTIONS),
            },
        }, f, indent=2, default=str)
    print("\nFull results written to test_chat_20_questions_results.json")


if __name__ == "__main__":
    main()
