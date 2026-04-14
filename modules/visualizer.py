"""Visualizer module — publication-style figures for bibliometric analysis.

Generates matplotlib + plotly figures from pipeline outputs:
1. Annual publication trend (dual-axis line + bar)
2. Country collaboration choropleth map
3. Top N authors/institutions/journals bar charts
4. Keyword burst timeline heatmap
5. TSR ranking radar chart
6. Topic evolution Sankey diagram
7. Co-word network visualization
8. Author collaboration network visualization
9. Topic × country heatmap
10. Bradford's law zone chart
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from modules.base import BaseModule, HardwareSpec, RunContext

logger = logging.getLogger(__name__)


class Visualizer(BaseModule):
    """Generate publication-style figures from pipeline module outputs."""

    @property
    def name(self) -> str:
        return "visualizer"

    @property
    def version(self) -> str:
        return "1.0.0"

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "papers_json_path": {
                    "type": "string",
                    "description": "Path to papers.json from paper_fetcher",
                },
            },
        }

    def output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "figures_dir": {
                    "type": "string",
                    "description": "Directory containing all generated figures",
                },
                "figures": {
                    "type": "object",
                    "description": "Mapping of figure name to file path",
                    "additionalProperties": {"type": "string"},
                },
            },
        }

    def config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["png", "svg", "pdf"],
                    "default": "png",
                    "description": "Output figure format",
                },
                "dpi": {
                    "type": "integer",
                    "default": 150,
                    "description": "Figure resolution",
                },
                "top_n": {
                    "type": "integer",
                    "default": 15,
                    "description": "Top N items for bar charts",
                },
                "style": {
                    "type": "string",
                    "default": "seaborn-v0_8-whitegrid",
                    "description": "Matplotlib style",
                },
            },
        }

    def get_hardware_requirements(self, config: dict) -> HardwareSpec:
        return HardwareSpec(
            min_memory_gb=1.0,
            recommended_memory_gb=2.0,
            cpu_cores=1,
            estimated_runtime_seconds=60,
        )

    def process(self, input_data: dict, config: dict, context: RunContext) -> dict:
        """Generate all configured figures."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        style = config.get("style", "seaborn-v0_8-whitegrid")
        try:
            plt.style.use(style)
        except OSError:
            plt.style.use("default")

        fmt = config.get("format", "png")
        dpi = config.get("dpi", 150)
        top_n = config.get("top_n", 15)

        output_dir = context.get_output_path(self.name, "figures")
        output_dir.mkdir(parents=True, exist_ok=True)

        figures = {}

        # Gather data from previous module outputs
        papers_json_path = input_data.get("papers_json_path")
        if "paper_fetcher" in context.previous_outputs:
            if not papers_json_path:
                papers_json_path = context.previous_outputs["paper_fetcher"].get("papers_json_path")

        papers = None
        if papers_json_path and Path(papers_json_path).exists():
            with open(papers_json_path, "r", encoding="utf-8") as f:
                papers = json.load(f)

        # --- Figure 1: Annual publication trend ---
        bib_outputs = context.previous_outputs.get("bibliometrics_analyzer", {})
        annual_path = bib_outputs.get("annual_growth_path")
        if annual_path and Path(annual_path).exists():
            try:
                fig_path = self._plot_annual_trend(annual_path, output_dir, fmt, dpi)
                figures["annual_trend"] = str(fig_path)
            except Exception as e:
                logger.warning("Failed to plot annual trend: %s", e)

        # --- Figure 2: Top authors ---
        top_authors_path = bib_outputs.get("top_authors_path")
        if top_authors_path and Path(top_authors_path).exists():
            try:
                fig_path = self._plot_top_n_bar(
                    top_authors_path, "author", "paper_count",
                    f"Top {top_n} Authors", output_dir, "top_authors", fmt, dpi, top_n
                )
                figures["top_authors"] = str(fig_path)
            except Exception as e:
                logger.warning("Failed to plot top authors: %s", e)

        # --- Figure 3: Top institutions ---
        top_inst_path = bib_outputs.get("top_institutions_path")
        if top_inst_path and Path(top_inst_path).exists():
            try:
                fig_path = self._plot_top_n_bar(
                    top_inst_path, "institution", "paper_count",
                    f"Top {top_n} Institutions", output_dir, "top_institutions", fmt, dpi, top_n
                )
                figures["top_institutions"] = str(fig_path)
            except Exception as e:
                logger.warning("Failed to plot top institutions: %s", e)

        # --- Figure 4: Top journals ---
        top_journals_path = bib_outputs.get("top_journals_path")
        if top_journals_path and Path(top_journals_path).exists():
            try:
                fig_path = self._plot_top_n_bar(
                    top_journals_path, "journal", "paper_count",
                    f"Top {top_n} Journals", output_dir, "top_journals", fmt, dpi, top_n
                )
                figures["top_journals"] = str(fig_path)
            except Exception as e:
                logger.warning("Failed to plot top journals: %s", e)

        # --- Figure 5: Country distribution ---
        country_outputs = context.previous_outputs.get("country_analyzer", {})
        country_counts_path = country_outputs.get("country_counts_path")
        if country_counts_path and Path(country_counts_path).exists():
            try:
                fig_path = self._plot_top_n_bar(
                    country_counts_path, "country", "paper_count",
                    f"Top {top_n} Countries", output_dir, "top_countries", fmt, dpi, top_n
                )
                figures["top_countries"] = str(fig_path)
            except Exception as e:
                logger.warning("Failed to plot top countries: %s", e)

        # --- Figure 6: Bradford's law zones ---
        bradford_path = bib_outputs.get("bradford_path")
        if bradford_path and Path(bradford_path).exists():
            try:
                fig_path = self._plot_bradford(bradford_path, output_dir, fmt, dpi)
                figures["bradford_zones"] = str(fig_path)
            except Exception as e:
                logger.warning("Failed to plot Bradford zones: %s", e)

        # --- Figure 7: Keyword burst timeline ---
        freq_outputs = context.previous_outputs.get("frequency_analyzer", {})
        kw_matrix_path = freq_outputs.get("keyword_year_matrix_path")
        if kw_matrix_path and Path(kw_matrix_path).exists():
            try:
                fig_path = self._plot_keyword_heatmap(
                    kw_matrix_path, output_dir, fmt, dpi, top_n=20
                )
                figures["keyword_timeline"] = str(fig_path)
            except Exception as e:
                logger.warning("Failed to plot keyword heatmap: %s", e)

        # --- Figure 8: TSR ranking ---
        tsr_outputs = context.previous_outputs.get("tsr_ranker", {})
        tsr_path = tsr_outputs.get("tsr_scores")
        if tsr_path and Path(tsr_path).exists():
            try:
                fig_path = self._plot_tsr_ranking(tsr_path, output_dir, fmt, dpi, top_n)
                figures["tsr_ranking"] = str(fig_path)
            except Exception as e:
                logger.warning("Failed to plot TSR ranking: %s", e)

        # --- Figure 9: Topic × country heatmap ---
        topic_outputs = context.previous_outputs.get("topic_modeler", {})
        topic_country_path = topic_outputs.get("topic_country_matrix_path")
        if topic_country_path and Path(topic_country_path).exists():
            try:
                fig_path = self._plot_topic_country_heatmap(
                    topic_country_path, output_dir, fmt, dpi
                )
                figures["topic_country_heatmap"] = str(fig_path)
            except Exception as e:
                logger.warning("Failed to plot topic × country heatmap: %s", e)

        # --- Figure 10: Topic × year heatmap ---
        topic_year_path = topic_outputs.get("topic_year_matrix_path")
        if topic_year_path and Path(topic_year_path).exists():
            try:
                fig_path = self._plot_topic_year_heatmap(
                    topic_year_path, output_dir, fmt, dpi
                )
                figures["topic_year_heatmap"] = str(fig_path)
            except Exception as e:
                logger.warning("Failed to plot topic × year heatmap: %s", e)

        # --- Figure 11: H-index for top authors ---
        h_index_path = bib_outputs.get("h_index_path")
        if h_index_path and Path(h_index_path).exists():
            try:
                fig_path = self._plot_top_n_bar(
                    h_index_path, "author", "h_index",
                    f"Top {top_n} Authors (H-index)", output_dir, "h_index", fmt, dpi, top_n
                )
                figures["h_index"] = str(fig_path)
            except Exception as e:
                logger.warning("Failed to plot H-index: %s", e)

        logger.info("Generated %d figures", len(figures))

        return {
            "figures_dir": str(output_dir),
            "figures": figures,
        }

    # ------------------------------------------------------------------
    # Plotting methods
    # ------------------------------------------------------------------

    def _plot_annual_trend(
        self, csv_path: str, output_dir: Path, fmt: str, dpi: int
    ) -> Path:
        """Plot annual publication count (bars) + growth rate (line)."""
        import matplotlib.pyplot as plt

        df = pd.read_csv(csv_path)
        fig, ax1 = plt.subplots(figsize=(10, 6))

        years = df["year"].astype(int)
        counts = df["paper_count"]

        ax1.bar(years, counts, color="#4C72B0", alpha=0.7, label="Paper Count")
        ax1.set_xlabel("Year")
        ax1.set_ylabel("Number of Papers", color="#4C72B0")
        ax1.tick_params(axis="y", labelcolor="#4C72B0")

        ax2 = ax1.twinx()
        growth = df["growth_rate_pct"]
        ax2.plot(years, growth, color="#DD8452", marker="o", linewidth=2, label="Growth Rate (%)")
        ax2.set_ylabel("Growth Rate (%)", color="#DD8452")
        ax2.tick_params(axis="y", labelcolor="#DD8452")
        ax2.axhline(y=0, color="gray", linestyle="--", alpha=0.3)

        fig.suptitle("Annual Publication Trend", fontsize=14, fontweight="bold")
        fig.tight_layout()

        path = output_dir / f"annual_trend.{fmt}"
        fig.savefig(path, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        return path

    def _plot_top_n_bar(
        self, csv_path: str, name_col: str, value_col: str,
        title: str, output_dir: Path, fig_name: str,
        fmt: str, dpi: int, top_n: int = 15,
    ) -> Path:
        """Plot horizontal bar chart for top N items."""
        import matplotlib.pyplot as plt

        df = pd.read_csv(csv_path).head(top_n)
        fig, ax = plt.subplots(figsize=(10, max(4, len(df) * 0.35)))

        names = df[name_col].astype(str)
        # Truncate long names
        names = names.apply(lambda x: x[:40] + "..." if len(x) > 40 else x)
        values = df[value_col]

        colors = plt.cm.viridis(np.linspace(0.2, 0.8, len(df)))
        bars = ax.barh(range(len(df)), values, color=colors)
        ax.set_yticks(range(len(df)))
        ax.set_yticklabels(names, fontsize=9)
        ax.invert_yaxis()
        ax.set_xlabel(value_col.replace("_", " ").title())
        ax.set_title(title, fontsize=13, fontweight="bold")

        # Add value labels
        for bar, val in zip(bars, values):
            ax.text(bar.get_width() + max(values) * 0.01, bar.get_y() + bar.get_height() / 2,
                    f"{int(val)}", va="center", fontsize=8)

        fig.tight_layout()
        path = output_dir / f"{fig_name}.{fmt}"
        fig.savefig(path, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        return path

    def _plot_bradford(
        self, csv_path: str, output_dir: Path, fmt: str, dpi: int
    ) -> Path:
        """Plot Bradford's law zone distribution."""
        import matplotlib.pyplot as plt

        df = pd.read_csv(csv_path)
        if df.empty or "zone" not in df.columns:
            raise ValueError("No Bradford data")

        zone_counts = df.groupby("zone")["paper_count"].sum()
        zone_labels = {1: "Zone 1 (Core)", 2: "Zone 2", 3: "Zone 3 (Peripheral)"}

        fig, ax = plt.subplots(figsize=(8, 5))
        zones = [zone_counts.get(z, 0) for z in [1, 2, 3]]
        labels = [zone_labels.get(z, f"Zone {z}") for z in [1, 2, 3]]
        colors = ["#C44E52", "#4C72B0", "#55A868"]

        ax.bar(labels, zones, color=colors, edgecolor="white")
        ax.set_ylabel("Number of Papers")
        ax.set_title("Bradford's Law — Journal Zone Distribution", fontsize=13, fontweight="bold")

        # Add n_journals annotation
        zone_journals = df.groupby("zone").size()
        for i, z in enumerate([1, 2, 3]):
            n_j = zone_journals.get(z, 0)
            ax.text(i, zones[i] + max(zones) * 0.02, f"{n_j} journals",
                    ha="center", fontsize=9, color="gray")

        fig.tight_layout()
        path = output_dir / f"bradford_zones.{fmt}"
        fig.savefig(path, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        return path

    def _plot_keyword_heatmap(
        self, csv_path: str, output_dir: Path, fmt: str, dpi: int, top_n: int = 20
    ) -> Path:
        """Plot keyword × year frequency heatmap for top keywords."""
        import matplotlib.pyplot as plt

        df = pd.read_csv(csv_path, index_col=0)
        if df.empty:
            raise ValueError("Empty keyword-year matrix")

        # Select top N keywords by total frequency
        top_keywords = df.sum(axis=1).sort_values(ascending=False).head(top_n).index
        df_top = df.loc[top_keywords]

        fig, ax = plt.subplots(figsize=(max(10, len(df_top.columns) * 0.4), max(6, top_n * 0.4)))
        im = ax.imshow(df_top.values, aspect="auto", cmap="YlOrRd")

        ax.set_xticks(range(len(df_top.columns)))
        ax.set_xticklabels(df_top.columns, rotation=45, ha="right", fontsize=8)
        ax.set_yticks(range(len(df_top.index)))
        ax.set_yticklabels(df_top.index, fontsize=8)
        ax.set_title("Keyword Frequency Over Time (Top Keywords)", fontsize=13, fontweight="bold")

        fig.colorbar(im, ax=ax, label="Frequency", shrink=0.8)
        fig.tight_layout()

        path = output_dir / f"keyword_timeline.{fmt}"
        fig.savefig(path, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        return path

    def _plot_tsr_ranking(
        self, csv_path: str, output_dir: Path, fmt: str, dpi: int, top_n: int = 15
    ) -> Path:
        """Plot TSR ranking as horizontal bar chart."""
        import matplotlib.pyplot as plt

        df = pd.read_csv(csv_path).head(top_n)
        if df.empty:
            raise ValueError("Empty TSR ranking")

        # Use first column as topic label, look for tsr_score or similar
        topic_col = df.columns[0]
        score_col = None
        for col in ["tsr_score", "score", "ranking"]:
            if col in df.columns:
                score_col = col
                break
        if score_col is None:
            # Use last numeric column
            for col in reversed(df.columns):
                if pd.api.types.is_numeric_dtype(df[col]):
                    score_col = col
                    break
        if score_col is None:
            raise ValueError("No numeric score column found in TSR ranking")

        fig, ax = plt.subplots(figsize=(10, max(4, len(df) * 0.4)))
        topics = df[topic_col].astype(str).apply(lambda x: x[:35] + "..." if len(x) > 35 else x)
        scores = df[score_col]

        colors = plt.cm.RdYlGn(np.linspace(0.3, 0.9, len(df)))
        bars = ax.barh(range(len(df)), scores, color=colors)
        ax.set_yticks(range(len(df)))
        ax.set_yticklabels(topics, fontsize=9)
        ax.invert_yaxis()
        ax.set_xlabel("TSR Score")
        ax.set_title("Topic Significance Ranking", fontsize=13, fontweight="bold")

        for bar, val in zip(bars, scores):
            ax.text(bar.get_width() + max(scores) * 0.01, bar.get_y() + bar.get_height() / 2,
                    f"{val:.3f}", va="center", fontsize=8)

        fig.tight_layout()
        path = output_dir / f"tsr_ranking.{fmt}"
        fig.savefig(path, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        return path

    def _plot_topic_country_heatmap(
        self, csv_path: str, output_dir: Path, fmt: str, dpi: int
    ) -> Path:
        """Plot topic × country distribution heatmap."""
        import matplotlib.pyplot as plt

        df = pd.read_csv(csv_path, index_col=0) if "topic" not in pd.read_csv(csv_path, nrows=1).columns else pd.read_csv(csv_path).set_index("topic")
        # If "topic" is a column, set it as index
        df_check = pd.read_csv(csv_path, nrows=2)
        if "topic" in df_check.columns:
            df = pd.read_csv(csv_path).set_index("topic")
        else:
            df = pd.read_csv(csv_path, index_col=0)

        if df.empty:
            raise ValueError("Empty topic × country matrix")

        # Select top 15 countries by total weight
        top_countries = df.sum(axis=0).sort_values(ascending=False).head(15).index
        df_top = df[top_countries]

        fig, ax = plt.subplots(figsize=(max(8, len(top_countries) * 0.6), max(5, len(df_top) * 0.5)))
        im = ax.imshow(df_top.values, aspect="auto", cmap="Blues")

        ax.set_xticks(range(len(df_top.columns)))
        ax.set_xticklabels(df_top.columns, rotation=45, ha="right", fontsize=8)
        ax.set_yticks(range(len(df_top.index)))
        ax.set_yticklabels(df_top.index, fontsize=9)
        ax.set_title("Topic × Country Distribution", fontsize=13, fontweight="bold")

        fig.colorbar(im, ax=ax, label="Weighted Count", shrink=0.8)
        fig.tight_layout()

        path = output_dir / f"topic_country_heatmap.{fmt}"
        fig.savefig(path, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        return path

    def _plot_topic_year_heatmap(
        self, csv_path: str, output_dir: Path, fmt: str, dpi: int
    ) -> Path:
        """Plot topic × year distribution heatmap."""
        import matplotlib.pyplot as plt

        df_check = pd.read_csv(csv_path, nrows=2)
        if "topic" in df_check.columns:
            df = pd.read_csv(csv_path).set_index("topic")
        else:
            df = pd.read_csv(csv_path, index_col=0)

        if df.empty:
            raise ValueError("Empty topic × year matrix")

        fig, ax = plt.subplots(figsize=(max(8, len(df.columns) * 0.4), max(5, len(df) * 0.5)))
        im = ax.imshow(df.values, aspect="auto", cmap="Oranges")

        ax.set_xticks(range(len(df.columns)))
        ax.set_xticklabels(df.columns, rotation=45, ha="right", fontsize=8)
        ax.set_yticks(range(len(df.index)))
        ax.set_yticklabels(df.index, fontsize=9)
        ax.set_title("Topic × Year Distribution", fontsize=13, fontweight="bold")

        fig.colorbar(im, ax=ax, label="Weighted Count", shrink=0.8)
        fig.tight_layout()

        path = output_dir / f"topic_year_heatmap.{fmt}"
        fig.savefig(path, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        return path
