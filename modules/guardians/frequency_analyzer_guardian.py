"""Guardian agent for the Frequency Analyzer module."""

from core.agent_guardian import GuardianAgent, ErrorAnalysis, FixCode
from datetime import datetime
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class FrequencyAnalyzerGuardianAgent(GuardianAgent):
    """Guardian specialized for the frequency_analyzer module."""

    def analyze_error(self, error: Exception, context: dict) -> ErrorAnalysis:
        error_str = str(error)
        error_type = type(error).__name__

        if any(kw in error_str.lower() for kw in ["zero division", "overflow", "nan", "computation"]):
            return ErrorAnalysis(
                error_type="computation_error", error_message=error_str,
                root_cause="计算中出现除零或无效值", suggested_fix="添加数值安全检查",
                confidence=0.8, context={"original_error": error_type},
            )

        if "MemoryError" in error_type or "memory" in error_str.lower():
            return ErrorAnalysis(
                error_type="memory", error_message=error_str,
                root_cause="频率分析所需内存超出可用量", suggested_fix="分块计算频率",
                confidence=0.9, context={"original_error": error_type},
            )

        if any(kw in error_str.lower() for kw in ["format", "type", "dtype", "mismatch"]):
            return ErrorAnalysis(
                error_type="data_format", error_message=error_str,
                root_cause="输入数据类型不匹配", suggested_fix="自动类型转换",
                confidence=0.75, context={"original_error": error_type},
            )

        return ErrorAnalysis(
            error_type="unknown", error_message=error_str,
            root_cause="Unknown frequency analysis error", suggested_fix="Generic error handler",
            confidence=0.3, context={"error_type": error_type},
        )

    def generate_fix(self, analysis: ErrorAnalysis) -> Optional[FixCode]:
        timestamp = datetime.now().isoformat()

        if analysis.error_type == "computation_error":
            return FixCode(
                module_name=self.module_name, code=self._SAFE_COMPUTE,
                description="安全频率计算", timestamp=timestamp,
                error_type=analysis.error_type, metadata={"confidence": analysis.confidence},
            )
        if analysis.error_type == "memory":
            return FixCode(
                module_name=self.module_name, code=self._CHUNK_COMPUTE,
                description="分块频率计算", timestamp=timestamp,
                error_type=analysis.error_type, metadata={"confidence": analysis.confidence},
            )
        if analysis.error_type == "data_format":
            return FixCode(
                module_name=self.module_name, code=self._TYPE_FIX,
                description="自动类型转换", timestamp=timestamp,
                error_type=analysis.error_type, metadata={"confidence": analysis.confidence},
            )
        return None

    _SAFE_COMPUTE = '''
def safe_term_frequency(dtm):
    """Auto-generated fix: 安全的词频计算."""
    import numpy as np
    dtm = np.array(dtm, dtype=float)
    dtm = np.nan_to_num(dtm, nan=0.0, posinf=0.0, neginf=0.0)
    return np.sum(dtm, axis=0)


def safe_tf_idf(dtm):
    """Auto-generated fix: 安全的 TF-IDF 计算."""
    import numpy as np
    dtm = np.array(dtm, dtype=float)
    dtm = np.nan_to_num(dtm, nan=0.0)
    df = np.sum(dtm > 0, axis=0)
    n_docs = dtm.shape[0]
    idf = np.log(n_docs / (df + 1))
    tfidf = dtm * idf
    return np.nan_to_num(tfidf)
'''

    _CHUNK_COMPUTE = '''
def compute_frequency_chunked(dtm, chunk_size=5000):
    """Auto-generated fix: 分块计算频率."""
    import numpy as np
    import gc

    n_docs = dtm.shape[0]
    freq = np.zeros(dtm.shape[1])

    for start in range(0, n_docs, chunk_size):
        end = min(start + chunk_size, n_docs)
        chunk = dtm[start:end]
        freq += np.array(chunk.sum(axis=0)).flatten()
        del chunk
        gc.collect()

    return freq
'''

    _TYPE_FIX = '''
def ensure_numeric_dtm(dtm):
    """Auto-generated fix: 确保 DTM 是数值类型."""
    import numpy as np
    import scipy.sparse as sp

    if sp.issparse(dtm):
        dtm = dtm.astype(np.float64)
    else:
        dtm = np.array(dtm, dtype=np.float64)

    dtm = np.nan_to_num(dtm, nan=0.0)
    return dtm
'''
