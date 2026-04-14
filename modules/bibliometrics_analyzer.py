"""Bibliometrics Analyzer module — descriptive statistics and bibliometric indicators.

Reads papers.json from paper_fetcher and produces:
- descriptive_stats.json: overall publication statistics
- annual_growth.csv: year-by-year paper counts and growth rates
- top_authors.csv: top N authors by paper count
- top_institutions.csv: top N institutions by paper count
- top_journals.csv: top N journals by paper count
- h_index_summary.csv: H-index for top authors
- bradford_zones.csv: Bradford's law journal zone classification
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from modules.base import BaseModule, HardwareSpec, RunContext

logger = logging.getLogger(__name__)


class BibliometricsAnalyzer(BaseModule):
    """Compute bibliometric indicators and descriptive statistics."""

    @property
    def name(self) -> str:
        return "bibliometrics_analyzer"

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
                "descriptive_stats_path": {"type": "string"},
                "annual_growth_path": {"type": "string"},
                "top_authors_path": {"type": "string"},
                "top_institutions_path": {"type": "string"},
                "top_journals_path": {"type": "string"},
                "h_index_path": {"type": "string"},
                "bradford_path": {"type": "string"},
                "summary": {"type": "object"}
            }
        }

    def config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "top_n_authors": {
                    "type": "integer",
                    "default": 20,
                    "description": "Number of top authors to output"
                },
                "top_n_institutions": {
                    "type": "integer",
                    "default": 20,
                    "description": "Number of top institutions to output"
                },
                "top_n_journals": {
                    "type": "integer",
                    "default": 20,
                    "description": "Number of top journals to output"
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
        """Compute bibliometric statistics from paper metadata."""
        top_n_authors = config.get("top_n_authors", 20)
        top_n_institutions = config.get("top_n_institutions", 20)
        top_n_journals = config.get("top_n_journals", 20)

        papers_json_path = input_data.get("papers_json_path", "")
        if not papers_json_path:
            logger.warning("papers_json_path is empty, bibliometrics analysis cannot be performed")
            # Return empty output
            output_dir = context.get_output_path(self.name, "")
            output_dir.mkdir(parents=True, exist_ok=True)
            empty_stats = {
                "total_papers": 0,
                "year_range": "N/A",
                "unique_authors": 0,
                "unique_institutions": 0,
                "unique_journals": 0,
                "unique_countries": 0,
                "papers_with_doi": 0,
                "papers_with_mesh": 0,
                "mean_citations_per_paper": 0,
            }
            (output_dir / "descriptive_stats.json").write_text(
                json.dumps(empty_stats, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            for fname in ["annual_growth", "top_authors", "top_institutions", "top_journals", "h_index_summary", "bradford_zones"]:
                (output_dir / f"{fname}.csv").write_text("\n", encoding="utf-8")
            return {
                "descriptive_stats_path": str(output_dir / "descriptive_stats.json"),
                "annual_growth_path": str(output_dir / "annual_growth.csv"),
                "top_authors_path": str(output_dir / "top_authors.csv"),
                "top_institutions_path": str(output_dir / "top_institutions.csv"),
                "top_journals_path": str(output_dir / "top_journals.csv"),
                "h_index_path": str(output_dir / "h_index_summary.csv"),
                "bradford_path": str(output_dir / "bradford_zones.csv"),
                "summary": empty_stats,
                "papers_json_path": "",
                "papers_csv_path": input_data.get("papers_csv_path", ""),
            }

        papers_json_path = Path(papers_json_path)
        if not papers_json_path.exists():
            raise FileNotFoundError(f"papers.json not found: {papers_json_path}")

        with open(papers_json_path, "r", encoding="utf-8") as f:
            papers = json.load(f)

        logger.info("Computing bibliometrics for %d papers", len(papers))

        output_dir = context.get_output_path(self.name, "")
        output_dir.mkdir(parents=True, exist_ok=True)

        # --- Annual growth ---
        annual_growth_path = output_dir / "annual_growth.csv"
        year_counts = Counter()
        for p in papers:
            y = p.get("year")
            if y:
                year_counts[y] += 1

        annual_rows = []
        sorted_years = sorted(year_counts.keys())
        for i, year in enumerate(sorted_years):
            count = year_counts[year]
            prev_count = year_counts[sorted_years[i - 1]] if i > 0 else None
            growth_rate = ((count - prev_count) / prev_count * 100) if prev_count and prev_count > 0 else None
            annual_rows.append({
                "year": year,
                "paper_count": count,
                "growth_rate_pct": round(growth_rate, 2) if growth_rate is not None else None
            })
        pd.DataFrame(annual_rows).to_csv(annual_growth_path, index=False, encoding="utf-8")

        # --- Top authors ---
        top_authors_path = output_dir / "top_authors.csv"
        author_counts = Counter()
        author_citations: dict[str, list[int]] = defaultdict(list)  # for H-index
        for p in papers:
            for author in p.get("authors", []):
                name = author.get("name", "")
                if name:
                    author_counts[name] += 1
                    # Collect citation counts for H-index
                    cited = p.get("cited_by_count")
                    if cited is not None:
                        author_citations[name].append(cited)

        top_authors = author_counts.most_common(top_n_authors)
        author_rows = [
            {"author": name, "paper_count": count,
             "country": next(
                 (a.get("country", "") for p2 in papers
                  for a in p2.get("authors", []) if a.get("name") == name and a.get("country")),
                 ""
             )}
            for name, count in top_authors
        ]
        pd.DataFrame(author_rows).to_csv(top_authors_path, index=False, encoding="utf-8")

        # --- Top institutions ---
        top_institutions_path = output_dir / "top_institutions.csv"
        inst_counts = Counter()
        for p in papers:
            seen_insts = set()  # count each institution once per paper
            for author in p.get("authors", []):
                for aff in author.get("affiliations", []):
                    if aff and aff not in seen_insts:
                        inst_counts[aff] += 1
                        seen_insts.add(aff)

        inst_rows = [
            {"institution": name, "paper_count": count}
            for name, count in inst_counts.most_common(top_n_institutions)
        ]
        pd.DataFrame(inst_rows).to_csv(top_institutions_path, index=False, encoding="utf-8")

        # --- Top journals ---
        top_journals_path = output_dir / "top_journals.csv"
        journal_counts = Counter()
        for p in papers:
            j = p.get("journal_name", "")
            if j:
                journal_counts[j] += 1

        journal_rows = [
            {"journal": name, "paper_count": count,
             "issn": next(
                 (p.get("journal_issn", "") for p in papers if p.get("journal_name") == name and p.get("journal_issn")),
                 ""
             )}
            for name, count in journal_counts.most_common(top_n_journals)
        ]
        pd.DataFrame(journal_rows).to_csv(top_journals_path, index=False, encoding="utf-8")

        # --- H-index for top authors ---
        h_index_path = output_dir / "h_index_summary.csv"
        h_rows = []
        for name, _ in top_authors:
            citations = sorted(author_citations.get(name, []), reverse=True)
            h = 0
            for i, c in enumerate(citations):
                if c >= i + 1:
                    h = i + 1
                else:
                    break
            h_rows.append({"author": name, "h_index": h, "total_citations": sum(citations)})
        pd.DataFrame(h_rows).to_csv(h_index_path, index=False, encoding="utf-8")

        # --- Bradford's law zones ---
        bradford_path = output_dir / "bradford_zones.csv"
        if journal_counts:
            sorted_journals = journal_counts.most_common()
            total_papers = sum(journal_counts.values())
            third = total_papers / 3

            # Zone 1: core journals producing first 1/3 of papers
            # Zone 2: journals producing second 1/3
            # Zone 3: remaining journals
            cumulative = 0
            zones = []
            zone_num = 1
            for jname, count in sorted_journals:
                cumulative += count
                if zone_num == 1 and cumulative >= third:
                    zones.append({"journal": jname, "paper_count": count, "zone": 1})
                    zone_num = 2
                elif zone_num == 2 and cumulative >= 2 * third:
                    zones.append({"journal": jname, "paper_count": count, "zone": 2})
                    zone_num = 3
                else:
                    zones.append({"journal": jname, "paper_count": count, "zone": zone_num})

            pd.DataFrame(zones).to_csv(bradford_path, index=False, encoding="utf-8")
        else:
            pd.DataFrame(columns=["journal", "paper_count", "zone"]).to_csv(
                bradford_path, index=False, encoding="utf-8"
            )

        # --- Descriptive stats summary ---
        year_list = [p.get("year") for p in papers if p.get("year")]
        descriptive_stats = {
            "total_papers": len(papers),
            "year_range": f"{min(year_list)}-{max(year_list)}" if year_list else "N/A",
            "unique_authors": len(author_counts),
            "unique_institutions": len(inst_counts),
            "unique_journals": len(journal_counts),
            "unique_countries": len(set(
                a.get("country", "") for p in papers for a in p.get("authors", [])
                if a.get("country")
            )),
            "papers_with_doi": sum(1 for p in papers if p.get("doi")),
            "papers_with_mesh": sum(1 for p in papers if p.get("mesh_terms")),
            "mean_citations_per_paper": round(
                sum(p.get("cited_by_count", 0) or 0 for p in papers) / len(papers), 2
            ) if papers else 0,
        }
        descriptive_stats_path = output_dir / "descriptive_stats.json"
        with open(descriptive_stats_path, "w", encoding="utf-8") as f:
            json.dump(descriptive_stats, f, ensure_ascii=False, indent=2)

        logger.info("Bibliometrics complete: %d papers, %d authors, %d journals",
                     len(papers), len(author_counts), len(journal_counts))

        return {
            "descriptive_stats_path": str(descriptive_stats_path),
            "annual_growth_path": str(annual_growth_path),
            "top_authors_path": str(top_authors_path),
            "top_institutions_path": str(top_institutions_path),
            "top_journals_path": str(top_journals_path),
            "h_index_path": str(h_index_path),
            "bradford_path": str(bradford_path),
            "summary": descriptive_stats,
            # Pass through for downstream modules
            "papers_json_path": input_data.get("papers_json_path", ""),
            "papers_csv_path": input_data.get("papers_csv_path", ""),
        }
