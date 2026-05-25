"""Drift Detection Engine for data and metric drift analysis.

Detects multiple types of drift:
- Data drift: Changes in data volume or structure
- Metric drift: Changes in key metric values
- Trend drift: Changes in trend patterns
- Distribution drift: Changes in data distribution
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timedelta
import numpy as np
import logging

logger = logging.getLogger(__name__)


class DriftType(Enum):
    """Types of drift that can be detected"""
    SCHEMA_DRIFT = "schema_drift"
    DATA_DRIFT = "data_drift"
    METRIC_DRIFT = "metric_drift"
    TREND_DRIFT = "trend_drift"
    DISTRIBUTION_DRIFT = "distribution_drift"
    ANOMALY_DRIFT = "anomaly_drift"


@dataclass
class DriftResult:
    """A detected drift with full explainability"""
    drift_type: DriftType
    detected: bool
    confidence: float
    
    # Comparison data
    current_period: Dict[str, Any]
    baseline_period: Dict[str, Any]
    
    # Statistical results
    statistical_test: str
    p_value: float
    effect_size: float
    
    # Drift details
    drift_description: str
    affected_columns: List[str]
    sample_before: Optional[List[Dict]]
    sample_after: Optional[List[Dict]]
    
    # Explainability
    what_changed: str
    why_it_changed: str
    severity: str  # 'critical', 'high', 'medium', 'low'
    impact: str
    recommended_action: str
    
    timestamp: datetime


class DriftDetectionEngine:
    """Multi-dimensional drift detection for data and metrics
    
    Usage:
        engine = DriftDetectionEngine()
        
        drifts = engine.analyze(
            current_data=[...],
            baseline_data=[...],
            context={'metric_name': 'revenue'}
        )
        
        for drift in drifts:
            if drift.detected:
                print(f"{drift.drift_type.value}: {drift.what_changed}")
                print(f"  Impact: {drift.impact}")
                print(f"  Action: {drift.recommended_action}")
    """
    
    def __init__(self):
        self.detectors = {
            DriftType.DATA_DRIFT: self._detect_data_drift,
            DriftType.METRIC_DRIFT: self._detect_metric_drift,
            DriftType.TREND_DRIFT: self._detect_trend_drift,
            DriftType.DISTRIBUTION_DRIFT: self._detect_distribution_drift,
        }
        
        # Configuration
        self.data_volume_threshold = 0.20  # 20% change threshold
        self.metric_drift_threshold = 0.15  # 15% metric change
        self.trend_change_threshold = 0.30  # 30% slope change
        self.distribution_p_threshold = 0.05  # KS test p-value
    
    def analyze(
        self,
        current_data: List[Dict[str, Any]],
        baseline_data: List[Dict[str, Any]],
        context: Dict[str, Any] = None
    ) -> List[DriftResult]:
        """Run all drift detectors on current vs baseline data
        
        Args:
            current_data: Current period data
            baseline_data: Baseline period data
            context: Additional context
            
        Returns:
            List of drift detection results
        """
        if context is None:
            context = {}
        
        results = []
        
        for drift_type, detector in self.detectors.items():
            try:
                result = detector(current_data, baseline_data, context)
                if result.detected:
                    results.append(result)
            except Exception as e:
                logger.warning(f"Drift detector {drift_type} failed: {e}")
        
        # Sort by severity
        severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
        results.sort(key=lambda r: severity_order.get(r.severity, 4))
        
        logger.info(f"Detected {len(results)} drift types")
        
        return results
    
    def _detect_data_drift(
        self,
        current: List[Dict],
        baseline: List[Dict],
        context: Dict
    ) -> DriftResult:
        """Detect changes in data characteristics (volume, schema)"""
        
        # Compare row counts
        current_count = len(current)
        baseline_count = len(baseline)
        count_change = abs(current_count - baseline_count) / max(baseline_count, 1)
        
        # Check for column changes
        current_cols = set(current[0].keys()) if current else set()
        baseline_cols = set(baseline[0].keys()) if baseline else set()
        new_cols = current_cols - baseline_cols
        removed_cols = baseline_cols - current_cols
        
        drift_detected = count_change > self.data_volume_threshold or bool(new_cols or removed_cols)
        
        # Calculate confidence
        confidence = min(count_change * 3, 0.95) if count_change > 0 else 0.3
        if new_cols or removed_cols:
            confidence = max(confidence, 0.8)
        
        # Determine severity
        if count_change > 0.5 or new_cols or removed_cols:
            severity = 'critical'
        elif count_change > 0.3:
            severity = 'high'
        elif count_change > 0.2:
            severity = 'medium'
        else:
            severity = 'low'
        
        return DriftResult(
            drift_type=DriftType.DATA_DRIFT,
            detected=drift_detected,
            confidence=confidence,
            current_period={'row_count': current_count, 'columns': list(current_cols)},
            baseline_period={'row_count': baseline_count, 'columns': list(baseline_cols)},
            statistical_test='row_count_comparison',
            p_value=1 - confidence,
            effect_size=count_change,
            drift_description=f"Data volume changed by {count_change*100:.1f}% ({baseline_count} → {current_count} rows)",
            affected_columns=list(new_cols | removed_cols),
            sample_before=baseline[:3] if baseline else None,
            sample_after=current[:3] if current else None,
            what_changed=f"Row count: {baseline_count} → {current_count} ({count_change*100:+.1f}%). New columns: {len(new_cols)}, Removed: {len(removed_cols)}",
            why_it_changed="Volume change indicates business activity shift, data pipeline modification, or ETL process change",
            severity=severity,
            impact="Analysis scope significantly altered - comparability may be affected" if count_change > 0.3 else "Minor variance in data volume",
            recommended_action="""• Verify data pipeline health and ETL job logs
