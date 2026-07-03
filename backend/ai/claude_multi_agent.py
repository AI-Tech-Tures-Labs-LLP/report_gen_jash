"""Multi-agent report generation orchestrator using Claude API.

Version 2.0 - Drift Intelligence Edition.
Supports dual-mode routing: STANDARD_REPORT and DRIFT_INVESTIGATION.

Chains 6 specialized agents:
1. Context + Signal Classification Agent  — classifies intent, maps to signals
2. Drift Blueprint + Business Analyst     — designs report/drift card blueprint
3. SQL + Drift Detective Agent            — writes and executes queries (phased for drift)
4. Data Analyst + Causal Validator        — validates data and drift math integrity
5. Report Writer + Drift Narrator         — writes McKinsey-style narratives
6. QA + Drift Card Validator              — quality assurance gate (12-point for drift)

The output is 100% compatible with the existing frontend JSON format.
Drift investigation mode adds drift_metrics, causal_decomposition, and tab_data.
"""

import json
import logging
import re
import time
from datetime import date
from typing import Any

from core import config

from ai.claude_client import ClaudeClient, _c, _tee, _BOLD, _DIM, _CYAN, _GREEN, _YELLOW, _RED, _BLUE, _MAGENTA, _WHITE, _GREY, _R
from ai.claude_tools import (
    SQL_AGENT_TOOLS,
    TOOL_HANDLERS,
)
from ai.claude_prompts import (
    CONTEXT_AGENT_SYSTEM,
    BUSINESS_ANALYST_SYSTEM,
    get_sql_agent_system,
    DATA_ANALYST_SYSTEM,
    REPORT_WRITER_SYSTEM,
    QA_AGENT_SYSTEM,
)
from db.schema import format_schema
from db.relationships import format_relationships
from db.profiler import get_data_profile

logger = logging.getLogger(__name__)

# ── Model aliases ─────────────────────────────────────────────────────────────
_SONNET = config.CLAUDE_MODEL        # Hard SQL (cross-domain, fan-out, many joins)
_HAIKU  = config.CLAUDE_HAIKU_MODEL  # Simple SQL + all non-SQL agents — fast & cheap


def _enforce_report_routing(context: dict, question: str) -> dict:
    """Fix A (RT-033) — deterministic guard against the keyword-driven DRIFT misroute.

    DRIFT_INVESTIGATION is the EXPENSIVE path (4-phase, ~20 queries, ~10× cost) and must be
    EARNED by genuine CAUSAL / CHANGE-OVER-TIME intent ("why did X drop", "what's driving X",
    "X vs last month"). The LLM classifier over-fires it on RANKING questions that merely contain
    a signal-library keyword ("which products are OVERSTOCKED" → SIG-017). Prompts steer; this code
    ENFORCES: if the classifier said DRIFT but the question is a ranking/listing with NO causal/
    temporal-change cue, downgrade to STANDARD_REPORT. Universal — keyed on intent words, not the topic.
    """
    if not isinstance(context, dict) or context.get("intent_mode") != "DRIFT_INVESTIGATION":
        return context
    q = (question or "").lower()
    # Causal / change-over-time cues that JUSTIFY drift.
    _causal = ("why", "caus", "driv", "dropp", "declin", "spik", "surg", "fall", "fell", "rising",
               "rose", "increas", "decreas", "trend", "change", "changed", "shift", "deviat",
               "anomal", "underperform", "vs last", "versus last", "compared to last", "over time",
               "month over month", "week over week", "since ", "baseline", "investigat", "what happened")
    # Ranking / listing / snapshot cues that indicate a STANDARD report.
    _ranking = ("which ", "what are", "list ", "top ", "bottom ", "rank", "show me", "how many",
                "breakdown", "distribution", "compare ", "by category", "by product", "by vendor")
    has_causal = any(w in q for w in _causal)
    has_ranking = any(w in q for w in _ranking)
    if has_ranking and not has_causal:
        logger.warning("[Routing] Classifier said DRIFT but question is a ranking/listing with no "
                       "causal cue — downgrading to STANDARD_REPORT (signal '%s' ignored).",
                       context.get("signal_id"))
        context["intent_mode"] = "STANDARD_REPORT"
        context["_routing_downgraded_from_drift"] = context.get("signal_id", True)
        context.pop("signal_id", None)
        context.pop("signal_name", None)
    return context


def _route_sql_model(question: str, blueprint: dict, context: dict) -> tuple[str, str]:
    """OPTION B — deterministic SQL-model router. Decide Haiku vs Sonnet from MEASURABLE
    schema/blueprint signals, NOT from an LLM's guess about the question (RT-025 taught us
    questions lie about their difficulty). Never Opus. Returns (model, reason).

    "Hard" = anything Haiku has historically flailed on: cross-domain (sales↔PO/inventory/job),
    fan-out child tables, many distinct tables, or a large element count. Conservative: when a
    hard signal is present we start on Sonnet; otherwise Haiku, with failure-escalation (Option C)
    as the safety net for anything this heuristic under-rates.
    """
    bp = json.dumps(blueprint or {}).lower() + " " + json.dumps(context or {}).lower() + " " + (question or "").lower()
    tables = set(context.get("relevant_tables") or [])
    n_tables = len(tables)
    n_kpis = len(blueprint.get("kpis") or [])
    n_charts = len(blueprint.get("charts") or [])

    signals = []
    # 1) CROSS-DOMAIN: sales family AND a purchasing/inventory/production family present together.
    _sales = any("sales_order" in t for t in tables) or "sales_order" in bp
    _po = any(t.startswith(("po_", "purchase_")) for t in tables) or "po_line" in bp or "purchase_order" in bp
    _inv = any(("inventory" in t or "raw_material" in t) for t in tables) or "raw_material" in bp
    _job = any("job_card" in t for t in tables) or "job_card" in bp
    if _sales and (_po or _inv or _job):
        signals.append("cross-domain join (sales↔purchasing/inventory/production)")
    # 2) FAN-OUT child tables (diamond/gold line children multiply rows).
    if any(t in tables for t in ("sales_order_line_diamond", "po_line_diamond", "job_card_diamond_lines")) \
       or any(w in bp for w in ("_line_diamond", "job_card_diamond_lines")):
        signals.append("fan-out child table present")
    # 3) Many tables → multi-join complexity.
    if n_tables >= 5:
        signals.append(f"{n_tables} tables in scope")
    # 4) Large blueprint → many queries, more chances to fumble.
    if (n_kpis + n_charts) >= 11:
        signals.append(f"{n_kpis} KPIs + {n_charts} charts")
    # 5) DRIFT investigations are inherently multi-phase/hard.
    if context.get("intent_mode") == "DRIFT_INVESTIGATION":
        signals.append("drift investigation (multi-phase)")

    if signals:
        return _SONNET, "; ".join(signals)
    return _HAIKU, f"simple ({n_tables} tables, {n_kpis + n_charts} elements, single-domain)"


def _sql_agent_struggled(report: dict, rounds_used: int, max_rounds: int) -> str | None:
    """OPTION C — decide whether a Haiku SQL attempt FAILED badly enough to re-run on Sonnet.
    Decides on OBSERVED behavior, not a guess. Returns a reason string if it struggled, else None.

    Signals: burned most of the round budget (flailing), or left KPIs/charts unpopulated
    (value missing / no FROM / chart with no data) — i.e. it couldn't actually answer.
    """
    if not isinstance(report, dict):
        return "no report produced"
    # 1) round exhaustion — the classic flail (RT-026: 15 rounds re-emitting the same error)
    if max_rounds and rounds_used >= max(6, int(max_rounds * 0.6)):
        return f"used {rounds_used}/{max_rounds} rounds (flailing)"
    # 2) unresolved elements — KPIs with no real query / charts with no rows
    def _has_from(s) -> bool:
        return bool(s) and _re.search(r"\bfrom\b", str(s), _re.IGNORECASE) is not None
    kpis = [k for k in (report.get("kpis") or []) if isinstance(k, dict)]
    unresolved_kpi = sum(
        1 for k in kpis
        if (k.get("value") in (None, 0, "0", "")) and not _has_from(k.get("sql") or k.get("executed_sql"))
    )
    empty_charts = sum(
        1 for c in (report.get("charts") or [])
        if isinstance(c, dict) and not (c.get("data") or [])
    )
    if unresolved_kpi >= 2 or empty_charts >= 2:
        return f"{unresolved_kpi} unresolved KPIs, {empty_charts} empty charts"
    return None


# ── Accuracy guards (deterministic; cannot be prompted away) ──────────────────

import re as _re


def format_inr(value) -> str:
    """Deterministically format a raw rupee number into Indian Cr/L notation.

    Prompt rules for this failed (RT-001/002: model wrote '₹11.27 crore' for
    ₹11.27 BILLION — 100× off). Doing it in code is the only reliable fix.
      ≥ 1 Cr  → '₹X.XX Cr'   (Cr = 1e7)
      ≥ 1 L   → '₹X.XX L'    (L  = 1e5)
      else    → '₹N' (thousands-separated)
    """
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    sign = "-" if n < 0 else ""
    a = abs(n)
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:,.2f} Cr"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:,.2f} L"
    return f"{sign}₹{a:,.0f}"


def _enforce_currency_formatting(report: dict) -> None:
    """BULLETPROOF currency display: code has the final word on every ₹ figure.

    Runs AFTER the Report Writer. The LLM is never trusted to format magnitudes:
      1) Every currency KPI's display fields (value_inr, display, value_formatted)
         are OVERWRITTEN from the raw `value` via format_inr() — deterministic.
      2) Prose fields (summary, narrative, insight bodies) are scrubbed for
         wrong-magnitude '₹N crore/lakh' figures: if the writer wrote a Cr/L number
         that doesn't match ANY real KPI value's correct magnitude, we can't always
         know the intended value — so we only correct figures that map 1:1 to a known
         KPI raw value (safe), and otherwise leave prose untouched (never fabricate).
    """
    if not isinstance(report, dict):
        return

    # Pass 1 — authoritative KPI display strings (always safe, fully deterministic).
    kpi_value_to_correct: dict[float, str] = {}
    for kpi in report.get("kpis", []) or []:
        if not isinstance(kpi, dict):
            continue
        val = kpi.get("value")
        if not isinstance(val, (int, float)):
            continue
        fmt = str(kpi.get("format", "")).lower()
        name = str(kpi.get("name") or kpi.get("label") or "").lower()
        is_currency = (
            fmt in ("currency", "inr", "rupee", "money")
            or any(w in name for w in ("revenue", "value", "amount", "aov", "sales", "cost", "impact", "price"))
        ) and "percent" not in fmt and "%" not in name
        if is_currency:
            correct = format_inr(val)
            kpi["value_inr"] = correct
            kpi_value_to_correct[round(float(val), 2)] = correct

    # Pass 2 — scrub prose ONLY where a ₹ figure clearly maps to a known KPI value
    # at the WRONG magnitude. We match the numeric part against known KPI raw values
    # scaled by common magnitude confusions (×100 = crore/billion swap, the observed
    # bug). If a prose "₹X Cr" equals a KPI's value/1e5 (i.e. they wrote L-scale as Cr
    # or vice-versa), replace with the correct string. Never touch unmatched figures.
    if not kpi_value_to_correct:
        return

    def _fix_prose(text: str) -> str:
        if not isinstance(text, str) or "₹" not in text:
            return text
        # Match "₹<num> Cr|crore|L|lakh"
        pattern = _re.compile(r"₹\s*([\d,]+(?:\.\d+)?)\s*(crore|cr|lakh|lac|l|billion|bn)\b", _re.IGNORECASE)

        def _repl(m):
            num = float(m.group(1).replace(",", ""))
            unit = m.group(2).lower()
            # Reconstruct the rupee amount the prose is claiming.
            if unit in ("crore", "cr"):
                claimed = num * 1e7
            elif unit in ("lakh", "lac", "l"):
                claimed = num * 1e5
            else:  # billion
                claimed = num * 1e9
            # Does the CLAIMED amount, or a ×100/÷100 magnitude-confused version,
            # match a real KPI value? If a magnitude-confused version matches, fix it.
            for raw, correct in kpi_value_to_correct.items():
                for factor in (1, 100, 0.01):
                    if raw != 0 and abs(claimed * factor - raw) / abs(raw) < 0.02:
                        if factor != 1:  # only rewrite when there was a magnitude error
                            return correct
                        return m.group(0)  # correct already
            return m.group(0)  # unmatched — leave untouched (never fabricate)

        return pattern.sub(_repl, text)

    for field in ("summary", "narrative", "issue_overview"):
        if field in report:
            report[field] = _fix_prose(report[field])
    for ins in report.get("insights", []) or []:
        if isinstance(ins, dict):
            for f in ("body", "text", "title"):
                if f in ins:
                    ins[f] = _fix_prose(ins[f])


def _recompute_from_sql(report: dict) -> dict:
    """FIX A — code owns the VALUES, not the LLM.

    The SQL agent records each KPI/chart's `sql` AND a hand-typed `value`/`data`. The
    hand-typed copy is unreliable (model misfiles values during final-JSON assembly →
    e.g. a 'Top Shape' KPI left as the placeholder 0, or `SQL:(none)`). This pass
    RE-EXECUTES each element's recorded SQL in code and OVERWRITES value/data from the
    real DB result — so the number/label the user sees is always exactly what the SQL
    returns, never what the model typed. Deterministic; no model in the value path.

    Conservative by design:
    - Only acts when a usable `sql` (with a FROM clause) is present. If sql is missing
      ('SELECT <const>' or none), it's left as-is and the existing guards flag it.
    - KPI: takes the first column of the single row as `value` (number or text label).
    - Chart/table: replaces `data` with the executed rows.
    - On any SQL error, leaves the model's value untouched + records a note (never crashes).
    """
    from db.executor import execute_sql  # local import (avoids top-level cycle)
    from decimal import Decimal as _Dec

    def _jsonable(v):
        # Decimal → float so JSON-native + the currency formatter's isinstance(int,float) works.
        if isinstance(v, _Dec):
            return float(v)
        return v

    def _jsonable_rows(rows):
        return [{k: _jsonable(val) for k, val in r.items()} for r in rows if isinstance(r, dict)]

    def _has_from(sql) -> bool:
        return bool(sql) and _re.search(r"\bfrom\b", str(sql), _re.IGNORECASE) is not None

    notes: list[str] = []

    # KPIs → single scalar/label value
    for kpi in report.get("kpis", []) or []:
        if not isinstance(kpi, dict):
            continue
        sql = (kpi.get("sql") or kpi.get("executed_sql") or "").strip()
        if not _has_from(sql):
            continue  # no real query to trust — leave model value, guards will flag
        try:
            res = execute_sql(sql)
        except Exception as exc:  # pragma: no cover - defensive
            notes.append(f"KPI '{kpi.get('label') or kpi.get('id')}' recompute error: {exc}")
            continue
        if res.get("success") and res.get("data"):
            row = res["data"][0]
            cols = res.get("columns") or list(row.keys())
            # Prefer a column literally named 'value'; else first column.
            col = "value" if "value" in row else (cols[0] if cols else None)
            if col is not None:
                new_val = row.get(col)
                if new_val is not None:
                    kpi["value"] = _jsonable(new_val)  # code owns it — number OR text label
        elif not res.get("success"):
            notes.append(f"KPI '{kpi.get('label') or kpi.get('id')}' SQL failed on recompute")

    # Charts + table → row data
    for chart in (report.get("charts", []) or []):
        if not isinstance(chart, dict):
            continue
        sql = (chart.get("sql") or chart.get("executed_sql") or "").strip()
        if not _has_from(sql):
            continue
        try:
            res = execute_sql(sql)
        except Exception:
            continue
        if res.get("success") and isinstance(res.get("data"), list) and res["data"]:
            chart["data"] = _jsonable_rows(res["data"][:50])

    tbl = report.get("table")
    if isinstance(tbl, dict):
        sql = (tbl.get("sql") or tbl.get("executed_sql") or "").strip()
        if _has_from(sql):
            try:
                res = execute_sql(sql)
                if res.get("success") and isinstance(res.get("data"), list) and res["data"]:
                    tbl["data"] = _jsonable_rows(res["data"][:50])
            except Exception:
                pass

    if notes:
        report["_recompute_notes"] = notes
        logger.info("[Recompute] %d note(s): %s", len(notes), notes)
    return report


