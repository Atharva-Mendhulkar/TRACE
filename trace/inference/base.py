"""
Base Learner Engine Interface (PRD §14).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from trace.models.pdfa import PDFA


class LearnerEngine(ABC):
    """Abstract interface for passive automata learning engines."""

    name: str

    @abstractmethod
    def fit(self, traces: List[List[str]]) -> PDFA:
        """Learn a PDFA from a corpus of positive trace sequences."""
        pass
