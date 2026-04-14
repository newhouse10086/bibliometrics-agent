"""Shell 命令执行工具."""

import subprocess
import logging

from core.tools import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class Shell(BaseTool):
    """执行 Shell 命令."""

    name = "Shell"
    description = "执行 Shell 命令，用于测试修复、诊断错误"

    def run(self, command: str, timeout: int = 30, capture_output: bool = True) -> ToolResult:
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace_dir,
                capture_output=capture_output,
                text=True,
                timeout=timeout,
            )

            output = result.stdout if capture_output else None
            error = result.stderr if capture_output and result.returncode != 0 else None

            return ToolResult(
                success=result.returncode == 0,
                output=output,
                error=error,
                metadata={"command": command, "returncode": result.returncode},
            )

        except subprocess.TimeoutExpired:
            return ToolResult(success=False, output=None, error=f"Command timeout after {timeout}s")
        except Exception as e:
            return ToolResult(success=False, output=None, error=str(e))
