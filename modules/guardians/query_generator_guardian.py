"""Guardian agent for the Query Generator module."""

from core.agent_guardian import GuardianAgent, ErrorAnalysis, FixCode
from datetime import datetime
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class QueryGeneratorGuardianAgent(GuardianAgent):
    """Guardian specialized for the query_generator module."""

    def analyze_error(self, error: Exception, context: dict) -> ErrorAnalysis:
        error_str = str(error)
        error_type = type(error).__name__

        if any(kw in error_str.lower() for kw in ["api", "request", "http"]):
            return ErrorAnalysis(
                error_type="api_error", error_message=error_str,
                root_cause="API 调用失败", suggested_fix="重试 API 调用",
                confidence=0.8, context={"original_error": error_type},
            )

        if any(kw in error_str.lower() for kw in ["json", "parse", "decode"]):
            return ErrorAnalysis(
                error_type="json_parse", error_message=error_str,
                root_cause="JSON 解析错误", suggested_fix="添加 JSON 容错解析",
                confidence=0.85, context={"original_error": error_type},
            )

        if any(kw in error_str.lower() for kw in ["empty", "no result", "null"]):
            return ErrorAnalysis(
                error_type="empty_result", error_message=error_str,
                root_cause="查询生成了空结果", suggested_fix="使用回退查询策略",
                confidence=0.7, context={"original_error": error_type},
            )

        return ErrorAnalysis(
            error_type="unknown", error_message=error_str,
            root_cause="Unknown query generator error", suggested_fix="Generic error handler",
            confidence=0.3, context={"error_type": error_type},
        )

    def generate_fix(self, analysis: ErrorAnalysis) -> Optional[FixCode]:
        timestamp = datetime.now().isoformat()

        if analysis.error_type == "api_error":
            return FixCode(
                module_name=self.module_name, code=self._API_RETRY,
                description="API 调用重试", timestamp=timestamp,
                error_type=analysis.error_type, metadata={"confidence": analysis.confidence},
            )
        if analysis.error_type == "json_parse":
            return FixCode(
                module_name=self.module_name, code=self._JSON_FIX,
                description="JSON 容错解析", timestamp=timestamp,
                error_type=analysis.error_type, metadata={"confidence": analysis.confidence},
            )
        if analysis.error_type == "empty_result":
            return FixCode(
                module_name=self.module_name, code=self._FALLBACK_QUERY,
                description="回退查询策略", timestamp=timestamp,
                error_type=analysis.error_type, metadata={"confidence": analysis.confidence},
            )
        return None

    _API_RETRY = '''
def query_with_retry(api_func, max_retries=3):
    """Auto-generated fix: 带重试的查询生成."""
    import time
    import logging
    logger = logging.getLogger(__name__)
    for attempt in range(max_retries):
        try:
            return api_func()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = 2 ** attempt
            logger.warning(f"Query attempt {attempt+1} failed, retrying in {delay}s")
            time.sleep(delay)
'''

    _JSON_FIX = '''
def parse_json_safely(text):
    """Auto-generated fix: 安全解析 JSON."""
    import json
    import re
    import logging
    logger = logging.getLogger(__name__)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\\{.*\\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        logger.warning("Could not parse JSON, returning empty dict")
        return {}
'''

    _FALLBACK_QUERY = '''
def generate_fallback_query(domain):
    """Auto-generated fix: 生成回退查询."""
    templates = [
        f'("{domain}" OR "{domain} review") AND ("bibliometric" OR "scientometric")',
        f'title:("{domain}") AND ("literature review" OR "systematic review")',
        f'("{domain}") AND PUBYEAR > 2018',
    ]
    return templates
'''
