"""Guardian agent for the Preprocessor module.

基于 YAML 配置驱动的错误模式匹配和修复生成。
配置文件: .agents/configs/preprocessor_guardian.yaml
"""

from core.agent_guardian import GuardianAgent, ErrorAnalysis, FixCode
from core.agent_spec import get_agent_config_for_module, AgentSpec, ErrorPattern
from datetime import datetime
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class PreprocessorGuardianAgent(GuardianAgent):
    """Guardian specialized for the preprocessor module.

    支持两种模式:
    1. YAML 配置驱动: 从 .agents/configs/preprocessor_guardian.yaml 加载错误模式
    2. 硬编码回退: 当 YAML 配置不可用时使用内置模式
    """

    def __init__(self, module_name: str = "preprocessor"):
        super().__init__(module_name)
        self._spec: Optional[AgentSpec] = None
        self._load_spec()

    def _load_spec(self):
        """尝试从 YAML 加载智能体配置."""
        try:
            spec = get_agent_config_for_module(self.module_name)
            if spec:
                self._spec = spec
                self.logger.info(
                    f"Loaded config-driven spec with {len(spec.error_patterns)} error patterns"
                )
            else:
                self.logger.info("No YAML config found, using built-in patterns")
        except Exception as e:
            self.logger.warning(f"Failed to load YAML config: {e}, using built-in patterns")

    # ------------------------------------------------------------------
    #  Error analysis
    # ------------------------------------------------------------------

    def analyze_error(self, error: Exception, context: dict) -> ErrorAnalysis:
        """分析预处理执行错误.

        优先使用 YAML 配置的错误模式，回退到硬编码逻辑。
        """
        error_str = str(error)
        error_type = type(error).__name__

        # 尝试 YAML 配置驱动匹配
        if self._spec and self._spec.error_patterns:
            match = self._match_error_pattern(error_type, error_str, context)
            if match:
                pattern, confidence = match
                return ErrorAnalysis(
                    error_type=pattern.name,
                    error_message=error_str,
                    root_cause=self._infer_root_cause(pattern.name, error_str, context),
                    suggested_fix=self._infer_suggested_fix(pattern.name, context),
                    confidence=confidence,
                    context={"original_error": error_type, "matched_pattern": pattern.name},
                )

        # 回退: 硬编码模式匹配
        return self._analyze_error_builtin(error, context)

    def _match_error_pattern(
        self, error_type: str, error_str: str, context: dict
    ) -> Optional[tuple[ErrorPattern, float]]:
        """根据 YAML 定义的错误模式匹配当前错误."""
        error_lower = error_str.lower()

        for pattern in self._spec.error_patterns:
            # 异常类型匹配
            type_match = error_type in pattern.exceptions

            # 关键词匹配
            keyword_hits = sum(1 for kw in pattern.keywords if kw in error_lower)
            keyword_ratio = keyword_hits / max(len(pattern.keywords), 1)

            if type_match and keyword_hits > 0:
                confidence = min(pattern.confidence + 0.05, 1.0)
                return pattern, confidence
            elif type_match:
                return pattern, pattern.confidence * 0.9
            elif keyword_ratio >= 0.5:
                return pattern, pattern.confidence * 0.7

        return None

    def _analyze_error_builtin(self, error: Exception, context: dict) -> ErrorAnalysis:
        """内置的错误分析逻辑（YAML 配置不可用时的回退）."""
        error_str = str(error)
        error_type = type(error).__name__

        if "UnicodeDecodeError" in error_type or "codec" in error_str.lower():
            return ErrorAnalysis(
                error_type="encoding",
                error_message=error_str,
                root_cause="File encoding is not UTF-8 or supported format",
                suggested_fix="Add automatic encoding detection with fallback handling",
                confidence=0.9,
                context={"original_error": error_type},
            )

        if "MemoryError" in error_type:
            return ErrorAnalysis(
                error_type="memory",
                error_message=error_str,
                root_cause="Document corpus too large to fit in memory",
                suggested_fix="Implement chunked processing or reduce vocabulary size",
                confidence=0.85,
                context={"estimated_docs": context.get("n_docs", "unknown")},
            )

        if (
            "OSError" in error_type
            or "ModuleNotFoundError" in error_type
            or ("spacy" in error_str.lower() and "not found" in error_str.lower())
        ):
            return ErrorAnalysis(
                error_type="spacy_model",
                error_message=error_str,
                root_cause="Required spaCy language model not installed",
                suggested_fix="Download the spaCy model automatically",
                confidence=0.95,
                context={"model": self._extract_spacy_model(error_str)},
            )

        if "ValueError" in error_type and (
            "dtm" in error_str.lower() or "vocab" in error_str.lower()
        ):
            return ErrorAnalysis(
                error_type="dtm_vocabulary",
                error_message=error_str,
                root_cause="Invalid document-term matrix or vocabulary structure",
                suggested_fix="Regenerate DTM with different preprocessing parameters",
                confidence=0.75,
                context={"vocab_size": context.get("vocab_size", "unknown")},
            )

        return ErrorAnalysis(
            error_type="unknown",
            error_message=error_str,
            root_cause="Unknown preprocessing error",
            suggested_fix="Generic error handler with detailed logging",
            confidence=0.3,
            context={"error_type": error_type},
        )

    # ------------------------------------------------------------------
    #  Fix generation
    # ------------------------------------------------------------------

    def generate_fix(self, analysis: ErrorAnalysis) -> Optional[FixCode]:
        """生成修复代码.

        优先使用 YAML 配置的修复模板路径加载模板，
        回退到硬编码模板字符串。
        """
        timestamp = datetime.now().isoformat()

        # 尝试从 YAML 配置的模板路径加载
        template_code = self._load_fix_template(analysis.error_type)
        if template_code:
            return FixCode(
                module_name=self.module_name,
                code=template_code,
                description=self._get_fix_description(analysis.error_type),
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence, "source": "template"},
            )

        # 回退: 内置模板
        return self._generate_fix_builtin(analysis, timestamp)

    def _load_fix_template(self, error_type: str) -> Optional[str]:
        """从 YAML 配置的模板路径加载修复模板文件."""
        if not self._spec:
            return None

        template_info = self._spec.fix_templates.get(error_type)
        if not template_info or not template_info.file:
            return None

        try:
            from pathlib import Path

            template_path = Path(template_info.file)
            if not template_path.is_absolute():
                template_path = Path(__file__).resolve().parent.parent.parent / template_info.file

            if template_path.exists():
                code = template_path.read_text(encoding="utf-8")
                self.logger.info(f"Loaded fix template from: {template_path}")
                return code
            else:
                self.logger.warning(f"Template file not found: {template_path}")
        except Exception as e:
            self.logger.warning(f"Failed to load template: {e}")

        return None

    def _get_fix_description(self, error_type: str) -> str:
        """获取修复描述."""
        descriptions = {
            "encoding": "Auto-detect and handle file encoding errors",
            "memory": "Process documents in chunks or reduce vocabulary to avoid memory errors",
            "spacy_model": f"Automatically download missing spaCy model: en_core_web_sm",
        }
        return descriptions.get(error_type, f"Fix for {error_type}")

    def _generate_fix_builtin(self, analysis: ErrorAnalysis, timestamp: str) -> Optional[FixCode]:
        """内置修复代码生成（回退）."""
        if analysis.error_type == "encoding":
            return FixCode(
                module_name=self.module_name,
                code=self._ENCODING_FIX_TEMPLATE,
                description="Auto-detect and handle file encoding errors",
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence, "source": "builtin"},
            )

        if analysis.error_type == "memory":
            return FixCode(
                module_name=self.module_name,
                code=self._MEMORY_FIX_TEMPLATE,
                description="Process documents in chunks or reduce vocabulary to avoid memory errors",
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence, "source": "builtin"},
            )

        if analysis.error_type == "spacy_model":
            model_name = analysis.context.get("model", "en_core_web_sm")
            return FixCode(
                module_name=self.module_name,
                code=self._SPACY_FIX_TEMPLATE.format(model_name=model_name),
                description=f"Automatically download missing spaCy model: {model_name}",
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence, "source": "builtin", "model": model_name},
            )

        self.logger.warning(f"No fix template for error type: {analysis.error_type}")
        return None

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    def _infer_root_cause(self, error_type: str, error_str: str, context: dict) -> str:
        """根据错误类型推断根本原因."""
        causes = {
            "encoding": "File encoding is not UTF-8 or supported format",
            "memory": "Document corpus too large to fit in memory",
            "spacy_model": "Required spaCy language model not installed",
            "dtm_vocabulary": "Invalid document-term matrix or vocabulary structure",
        }
        return causes.get(error_type, f"Unknown error: {error_str[:100]}")

    def _infer_suggested_fix(self, error_type: str, context: dict) -> str:
        """根据错误类型建议修复方案."""
        fixes = {
            "encoding": "Add automatic encoding detection with fallback handling",
            "memory": "Implement chunked processing or reduce vocabulary size",
            "spacy_model": "Download the spaCy model automatically",
            "dtm_vocabulary": "Regenerate DTM with different preprocessing parameters",
        }
        return fixes.get(error_type, "Generic error handler with detailed logging")

    @staticmethod
    def _extract_spacy_model(error_str: str) -> str:
        """从错误消息中提取 spaCy 模型名称."""
        import re

        match = re.search(r"['\"]([a-z]{2}_core_web_[a-z]+)['\"]", error_str)
        return match.group(1) if match else "en_core_web_sm"

    # ------------------------------------------------------------------
    #  Inline templates (fallback when template files are absent)
    # ------------------------------------------------------------------

    _ENCODING_FIX_TEMPLATE = '''
def load_documents_with_encoding_fix(file_path, fallback_encoding='utf-8'):
    """Auto-generated fix: Detect and handle various file encodings.

    Tries multiple strategies:
    1. Detect encoding with chardet
    2. Try common encodings (utf-8, latin1, cp1252)
    3. Fallback to error-tolerant decoding
    """
    import chardet
    from pathlib import Path

    path = Path(file_path)
    with open(path, 'rb') as f:
        raw_data = f.read()

    # Strategy 1: chardet detection
    try:
        detected = chardet.detect(raw_data)
        encoding = detected.get('encoding', fallback_encoding)
        if encoding:
            return raw_data.decode(encoding)
    except Exception:
        pass

    # Strategy 2: Common encodings
    for enc in ['utf-8', 'latin1', 'iso-8859-1', 'cp1252', 'gbk', 'gb2312']:
        try:
            return raw_data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue

    # Strategy 3: Fallback
    return raw_data.decode(fallback_encoding, errors='ignore')
'''

    _MEMORY_FIX_TEMPLATE = '''
def preprocess_with_chunking(documents, chunk_size=1000):
    """Auto-generated fix: Process documents in chunks to reduce memory usage."""
    import gc

    all_processed = {}
    doc_items = list(documents.items())
    total_chunks = (len(doc_items) + chunk_size - 1) // chunk_size

    for chunk_idx in range(total_chunks):
        start_idx = chunk_idx * chunk_size
        end_idx = min((chunk_idx + 1) * chunk_size, len(doc_items))
        chunk = dict(doc_items[start_idx:end_idx])

        try:
            processed_chunk = {}
            all_processed.update(processed_chunk)
            del chunk, processed_chunk
            gc.collect()
        except MemoryError:
            smaller = chunk_size // 2
            if smaller > 10:
                return preprocess_with_chunking(documents, smaller)
            raise RuntimeError("Cannot process even with minimal chunk size")

    return all_processed
'''

    _SPACY_FIX_TEMPLATE = '''
def ensure_spacy_model_installed(model_name="{model_name}"):
    """Auto-generated fix: Download spaCy model if missing."""
    import subprocess
    import sys
    import spacy
    import logging

    logger = logging.getLogger(__name__)

    try:
        return spacy.load(model_name)
    except OSError:
        logger.warning(f"spaCy model '{{model_name}}' not found. Downloading...")
        try:
            subprocess.check_call([
                sys.executable, "-m", "spacy", "download", model_name
            ])
            return spacy.load(model_name)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"Could not download spaCy model '{{model_name}}'. "
                f"Install manually: python -m spacy download {{model_name}}"
            )
'''
