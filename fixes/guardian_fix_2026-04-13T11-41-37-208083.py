"""
Guardian Agent LLM-Generated Fix
Description: Added checkpoint directory fallback for topic model files when topic_modeler is not in previous_outputs. Also handles empty corpus case gracefully.
Error Type: KeyError
Timestamp: 2026-04-13T11:41:37.207632
"""

"""TSR (Topic Significance Ranking) Module - Fixed Version.

Implements topic significance ranking using KL divergence, cosine dissimilarity,
and Pearson correlation against three reference distributions (uniform, vacuous, background).

Reference: sample-project/Topic Significance Ranking.R

FIX: Added fallback mechanism to locate topic model files from checkpoint directory
when topic_modeler is not in context.previous_outputs. Also handles empty corpus case.
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
        return "1.0.1"

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

    def _find_topic_model_files(self, context: RunContext):
        """Fallback: search for topic model files in checkpoint directory.

        When topic_modeler is not in previous_outputs, try to find the
        topic_word_distribution.csv and doc_topic_distribution.csv files
        in the checkpoint directory.

        Returns:
            Tuple of (topic_word_path, doc_topic_path, vocab, total_words) or None
        """
        checkpoint_dir = context.checkpoint_dir

        # Search for topic model output files in checkpoint directory
        topic_word_path = None
        doc_topic_path = None

        # Try common locations
        topic_modeler_dir = checkpoint_dir / "topic_modeler"
        if topic_modeler_dir.exists():
            tw_file = topic_modeler_dir / "topic_word_distribution.csv"
            dt_file = topic_modeler_dir / "doc_topic_distribution.csv"
            if tw_file.exists() and dt_file.exists():
                topic_word_path = tw_file
                doc_topic_path = dt_file
                logger.info(f"Found topic model files in checkpoint: {topic_word_path}")

        if topic_word_path is None or doc_topic_path is None:
            return None

        # Load vocab from preprocessor checkpoint
        vocab = None
        total_words = None

        preprocessor_dir = checkpoint_dir / "preprocessor"
        if preprocessor_dir.exists():
            vocab_file = preprocessor_dir / "vocab.txt"
            dtm_file = preprocessor_dir / "dtm.csv"
            if vocab_file.exists():
                vocab = vocab_file.read_text(encoding="utf-8").strip().split("\n")
                logger.info(f"Loaded vocab from checkpoint: {len(vocab)} words")
            if dtm_file.exists():
                dtm_df = pd.read_csv(dtm_file, index_col=0)
                total_words = int(dtm_df.values.sum())
                logger.info(f"Calculated total_words from DTM checkpoint: {total_words}")

        if vocab is None or total_words is None:
            logger.warning("Could not load vocab/total_words from checkpoint")
            return None

        return topic_word_path, doc_topic_path, vocab, total_words

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

        # Strategy 1: Try input_data first
        if "topic_word_matrix" in input_data:
            topic_word_path = Path(input_data["topic_word_matrix"])
            doc_topic_path = Path(input_data["doc_topic_matrix"])
            vocab = input_data["vocab"]
            total_words = input_data["total_words"]
            logger.info("Loaded data from input_data")

        # Strategy 2: Try topic_modeler previous_outputs
        elif "topic_modeler" in context.previous_outputs:
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
                logger.info(f"Loaded from topic_modeler previous_outputs, total_words={total_words}")
            else:
                raise KeyError("Cannot find vocab and total_words - preprocessor output not available in context.previous_outputs")

        # Strategy 3: Fallback to checkpoint directory
        else:
            logger.warning("topic_modeler not in previous_outputs, attempting checkpoint fallback...")
            checkpoint_result = self._find_topic_model_files(context)
            if checkpoint_result is not None:
                topic_word_path, doc_topic_path, vocab, total_words = checkpoint_result
                logger.info("Successfully loaded topic model files from checkpoint directory")
            else:
                raise KeyError(
                    "topic_word_matrix not found in input_data or topic_modeler previous_outputs, "
                    "and checkpoint fallback failed. "
                    "Please ensure topic_modeler has completed successfully before running tsr_ranker."
                )

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
        kl_uniform = self._kl_divergence(topic_word, P_omega_u)
        kl_vacuous = self._kl_divergence(topic_word, P_omega_v)
        kl_background = self._kl_divergence(topic_word, P_omega_b)

        cos_uniform = self._cosine_dissimilarity(topic_word, P_omega_u)
        cos_vacuous = self._cosine_dissimilarity(topic_word, P_omega_v)
        cos_background = self._cosine_dissimilarity(topic_word, P_omega_b)

        pearson_uniform = self._pearson_correlation(topic_word, P_omega_u)
        pearson_vacuous = self._pearson_correlation(topic_word, P_omega_v)
        pearson_background = self._pearson_correlation(topic_word, P_omega_b)

        # Step 3: Compute Sk (topic significance)
        sk_uniform = self._normalize(kl_uniform)
        sk_vacuous = self._normalize(kl_vacuous)
        sk_background = self._normalize(kl_background)

        sk = (
            weights.get("uniform", 0.6) * sk_uniform
            + weights.get("vacuous", 0.4) * sk_vacuous
            + weights.get("background", 0.0) * sk_background
        )

        # Step 4: Compute φk (topic distinctiveness)
        phi_uniform = self._normalize(cos_uniform)
        phi_vacuous = self._normalize(cos_vacuous)
        phi_background = self._normalize(pearson_background)

        phi = (
            phi_weights.get("uniform", 0.25) * phi_uniform
            + phi_weights.get("vacuous", 0.25) * phi_vacuous
            + phi_weights.get("background", 0.5) * phi_background
        )

        # Step 5: Final TSR score
        tsr_scores = sk * phi

        # Create output directory
        output_dir = context.get_output_path(self.name, "")
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save TSR scores
        tsr_df = pd.DataFrame({
            "topic": range(K),
            "tsr_score": tsr_scores
        })
        tsr_path = output_dir / "tsr_scores.csv"
        tsr_df.to_csv(tsr_path, index=False)

        # Save metrics
        metrics = {
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
                "uniform": pearson_uniform.tolist(),
                "vacuous": pearson_vacuous.tolist(),
                "background": pearson_background.tolist()
            },
            "sk_scores": sk.tolist(),
            "phi_scores": phi.tolist()
        }

        metrics_path = output_dir / "metrics.json"
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2)

        logger.info(f"TSR ranking complete. Top topic: {int(np.argmax(tsr_scores))}")

        return {
            "tsr_scores": str(tsr_path),
            "metrics": str(metrics_path),
            "stats": {
                "n_topics": K,
                "n_docs": D,
                "vocab_size": W,
                "top_topic": int(np.argmax(tsr_scores))
            }
        }

    def _uniform_distribution(self, W: int) -> np.ndarray:
        """Compute uniform distribution over vocabulary."""
        return np.ones(W) / W

    def _vacuous_distribution(
        self, topic_word: np.ndarray, doc_topic: np.ndarray, total_words: int
    ) -> np.ndarray:
        """Compute vacuous distribution."""
        doc_topic_sum = doc_topic.sum(axis=0)  # K
        if doc_topic_sum.sum() == 0:
            return np.ones(topic_word.shape[1]) / topic_word.shape[1]
        topic_weights = doc_topic_sum / doc_topic_sum.sum()
        return topic_weights @ topic_word  # W

    def _background_distribution(
        self, topic_word: np.ndarray, total_words: int
    ) -> np.ndarray:
        """Compute background distribution."""
        if total_words == 0:
            return np.ones(topic_word.shape[1]) / topic_word.shape[1]
        return topic_word.mean(axis=0)

    def _kl_divergence(self, P: np.ndarray, Q: np.ndarray) -> np.ndarray:
        """Compute KL divergence for each topic."""
        # Add small epsilon to avoid log(0)
        eps = 1e-10
        P_safe = P + eps
        Q_safe = Q + eps
        # Normalize
        P_safe = P_safe / P_safe.sum(axis=1, keepdims=True)
        Q_safe = Q_safe / Q_safe.sum()
        return (P_safe * np.log(P_safe / Q_safe)).sum(axis=1)

    def _cosine_dissimilarity(self, P: np.ndarray, Q: np.ndarray) -> np.ndarray:
        """Compute cosine dissimilarity for each topic."""
        eps = 1e-10
        dot_product = P @ Q
        norm_P = np.linalg.norm(P, axis=1)
        norm_Q = np.linalg.norm(Q)
        cosine_sim = dot_product / (norm_P * norm_Q + eps)
        return 1 - cosine_sim

    def _pearson_correlation(self, P: np.ndarray, Q: np.ndarray) -> np.ndarray:
        """Compute Pearson correlation for each topic."""
        correlations = []
        for i in range(P.shape[0]):
            p = P[i]
            corr = np.corrcoef(p, Q)[0, 1]
            if np.isnan(corr):
                corr = 0.0
            correlations.append(corr)
        return np.array(correlations)

    def _normalize(self, x: np.ndarray) -> np.ndarray:
        """Min-max normalization."""
        x_min = x.min()
        x_max = x.max()
        if x_max - x_min == 0:
            return np.ones_like(x)
        return (x - x_min) / (x_max - x_min)
