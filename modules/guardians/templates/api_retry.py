"""API 调用重试模板.

指数退避重试策略，处理 API 限流和临时错误。
"""


def fetch_with_retry(
    api_func,
    max_retries=5,
    base_delay=1.0,
    max_delay=60.0,
    backoff_factor=2.0,
    retryable_status_codes=(429, 500, 502, 503, 504),
):
    """带指数退避的 API 重试.

    Args:
        api_func: 无参数的可调用对象，执行 API 请求
        max_retries: 最大重试次数
        base_delay: 基础延迟（秒）
        max_delay: 最大延迟（秒）
        backoff_factor: 退避因子
        retryable_status_codes: 可重试的 HTTP 状态码

    Returns:
        API 响应

    Raises:
        最后一次失败的异常
    """
    import time
    import logging

    logger = logging.getLogger(__name__)

    for attempt in range(max_retries + 1):
        try:
            return api_func()
        except Exception as e:
            # 检查是否可重试
            status_code = getattr(e, "status_code", None) or getattr(
                getattr(e, "response", None), "status_code", None
            )

            is_retryable = (
                status_code in retryable_status_codes
                or "timeout" in str(e).lower()
                or "connection" in str(e).lower()
            )

            if not is_retryable or attempt == max_retries:
                logger.error(f"API call failed (attempt {attempt + 1}): {e}")
                raise

            delay = min(base_delay * (backoff_factor ** attempt), max_delay)
            logger.warning(
                f"API call failed (attempt {attempt + 1}/{max_retries}), "
                f"retrying in {delay:.1f}s: {e}"
            )
            time.sleep(delay)


def fetch_papers_with_pagination(
    fetch_func,
    query,
    max_pages=100,
    page_size=50,
    **kwargs,
):
    """分页获取论文数据.

    Args:
        fetch_func: 单页获取函数 (query, start, rows) -> list
        query: 搜索查询
        max_pages: 最大页数
        page_size: 每页数量

    Returns:
        全部论文列表
    """
    all_papers = []

    for page in range(max_pages):
        start = page * page_size

        def _fetch():
            return fetch_func(query, start, page_size, **kwargs)

        results = fetch_with_retry(_fetch)

        if not results:
            break

        all_papers.extend(results)

        if len(results) < page_size:
            break

    return all_papers
