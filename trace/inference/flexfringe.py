"""
FlexFringe Subprocess Wrapper (PRD §14).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from trace.inference.base import LearnerEngine
from trace.models.pdfa import PDFA


class FlexFringeSubprocessEngine(LearnerEngine):
    """
    Primary passive learning engine wrapper for external FlexFringe C++ state-merging tool (PRD §14).
    """

    name = "flexfringe"

    def __init__(
        self,
        binary_path: Optional[str] = None,
        heuristic: str = "alergia",
        alpha: float = 0.05,
        min_support: int = 1,
    ):
        self.binary_path = binary_path or shutil.which("flexfringe")
        self.heuristic = heuristic
        self.alpha = alpha
        self.min_support = min_support

    def is_available(self) -> bool:
        return self.binary_path is not None and os.path.isfile(self.binary_path)

    def fit(self, traces: List[List[str]]) -> PDFA:
        """Fit a PDFA using FlexFringe subprocess or native reference engine fallback."""
        if not traces:
            return PDFA(q0="q0")

        if not self.is_available():
            from trace.inference.native_alergia import NativeALERGIAEngine
            from trace.inference.native_edsm import NativeEDSMEngine

            if self.heuristic == "edsm":
                return NativeEDSMEngine().fit(traces)
            return NativeALERGIAEngine(alpha=self.alpha, min_support=self.min_support).fit(traces)

        with tempfile.TemporaryDirectory() as tmpdir:
            dat_path = Path(tmpdir) / "traces.dat"
            dot_path = Path(tmpdir) / "traces.dot"
            self._write_dat_file(traces, dat_path)

            # Invoke flexfringe command
            cmd = [
                str(self.binary_path),
                "--heuristic", self.heuristic,
                "-s", str(self.min_support),
                str(dat_path),
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    cwd=tmpdir,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=True,
                )
                # Look for output .dot file
                candidates = list(Path(tmpdir).glob("*.dot"))
                if not candidates:
                    # Fallback to native
                    return NativeStateMergingLearner(heuristic=self.heuristic).fit(traces)

                dot_content = candidates[0].read_text(encoding="utf-8")
                return self._parse_dot(dot_content)

            except Exception:
                # Fallback to native learner if subprocess execution fails
                return NativeStateMergingLearner(heuristic=self.heuristic).fit(traces)

    def _write_dat_file(self, traces: List[List[str]], path: Path) -> None:
        """Write traces in FlexFringe .dat format (PRD §14.4)."""
        alphabet: Set[str] = set()
        for t in traces:
            alphabet.update(t)

        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{len(traces)} {len(alphabet)}\n")
            for t in traces:
                f.write(f"1 {len(t)} {' '.join(t)}\n")

    def _parse_dot(self, dot_text: str) -> PDFA:
        """Parse Graphviz DOT string exported by FlexFringe into a PDFA."""
        pdfa = PDFA(q0="q0")
        # Match lines like: "q0" -> "q1" [label="search/0.75"] or label="sym (10)"
        trans_pattern = re.compile(r'"?(\w+)"?\s*->\s*"?(\w+)"?\s*\[label="([^"]+)"\]')
        final_pattern = re.compile(r'"?(\w+)"?\s*\[.*shape=(doublecircle|box).*\]')

        for line in dot_text.splitlines():
            line = line.strip()
            f_match = final_pattern.search(line)
            if f_match:
                pdfa.mark_final(f_match.group(1))

            t_match = trans_pattern.search(line)
            if t_match:
                from_st = t_match.group(1)
                to_st = t_match.group(2)
                raw_label = t_match.group(3)

                # Parse label (format varies: "sym/0.5" or "sym:count" or "sym")
                parts = re.split(r"[/:(]", raw_label)
                sym = parts[0].strip()
                prob = 1.0
                if len(parts) > 1:
                    try:
                        clean_num = parts[1].replace(")", "").strip()
                        prob = float(clean_num)
                    except ValueError:
                        prob = 1.0

                pdfa.add_transition(from_st, sym, to_st, frequency=1, recompute_probs=False)
                pdfa.P[(from_st, sym)] = prob

        pdfa.recompute_all_probabilities()
        return pdfa


# Backwards compatibility alias
FlexFringeRunner = FlexFringeSubprocessEngine

