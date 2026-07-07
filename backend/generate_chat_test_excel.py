"""Build an Excel report from test_chat_20_questions_results.json.

Sheet 1 "Summary": one row per question with all stats (status, mode, timing,
cost, tokens, row count, SQL, answer).
Sheet 2 "Totals": aggregate stats (total/avg cost, time, tokens, leak check).

Run: python generate_chat_test_excel.py
"""
import json
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

with open("test_chat_20_questions_results.json", "r", encoding="utf-8") as f:
    data = json.load(f)

results = data["results"]
summary = data["summary"]

wb = Workbook()

# ── Sheet 1: per-question detail ──────────────────────────────────────────
ws = wb.active
ws.title = "Chat Test Results"

headers = [
    "#", "Module", "Original Question", "Reframed Question (chat-safe)",
    "Status", "Intent Mode", "Complexity", "Routing Reason",
    "Time (s)", "Cost (USD)", "Total Tokens", "Agent Calls", "Row Count",
    "SQL", "Answer",
]
ws.append(headers)

header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
header_font = Font(color="FFFFFF", bold=True)
thin_border = Border(*(Side(style="thin", color="D9D9D9"),) * 4)
wrap_top = Alignment(wrap_text=True, vertical="top")

for col in range(1, len(headers) + 1):
    c = ws.cell(row=1, column=col)
    c.fill = header_fill
    c.font = header_font
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    c.border = thin_border

ok_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
skip_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
err_fill = PatternFill(start_color="FCE4EC", end_color="FCE4EC", fill_type="solid")

for r in results:
    status = r.get("status", "")
    row = [
        r["id"],
        r.get("module", ""),
        r.get("original", ""),
        r.get("reframed", ""),
        status,
        r.get("intent_mode", ""),
        r.get("intent_complexity", ""),
        r.get("intent_reason", ""),
        r.get("elapsed_s", 0),
        r.get("cost_usd", 0),
        r.get("total_tokens", ""),
        r.get("agent_calls", ""),
        r.get("row_count", ""),
        r.get("sql", ""),
        r.get("answer", ""),
    ]
    ws.append(row)
    row_idx = ws.max_row
    fill = ok_fill if status.startswith("OK") else (skip_fill if status.startswith("SKIPPED") else err_fill)
    for col in range(1, len(headers) + 1):
        c = ws.cell(row=row_idx, column=col)
        c.border = thin_border
        c.alignment = wrap_top
        if col in (5, 6):  # Status, Intent Mode
            c.fill = fill
        if col == 10:  # Cost column
            c.number_format = '"$"#,##0.00000'

# Column widths
widths = {
    "A": 4, "B": 16, "C": 42, "D": 42, "E": 10, "F": 10, "G": 11, "H": 34,
    "I": 9, "J": 12, "K": 12, "L": 11, "M": 10, "N": 55, "O": 55,
}
for col, w in widths.items():
    ws.column_dimensions[col].width = w

ws.freeze_panes = "A2"
ws.auto_filter.ref = ws.dimensions

# ── Sheet 2: summary/totals ────────────────────────────────────────────────
ws2 = wb.create_sheet("Totals")
ws2.append(["Metric", "Value"])
for col in range(1, 3):
    c = ws2.cell(row=1, column=col)
    c.fill = header_fill
    c.font = header_font
    c.alignment = Alignment(horizontal="center")

n = summary["questions_run"]
ok_count = sum(1 for r in results if r["status"].startswith("OK"))
skip_count = sum(1 for r in results if r["status"].startswith("SKIPPED"))
err_count = sum(1 for r in results if r["status"].startswith("ERROR"))
avg_cost = summary["total_cost_usd"] / n if n else 0
avg_time = summary["total_time_s"] / n if n else 0
avg_tokens = sum(r.get("total_tokens") or 0 for r in results) / n if n else 0
zero_row_count = sum(1 for r in results if r.get("row_count") == 0)

totals_rows = [
    ("Questions run", n),
    ("Questions answered OK", ok_count),
    ("Questions skipped (routed to report/other)", skip_count),
    ("Questions errored", err_count),
    ("Report-mode leaks (must be 0)", summary["report_mode_leaks"]),
    ("Questions that returned 0 rows (empty result)", zero_row_count),
    ("", ""),
    ("Total time (s)", round(summary["total_time_s"], 2)),
    ("Average time per question (s)", round(avg_time, 2)),
    ("", ""),
    ("Total cost (USD)", round(summary["total_cost_usd"], 6)),
    ("Average cost per question (USD)", round(avg_cost, 6)),
    ("", ""),
    ("Average tokens per question", round(avg_tokens)),
]
for label, val in totals_rows:
    ws2.append([label, val])

for row_idx in range(2, ws2.max_row + 1):
    for col in range(1, 3):
        c = ws2.cell(row=row_idx, column=col)
        c.border = thin_border
c2 = ws2.cell(row=1 + totals_rows.index(("Total cost (USD)", round(summary["total_cost_usd"], 6))) + 1, column=2)
c2.number_format = '"$"#,##0.000000'
c3 = ws2.cell(row=1 + totals_rows.index(("Average cost per question (USD)", round(avg_cost, 6))) + 1, column=2)
c3.number_format = '"$"#,##0.000000'

ws2.column_dimensions["A"].width = 42
ws2.column_dimensions["B"].width = 18

out_path = "Chat_20_Question_Test_Results.xlsx"
wb.save(out_path)
print(f"Wrote {out_path}")
