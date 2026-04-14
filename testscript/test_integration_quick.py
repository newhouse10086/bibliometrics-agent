#!/usr/bin/env python
"""Integration validation script.

Tests module chaining and data flow without external API calls.
"""

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from core.orchestrator import PipelineOrchestrator
from core.state_manager import StateManager
from modules.registry import ModuleRegistry


def check_module_interfaces():
    """Check that all modules have consistent input/output schemas."""
    print("=" * 70)
    print("MODULE INTERFACE CHECK")
    print("=" * 70)

    registry = ModuleRegistry()
    registry.auto_discover()

    print("\nChecking module interfaces...")

    for module_name in sorted(registry.list_modules()):
        module = registry.get(module_name)

        input_schema = module.input_schema()
        output_schema = module.output_schema()
        config_schema = module.config_schema()

        print(f"\n{module_name} (v{module.version}):")
        print(f"  Input fields: {len(input_schema.get('required', []))} required")
        print(f"  Output fields: {len(output_schema.get('properties', {}))}")
        print(f"  Config options: {len(config_schema.get('properties', {}))}")

        assert "type" in input_schema
        assert "type" in output_schema

    print("\n[OK] All module interfaces valid")
    return True


def test_module_integration():
    """Test integration of core modules with mock data."""
    print("\n" + "=" * 70)
    print("MODULE INTEGRATION TEST (Mock Data)")
    print("=" * 70)

    # Create temp directory
    tmpdir = Path(tempfile.mkdtemp())

    try:
        # Initialize
        print("\n1. Initializing pipeline...")

        registry = ModuleRegistry()
        registry.auto_discover()

        checkpoint_dir = tmpdir / "runs"
        checkpoint_dir.mkdir(parents=True)
        data_dir = tmpdir / "test_data"
        data_dir.mkdir()

        # Create mock papers
        print("Creating mock data...")
        papers = []
        for i in range(20):
            papers.append({
                "NUM": i,
                "TIAB": f"Machine learning healthcare application {i}. Neural networks deep learning medical diagnosis.",
                "title": f"Sample Paper {i}",
                "abstract": f"Machine learning healthcare application {i}. Neural networks deep learning medical diagnosis.",
                "year": 2020 + (i % 5),
            })

        papers_df = pd.DataFrame(papers)
        papers_file = data_dir / "papers.csv"
        papers_df.to_csv(papers_file, index=False)
        print(f"Created {len(papers)} mock papers")

        state_manager = StateManager(checkpoint_dir)

        config = {
            "modules": {
                "preprocessor": {
                    "language": "en",
                },
                "topic_modeler": {
                    "min_topics": 2,
                    "max_topics": 3,
                },
                "network_analyzer": {
                    "min_co_occurrence": 1,
                    "top_k_words": 10,
                }
            }
        }

        pipeline_order = [
            "preprocessor",
            "frequency_analyzer",
            # Skip topic_modeler and tsr_ranker for quick test (need more complex data)
            # "topic_modeler",
            # "tsr_ranker",
            "network_analyzer",
        ]

        orchestrator = PipelineOrchestrator(
            registry=registry,
            state_manager=state_manager,
            config=config,
            pipeline_order=pipeline_order,
        )

        print("   [OK] Pipeline initialized")

        # Run
        print("\n2. Running pipeline...")

        run_id = "test_integration_001"

        initial_input = {
            "documents": str(papers_file),
            "format": "csv"
        }

        result = orchestrator.run(
            run_id=run_id,
            initial_input=initial_input,
            mode="batch",
            resume=False,
        )

        print("\n3. Pipeline completed!")

        # Verify
        print("\n4. Verifying outputs...")

        run_dir = checkpoint_dir / run_id

        # Check preprocessor
        dtm_file = run_dir / "preprocessor" / "dtm.csv"
        vocab_file = run_dir / "preprocessor" / "vocab.txt"

        if dtm_file.exists() and vocab_file.exists():
            dtm_df = pd.read_csv(dtm_file)
            vocab = vocab_file.read_text(encoding="utf-8").strip().split("\n")
            print(f"   [OK] Preprocessor: DTM {dtm_df.shape}, vocab {len(vocab)}")
        else:
            print(f"   [FAIL] Preprocessor output missing")
            return False

        # Check network
        network_file = run_dir / "network_analyzer" / "co_word_network.graphml"

        if network_file.exists():
            import networkx as nx
            G = nx.read_graphml(network_file)
            print(f"   [OK] Network: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
        else:
            print(f"   [FAIL] Network output missing")
            return False

        print("\n" + "=" * 70)
        print("[SUCCESS] INTEGRATION TEST PASSED!")
        print("=" * 70)
        print("\nVerified: preprocessor -> frequency_analyzer -> network_analyzer")

        return True

    except Exception as e:
        print(f"\n[FAIL] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = check_module_interfaces()

    if success:
        success = test_module_integration()

    sys.exit(0 if success else 1)
