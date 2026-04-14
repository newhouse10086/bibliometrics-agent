"""Test PubMed data acquisition pipeline.

Tests the paper_fetcher → metadata_normalizer chain:
1. PubMed ESearch + EFetch (live API call)
2. MetadataNormalizer normalization and country resolution
3. Multi-source merge/deduplication
4. Output file integrity (papers.json + papers.csv)
5. Data quality checks (MeSH, affiliations, countries, etc.)

Usage:
    python test_pubmed_pipeline.py [--query QUERY] [--max MAX] [--sources SOURCES]
"""

import json
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from modules.metadata_normalizer import MetadataNormalizer
from modules.paper_fetcher import PaperFetcher
from modules.base import RunContext


# ── Helpers ──────────────────────────────────────────────────────────────────

def sep(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def check(condition: bool, label: str) -> None:
    icon = "OK" if condition else "FAIL"
    print(f"  [{icon}] {label}")
    if not condition:
        global FAIL_COUNT
        FAIL_COUNT += 1


FAIL_COUNT = 0


# ── Test 1: PubMed ESearch ──────────────────────────────────────────────────

def test_pubmed_esearch(query: str, max_papers: int) -> list[str]:
    sep("Test 1: PubMed ESearch")
    fetcher = PaperFetcher()
    pmids = fetcher._pubmed_esearch(
        query=query,
        max_papers=max_papers,
        year_range={},
        api_key="",
        email="",
        retmax=50,
        delay=0.4,
    )
    check(len(pmids) > 0, f"Got {len(pmids)} PMIDs for query '{query}'")
    check(len(pmids) <= max_papers, f"PMID count ≤ max_papers ({max_papers})")
    if pmids:
        print(f"  First 5 PMIDs: {pmids[:5]}")
    return pmids


# ── Test 2: PubMed EFetch + XML Parsing ────────────────────────────────────

def test_pubmed_efetch(pmids: list[str]) -> list[dict]:
    sep("Test 2: PubMed EFetch + XML Parsing")
    fetcher = PaperFetcher()
    raw_papers = fetcher._pubmed_efetch(pmids[:10], api_key="", email="", delay=0.4)

    check(len(raw_papers) > 0, f"Parsed {len(raw_papers)} articles from XML")

    if raw_papers:
        p = raw_papers[0]
        print(f"\n  Sample paper (PMID {p.get('pmid', '?')}):")
        print(f"    title:    {p.get('title', '')[:80]}...")
        print(f"    abstract: {p.get('abstract', '')[:80]}...")
        print(f"    year:     {p.get('year')}")
        print(f"    journal:  {p.get('journal_name', '')}")
        print(f"    doi:      {p.get('doi', '')}")
        print(f"    authors:  {len(p.get('authors', []))}")
        print(f"    mesh:     {p.get('mesh_terms', [])[:5]}")
        print(f"    keywords: {p.get('keywords', [])[:3]}")

        # Field-level checks
        check(bool(p.get("title")), "Has title")
        check(bool(p.get("abstract")), "Has abstract")
        check(p.get("year") is not None, "Has year")
        check(bool(p.get("journal_name")), "Has journal name")
        check(bool(p.get("pmid")), "Has PMID")
        check(len(p.get("authors", [])) > 0, "Has authors")

        # Count field coverage across all papers
        has_mesh = sum(1 for r in raw_papers if r.get("mesh_terms"))
        has_doi = sum(1 for r in raw_papers if r.get("doi"))
        has_abstract = sum(1 for r in raw_papers if r.get("abstract"))
        has_authors = sum(1 for r in raw_papers if r.get("authors"))
        has_affs = sum(
            1 for r in raw_papers
            if any(a.get("affiliations") for a in r.get("authors", []))
        )

        print(f"\n  Field coverage ({len(raw_papers)} papers):")
        print(f"    abstract: {has_abstract}/{len(raw_papers)}")
        print(f"    authors:  {has_authors}/{len(raw_papers)}")
        print(f"    MeSH:     {has_mesh}/{len(raw_papers)}")
        print(f"    DOI:      {has_doi}/{len(raw_papers)}")
        print(f"    affils:   {has_affs}/{len(raw_papers)}")

        check(has_mesh > 0, "At least 1 paper has MeSH terms")
        check(has_doi > 0, "At least 1 paper has DOI")

    return raw_papers


# ── Test 3: MetadataNormalizer ──────────────────────────────────────────────

def test_normalizer(raw_papers: list[dict]) -> list[dict]:
    sep("Test 3: MetadataNormalizer")

    normalizer = MetadataNormalizer()

    # 3a: Normalize a PubMed record
    normalized = normalizer.normalize(raw_papers[0], source="pubmed")
    check(bool(normalized.get("pmid")), f"Normalized PMID: {normalized.get('pmid')}")
    check(bool(normalized.get("title")), "Has title after normalization")
    check(isinstance(normalized.get("mesh_terms"), list), "mesh_terms is list")
    check(isinstance(normalized.get("authors"), list), "authors is list")
    check("_source" in normalized, "_source field present")

    # 3b: Country resolution
    print("\n  Country resolution tests:")
    test_cases = [
        ("Department of Computer Science, MIT, Cambridge, United States", "US"),
        ("Tsinghua University, Beijing, China", "CN"),
        ("University of Oxford, Oxford, United Kingdom", "GB"),
        ("Tokyo University, Tokyo, Japan", "JP"),
    ]
    for aff, expected in test_cases:
        result = normalizer.resolve_country(aff)
        ok = result == expected
        check(ok, f"'{aff[:40]}...' → {result} (expected {expected})")

    # 3c: Normalize all raw papers and check country resolution
    all_normalized = [normalizer.normalize(p, source="pubmed") for p in raw_papers]
    with_country = sum(
        1 for p in all_normalized
        if any(a.get("country") for a in p.get("authors", []))
    )
    print(f"\n  Country resolution: {with_country}/{len(all_normalized)} papers have author countries")
    check(with_country > 0, "At least 1 paper has country-resolved authors")

    # 3d: Merge records (simulate same paper from PubMed + OpenAlex)
    print("\n  Merge test (PubMed + fake OpenAlex):")
    # Find a paper with MeSH terms for the merge test
    paper_with_mesh = None
    for p in raw_papers:
        if p.get("mesh_terms"):
            paper_with_mesh = p
            break
    merge_paper = paper_with_mesh or raw_papers[0]
    doi = merge_paper.get("doi", "")
    # Build a fake OpenAlex record in proper API format
    fake_oa = {
        "doi": f"https://doi.org/{doi}" if doi else "",
        "title": merge_paper.get("title", ""),
        "abstract_inverted_index": None,
        "publication_year": merge_paper.get("year"),
        "primary_location": None,
        "authorships": [],
        "concepts": [{"display_name": "Computer Science", "level": 1}],
        "referenced_works": ["https://openalex.org/W1", "https://openalex.org/W2"],
        "cited_by_count": 42,
        "referenced_works_count": 2,
        "type": "article",
        "ids": {"pmid": "", "pmcid": ""},
    }
    merged = normalizer.merge_records([
        normalizer.normalize(merge_paper, source="pubmed"),
        normalizer.normalize(fake_oa, source="openalex"),
    ])
    check(merged.get("cited_by_count") == 42, "Merged cited_by_count from OpenAlex")
    check(len(merged.get("references_dois", [])) > 0, "Merged references_dois from OpenAlex")
    check(len(merged.get("fields_of_study", [])) > 0, "Merged fields_of_study from OpenAlex")
    if paper_with_mesh:
        check(len(merged.get("mesh_terms", [])) > 0, "Kept mesh_terms from PubMed")
    else:
        print("  [SKIP] No paper with MeSH terms available for mesh merge test")
    check("_source" not in merged, "_source removed after merge")

    return all_normalized


# ── Test 4: Full pipeline (paper_fetcher.process) ──────────────────────────

def test_full_pipeline(query: str, max_papers: int, sources: list[str]) -> dict:
    sep("Test 4: Full paper_fetcher pipeline")

    fetcher = PaperFetcher()

    # Create a temp context
    output_dir = Path(__file__).parent / "test_output_pubmed"
    output_dir.mkdir(parents=True, exist_ok=True)
    context = RunContext(
        project_dir=output_dir,
        run_id="test_pubmed",
        checkpoint_dir=output_dir,
        previous_outputs={},
    )

    config = {
        "api_delay": 0.5,
        "batch_size": 50,
        "require_abstract": True,
        "sources": sources,
        "pubmed_retmax": 50,
    }

    input_data = {
        "semantic_scholar_query": query,
        "max_papers": max_papers,
    }

    print(f"  Query: {query}")
    print(f"  Max papers: {max_papers}")
    print(f"  Sources: {sources}")
    print(f"  Output dir: {output_dir}")

    start_time = time.time()
    result = fetcher.process(input_data, config, context)
    elapsed = time.time() - start_time

    check(result.get("num_papers", 0) > 0,
          f"Got {result.get('num_papers', 0)} papers in {elapsed:.1f}s")

    # Check output files
    papers_json_path = result.get("papers_json_path")
    papers_csv_path = result.get("papers_csv_path")

    check(papers_json_path and Path(papers_json_path).exists(),
          f"papers.json exists: {papers_json_path}")
    check(papers_csv_path and Path(papers_csv_path).exists(),
          f"papers.csv exists: {papers_csv_path}")

    if papers_json_path and Path(papers_json_path).exists():
        with open(papers_json_path, "r", encoding="utf-8") as f:
            papers = json.load(f)

        print(f"\n  Papers overview ({len(papers)} papers):")

        # Field coverage
        fields_to_check = [
            "pmid", "doi", "title", "abstract", "year",
            "journal_name", "mesh_terms", "keywords", "authors",
        ]
        for field in fields_to_check:
            if field == "authors":
                count = sum(1 for p in papers if p.get("authors"))
            elif field in ("mesh_terms", "keywords"):
                count = sum(1 for p in papers if p.get(field))
            else:
                count = sum(1 for p in papers if p.get(field))
            pct = count / len(papers) * 100 if papers else 0
            print(f"    {field:20s}: {count:4d}/{len(papers)} ({pct:5.1f}%)")

        # Author country coverage
        with_country = sum(
            1 for p in papers
            if any(a.get("country") for a in p.get("authors", []))
        )
        pct_country = with_country / len(papers) * 100 if papers else 0
        print(f"    {'author_country':20s}: {with_country:4d}/{len(papers)} ({pct_country:5.1f}%)")

        # Sample one paper with full detail
        if papers:
            p = papers[0]
            print(f"\n  Sample paper detail:")
            print(f"    PMID:   {p.get('pmid')}")
            print(f"    DOI:    {p.get('doi')}")
            print(f"    Title:  {p.get('title', '')[:80]}")
            print(f"    Year:   {p.get('year')}")
            print(f"    Journal: {p.get('journal_name')}")
            print(f"    MeSH:   {p.get('mesh_terms', [])[:5]}")
            authors = p.get("authors", [])
            if authors:
                a = authors[0]
                print(f"    1st author: {a.get('name')}")
                print(f"    affiliations: {a.get('affiliations', [])[:2]}")
                print(f"    country: {a.get('country', 'N/A')}")

    # Check CSV
    if papers_csv_path and Path(papers_csv_path).exists():
        import pandas as pd
        df = pd.read_csv(papers_csv_path)
        check("NUM" in df.columns, "CSV has NUM column")
        check("TIAB" in df.columns, "CSV has TIAB column")
        check("mesh_terms" in df.columns, "CSV has mesh_terms column")
        check("pmid" in df.columns, "CSV has pmid column")
        check("doi" in df.columns, "CSV has doi column")
        print(f"\n  CSV shape: {df.shape}")
        print(f"  CSV columns: {list(df.columns)}")

    return result


# ── Test 5: Multi-source deduplication ──────────────────────────────────────

def test_deduplication(query: str) -> None:
    sep("Test 5: Multi-source deduplication")

    normalizer = MetadataNormalizer()
    fetcher = PaperFetcher()

    # Fetch a small batch from PubMed
    pmids = fetcher._pubmed_esearch(query, 5, {}, "", "", 5, 0.4)
    if not pmids:
        print("  [SKIP] No PMIDs returned")
        return

    time.sleep(0.4)
    pubmed_raw = fetcher._pubmed_efetch(pmids[:5], "", "", 0.4)

    # Create fake duplicate from OpenAlex using the same DOI
    # Find a paper with MeSH terms for a better dedup test
    paper_with_mesh = None
    for p in pubmed_raw:
        if p.get("mesh_terms"):
            paper_with_mesh = p
            break
    test_paper = paper_with_mesh or (pubmed_raw[0] if pubmed_raw else None)

    if test_paper and test_paper.get("doi"):
        doi = test_paper["doi"]
        fake_oa = {
            "doi": f"https://doi.org/{doi}",
            "title": test_paper.get("title", ""),
            "abstract_inverted_index": None,
            "publication_year": test_paper.get("year"),
            "primary_location": None,
            "authorships": [],
            "concepts": [{"display_name": "Test Field", "level": 1}],
            "referenced_works": ["https://openalex.org/W1", "https://openalex.org/W2"],
            "cited_by_count": 100,
            "referenced_works_count": 50,
            "type": "article",
            "ids": {"pmid": "", "pmcid": ""},
        }

        paper_lists = {
            "pubmed": [test_paper],
            "openalex": [fake_oa],
        }

        merged = normalizer.merge_paper_lists(paper_lists)

        check(len(merged) == 1,
              f"Deduplicated to {len(merged)} record (expected 1)")
        if merged:
            p = merged[0]
            check(p.get("cited_by_count") == 100,
                  f"Merged cited_by_count: {p.get('cited_by_count')}")
            if paper_with_mesh:
                check(len(p.get("mesh_terms", [])) > 0,
                      f"Kept MeSH terms from PubMed: {len(p.get('mesh_terms', []))}")
            else:
                print("  [SKIP] No paper with MeSH terms for dedup mesh test")
            check(len(p.get("fields_of_study", [])) > 0,
                  f"Merged fields_of_study from OpenAlex: {p.get('fields_of_study')}")
            check(p.get("doi", "").lower() == doi.lower() or p.get("doi") == doi,
                  f"DOI preserved: {p.get('doi')}")
    else:
        print("  [SKIP] No DOI in PubMed sample — cannot test dedup")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Test PubMed pipeline")
    parser.add_argument("--query", default="machine learning healthcare", help="Search query")
    parser.add_argument("--max", type=int, default=20, help="Max papers to fetch")
    parser.add_argument("--sources", default="pubmed", help="Comma-separated sources (pubmed,openalex,crossref)")
    args = parser.parse_args()

    query = args.query
    max_papers = args.max
    sources = [s.strip() for s in args.sources.split(",")]

    print(f"PubMed Pipeline Test")
    print(f"  Query:   {query}")
    print(f"  Max:     {max_papers}")
    print(f"  Sources: {sources}")

    # Test 1: ESearch
    pmids = test_pubmed_esearch(query, max_papers)
    if not pmids:
        print("\n[FAIL] PubMed ESearch returned no results. Check network or query.")
        sys.exit(1)

    time.sleep(0.4)

    # Test 2: EFetch
    raw_papers = test_pubmed_efetch(pmids)
    if not raw_papers:
        print("\n[FAIL] PubMed EFetch returned no papers. Check network.")
        sys.exit(1)

    # Test 3: Normalizer
    test_normalizer(raw_papers)

    # Test 4: Full pipeline
    test_full_pipeline(query, max_papers, sources)

    # Test 5: Deduplication
    test_deduplication(query)

    # Summary
    sep("Summary")
    if FAIL_COUNT == 0:
        print("  All tests passed!")
    else:
        print(f"  {FAIL_COUNT} check(s) failed.")
    print()

    return FAIL_COUNT


if __name__ == "__main__":
    sys.exit(main())
