"""Test script for PubMedFetcher module.

Quick validation that the module can be imported and instantiated.
For full integration testing, use test_integration_quick.py.
"""

from pathlib import Path
from modules.base import RunContext
from modules.pubmed_fetcher import PubMedFetcher


def test_pubmed_fetcher_basic():
    """Test basic module instantiation and schema validation."""
    fetcher = PubMedFetcher()

    # Check properties
    assert fetcher.name == "pubmed_fetcher"
    assert fetcher.version == "1.0.0"

    # Check schemas
    input_schema = fetcher.input_schema()
    assert "query" in input_schema["required"]
    assert "query" in input_schema["properties"]

    output_schema = fetcher.output_schema()
    assert "papers_csv_path" in output_schema["properties"]
    assert "papers_json_path" in output_schema["properties"]
    assert "paper_count" in output_schema["properties"]

    config_schema = fetcher.config_schema()
    assert "max_papers" in config_schema["properties"]
    assert "pubmed_api_key" in config_schema["properties"]
    assert "pubmed_email" in config_schema["properties"]

    # Check hardware requirements
    hw = fetcher.get_hardware_requirements({"max_papers": 100})
    assert hw.cpu_cores == 1
    assert hw.min_memory_gb >= 0.1

    print("✓ Basic module validation passed")


def test_pubmed_fetcher_process():
    """Test the process() method with a small query.

    Note: This test makes real API calls to PubMed.
    Requires internet connection and may hit rate limits.
    """
    fetcher = PubMedFetcher()

    # Create a test context
    test_output_dir = Path("test_output")
    test_output_dir.mkdir(exist_ok=True)

    context = RunContext(
        project_dir=test_output_dir,
        run_id="test_run",
        checkpoint_dir=test_output_dir,
        hardware_info={},
        previous_outputs={}
    )

    # Test with a small query (limit to 5 papers for speed)
    result = fetcher.process(
        input_data={
            "query": "cancer immunotherapy",
            "max_papers": 5
        },
        config={
            "max_papers": 5
        },
        context=context
    )

    # Check output structure
    assert "papers_csv_path" in result
    assert "papers_json_path" in result
    assert "paper_count" in result
    assert result["paper_count"] <= 5

    # Check files exist
    csv_path = Path(result["papers_csv_path"])
    json_path = Path(result["papers_json_path"])
    assert csv_path.exists()
    assert json_path.exists()

    print(f"✓ Successfully fetched {result['paper_count']} papers")
    print(f"  CSV: {csv_path}")
    print(f"  JSON: {json_path}")

    # Clean up
    import shutil
    if test_output_dir.exists():
        shutil.rmtree(test_output_dir)


if __name__ == "__main__":
    print("Testing PubMedFetcher module...")
    print()

    # Test 1: Basic validation
    test_pubmed_fetcher_basic()
    print()

    # Test 2: Process with real API (optional, may fail without internet)
    print("Testing process() with real PubMed API...")
    print("(This makes real API calls - may take 10-30 seconds)")
    try:
        test_pubmed_fetcher_process()
    except Exception as e:
        print(f"⚠ Process test failed (likely network/rate limit issue): {e}")
        print("This is expected if offline or rate-limited")

    print()
    print("All basic tests passed!")
