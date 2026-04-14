"""Country Analyzer module — geographic distribution and collaboration analysis.

Reads papers.json from paper_fetcher, extracts country data from author affiliations,
and produces:
- country_counts.csv: paper counts per country
- country_year_matrix.csv: country × year publication matrix
- country_collaboration_edges.csv: international collaboration network edges
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

import pandas as pd

from modules.base import BaseModule, HardwareSpec, RunContext

logger = logging.getLogger(__name__)


class CountryAnalyzer(BaseModule):
    """Analyze geographic distribution and international collaboration patterns."""

    @property
    def name(self) -> str:
        return "country_analyzer"

    @property
    def version(self) -> str:
        return "1.0.0"

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "required": ["papers_json_path"],
            "properties": {
                "papers_json_path": {
                    "type": "string",
                    "description": "Path to papers.json from paper_fetcher"
                }
            }
        }

    def output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "country_counts_path": {"type": "string"},
                "country_year_matrix_path": {"type": "string"},
                "country_collaboration_path": {"type": "string"},
                "num_countries": {"type": "integer"},
                "num_collaboration_edges": {"type": "integer"},
                "top_countries": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Top N countries with counts"
                }
            }
        }

    def config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "top_n": {
                    "type": "integer",
                    "default": 20,
                    "description": "Number of top countries to report"
                },
                "min_papers_per_country": {
                    "type": "integer",
                    "default": 1,
                    "description": "Minimum papers to include a country in output"
                }
            }
        }

    def get_hardware_requirements(self, config: dict) -> HardwareSpec:
        return HardwareSpec(
            min_memory_gb=0.2,
            recommended_memory_gb=0.5,
            cpu_cores=1,
            estimated_runtime_seconds=5
        )

    def process(self, input_data: dict, config: dict, context: RunContext) -> dict:
        """Analyze country distribution from paper metadata."""
        top_n = config.get("top_n", 20)
        min_papers = config.get("min_papers_per_country", 1)

        papers_json_path = input_data.get("papers_json_path", "")
        if not papers_json_path:
            logger.warning("papers_json_path is empty, country analysis cannot be performed")
            # Return empty output
            output_dir = context.get_output_path(self.name, "")
            output_dir.mkdir(parents=True, exist_ok=True)
            empty_csv = output_dir / "country_counts.csv"
            empty_csv.write_text("country,paper_count\n", encoding="utf-8")
            return {
                "country_counts_path": str(empty_csv),
                "country_year_matrix_path": str(output_dir / "country_year_matrix.csv"),
                "collaboration_edges_path": str(output_dir / "country_collaboration_edges.csv"),
                "summary": {"total_countries": 0, "total_papers": 0},
                "papers_json_path": "",
                "papers_csv_path": input_data.get("papers_csv_path", ""),
            }

        papers_json_path = Path(papers_json_path)
        if not papers_json_path.exists():
            raise FileNotFoundError(f"papers.json not found: {papers_json_path}")

        with open(papers_json_path, "r", encoding="utf-8") as f:
            papers = json.load(f)

        logger.info("Analyzing countries for %d papers", len(papers))

        # Extract country data
        country_paper_counts = Counter()  # country -> paper count
        country_year_counts: dict[str, Counter] = defaultdict(Counter)  # country -> {year: count}
        collab_edges: Counter = Counter()  # (country_a, country_b) -> count

        for paper in papers:
            year = paper.get("year")
            authors = paper.get("authors", [])

            # Get unique countries for this paper
            paper_countries = set()
            for author in authors:
                country = author.get("country", "")
                if country:
                    paper_countries.add(country)

            if not paper_countries:
                continue

            # Count each country's participation
            for country in paper_countries:
                country_paper_counts[country] += 1
                if year:
                    country_year_counts[country][year] += 1

            # Collaboration edges (international co-authorship)
            if len(paper_countries) > 1:
                for c1, c2 in combinations(sorted(paper_countries), 2):
                    collab_edges[(c1, c2)] += 1

        # Filter by minimum papers
        filtered_countries = {
            c: count for c, count in country_paper_counts.items()
            if count >= min_papers
        }

        # Sort by count
        sorted_countries = sorted(filtered_countries.items(), key=lambda x: -x[1])

        # Output directory
        output_dir = context.get_output_path(self.name, "")
        output_dir.mkdir(parents=True, exist_ok=True)

        # 1. country_counts.csv
        country_counts_path = output_dir / "country_counts.csv"
        counts_rows = [
            {"country": c, "country_code": c, "paper_count": count,
             "percentage": round(count / len(papers) * 100, 2) if papers else 0}
            for c, count in sorted_countries
        ]
        pd.DataFrame(counts_rows).to_csv(country_counts_path, index=False, encoding="utf-8")

        # 2. country_year_matrix.csv
        country_year_matrix_path = output_dir / "country_year_matrix.csv"
        if country_year_counts:
            # Only include top N countries for readability
            top_countries = [c for c, _ in sorted_countries[:top_n]]
            all_years = sorted(set(
                y for c in top_countries for y in country_year_counts.get(c, {})
            ))
            matrix_rows = []
            for country in top_countries:
                row = {"country": country}
                for year in all_years:
                    row[str(year)] = country_year_counts.get(country, {}).get(year, 0)
                matrix_rows.append(row)
            pd.DataFrame(matrix_rows).to_csv(
                country_year_matrix_path, index=False, encoding="utf-8"
            )
        else:
            pd.DataFrame(columns=["country"]).to_csv(
                country_year_matrix_path, index=False, encoding="utf-8"
            )

        # 3. country_collaboration_edges.csv
        country_collab_path = output_dir / "country_collaboration_edges.csv"
        if collab_edges:
            edge_rows = [
                {"country_a": c1, "country_b": c2, "weight": count}
                for (c1, c2), count in sorted(collab_edges.items(), key=lambda x: -x[1])
            ]
            pd.DataFrame(edge_rows).to_csv(country_collab_path, index=False, encoding="utf-8")
        else:
            pd.DataFrame(columns=["country_a", "country_b", "weight"]).to_csv(
                country_collab_path, index=False, encoding="utf-8"
            )

        top_countries_list = [
            {"country": c, "paper_count": count}
            for c, count in sorted_countries[:top_n]
        ]

        logger.info("Country analysis complete: %d countries, %d collaboration edges",
                     len(filtered_countries), len(collab_edges))

        return {
            "country_counts_path": str(country_counts_path),
            "country_year_matrix_path": str(country_year_matrix_path),
            "country_collaboration_path": str(country_collab_path),
            "num_countries": len(filtered_countries),
            "num_collaboration_edges": len(collab_edges),
            "top_countries": top_countries_list,
            # Pass through for downstream modules
            "papers_json_path": str(papers_json_path),
            "papers_csv_path": input_data.get("papers_csv_path", ""),
        }
