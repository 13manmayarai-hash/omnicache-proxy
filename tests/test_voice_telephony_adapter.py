"""
Comprehensive Test Suite for OmniCache Voice & Telephony Agent Adapter (v2.9.6).
Verifies:
1. Speech-to-Text transcript normalization & filler word stripping.
2. Acoustic annotations removal & stutter syllable collapse.
3. Telephony caller metadata canonicalization (Twilio Call SIDs, Phone numbers, ISO Timestamps, Room IDs).
4. Multi-turn dialogue history compaction for phone bot turns.
5. Telephony sub-millisecond fast-path intent matching.
6. End-to-end OpenAI gateway integration & cross-caller prompt cache hits.
7. End-to-end Anthropic gateway integration & LiveKit headers.
8. WebSocket real-time telemetry event streaming for voice.
9. CLI init preset generation for voice agents.
"""

import json
import os
import pytest
from unittest.mock import patch, AsyncMock
from starlette.testclient import TestClient

from core.telephony_filter import TelephonyFilter, telephony_filter
from server.gateway import app, cache_instance, METRICS_LEDGER
from server.cli import run_init


@pytest.fixture
def client():
    return TestClient(app)


# -----------------------------------------------------------------------------
# 1. Unit Tests: Transcript Normalization & Disfluency Stripping
# -----------------------------------------------------------------------------
def test_transcript_filler_stripping():
    tf = TelephonyFilter(strip_fillers=True)

    raw_text = "uh, yeah, um, I would like to check my account balance, you know?"
    cleaned, removed = tf.normalize_transcript(raw_text)

    assert removed >= 3
    assert "uh" not in cleaned.lower().split()
    assert "um" not in cleaned.lower().split()
    assert "you know" not in cleaned.lower()
    assert cleaned.startswith("Yeah")
    assert cleaned.endswith("?")


def test_transcript_guard_never_strips_to_empty():
    tf = TelephonyFilter(strip_fillers=True)

    # If the user literally just said a standalone filler or confirmation
    raw = "uh"
    cleaned, removed = tf.normalize_transcript(raw)
    assert len(cleaned) > 0

    raw_confirm = "uh-huh"
    cleaned_c, _ = tf.normalize_transcript(raw_confirm)
    assert len(cleaned_c) > 0


def test_acoustic_annotations_and_stutter_collapse():
    tf = TelephonyFilter()

    raw_text = "[clears throat] w-what time does the branch open? [pause] (laughter)"
    cleaned, removed = tf.normalize_transcript(raw_text)

    assert removed >= 3
    assert "[clears throat]" not in cleaned
    assert "[pause]" not in cleaned
    assert "(laughter)" not in cleaned
    assert "w-what" not in cleaned
    assert "What time does the branch open?" in cleaned


# -----------------------------------------------------------------------------
# 2. Unit Tests: Caller Session Metadata Canonicalization
# -----------------------------------------------------------------------------
def test_caller_metadata_canonicalization():
    tf = TelephonyFilter(canonicalize_metadata=True)

    # In telephony systems, each inbound call gets unique session IDs in prompt
    sys_call_1 = (
        "You are an AI receptionist for Acme Clinic.\n"
        "Caller Phone: +14155552671\n"
        "Twilio Call SID: CA48f98c89b213456789abcdef01234567\n"
        "Call Time: 2026-09-07T05:22:15.123Z\n"
        "Room Token: room_voice_774912"
    )

    sys_call_2 = (
        "You are an AI receptionist for Acme Clinic.\n"
        "Caller Phone: +12125559988\n"
        "Twilio Call SID: CAbbaabbccddeeff001122334455667788\n"
        "Call Time: 2026-09-07T06:14:50.000Z\n"
        "Room Token: room_voice_110293"
    )

    canon_1, meta_map_1, count_1 = tf.canonicalize_telephony_metadata(sys_call_1)
    canon_2, meta_map_2, count_2 = tf.canonicalize_telephony_metadata(sys_call_2)

    assert count_1 >= 4
    assert count_2 >= 4
    assert "<CALL_SID>" in canon_1
    assert "<CALLER_PHONE>" in canon_1
    assert "<TIMESTAMP>" in canon_1
    assert "<SESSION_ID>" in canon_1

    # CRITICAL INVARIANT: The canonicalized prompts from two distinct phone calls
    # must be 100% IDENTICAL to trigger prompt cache hits!
    assert canon_1 == canon_2


