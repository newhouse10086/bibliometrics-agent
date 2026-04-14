"""TSR (Topic Significance Ranking) Module.

Implements topic significance ranking using KL divergence, cosine dissimilarity,
and Pearson correlation against three reference distributions (uniform, vacuous, background).

Reference: sample-project/Topic Significance Ranking.R
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from jsonschema import validate

from modules.base import BaseModule, HardwareSpec, RunContext

logger = logging.getLogger(__name__)


class TSRRanker(BaseModule):
    """Topic Significance Ranking module.

    Ranks topics by significance using composite scores combining:
    - KL divergence against uniform/vacuous/background distributions
    - Cosine dissimilarity against uniform/vacuous/background distributions
    - Pearson correlation against uniform/vacuous/background distributions

    Uses 4-stage weighting:
    1. Cross-weighting
    2. Min-max normalization
    3. Combine references
    4. Final TSR = Sk × φk
    """

    @property
    def name(self) -> str:
        return "tsr_ranker"

    @property
    def version(self) -> str:
        return "1.0.0"

    def input_schema(self) -> dict:
        """Define input schema.

        Expected inputs:
        - topic_word_matrix: Topic-word distribution matrix (K x W)
        - doc_topic_matrix: Document-topic distribution matrix (D x K)
        - vocab: List of vocabulary words
        - total_words: Total word count in corpus
        """
        return {
            "type": "object",
            "properties": {
                "topic_word_matrix": {
                    "type": "string",
                    "description": "Path to topic-word distribution CSV (rows=topics, cols=words)"
                },
                "doc_topic_matrix": {
                    "type": "string",
                    "description": "Path to document-topic distribution CSV (rows=docs, cols=topics)"
                },
                "vocab": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Vocabulary list"
                },
                "total_words": {
                    "type": "integer",
                    "description": "Total word count in corpus"
                }
            },
            "required": ["topic_word_matrix", "doc_topic_matrix", "vocab", "total_words"]
        }

    def output_schema(self) -> dict:
        """Define output schema."""
        return {
            "type": "object",
            "properties": {
                "tsr_scores": {
                    "type": "string",
                    "description": "Path to TSR scores CSV (topic, tsr_score)"
                },
                "metrics": {
                    "type": "object",
                    "properties": {
                        "kl_divergence": {"type": "array"},
                        "cosine_dissimilarity": {"type": "array"},
                        "pearson_correlation": {"type": "array"},
                        "sk_scores": {"type": "array"},
                        "phi_scores": {"type": "array"}
                    }
                },
                "stats": {
                    "type": "object",
                    "properties": {
                        "n_topics": {"type": "integer"},
                        "n_docs": {"type": "integer"},
                        "vocab_size": {"type": "integer"},
                        "top_topic": {"type": "integer"}
                    }
                }
            }
        }

    def config_schema(self) -> dict:
        """Define configuration schema."""
        return {
            "type": "object",
            "properties": {
                "weights": {
                    "type": "object",
                    "properties": {
                        "uniform": {"type": "number", "minimum": 0, "maximum": 1},
                        "vacuous": {"type": "number", "minimum": 0, "maximum": 1},
                        "background": {"type": "number", "minimum": 0, "maximum": 1}
                    }
                },
                "phi_weights": {
                    "type": "object",
                    "properties": {
                        "uniform": {"type": "number", "minimum": 0, "maximum": 1},
                        "vacuous": {"type": "number", "minimum": 0, "maximum": 1},
                        "background": {"type": "number", "minimum": 0, "maximum": 1}
                    }
                }
            }
        }

    def get_hardware_requirements(self, config: dict) -> HardwareSpec:
        """Estimate hardware requirements."""
        return HardwareSpec(
            cpu_cores=1,
            min_memory_gb=2.0,
            recommended_memory_gb=4.0,
            gpu_required=False,
            estimated_runtime_seconds=300,
        )

    def process(
        self,
        input_data: dict,
        config: dict,
        context: RunContext
    ) -> dict:
        """Execute TSR ranking.

        Args:
            input_data: Input data containing matrices and vocab
            config: Configuration with weights
            context: Runtime context

        Returns:
            TSR scores and metrics
        """
        logger.info("Starting TSR ranking...")

        # Load matrices - try input_data first, then check previous_outputs from topic_modeler
        if "topic_word_matrix" in input_data:
            topic_word_path = Path(input_data["topic_word_matrix"])
            doc_topic_path = Path(input_data["doc_topic_matrix"])
            vocab = input_data["vocab"]
            total_words = input_data["total_words"]
        elif "topic_modeler" in context.previous_outputs:
            # Get from topic_modeler's output
            topic_modeler_output = context.previous_outputs["topic_modeler"]
            topic_word_path = Path(topic_modeler_output["topic_word_path"])
            doc_topic_path = Path(topic_modeler_output["doc_topic_path"])
            # Load vocab from preprocessor if available
            if "preprocessor" in context.previous_outputs:
                preprocessor_output = context.previous_outputs["preprocessor"]
                vocab = Path(preprocessor_output["vocab_path"]).read_text(encoding="utf-8").strip().split("\n")
                # Calculate total_words from DTM
                dtm_path = Path(preprocessor_output["dtm_path"])
                dtm_df = pd.read_csv(dtm_path, index_col=0)
                total_words = int(dtm_df.values.sum())
                logger.info(f"Calculated total_words from DTM: {total_words}")
            else:
                raise KeyError("Cannot find vocab and total_words - preprocessor output not available in context.previous_outputs")
        else:
            raise KeyError("topic_word_matrix not found in input_data or topic_modeler previous_outputs")

        topic_word_df = pd.read_csv(topic_word_path, index_col=0)
        doc_topic_df = pd.read_csv(doc_topic_path, index_col=0)

        # Convert to numeric numpy arrays (ensure float type)
        topic_word = topic_word_df.values.astype(float)  # K x W
        doc_topic = doc_topic_df.values.astype(float)    # D x K

        K, W = topic_word.shape
        D = doc_topic.shape[0]

        logger.info(f"Input shapes: topic_word={topic_word.shape}, doc_topic={doc_topic.shape}")
        logger.info(f"K={K} topics, W={W} words, D={D} documents, total_words={total_words}")

        # Get weights from config
        weights = config.get("weights", {
            "uniform": 0.6,
            "vacuous": 0.4
        })
        phi_weights = config.get("phi_weights", {
            "uniform": 0.25,
            "vacuous": 0.25,
            "background": 0.5
        })

        # Step 1: Compute reference distributions
        P_omega_u = self._uniform_distribution(W)  # Uniform
        P_omega_v = self._vacuous_distribution(topic_word, doc_topic, total_words)  # Vacuous semantic
        P_omega_b = self._background_distribution(D)  # Background

        logger.info("Computed reference distributions")

        # Step 2: Compute KL divergence
        kl_uniform = self._kl_divergence(topic_word, P_omega_u, axis=1)
        kl_vacuous = self._kl_divergence_vacuous(topic_word, P_omega_v)
        kl_background = self._kl_divergence(doc_topic, P_omega_b, axis=0)

        logger.info("Computed KL divergence")

        # Step 3: Compute cosine dissimilarity
        cos_uniform = self._cosine_similarity(topic_word, P_omega_u, axis=1)
        cos_vacuous = self._cosine_similarity_vacuous(topic_word, P_omega_v)
        cos_background = self._cosine_similarity(doc_topic, P_omega_b, axis=0)

        logger.info("Computed cosine dissimilarity")

        # Step 4: Compute Pearson correlation
        corr_uniform = self._pearson_correlation_uniform(topic_word)
        corr_vacuous = self._pearson_correlation_vacuous(topic_word, P_omega_v)
        corr_background = self._pearson_correlation_background(doc_topic)

        logger.info("Computed Pearson correlation")

        # Step 5: 4-stage weighting
        # Stage 1: Cross-weighting
        kl_u_c1 = self._cross_weight(kl_uniform)
        kl_v_c1 = self._cross_weight(kl_vacuous)
        kl_b_c1 = self._cross_weight(kl_background)

        cos_u_c1 = self._cross_weight(cos_uniform)
        cos_v_c1 = self._cross_weight(cos_vacuous)
        cos_b_c1 = self._cross_weight(cos_background)

        corr_u_c1 = self._cross_weight(corr_uniform)
        corr_v_c1 = self._cross_weight(corr_vacuous)
        corr_b_c1 = self._cross_weight(corr_background)

        # Stage 2: Min-max normalization
        kl_u_c2 = self._min_max_normalize(kl_u_c1)
        kl_v_c2 = self._min_max_normalize(kl_v_c1)
        kl_b_c2 = self._min_max_normalize(kl_b_c1)

        cos_u_c2 = self._min_max_normalize(cos_u_c1)
        cos_v_c2 = self._min_max_normalize(cos_v_c1)
        cos_b_c2 = self._min_max_normalize(cos_b_c1)

        corr_u_c2 = self._min_max_normalize(corr_u_c1)
        corr_v_c2 = self._min_max_normalize(corr_v_c1)
        corr_b_c2 = self._min_max_normalize(corr_b_c1)

        logger.info("Completed stages 1-2 weighting")

        # Stage 3: Combine references
        S_u1 = (kl_u_c1 + cos_u_c1 + corr_u_c1) / 3
        S_v1 = (kl_v_c1 + cos_v_c1 + corr_v_c1) / 3
        S_b1 = (kl_b_c1 + cos_b_c1 + corr_b_c1) / 3

        S_u2 = (kl_u_c2 + cos_u_c2 + corr_u_c2) / 3
        S_v2 = (kl_v_c2 + cos_v_c2 + corr_v_c2) / 3
        S_b2 = (kl_b_c2 + cos_b_c2 + corr_b_c2) / 3

        # Stage 4: Final TSR
        Sk = S_b1 * (weights["uniform"] * S_u1 + weights["vacuous"] * S_v1)
        phi_k = (phi_weights["uniform"] * S_u2 +
                 phi_weights["vacuous"] * S_v2 +
                 phi_weights["background"] * S_b2)

        tsr_scores = Sk * phi_k

        logger.info("Completed TSR calculation")

        # Create output DataFrame
        topic_names = [f"Topic_{i}" for i in range(K)]
        tsr_df = pd.DataFrame({
            "topic": topic_names,
            "tsr_score": tsr_scores
        }).sort_values("tsr_score", ascending=False)

        # Save results
        output_dir = context.get_output_path(self.name, "")
        output_dir.mkdir(parents=True, exist_ok=True)

        output_path = output_dir / "tsr_scores.csv"
        tsr_df.to_csv(output_path, index=False)

        logger.info(f"Saved TSR scores to {output_path}")

        # Prepare output
        output = {
            "tsr_scores": str(output_path),
            "metrics": {
                "kl_divergence": {
                    "uniform": kl_uniform.tolist(),
                    "vacuous": kl_vacuous.tolist(),
                    "background": kl_background.tolist()
                },
                "cosine_dissimilarity": {
                    "uniform": cos_uniform.tolist(),
                    "vacuous": cos_vacuous.tolist(),
                    "background": cos_background.tolist()
                },
                "pearson_correlation": {
                    "uniform": corr_uniform.tolist(),
                    "vacuous": corr_vacuous.tolist(),
                    "background": corr_background.tolist()
                },
                "sk_scores": Sk.tolist(),
                "phi_scores": phi_k.tolist()
            },
            "stats": {
                "n_topics": K,
                "n_docs": D,
                "vocab_size": W,
                "top_topic": int(tsr_df.iloc[0]["topic"].split("_")[1])
            }
        }

        return output

    # Reference distributions

    def _uniform_distribution(self, vocab_size: int) -> np.ndarray:
        """Uniform distribution over vocabulary."""
        return np.ones(vocab_size) / vocab_size

    def _vacuous_distribution(
        self,
        topic_word: np.ndarray,
        doc_topic: np.ndarray,
        total_words: int
    ) -> np.ndarray:
        """Vacuous semantic distribution.

        P(wi) = Σ_n P(wi|topic_n) * P(topic_n)

        where P(topic_n) = Σ_d P(topic_n|doc_d) / total_words
        """
        K, W = topic_word.shape

        # Compute P(topic_n) for each topic
        P_topic = doc_topic.sum(axis=0) / total_words  # Shape: (K,)

        # Compute P(wi) for each word
        P_omega_v = np.zeros(W)
        for k in range(W):
            for n in range(K):
                P_omega_v[k] += topic_word[n, k] * P_topic[n]

        return P_omega_v

    def _background_distribution(self, n_docs: int) -> float:
        """Background distribution over documents."""
        return 1.0 / n_docs

    # KL Divergence

    def _kl_divergence(
        self,
        P: np.ndarray,
        Q: float,
        axis: int
    ) -> np.ndarray:
        """KL divergence: Σ P * log(P / Q).

        Args:
            P: Distribution matrix
            Q: Reference value (scalar or array)
            axis: 0 for doc-topic, 1 for topic-word

        Returns:
            KL divergence for each topic
        """
        if axis == 0:
            # doc-topic matrix: shape (D, K)
            kl = np.sum(P * np.log(P / Q), axis=0)
        else:
            # topic-word matrix: shape (K, W)
            kl = np.sum(P * np.log(P / Q), axis=1)

        # Handle NaN/Inf
        kl = np.nan_to_num(kl, nan=0.0, posinf=0.0, neginf=0.0)

        return kl

    def _kl_divergence_vacuous(
        self,
        topic_word: np.ndarray,
        P_omega_v: np.ndarray
    ) -> np.ndarray:
        """KL divergence against vacuous distribution.

        Dv[k] = Σ_w P(w|topic_k) * log(P(w|topic_k) / P_omega_v[w])
        """
        K, W = topic_word.shape
        kl_v = np.zeros(K)

        for k in range(K):
            kl_v[k] = np.sum(
                topic_word[k, :] * np.log(topic_word[k, :] / P_omega_v)
            )

        # Handle NaN/Inf
        kl_v = np.nan_to_num(kl_v, nan=0.0, posinf=0.0, neginf=0.0)

        return kl_v

    # Cosine Similarity

    def _cosine_similarity(
        self,
        P: np.ndarray,
        Q: float,
        axis: int
    ) -> np.ndarray:
        """Cosine similarity: Σ(P*Q) / (||P|| * ||Q||).

        Args:
            P: Distribution matrix
            Q: Reference value (scalar)
            axis: 0 for doc-topic, 1 for topic-word

        Returns:
            Cosine similarity for each topic
        """
        if axis == 0:
            # doc-topic matrix: shape (D, K)
            num = np.sum(P * Q, axis=0)
            denom = np.sqrt(np.sum(P**2, axis=0)) * np.sqrt(np.sum(Q**2))
        else:
            # topic-word matrix: shape (K, W)
            num = np.sum(P * Q, axis=1)
            denom = np.sqrt(np.sum(P**2, axis=1)) * np.sqrt(np.sum(Q**2))

        cos = num / denom

        # Handle NaN/Inf
        cos = np.nan_to_num(cos, nan=0.0, posinf=0.0, neginf=0.0)

        return cos

    def _cosine_similarity_vacuous(
        self,
        topic_word: np.ndarray,
        P_omega_v: np.ndarray
    ) -> np.ndarray:
        """Cosine similarity against vacuous distribution.

        Vcos[k] = Σ(P(w|topic_k) * P_omega_v[w]) /
                  (||P(w|topic_k)|| * ||P_omega_v||)
        """
        K, W = topic_word.shape
        cos_v = np.zeros(K)

        for k in range(K):
            X = topic_word[k, :]
            Y = P_omega_v

            num = np.sum(X * Y)
            denom = np.sqrt(np.sum(X**2)) * np.sqrt(np.sum(Y**2))

            cos_v[k] = num / denom if denom > 0 else 0.0

        return cos_v

    # Pearson Correlation

    def _pearson_correlation_uniform(
        self,
        topic_word: np.ndarray
    ) -> np.ndarray:
        """Pearson correlation against uniform distribution.

        Corr(P(w|topic_k), Σ_w P(w|topic))
        """
        K, W = topic_word.shape

        # Sum across topics for each word
        Y_u = topic_word.sum(axis=0)  # Shape: (W,)

        # Compute correlation for each topic
        corr_u = np.zeros(K)
        for k in range(K):
            X = topic_word[k, :]
            corr_u[k] = np.corrcoef(X, Y_u)[0, 1]

        # Handle NaN
        corr_u = np.nan_to_num(corr_u, nan=0.0)

        return corr_u

    def _pearson_correlation_vacuous(
        self,
        topic_word: np.ndarray,
        P_omega_v: np.ndarray
    ) -> np.ndarray:
        """Pearson correlation against vacuous distribution.

        Corr(P(w|topic_k), P_omega_v)
        """
        K, W = topic_word.shape

        corr_v = np.zeros(K)
        for k in range(K):
            X = topic_word[k, :]
            corr_v[k] = np.corrcoef(X, P_omega_v)[0, 1]

        # Handle NaN
        corr_v = np.nan_to_num(corr_v, nan=0.0)

        return corr_v

    def _pearson_correlation_background(
        self,
        doc_topic: np.ndarray
    ) -> np.ndarray:
        """Pearson correlation against background distribution.

        Corr(P(d|topic_k), Σ_d P(d|topic))
        """
        D, K = doc_topic.shape

        # Sum across topics for each document
        Y_b = doc_topic.sum(axis=1)  # Shape: (D,)

        # Compute correlation for each topic
        corr_b = np.zeros(K)
        for k in range(K):
            X = doc_topic[:, k]
            corr_b[k] = np.corrcoef(X, Y_b)[0, 1]

        # Handle NaN
        corr_b = np.nan_to_num(corr_b, nan=0.0)

        return corr_b

    # Weighting functions

    def _cross_weight(self, values: np.ndarray) -> np.ndarray:
        """Stage 1: Cross-weighting.

        c1[i] = value[i] * (sum(values) - value[i]) / sum(values)
        """
        total = values.sum()
        return values * (total - values) / total if total > 0 else values

    def _min_max_normalize(self, values: np.ndarray) -> np.ndarray:
        """Stage 2: Min-max normalization.

        c2[i] = (value[i] - min) / (max - min)
        """
        min_val = values.min()
        max_val = values.max()

        if max_val - min_val > 0:
            return (values - min_val) / (max_val - min_val)
        else:
            return np.zeros_like(values)
