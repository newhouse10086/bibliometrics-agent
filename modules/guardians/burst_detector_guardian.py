"""Guardian agent for the Burst Detector module."""

from core.agent_guardian import GuardianAgent, ErrorAnalysis, FixCode
from datetime import datetime
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class BurstDetectorGuardianAgent(GuardianAgent):
    """Guardian specialized for the burst_detector module."""

    def analyze_error(self, error: Exception, context: dict) -> ErrorAnalysis:
        error_str = str(error)
        error_type = type(error).__name__

        if any(kw in error_str.lower() for kw in ["parameter", "invalid", "range", "value"]):
            return ErrorAnalysis(
                error_type="parameter_error", error_message=error_str,
                root_cause="参数范围无效", suggested_fix="修正为有效参数范围",
                confidence=0.85, context={"original_error": error_type},
            )

        if any(kw in error_str.lower() for kw in ["format", "type", "shape", "dimension"]):
            return ErrorAnalysis(
                error_type="data_format", error_message=error_str,
                root_cause="输入数据格式不匹配", suggested_fix="自动转换数据格式",
                confidence=0.8, context={"original_error": error_type},
            )

        if any(kw in error_str.lower() for kw in ["algorithm", "convergence", "iteration", "singular"]):
            return ErrorAnalysis(
                error_type="algorithm_error", error_message=error_str,
                root_cause="突发检测算法执行失败", suggested_fix="使用简化的回退算法",
                confidence=0.7, context={"original_error": error_type},
            )

        return ErrorAnalysis(
            error_type="unknown", error_message=error_str,
            root_cause="Unknown burst detection error", suggested_fix="Generic error handler",
            confidence=0.3, context={"error_type": error_type},
        )

    def generate_fix(self, analysis: ErrorAnalysis) -> Optional[FixCode]:
        timestamp = datetime.now().isoformat()

        if analysis.error_type == "parameter_error":
            return FixCode(
                module_name=self.module_name, code=self._PARAM_FIX,
                description="参数范围修正", timestamp=timestamp,
                error_type=analysis.error_type, metadata={"confidence": analysis.confidence},
            )
        if analysis.error_type == "data_format":
            return FixCode(
                module_name=self.module_name, code=self._FORMAT_FIX,
                description="数据格式转换", timestamp=timestamp,
                error_type=analysis.error_type, metadata={"confidence": analysis.confidence},
            )
        if analysis.error_type == "algorithm_error":
            return FixCode(
                module_name=self.module_name, code=self._FALLBACK_ALGO,
                description="回退到简化突发检测算法", timestamp=timestamp,
                error_type=analysis.error_type, metadata={"confidence": analysis.confidence},
            )
        return None

    _PARAM_FIX = '''
def validate_burst_params(params):
    """Auto-generated fix: 验证并修正突发检测参数."""
    import logging
    logger = logging.getLogger(__name__)

    defaults = {"s": 2.0, "gamma": 1.0, "k": 5}
    validated = {}

    for key, default_val in defaults.items():
        val = params.get(key, default_val)
        if not isinstance(val, (int, float)) or val <= 0:
            logger.warning(f"Invalid {key}={val}, using default {default_val}")
            val = default_val
        validated[key] = val

    return validated
'''

    _FORMAT_FIX = '''
def prepare_keyword_matrix(data):
    """Auto-generated fix: 准备关键词-年份矩阵."""
    import numpy as np
    import pandas as pd
    import logging
    logger = logging.getLogger(__name__)

    if isinstance(data, pd.DataFrame):
        return data.select_dtypes(include=[np.number])

    arr = np.array(data, dtype=float)
    arr = np.nan_to_num(arr, nan=0.0)
    return arr
'''

    _FALLBACK_ALGO = '''
def simple_burst_detection(time_series, threshold_factor=2.0):
    """Auto-generated fix: 简化的突发检测（回退算法）."""
    import numpy as np

    series = np.array(time_series, dtype=float)
    mean = np.mean(series)
    std = np.std(series)

    if std == 0:
        return np.zeros(len(series), dtype=int)

    threshold = mean + threshold_factor * std
    bursts = (series > threshold).astype(int)

    return bursts
'''
