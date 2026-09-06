"""
Radix Prefix-Tree Engine for Multi-Turn AI Agent Dialogues.
Enables conversation branching, prefix sub-tree reuse, and 1024-token ephemeral cache alignment.
"""

import hashlib
import json
import time
from typing import Dict, List, Any, Optional, Tuple

from core.hasher import RequestHasher

class RadixNode:
    """A single conversation turn node in the Radix prefix tree."""
    def __init__(self, node_id: str, role: str, content_hash: str, turn_index: int):
        self.node_id = node_id
        self.role = role
        self.content_hash = content_hash
        self.turn_index = turn_index
        self.children: Dict[str, "RadixNode"] = {}  # child_content_hash -> RadixNode
        self.cached_completion: Optional[Dict[str, Any]] = None
        self.stream_chunks: Optional[List[Dict[str, Any]]] = None
        self.model: Optional[str] = None
        self.org_id: Optional[str] = None
        self.tool_calls: Optional[List[Dict[str, Any]]] = None
        self.completions: Dict[str, Dict[str, Any]] = {}
        self.created_at = time.time()
        self.access_count = 0
        self.last_accessed = time.time()

class RadixPrefixTree:
    """In-memory Radix Prefix Tree for multi-turn conversations and agent loops."""
    def __init__(self):
        self.root = RadixNode(node_id="root", role="system", content_hash="root", turn_index=-1)
        self.total_nodes = 1
        self.prefix_hits = 0
        self.exact_hits = 0

    @staticmethod
    def hash_turn(turn: Dict[str, Any]) -> str:
        """
        Computes a deterministic hash of a single message turn.
        Normalizes dynamic timestamps in system prompts and handles structured content blocks.
        """
        role = turn.get("role", "")
        content = turn.get("content", "")
        if isinstance(content, list):
            norm_blocks = []
            for b in content:
                if isinstance(b, dict):
                    b_copy = dict(b)
                    if b_copy.get("type") == "text" and "text" in b_copy:
                        text_val = b_copy["text"]
                        if role == "system":
                            text_val = RequestHasher.normalize_system_prompt(text_val)
                        b_copy["text"] = text_val
                    norm_blocks.append(b_copy)
                else:
                    norm_blocks.append(str(b))
            content_str = json.dumps(norm_blocks, sort_keys=True)
        else:
            content_str = str(content)
            if role == "system":
                content_str = RequestHasher.normalize_system_prompt(content_str)

        tool_calls = turn.get("tool_calls", None)
        tool_call_id = turn.get("tool_call_id", None)
        name = turn.get("name", None)
        raw_repr = f"{role}:{content_str}:{json.dumps(tool_calls, sort_keys=True)}:{tool_call_id}:{name}"
        return hashlib.sha256(raw_repr.encode("utf-8")).hexdigest()[:16]

    def match_prefix(self, messages: List[Dict[str, Any]]) -> Tuple[int, Optional[RadixNode]]:
        """
        Traverses the tree to find the longest matching prefix of message turns.
        Returns (matched_turn_count, last_matched_node).
        """
        curr = self.root
        matched_turns = 0

        for i, turn in enumerate(messages):
            turn_hash = self.hash_turn(turn)
            if turn_hash in curr.children:
                curr = curr.children[turn_hash]
                curr.access_count += 1
                curr.last_accessed = time.time()
                matched_turns += 1
            else:
                break

        if matched_turns > 0:
            self.prefix_hits += 1

        return matched_turns, (curr if curr is not self.root else None)

    def lookup_conversation(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        org_id: Optional[str] = None
    ) -> Tuple[bool, Optional[Dict[str, Any]], int, Optional[RadixNode]]:
        """
        Traverses the radix tree for the entire conversation turn sequence.
        If all turns match and the terminal node holds a cached completion matching
        the requested model/org_id, returns (True, cached_completion, matched_turns, terminal_node).
        Otherwise returns (False, None, matched_turns, longest_matched_node).
        """
        curr = self.root
        matched_turns = 0

        for turn in messages:
            turn_hash = self.hash_turn(turn)
            if turn_hash in curr.children:
                curr = curr.children[turn_hash]
                curr.access_count += 1
                curr.last_accessed = time.time()
                matched_turns += 1
            else:
                break

        if matched_turns > 0:
            self.prefix_hits += 1

        if matched_turns == len(messages) and curr is not self.root:
            matched_entry = None
            if org_id and model:
                comp_key = f"{org_id}:{model}"
                if comp_key in curr.completions:
                    matched_entry = curr.completions[comp_key]
            if not matched_entry:
                for k, comp in curr.completions.items():
                    if org_id and comp.get("org_id") and comp.get("org_id") != org_id:
                        continue
                    if model and comp.get("model") and comp.get("model") != model:
                        continue
                    matched_entry = comp
                    break
            if not matched_entry and curr.cached_completion:
                if (not org_id or not curr.org_id or curr.org_id == org_id) and \
                   (not model or not curr.model or curr.model == model):
                    matched_entry = {
                        "completion": curr.cached_completion,
                        "stream_chunks": curr.stream_chunks,
                        "tool_calls": curr.tool_calls,
                        "model": curr.model,
                        "org_id": curr.org_id,
                    }

            if matched_entry:
                self.exact_hits += 1
                return True, matched_entry.get("completion"), matched_turns, curr

        return False, None, matched_turns, (curr if curr is not self.root else None)

    def insert_conversation(
        self,
        messages: List[Dict[str, Any]],
        completion: Dict[str, Any],
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        model: str = "",
        org_id: str = "default_org",
        stream_chunks: Optional[List[Dict[str, Any]]] = None
    ) -> RadixNode:
        """
        Inserts a full conversation path into the radix tree and stores the terminal completion.
        Supports model, tenant isolation, and stream chunk replay.
        """
        curr = self.root
        for i, turn in enumerate(messages):
            turn_hash = self.hash_turn(turn)
            if turn_hash not in curr.children:
                new_node_id = f"node_{self.total_nodes}_{turn_hash[:8]}"
                new_node = RadixNode(
                    node_id=new_node_id,
                    role=turn.get("role", "user"),
                    content_hash=turn_hash,
                    turn_index=i
                )
                curr.children[turn_hash] = new_node
                self.total_nodes += 1
            curr = curr.children[turn_hash]

        comp_key = f"{org_id}:{model}" if (org_id and model) else (org_id or model or "default")
        curr.completions[comp_key] = {
            "completion": completion,
            "stream_chunks": stream_chunks,
            "tool_calls": tool_calls,
            "model": model,
            "org_id": org_id,
            "created_at": time.time()
        }
        curr.cached_completion = completion
        curr.stream_chunks = stream_chunks
        curr.model = model
        curr.org_id = org_id
        curr.tool_calls = tool_calls
        curr.access_count += 1
        curr.last_accessed = time.time()
        return curr

    def align_ephemeral_cache_blocks(self, messages: List[Dict[str, Any]], block_size_tokens: int = 1024) -> List[Dict[str, Any]]:
        """
        Aligns message turns to downstream provider (Anthropic/OpenAI) 1024-token prompt caching blocks.
        Injects Anthropic cache_control metadata on the last turn that crosses the 1024-token boundary.
        """
        cumulative_tokens = 0
        aligned_messages = []

        for turn in messages:
            turn_copy = dict(turn)
            # Estimate token count ~ words * 1.3
            content_val = turn.get("content", "")
            if isinstance(content_val, list):
                text_parts = [b.get("text", "") for b in content_val if isinstance(b, dict) and b.get("type") == "text"]
                content_str = " ".join(text_parts)
            else:
                content_str = str(content_val)

            est_tokens = int(len(content_str.split()) * 1.3) + 4
            cumulative_tokens += est_tokens

            if cumulative_tokens >= block_size_tokens and "cache_control" not in turn_copy:
                # Add ephemeral cache breakpoint
                turn_copy["cache_control"] = {"type": "ephemeral"}
                if isinstance(turn_copy.get("content"), list) and turn_copy["content"]:
                    last_b = dict(turn_copy["content"][-1])
                    last_b["cache_control"] = {"type": "ephemeral"}
                    turn_copy["content"] = list(turn_copy["content"][:-1]) + [last_b]
                cumulative_tokens = 0  # reset for next block

            aligned_messages.append(turn_copy)

        return aligned_messages

# Global Radix Prefix Tree instance
radix_tree = RadixPrefixTree()
