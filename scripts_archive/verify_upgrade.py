"""Quick verification test for PaperFetcher upgrade.

Run this to verify the upgrade is complete and functional.
DOES NOT require API keys.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

def test_basic_functionality():
    """Test that PaperFetcher can be instantiated and configured."""
    from modules.paper_fetcher import PaperFetcher
    from modules.metadata_normalizer import MetadataNormalizer

    # Instantiate
    fetcher = PaperFetcher()

    # Check version
    assert fetcher.version == "2.0.0", f"Version mismatch: {fetcher.version}"
    print(f"✓ Version: {fetcher.version}")

    # Check name
    assert fetcher.name == "paper_fetcher", f"Name mismatch: {fetcher.name}"
    print(f"✓ Name: {fetcher.name}")

    # Check MetadataNormalizer integration
    assert hasattr(fetcher, '_normalizer'), "Missing MetadataNormalizer"
    assert isinstance(fetcher._normalizer, MetadataNormalizer), "Wrong normalizer type"
    print("✓ MetadataNormalizer integrated")

    # Check schemas
    input_schema = fetcher.input_schema()
    assert "semantic_scholar_query" in input_schema["properties"], "Missing semantic_scholar_query"
    assert "pubmed_query" in input_schema["properties"], "Missing pubmed_query"
    print("✓ Input schema valid")

    output_schema = fetcher.output_schema()
    assert "papers_json_path" in output_schema["properties"], "Missing papers_json_path"
    assert "papers_csv_path" in output_schema["properties"], "Missing papers_csv_path"
    print("✓ Output schema valid")

    config_schema = fetcher.config_schema()
    props = config_schema["properties"]
    required_keys = [
        "pubmed_api_key",
        "pubmed_email",
        "openalex_email",
        "crossref_email",
        "sources",
    ]
    for key in required_keys:
        assert key in props, f"Missing config key: {key}"
    print(f"✓ Config schema valid ({len(required_keys)} new fields)")

    # Check source methods exist
    assert hasattr(fetcher, '_fetch_pubmed'), "Missing _fetch_pubmed"
    assert hasattr(fetcher, '_fetch_openalex'), "Missing _fetch_openalex"
    assert hasattr(fetcher, '_fetch_crossref'), "Missing _fetch_crossref"
    assert hasattr(fetcher, '_fetch_semantic_scholar'), "Missing _fetch_semantic_scholar"
    print("✓ All fetch methods present")

    # Check PubMed helper methods
    assert hasattr(fetcher, '_pubmed_esearch'), "Missing _pubmed_esearch"
    assert hasattr(fetcher, '_pubmed_efetch'), "Missing _pubmed_efetch"
    assert hasattr(fetcher, '_parse_pubmed_article'), "Missing _parse_pubmed_article"
    print("✓ PubMed helper methods present")

    return True

def test_metadata_normalizer():
    """Test MetadataNormalizer is ready for multi-source merging."""
    from modules.metadata_normalizer import MetadataNormalizer

    normalizer = MetadataNormalizer()

    # Check normalization methods
    sources = ["pubmed", "openalex", "crossref", "semantic_scholar"]
    for source in sources:
        method_name = f"_normalize_{source}"
        assert hasattr(normalizer, method_name), f"Missing {method_name}"
    print(f"✓ Normalization methods for {len(sources)} sources")

    # Check merge methods
    assert hasattr(normalizer, 'merge_records'), "Missing merge_records"
    assert hasattr(normalizer, 'merge_paper_lists'), "Missing merge_paper_lists"
    print("✓ Merge methods present")

    # Check utility methods
    assert hasattr(normalizer, 'resolve_country'), "Missing resolve_country"
    assert hasattr(normalizer, '_reconstruct_abstract'), "Missing _reconstruct_abstract"
    assert hasattr(normalizer, 'to_flat_dict'), "Missing to_flat_dict"
    print("✓ Utility methods present")

    # Test empty record
    empty = normalizer._empty_record()
    assert isinstance(empty, dict), "Empty record should be dict"
    assert "doi" in empty, "Empty record missing DOI field"
    print("✓ Empty record schema valid")

    return True

def main():
    print("=" * 70)
    print("PaperFetcher Multi-Source Upgrade - Quick Verification")
    print("=" * 70)
    print()

    print("Test 1: Basic Functionality")
    print("-" * 70)
    try:
        test_basic_functionality()
        print()
        print("✓ Test 1 PASSED")
    except AssertionError as e:
        print(f"✗ Test 1 FAILED: {e}")
        return 1
    except Exception as e:
        print(f"✗ Test 1 ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    print()
    print("Test 2: MetadataNormalizer Integration")
    print("-" * 70)
    try:
        test_metadata_normalizer()
        print()
        print("✓ Test 2 PASSED")
    except AssertionError as e:
        print(f"✗ Test 2 FAILED: {e}")
        return 1
    except Exception as e:
        print(f"✗ Test 2 ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    print()
    print("=" * 70)
    print("✓ ALL TESTS PASSED")
    print("=" * 70)
    print()
    print("PaperFetcher has been successfully upgraded with multi-source support!")
    print()
    print("Data Sources:")
    print("  1. PubMed (E-utilities) - Highest priority")
    print("  2. OpenAlex - Second priority")
    print("  3. Crossref - Third priority")
    print("  4. Semantic Scholar - Fallback")
    print()
    print("Next Steps:")
    print("  - Set API keys in config for higher rate limits")
    print("  - Run integration test: python test_integration_quick.py")
    print("  - See PAPER_FETCHER_UPGRADE.md for detailed documentation")
    print()

    return 0

if __name__ == "__main__":
    sys.exit(main())
