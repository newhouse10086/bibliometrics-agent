"""工具基类和注册表."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """工具执行结果."""

    success: bool
    output: Any
    error: Optional[str] = None
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class BaseTool(ABC):
    """工具基类."""

    name: str
    description: str

    def __init__(self, workspace_dir: Path):
        self.workspace_dir = workspace_dir
        self.logger = logging.getLogger(f"tool.{self.name}")

    @abstractmethod
    def run(self, *args, **kwargs) -> ToolResult:
        pass

    def validate_args(self, *args, **kwargs) -> bool:
        return True


class ToolRegistry:
    """工具注册表."""

    def __init__(self, workspace_dir: Path):
        self.workspace_dir = workspace_dir
        self._tools: dict[str, type[BaseTool]] = {}

    def register(self, tool_class: type[BaseTool]):
        self._tools[tool_class.name] = tool_class
        logger.info(f"Registered tool: {tool_class.name}")

    def get(self, tool_name: str) -> Optional[BaseTool]:
        tool_class = self._tools.get(tool_name)
        if tool_class:
            return tool_class(self.workspace_dir)
        return None

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())


def create_default_registry(workspace_dir: Path) -> ToolRegistry:
    """创建默认工具注册表（包含全部工具）."""
    from core.tools.file_tools import ReadFile, WriteFile, Glob, Grep
    from core.tools.shell_tool import Shell
    from core.tools.web_tools import WebSearch, WebFetch, HttpRequest

    registry = ToolRegistry(workspace_dir)

    # 文件操作
    registry.register(ReadFile)
    registry.register(WriteFile)
    registry.register(Glob)
    registry.register(Grep)

    # Shell 执行
    registry.register(Shell)

    # Web 工具
    registry.register(WebSearch)
    registry.register(WebFetch)
    registry.register(HttpRequest)

    return registry
