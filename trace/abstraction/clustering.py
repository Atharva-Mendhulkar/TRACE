"""
Semantic Abstraction Clustering & Taxonomy Management (PRD §12).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol
import numpy as np

from trace.abstraction.rules import RuleBasedAbstraction


class EmbeddingModel(Protocol):
    model_id: str
    model_version: str
    dimensions: int

    def embed(self, texts: List[str]) -> np.ndarray:
        ...


@dataclass
class SymbolTaxonomy:
    taxonomy_version: int
    embedding_model_version: str
    content_hash: str
    canonical_symbols: List[str]
    centroids: Dict[str, List[float]] = field(default_factory=dict)


class SemanticAbstractionEngine:
    """Manages taxonomy versions and nearest-centroid canonicalization."""

    def __init__(
        self,
        taxonomy_version: int = 1,
        similarity_threshold: float = 0.75,
        rule_fallback: Optional[RuleBasedAbstraction] = None,
    ):
        self.taxonomy_version = taxonomy_version
        self.similarity_threshold = similarity_threshold
        self.rule_engine = rule_fallback or RuleBasedAbstraction()
        self.centroids: Dict[str, np.ndarray] = {}
        self.embedding_model: Optional[EmbeddingModel] = None

    def set_embedding_model(self, model: EmbeddingModel) -> None:
        self.embedding_model = model

    def register_centroids(self, centroids: Dict[str, List[float]]) -> None:
        """Register centroid vectors for canonical symbols."""
        self.centroids = {k: np.array(v, dtype=np.float32) for k, v in centroids.items()}

    def canonicalize(
        self,
        raw_symbol: str,
        docstring: str = "",
        param_schema: Dict[str, Any] | None = None,
        event_type: str = "tool_call",
    ) -> str:
        """Assign canonical symbol to raw action name (PRD §12.1)."""
        # If no embedding model or no centroids registered, use deterministic rule engine
        if not self.embedding_model or not self.centroids:
            return self.rule_engine.canonicalize(raw_symbol, event_type=event_type)

        # Build embedding input text (PRD §12.3)
        schema_keys = sorted(param_schema.keys()) if param_schema else []
        text = f"{raw_symbol}\n{docstring or ''}\n{json.dumps(schema_keys)}"
        
        vec = self.embedding_model.embed([text])[0]
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm

        # Nearest centroid lookup via cosine similarity
        best_symbol = None
        best_sim = -1.0

        for sym, centroid in self.centroids.items():
            c_norm = np.linalg.norm(centroid)
            if c_norm > 0:
                c_normed = centroid / c_norm
            else:
                c_normed = centroid
            sim = float(np.dot(vec, c_normed))
            if sim > best_sim:
                best_sim = sim
                best_symbol = sym

        if best_sim >= self.similarity_threshold and best_symbol:
            return best_symbol

        # Unknown symbol handling (PRD §12.6)
        raw_hash = hashlib.sha256(raw_symbol.encode("utf-8")).hexdigest()[:8]
        return f"unknown_symbol_{raw_hash}"
