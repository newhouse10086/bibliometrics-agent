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
    """Topic Significance Ranking module - Fixed version.

    FIX: Added checkpoint directory fallback and empty corpus detection.
    """

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
                "topic_word_matrix": {"type": "string"},
                "doc_topic_matrix": {"type": "string"},
                "vocab": {"type": "array", "items": {"type": "string"}},
                "total_words": {"type": "integer"}
            },
            "required": ["topic_word_matrix", "doc_topic_matrix", "vocab", "total_words"]
        }

    def output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "tsr_scores": {"type": "string"},
                "metrics": {"type": "object"},
                "stats": {"type": "object"}
            }
        }

    def config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "weights": {"type": "object"},
                "phi_weights": {"type": "object"}
            }
        }

    def get_hardware_requirements(self, config: dict) -> HardwareSpec:
        return HardwareSpec(
            cpu_cores=1, min_memory_gb=2.0, recommended_memory_gb=4.0,
            gpu_required=False, estimated_runtime_seconds=300,
        )

    def _find_topic_model_files(self, context: RunContext):
        """Fallback: search for topic model files in checkpoint directory."""
        checkpoint_dir = context.checkpoint_dir
        topic_modeler_dir = checkpoint_dir / "topic_modeler"

        if not topic_modeler_dir.exists():
            return None

        tw_file = topic_modeler_dir / "topic_word_distribution.csv"
        dt_file = topic_modeler_dir / "doc_topic_distribution.csv"

        if not (tw_file.exists() and dt_file.exists()):
            return None

        vocab = None
        total_words = None
        preprocessor_dir = checkpoint_dir / "preprocessor"

        if preprocessor_dir.exists():
            vocab_file = preprocessor_dir / "vocab.txt"
            dtm_file = preprocessor_dir / "dtm.csv"
            if vocab_file.exists():
                vocab = vocab_file.read_text(encoding="utf-8").strip().split("\n")
            if dtm_file.exists():
                dtm_df = pd.read_csv(dtm_file, index_col=0)
                total_words = int(dtm_df.values.sum())

        if vocab is None or total_words is None:
            return None

        return tw_file, dt_file, vocab, total_words

    def _check_empty_corpus(self, context: RunContext) -> bool:
        """Check if upstream modules produced empty data."""
        prev = context.previous_outputs
        if "preprocessor" in prev:
            stats = prev["preprocessor"].get("stats", {})
            if stats.get("n_docs", 0) == 0:
                return True
        if "paper_fetcher" in prev:
            if prev["paper_fetcher"].get("num_papers", 0) == 0:
                return True
        return False

    def process(self, input_data: dict, config: dict, context: RunContext) -> dict:
        logger.info("Starting TSR ranking...")

        # Check for empty corpus from upstream
        if self._check_empty_corpus(context):
            logger.warning("Upstream data is empty (0 papers/docs). Skipping TSR ranking.")
            output_dir = context.get_output_path(self.name, "")
            output_dir.mkdir(parents=True, exist_ok=True)
            scores_path = output_dir / "tsr_scores.csv"
            pd.DataFrame(columns=["topic", "tsr_score"]).to_csv(scores_path, index=False)
            return {
                "tsr_scores": str(scores_path),
                "metrics": {"kl_divergence": [], "cosine_dissimilarity": [],
                            "pearson_correlation": [], "sk_scores": [], "phi_scores": []},
                "stats": {"n_topics": 0, "n_docs": 0, "vocab_size": 0, "top_topic": -1}
            }

        topic_word_path = None
        doc_topic_path = None
        vocab = None
        total_words = None

        # Strategy 1: Try input_data
        if "topic_word_matrix" in input_data:
            topic_word_path = Path(input_data["topic_word_matrix"])
            doc_topic_path = Path(input_data["doc_topic_matrix"])
            vocab = input_data["vocab"]
            total_words = input_data["total_words"]
            logger.info("Loaded data from input_data")

        # Strategy 2: Try topic_modeler previous_outputs
        elif "topic_modeler" in context.previous_outputs:
            tm_out = context.previous_outputs["topic_modeler"]
            topic_word_path = Path(tm_out["topic_word_path"])
            doc_topic_path = Path(tm_out["doc_topic_path"])
            if "preprocessor" in context.previous_outputs:
                pp_out = context.previous_outputs["preprocessor"]
                vocab = Path(pp_out["vocab_path"]).read_text(encoding="utf-8").strip().split("\n")
                dtm_df = pd.read_csv(pp_out["dtm_path"], index_col=0)
                total_words = int(dtm_df.values.sum())
            logger.info("Loaded from topic_modeler previous_outputs")

        # Strategy 3: Fallback to checkpoint directory
        else:
            logger.warning("topic_modeler not in previous_outputs, trying checkpoint fallback...")
            result = self._find_topic_model_files(context)
            if result is not None:
                topic_word_path, doc_topic_path, vocab, total_words = result
                logger.info("Loaded from checkpoint directory fallback")
            else:
                raise KeyError(
                    "topic_word_matrix not found in input_data or topic_modeler previous_outputs, "
                    "and checkpoint fallback failed. "
                    "Please ensure topic_modeler has completed successfully before running tsr_ranker."
                )

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

        P_omega_u = self._uniform_distribution(W)
        P_omega_v = self._vacuous_distribution(topic_word, doc_topic, total_words)
        P_omega_b = self._background_distribution(topic_word, total_words)

        kl_u = self._kl_divergence(topic_word, P_omega_u)
        kl_v = self._kl_divergence(topic_word, P_omega_v)
        kl_b = self._kl_divergence(topic_word, P_omega_b)

        cos_u = self._cosine_dissimilarity(topic_word, P_omega_u)
        cos_v = self._cosine_dissimilarity(topic_word, P_omega_v)
        cos_b = self._cosine_dissimilarity(topic_word, P_omega_b)

        pear_u = self._pearson_correlation(topic_word, P_omega_u)
        pear_v = self._pearson_correlation(topic_word, P_omega_v)
        pear_b = self._pearson_correlation(topic_word, P_omega_b)

        w_u = weights.get("uniform", 0.6)
        w_v = weights.get("vacuous", 0.4)
        w_b = weights.get("background", 0.0)

        kl_combined = w_u * kl_u + w_v * kl_v + w_b * kl_b
        cos_combined = w_u * cos_u + w_v * cos_v + w_b * cos_b
        pear_combined = w_u * pear_u + w_v * pear_v + w_b * pear_b

        sk = self._cross_weight_normalize(kl_combined, cos_combined, pear_combined)

        pw_u = phi_weights.get("uniform", 0.25)
        pw_v = phi_weights.get("vacuous", 0.25)
        pw_b = phi_weights.get("background", 0.5)

        phi = self._compute_phi(topic_word, doc_topic, pw_u, pw_v, pw_b, P_omega_u, P_omega_v, P_omega_b)

        tsr_scores = sk * phi

        output_dir = context.get_output_path(self.name, "")
        output_dir.mkdir(parents=True, exist_ok=True)

        scores_df = pd.DataFrame({"topic": range(K), "tsr_score": tsr_scores})
        scores_path = output_dir / "tsr_scores.csv"
        scores_df.to_csv(scores_path, index=False)

        metrics = {
            "kl_divergence": kl_combined.tolist(),
            "cosine_dissimilarity": cos_combined.tolist(),
            "pearson_correlation": pear_combined.tolist(),
            "sk_scores": sk.tolist(),
            "phi_scores": phi.tolist()
        }

        stats = {
            "n_topics": int(K),
            "n_docs": int(D),
            "vocab_size": int(W),
            "top_topic": int(np.argmax(tsr_scores))
        }

        logger.info(f"TSR ranking complete. Top topic: {stats['top_topic']}")

        return {
            "tsr_scores": str(scores_path),
            "metrics": metrics,
            "stats": stats
        }

    def _uniform_distribution(self, W: int) -> np.ndarray:
        return np.ones(W) / W

    def _vacuous_distribution(self, topic_word: np.ndarray, doc_topic: np.ndarray, total_words: int) -> np.ndarray:
        doc_topic_norm = doc_topic / doc_topic.sum(axis=1, keepdims=True)
        P_omega_v = doc_topic_norm.T @ topic_word
        P_omega_v = P_omega_v.sum(axis=0)
        P_omega_v = P_omega_v / P_omega_v.sum()
        return P_omega_v

    def _background_distribution(self, topic_word: np.ndarray, total_words: int) -> np.ndarray:
        P_omega_b = topic_word.sum(axis=0)
        P_omega_b = P_omega_b / P_omega_b.sum()
        return P_omega_b

    def _kl_divergence(self, P: np.ndarray, Q: np.ndarray) -> np.ndarray:
        eps = 1e-10
        P_safe = P + eps
        Q_safe = Q + eps
        P_safe = P_safe / P_safe.sum(axis=1, keepdims=True)
        Q_safe = Q_safe / Q_safe.sum()
        return np.sum(P_safe * np.log(P_safe / Q_safe), axis=1)

    def _cosine_dissimilarity(self, P: np.ndarray, Q: np.ndarray) -> np.ndarray:
        eps = 1e-10
        dot = P @ Q
        norm_P = np.linalg.norm(P, axis=1)
        norm_Q = np.linalg.norm(Q)
        cos_sim = dot / (norm_P * norm_Q + eps)
        return 1.0 - cos_sim

    def _pearson_correlation(self, P: np.ndarray, Q: np.ndarray) -> np.ndarray:
        Q_centered = Q - Q.mean()
        P_centered = P - P.mean(axis=1, keepdims=True)
        numerator = np.sum(P_centered * Q_centered, axis=1)
        denominator = np.sqrt(np.sum(P_centered**2, axis=1) * np.sum(Q_centered**2))
        eps = 1e-10
        return numerator / (denominator + eps)

    def _cross_weight_normalize(self, kl: np.ndarray, cos: np.ndarray, pear: np.ndarray) -> np.ndarray:
        def min_max_norm(x):
            mn, mx = x.min(), x.max()
            if mx - mn < 1e-10:
                return np.ones_like(x) * 0.5
            return (x - mn) / (mx - mn)

        kl_n = min_max_norm(kl)
        cos_n = min_max_norm(cos)
        pear_n = min_max_norm(pear)

        w_kl = 1.0 / (kl_n.sum() + 1e-10)
        w_cos = 1.0 / (cos_n.sum() + 1e-10)
        w_pear = 1.0 / (pear_n.sum() + 1e-10)

        combined = (w_kl * kl_n + w_cos * cos_n + w_pear * pear_n) / (w_kl + w_cos + w_pear)
        return min_max_norm(combined)

    def _compute_phi(self, topic_word, doc_topic, w_u, w_v, w_b, P_u, P_v, P_b):
        eps = 1e-10
        topic_mass = doc_topic.sum(axis=0)
        topic_mass = topic_mass / (topic_mass.sum() + eps)

        kl_u = np.array([self._kl_single(topic_word[k], P_u) for k in range(topic_word.shape[0])])
        kl_v = np.array([self._kl_single(topic_word[k], P_v) for k in range(topic_word.shape[0])])
        kl_b = np.array([self._kl_single(topic_word[k], P_b) for k in range(topic_word.shape[0])])

        phi = w_u * kl_u + w_v * kl_v + w_b * kl_b
        phi = phi * topic_mass

        mn, mx = phi.min(), phi.max()
        if mx - mn < 1e-10:
            return np.ones_like(phi) * 0.5
        return (phi - mn) / (mx - mn)

    def _kl_single(self, p, q):
        eps = 1e-10
        p_s = p + eps
        q_s = q + eps
        p_s = p_s / p_s.sum()
        q_s = q_s / q_s.sum()
        return np.sum(p_s * np.log(p_s / q_s))
