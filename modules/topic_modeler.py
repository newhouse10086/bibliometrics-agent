"""TopicModeler module — LDA topic modeling with automatic topic number selection.

Reference: sample-project/LDA-Origin.ipynb
Flow:
  1. Build DTM from corpus
  2. Grid search K=1..max_topics using 3 metrics (Arun, CaoJuan, Mimno coherence)
  3. Select optimal K (default: based on coherence peak or elbow)
  4. Fit final model
  5. Generate: topic-word distribution, doc-topic distribution, pyLDAvis, word clouds
  6. Post-processing: topic × country/journal/year matrices, topic top papers
"""

from __future__ import annotations

import json
import logging
import pickle
from collections import Counter, defaultdict
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
        return "2.0.0"

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
                "topic_country_matrix_path": {"type": "string", "description": "Topic × country distribution CSV"},
                "topic_journal_matrix_path": {"type": "string", "description": "Topic × journal distribution CSV"},
                "topic_year_matrix_path": {"type": "string", "description": "Topic × year distribution CSV"},
                "topic_top_papers_path": {"type": "string", "description": "Top papers per topic CSV"},
                "stats": {
                    "type": "object",
                    "properties": {
                        "n_topics": {"type": "integer"},
                        "selected_k": {"type": "integer"},
                        "best_k": {"type": "integer"},
                        "best_metric": {"type": "string"},
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
        # Rough estimate: larger K and vocab need more memory
        return HardwareSpec(
            min_memory_gb=2.0 + max_k * 0.1,
            recommended_memory_gb=4.0 + max_k * 0.2,
            cpu_cores=2,
            estimated_runtime_seconds=300 + max_k * 30,
        )

    def process(self, input_data: dict, config: dict, context: RunContext) -> dict:
        """Execute LDA modeling pipeline from LDA-Origin.ipynb."""
        import matplotlib
        matplotlib.use("Agg")  # Non-interactive backend
        import matplotlib.pyplot as plt
        import pyLDAvis

        output_dir = context.get_output_path(self.name, "")
        output_dir.mkdir(parents=True, exist_ok=True)

        # --- Step 0: Load preprocessed data ---
        logger.info("Loading DTM and vocabulary...")

        # Try to get DTM from input_data or previous outputs
        if "dtm_path" in input_data:
            dtm_path = input_data["dtm_path"]
            vocab_path = input_data["vocab_path"]
            doc_labels_path = input_data["doc_labels_path"]
        elif "preprocessor" in context.previous_outputs:
            # Get from preprocessor's output
            prev_output = context.previous_outputs["preprocessor"]
            dtm_path = prev_output["dtm_path"]
            vocab_path = prev_output["vocab_path"]
            doc_labels_path = prev_output["doc_labels_path"]
        else:
            raise KeyError("dtm_path not found in input or previous outputs")

        dtm_df = pd.read_csv(dtm_path, index_col=0)
        dtm_matrix = dtm_df.values.astype(np.int64)  # Convert to int64 for tmtoolkit
        vocab = np.array(vocab_path)
        if isinstance(vocab_path, str):
            vocab = np.array(Path(vocab_path).read_text(encoding="utf-8").strip().split("\n"))
        doc_labels = Path(doc_labels_path).read_text(encoding="utf-8").strip().split("\n")

        # --- Data validation ---
        n_docs, n_terms = dtm_matrix.shape
        logger.info("DTM shape: %s, vocab: %d, docs: %d", dtm_matrix.shape, len(vocab), len(doc_labels))

        if n_docs == 0 or n_terms == 0:
            raise ValueError(f"DTM has zero-sized dimension: shape={dtm_matrix.shape}. "
                             f"Check preprocessor output — corpus may be too small or filtering too aggressive.")

        # Remove empty documents (all-zero rows) that cause dimension inference errors
        nonempty_mask = dtm_matrix.sum(axis=1) > 0
        n_empty = int((~nonempty_mask).sum())
        if n_empty > 0:
            logger.warning("Removing %d empty documents from DTM", n_empty)
            dtm_matrix = dtm_matrix[nonempty_mask]
            doc_labels = [d for d, m in zip(doc_labels, nonempty_mask) if m]
            if dtm_matrix.shape[0] == 0:
                raise ValueError("All documents are empty after filtering. Check preprocessor output.")

        # Remove empty vocabulary terms (all-zero columns)
        nonempty_vocab_mask = dtm_matrix.sum(axis=0) > 0
        n_empty_vocab = int((~nonempty_vocab_mask).sum())
        if n_empty_vocab > 0:
            logger.warning("Removing %d empty vocabulary terms from DTM", n_empty_vocab)
            dtm_matrix = dtm_matrix[:, nonempty_vocab_mask]
            vocab = vocab[nonempty_vocab_mask]
            if dtm_matrix.shape[1] == 0:
                raise ValueError("All vocabulary terms are empty after filtering. Check preprocessor output.")

        # Check DTM density
        dtm_density = dtm_matrix.nnz / (dtm_matrix.shape[0] * dtm_matrix.shape[1]) if hasattr(dtm_matrix, 'nnz') else np.count_nonzero(dtm_matrix) / (dtm_matrix.shape[0] * dtm_matrix.shape[1])
        logger.info("DTM density: %.4f (%d non-zero entries)", dtm_density, np.count_nonzero(dtm_matrix))

        # --- Step 1: Grid search for optimal K ---
        min_k = config.get("min_topics", 1)
        max_k = config.get("max_topics", 30)
        step = config.get("step", 1)

        # Auto-adjust max_topics based on data size
        # Rule: need at least 5 docs per topic for stable LDA
        max_feasible_k = max(2, dtm_matrix.shape[0] // 5)
        if max_k > max_feasible_k:
            logger.info("Adjusting max_topics from %d to %d based on corpus size (%d docs)",
                        max_k, max_feasible_k, dtm_matrix.shape[0])
            max_k = max_feasible_k
        if min_k > max_k:
            min_k = max(1, max_k - 1)

        logger.info("Evaluating LDA models for K=%d..%d (step=%d)...", min_k, max_k, step)

        const_params = {
            "n_iter": config.get("n_iter", 1000),
            "random_state": config.get("random_state", 42),
            "eta": config.get("eta", 0.1),
        }

        var_params = [{"n_topics": k, "alpha": 1.0 / k} for k in range(min_k, max_k + 1, step)]

        # Try-except with retry: reduce topic range on dimension errors
        eval_results = None
        for attempt in range(3):
            try:
                eval_results = tm_lda.evaluate_topic_models(
                    dtm_matrix,
                    varying_parameters=var_params,
                    constant_parameters=const_params,
                    n_max_processes=1,  # Disable multiprocessing for Windows compatibility
                    metric=config.get("metrics", ["arun_2010", "cao_juan_2009", "coherence_mimno_2011"]),
                    return_models=True,
                )
                break
            except ValueError as e:
                if "dimensions" in str(e).lower() or "zero sized" in str(e).lower():
                    # Reduce max topics and try again
                    max_k = max(2, max_k // 2)
                    min_k = max(1, min_k)
                    var_params = [{"n_topics": k, "alpha": 1.0 / k} for k in range(min_k, max_k + 1, step)]
                    logger.warning("Dimension error on attempt %d, retrying with K=%d..%d: %s",
                                   attempt + 1, min_k, max_k, e)
                    if max_k < 2:
                        raise ValueError(f"Cannot run LDA: corpus too small or too sparse. DTM shape={dtm_matrix.shape}") from e
                else:
                    raise

        if eval_results is None:
            raise ValueError("Failed to evaluate topic models after 3 attempts")

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

        # Get the best model
        best_result = [r for r in eval_by_k if r[0] == best_k][0]
        best_model = best_result[1]["model"]

        # --- Step 4: Generate topic labels ---
        logger.info("Generating topic labels...")
        doc_lengths_arr = doc_lengths(dtm_matrix)
        topic_labels = generate_topic_labels_from_top_words(
            best_model.topic_word_,
            best_model.doc_topic_,
            doc_lengths_arr,
            vocab,
            lambda_=config.get("lambda_", 0.6),
        )

        # --- Step 5: Save topic-word distribution ---
        logger.info("Saving topic-word distribution...")

        # Save full topic-word distribution matrix (for TSR ranking)
        topic_word_full_df = pd.DataFrame(
            best_model.topic_word_,
            index=topic_labels,
            columns=vocab
        )
        topic_word_path = output_dir / "topic_word_distribution.csv"
        topic_word_full_df.to_csv(topic_word_path)

        # Also save top N words per topic (for human readability)
        top_n = config.get("top_n_words", 20)
        topic_word_summary_df = ldamodel_top_topic_words(
            best_model.topic_word_,
            vocab,
            row_labels=topic_labels,
            top_n=top_n,
        )
        topic_word_summary_path = output_dir / "topic_word_summary.csv"
        topic_word_summary_df.to_csv(topic_word_summary_path)

        # --- Step 6: Save document-topic distribution ---
        logger.info("Saving document-topic distribution...")

        # Save full document-topic distribution matrix
        doc_topic_full_df = pd.DataFrame(
            best_model.doc_topic_,
            index=doc_labels,
            columns=topic_labels
        )
        doc_topic_path = output_dir / "doc_topic_distribution.csv"
        doc_topic_full_df.to_csv(doc_topic_path)

        # Also save top documents per topic (for human readability)
        doc_topic_summary_df = ldamodel_top_topic_docs(
            best_model.doc_topic_,
            doc_labels,
            top_n=10,
            topic_labels=topic_labels,
        )
        doc_topic_summary_path = output_dir / "doc_topic_summary.csv"
        doc_topic_summary_df.to_csv(doc_topic_summary_path)

        # --- Step 7: Generate pyLDAvis visualization ---
        logger.info("Generating pyLDAvis visualization...")
        ldavis_params = parameters_for_ldavis(
            best_model.topic_word_,
            best_model.doc_topic_,
            dtm_matrix,
            vocab,
        )
        ldavis_data = pyLDAvis.prepare(**ldavis_params)
        ldavis_path = output_dir / "ldavis.html"
        pyLDAvis.save_html(ldavis_data, str(ldavis_path))

        # --- Step 8: Generate word clouds ---
        logger.info("Generating word clouds...")
        wordclouds_dir = output_dir / "wordclouds"
        wordclouds_dir.mkdir(exist_ok=True)

        topic_clouds = generate_wordclouds_for_topic_words(
            best_model.topic_word_,
            vocab,
            top_n=50,
            topic_labels=topic_labels,
            background_color="white",
            width=400,
            height=300,
        )
        write_wordclouds_to_folder(topic_clouds, str(wordclouds_dir))

        # --- Step 9: Save model pickle ---
        model_path = output_dir / "lda_model.pickle"
        save_ldamodel_to_pickle(
            str(model_path),
            best_model,
            vocab,
            doc_labels,
            dtm=dtm_matrix,
        )

        # Save topic labels
        topic_labels_path = output_dir / "topic_labels.txt"
        topic_labels_path.write_text("\n".join(topic_labels), encoding="utf-8")

        # Compute coherence for selected model
        coherence = float(best_result[1].get("coherence_mimno_2011", 0.0))

        logger.info("LDA modeling complete: K=%d, coherence=%.4f", best_k, coherence)

        # --- Step 10: Post-processing — topic × metadata matrices ---
        topic_country_matrix_path = None
        topic_journal_matrix_path = None
        topic_year_matrix_path = None
        topic_top_papers_path = None

        papers_json_path = None
        if "paper_fetcher" in context.previous_outputs:
            papers_json_path = context.previous_outputs["paper_fetcher"].get("papers_json_path")

        if papers_json_path and Path(papers_json_path).exists():
            logger.info("Building topic × metadata matrices from papers.json...")
            try:
                with open(papers_json_path, "r", encoding="utf-8") as _f:
                    papers_data = json.load(_f)

                postproc = self._build_postprocessing_matrices(
                    papers_data, doc_labels, topic_labels,
                    best_model.doc_topic_, output_dir,
                )
                topic_country_matrix_path = postproc.get("topic_country_matrix_path")
                topic_journal_matrix_path = postproc.get("topic_journal_matrix_path")
                topic_year_matrix_path = postproc.get("topic_year_matrix_path")
                topic_top_papers_path = postproc.get("topic_top_papers_path")
            except Exception as e:
                logger.warning("Post-processing matrices failed: %s", e)
        else:
            logger.info("No papers.json available, skipping post-processing matrices")

        result = {
            "model_path": str(model_path),
            "topic_word_path": str(topic_word_path),
            "topic_word_summary_path": str(topic_word_summary_path),
            "doc_topic_path": str(doc_topic_path),
            "topic_labels_path": str(topic_labels_path),
            "ldavis_path": str(ldavis_path),
            "wordclouds_dir": str(wordclouds_dir),
            "evaluation_plot_path": str(eval_plot_path),
            "stats": {
                "n_topics": int(best_k),
                "best_k": int(best_k),
                "best_metric": selection_metric,
                "coherence": round(coherence, 4),
            },
        }

        if topic_country_matrix_path:
            result["topic_country_matrix_path"] = topic_country_matrix_path
        if topic_journal_matrix_path:
            result["topic_journal_matrix_path"] = topic_journal_matrix_path
        if topic_year_matrix_path:
            result["topic_year_matrix_path"] = topic_year_matrix_path
        if topic_top_papers_path:
            result["topic_top_papers_path"] = topic_top_papers_path

        return result

    def _select_best_k(self, eval_by_k: list, metric: str, min_k: int, max_k: int) -> int:
        """Select best K based on the specified metric.

        For coherence, select K with maximum coherence.
        For Arun and CaoJuan, select K at the elbow point.
        """
        k_values = [r[0] for r in eval_by_k]
        metric_values = [r[1].get(metric, 0.0) for r in eval_by_k]

        if not metric_values:
            return min(5, max_k)

        if "coherence" in metric:
            # Higher coherence is better
            best_idx = int(np.argmax(metric_values))
        else:
            # For Arun/CaoJuan, find elbow (simplified: first local minimum)
            arr = np.array(metric_values)
            diffs = np.diff(arr)
            for i, d in enumerate(diffs):
                if d > 0:  # Found minimum
                    best_idx = i
                    break
            else:
                best_idx = len(metric_values) // 3  # Default to 1/3 of range

        return k_values[best_idx]

    def _build_postprocessing_matrices(
        self,
        papers: list[dict],
        doc_labels: list[str],
        topic_labels: list[str],
        doc_topic_matrix: np.ndarray,
        output_dir: Path,
    ) -> dict:
        """Build topic × country/journal/year matrices and topic top papers.

        Maps documents (by doc_label) back to their paper metadata to compute
        how topics distribute across countries, journals, and years.
        """
        # Build doc_label → paper lookup
        # doc_labels may be NUM indices or PMIDs/DOIs
        label_to_paper: dict[str, dict] = {}

        for paper in papers:
            # Try matching by PMID, DOI, or title
            for key in ["pmid", "doi"]:
                val = paper.get(key, "")
                if val:
                    label_to_paper[str(val)] = paper
            # Also index by title for CSV-based doc labels
            title = paper.get("title", "")
            if title:
                label_to_paper[title[:80]] = paper

        # Also index by position (NUM-based doc labels from CSV)
        for i, paper in enumerate(papers):
            label_to_paper[str(i)] = paper

        n_topics = len(topic_labels)

        # --- Topic × Country matrix ---
        topic_country_counts: dict[str, Counter] = defaultdict(Counter)
        # --- Topic × Journal matrix ---
        topic_journal_counts: dict[str, Counter] = defaultdict(Counter)
        # --- Topic × Year matrix ---
        topic_year_counts: dict[str, Counter] = defaultdict(Counter)
        # --- Topic top papers ---
        topic_papers: dict[str, list[tuple[float, dict]]] = {
            label: [] for label in topic_labels
        }

        for doc_idx, doc_label in enumerate(doc_labels):
            paper = label_to_paper.get(str(doc_label))
            if not paper:
                continue

            # Get topic distribution for this document
            topic_dist = doc_topic_matrix[doc_idx]
            dominant_topic_idx = int(np.argmax(topic_dist))
            dominant_topic = topic_labels[dominant_topic_idx]

            # Country
            countries = set()
            for author in paper.get("authors", []):
                country = author.get("country", "")
                if country:
                    countries.add(country)

            for topic_idx, topic_label in enumerate(topic_labels):
                weight = topic_dist[topic_idx]
                if weight < 0.01:
                    continue
                for country in countries:
                    topic_country_counts[topic_label][country] += weight
                journal = paper.get("journal_name", "")
                if journal:
                    topic_journal_counts[topic_label][journal] += weight
                year = paper.get("year")
                if year:
                    topic_year_counts[topic_label][year] += weight

            # Track top papers per topic (by topic probability)
            topic_papers[dominant_topic].append(
                (float(topic_dist[dominant_topic_idx]), paper)
            )

        result = {}

        # Save topic × country matrix
        if any(topic_country_counts[t] for t in topic_labels):
            all_countries = sorted(set(
                c for t in topic_labels for c in topic_country_counts[t]
            ))
            rows = []
            for t in topic_labels:
                row = {"topic": t}
                for c in all_countries:
                    row[c] = round(topic_country_counts[t].get(c, 0.0), 2)
                rows.append(row)
            path = output_dir / "topic_country_matrix.csv"
            pd.DataFrame(rows).to_csv(path, index=False)
            result["topic_country_matrix_path"] = str(path)
            logger.info("Saved topic × country matrix (%d topics × %d countries)",
                        n_topics, len(all_countries))

        # Save topic × journal matrix (top N journals only)
        if any(topic_journal_counts[t] for t in topic_labels):
            # Get top 50 journals across all topics
            total_journal = Counter()
            for t in topic_labels:
                total_journal.update(topic_journal_counts[t])
            top_journals = [j for j, _ in total_journal.most_common(50)]

            rows = []
            for t in topic_labels:
                row = {"topic": t}
                for j in top_journals:
                    row[j] = round(topic_journal_counts[t].get(j, 0.0), 2)
                rows.append(row)
            path = output_dir / "topic_journal_matrix.csv"
            pd.DataFrame(rows).to_csv(path, index=False)
            result["topic_journal_matrix_path"] = str(path)
            logger.info("Saved topic × journal matrix (%d topics × %d journals)",
                        n_topics, len(top_journals))

        # Save topic × year matrix
        if any(topic_year_counts[t] for t in topic_labels):
            all_years = sorted(set(
                y for t in topic_labels for y in topic_year_counts[t]
            ))
            rows = []
            for t in topic_labels:
                row = {"topic": t}
                for y in all_years:
                    row[str(y)] = round(topic_year_counts[t].get(y, 0.0), 2)
                rows.append(row)
            path = output_dir / "topic_year_matrix.csv"
            pd.DataFrame(rows).to_csv(path, index=False)
            result["topic_year_matrix_path"] = str(path)
            logger.info("Saved topic × year matrix (%d topics × %d years)",
                        n_topics, len(all_years))

        # Save top papers per topic
        top_papers_rows = []
        for topic_label in topic_labels:
            papers_list = topic_papers[topic_label]
            # Sort by topic probability descending
            papers_list.sort(key=lambda x: -x[0])
            for rank, (prob, paper) in enumerate(papers_list[:5], 1):
                top_papers_rows.append({
                    "topic": topic_label,
                    "rank": rank,
                    "probability": round(prob, 4),
                    "title": paper.get("title", ""),
                    "year": paper.get("year", ""),
                    "journal": paper.get("journal_name", ""),
                    "doi": paper.get("doi", ""),
                    "pmid": paper.get("pmid", ""),
                })

        if top_papers_rows:
            path = output_dir / "topic_top_papers.csv"
            pd.DataFrame(top_papers_rows).to_csv(path, index=False)
            result["topic_top_papers_path"] = str(path)
            logger.info("Saved topic top papers (%d entries)", len(top_papers_rows))

        return result