• Check for missing data loads or extraction issues
• Confirm business activity levels match expectations
• Review any recent schema or process changes""",
            timestamp=datetime.now()
        )
    
    def _detect_metric_drift(
        self,
        current: List[Dict],
        baseline: List[Dict],
        context: Dict
    ) -> DriftResult:
        """Detect changes in key metric values"""
        
        # Find numeric columns
        numeric_cols = []
        if current:
            for col, val in current[0].items():
                if isinstance(val, (int, float)) and col not in ['id', 'row_num', 'timestamp']:
                    numeric_cols.append(col)
        
        drift_detected = False
        affected_cols = []
        max_drift = 0
        drift_details = []
        
        for col in numeric_cols:
            current_vals = [r.get(col, 0) for r in current if r.get(col) is not None]
            baseline_vals = [r.get(col, 0) for r in baseline if r.get(col) is not None]
            
            if current_vals and baseline_vals:
                current_mean = np.mean(current_vals)
                baseline_mean = np.mean(baseline_vals)
                
                if baseline_mean != 0:
                    drift_pct = abs(current_mean - baseline_mean) / baseline_mean
                    
                    if drift_pct > self.metric_drift_threshold:
                        drift_detected = True
                        affected_cols.append(col)
                        max_drift = max(max_drift, drift_pct)
                        drift_details.append(f"{col}: {baseline_mean:.2f} → {current_mean:.2f} ({drift_pct*100:+.1f}%)")
        
        # Determine severity
        if max_drift > 0.5:
            severity = 'critical'
        elif max_drift > 0.3:
            severity = 'high'
        elif max_drift > 0.15:
            severity = 'medium'
        else:
            severity = 'low'
        
        metrics_current = {col: np.mean([r.get(col, 0) for r in current]) for col in affected_cols}
        metrics_baseline = {col: np.mean([r.get(col, 0) for r in baseline]) for col in affected_cols}
        
        return DriftResult(
            drift_type=DriftType.METRIC_DRIFT,
            detected=drift_detected,
            confidence=min(max_drift * 2, 0.95),
            current_period={'metrics': metrics_current},
            baseline_period={'metrics': metrics_baseline},
            statistical_test='mean_comparison',
            p_value=0.05 if drift_detected else 0.5,
            effect_size=max_drift,
            drift_description=f"Metric drift detected in {len(affected_cols)} columns: {', '.join(drift_details[:3])}",
            affected_columns=affected_cols,
            sample_before=None,
            sample_after=None,
            what_changed=f"Metrics changed by up to {max_drift*100:.1f}% across {len(affected_cols)} indicators",
            why_it_changed="Business performance shift, market condition changes, or strategic interventions affecting KPIs",
            severity=severity,
            impact="KPI targets and forecasts may require adjustment based on new baseline" if max_drift > 0.3 else "Normal variance within acceptable range",
            recommended_action="""• Review KPI definitions for consistency
