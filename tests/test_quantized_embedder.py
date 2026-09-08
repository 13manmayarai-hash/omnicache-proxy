"""
Unit and integration tests for Hardware-Accelerated Local Quantized Embedder (v3.0.1).
Verifies deterministic orthogonal projection, subword n-gram tokenization,
Int8/Int4 quantization, SIMD cosine similarity, AutoEmbedder integration,
and OpenAI-compatible /v1/embeddings gateway endpoints.
"""

import math
import pytest
from starlette.testclient import TestClient

from core.config import config
from core.quantized_embedder import QuantizedEmbedder, quantized_embedder
from core.embeddings import AutoEmbedder
from server.gateway import app


@pytest.fixture
def client():
    return TestClient(app)


def test_quantized_embedder_initialization():
    emb = QuantizedEmbedder(dimensions=256)
    assert emb.dimensions == 256
    assert emb.hardware_mode in ("arm_neon_int8", "avx2_int8", "simd_int8_accelerated")
    assert len(emb._weights) == 2048
    assert len(emb._weights[0]) == 256
    # Singleton access
    assert QuantizedEmbedder.get_instance() is not None


def test_deterministic_weights():
    # Weights should be 100% deterministic without any remote network access
    w1 = QuantizedEmbedder._generate_int8_weights(16, 64)
    w2 = QuantizedEmbedder._generate_int8_weights(16, 64)
    assert w1 == w2
    # Verify values are in signed int8 range [-128, 127]
    for row in w1:
        assert len(row) == 64
        for val in row:
            assert -128 <= val <= 127


def test_clean_and_tokenize():
    # Test camelCase splitting and punctuation handling
    tokens = QuantizedEmbedder.clean_and_tokenize("getUserById(record_id, authToken)")
    assert "user" in tokens
    assert "record" in tokens
    assert "auth" in tokens
    assert "token" in tokens


def test_float_vector_properties():
    emb = QuantizedEmbedder(dimensions=256)
    vec = emb.embed("OmniCache high performance semantic cache")
    assert len(vec) == 256
    # Unit normalization check
    norm_sq = sum(x * x for x in vec)
    assert math.isclose(norm_sq, 1.0, rel_tol=1e-3, abs_tol=1e-3)


def test_int8_vector_and_cosine_similarity():
    emb = QuantizedEmbedder(dimensions=256)
    t1 = "Fast local semantic caching with quantized embeddings"
    t2 = "Fast edge semantic cache using quantized vector embeddings"
    t3 = "Authentic Italian recipe for homemade pasta dough and pizza crust"

    v1 = emb.embed_int8(t1)
    v2 = emb.embed_int8(t2)
    v3 = emb.embed_int8(t3)

    assert isinstance(v1, bytes)
    assert len(v1) == 256

    # Identity
    assert emb.cosine_similarity_int8(v1, v1) == pytest.approx(1.0, 1e-4)

    # Symmetry
    assert emb.cosine_similarity_int8(v1, v2) == pytest.approx(emb.cosine_similarity_int8(v2, v1), 1e-4)

    # Semantic discrimination
    sim_related = emb.cosine_similarity_int8(v1, v2)
    sim_unrelated = emb.cosine_similarity_int8(v1, v3)
    assert sim_related > sim_unrelated
    assert sim_related > 0.50
    assert sim_unrelated < 0.35


def test_int4_nibble_packing_and_unpacking():
    emb = QuantizedEmbedder(dimensions=256)
    v_int8 = emb.embed_int8("OmniCache 4-bit nibble compression test")
    assert len(v_int8) == 256

    # Pack into int4 nibbles: 256 int8 values -> 128 bytes (8x compression vs float32)
    packed = emb.pack_int4(v_int8)
    assert isinstance(packed, bytes)
    assert len(packed) == 128

    # Unpack back to integer sequence
    unpacked = emb.unpack_int4(packed, 256)
    assert len(unpacked) == 256
    for val in unpacked:
        assert -8 <= val <= 7


def test_auto_embedder_integration():
    # Test AutoEmbedder with quantized backend
    auto_emb = AutoEmbedder(backend="quantized", dimensions=256)
    assert auto_emb.backend == "quantized"
    vec = auto_emb.embed("Distributed mesh state synchronization")
    assert len(vec) == 256
    norm = math.sqrt(sum(x * x for x in vec))
    assert math.isclose(norm, 1.0, rel_tol=1e-3)


def test_v1_embeddings_endpoint(client):
    # Single string input
    resp = client.post("/v1/embeddings", json={
        "model": "omnicache-quantized-256",
        "input": "Optimizing semantic caching for AI agents"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    assert len(data["data"]) == 1
    assert data["data"][0]["object"] == "embedding"
    assert data["data"][0]["index"] == 0
    assert len(data["data"][0]["embedding"]) == 256
    assert data["quantization"]["dimensions"] == 256
    assert data["quantization"]["offline"] is True

    # Batch list input
    resp_batch = client.post("/v1/embeddings", json={
        "model": "omnicache-quantized-256",
        "input": ["First prompt", "Second prompt", "Third prompt"]
    })
    assert resp_batch.status_code == 200
    data_batch = resp_batch.json()
    assert len(data_batch["data"]) == 3
    assert data_batch["usage"]["total_tokens"] >= 6


def test_v1_embeddings_quantized_formats(client):
    # Int8 format endpoint
    resp = client.post("/v1/embeddings/quantized", json={
        "input": ["Int8 quantized representation"],
        "format": "int8"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["format"] == "int8"
    assert data["quantization"]["bits"] == 8
    assert len(data["data"][0]["embedding"]) == 256
    for v in data["data"][0]["embedding"]:
        assert -128 <= v <= 127

    # Int4 format endpoint
    resp_i4 = client.post("/v1/embeddings/quantized", json={
        "input": ["Int4 packed nibbles"],
        "format": "int4"
    })
    assert resp_i4.status_code == 200
    data_i4 = resp_i4.json()
    assert data_i4["format"] == "int4"
    assert data_i4["quantization"]["bits"] == 4
    assert len(data_i4["data"][0]["embedding"]) == 128  # 128 bytes packed for 256-d


def test_v1_embeddings_validation(client):
    # Missing input
    resp = client.post("/v1/embeddings", json={"model": "omnicache-quantized-256"})
    assert resp.status_code == 400
    assert "input" in resp.json()["error"]["message"]


def test_embedder_stats_and_metrics():
    stats = quantized_embedder.stats()
    assert stats["engine"] == "QuantizedEmbedder"
    assert stats["version"] == "3.0.2"
    assert stats["dimensions"] == 256
    assert stats["quantization_bits"] == 8
    assert stats["offline_airgapped"] is True
    assert stats["zero_external_api"] is True
    assert "hardware_mode" in stats
    assert stats["embeddings_generated"] >= 1
