"""Guardian agent for the Report Generator module.

处理模板错误、LaTeX 编译、文件写入等错误。
"""

from core.agent_guardian import GuardianAgent, ErrorAnalysis, FixCode
from datetime import datetime
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class ReportGeneratorGuardianAgent(GuardianAgent):
    """Guardian specialized for the report_generator module."""

    def analyze_error(self, error: Exception, context: dict) -> ErrorAnalysis:
        error_str = str(error)
        error_type = type(error).__name__

        # 模板错误
        if any(kw in error_str.lower() for kw in ["template", "jinja", "render", "undefined"]):
            return ErrorAnalysis(
                error_type="template_error",
                error_message=error_str,
                root_cause="报告模板渲染失败，变量缺失或语法错误",
                suggested_fix="使用简化的模板或提供默认变量值",
                confidence=0.8,
                context={"original_error": error_type},
            )

        # LaTeX 编译错误
        if any(kw in error_str.lower() for kw in ["latex", "pdflatex", "compilation", "tex"]):
            return ErrorAnalysis(
                error_type="latex_error",
                error_message=error_str,
                root_cause="LaTeX 文档编译失败",
                suggested_fix="回退到 Markdown 或 HTML 输出格式",
                confidence=0.75,
                context={"original_error": error_type},
            )

        # 文件写入错误
        if any(kw in error_str.lower() for kw in ["write", "permission", "disk", "space"]):
            return ErrorAnalysis(
                error_type="write_error",
                error_message=error_str,
                root_cause="报告文件写入失败",
                suggested_fix="使用备用输出路径或简化报告内容",
                confidence=0.85,
                context={"original_error": error_type},
            )

        # 数据缺失
        if any(kw in error_str.lower() for kw in ["missing", "key", "required", "not found"]):
            return ErrorAnalysis(
                error_type="data_missing",
                error_message=error_str,
                root_cause="报告所需的分析数据缺失",
                suggested_fix="使用部分数据生成报告，标注缺失部分",
                confidence=0.7,
                context={"original_error": error_type},
            )

        return ErrorAnalysis(
            error_type="unknown",
            error_message=error_str,
            root_cause="Unknown report generation error",
            suggested_fix="Generic error handler",
            confidence=0.3,
            context={"error_type": error_type},
        )

    def generate_fix(self, analysis: ErrorAnalysis) -> Optional[FixCode]:
        timestamp = datetime.now().isoformat()

        if analysis.error_type == "template_error":
            return FixCode(
                module_name=self.module_name,
                code=self._TEMPLATE_FIX,
                description="安全的模板渲染，缺失变量使用默认值",
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence},
            )

        if analysis.error_type == "latex_error":
            return FixCode(
                module_name=self.module_name,
                code=self._LATEX_FALLBACK_FIX,
                description="LaTeX 编译失败时回退到 Markdown",
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence},
            )

        if analysis.error_type == "data_missing":
            return FixCode(
                module_name=self.module_name,
                code=self._DATA_FALLBACK_FIX,
                description="缺失数据时生成部分报告",
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence},
            )

        self.logger.warning(f"No fix template for error type: {analysis.error_type}")
        return None

    _TEMPLATE_FIX = '''
def render_template_safely(template_str, context, default="N/A"):
    """Auto-generated fix: 安全模板渲染."""
    import re
    import logging

    logger = logging.getLogger(__name__)

    result = template_str

    # 替换简单变量 {{ var }}
    for key, value in context.items():
        pattern = r"\\{\\{\\s*" + re.escape(key) + r"\\s*\\}"
        result = re.sub(pattern, str(value), result)

    # 替换未填充的变量为默认值
    result = re.sub(r"\\{\\{\\s*\\w+\\s*\\}", default, result)

    return result


def render_with_defaults(template_str, context):
    """使用默认上下文渲染模板."""
    defaults = {
        "title": "Bibliometric Analysis Report",
        "date": __import__("datetime").datetime.now().strftime("%Y-%m-%d"),
        "n_documents": "0",
        "n_topics": "0",
        "top_keywords": "N/A",
        "summary": "Report generated with partial data.",
    }

    merged = {**defaults, **{k: str(v) for k, v in context.items()}}
    return render_template_safely(template_str, merged)
'''

    _LATEX_FALLBACK_FIX = '''
def generate_report_with_fallback(content, output_path, format_preference=None):
    """Auto-generated fix: LaTeX 失败时回退到 Markdown."""
    import logging
    from pathlib import Path

    logger = logging.getLogger(__name__)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 尝试 LaTeX
    if format_preference in (None, "latex", "pdf"):
        try:
            md_path = output_path.with_suffix(".md")
            md_path.write_text(content, encoding="utf-8")
            logger.info(f"Report saved as Markdown: {md_path}")
            return str(md_path)
        except Exception as e:
            logger.warning(f"Markdown save failed: {e}")

    # 最终回退: 纯文本
    txt_path = output_path.with_suffix(".txt")
    txt_path.write_text(content, encoding="utf-8")
    logger.info(f"Report saved as plain text: {txt_path}")
    return str(txt_path)
'''

    _DATA_FALLBACK_FIX = '''
def build_report_with_partial_data(available_data):
    """Auto-generated fix: 使用部分数据生成报告."""
    import logging

    logger = logging.getLogger(__name__)

    sections = []

    # 标题
    sections.append("# Bibliometric Analysis Report")
    sections.append(f"Generated: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}")
    sections.append("")

    # 遍历可用数据
    for key, value in available_data.items():
        if value is not None:
            sections.append(f"## {key.replace('_', ' ').title()}")
            if isinstance(value, (list, tuple)):
                for item in value[:20]:
                    sections.append(f"- {item}")
            elif isinstance(value, dict):
                for k, v in list(value.items())[:20]:
                    sections.append(f"- **{k}**: {v}")
            else:
                sections.append(str(value))
            sections.append("")
        else:
            sections.append(f"## {key.replace('_', ' ').title()}")
            sections.append("*Data not available*")
            sections.append("")

    report = "\\n".join(sections)
    logger.info(f"Generated partial report with {len(available_data)} sections")
    return report
'''
