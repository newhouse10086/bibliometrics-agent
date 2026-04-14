"""
Guardian Agent LLM-Generated Fix
Description: Guard fix for topic_modeler: adds empty corpus detection before calling tmtoolkit.evaluate_topic_models. When DTM has 0 rows (no papers fetched), returns early with empty output artifacts instead of crashing with ValueError.
Error Type: ValueError
Timestamp: 2026-04-09T08:39:26.456515
"""

"""Guardian fix for topic_modeler — handle empty corpus gracefully.

Root cause: paper_fetcher returned 0 papers, so the DTM has 0 rows.
tmtoolkit's evaluate_topic_models fails with:
  ValueError: cannot infer dimensions from zero sized index arrays

Fix: detect empty corpus before calling tmtoolkit and return early
with a clear message and empty output artifacts.
"""

import logging
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def process_with_empty_corpus_check(input_data, config, context):
    """Execute LDA modeling pipeline with empty-corpus guard.

    This function wraps the original TopicModeler.process logic but adds
    an early-exit check when the DTM has zero documents.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output_dir = context.get_output_path("topic_modeler", "")
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

    n_docs = dtm_matrix.shape[0]
    vocab_size = dtm_matrix.shape[1] if dtm_matrix.ndim > 1 else 0

    logger.info("DTM shape: %s, vocab: %d, docs: %d", dtm_matrix.shape, len(vocab), len(doc_labels))

    # --- GUARD: Check for empty corpus ---
    if n_docs == 0 or vocab_size == 0:
        logger.warning(
            "Empty corpus detected (n_docs=%d, vocab_size=%d). "
            "Skipping topic modeling — no papers were fetched or preprocessed.",
            n_docs, vocab_size,
        )

        # Create empty output artifacts so downstream modules don't crash
        model_path = output_dir / "lda_model.pickle"
        topic_word_path = output_dir / "topic_word_dist.csv"
        doc_topic_path = output_dir / "doc_topic_dist.csv"
        topic_labels_path = output_dir / "topic_labels.txt"
        ldavis_path = output_dir / "ldavis.html"
        wordclouds_dir = output_dir / "wordclouds"
        eval_plot_path = output_dir / "topic_selection.png"

        wordclouds_dir.mkdir(parents=True, exist_ok=True)

        # Save empty model placeholder
        with open(model_path, "wb") as f:
            pickle.dump({"n_topics": 0, "status": "empty_corpus"}, f)

        # Save empty CSVs
        pd.DataFrame().to_csv(topic_word_path)
        pd.DataFrame().to_csv(doc_topic_path)

        # Save empty topic labels
        Path(topic_labels_path).write_text("", encoding="utf-8")

        # Save empty LDAvis placeholder
        Path(ldavis_path).write_text("<html><body>No topics — corpus is empty.</body></html>", encoding="utf-8")

        # Save empty evaluation plot
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.text(0.5, 0.5, "No topics — corpus is empty (0 documents)",
                ha="center", va="center", fontsize=14, color="red")
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        plt.tight_layout()
        plt.savefig(eval_plot_path, dpi=150)
        plt.close()

        return {
            "model_path": str(model_path),
            "topic_word_path": str(topic_word_path),
            "doc_topic_path": str(doc_topic_path),
            "topic_labels_path": str(topic_labels_path),
            "ldavis_path": str(ldavis_path),
            "wordclouds_dir": str(wordclouds_dir),
            "evaluation_plot_path": str(eval_plot_path),
            "stats": {
                "n_topics": 0,
                "selected_k": 0,
                "coherence": 0.0,
                "status": "empty_corpus",
                "message": "No papers available for topic modeling. Check paper_fetcher and preprocessor outputs.",
            },
        }

    # --- Normal path: proceed with topic modeling ---
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

    logger.info("Plotting evaluation metrics...")
    fig, ax = plt.subplots(figsize=(10, 6))
    plot_eval_results(eval_by_k)
    eval_plot_path = output_dir / "topic_selection.png"
    plt.tight_layout()
    plt.savefig(eval_plot_path, dpi=150)
    plt.close()

    selection_metric = config.get("selection_metric", "coherence_mimno_2011")
    best_k = _select_best_k(eval_by_k, selection_metric, min_k, max_k)
    logger.info("Selected K=%d based on %s", best_k, selection_metric)

    best_result = [r for r in eval_by_k if r[0] == best_k][0]
    best_model = best_result[1]

    # --- Step 4: Extract results ---
    top_n_words = config.get("top_n_words", 20)
    lambda_ = config.get("lambda_", 0.6)

    topic_word_df = ldamodel_top_topic_words(best_model, vocab, top_n=top_n_words)
    topic_word_path = output_dir / "topic_word_dist.csv"
    topic_word_df.to_csv(topic_word_path)

    doc_topic_df = ldamodel_top_topic_docs(best_model, doc_labels, top_n=5)
    doc_topic_path = output_dir / "doc_topic_dist.csv"
    doc_topic_df.to_csv(doc_topic_path)

    topic_labels = generate_topic_labels_from_top_words(
        best_model, vocab, top_n=top_n_words, lambda_=lambda_
    )
    topic_labels_path = output_dir / "topic_labels.txt"
    Path(topic_labels_path).write_text("\n".join(topic_labels), encoding="utf-8")

    model_path = output_dir / "lda_model.pickle"
    save_ldamodel_to_pickle(best_model, str(model_path))

    # --- Step 5: pyLDAvis ---
    logger.info("Generating pyLDAvis visualization...")
    doc_lengths_arr = doc_lengths(dtm_matrix)
    ldavis_params = parameters_for_ldavis(best_model, dtm_matrix, doc_lengths_arr, vocab)
    ldavis_data = pyLDAvis.prepare(**ldavis_params)
    ldavis_path = output_dir / "ldavis.html"
    pyLDAvis.save_html(ldavis_data, str(ldavis_path))

    # --- Step 6: Word clouds ---
    logger.info("Generating word clouds...")
    wordclouds_dir = output_dir / "wordclouds"
    wordclouds_dir.mkdir(parents=True, exist_ok=True)
    topic_words = generate_wordclouds_for_topic_words(best_model, vocab, top_n=top_n_words)
    write_wordclouds_to_folder(topic_words, str(wordclouds_dir))

    coherence = best_result[2].get(selection_metric, 0.0)

    return {
        "model_path": str(model_path),
        "topic_word_path": str(topic_word_path),
        "doc_topic_path": str(doc_topic_path),
        "topic_labels_path": str(topic_labels_path),
        "ldavis_path": str(ldavis_path),
        "wordclouds_dir": str(wordclouds_dir),
        "evaluation_plot_path": str(eval_plot_path),
        "stats": {
            "n_topics": best_k,
            "selected_k": best_k,
            "coherence": float(coherence),
        },
    }


def _select_best_k(eval_by_k, metric_name, min_k, max_k):
    """Select best K based on the specified metric."""
    metric_values = []
    for k, model, metrics in eval_by_k:
        if metric_name in metrics:
            metric_values.append((k, metrics[metric_name]))

    if not metric_values:
        logger.warning("No valid metric values found, using min_k=%d", min_k)
        return min_k

    # For coherence, higher is better
    if "coherence" in metric_name:
        best_k = max(metric_values, key=lambda x: x[1])[0]
    else:
        # For Arun/CaoJuan, lower is better
        best_k = min(metric_values, key=lambda x: x[1])[0]

    return best_k
