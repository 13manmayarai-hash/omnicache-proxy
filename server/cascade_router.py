"""
Adaptive Cost Arbitrage & Speculative Model Cascade Router.
Analyzes prompt complexity in <0.2ms and dynamically routes simple queries to ultra-cheap models
(Gemini 2.5 Flash / Haiku 3.5), saving up to 75% on non-cached upstream traffic.
"""

import re
import time
from typing import Dict, Any, Tuple, Optional

# Complexity Indicator Patterns
DEEP_REASONING_PATTERNS = re.compile(
    r"\b(prove|derive|architect|distributed|concurrency|race condition|deadlock|quantum|cryptographic|kernel|assembly|ast|bytecode|formal verification|differential equations|mathematical)\b",
    re.IGNORECASE
)

TRIVIAL_PATTERNS = re.compile(
    r"\b(translate|capitalize|uppercase|lowercase|format this|fix grammar|summarize in 1 sentence|spell check|json format|sort this list|extract email)\b",
    re.IGNORECASE
)

MODEL_TIERS = {
    "tier_1_economy": {
        "models": ["gemini-2.5-flash", "llama-3.3-70b", "mistral-small"],
        "cost_per_1m_input": 0.05,
        "cost_per_1m_output": 0.15
    },
    "tier_2_balanced": {
        "models": ["claude-3-5-haiku-20241022", "gpt-4o-mini"],
        "cost_per_1m_input": 0.80,
        "cost_per_1m_output": 4.00
    },
    "tier_3_frontier": {
        "models": ["claude-3-7-sonnet", "claude-3-5-sonnet-20241022", "gpt-4o", "o3-mini"],
        "cost_per_1m_input": 3.00,
        "cost_per_1m_output": 15.00
    }
}

class CascadeRouter:
    """Intelligent complexity classifier and cost-arbitrage routing engine."""
    def __init__(self):
        self.total_routed = 0
        self.downgraded_count = 0
        self.arbitrage_savings_usd = 0.0

    @staticmethod
    def classify_complexity(payload: Dict[str, Any]) -> float:
        """
        Computes complexity score from 0.0 (trivial) to 1.0 (deep multi-step reasoning) in <0.2ms.
        """
        messages = payload.get("messages", [])
        if not messages:
            return 0.5

        # Concatenate text content
        text_parts = []
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and "text" in b:
                        text_parts.append(b["text"])
        
        full_text = " ".join(text_parts)
        word_count = len(full_text.split())

        # Base score on length
        score = min(0.4, word_count / 800.0)

        # Keyword checks
        if TRIVIAL_PATTERNS.search(full_text):
            score -= 0.25

        deep_matches = len(DEEP_REASONING_PATTERNS.findall(full_text))
        if deep_matches > 0:
            score += min(0.65, 0.40 + (deep_matches * 0.10))

        # Code detection
        if "```" in full_text or "def " in full_text or "class " in full_text:
            score += 0.20

        # Structured schema or tool calls
        if payload.get("tools") or payload.get("response_format"):
            score += 0.15

        # Clamp between 0.05 and 0.99
        return max(0.05, min(0.99, score))

    def evaluate_route(self, requested_model: str, payload: Dict[str, Any], allow_cascade: bool = True) -> Tuple[str, str, float]:
        """
        Determines the optimal model route for a given payload.
        Returns (selected_model, target_tier, complexity_score).
        """
        self.total_routed += 1
        complexity = self.classify_complexity(payload)

        if not allow_cascade:
            return requested_model, "tier_3_frontier", complexity

        # If simple and requested an expensive model -> Downgrade to Tier 1 / Tier 2
        is_requested_frontier = any(m in requested_model.lower() for m in ["gpt-4o", "claude-3-5-sonnet", "claude-3-7-sonnet", "o1", "o3"])

        if is_requested_frontier and complexity < 0.35:
            # Downgrade to high-speed economy model
            target_model = "gemini-2.5-flash"
            self.downgraded_count += 1
            # Estimate savings
            est_prompt_tokens = len(str(payload.get("messages", "")).split())
            diff_per_token = (3.00 - 0.05) / 1_000_000
            self.arbitrage_savings_usd += (est_prompt_tokens * diff_per_token)
            return target_model, "tier_1_economy", complexity
        elif is_requested_frontier and complexity < 0.65:
            target_model = "claude-3-5-haiku-20241022" if "claude" in requested_model else "gpt-4o-mini"
            self.downgraded_count += 1
            return target_model, "tier_2_balanced", complexity

        return requested_model, "tier_3_frontier", complexity

# Global Cascade Router instance
cascade_router = CascadeRouter()
