"""Guardian agent for the TSR Ranker module.

处理数学计算错误、指标缺失、权重配置错误等。
"""

from core.agent_guardian import GuardianAgent, ErrorAnalysis, FixCode
from datetime import datetime
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class TSRRankerGuardianAgent(GuardianAgent):
    """Guardian specialized for the tsr_ranker module."""

    def analyze_error(self, error: Exception, context: dict) -> ErrorAnalysis:
        error_str = str(error)
        error_type = type(error).__name__

        # 数学计算错误
        if any(kw in error_str.lower() for kw in ["zero division", "overflow", "nan", "inf", "math domain"]):
            return ErrorAnalysis(
                error_type="computation_error",
                error_message=error_str,
                root_cause="数学计算出现除零、溢出或无效值",
                suggested_fix="添加数值安全检查和默认值处理",
                confidence=0.85,
                context={"original_error": error_type},
            )

        # 指标缺失
        if any(kw in error_str.lower() for kw in ["missing", "not found", "key", "metric", "column"]):
            return ErrorAnalysis(
                error_type="metric_missing",
                error_message=error_str,
                root_cause="所需指标或数据列不存在",
                suggested_fix="使用可用的替代指标或生成默认值",
                confidence=0.8,
                context={"original_error": error_type},
            )

        # 权重/配置错误
        if any(kw in error_str.lower() for kw in ["weight", "config", "invalid", "range"]):
            return ErrorAnalysis(
                error_type="config_error",
                error_message=error_str,
                root_cause="TSR 权重或配置参数无效",
                suggested_fix="回退到默认权重配置",
                confidence=0.75,
                context={"original_error": error_type},
            )

        return ErrorAnalysis(
            error_type="unknown",
            error_message=error_str,
            root_cause="Unknown TSR ranking error",
            suggested_fix="Generic error handler",
            confidence=0.3,
            context={"error_type": error_type},
        )

    def generate_fix(self, analysis: ErrorAnalysis) -> Optional[FixCode]:
        timestamp = datetime.now().isoformat()

        if analysis.error_type == "computation_error":
            return FixCode(
                module_name=self.module_name,
                code=self._SAFE_COMPUTE_FIX,
                description="安全的数值计算，处理除零和NaN",
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence},
            )

        if analysis.error_type == "metric_missing":
            return FixCode(
                module_name=self.module_name,
                code=self._METRIC_FALLBACK_FIX,
                description="缺失指标时使用替代方案",
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence},
            )

        if analysis.error_type == "config_error":
            return FixCode(
                module_name=self.module_name,
                code=self._DEFAULT_CONFIG_FIX,
                description="回退到默认 TSR 权重配置",
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence},
            )

        self.logger.warning(f"No fix template for error type: {analysis.error_type}")
        return None

    _SAFE_COMPUTE_FIX = '''
def safe_divide(numerator, denominator, default=0.0):
    """Auto-generated fix: 安全除法，避免除零和NaN."""
    import numpy as np

    if denominator == 0 or (isinstance(denominator, float) and not np.isfinite(denominator)):
        return default

    result = numerator / denominator

    if not np.isfinite(result):
        return default

    return result


def safe_normalize(values):
    """Auto-generated fix: 安全归一化."""
    import numpy as np

    arr = np.array(values, dtype=float)

    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)

    total = arr.sum()
    if total == 0:
        return np.ones_like(arr) / len(arr)

    return arr / total
'''

    _METRIC_FALLBACK_FIX = '''
def compute_metric_with_fallback(data, metric_name, fallback_value=0.0):
    """Auto-generated fix: 带回退的指标计算."""
    import numpy as np
    import logging

    logger = logging.getLogger(__name__)

    if metric_name in data:
        val = data[metric_name]
        if val is not None and np.isfinite(val):
            return val

    logger.warning(f"Metric '{metric_name}' missing or invalid, using fallback={fallback_value}")
    return fallback_value
'''

    _DEFAULT_CONFIG_FIX = '''
DEFAULT_TSR_WEIGHTS = {
    "kl_divergence": 0.4,
    "cosine_dissimilarity": 0.3,
    "pearson_correlation": 0.3,
}

def validate_and_fix_weights(weights):
    """Auto-generated fix: 验证并修复权重配置."""
    import logging

    logger = logging.getLogger(__name__)

    if not weights or not isinstance(weights, dict):
        logger.warning("Invalid weights, using defaults")
        return DEFAULT_TSR_WEIGHTS.copy()

    total = sum(weights.values())
    if abs(total - 1.0) > 0.01:
        logger.warning(f"Weights sum to {total:.3f}, renormalizing")
        weights = {k: v / total for k, v in weights.items()}

    for key in DEFAULT_TSR_WEIGHTS:
        if key not in weights:
            logger.warning(f"Missing weight '{key}', using default")
            weights[key] = DEFAULT_TSR_WEIGHTS[key]

    return weights
'''
