"""Query Generator module — 自动生成学术检索式.

根据用户输入的研究领域,使用LLM生成优化的检索策略(适用于Semantic Scholar, PubMed等)。
"""

from __future__ import annotations

import logging
from typing import Any

from llm.openai_completion import OpenAICompletion
from modules.base import BaseModule, HardwareSpec, RunContext

logger = logging.getLogger(__name__)


class QueryGenerator(BaseModule):
    """Generate optimized search queries for bibliometric analysis."""

    @property
    def name(self) -> str:
        return "query_generator"

    @property
    def version(self) -> str:
        return "0.1.0"

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "required": ["research_domain"],
            "properties": {
                "research_domain": {
                    "type": "string",
                    "description": "Research domain or topic (e.g., 'machine learning in healthcare', 'bibliometric analysis')"
                },
                "additional_keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional additional keywords to include"
                },
                "exclude_keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keywords to exclude from search"
                },
                "year_range": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "integer"},
                        "end": {"type": "integer"}
                    },
                    "description": "Publication year range"
                }
            }
        }

    def output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "semantic_scholar_query": {"type": "string"},
                "pubmed_query": {"type": "string"},
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "estimated_papers": {"type": "integer"},
                "search_strategy": {"type": "string"}
            }
        }

    def config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "llm_model": {"type": "string", "default": "gpt-4o-mini"},
                "max_keywords": {"type": "integer", "default": 20},
                "include_meSH_terms": {"type": "boolean", "default": True}
            }
        }

    def get_hardware_requirements(self, config: dict) -> HardwareSpec:
        return HardwareSpec(
            min_memory_gb=0.5,
            recommended_memory_gb=1.0,
            cpu_cores=1,
            estimated_runtime_seconds=10
        )

    def process(self, input_data: dict, config: dict, context: RunContext) -> dict:
        """Generate search queries using LLM."""
        research_domain = input_data["research_domain"]
        additional_keywords = input_data.get("additional_keywords", [])
        exclude_keywords = input_data.get("exclude_keywords", [])
        year_range = input_data.get("year_range", {})

        logger.info("Generating search queries for domain: %s", research_domain)

        # Use LLM to generate query
        try:
            client = OpenAICompletion(
                model=config.get("llm_model", "gpt-4o-mini"),
                temperature=0.3
            )

            prompt = self._build_prompt(
                research_domain,
                additional_keywords,
                exclude_keywords,
                year_range
            )

            response = client.completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1000
            )

            llm_response = response["choices"][0]["message"]["content"]

            # Parse LLM response
            queries = self._parse_llm_response(llm_response)

            logger.info("Generated queries: %s", queries)

            return queries

        except Exception as e:
            logger.error("LLM query generation failed: %s", e)
            # Fallback: simple keyword-based query
            return self._generate_fallback_query(research_domain, additional_keywords)

    def _build_prompt(
        self,
        research_domain: str,
        additional_keywords: list[str],
        exclude_keywords: list[str],
        year_range: dict
    ) -> str:
        """Build prompt for LLM to generate search queries."""

        prompt = f"""Generate optimized search queries for academic literature search.

Research Domain: {research_domain}
"""

        if additional_keywords:
            prompt += f"\nAdditional Keywords to Include: {', '.join(additional_keywords)}"

        if exclude_keywords:
            prompt += f"\nKeywords to Exclude: {', '.join(exclude_keywords)}"

        if year_range:
            start = year_range.get("start", "any")
            end = year_range.get("end", "present")
            prompt += f"\nYear Range: {start} to {end}"

        prompt += """

Please provide:
1. Semantic Scholar query syntax (using AND, OR, NOT operators)
2. PubMed query syntax (with MeSH terms if applicable)
3. List of key search terms/concepts
4. Estimated number of papers this query might return
5. Brief search strategy explanation

Format your response as:
```
SEMANTIC_SCHOLAR: [query here]
PUBMED: [query here]
KEYWORDS: [comma-separated list]
ESTIMATED_PAPERS: [number]
STRATEGY: [explanation]
```

Make the query comprehensive but focused. Include synonyms and related terms."""

        return prompt

    def _parse_llm_response(self, response: str) -> dict:
        """Parse LLM response to extract queries."""
        result = {
            "semantic_scholar_query": "",
            "pubmed_query": "",
            "keywords": [],
            "estimated_papers": 1000,
            "search_strategy": response
        }

        # Simple parsing
        lines = response.split('\n')
        for line in lines:
            line = line.strip()
            if line.startswith("SEMANTIC_SCHOLAR:"):
                result["semantic_scholar_query"] = line.split(":", 1)[1].strip()
            elif line.startswith("PUBMED:"):
                result["pubmed_query"] = line.split(":", 1)[1].strip()
            elif line.startswith("KEYWORDS:"):
                keywords_str = line.split(":", 1)[1].strip()
                result["keywords"] = [k.strip() for k in keywords_str.split(",")]
            elif line.startswith("ESTIMATED_PAPERS:"):
                try:
                    result["estimated_papers"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass

        # Fallback if parsing failed
        if not result["semantic_scholar_query"]:
            result["semantic_scholar_query"] = response

        return result

    def _generate_fallback_query(
        self,
        research_domain: str,
        additional_keywords: list[str]
    ) -> dict:
        """Generate simple query without LLM."""
        keywords = [research_domain] + additional_keywords

        semantic_query = " AND ".join(f'"{kw}"' for kw in keywords)
        pubmed_query = " AND ".join(keywords)

        return {
            "semantic_scholar_query": semantic_query,
            "pubmed_query": pubmed_query,
            "keywords": keywords,
            "estimated_papers": 1000,
            "search_strategy": f"Simple keyword search for: {', '.join(keywords)}"
        }
