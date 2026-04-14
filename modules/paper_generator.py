"""Paper Generator module — generates a bibliometric analysis paper.

Uses LLM to write each section in Markdown based on actual pipeline data,
then converts to PDF via scripts/build_pdf.py (fpdf2-based, no LaTeX needed).

Output directory structure:
    outputs/paper_generator/
    ├── main.tex          (LaTeX source — editable)
    ├── main.pdf          (PDF via fpdf2)
    ├── title.txt
    ├── sections/
    │   ├── abstract.md
    │   ├── introduction.md
    │   ├── data_methods.md
    │   ├── results.md
    │   ├── discussion.md
    │   └── conclusion.md
    ├── refs/
    │   ├── references.bib
    │   └── references.txt
    ├── figures/       (copied from visualizer)
    └── main.pdf       (if compilation succeeds)
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

from modules.base import BaseModule, HardwareSpec, RunContext

logger = logging.getLogger(__name__)


class PaperGenerator(BaseModule):
    """Generate a complete bibliometric analysis paper from pipeline outputs."""

    @property
    def name(self) -> str:
        return "paper_generator"

    @property
    def version(self) -> str:
        return "2.0.0"

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
                "tex_path": {"type": "string"},
                "sections_dir": {"type": "string"},
                "pdf_path": {"type": "string"},
                "bib_path": {"type": "string"},
                "stats": {
                    "type": "object",
                    "properties": {
                        "n_sections": {"type": "integer"},
                        "n_figures": {"type": "integer"},
                        "n_references": {"type": "integer"},
                        "compilation_success": {"type": "boolean"},
                    },
                },
            },
        }

    def config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "language": {
                    "type": "string",
                    "enum": ["zh", "en"],
                    "default": "zh",
                    "description": "Paper language (zh=Chinese, en=English)",
                },
                "title": {
                    "type": "string",
                    "default": "",
                    "description": "Paper title (empty = auto-generate)",
                },
                "compile_pdf": {
                    "type": "boolean",
                    "default": True,
                    "description": "Compile to PDF via Python script",
                },
                "max_papers_in_refs": {
                    "type": "integer",
                    "default": 50,
                    "description": "Max references in bibliography",
                },
            },
        }

    def get_hardware_requirements(self, config: dict) -> HardwareSpec:
        return HardwareSpec(
            min_memory_gb=1.0,
            recommended_memory_gb=2.0,
            cpu_cores=1,
            estimated_runtime_seconds=120,
        )

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------
    def process(self, input_data: dict, config: dict, context: RunContext) -> dict:
        output_dir = context.get_output_path(self.name, "")
        output_dir.mkdir(parents=True, exist_ok=True)

        language = config.get("language", "zh")
        compile_pdf = config.get("compile_pdf", True)
        max_refs = config.get("max_papers_in_refs", 50)
        custom_title = config.get("title", "")

        prev = context.previous_outputs
        papers = self._load_papers(prev)
        figures = self._collect_figures(prev, output_dir / "figures")

        logger.info("PaperGenerator: %d papers, %d figures, language=%s",
                     len(papers), len(figures), language)

        # 1. Generate BibTeX + plain-text references
        bib_dir = output_dir / "refs"
        bib_dir.mkdir(parents=True, exist_ok=True)
        n_refs = self._generate_references(papers, bib_dir, max_refs)

        # 2. Copy figures
        figures_dir = output_dir / "figures"
        figures_dir.mkdir(parents=True, exist_ok=True)
        fig_map = self._copy_figures(figures, figures_dir)

        # 3. Prepare data summaries for LLM
        data = self._prepare_data_summary(papers, prev, fig_map, language)

        # 4. Create LLM provider
        llm = self._create_llm(config, context.project_dir, context.run_id)

        # 5. Generate title
        title = custom_title or self._generate_title(llm, data, language)
        (output_dir / "title.txt").write_text(title, encoding="utf-8")

        # 6. Generate sections (Markdown)
        sections_dir = output_dir / "sections"
        sections_dir.mkdir(parents=True, exist_ok=True)

        section_names = [
            "abstract", "introduction", "data_methods",
            "results", "discussion", "conclusion",
        ]
        n_sections = 0
        for sec in section_names:
            logger.info("Generating section: %s", sec)
            content = self._generate_section(llm, sec, data, language, title)
            if content:
                sec_path = sections_dir / f"{sec}.md"
                sec_path.write_text(content, encoding="utf-8")
                n_sections += 1

        # 8. Assemble LaTeX document from Markdown sections
        self._assemble_latex(output_dir, sections_dir, title, language, bib_dir)

        # 9. Build PDF
        pdf_success = False
        pdf_error = ""
        if compile_pdf:
            pdf_success, pdf_error = self._build_pdf(output_dir, language)
            if not pdf_success:
                logger.warning("PDF build failed: %s", pdf_error)

        return {
            "tex_path": str(output_dir / "main.tex"),
            "sections_dir": str(sections_dir),
            "pdf_path": str(output_dir / "main.pdf") if pdf_success else "",
            "bib_path": str(bib_dir / "references.bib"),
            "stats": {
                "n_sections": n_sections,
                "n_figures": len(fig_map),
                "n_references": n_refs,
                "compilation_success": pdf_success,
                "compilation_error": pdf_error if not pdf_success else "",
            },
        }

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------
    def _load_papers(self, prev: dict) -> list[dict]:
        pf = prev.get("paper_fetcher", {})
        path = pf.get("papers_json_path")
        if path and Path(path).exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def _collect_figures(self, prev: dict, figures_dir: Path) -> dict[str, Path]:
        fig_map: dict[str, Path] = {}
        viz = prev.get("visualizer", {})
        figs = viz.get("figures", {})
        for name, path_str in figs.items():
            p = Path(path_str)
            if p.exists():
                fig_map[name] = p
        return fig_map

    def _copy_figures(self, fig_map: dict[str, Path], figures_dir: Path) -> dict[str, str]:
        """Copy figures to output dir, return {name: filename_in_figures_dir}."""
        result = {}
        for name, src in fig_map.items():
            dst = figures_dir / src.name
            shutil.copy2(src, dst)
            result[name] = src.name
        return result

    def _prepare_data_summary(self, papers: list, prev: dict,
                               fig_map: dict, language: str) -> dict:
        """Prepare concise data summaries for LLM prompts."""
        data = {"language": language}

        # Basic stats
        n_papers = len(papers)
        years = [p.get("year") for p in papers if p.get("year")]
        data["n_papers"] = n_papers
        data["year_range"] = f"{min(years)}-{max(years)}" if years else "N/A"

        # Year distribution
        year_counts = Counter(years)
        data["year_distribution"] = sorted(year_counts.items())[:30]

        # Top countries
        country_counts: Counter = Counter()
        for p in papers:
            for a in (p.get("authors") or []):
                if isinstance(a, dict) and a.get("country"):
                    country_counts[a["country"]] += 1
        data["top_countries"] = country_counts.most_common(20)

        # Top authors
        author_counts: Counter = Counter()
        for p in papers:
            for a in (p.get("authors") or []):
                name = a.get("name", "") if isinstance(a, dict) else str(a)
                if name:
                    author_counts[name] += 1
        data["top_authors"] = author_counts.most_common(20)

        # Top journals
        journal_counts = Counter(p.get("journal_name") for p in papers if p.get("journal_name"))
        data["top_journals"] = journal_counts.most_common(20)

        # Keywords
        mesh_counts: Counter = Counter()
        kw_counts: Counter = Counter()
        for p in papers:
            for t in (p.get("mesh_terms") or []):
                mesh_counts[t] += 1
            for kw in (p.get("author_keywords") or []):
                kw_counts[kw] += 1
        data["top_mesh_terms"] = mesh_counts.most_common(20)
        data["top_keywords"] = kw_counts.most_common(20)

        # Data sources
        pf = prev.get("paper_fetcher", {})
        data["sources"] = {}
        for src in ["pubmed", "openalex", "crossref", "semantic_scholar"]:
            cnt = pf.get(f"{src}_count", 0)
            if cnt:
                data["sources"][src] = cnt

        # Topic modeling
        tm = prev.get("topic_modeler", {})
        data["n_topics"] = tm.get("n_topics", 0)
        tw_path = tm.get("topic_word_summary_path")
        if tw_path and Path(tw_path).exists():
            try:
                tw_df = pd.read_csv(tw_path)
                data["topics"] = []
                for _, r in tw_df.iterrows():
                    topic = str(r.get("topic", r.get("Topic", "")))
                    words = str(r.get("top_words", r.get("Top Words", "")))
                    data["topics"].append({"topic": topic, "words": words})
            except Exception:
                pass

        # Burst detection
        bd = prev.get("burst_detector", {})
        data["burst_keywords"] = bd.get("burst_keywords", [])

        # Network stats
        na = prev.get("network_analyzer", {})
        data["network_types"] = list(na.get("networks", {}).keys())
        data["network_stats"] = na.get("stats", {})

        # Bibliometrics
        ba = prev.get("bibliometrics_analyzer", {})
        data["descriptive_stats"] = ba.get("descriptive_stats", {})

        # Figures
        data["figures"] = {name: fname for name, fname in fig_map.items()}

        # Research domain
        qg = prev.get("query_generator", {})
        data["research_domain"] = qg.get("research_domain", "")

        return data

    # ------------------------------------------------------------------
    # LLM integration
    # ------------------------------------------------------------------
    def _create_llm(self, config: dict, project_dir: Path = None, run_id: str = ""):
        """Create LLM provider from project config, config dict, or environment."""
        from core.llm import create_provider

        # Check for project-specific LLM config in state.json
        if project_dir and run_id:
            try:
                from core.state_manager import StateManager
                state_manager = StateManager(project_dir)
                llm_config = state_manager.get_llm_config(run_id)
                if llm_config:
                    return create_provider(llm_config_from_state=llm_config)
            except Exception as e:
                logger.debug(f"Could not load project-specific LLM config: {e}")

        # Fallback to module config or environment variables
        llm_config = config.get("llm", {})
        api_key = llm_config.get("api_key") or os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        base_url = (llm_config.get("base_url")
                     or os.getenv("OPENROUTER_BASE_URL")
                     or os.getenv("OPENAI_BASE_URL")
                     or "https://openrouter.ai/api/v1")
        model = llm_config.get("model") or os.getenv("LLM_MODEL", "qwen/qwen3.6-plus")

        return create_provider({
            "provider": "openai",
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
        })

    def _generate_title(self, llm, data: dict, language: str) -> str:
        """Generate paper title via LLM."""
        from core.llm import Message

        lang_name = "中文" if language == "zh" else "English"
        domain = data.get("research_domain", "bibliometric analysis")
        n_papers = data.get("n_papers", 0)
        year_range = data.get("year_range", "N/A")

        system = "You are an academic paper title generator. Output ONLY the title, nothing else."
        user = (
            f"Generate a {lang_name} title for a bibliometric analysis paper.\n"
            f"Research domain: {domain}\n"
            f"Papers analyzed: {n_papers}\n"
            f"Time range: {year_range}\n"
            f"The title should be concise, professional, and suitable for a journal publication."
        )

        try:
            resp = llm.chat(
                [Message(role="system", content=system), Message(role="user", content=user)],
                temperature=0.6, max_tokens=200,
            )
            title = (resp.content or "").strip().strip('"').strip("'")
            return title or f"Bibliometric Analysis of {domain}"
        except Exception as e:
            logger.warning("Title generation failed: %s", e)
            return f"Bibliometric Analysis of {domain}"

    def _generate_section(self, llm, section: str, data: dict,
                           language: str, title: str) -> str:
        """Generate one Markdown section via LLM."""
        from core.llm import Message

        lang_name = "中文" if language == "zh" else "English"
        prompt = self._section_prompts(section, data, language)

        system = (
            f"You are an expert academic writer specializing in bibliometric analysis papers. "
            f"Write in {lang_name}. "
            f"Output ONLY Markdown content — no preamble, no ```markdown fences, no explanation. "
            f"Use proper Markdown formatting. "
            f"For tables use Markdown pipe tables (| Header | Header |). "
            f"For figures use ![caption](figures/filename.png). "
            f"For citations use [AuthorYear] format as inline references. "
            f"Use ## for subsections. "
            f"Do NOT include a ## heading for the section name itself — that will be added automatically. "
            f"Start directly with the content."
        )

        try:
            resp = llm.chat(
                [Message(role="system", content=system), Message(role="user", content=prompt)],
                temperature=0.4, max_tokens=4096,
            )
            content = (resp.content or "").strip()
            # Clean up any markdown fences
            content = re.sub(r'^```(?:markdown|md)?\s*', '', content)
            content = re.sub(r'\s*```$', '', content)
            return content.strip()
        except Exception as e:
            logger.error("Section %s generation failed: %s", section, e)
            return f"*Section generation failed: {e}*\n\nTODO: Write {section} section manually."

    def _section_prompts(self, section: str, data: dict, language: str) -> str:
        """Build the prompt for each section with relevant data."""
        if section == "abstract":
            return self._prompt_abstract(data, language)
        elif section == "introduction":
            return self._prompt_introduction(data, language)
        elif section == "data_methods":
            return self._prompt_data_methods(data, language)
        elif section == "results":
            return self._prompt_results(data, language)
        elif section == "discussion":
            return self._prompt_discussion(data, language)
        elif section == "conclusion":
            return self._prompt_conclusion(data, language)
        return ""

    def _prompt_abstract(self, data: dict, lang: str) -> str:
        return (
            f"Write an abstract for a bibliometric analysis paper.\n"
            f"Domain: {data.get('research_domain', '')}\n"
            f"Papers: {data.get('n_papers', 0)}, Years: {data.get('year_range', '')}\n"
            f"Topics: {data.get('n_topics', 0)}\n"
            f"Top countries: {[c for c, _ in data.get('top_countries', [])[:5]]}\n"
            f"Top journals: {[j for j, _ in data.get('top_journals', [])[:5]]}\n"
            f"Networks: {data.get('network_types', [])}\n\n"
            f"150-250 words. Do NOT include any heading."
        )

    def _prompt_introduction(self, data: dict, lang: str) -> str:
        domain = data.get("research_domain", "")
        return (
            f"Write the Introduction section for a bibliometric analysis paper about '{domain}'.\n"
            f"Papers analyzed: {data.get('n_papers', 0)}, Years: {data.get('year_range', '')}\n\n"
            f"Include subsections:\n"
            f"1. Research Background — why this domain matters, current state of research\n"
            f"2. Research Objectives — what this bibliometric study aims to achieve\n"
            f"3. Significance — contribution to the field\n\n"
            f"Use ## for subsections. Write 800-1200 words."
        )

    def _prompt_data_methods(self, data: dict, lang: str) -> str:
        sources = data.get("sources", {})
        src_str = ", ".join(f"{k} ({v})" for k, v in sources.items()) if sources else "bibliographic databases"
        return (
            f"Write the Data and Methods section.\n\n"
            f"Data sources: {src_str}\n"
            f"Total papers after deduplication: {data.get('n_papers', 0)}\n"
            f"Time range: {data.get('year_range', '')}\n\n"
            f"Describe:\n"
            f"1. Search Strategy — databases used, query construction, inclusion/exclusion criteria\n"
            f"2. Data Collection — how papers were retrieved, deduplication method\n"
            f"3. Analytical Methods:\n"
            f"   - Bibliometric indicators (publication count, citation, H-index, Bradford's law)\n"
            f"   - Keyword analysis (MeSH terms, author keywords, frequency analysis)\n"
            f"   - Topic modeling (LDA with optimal K selection)\n"
            f"   - Burst detection (Kleinberg algorithm)\n"
            f"   - Topic Significance Ranking (TSR)\n"
            f"   - Network analysis (co-authorship, co-citation, bibliographic coupling)\n\n"
            f"Use ## for subsections. Write 800-1200 words."
        )

    def _prompt_results(self, data: dict, lang: str) -> str:
        figures = data.get("figures", {})
        fig_list = "\n".join(f"  - {name}: figures/{fname}" for name, fname in figures.items())

        countries = data.get("top_countries", [])[:15]
        country_table = self._make_markdown_table(
            ["Country", "Papers", "%"],
            [[c, str(n), f"{n/data['n_papers']*100:.1f}%"] for c, n in countries],
            "Top countries by publication count",
        )

        authors = data.get("top_authors", [])[:15]
        authors_table = self._make_markdown_table(
            ["Author", "Papers"],
            [[a, str(n)] for a, n in authors],
            "Top authors by publication count",
        )

        journals = data.get("top_journals", [])[:15]
        journals_table = self._make_markdown_table(
            ["Journal", "Papers", "%"],
            [[j, str(n), f"{n/data['n_papers']*100:.1f}%"] for j, n in journals],
            "Top journals by publication count",
        )

        topics = data.get("topics", [])
        topics_str = "\n".join(f"  Topic {t['topic']}: {t['words']}" for t in topics[:10])

        burst = data.get("burst_keywords", [])
        burst_str = ", ".join(str(b) for b in burst[:15]) if burst else "N/A"

        return (
            f"Write the Results section for a bibliometric analysis paper.\n\n"
            f"TOTAL: {data.get('n_papers', 0)} papers, Years: {data.get('year_range', '')}\n\n"
            f"Available figures (reference with ![caption](figures/filename.png)):\n{fig_list}\n\n"
            f"Country distribution:\n{country_table}\n\n"
            f"Author distribution:\n{authors_table}\n\n"
            f"Journal distribution:\n{journals_table}\n\n"
            f"Topics ({data.get('n_topics', 0)} total):\n{topics_str}\n\n"
            f"Burst keywords: {burst_str}\n"
            f"Network types: {data.get('network_types', [])}\n\n"
            f"Include subsections:\n"
            f"1. Publication Trends — year distribution, growth rate (include figure)\n"
            f"2. Geographic Distribution — country collaboration (include figure)\n"
            f"3. Author Analysis — productivity, H-index (include figure)\n"
            f"4. Journal Analysis — Bradford's law (include figure)\n"
            f"5. Keyword Analysis — MeSH terms, burst detection (include figures)\n"
            f"6. Topic Analysis — topic-word distribution, topic-country heatmap\n"
            f"7. Network Analysis — collaboration networks, centrality\n\n"
            f"For figures use: ![Caption](figures/FILENAME.png)\n\n"
            f"Use ## for subsections. Be specific with numbers. Write 2000-3000 words."
        )

    def _prompt_discussion(self, data: dict, lang: str) -> str:
        domain = data.get("research_domain", "")
        countries = [c for c, _ in data.get("top_countries", [])[:5]]
        topics = data.get("topics", [])[:5]
        topics_str = "; ".join(t.get("words", "")[:50] for t in topics)

        return (
            f"Write the Discussion section for a bibliometric analysis paper about '{domain}'.\n\n"
            f"Key findings to discuss:\n"
            f"- Leading countries: {', '.join(countries)}\n"
            f"- Main research topics: {topics_str}\n"
            f"- {data.get('n_papers', 0)} papers analyzed from {data.get('year_range', '')}\n"
            f"- Burst keywords: {data.get('burst_keywords', [])[:5]}\n"
            f"- Network types analyzed: {data.get('network_types', [])}\n\n"
            f"Include:\n"
            f"1. Key Findings — interpret the most important results\n"
            f"2. Research Hotspots and Trends — emerging directions\n"
            f"3. Comparison with Previous Studies\n"
            f"4. Limitations of This Study\n\n"
            f"Use ## for subsections. Write 1000-1500 words."
        )

    def _prompt_conclusion(self, data: dict, lang: str) -> str:
        domain = data.get("research_domain", "")
        return (
            f"Write the Conclusion section for a bibliometric analysis paper about '{domain}'.\n\n"
            f"Summary: {data.get('n_papers', 0)} papers, {data.get('year_range', '')}, "
            f"{data.get('n_topics', 0)} topics identified.\n"
            f"Top countries: {[c for c, _ in data.get('top_countries', [])[:3]]}\n\n"
            f"Include:\n"
            f"1. Main conclusions\n"
            f"2. Future research directions\n"
            f"3. Practical implications\n\n"
            f"Write 400-600 words."
        )

    def _make_markdown_table(self, headers: list[str], rows: list[list[str]],
                              caption: str) -> str:
        """Generate a Markdown table string for inclusion in prompts."""
        header_row = "| " + " | ".join(headers) + " |"
        separator = "| " + " | ".join("---" for _ in headers) + " |"
        data_rows = "\n".join(
            "| " + " | ".join(cell for cell in row) + " |"
            for row in rows
        )
        return f"**{caption}**\n\n{header_row}\n{separator}\n{data_rows}"

    # ------------------------------------------------------------------
    # References generation
    # ------------------------------------------------------------------
    def _generate_references(self, papers: list, bib_dir: Path, max_refs: int) -> int:
        """Generate both BibTeX and plain-text references."""
        # BibTeX
        entries = []
        for i, p in enumerate(papers[:max_refs]):
            authors_list = p.get("authors") or []
            author_names = []
            for a in authors_list:
                if isinstance(a, dict):
                    author_names.append(a.get("name", ""))
                else:
                    author_names.append(str(a))
            author_str = " and ".join(a for a in author_names if a)

            first_author = author_names[0].split()[-1] if author_names and author_names[0] else f"author{i}"
            year = p.get("year", "XXXX")
            key = re.sub(r'[^a-zA-Z0-9]', '', f"{first_author}{year}")

            title = p.get("title", "").replace("&", r"\&")
            journal = p.get("journal_name", "")
            doi = p.get("doi", "")
            volume = p.get("volume", "")
            issue = p.get("issue", "")
            pages = p.get("pages", "")

            entry_lines = [f"@article{{{key},"]
            entry_lines.append(f"  title = {{{title}}},")
            if author_str:
                entry_lines.append(f"  author = {{{author_str}}},")
            if journal:
                entry_lines.append(f"  journal = {{{journal}}},")
            if year and year != "XXXX":
                entry_lines.append(f"  year = {{{year}}},")
            if volume:
                entry_lines.append(f"  volume = {{{volume}}},")
            if issue:
                entry_lines.append(f"  number = {{{issue}}},")
            if pages:
                entry_lines.append(f"  pages = {{{pages}}},")
            if doi:
                entry_lines.append(f"  doi = {{{doi}}},")
            entry_lines.append("}")
            entries.append("\n".join(entry_lines))

        bib_path = bib_dir / "references.bib"
        bib_path.parent.mkdir(parents=True, exist_ok=True)
        bib_path.write_text("\n\n".join(entries), encoding="utf-8")

        # Plain-text references for PDF builder
        refs_txt = []
        for i, p in enumerate(papers[:max_refs]):
            authors_list = p.get("authors") or []
            author_names = [
                a.get("name", "") if isinstance(a, dict) else str(a)
                for a in authors_list
            ]
            author_str = ", ".join(a for a in author_names if a)
            title = p.get("title", "")
            journal = p.get("journal_name", "")
            year = p.get("year", "")
            doi = p.get("doi", "")
            parts = [f"[{i+1}]"]
            if author_str:
                parts.append(f"{author_str}.")
            if title:
                parts.append(f"{title}.")
            if journal:
                parts.append(f"{journal},")
            if year:
                parts.append(f"{year}.")
            if doi:
                parts.append(f"DOI: {doi}")
            refs_txt.append(" ".join(parts))

        (bib_dir / "references.txt").write_text("\n".join(refs_txt), encoding="utf-8")
        logger.info("Generated %d BibTeX entries + plain-text refs", len(entries))
        return len(entries)

    # ------------------------------------------------------------------
    # LaTeX assembly (Markdown → LaTeX conversion)
    # ------------------------------------------------------------------
    def _assemble_latex(self, output_dir: Path, sections_dir: Path,
                         title: str, language: str, bib_dir: Path) -> None:
        """Assemble a LaTeX document from Markdown section files."""
        section_names = ["abstract", "introduction", "data_methods",
                         "results", "discussion", "conclusion"]

        # Convert each .md section to LaTeX
        tex_sections = {}
        for sec in section_names:
            md_file = sections_dir / f"{sec}.md"
            if md_file.exists():
                md_content = md_file.read_text(encoding="utf-8")
                tex_sections[sec] = self._md_to_latex(md_content, sec)

        # Build main.tex
        if language == "zh":
            preamble = (
                "\\documentclass[12pt,a4paper]{article}\n"
                "\\usepackage[UTF8]{ctex}\n"
                "\\usepackage{graphicx}\n"
                "\\usepackage{booktabs}\n"
                "\\usepackage{hyperref}\n"
                "\\usepackage{geometry}\n"
                "\\usepackage{tabularx}\n"
                "\\usepackage{multirow}\n"
                "\\usepackage{amsmath}\n"
                "\\geometry{margin=2.5cm}\n"
                "\\graphicspath{{figures/}}\n"
            )
        else:
            preamble = (
                "\\documentclass[12pt,a4paper]{article}\n"
                "\\usepackage[english]{babel}\n"
                "\\usepackage{graphicx}\n"
                "\\usepackage{booktabs}\n"
                "\\usepackage{hyperref}\n"
                "\\usepackage{geometry}\n"
                "\\usepackage{tabularx}\n"
                "\\usepackage{multirow}\n"
                "\\usepackage{amsmath}\n"
                "\\geometry{margin=2.5cm}\n"
                "\\graphicspath{{figures/}}\n"
            )

        # Escape special chars in title
        safe_title = self._escape_latex(title)

        body_parts = [f"\\title{{{safe_title}}}"]
        body_parts.append("\\author{Bibliometrics Agent}")
        body_parts.append("\\date{\\today}")
        body_parts.append("\\begin{document}")
        body_parts.append("\\maketitle")

        # Abstract
        if "abstract" in tex_sections:
            body_parts.append("\\begin{abstract}")
            body_parts.append(tex_sections["abstract"])
            body_parts.append("\\end{abstract}")

        # Other sections
        section_headings = {
            "introduction": "Introduction",
            "data_methods": "Data and Methods",
            "results": "Results",
            "discussion": "Discussion",
            "conclusion": "Conclusion",
        }
        if language == "zh":
            section_headings = {
                "introduction": "引言",
                "data_methods": "数据与方法",
                "results": "结果",
                "discussion": "讨论",
                "conclusion": "结论",
            }

        for sec in ["introduction", "data_methods", "results", "discussion", "conclusion"]:
            if sec in tex_sections:
                heading = section_headings.get(sec, sec.replace("_", " ").title())
                body_parts.append(f"\\section{{{heading}}}")
                body_parts.append(tex_sections[sec])

        # Bibliography
        body_parts.append("\\bibliographystyle{unsrt}")
        body_parts.append("\\bibliography{refs/references}")
        body_parts.append("\\end{document}")

        main_tex = preamble + "\n\n" + "\n\n".join(body_parts) + "\n"
        (output_dir / "main.tex").write_text(main_tex, encoding="utf-8")
        logger.info("Assembled main.tex (%d chars)", len(main_tex))

    def _md_to_latex(self, md_text: str, section_name: str) -> str:
        """Convert Markdown content to LaTeX (best-effort)."""
        lines = md_text.split("\n")
        tex_lines: list[str] = []
        i = 0

        while i < len(lines):
            stripped = lines[i].strip()

            # Heading: ## Subsection
            m = re.match(r'^(#{2,4})\s+(.+)$', stripped)
            if m:
                level = len(m.group(1))
                text = self._escape_latex(m.group(2))
                if level == 2:
                    tex_lines.append(f"\\subsection{{{text}}}")
                elif level == 3:
                    tex_lines.append(f"\\subsubsection{{{text}}}")
                else:
                    tex_lines.append(f"\\paragraph{{{text}}}")
                i += 1
                continue

            # Image: ![alt](path)
            m = re.match(r'!\[([^\]]*)\]\(([^)]+)\)', stripped)
            if m:
                alt = self._escape_latex(m.group(1))
                path = m.group(2).replace("figures/", "")
                tex_lines.append("\\begin{figure}[htbp]")
                tex_lines.append("\\centering")
                tex_lines.append(f"\\includegraphics[width=0.85\\linewidth]{{{path}}}")
                tex_lines.append(f"\\caption{{{alt}}}")
                tex_lines.append("\\end{figure}")
                i += 1
                continue

            # Table: collect consecutive | lines
            if stripped.startswith("|"):
                table_lines: list[str] = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    table_lines.append(lines[i])
                    i += 1
                tex_table = self._md_table_to_latex(table_lines)
                if tex_table:
                    tex_lines.append(tex_table)
                continue

            # List item: - item
            if re.match(r'^[-*+]\s+', stripped):
                list_items: list[str] = []
                while i < len(lines) and re.match(r'^[-*+]\s+', lines[i].strip()):
                    m = re.match(r'^[-*+]\s+(.+)$', lines[i].strip())
                    if m:
                        list_items.append(f"\\item {self._inline_md_to_latex(m.group(1))}")
                    i += 1
                tex_lines.append("\\begin{itemize}")
                tex_lines.extend(list_items)
                tex_lines.append("\\end{itemize}")
                continue

            # Empty line
            if not stripped:
                tex_lines.append("")
                i += 1
                continue

            # Regular paragraph
            tex_lines.append(self._inline_md_to_latex(stripped))
            i += 1

        return "\n".join(tex_lines)

    def _inline_md_to_latex(self, text: str) -> str:
        """Convert inline Markdown to LaTeX."""
        # Bold: **text** → \textbf{text}
        text = re.sub(r'\*\*(.+?)\*\*', r'\\textbf{\1}', text)
        # Italic: *text* → \textit{text}
        text = re.sub(r'\*(.+?)\*', r'\\textit{\1}', text)
        # Code: `text` → \texttt{text}
        text = re.sub(r'`(.+?)`', r'\\texttt{\1}', text)
        # Link: [text](url) → text
        text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
        # Citation: [AuthorYear] → \cite{AuthorYear}
        text = re.sub(r'\[([A-Z][a-zA-Z]+\d{4})\]', r'\\cite{\1}', text)
        return self._escape_latex(text)

    def _md_table_to_latex(self, table_lines: list[str]) -> str:
        """Convert Markdown table lines to LaTeX tabular."""
        rows: list[list[str]] = []
        for line in table_lines:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.match(r'^[-:]+$', c) for c in cells):
                continue
            rows.append(cells)
        if not rows:
            return ""

        n_cols = len(rows[0])
        col_spec = "l" * n_cols
        header = " & ".join(self._escape_latex(c) for c in rows[0]) + " \\\\"
        data = "\n".join(
            " & ".join(self._escape_latex(c) for c in row) + " \\\\"
            for row in rows[1:]
        )
        return (
            f"\\begin{{tabular}}{{{col_spec}}}\n"
            f"\\toprule\n{header}\n\\midrule\n{data}\n\\bottomrule\n"
            f"\\end{{tabular}}"
        )

    @staticmethod
    def _escape_latex(text: str) -> str:
        """Escape special LaTeX characters (preserves existing LaTeX commands)."""
        for old, new in [
            ("&", r"\&"), ("%", r"\%"), ("$", r"\$"), ("#", r"\#"),
            ("_", r"\_"), ("~", r"\textasciitilde{}"),
        ]:
            text = text.replace(old, new)
        return text

    # ------------------------------------------------------------------
    # PDF build (via Python script)
    # ------------------------------------------------------------------
    def _build_pdf(self, output_dir: Path, language: str) -> tuple[bool, str]:
        """Build PDF from Markdown sections using scripts/build_pdf.py."""
        script_path = Path(__file__).parent.parent / "scripts" / "build_pdf.py"
        if not script_path.exists():
            return False, f"build_pdf.py not found at {script_path}"

        output_pdf = output_dir / "main.pdf"
        try:
            result = subprocess.run(
                [sys.executable, str(script_path), str(output_dir), str(output_pdf), language],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(output_dir),
            )
            if result.returncode == 0 and output_pdf.exists():
                logger.info("PDF built successfully: %s", output_pdf)
                return True, "PDF built successfully"
            else:
                error = result.stderr or result.stdout or "Unknown error"
                logger.error("PDF build stderr: %s", result.stderr)
                return False, f"PDF build failed: {error[:500]}"
        except subprocess.TimeoutExpired:
            return False, "PDF build timed out (120s)"
        except Exception as e:
            return False, f"PDF build error: {e}"
