"""Deterministic drift-card math.

This module is the SINGLE SOURCE OF TRUTH for every numeric computation on a
DRIFT_INVESTIGATION report: severity scoring, contribution-percentage
normalization, and impact auditing. Previously these were described as formulas
inside the Data Analyst / QA agent prompts and "computed" by the LLM — which is
unreliable (LLMs approximate arithmetic, especially LOG10 / weighted sums, and
produce different results across runs).

The pipeline now calls `compute_drift_math(report)` after the SQL Agent has
fetched the raw numbers (Agent 3) and BEFORE the narrator writes prose (Agent 5).
The LLM no longer does any of this arithmetic; it only reads/narrates the result.

All functions are pure (dict in → dict out), defensive against missing/malformed
fields (the input dict is LLM-produced), and never raise on bad data — a bad
field degrades to a safe default and is recorded in `data_quality_notes`.

Formula reference (was DATA_ANALYST_SYSTEM "Check 6"):
    severity_score = impact_normalized          * 0.40
                   + consecutive_periods_factor  * 0.25
                   + concentration_index         * 0.20
                   + related_signals_factor      * 0.10
                   + is_top_scope                * 0.05
where:
    impact_normalized         = min(1.0, log10(max(1, impact_inr)) / 7)
    consecutive_periods_factor= min(1.0, consecutive_periods * 0.10)
    concentration_index       = clamp(value from SQL agent, 0..1)
    related_signals_factor    = min(1.0, firing_related_signals * 0.25)
    is_top_scope              = 1.0 if scope is a top entity else 0.0
classification:
    >=0.75 CRITICAL | 0.50-0.74 HIGH | 0.25-0.49 MEDIUM | <0.25 LOW
"""

from __future__ import annotations

import math
from typing import Any

# ── Severity weights (single source of truth) ────────────────────────────────
_W_IMPACT = 0.40
_W_CONSECUTIVE = 0.25
_W_CONCENTRATION = 0.20
_W_RELATED = 0.10
_W_TOP_SCOPE = 0.05

# Impact normalization: log10 scaled, saturates at ₹10Cr (1e7 → log10 = 7).
_IMPACT_LOG_DIVISOR = 7.0

# Contribution-sum tolerance band (percent). Outside this we rescale to 100.
_CONTRIB_SUM_LOW = 80.0
_CONTRIB_SUM_HIGH = 120.0
_MONOPOLY_PCT = 90.0


def _to_float(value: Any, default: float = 0.0) -> float:
    """Coerce an LLM-produced value to float, never raising."""
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        # strip ₹, %, commas, whitespace that an LLM may have left in
        cleaned = str(value).replace(",", "").replace("₹", "").replace("%", "").strip()
        return float(cleaned)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def classify_severity(score: float) -> str:
    """Map a 0–1 severity score to a label. Single source of truth."""
    if score >= 0.75:
        return "CRITICAL"
    if score >= 0.50:
        return "HIGH"
    if score >= 0.25:
        return "MEDIUM"
    return "LOW"


def compute_severity_score(
    impact_inr: float,
    consecutive_periods: float,
    concentration_index: float,
    related_signals_firing: int,
    is_top_scope: bool,
) -> float:
    """Deterministic weighted severity score in [0, 1]."""
    impact_normalized = min(1.0, math.log10(max(1.0, impact_inr)) / _IMPACT_LOG_DIVISOR)
    consecutive_factor = min(1.0, max(0.0, consecutive_periods) * 0.10)
    concentration = _clamp(concentration_index, 0.0, 1.0)
    related_factor = min(1.0, max(0, related_signals_firing) * 0.25)
    top_scope = 1.0 if is_top_scope else 0.0

    score = (
        impact_normalized * _W_IMPACT
        + consecutive_factor * _W_CONSECUTIVE
        + concentration * _W_CONCENTRATION
        + related_factor * _W_RELATED
        + top_scope * _W_TOP_SCOPE
    )
    return round(_clamp(score, 0.0, 1.0), 4)


def _count_firing_related_signals(report: dict) -> int:
    """Count related signals the SQL agent reported as currently firing."""
    related = report.get("related_signals")
    if not isinstance(related, list):
        return 0
    count = 0
    for sig in related:
        if isinstance(sig, dict) and sig.get("is_firing") is True:
            count += 1
    return count


