"""
Unit and Integration Tests for Phase 2 ANN Vector Indexing and Semantic Embedders.
"""

import unittest
import time
from core.ann_index import MultiTableLSHIndex, ANNIndexFactory
from core.embeddings import FastHashEmbedder, ONNXSemanticEmbedder, AutoEmbedder, FastSemanticEmbedder
from core.vector_cache import DualTierCache

class TestPhase2ANNAndEmbeddings(unittest.TestCase):

    def test_01_embedder_dimensions_and_normalization(self):
        """Verify embedders produce unit-normalized vectors."""
        embedder = FastHashEmbedder()
        self.assertEqual(embedder.dimensions, 512)

        vec = embedder.embed("How do I configure SSL in Nginx?")
        self.assertEqual(len(vec), 512)
        # Verify L2 unit norm sum(x^2) ~= 1.0
        norm_sq = sum(x * x for x in vec)
        self.assertAlmostEqual(norm_sq, 1.0, places=4)

        # Verify ONNX embedder fallback
        onnx_emb = ONNXSemanticEmbedder()
        self.assertEqual(onnx_emb.dimensions, 384)
        vec_onnx = onnx_emb.embed("Configure SSL")
        self.assertEqual(len(vec_onnx), 384)
        self.assertAlmostEqual(sum(x * x for x in vec_onnx), 1.0, places=4)

    def test_02_semantic_similarity_accuracy(self):
        """Verify semantic similarity between related prompts exceeds threshold."""
        p1 = "Where is the Eiffel Tower located in Paris?"
        p2 = "Tell me where the Eiffel Tower is located in Paris."
        p3 = "What is the capital of Australia?"

        v1 = FastSemanticEmbedder.embed(p1)
        v2 = FastSemanticEmbedder.embed(p2)
        v3 = FastSemanticEmbedder.embed(p3)

        sim_related = FastSemanticEmbedder.cosine_similarity(v1, v2)
        sim_unrelated = FastSemanticEmbedder.cosine_similarity(v1, v3)

        self.assertGreater(sim_related, 0.90, f"Related prompts should have high similarity, got {sim_related}")
        self.assertLess(sim_unrelated, 0.40, f"Unrelated prompts should have low similarity, got {sim_unrelated}")

    def test_03_lsh_ann_index_retrieval(self):
        """Verify MultiTableLSHIndex accurately retrieves nearest neighbors."""
        index = MultiTableLSHIndex(dimensions=512, num_tables=4, hash_bits=8)

        # Index 100 sample vectors
        base_prompt = "Where is the Eiffel Tower located in Paris?"
        base_vec = FastSemanticEmbedder.embed(base_prompt)
        index.add("key_target", base_vec)

        for i in range(100):
            vec = FastSemanticEmbedder.embed(f"Unrelated query topic {i} for testing database indexes")
            index.add(f"key_distractor_{i}", vec)

        self.assertEqual(index.size(), 101)

        # Search using a rephrased query
        query_vec = FastSemanticEmbedder.embed("Tell me where the Eiffel Tower is located in Paris.")
        results = index.search(query_vec, top_k=5)

        self.assertGreater(len(results), 0)
        # Target must be among top results
        top_keys = [k for k, _ in results]
        self.assertIn("key_target", top_keys)

        # Removal
        index.remove("key_target")
        self.assertEqual(index.size(), 100)
        results_after = index.search(query_vec, top_k=5)
        self.assertNotIn("key_target", [k for k, _ in results_after])

    def test_04_dual_tier_cache_ann_integration(self):
        """Verify DualTierCache seamlessly uses ANN candidate search at scale."""
        cache = DualTierCache()

        # Seed 60 diverse entries to trigger ANN indexing (> 50 threshold)
        for i in range(60):
            payload = {
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": f"Explain topic number {i} in software engineering"}],
                "temperature": 0.2
            }
            res = {"choices": [{"message": {"content": f"Topic {i} details."}}]}
            cache.store(payload, res, org_id="tenant_ann")

        # Target entry
        target_payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Where is the Eiffel Tower located in Paris?"}],
            "temperature": 0.2
        }
        target_res = {"choices": [{"message": {"content": "It is located on the Champ de Mars."}}]}
        cache.store(target_payload, target_res, org_id="tenant_ann")

        # Lookup with rephrased query
        query_payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Tell me where the Eiffel Tower is located in Paris."}],
            "temperature": 0.2
        }

        status, entry, score, reason = cache.lookup(query_payload, org_id="tenant_ann")
        self.assertEqual(status, "HIT_SEMANTIC")
        self.assertIsNotNone(entry)
        self.assertGreaterEqual(score, 0.90)

if __name__ == "__main__":
    unittest.main()
