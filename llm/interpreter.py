"""LLM Interpreter for explaining bibliometric analysis results.

This module provides LLM-powered interpretation of analysis outputs,
including topic explanations, trend analysis, and report generation.
"""

from __future__ import annotations

import logging
from typing import Any

from llm.openai_completion import OpenAICompletion

logger = logging.getLogger(__name__)


class LLMInterpreter:
    """LLM-based interpreter for bibliometric analysis results.

    Uses OpenAI completion API.
    """

    def __init__(self, model: str = "gpt-4o-mini", temperature: float = 0.3):
        self.model = model
        self.temperature = temperature
        self.client = OpenAICompletion(model=model, temperature=temperature)

    def interpret_topic_modeling(
        self,
        topic_word_dist: dict[int, list[tuple[str, float]]],
        topic_labels: list[str],
        n_topics: int,
    ) -> str:
        """Generate LLM interpretation of LDA topic modeling results.

        Args:
            topic_word_dist: Dict of {topic_id: [(word, weight), ...]}
            topic_labels: Generated topic labels
            n_topics: Number of topics

        Returns:
            Natural language interpretation of the topics
        """
        try:
            # Format topics for prompt
            topics_text = []
            for topic_id, words_weights in topic_word_dist.items():
                top_words = [w for w, _ in words_weights[:10]]
                label = topic_labels[topic_id] if topic_id < len(topic_labels) else f"Topic {topic_id}"
                topics_text.append(f"**{label}**: {', '.join(top_words)}")

            prompt = f"""Analyze the following LDA topic modeling results from a bibliometric study:

Number of topics: {n_topics}

Topics:
{chr(10).join(topics_text)}

Please provide:
1. A brief overview of the main themes in this research domain
2. Key insights about the topic distribution
3. Potential research trends or gaps
4. Suggestions for further analysis

Keep the interpretation concise and academic in tone."""

            response = self.client.completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1000,
            )

            interpretation = response["choices"][0]["message"]["content"]
            logger.info("Generated LLM interpretation for %d topics", n_topics)
            return interpretation

        except Exception as e:
            logger.error("LLM interpretation failed: %s", e)
            return f"LLM interpretation unavailable: {str(e)}"

    def interpret_burst_detection(
        self,
        burst_results: dict[str, list[dict[str, Any]]],
    ) -> str:
        """Interpret burst detection results to identify research trends.

        Args:
            burst_results: Dict of {keyword: [burst_periods]}

        Returns:
            Natural language interpretation of burst patterns
        """
        try:
            # Format burst keywords
            burst_text = []
            for keyword, bursts in list(burst_results.items())[:20]:  # Top 20
                periods = [f"{b['start']}-{b['end']}" for b in bursts if b.get("start")]
                if periods:
                    burst_text.append(f"- {keyword}: {', '.join(periods)}")

            prompt = f"""Analyze the following burst detection results from a bibliometric study:

Burst keywords (showing sudden increases in research interest):
{chr(10).join(burst_text)}

Please provide:
1. Major research trends identified by burst patterns
2. Temporal evolution of the research field
3. Emerging vs. declining topics
4. Potential future directions

Keep the interpretation concise and academic in tone."""

            response = self.client.completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=800,
            )

            return response["choices"][0]["message"]["content"]

        except Exception as e:
            logger.error("Burst interpretation failed: %s", e)
            return f"LLM interpretation unavailable: {str(e)}"

    def generate_summary_report(
        self,
        analysis_results: dict[str, Any],
    ) -> str:
        """Generate a comprehensive summary report of all analyses.

        Args:
            analysis_results: Dict containing all module outputs

        Returns:
            Markdown-formatted report
        """
        report_parts = [
            "# Bibliometric Analysis Report\n",
            "## Overview\n",
            f"This report summarizes the bibliometric analysis results.\n",
        ]

        # Add topic modeling results if available
        if "topic_modeler" in analysis_results:
            tm_stats = analysis_results["topic_modeler"].get("stats", {})
            report_parts.append(
                f"\n## Topic Modeling\n\n"
                f"Number of topics: {tm_stats.get('n_topics', 'N/A')}\n"
                f"Coherence score: {tm_stats.get('coherence', 'N/A')}\n"
            )

        # Add LLM interpretation if available
        if "llm_interpretation" in analysis_results:
            report_parts.append(
                f"\n## Interpretation\n\n{analysis_results['llm_interpretation']}\n"
            )

        return "".join(report_parts)


def create_interpreter(config: dict[str, Any]) -> LLMInterpreter:
    """Factory function to create LLM interpreter from config."""
    llm_config = config.get("llm", {})
    return LLMInterpreter(
        model=llm_config.get("model", "gpt-4o-mini"),
        temperature=llm_config.get("temperature", 0.3),
    )