• Adjust forecasts and targets appropriately
• Investigate underlying business drivers
• Document learnings for future planning""",
            timestamp=datetime.now()
        )
    
    def _detect_trend_drift(
        self,
        current: List[Dict],
        baseline: List[Dict],
        context: Dict
    ) -> DriftResult:
        """Detect changes in trend patterns"""
        
        trend_col = context.get('trend_column', 'value')
        
        current_vals = [r.get(trend_col, 0) for r in current if r.get(trend_col)]
        baseline_vals = [r.get(trend_col, 0) for r in baseline if r.get(trend_col)]
        
        if len(current_vals) < 3 or len(baseline_vals) < 3:
            return DriftResult(
                drift_type=DriftType.TREND_DRIFT,
                detected=False,
                confidence=0,
                current_period={},
                baseline_period={},
                statistical_test='linear_regression',
                p_value=1.0,
                effect_size=0,
                drift_description="Insufficient data for trend analysis",
                affected_columns=[],
                sample_before=None,
                sample_after=None,
                what_changed="N/A - Insufficient data points",
                why_it_changed="N/A",
                severity='low',
                impact="None",
                recommended_action="Collect more time-series data (minimum 3 points per period)",
                timestamp=datetime.now()
            )
        
        # Calculate trend slopes
        x_current = np.arange(len(current_vals))
        x_baseline = np.arange(len(baseline_vals))
        
        try:
            slope_current = np.polyfit(x_current, current_vals, 1)[0]
            slope_baseline = np.polyfit(x_baseline, baseline_vals, 1)[0]
        except:
            slope_current = 0
            slope_baseline = 0
        
        # Detect trend change
        slope_change = abs((slope_current - slope_baseline) / (abs(slope_baseline) + 1e-10))
        drift_detected = slope_change > self.trend_change_threshold
        
        # Determine severity
        if slope_change > 0.5:
            severity = 'high'
        elif slope_change > 0.3:
            severity = 'medium'
        else:
            severity = 'low'
        
        direction = "accelerating" if abs(slope_current) > abs(slope_baseline) else "decelerating"
        trend_direction = "upward" if slope_current > 0 else "downward"
        
        return DriftResult(
            drift_type=DriftType.TREND_DRIFT,
            detected=drift_detected,
            confidence=min(slope_change * 2, 0.95),
            current_period={'trend_slope': slope_current, 'values': current_vals[:5]},
            baseline_period={'trend_slope': slope_baseline, 'values': baseline_vals[:5]},
            statistical_test='slope_comparison',
            p_value=0.05 if drift_detected else 0.5,
            effect_size=slope_change,
            drift_description=f"Trend slope changed by {slope_change*100:.1f}% (direction: {direction}, trajectory: {trend_direction})",
            affected_columns=[trend_col],
            sample_before=None,
            sample_after=None,
            what_changed=f"Growth rate changed from {slope_baseline:.4f} to {slope_current:.4f} per period ({slope_change*100:+.1f}%)",
            why_it_changed="Market dynamics shift, strategic interventions, or external factors affecting growth trajectory",
            severity=severity,
            impact="Forecast accuracy may be significantly affected - models need recalibration" if drift_detected else "Stable trend continues",
            recommended_action="""• Recalibrate forecasting models with new trend parameters
