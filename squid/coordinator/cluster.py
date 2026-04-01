"""Two-stage clustering — embedding pre-filter + LLM adjudication."""

from typing import Dict, List, Tuple

from squid.schema.finding import Finding


class ClusterEngine:
    """
    Stage 1: Embedding pre-filter (cosine similarity > 0.70 → candidates)
    Stage 2: LLM adjudication (AGREE / PARTIALLY_AGREE / DISAGREE)
    """

    def __init__(self, similarity_threshold: float = 0.70, divergence_threshold: float = 0.40):
        self.similarity_threshold = similarity_threshold
        self.divergence_threshold = divergence_threshold
        self._model = None

    def _get_embedding_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer("all-MiniLM-L6-v2")
        return self._model

    def stage1_prefilter(
        self, findings: List[Finding]
    ) -> Tuple[List[Tuple[str, str, float]], List[Tuple[str, str, float]]]:
        if len(findings) < 2:
            return [], []

        import numpy as np
        model = self._get_embedding_model()
        embeddings = model.encode([f.claim for f in findings], show_progress_bar=False)

        similar, divergent = [], []
        for i in range(len(findings)):
            for j in range(i + 1, len(findings)):
                sim = float(np.dot(embeddings[i], embeddings[j]) / (
                    np.linalg.norm(embeddings[i]) * np.linalg.norm(embeddings[j])
                ))
                if sim > self.similarity_threshold:
                    similar.append((findings[i].id, findings[j].id, sim))
                elif sim < self.divergence_threshold:
                    divergent.append((findings[i].id, findings[j].id, sim))
        return similar, divergent

    async def stage2_adjudicate(self, claim_a: str, claim_b: str) -> Dict:
        """LLM decides AGREE / PARTIALLY_AGREE / DISAGREE. Uses Haiku."""
        return {
            "verdict": "PARTIALLY_AGREE",
            "rationale": "Adjudication pending LLM integration",
        }
