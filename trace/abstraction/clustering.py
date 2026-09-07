"""
Semantic Abstraction Clustering & Taxonomy Management (PRD §12).
"""

from __future__ import annotations

import collections
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple
import numpy as np

from trace.abstraction.rules import RuleBasedAbstraction


class EmbeddingModel(Protocol):
    model_id: str
    model_version: str
    dimensions: int

    def embed(self, texts: List[str]) -> np.ndarray:
        ...


class SimpleTextEmbeddingModel:
    """Deterministic n-gram frequency embedding model for Phase 2 offline/local clustering."""

    def __init__(self, dimensions: int = 128):
        self.model_id = "trace-simple-ngram-embedder"
        self.model_version = "v1"
        self.dimensions = dimensions

    def _hash_ngram(self, ngram: str) -> int:
        return int(hashlib.md5(ngram.encode("utf-8")).hexdigest(), 16) % self.dimensions

    def embed(self, texts: List[str]) -> np.ndarray:
        vectors = np.zeros((len(texts), self.dimensions), dtype=np.float32)
        for i, text in enumerate(texts):
            words = re.findall(r"\w+", text.lower())
            counts = collections.defaultdict(float)
            # Token unigrams and character 3-grams
            for w in words:
                counts[self._hash_ngram(w)] += 1.0
                for j in range(len(w) - 2):
                    sub = w[j : j + 3]
                    counts[self._hash_ngram(sub)] += 0.5
            
            for dim, val in counts.items():
                vectors[i, dim] = val

            norm = np.linalg.norm(vectors[i])
            if norm > 0:
                vectors[i] /= norm
        return vectors


@dataclass
class SymbolTaxonomy:
    taxonomy_version: int
    embedding_model_version: str
    content_hash: str
    canonical_symbols: List[str]
    centroids: Dict[str, List[float]] = field(default_factory=dict)
    cluster_members: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SymbolTaxonomy:
        return cls(**data)


class AgglomerativeClusterer:
    """Agglomerative clustering with cosine distance threshold (PRD §12.4)."""

    def __init__(
        self,
        distance_threshold: float = 0.35,
        embedder: Optional[EmbeddingModel] = None,
    ):
        self.distance_threshold = distance_threshold
        self.embedder = embedder or SimpleTextEmbeddingModel()

    def fit(
        self,
        raw_items: List[Tuple[str, str, str]],  # (raw_symbol, docstring, param_schema_hash)
        taxonomy_version: int = 1,
    ) -> SymbolTaxonomy:
        """Clusters raw actions into canonical centroids."""
        if not raw_items:
            return SymbolTaxonomy(
                taxonomy_version=taxonomy_version,
                embedding_model_version=getattr(self.embedder, "model_version", "v1"),
                content_hash="empty",
                canonical_symbols=[],
            )

        # Unique representations
        unique_items = sorted(list(set(raw_items)), key=lambda x: (x[0], x[2]))
        texts = [
            f"{sym}\n{doc}\n{hsh}" for sym, doc, hsh in unique_items
        ]
        embeddings = self.embedder.embed(texts)

        # Initially, each item is its own cluster
        clusters: List[List[int]] = [[i] for i in range(len(unique_items))]

        # Iterative pairwise single-linkage agglomeration
        while len(clusters) > 1:
            best_dist = float("inf")
            merge_pair = (-1, -1)

            for i in range(len(clusters)):
                vec_i = np.mean([embeddings[idx] for idx in clusters[i]], axis=0)
                norm_i = np.linalg.norm(vec_i)
                if norm_i > 0:
                    vec_i /= norm_i

                for j in range(i + 1, len(clusters)):
                    vec_j = np.mean([embeddings[idx] for idx in clusters[j]], axis=0)
                    norm_j = np.linalg.norm(vec_j)
                    if norm_j > 0:
                        vec_j /= norm_j

                    dist = 1.0 - float(np.dot(vec_i, vec_j))
                    if dist < best_dist:
                        best_dist = dist
                        merge_pair = (i, j)

            if best_dist <= self.distance_threshold and merge_pair[0] >= 0:
                i, j = merge_pair
                clusters[i].extend(clusters[j])
                clusters.pop(j)
            else:
                break

        # Compute normalized centroids and select canonical symbol names
        centroids: Dict[str, List[float]] = {}
        cluster_members: Dict[str, List[str]] = {}
        canonical_symbols: List[str] = []

        for cluster_indices in clusters:
            symbols = [unique_items[idx][0] for idx in cluster_indices]
            canonical_name = min(symbols, key=lambda s: (len(s), s))
            if canonical_name in centroids:
                canonical_name = f"{canonical_name}_{cluster_indices[0]}"

            c_vec = np.mean([embeddings[idx] for idx in cluster_indices], axis=0)
            c_norm = np.linalg.norm(c_vec)
            if c_norm > 0:
                c_vec /= c_norm

            centroids[canonical_name] = c_vec.tolist()
            cluster_members[canonical_name] = symbols
            canonical_symbols.append(canonical_name)

        content_str = json.dumps(
            {"symbols": sorted(canonical_symbols), "threshold": self.distance_threshold},
            sort_keys=True,
        )
        content_hash = hashlib.sha256(content_str.encode("utf-8")).hexdigest()[:16]

        return SymbolTaxonomy(
            taxonomy_version=taxonomy_version,
            embedding_model_version=getattr(self.embedder, "model_version", "v1"),
            content_hash=content_hash,
            canonical_symbols=sorted(canonical_symbols),
            centroids=centroids,
            cluster_members=cluster_members,
        )


