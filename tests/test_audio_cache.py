"""
Multi-Modal Raw Audio Caching & Perceptual Acoustic Matching Test Suite.
Verifies pure-Python spectral sub-band hashing (aHash), VAD silence trimming,
acoustic invariance to gain/noise, OpenAI / Anthropic gateway integration, and CLI presets.
"""

import base64
import math
import struct
import json
import pytest
from unittest.mock import patch, MagicMock
from starlette.testclient import TestClient

from core.audio_cache import AudioPerceptualHasher, AudioCache, audio_cache
from server.gateway import app, METRICS_LEDGER


def create_synthetic_audio(freq: float = 440.0, gain: float = 1.0, noise: float = 0.0, silence_ms: int = 100) -> bytes:
    """Generates synthetic 16kHz PCM16 mono audio bytes."""
    samples = []
    # Leading silence
    silence_samples = int(16000 * (silence_ms / 1000.0))
    samples.extend([0] * silence_samples)
    
    # Active waveform (0.5s = 8000 samples)
    for i in range(8000):
        t = i / 16000.0
        val = math.sin(2 * math.pi * freq * t) + 0.4 * math.sin(2 * math.pi * 2 * freq * t)
        val = int(val * 16000 * gain)
        if noise:
            val += int(math.sin(i * 123.4) * noise * 2000)
        samples.append(max(-32768, min(32767, val)))
        
    # Trailing silence
    samples.extend([0] * silence_samples)
    return struct.pack(f"<{len(samples)}h", *samples)


def create_synthetic_wav_bytes(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """Wraps PCM16 samples in standard 44-byte RIFF/WAVE header."""
    num_channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * num_channels * (bits_per_sample // 8)
    block_align = num_channels * (bits_per_sample // 8)
    data_size = len(pcm_bytes)
    riff_size = 36 + data_size

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", riff_size, b"WAVE",
        b"fmt ", 16, 1, num_channels, sample_rate, byte_rate, block_align, bits_per_sample,
        b"data", data_size
    )
    return header + pcm_bytes


def test_audio_bytes_extraction():
    pcm = create_synthetic_audio(440.0)
    b64_str = base64.b64encode(pcm).decode("utf-8")
    data_uri = f"data:audio/wav;base64,{b64_str}"

    assert AudioPerceptualHasher.extract_audio_bytes(data_uri) == pcm
    assert AudioPerceptualHasher.extract_audio_bytes(b64_str) == pcm
    assert AudioPerceptualHasher.extract_audio_bytes("") is None


def test_parse_wav_samples():
    pcm = create_synthetic_audio(440.0)
    wav = create_synthetic_wav_bytes(pcm)

    samples_from_wav = AudioPerceptualHasher.parse_wav_samples(wav)
    samples_from_pcm = AudioPerceptualHasher.parse_wav_samples(pcm)

    assert len(samples_from_wav) > 0
    assert len(samples_from_pcm) > 0
    assert samples_from_wav == samples_from_pcm


def test_vad_silence_trimming():
    # Audio with 300ms leading and trailing silence
    pcm_with_silence = create_synthetic_audio(440.0, gain=1.0, silence_ms=300)
    samples = AudioPerceptualHasher.parse_wav_samples(pcm_with_silence)
    
    trimmed = AudioPerceptualHasher.vad_trim_silence(samples)
    assert len(trimmed) < len(samples)
    assert len(trimmed) >= 4000


def test_compute_spectral_fingerprint_deterministic():
    pcm = create_synthetic_audio(440.0)
    h1 = AudioPerceptualHasher.compute_spectral_fingerprint64(pcm)
    h2 = AudioPerceptualHasher.compute_spectral_fingerprint64(pcm)

    assert len(h1) == 16
    assert h1 == h2
    assert AudioPerceptualHasher.hamming_distance(h1, h2) == 0


def test_acoustic_invariance_volume_and_noise():
    # Base utterance
    pcm1 = create_synthetic_audio(freq=500.0, gain=1.0, noise=0.0, silence_ms=200)
    # Same utterance spoken softer with slight mic noise and different pause length
    pcm1_var = create_synthetic_audio(freq=500.0, gain=0.7, noise=0.08, silence_ms=50)

    h1 = AudioPerceptualHasher.compute_spectral_fingerprint64(pcm1)
    h1_var = AudioPerceptualHasher.compute_spectral_fingerprint64(pcm1_var)

    dist = AudioPerceptualHasher.hamming_distance(h1, h1_var)
    assert dist <= 6, f"Expected acoustic match with dist <= 6, got {dist}"


def test_acoustic_discriminability():
    # Two very distinct acoustic frequencies
    pcm1 = create_synthetic_audio(freq=300.0, gain=1.0)
    pcm2 = create_synthetic_audio(freq=2400.0, gain=1.0)

    h1 = AudioPerceptualHasher.compute_spectral_fingerprint64(pcm1)
    h2 = AudioPerceptualHasher.compute_spectral_fingerprint64(pcm2)

    dist = AudioPerceptualHasher.hamming_distance(h1, h2)
    assert dist > 6, f"Expected distinct audio to have dist > 6, got {dist}"


def test_audio_cache_store_and_lookup():
    test_cache = AudioCache(max_hamming_distance=6)
    pcm1 = create_synthetic_audio(freq=600.0, gain=1.0)
    h1 = AudioPerceptualHasher.compute_spectral_fingerprint64(pcm1)

    reply = {"id": "reply_123", "choices": [{"message": {"content": "Hello!"}}]}
    test_cache.store_audio(h1, "greet", reply, tokens_saved=300)

    # Lookup identical
    hit, res, dist = test_cache.lookup_audio(h1, "greet")
    assert hit is True
    assert dist == 0
    assert res == reply

    # Lookup with acoustic variation (different gain)
    pcm1_var = create_synthetic_audio(freq=600.0, gain=0.75, noise=0.05)
    h1_var = AudioPerceptualHasher.compute_spectral_fingerprint64(pcm1_var)
    hit_var, res_var, dist_var = test_cache.lookup_audio(h1_var, "greet")
    assert hit_var is True
    assert dist_var <= 6

    # Lookup with different text prompt context
    hit_diff_text, _, _ = test_cache.lookup_audio(h1, "farewell")
    assert hit_diff_text is False

    stats = test_cache.stats()
    assert stats["audio_hits"] == 2
    assert stats["audio_tokens_saved"] == 600


def test_openai_chat_completions_input_audio_e2e():
    client = TestClient(app)
    pcm = create_synthetic_audio(freq=750.0, gain=1.0)
    wav = create_synthetic_wav_bytes(pcm)
    b64_audio = base64.b64encode(wav).decode("utf-8")

    import time
    prompt_txt = f"What is my account balance? (session {time.time()})"

    req_payload = {
        "model": "gpt-4o-audio-preview",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_txt},
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": b64_audio,
                            "format": "wav"
                        }
                    }
                ]
            }
        ]
    }

    mock_resp = {
        "id": "chatcmpl_mock_audio_1",
        "object": "chat.completion",
        "model": "gpt-4o-audio-preview",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Your balance is $4,500.00."
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {"prompt_tokens": 180, "completion_tokens": 60, "total_tokens": 240}
    }

    with patch("server.upstream.upstream_client.forward_non_stream", return_value=(200, mock_resp, {"content-type": "application/json"})):
        # Turn 1: MISS -> upstream forwarded and stored in audio cache
        res1 = client.post("/v1/chat/completions", json=req_payload)
        assert res1.status_code == 200
        assert res1.headers.get("X-OmniCache-Decision") == "MISS"

    # Turn 2: Acoustic variant (volume gain 0.8, slight noise)
    pcm_var = create_synthetic_audio(freq=750.0, gain=0.8, noise=0.04)
    wav_var = create_synthetic_wav_bytes(pcm_var)
    b64_audio_var = base64.b64encode(wav_var).decode("utf-8")

    req_payload_var = {
        "model": "gpt-4o-audio-preview",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_txt},
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": b64_audio_var,
                            "format": "wav"
                        }
                    }
                ]
            }
        ]
    }

    # Turn 2: HIT_AUDIO served locally in sub-millisecond time!
    res2 = client.post("/v1/chat/completions", json=req_payload_var)
    assert res2.status_code == 200
    assert res2.headers.get("X-Cache-Status") == "HIT_AUDIO"
    assert res2.headers.get("X-OmniCache-Decision") == "HIT"
    assert "HIT_AUDIO_SPECTRAL" in res2.headers.get("X-OmniCache-Reason")
    assert res2.json()["choices"][0]["message"]["content"] == "Your balance is $4,500.00."


