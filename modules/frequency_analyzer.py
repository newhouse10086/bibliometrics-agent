"""FrequencyAnalyzer module — Extract keywords and build keyword-year frequency matrix.

Multi-source keyword priority (config keyword_source="auto"):
1. author_keywords — author-specified keywords (highest quality)
2. mesh_terms — MeSH subject descriptors from PubMed
3. keywords_plus — Crossref/Scopus keywords
4. TIAB extraction — tokenized title+abstract (fallback)

MeSH terms are treated as atomic phrases (underscore-joined, not split).

Generates a keyword-year frequency matrix from paper metadata for burst detection
and other temporal analyses.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from modules.base import BaseModule, HardwareSpec, RunContext

logger = logging.getLogger(__name__)


class FrequencyAnalyzer(BaseModule):
    """Extract keywords and build keyword-year frequency matrix."""

    @property
    def name(self) -> str:
        return "frequency_analyzer"

    @property
    def version(self) -> str:
        return "2.0.0"

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "required": ["papers_csv_path"],
            "properties": {
                "papers_csv_path": {
                    "type": "string",
                    "description": "Path to papers CSV file from PaperFetcher",
                },
                "papers_json_path": {
                    "type": "string",
                    "description": "Path to papers.json (preferred for multi-source keywords)",
                },
                "text_column": {
                    "type": "string",
                    "default": "TIAB",
                    "description": "Column containing title + abstract text",
                },
                "year_column": {
                    "type": "string",
                    "default": "year",
                    "description": "Column containing publication year",
                },
                "keywords_column": {
                    "type": "string",
                    "description": "Column containing pre-defined keywords (optional, CSV only)",
                },
            },
        }

    def output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "keyword_year_matrix_path": {
                    "type": "string",
                    "description": "Path to keyword-year frequency matrix (CSV)",
                },
                "top_keywords_path": {
                    "type": "string",
                    "description": "Path to top keywords by frequency (CSV)",
                },
                "keyword_sources_path": {
                    "type": "string",
                    "description": "Path to keyword→source mapping (CSV)",
                },
                "stats": {
                    "type": "object",
                    "properties": {
                        "total_papers": {"type": "integer"},
                        "total_keywords": {"type": "integer"},
                        "year_range": {
                            "type": "object",
                            "properties": {
                                "min": {"type": "integer"},
                                "max": {"type": "integer"},
                            },
                        },
                    },
                },
            },
        }

    def config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "extraction_method": {
                    "type": "string",
                    "enum": ["keywords_field", "text_extraction", "both"],
                    "default": "both",
                    "description": "How to extract keywords",
                },
                "keyword_source": {
                    "type": "string",
                    "enum": ["auto", "author_keywords", "mesh_terms", "keywords_plus", "tiab"],
                    "default": "auto",
                    "description": (
                        "Keyword source priority. 'auto' uses: "
                        "author_keywords > mesh_terms > keywords_plus > tiab"
                    ),
                },
                "min_keyword_length": {
                    "type": "integer",
                    "default": 3,
                    "description": "Minimum keyword length",
                },
                "max_keywords": {
                    "type": "integer",
                    "default": 1000,
                    "description": "Maximum number of keywords to keep",
                },
                "min_frequency": {
                    "type": "integer",
                    "default": 2,
                    "description": "Minimum keyword frequency to include",
                },
                "use_tfidf_filtering": {
                    "type": "boolean",
                    "default": False,
                    "description": "Use TF-IDF to filter common words",
                },
            },
        }

    def get_hardware_requirements(self, config: dict) -> HardwareSpec:
        """Frequency analysis is computationally light."""
        return HardwareSpec(
            cpu_cores=1,
            min_memory_gb=1.0,
            recommended_memory_gb=2.0,
            gpu_required=False,
            estimated_runtime_seconds=60,
        )

    def process(self, input_data: dict, config: dict, context: RunContext) -> dict:
        """Extract keywords and build keyword-year frequency matrix."""
        logger.info("Starting frequency analysis")

        keyword_source = config.get("keyword_source", "auto")

        # Try papers.json first for multi-source keyword support
        papers_json_path = input_data.get("papers_json_path")
        if papers_json_path and Path(papers_json_path).exists():
            return self._process_from_json(papers_json_path, keyword_source, config, context)

        # Fallback to CSV-based processing
        papers_path = Path(input_data["papers_csv_path"])
        df = pd.read_csv(papers_path)
        logger.info("Loaded %d papers from CSV", len(df))

        # Check required columns
        year_col = input_data.get("year_column", "year")
        text_col = input_data.get("text_column", "TIAB")
        keywords_col = input_data.get("keywords_column")

        if year_col not in df.columns:
            raise ValueError(f"Year column '{year_col}' not found in papers CSV")

        # Extract keywords
        extraction_method = config.get("extraction_method", "both")
        logger.info("Extracting keywords using method: %s", extraction_method)

        keyword_sets = []

        if extraction_method in ["keywords_field", "both"] and keywords_col and keywords_col in df.columns:
            logger.info("Extracting keywords from keywords field")
            for idx, row in df.iterrows():
                keywords_str = row.get(keywords_col, "")
                if pd.notna(keywords_str):
                    keywords = self._parse_keywords(keywords_str)
                    keyword_sets.append((row[year_col], keywords))

        if extraction_method in ["text_extraction", "both"] and text_col in df.columns:
            logger.info("Extracting keywords from text")
            for idx, row in df.iterrows():
                text = row.get(text_col, "")
                year = row.get(year_col)
                if pd.notna(text) and pd.notna(year):
                    keywords = self._extract_keywords_from_text(
                        text,
                        min_length=config.get("min_keyword_length", 3),
                    )
                    keyword_sets.append((year, keywords))

        # Build keyword-year frequency matrix
        logger.info("Building keyword-year frequency matrix")
        matrix_df = self._build_keyword_year_matrix(
            keyword_sets,
            max_keywords=config.get("max_keywords", 1000),
            min_frequency=config.get("min_frequency", 2),
        )

        # Handle empty data
        if matrix_df.empty:
            logger.warning("No keyword data available, returning empty results")
            output_dir = context.get_output_path(self.name, "")
            output_dir.mkdir(parents=True, exist_ok=True)
            matrix_path = output_dir / "keyword_year_matrix.csv"
            matrix_df.to_csv(matrix_path)
            top_keywords_path = output_dir / "top_keywords.csv"
            pd.DataFrame(columns=["keyword", "frequency"]).to_csv(top_keywords_path, index=False)
            sources_path = output_dir / "keyword_sources.csv"
            pd.DataFrame(columns=["keyword", "source"]).to_csv(sources_path, index=False)
            return {
                "keyword_year_matrix_path": str(matrix_path),
                "top_keywords_path": str(top_keywords_path),
                "keyword_sources_path": str(sources_path),
                "stats": {
                    "total_papers": len(df),
                    "total_keywords": 0,
                    "year_range": None,
                },
                "num_papers": len(df),
            }

        # Save outputs
        output_dir = context.get_output_path(self.name, "")
        output_dir.mkdir(parents=True, exist_ok=True)

        matrix_path = output_dir / "keyword_year_matrix.csv"
        matrix_df.to_csv(matrix_path)

        top_keywords = matrix_df.sum(axis=1).sort_values(ascending=False)
        top_keywords_df = pd.DataFrame({
            "keyword": top_keywords.index,
            "frequency": top_keywords.values,
        })
        top_keywords_path = output_dir / "top_keywords.csv"
        top_keywords_df.to_csv(top_keywords_path, index=False)

        sources_path = output_dir / "keyword_sources.csv"
        pd.DataFrame(columns=["keyword", "source"]).to_csv(sources_path, index=False)

        years = df[year_col].dropna()
        stats = {
            "total_papers": len(df),
            "total_keywords": len(matrix_df),
            "year_range": {
                "min": int(years.min()) if len(years) > 0 else None,
                "max": int(years.max()) if len(years) > 0 else None,
            } if len(years) > 0 else None,
        }

        logger.info("Frequency analysis complete: %d keywords extracted", stats["total_keywords"])

        return {
            "keyword_year_matrix_path": str(matrix_path),
            "top_keywords_path": str(top_keywords_path),
            "keyword_sources_path": str(sources_path),
            "stats": stats,
        }

    def _process_from_json(
        self,
        papers_json_path: str,
        keyword_source: str,
        config: dict,
        context: RunContext,
    ) -> dict:
        """Process keywords from papers.json with multi-source priority."""
        with open(papers_json_path, "r", encoding="utf-8") as f:
            papers = json.load(f)

        logger.info("Loaded %d papers from papers.json", len(papers))

        # Priority order for "auto" mode
        source_priority = ["author_keywords", "mesh_terms", "keywords_plus"]

        keyword_sets = []
        keyword_source_map: dict[str, str] = {}  # keyword → source

        for paper in papers:
            year = paper.get("year")
            if not year:
                continue

            keywords = []
            source_used = "tiab"  # fallback

            if keyword_source == "auto":
                # Try each source in priority order
                for src in source_priority:
                    vals = paper.get(src, [])
                    if vals:
                        keywords = [self._normalize_mesh_term(k) for k in vals]
                        source_used = src
                        break
            elif keyword_source == "tiab":
                pass  # will extract from text below
            else:
                # Specific source requested
                vals = paper.get(keyword_source, [])
                if vals:
                    keywords = [self._normalize_mesh_term(k) for k in vals]
                    source_used = keyword_source

            # Fallback: extract from title+abstract
            if not keywords:
                tiab = f"{paper.get('title', '')} {paper.get('abstract', '')}".strip()
                if tiab:
                    keywords = self._extract_keywords_from_text(
                        tiab, min_length=config.get("min_keyword_length", 3)
                    )
                    source_used = "tiab"

            if keywords:
                keyword_sets.append((year, keywords))
                for kw in keywords:
                    if kw not in keyword_source_map or source_used != "tiab":
                        keyword_source_map[kw] = source_used

        # Build matrix
        matrix_df = self._build_keyword_year_matrix(
            keyword_sets,
            max_keywords=config.get("max_keywords", 1000),
            min_frequency=config.get("min_frequency", 2),
        )

        output_dir = context.get_output_path(self.name, "")
        output_dir.mkdir(parents=True, exist_ok=True)

        if matrix_df.empty:
            matrix_path = output_dir / "keyword_year_matrix.csv"
            matrix_df.to_csv(matrix_path)
            top_keywords_path = output_dir / "top_keywords.csv"
            pd.DataFrame(columns=["keyword", "frequency"]).to_csv(top_keywords_path, index=False)
            sources_path = output_dir / "keyword_sources.csv"
            pd.DataFrame(columns=["keyword", "source"]).to_csv(sources_path, index=False)
            return {
                "keyword_year_matrix_path": str(matrix_path),
                "top_keywords_path": str(top_keywords_path),
                "keyword_sources_path": str(sources_path),
                "stats": {"total_papers": len(papers), "total_keywords": 0, "year_range": None},
            }

        matrix_path = output_dir / "keyword_year_matrix.csv"
        matrix_df.to_csv(matrix_path)

        top_keywords = matrix_df.sum(axis=1).sort_values(ascending=False)
        top_keywords_df = pd.DataFrame({
            "keyword": top_keywords.index,
            "frequency": top_keywords.values,
        })
        top_keywords_path = output_dir / "top_keywords.csv"
        top_keywords_df.to_csv(top_keywords_path, index=False)

        # Save keyword → source mapping
        sources_rows = [
            {"keyword": kw, "source": keyword_source_map.get(kw, "unknown")}
            for kw in top_keywords.index
        ]
        sources_path = output_dir / "keyword_sources.csv"
        pd.DataFrame(sources_rows).to_csv(sources_path, index=False)

        years_list = [p.get("year") for p in papers if p.get("year")]
        stats = {
            "total_papers": len(papers),
            "total_keywords": len(matrix_df),
            "year_range": {
                "min": min(years_list),
                "max": max(years_list),
            } if years_list else None,
        }

        logger.info("Frequency analysis complete: %d keywords extracted", stats["total_keywords"])

        return {
            "keyword_year_matrix_path": str(matrix_path),
            "top_keywords_path": str(top_keywords_path),
            "keyword_sources_path": str(sources_path),
            "stats": stats,
        }

    @staticmethod
    def _normalize_mesh_term(term: str) -> str:
        """Normalize MeSH/keyword terms — join multi-word phrases with underscores.

        MeSH descriptors like "Machine Learning" are atomic phrases that should
        not be split. We join them with underscores to preserve atomicity.
        """
        term = term.strip()
        if " " in term:
            return term.replace(" ", "_")
        return term

    def _parse_keywords(self, keywords_str: str) -> list[str]:
        """Parse keywords from a keywords field."""
        # Handle different formats: comma-separated, semicolon-separated, etc.
        keywords_str = str(keywords_str)

        # Try different separators
        for sep in [";", ",", "\n"]:
            if sep in keywords_str:
                keywords = [kw.strip() for kw in keywords_str.split(sep)]
                return [kw for kw in keywords if kw]

        # Single keyword
        keywords_str = keywords_str.strip()
        return [keywords_str] if keywords_str else []

    def _extract_keywords_from_text(self, text: str, min_length: int = 3) -> list[str]:
        """Extract keywords from text using simple NLP techniques."""
        # Lowercase
        text = text.lower()

        # Remove punctuation and numbers
        text = re.sub(r"[^a-z\s]", " ", text)

        # Tokenize
        tokens = text.split()

        # Filter by length
        tokens = [t for t in tokens if len(t) >= min_length]

        # Remove common stopwords (basic list)
        stopwords = {
            "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
            "of", "with", "by", "from", "as", "is", "was", "are", "were", "been",
            "be", "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "must", "shall", "can", "need", "dare", "ought",
            "used", "this", "that", "these", "those", "i", "you", "he", "she", "it",
            "we", "they", "what", "which", "who", "whom", "when", "where", "why", "how",
            "all", "each", "every", "both", "few", "more", "most", "other", "some",
            "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too",
            "very", "just", "also", "now", "here", "there", "then", "once", "using",
            "based", "study", "paper", "article", "research", "method", "result",
            "conclusion", "abstract", "introduction", "discussion", "background",
        }
        tokens = [t for t in tokens if t not in stopwords]

        return tokens

    def _build_keyword_year_matrix(
        self,
        keyword_sets: list[tuple[int, list[str]]],
        max_keywords: int = 1000,
        min_frequency: int = 2,
    ) -> pd.DataFrame:
        """Build keyword-year frequency matrix."""
        # Count keyword frequencies per year
        year_keyword_counts: dict[int, Counter] = {}

        for year, keywords in keyword_sets:
            if year not in year_keyword_counts:
                year_keyword_counts[year] = Counter()
            year_keyword_counts[year].update(keywords)

        # Get all years
        years = sorted(year_keyword_counts.keys())
        if not years:
            logger.warning("No year data available, returning empty matrix")
            return pd.DataFrame()
        logger.info(f"Year range: {years[0]} - {years[-1]} ({len(years)} years)")

        # Get top keywords across all years
        total_counter = Counter()
        for counter in year_keyword_counts.values():
            total_counter.update(counter)

        # Filter by minimum frequency and select top keywords
        top_keywords = [
            kw for kw, count in total_counter.most_common(max_keywords)
            if count >= min_frequency
        ]
        logger.info(f"Selected {len(top_keywords)} keywords (min frequency: {min_frequency})")

        # Build matrix
        matrix = np.zeros((len(top_keywords), len(years)), dtype=int)

        for year_idx, year in enumerate(years):
            counter = year_keyword_counts.get(year, Counter())
            for keyword_idx, keyword in enumerate(top_keywords):
                matrix[keyword_idx, year_idx] = counter.get(keyword, 0)

        # Create DataFrame
        matrix_df = pd.DataFrame(matrix, index=top_keywords, columns=years)
        matrix_df.index.name = "keyword"

        return matrix_df
