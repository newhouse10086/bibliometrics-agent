"""
Guardian Agent Fix for tsr_ranker
Description: Handle empty corpus gracefully when topic_modeler output is missing or contains None paths.
Error Type: KeyError
Timestamp: 2026-04-09
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
        """Execute TSR ranking."""
        logger.info("Starting TSR ranking...")

        # --- FIX: Handle empty corpus gracefully ---
        # Check if topic_modeler output exists and has valid paths
        topic_modeler_output = context.previous_outputs.get("topic_modeler")

        if topic_modeler_output is not None:
            # topic_modeler ran but may have returned None paths (empty corpus)
            topic_word_path = topic_modeler_output.get("topic_word_path")
            doc_topic_path = topic_modeler_output.get("doc_topic_path")

            if topic_word_path is None or doc_topic_path is None:
                # Empty corpus scenario - topic_modeler skipped modeling
                logger.warning(
                    "topic_modeler returned None paths - empty corpus detected. "
                    "Skipping TSR ranking gracefully."
                )
                return self._empty_corpus_result(
                    n_topics=0,
                    n_docs=0,
                    vocab_size=0,
                    message="No topics available for ranking. The corpus was empty (0 papers fetched)."
                )

            # Valid paths from topic_modeler
            topic_word_path = Path(topic_word_path)
            doc_topic_path = Path(doc_topic_path)

            # Load vocab from preprocessor
            if "preprocessor" in context.previous_outputs:
                preprocessor_output = context.previous_outputs["preprocessor"]
                vocab = Path(preprocessor_output["vocab_path"]).read_text(encoding="utf-8").strip().split("\n")
                dtm_path = Path(preprocessor_output["dtm_path"])
                dtm_df = pd.read_csv(dtm_path, index_col=0)
                total_words = int(dtm_df.values.sum())
                logger.info(f"Calculated total_words from DTM: {total_words}")
            else:
                raise KeyError("Cannot find vocab and total_words - preprocessor output not available")

        elif "topic_word_matrix" in input_data:
            # Direct input data
            topic_word_path = Path(input_data["topic_word_matrix"])
            doc_topic_path = Path(input_data["doc_topic_matrix"])
            vocab = input_data["vocab"]
            total_words = input_data["total_words"]
        else:
            # --- FIX: Check if preprocessor has empty corpus before raising error ---
            if "preprocessor" in context.previous_outputs:
                preprocessor_output = context.previous_outputs["preprocessor"]
                stats = preprocessor_output.get("stats", {})
                n_docs = stats.get("n_docs", 0)
                vocab_size = stats.get("vocab_size", 0)

                if n_docs == 0 or vocab_size == 0:
                    logger.warning(
                        "Empty corpus detected (n_docs=%d, vocab_size=%d). "
                        "Skipping TSR ranking gracefully.",
                        n_docs, vocab_size
                    )
                    return self._empty_corpus_result(
                        n_topics=0,
                        n_docs=n_docs,
                        vocab_size=vocab_size,
                        message=f"No topics available for ranking. Empty corpus (n_docs={n_docs}, vocab_size={vocab_size})."
                    )

            # If we get here, we truly can't find the required data
            raise KeyError(
                "topic_word_matrix not found in input_data or topic_modeler previous_outputs. "
                "Check that topic_modeler ran successfully and produced output."
            )

        # --- Normal processing continues below ---
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
        P_omega_v = self._vacuous_distribution(topic_word, doc_topic, total_words)  # Vacuous
        P_omega_b = self._background_distribution(topic_word, total_words)  # Background

        # Step 2: Compute KL divergence, cosine dissimilarity, Pearson correlation
        kl_u = self._kl_divergence(topic_word, P_omega_u)
        kl_v = self._kl_divergence(topic_word, P_omega_v)
        kl_b = self._kl_divergence(topic_word, P_omega_b)

        cos_u = self._cosine_dissimilarity(topic_word, P_omega_u)
        cos_v = self._cosine_dissimilarity(topic_word, P_omega_v)
        cos_b = self._cosine_dissimilarity(topic_word, P_omega_b)

        pear_u = self._pearson_correlation(topic_word, P_omega_u)
        pear_v = self._pearson_correlation(topic_word, P_omega_v)
        pear_b = self._pearson_correlation(topic_word, P_omega_b)

        # Step 3: Cross-weighting (Stage 1)
        # S_k = w_u * KL_u + w_v * KL_v + w_b * KL_b
        w_u = weights.get("uniform", 0.6)
        w_v = weights.get("vacuous", 0.4)
        w_b = weights.get("background", 0.0)

        sk_raw = w_u * kl_u + w_v * kl_v + w_b * kl_b

        # phi_k = w_u * (1 - |pear_u|) + w_v * (1 - |pear_v|) + w_b * (1 - |pear_b|)
        phi_u_w = phi_weights.get("uniform", 0.25)
        phi_v_w = phi_weights.get("vacuous", 0.25)
        phi_b_w = phi_weights.get("background", 0.5)

        phi_raw = phi_u_w * (1 - np.abs(pear_u)) + phi_v_w * (1 - np.abs(pear_v)) + phi_b_w * (1 - np.abs(pear_b))

        # Step 4: Min-max normalization (Stage 2)
        sk_norm = self._min_max_normalize(sk_raw)
        phi_norm = self._min_max_normalize(phi_raw)

        # Step 5: Combine references (Stage 3) - already done in weighted sum
        # Step 6: Final TSR = Sk × φk (Stage 4)
        tsr_scores = sk_norm * phi_norm

        # Save results
        output_dir = context.get_output_path(self.name, "")
        output_dir.mkdir(parents=True, exist_ok=True)

        tsr_df = pd.DataFrame({
            "topic": range(K),
            "tsr_score": tsr_scores
        })
        tsr_path = output_dir / "tsr_scores.csv"
        tsr_df.to_csv(tsr_path, index=False)

        logger.info(f"TSR scores saved to {tsr_path}")
        logger.info(f"Top topic: {np.argmax(tsr_scores)} with score {np.max(tsr_scores):.4f}")

        return {
            "tsr_scores": str(tsr_path),
            "metrics": {
                "kl_divergence": {
                    "uniform": kl_u.tolist(),
                    "vacuous": kl_v.tolist(),
                    "background": kl_b.tolist()
                },
                "cosine_dissimilarity": {
                    "uniform": cos_u.tolist(),
                    "vacuous": cos_v.tolist(),
                    "background": cos_b.tolist()
                },
                "pearson_correlation": {
                    "uniform": pear_u.tolist(),
                    "vacuous": pear_v.tolist(),
                    "background": pear_b.tolist()
                },
                "sk_scores": sk_norm.tolist(),
                "phi_scores": phi_norm.tolist()
            },
            "stats": {
                "n_topics": K,
                "n_docs": D,
                "vocab_size": W,
                "top_topic": int(np.argmax(tsr_scores))
            }
        }

    def _empty_corpus_result(self, n_topics: int, n_docs: int, vocab_size: int, message: str) -> dict:
        """Return a graceful result for empty corpus scenario."""
        output_dir = Path("checkpoints") / "empty_corpus"
        output_dir.mkdir(parents=True, exist_ok=True)

        tsr_path = output_dir / "tsr_scores.csv"
        pd.DataFrame({"topic": [], "tsr_score": []}).to_csv(tsr_path, index=False)

        return {
            "tsr_scores": str(tsr_path),
            "metrics": {
                "kl_divergence": {"uniform": [], "vacuous": [], "background": []},
                "cosine_dissimilarity": {"uniform": [], "vacuous": [], "background": []},
                "pearson_correlation": {"uniform": [], "vacuous": [], "background": []},
                "sk_scores": [],
                "phi_scores": []
            },
            "stats": {
                "n_topics": n_topics,
                "n_docs": n_docs,
                "vocab_size": vocab_size,
                "top_topic": -1
            },
            "warning": message
        }

    def _uniform_distribution(self, W: int) -> np.ndarray:
        """Compute uniform reference distribution."""
        return np.ones(W) / W

    def _vacuous_distribution(self, topic_word: np.ndarray, doc_topic: np.ndarray, total_words: int) -> np.ndarray:
        """Compute vacuous reference distribution."""
        # P_omega_v = sum_k (P(omega|k) * P(k))
        # P(k) = N_k / N where N_k is words assigned to topic k
        topic_counts = doc_topic.sum(axis=0)  # D x K -> K
        topic_probs = topic_counts / topic_counts.sum()  # Normalize
        P_omega_v = topic_word.T @ topic_probs  # W x K @ K -> W
        return P_omega_v / P_omega_v.sum()

    def _background_distribution(self, topic_word: np.ndarray, total_words: int) -> np.ndarray:
        """Compute background reference distribution."""
        # Average across all topics
        P_omega_b = topic_word.mean(axis=0)  # K x W -> W
        return P_omega_b / P_omega_b.sum()

    def _kl_divergence(self, P: np.ndarray, Q: np.ndarray) -> np.ndarray:
        """Compute KL divergence for each topic row."""
        # Add small epsilon to avoid log(0)
        eps = 1e-10
        P_safe = P + eps
        Q_safe = Q + eps
        # Normalize rows
        P_norm = P_safe / P_safe.sum(axis=1, keepdims=True)
        Q_norm = Q_safe / Q_safe.sum()
        return np.sum(P_norm * np.log(P_norm / Q_norm), axis=1)

    def _cosine_dissimilarity(self, P: np.ndarray, Q: np.ndarray) -> np.ndarray:
        """Compute cosine dissimilarity for each topic row."""
        # Normalize rows
        P_norm = P / (np.linalg.norm(P, axis=1, keepdims=True) + 1e-10)
        Q_norm = Q / (np.linalg.norm(Q) + 1e-10)
        cosine_sim = P_norm @ Q_norm
        return 1 - cosine_sim  # Dissimilarity

    def _pearson_correlation(self, P: np.ndarray, Q: np.ndarray) -> np.ndarray:
        """Compute Pearson correlation for each topic row."""
        correlations = []
        for i in range(P.shape[0]):
            p_row = P[i]
            corr = np.corrcoef(p_row, Q)[0, 1]
            if np.isnan(corr):
                corr = 0.0
            correlations.append(corr)
        return np.array(correlations)

    def _min_max_normalize(self, x: np.ndarray) -> np.ndarray:
        """Min-max normalization."""
        x_min = x.min()
        x_max = x.max()
        if x_max - x_min < 1e-10:
            return np.ones_like(x) * 0.5
        return (x - x_min) / (x_max - x_min)