def normalize_contributions(report: dict, notes: list[str]) -> None:
    """Rescale each dimension's contribution_pct so the dimension sums to 100%.

    Mutates `report['causal_decomposition'][i]['data']` in place. Records what it
    did (sum before, whether it rescaled, monopoly flags) into `notes`.
    """
    decomposition = report.get("causal_decomposition")
    if not isinstance(decomposition, list):
        return

    for dim in decomposition:
        if not isinstance(dim, dict):
            continue
        rows = dim.get("data")
        if not isinstance(rows, list) or not rows:
            continue
        dim_name = dim.get("dimension", "?")

        # If contribution_pct is absent but delta is present, derive it from deltas.
        have_contrib = any(
            isinstance(r, dict) and r.get("contribution_pct") is not None for r in rows
        )
        if not have_contrib:
            total_delta = sum(abs(_to_float(r.get("delta"))) for r in rows if isinstance(r, dict))
            if total_delta > 0:
                for r in rows:
                    if isinstance(r, dict):
                        r["contribution_pct"] = round(
                            abs(_to_float(r.get("delta"))) / total_delta * 100, 2
                        )
                notes.append(f"[{dim_name}] contribution_pct derived from deltas (was missing).")
            else:
                notes.append(f"[{dim_name}] no contribution_pct and no usable deltas - left as-is.")
                continue

        current_sum = sum(_to_float(r.get("contribution_pct")) for r in rows if isinstance(r, dict))

        # Monopoly flag (informational — do not reject).
        for r in rows:
            if isinstance(r, dict) and _to_float(r.get("contribution_pct")) > _MONOPOLY_PCT:
                notes.append(
                    f"[{dim_name}] single entity "
                    f"'{r.get('entity_name', '?')}' contributes "
                    f"{_to_float(r.get('contribution_pct')):.1f}% (>90%) - verify legitimacy."
                )

        if current_sum <= 0:
            notes.append(f"[{dim_name}] contribution sum is {current_sum:.1f}% - cannot rescale.")
            continue

        if not (_CONTRIB_SUM_LOW <= current_sum <= _CONTRIB_SUM_HIGH):
            for r in rows:
                if isinstance(r, dict):
                    r["contribution_pct"] = round(
                        _to_float(r.get("contribution_pct")) / current_sum * 100, 2
                    )
            notes.append(
                f"[{dim_name}] contributions summed to {current_sum:.1f}% "
                f"(outside 80-120%) - rescaled to 100%."
            )
        else:
            notes.append(f"[{dim_name}] contributions summed to {current_sum:.1f}% - within tolerance.")


def compute_drift_math(report: dict, context: dict | None = None) -> dict:
    """Apply all deterministic drift math to a DRIFT_INVESTIGATION report.

    Returns the SAME report dict, mutated in place, with:
      - normalized contribution_pct per dimension (sums to 100%)
      - a code-computed `severity_score` (float, 0–1)
      - `severity` label consistent with the score
      - `data_quality_notes` (list[str]) appended with everything the code did

    Safe on partial input: missing fields degrade to defaults and are noted.
    The LLM does NONE of this arithmetic anymore — it only narrates the result.
    """
    context = context or {}
    notes: list[str] = []
    existing_notes = report.get("data_quality_notes")
    if isinstance(existing_notes, list):
        notes = [str(n) for n in existing_notes]
    elif isinstance(existing_notes, str) and existing_notes.strip():
        notes = [existing_notes.strip()]

    # 1. Contribution normalization (mutates causal_decomposition rows).
    normalize_contributions(report, notes)

    # 2. Severity score from drift_metrics + related signals + scope.
    metrics = report.get("drift_metrics")
    if isinstance(metrics, dict):
        impact_inr = _to_float(metrics.get("impact_inr"))
        consecutive = _to_float(metrics.get("consecutive_periods"))
        concentration = _to_float(metrics.get("concentration_index"))
        related_firing = _count_firing_related_signals(report)
        is_top_scope = bool(
            context.get("is_top_scope")
            or report.get("is_top_scope")
            or (context.get("scope_type") in ("CUSTOMER", "HUNTER", "TERRITORY")
                and bool(context.get("scope_reference")))
        )

        score = compute_severity_score(
            impact_inr=impact_inr,
            consecutive_periods=consecutive,
            concentration_index=concentration,
            related_signals_firing=related_firing,
            is_top_scope=is_top_scope,
        )
        label = classify_severity(score)

        prior_label = report.get("severity")
        report["severity_score"] = score
        report["severity"] = label
        if prior_label and prior_label != label:
            notes.append(
                f"Severity recomputed by code: {score:.2f} -> {label} "
                f"(was '{prior_label}' from estimate)."
            )
        else:
            notes.append(f"Severity computed by code: {score:.2f} -> {label}.")

        # 3. Variance consistency check (informational).
        current = _to_float(metrics.get("current_value"))
        baseline = _to_float(metrics.get("baseline_value"))
        reported_var = metrics.get("variance_absolute")
        if reported_var is not None:
            computed_var = round(current - baseline, 4)
            if abs(computed_var - _to_float(reported_var)) > 0.01:
                metrics["variance_absolute"] = computed_var
                notes.append(
                    f"variance_absolute corrected by code: {reported_var} -> {computed_var} "
                    f"(current {current} - baseline {baseline})."
                )
    else:
        notes.append("No drift_metrics object found - severity not computed.")

    report["data_quality_notes"] = notes
    return report
