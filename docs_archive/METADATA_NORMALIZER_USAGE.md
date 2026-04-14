# Metadata Normalizer Usage Examples

## Basic Usage

### 1. Normalize a Single PubMed Record

```python
from modules.metadata_normalizer import MetadataNormalizer

normalizer = MetadataNormalizer()

# Raw PubMed data (from EFetch XML parsing)
pubmed_raw = {
    "pmid": "12345678",
    "doi": "10.1234/test.2024.001",
    "title": "Machine Learning in Healthcare",
    "abstract": "This paper explores...",
    "year": 2024,
    "journal_name": "Nature Medicine",
    "authors": [
        {
            "name": "John Smith",
            "affiliations": ["MIT, Cambridge, USA"]
        }
    ],
    "mesh_terms": ["Machine Learning", "Healthcare"]
}

# Normalize to unified schema
normalized = normalizer.normalize(pubmed_raw, source="pubmed")

print(normalized["pmid"])  # "12345678"
print(normalized["authors"][0]["country"])  # "US" (auto-resolved)
print(normalized["mesh_terms"])  # ["Machine Learning", "Healthcare"]
```

### 2. Normalize an OpenAlex Record

```python
# Raw OpenAlex data (from Works API)
openalex_raw = {
    "id": "https://openalex.org/W1234567890",
    "doi": "https://doi.org/10.1234/test.2024.001",
    "title": "Machine Learning in Healthcare",
    "abstract_inverted_index": {
        "This": [0],
        "paper": [1],
        "explores": [2],
        # ... more words
    },
    "publication_year": 2024,
    "authorships": [
        {
            "author": {
                "display_name": "John Smith",
                "orcid": "https://orcid.org/0000-0001-2345-6789"
            },
            "institutions": [
                {
                    "display_name": "MIT",
                    "country_code": "US",
                    "ror_id": "https://ror.org/042nb2s44"
                }
            ]
        }
    ],
    "cited_by_count": 42
}

normalized = normalizer.normalize(openalex_raw, source="openalex")

# Abstract automatically reconstructed from inverted index
print(normalized["abstract"])  # "This paper explores..."

# ORCID cleaned (https://orcid.org/ prefix removed)
print(normalized["authors"][0]["orcid"])  # "0000-0001-2345-6789"

# ROR data preserved
print(normalized["authors"][0]["ror_id"])  # "https://ror.org/042nb2s44"
```

### 3. Merge Multiple Sources for Same Paper

```python
# Fetch from multiple APIs
pubmed_record = normalizer.normalize(pubmed_raw, source="pubmed")
openalex_record = normalizer.normalize(openalex_raw, source="openalex")
crossref_record = normalizer.normalize(crossref_raw, source="crossref")

# Merge (PubMed has highest priority)
merged = normalizer.merge_records([
    pubmed_record,
    openalex_record,
    crossref_record
])

# Uses PubMed's title (highest priority)
print(merged["title"])  # PubMed title

# Uses PubMed's abstract (highest priority)
print(merged["abstract"])  # PubMed abstract

# Takes maximum citation count
print(merged["cited_by_count"])  # max(42, 50, 60) = 60

# Merges authors from all sources (deduplicated by name)
print(len(merged["authors"]))  # Union of all unique authors

# Merges keywords (union)
print(merged["mesh_terms"])  # Union from all sources
```

### 4. Batch Merge Multiple Paper Lists

```python
# Fetch from multiple sources
pubmed_papers = fetch_from_pubmed(query)  # List of raw dicts
openalex_papers = fetch_from_openalex(query)  # List of raw dicts
crossref_papers = fetch_from_crossref(query)  # List of raw dicts

# Batch merge with deduplication
merged_papers = normalizer.merge_paper_lists({
    "pubmed": pubmed_papers,
    "openalex": openalex_papers,
    "crossref": crossref_papers
})

print(f"Merged {len(merged_papers)} unique papers")

# Each paper is fully merged across sources
for paper in merged_papers:
    print(f"DOI: {paper['doi']}, Authors: {len(paper['authors'])}")
```

## Country Resolution

### 1. Basic Country Resolution

```python
# From affiliation string
country = normalizer.resolve_country("MIT, Cambridge, USA")
print(country)  # "US"

country = normalizer.resolve_country("Oxford University, UK")
print(country)  # "GB"

country = normalizer.resolve_country("Tsinghua University, Beijing, China")
print(country)  # "CN"
```

### 2. With ROR Data (Highest Priority)

```python
ror_data = {
    "country_code": "US",
    "country_name": "United States"
}

# ROR data overrides affiliation text
country = normalizer.resolve_country(
    "Some Institute, Unknown Location",
    ror_data=ror_data
)
print(country)  # "US" (from ROR)
```

### 3. Edge Cases

