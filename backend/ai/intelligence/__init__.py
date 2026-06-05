"""Intelligence Layer for Signal Detection and Drift Analysis.

Provides real-time detection of:
- Signals: Spikes, drops, outliers, trends, anomalies
- Drifts: Data drift, metric drift, trend drift, distribution drift
"""

from .signal_detector import (
    SignalDetectionEngine,
    DetectedSignal,
    SignalType,
    SignalSeverity,
)
from .drift_detector import (
    DriftDetectionEngine,
    DriftResult,
    DriftType,
)

__all__ = [
    'SignalDetectionEngine',
    'DetectedSignal',
    'SignalType',
    'SignalSeverity',
    'DriftDetectionEngine',
    'DriftResult',
    'DriftType',
]
