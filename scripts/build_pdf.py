"""Standalone PDF builder for bibliometric papers.

Reads Markdown sections from an input directory and generates a formatted PDF
using fpdf2 with CJK (Chinese) font support.

Usage:
    python scripts/build_pdf.py <input_dir> <output.pdf> [zh|en]

Input directory structure:
    input_dir/
      title.txt              Paper title
      sections/
        abstract.md
        introduction.md
        data_methods.md
        results.md
        discussion.md
        conclusion.md
      refs/
        references.txt       Plain-text reference list
      figures/
        *.png                Chart images
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from fpdf import FPDF


# ---------------------------------------------------------------------------
#  Font resolution
# ---------------------------------------------------------------------------

def _find_cjk_fonts() -> tuple[str | None, str | None]:
    """Find CJK-capable TTF fonts. Returns (regular_path, bold_path) or Nones."""
    # 1. Bundled fonts in scripts/fonts/
    bundle_dir = Path(__file__).parent / "fonts"
    bundled_regular = bundle_dir / "NotoSansSC-Regular.ttf"
    bundled_bold = bundle_dir / "NotoSansSC-Bold.ttf"
    if bundled_regular.exists() and bundled_bold.exists():
        return str(bundled_regular), str(bundled_bold)

    # 2. Windows system fonts
    sys_fonts = Path("C:/Windows/Fonts")
    candidates = [
        ("msyh.ttc", "msyhbd.ttc"),   # Microsoft YaHei
        ("simhei.ttf", "simhei.ttf"),  # SimHei (no bold variant)
        ("simsun.ttc", "simsun.ttc"),  # SimSun
    ]
    for regular, bold in candidates:
        if (sys_fonts / regular).exists():
            bold_path = sys_fonts / bold if (sys_fonts / bold).exists() else sys_fonts / regular
            return str(sys_fonts / regular), str(bold_path)

    return None, None


# ---------------------------------------------------------------------------
#  PDF renderer
# ---------------------------------------------------------------------------

class PaperPDF(FPDF):
    """PDF renderer with CJK support and Markdown parsing."""

    def __init__(self, language: str = "zh"):
        super().__init__()
        self.language = language
        self._setup_fonts()
        self.set_auto_page_break(auto=True, margin=25)

    def _setup_fonts(self):
        regular, bold = _find_cjk_fonts()
        if regular:
            self.add_font("cjk", "", regular)
            self.add_font("cjk", "B", bold or regular)
            self._font_family = "cjk"
        else:
            self._font_family = "Helvetica"

    @property
    def font_regular(self):
        return self._font_family

    @property
    def font_bold(self):
        return self._font_family

    def header(self):
        if self.page_no() > 1:
            self.set_font(self.font_regular, "", 8)
            self.set_text_color(150, 150, 150)
            self.cell(0, 8, "", align="C")
            self.ln(2)

    def footer(self):
        self.set_y(-15)
        self.set_font(self.font_regular, "", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, str(self.page_no()), align="C")
        self.set_text_color(0, 0, 0)

    # --- Markdown block parsing ---

    def render_markdown(self, md_text: str, figures_dir: Path | None = None):
        """Parse and render a Markdown string to PDF."""
        blocks = self._split_blocks(md_text)
        for block in blocks:
            self._render_block(block, figures_dir)

    def _split_blocks(self, text: str) -> list[dict]:
        blocks: list[dict] = []
        lines = text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]

            # Heading
            m = re.match(r"^(#{1,4})\s+(.+)$", line)
            if m:
                blocks.append({"type": "heading", "level": len(m.group(1)), "text": m.group(2)})
                i += 1
                continue

            # Image: ![alt](path)
            m = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", line.strip())
            if m:
                blocks.append({"type": "image", "alt": m.group(1), "path": m.group(2)})
                i += 1
                continue

            # Table (starts with |)
            if line.strip().startswith("|"):
                table_lines: list[str] = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    table_lines.append(lines[i])
                    i += 1
                blocks.append({"type": "table", "lines": table_lines})
                continue

            # List item
            if re.match(r"^(\s*)[-*+]\s+", line):
                items: list[dict] = []
                while i < len(lines) and re.match(r"^(\s*)[-*+]\s+", lines[i]):
                    lm = re.match(r"^(\s*)[-*+]\s+(.+)$", lines[i])
                    if lm:
                        items.append({"indent": len(lm.group(1)), "text": lm.group(2)})
                    i += 1
                blocks.append({"type": "list", "items": items})
                continue

            # Numbered list
            if re.match(r"^(\s*)\d+\.\s+", line):
                items = []
                while i < len(lines) and re.match(r"^(\s*)\d+\.\s+", lines[i]):
                    lm = re.match(r"^(\s*)\d+\.\s+(.+)$", lines[i])
                    if lm:
                        items.append({"indent": len(lm.group(1)), "text": lm.group(2)})
                    i += 1
                blocks.append({"type": "olist", "items": items})
                continue

            # Paragraph (non-empty, non-special)
            if line.strip():
                para_lines: list[str] = []
                while i < len(lines) and lines[i].strip() and not lines[i].startswith("#"):
                    if lines[i].strip().startswith("|") or re.match(r"^[-*+]\s+", lines[i].strip()):
                        break
                    para_lines.append(lines[i])
                    i += 1
                if para_lines:
                    blocks.append({"type": "paragraph", "text": " ".join(para_lines)})
                continue

            i += 1  # skip blank lines

        return blocks

    def _render_block(self, block: dict, figures_dir: Path | None = None):
        btype = block["type"]
        if btype == "heading":
            sizes = {1: 16, 2: 13, 3: 11, 4: 10}
            self.set_font(self.font_bold, "B", sizes.get(block["level"], 11))
            self.set_text_color(30, 30, 30)
            self.multi_cell(0, 7, block["text"])
            self.ln(3)
        elif btype == "paragraph":
            self.set_font(self.font_regular, "", 10)
            self.set_text_color(40, 40, 40)
            text = self._clean_inline(block["text"])
            self.multi_cell(0, 5.5, text)
            self.ln(2)
        elif btype == "list":
            self.set_font(self.font_regular, "", 10)
            self.set_text_color(40, 40, 40)
            for item in block["items"]:
                x = self.l_margin + item["indent"] * 3
                self.set_x(x)
                self.cell(5, 5.5, chr(8226))  # bullet
                self.multi_cell(0, 5.5, self._clean_inline(item["text"]))
            self.ln(2)
        elif btype == "olist":
            self.set_font(self.font_regular, "", 10)
            self.set_text_color(40, 40, 40)
            for idx, item in enumerate(block["items"], 1):
                x = self.l_margin + item["indent"] * 3
                self.set_x(x)
                self.cell(7, 5.5, f"{idx}.")
                self.multi_cell(0, 5.5, self._clean_inline(item["text"]))
            self.ln(2)
        elif btype == "table":
            self._render_table(block["lines"])
        elif btype == "image":
            self._render_image(block["path"], block["alt"], figures_dir)

    def _clean_inline(self, text: str) -> str:
        """Remove Markdown inline formatting for plain text rendering."""
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)   # bold
        text = re.sub(r"\*(.+?)\*", r"\1", text)        # italic
        text = re.sub(r"`(.+?)`", r"\1", text)          # code
        text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text) # links
        return text

    def _render_table(self, lines: list[str]):
        rows: list[list[str]] = []
        for line in lines:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.match(r"^[-:]+$", c) for c in cells):
                continue  # skip separator
            rows.append(cells)
        if not rows:
            return

        n_cols = len(rows[0])
        page_w = self.epw
        col_w = page_w / n_cols

        self.set_font(self.font_regular, "B", 8.5)
        self.set_fill_color(240, 240, 240)
        self.set_text_color(30, 30, 30)
        for cell in rows[0]:
            self.cell(col_w, 6.5, cell[:40], border=1, fill=True)
        self.ln()

        self.set_font(self.font_regular, "", 8.5)
        self.set_text_color(40, 40, 40)
        for row in rows[1:]:
            for cell in row:
                self.cell(col_w, 6, cell[:50], border=1)
            self.ln()
        self.ln(3)

    def _render_image(self, path: str, alt: str, figures_dir: Path | None = None):
        """Embed an image if it exists."""
        # Resolve relative paths against figures_dir
        candidates = [Path(path)]
        if figures_dir:
            candidates.insert(0, figures_dir / Path(path).name)
            candidates.insert(0, figures_dir / path)

        img_path = None
        for c in candidates:
            if c.exists():
                img_path = c
                break

        if img_path and img_path.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".bmp"):
            try:
                w = min(self.epw * 0.85, 160)
                x = (self.w - w) / 2
                self.image(str(img_path), x=x, w=w)
                if alt:
                    self.set_font(self.font_regular, "", 8)
                    self.set_text_color(120, 120, 120)
                    self.cell(0, 5, alt, align="C")
                    self.ln()
            except Exception:
                # Image failed, show alt text
                self.set_font(self.font_regular, "", 9)
                self.set_text_color(120, 120, 120)
                self.cell(0, 5, f"[Image: {alt or path}]")
                self.ln()
        else:
            self.set_font(self.font_regular, "", 9)
            self.set_text_color(120, 120, 120)
            self.cell(0, 5, f"[Image: {alt or path}]")
            self.ln()
        self.set_text_color(40, 40, 40)
        self.ln(2)


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 3:
        print("Usage: python build_pdf.py <input_dir> <output.pdf> [zh|en]")
        sys.exit(1)

    input_dir = Path(sys.argv[1])
    output_pdf = Path(sys.argv[2])
    language = sys.argv[3] if len(sys.argv) > 3 else "zh"

    if not input_dir.exists():
        print(f"Error: input directory not found: {input_dir}")
        sys.exit(1)

    pdf = PaperPDF(language=language)
    figures_dir = input_dir / "figures"

    # Title page
    pdf.add_page()
    title = ""
    title_file = input_dir / "title.txt"
    if title_file.exists():
        title = title_file.read_text(encoding="utf-8").strip()

    if title:
        pdf.set_font(pdf.font_bold, "B", 18)
        pdf.set_text_color(20, 20, 20)
        pdf.multi_cell(0, 10, title, align="C")
        pdf.ln(12)

    # Section order
    section_order = [
        "abstract", "introduction", "data_methods",
        "results", "discussion", "conclusion",
    ]

    sections_dir = input_dir / "sections"
    for sec_name in section_order:
        sec_file = sections_dir / f"{sec_name}.md"
        if sec_file.exists():
            content = sec_file.read_text(encoding="utf-8")
            if content.strip():
                # Add section heading
                heading_map = {
                    "abstract": "Abstract" if language == "en" else "摘要",
                    "introduction": "Introduction" if language == "en" else "引言",
                    "data_methods": "Data and Methods" if language == "en" else "数据与方法",
                    "results": "Results" if language == "en" else "结果",
                    "discussion": "Discussion" if language == "en" else "讨论",
                    "conclusion": "Conclusion" if language == "en" else "结论",
                }
                heading = heading_map.get(sec_name, sec_name.replace("_", " ").title())
                pdf.set_font(pdf.font_bold, "B", 14)
                pdf.set_text_color(30, 30, 30)
                pdf.multi_cell(0, 8, heading)
                pdf.ln(4)
                pdf.render_markdown(content, figures_dir)

    # References
    refs_file = input_dir / "refs" / "references.txt"
    if refs_file.exists():
        pdf.add_page()
        pdf.set_font(pdf.font_bold, "B", 14)
        pdf.set_text_color(30, 30, 30)
        heading = "References" if language == "en" else "参考文献"
        pdf.multi_cell(0, 8, heading)
        pdf.ln(4)
        pdf.set_font(pdf.font_regular, "", 8.5)
        pdf.set_text_color(40, 40, 40)
        refs_text = refs_file.read_text(encoding="utf-8")
        pdf.multi_cell(0, 4.5, refs_text)

    # Output
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output_pdf))
    print(f"PDF generated: {output_pdf}")


if __name__ == "__main__":
    main()