```python
# Empty affiliation
country = normalizer.resolve_country("")
print(country)  # "" (empty string)

# Unresolvable
country = normalizer.resolve_country("Some random text")
print(country)  # "" (empty string)

# Multiple country patterns (last match wins)
country = normalizer.resolve_country("UK-USA Collaboration, Germany")
print(country)  # "DE" (last segment after comma)
```

## Advanced Usage

### 1. Custom Processing Pipeline

```python
def process_papers(query, max_papers=1000):
    normalizer = MetadataNormalizer()

    # Fetch from multiple sources
    pubmed_papers = fetch_pubmed(query, max_papers)
    openalex_papers = fetch_openalex(query, max_papers)
    crossref_papers = fetch_crossref(query, max_papers)

    # Normalize each source
    pubmed_normalized = [
        normalizer.normalize(p, source="pubmed")
        for p in pubmed_papers
    ]

    openalex_normalized = [
        normalizer.normalize(p, source="openalex")
        for p in openalex_papers
    ]

    crossref_normalized = [
        normalizer.normalize(p, source="crossref")
        for p in crossref_papers
    ]

    # Merge and deduplicate
    merged = normalizer.merge_paper_lists({
        "pubmed": pubmed_normalized,
        "openalex": openalex_normalized,
        "crossref": crossref_normalized
    })

    return merged
```

### 2. Export to CSV

```python
import pandas as pd

# Merge papers
merged_papers = normalizer.merge_paper_lists(paper_lists)

# Convert to flat CSV format
flat_rows = [
    normalizer.to_flat_dict(paper, index=i)
    for i, paper in enumerate(merged_papers)
]

df = pd.DataFrame(flat_rows)
df.to_csv("papers.csv", index=False)

# CSV has special columns for backward compatibility:
# - NUM: Row number (1-indexed)
# - TIAB: Title + Abstract concatenated (for topic modeling)
# - author_countries: Comma-separated country codes
# - author_affiliations: Semicolon-separated affiliation lists
```

### 3. Filter by Country

```python
# Merge papers
merged_papers = normalizer.merge_paper_lists(paper_lists)

# Filter papers with at least one US author
us_papers = [
    p for p in merged_papers
    if any(a.get("country") == "US" for a in p["authors"])
]

print(f"Found {len(us_papers)} papers with US authors")
```

### 4. Analyze Author Countries

```python
from collections import Counter

merged_papers = normalizer.merge_paper_lists(paper_lists)

# Count papers by country
country_counts = Counter()
for paper in merged_papers:
    countries = {a.get("country") for a in paper["authors"] if a.get("country")}
    for country in countries:
        country_counts[country] += 1

print("Top countries:")
for country, count in country_counts.most_common(10):
    print(f"  {country}: {count} papers")
```

## Integration with PaperFetcher

The `MetadataNormalizer` is already integrated into the `paper_fetcher` module:

```python
# In modules/paper_fetcher.py
from modules.metadata_normalizer import MetadataNormalizer

class PaperFetcher(BaseModule):
    def __init__(self):
        self._normalizer = MetadataNormalizer()

    def process(self, input_data, config, context):
        # Fetch from multiple sources
        paper_lists = {
            "pubmed": self._fetch_pubmed(...),
            "openalex": self._fetch_openalex(...),
            "crossref": self._fetch_crossref(...),
        }

        # Merge and deduplicate
        merged = self._normalizer.merge_paper_lists(paper_lists)

        # Save outputs
        save_json(merged, "papers.json")
        save_csv(merged, "papers.csv")

        return {"num_papers": len(merged), ...}
```

## Error Handling

The normalizer handles errors gracefully:

```python
# Unknown source
record = normalizer.normalize(data, source="unknown")
# Returns empty record with all fields

# Malformed data
record = normalizer.normalize({"title": "Test"}, source="pubmed")
# Missing fields become empty strings/None

# Missing pycountry
# System still works with ROR + regex matching

# Missing affiliations
country = normalizer.resolve_country(None)
# Returns empty string, no crash
```

## Performance Tips

1. **Batch Processing**: Use `merge_paper_lists()` instead of calling `merge_records()` in a loop
2. **Caching**: Cache country resolution results if processing many papers with same affiliations
3. **Early Filtering**: Filter papers before merging if you don't need all sources
4. **Parallel Fetching**: Fetch from multiple APIs in parallel, then merge results

## Testing

Run the test suite to verify functionality:

```bash
# Install dependencies
pip install -e .

# Run tests
python -m pytest tests/test_metadata_normalizer.py -v

# Run specific test
python -m pytest tests/test_metadata_normalizer.py::test_merge_records_multiple_same_paper -v
```

## Summary

The `MetadataNormalizer` provides:
- ✅ Unified schema across PubMed, OpenAlex, Crossref, Semantic Scholar
- ✅ Intelligent merging with priority-based conflict resolution
- ✅ Automatic country resolution from affiliations
- ✅ Batch processing with deduplication
- ✅ CSV export with TIAB field for topic modeling
- ✅ Comprehensive error handling
- ✅ Well-tested (25 test cases)
