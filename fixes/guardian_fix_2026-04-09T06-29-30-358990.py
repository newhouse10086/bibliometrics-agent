"""
Guardian Agent LLM-Generated Fix
Description: Add early check for empty DTM (zero documents) before calling tm_lda.evaluate_topic_models(). When DTM has 0 rows, return a graceful empty result instead of crashing with ValueError from scipy.sparse.coo_matrix.
Error Type: ValueError
Timestamp: 2026-04-09T06:29:30.358650
"""

"""Fix for topic_modeler — handle empty DTM (zero papers) gracefully.

Root Cause:
  The paper_fetcher returned 0 papers, so the preprocessor produced an empty
  DTM (0 rows). When tm_lda.evaluate_topic_models() receives a zero-sized
  document-term matrix, scipy.sparse.coo_matrix raises:
    ValueError: cannot infer dimensions from zero sized index arrays

Fix:
  Add an early check after loading the DTM. If the DTM has zero documents
  (rows), skip topic modeling and return a graceful result indicating that
  no papers were available for analysis.
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

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
            vocab = np.array(Path(vocab_path).read_text(encoding="utf-8").strip().split("\n"))
        doc_labels = Path(doc_labels_path).read_text(encoding="utf-8").strip().split("\n")

        logger.info("DTM shape: %s, vocab: %d, docs: %d",
                     dtm_matrix.shape, len(vocab), len(doc_labels))

        # --- FIX: Handle empty DTM (zero documents) gracefully ---
        n_docs = dtm_matrix.shape[0]
        if n_docs == 0:
            logger.warning(
                "DTM has zero documents — cannot perform topic modeling. "
                "This usually means the paper_fetcher returned no papers. "
                "Returning empty result."
            )
            # Create placeholder outputs so downstream modules don't crash
            empty_stats = {
                "n_topics": 0,
                "selected_k": 0,
                "coherence": 0.0,
                "message": "No documents available for topic modeling. "
                           "Please check the paper_fetcher output and search query.",
            }
            return {
                "model_path": "",
                "topic_word_path": "",
                "doc_topic_path": "",
                "topic_labels_path": "",
                "ldavis_path": "",
                "wordclouds_dir": "",
                "evaluation_plot_path": "",
                "stats": empty_stats,
            }

        # --- Step 1: Grid search for optimal K ---
        from tmtoolkit.topicmod import tm_lda
        from tmtoolkit.topicmod.evaluate import results_by_parameter

        min_k = config.get("min_topics", 1)
        max_k = config.get("max_topics", 30)
        step = config.get("step", 1)

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
        from tmtoolkit.topicmod.visualize import plot_eval_results

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
        from tmtoolkit.topicmod.model_io import (
            ldamodel_top_topic_words,
            ldamodel_top_topic_docs,
            save_ldamodel_to_pickle,
        )
        from tmtoolkit.topicmod.model_stats import generate_topic_labels_from_top_words

        top_n = config.get("top_n_words", 20)
        topic_word_df = ldamodel_top_topic_words(best_model, top_n=top_n)
        doc_topic_df = ldamodel_top_topic_docs(best_model)

        topic_word_path = output_dir / "topic_words.csv"
        doc_topic_path = output_dir / "doc_topics.csv"
        model_path = output_dir / "lda_model.pickle"

        topic_word_df.to_csv(topic_word_path)
        doc_topic_df.to_csv(doc_topic_path)
        save_ldamodel_to_pickle(best_model, str(model_path))

        # --- Step 5: Generate topic labels ---
        topic_labels = generate_topic_labels_from_top_words(
            best_model, top_n=top_n, lambda_=config.get("lambda_", 0.6)
        )
        topic_labels_path = output_dir / "topic_labels.txt"
        Path(topic_labels_path).write_text("\n".join(topic_labels), encoding="utf-8")

        # --- Step 6: Generate pyLDAvis ---
        from tmtoolkit.topicmod.visualize import parameters_for_ldavis
        import pyLDAvis

        logger.info("Generating pyLDAvis visualization...")
        vis_params = parameters_for_ldavis(best_model)
        vis_data = pyLDAvis.prepare(**vis_params)
        ldavis_path = output_dir / "ldavis.html"
        pyLDAvis.save_html(vis_data, str(ldavis_path))

        # --- Step 7: Generate word clouds ---
        from tmtoolkit.topicmod.visualize import (
            generate_wordclouds_for_topic_words,
            write_wordclouds_to_folder,
        )

        logger.info("Generating word clouds...")
        wordclouds = generate_wordclouds_for_topic_words(best_model, top_n=top_n)
        wordclouds_dir = output_dir / "wordclouds"
        wordclouds_dir.mkdir(exist_ok=True)
        write_wordclouds_to_folder(wordclouds, str(wordclouds_dir))

        # --- Step 8: Collect stats ---
        coherence_val = best_result[2].get(selection_metric, 0.0)

        stats = {
            "n_topics": best_k,
            "selected_k": best_k,
            "coherence": float(coherence_val),
        }

        logger.info("Topic modeling complete: K=%d, coherence=%.4f", best_k, coherence_val)

        return {
            "model_path": str(model_path),
            "topic_word_path": str(topic_word_path),
            "doc_topic_path": str(doc_topic_path),
            "topic_labels_path": str(topic_labels_path),
            "ldavis_path": str(ldavis_path),
            "wordclouds_dir": str(wordclouds_dir),
            "evaluation_plot_path": str(eval_plot_path),
            "stats": stats,
        }

    def _select_best_k(self, eval_by_k, metric, min_k, max_k):
        """Select best K based on the specified metric."""
        import numpy as np

        metric_values = []
        k_values = []
        for k, model, metrics_dict in eval_by_k:
            if metric in metrics_dict:
                k_values.append(k)
                metric_values.append(metrics_dict[metric])

        if not k_values:
            logger.warning("No valid metric values for %s, using default K=%d", metric, min_k)
            return min_k

        # For coherence metrics, higher is better
        best_idx = int(np.argmax(metric_values))
        return k_values[best_idx]
