"""智能体配置加载器.

参考 kimi-cli 的 agentspec 模块，从 YAML 配置文件加载 Guardian Agent 的定义，
包括工具集、错误模式、修复模板等。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".agents" / "configs"


@dataclass
class ErrorPattern:
    """错误模式定义."""

    name: str
    exceptions: list[str]
    keywords: list[str]
    confidence: float
    template: Optional[str] = None


@dataclass
class FixTemplate:
    """修复模板定义."""

    name: str
    file: str
    description: str


@dataclass
class SubagentSpec:
    """子智能体定义."""

    name: str
    module: str
    description: str
    config_path: Optional[str] = None


@dataclass
class EscalationRule:
    """升级规则."""

    condition: str
    action: str
    message: str


@dataclass
class HITLCheckpoint:
    """HITL 检查点."""

    after: str
    action: str
    message: str


@dataclass
class AgentSpec:
    """智能体完整配置."""

    name: str
    module: str
    description: str = ""
    extends: Optional[str] = None

    # 工具
    tools: list[str] = field(default_factory=list)
    skills: list[dict] = field(default_factory=list)

    # 错误模式
    error_patterns: list[ErrorPattern] = field(default_factory=list)
    fix_templates: dict[str, FixTemplate] = field(default_factory=dict)

    # 子智能体
    subagents: dict[str, SubagentSpec] = field(default_factory=dict)

    # 错误处理策略
    max_retries: int = 3
    retry_delay: float = 2.0
    backoff_factor: float = 2.0
    escalation_threshold: float = 0.5
    auto_apply_threshold: float = 0.9
    require_confirmation: bool = True

    # 工作区
    fixes_dir: str = "fixes"
    logs_dir: str = "agent_logs"
    templates_dir: str = "modules/guardians/templates"

    # 流水线
    pipeline_order: list[str] = field(default_factory=list)

    # 升级规则
    escalation_rules: list[EscalationRule] = field(default_factory=list)

    # HITL 检查点
    hitl_checkpoints: list[HITLCheckpoint] = field(default_factory=list)

    # 原始配置（保留完整 YAML 数据）
    raw_config: dict = field(default_factory=dict)


def load_yaml(path: Path) -> dict:
    """加载 YAML 文件."""
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def resolve_path(ref: str, base_path: Path) -> Path:
    """解析相对路径."""
    p = Path(ref)
    if p.is_absolute():
        return p
    return base_path / p


def load_agent_spec(config_name: str) -> AgentSpec:
    """从 YAML 配置文件加载智能体定义.

    Args:
        config_name: 配置名称（不含 .yaml 后缀），如 "preprocessor_guardian"

    Returns:
        AgentSpec 实例

    Raises:
        FileNotFoundError: 配置文件不存在
    """
    config_path = AGENTS_DIR / f"{config_name}.yaml"

    if not config_path.exists():
        raise FileNotFoundError(f"Agent config not found: {config_path}")

    raw = load_yaml(config_path)
    agent_data = raw.get("agent", {})

    # 处理继承（extend）
    extends = agent_data.get("extend")
    if extends:
        parent_path = resolve_path(extends, config_path.parent)
        parent_raw = load_yaml(parent_path)
        parent_agent = parent_raw.get("agent", {})

        # 合并：子配置覆盖父配置
        merged = {**parent_agent, **agent_data}

        # 列表类型合并（而非覆盖）
        for key in ("tools", "skills"):
            if key in parent_agent and key in agent_data:
                merged[key] = parent_agent.get(key, []) + agent_data.get(key, [])
    else:
        merged = agent_data

    # 解析错误模式
    error_patterns = []
    for pattern_data in merged.get("error_patterns", []):
        error_patterns.append(ErrorPattern(
            name=pattern_data["name"],
            exceptions=pattern_data.get("exceptions", []),
            keywords=pattern_data.get("keywords", []),
            confidence=pattern_data.get("confidence", 0.5),
            template=pattern_data.get("template"),
        ))

    # 解析修复模板
    fix_templates = {}
    for tmpl_name, tmpl_data in merged.get("fix_templates", {}).items():
        fix_templates[tmpl_name] = FixTemplate(
            name=tmpl_name,
            file=tmpl_data["file"],
            description=tmpl_data.get("description", ""),
        )

    # 解析子智能体
    subagents = {}
    for sa_name, sa_data in merged.get("subagents", {}).items():
        subagents[sa_name] = SubagentSpec(
            name=sa_name,
            module=sa_data.get("module", sa_name),
            description=sa_data.get("description", ""),
            config_path=sa_data.get("path"),
        )

    # 解析错误处理策略
    eh = merged.get("error_handling", {})
    decisions = merged.get("decisions", {})
    workspace = merged.get("workspace", {})

    # 解析流水线
    pipeline = merged.get("pipeline", {})
    pipeline_order = pipeline.get("order", [])

    # 解析升级规则
    escalation_rules = []
    for rule_data in merged.get("escalation", {}).get("rules", []):
        escalation_rules.append(EscalationRule(
            condition=rule_data["condition"],
            action=rule_data["action"],
            message=rule_data["message"],
        ))

    # 解析 HITL 检查点
    hitl_checkpoints = []
    for cp_data in merged.get("hitl", {}).get("checkpoints", []):
        hitl_checkpoints.append(HITLCheckpoint(
            after=cp_data["after"],
            action=cp_data["action"],
            message=cp_data["message"],
        ))

    return AgentSpec(
        name=merged.get("name", config_name),
        module=merged.get("module", ""),
        description=merged.get("description", ""),
        extends=extends,
        tools=merged.get("tools", []),
        skills=merged.get("skills", []),
        error_patterns=error_patterns,
        fix_templates=fix_templates,
        subagents=subagents,
        max_retries=eh.get("max_retries", 3),
        retry_delay=eh.get("retry_delay", 2.0),
        backoff_factor=eh.get("backoff_factor", 2.0),
        escalation_threshold=eh.get("escalation_threshold", 0.5),
        auto_apply_threshold=decisions.get("auto_apply_threshold", 0.9),
        require_confirmation=decisions.get("require_confirmation", True),
        fixes_dir=workspace.get("fixes_dir", "fixes"),
        logs_dir=workspace.get("logs_dir", "agent_logs"),
        templates_dir=workspace.get("templates_dir", "modules/guardians/templates"),
        pipeline_order=pipeline_order,
        escalation_rules=escalation_rules,
        hitl_checkpoints=hitl_checkpoints,
        raw_config=merged,
    )


def list_available_agents() -> list[str]:
    """列出所有可用的智能体配置."""
    if not AGENTS_DIR.exists():
        return []
    return [
        p.stem
        for p in AGENTS_DIR.glob("*.yaml")
        if p.stem != "base_guardian"  # 基类配置不直接使用
    ]


def get_agent_config_for_module(module_name: str) -> Optional[AgentSpec]:
    """获取模块对应的智能体配置.

    Args:
        module_name: 模块名称（如 "preprocessor"）

    Returns:
        AgentSpec 或 None（如果模块没有配置）
    """
    config_name = f"{module_name}_guardian"
    config_path = AGENTS_DIR / f"{config_name}.yaml"

    if config_path.exists():
        return load_agent_spec(config_name)

    return None
