"""
Tests for Semantic Action Clustering & Nearest-Centroid Assignment (PRD §12).
"""

import numpy as np
import pytest

from trace.abstraction.clustering import (
    AgglomerativeClusterer,
    SemanticAbstractionEngine,
    SimpleTextEmbeddingModel,
    SymbolTaxonomy,
)


def test_simple_ngram_embedder_determinism():
    embedder = SimpleTextEmbeddingModel(dimensions=64)
    texts = ["web_search query string", "execute_code bash command"]
    v1 = embedder.embed(texts)
    v2 = embedder.embed(texts)

    assert v1.shape == (2, 64)
    np.testing.assert_allclose(v1, v2)
    # Check unit normalization
    assert np.isclose(np.linalg.norm(v1[0]), 1.0)
    assert np.isclose(np.linalg.norm(v1[1]), 1.0)


def test_agglomerative_clustering_groups_synonyms():
    embedder = SimpleTextEmbeddingModel(dimensions=128)
    clusterer = AgglomerativeClusterer(distance_threshold=0.45, embedder=embedder)

    raw_items = [
        ("web_search", "search the web", "hash_web"),
        ("search_web", "query the search engine", "hash_web"),
        ("google_search", "search google for pages", "hash_web"),
        ("file_delete", "delete a file from disk", "hash_file"),
        ("delete_file", "remove a file from disk", "hash_file"),
    ]

    taxonomy = clusterer.fit(raw_items, taxonomy_version=1)
    assert taxonomy.taxonomy_version == 1
    assert len(taxonomy.canonical_symbols) >= 2

    # Check centroids are populated
    for sym in taxonomy.canonical_symbols:
        assert sym in taxonomy.centroids
        vec = np.array(taxonomy.centroids[sym])
        assert np.isclose(np.linalg.norm(vec), 1.0, atol=1e-3)


def test_nearest_centroid_canonicalization():
    embedder = SimpleTextEmbeddingModel(dimensions=128)
    clusterer = AgglomerativeClusterer(distance_threshold=0.45, embedder=embedder)

    raw_items = [
        ("web_search", "search the internet", "h_search"),
        ("file_delete", "delete and remove record", "h_delete"),
    ]
    taxonomy = clusterer.fit(raw_items)
    engine = SemanticAbstractionEngine(taxonomy=taxonomy, similarity_threshold=0.5, embedder=embedder)

    # Test exact / near match
    mapped = engine.canonicalize("web_search", docstring="search the internet", param_schema_hash="h_search")
    assert "search" in mapped

    # Test delegate pass-through
    assert engine.canonicalize("reviewer", event_type="delegate") == "delegate(reviewer)"
    assert engine.canonicalize("delegate(coder)", event_type="delegate") == "delegate(coder)"

    # Test unknown symbol fallback
    low_sim_engine = SemanticAbstractionEngine(taxonomy=taxonomy, similarity_threshold=0.99, embedder=embedder)
    unknown = low_sim_engine.canonicalize("completely_alien_operation_xyz", docstring="unrelated")
    assert unknown.startswith("unknown_symbol_")
