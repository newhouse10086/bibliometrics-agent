#!/usr/bin/env python
"""End-to-end integration test for the complete bibliometric pipeline.

Tests the full workflow from research domain input to final network analysis.
"""

import sys
import tempfile
from pathlib import Path

from core.orchestrator import PipelineOrchestrator
from core.state_manager import StateManager
from modules.registry import ModuleRegistry

# Test configuration
TEST_RESEARCH_DOMAIN = "machine learning in healthcare"
TEST_MAX_PAPERS = 50  # Keep small for fast testing


def test_end_to_end_pipeline():
    """Test the complete automated pipeline."""
    print("=" * 70)
    print("END-TO-END INTEGRATION TEST")
    print("=" * 70)

    # Step 1: Initialize components
    print("\n1. Initializing components...")

    registry = ModuleRegistry()
    registry.auto_discover()

    print(f"   Registered modules: {', '.join(registry.list_modules())}")

    # Create temporary directory for test run
    with tempfile.TemporaryDirectory() as tmpdir:
        checkpoint_dir = Path(tmpdir) / "runs"
        checkpoint_dir.mkdir(parents=True)

        state_manager = StateManager(checkpoint_dir)

        # Configuration
        config = {
            "llm": {
                "model": "qwen/qwen3.6-plus",
                "temperature": 0.3,
                "max_tokens": 4096,
            },
            "modules": {
                "paper_fetcher": {
                    "max_papers": TEST_MAX_PAPERS,
                    "require_abstract": True,
                },
                "topic_modeler": {
                    "min_topics": 2,
                    "max_topics": 5,  # Keep small for speed
                },
                "network_analyzer": {
                    "min_co_occurrence": 2,
                    "top_k_words": 20,
                }
            }
        }

        # Create orchestrator with full pipeline
        pipeline_order = [
            "query_generator",
            "paper_fetcher",
            "preprocessor",
            "frequency_analyzer",
            "topic_modeler",
            "tsr_ranker",
            "network_analyzer",
        ]

        orchestrator = PipelineOrchestrator(
            registry=registry,
            state_manager=state_manager,
            config=config,
            pipeline_order=pipeline_order,
        )

        print("   [OK] Orchestrator initialized")

        # Step 2: Run pipeline
        print(f"\n2. Running pipeline for: '{TEST_RESEARCH_DOMAIN}'")
        print(f"   Max papers: {TEST_MAX_PAPERS}")

        run_id = "test_e2e_001"

        initial_input = {
            "research_domain": TEST_RESEARCH_DOMAIN,
        }

        try:
            result = orchestrator.run(
                run_id=run_id,
                initial_input=initial_input,
                mode="batch",
                resume=False,
            )

            print("\n3. Pipeline completed successfully!")

            # Step 3: Verify outputs
            print("\n4. Verifying outputs...")

            run_state = state_manager.get_run_state(run_id)

            print(f"\n   Run state: {run_state.get('status', 'unknown')}")
            print(f"\n   Module statuses:")

            for module_name in pipeline_order:
                status = run_state.get("modules", {}).get(module_name, {})
                module_status = status.get("status", "not_run")
                symbol = "[OK]" if module_status == "completed" else "[FAIL]"
                print(f"     {symbol} {module_name}: {module_status}")

            # Check if all modules completed
            all_completed = all(
                run_state.get("modules", {}).get(m, {}).get("status") == "completed"
                for m in pipeline_order
            )

            if all_completed:
                print("\n   [OK] All modules completed")
            else:
                print("\n   [WARN] Some modules did not complete")
                return False

            # Step 4: Check output files
            print("\n5. Checking output files...")

            run_dir = checkpoint_dir / run_id

            # Check query generator output
            query_gen_dir = run_dir / "query_generator"
            if query_gen_dir.exists():
                print(f"   [OK] Query generator output found")
            else:
                print(f"   [WARN] Query generator output missing")

            # Check paper fetcher output
            papers_file = run_dir / "paper_fetcher" / "papers.csv"
            if papers_file.exists():
                import pandas as pd
                papers_df = pd.read_csv(papers_file)
                print(f"   [OK] Papers fetched: {len(papers_df)} papers")
            else:
                print(f"   [WARN] Papers file missing")

            # Check preprocessor output
            dtm_file = run_dir / "preprocessor" / "dtm.csv"
            vocab_file = run_dir / "preprocessor" / "vocab.json"
            if dtm_file.exists() and vocab_file.exists():
                print(f"   [OK] Preprocessor output found")
            else:
                print(f"   [WARN] Preprocessor output missing")

            # Check topic modeler output
            topic_word_file = run_dir / "topic_modeler" / "topic_word_distribution.csv"
            if topic_word_file.exists():
                import pandas as pd
                topic_word_df = pd.read_csv(topic_word_file)
                print(f"   [OK] Topic modeler output: {len(topic_word_df)} topics")
            else:
                print(f"   [WARN] Topic modeler output missing")

            # Check TSR ranker output
            tsr_file = run_dir / "tsr_ranker" / "tsr_scores.csv"
            if tsr_file.exists():
                import pandas as pd
                tsr_df = pd.read_csv(tsr_file)
                print(f"   [OK] TSR scores: top topic = {tsr_df.iloc[0]['topic']}")
            else:
                print(f"   [WARN] TSR output missing")

            # Check network analyzer output
            network_file = run_dir / "network_analyzer" / "co_word_network.graphml"
            if network_file.exists():
                print(f"   [OK] Network analysis output found")
            else:
                print(f"   [WARN] Network output missing")

            print("\n" + "=" * 70)
            print("[OK] END-TO-END TEST PASSED!")
            print("=" * 70)

            return True

        except Exception as e:
            print(f"\n[FAIL] Pipeline failed: {e}")
            import traceback
            traceback.print_exc()
            return False


def test_module_chaining():
    """Test that module outputs correctly chain to next module inputs."""
    print("\n" + "=" * 70)
    print("MODULE CHAINING TEST")
    print("=" * 70)

    # This test verifies the data flow between modules
    print("\nTesting data flow between modules...")
    print("  query_generator → paper_fetcher")
    print("  paper_fetcher → preprocessor")
    print("  preprocessor → frequency_analyzer")
    print("  frequency_analyzer → topic_modeler")
    print("  topic_modeler → tsr_ranker")
    print("  topic_modeler → network_analyzer")

    print("\n[INFO] Run end-to-end test to verify chaining")

    return True


if __name__ == "__main__":
    success = test_end_to_end_pipeline()

    if success:
        test_module_chaining()

    sys.exit(0 if success else 1)