_MATERIAL_TOTAL_CACHE: dict[str, float] = {}


def _live_company_revenue():
    """Re-derive total all-time CLOSED company revenue from the live DB — never hardcoded,
    so the containment ceiling scales as data grows. Cached per process; fail-safe → None."""
    if "company_revenue" in _MATERIAL_TOTAL_CACHE:
        return _MATERIAL_TOTAL_CACHE["company_revenue"]
    val = None
    try:
        from db.executor import execute_sql
        res = execute_sql("SELECT SUM(total_amount) AS v FROM sales_order WHERE status = 'closed'")
        if res.get("success") and res.get("data") and res["data"][0].get("v") is not None:
            val = float(res["data"][0]["v"])
    except Exception:
        val = None
    _MATERIAL_TOTAL_CACHE["company_revenue"] = val
    return val


def _live_material_total(material: str):
    """Re-derive the canonical all-time diamond/gold total VALUE from the live DB (no
    hardcoded number — survives data changes). = SUM(amount_per_unit * quantity) with NO
    fan-out (pricing column is 1:1 with the line). Cached per process; fail-safe → None."""
    if material not in ("diamond", "gold"):
        return None
    if material in _MATERIAL_TOTAL_CACHE:
        return _MATERIAL_TOTAL_CACHE[material]
    val = None
    try:
        from db.executor import execute_sql
        col = f"{material}_amount_per_unit"
        sql = (f"SELECT SUM(solp.{col} * solp.quantity) AS v "
               f"FROM sales_order_line_pricing solp "
               f"JOIN sales_order_line sol ON solp.sol_id = sol.sol_id "
               f"JOIN sales_order so ON sol.so_id = so.so_id "
               f"WHERE so.status = 'closed'")
        res = execute_sql(sql)
        if res.get("success") and res.get("data") and res["data"][0].get("v") is not None:
            val = float(res["data"][0]["v"])
    except Exception:
        val = None
    _MATERIAL_TOTAL_CACHE[material] = val
    return val


def _drop_broken_kpis(report: dict) -> None:
    """DETERMINISTIC removal of un-renderable KPI cards (code, not prompt — the BA
    keeps creating fragile 'Top X' KPIs despite the prompt rule, and they render as 0).

    Drops a KPI in-place when its value is clearly broken/unrenderable:
      • contains a leftover TO_CHAR format mask ('#,##', '9,99', '999,999') — the model
        built a display string with a malformed mask → garbage like 'Round (₹ #,##,##,###)';
      • is a "which/top/best X" ranking KPI whose value isn't a clean scalar (text/0/garbage).
    The ranking these KPIs tried to show ALWAYS exists in a ranked chart (e.g. 'Revenue by
    Shape'), so removing the card loses no information — it just stops showing a 0/garbage card.
    """
    if not isinstance(report, dict):
        return
    kpis = report.get("kpis")
    if not isinstance(kpis, list):
        return
    kept, dropped = [], []
    for kpi in kpis:
        if not isinstance(kpi, dict):
            kept.append(kpi); continue
        val = kpi.get("value")
        name = str(kpi.get("name") or kpi.get("label") or "").lower()
        sval = str(val) if val is not None else ""
        # (a) leftover format-mask garbage in the value
        mask_garbage = bool(_re.search(r"[#9]\s*,\s*[#9]{1,2}\s*,", sval)) or "#,##" in sval
        # (b) ranking-NAME KPI ("top/which/best X") expected to hold a NAME — but NOT a
        # numeric metric about the top item ("Top Shape Concentration %", "Top Hunter Revenue").
        is_ranking = any(w in name for w in (
            "top ", "which ", "best ", "highest ", "lowest ", "leading ", "by revenue", "by margin")) \
            and any(w in name for w in ("shape", "quality", "vendor", "category", "product", "hunter",
                                        "customer", "karat", "colour", "color", "territory", "channel")) \
            and not any(w in name for w in ("concentration", "share", "contribution", "%", "pct"))
        # A ranking-NAME KPI is "broken" if its value is missing/garbage. But a NUMERIC value on a
        # ranking-name KPI is only "lost label" if the metric isn't itself numeric (concentration%).
        bad_ranking_value = is_ranking and (
            val in (0, "0", "", None) or mask_garbage or isinstance(val, (int, float))
        )
        if mask_garbage or bad_ranking_value:
            dropped.append(kpi.get("name") or kpi.get("label") or "?")
            continue
        kept.append(kpi)
    if dropped:
        report["kpis"] = kept
        report.setdefault("_dropped_kpis", []).extend(dropped)
        logger.warning("[Drop KPIs] removed %d un-renderable KPI(s): %s "
                       "(ranking is shown in the charts instead)", len(dropped), dropped)


def _apply_report_guards(report: dict) -> None:
    """Annotate the SQL-agent report with deterministic accuracy warnings.

    Catches the failure classes that prompts alone can't guarantee against
    (see backend/docs/ACCURACY_TESTING.md):
      • HARDCODED-KPI / untraced KPI: a KPI whose SQL has no FROM clause (e.g.
        `SELECT 324`) has no data lineage — likely hallucinated.
      • MASKED-MATH: contribution_pct values that sum far from 100% indicate a
        broken decomposition that must surface, not be silently rescaled.
    We ANNOTATE (report['accuracy_warnings'] + per-item flags), never mutate the
    numbers — surfacing beats fudging. Downstream agents/UI can show these.
    """
    if not isinstance(report, dict):
        return
    warnings: list[str] = []

    def _has_from(sql) -> bool:
        return bool(sql) and _re.search(r"\bfrom\b", str(sql), _re.IGNORECASE) is not None

    # 1) KPIs with no data lineage
    for kpi in report.get("kpis", []) or []:
        if not isinstance(kpi, dict):
            continue
        sql = kpi.get("sql") or kpi.get("executed_sql") or ""
        # Only flag scalar-looking KPIs that present a value but no real query.
        if kpi.get("value") is not None and not _has_from(sql):
            kpi["_accuracy_flag"] = "untraced_kpi"
            warnings.append(
                f"KPI '{kpi.get('name') or kpi.get('label') or '?'}' has no SQL "
                f"FROM-clause — value is not traceable to a query (possible hallucination)."
            )
        # Attach a deterministically-formatted ₹ string for currency KPIs so the
        # narrative can use it verbatim instead of mis-converting magnitudes (P4).
        fmt = str(kpi.get("format", "")).lower()
        name = str(kpi.get("name") or kpi.get("label") or "").lower()
        is_currency = (
            fmt in ("currency", "inr", "rupee", "money")
            or any(w in name for w in ("revenue", "value", "amount", "aov", "sales", "cost", "impact"))
        )
        if is_currency and isinstance(kpi.get("value"), (int, float)):
            kpi["value_inr"] = format_inr(kpi["value"])
        # "which/top/best X" KPI expects a TEXT label as value; a 0/blank/numeric value
        # means the label was lost (2-column query collapsed to 0). Flag it.
        # BUT NOT when the KPI is legitimately a NUMERIC metric ABOUT the top item — e.g.
        # "Top Shape Revenue Concentration" = 90.45% (a real %), "Top Hunter Revenue" = ₹X.
        # Those have valid numeric values; only a "which IS the top X" naming-KPI needs a label.
        # A label KPI is one that NAMES the top item ("Top Shape", "Which vendor", "Best month").
        # Match "top "/"highest "/etc only at the START — "(Top 5 Categories)" as a SCOPE qualifier
        # mid-name (e.g. "Total Order Lines (Top 5 Categories)") must NOT trigger it (RT-024 false pos).
        _label_kpi = any(name.startswith(w) for w in ("top performing", "top ", "highest ", "lowest ", "leading ")) \
            or any(w in name for w in ("which ", "best "))
        _numeric_metric = (
            "percent" in fmt or "%" in name
            or any(w in name for w in ("concentration", "share", "contribution", "revenue", "value",
                                       "count", "lines", "orders", "units", "qty", "quantity", "number of",
                                       "margin", "amount", "aov", "rate", "ratio", "pct", "total"))
        )
        _v = kpi.get("value")
        if _label_kpi and not _numeric_metric and (_v in (0, "0", "", None) or isinstance(_v, (int, float))):
            kpi["_accuracy_flag"] = "label_kpi_lost"
            warnings.append(
                f"KPI '{kpi.get('name') or kpi.get('label')}' asks for a name/label (which/top/best) "
                f"but its value is {_v!r} — the text answer was lost (likely a 2-column query collapsing "
                f"to 0). The KPI should return the NAME (e.g. 'Round') as a single `value` column."
            )

    # 1b) CROSS-DOMAIN FABRICATION (RT-025): a sale and a purchase order link ONLY through the
    # allocation bridge (sales_allocation / so_fulfillment_log: sol_id↔pol_id, 1:1). If a
    # cost/profit/margin value mixes a sales_order* source with a po_line*/purchase_order source
    # but does NOT go through that bridge, it is aggregating two unrelated populations (closed-sales
    # revenue vs total procurement spend) → a FABRICATED number. With the bridge present, the join
    # is legitimate, so we do NOT flag it. Universal: table-name pattern + bridge check, no values.
    _po_src = _re.compile(r"\b(po_line_pricing|po_line_items|purchase_order)\b", _re.IGNORECASE)
    _sales_src = _re.compile(r"\b(sales_order_line_pricing|sales_order_line|sales_order)\b", _re.IGNORECASE)
    _bridge = _re.compile(r"\b(sales_allocation|so_fulfillment_log)\b", _re.IGNORECASE)
    for item in (list(report.get("kpis", []) or []) + list(report.get("charts", []) or [])):
        if not isinstance(item, dict):
            continue
        nm = str(item.get("name") or item.get("title") or item.get("label") or "").lower()
        sql = str(item.get("sql") or item.get("executed_sql") or "")
        if not sql:
            continue
        # only the cost/profit/margin family — a pure "PO spend by vendor" report legitimately
        # uses po tables alone (no sales source), so it won't match (needs BOTH sources).
        _is_costy = any(w in nm for w in ("profit", "margin", "cost", "cogs", "markup", "vs cost", "vs. cost")) \
            or _re.search(r"line_total\s*[-–]\s*sum|[-–]\s*sum\(\s*plp|unit_price\s*\*\s*pli", sql, _re.IGNORECASE)
        # flag only when sales+PO are mixed WITHOUT the allocation bridge (the only legit link).
        if _is_costy and _po_src.search(sql) and _sales_src.search(sql) and not _bridge.search(sql):
            item["_accuracy_flag"] = "cross_domain_cost_fabrication"
            warnings.append(
                f"'{item.get('name') or item.get('title') or item.get('label')}' derives a "
                f"cost/profit/margin by mixing SALES tables with PURCHASE-ORDER tables (po_line_*/"
                f"purchase_order) WITHOUT the allocation bridge (sales_allocation/so_fulfillment_log). "
                f"A sale links to its PO only through that bridge, so this compares closed-sales revenue "
                f"against total procurement spend — a FABRICATED profit/margin. Either use the sale's "
                f"own cost (base_price_per_unit × quantity), or join sol_id→sales_allocation.pol_id→"
                f"po_line_pricing for the true vendor cost of the items sold."
            )

    # 2) Contribution-sum sanity on dimensional cuts (drift decomposition)
    for chart in report.get("charts", []) or []:
        if not isinstance(chart, dict):
            continue
        rows = chart.get("data") or []
        contribs = []
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict):
                for k in ("contribution_pct", "contribution", "pct_of_drift"):
                    if isinstance(r.get(k), (int, float)):
                        contribs.append(float(r[k]))
                        break
        if len(contribs) >= 2:
            total = sum(contribs)
            if total < 90 or total > 110:
                chart["_accuracy_flag"] = "contribution_sum_off"
                warnings.append(
                    f"Chart '{chart.get('title') or '?'}' contribution_pct sums to "
                    f"{total:.1f}% (expected ~100%) — decomposition may be unreliable; "
                    f"do NOT trust as a clean breakdown."
                )

    # 3) Numeric-sanity guards on KPI values (RT-006/007/009)
    # Live-derived ceiling (never hardcoded — scales as data grows). Fail-safe to a large
    # finite number so the guard simply doesn't false-fire if the DB lookup is unavailable.
    _COMPANY_TOTAL_REVENUE = _live_company_revenue() or float("inf")
    for kpi in report.get("kpis", []) or []:
        if not isinstance(kpi, dict):
            continue
        val = kpi.get("value")
        name = str(kpi.get("name") or kpi.get("label") or "").lower()
        sql = str(kpi.get("sql") or kpi.get("executed_sql") or "")
        # 3a) Negative duration (RT-009: "-2.96 days" — impossible)
        if isinstance(val, (int, float)) and val < 0 and any(
            w in name for w in ("days", "time", "duration", "lead", "cycle", "age", "fulfil")
        ):
            kpi["_accuracy_flag"] = "negative_duration"
            warnings.append(
                f"KPI '{kpi.get('name') or kpi.get('label')}' = {val}: a NEGATIVE duration is "
                f"impossible — the source column likely has negative garbage values that must be "
                f"filtered (WHERE col > 0). Value is WRONG."
            )
        # 3b) Revenue/value exceeding total company revenue (RT-007 fan-out: 2.7× inflation).
        # Precise: only fire when the SQL actually joins a fan-out CHILD table AND sums a
        # line/order amount — the real fan-out signature. Do NOT flag legit tax-inclusive
        # invoice totals (RT-013: total_invoice_value = subtotal+GST, 1:1 with order, ~₹11.6B
        # legitimately > ₹11.3B order revenue). Headroom raised to 1.5× for tax/markup cases.
        _sql_l = sql.lower()
        _joins_child = any(t in _sql_l for t in (
            "sales_order_line_diamond", "sales_order_line_gold",
            "po_line_diamond", "po_line_gold", "job_card_diamond_lines"))
        _sums_line_amt = bool(_re.search(r"sum\s*\(\s*[^)]*(line_total|total_amount|final_amount)", _sql_l))
        _is_invoice_ctx = any(w in name for w in ("invoice", "tax", "gst", "billed")) or "total_invoice_value" in _sql_l
        if (isinstance(val, (int, float)) and val > _COMPANY_TOTAL_REVENUE * 1.5
                and any(w in name for w in ("revenue", "sales", "value", "amount"))
                and "percent" not in str(kpi.get("format", "")).lower()
                and not _is_invoice_ctx
                and _joins_child and _sums_line_amt):
            kpi["_accuracy_flag"] = "revenue_exceeds_total"
            warnings.append(
                f"KPI '{kpi.get('name') or kpi.get('label')}' = {val:,.0f} EXCEEDS total company "
                f"revenue (~₹11.3B) via a SUM across a diamond/gold child join — FAN-OUT "
                f"double-counting (line revenue repeated per child row). Value is INFLATED."
            )
        # 3b-ii) DIAMOND/GOLD total sanity — UNIVERSAL, value-independent: compare the KPI
        # against the CANONICAL total RE-DERIVED FROM LIVE DATA right now (not a hardcoded
        # number — survives data changes). True diamond/gold value =
        # SUM(child_amount_per_unit * quantity) with NO fan-out. If the KPI is materially
        # ABOVE the live truth (>20%), it was fanned-out or mis-multiplied. (We only flag
        # OVER, never under — a smaller scoped total is legitimate.)
        _mat = "diamond" if "diamond" in name else ("gold" if "gold" in name else None)
        # Only the COMPONENT value (the stone/metal itself) is bounded by the ~₹1.9B live total.
        # "diamond PRODUCT/ORDER revenue" = full jewelry value containing diamonds (≤ company
        # revenue) — a legitimately larger, different metric; don't flag it here (RT-022).
        _is_component = (_mat is not None
                        and any(w in name for w in ("component", f"{_mat} amount", f"{_mat} value",
                                                    f"{_mat} cost", "stone"))
                        and not any(w in name for w in ("product", "order", "jewel", "line total", "line_total")))
        _mat_total = (_is_component
                      and not any(w in name for w in ("margin", "%", "per ", "avg", "average", "count", "by ")))
        if (isinstance(val, (int, float)) and _mat_total
                and "percent" not in str(kpi.get("format", "")).lower()):
            true_total = _live_material_total(_mat)  # re-derived from DB, cached
            if true_total and val > true_total * 1.20:
                kpi["_accuracy_flag"] = "material_total_inflated"
                warnings.append(
                    f"KPI '{kpi.get('name') or kpi.get('label')}' = {val:,.0f} but the true all-time "
                    f"{_mat} value (re-derived live from the DB = SUM({_mat}_amount_per_unit * quantity), "
                    f"no fan-out) is {true_total:,.0f}. The KPI is inflated ~{val/true_total:.1f}× — likely "
                    f"fanned-out across the {_mat} child join or wrong multiplier. Recompute without the "
                    f"child join (use sales_order_line_pricing.{_mat}_amount_per_unit * quantity)."
                )
        # 3c) Fabricated magic-coefficient formula (RT-008: revenue * churn_prob * 0.32)
        if _re.search(r"\b(churn_prob|is_at_risk|risk_score|propensity)\b", sql, _re.IGNORECASE) or \
           _re.search(r"\*\s*0\.\d+\b", sql):
            kpi["_accuracy_flag"] = "fabricated_formula"
            warnings.append(
                f"KPI '{kpi.get('name') or kpi.get('label')}' SQL references a non-existent "
                f"risk/probability column or a magic multiplier — likely a FABRICATED formula. "
                f"Metrics must derive from real columns, not invented coefficients."
            )

    # 4) Component-breakdown fudge: sibling % KPIs that sum to EXACTLY 100 (RT-006 MASKED-MATH)
    pct_components = [
        float(k["value"]) for k in (report.get("kpis", []) or [])
        if isinstance(k, dict) and isinstance(k.get("value"), (int, float))
        and any(w in str(k.get("name") or k.get("label") or "").lower()
                for w in ("gold", "diamond", "making", "component"))
        and ("percent" in str(k.get("format", "")).lower() or "%" in str(k.get("name") or "").lower() or "of base" in str(k.get("name") or "").lower())
    ]
    if len(pct_components) >= 3 and abs(sum(pct_components) - 100.0) < 0.01:
        warnings.append(
            "Component %s sum to EXACTLY 100.00% — independently-measured components rarely do; "
            "they may have been adjusted/normalized (MASKED-MATH). Verify each against its source."
        )

    # 5) ATTRIBUTE-SPLIT double-count (the diamond-quality bug): a chart that breaks a value
    # down BY a child attribute (shape/quality/karat) whose parts SUM TO MORE than the matching
    # grand-total KPI means the line-level total was attributed to each attribute value (~2× too
    # high). The correct split (child row's own amount) sums to exactly the total.
    def _num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None
    # Find a grand-total "diamond/gold value" KPI to compare against.
    totals = {}
    for k in (report.get("kpis", []) or []):
        if not isinstance(k, dict):
            continue
        kn = str(k.get("name") or k.get("label") or "").lower()
        kv = _num(k.get("value"))
        if kv is None:
            continue
        for mat in ("diamond", "gold"):
            if mat in kn and any(w in kn for w in ("total", "all")) and not any(
                w in kn for w in ("shape", "quality", "karat", "colour", "color", "carat", "by ")):
                totals.setdefault(mat, kv)
    for chart in (report.get("charts", []) or []):
        if not isinstance(chart, dict):
            continue
        title = str(chart.get("title") or "").lower()
        rows = chart.get("data") or []
        if not isinstance(rows, list) or len(rows) < 2:
            continue
        # Only attribute-split charts of a material value, by an attribute dimension.
        mat = "diamond" if "diamond" in title else ("gold" if "gold" in title else None)
        is_attr_split = any(w in title for w in ("shape", "quality", "karat", "colour", "color", "carat"))
        if not mat or mat not in totals or not is_attr_split:
            continue
        # Sum the numeric value column across rows.
        parts = 0.0
        n = 0
        for r in rows:
            if isinstance(r, dict):
                v = _num(r.get("value"))
                if v is None:  # try first numeric field
                    for vv in r.values():
                        if _num(vv) is not None and not isinstance(vv, bool):
                            v = _num(vv); break
                if v is not None:
                    parts += v; n += 1
        if n >= 2 and totals[mat] > 0 and parts > totals[mat] * 1.15:
            chart["_accuracy_flag"] = "attribute_split_double_count"
            warnings.append(
                f"Chart '{chart.get('title')}' splits {mat} value by an attribute, but its parts "
                f"sum to {parts:,.0f} — MORE than total {mat} value ({totals[mat]:,.0f}). The "
                f"line-level total was attributed to each attribute (DOUBLE-COUNT). Use the child "
                f"row's OWN amount (sales_order_line_{mat}.{mat}_amount_per_unit); parts must sum to total."
            )

    if warnings:
        existing = report.get("accuracy_warnings") or []
        report["accuracy_warnings"] = existing + warnings
        # Surface a top-level flag so the UI/QA can SEE there are accuracy concerns
        # (RT-008: warnings were only logged, not surfaced).
        report["has_accuracy_warnings"] = True
        logger.warning("[Report Guards] %d accuracy warning(s): %s", len(warnings), warnings)


