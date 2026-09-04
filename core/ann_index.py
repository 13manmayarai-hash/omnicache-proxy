"""
Approximate Nearest Neighbor (ANN) Vector Index Engine for OmniCache.
Provides sub-millisecond similarity search across large tenant caches using
Multi-Table Locality Sensitive Hashing (LSH) and Vantage-Point (VP) spatial partitioning.
"""

import math
import hashlib
from abc import ABC, abstractmethod
from typing import List, Dict, Tuple, Optional, Set, Any


class BaseANNIndex(ABC):
    """Abstract interface for vector similarity indices."""

    @abstractmethod
    def add(self, key: str, vector: List[float]):
        pass

    @abstractmethod
    def remove(self, key: str):
        pass

    @abstractmethod
    def search(self, query_vector: List[float], top_k: int = 50, min_similarity: float = 0.0) -> List[Tuple[str, float]]:
        pass

    @abstractmethod
    def size(self) -> int:
        pass

    @abstractmethod
    def clear(self):
        pass


class MultiTableLSHIndex(BaseANNIndex):
    """
    Multi-Table Random Hyperplane Locality Sensitive Hashing (Cosine LSH).
    Maps high-dimensional unit vectors into multi-bit hash buckets to prune
    search space from O(N) to O(1) / O(candidates), followed by exact cosine scoring.
    """

    def __init__(self, dimensions: int = 512, num_tables: int = 4, hash_bits: int = 8):
        self.dimensions = dimensions
        self.num_tables = num_tables
        self.hash_bits = hash_bits
        
        # Deterministically generate orthogonal random hyperplanes for each table
        self.hyperplanes: List[List[List[float]]] = []
        for t in range(num_tables):
            table_planes = []
            for b in range(hash_bits):
                # Generate pseudo-random deterministic hyperplane using sha256 seed
                plane = []
                for d in range(dimensions):
                    seed = f"omnicache:lsh:plane:{t}:{b}:{d}".encode("utf-8")
                    val = (int(hashlib.sha256(seed).hexdigest()[:8], 16) / 0xFFFFFFFF) * 2.0 - 1.0
                    plane.append(val)
                # Normalize plane
                norm = math.sqrt(sum(x * x for x in plane)) or 1.0
                table_planes.append([x / norm for x in plane])
            self.hyperplanes.append(table_planes)

        # Storage: table_idx -> bucket_code -> Set[key]
        self.buckets: List[Dict[int, Set[str]]] = [{} for _ in range(num_tables)]
        # Key -> Vector mapping
        self.vectors: Dict[str, List[float]] = {}

    def _hash_vector(self, vector: List[float], table_idx: int) -> int:
        code = 0
        planes = self.hyperplanes[table_idx]
        for b, plane in enumerate(planes):
            # Dot product with hyperplane
            dot = sum(v * p for v, p in zip(vector, plane))
            if dot >= 0:
                code |= (1 << b)
        return code

    def add(self, key: str, vector: List[float]):
        if not vector or len(vector) != self.dimensions:
            return
        self.vectors[key] = vector
        for t in range(self.num_tables):
            code = self._hash_vector(vector, t)
            if code not in self.buckets[t]:
                self.buckets[t][code] = set()
            self.buckets[t][code].add(key)

    def remove(self, key: str):
        if key not in self.vectors:
            return
        vector = self.vectors[key]
        for t in range(self.num_tables):
            code = self._hash_vector(vector, t)
            if code in self.buckets[t] and key in self.buckets[t][code]:
                self.buckets[t][code].remove(key)
                if not self.buckets[t][code]:
                    del self.buckets[t][code]
        del self.vectors[key]

    def search(self, query_vector: List[float], top_k: int = 50, min_similarity: float = 0.0) -> List[Tuple[str, float]]:
        if not query_vector or len(query_vector) != self.dimensions or not self.vectors:
            return []

        # If cache is small (<= 200 items), linear exact scan is fast and has 100% recall
        if len(self.vectors) <= 200:
            candidates = list(self.vectors.keys())
        else:
            # Multi-probe LSH bucket lookup
            candidate_set: Set[str] = set()
            for t in range(self.num_tables):
                code = self._hash_vector(query_vector, t)
                if code in self.buckets[t]:
                    candidate_set.update(self.buckets[t][code])
                # Probe 1-bit hamming neighbors for high recall
                for bit in range(self.hash_bits):
                    neighbor_code = code ^ (1 << bit)
                    if neighbor_code in self.buckets[t]:
                        candidate_set.update(self.buckets[t][neighbor_code])

            # If candidates are too sparse, fallback to sample
            if len(candidate_set) < min(top_k, len(self.vectors)):
                candidate_set = set(self.vectors.keys())

            candidates = list(candidate_set)

        # Compute exact cosine similarities on candidates
        scored: List[Tuple[str, float]] = []
        for key in candidates:
            vec = self.vectors.get(key)
            if vec is None:
                continue
            sim = max(0.0, min(1.0, sum(q * v for q, v in zip(query_vector, vec))))
            if sim >= min_similarity:
                scored.append((key, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def size(self) -> int:
        return len(self.vectors)

    def clear(self):
        self.vectors.clear()
        for t in range(self.num_tables):
            self.buckets[t].clear()


class ANNIndexFactory:
    """Factory to create the best available ANN index for the runtime environment."""

    @staticmethod
    def create(dimensions: int = 512) -> BaseANNIndex:
        try:
            import faiss
            # If faiss is installed, use FaissHNSWIndex
            return FaissHNSWIndex(dimensions=dimensions)
        except Exception:
            # High-performance built-in multi-table LSH
            return MultiTableLSHIndex(dimensions=dimensions)


class FaissHNSWIndex(BaseANNIndex):
    """Optional FAISS-accelerated HNSW vector index with leak-free churn compaction."""

    def __init__(self, dimensions: int = 512, m: int = 32):
        import faiss
        import numpy as np
        self.dimensions = dimensions
        self.m = m
        self.index = faiss.IndexHNSWFlat(dimensions, m, faiss.METRIC_INNER_PRODUCT)
        self.key_to_id: Dict[str, int] = {}
        self.id_to_key: Dict[int, str] = {}
        self.vectors: Dict[str, List[float]] = {}
        self.current_id = 0
        self.removed_count = 0
        self.rebuild_count = 0
        self.np = np

    def _rebuild(self):
        """Rebuilds the underlying FAISS HNSW graph to reclaim deleted vector memory."""
        import faiss
        self.index = faiss.IndexHNSWFlat(self.dimensions, self.m, faiss.METRIC_INNER_PRODUCT)
        self.key_to_id.clear()
        self.id_to_key.clear()
        self.current_id = 0
        self.removed_count = 0
        self.rebuild_count += 1
        if not self.vectors:
            return

        keys = list(self.vectors.keys())
        matrix = self.np.array([self.vectors[k] for k in keys], dtype=self.np.float32)
        self.index.add(matrix)
        for i, k in enumerate(keys):
            self.key_to_id[k] = i
            self.id_to_key[i] = k
        self.current_id = len(keys)

    def add(self, key: str, vector: List[float]):
        if not vector or len(vector) != self.dimensions:
            return
        if key in self.key_to_id:
            self.remove(key)
        self.vectors[key] = vector
        arr = self.np.array([vector], dtype=self.np.float32)
        idx = self.current_id
        self.current_id += 1
        self.key_to_id[key] = idx
        self.id_to_key[idx] = key
        self.index.add(arr)

    def remove(self, key: str):
        if key in self.key_to_id:
            idx = self.key_to_id.pop(key)
            self.id_to_key.pop(idx, None)
            self.vectors.pop(key, None)
            self.removed_count += 1

            # Trigger compaction if removals exceed 20% of total lifetime entries (and >= 20 removals)
            total_active = len(self.vectors)
            total_churn = total_active + self.removed_count
            if self.removed_count >= 20 and self.removed_count > 0.20 * total_churn:
                self._rebuild()

    def search(self, query_vector: List[float], top_k: int = 50, min_similarity: float = 0.0) -> List[Tuple[str, float]]:
        if self.index.ntotal == 0 or not self.key_to_id:
            return []
        arr = self.np.array([query_vector], dtype=self.np.float32)
        distances, indices = self.index.search(arr, min(top_k, self.index.ntotal))
        results = []
        for d, idx in zip(distances[0], indices[0]):
            if idx in self.id_to_key:
                score = float(d)
                if score >= min_similarity:
                    results.append((self.id_to_key[idx], score))
        return results

    def size(self) -> int:
        return len(self.key_to_id)

    def clear(self):
        import faiss
        self.index = faiss.IndexHNSWFlat(self.dimensions, self.m, faiss.METRIC_INNER_PRODUCT)
        self.key_to_id.clear()
        self.id_to_key.clear()
        self.vectors.clear()
        self.current_id = 0
        self.removed_count = 0
