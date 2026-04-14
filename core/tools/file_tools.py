"""文件操作工具: ReadFile, WriteFile, Glob, Grep."""

from pathlib import Path
from typing import Optional
import logging
import re

from core.tools import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class ReadFile(BaseTool):
    """读取文件."""

    name = "ReadFile"
    description = "读取文件内容，支持文本和二进制文件"

    def run(self, file_path: str, encoding: str = "utf-8", offset: int = 0, limit: int = 0) -> ToolResult:
        try:
            path = Path(file_path)
            if not path.is_absolute():
                path = self.workspace_dir / path

            if not path.exists():
                return ToolResult(success=False, output=None, error=f"File not found: {path}")

            content = path.read_text(encoding=encoding)

            if offset or limit:
                lines = content.splitlines()
                end = offset + limit if limit else len(lines)
                content = "\n".join(lines[offset:end])

            return ToolResult(
                success=True, output=content,
                metadata={"file_path": str(path), "size": len(content)},
            )
        except Exception as e:
            return ToolResult(success=False, output=None, error=str(e))


class WriteFile(BaseTool):
    """写入文件."""

    name = "WriteFile"
    description = "写入文件内容，用于生成修复代码"

    def run(self, file_path: str, content: str, encoding: str = "utf-8") -> ToolResult:
        try:
            path = Path(file_path)
            if not path.is_absolute():
                path = self.workspace_dir / path

            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding=encoding)

            return ToolResult(
                success=True, output=str(path),
                metadata={"file_path": str(path), "size": len(content)},
            )
        except Exception as e:
            return ToolResult(success=False, output=None, error=str(e))


class Glob(BaseTool):
    """文件搜索."""

    name = "Glob"
    description = "搜索匹配 glob 模式的文件"

    def run(self, pattern: str, directory: Optional[str] = None) -> ToolResult:
        try:
            search_dir = Path(directory) if directory else self.workspace_dir
            if not search_dir.is_absolute():
                search_dir = self.workspace_dir / search_dir

            matches = sorted(search_dir.glob(pattern))

            return ToolResult(
                success=True,
                output=[str(m.relative_to(self.workspace_dir)) for m in matches],
                metadata={"pattern": pattern, "count": len(matches)},
            )
        except Exception as e:
            return ToolResult(success=False, output=[], error=str(e))


class Grep(BaseTool):
    """内容搜索."""

    name = "Grep"
    description = "在文件中搜索匹配正则表达式的行"

    def run(self, pattern: str, directory: Optional[str] = None, file_pattern: str = "*.py") -> ToolResult:
        try:
            search_dir = Path(directory) if directory else self.workspace_dir
            if not search_dir.is_absolute():
                search_dir = self.workspace_dir / search_dir

            regex = re.compile(pattern, re.IGNORECASE)
            results = []

            for file_path in search_dir.rglob(file_pattern):
                if file_path.is_file():
                    try:
                        lines = file_path.read_text(encoding="utf-8").splitlines()
                        for i, line in enumerate(lines, 1):
                            if regex.search(line):
                                results.append({
                                    "file": str(file_path.relative_to(self.workspace_dir)),
                                    "line": i,
                                    "content": line.strip(),
                                })
                    except Exception:
                        continue

            return ToolResult(
                success=True, output=results,
                metadata={"pattern": pattern, "matches": len(results)},
            )
        except Exception as e:
            return ToolResult(success=False, output=[], error=str(e))
