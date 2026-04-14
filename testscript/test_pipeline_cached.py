#!/usr/bin/env python3
"""Test pipeline using cached papers data.

Runs modules 3-10 (preprocessor to report_generator) using cached papers.
"""
import sys
from pathlib import Path

# Setup path
sys.path.insert(0, str(Path(__file__).parent))

from core.orchestrator import PipelineOrchestrator
from core.state_manager import StateManager
from modules.registry import ModuleRegistry
from modules.base import RunContext

def test_cached_pipeline():
    """Run pipeline with cached papers data."""

    # Initialize components
    registry = ModuleRegistry()
    registry.auto_discover()

    # Custom config to speed up topic modeling and burst detection
    custom_config = {
        "modules": {
            "topic_modeler": {
                "min_topics": 1,
                "max_topics": 10,  # Reduced from 30 for faster testing
                "step": 2,  # Evaluate every 2nd topic number
                "n_iter": 500,  # Reduced iterations for faster testing
            },
            "burst_detector": {
                "frequency_threshold_percentile": 0.9,  # Only top 10% keywords
            }
        }
    }

    state_manager = StateManager(checkpoint_dir=Path("checkpoints"))
    orchestrator = PipelineOrchestrator(
        registry=registry,
        state_manager=state_manager,
        config=custom_config  # Pass config here
    )

    # Use cached papers
    cached_papers = Path("checkpoints/d14847af/paper_fetcher/papers.csv")

    print(f"Testing pipeline with cached papers: {cached_papers}")
    print(f"Papers count: {sum(1 for _ in open(cached_papers, encoding='utf-8')) - 1}")  # -1 for header

    # Run pipeline starting from preprocessor
    run_id = "test_cached_run"

    try:
        output = orchestrator.run(
            run_id=run_id,
            initial_input={
                "papers_csv_path": str(cached_papers),
                # Add pre-generated queries (from cache)
                "queries": {
                    "semantic_scholar_query": "AI in Healthcare",
                    "keywords": ["AI", "Healthcare", "Artificial Intelligence"]
                }
            },
            start_from="preprocessor",  # Skip query_generator and paper_fetcher
            end_at="network_analyzer",  # Stop before visualizer/report_generator (not implemented yet)
            mode="batch"
        )

        print(f"\nPipeline completed successfully!")
        print(f"Run ID: {run_id}")
        print(f"Final output: {output}")

        # Check outputs
        checkpoint_dir = Path("checkpoints") / run_id
        print(f"\nGenerated files:")
        for module_dir in checkpoint_dir.iterdir():
            if module_dir.is_dir():
                files = list(module_dir.glob("*"))
                print(f"  {module_dir.name}: {len(files)} files")

    except Exception as e:
        print(f"\nPipeline failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True

if __name__ == "__main__":
    success = test_cached_pipeline()
    sys.exit(0 if success else 1)
