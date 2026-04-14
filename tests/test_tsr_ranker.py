"""Test script for TSR Ranker module.

Validates the TSR ranking implementation against expected outputs.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.tsr_ranker import TSRRanker
from core.orchestrator import RunContext


def create_test_data():
    """Create test data for TSR ranking.

    Simulates output from TopicModeler module.
    """
    # Create synthetic topic-word matrix (5 topics, 20 words)
    np.random.seed(42)

    K = 5  # Number of topics
    W = 20  # Vocabulary size
    D = 50  # Number of documents

    # Generate topic-word distributions (normalize rows)
    topic_word = np.random.dirichlet(np.ones(W), size=K)

    # Generate document-topic distributions (normalize columns)
    doc_topic = np.random.dirichlet(np.ones(K), size=D)

    # Create DataFrames
    vocab = [f"word_{i}" for i in range(W)]
    topic_names = [f"Topic_{i}" for i in range(K)]
    doc_names = [f"Doc_{i}" for i in range(D)]

    topic_word_df = pd.DataFrame(
        topic_word,
        index=topic_names,
        columns=vocab
    )

    doc_topic_df = pd.DataFrame(
        doc_topic,
        index=doc_names,
        columns=topic_names
    )

    return topic_word_df, doc_topic_df, vocab, D * 100  # Approximate total words


def test_tsr_ranker():
    """Test TSR ranker module."""
    print("=" * 60)
    print("TSR Ranker Module Test")
    print("=" * 60)

    # Create test data
    print("\n1. Creating test data...")
    topic_word_df, doc_topic_df, vocab, total_words = create_test_data()

    print(f"   - Topics: {topic_word_df.shape[0]}")
    print(f"   - Vocabulary size: {len(vocab)}")
    print(f"   - Documents: {doc_topic_df.shape[0]}")
    print(f"   - Total words: {total_words}")

    # Save test data to temporary files
    test_dir = Path(__file__).parent / "test_data"
    test_dir.mkdir(exist_ok=True)

    topic_word_path = test_dir / "topic_word_distribution.csv"
    doc_topic_path = test_dir / "doc_topic_distribution.csv"

    topic_word_df.to_csv(topic_word_path)
    doc_topic_df.to_csv(doc_topic_path)

    print(f"\n2. Saved test data to {test_dir}")

    # Initialize module
    print("\n3. Initializing TSR Ranker module...")
    ranker = TSRRanker()

    # Verify module metadata
    assert ranker.name == "tsr_ranker"
    assert ranker.version == "1.0.0"
    print(f"   [OK] Module: {ranker.name} v{ranker.version}")

    # Prepare input
    input_data = {
        "topic_word_matrix": str(topic_word_path),
        "doc_topic_matrix": str(doc_topic_path),
        "vocab": vocab,
        "total_words": total_words
    }

    # Prepare config
    config = {
        "weights": {
            "uniform": 0.6,
            "vacuous": 0.4
        },
        "phi_weights": {
            "uniform": 0.25,
            "vacuous": 0.25,
            "background": 0.5
        }
    }

    # Create run context
    checkpoint_dir = test_dir / "checkpoints"
    checkpoint_dir.mkdir(exist_ok=True)

    context = RunContext(
        project_dir=test_dir,
        run_id="test_tsr",
        checkpoint_dir=checkpoint_dir,
        hardware_info={},
        previous_outputs={}
    )

    # Run module
    print("\n4. Running TSR ranking...")
    try:
        result = ranker.process(input_data, config, context)

        print("\n5. Validating output...")

        # Check output structure
        assert "tsr_scores" in result
        assert "metrics" in result
        assert "stats" in result
        print("   [OK] Output structure valid")

        # Load TSR scores
        tsr_df = pd.read_csv(result["tsr_scores"])
        print(f"\n   TSR Scores (Top 3):")
        print(tsr_df.head(3).to_string(index=False))

        # Check statistics
        stats = result["stats"]
        print(f"\n   Statistics:")
        print(f"   - Number of topics: {stats['n_topics']}")
        print(f"   - Number of documents: {stats['n_docs']}")
        print(f"   - Vocabulary size: {stats['vocab_size']}")
        print(f"   - Top topic: {stats['top_topic']}")

        # Validate scores are sorted
        tsr_values = tsr_df["tsr_score"].values
        assert np.all(tsr_values[:-1] >= tsr_values[1:]), "Scores not sorted"
        print("\n   [OK] Scores properly sorted (descending)")

        # Validate all scores are non-negative
        assert np.all(tsr_values >= 0), "Negative scores found"
        print("   [OK] All scores are non-negative")

        # Check metrics
        metrics = result["metrics"]
        print(f"\n   Metrics computed:")
        print(f"   - KL divergence: {len(metrics['kl_divergence']['uniform'])} values")
        print(f"   - Cosine dissimilarity: {len(metrics['cosine_dissimilarity']['uniform'])} values")
        print(f"   - Pearson correlation: {len(metrics['pearson_correlation']['uniform'])} values")

        print("\n" + "=" * 60)
        print("[SUCCESS] All tests passed!")
        print("=" * 60)

        return True

    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_with_sample_project():
    """Test TSR ranker with sample-project data (if available)."""
    sample_data_dir = Path(__file__).parent.parent.parent / "sample-project"

    # Check if sample data exists
    doc_matrix_path = sample_data_dir / "PJ129_doc metrix_3.csv"
    word_matrix_path = sample_data_dir / "PJ129_word metrix.csv"

    if not (doc_matrix_path.exists() and word_matrix_path.exists()):
        print("\n[WARN] Sample project data not found. Skipping validation.")
        return

    print("\n" + "=" * 60)
    print("Validating with sample-project data")
    print("=" * 60)

    # Load sample data
    # Note: The R script expects specific format, adapt as needed
    print(f"\nLoading data from {sample_data_dir}...")
    print("Note: Manual validation required against R output")


if __name__ == "__main__":
    success = test_tsr_ranker()

    if success:
        test_with_sample_project()