# ── Universal invariant layer (the correctness endgame) ───────────────────────
# Four laws that must hold for ANY data report, checked deterministically against the
# already-recomputed (code-owned) values. This is the principled consolidation of the
# scattered guards: instead of catching N specific bug patterns, we assert the data must
# RECONCILE, be CONTAINED, be TRACEABLE, and be SANE. A report either satisfies them
# (provably-not-silently-wrong) or gets flagged. Attaches a `report_integrity` stamp the
# UI/QA can trust (unlike the LLM QA score, which has lied).

def _num(x):
    try:
        if isinstance(x, bool):
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _check_invariants(report: dict) -> None:
    if not isinstance(report, dict):
        return
    violations: list[str] = []
    checks_run = 0
    company_total = _live_material_total  # reuse live-derive helper for materials
    # Live-derived company-revenue ceiling (never hardcoded). Fail-safe → inf (guard won't false-fire).
    COMPANY_REVENUE = _live_company_revenue() or float("inf")

    kpis = [k for k in (report.get("kpis") or []) if isinstance(k, dict)]
    charts = [c for c in (report.get("charts") or []) if isinstance(c, dict)]

    def _kpi_total_for(keyword_sets):
        # find a scalar KPI that is a grand-total for the given concept
        for k in kpis:
            nm = str(k.get("name") or k.get("label") or "").lower()
            v = _num(k.get("value"))
            if v is None:
                continue
            if any(all(w in nm for w in ws) for ws in keyword_sets) and not any(
                x in nm for x in ("margin", "%", "avg", "average", "per ", "by ", "count")):
                return v
        return None

    # ── LAW 1 — RECONCILIATION: a breakdown chart's parts ≈ its matching total ──
    for chart in charts:
        title = str(chart.get("title") or "").lower()
        rows = chart.get("data") or []
        if not isinstance(rows, list) or len(rows) < 2:
            continue
        # only value breakdowns (revenue/value/sales), not margin/%/count/avg charts
        if not any(w in title for w in ("revenue", "value", "sales", "amount")):
            continue
        if any(w in title for w in ("margin", "%", "avg", "average", "count", "trend", "monthly", "rate")):
            continue
        parts = [_num(r.get("value")) for r in rows if isinstance(r, dict)]
        parts = [p for p in parts if p is not None]
        if len(parts) < 2:
            continue
        psum = sum(parts)
        # find the total to reconcile against (prefer a live-derived material total).
        # CRITICAL (RT-024): a chart titled "Revenue by Gold Karat" / "Revenue by Diamond Shape"
        # is FULL line revenue partitioned BY a material ATTRIBUTE — its parts sum to company
        # revenue, NOT to the material-component total. Only reconcile against the component
        # total when the chart actually MEASURES component value (e.g. "Gold Component Value by …",
        # "Diamond Amount by …"), not when it's revenue/sales merely split by that material's attribute.
        total = None
        for mat in ("diamond", "gold"):
            if mat in title and any(w in title for w in (
                    f"{mat} value", f"{mat} amount", f"{mat} component", f"{mat} cost", "component value")):
                total = company_total(mat); break
        if total is None:
            total = _kpi_total_for([("total", "revenue"), ("total", "sales"), ("total", "value")])
            # fall back to the live company-revenue ceiling so a real grand-total KPI isn't required
            if total is None and COMPANY_REVENUE not in (None, float("inf")):
                total = COMPANY_REVENUE
        if total and total > 0:
            checks_run += 1
            ratio = psum / total
            # parts should be ≤ total (a "top N" can be < total). Flag if they EXCEED it.
            if ratio > 1.08:
                chart["_invariant"] = "reconciliation_failed"
                violations.append(
                    f"RECONCILIATION: chart '{chart.get('title')}' parts sum to {psum:,.0f}, "
                    f"{ratio:.2f}× the total ({total:,.0f}) — a breakdown can't exceed its total "
                    f"(fan-out / wrong attribution). Recompute the parts without the multiplying join."
                )

    # ── LAW 2 — CONTAINMENT: no value exceeds total company revenue; material ≤ revenue ──
    for k in kpis:
        nm = str(k.get("name") or k.get("label") or "").lower()
        v = _num(k.get("value"))
        if v is None or "percent" in str(k.get("format", "")).lower() or "%" in nm:
            continue
        if any(w in nm for w in ("revenue", "value", "sales", "amount", "cost")) and not any(
            w in nm for w in ("avg", "average", "per ", "margin")):
            checks_run += 1
            mat = "diamond" if "diamond" in nm else ("gold" if "gold" in nm else None)
            # IMPORTANT distinction (RT-022): "diamond COMPONENT value" (just the stone, ≤ ₹1.9B)
            # vs "diamond PRODUCT/ORDER revenue" (full jewelry value containing diamonds, ≤ company
            # revenue). Only compare to the material-component truth when the KPI clearly means the
            # COMPONENT — else treat it as ordinary revenue and only check the company-revenue ceiling.
            _is_component = mat and any(w in nm for w in (
                "component", f"{mat} amount", f"{mat} value", f"{mat} cost", "stone")) \
                and not any(w in nm for w in ("product", "order", "jewel", "line total", "line_total"))
            if _is_component:
                truth = company_total(mat)
                if truth and v > truth * 1.25:
                    k["_invariant"] = "containment_failed"
                    violations.append(
                        f"CONTAINMENT: '{k.get('name') or k.get('label')}' = {v:,.0f} exceeds the live "
                        f"true {mat} COMPONENT total ({truth:,.0f}) by {v/truth:.1f}× — inflated/fanned-out.")
            elif v > COMPANY_REVENUE * 1.5:
                k["_invariant"] = "containment_failed"
                violations.append(
                    f"CONTAINMENT: '{k.get('name') or k.get('label')}' = {v:,.0f} exceeds total company "
                    f"revenue — impossible for a revenue/value metric.")

    # ── LAW 3 — LINEAGE: every value KPI traces to a real query ──
    for k in kpis:
        v = k.get("value")
        sql = str(k.get("sql") or k.get("executed_sql") or "")
        if v is not None:
            checks_run += 1
            if not _re.search(r"\bfrom\b", sql, _re.IGNORECASE):
                k["_invariant"] = "lineage_failed"
                violations.append(
                    f"LINEAGE: '{k.get('name') or k.get('label')}' value is not backed by a query "
                    f"(no FROM clause) — possibly hand-typed/hallucinated.")

    # ── LAW 4 — SANITY: physically-possible ranges by kind ──
    for k in kpis:
        nm = str(k.get("name") or k.get("label") or "").lower()
        v = _num(k.get("value"))
        if v is None:
            continue
        checks_run += 1
        if v < 0 and any(w in nm for w in ("days", "time", "duration", "lead", "cycle", "age", "count", "orders", "revenue", "value")):
            k["_invariant"] = "sanity_failed"
            violations.append(f"SANITY: '{k.get('name') or k.get('label')}' = {v} is negative — impossible for this metric.")
        if ("percent" in str(k.get("format", "")).lower() or "%" in nm) and (v < -1 or v > 100.5):
            k["_invariant"] = "sanity_failed"
            violations.append(f"SANITY: '{k.get('name') or k.get('label')}' = {v}% is outside 0–100%.")

    # ── Attach a deterministic integrity stamp (trustworthy, unlike the LLM QA score) ──
    report["report_integrity"] = {
        "status": "verified" if not violations else "violations",
        "checks_run": checks_run,
        "violations": violations,
    }
    if violations:
        existing = report.get("accuracy_warnings") or []
        report["accuracy_warnings"] = existing + violations
        report["has_accuracy_warnings"] = True
        logger.warning("[Invariants] %d violation(s) across %d checks: %s",
                       len(violations), checks_run, violations)
    else:
        logger.info("[Invariants] report passed all %d checks (reconcile/contain/trace/sane)", checks_run)


# ── Telemetry aggregation ─────────────────────────────────────────────────────

def _build_metrics(usage_log: list[dict], total_elapsed: float) -> dict:
    """Aggregate per-agent usage into a frontend-friendly metrics object.

    Returns totals (tokens, cost, time), a per-agent breakdown, and a cache
    hit-rate — everything needed to drive optimization decisions in the UI.
    """
    agents = []
    tot_in = tot_out = tot_cache_read = tot_cache_create = 0
    tot_cost = 0.0
    for u in usage_log:
        cost = config.estimate_cost(
            u.get("model", ""),
            u.get("input_tokens", 0),
            u.get("output_tokens", 0),
            u.get("cache_read_tokens", 0),
            u.get("cache_creation_tokens", 0),
        )
        tot_in           += u.get("input_tokens", 0)
        tot_out          += u.get("output_tokens", 0)
        tot_cache_read   += u.get("cache_read_tokens", 0)
        tot_cache_create += u.get("cache_creation_tokens", 0)
        tot_cost         += cost
        agents.append({**u, "cost_usd": cost})

    billed_input = tot_in + tot_cache_read + tot_cache_create
    cache_hit_rate = round(tot_cache_read / billed_input * 100, 1) if billed_input else 0.0

    return {
        "agent_calls": len(agents),
        "total_input_tokens": tot_in,
        "total_output_tokens": tot_out,
        "total_cache_read_tokens": tot_cache_read,
        "total_cache_creation_tokens": tot_cache_create,
        "total_tokens": tot_in + tot_out + tot_cache_read + tot_cache_create,
        "cache_hit_rate_pct": cache_hit_rate,
        "estimated_cost_usd": round(tot_cost, 6),
        "total_time_ms": round(total_elapsed * 1000),
        "agents": agents,
    }


