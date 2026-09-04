"""
Multi-Modal Visual Deduplication & Benchmark Suite for OmniCache.
Verifies perceptual dHash computation, Hamming distance tolerance,
multi-modal payload extraction, and sub-millisecond screenshot deduplication.
"""

import base64
import hashlib
import time
import pytest
from starlette.testclient import TestClient

from core.vision_cache import VisionPerceptualHasher, VisionCache, vision_cache
from server.gateway import app, METRICS_LEDGER

def create_synthetic_image_bytes(pattern_id: int, noise_bits: int = 0) -> bytes:
    """
    Generates a deterministic synthetic 72-byte pseudo-image buffer with distinct gradients.
    """
    base = bytearray()
    for i in range(3):
        base.extend(hashlib.sha256(f"pattern-seed-{pattern_id}-{i}".encode()).digest())
    base = bytearray(base[:72])
    if noise_bits > 0:
        for b in range(noise_bits):
            # Subtle perturbation
            base[b] = (base[b] ^ 0x01)
    return bytes(base)


def test_perceptual_hasher_deterministic_and_hamming():
    img1 = create_synthetic_image_bytes(1, noise_bits=0)
    img1_noise = create_synthetic_image_bytes(1, noise_bits=1)
    img2 = create_synthetic_image_bytes(999, noise_bits=0)

    hash1 = VisionPerceptualHasher.compute_dhash64(img1)
    hash1_noise = VisionPerceptualHasher.compute_dhash64(img1_noise)
    hash2 = VisionPerceptualHasher.compute_dhash64(img2)

    assert len(hash1) == 16
    assert VisionPerceptualHasher.hamming_distance(hash1, hash1) == 0
    
    # Near identical noise should have small Hamming distance <= 4
    dist_noise = VisionPerceptualHasher.hamming_distance(hash1, hash1_noise)
    assert dist_noise <= 4

    # Completely different pattern should have larger Hamming distance
    dist_diff = VisionPerceptualHasher.hamming_distance(hash1, hash2)
    assert dist_diff > 4


def test_vision_cache_payload_extraction_openai_and_anthropic():
    raw = create_synthetic_image_bytes(5)
    b64_str = base64.b64encode(raw).decode("utf-8")
    data_uri = f"data:image/png;base64,{b64_str}"

    vcache = VisionCache(max_hamming_distance=4)

    # OpenAI format payload
    openai_payload = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this UI error:"},
                    {"type": "image_url", "image_url": {"url": data_uri}}
                ]
            }
        ]
    }
    extracted_oa = vcache.extract_images_from_payload(openai_payload)
    assert len(extracted_oa) == 1
    dhash_oa, prompt_oa = extracted_oa[0]
    assert len(dhash_oa) == 16
    assert "Describe this UI error:" in prompt_oa

    # Anthropic format payload
    anthropic_payload = {
        "model": "claude-3-7-sonnet",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze this crash stack:"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_str}}
                ]
            }
        ]
    }
    extracted_anth = vcache.extract_images_from_payload(anthropic_payload)
    assert len(extracted_anth) == 1
    dhash_anth, prompt_anth = extracted_anth[0]
    assert dhash_anth == dhash_oa
    assert "Analyze this crash stack:" in prompt_anth


def test_vision_cache_hit_miss_and_prompt_isolation():
    vcache = VisionCache(max_hamming_distance=4)
    img_bytes = create_synthetic_image_bytes(42)
    img_bytes_slight_mod = create_synthetic_image_bytes(42, noise_bits=1)
    img_diff = create_synthetic_image_bytes(888)

    hash1 = VisionPerceptualHasher.compute_dhash64(img_bytes)
    hash1_mod = VisionPerceptualHasher.compute_dhash64(img_bytes_slight_mod)
    hash_diff = VisionPerceptualHasher.compute_dhash64(img_diff)

    sample_response = {
        "id": "chatcmpl-vision-123",
        "choices": [{"message": {"role": "assistant", "content": "Visual inspection: Error 500 in auth handler."}}]
    }

    # Store first perception
    vcache.store_image(hash1, "Inspect error", sample_response)

    # Lookup identical
    hit, resp, dist = vcache.lookup_image(hash1, "Inspect error")
    assert hit is True
    assert dist == 0
    assert resp["choices"][0]["message"]["content"] == "Visual inspection: Error 500 in auth handler."

    # Lookup slightly modified screenshot (perceptually similar)
    hit_mod, resp_mod, dist_mod = vcache.lookup_image(hash1_mod, "Inspect error")
    assert hit_mod is True
    assert dist_mod <= 4

    # Lookup different prompt text -> should MISS (prompt isolation)
    hit_p, _, _ = vcache.lookup_image(hash1, "Different prompt about color")
    assert hit_p is False

    # Lookup different image -> should MISS
    hit_diff, _, _ = vcache.lookup_image(hash_diff, "Inspect error")
    assert hit_diff is False


def test_perceptual_hasher_performance_benchmark():
    """Benchmark perceptual hashing throughput (target > 5,000 images/sec)."""
    raw_images = [create_synthetic_image_bytes(i) for i in range(100)]
    
    start_time = time.perf_counter()
    iterations = 2000
    for i in range(iterations):
        img = raw_images[i % 100]
        VisionPerceptualHasher.compute_dhash64(img)
    elapsed = time.perf_counter() - start_time
    
    per_hash_ms = (elapsed / iterations) * 1000.0
    hashes_per_sec = iterations / elapsed

    print(f"\n[Visual Benchmark] Latency: {per_hash_ms:.4f}ms/image | Throughput: {hashes_per_sec:,.0f} hashes/sec")
    assert per_hash_ms < 0.2  # Must be strictly under 0.2ms


def test_e2e_gateway_vision_cache_hit(monkeypatch):
    client = TestClient(app)
    raw = create_synthetic_image_bytes(77)
    b64_str = base64.b64encode(raw).decode("utf-8")
    data_uri = f"data:image/png;base64,{b64_str}"

    req_payload = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is shown in this terminal screenshot?"},
                    {"type": "image_url", "image_url": {"url": data_uri}}
                ]
            }
        ]
    }

    # Pre-populate vision cache
    dhash = VisionPerceptualHasher.compute_dhash64(raw)
    mock_resp = {
        "id": "chatcmpl-test-v1",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "gpt-4o",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "The terminal shows a successful build."}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 800, "completion_tokens": 15, "total_tokens": 815}
    }
    vision_cache.store_image(dhash, "What is shown in this terminal screenshot?", mock_resp)

    # Gateway request
    res = client.post("/v1/chat/completions", json=req_payload)
    assert res.status_code == 200
    assert res.headers.get("X-OmniCache-Decision") == "HIT"
    assert "perceptual visual match" in res.headers.get("X-OmniCache-Reason", "").lower()
    data = res.json()
    assert "The terminal shows a successful build." in data["choices"][0]["message"]["content"]
