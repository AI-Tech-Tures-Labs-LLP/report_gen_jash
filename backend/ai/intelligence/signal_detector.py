"""Signal Detection Engine for intelligent data analysis.

Detects signals in query results:
- Spikes: Sudden upward jumps
- Drops: Sudden downward falls
- Outliers: Statistical anomalies
- Trends: Directional changes
- Anomalies: Unusual patterns
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum
from datetime import datetime
import numpy as np
import logging

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """Types of signals that can be detected"""
    SPIKE = "spike"
    DROP = "drop"
    OUTLIER = "outlier"
    TREND = "trend"
    CORRELATION = "correlation"
    SEASONALITY = "seasonality"
    METRIC_SHIFT = "metric_shift"
    ANOMALY = "anomaly"


class SignalSeverity(Enum):
    """Severity levels for detected signals"""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class DetectedSignal:
    """A detected signal with full explainability"""
    signal_type: SignalType
    severity: SignalSeverity
    location: str  # Where in the data
    description: str
    value: float
    baseline: float
    variance_pct: float
    confidence: float
    timestamp: datetime
    
    # Explainability fields
    what_changed: str
    why_changed: str
    impact_assessment: str
    recommended_action: str


class SignalDetectionEngine:
    """Real-time signal detection in query results
    
    Usage:
        engine = SignalDetectionEngine()
        
        signals = engine.analyze(
            data=[{'month': 'Jan', 'revenue': 100}, {'month': 'Feb', 'revenue': 200}],
            value_column='revenue',
            label_column='month',
            context={'currency': 'INR'}
        )
        
        for signal in signals:
            print(f"{signal.signal_type.value}: {signal.description}")
            print(f"  What: {signal.what_changed}")
            print(f"  Why: {signal.why_changed}")
            print(f"  Action: {signal.recommended_action}")
    """
    
    def __init__(self):
        self.detectors = {
            SignalType.SPIKE: self._detect_spikes,
            SignalType.DROP: self._detect_drops,
            SignalType.OUTLIER: self._detect_outliers,
            SignalType.TREND: self._detect_trends,
            SignalType.ANOMALY: self._detect_anomalies,
        }
        
        # Configuration
        self.spike_threshold = 2.5  # Z-score threshold
        self.drop_threshold = -2.5
        self.trend_change_threshold = 0.3  # 30% change in slope
        self.outlier_iqr_multiplier = 1.5
    
    def analyze(
        self, 
        data: List[Dict[str, Any]], 
        value_column: str,
        label_column: str,
        context: Dict[str, Any] = None
    ) -> List[DetectedSignal]:
        """Analyze data and detect all signals
        
        Args:
            data: List of data rows
            value_column: Column name containing values to analyze
            label_column: Column name for labels (x-axis)
            context: Additional context for signal analysis
            
        Returns:
            List of detected signals
        """
        if context is None:
            context = {}
        
        signals = []
        
        # Extract values
        values = []
        labels = []
        for row in data:
            val = row.get(value_column)
            if val is not None:
                try:
                    values.append(float(val))
                    labels.append(str(row.get(label_column, '')))
                except (ValueError, TypeError):
                    continue
        
        if len(values) < 3:
            logger.debug("Insufficient data for signal detection (need >= 3 points)")
            return signals
        
        # Run all detectors
        for signal_type, detector in self.detectors.items():
            try:
                detected = detector(values, labels, context)
                signals.extend(detected)
            except Exception as e:
                logger.warning(f"Signal detector {signal_type} failed: {e}")
        
        # Sort by severity and confidence
        severity_order = {
            SignalSeverity.CRITICAL: 0,
            SignalSeverity.HIGH: 1,
            SignalSeverity.MEDIUM: 2,
            SignalSeverity.LOW: 3
        }
        
        signals.sort(key=lambda s: (severity_order[s.severity], -s.confidence))
        
        logger.info(f"Detected {len(signals)} signals in {len(values)} data points")
        
        return signals
    
    def _detect_spikes(
        self, 
        values: List[float], 
        labels: List[str],
        context: Dict[str, Any]
    ) -> List[DetectedSignal]:
        """Detect significant upward spikes using Z-score"""
        
        signals = []
        window = min(4, len(values) - 1)
        
        if window < 2:
            return signals
        
        for i in range(window, len(values)):
            window_values = values[i-window:i]
            mean = np.mean(window_values)
            std = np.std(window_values) or (mean * 0.1)
            
            current = values[i]
            z_score = (current - mean) / std if std > 0 else 0
            
            # Spike: z-score > threshold and > 20% increase
            if z_score > self.spike_threshold and current > mean * 1.2:
                variance_pct = ((current - mean) / mean) * 100
                
                # Determine severity
                severity = SignalSeverity.HIGH if z_score > 3 else SignalSeverity.MEDIUM
                if variance_pct > 50:
                    severity = SignalSeverity.CRITICAL
                elif variance_pct > 30:
                    severity = SignalSeverity.HIGH
                
                signals.append(DetectedSignal(
                    signal_type=SignalType.SPIKE,
                    severity=severity,
                    location=labels[i],
                    description=f"Value spiked to {self._format_value(current)} ({variance_pct:+.1f}% vs {window}-period avg of {self._format_value(mean)})",
                    value=current,
                    baseline=mean,
                    variance_pct=variance_pct,
                    confidence=min(abs(z_score) / 4, 0.99),
                    timestamp=datetime.now(),
                    what_changed=f"Metric increased by {variance_pct:.1f}% from {self._format_value(mean)} to {self._format_value(current)}",
                    why_changed=self._hypothesize_spike(context, labels[i], variance_pct),
                    impact_assessment=self._assess_impact(current, mean, context),
                    recommended_action=self._recommend_spike_action(context, severity)
                ))
        
        return signals
    
    def _detect_drops(
        self, 
        values: List[float], 
        labels: List[str],
        context: Dict[str, Any]
    ) -> List[DetectedSignal]:
        """Detect significant downward drops using Z-score"""
        
        signals = []
        window = min(4, len(values) - 1)
        
        if window < 2:
            return signals
        
        for i in range(window, len(values)):
            window_values = values[i-window:i]
            mean = np.mean(window_values)
            std = np.std(window_values) or (mean * 0.1)
            
            current = values[i]
            z_score = (current - mean) / std if std > 0 else 0
            
            # Drop: z-score < -threshold and > 20% decrease
            if z_score < self.drop_threshold and current < mean * 0.8:
                variance_pct = ((current - mean) / mean) * 100
                
                # Determine severity
                severity = SignalSeverity.HIGH if z_score < -3 else SignalSeverity.MEDIUM
                if variance_pct < -50:
                    severity = SignalSeverity.CRITICAL
                elif variance_pct < -30:
                    severity = SignalSeverity.HIGH
                
                signals.append(DetectedSignal(
                    signal_type=SignalType.DROP,
                    severity=severity,
                    location=labels[i],
                    description=f"Value dropped to {self._format_value(current)} ({variance_pct:+.1f}% vs {window}-period avg of {self._format_value(mean)})",
                    value=current,
                    baseline=mean,
                    variance_pct=variance_pct,
                    confidence=min(abs(z_score) / 4, 0.99),
                    timestamp=datetime.now(),
                    what_changed=f"Metric decreased by {abs(variance_pct):.1f}% from {self._format_value(mean)} to {self._format_value(current)}",
                    why_changed=self._hypothesize_drop(context, labels[i], variance_pct),
                    impact_assessment=self._assess_impact(current, mean, context),
                    recommended_action=self._recommend_drop_action(context, severity)
                ))
        
        return signals
    
    def _detect_outliers(
        self, 
        values: List[float], 
        labels: List[str],
        context: Dict[str, Any]
    ) -> List[DetectedSignal]:
        """Detect statistical outliers using IQR method"""
        
        signals = []
        
        q1 = np.percentile(values, 25)
        q3 = np.percentile(values, 75)
        iqr = q3 - q1
        
        lower_bound = q1 - self.outlier_iqr_multiplier * iqr
        upper_bound = q3 + self.outlier_iqr_multiplier * iqr
        
        for i, (value, label) in enumerate(zip(values, labels)):
            if value < lower_bound or value > upper_bound:
                is_high = value > upper_bound
                
                # Determine severity
                extreme_lower = q1 - 3 * iqr
                extreme_upper = q3 + 3 * iqr
                
                if value < extreme_lower or value > extreme_upper:
                    severity = SignalSeverity.HIGH
                else:
                    severity = SignalSeverity.MEDIUM
                
                median = np.median(values)
                variance_pct = ((value - median) / median) * 100 if median else 0
                
                signals.append(DetectedSignal(
                    signal_type=SignalType.OUTLIER,
                    severity=severity,
                    location=label,
                    description=f"Statistical outlier detected: {self._format_value(value)} (outside {self._format_value(lower_bound)} - {self._format_value(upper_bound)} range)",
                    value=value,
                    baseline=median,
                    variance_pct=variance_pct,
                    confidence=0.85,
                    timestamp=datetime.now(),
                    what_changed=f"Value {abs(variance_pct):.1f}% outside normal range ({self._format_value(lower_bound)} - {self._format_value(upper_bound)})",
                    why_changed="Possible data entry error, special event, or genuine anomaly requiring investigation",
                    impact_assessment="Verify data accuracy before taking action - may indicate measurement error or exceptional event",
                    recommended_action="• Review source data for this period\n• Cross-check with operational records\n• Validate data collection process"
                ))
        
        return signals
    
    def _detect_trends(
        self, 
        values: List[float], 
        labels: List[str],
        context: Dict[str, Any]
    ) -> List[DetectedSignal]:
        """Detect trend changes using linear regression"""
        
        signals = []
        
        if len(values) < 6:
            return signals
        
        # Split data into halves
        mid = len(values) // 2
        first_half = values[:mid]
        second_half = values[mid:]
        
        # Calculate trend slopes
        x1 = np.arange(len(first_half))
        x2 = np.arange(len(second_half))
        
        try:
            slope1 = np.polyfit(x1, first_half, 1)[0] if len(set(first_half)) > 1 else 0
            slope2 = np.polyfit(x2, second_half, 1)[0] if len(set(second_half)) > 1 else 0
        except:
            return signals
        
        # Detect trend change
        if abs(slope1) > 0 and abs(slope2) > 0:
            slope_change = abs((slope2 - slope1) / slope1) if slope1 != 0 else 0
            
            if slope_change > self.trend_change_threshold:
                direction = "accelerating" if abs(slope2) > abs(slope1) else "decelerating"
                trend_direction = "upward" if slope2 > 0 else "downward"
                
                severity = SignalSeverity.HIGH if slope_change > 0.5 else SignalSeverity.MEDIUM
                
                signals.append(DetectedSignal(
                    signal_type=SignalType.TREND,
                    severity=severity,
                    location=f"Period {labels[mid]} onwards",
                    description=f"Trend {direction}: {trend_direction} trajectory changed significantly (slope change: {slope_change*100:.1f}%)",
                    value=slope2,
                    baseline=slope1,
                    variance_pct=slope_change * 100,
                    confidence=min(slope_change * 2, 0.95),
                    timestamp=datetime.now(),
                    what_changed=f"Growth rate changed by {slope_change*100:.1f}% from {slope1:.2f} to {slope2:.2f}",
                    why_changed="Market conditions, seasonality effects, or strategic changes may be driving the trend shift",
                    impact_assessment="Forecast accuracy may be affected - consider updating predictive models",
                    recommended_action="• Analyze period-over-period drivers\n• Review correlation with external factors\n• Update growth assumptions in forecasts"
                ))
        
        return signals
    
    def _detect_anomalies(
        self, 
        values: List[float], 
        labels: List[str],
        context: Dict[str, Any]
    ) -> List[DetectedSignal]:
        """Detect anomalies using 3-sigma rule"""
        
        signals = []
        
        mean = np.mean(values)
        std = np.std(values)
        
        if std == 0:
            return signals
        
        for i, (value, label) in enumerate(zip(values, labels)):
            z_score = (value - mean) / std
            
            if abs(z_score) > 3:  # 3 sigma rule
                severity = SignalSeverity.CRITICAL if abs(z_score) > 4 else SignalSeverity.HIGH
                variance_pct = ((value - mean) / mean) * 100 if mean else 0
                
                signals.append(DetectedSignal(
                    signal_type=SignalType.ANOMALY,
                    severity=severity,
                    location=label,
                    description=f"Statistical anomaly detected (Z-score: {z_score:.2f}, {abs(z_score):.1f} standard deviations from mean)",
                    value=value,
                    baseline=mean,
                    variance_pct=variance_pct,
                    confidence=min(abs(z_score) / 5, 0.99),
                    timestamp=datetime.now(),
                    what_changed=f"Value {abs(z_score):.1f} standard deviations from mean ({self._format_value(mean)} ± {self._format_value(std)})",
                    why_changed="Unusual event or potential data quality issue requiring immediate investigation",
                    impact_assessment="High priority review recommended" if severity == SignalSeverity.CRITICAL else "Review for pattern identification",
                    recommended_action="• Investigate underlying cause immediately\n• Check for data pipeline issues\n• Verify with operational teams" if severity == SignalSeverity.CRITICAL else "• Monitor for recurrence\n• Add to anomaly watchlist"
                ))
        
        return signals
    
    # Helper methods for explainability
    def _format_value(self, value: float) -> str:
        """Format numeric value with appropriate units"""
        abs_val = abs(value)
        
        if abs_val >= 1e7:
            return f"₹{value/1e7:.2f}Cr"
        elif abs_val >= 1e5:
            return f"₹{value/1e5:.2f}L"
        elif abs_val >= 1e3:
            return f"₹{value/1e3:.2f}K"
        else:
            return f"{value:,.0f}"
    
    def _hypothesize_spike(self, context: Dict, location: str, variance: float) -> str:
        """Generate hypothesis for spike occurrence"""
        
        hypotheses = [
            f"Seasonal demand surge in {location} - check for festival/holiday effects",
            "Promotional campaign impact - verify marketing spend correlation",
            "New product launch effect - cross-reference with product release dates",
            "Competitor market share shift - competitor may have exited market",
            "Pricing strategy change - check for discount/promotional pricing",
            "Bulk order from key account - review major customer transactions"
        ]
        
        # Use location hash for deterministic selection
        idx = hash(location) % len(hypotheses)
        return hypotheses[idx]
    
    def _hypothesize_drop(self, context: Dict, location: str, variance: float) -> str:
        """Generate hypothesis for drop occurrence"""
        
        hypotheses = [
            f"Supply chain disruption in {location} - check inventory levels",
            "Customer churn to competitor - review customer retention metrics",
            "Economic downturn impact - correlate with market indicators",
            "Seasonal low period - expected post-holiday/seasonal adjustment",
            "Operational issues - check fulfillment and delivery performance",
            "Product quality concerns - review customer feedback and returns"
        ]
        
        idx = hash(location) % len(hypotheses)
        return hypotheses[idx]
    
    def _assess_impact(self, current: float, baseline: float, context: Dict) -> str:
        """Assess business impact of signal"""
        
        diff = abs(current - baseline)
        currency = context.get('currency', 'INR')
        metric_name = context.get('metric_name', 'metric')
        
        if diff > 1e7:  # > 1 Cr
            return f"**Critical Impact**: ₹{diff/1e7:.2f}Cr variance in {metric_name}. Immediate management attention required."
        elif diff > 1e6:  # > 10L
            return f"**High Impact**: ₹{diff/1e5:.2f}L variance in {metric_name}. Review with department head recommended."
        elif diff > 1e5:  # > 1L
            return f"**Moderate Impact**: ₹{diff/1e5:.2f}L variance in {metric_name}. Monitor and investigate if pattern persists."
        else:
            return f"**Limited Impact**: ₹{diff/1000:.0f}K variance in {metric_name}. Within normal variance range."
    
    def _recommend_spike_action(self, context: Dict, severity: SignalSeverity) -> str:
        """Recommend action for spike signal"""
        
        if severity == SignalSeverity.CRITICAL:
            return """• **IMMEDIATE**: Verify data accuracy - check for double-counting or system errors