# -----------------------------------------------------------------------------
# 3. Unit Tests: Multi-Turn Voice History Compaction
# -----------------------------------------------------------------------------
def test_multi_turn_voice_history_compaction():
    tf = TelephonyFilter(max_active_turns=4)

    messages = [
        {"role": "system", "content": "You are a customer service assistant."},
        {"role": "user", "content": "What is my order status?"},
        {"role": "assistant", "content": "Your order #1234 has been shipped and is currently in transit to your local distribution center via FedEx Express."},
        {"role": "user", "content": "ok"},
        {"role": "assistant", "content": "It is scheduled to arrive tomorrow afternoon by 4:00 PM. Would you like a tracking notification link sent to your mobile phone via SMS?"},
        {"role": "user", "content": "yeah"},
        {"role": "assistant", "content": "Great, what number should I use?"},
        {"role": "user", "content": "Use 555-0199"},
        {"role": "assistant", "content": "SMS confirmed."},
        {"role": "user", "content": "Thanks, anything else?"}
    ]

    compacted, tokens_saved = tf.compact_voice_history(messages, max_active_turns=4)

    assert tokens_saved > 0
    assert len(compacted) == len(messages)
    # The historical "ok" at index 3 must be compacted to [Caller acknowledged]
    assert compacted[3]["content"] == "[Caller acknowledged]"
    # The historical "yeah" at index 5 must be compacted to [Caller acknowledged]
    assert compacted[5]["content"] == "[Caller acknowledged]"
    # The recent turns (last 4) must be completely untouched
    assert compacted[-1]["content"] == "Thanks, anything else?"
    assert compacted[-2]["content"] == "SMS confirmed."


# -----------------------------------------------------------------------------
# 4. Unit Tests: Sub-Millisecond Fast-Path Intent Matching
# -----------------------------------------------------------------------------
def test_fast_path_instant_replies():
    tf = TelephonyFilter(fast_path=True)

    # 1. Line check / latency check
    reply_1 = tf.match_fast_path_intent("can you hear me?")
    assert reply_1 is not None
    assert "hear you clearly" in reply_1

    # 2. Hold request
    reply_2 = tf.match_fast_path_intent("hold on a second please")
    assert reply_2 is not None
    assert "Take your time" in reply_2

    # 3. Repeat request with conversation history
    history = [
        {"role": "user", "content": "What was the total?"},
        {"role": "assistant", "content": "The total amount due is $42.50."}
    ]
    reply_3 = tf.match_fast_path_intent("what did you say?", conversation_history=history)
    assert reply_3 is not None
    assert "The total amount due is $42.50" in reply_3


# -----------------------------------------------------------------------------
# 5. Integration Tests: End-to-End OpenAI Gateway Voice Mode
# -----------------------------------------------------------------------------
def test_openai_gateway_voice_fast_path(client):
    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "You are a phone assistant."},
            {"role": "user", "content": "Are you still there?"}
        ]
    }

    resp = client.post(
        "/v1/chat/completions",
        json=payload,
        headers={
            "Authorization": "Bearer test-key",
            "X-OmniCache-Voice-Mode": "true"
        }
    )

    assert resp.status_code == 200
    data = resp.json()
    assert resp.headers.get("X-Cache-Status") == "HIT_EXACT"
    assert resp.headers.get("X-OmniCache-Fast-Path") == "telephony"
    assert "hear you clearly" in data["choices"][0]["message"]["content"]


