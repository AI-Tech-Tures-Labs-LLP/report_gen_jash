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
_SONNET = config.CLAUDE_MODEL        # Available for complex tasks if needed
_HAIKU  = config.CLAUDE_HAIKU_MODEL  # All agents — fast & cost-effective


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
    _COMPANY_TOTAL_REVENUE = 11_300_000_000  # ~₹11.3B all-time closed; revenue can't exceed this
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
        # 3b) Revenue/value exceeding total company revenue (RT-007 fan-out: 2.7× inflation)
        if isinstance(val, (int, float)) and val > _COMPANY_TOTAL_REVENUE and any(
            w in name for w in ("revenue", "sales", "value", "amount")
        ) and "percent" not in str(kpi.get("format", "")).lower():
            kpi["_accuracy_flag"] = "revenue_exceeds_total"
            warnings.append(
                f"KPI '{kpi.get('name') or kpi.get('label')}' = {val:,.0f} EXCEEDS total company "
                f"revenue (~₹11.3B) — almost certainly FAN-OUT double-counting (a SUM across a "
                f"diamond/gold child join repeats line revenue per child row). Value is INFLATED."
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

    if warnings:
        existing = report.get("accuracy_warnings") or []
        report["accuracy_warnings"] = existing + warnings
        # Surface a top-level flag so the UI/QA can SEE there are accuracy concerns
        # (RT-008: warnings were only logged, not surfaced).
        report["has_accuracy_warnings"] = True
        logger.warning("[Report Guards] %d accuracy warning(s): %s", len(warnings), warnings)


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


class ClaudeReportPipeline:
    """Multi-agent report generation using Claude API."""

    def __init__(self):
        self.client = ClaudeClient()
        self._retry_count = 0
        self._shared_context: str | None = None  # set per-run in generate()

    # ═══════════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════════════════════

    def generate(self, question: str, force_refresh: bool = False) -> dict[str, Any]:
        """Generate a complete report using the 6-agent pipeline.

        Supports two modes:
        - STANDARD_REPORT: Traditional KPI + chart dashboard
        - DRIFT_INVESTIGATION: Full drift card with causal decomposition
        """
        global _AGENTS
        pipeline_start = time.time()
        _pipeline_banner(question)
        logger.info("Claude pipeline START — question: %s", question[:120])

        self._retry_count = 0
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
                "error": f"Report generation failed: {str(exc)}",
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
            return self.client.extract_json(response)
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

        response = self.client.call_agent(
            system_prompt=system_prompt,
            user_message=user_msg,
            tools=SQL_AGENT_TOOLS,
            tool_handlers=TOOL_HANDLERS,
            max_tool_rounds=max_rounds,
            agent_name="Drift Detective" if intent_mode == 'DRIFT_INVESTIGATION' else "SQL Agent",
            model=_HAIKU,
            use_cache=True,
            cached_prefix=self._shared_context,
        )

        try:
            report = self.client.extract_json(response)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("SQL agent JSON parse failed: %s", exc)
            report = blueprint.copy()
            for kpi in report.get('kpis', []):
                kpi.setdefault('value', 0)
                kpi.setdefault('sql', '')
            for chart in report.get('charts', []):
                chart.setdefault('data', [])
                chart.setdefault('sql', '')

        _apply_report_guards(report)
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