• **URGENT**: Scale operations to meet demand - increase inventory/production
• **TODAY**: Review inventory levels - ensure stock availability
• **2 HOURS**: Brief management on spike and preliminary findings
• **24 HOURS**: Deep-dive analysis on drivers and sustainability"""
        elif severity == SignalSeverity.HIGH:
            return """• Verify spike sustainability - check if one-time or trend
• Analyze contributing factors - segment by product/region/customer
• Document learnings for replication across other areas
• Update forecasts if spike is determined to be sustainable"""
        else:
            return """• Monitor for continuation over next 2-3 periods
• Note in monthly review as positive variance
• Document potential best practices if sustainable"""
    
    def _recommend_drop_action(self, context: Dict, severity: SignalSeverity) -> str:
        """Recommend action for drop signal"""
        
        if severity == SignalSeverity.CRITICAL:
            return """• **IMMEDIATE**: Activate contingency plans for revenue recovery
• **URGENT**: Launch customer outreach campaign to prevent further churn
• **TODAY**: Review pricing strategy - consider promotional response
• **2 HOURS**: Emergency team meeting to assess situation
• **24 HOURS**: Root cause analysis and action plan presentation to leadership"""
        elif severity == SignalSeverity.HIGH:
            return """• Analyze root cause - segment drop by product/region/customer
• Monitor next 2-3 periods to confirm trend vs. anomaly
• Prepare mitigation options including promotional interventions
• Brief management on situation and response plan"""
        else:
            return """• Monitor for continuation over next 2-3 periods
• Prepare contingency options if drop continues
• Document as variance for monthly review"""
