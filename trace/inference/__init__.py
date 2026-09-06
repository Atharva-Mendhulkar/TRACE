"""
TRACE Inference Package with Distinct Passive Learning Engines (PRD §14).
"""

from typing import Any, Optional

from trace.inference.base import LearnerEngine
from trace.inference.flexfringe import FlexFringeRunner, FlexFringeSubprocessEngine
from trace.inference.native_alergia import NativeALERGIAEngine
from trace.inference.native_edsm import NativeEDSMEngine
from trace.inference.native_rpni import NativeRPNIEngine
from trace.inference.pta import PrefixTreeAcceptor

# NativeStateMergingLearner default alias to NativeALERGIAEngine
NativeStateMergingLearner = NativeALERGIAEngine


def get_learner_engine(name: str = "native-alergia", **kwargs: Any) -> LearnerEngine:
    """Factory function for selecting an automata learning engine."""
    canonical_name = name.lower().strip()
    if canonical_name in ("flexfringe", "subprocess"):
        return FlexFringeSubprocessEngine(**kwargs)
    elif canonical_name in ("native-alergia", "alergia", "native"):
        return NativeALERGIAEngine(**kwargs)
    elif canonical_name in ("native-edsm", "edsm"):
        return NativeEDSMEngine(**kwargs)
    elif canonical_name in ("native-rpni", "rpni"):
        return NativeRPNIEngine(**kwargs)
    else:
        raise ValueError(
            f"Unknown learner engine '{name}'. Supported: 'flexfringe', 'native-alergia', 'native-edsm', 'native-rpni'."
        )


__all__ = [
    "LearnerEngine",
    "PrefixTreeAcceptor",
    "NativeALERGIAEngine",
    "NativeEDSMEngine",
    "NativeRPNIEngine",
    "NativeStateMergingLearner",
    "FlexFringeSubprocessEngine",
    "FlexFringeRunner",
    "get_learner_engine",
]
