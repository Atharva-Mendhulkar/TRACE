"""
Behavioral Drift Detector (PRD §22).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from uuid import uuid4

import numpy as np
from scipy import stats


@dataclass
class DriftEvent:
    drift_id: str
    agent_id: str
    test_used: str
    statistic: float
    p_value: float
    severity: str
    window_size: int
    relearn_triggered: bool
    timestamp: str


class DriftDetector:
    """
    Monitors rolling conformance-score (NLL) distributions for shifts relative to baseline.
    Implements two-sample Kolmogorov-Smirnov test + tail-quantile check (PRD §22.2).
    """

    def __init__(
        self,
        agent_id: str,
        window_size: int = 50,
        min_sample_size: int = 20,
        alpha: float = 0.05,
        severity_threshold: float = 0.3,
        cooldown_seconds: int = 300,
    ):
        self.agent_id = agent_id
        self.window_size = window_size
        self.min_sample_size = min_sample_size
        self.alpha = alpha
        self.severity_threshold = severity_threshold
        self.cooldown_seconds = cooldown_seconds

        self.baseline_scores: List[float] = []
        self.rolling_scores: List[float] = []
        self.last_drift_time: Optional[datetime.datetime] = None

    def set_baseline(self, scores: List[float]) -> None:
        """Initialize reference distribution from training/validation window."""
        self.baseline_scores = [s for s in scores if s < float("inf") and not np.isnan(s)]

    def record_trace_conformance(self, mean_nll: float) -> Optional[DriftEvent]:
        """Record trace conformance score and run drift evaluation if window is ready."""
        if mean_nll == float("inf") or np.isnan(mean_nll):
            return None

        self.rolling_scores.append(mean_nll)
        if len(self.rolling_scores) > self.window_size:
            self.rolling_scores.pop(0)

        # Check if enough samples
        if len(self.rolling_scores) < self.min_sample_size or len(self.baseline_scores) < self.min_sample_size:
            return None

        # Check cooldown
        now = datetime.datetime.now(datetime.timezone.utc)
        if self.last_drift_time:
            elapsed = (now - self.last_drift_time).total_seconds()
            if elapsed < self.cooldown_seconds:
                return None

        # Run Two-Sample Kolmogorov-Smirnov test (PRD §22.2)
        ks_res = stats.ks_2samp(self.baseline_scores, self.rolling_scores)
        statistic = float(ks_res.statistic)
        p_value = float(ks_res.pvalue)

        # Tail-quantile check (95th percentile shift)
        base_q95 = np.percentile(self.baseline_scores, 95)
        curr_q95 = np.percentile(self.rolling_scores, 95)
        tail_shift = float(curr_q95 - base_q95)

        is_drift = (p_value < self.alpha and statistic >= self.severity_threshold) or (tail_shift > 2.0 * np.std(self.baseline_scores))

        if is_drift:
            self.last_drift_time = now
            severity = "HIGH" if statistic > 0.5 or tail_shift > 3.0 else "MEDIUM"
            return DriftEvent(
                drift_id=str(uuid4()),
                agent_id=self.agent_id,
                test_used="ks_2samp+tail_quantile",
                statistic=statistic,
                p_value=p_value,
                severity=severity,
                window_size=len(self.rolling_scores),
                relearn_triggered=True,
                timestamp=now.isoformat(),
            )

        return None
