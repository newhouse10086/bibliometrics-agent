"""
Guardian Agent LLM-Generated Fix
Description: Add validation to check if DTM is empty (0 docs or 0 vocab) before calling tmtoolkit.evaluate_topic_models. Also add bounds checking for max_k relative to n_docs and n_vocab.
Error Type: ValueError
Timestamp: 2026-04-09T07:40:08.215092
"""

"""Fix for topic_modeler module — handles empty DTM gracefully.

Root Cause:
    The preprocessor produced an empty DTM (0 documents, 0 vocabulary), likely because
    the paper_fetcher returned 0 papers. When tmtoolkit's evaluate_topic_models receives
    an empty matrix, it tries to create scipy COO matrices with zero-sized index arrays,
    which raises ValueError: "cannot infer dimensions from zero sized index arrays".

Fix:
    Add validation at the start of process() to check if the DTM is empty before
    calling tmtoolkit. If empty, raise a clear, actionable error message.
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from tmtoolkit.topicmod import tm_lda
from tmtoolkit.topicmod.evaluate import results_by_parameter
from tmtoolkit.topicmod.model_stats import generate_topic_labels_from_top_words
from tmtoolkit.topicmod.model_io import (
    ldamodel_top_topic_words,
    ldamodel_top_topic_docs,
    save_ldamodel_to_pickle,
)
from tmtoolkit.topicmod.visualize import (
    plot_eval_results,
    parameters_for_ldavis,
    generate_wordclouds_for_topic_words,
    write_wordclouds_to_folder,
)
from tmtoolkit.bow.bow_stats import doc_lengths

from modules.base import BaseModule, HardwareSpec, RunContext

logger = logging.getLogger(__name__)


class TopicModeler(BaseModule):
    """LDA topic modeling following the LDA-Origin.ipynb workflow."""

    @property
    def name(self) -> str:
        return "topic_modeler"

    @property
    def version(self) -> str:
        return "0.1.0"

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "required": ["dtm_path", "vocab_path", "doc_labels_path"],
            "properties": {
                "dtm_path": {"type": "string", "description": "Path to DTM CSV from preprocessor"},
                "vocab_path": {"type": "string", "description": "Path to vocab file"},
                "doc_labels_path": {"type": "string", "description": "Path to doc labels file"},
            },
        }

    def output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "model_path": {"type": "string", "description": "Path to saved LDA model pickle"},
                "topic_word_path": {"type": "string", "description": "Topic-word distribution CSV"},
                "doc_topic_path": {"type": "string", "description": "Document-topic distribution CSV"},
                "topic_labels_path": {"type": "string", "description": "Topic labels list"},
                "ldavis_path": {"type": "string", "description": "pyLDAvis HTML file"},
                "wordclouds_dir": {"type": "string", "description": "Directory with word cloud images"},
                "evaluation_plot_path": {"type": "string", "description": "Topic selection metrics plot"},
                "stats": {
                    "type": "object",
                    "properties": {
                        "n_topics": {"type": "integer"},
                        "selected_k": {"type": "integer"},
                        "coherence": {"type": "number"},
                    },
                },
            },
        }

    def config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "min_topics": {"type": "integer", "default": 1},
                "max_topics": {"type": "integer", "default": 30},
                "step": {"type": "integer", "default": 1},
                "n_iter": {"type": "integer", "default": 1000},
                "eta": {"type": "number", "default": 0.1},
                "random_state": {"type": "integer", "default": 42},
                "metrics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["arun_2010", "cao_juan_2009", "coherence_mimno_2011"],
                },
                "selection_metric": {
                    "type": "string",
                    "default": "coherence_mimno_2011",
                    "description": "Which metric to use for selecting best K",
                },
                "top_n_words": {"type": "integer", "default": 20},
                "lambda_": {"type": "number", "default": 0.6,
                            "description": "Relevance parameter for topic labels"},
            },
        }

    def get_hardware_requirements(self, config: dict) -> HardwareSpec:
        max_k = config.get("max_topics", 30)
        return HardwareSpec(
            min_memory_gb=2.0 + max_k * 0.1,
            recommended_memory_gb=4.0 + max_k * 0.2,
            cpu_cores=2,
            estimated_runtime_seconds=300 + max_k * 30,
        )

    def process(self, input_data: dict, config: dict, context: RunContext) -> dict:
        """Execute LDA modeling pipeline from LDA-Origin.ipynb."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import pyLDAvis

        output_dir = context.get_output_path(self.name, "")
        output_dir.mkdir(parents=True, exist_ok=True)

        # --- Step 0: Load preprocessed data ---
        logger.info("Loading DTM and vocabulary...")

        if "dtm_path" in input_data:
            dtm_path = input_data["dtm_path"]
            vocab_path = input_data["vocab_path"]
            doc_labels_path = input_data["doc_labels_path"]
        elif "preprocessor" in context.previous_outputs:
            prev_output = context.previous_outputs["preprocessor"]
            dtm_path = prev_output["dtm_path"]
            vocab_path = prev_output["vocab_path"]
            doc_labels_path = prev_output["doc_labels_path"]
        else:
            raise KeyError("dtm_path not found in input or previous outputs")

        dtm_df = pd.read_csv(dtm_path, index_col=0)
        dtm_matrix = dtm_df.values.astype(np.int64)

        vocab = np.array(vocab_path)
        if isinstance(vocab_path, str):
            vocab_text = Path(vocab_path).read_text(encoding="utf-8").strip()
            vocab = np.array(vocab_text.split("\n")) if vocab_text else np.array([])

        doc_labels_text = Path(doc_labels_path).read_text(encoding="utf-8").strip()
        doc_labels = doc_labels_text.split("\n") if doc_labels_text else []

        n_docs, n_vocab = dtm_matrix.shape
        logger.info("DTM shape: %s, vocab: %d, docs: %d", dtm_matrix.shape, len(vocab), len(doc_labels))

        # --- FIX: Validate DTM is not empty before calling tmtoolkit ---
        if n_docs == 0 or n_vocab == 0:
            raise ValueError(
                f"Cannot perform topic modeling: DTM is empty ({n_docs} documents, {n_vocab} vocabulary terms). "
                f"This usually means the paper_fetcher returned 0 papers, or the preprocessor filtered out all documents. "
                f"Please check the paper_fetcher output and preprocessor configuration (stopwords, thresholds)."
            )

        # Also check that there are non-zero entries in the DTM
        if dtm_matrix.sum() == 0:
            raise ValueError(
                f"Cannot perform topic modeling: DTM contains only zeros ({n_docs} docs x {n_vocab} terms). "
                f"All token counts are zero, which means no valid terms remain after preprocessing."
            )

        # --- Step 1: Grid search for optimal K ---
        min_k = config.get("min_topics", 1)
        max_k = config.get("max_topics", 30)
        step = config.get("step", 1)

        # Ensure max_k doesn't exceed number of documents
        if max_k >= n_docs:
            max_k = max(1, n_docs - 1)
            logger.warning("Reduced max_topics to %d (must be < n_docs=%d)", max_k, n_docs)

        # Ensure max_k doesn't exceed vocabulary size
        if max_k >= n_vocab:
            max_k = max(1, n_vocab - 1)
            logger.warning("Reduced max_topics to %d (must be < vocab_size=%d)", max_k, n_vocab)

        if min_k > max_k:
            min_k = max_k
            logger.warning("Adjusted min_topics to %d to match max_topics", min_k)

        logger.info("Evaluating LDA models for K=%d..%d (step=%d)...", min_k, max_k, step)

        const_params = {
            "n_iter": config.get("n_iter", 1000),
            "random_state": config.get("random_state", 42),
            "eta": config.get("eta", 0.1),
        }

        var_params = [{"n_topics": k, "alpha": 1.0 / k} for k in range(min_k, max_k + 1, step)]

        eval_results = tm_lda.evaluate_topic_models(
            dtm_matrix,
            varying_parameters=var_params,
            constant_parameters=const_params,
            metric=config.get("metrics", ["arun_2010", "cao_juan_2009", "coherence_mimno_2011"]),
            return_models=True,
        )

        eval_by_k = results_by_parameter(eval_results, "n_topics")

        # --- Step 2: Plot evaluation metrics ---
        logger.info("Plotting evaluation metrics...")
        fig, ax = plt.subplots(figsize=(10, 6))
        plot_eval_results(eval_by_k)
        eval_plot_path = output_dir / "topic_selection.png"
        plt.tight_layout()
        plt.savefig(eval_plot_path, dpi=150)
        plt.close()

        # --- Step 3: Select best K ---
        selection_metric = config.get("selection_metric", "coherence_mimno_2011")
        best_k = self._select_best_k(eval_by_k, selection_metric, min_k, max_k)
        logger.info("Selected K=%d based on %s", best_k, selection_metric)

        best_result = [r for r in eval_by_k if r[0] == best_k][0]
        best_model = best_result[1]

        # --- Step 4: Extract topic-word and doc-topic distributions ---
        logger.info("Extracting topic-word and doc-topic distributions...")
        top_n_words = config.get("top_n_words", 20)

        topic_word_df = ldamodel_top_topic_words(best_model, vocab, top_n=top_n_words)
        topic_word_path = output_dir / "topic_words.csv"
        topic_word_df.to_csv(topic_word_path, index=True)

        doc_topic_df = ldamodel_top_topic_docs(best_model, doc_labels, top_n=5)
        doc_topic_path = output_dir / "doc_topics.csv"
        doc_topic_df.to_csv(doc_topic_path, index=True)

        # --- Step 5: Generate topic labels ---
        logger.info("Generating topic labels...")
        lambda_ = config.get("lambda_", 0.6)
        topic_labels = generate_topic_labels_from_top_words(
            best_model, vocab, top_n=top_n_words, lambda_=lambda_
        )
        topic_labels_path = output_dir / "topic_labels.txt"
        Path(topic_labels_path).write_text("\n".join(topic_labels), encoding="utf-8")

        # --- Step 6: Generate pyLDAvis ---
        logger.info("Generating pyLDAvis visualization...")
        try:
            vis_params = parameters_for_ldavis(best_model, dtm_matrix, vocab)
            vis_data = pyLDAvis.prepare(**vis_params)
            ldavis_path = output_dir / "ldavis.html"
            pyLDAvis.save_html(vis_data, str(ldavis_path))
        except Exception as e:
            logger.warning("Failed to generate pyLDAvis: %s", e)
            ldavis_path = None

        # --- Step 7: Generate word clouds ---
        logger.info("Generating word clouds...")
        try:
            topic_words_dict = {}
            for topic_idx in range(best_k):
                topic_words = topic_word_df.iloc[topic_idx].tolist()
                topic_words_dict[f"Topic {topic_idx + 1}"] = topic_words

            wordclouds_dir = output_dir / "wordclouds"
            wordclouds_dir.mkdir(exist_ok=True)
            write_wordclouds_to_folder(topic_words_dict, str(wordclouds_dir))
        except Exception as e:
            logger.warning("Failed to generate word clouds: %s", e)
            wordclouds_dir = None

        # --- Step 8: Save model ---
        model_path = output_dir / "lda_model.pickle"
        save_ldamodel_to_pickle(best_model, str(model_path))

        # --- Step 9: Compute stats ---
        coherence_score = best_result[2].get(selection_metric, None)

        stats = {
            "n_topics": best_k,
            "selected_k": best_k,
            "coherence": float(coherence_score) if coherence_score is not None else None,
            "n_docs": n_docs,
            "vocab_size": n_vocab,
        }

        logger.info("Topic modeling complete: K=%d, coherence=%.4f", best_k, coherence_score or 0)

        return {
            "model_path": str(model_path),
            "topic_word_path": str(topic_word_path),
            "doc_topic_path": str(doc_topic_path),
            "topic_labels_path": str(topic_labels_path),
            "ldavis_path": str(ldavis_path) if ldavis_path else None,
            "wordclouds_dir": str(wordclouds_dir) if wordclouds_dir else None,
            "evaluation_plot_path": str(eval_plot_path),
            "stats": stats,
        }

    def _select_best_k(self, eval_by_k, metric, min_k, max_k):
        """Select best K based on the specified metric."""
        metric_values = [(r[0], r[2].get(metric)) for r in eval_by_k if r[2].get(metric) is not None]

        if not metric_values:
            logger.warning("No valid metric values for %s, using default K=%d", metric, min_k)
            return min_k

        # For coherence metrics, higher is better
        if "coherence" in metric:
            best_k = max(metric_values, key=lambda x: x[1])[0]
        else:
            # For Arun/CaoJuan, lower is better (but may have local minima)
            best_k = min(metric_values, key=lambda x: x[1])[0]

        return best_k
