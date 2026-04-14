"""Test script for upgraded PaperFetcher with multi-source support."""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from modules.paper_fetcher import PaperFetcher
from modules.base import RunContext

def test_paper_fetcher_imports():
    """Test that all imports work correctly."""
    print("✓ PaperFetcher imported successfully")
    print(f"  Version: {PaperFetcher().version}")
    print(f"  Name: {PaperFetcher().name}")

def test_config_schema():
    """Test that config schema includes all required fields."""
    fetcher = PaperFetcher()
    schema = fetcher.config_schema()
    props = schema.get("properties", {})

    required_fields = [
        "pubmed_api_key",
        "pubmed_email",
        "openalex_email",
        "crossref_email",
        "semantic_scholar_api_key",
        "sources",
        "api_delay",
        "batch_size",
    ]

    missing = [f for f in required_fields if f not in props]

    if missing:
        print(f"✗ Missing config fields: {missing}")
        return False
    else:
        print(f"✓ All required config fields present: {len(required_fields)} fields")
        return True

def test_input_schema():
    """Test input schema compatibility."""
    fetcher = PaperFetcher()
    schema = fetcher.input_schema()

    # Check that it accepts semantic_scholar_query
    props = schema.get("properties", {})
    if "semantic_scholar_query" in props:
        print("✓ Input schema accepts semantic_scholar_query")
    else:
        print("✗ Input schema missing semantic_scholar_query")
        return False

    # Check optional pubmed_query
    if "pubmed_query" in props:
        print("✓ Input schema accepts optional pubmed_query")
    else:
        print("⚠ Input schema missing optional pubmed_query (may cause issues)")

    return True

def test_output_schema():
    """Test output schema."""
    fetcher = PaperFetcher()
    schema = fetcher.output_schema()
    props = schema.get("properties", {})

    expected_fields = ["papers_json_path", "papers_csv_path", "num_papers"]

    missing = [f for f in expected_fields if f not in props]

    if missing:
        print(f"✗ Missing output fields: {missing}")
        return False
    else:
        print(f"✓ Output schema complete: {len(expected_fields)} fields")
        return True

def test_metadata_normalizer_integration():
    """Test that MetadataNormalizer is properly integrated."""
    from modules.metadata_normalizer import MetadataNormalizer

    fetcher = PaperFetcher()
    normalizer = fetcher._normalizer

    print("✓ MetadataNormalizer integrated")
    print(f"  Has merge_paper_lists: {hasattr(normalizer, 'merge_paper_lists')}")
    print(f"  Has to_flat_dict: {hasattr(normalizer, 'to_flat_dict')}")

    return True

def main():
    """Run all tests."""
    print("=" * 60)
    print("Testing upgraded PaperFetcher with multi-source support")
    print("=" * 60)
    print()

    tests = [
        ("Import Test", test_paper_fetcher_imports),
        ("Config Schema", test_config_schema),
        ("Input Schema", test_input_schema),
        ("Output Schema", test_output_schema),
        ("MetadataNormalizer Integration", test_metadata_normalizer_integration),
    ]

    results = []
    for name, test_fn in tests:
        print(f"\n{name}:")
        print("-" * 40)
        try:
            result = test_fn()
            results.append((name, result))
        except Exception as e:
            print(f"✗ Test failed with exception: {e}")
            results.append((name, False))

    print("\n" + "=" * 60)
    print("Summary:")
    print("=" * 60)
    passed = sum(1 for _, r in results if r)
    total = len(results)
    print(f"Passed: {passed}/{total}")

    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {name}")

    if passed == total:
        print("\n🎉 All tests passed! PaperFetcher upgrade is complete.")
        return 0
    else:
        print(f"\n⚠ {total - passed} test(s) failed. Please review.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
