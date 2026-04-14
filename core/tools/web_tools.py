"""Web 工具: WebSearch, WebFetch, HttpRequest.

提供网络搜索、网页抓取、HTTP 请求能力，供 Guardian Agent 查询文档和搜索解决方案。
所有工具仅依赖 requests + re/beautifulsoup4，无需搜索 API key。
"""

from pathlib import Path
from typing import Optional
import json
import logging
import re
import urllib.parse

from core.tools import BaseTool, ToolResult

logger = logging.getLogger(__name__)

# 通用请求头，模拟浏览器
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5,zh-CN;q=0.3",
}


# ---------------------------------------------------------------------------
#  WebSearch — 搜索引擎查询（纯 requests 实现）
# ---------------------------------------------------------------------------

class WebSearch(BaseTool):
    """使用搜索引擎查询信息.

    优先级:
    1. Bing 网页抓取解析（免费，无需 API key）
    2. DuckDuckGo HTML 抓取（备选）
    3. 链接建议回退
    """

    name = "WebSearch"
    description = "搜索错误解决方案、文档、技术问题"

    def run(self, query: str, max_results: int = 5) -> ToolResult:
        self.logger.info(f"WebSearch: {query}")

        # 策略 1: Bing 网页抓取
        try:
            results = self._search_bing_html(query, max_results)
            if results:
                return ToolResult(
                    success=True,
                    output=self._format_results(results),
                    metadata={"backend": "bing_html", "count": len(results)},
                )
        except Exception as e:
            self.logger.warning(f"Bing HTML search failed: {e}")

        # 策略 2: DuckDuckGo HTML 抓取
        try:
            results = self._search_duckduckgo_html(query, max_results)
            if results:
                return ToolResult(
                    success=True,
                    output=self._format_results(results),
                    metadata={"backend": "ddg_html", "count": len(results)},
                )
        except Exception as e:
            self.logger.warning(f"DuckDuckGo HTML search failed: {e}")

        # 策略 3: duckduckgo-search 库（如果安装了）
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=max_results))
            if raw:
                results = [
                    {
                        "title": r.get("title", ""),
                        "href": r.get("href", ""),
                        "body": r.get("body", ""),
                    }
                    for r in raw
                ]
                return ToolResult(
                    success=True,
                    output=self._format_results(results),
                    metadata={"backend": "ddg_lib", "count": len(results)},
                )
        except ImportError:
            pass
        except Exception as e:
            self.logger.warning(f"DuckDuckGo lib search failed: {e}")

        # 策略 4: 链接建议回退
        return ToolResult(
            success=True,
            output=self._fallback_links(query),
            metadata={"backend": "link_suggestions"},
        )

    # ---- Bing 网页抓取 ----

    def _search_bing_html(self, query: str, max_results: int) -> list[dict]:
        """抓取 Bing 搜索结果页面并解析."""
        import requests

        url = "https://www.bing.com/search"
        params = {"q": query, "count": max_results}

        resp = requests.get(url, headers=_HEADERS, params=params, timeout=10)
        resp.raise_for_status()

        return self._parse_bing_html(resp.text, max_results)

    def _parse_bing_html(self, html: str, max_results: int) -> list[dict]:
        """解析 Bing 搜索结果 HTML."""
        results = []

        # 尝试用 BeautifulSoup 解析
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")

            for li in soup.select("li.b_algo"):
                title_tag = li.select_one("h2 a")
                if not title_tag:
                    continue
                title = title_tag.get_text(strip=True)
                href = title_tag.get("href", "")

                # 摘要
                snippet_tag = li.select_one(".b_caption p, .b_lineclamp2")
                body = snippet_tag.get_text(strip=True) if snippet_tag else ""

                if title and href:
                    results.append({"title": title, "href": href, "body": body})

                if len(results) >= max_results:
                    break

        except ImportError:
            # 无 BeautifulSoup 时用正则回退
            results = self._parse_bing_regex(html, max_results)

        return results

    def _parse_bing_regex(self, html: str, max_results: int) -> list[dict]:
        """用正则从 Bing HTML 提取结果（无 BeautifulSoup 时）."""
        results = []

        # 匹配 <li class="b_algo"> 块
        blocks = re.findall(r'<li class="b_algo">(.*?)</li>', html, re.DOTALL)
        for block in blocks[:max_results]:
            # 提取标题和链接
            m = re.search(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            if not m:
                continue
            href = m.group(1)
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if not title:
                continue

            # 提取摘要
            snippet_m = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
            body = re.sub(r"<[^>]+>", "", snippet_m.group(1)).strip() if snippet_m else ""

            results.append({"title": title, "href": href, "body": body})

        return results

    # ---- DuckDuckGo HTML 抓取 ----

    def _search_duckduckgo_html(self, query: str, max_results: int) -> list[dict]:
        """抓取 DuckDuckGo HTML 版搜索结果."""
        import requests

        url = "https://html.duckduckgo.com/html/"
        data = {"q": query, "kl": "wt-wt"}

        resp = requests.post(url, headers=_HEADERS, data=data, timeout=10)
        resp.raise_for_status()

        return self._parse_ddg_html(resp.text, max_results)

    def _parse_ddg_html(self, html: str, max_results: int) -> list[dict]:
        """解析 DuckDuckGo HTML 搜索结果."""
        results = []

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")

            for div in soup.select(".result"):
                title_tag = div.select_one(".result__a")
                snippet_tag = div.select_one(".result__snippet")

                if not title_tag:
                    continue

                title = title_tag.get_text(strip=True)
                href = title_tag.get("href", "")
                body = snippet_tag.get_text(strip=True) if snippet_tag else ""

                # DDG 的 href 可能是跳转链接，提取真实 URL
                if "//duckduckgo.com/l/" in href:
                    real = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])
                    href = real[0] if real else href

                if title:
                    results.append({"title": title, "href": href, "body": body})
                if len(results) >= max_results:
                    break

        except ImportError:
            # 正则回退
            blocks = re.findall(r'class="result[^"]*">(.*?)</div>\s*</div>', html, re.DOTALL)
            for block in blocks[:max_results]:
                m = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
                if not m:
                    continue
                href = m.group(1)
                title = re.sub(r"<[^>]+>", "", m.group(2)).strip()

                snip_m = re.search(r'class="result__snippet"[^>]*>(.*?)</[a-z]+>', block, re.DOTALL)
                body = re.sub(r"<[^>]+>", "", snip_m.group(1)).strip() if snip_m else ""

                if "//duckduckgo.com/l/" in href:
                    real = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])
                    href = real[0] if real else href

                if title:
                    results.append({"title": title, "href": href, "body": body})

        return results

    # ---- 通用 ----

    def _format_results(self, results: list[dict]) -> str:
        lines = []
        for i, r in enumerate(results, 1):
            lines.append(
                f"{i}. {r['title']}\n"
                f"   URL: {r['href']}\n"
                f"   {r['body']}\n"
            )
        return "\n".join(lines)

    def _fallback_links(self, query: str) -> str:
        q = urllib.parse.quote_plus(query)
        return (
            f"搜索后端不可用。请手动访问:\n"
            f"- Google: https://www.google.com/search?q={q}\n"
            f"- Bing:   https://www.bing.com/search?q={q}\n"
            f"- SO:     https://stackoverflow.com/search?q={q}\n"
        )


