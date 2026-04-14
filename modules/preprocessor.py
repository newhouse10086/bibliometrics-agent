"""Preprocessor module — text cleaning pipeline.

Reference: sample-project/LDA-Origin.ipynb
Flow: lemmatize → remove_punctuation → lowercase → clean special chars →
      filter_clean_tokens(remove_shorter_than=3, remove_numbers) →
      remove_common_tokens(df=0.1) → remove_uncommon_tokens(df=0.005) →
      custom stopwords → collocations(PMI3)
"""

from __future__ import annotations

import json
import logging
import string
from pathlib import Path

import numpy as np
import pandas as pd
from tmtoolkit.corpus import (
    Corpus,
    corpus_unique_chars,
    doc_labels,
    dtm,
    filter_clean_tokens,
    lemmatize,
    print_summary,
    remove_chars,
    remove_common_tokens,
    remove_punctuation,
    remove_tokens,
    remove_uncommon_tokens,
    to_lowercase,
    transform_tokens,
    vocabulary_size,
)

from modules.base import BaseModule, HardwareSpec, RunContext

logger = logging.getLogger(__name__)

# Default domain-agnostic stopwords from sample-project
DEFAULT_STOPWORDS = [
    "life", "people", "system", "use", "week", "home", "research", "day",
    "care", "human", "month", "design", "health", "line", "patients",
    "score", "time", "dose", "clinical", "therapy", "treatment", "woman",
    "patient", "%", "case", "level", "study", "show", "disease", "include",
    "find", "suggest", "result", "group", "area", "aim",
]