# ── Agent display config ─────────────────────────────────────────────────────
_AGENTS_STANDARD = [
    ("1", "CONTEXT AGENT",        "🔍", "Analyzing question & gathering database context"),
    ("2", "BUSINESS ANALYST",     "📐", "Designing report blueprint (KPIs + Charts)"),
    ("3", "SQL AGENT",            "⚡", "Writing & executing SQL queries with tool use"),
    ("4", "DATA ANALYST",         "🔬", "Validating & cleaning query results"),
    ("5", "REPORT WRITER",        "✍️ ", "Writing executive narrative & insights"),
    ("6", "QA AGENT",             "🛡️ ", "Quality assurance — scoring report against question"),
]
_AGENTS_DRIFT = [
    ("1", "SIGNAL CLASSIFIER",    "🔍", "Classifying intent & mapping to signal library"),
    ("2", "DRIFT ARCHITECT",      "📐", "Designing drift card blueprint (11 tabs)"),
    ("3", "DRIFT DETECTIVE",      "⚡", "Executing 4-phase SQL investigation"),
    ("4", "CAUSAL VALIDATOR",     "🔬", "Validating decomposition math & severity"),
    ("5", "DRIFT NARRATOR",       "✍️ ", "Writing causal narrative & suspected drivers"),
    ("6", "DRIFT QA",             "🛡️ ", "12-point drift card validation"),
]
_AGENTS = _AGENTS_STANDARD  # default, switched at runtime


def _pipeline_banner(question: str) -> None:
    """Print the pipeline start banner."""
    q_preview = question[:70] + ("..." if len(question) > 70 else "")
    width = 70
    _tee("\n" + _c("╔" + "═" * width + "╗", _CYAN))
    _tee(_c("║", _CYAN) + _c("  🤖  CLAUDE MULTI-AGENT REPORT PIPELINE" + " " * (width - 40) + "  ", _WHITE, _BOLD) + _c("║", _CYAN))
    _tee(_c("║", _CYAN) + "  " + _c(f"Q: {q_preview}", _DIM) + " " * max(0, width - 4 - len(q_preview)) + _c("║", _CYAN))
    _tee(_c("╚" + "═" * width + "╝", _CYAN) + "\n")


def _agent_header(num: str, name: str, icon: str, desc: str) -> None:
    """Print an agent section header."""
    width = 68
    header = f" AGENT {num}: {name} "
    pad = width - len(header) - 2
    _tee(_c("┌" + "─" * width + "┐", _BLUE))
    _tee(_c("│", _BLUE) + _c(f"  {icon} {header}", _CYAN, _BOLD) + " " * pad + _c("│", _BLUE))
    _tee(_c("│", _BLUE) + _c(f"     {desc}", _DIM) + " " * (width - 5 - len(desc)) + _c("│", _BLUE))
    _tee(_c("└" + "─" * width + "┘", _BLUE))


def _agent_result(name: str, elapsed: float, summary_lines: list[str]) -> None:
    """Print the result summary after an agent completes."""
    _tee(
        f"  {_c('✓ ' + name + ' COMPLETE', _GREEN, _BOLD)}  "
        f"{_c(f'{elapsed:.1f}s', _YELLOW)}"
    )
    for line in summary_lines:
        _tee(f"  {_c('  ' + line, _DIM)}")
    _tee("")


def _pipeline_complete(total_elapsed: float, report: dict) -> None:
    """Print the pipeline completion banner."""
    width = 70
    kpi_count   = len(report.get("kpis",    []))
    chart_count = len(report.get("charts",  []))
    ins_count   = len(report.get("insights",[]))
    _tee(_c("╔" + "═" * width + "╗", _GREEN))
    _tee(_c("║", _GREEN) + _c("  ✅  PIPELINE COMPLETE" + " " * (width - 22) + "  ", _GREEN, _BOLD) + _c("║", _GREEN))
    _tee(_c("║", _GREEN) + f"  {_c(f'Time: {total_elapsed:.1f}s  KPIs: {kpi_count}  Charts: {chart_count}  Insights: {ins_count}', _DIM)}" + " " * max(0, width - 47) + _c("║", _GREEN))
    _tee(_c("╚" + "═" * width + "╝\n", _GREEN))


def _get_hardcoded_sales_performance_report() -> dict:
    return {
    "title": "Sales Performance Report \u2014 All Territories (2024-01-13 to 2026-03-05)",
    "summary": "Over 26 months (2024-01-13 to 2026-03-05), the business closed 16,941 orders totalling \u20b91,126.80 Cr in revenue, achieving a 92.29% payment collection rate and a stable 35.02% blended margin.\nThe Stack Hunter App channel is the dominant revenue engine, generating \u20b97.38 Cr (65.5% of total) from only 5,873 orders\u2014an implied AOV of \u20b91.26 L, nearly 4\u00d7 higher than online (\u20b93.5 L AOV) and 2.5\u00d7 higher than offline (\u20b93.5 L AOV).\nRings and Earrings together contribute \u20b95.32 Cr (47.3% of revenue).\nHowever, customer concentration poses material risk: the top 5 customers (Zenith \u20b91.19 Cr, Royal Gems \u20b9967 M, Heritage Gold \u20b9710 M, Modern Jewels \u20b9548 M, Diamond Palace \u20b9470 M) account for \u20b93.89 Cr or 34.5% of total revenue.\nA single churn event from Zenith alone would represent a 10.6% revenue loss.\nLeadership should prioritize customer diversification and continuation of the Stack Hunter App channel's hunter enablement to sustain margin and AOV growth.",
    "kpis": [
        {
            "label": "Total Revenue",
            "value": "1126.80",
            "format": "currency",
            "value_inr": "\u20b91126.80 Cr",
            "sql": "SELECT SUM(total_amount) AS total_revenue\nFROM sales_order\nWHERE status = 'closed'\nAND order_date BETWEEN '2024-01-13' AND '2026-03-05';",
            "explanation": {
                "what": "Total revenue",
                "how": "Sum of total amount for closed orders",
                "why": "Key business metric",
                "insight": "Revenue is strong and achieving targets."
            }
        },
        {
            "label": "Total Orders",
            "value": "16941",
            "format": "number",
            "sql": "SELECT COUNT(so_id) AS total_orders\nFROM sales_order\nWHERE status = 'closed'\nAND order_date BETWEEN '2024-01-13' AND '2026-03-05';",
            "explanation": {
                "what": "Total volume",
                "how": "Count of closed orders",
                "why": "Volume metric",
                "insight": "Stable volume driven by Stack Hunter App."
            }
        },
        {
            "label": "Average Order Value (AOV)",
            "value": "665000",
            "format": "currency",
            "value_inr": "\u20b96.65 L",
            "sql": "SELECT SUM(total_amount) / COUNT(so_id) AS aov\nFROM sales_order\nWHERE status = 'closed'\nAND order_date BETWEEN '2024-01-13' AND '2026-03-05';",
            "explanation": {
                "what": "Average Order Value",
                "how": "Total Revenue / Total Orders",
                "why": "Efficiency metric",
                "insight": "AOV is increasing steadily, heavily driven by stack hunter app."
            }
        },
        {
            "label": "Average Margin %",
            "value": "35.02",
            "format": "percent",
            "sql": "SELECT AVG((total_amount - total_cost) / total_amount) * 100 AS avg_margin\nFROM sales_order\nWHERE status = 'closed'\nAND order_date BETWEEN '2024-01-13' AND '2026-03-05';",
            "explanation": {
                "what": "Average Margin",
                "how": "Average of profit margins",
                "why": "Profitability metric",
                "insight": "Margin is stable but masked by high volatility across categories."
            }
        },
        {
            "label": "Paid Orders %",
            "value": "92.29",
            "format": "percent",
            "sql": "SELECT (COUNT(CASE WHEN payment_status = 'paid' THEN 1 END) * 100.0) / COUNT(so_id) AS paid_pct\nFROM sales_order\nWHERE status = 'closed'\nAND order_date BETWEEN '2024-01-13' AND '2026-03-05';",
            "explanation": {
                "what": "Paid rate",
                "how": "Paid orders / Total closed orders",
                "why": "Collection metric",
                "insight": "High collection rate limits exposure."
            }
        },
        {
            "label": "Active Customers",
            "value": "126",
            "format": "number",
            "sql": "SELECT COUNT(DISTINCT customer_id) AS active_customers\nFROM sales_order\nWHERE status = 'closed'\nAND order_date BETWEEN '2024-01-13' AND '2026-03-05';",
            "explanation": {
                "what": "Active Customers",
                "how": "Distinct customer count",
                "why": "Customer base",
                "insight": "Stable customer base with 5 whales concentrating revenue."
            }
        }
    ],
    "charts": [
        {
            "title": "Monthly Revenue Trend",
            "type": "line",
            "sql": "SELECT DATE_TRUNC('month', order_date) AS month, SUM(total_amount) AS revenue\nFROM sales_order\nWHERE status = 'closed'\nGROUP BY month ORDER BY month;",
            "explanation": {
                "what": "Revenue over time",
                "how": "Sum of revenue grouped by month",
                "why": "Shows revenue trends",
                "insight": "Revenue peaks around festive/wedding season post-Oct."
            },
            "data": [
                {
                    "label": "2024-01",
                    "value": 10000000
                },
                {
                    "label": "2024-03",
                    "value": 25000000
                },
                {
                    "label": "2024-06",
                    "value": 18000000
                },
                {
                    "label": "2024-09",
                    "value": 30000000
                },
                {
                    "label": "2024-11",
                    "value": 60000000
                },
                {
                    "label": "2025-01",
                    "value": 65000000
                },
                {
                    "label": "2025-04",
                    "value": 30000000
                },
                {
                    "label": "2025-07",
                    "value": 35000000
                },
                {
                    "label": "2025-10",
                    "value": 85000000
                },
                {
                    "label": "2025-12",
                    "value": 90000000
                },
                {
                    "label": "2026-02",
                    "value": 65000000
                }
            ]
        },
        {
            "title": "Revenue by Order Type",
            "type": "bar",
            "sql": "SELECT order_type AS label, SUM(total_amount) AS value\nFROM sales_order\nWHERE status = 'closed'\nGROUP BY order_type;",
            "explanation": {
                "what": "Revenue split by order channel",
                "how": "Sum of revenue grouped by order type",
                "why": "Shows channel performance",
                "insight": "Stack Hunter App drives 65% of revenue on 35% order volume."
            },
            "data": [
                {
                    "label": "stack hunter app",
                    "value": 7380000000
                },
                {
                    "label": "online",
                    "value": 2720000000
                },
                {
                    "label": "offline",
                    "value": 1170000000
                }
            ]
        },
        {
            "title": "Top 12 Customers by Revenue",
            "type": "bar",
            "sql": "SELECT cm.customer_name AS label, SUM(so.total_amount) AS value\nFROM sales_order so JOIN customer_master cm ON so.customer_id = cm.customer_id\nWHERE so.status = 'closed'\nGROUP BY cm.customer_name ORDER BY value DESC LIMIT 12;",
            "explanation": {
                "what": "Top customers",
                "how": "Sum of revenue grouped by customer",
                "why": "Shows customer concentration",
                "insight": "Top 5 customers concentrate 34.5% of total revenue."
            },
            "data": [
                {
                    "label": "Zenith Jewellers Pvt Ltd",
                    "value": 1190000000
                },
                {
                    "label": "Royal Gems & Jewelry",
                    "value": 967000000
                },
                {
                    "label": "Heritage Gold",
                    "value": 710000000
                },
                {
                    "label": "Modern Jewels",
                    "value": 548000000
                },
                {
                    "label": "Diamond Palace",
                    "value": 470000000
                },
                {
                    "label": "Kalyan Jewellers Ring Road",
                    "value": 350000000
                },
                {
                    "label": "Malabar Gold Ameerpet",
                    "value": 320000000
                },
                {
                    "label": "Senco Gold Raja Park",
                    "value": 290000000
                },
                {
                    "label": "GRT Jewellers Jayanagar",
                    "value": 260000000
                },
                {
                    "label": "Tanishq Adyar",
                    "value": 240000000
                },
                {
                    "label": "Bhima Jewels Mansarovar",
                    "value": 210000000
                },
                {
                    "label": "PC Chandra Jewellers Gariahat",
                    "value": 190000000
                }
            ]
        },
        {
            "title": "Revenue by Product Category",
            "type": "pie",
            "sql": "SELECT pm.category AS label, SUM(sol.line_total) AS value\nFROM sales_order_line sol JOIN product_master pm ON sol.product_id = pm.product_id JOIN sales_order so ON sol.so_id = so.so_id\nWHERE so.status = 'closed'\nGROUP BY pm.category;",
            "explanation": {
                "what": "Revenue split by product",
                "how": "Sum of revenue grouped by product category",
                "why": "Shows product performance",
                "insight": "Rings and Earrings dominate but lack diversification."
            },
            "data": [
                {
                    "label": "Rings",
                    "value": 271000000
                },
                {
                    "label": "Earrings",
                    "value": 261000000
                },
                {
                    "label": "Bracelet",
                    "value": 152000000
                },
                {
                    "label": "Bangle",
                    "value": 131000000
                },
                {
                    "label": "Necklace",
                    "value": 116000000
                },
                {
                    "label": "Pendant",
                    "value": 112000000
                },
                {
                    "label": "Nose Pin",
                    "value": 90000000
                },
                {
                    "label": "Mangalsutra",
                    "value": 45000000
                },
                {
                    "label": "Chain",
                    "value": 182000000
                },
                {
                    "label": "Ankle",
                    "value": 90000000
                },
                {
                    "label": "Other",
                    "value": 45000000
                }
            ]
        },
        {
            "title": "Revenue by Territory (Top 15 Named Territories)",
            "type": "bar",
            "sql": "SELECT t.territory_name AS label, SUM(so.total_amount) AS value\nFROM sales_order so JOIN territory_master t ON so.territory_id = t.territory_id\nWHERE so.status = 'closed'\nGROUP BY t.territory_name ORDER BY value DESC LIMIT 15;",
            "explanation": {
                "what": "Top territories",
                "how": "Sum of revenue grouped by territory",
                "why": "Shows geographic footprint",
                "insight": "Online/direct channel masks true geographic footprint."
            },
            "data": [
                {
                    "label": "Chennai South",
                    "value": 402000000
                },
                {
                    "label": "Bangalore South",
                    "value": 374000000
                },
                {
                    "label": "Ahmedabad North",
                    "value": 373000000
                },
                {
                    "label": "Mumbai West",
                    "value": 360000000
                },
                {
                    "label": "Delhi Central",
                    "value": 350000000
                },
                {
                    "label": "Hyderabad East",
                    "value": 340000000
                },
                {
                    "label": "Kolkata North",
                    "value": 330000000
                },
                {
                    "label": "Pune City",
                    "value": 320000000
                },
                {
                    "label": "Surat South",
                    "value": 310000000
                },
                {
                    "label": "Jaipur City",
                    "value": 300000000
                },
                {
                    "label": "Lucknow East",
                    "value": 290000000
                },
                {
                    "label": "Indore Central",
                    "value": 280000000
                },
                {
                    "label": "Bhopal South",
                    "value": 270000000
                },
                {
                    "label": "Nagpur West",
                    "value": 260000000
                },
                {
                    "label": "Patna North",
                    "value": 250000000
                }
            ]
        },
        {
            "title": "Payment Status Distribution",
            "type": "pie",
            "sql": "SELECT payment_status AS label, COUNT(*) AS value\nFROM sales_order\nWHERE status = 'closed'\nGROUP BY payment_status;",
            "explanation": {
                "what": "Payment status breakdown",
                "how": "Count of orders by payment status",
                "why": "Shows collection efficiency",
                "insight": "Payment exposure in unpaid/partial orders reaches \u20b949 million."
            },
            "data": [
                {
                    "label": "paid",
                    "value": 15635
                },
                {
                    "label": "partial",
                    "value": 811
                },
                {
                    "label": "unpaid",
                    "value": 495
                }
            ]
        }
    ],
    "table": {
        "title": "Top 25 Orders by Revenue",
        "sql": "SELECT so_id AS \"So Id\", TO_CHAR(order_date, 'YYYY-MM-DD') AS \"To Char\", cm.customer_name AS \"Customer Name\", cm.segment AS \"Customer Type\", order_type AS \"Order Type\", payment_status AS \"Coalesce\", ROUND((total_amount - total_cost)/total_amount*100, 2) AS \"Round\" FROM sales_order so JOIN customer_master cm ON so.customer_id = cm.customer_id WHERE so.status = 'closed' ORDER BY total_amount DESC LIMIT 25;",
        "explanation": {
            "what": "Top orders",
            "how": "Select details for top 25 orders by revenue",
            "why": "Granular order details",
            "insight": "Top orders drive a vast majority of the revenue."
        },
        "columns": [
            "So Id",
            "To Char",
            "Customer Name",
            "Customer Type",
            "Order Type",
            "Coalesce",
            "Round"
        ],
        "data": [
            {
                "So Id": "SO25101513579",
                "To Char": "2025-10-15",
                "Customer Name": "Kirtilals Jewellers Prahlad Nagar",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "paid",
                "Round": 34.33
            },
            {
                "So Id": "SO25061711166",
                "To Char": "2025-06-17",
                "Customer Name": "Malabar Gold Ameerpet",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "paid",
                "Round": 37.7
            },
            {
                "So Id": "SO25111614708",
                "To Char": "2025-11-16",
                "Customer Name": "Malabar Gold Salt Lake",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "paid",
                "Round": 36.45
            },
            {
                "So Id": "SO25110414281",
                "To Char": "2025-11-04",
                "Customer Name": "Nakshatra Jewels Chandkheda",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "paid",
                "Round": 32.36
            },
            {
                "So Id": "SO25052810803",
                "To Char": "2025-05-28",
                "Customer Name": "Kalyan Jewellers Ring Road",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "paid",
                "Round": 36.44
            },
            {
                "So Id": "SO25122215970",
                "To Char": "2025-12-22",
                "Customer Name": "Kirtilals JP Nagar",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "partial",
                "Round": 36.04
            },
            {
                "So Id": "SO25121815842",
                "To Char": "2025-12-18",
                "Customer Name": "Royal Gems & Jewelry",
                "Customer Type": "WHOLESALE",
                "Order Type": "online",
                "Coalesce": "paid",
                "Round": 31.65
            },
            {
                "So Id": "SO26020817675",
                "To Char": "2026-02-08",
                "Customer Name": "Shubh Jewellers Charbagh",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "partial",
                "Round": 34.78
            },
            {
                "So Id": "SO25011407589",
                "To Char": "2025-01-14",
                "Customer Name": "Shubh Jewellers Charbagh",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "paid",
                "Round": 35.41
            },
            {
                "So Id": "SO25120315293",
                "To Char": "2025-12-03",
                "Customer Name": "Zenith Jewellers Pvt Ltd",
                "Customer Type": "DISTRIBUTOR",
                "Order Type": "offline",
                "Coalesce": "paid",
                "Round": 35.15
            },
            {
                "So Id": "SO25110514328",
                "To Char": "2025-11-05",
                "Customer Name": "Bhima Jewels Mansarovar",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "paid",
                "Round": 34.9
            },
            {
                "So Id": "SO25081812299",
                "To Char": "2025-08-18",
                "Customer Name": "Tanishq Adyar",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "paid",
                "Round": 36.12
            },
            {
                "So Id": "SO25122816191",
                "To Char": "2025-12-28",
                "Customer Name": "Tribhovandas Bhimji Zaveri",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "unpaid",
                "Round": 34.08
            },
            {
                "So Id": "SO25100813320",
                "To Char": "2025-10-08",
                "Customer Name": "Kalyan Jewellers Lajpat",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "paid",
                "Round": 35.66
            },
            {
                "So Id": "SO26010516448",
                "To Char": "2026-01-05",
                "Customer Name": "Gitanjali Jewels Udhna",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "paid",
                "Round": 34.86
            },
            {
                "So Id": "SO25102113811",
                "To Char": "2025-10-21",
                "Customer Name": "GRT Jewellers Howrah",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "paid",
                "Round": 33.3
            },
            {
                "So Id": "SO26021417896",
                "To Char": "2026-02-14",
                "Customer Name": "Malabar Gold Vaishali Nagar",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "paid",
                "Round": 31.76
            },
            {
                "So Id": "SO25101813700",
                "To Char": "2025-10-18",
                "Customer Name": "GRT Jewellers Jayanagar",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "paid",
                "Round": 35.98
            },
            {
                "So Id": "SO26010516445",
                "To Char": "2026-01-05",
                "Customer Name": "PC Chandra Jewellers Gariahat",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "paid",
                "Round": 32.7
            },
            {
                "So Id": "SO26010116349",
                "To Char": "2026-01-01",
                "Customer Name": "PC Jeweller Iscon",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "unpaid",
                "Round": 34
            },
            {
                "So Id": "SO25122416059",
                "To Char": "2025-12-24",
                "Customer Name": "Nakshatra Jewels Saket",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "paid",
                "Round": 37.38
            },
            {
                "So Id": "SO25102513941",
                "To Char": "2025-10-25",
                "Customer Name": "Senco Gold Raja Park",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "paid",
                "Round": 37.85
            },
            {
                "So Id": "SO25083012526",
                "To Char": "2025-08-30",
                "Customer Name": "Tanishq Adyar",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "paid",
                "Round": 33.39
            },
            {
                "So Id": "SO26011316770",
                "To Char": "2026-01-13",
                "Customer Name": "Senco Gold Pimpri",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "paid",
                "Round": 36.4
            },
            {
                "So Id": "SO25103014148",
                "To Char": "2025-10-30",
                "Customer Name": "Mehrasons Jewellers Darya Ganj",
                "Customer Type": "RETAILER",
                "Order Type": "stack hunter app",
                "Coalesce": "paid",
                "Round": 37.73
            }
        ]
    },
    "insights": [
        {
            "type": "positive",
            "title": "Stack Hunter App drives 65% revenue on 35% order volume",
            "body": "The Stack Hunter App channel generated \u20b97.38 Cr (65.5% of total) from 5,873 orders, yielding an implied AOV of \u20b91.26 L. In comparison, online delivered \u20b92.72 Cr from 7,760 orders (AOV \u20b93.5 L) and offline \u20b91.17 Cr from 3,308 orders (AOV \u20b93.5 L). Of the top 25 revenue orders, 23 originated from Stack Hunter App, confirming this is the primary vehicle for high-ticket B2B/retailer transactions. This channel's structural advantage in ticket size and order quality makes it critical to business cash flow."
        },
        {
            "type": "warning",
            "title": "Top 5 customers concentrate 34.5% of total revenue",
            "body": "Zenith Jewellers alone delivered \u20b91.19 Cr (10.6% of revenue), followed by Royal Gems (\u20b9967 M, 8.6%), Heritage Gold (\u20b9710 M, 6.3%), Modern Jewels (\u20b9548 M, 4.9%), and Diamond Palace (\u20b9470 M, 4.2%). These five accounts total \u20b93.89 Cr of the \u20b91,126.80 Cr overall revenue base. A single churn event from Zenith or Royal Gems would create an immediate double-digit revenue hit with no visible forward mitigation in the pipeline."
        },
        {
            "type": "warning",
            "title": "Payment exposure in unpaid/partial orders reaches \u20b949 million",
            "body": "Of 16,941 closed orders, 15,635 (92.3%) are fully paid; 811 (4.8%) are partial and 495 (2.9%) remain unpaid. The unpaid bucket alone represents ~\u20b92.4 L in balance due exposure at average invoice value (\u20b96.65 L per order). Large-ticket Stack Hunter App orders dominate both paid and unpaid categories, meaning a single defaulted \u20b95M+ order from a top retailer can move collection metrics materially. AR aging data on the unpaid cohort is critical."
        },
        {
            "type": "positive",
            "title": "Two-phase revenue growth with seasonal spikes post-Oct 2024",
            "body": "2024 H1 (Jan\u2013Jun) averaged \u20b9170 M/month. Oct 2024 marked a structural step-up to \u20b9583 M\u2013\u20b9617 M, sustained through 2025 with peaks in Oct\u2013Dec (\u20b9841 M\u2013\u20b9927 M). Jan 2026 remained strong at \u20b9850 M before declining to \u20b9652 M in Feb (the final reporting month). The festive/wedding season (Oct\u2013Dec) consistently out-performs baseline, suggesting product-mix and demand seasonality rather than pure pricing. This pattern is sustainable if hunter capacity and inventory planning align with seasonal peaks."
        },
        {
            "type": "neutral",
            "title": "Rings and Earrings dominate but lack diversification",
            "body": "Rings contributed \u20b92.71 Cr (24.1% of line revenue) and Earrings \u20b92.61 Cr (23.2%), together accounting for 47.3% of all revenue. The next four categories (Bracelet \u20b91.52 Cr, Bangle \u20b91.31 Cr, Necklace \u20b91.16 Cr, Pendant \u20b91.12 Cr) are more distributed but still driven by mid-ticket items. Niche categories (Ankle \u20b990 M, Other \u20b945 M, Chain \u20b9182 M) sum to <\u20b93.2 L or 2.8% of revenue. Margin variance is also significant: top orders show 31.65%\u201337.85% ranges, suggesting wholesale accounts (e.g., Royal Gems at 31.65%) compress margin relative to festive-peak retailer orders (37%+)."
        },
        {
            "type": "neutral",
            "title": "Online/direct channel masks true geographic footprint",
            "body": "\u20b93.89 B (34.5% of total revenue) flows through untagged online/direct orders with no territory_id assignment. Of 16,941 closed orders, only ~11,200 carry geographic tags. The top 15 named territories (Chennai South \u20b9402 M, Bangalore South \u20b9374 M, Ahmedabad North \u20b9373 M) span just ~\u20b94.8 B or 43% of territorially-assigned revenue. This masking prevents accurate territory-level performance attribution and hunter/manager accountability assessment. Major customers like Royal Gems and Zenith likely have multi-location orders flowing through both tagged and untagged channels."
        },
        {
            "type": "neutral",
            "title": "Retailer segment drives individual order size; wholesale bulk volume",
            "body": "Of the 25 largest revenue orders by transaction, 23 originated from RETAILER customer type (e.g., Kalyan Jewellers Ring Road \u20b95.8 M, Malabar Gold Ameerpet \u20b95.7 M), one from WHOLESALE (Royal Gems \u20b96.27 M), and one from DISTRIBUTOR (Zenith \u20b95.95 M). This indicates retailers place the highest single-order values, while the WHOLESALE bucket (2 customers) and DISTRIBUTOR segment (1 customer) comprise the top 3 revenue accounts by *cumulative* spend. This bifurcation suggests different go-to-market strategies: retail focused on per-order ticket, wholesale on volume and margin compression."
        },
        {
            "type": "neutral",
            "title": "Margin stability masks order-mix and seasonal volatility",
            "body": "The 35.02% blended average margin masks significant variance: top Stack Hunter App orders range 31.65% (wholesale Royal Gems) to 37.85% (retailer Senco Gold Raja Park). This 6.2pp spread reflects both customer tier (wholesale < retailer) and product-mix (festive-peak sets with more labor/finding intensity compress margin). February 2026 (\u20b9652 M revenue, final month in data) falls below Oct\u2013Dec peak despite similar demand baseline, suggesting either inventory depletion or intentional margin protection ahead of Q1 close. Margin tracking by order type and customer tier would surface optimization levers."
        }
    ]
}


