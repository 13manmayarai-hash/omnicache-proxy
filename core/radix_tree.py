"""
Radix Prefix-Tree Engine for Multi-Turn AI Agent Dialogues.
Enables conversation branching, prefix sub-tree reuse, and 1024-token ephemeral cache alignment.
"""

import hashlib
import json
import time
from typing import Dict, List, Any, Optional, Tuple

class RadixNode:
    """A single conversation turn node in the Radix prefix tree."""
    def __init__(self, node_id: str, role: str, content_hash: str, turn_index: int):
        self.node_id = node_id
        self.role = role
        self.content_hash = content_hash
        self.turn_index = turn_index
        self.children: Dict[str, "RadixNode"] = {}  # child_content_hash -> RadixNode
        self.cached_completion: Optional[Dict[str, Any]] = None
        self.tool_calls: Optional[List[Dict[str, Any]]] = None
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
        """Computes a deterministic hash of a single message turn."""
        role = turn.get("role", "")
        content = turn.get("content", "")
        tool_calls = turn.get("tool_calls", None)
        raw_repr = f"{role}:{content}:{json.dumps(tool_calls, sort_keys=True)}"
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

    def insert_conversation(self, messages: List[Dict[str, Any]], completion: Dict[str, Any], tool_calls: Optional[List[Dict[str, Any]]] = None) -> RadixNode:
        """
        Inserts a full conversation path into the radix tree and stores the terminal completion.
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

        curr.cached_completion = completion
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
            content_str = str(turn.get("content", ""))
            est_tokens = int(len(content_str.split()) * 1.3) + 4
            cumulative_tokens += est_tokens

            if cumulative_tokens >= block_size_tokens and "cache_control" not in turn_copy:
                # Add ephemeral cache breakpoint
                turn_copy["cache_control"] = {"type": "ephemeral"}
                cumulative_tokens = 0  # reset for next block

            aligned_messages.append(turn_copy)

        return aligned_messages

# Global Radix Prefix Tree instance
radix_tree = RadixPrefixTree()