def test_anthropic_messages_audio_block_e2e():
    client = TestClient(app)
    pcm = create_synthetic_audio(freq=880.0, gain=1.0)
    b64_audio = base64.b64encode(pcm).decode("utf-8")

    import time
    prompt_txt = f"Transcribe and analyze this voice note (session {time.time()})"

    anthropic_payload = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_txt},
                    {
                        "type": "audio",
                        "source": {
                            "type": "base64",
                            "media_type": "audio/wav",
                            "data": b64_audio
                        }
                    }
                ]
            }
        ]
    }

    mock_resp = {
        "id": "msg_anthropic_audio_mock",
        "type": "message",
        "role": "assistant",
        "model": "claude-3-5-sonnet-20241022",
        "content": [{"type": "text", "text": "The voice note discusses the Q3 product release schedule."}],
        "usage": {"input_tokens": 150, "output_tokens": 80}
    }

    with patch("server.upstream.upstream_client.forward_anthropic_messages", return_value=(200, mock_resp, {"content-type": "application/json"})):
        # Turn 1: MISS
        res1 = client.post("/v1/messages", json=anthropic_payload)
        assert res1.status_code == 200
        assert res1.headers.get("X-OmniCache-Decision") == "MISS"

    # Turn 2: Query with near-identical audio
    pcm_var = create_synthetic_audio(freq=880.0, gain=0.85)
    b64_audio_var = base64.b64encode(pcm_var).decode("utf-8")
    anthropic_payload_var = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_txt},
                    {
                        "type": "audio",
                        "source": {
                            "type": "base64",
                            "media_type": "audio/wav",
                            "data": b64_audio_var
                        }
                    }
                ]
            }
        ]
    }

    res2 = client.post("/v1/messages", json=anthropic_payload_var)
    assert res2.status_code == 200
    assert res2.headers.get("X-Cache-Status") == "HIT_AUDIO"
    assert res2.headers.get("X-OmniCache-Decision") == "HIT"
    assert "HIT_AUDIO_SPECTRAL" in res2.headers.get("X-OmniCache-Reason")
    assert "Q3 product release schedule" in res2.json()["content"][0]["text"]


def test_cli_audio_preset():
    from server.cli import run_init
    import os
    run_init(agent="audio", show_only=False)

    preset_path = os.path.join(os.getcwd(), ".omnicache-audio.json")
    assert os.path.exists(preset_path)
    with open(preset_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["omnicache"]["audio_cache"] is True
    assert data["omnicache"]["spectral_subband_hasher"] == "aHash-64"
    assert data["omnicache"]["max_hamming_distance"] == 6
