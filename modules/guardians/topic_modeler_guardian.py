"""Guardian agent for the Topic Modeler module.

处理 LDA 收敛、维度不匹配、内存错误等。
"""

from core.agent_guardian import GuardianAgent, ErrorAnalysis, FixCode
from datetime import datetime
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class TopicModelerGuardianAgent(GuardianAgent):
    """Guardian specialized for the topic_modeler module."""

    def analyze_error(self, error: Exception, context: dict) -> ErrorAnalysis:
        error_str = str(error)
        error_type = type(error).__name__

        # 依赖缺失错误
        if any(kw in error_str for kw in ["No module named", "ImportError", "ModuleNotFoundError", "cannot import"]):
            # 提取缺失的模块名
            import re
            match = re.search(r"No module named '([^']+)'", error_str)
            missing_module = match.group(1) if match else "unknown"

            return ErrorAnalysis(
                error_type="dependency_missing",
                error_message=error_str,
                root_cause=f"缺失依赖库: {missing_module}",
                suggested_fix=f"安装缺失的依赖: pip install {missing_module}",
                confidence=0.95,
                context={"missing_module": missing_module, "original_error": error_type},
            )

        # LDA 收敛错误
        if any(kw in error_str.lower() for kw in ["convergence", "did not converge", "iteration"]):
            return ErrorAnalysis(
                error_type="lda_convergence",
                error_message=error_str,
                root_cause="LDA 模型未在指定迭代次数内收敛",
                suggested_fix="增加迭代次数或调整学习率",
                confidence=0.8,
                context={"original_error": error_type},
            )

        # 维度不匹配
        if any(kw in error_str.lower() for kw in ["shape", "dimension", "mismatch", "matrix"]):
            return ErrorAnalysis(
                error_type="dimension_mismatch",
                error_message=error_str,
                root_cause="DTM 矩阵维度与模型参数不匹配",
                suggested_fix="重新检查 DTM 和词汇表的一致性",
                confidence=0.85,
                context={"original_error": error_type},
            )

        # 内存错误
        if "MemoryError" in error_type or "memory" in error_str.lower():
            return ErrorAnalysis(
                error_type="memory",
                error_message=error_str,
                root_cause="主题建模所需内存超出可用量",
                suggested_fix="减少主题数或使用在线 LDA",
                confidence=0.9,
                context={"n_topics": context.get("n_topics", "unknown")},
            )

        # 稀疏矩阵错误
        if any(kw in error_str.lower() for kw in ["sparse", "csc", "csr"]):
            return ErrorAnalysis(
                error_type="sparse_format",
                error_message=error_str,
                root_cause="稀疏矩阵格式不正确",
                suggested_fix="转换为正确的稀疏格式",
                confidence=0.75,
                context={"original_error": error_type},
            )

        return ErrorAnalysis(
            error_type="unknown",
            error_message=error_str,
            root_cause="Unknown topic modeling error",
            suggested_fix="Generic error handler",
            confidence=0.3,
            context={"error_type": error_type},
        )

    def generate_fix(self, analysis: ErrorAnalysis) -> Optional[FixCode]:
        timestamp = datetime.now().isoformat()

        if analysis.error_type == "lda_convergence":
            return FixCode(
                module_name=self.module_name,
                code=self._LDA_CONVERGENCE_FIX,
                description="调整 LDA 超参数以改善收敛",
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence},
            )

        if analysis.error_type == "dimension_mismatch":
            return FixCode(
                module_name=self.module_name,
                code=self._DIMENSION_FIX,
                description="修复 DTM 维度不匹配",
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence},
            )

        self.logger.warning(f"No fix template for error type: {analysis.error_type}")
        return None

    _LDA_CONVERGENCE_FIX = '''
def train_lda_with_retry(dtm, n_topics, max_attempts=3):
    """Auto-generated fix: 带重试的 LDA 训练."""
    from gensim import corpora, models
    import logging

    logger = logging.getLogger(__name__)

    params_schedule = [
        {"iterations": 1000, "passes": 20, "alpha": "auto"},
        {"iterations": 2000, "passes": 30, "alpha": "symmetric"},
        {"iterations": 3000, "passes": 50, "alpha": "asymmetric"},
    ]

    for attempt, params in enumerate(params_schedule[:max_attempts]):
        try:
            logger.info(f"LDA attempt {attempt+1}: {params}")
            model = models.LdaModel(
                corpus=dtm,
                num_topics=n_topics,
                **params,
                random_state=42,
            )
            return model
        except Exception as e:
            logger.warning(f"LDA attempt {attempt+1} failed: {e}")
            if attempt == max_attempts - 1:
                raise
'''

    _DIMENSION_FIX = '''
def validate_and_fix_dtm(dtm, vocabulary):
    """Auto-generated fix: 验证并修复 DTM 维度."""
    import numpy as np
    import logging

    logger = logging.getLogger(__name__)

    if hasattr(dtm, 'shape'):
        n_docs, n_terms = dtm.shape
    else:
        dtm = np.array(dtm)
        n_docs, n_terms = dtm.shape

    vocab_size = len(vocabulary) if hasattr(vocabulary, '__len__') else vocabulary

    if n_terms != vocab_size:
        logger.warning(f"DTM has {n_terms} terms but vocabulary has {vocab_size}")

        if n_terms < vocab_size:
            padding = np.zeros((n_docs, vocab_size - n_terms))
            dtm = np.hstack([dtm, padding])
        else:
            dtm = dtm[:, :vocab_size]

    return dtm.astype(np.int64)
'''
