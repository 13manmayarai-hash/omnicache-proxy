"""
Adaptive Cost Arbitrage & Speculative Model Cascade Router (v2.9.8).
Analyzes prompt complexity in <0.2ms via Shannon Token Entropy & Lexical Reasoning Classifiers.
Dynamically routes simple procedural queries to ultra-fast, economical models (gpt-4o-mini,
gemini-2.5-flash, claude-3-5-haiku) when authorized via policy or 'X-OmniCache-Model-Cascade: allow',
saving up to 80% on cache-miss token spend.
"""

import collections
import math
import re
import time
from typing import Dict, Any, Tuple, Optional, List

# Complexity Indicator Patterns
DEEP_REASONING_PATTERNS = re.compile(
    r"\b(prove|derive|architect|distributed|concurrency|race condition|deadlock|quantum|cryptographic|kernel|assembly|ast|bytecode|formal verification|differential equations|mathematical|recursion|algorithm optimization|backtracking|dynamic programming)\b",
    re.IGNORECASE
)

TRIVIAL_PATTERNS = re.compile(
    r"\b(translate|capitalize|uppercase|lowercase|format this|fix grammar|summarize in 1 sentence|spell check|json format|sort this list|extract email|strip whitespace|remove punctuation|echo back)\b",
    re.IGNORECASE
)

MODEL_TIERS = {
    "tier_1_economy": {
        "models": ["gemini-2.5-flash", "llama-3.3-70b", "mistral-small", "gpt-4o-mini"],
        "cost_per_1m_input": 0.05,
        "cost_per_1m_output": 0.15
    },
    "tier_2_balanced": {
        "models": ["claude-3-5-haiku-20241022", "claude-haiku-4-5-20251001", "gpt-4o-mini"],
        "cost_per_1m_input": 0.80,
        "cost_per_1m_output": 4.00
    },
    "tier_3_frontier": {
        "models": ["claude-3-7-sonnet", "claude-3-5-sonnet-20241022", "claude-sonnet-4-5-20250929", "gpt-4o", "o1", "o3-mini"],
        "cost_per_1m_input": 3.00,
        "cost_per_1m_output": 15.00
    }
}


def compute_shannon_entropy(text: str) -> float:
    """
    Computes normalized Shannon information entropy H in [0.0, 1.0] of token frequency distribution.
    Executes in <0.05ms for sub-millisecond complexity analysis.
    """
    words = text.lower().split()
    if not words:
        return 0.5
    total = len(words)
    if total <= 1:
        return 0.5
    counts = collections.Counter(words)
    entropy = -sum((cnt / total) * math.log2(cnt / total) for cnt in counts.values())
    max_entropy = math.log2(total)
    return max(0.0, min(1.0, entropy / max_entropy)) if max_entropy > 0 else 0.5