def test_openai_gateway_cross_caller_cache_hit(client):
    """
    Verifies that two calls with distinct phone numbers and Call SIDs
    hit the EXACT same prompt cache after telephony metadata canonicalization!
    """
    call_1_payload = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "system",
                "content": "You are Acme phone agent. Call SID: CA11112222333344445555666677778888, Caller: +14155550199"
            },
            {
                "role": "user",
                "content": "uh, what are your office hours today?"
            }
        ]
    }

    mock_resp = {
        "id": "chatcmpl_mock_hours",
        "object": "chat.completion",
        "created": 1725700000,
        "model": "gpt-4o",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "We are open 9am to 5pm Monday through Friday."}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 15, "total_tokens": 55}
    }

    # First call: primes the cache
    with patch("server.upstream.upstream_client.forward_non_stream", new=AsyncMock(return_value=(200, mock_resp, {}))):
        res1 = client.post(
            "/v1/chat/completions",
            json=call_1_payload,
            headers={"Authorization": "Bearer test-key", "X-OmniCache-Voice-Mode": "true"}
        )
    assert res1.status_code == 200
    assert res1.headers.get("X-OmniCache-Voice-Filtered") == "true"

    # Second call: completely DIFFERENT caller phone number and DIFFERENT Call SID!
    # And caller says "What are your office hours today?" (without "uh")
    call_2_payload = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "system",
                "content": "You are Acme phone agent. Call SID: CA99998888777766665555444433332222, Caller: +12125559900"
            },
            {
                "role": "user",
                "content": "What are your office hours today?"
            }
        ]
    }

    res2 = client.post(
        "/v1/chat/completions",
        json=call_2_payload,
        headers={"Authorization": "Bearer test-key", "X-OmniCache-Voice-Mode": "true"}
    )

    assert res2.status_code == 200
    # Must be an instant L1 HIT_EXACT!
    assert res2.headers.get("X-Cache-Status") == "HIT_EXACT"
    assert res2.json()["choices"][0]["message"]["content"] == "We are open 9am to 5pm Monday through Friday."


# -----------------------------------------------------------------------------
# 6. Integration Tests: Anthropic Gateway & LiveKit Telephony Header
# -----------------------------------------------------------------------------
def test_anthropic_gateway_livekit_voice_mode(client):
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "system": "You are a voice assistant in room room_voice_774912 for caller +14155552671.",
        "messages": [
            {"role": "user", "content": "Can you hear me clearly?"}
        ]
    }

    resp = client.post(
        "/v1/messages",
        json=payload,
        headers={
            "x-api-key": "test-key",
            "X-OmniCache-Telephony": "livekit"
        }
    )

    assert resp.status_code == 200
    assert resp.headers.get("X-Cache-Status") == "HIT_EXACT"
    assert resp.headers.get("X-OmniCache-Fast-Path") == "telephony"
    data = resp.json()
    assert data["type"] == "message"
    assert "hear you clearly" in data["content"][0]["text"]


# -----------------------------------------------------------------------------
# 7. Integration Tests: WebSocket Telemetry Event for Voice
# -----------------------------------------------------------------------------
def test_websocket_telephony_filtered_event(client):
    with client.websocket_connect("/ws") as ws:
        init_data = ws.receive_json()
        assert init_data.get("type") == "connection_established"

        # Fire a request that gets voice filtered
        payload = {
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "Twilio Call SID: CA48f98c89b213456789abcdef01234567"},
                {"role": "user", "content": "uh, um, I need assistance"}
            ]
        }
        mock_resp = {
            "id": "chatcmpl_mock_ws",
            "object": "chat.completion",
            "created": 1725700000,
            "model": "gpt-4o",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "How can I help you?"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
        }

        with patch("server.upstream.upstream_client.forward_non_stream", new=AsyncMock(return_value=(200, mock_resp, {}))):
            client.post("/v1/chat/completions", json=payload, headers={"Authorization": "Bearer test-key", "X-OmniCache-Voice-Mode": "true"})

        # Receive WebSocket event
        event = ws.receive_json()
        assert event.get("type") == "event"
        assert event.get("event_type") == "telephony_filtered"
        assert event["data"]["fillers_stripped"] >= 2
        assert event["data"]["metadata_canonicalized"] >= 1


# -----------------------------------------------------------------------------
# 8. Integration Tests: CLI init Voice Preset
# -----------------------------------------------------------------------------
def test_cli_init_voice_preset(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_init(agent="voice", show_only=False)

    voice_file = tmp_path / ".omnicache-voice.json"
    assert voice_file.exists()

    with open(voice_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["omnicache"]["voice_mode"] is True
    assert data["omnicache"]["strip_fillers"] is True
    assert data["omnicache"]["canonicalize_telephony_metadata"] is True
    assert "livekit_adapter" in data
    assert "twilio_media_streams" in data