# ---------------------------------------------------------------------------
#  WebFetch — 抓取网页内容
# ---------------------------------------------------------------------------

class WebFetch(BaseTool):
    """抓取网页内容."""

    name = "WebFetch"
    description = "抓取网页内容，获取文档、API 文档、Stack Overflow 解决方案"

    def run(self, url: str, extract_text: bool = True, timeout: int = 15) -> ToolResult:
        self.logger.info(f"WebFetch: {url}")

        try:
            import requests
            import urllib3

            # 禁用 SSL 警告（开发环境，适用于 Windows 企业环境）
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

            resp = requests.get(url, headers=_HEADERS, timeout=timeout, verify=False)
            resp.raise_for_status()

            content_type = resp.headers.get("Content-Type", "")

            # JSON
            if "json" in content_type:
                try:
                    data = resp.json()
                    text = json.dumps(data, indent=2, ensure_ascii=False)
                except (json.JSONDecodeError, ValueError):
                    text = resp.text
            # HTML
            elif extract_text and "html" in content_type:
                text = self._extract_text(resp.text)
            else:
                text = resp.text

            # 截断
            if len(text) > 8000:
                text = text[:8000] + f"\n... [truncated, total {len(resp.text)} chars]"

            return ToolResult(
                success=True, output=text,
                metadata={"url": url, "size": len(resp.text), "content_type": content_type},
            )

        except ImportError:
            return ToolResult(success=False, output=None, error="requests not installed")
        except Exception as e:
            return ToolResult(success=False, output=None, error=str(e))

    def _extract_text(self, html: str) -> str:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            text = soup.get_text(separator="\n")
            lines = [line.strip() for line in text.splitlines()]
            return "\n".join(l for l in lines if l)
        except ImportError:
            text = re.sub(r"<[^>]+>", " ", html)
            return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
#  HttpRequest — 通用 HTTP 请求
# ---------------------------------------------------------------------------

class HttpRequest(BaseTool):
    """通用 HTTP 请求工具."""

    name = "HttpRequest"
    description = "发送 HTTP 请求（GET/POST/PUT/DELETE），用于调用 API"

    def run(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[dict] = None,
        body: Optional[str] = None,
        timeout: int = 15,
    ) -> ToolResult:
        try:
            import requests

            kwargs: dict = {"timeout": timeout, "headers": {**_HEADERS, **(headers or {})}}
            if body:
                kwargs["data"] = body

            resp = requests.request(method.upper(), url, **kwargs)

            try:
                json_resp = resp.json()
                output = json.dumps(json_resp, indent=2, ensure_ascii=False)[:8000]
            except (json.JSONDecodeError, ValueError):
                output = resp.text[:8000] if resp.text else "(empty response)"

            return ToolResult(
                success=200 <= resp.status_code < 300,
                output=output,
                error=f"HTTP {resp.status_code}" if resp.status_code >= 400 else None,
                metadata={
                    "url": url,
                    "method": method.upper(),
                    "status_code": resp.status_code,
                    "content_type": resp.headers.get("Content-Type", ""),
                },
            )

        except ImportError:
            return ToolResult(success=False, output=None, error="requests not installed")
        except Exception as e:
            return ToolResult(success=False, output=None, error=str(e))
