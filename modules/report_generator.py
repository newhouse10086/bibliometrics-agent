"""Report Generator module — self-contained HTML report with base64-embedded figures.

Generates a Jinja2-powered HTML report from pipeline outputs:
1. Executive Summary
2. Publication Trends
3. Geographic Distribution
4. Author Analysis
5. Journal Analysis
6. Keyword Analysis
7. Topic Analysis
8. Network Analysis
9. Methodology
"""

from __future__ import annotations

import base64
import json
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd

from modules.base import BaseModule, HardwareSpec, RunContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Inline Jinja2-like template (avoids adding jinja2 as a hard dependency,
# but if available we use it; otherwise fall back to str.format_map)
# ---------------------------------------------------------------------------
REPORT_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bibliometric Analysis Report</title>
<style>
  :root {{
--primary: #1a365d; --accent: #2b6cb0; --bg: #f7fafc;
--card-bg: #ffffff; --text: #2d3748; --muted: #718096;
--border: #e2e8f0; --success: #38a169; --warn: #d69e2e;
}}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
         background: var(--bg); color: var(--text); line-height: 1.6; }}
  .container {{ max-width: 1100px; margin: 0 auto; padding: 2rem 1.5rem; }}
  h1 {{ font-size: 2rem; color: var(--primary); margin-bottom: 0.25rem; }}
  h2 {{ font-size: 1.5rem; color: var(--primary); margin: 2.5rem 0 1rem;
       border-bottom: 2px solid var(--accent); padding-bottom: 0.3rem; }}
  h3 {{ font-size: 1.15rem; color: var(--accent); margin: 1.2rem 0 0.5rem; }}
  .subtitle {{ color: var(--muted); margin-bottom: 2rem; }}
  .card {{ background: var(--card-bg); border-radius: 8px; padding: 1.5rem;
           margin-bottom: 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 1rem; margin-bottom: 1.5rem; }}
  .stat-item {{ background: var(--card-bg); border-radius: 8px; padding: 1rem;
                text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  .stat-value {{ font-size: 1.8rem; font-weight: 700; color: var(--accent); }}
  .stat-label {{ font-size: 0.85rem; color: var(--muted); }}
  table {{ width: 100%; border-collapse: collapse; margin: 0.5rem 0 1rem; font-size: 0.9rem; }}
  th {{ background: var(--primary); color: #fff; padding: 0.6rem 0.8rem;
       text-align: left; font-weight: 600; }}
  td {{ padding: 0.5rem 0.8rem; border-bottom: 1px solid var(--border); }}
  tr:nth-child(even) {{ background: #f7fafc; }}
  tr:hover {{ background: #edf2f7; }}
  .figure {{ text-align: center; margin: 1.5rem 0; }}
  .figure img {{ max-width: 100%; border-radius: 6px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
  .figure-caption {{ font-size: 0.85rem; color: var(--muted); margin-top: 0.4rem; }}
  .badge {{ display: inline-block; padding: 0.15rem 0.5rem; border-radius: 999px;
            font-size: 0.75rem; font-weight: 600; }}
  .badge-blue {{ background: #ebf8ff; color: #2b6cb0; }}
  .badge-green {{ background: #f0fff4; color: #38a169; }}
  .toc {{ background: var(--card-bg); border-radius: 8px; padding: 1.2rem 1.5rem;
          margin-bottom: 2rem; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  .toc ul {{ list-style: none; padding: 0; }}
  .toc li {{ padding: 0.25rem 0; }}
  .toc a {{ color: var(--accent); text-decoration: none; }}
  .toc a:hover {{ text-decoration: underline; }}
  .footer {{ text-align: center; color: var(--muted); font-size: 0.8rem;
             margin-top: 3rem; padding-top: 1.5rem; border-top: 1px solid var(--border); }}
  @media print {{ body {{ background: #fff; }} .card {{ box-shadow: none; border: 1px solid var(--border); }} }}
</style>
</head>
<body>
<div class="container">

<h1>Bibliometric Analysis Report</h1>
<p class="subtitle">{subtitle}</p>

<div class="toc">
  <h3>Table of Contents</h3>
  <ul>
    <li><a href="#executive-summary">1. Executive Summary</a></li>
    <li><a href="#publication-trends">2. Publication Trends</a></li>
    <li><a href="#geographic-distribution">3. Geographic Distribution</a></li>
    <li><a href="#author-analysis">4. Author Analysis</a></li>
    <li><a href="#journal-analysis">5. Journal Analysis</a></li>
    <li><a href="#keyword-analysis">6. Keyword Analysis</a></li>
    <li><a href="#topic-analysis">7. Topic Analysis</a></li>
    <li><a href="#network-analysis">8. Network Analysis</a></li>
    <li><a href="#methodology">9. Methodology</a></li>
  </ul>
</div>

{executive_summary}

{publication_trends}

{geographic_distribution}

{author_analysis}

{journal_analysis}

{keyword_analysis}

{topic_analysis}

{network_analysis}

{methodology}

<div class="footer">
  Generated by Bibliometrics Agent on {generated_date}
</div>

</div>
</body>
</html>
"""


class ReportGenerator(BaseModule):
    """Generate a self-contained HTML bibliometric report from pipeline outputs."""

    @property
    def name(self) -> str:
        return "report_generator"

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
                "report_path": {"type": "string", "description": "Path to HTML report"},
                "stats": {
                    "type": "object",
                    "properties": {
                        "n_sections": {"type": "integer"},
                        "n_figures": {"type": "integer"},
                        "n_tables": {"type": "integer"},
                    },
                },
            },
        }

    def config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "default": "Bibliometric Analysis Report",
                    "description": "Report title",
                },
                "include_sections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [
                        "executive_summary", "publication_trends",
                        "geographic_distribution", "author_analysis",
                        "journal_analysis", "keyword_analysis",
                        "topic_analysis", "network_analysis", "methodology",
                    ],
                    "description": "Sections to include in the report",
                },
                "top_n": {
                    "type": "integer",
                    "default": 20,
                    "description": "Number of top items to show in tables",
                },
                "embed_figures": {
                    "type": "boolean",
                    "default": True,
                    "description": "Embed figures as base64 (True) or link to files (False)",
                },
            },
        }

    def get_hardware_requirements(self, config: dict) -> HardwareSpec:
        return HardwareSpec(
            min_memory_gb=1.0,
            recommended_memory_gb=2.0,
            cpu_cores=1,
            estimated_runtime_seconds=30,
        )

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------
    def process(self, input_data: dict, config: dict, context: RunContext) -> dict:
        output_dir = context.get_output_path(self.name, "")
        output_dir.mkdir(parents=True, exist_ok=True)
        figures_dir = output_dir / "figures"
        figures_dir.mkdir(parents=True, exist_ok=True)

        top_n = config.get("top_n", 20)
        embed = config.get("embed_figures", True)
        include = config.get("include_sections", [])

        # Gather data from upstream modules
        prev = context.previous_outputs
        papers_data = self._load_papers(prev)
        fig_map = self._collect_figures(prev, figures_dir)

        n_sections = 0
        n_figures = 0
        n_tables = 0

        # --- 1. Executive Summary ---
        exec_html = ""
        if "executive_summary" in include:
            exec_html, _f, _t = self._section_executive_summary(papers_data, prev, top_n)
            n_sections += 1; n_figures += _f; n_tables += _t

        # --- 2. Publication Trends ---
        trends_html = ""
        if "publication_trends" in include:
            trends_html, _f, _t = self._section_publication_trends(papers_data, prev, fig_map, embed)
            n_sections += 1; n_figures += _f; n_tables += _t

        # --- 3. Geographic Distribution ---
        geo_html = ""
        if "geographic_distribution" in include:
            geo_html, _f, _t = self._section_geographic(papers_data, prev, fig_map, embed, top_n)
            n_sections += 1; n_figures += _f; n_tables += _t

        # --- 4. Author Analysis ---
        author_html = ""
        if "author_analysis" in include:
            author_html, _f, _t = self._section_author(papers_data, prev, fig_map, embed, top_n)
            n_sections += 1; n_figures += _f; n_tables += _t

        # --- 5. Journal Analysis ---
        journal_html = ""
        if "journal_analysis" in include:
            journal_html, _f, _t = self._section_journal(papers_data, prev, fig_map, embed, top_n)
            n_sections += 1; n_figures += _f; n_tables += _t

        # --- 6. Keyword Analysis ---
        kw_html = ""
        if "keyword_analysis" in include:
            kw_html, _f, _t = self._section_keyword(papers_data, prev, fig_map, embed)
            n_sections += 1; n_figures += _f; n_tables += _t

        # --- 7. Topic Analysis ---
        topic_html = ""
        if "topic_analysis" in include:
            topic_html, _f, _t = self._section_topic(papers_data, prev, fig_map, embed)
            n_sections += 1; n_figures += _f; n_tables += _t

        # --- 8. Network Analysis ---
        net_html = ""
        if "network_analysis" in include:
            net_html, _f, _t = self._section_network(papers_data, prev)
            n_sections += 1; n_figures += _f; n_tables += _t

        # --- 9. Methodology ---
        meth_html = ""
        if "methodology" in include:
            meth_html = self._section_methodology(papers_data, prev)
            n_sections += 1

        # Assemble
        report_html = REPORT_TEMPLATE.format(
            subtitle=config.get("title", "Bibliometric Analysis Report"),
            generated_date=datetime.now().strftime("%Y-%m-%d %H:%M"),
            executive_summary=exec_html,
            publication_trends=trends_html,
            geographic_distribution=geo_html,
            author_analysis=author_html,
            journal_analysis=journal_html,
            keyword_analysis=kw_html,
            topic_analysis=topic_html,
            network_analysis=net_html,
            methodology=meth_html,
        )

        report_path = output_dir / "report.html"
        report_path.write_text(report_html, encoding="utf-8")
        logger.info("Report written to %s (%d sections, %d figures, %d tables)",
                     report_path, n_sections, n_figures, n_tables)

        return {
            "report_path": str(report_path),
            "stats": {
                "n_sections": n_sections,
                "n_figures": n_figures,
                "n_tables": n_tables,
            },
        }

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------
    def _load_papers(self, prev: dict) -> list[dict]:
        """Load papers.json from paper_fetcher output."""
        pf = prev.get("paper_fetcher", {})
        path = pf.get("papers_json_path")
        if path and Path(path).exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def _collect_figures(self, prev: dict, figures_dir: Path) -> dict[str, Path]:
        """Gather figure paths from visualizer output."""
        fig_map: dict[str, Path] = {}
        viz = prev.get("visualizer", {})
        figs = viz.get("figures", {})
        for name, path_str in figs.items():
            p = Path(path_str)
            if p.exists():
                fig_map[name] = p
        return fig_map

    def _embed_img(self, path: Path, embed: bool) -> str:
        """Return an <img> tag — embedded base64 or relative link."""
        if not path or not path.exists():
            return ""
        if embed:
            ext = path.suffix.lstrip(".")
            mime = {"png": "image/png", "svg": "image/svg+xml",
                    "pdf": "application/pdf", "jpg": "image/jpeg",
                    "jpeg": "image/jpeg"}.get(ext, "image/png")
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            return f'<img src="data:{mime};base64,{data}" alt="{path.stem}">'
        else:
            return f'<img src="{path}" alt="{path.stem}">'

    def _fig(self, fig_map: dict, key: str, caption: str, embed: bool) -> tuple[str, int]:
        """Return figure HTML and figure count."""
        path = fig_map.get(key)
        if not path:
            return "", 0
        img = self._embed_img(path, embed)
        if not img:
            return "", 0
        html = f'<div class="figure" id="fig-{key}">{img}<p class="figure-caption">{caption}</p></div>'
        return html, 1

    def _table(self, headers: list[str], rows: list[list], caption: str = "") -> tuple[str, int]:
        """Return an HTML table and table count."""
        th = "".join(f"<th>{h}</th>" for h in headers)
        trs = []
        for row in rows:
            tds = "".join(f"<td>{c}</td>" for c in row)
            trs.append(f"<tr>{tds}</tr>")
        cap_html = f"<p class=\"figure-caption\">{caption}</p>" if caption else ""
        html = f'{cap_html}<table><thead><tr>{th}</tr></thead><tbody>{"".join(trs)}</tbody></table>'
        return html, 1

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------
    def _section_executive_summary(self, papers: list, prev: dict, top_n: int) -> tuple[str, int, int]:
        n_papers = len(papers)
        years = [p.get("year") for p in papers if p.get("year")]
        year_min = min(years) if years else "N/A"
        year_max = max(years) if years else "N/A"

        # Count unique authors
        authors_seen = set()
        for p in papers:
            for a in (p.get("authors") or []):
                name = a.get("name", "") if isinstance(a, dict) else str(a)
                if name:
                    authors_seen.add(name)

        # Count journals
        journals = [p.get("journal_name") for p in papers if p.get("journal_name")]
        n_journals = len(set(journals))

        # Count countries
        countries = set()
        for p in papers:
            for a in (p.get("authors") or []):
                if isinstance(a, dict) and a.get("country"):
                    countries.add(a["country"])

        # Keywords
        mesh_count = sum(len(p.get("mesh_terms") or []) for p in papers)

        stats_html = f"""
        <h2 id="executive-summary">1. Executive Summary</h2>
        <div class="stat-grid">
          <div class="stat-item"><div class="stat-value">{n_papers}</div><div class="stat-label">Papers</div></div>
          <div class="stat-item"><div class="stat-value">{year_min}–{year_max}</div><div class="stat-label">Year Range</div></div>
          <div class="stat-item"><div class="stat-value">{len(authors_seen)}</div><div class="stat-label">Unique Authors</div></div>
          <div class="stat-item"><div class="stat-value">{n_journals}</div><div class="stat-label">Journals</div></div>
          <div class="stat-item"><div class="stat-value">{len(countries)}</div><div class="stat-label">Countries</div></div>
          <div class="stat-item"><div class="stat-value">{mesh_count}</div><div class="stat-label">MeSH Terms</div></div>
        </div>
        """
        return stats_html, 0, 0

    def _section_publication_trends(self, papers: list, prev: dict,
                                     fig_map: dict, embed: bool) -> tuple[str, int, int]:
        n_figs = 0
        n_tables = 0
        parts = ['<h2 id="publication-trends">2. Publication Trends</h2>']

        # Annual counts
        year_counts = Counter(p.get("year") for p in papers if p.get("year"))
        if year_counts:
            sorted_years = sorted(year_counts.items())
            rows = []
            for i, (yr, cnt) in enumerate(sorted_years):
                growth = ""
                if i > 0:
                    prev_cnt = sorted_years[i - 1][1]
                    if prev_cnt > 0:
                        g = (cnt - prev_cnt) / prev_cnt * 100
                        growth = f"{g:+.1f}%"
                rows.append([str(yr), str(cnt), growth])
            tbl, _t = self._table(["Year", "Count", "Growth Rate"], rows, "Annual publication counts")
            parts.append(tbl)
            n_tables += _t

        # Figure
        fig_html, _f = self._fig(fig_map, "annual_trend", "Figure 1: Annual publication trend", embed)
        if fig_html:
            parts.append(fig_html)
            n_figs += _f

        return "\n".join(parts), n_figs, n_tables

    def _section_geographic(self, papers: list, prev: dict,
                             fig_map: dict, embed: bool, top_n: int) -> tuple[str, int, int]:
        n_figs = 0
        n_tables = 0
        parts = ['<h2 id="geographic-distribution">3. Geographic Distribution</h2>']

        # Country counts from papers
        country_counts: Counter = Counter()
        for p in papers:
            paper_countries = set()
            for a in (p.get("authors") or []):
                if isinstance(a, dict) and a.get("country"):
                    paper_countries.add(a["country"])
            for c in paper_countries:
                country_counts[c] += 1

        if country_counts:
            rows = []
            for country, cnt in country_counts.most_common(top_n):
                pct = cnt / len(papers) * 100 if papers else 0
                rows.append([country, str(cnt), f"{pct:.1f}%"])
            tbl, _t = self._table(["Country", "Papers", "% of Total"], rows, "Top countries by publication count")
            parts.append(tbl)
            n_tables += _t

        # Figure
        fig_html, _f = self._fig(fig_map, "top_countries", "Figure 2: Top countries by publication count", embed)
        if fig_html:
            parts.append(fig_html)
            n_figs += _f

        # Collaboration table from country_analyzer
        ca = prev.get("country_analyzer", {})
        collab_path = ca.get("country_collaboration_path")
        if collab_path and Path(collab_path).exists():
            try:
                collab_df = pd.read_csv(collab_path)
                rows = []
                for _, r in collab_df.head(top_n).iterrows():
                    rows.append([str(r.get("country_1", "")), str(r.get("country_2", "")),
                                 str(r.get("weight", r.get("collaboration_count", "")))])
                tbl, _t = self._table(["Country A", "Country B", "Collaborations"],
                                       rows, "International collaboration pairs")
                parts.append(tbl)
                n_tables += _t
            except Exception:
                logger.warning("Failed to read country collaboration data")

        return "\n".join(parts), n_figs, n_tables

    def _section_author(self, papers: list, prev: dict,
                         fig_map: dict, embed: bool, top_n: int) -> tuple[str, int, int]:
        n_figs = 0
        n_tables = 0
        parts = ['<h2 id="author-analysis">4. Author Analysis</h2>']

        # Author publication counts
        author_counts: Counter = Counter()
        author_citations: Counter = Counter()
        for p in papers:
            cited = p.get("cited_by_count", 0) or 0
            for a in (p.get("authors") or []):
                name = a.get("name", "") if isinstance(a, dict) else str(a)
                if name:
                    author_counts[name] += 1
                    author_citations[name] += cited

        if author_counts:
            rows = []
            for name, cnt in author_counts.most_common(top_n):
                tc = author_citations.get(name, 0)
                rows.append([name, str(cnt), str(tc)])
            tbl, _t = self._table(["Author", "Papers", "Total Citations"],
                                   rows, "Top authors by publication count")
            parts.append(tbl)
            n_tables += _t

        # H-index from bibliometrics_analyzer
        ba = prev.get("bibliometrics_analyzer", {})
        h_path = ba.get("h_index_path")
        if h_path and Path(h_path).exists():
            try:
                h_df = pd.read_csv(h_path)
                rows = []
                for _, r in h_df.head(top_n).iterrows():
                    rows.append([str(r.get("author", "")), str(r.get("h_index", ""))])
                tbl, _t = self._table(["Author", "H-Index"], rows, "Author H-index")
                parts.append(tbl)
                n_tables += _t
            except Exception:
                logger.warning("Failed to read H-index data")

        # Figures
        for key, cap in [("top_authors", "Figure 3: Top authors by publication count"),
                          ("h_index", "Figure 4: Author H-index distribution")]:
            fig_html, _f = self._fig(fig_map, key, cap, embed)
            if fig_html:
                parts.append(fig_html)
                n_figs += _f

        return "\n".join(parts), n_figs, n_tables

    def _section_journal(self, papers: list, prev: dict,
                          fig_map: dict, embed: bool, top_n: int) -> tuple[str, int, int]:
        n_figs = 0
        n_tables = 0
        parts = ['<h2 id="journal-analysis">5. Journal Analysis</h2>']

        journal_counts: Counter = Counter()
        for p in papers:
            j = p.get("journal_name")
            if j:
                journal_counts[j] += 1

        if journal_counts:
            rows = []
            for j, cnt in journal_counts.most_common(top_n):
                pct = cnt / len(papers) * 100 if papers else 0
                rows.append([j, str(cnt), f"{pct:.1f}%"])
            tbl, _t = self._table(["Journal", "Papers", "% of Total"],
                                   rows, "Top journals by publication count")
            parts.append(tbl)
            n_tables += _t

        # Bradford zones from bibliometrics_analyzer
        ba = prev.get("bibliometrics_analyzer", {})
        bradford_path = ba.get("bradford_path")
        if bradford_path and Path(bradford_path).exists():
            try:
                brad_df = pd.read_csv(bradford_path)
                rows = []
                for _, r in brad_df.iterrows():
                    rows.append([str(r.get("zone", "")),
                                 str(r.get("n_journals", "")),
                                 str(r.get("n_papers", ""))])
                tbl, _t = self._table(["Zone", "Journals", "Papers"],
                                       rows, "Bradford's law zones")
                parts.append(tbl)
                n_tables += _t
            except Exception:
                logger.warning("Failed to read Bradford zones data")

        # Figure
        for key, cap in [("top_journals", "Figure 5: Top journals by publication count"),
                          ("bradford_zones", "Figure 6: Bradford's law zone distribution")]:
            fig_html, _f = self._fig(fig_map, key, cap, embed)
            if fig_html:
                parts.append(fig_html)
                n_figs += _f

        return "\n".join(parts), n_figs, n_tables

    def _section_keyword(self, papers: list, prev: dict,
                          fig_map: dict, embed: bool) -> tuple[str, int, int]:
        n_figs = 0
        n_tables = 0
        parts = ['<h2 id="keyword-analysis">6. Keyword Analysis</h2>']

        # MeSH terms
        mesh_counts: Counter = Counter()
        for p in papers:
            for t in (p.get("mesh_terms") or []):
                mesh_counts[t] += 1

        if mesh_counts:
            rows = [[t, str(c)] for t, c in mesh_counts.most_common(20)]
            tbl, _t = self._table(["MeSH Term", "Frequency"], rows, "Top MeSH terms")
            parts.append(tbl)
            n_tables += _t

        # Author keywords
        ak_counts: Counter = Counter()
        for p in papers:
            for kw in (p.get("author_keywords") or []):
                ak_counts[kw] += 1

        if ak_counts:
            rows = [[kw, str(c)] for kw, c in ak_counts.most_common(20)]
            tbl, _t = self._table(["Author Keyword", "Frequency"], rows, "Top author keywords")
            parts.append(tbl)
            n_tables += _t

        # Frequency analyzer data
        fa = prev.get("frequency_analyzer", {})
        freq_path = fa.get("keyword_year_matrix_path")
        if freq_path and Path(freq_path).exists():
            try:
                freq_df = pd.read_csv(freq_path, index_col=0)
                top_kw = freq_df.sum(axis=1).sort_values(ascending=False).head(20)
                rows = [[kw, f"{cnt:.0f}"] for kw, cnt in top_kw.items()]
                tbl, _t = self._table(["Keyword", "Total Frequency"], rows, "Top keywords by frequency")
                parts.append(tbl)
                n_tables += _t
            except Exception:
                logger.warning("Failed to read frequency data")

        # Figures
        for key, cap in [("keyword_timeline", "Figure 7: Keyword timeline heatmap"),
                          ("tsr_ranking", "Figure 8: Topic significance ranking")]:
            fig_html, _f = self._fig(fig_map, key, cap, embed)
            if fig_html:
                parts.append(fig_html)
                n_figs += _f

        return "\n".join(parts), n_figs, n_tables

    def _section_topic(self, papers: list, prev: dict,
                        fig_map: dict, embed: bool) -> tuple[str, int, int]:
        n_figs = 0
        n_tables = 0
        parts = ['<h2 id="topic-analysis">7. Topic Analysis</h2>']

        tm = prev.get("topic_modeler", {})

        # Topic-word summary
        tw_path = tm.get("topic_word_summary_path")
        if tw_path and Path(tw_path).exists():
            try:
                tw_df = pd.read_csv(tw_path)
                rows = []
                for _, r in tw_df.iterrows():
                    topic = str(r.get("topic", r.get("Topic", "")))
                    words = str(r.get("top_words", r.get("Top Words", "")))
                    rows.append([topic, words])
                tbl, _t = self._table(["Topic", "Top Words"], rows, "Topic-word distribution")
                parts.append(tbl)
                n_tables += _t
            except Exception:
                logger.warning("Failed to read topic-word summary")

        # Topic top papers
        tp_path = tm.get("topic_top_papers_path")
        if tp_path and Path(tp_path).exists():
            try:
                tp_df = pd.read_csv(tp_path)
                rows = []
                for _, r in tp_df.iterrows():
                    rows.append([
                        str(r.get("topic", "")),
                        str(r.get("title", ""))[:80],
                        str(r.get("year", "")),
                        str(r.get("journal", r.get("journal_name", ""))),
                    ])
                tbl, _t = self._table(["Topic", "Title", "Year", "Journal"],
                                       rows, "Top papers per topic")
                parts.append(tbl)
                n_tables += _t
            except Exception:
                logger.warning("Failed to read topic top papers")

        # Topic-country heatmap
        tc_path = tm.get("topic_country_matrix_path")
        if tc_path and Path(tc_path).exists():
            try:
                tc_df = pd.read_csv(tc_path, index_col=0)
                top_countries = tc_df.sum(axis=0).sort_values(ascending=False).head(10).index
                rows = []
                for topic in tc_df.index:
                    vals = [f"{tc_df.loc[topic, c]:.2f}" for c in top_countries]
                    rows.append([str(topic)] + vals)
                tbl, _t = self._table(
                    ["Topic"] + list(top_countries),
                    rows, "Topic × Country weighted distribution"
                )
                parts.append(tbl)
                n_tables += _t
            except Exception:
                logger.warning("Failed to read topic-country matrix")

        # Figures
        for key, cap in [("topic_country_heatmap", "Figure 9: Topic × Country heatmap"),
                          ("topic_year_heatmap", "Figure 10: Topic × Year heatmap")]:
            fig_html, _f = self._fig(fig_map, key, cap, embed)
            if fig_html:
                parts.append(fig_html)
                n_figs += _f

        return "\n".join(parts), n_figs, n_tables

    def _section_network(self, papers: list, prev: dict) -> tuple[str, int, int]:
        n_figs = 0
        n_tables = 0
        parts = ['<h2 id="network-analysis">8. Network Analysis</h2>']

        na = prev.get("network_analyzer", {})
        networks = na.get("networks", {})
        stats = na.get("stats", {})

        if networks:
            rows = []
            for net_type in networks:
                net_data = networks[net_type]
                net_stats = stats.get(net_type, {})
                graphml = net_data.get("graphml_path", "")
                n_nodes = net_stats.get("n_nodes", "N/A")
                n_edges = net_stats.get("n_edges", "N/A")
                density = net_stats.get("density", "N/A")
                rows.append([
                    net_type.replace("_", " ").title(),
                    str(n_nodes), str(n_edges),
                    f"{density:.4f}" if isinstance(density, float) else str(density),
                ])
            tbl, _t = self._table(
                ["Network Type", "Nodes", "Edges", "Density"],
                rows, "Network analysis summary"
            )
            parts.append(tbl)
            n_tables += _t

            # Centrality tables for each network
            for net_type, net_data in networks.items():
                cent_path = net_data.get("centrality_path")
                if cent_path and Path(cent_path).exists():
                    try:
                        cent_df = pd.read_csv(cent_path)
                        label = net_type.replace("_", " ").title()
                        top = cent_df.head(10)
                        cols = [c for c in top.columns if c != "Unnamed: 0"]
                        rows = []
                        for _, r in top.iterrows():
                            rows.append([str(r.get(c, "")) for c in cols])
                        tbl, _t = self._table(
                            cols, rows,
                            f"Top nodes by centrality — {label}"
                        )
                        parts.append(tbl)
                        n_tables += _t
                    except Exception:
                        logger.warning("Failed to read centrality data for %s", net_type)
        else:
            parts.append('<p>No network analysis data available.</p>')

        return "\n".join(parts), n_figs, n_tables

    def _section_methodology(self, papers: list, prev: dict) -> str:
        parts = ['<h2 id="methodology">9. Methodology</h2>']
        parts.append('<div class="card">')

        # Data sources
        pf = prev.get("paper_fetcher", {})
        sources = []
        if pf.get("pubmed_count"):
            sources.append(f"PubMed ({pf['pubmed_count']} papers)")
        if pf.get("openalex_count"):
            sources.append(f"OpenAlex ({pf['openalex_count']} papers)")
        if pf.get("crossref_count"):
            sources.append(f"Crossref ({pf['crossref_count']} papers)")
        if pf.get("semantic_scholar_count"):
            sources.append(f"Semantic Scholar ({pf['semantic_scholar_count']} papers)")

        if sources:
            parts.append("<h3>Data Sources</h3>")
            parts.append("<p>" + ", ".join(sources) + "</p>")
        else:
            parts.append("<h3>Data Sources</h3>")
            parts.append(f"<p>{len(papers)} papers from bibliographic databases.</p>")

        # Pipeline modules
        parts.append("<h3>Analysis Pipeline</h3>")
        parts.append("<p>The following analytical modules were applied sequentially:</p>")
        parts.append("<ol>")
        module_labels = {
            "query_generator": "Query Generation — automated search query construction",
            "paper_fetcher": "Paper Fetching — multi-source literature retrieval and deduplication",
            "country_analyzer": "Country Analysis — geographic distribution and collaboration mapping",
            "bibliometrics_analyzer": "Bibliometric Statistics — descriptive metrics, H-index, Bradford's law",
            "preprocessor": "Text Preprocessing — lemmatization, stopword removal, keyword boosting",
            "frequency_analyzer": "Keyword Frequency — multi-source keyword extraction and year matrix",
            "topic_modeler": "Topic Modeling — LDA with optimal K selection and post-processing",
            "burst_detector": "Burst Detection — Kleinberg algorithm for keyword trend spikes",
            "tsr_ranker": "Topic Significance Ranking — composite TSR scoring",
            "network_analyzer": "Network Analysis — collaboration, citation, and co-word networks",
            "visualizer": "Visualization — publication-style figures generation",
            "report_generator": "Report Generation — self-contained HTML report",
        }
        for mod_name in prev:
            label = module_labels.get(mod_name, mod_name.replace("_", " ").title())
            parts.append(f"<li>{label}</li>")
        parts.append("</ol>")

        parts.append("<h3>Preprocessing Details</h3>")
        pp = prev.get("preprocessor", {})
        pp_stats = pp.get("stats", {})
        if pp_stats:
            parts.append("<ul>")
            parts.append(f"<li>Documents processed: {pp_stats.get('n_docs', 'N/A')}</li>")
            parts.append(f"<li>Vocabulary size: {pp_stats.get('vocab_size', 'N/A')}</li>")
            parts.append(f"<li>Average document length: {pp_stats.get('avg_doc_length', 'N/A')} tokens</li>")
            parts.append("</ul>")

        parts.append("<h3>Topic Modeling</h3>")
        tm = prev.get("topic_modeler", {})
        if tm.get("n_topics"):
            parts.append(f"<p>Optimal number of topics: {tm['n_topics']}</p>")

        parts.append("</div>")
        return "\n".join(parts)
