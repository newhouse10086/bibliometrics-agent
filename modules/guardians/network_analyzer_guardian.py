"""Guardian agent for the Network Analyzer module.

处理图构建、布局计算、内存错误等。
"""

from core.agent_guardian import GuardianAgent, ErrorAnalysis, FixCode
from datetime import datetime
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class NetworkAnalyzerGuardianAgent(GuardianAgent):
    """Guardian specialized for the network_analyzer module."""

    def analyze_error(self, error: Exception, context: dict) -> ErrorAnalysis:
        error_str = str(error)
        error_type = type(error).__name__

        # 图构建错误
        if any(kw in error_str.lower() for kw in ["graph", "node", "edge", "networkx", "adjacency"]):
            return ErrorAnalysis(
                error_type="graph_construction",
                error_message=error_str,
                root_cause="图数据结构构建失败",
                suggested_fix="验证输入数据的图结构完整性",
                confidence=0.8,
                context={"original_error": error_type},
            )

        # 布局计算错误
        if any(kw in error_str.lower() for kw in ["layout", "position", "spring", "kamada"]):
            return ErrorAnalysis(
                error_type="layout_error",
                error_message=error_str,
                root_cause="图布局算法计算失败",
                suggested_fix="使用更简单的布局算法或减少节点数",
                confidence=0.75,
                context={"original_error": error_type},
            )

        # 内存错误
        if "MemoryError" in error_type or "memory" in error_str.lower():
            return ErrorAnalysis(
                error_type="memory",
                error_message=error_str,
                root_cause="图太大无法全部加载到内存",
                suggested_fix="只分析核心子图或使用稀疏表示",
                confidence=0.9,
                context={"original_error": error_type},
            )

        return ErrorAnalysis(
            error_type="unknown",
            error_message=error_str,
            root_cause="Unknown network analysis error",
            suggested_fix="Generic error handler",
            confidence=0.3,
            context={"error_type": error_type},
        )

    def generate_fix(self, analysis: ErrorAnalysis) -> Optional[FixCode]:
        timestamp = datetime.now().isoformat()

        if analysis.error_type == "graph_construction":
            return FixCode(
                module_name=self.module_name,
                code=self._GRAPH_FIX,
                description="修复图构建错误",
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence},
            )

        if analysis.error_type == "layout_error":
            return FixCode(
                module_name=self.module_name,
                code=self._LAYOUT_FALLBACK,
                description="布局计算回退方案",
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence},
            )

        self.logger.warning(f"No fix template for error type: {analysis.error_type}")
        return None

    _GRAPH_FIX = '''
def build_graph_safely(co_occurrence_matrix, threshold=0.0):
    """Auto-generated fix: 安全构建共现网络图."""
    import networkx as nx
    import numpy as np
    import logging

    logger = logging.getLogger(__name__)

    try:
        G = nx.from_numpy_array(co_occurrence_matrix)
    except Exception:
        G = nx.Graph()
        matrix = np.array(co_occurrence_matrix)
        n = matrix.shape[0]

        for i in range(n):
            G.add_node(i)
            for j in range(i + 1, n):
                weight = float(matrix[i, j])
                if weight > threshold:
                    G.add_edge(i, j, weight=weight)

    # 移除孤立节点（可选）
    isolates = list(nx.isolates(G))
    if isolates:
        logger.info(f"Removing {len(isolates)} isolated nodes")

    return G
'''

    _LAYOUT_FALLBACK = '''
def compute_layout_with_fallback(G, max_nodes_for_spring=500):
    """Auto-generated fix: 布局计算带回退."""
    import networkx as nx
    import logging

    logger = logging.getLogger(__name__)

    n_nodes = G.number_of_nodes()

    if n_nodes == 0:
        return {}

    # 优先使用 spring layout
    if n_nodes <= max_nodes_for_spring:
        try:
            return nx.spring_layout(G, k=1.5/np.sqrt(n_nodes), iterations=50, seed=42)
        except Exception:
            pass

    # 回退 1: circular layout
    try:
        return nx.circular_layout(G)
    except Exception:
        pass

    # 回退 2: random layout
    try:
        return nx.random_layout(G, seed=42)
    except Exception:
        return {n: (i / n_nodes, 0.5) for i, n in enumerate(G.nodes())}
'''