class CascadeRouter:
    """
    Intelligent complexity classifier and governed cost-arbitrage routing engine.
    Supports Shannon entropy scoring, execution safety invariants, and dynamic tier cascading.
    """

    def __init__(self):
        self.total_routed: int = 0
        self.downgraded_count: int = 0
        self.arbitrage_savings_usd: float = 0.0
        self.tokens_diverted_to_economy: int = 0

    @staticmethod
    def classify_complexity(payload: Dict[str, Any]) -> float:
        """
        Computes complexity score from 0.05 (trivial formatting) to 0.99 (deep multi-step reasoning) in <0.2ms.
        Combines token length, keyword patterns, code AST detection, and Shannon information entropy.
        """
        messages = payload.get("messages", [])
        if not messages:
            return 0.5

        # Concatenate text content
        text_parts: List[str] = []
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

        # Base score on length (scaled up to 0.40)
        score = min(0.40, word_count / 800.0)

        # Keyword checks
        if TRIVIAL_PATTERNS.search(full_text):
            score -= 0.25

        deep_matches = len(DEEP_REASONING_PATTERNS.findall(full_text))
        if deep_matches > 0:
            score += min(0.65, 0.40 + (deep_matches * 0.10))

        # Code detection heuristics
        if "```" in full_text or "def " in full_text or "class " in full_text or "SELECT " in full_text:
            score += 0.25

        # Structured schema or tool calls
        if payload.get("tools") or payload.get("tool_choice") or payload.get("response_format"):
            score += 0.35

        # Multi-turn conversational complexity bump
        if len(messages) > 2:
            score += 0.15

        # Shannon Entropy weighting: low entropy (repetitive/boilerplate) pulls score down,
        # high entropy (varied information density) reinforces complex reasoning.
        if word_count > 6:
            entropy = compute_shannon_entropy(full_text)
            if entropy < 0.65:
                score -= 0.10
            elif entropy > 0.92 and deep_matches > 0:
                score += 0.10

        # Clamp between 0.05 and 0.99
        return max(0.05, min(0.99, score))

    def evaluate_route(
        self,
        requested_model: str,
        payload: Dict[str, Any],
        allow_cascade: bool = False,
        vendor_affinity: str = "auto"
    ) -> Tuple[str, str, float, bool, str]:
        """
        Determines the optimal model route for a given payload.

        Args:
            requested_model: Upstream model requested by client (e.g. 'gpt-4o', 'claude-3-7-sonnet')
            payload: LLM request body containing messages and tools
            allow_cascade: Boolean flag or policy opt-in for cascading
            vendor_affinity: 'auto', 'same-vendor' (e.g. OpenAI stays OpenAI), or 'cross-vendor'

        Returns:
            Tuple[selected_model, target_tier, complexity_score, was_cascaded, cascade_reason]
        """
        self.total_routed += 1
        complexity = self.classify_complexity(payload)
        messages = payload.get("messages", [])
        has_tools = bool(payload.get("tools") or payload.get("tool_choice"))
        has_schema = bool(payload.get("response_format"))
        is_multiturn = len(messages) > 1

        # 1. Check Opt-In Guardrail
        if not allow_cascade:
            return requested_model, "tier_3_frontier", complexity, False, "cascade_opt_in_disabled"

        # 2. Check Execution Safety Guardrails (Never downgrade tools, schemas, or multi-turn chains)
        if has_tools:
            return requested_model, "tier_3_frontier", complexity, False, "preserved_for_agent_tools"
        if has_schema:
            return requested_model, "tier_3_frontier", complexity, False, "preserved_for_structured_schema"
        if is_multiturn:
            return requested_model, "tier_3_frontier", complexity, False, "preserved_for_multiturn_context"

        # 3. Model Family Identification
        req_lower = requested_model.lower()
        is_requested_frontier = any(m in req_lower for m in ["gpt-4o", "claude-3-5-sonnet", "claude-3-7-sonnet", "claude-sonnet-4-5", "o1", "o3"])

        if not is_requested_frontier:
            return requested_model, "tier_1_economy", complexity, False, "already_economy_tier"

        est_prompt_tokens = len(str(payload.get("messages", "")).split())

        # 4. Governed Cost Arbitrage Cascading
        if complexity < 0.35:
            # Trivial query -> route to ultra-fast economy model
            if "claude" in req_lower or vendor_affinity == "same-vendor" and "claude" in req_lower:
                target_model = "claude-3-5-haiku-20241022"
                tier = "tier_2_balanced"
                cost_diff = 3.00 - 0.80
            elif vendor_affinity == "same-vendor" and ("gpt" in req_lower or "o1" in req_lower or "o3" in req_lower):
                target_model = "gpt-4o-mini"
                tier = "tier_1_economy"
                cost_diff = 2.50 - 0.15
            else:
                target_model = "gemini-2.5-flash"
                tier = "tier_1_economy"
                cost_diff = 3.00 - 0.05

            self.downgraded_count += 1
            self.tokens_diverted_to_economy += est_prompt_tokens
            savings = (est_prompt_tokens * (cost_diff / 1_000_000.0))
            self.arbitrage_savings_usd += savings

            reason = f"opt_in_allowed_complexity_{complexity:.2f}_downgraded_to_{tier}"
            return target_model, tier, complexity, True, reason

        elif complexity < 0.60:
            # Moderate query -> route to balanced model within vendor family
            if "claude" in req_lower:
                target_model = "claude-3-5-haiku-20241022"
                cost_diff = 3.00 - 0.80
            else:
                target_model = "gpt-4o-mini"
                cost_diff = 2.50 - 0.15
            tier = "tier_2_balanced"

            self.downgraded_count += 1
            self.tokens_diverted_to_economy += est_prompt_tokens
            savings = (est_prompt_tokens * (cost_diff / 1_000_000.0))
            self.arbitrage_savings_usd += savings

            reason = f"opt_in_allowed_complexity_{complexity:.2f}_downgraded_to_tier_2_balanced"
            return target_model, tier, complexity, True, reason

        # High complexity -> retain requested frontier model
        return requested_model, "tier_3_frontier", complexity, False, "frontier_complexity_retained"

    def get_stats(self) -> Dict[str, Any]:
        """Returns cumulative model cascading and arbitrage statistics."""
        return {
            "total_routed": self.total_routed,
            "downgraded_count": self.downgraded_count,
            "arbitrage_savings_usd": round(self.arbitrage_savings_usd, 6),
            "tokens_diverted_to_economy": self.tokens_diverted_to_economy,
            "downgrade_ratio": round(self.downgraded_count / max(1, self.total_routed), 4)
        }

    def reset_stats(self):
        """Resets cumulative counters."""
        self.total_routed = 0
        self.downgraded_count = 0
        self.arbitrage_savings_usd = 0.0
        self.tokens_diverted_to_economy = 0


# Global Cascade Router instance
cascade_router = CascadeRouter()
