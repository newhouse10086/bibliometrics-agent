"""Test script for Network Analyzer module.

Validates the network analysis implementation.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.network_analyzer import NetworkAnalyzer
from core.orchestrator import RunContext


def create_test_data():
    """Create test data for network analysis.

    Simulates output from Preprocessor module.
    """
    # Create synthetic DTM
    np.random.seed(42)

    D = 100  # Number of documents
    W = 50   # Vocabulary size

    # Generate document-term matrix
    dtm = np.random.poisson(lam=2, size=(D, W))

    # Make it sparse (most entries zero)
    dtm[dtm > 5] = 0

    # Create vocabulary
    vocab = [f"word_{i}" for i in range(W)]

    # Create DataFrames
    doc_names = [f"Doc_{i}" for i in range(D)]

    dtm_df = pd.DataFrame(
        dtm,
        index=doc_names,
        columns=vocab
    )

    return dtm_df, vocab


def test_network_analyzer():
    """Test network analyzer module."""
    print("=" * 60)
    print("Network Analyzer Module Test")
    print("=" * 60)

    # Create test data
    print("\n1. Creating test data...")
    dtm_df, vocab = create_test_data()

    print(f"   - Documents: {dtm_df.shape[0]}")
    print(f"   - Vocabulary size: {len(vocab)}")
    print(f"   - Non-zero entries: {(dtm_df.values > 0).sum()}")

    # Save test data
    test_dir = Path(__file__).parent / "test_data"
    test_dir.mkdir(exist_ok=True)

    dtm_path = test_dir / "dtm.csv"
    dtm_df.to_csv(dtm_path)

    print(f"\n2. Saved test data to {test_dir}")

    # Initialize module
    print("\n3. Initializing Network Analyzer module...")
    analyzer = NetworkAnalyzer()

    # Verify module metadata
    assert analyzer.name == "network_analyzer"
    assert analyzer.version == "1.0.0"
    print(f"   [OK] Module: {analyzer.name} v{analyzer.version}")

    # Prepare input
    input_data = {
        "dtm_path": str(dtm_path),
        "vocab": vocab
    }

    # Prepare config
    config = {
        "min_co_occurrence": 2,
        "top_k_words": 30,
        "enable_visualization": True,
        "visualization_height": "600px"
    }

    # Create run context
    checkpoint_dir = test_dir / "checkpoints"
    checkpoint_dir.mkdir(exist_ok=True)

    context = RunContext(
        project_dir=test_dir,
        run_id="test_network",
        checkpoint_dir=checkpoint_dir,
        hardware_info={},
        previous_outputs={}
    )

    # Run module
    print("\n4. Running network analysis...")
    try:
        result = analyzer.process(input_data, config, context)

        print("\n5. Validating output...")

        # Check output structure
        assert "co_word_network" in result
        assert "centrality_metrics" in result
        assert "communities" in result
        assert "stats" in result
        print("   [OK] Output structure valid")

        # Check network file exists
        network_path = Path(result["co_word_network"])
        assert network_path.exists(), f"Network file not found: {network_path}"
        print(f"   [OK] Network file created: {network_path.name}")

        # Check centrality metrics
        centrality_df = pd.read_csv(result["centrality_metrics"])
        print(f"\n   Top 5 Central Words:")
        print(centrality_df.head(5).to_string(index=False))

        # Check communities
        community_df = pd.read_csv(result["communities"])
        print(f"\n   Communities Detected:")
        print(community_df.head(5).to_string(index=False))

        # Check statistics
        stats = result["stats"]
        print(f"\n   Network Statistics:")
        print(f"   - Nodes: {stats['n_nodes']}")
        print(f"   - Edges: {stats['n_edges']}")
        print(f"   - Density: {stats['density']:.4f}")
        print(f"   - Communities: {stats['n_communities']}")
        print(f"   - Avg Degree: {stats['avg_degree']:.2f}")

        # Validate statistics
        assert stats["n_nodes"] > 0, "Network has no nodes"
        assert stats["n_edges"] > 0, "Network has no edges"
        assert 0 <= stats["density"] <= 1, "Invalid network density"
        print("\n   [OK] Statistics valid")

        # Check visualization (if enabled)
        if "co_word_visualization" in result:
            viz_path = Path(result["co_word_visualization"])
            if viz_path.exists():
                print(f"\n   [OK] Visualization created: {viz_path.name}")

        print("\n" + "=" * 60)
        print("[SUCCESS] All tests passed!")
        print("=" * 60)

        return True

    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_network_analyzer()
    sys.exit(0 if success else 1)
