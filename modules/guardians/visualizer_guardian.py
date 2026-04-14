"""Guardian agent for the Visualizer module.

处理图表渲染、文件路径、依赖缺失等错误。
"""

from core.agent_guardian import GuardianAgent, ErrorAnalysis, FixCode
from datetime import datetime
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class VisualizerGuardianAgent(GuardianAgent):
    """Guardian specialized for the visualizer module."""

    def analyze_error(self, error: Exception, context: dict) -> ErrorAnalysis:
        error_str = str(error)
        error_type = type(error).__name__

        # 渲染错误
        if any(kw in error_str.lower() for kw in ["render", "plot", "figure", "axes", "display"]):
            return ErrorAnalysis(
                error_type="render_error",
                error_message=error_str,
                root_cause="图表渲染失败",
                suggested_fix="使用简化的渲染参数或回退渲染器",
                confidence=0.8,
                context={"original_error": error_type},
            )

        # 文件路径错误
        if any(kw in error_str.lower() for kw in ["path", "not found", "no such", "directory", "permission"]):
            return ErrorAnalysis(
                error_type="file_error",
                error_message=error_str,
                root_cause="输出文件路径无效或无写入权限",
                suggested_fix="创建缺失目录或使用备用路径",
                confidence=0.85,
                context={"original_error": error_type},
            )

        # 依赖缺失
        if any(kw in error_str.lower() for kw in ["import", "module", "no module", "not found"]):
            return ErrorAnalysis(
                error_type="dependency_missing",
                error_message=error_str,
                root_cause="可视化依赖包未安装",
                suggested_fix="使用替代渲染方案或提示安装依赖",
                confidence=0.9,
                context={"original_error": error_type},
            )

        return ErrorAnalysis(
            error_type="unknown",
            error_message=error_str,
            root_cause="Unknown visualization error",
            suggested_fix="Generic error handler",
            confidence=0.3,
            context={"error_type": error_type},
        )

    def generate_fix(self, analysis: ErrorAnalysis) -> Optional[FixCode]:
        timestamp = datetime.now().isoformat()

        if analysis.error_type == "render_error":
            return FixCode(
                module_name=self.module_name,
                code=self._SAFE_RENDER_FIX,
                description="安全的图表渲染，回退到 Agg 后端",
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence},
            )

        if analysis.error_type == "file_error":
            return FixCode(
                module_name=self.module_name,
                code=self._PATH_FIX,
                description="自动创建缺失目录并处理路径问题",
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence},
            )

        if analysis.error_type == "dependency_missing":
            return FixCode(
                module_name=self.module_name,
                code=self._DEPS_FIX,
                description="检测并处理缺失的可视化依赖",
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence},
            )

        self.logger.warning(f"No fix template for error type: {analysis.error_type}")
        return None

    _SAFE_RENDER_FIX = '''
def safe_save_figure(fig, output_path, dpi=150, formats=None):
    """Auto-generated fix: 安全保存图表，支持回退渲染器."""
    import logging
    from pathlib import Path

    logger = logging.getLogger(__name__)
    formats = formats or ["png"]

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    for fmt in formats:
        try:
            save_path = output_path.with_suffix(f".{fmt}")
            fig.savefig(str(save_path), dpi=dpi, bbox_inches="tight", format=fmt)
            logger.info(f"Saved figure: {save_path}")
            return str(save_path)
        except Exception as e:
            logger.warning(f"Failed to save as {fmt}: {e}")
            continue

    # Final fallback: try with Agg backend
    try:
        import matplotlib
        matplotlib.use("Agg")
        save_path = output_path.with_suffix(".png")
        fig.savefig(str(save_path), dpi=dpi, bbox_inches="tight")
        return str(save_path)
    except Exception as e:
        logger.error(f"All render attempts failed: {e}")
        return None
'''

    _PATH_FIX = '''
def ensure_output_dir(output_path):
    """Auto-generated fix: 确保输出目录存在."""
    from pathlib import Path
    import logging

    logger = logging.getLogger(__name__)

    path = Path(output_path)

    if path.suffix:
        directory = path.parent
    else:
        directory = path

    try:
        directory.mkdir(parents=True, exist_ok=True)
        return str(path)
    except PermissionError:
        fallback = Path.cwd() / "output" / path.name
        fallback.parent.mkdir(parents=True, exist_ok=True)
        logger.warning(f"Permission denied, using fallback: {fallback}")
        return str(fallback)
'''

    _DEPS_FIX = '''
def get_available_backend():
    """Auto-generated fix: 检测可用的可视化后端."""
    import logging

    logger = logging.getLogger(__name__)

    backends = ["matplotlib", "plotly", "bokeh"]

    for backend in backends:
        try:
            __import__(backend)
            logger.info(f"Using visualization backend: {backend}")
            return backend
        except ImportError:
            continue

    logger.warning("No visualization backend available")
    return None


def safe_plot(data, title="", output_path=None):
    """使用可用的后端绘图."""
    backend = get_available_backend()

    if backend == "matplotlib":
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots()
        ax.plot(data)
        ax.set_title(title)
        if output_path:
            fig.savefig(output_path, dpi=150, bbox_inches="tight")
        return fig
    else:
        logger = logging.getLogger(__name__)
        logger.warning("No plot backend, saving data as text")
        if output_path:
            with open(output_path + ".txt", "w") as f:
                for i, v in enumerate(data):
                    f.write(f"{i}\\t{v}\\n")
        return None
'''
