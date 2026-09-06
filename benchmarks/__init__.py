"""
TRACE Benchmarks Package.
"""

from benchmarks.synthetic_generator import (
    SyntheticTraceGenerator,
    create_ground_truth_research_agent_pdfa,
    run_rq2_benchmark,
)

__all__ = [
    "SyntheticTraceGenerator",
    "create_ground_truth_research_agent_pdfa",
    "run_rq2_benchmark",
]
