"""Guardian agent for the Paper Fetcher module.

处理 API 限流、网络超时、数据格式错误等。
"""

from core.agent_guardian import GuardianAgent, ErrorAnalysis, FixCode
from core.agent_spec import get_agent_config_for_module, AgentSpec
from datetime import datetime
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class PaperFetcherGuardianAgent(GuardianAgent):
    """Guardian specialized for the paper_fetcher module."""

    def __init__(self, module_name: str = "paper_fetcher"):
        super().__init__(module_name)
        self._spec: Optional[AgentSpec] = None
        self._load_spec()

    def _load_spec(self):
        try:
            spec = get_agent_config_for_module(self.module_name)
            if spec:
                self._spec = spec
                self.logger.info(f"Loaded spec with {len(spec.error_patterns)} error patterns")
        except Exception as e:
            self.logger.warning(f"Failed to load YAML config: {e}")

    def analyze_error(self, error: Exception, context: dict) -> ErrorAnalysis:
        error_str = str(error)
        error_type = type(error).__name__

        # API 限流
        if any(kw in error_str.lower() for kw in ["429", "rate limit", "too many requests", "quota"]):
            return ErrorAnalysis(
                error_type="api_rate_limit",
                error_message=error_str,
                root_cause="API 调用频率超过限制",
                suggested_fix="添加指数退避重试逻辑",
                confidence=0.9,
                context={"original_error": error_type},
            )

        # 网络超时
        if any(kw in error_str.lower() for kw in ["timeout", "connection", "refused", "unreachable"]):
            return ErrorAnalysis(
                error_type="network_timeout",
                error_message=error_str,
                root_cause="网络连接超时或服务器不可达",
                suggested_fix="添加网络重试和超时配置",
                confidence=0.85,
                context={"original_error": error_type},
            )

        # JSON 解析错误
        if any(kw in error_str.lower() for kw in ["json", "decode", "parse", "unexpected"]):
            return ErrorAnalysis(
                error_type="invalid_response",
                error_message=error_str,
                root_cause="API 返回了非预期格式的响应",
                suggested_fix="添加响应格式验证和容错解析",
                confidence=0.75,
                context={"original_error": error_type},
            )

        # API Key 无效
        if any(kw in error_str.lower() for kw in ["api key", "unauthorized", "authentication", "401", "403"]):
            return ErrorAnalysis(
                error_type="api_auth",
                error_message=error_str,
                root_cause="API Key 无效或已过期",
                suggested_fix="请用户更新 API Key",
                confidence=0.95,
                context={"original_error": error_type},
            )

        return ErrorAnalysis(
            error_type="unknown",
            error_message=error_str,
            root_cause="Unknown paper fetcher error",
            suggested_fix="Generic error handler",
            confidence=0.3,
            context={"error_type": error_type},
        )

    def generate_fix(self, analysis: ErrorAnalysis) -> Optional[FixCode]:
        timestamp = datetime.now().isoformat()

        if analysis.error_type == "api_rate_limit":
            return FixCode(
                module_name=self.module_name,
                code=self._API_RETRY_TEMPLATE,
                description="指数退避重试 API 调用",
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence},
            )

        if analysis.error_type == "network_timeout":
            return FixCode(
                module_name=self.module_name,
                code=self._NETWORK_RETRY_TEMPLATE,
                description="网络请求重试和超时处理",
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence},
            )

        if analysis.error_type == "invalid_response":
            return FixCode(
                module_name=self.module_name,
                code=self._RESPONSE_PARSER_TEMPLATE,
                description="API 响应格式容错解析",
                timestamp=timestamp,
                error_type=analysis.error_type,
                metadata={"confidence": analysis.confidence},
            )

        self.logger.warning(f"No fix template for error type: {analysis.error_type}")
        return None

    _API_RETRY_TEMPLATE = '''
def fetch_with_retry(api_func, max_retries=5, base_delay=1.0, backoff_factor=2.0):
    """Auto-generated fix: 指数退避重试 API 调用."""
    import time
    import logging

    logger = logging.getLogger(__name__)

    for attempt in range(max_retries):
        try:
            return api_func()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = min(base_delay * (backoff_factor ** attempt), 60)
            logger.warning(f"API call failed (attempt {attempt+1}/{max_retries}), retrying in {delay:.1f}s: {e}")
            time.sleep(delay)
'''

    _NETWORK_RETRY_TEMPLATE = '''
def fetch_with_timeout(url, timeout=30, max_retries=3):
    """Auto-generated fix: 带超时和重试的网络请求."""
    import requests
    import time
    import logging

    logger = logging.getLogger(__name__)

    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.exceptions.Timeout:
            logger.warning(f"Request timeout (attempt {attempt+1}/{max_retries})")
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Connection error: {e}")
            if attempt == max_retries - 1:
                raise
            time.sleep(3)
'''

    _RESPONSE_PARSER_TEMPLATE = '''
def parse_api_response_safely(response_text):
    """Auto-generated fix: 安全解析 API 响应."""
    import json
    import logging

    logger = logging.getLogger(__name__)

    try:
        data = json.loads(response_text)
        return data
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error: {e}")
        # 尝试修复常见的 JSON 格式问题
        text = response_text.strip()
        if text.startswith("(") and text.endswith(")"):
            text = text[1:-1]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.error("Could not parse response as JSON")
            return None
'''