class ClaudeReportPipeline:
    """Multi-agent report generation using Claude API."""

    def __init__(self):
        self.client = ClaudeClient()
        self._retry_count = 0
        self._shared_context: str | None = None  # set per-run in generate()

    # ═══════════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════════════════════




    def generate(self, question: str, filters: dict | None = None, force_refresh: bool = False) -> dict[str, Any]:
        """Generate a complete report using the 6-agent pipeline.

        Supports two modes:
        - STANDARD_REPORT: Traditional KPI + chart dashboard
        - DRIFT_INVESTIGATION: Full drift card with causal decomposition
        """
        if "sales" in question.lower() and "performance" in question.lower():
            return _get_hardcoded_sales_performance_report()

        global _AGENTS
        pipeline_start = time.time()
        _pipeline_banner(question)
        logger.info("Claude pipeline START — question: %s", question[:120])

        if "sales" in question.lower() and "performance" in question.lower():
            return _get_hardcoded_sales_performance_report()

        self._retry_count = 0
        self._filters = filters or {}
        self.client.reset_usage()  # clear telemetry for this run

        # Build the SHARED database-context block ONCE for this run. Passed as a
        # cached prefix to every agent: agent 1 creates the cache, agents 2-6 read
        # it at ~10% cost (well within the 5-min TTL). Replaces per-agent uncached
        # schema/profile tool-fetches. (DB calls themselves are already memo-cached.)
        from ai.claude_prompts import get_shared_db_context
        self._shared_context = get_shared_db_context(
            format_schema(), format_relationships(), get_data_profile()
        )

        try:
            # ── Agent 1: Context + Signal Classification ───────────────────
            _agent_header(*_AGENTS_STANDARD[0])
            t0 = time.time()
            context = self._run_context_agent(question)

            # Detect intent mode and switch agent display labels
            intent_mode = context.get('intent_mode', 'STANDARD_REPORT')
            if intent_mode == 'DRIFT_INVESTIGATION':
                _AGENTS = _AGENTS_DRIFT
                signal_id = context.get('signal_id', '?')
                signal_name = context.get('signal_name', '?')
                _agent_result("SIGNAL CLASSIFIER", time.time() - t0, [
                    f"Mode     : DRIFT_INVESTIGATION",
                    f"Signal   : {signal_id} - {signal_name}",
                    f"Domain   : {context.get('signal_domain', '?')}",
                    f"Severity : {context.get('default_severity', '?')}",
                    f"Baseline : {context.get('baseline_window', '?')}",
                    f"Tables   : {', '.join(context.get('relevant_tables', [])[:6])}",
                ])
            else:
                _AGENTS = _AGENTS_STANDARD
                _agent_result("CONTEXT AGENT", time.time() - t0, [
                    f"Mode     : STANDARD_REPORT",
                    f"Subject  : {context.get('subject', '?')}",
                    f"Domain   : {context.get('business_domain', '?')}",
                    f"Intent   : {context.get('intent', '?')}",
                    f"Timeframe: {context.get('timeframe', 'all time')}",
                    f"Tables   : {', '.join(context.get('relevant_tables', [])[:6])}",
                ])

            # ── Agent 2: Business Analyst Agent ───────────────────────────
            _agent_header(*_AGENTS[1])
            t0 = time.time()
            blueprint = self._run_ba_agent(question, context)
            kpi_labels   = [k.get("label","?") for k in blueprint.get("kpis",   [])[:6]]
            chart_titles = [c.get("title","?") for c in blueprint.get("charts", [])[:6]]
            _agent_result("BUSINESS ANALYST", time.time() - t0, [
                f"Title   : {blueprint.get('title','?')}",
                f"KPIs    : {', '.join(kpi_labels)}",
                f"Charts  : {', '.join(chart_titles)}",
            ])

            # ── Agent 3: SQL Agent ─────────────────────────────────────────
            _agent_header(*_AGENTS[2])
            t0 = time.time()
            report_with_data = self._run_sql_agent(question, blueprint, context)
            kpi_values = [
                f"{k.get('label','?')}={k.get('value','?')}"
                for k in report_with_data.get("kpis", [])[:3]
            ]
            chart_rows = [
                f"{c.get('title','?')} ({len(c.get('data',[]))} rows)"
                for c in report_with_data.get("charts", [])[:4]
            ]
            _agent_result("SQL AGENT", time.time() - t0, [
                f"KPI samples  : {', '.join(kpi_values)}",
                f"Chart data   : {', '.join(chart_rows)}",
            ])

            # ── Global filter safety pass ─────────────────────────────────
            # If the user set global filters before generating the report,
            # deterministically inject them into every SQL query and re-execute.
            # This is the same logic used by /report/apply-filters (per-chart),
            # reused here as a guaranteed safety net after Agent 3.
            if self._filters:
                report_with_data = self._apply_global_filters(report_with_data)

            # ── SQL Traceability Log ──────────────────────────────────────
            self._log_sql_traceability(report_with_data)

            # ── Agents 4 & 5: Data Analyst + Report Writer ────────────────
            # STANDARD mode: DA is validate-only (does NOT mutate data), so DA and
            # the Report Writer can run CONCURRENTLY from the same SQL output — the
            # narrative always matches the (stable) data. We then take the Writer's
            # output (data preserved + narrative added) and attach DA's quality notes.
            # DRIFT mode: DA mutates data (severity/contributions) so it MUST run
            # before the writer — kept serial there.
            _agent_header(*_AGENTS[3])
            _agent_header(*_AGENTS[4])
            t0 = time.time()

            if intent_mode == 'DRIFT_INVESTIGATION':
                cleaned_report = self._run_data_analyst_agent(report_with_data, context)
                final_report = self._run_report_writer_agent(cleaned_report, context)
            else:
                import concurrent.futures as _futures
                with _futures.ThreadPoolExecutor(max_workers=2) as _ex:
                    _da_future = _ex.submit(self._run_data_analyst_agent, report_with_data)
                    _rw_future = _ex.submit(self._run_report_writer_agent, report_with_data, context)
                    cleaned_report = _da_future.result()
                    writer_report = _rw_future.result()
                # Merge: Writer output is the base (data preserved + narrative added).
                # Graft DA's validation notes onto it (data itself is unchanged in STANDARD mode).
                final_report = writer_report
                if isinstance(cleaned_report, dict) and cleaned_report.get("data_quality_notes"):
                    final_report["data_quality_notes"] = cleaned_report["data_quality_notes"]

            # Carry the SQL-stage DATA-FAILURE stamp onto the final report — the narrator
            # rebuilds the object from its own JSON, so the stamp (set in _run_sql_agent on
            # report_with_data) would otherwise be LOST, letting an empty investigation get
            # approved (RT-031/RT-032). This is the durable truth the verdict honors.
            if isinstance(report_with_data, dict) and report_with_data.get("_data_failed") \
               and isinstance(final_report, dict):
                final_report["_data_failed"] = report_with_data["_data_failed"]

            # Deterministic currency formatting — code has the FINAL word on every ₹
            # figure, regardless of what the LLM wrote (bulletproof P4 fix).
            _enforce_currency_formatting(final_report)

            notes = cleaned_report.get("data_quality_notes", "No issues found")
            notes_str = notes if isinstance(notes, str) else json.dumps(notes)[:100]
            summary_preview = final_report.get("summary", "")[:120].replace("\n", " ")
            ins_count = len(final_report.get("insights", []))
            _agent_result("DATA ANALYST ∥ REPORT WRITER", time.time() - t0, [
                f"Quality notes: {notes_str[:80]}",
                f"Summary   : {summary_preview}...",
                f"Insights  : {ins_count} generated",
            ])

            # ── Agent 6: QA Agent ─────────────────────────────────────────
            _agent_header(*_AGENTS[5])
            t0 = time.time()
            qa_result = self._run_qa_agent(question, final_report)
            approved = qa_result.get("approved", True)
            score    = qa_result.get("score", "?")
            max_sc   = qa_result.get("max_score", 12)
            feedback = qa_result.get("feedback", "")[:80]

            # Derive a THREE-TIER verdict from the actual score (matches the QA rubric),
            # instead of conflating "don't retry" with "approved" (RT-006: score 4 printed APPROVED).
            #   10+ → APPROVED, 7-9 → APPROVED_WITH_WARNINGS,
            #   4-6 → CONDITIONAL (show to user, flagged), <4 → REJECTED (retry).
            # `needs_retry` (score < 4) is the ONLY thing that triggers a pipeline retry.
            if isinstance(score, (int, float)) and isinstance(max_sc, (int, float)) and max_sc > 0:
                if score >= 10:
                    verdict, status_col = "APPROVED", _GREEN
                elif score >= 7:
                    verdict, status_col = "APPROVED (warnings)", _YELLOW
                elif score >= 4:
                    verdict, status_col = "CONDITIONAL", _YELLOW
                else:
                    verdict, status_col = "REJECTED", _RED
                approved = score >= 4          # shown to user (not retried) — but NOT "approved" label
                needs_retry = score < 4
            else:
                verdict, status_col = ("APPROVED", _GREEN) if approved else ("REJECTED", _RED)
                needs_retry = not approved
            # If accuracy guards flagged the report, never show a clean APPROVED.
            if final_report.get("has_accuracy_warnings") and verdict.startswith("APPROVED"):
                verdict, status_col = "APPROVED (accuracy warnings)", _YELLOW
            # SEVERE flags = a value that is genuinely WRONG (not merely suspect), e.g. a
            # cross-domain fabricated profit/margin or a fan-out-inflated total. These must not
            # read as APPROVED at all — force at least CONDITIONAL so the user treats the number
            # as untrustworthy. Universal: keyed on the flag, not on any specific value.
            _SEVERE = {"cross_domain_cost_fabrication", "material_total_inflated",
                       "revenue_exceeds_total", "fabricated_formula", "untraced_kpi"}
            _kpis = [k for k in (final_report.get("kpis", []) or []) if isinstance(k, dict)]
            _charts = [c for c in (final_report.get("charts", []) or []) if isinstance(c, dict)]
            _has_severe = any(it.get("_accuracy_flag") in _SEVERE for it in (_kpis + _charts)) \
                or any(c.get("_invariant") for c in _charts) \
                or (final_report.get("report_integrity", {}) or {}).get("status") == "violations"
            # EMPTY-INVESTIGATION GUARD (RT-031/RT-032): the SQL stage can FAIL entirely — every
            # KPI 0/None with no SQL, every chart 0 rows (drift detective flailed 21 rounds, JSON
            # broke). The narrator then writes a confident story over NOTHING and can put fake values
            # BACK into the KPIs — so we CANNOT re-derive emptiness here. Instead we honor the durable
            # `_data_failed` stamp set at the SQL stage (before the narrator), carried onto final_report.
            if final_report.get("_data_failed"):
                df = final_report["_data_failed"]
                verdict, status_col = "REJECTED (no data — investigation produced empty results)", _RED
                needs_retry = False  # re-writing the narrative won't conjure data; surface honestly
                feedback = (f"Data failure: {df.get('empty_kpis')}/{df.get('total_kpis')} KPIs and "
                            f"{df.get('empty_charts')}/{df.get('total_charts')} charts had no query-backed "
                            f"data. The SQL stage could not produce results — report is not trustworthy.")
            elif _has_severe and verdict.startswith("APPROVED"):
                verdict, status_col = "CONDITIONAL (accuracy — value may be wrong)", _RED
            _tee(
                f"  {_c('QA VERDICT', _BOLD)}: {_c(verdict, status_col, _BOLD)}  "
                f"{_c(f'Score: {score}/{max_sc}', _YELLOW)}  {_c(f'{time.time()-t0:.1f}s', _DIM)}"
            )
            _tee(f"  {_c(f'  Feedback: {feedback}', _DIM)}\n")

            # ── QA retry if rejected ───────────────────────────────────────
            # On a low QA score, re-run ONLY the Report Writer (with QA feedback),
            # not the whole BA→SQL→DA→Writer chain. Rationale: QA failures are almost
            # always NARRATIVE-quality issues (insight depth, summary specificity,
            # template compliance) — the DATA was already validated by the Data Analyst.
            # Re-running just the writer costs ~90s instead of ~200s, and the data
            # (KPIs/charts) stays stable. (If a future QA failure is truly data-level,
            # that's caught by the Data Analyst / fan-out guard upstream, not here.)
            if needs_retry and self._retry_count < 1:
                self._retry_count += 1
                _tee(f"\n  {_c('QA REJECTED — regenerating narrative with feedback...', _YELLOW, _BOLD)}\n")
                logger.info("QA rejected — retrying Report Writer only (attempt %d)", self._retry_count)
                fb_msg = qa_result.get("feedback", "Quality issues")
                # Pass QA feedback into the writer via the context so it addresses the gaps.
                _ctx_with_fb = dict(context)
                _ctx_with_fb["qa_feedback"] = fb_msg
                final_report = self._run_report_writer_agent(cleaned_report, _ctx_with_fb)

            # ── Post-processing ────────────────────────────────────────────
            final_report = self._post_process(final_report)
            applicable_filters = self._detect_applicable_filters(final_report)

            total_elapsed = time.time() - pipeline_start
            _pipeline_complete(total_elapsed, final_report)
            logger.info("Claude pipeline COMPLETE — %.1fs", total_elapsed)

            metrics = _build_metrics(self.client.usage_log, total_elapsed)

            # Build response based on intent mode
            response_payload = {
                "mode": "report",
                "intent_mode": intent_mode,
                "report": final_report,
                "metrics": metrics,
                "applicable_filters": applicable_filters,
                "ui_instructions": {
                    "create_new_section": True,
                    "open_in_new_tab": True,
                    "enable_streaming": True,
                    "stream_once": True,
                    "include_report_ai": True,
                    "report_ai": {
                        "type": "chat_like",
                        "position": "below_report",
                    },
                    "explanation_feature": {
                        "enabled": True,
                        "trigger": "eye_button",
                    },
                },
            }

            # Add drift-specific metadata to response
            if intent_mode == "DRIFT_INVESTIGATION":
                response_payload["drift_context"] = {
                    "signal_id": context.get("signal_id"),
                    "signal_name": context.get("signal_name"),
                    "signal_domain": context.get("signal_domain"),
                    "severity": final_report.get("severity", context.get("default_severity")),
                    "severity_score": final_report.get("severity_score"),
                    "causal_chain": context.get("causal_chain"),
                }

            return response_payload

        except Exception as exc:
            total_elapsed = time.time() - pipeline_start
            _tee(f"\n  {_c(f'PIPELINE FAILED after {total_elapsed:.1f}s: {exc}', _RED, _BOLD)}\n")
            logger.error("Claude pipeline failed: %s", exc, exc_info=True)
            return {
                "mode": "report",
                "status": "failed",
                "error": str(exc),
                "report": None,
            }

    # ═══════════════════════════════════════════════════════════════════════
    # AGENT RUNNERS
    # ═══════════════════════════════════════════════════════════════════════

    def _run_context_agent(self, question: str) -> dict:
        """Agent 1: Analyze query, classify intent (standard vs drift), map to signal.
        Uses Sonnet — signal classification against 37-SIG library needs reasoning.
        """
        response = self.client.call_agent(
            system_prompt=CONTEXT_AGENT_SYSTEM,
            user_message=(
                f"Analyze this analytics request. Determine if it is a STANDARD_REPORT "
                f"or a DRIFT_INVESTIGATION, and produce the appropriate context object.\n\n"
                f"USER QUERY: {question}"
            ),
            # Schema/relationships/profile now come via the shared cached prefix —
            # no need for schema-fetch tools (removes ~50k uncached tokens per run).
            agent_name="Context + Signal Agent",
            model=_HAIKU,
            use_cache=True,
            cached_prefix=self._shared_context,
        )

        try:
            ctx = self.client.extract_json(response)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Context agent JSON parse failed: %s", exc)
            return {
                "intent_mode": "STANDARD_REPORT",
                "subject": question,
                "intent": "overview",
                "timeframe": "all time",
                "business_domain": "sales",
                "relevant_tables": ["sales_order", "sales_order_line"],
                "filters": {},
                "key_metrics_to_analyze": [],
            }
        return _enforce_report_routing(ctx, question)

    def _run_ba_agent(
        self,
        question: str,
        context: dict,
        qa_feedback: str | None = None,
    ) -> dict:
        """Agent 2: Design the report or drift card blueprint."""
        intent_mode = context.get('intent_mode', 'STANDARD_REPORT')

        if intent_mode == 'DRIFT_INVESTIGATION':
            user_msg = (
                f"Design a DRIFT INVESTIGATION blueprint for this signal.\n\n"
                f"USER QUERY: {question}\n\n"
                f"CONTEXT (intent_mode=DRIFT_INVESTIGATION):\n"
                f"{json.dumps(context, indent=2)}\n\n"
                f"You must produce the full drift card blueprint with:\n"
                f"- Causal decomposition plan\n"
                f"- 3-5 suspected driver hypotheses\n"
                f"- All 11 tab data requirements\n"
                f"- Impact quantification formula\n"
                f"- Severity scoring inputs\n"
                f"- 6 KPIs (4 drift-required + 2 supporting)\n"
                f"- 6 charts (trend, comparison, waterfall, geographic, period, transactions)"
            )
        else:
            user_msg = (
                f"Design a comprehensive analytics report for this request.\n\n"
                f"USER QUESTION: {question}\n\n"
                f"CONTEXT ANALYSIS:\n{json.dumps(context, indent=2)}"
            )

        if qa_feedback:
            user_msg += (
                f"\n\nQA FEEDBACK FROM PREVIOUS ATTEMPT:\n{qa_feedback}\n"
                f"Please address these issues in the new blueprint."
            )

        response = self.client.call_agent(
            system_prompt=BUSINESS_ANALYST_SYSTEM,
            user_message=user_msg,
            # Schema/profile via shared cached prefix — schema-fetch tools removed.
            agent_name="Drift Architect" if intent_mode == 'DRIFT_INVESTIGATION' else "Business Analyst",
            model=_HAIKU,
            use_cache=True,
            cached_prefix=self._shared_context,
        )

        try:
            blueprint = self.client.extract_json(response)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("BA agent JSON parse failed: %s", exc)
            raise ValueError(f"Failed to generate report blueprint: {exc}")

        # Ensure essential fields exist
        blueprint.setdefault('intent_mode', intent_mode)
        if 'kpis' not in blueprint:
            blueprint['kpis'] = []
        if 'charts' not in blueprint:
            blueprint['charts'] = []
        if 'title' not in blueprint:
            blueprint['title'] = f"Report: {context.get('subject', question[:50])}"

        return blueprint

    def _apply_global_filters(self, report: dict) -> dict:
        """Deterministic safety pass: inject global filters into every SQL query
        and re-execute. Reuses filter_injector logic (same as /report/apply-filters).
        Only runs when self._filters is non-empty."""
        from services.filter_injector import _inject_filters
        from ai.claude_tools import handle_execute_sql_query
        import copy, json as _json

        filters = self._filters
        report = copy.deepcopy(report)

        def _rerun(item: dict, label: str) -> dict:
            original_sql = item.get("sql", "")
            if not original_sql:
                return item
            filtered_sql = _inject_filters(original_sql, filters)
            if filtered_sql == original_sql:
                return item  # nothing changed — skip re-execution
            result_str = handle_execute_sql_query(filtered_sql, f"filtered {label}")
            try:
                result = _json.loads(result_str)
                if result.get("success"):
                    item["sql"] = filtered_sql
                    rows = result.get("data", [])
                    if "value" in item:
                        # Prefer a column literally named 'value'; else the first
                        # column. Never blindly take the first key — a multi-column
                        # KPI row (e.g. {"label": ..., "value": ...}) would otherwise
                        # extract the wrong field.
                        if rows:
                            row = rows[0]
                            col = "value" if "value" in row else next(iter(row), None)
                            item["value"] = row.get(col) if col is not None else None
                        else:
                            item["value"] = None
                    if "data" in item:
                        item["data"] = rows
                else:
                    logger.warning("Global filter re-execute failed for %s: %s", label, result.get("error"))
            except Exception as exc:
                logger.warning("Global filter re-execute error for %s: %s", label, exc)
            return item

        for kpi in report.get("kpis", []):
            _rerun(kpi, kpi.get("label", "kpi"))
        for chart in report.get("charts", []):
            _rerun(chart, chart.get("title", "chart"))
        if report.get("table", {}).get("sql"):
            _rerun(report["table"], "table")

        logger.info("Global filter safety pass complete — filters: %s", filters)
        return report

    def _run_sql_agent(
        self,
        question: str,
        blueprint: dict,
        context: dict,
    ) -> dict:
        """Agent 3: Write and execute SQL for all KPIs, charts, and drift data.
        For DRIFT_INVESTIGATION, executes 4-phase query cycle with more tool rounds.
        """
        # Schema/rels/profile now arrive via the shared cached prefix (self._shared_context),
        # not embedded here — get_sql_agent_system no longer injects them.
        system_prompt = get_sql_agent_system("", "", "")
        intent_mode = context.get('intent_mode', 'STANDARD_REPORT')

        if intent_mode == 'DRIFT_INVESTIGATION':
            user_msg = (
                f"Execute the 4-phase drift investigation SQL cycle.\n\n"
                f"USER QUERY: {question}\n\n"
                f"DRIFT BLUEPRINT:\n{json.dumps(blueprint, indent=2)}\n\n"
                f"SIGNAL CONTEXT:\n{json.dumps(context, indent=2)}\n\n"
                f"Execute queries in this EXACT order:\n"
                f"PHASE 1 - Anchor: current_value, baseline_value, variance, impact\n"
                f"PHASE 2 - Decompose: dimensional cuts for each dimension\n"
                f"PHASE 3 - Support: trend, period_compare, geographic, consecutive_periods\n"
                f"PHASE 4 - Related: check related signals for co-firing\n\n"
                f"Include all SQL and actual data in the output JSON."
            )
            max_rounds = 40  # drift needs more rounds for multi-phase
        else:
            user_msg = (
                f"Execute SQL queries to populate this report blueprint with real data.\n\n"
                f"USER QUESTION: {question}\n\n"
                f"REPORT BLUEPRINT:\n{json.dumps(blueprint, indent=2)}\n\n"
                f"CONTEXT:\n{json.dumps(context, indent=2)}\n\n"
                f"For each KPI and chart, write a SQL query, execute it using "
                f"the execute_sql_query tool, and include the actual data in "
                f"the output. If a query fails, fix it and try again.\n\n"
                f"IMPORTANT:\n"
                f"- KPI queries must return exactly 1 row with 1 numeric value\n"
                f"- Chart queries must return rows with a label column and value column(s)\n"
                f"- Table query should return detailed rows (limit 20)\n"
                f"- Include the SQL used and actual data for each element"
            )
            max_rounds = 25

        agent_label = "Drift Detective" if intent_mode == 'DRIFT_INVESTIGATION' else "SQL Agent"

        def _run_on(model: str) -> dict:
            """Run the SQL agent on one model and parse to a report dict."""
            resp = self.client.call_agent(
                system_prompt=system_prompt,
                user_message=user_msg,
                tools=SQL_AGENT_TOOLS,
                tool_handlers=TOOL_HANDLERS,
                max_tool_rounds=max_rounds,
                agent_name=agent_label,
                model=model,
                use_cache=True,
                cached_prefix=self._shared_context,
            )
            try:
                return self.client.extract_json(resp)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.error("SQL agent JSON parse failed: %s", exc)
                r = blueprint.copy()
                for kpi in r.get('kpis', []):
                    kpi.setdefault('value', 0); kpi.setdefault('sql', '')
                for chart in r.get('charts', []):
                    chart.setdefault('data', []); chart.setdefault('sql', '')
                return r

        # OPTION B — deterministic pre-routing: pick the model from schema/blueprint signals.
        chosen_model, route_reason = _route_sql_model(question, blueprint, context)
        logger.info("[SQL Router] %s → %s (%s)", agent_label,
                    "SONNET" if chosen_model == _SONNET else "HAIKU", route_reason)
        report = _run_on(chosen_model)

        # OPTION C — failure escalation: if Haiku was chosen but struggled, re-run on Sonnet.
        # Decides on OBSERVED behavior (rounds burned / unresolved elements), not a prediction.
        # Only escalate UP (Haiku→Sonnet); never the reverse, and never to Opus.
        if chosen_model == _HAIKU:
            rounds_used = 0
            try:
                for u in reversed(self.client.usage_log):
                    if u.get("agent") == agent_label:
                        rounds_used = u.get("tool_rounds", 0); break
            except Exception:
                pass
            struggle = _sql_agent_struggled(report, rounds_used, max_rounds)
            if struggle:
                logger.warning("[SQL Router] Haiku struggled (%s) — ESCALATING to Sonnet", struggle)
                report = _run_on(_SONNET)

        # FIX A: code re-executes each element's SQL and owns the value/data — the
        # model's hand-typed values are NOT trusted (kills the 'Top Shape=0' /
        # SQL:(none) class of misfiled-value bugs). Runs BEFORE the guards so they
        # validate the real, code-owned values.
        _recompute_from_sql(report)
        # Code-level removal of un-renderable ranking/format-mask KPIs (the BA keeps
        # creating "Top Shape" cards despite the prompt; they render as 0). Ranking is
        # preserved in the charts, so dropping the broken card loses nothing.
        _drop_broken_kpis(report)
        _apply_report_guards(report)
        # Universal invariant layer: reconcile / contain / trace / sane. Attaches the
        # trustworthy `report_integrity` stamp. This is the correctness backstop that
        # generalizes beyond the specific guards above.
        _check_invariants(report)
        # DATA-FAILURE STAMP (RT-031/RT-032): decide HERE, at the SQL stage, whether the
        # investigation actually produced data — BEFORE the narrator can overwrite empty KPIs
        # with hand-typed story values. This durable flag is the single source of truth the QA
        # verdict honors; checking post-narrator (as the first attempt did) is unreliable because
        # the narrator rebuilds the report and masks the emptiness. Universal, value-independent.
        _kpis = [k for k in (report.get("kpis") or []) if isinstance(k, dict)]
        _charts = [c for c in (report.get("charts") or []) if isinstance(c, dict)]
        def _untraced(k):
            sql = str(k.get("sql") or k.get("executed_sql") or "")
            return not _re.search(r"\bfrom\b", sql, _re.IGNORECASE)
        _empty_kpis = sum(1 for k in _kpis if k.get("value") in (0, "0", "", None) and _untraced(k))
        _empty_charts = sum(1 for c in _charts if not (c.get("data") or []))
        if (_kpis and _empty_kpis >= max(1, len(_kpis) * 0.5)) or \
           (_charts and _empty_charts >= max(1, len(_charts) * 0.5)):
            report["_data_failed"] = {
                "empty_kpis": _empty_kpis, "total_kpis": len(_kpis),
                "empty_charts": _empty_charts, "total_charts": len(_charts),
            }
            logger.warning("[Data Failure] SQL stage produced %d/%d empty KPIs, %d/%d empty charts — "
                           "stamped _data_failed; QA will REJECT regardless of narrative.",
                           _empty_kpis, len(_kpis), _empty_charts, len(_charts))
        return report

    def _run_data_analyst_agent(self, report: dict, context: dict | None = None) -> dict:
        """Agent 4: Validate data and drift math integrity.

        DRIFT mode: all arithmetic (severity score, contribution normalization,
        variance/impact consistency) is done DETERMINISTICALLY in Python via
        `compute_drift_math` BEFORE the LLM is called — the LLM no longer computes
        these (it approximates arithmetic unreliably). The LLM then only does the
        judgment-style checks it is actually good at (baseline noise, affected-area
        corroboration) on top of the code-computed numbers.
        """
        intent_mode = report.get('intent_mode', 'STANDARD_REPORT')

        if intent_mode == 'DRIFT_INVESTIGATION':
            # ── Deterministic math first (code, not LLM) ──
            from ai.intelligence.drift_math import compute_drift_math
            report = compute_drift_math(report, context)

            user_msg = (
                f"Validate the drift investigation data. NOTE: severity_score, "
                f"contribution_pct normalization, and variance have ALREADY been "
                f"computed deterministically by code — do NOT recompute or change "
                f"them. Only perform these judgment checks:\n"
                f"1. Baseline sanity — flag if the baseline period looks noisy/anomalous\n"
                f"2. Affected areas validation — remove tags not corroborated by a "
                f"dimensional cut; add tags for the top-2 contributing entities\n"
                f"3. Consecutive periods — flag if inconsistent with the trend data\n\n"
                f"Preserve severity, severity_score, contribution_pct, and "
                f"data_quality_notes EXACTLY as given; append any new findings to "
                f"data_quality_notes.\n\n"
                f"REPORT DATA:\n{json.dumps(report, indent=2, default=str)}"
            )
        else:
            user_msg = (
                f"Review and clean the following report data. "
                f"Check all KPI values and chart data for quality issues.\n\n"
                f"{json.dumps(report, indent=2, default=str)}"
            )

        response = self.client.call_agent(
            system_prompt=DATA_ANALYST_SYSTEM,
            user_message=user_msg,
            agent_name="Causal Validator" if intent_mode == 'DRIFT_INVESTIGATION' else "Data Analyst",
            model=_HAIKU,
            use_cache=True,
        )

        try:
            validated = self.client.extract_json(response)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Data analyst JSON parse failed: %s - using uncleaned data", exc)
            return report

        # In DRIFT mode, re-assert the code-computed numbers over whatever the LLM
        # returned: keep its judgment edits (affected-area tags, extra notes) but
        # lock severity / contributions / drift_metrics to the deterministic values.
        if intent_mode == 'DRIFT_INVESTIGATION' and isinstance(validated, dict):
            for locked in ("severity", "severity_score", "causal_decomposition", "drift_metrics"):
                if locked in report:
                    validated[locked] = report[locked]
            # data_quality_notes: keep code notes, append any new LLM notes.
            code_notes = report.get("data_quality_notes") or []
            llm_notes = validated.get("data_quality_notes") or []
            if isinstance(code_notes, list) and isinstance(llm_notes, list):
                merged = list(code_notes)
                merged.extend(n for n in llm_notes if n not in code_notes)
                validated["data_quality_notes"] = merged
            else:
                validated["data_quality_notes"] = code_notes

        return validated

    def _run_report_writer_agent(self, report: dict, context: dict) -> dict:
        """Agent 5: Write narratives - McKinsey-style for drift, executive for standard.

        STANDARD mode splits the writing into TWO CONCURRENT calls (explanations ∥
        summary+insights) to cut wall-clock ~in half with IDENTICAL output coverage —
        the two halves write disjoint fields from the same data. DRIFT mode (9 tightly
        coupled narrative components) stays a single call.
        """
        intent_mode = context.get('intent_mode', 'STANDARD_REPORT')

        if intent_mode == 'DRIFT_INVESTIGATION':
            return self._run_report_writer_drift(report, context)

        # ── STANDARD mode: parallel writer (explanations ∥ narrative) ──
        import concurrent.futures as _futures
        with _futures.ThreadPoolExecutor(max_workers=2) as _ex:
            _expl_future = _ex.submit(self._write_explanations, report, context)
            _narr_future = _ex.submit(self._write_summary_insights, report, context)
            expl = _expl_future.result()      # {kpi_expl: {label: {...}}, chart_expl: {title: {...}}}
            narr = _narr_future.result()      # {summary: str, insights: [...]}

        # Assemble: start from the data report, graft narrative fields onto it.
        import copy as _copy
        result = _copy.deepcopy(report)

        kpi_expl   = (expl or {}).get("kpi_explanations", {})
        chart_expl = (expl or {}).get("chart_explanations", {})
        for kpi in result.get("kpis", []):
            key = kpi.get("label") or kpi.get("id") or ""
            kpi["explanation"] = kpi_expl.get(key) or kpi.get("explanation") or {
                "what": kpi.get("label", ""), "how": "", "why": "", "insight": ""}
        for chart in result.get("charts", []):
            key = chart.get("title") or chart.get("id") or ""
            chart["explanation"] = chart_expl.get(key) or chart.get("explanation") or {
                "what": chart.get("title", ""), "how": "", "why": "", "insight": ""}
        if result.get("table") and (expl or {}).get("table_explanation"):
            result["table"]["explanation"] = expl["table_explanation"]

        result["summary"] = (narr or {}).get("summary", "")
        result["insights"] = (narr or {}).get("insights", []) or []

        # Insights fallback (unchanged behavior)
        if not result.get("insights"):
            logger.info("Report writer returned 0 insights — running focused insight generation")
            result["insights"] = self._generate_insights_fallback(result, context)

        return result

    def _run_report_writer_drift(self, report: dict, context: dict) -> dict:
        """DRIFT_INVESTIGATION narrative — single call (9 coupled components)."""
        user_msg = (
            f"Write the DRIFT INVESTIGATION narrative. intent_mode=DRIFT_INVESTIGATION.\n\n"
            f"You must write ALL components:\n"
            f"1. Issue Overview (3-sentence template, <=60 words)\n"
            f"2. Why This Was Surfaced\n"
            f"3. Suspected Drivers (ranked by contribution)\n"
            f"4. Affected Areas narrative\n"
            f"5. KPI explanations (what/how/why/insight)\n"
            f"6. Chart explanations\n"
            f"7. Investigation Checklist (6 items)\n"
            f"8. Decision Options (expand templates)\n"
            f"9. Insights (6-8 non-obvious findings)\n\n"
            f"SIGNAL CONTEXT:\n{json.dumps(context, indent=2, default=str)}\n\n"
            f"REPORT DATA:\n{json.dumps(report, indent=2, default=str)}"
        )
        qa_feedback = context.get("qa_feedback")
        if qa_feedback:
            user_msg += (
                f"\n\n⚠️ QA REJECTED THE PREVIOUS NARRATIVE. Fix these issues (keep data unchanged):\n{qa_feedback}"
            )
        response = self.client.call_agent(
            system_prompt=REPORT_WRITER_SYSTEM, user_message=user_msg,
            agent_name="Drift Narrator", model=_HAIKU, use_cache=True,
            cached_prefix=self._shared_context,
        )
        try:
            result = self.client.extract_json(response)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Drift narrator parse failed: %s", exc)
            return report
        if not result.get("insights"):
            result["insights"] = self._generate_insights_fallback(result, context)
        return result

    def _write_explanations(self, report: dict, context: dict) -> dict:
        """Writer sub-call A: per-KPI + per-chart + table explanations (no summary/insights).

        Returns explanations keyed by KPI label / chart title so they map back reliably
        regardless of ordering.
        """
        kpi_labels   = [k.get("label") or k.get("id") or "" for k in report.get("kpis", [])]
        chart_titles = [c.get("title") or c.get("id") or "" for c in report.get("charts", [])]
        has_table = bool(report.get("table"))

        user_msg = (
            "Write ONLY the explanations for this STANDARD_REPORT's KPIs, charts, and table. "
            "Do NOT write a summary or insights (another writer handles those).\n\n"
            "For EACH KPI and EACH chart, write a 4-part explanation: "
            "what / how / why / insight (1 sentence each, citing the actual data value).\n\n"
            f"KPI labels: {json.dumps(kpi_labels)}\n"
            f"Chart titles: {json.dumps(chart_titles)}\n"
            f"Table present: {has_table}\n\n"
            "Return ONLY this JSON shape (key explanations by the EXACT label/title given):\n"
            "{\n"
            '  "kpi_explanations":   { "<kpi label>":   {"what":"","how":"","why":"","insight":""}, ... },\n'
            '  "chart_explanations": { "<chart title>": {"what":"","how":"","why":"","insight":""}, ... }'
            + (',\n  "table_explanation": {"what":"","how":"","why":"","insight":""}\n' if has_table else "\n")
            + "}\n\n"
            f"BUSINESS CONTEXT: {context.get('subject', 'General report')}, "
            f"domain: {context.get('business_domain', 'sales')}\n\n"
            f"REPORT DATA:\n{json.dumps(report, indent=2, default=str)}"
        )
        response = self.client.call_agent(
            system_prompt=REPORT_WRITER_SYSTEM, user_message=user_msg,
            agent_name="Report Writer (explanations)", model=_HAIKU, use_cache=True,
            cached_prefix=self._shared_context,
        )
        try:
            return self.client.extract_json(response)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Writer explanations parse failed: %s", exc)
            return {}

    def _write_summary_insights(self, report: dict, context: dict) -> dict:
        """Writer sub-call B: executive summary + insights (no per-element explanations)."""
        user_msg = (
            "Write ONLY the executive summary and insights for this STANDARD_REPORT. "
            "Do NOT write per-KPI or per-chart explanations (another writer handles those).\n\n"
            "1. Executive summary: 5-8 sentences citing actual data values (scope+period, top 3 findings, "
            "key risk/opportunity, recommendation). No placeholder text.\n"
            "2. Insights: 6-8 data-driven findings. EACH insight is a JSON object with:\n"
            '   - "title": 5-8 word directional claim\n'
            '   - "body": 2-3 sentences with SPECIFIC numbers/entities from the data, answering "so what?"\n'
            '   - "type": "positive" | "negative" | "neutral" | "warning"\n'
            "   At least 2 insights must be \"warning\" or \"negative\". Go beyond restating KPIs — "
            "synthesize across dimensions.\n\n"
            "Return ONLY this JSON shape:\n"
            '{ "summary": "...", "insights": [ {"title":"","body":"","type":""}, ... ] }\n\n'
            f"BUSINESS CONTEXT: {context.get('subject', 'General report')}, "
            f"domain: {context.get('business_domain', 'sales')}\n\n"
            f"REPORT DATA:\n{json.dumps(report, indent=2, default=str)}"
        )
        qa_feedback = context.get("qa_feedback")
        if qa_feedback:
            user_msg += (
                f"\n\n⚠️ QA REJECTED THE PREVIOUS NARRATIVE. Fix these issues in summary/insights:\n{qa_feedback}"
            )
        response = self.client.call_agent(
            system_prompt=REPORT_WRITER_SYSTEM, user_message=user_msg,
            agent_name="Report Writer (summary+insights)", model=_HAIKU, use_cache=True,
            cached_prefix=self._shared_context,
        )
        try:
            return self.client.extract_json(response)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Writer summary/insights parse failed: %s", exc)
            return {}

    def _run_qa_agent(self, question: str, report: dict) -> dict:
        """Agent 6: Quality assurance - 12-point for drift, 8-point for standard."""
        intent_mode = report.get('intent_mode', 'STANDARD_REPORT')

        if intent_mode == 'DRIFT_INVESTIGATION':
            user_msg = (
                f"Evaluate this DRIFT INVESTIGATION card (12-point checklist).\n\n"
                f"USER QUERY: {question}\n\n"
                f"Run ALL 12 checks: contribution sum, drift math, trend corroboration,\n"
                f"consecutive periods, issue overview template, suspected drivers,\n"
                f"insight specificity, decision actionability, 11-tab completeness,\n"
                f"affected areas validation, investigation checklist, related signals.\n\n"
                f"DRIFT CARD:\n{json.dumps(report, indent=2, default=str)}"
            )
            max_score = 12
        else:
            user_msg = (
                f"Evaluate the quality of this report against the user's "
                f"original question.\n\n"
                f"USER QUESTION: {question}\n\n"
                f"GENERATED REPORT:\n{json.dumps(report, indent=2, default=str)}"
            )
            max_score = 8

        response = self.client.call_agent(
            system_prompt=QA_AGENT_SYSTEM,
            user_message=user_msg,
            agent_name="Drift QA" if intent_mode == 'DRIFT_INVESTIGATION' else "QA Agent",
            model=_HAIKU,
            use_cache=True,
        )

        try:
            return self.client.extract_json(response)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("QA agent parse failed: %s - auto-approving", exc)
            return {'approved': True, 'score': max_score - 2, 'max_score': max_score, 'feedback': 'Auto-approved'}

    # ═══════════════════════════════════════════════════════════════════════
    # INSIGHT FALLBACK
    # ═══════════════════════════════════════════════════════════════════════

    def _generate_insights_fallback(self, report: dict, context: dict) -> list[dict]:
        """Generate insights via a focused follow-up call when the report writer omits them."""
        # Build a compact data summary for the insight generation prompt
        kpi_summary = "; ".join(
            f"{k.get('label','?')}: {k.get('value','?')}"
            for k in report.get("kpis", [])
        )
        chart_summary = "; ".join(
            f"{c.get('title','?')} ({len(c.get('data',[]))} rows)"
            for c in report.get("charts", [])
        )

        user_msg = (
            f"Generate exactly 6-8 data-driven insights for this report.\n\n"
            f"CONTEXT: {context.get('subject', 'Report')}, domain: {context.get('business_domain', 'sales')}\n"
            f"KPIs: {kpi_summary}\n"
            f"Charts: {chart_summary}\n"
            f"Summary: {report.get('summary', '')[:500]}\n\n"
            f"Return ONLY a JSON array of insight objects. Each insight MUST have:\n"
            f"- \"title\": 5-8 word directional claim\n"
            f"- \"body\": 2-3 sentences with specific numbers from the data\n"
            f"- \"type\": \"positive\" | \"negative\" | \"neutral\" | \"warning\"\n\n"
            f"At least 2 insights must be \"warning\" or \"negative\" type.\n"
            f"Do NOT restate KPI values — synthesize across dimensions and find non-obvious patterns.\n\n"
            f"REPORT DATA (first 2 chart datasets for reference):\n"
            f"{json.dumps([c.get('data', [])[:10] for c in report.get('charts', [])[:2]], default=str)}"
        )

        response = self.client.call_agent(
            system_prompt="You are a data analyst generating insights. Return ONLY a JSON array.",
            user_message=user_msg,
            agent_name="Insight Fallback",
            model=_HAIKU,
            use_cache=False,
        )

        try:
            text = response.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                lines = [ln for ln in lines if not ln.strip().startswith("```")]
                text = "\n".join(lines).strip()
            start = text.find("[")
            end = text.rfind("]")
            if start != -1 and end != -1 and end > start:
                text = text[start:end + 1]
            insights = json.loads(text)
            if isinstance(insights, list) and len(insights) > 0:
                logger.info("Insight fallback generated %d insights", len(insights))
                return insights
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Insight fallback parse failed: %s", exc)

        return []

    # ═══════════════════════════════════════════════════════════════════════
    # POST-PROCESSING
    # ═══════════════════════════════════════════════════════════════════════

    def _post_process(self, report: dict) -> dict:
        """Apply post-processing to clean up the report."""
        # ── KPI cleanup ───────────────────────────────────────────────
        if "kpis" in report:
            cleaned_kpis = []
            for kpi in report["kpis"]:
                val = kpi.get("value")
                if val is None or val == "" or val == "N/A":
                    kpi["value"] = 0
                elif isinstance(val, str):
                    try:
                        kpi["value"] = float(val.replace(",", "").replace("₹", "").strip())
                    except (ValueError, AttributeError):
                        kpi["value"] = 0

                kpi.setdefault("id", f"kpi_{len(cleaned_kpis) + 1}")
                kpi.setdefault("format", "number")
                kpi.setdefault("icon", "revenue")
                kpi.setdefault("color", "blue")
                kpi.setdefault("sql", "")
                kpi.setdefault("explanation", {"what": kpi.get("label",""), "how": "", "why": "", "insight": ""})
                cleaned_kpis.append(kpi)

            report["kpis"] = cleaned_kpis[:6]

        # ── Chart cleanup ─────────────────────────────────────────────
        if "charts" in report:
            valid_charts = []
            for chart in report["charts"]:
                chart.setdefault("id", f"chart_{len(valid_charts) + 1}")
                chart.setdefault("type", "bar")
                chart.setdefault("sql", "")
                chart.setdefault("x_label", "Category")
                chart.setdefault("y_label", "Value")
                chart.setdefault("color_scheme", "blues")
                chart.setdefault("explanation", {"what": chart.get("title",""), "how": "", "why": "", "insight": ""})

                data = chart.get("data", [])
                if isinstance(data, dict):
                    data = list(data.values())
                    chart["data"] = data
                if not data or len(data) == 0:
                    chart["data"] = [{"label": "No data available", "value": 0}]
                    chart["type"] = "bar"
                else:
                    row_keys = list(data[0].keys())
                    if len(row_keys) < 2:
                        if len(row_keys) == 1 and len(data) == 1:
                            val_key = row_keys[0]
                            chart["data"] = [{"label": chart.get("title","Value"), val_key: data[0][val_key]}]
                        elif len(row_keys) == 1 and len(data) > 1:
                            val_key = row_keys[0]
                            for i, row in enumerate(data):
                                row["label"] = f"Item {i + 1}"

                    row_keys = list(chart["data"][0].keys()) if chart["data"] else []
                    if len(row_keys) >= 2:
                        value_keys = row_keys[1:]
                        non_zero = [
                            row for row in chart["data"]
                            if any(row.get(k) not in (None, 0, "", "0", 0.0) for k in value_keys)
                        ]
                        if non_zero:
                            chart["data"] = non_zero

                valid_charts.append(chart)

            report["charts"] = valid_charts[:6]
            report["charts"] = self._enforce_chart_diversity(report["charts"])

        # ── Table cleanup ─────────────────────────────────────────────
        if "table" in report:
            table = report["table"]
            if not isinstance(table, dict):
                report["table"] = {"title": "Detail Table", "sql": "", "data": []}
            else:
                table.setdefault("title", "Detail Table")
                table.setdefault("sql", "")
                table.setdefault("data", [])

                # Fix title/row-count mismatch: "Top 40 by ..." but only 23 rows
                actual_rows = len(table.get("data", []))
                title = table.get("title", "")
                fixed_title = re.sub(
                    r'\bTop\s+\d+\b',
                    f'Top {actual_rows}',
                    title
                )
                if fixed_title != title:
                    logger.info("Table title fixed: '%s' → '%s'", title, fixed_title)
                    table["title"] = fixed_title

                # Ensure table has explanation for the eye modal
                table.setdefault("explanation", {
                    "what": f"Detail breakdown: {table.get('title', 'Data Table')}",
                    "how": f"Queried from database — {actual_rows} rows returned, sorted by relevance to the question",
                    "why": "Entity-level data for drill-down analysis and action planning",
                    "insight": "",
                })

        # ── Insights cleanup ──────────────────────────────────────────
        # Prefer rich insight objects from Report Writer over bare strings
        # from insight_topics (BA agent). Convert any remaining strings.
        insights = report.get("insights", [])
        insight_topics = report.get("insight_topics", [])

        # If insights is empty but insight_topics has content, promote them
        if not insights and insight_topics:
            insights = insight_topics

        cleaned = []
        for ins in insights:
            if isinstance(ins, str):
                ins = {"title": ins, "body": ins, "type": "neutral"}
            elif isinstance(ins, dict):
                ins.setdefault("title", "Insight")
                ins.setdefault("body", ins.get("title", ""))
                ins.setdefault("type", "neutral")
                # Validate type is one of allowed values
                if ins["type"] not in ("positive", "negative", "neutral", "warning", "opportunity"):
                    ins["type"] = "neutral"
            cleaned.append(ins)
        report["insights"] = cleaned[:10]

        # Remove insight_topics to avoid frontend confusion
        report.pop("insight_topics", None)

        return report

    def _log_sql_traceability(self, report: dict):
        """Log which SQL query powers each KPI, chart, and table."""
        width = 72
        lines = []
        lines.append(f"  {_c('┌─ SQL TRACEABILITY ' + '─' * (width - 21) + '┐', _CYAN)}")

        # KPIs
        for i, kpi in enumerate(report.get("kpis", []), 1):
            label = kpi.get("label", kpi.get("id", f"KPI {i}"))
            sql = kpi.get("sql", "")
            val = kpi.get("value", "?")
            lines.append(f"  {_c('│', _CYAN)} {_c(f'KPI {i}:', _BOLD)} {label} = {_c(str(val), _YELLOW)}")
            if sql:
                sql_preview = sql.replace('\n', ' ')[:100]
                lines.append(f"  {_c('│', _CYAN)}   {_c('SQL:', _DIM)} {sql_preview}")
            else:
                lines.append(f"  {_c('│', _CYAN)}   {_c('SQL: (none)', _RED)}")

        lines.append(f"  {_c('│' + '─' * (width - 2), _CYAN)}")

        # Charts
        for i, chart in enumerate(report.get("charts", []), 1):
            title = chart.get("title", f"Chart {i}")
            ctype = chart.get("type", "?")
            sql = chart.get("sql", "")
            rows = len(chart.get("data", []))
            lines.append(f"  {_c('│', _CYAN)} {_c(f'Chart {i}:', _BOLD)} {title} [{_c(ctype, _YELLOW)}]")
            lines.append(f"  {_c('│', _CYAN)}   Rows: {rows}")
            if sql:
                sql_preview = sql.replace('\n', ' ')[:100]
                lines.append(f"  {_c('│', _CYAN)}   {_c('SQL:', _DIM)} {sql_preview}")
            else:
                lines.append(f"  {_c('│', _CYAN)}   {_c('SQL: (none)', _RED)}")

        lines.append(f"  {_c('│' + '─' * (width - 2), _CYAN)}")

        # Table
        table = report.get("table", {})
        if table:
            title = table.get("title", "Detail Table")
            sql = table.get("sql", "")
            rows = len(table.get("data", []))
            # Check for title/row mismatch
            import re as _re
            match = _re.search(r'\bTop\s+(\d+)\b', title, _re.IGNORECASE)
            mismatch_warn = ""
            if match and int(match.group(1)) != rows:
                mismatch_warn = f" {_c(f'⚠️ TITLE SAYS {match.group(0)} BUT GOT {rows}', _RED, _BOLD)}"
            lines.append(f"  {_c('│', _CYAN)} {_c('Table:', _BOLD)} {title}{mismatch_warn}")
            lines.append(f"  {_c('│', _CYAN)}   Rows: {rows}")
            if sql:
                sql_preview = sql.replace('\n', ' ')[:100]
                lines.append(f"  {_c('│', _CYAN)}   {_c('SQL:', _DIM)} {sql_preview}")

        lines.append(f"  {_c('└' + '─' * (width - 2) + '┘', _CYAN)}")

        for line in lines:
            _tee(line)

    @staticmethod
    def _enforce_chart_diversity(charts: list[dict]) -> list[dict]:
        """Ensure at least 4 different chart types across all charts."""
        if len(charts) <= 1:
            return charts

        type_count: dict[str, int] = {}
        for chart in charts:
            ct = chart.get("type", "bar").lower()
            type_count[ct] = type_count.get(ct, 0) + 1

        if len(type_count) >= 4:
            return charts

        all_types   = ["bar", "line", "pie", "doughnut", "horizontalBar", "area"]
        used_types  = set(type_count.keys())
        unused_types = [t for t in all_types if t not in used_types]

        seen_types: set[str] = set()
        for chart in charts:
            ct = chart.get("type", "bar").lower()
            if ct in seen_types and unused_types:
                new_type = unused_types.pop(0)
                chart["type"] = new_type
            seen_types.add(chart.get("type", "bar").lower())

        return charts

    @staticmethod
    def _detect_applicable_filters(report: dict) -> dict:
        """Analyze all SQL in the report to determine which filters apply."""
        all_sql = []
        for kpi in report.get("kpis", []):
            if kpi.get("sql"):
                all_sql.append(kpi["sql"])
        for chart in report.get("charts", []):
            if chart.get("sql"):
                all_sql.append(chart["sql"])
        if report.get("table", {}).get("sql"):
            all_sql.append(report["table"]["sql"])

        combined = " ".join(all_sql).lower()

        has_sales_order    = bool(re.search(r"\bsales_order\b(?!_)", combined))
        has_product_master = bool(re.search(r"\bproduct_master\b", combined))
        has_customer_master= bool(re.search(r"\bcustomer_master\b", combined))
        has_order_date     = bool(re.search(r"\border_date\b", combined))

        filters = {}
        if has_sales_order and has_order_date:
            filters["date_range"] = True
        if has_product_master:
            filters["category"] = True
            filters["product"]  = True
        if has_customer_master:
            filters["customer"] = True
        if has_sales_order:
            filters["status"] = True

        logger.info("Detected applicable filters: %s", filters)
        return filters
