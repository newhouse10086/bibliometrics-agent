"""
Guardian Agent LLM-Generated Fix
Description: Fix tsr_ranker to gracefully handle missing topic_modeler output when upstream data is empty (0 papers fetched). Returns empty TSR results instead of crashing with KeyError.
Error Type: KeyError
Timestamp: 2026-04-09T08:45:45.337328
"""

"""TSR Ranker Guardian Fix — handles missing topic_modeler output gracefully.

Root Cause:
    paper_fetcher returned 0 papers → preprocessor produced empty corpus (n_docs=0)
    → topic_modeler failed and was skipped → tsr_ranker cannot find topic_modeler
    in context.previous_outputs and raises KeyError.

Fix Strategy:
    When topic_modeler output is missing, check if upstream data is empty.
    If so, return empty TSR results gracefully instead of crashing.
    This allows the pipeline to continue to downstream modules.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from modules.base import BaseModule, HardwareSpec, RunContext

logger = logging.getLogger(__name__)


class TSRRankerFix(BaseModule):
    """Fixed TSR Ranker that gracefully handles missing topic_modeler output."""

    @property
    def name(self) -> str:
        return "tsr_ranker"

    @property
    def version(self) -> str:
        return "1.0.1"

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "topic_word_matrix": {
                    "type": "string",
                    "description": "Path to topic-word distribution CSV"
                },
                "doc_topic_matrix": {
                    "type": "string",
                    "description": "Path to document-topic distribution CSV"
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
                "tsr_scores": {"type": "string", "description": "Path to TSR scores CSV"},
                "metrics": {"type": "object"},
                "stats": {"type": "object"}
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

    def _return_empty_results(self, output_dir: Path) -> dict:
        """Return empty results when no data is available."""
        logger.warning(
            "No topic modeling data available (upstream modules produced empty results). "
            "Returning empty TSR results to allow pipeline to continue."
        )

        # Create empty output files
        tsr_scores_path = output_dir / "tsr_scores.csv"
        pd.DataFrame(columns=["topic", "tsr_score"]).to_csv(tsr_scores_path, index=False)

        metrics_path = output_dir / "metrics.json"
        metrics = {
            "kl_divergence": [],
            "cosine_dissimilarity": [],
            "pearson_correlation": [],
            "sk_scores": [],
            "phi_scores": []
        }
        metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

        return {
            "tsr_scores": str(tsr_scores_path),
            "metrics": metrics,
            "stats": {
                "n_topics": 0,
                "n_docs": 0,
                "vocab_size": 0,
                "top_topic": -1
            }
        }

    def process(
        self,
        input_data: dict,
        config: dict,
        context: RunContext
    ) -> dict:
        """Execute TSR ranking with graceful handling of missing topic_modeler output."""
        logger.info("Starting TSR ranking...")

        output_dir = context.get_output_path(self.name, "")
        output_dir.mkdir(parents=True, exist_ok=True)

        # --- FIX: Handle missing topic_modeler output gracefully ---
        if "topic_word_matrix" in input_data:
            topic_word_path = Path(input_data["topic_word_matrix"])
            doc_topic_path = Path(input_data["doc_topic_matrix"])
            vocab = input_data["vocab"]
            total_words = input_data["total_words"]
        elif "topic_modeler" in context.previous_outputs:
            # Normal path: get from topic_modeler's output
            topic_modeler_output = context.previous_outputs["topic_modeler"]
            topic_word_path = Path(topic_modeler_output["topic_word_path"])
            doc_topic_path = Path(topic_modeler_output["doc_topic_path"])
            if "preprocessor" in context.previous_outputs:
                preprocessor_output = context.previous_outputs["preprocessor"]
                vocab = Path(preprocessor_output["vocab_path"]).read_text(encoding="utf-8").strip().split("\n")
                dtm_path = Path(preprocessor_output["dtm_path"])
                dtm_df = pd.read_csv(dtm_path, index_col=0)
                total_words = int(dtm_df.values.sum())
                logger.info(f"Calculated total_words from DTM: {total_words}")
            else:
                raise KeyError("Cannot find vocab and total_words - preprocessor output not available")
        else:
            # --- FIX: topic_modeler is missing, check if upstream data is empty ---
            logger.warning(
                "topic_modeler output not found in context.previous_outputs. "
                "Checking if upstream data is empty..."
            )

            if "preprocessor" in context.previous_outputs:
                preprocessor_output = context.previous_outputs["preprocessor"]
                stats = preprocessor_output.get("stats", {})
                n_docs = stats.get("n_docs", 0)
                vocab_size = stats.get("vocab_size", 0)

                if n_docs == 0 or vocab_size == 0:
                    logger.warning(
                        f"Upstream data is empty (n_docs={n_docs}, vocab_size={vocab_size}). "
                        f"topic_modeler likely failed due to no data. "
                        f"Returning empty TSR results."
                    )
                    return self._return_empty_results(output_dir)
                else:
                    raise KeyError(
                        f"topic_modeler output is missing but preprocessor has data "
                        f"(n_docs={n_docs}). topic_modeler may have failed unexpectedly."
                    )
            else:
                raise KeyError(
                    "topic_word_matrix not found in input_data or topic_modeler previous_outputs, "
                    "and preprocessor output is also unavailable."
                )

        # --- Normal processing path (unchanged from original) ---
        topic_word_df = pd.read_csv(topic_word_path, index_col=0)
        doc_topic_df = pd.read_csv(doc_topic_path, index_col=0)

        topic_word = topic_word_df.values.astype(float)
        doc_topic = doc_topic_df.values.astype(float)

        K, W = topic_word.shape
        D = doc_topic.shape[0]

        logger.info(f"Input shapes: topic_word={topic_word.shape}, doc_topic={doc_topic.shape}")
        logger.info(f"K={K} topics, W={W} words, D={D} documents, total_words={total_words}")

        weights = config.get("weights", {"uniform": 0.6, "vacuous": 0.4})
        phi_weights = config.get("phi_weights", {"uniform": 0.25, "vacuous": 0.25, "background": 0.5})

        # Compute reference distributions
        P_omega_u = self._uniform_distribution(W)
        P_omega_v = self._vacuous_distribution(topic_word, doc_topic, total_words)
        P_omega_b = self._background_distribution(topic_word, total_words)

        # Compute KL divergence, cosine dissimilarity, Pearson correlation
        kl_u = self._kl_divergence(topic_word, P_omega_u)
        kl_v = self._kl_divergence(topic_word, P_omega_v)
        kl_b = self._kl_divergence(topic_word, P_omega_b)

        cos_u = self._cosine_dissimilarity(topic_word, P_omega_u)
        cos_v = self._cosine_dissimilarity(topic_word, P_omega_v)
        cos_b = self._cosine_dissimilarity(topic_word, P_omega_b)

        pear_u = self._pearson_correlation(topic_word, P_omega_u)
        pear_v = self._pearson_correlation(topic_word, P_omega_v)
        pear_b = self._pearson_correlation(topic_word, P_omega_b)

        # Stage 1: Cross-weighting
        w_u = weights.get("uniform", 0.6)
        w_v = weights.get("vacuous", 0.4)
        w_b = weights.get("background", 0.0)

        sk_kl = w_u * kl_u + w_v * kl_v + w_b * kl_b
        sk_cos = w_u * cos_u + w_v * cos_v + w_b * cos_b
        sk_pear = w_u * abs(pear_u) + w_v * abs(pear_v) + w_b * abs(pear_b)

        # Stage 2: Min-max normalization
        sk_kl_norm = self._minmax_normalize(sk_kl)
        sk_cos_norm = self._minmax_normalize(sk_cos)
        sk_pear_norm = self._minmax_normalize(sk_pear)

        # Stage 3: Combine references for phi
        pw_u = phi_weights.get("uniform", 0.25)
        pw_v = phi_weights.get("vacuous", 0.25)
        pw_b = phi_weights.get("background", 0.5)

        phi_kl = pw_u * kl_u + pw_v * kl_v + pw_b * kl_b
        phi_cos = pw_u * cos_u + pw_v * cos_v + pw_b * cos_b
        phi_pear = pw_u * abs(pear_u) + pw_v * abs(pear_v) + pw_b * abs(pear_b)

        phi_kl_norm = self._minmax_normalize(phi_kl)
        phi_cos_norm = self._minmax_normalize(phi_cos)
        phi_pear_norm = self._minmax_normalize(phi_pear)

        phi = (phi_kl_norm + phi_cos_norm + phi_pear_norm) / 3.0
        sk = (sk_kl_norm + sk_cos_norm + sk_pear_norm) / 3.0

        # Stage 4: Final TSR = Sk × φk
        tsr = sk * phi

        # Save results
        tsr_scores_path = output_dir / "tsr_scores.csv"
        tsr_df = pd.DataFrame({"topic": range(K), "tsr_score": tsr})
        tsr_df.to_csv(tsr_scores_path, index=False)

        metrics = {
            "kl_divergence": kl_u.tolist(),
            "cosine_dissimilarity": cos_u.tolist(),
            "pearson_correlation": pear_u.tolist(),
            "sk_scores": sk.tolist(),
            "phi_scores": phi.tolist()
        }
        metrics_path = output_dir / "metrics.json"
        metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

        top_topic = int(np.argmax(tsr)) if len(tsr) > 0 else -1

        logger.info(f"TSR ranking complete. Top topic: {top_topic}")

        return {
            "tsr_scores": str(tsr_scores_path),
            "metrics": metrics,
            "stats": {
                "n_topics": K,
                "n_docs": D,
                "vocab_size": W,
                "top_topic": top_topic
            }
        }

    def _uniform_distribution(self, W: int) -> np.ndarray:
        return np.ones(W) / W

    def _vacuous_distribution(self, topic_word: np.ndarray, doc_topic: np.ndarray, total_words: int) -> np.ndarray:
        doc_topic_norm = doc_topic / (doc_topic.sum(axis=1, keepdims=True) + 1e-10)
        P_omega_v = doc_topic_norm.T @ topic_word
        P_omega_v = P_omega_v.sum(axis=0)
        P_omega_v = P_omega_v / (P_omega_v.sum() + 1e-10)
        return P_omega_v

    def _background_distribution(self, topic_word: np.ndarray, total_words: int) -> np.ndarray:
        P_omega_b = topic_word.mean(axis=0)
        P_omega_b = P_omega_b / (P_omega_b.sum() + 1e-10)
        return P_omega_b

    def _kl_divergence(self, P: np.ndarray, Q: np.ndarray) -> np.ndarray:
        P = P / (P.sum(axis=1, keepdims=True) + 1e-10)
        Q = Q / (Q.sum() + 1e-10)
        kl = np.sum(P * np.log((P + 1e-10) / (Q + 1e-10)), axis=1)
        return np.maximum(kl, 0)

    def _cosine_dissimilarity(self, P: np.ndarray, Q: np.ndarray) -> np.ndarray:
        P_norm = P / (np.linalg.norm(P, axis=1, keepdims=True) + 1e-10)
        Q_norm = Q / (np.linalg.norm(Q) + 1e-10)
        cos_sim = np.sum(P_norm * Q_norm, axis=1)
        return 1.0 - cos_sim

    def _pearson_correlation(self, P: np.ndarray, Q: np.ndarray) -> np.ndarray:
        P_centered = P - P.mean(axis=1, keepdims=True)
        Q_centered = Q - Q.mean()
        numerator = np.sum(P_centered * Q_centered, axis=1)
        denominator = (np.sqrt(np.sum(P_centered**2, axis=1)) * np.sqrt(np.sum(Q_centered**2)) + 1e-10)
        return numerator / denominator

    def _minmax_normalize(self, arr: np.ndarray) -> np.ndarray:
        arr_min = arr.min()
        arr_max = arr.max()
        if arr_max - arr_min < 1e-10:
            return np.ones_like(arr) * 0.5
        return (arr - arr_min) / (arr_max - arr_min)
