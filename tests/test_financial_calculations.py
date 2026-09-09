"""
Unit test suite for Financial Telemetry, Token Accounting, and Model Pricing Resolution.
Validates:
1. Exact mathematical parity against official provider pricing.
2. Robust longest-prefix resolution on dated model snapshots (e.g. gpt-4o-2024-08-06).
3. Distinctions between prompt tokens, completion tokens, and tool compaction tokens.
"""

import pytest
from server.upstream import UpstreamClient
from core.config import MODEL_PRICING


def test_pricing_table_parity_with_official_rates():
    """Validates baseline prices against official provider published rates."""
    assert MODEL_PRICING["gpt-4o"]["input"] == 2.50
    assert MODEL_PRICING["gpt-4o"]["output"] == 10.00
    assert MODEL_PRICING["gpt-4o-mini"]["input"] == 0.15
    assert MODEL_PRICING["gpt-4o-mini"]["output"] == 0.60
    assert MODEL_PRICING["claude-3-5-sonnet"]["input"] == 3.00
    assert MODEL_PRICING["claude-3-5-sonnet"]["output"] == 15.00
    assert MODEL_PRICING["claude-3-5-haiku"]["input"] == 0.80
    assert MODEL_PRICING["claude-3-5-haiku"]["output"] == 4.00
    assert MODEL_PRICING["gemini-1.5-pro"]["input"] == 1.25
    assert MODEL_PRICING["gemini-1.5-pro"]["output"] == 5.00


def test_resolve_model_pricing_dated_snapshots():
    """Ensures versioned/dated model IDs resolve to the correct model family rather than default."""
    p_gpt4o = UpstreamClient.resolve_model_pricing("gpt-4o-2024-08-06")
    assert p_gpt4o["input"] == 2.50
    assert p_gpt4o["output"] == 10.00

    p_mini = UpstreamClient.resolve_model_pricing("gpt-4o-mini-2024-07-18")
    assert p_mini["input"] == 0.15
    assert p_mini["output"] == 0.60

    p_sonnet = UpstreamClient.resolve_model_pricing("claude-3-5-sonnet-latest")
    assert p_sonnet["input"] == 3.00
    assert p_sonnet["output"] == 15.00

    p_haiku = UpstreamClient.resolve_model_pricing("claude-3-5-haiku-latest")
    assert p_haiku["input"] == 0.80
    assert p_haiku["output"] == 4.00

    p_gemini = UpstreamClient.resolve_model_pricing("gemini-1.5-flash-latest")
    assert p_gemini["input"] == 0.075


def test_calculate_savings_mathematical_precision():
    """
    Tests mathematical accuracy:
    10,000 prompt tokens + 2,000 completion tokens on claude-3-5-sonnet:
    Input: (10,000 / 1,000,000) * $3.00 = $0.0300
    Output: (2,000 / 1,000,000) * $15.00 = $0.0300
    Total: $0.0600
    """
    savings = UpstreamClient.calculate_savings("claude-3-5-sonnet", 10_000, 2_000)
    assert pytest.approx(savings, 1e-6) == 0.060000


def test_compaction_savings_only_prices_input_tokens():
    """
    Validates that context compaction (compacted_tokens > 0, completion_tokens == 0)
    accurately calculates savings on prompt tokens only without inflating completion costs.
    """
    # 5,000 compacted prompt tokens on gpt-4o:
    # (5,000 / 1,000,000) * $2.50 = $0.0125
    savings = UpstreamClient.calculate_savings("gpt-4o", 5_000, 0)
    assert pytest.approx(savings, 1e-6) == 0.012500
