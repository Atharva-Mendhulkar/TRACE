"""
TRACE Inference Package: Native State-Merging Learners (PRD §14).
"""

from trace.inference.native_alergia import NativeALERGIAEngine, NativeStateMergingLearner
from trace.inference.native_edsm import NativeEDSMEngine
from trace.inference.pta import PrefixTreeAcceptor


def get_learner_engine(name: str = "native-alergia", **kwargs):
    """Select an automata learning engine by name."""
    canonical = name.lower().strip()
    if canonical in ("native-alergia", "alergia", "native"):
        return NativeALERGIAEngine(**kwargs)
    if canonical in ("native-edsm", "edsm"):
        return NativeEDSMEngine(**kwargs)
    raise ValueError(
        f"Unknown learner engine '{name}'. Supported: 'native-alergia', 'native-edsm'."
    )


__all__ = [
    "PrefixTreeAcceptor",
    "NativeALERGIAEngine",
    "NativeEDSMEngine",
    "NativeStateMergingLearner",
    "get_learner_engine",
]
