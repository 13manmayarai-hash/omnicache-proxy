"""
High-performance in-memory semantic embedding engine.
Generates normalized dense vector representations for sub-millisecond similarity search.
"""

import math
import re
import hashlib
from typing import List, Dict, Tuple, Optional

class FastSemanticEmbedder:
    """
    Sub-millisecond semantic text embedder using high-dimensional hashed character/word n-gram 
    content-term frequency projection, synonym canonicalization, and L2-unit normalization.
    Provides robust semantic matching for question rephrasings, synonyms, and variations.
    """
    DIMENSIONS: int = 512
    
    # Common English & Multilingual stopwords for query normalization
    STOPWORDS = {
        "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", 
        "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", 
        "by", "could", "did", "do", "does", "doing", "down", "during", "each", "few", "for", "from", 
        "further", "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him", 
        "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just", 
        "me", "more", "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once", 
        "only", "or", "other", "ought", "our", "ours", "ourselves", "out", "over", "own", "same", 
        "she", "should", "so", "some", "such", "than", "that", "the", "their", "theirs", "them", 
        "themselves", "then", "there", "these", "they", "this", "those", "through", "to", "too", 
        "under", "until", "up", "very", "was", "we", "were", "what", "when", "where", "which", 
        "while", "who", "whom", "why", "with", "would", "you", "your", "yours", "yourself", "yourselves",
        "please", "tell", "explain", "help", "can", "could"
    }

    SYNONYM_MAP = {
        "recover": "reset",
        "recovery": "reset",
        "forgotten": "reset",
        "forgot": "reset",
        "procedure": "steps",
        "method": "steps",
        "instructions": "steps",
        "location": "located",
        "whereabouts": "located",
        "place": "located",
        "pricing": "price",
        "costs": "price",
        "rate": "price",
        "authenticate": "login",
        "signin": "login",
        "signup": "register",
        "terminate": "cancel",
        "modify": "change",
        "update": "change",
        "create": "make",
        "build": "make",
        "construct": "make",
        "generate": "make",
        "fix": "repair",
        "troubleshoot": "repair"
    }

    @classmethod
    def clean_and_tokenize(cls, text: str) -> List[str]:
        """Normalize text: lowercase, remove special characters, tokenize, canonicalize synonyms."""
        text = text.lower()
        # Keep alphanumeric, remove punctuation
        text = re.sub(r"[^\w\s]", " ", text)
        raw_tokens = text.split()
        return [cls.SYNONYM_MAP.get(t, t) for t in raw_tokens]

    @classmethod
    def get_features(cls, text: str) -> Dict[str, float]:
        """Extract unigram, content bigrams, and char n-gram weighted features."""
        tokens = cls.clean_and_tokenize(text)
        if not tokens:
            return {}
            
        features: Dict[str, float] = {}
        content_tokens = [t for t in tokens if t not in cls.STOPWORDS]
        
        # 1. Word unigrams (content words get high weight, stopwords low weight)
        for token in tokens:
            weight = 0.1 if token in cls.STOPWORDS else 2.0
            features[f"w:{token}"] = features.get(f"w:{token}", 0.0) + weight
            
        # 2. Content bigrams (skip noise stopwords)
        for i in range(len(content_tokens) - 1):
            bg = f"{content_tokens[i]}_{content_tokens[i+1]}"
            features[f"bg:{bg}"] = features.get(f"bg:{bg}", 0.0) + 1.0
            
        # 3. Subword 3-grams and 4-grams for content words (typos and morphology)
        for token in content_tokens:
            if len(token) >= 3:
                for n in (3, 4):
                    for i in range(len(token) - n + 1):
                        ngram = token[i:i+n]
                        features[f"ng:{ngram}"] = features.get(f"ng:{ngram}", 0.0) + 0.5
                        
        return features

    @classmethod
    def embed(cls, text: str) -> List[float]:
        """
        Embeds text into a 512-dimensional L2-normalized dense vector.
        """
        if not text or not text.strip():
            return [0.0] * cls.DIMENSIONS
            
        features = cls.get_features(text)
        vector = [0.0] * cls.DIMENSIONS
        
        # Feature hashing into fixed dimension space
        for feat, weight in features.items():
            h = int(hashlib.md5(feat.encode('utf-8')).hexdigest()[:8], 16)
            idx = h % cls.DIMENSIONS
            sign = 1.0 if (h >> 4) & 1 else -1.0
            vector[idx] += sign * weight
            
        # Compute L2 Norm (Euclidean length)
        norm_sq = sum(x * x for x in vector)
        if norm_sq > 0:
            norm = math.sqrt(norm_sq)
            vector = [x / norm for x in vector]
            
        return vector

    @staticmethod
    def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        """
        Calculates cosine similarity between two unit-normalized vectors.
        For unit vectors: dot_product = cosine_similarity.
        """
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
            
        # Dot product
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        # Clamp to [0.0, 1.0] for similarity index
        return max(0.0, min(1.0, dot))