class Preprocessor(BaseModule):
    """Text preprocessing module following the LDA-Origin.ipynb workflow.

    Supports keyword boosting: MeSH/author keywords get multiplied weight in the DTM
    when papers_json_path is provided (from the upgraded paper_fetcher).
    """

    @property
    def name(self) -> str:
        return "preprocessor"

    @property
    def version(self) -> str:
        return "2.0.0"

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "documents": {
                    "type": "object",
                    "description": "Dict of {doc_id: text} or path to CSV with NUM,TIAB columns",
                    "oneOf": [
                        {"type": "object", "additionalProperties": {"type": "string"}},
                        {"type": "string", "description": "Path to CSV file"},
                    ],
                },
                "format": {
                    "type": "string",
                    "enum": ["dict", "csv"],
                    "default": "csv",
                    "description": "Input format: 'dict' for {id: text}, 'csv' for file path",
                },
                # Support input from paper_fetcher module
                "papers_csv_path": {
                    "type": "string",
                    "description": "Path to papers CSV from paper_fetcher (alternative to 'documents')"
                },
            },
        }

    def output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "corpus_path": {"type": "string", "description": "Path to saved corpus pickle"},
                "dtm_path": {"type": "string", "description": "Path to DTM CSV"},
                "doc_labels_path": {"type": "string", "description": "Path to doc labels list"},
                "vocab_path": {"type": "string", "description": "Path to vocabulary list"},
                "keyword_source_map_path": {"type": "string", "description": "Path to keyword→source mapping JSON"},
                "stats": {
                    "type": "object",
                    "properties": {
                        "n_docs": {"type": "integer"},
                        "vocab_size": {"type": "integer"},
                        "avg_doc_length": {"type": "number"},
                    },
                },
            },
        }

    def config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "language": {"type": "string", "default": "en"},
                "spacy_model": {"type": "string", "default": "en_core_web_sm"},
                "remove_shorter_than": {"type": "integer", "default": 3},
                "remove_numbers": {"type": "boolean", "default": True},
                "df_max_threshold": {"type": "number", "default": 0.1,
                                     "description": "Remove tokens appearing in > this fraction of docs"},
                "df_min_threshold": {"type": "number", "default": 0.005,
                                     "description": "Remove tokens appearing in < this fraction of docs"},
                "stopwords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": DEFAULT_STOPWORDS,
                    "description": "Additional domain-specific stopwords",
                },
                "use_collocations": {"type": "boolean", "default": False},
                "keyword_boost_factor": {
                    "type": "number",
                    "default": 2.0,
                    "description": "Multiply DTM counts for MeSH/author keywords by this factor"
                },
                "boost_keywords_from": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["mesh_terms", "author_keywords", "keywords_plus"]},
                    "default": ["mesh_terms", "author_keywords"],
                    "description": "Which keyword sources to boost in the DTM"
                },
            },
        }

    def get_hardware_requirements(self, config: dict) -> HardwareSpec:
        return HardwareSpec(
            min_memory_gb=2.0,
            recommended_memory_gb=4.0,
            cpu_cores=1,
            estimated_runtime_seconds=60,
        )

    def process(self, input_data: dict, config: dict, context: RunContext) -> dict:
        """Execute the full preprocessing pipeline from LDA-Origin.ipynb."""
        import pickle

        # --- Step 0: Load data ---
        logger.info("Step 0: Loading documents...")

        # Handle input from paper_fetcher or direct documents
        original_papers_path = None
        if "papers_csv_path" in input_data:
            # Input from paper_fetcher module
            csv_path = input_data["papers_csv_path"]
            input_data = {"documents": csv_path, "format": "csv"}
            logger.info("Using papers from paper_fetcher: %s", csv_path)
            # Save original papers path for downstream modules (frequency_analyzer)
            original_papers_path = Path(csv_path)
        elif input_data.get("format") == "csv" and "documents" in input_data:
            # Direct CSV input - preserve path for downstream modules
            original_papers_path = Path(input_data["documents"])
            logger.info("Using direct CSV input: %s", original_papers_path)

        documents = self._load_documents(input_data)
        logger.info("Loaded %d documents", len(documents))

        # Initialize output directory
        output_dir = context.get_output_path(self.name, "")
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save original papers CSV for downstream modules if available
        papers_csv_for_downstream = None
        if original_papers_path:
            # Use the original papers CSV for frequency_analyzer
            papers_csv_for_downstream = str(original_papers_path)
        else:
            # Create papers CSV from documents for frequency_analyzer
            papers_df = pd.DataFrame([
                {"TIAB": doc, "year": None, "NUM": i}
                for i, doc in enumerate(documents)
            ])
            papers_path = output_dir / "papers_for_frequency.csv"
            papers_df.to_csv(papers_path, index=False)
            papers_csv_for_downstream = str(papers_path)
            logger.info(f"Saved papers for frequency analysis: {papers_path}")

        # Pre-clean documents: strip non-ASCII characters that cause tmtoolkit mask issues
        import unicodedata
        def _clean_text(doc):
            if doc is None or (isinstance(doc, float)):
                return ""
            doc = str(doc)
            # NFKD normalize then keep only ASCII printable
            normalized = unicodedata.normalize('NFKD', doc)
            return ''.join(c if c in string.printable else ' ' for c in normalized)

        if isinstance(documents, dict):
            documents = {k: _clean_text(v) for k, v in documents.items()}
        else:
            documents = [_clean_text(d) for d in documents]
        logger.info("Pre-cleaned %d documents (ASCII-only)", len(documents))

        # --- Step 1: Build Corpus ---
        logger.info("Step 1: Building corpus...")
        try:
            import spacy
            nlp = spacy.load(config.get("spacy_model", "en_core_web_sm"))
        except OSError:
            logger.warning("spaCy model not found, loading without spaCy")
            nlp = None

        corpus = Corpus(documents, language=config.get("language", "en"),
                        spacy_instance=nlp)
        print_summary(corpus)

        # --- Step 2: Lemmatize ---
        logger.info("Step 2: Lemmatizing...")
        lemmatize(corpus)

        # --- Step 3: Remove punctuation ---
        logger.info("Step 3: Removing punctuation...")
        remove_punctuation(corpus)

        # --- Step 4: Lowercase ---
        logger.info("Step 4: Converting to lowercase...")

        def _lowercase(t):
            return "".join(c.lower() if c.isupper() else c for c in t)

        transform_tokens(corpus, func=_lowercase, inplace=True)

        # --- Step 5: Clean special characters ---
        logger.info("Step 5: Cleaning special characters...")
        unprintable = {
            c for c in corpus_unique_chars(corpus)
            if c not in string.printable
        }
        if unprintable:
            logger.info("Removing %d non-printable characters", len(unprintable))
            remove_chars(corpus, unprintable)

        # Remove common junk characters from sample-project
        junk_chars = {
            "[", "\\", "]", "_", "`", "{", "|", "}", "\n", " ", "!", '"',
            "#", "$", "%", "&", "'", "(", ")", "*", ",", "-", ".", "/",
            ":", ";", "<", "=", ">", "?", "@",
        }
        remove_chars(corpus, junk_chars)

        # --- Step 6: Filter tokens ---
        logger.info("Step 6: Filtering tokens...")
        filter_clean_tokens(
            corpus,
            remove_shorter_than=config.get("remove_shorter_than", 3),
            remove_numbers=config.get("remove_numbers", True),
        )

        # --- Step 7: Remove common/uncommon tokens ---
        df_max = config.get("df_max_threshold", 0.1)
        df_min = config.get("df_min_threshold", 0.005)
        logger.info("Step 7: Removing common (df>%.3f) and uncommon (df<%.4f) tokens...",
                     df_max, df_min)
        remove_common_tokens(corpus, df_threshold=df_max)
        remove_uncommon_tokens(corpus, df_threshold=df_min)

        # --- Step 8: Remove custom stopwords ---
        stopwords = config.get("stopwords", DEFAULT_STOPWORDS)
        if stopwords:
            logger.info("Step 8: Removing %d custom stopwords...", len(stopwords))
            remove_tokens(corpus, stopwords, match_type="glob", ignore_case=True)

        # --- Step 9: Build DTM ---
        logger.info("Step 9: Building document-term matrix...")
        dtm_matrix, doc_labels_list, vocab = dtm(
            corpus, return_doc_labels=True, return_vocab=True
        )

        # --- Step 9.5: Keyword boosting ---
        keyword_source_map = {}
        boost_factor = config.get("keyword_boost_factor", 2.0)
        boost_sources = config.get("boost_keywords_from", ["mesh_terms", "author_keywords"])

        if boost_factor > 1.0 and "paper_fetcher" in context.previous_outputs:
            papers_json = context.previous_outputs["paper_fetcher"].get("papers_json_path")
            if papers_json and Path(papers_json).exists():
                import json as _json
                with open(papers_json, "r", encoding="utf-8") as _f:
                    papers_data = _json.load(_f)

                # Collect all boost keywords (lowered, underscore-joined for MeSH)
                boost_keywords = set()
                for p in papers_data:
                    for src in boost_sources:
                        for kw in (p.get(src) or []):
                            normalized = kw.strip().lower().replace(" ", "_")
                            if normalized:
                                boost_keywords.add(normalized)
                                keyword_source_map[normalized] = src

                # Apply boost in DTM
                vocab_list = list(vocab)
                dtm_arr = dtm_matrix.toarray() if hasattr(dtm_matrix, "toarray") else dtm_matrix
                boosted_count = 0
                for col_idx, term in enumerate(vocab_list):
                    if term in boost_keywords:
                        dtm_arr[:, col_idx] = (dtm_arr[:, col_idx] * boost_factor).astype(int)
                        boosted_count += 1

                logger.info("Boosted %d/%d vocabulary terms by %.1fx from %s sources",
                            boosted_count, len(vocab_list), boost_factor, boost_sources)

                # Use the boosted array going forward
                dtm_matrix = dtm_arr

        # --- Step 10: Save outputs ---
        # Save DTM
        if hasattr(dtm_matrix, "toarray"):
            dtm_arr = dtm_matrix.toarray()
        elif isinstance(dtm_matrix, np.ndarray):
            dtm_arr = dtm_matrix
        else:
            dtm_arr = np.asarray(dtm_matrix)

        dtm_df = pd.DataFrame(dtm_arr, index=list(doc_labels_list), columns=list(vocab))
        dtm_path = output_dir / "dtm.csv"
        dtm_df.to_csv(dtm_path)

        # Save keyword source map
        keyword_source_map_path = output_dir / "keyword_source_map.json"
        with open(keyword_source_map_path, "w", encoding="utf-8") as f:
            json.dump(keyword_source_map, f, ensure_ascii=False, indent=2)

        # Save vocab and doc labels
        vocab_path = output_dir / "vocab.txt"
        vocab_path.write_text("\n".join(vocab), encoding="utf-8")

        doc_labels_path = output_dir / "doc_labels.txt"
        doc_labels_path.write_text("\n".join(str(label) for label in doc_labels_list), encoding="utf-8")

        # Save corpus pickle
        corpus_path = output_dir / "corpus.pickle"
        with open(corpus_path, "wb") as f:
            pickle.dump(corpus, f)

        # Compute stats
        doc_lengths = dtm_matrix.sum(axis=1)
        if hasattr(doc_lengths, "A1"):
            doc_lengths = doc_lengths.A1
        avg_length = float(np.mean(doc_lengths)) if len(doc_lengths) > 0 else 0.0

        logger.info("Preprocessing complete: %d docs, %d vocab, avg %.1f tokens/doc",
                     len(doc_labels_list), len(vocab), avg_length)

        return {
            "corpus_path": str(corpus_path),
            "dtm_path": str(dtm_path),
            "doc_labels_path": str(doc_labels_path),
            "vocab_path": str(vocab_path),
            "keyword_source_map_path": str(keyword_source_map_path),
            "papers_csv_path": papers_csv_for_downstream,  # For frequency_analyzer
            "papers_json_path": input_data.get("papers_json_path", ""),  # For frequency_analyzer (multi-source keywords)
            "stats": {
                "n_docs": len(doc_labels_list),
                "vocab_size": len(vocab),
                "avg_doc_length": round(avg_length, 2),
            },
        }

    def _load_documents(self, input_data: dict) -> dict[str, str]:
        """Load documents from dict or CSV file."""
        fmt = input_data.get("format", "csv")

        if fmt == "dict":
            return input_data["documents"]

        # CSV format — expect NUM, TIAB columns (matching sample-project)
        csv_path = input_data["documents"]
        for encoding in ("utf-8", "latin1", "iso-8859-1", "cp1252"):
            try:
                df = pd.read_csv(csv_path, encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError(f"Could not read CSV file: {csv_path}")

        # Try standard column names from sample-project
        if "TIAB" in df.columns and "NUM" in df.columns:
            raw = df.set_index("NUM")["TIAB"].to_dict()
        elif "abstract" in df.columns:
            id_col = "id" if "id" in df.columns else df.columns[0]
            raw = df.set_index(id_col)["abstract"].to_dict()
        else:
            # Use first column as ID, second as text
            raw = df.set_index(df.columns[0])[df.columns[1]].to_dict()

        # Convert integer keys to strings — tmtoolkit Corpus conflicts with integer doc labels
        return {str(k): str(v) if v is not None and not isinstance(v, float) else ""
                for k, v in raw.items()}