class SemanticAbstractionEngine:
    """Manages taxonomy versions and nearest-centroid canonicalization (PRD §12)."""

    def __init__(
        self,
        taxonomy: Optional[SymbolTaxonomy] = None,
        similarity_threshold: float = 0.65,
        rule_fallback: Optional[RuleBasedAbstraction] = None,
        embedder: Optional[EmbeddingModel] = None,
    ):
        self.taxonomy = taxonomy
        self.similarity_threshold = similarity_threshold
        self.rule_engine = rule_fallback or RuleBasedAbstraction()
        self.embedder = embedder or SimpleTextEmbeddingModel()
        self.centroids: Dict[str, np.ndarray] = {}

        if taxonomy and taxonomy.centroids:
            self.load_taxonomy(taxonomy)

    def load_taxonomy(self, taxonomy: SymbolTaxonomy) -> None:
        self.taxonomy = taxonomy
        self.centroids = {
            k: np.array(v, dtype=np.float32) for k, v in taxonomy.centroids.items()
        }

    def canonicalize(
        self,
        raw_symbol: str,
        docstring: str = "",
        param_schema_hash: str = "",
        param_schema: Dict[str, Any] | None = None,
        event_type: str = "tool_call",
    ) -> str:
        """Assign canonical symbol to raw action name (PRD §12.1, §12.6)."""
        # If delegate event, canonicalize as delegate(role)
        if event_type == "delegate":
            return self.rule_engine.canonicalize(raw_symbol, event_type="delegate")

        # If no centroids registered, fallback to deterministic rule engine
        if not self.centroids:
            return self.rule_engine.canonicalize(raw_symbol, event_type=event_type)

        # Build embedding input text (PRD §12.3)
        hsh = param_schema_hash or (
            json.dumps(sorted(param_schema.keys())) if param_schema else ""
        )
        text = f"{raw_symbol}\n{docstring or ''}\n{hsh}"

        vec = self.embedder.embed([text])[0]
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm

        # Nearest centroid lookup via cosine similarity
        best_symbol = None
        best_sim = -1.0

        for sym, centroid in self.centroids.items():
            sim = float(np.dot(vec, centroid))
            if sim > best_sim:
                best_sim = sim
                best_symbol = sym

        if best_sim >= self.similarity_threshold and best_symbol:
            return best_symbol

        # Unknown symbol handling (PRD §12.6)
        raw_hash = hashlib.sha256(raw_symbol.encode("utf-8")).hexdigest()[:8]
        return f"unknown_symbol_{raw_hash}"