• Review strategic initiatives driving the change
• Update growth assumptions in financial planning
• Monitor for sustained vs. temporary shift""",
            timestamp=datetime.now()
        )
    
    def _detect_distribution_drift(
        self,
        current: List[Dict],
        baseline: List[Dict],
        context: Dict
    ) -> DriftResult:
        """Detect changes in data distribution using statistical tests"""
        
        # Find numeric columns for distribution test
        test_col = context.get('distribution_column')
        
        if not test_col and current:
            for col, val in current[0].items():
                if isinstance(val, (int, float)) and col not in ['id', 'row_num']:
                    test_col = col
                    break
        
        if not test_col:
            return DriftResult(
                drift_type=DriftType.DISTRIBUTION_DRIFT,
                detected=False,
                confidence=0,
                current_period={},
                baseline_period={},
                statistical_test='none',
                p_value=1.0,
                effect_size=0,
                drift_description="No numeric columns available for distribution test",
                affected_columns=[],
                sample_before=None,
                sample_after=None,
                what_changed="N/A - No suitable columns",
                why_it_changed="N/A",
                severity='low',
                impact="None",
                recommended_action="N/A",
                timestamp=datetime.now()
            )
        
        current_vals = [r.get(test_col, 0) for r in current if r.get(test_col) is not None]
        baseline_vals = [r.get(test_col, 0) for r in baseline if r.get(test_col) is not None]
        
        if len(current_vals) < 10 or len(baseline_vals) < 10:
            return DriftResult(
                drift_type=DriftType.DISTRIBUTION_DRIFT,
                detected=False,
                confidence=0,
                current_period={'mean': np.mean(current_vals) if current_vals else 0},
                baseline_period={'mean': np.mean(baseline_vals) if baseline_vals else 0},
                statistical_test='kolmogorov_smirnov',
                p_value=1.0,
                effect_size=0,
                drift_description="Insufficient samples for distribution test (need >= 10 per period)",
                affected_columns=[test_col],
                sample_before=None,
                sample_after=None,
                what_changed="N/A - Insufficient data",
                why_it_changed="N/A",
                severity='low',
                impact="None",
                recommended_action="Collect more samples for reliable distribution comparison",
                timestamp=datetime.now()
            )
        
        # Perform KS test if scipy available, otherwise use simple comparison
        try:
            from scipy import stats
            ks_stat, p_value = stats.ks_2samp(current_vals, baseline_vals)
        except ImportError:
            # Simple comparison without scipy
            ks_stat = abs(np.mean(current_vals) - np.mean(baseline_vals)) / (np.std(baseline_vals) + 1e-10)
            p_value = 0.05 if ks_stat > 0.5 else 0.5
        
        drift_detected = p_value < self.distribution_p_threshold
        
        # Determine severity
        if p_value < 0.01:
            severity = 'high'
        elif p_value < 0.05:
            severity = 'medium'
        else:
            severity = 'low'
        
        return DriftResult(
            drift_type=DriftType.DISTRIBUTION_DRIFT,
            detected=drift_detected,
            confidence=1 - p_value,
            current_period={
                'mean': np.mean(current_vals),
                'std': np.std(current_vals),
                'median': np.median(current_vals)
            },
            baseline_period={
                'mean': np.mean(baseline_vals),
                'std': np.std(baseline_vals),
                'median': np.median(baseline_vals)
            },
            statistical_test='kolmogorov_smirnov',
            p_value=p_value,
            effect_size=ks_stat,
            drift_description=f"Distribution changed significantly (KS statistic: {ks_stat:.3f}, p-value: {p_value:.4f})" if drift_detected else "Distribution stable",
            affected_columns=[test_col],
            sample_before=[{'value': v} for v in baseline_vals[:3]],
            sample_after=[{'value': v} for v in current_vals[:3]],
            what_changed=f"Mean: {np.mean(baseline_vals):.2f} → {np.mean(current_vals):.2f}, Std: {np.std(baseline_vals):.2f} → {np.std(current_vals):.2f}",
            why_it_changed="Underlying population characteristics have shifted - indicates fundamental change in data generating process" if drift_detected else "Stable distribution maintained",
            severity=severity,
            impact="Machine learning models and statistical analyses may need retraining" if drift_detected else "Current models remain valid",
            recommended_action="""• Retrain ML models with new distribution data
• Review feature distributions for training-serving skew
• Update data quality assumptions and monitoring thresholds
• Investigate root cause of distribution shift""" if drift_detected else "Continue monitoring - no action required",
            timestamp=datetime.now()
        )
