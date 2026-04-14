# PaperFetcher v2.0.0 - Quick Start Guide

## Basic Usage

```python
from modules.paper_fetcher import PaperFetcher
from modules.base import RunContext
from pathlib import Path

# Initialize fetcher
fetcher = PaperFetcher()

# Configure (minimal setup)
config = {
    "sources": ["pubmed", "openalex", "crossref"],
    "max_papers": 100
}

# Create run context
context = RunContext(
    run_id="test_run",
    checkpoint_dir=Path("checkpoints/test_run"),
    previous_outputs={}
)

# Fetch papers
result = fetcher.process(
    input_data={
        "semantic_scholar_query": "machine learning healthcare",
        "max_papers": 100
    },
    config=config,
    context=context
)

print(f"✓ Fetched {result['num_papers']} papers")
print(f"  JSON: {result['papers_json_path']}")
print(f"  CSV: {result['papers_csv_path']}")
```

---

## Configuration Options

### Source Selection

```python
# Use all sources
config = {
    "sources": ["pubmed", "openalex", "crossref", "semantic_scholar"]
}

# Use only PubMed
config = {
    "sources": ["pubmed"]
}

# Use PubMed + OpenAlex (recommended for most cases)
config = {
    "sources": ["pubmed", "openalex"]
}
```

### API Keys (Optional)

```python
config = {
    "pubmed_api_key": "your-pubmed-api-key",        # 10 req/s instead of 3
    "openalex_email": "you@example.com",            # Polite pool (10 req/s)
    "crossref_email": "you@example.com",            # Polite pool
    "semantic_scholar_api_key": "your-ss-key"       # Higher rate limits
}
```

### Year Range Filtering

```python
input_data = {
    "semantic_scholar_query": "machine learning healthcare",
    "year_range": {
        "start": 2020,
        "end": 2024
    }
}
```

### Advanced Options

```python
config = {
    "sources": ["pubmed", "openalex", "crossref"],
    "max_papers": 500,
    "api_delay": 1.5,              # Delay between API calls (seconds)
    "require_abstract": True,      # Only fetch papers with abstracts
    "pubmed_retmax": 200,          # Max PMIDs per PubMed ESearch call
}
```

---

## PubMed-Specific Queries

You can use different queries for PubMed and other sources:

```python
input_data = {
    "semantic_scholar_query": "machine learning healthcare",  # For OpenAlex/Crossref/SS
    "pubmed_query": "(machine learning[Title/Abstract]) AND healthcare[MeSH]",  # PubMed-specific
    "max_papers": 100
}
```

### PubMed Query Syntax Examples

```python
# Title/Abstract search
"machine learning[Title/Abstract]"

# MeSH term search
"healthcare[MeSH]"

# Combine with AND/OR
"(machine learning[Title/Abstract]) AND healthcare[MeSH]"

# Author search
"Smith J[Author]"

# Journal search
"Nature[Journal]"

# Date range (alternative to year_range)
"2020:2024[pdat]"
```

---

## Output Format

### papers.json (Canonical Unified Records)

```json
[
  {
    "pmid": "12345678",
    "doi": "10.1234/example.2024",
    "pmcid": "PMC1234567",
    "title": "Machine Learning in Healthcare: A Review",
    "abstract": "This paper reviews...",
    "year": 2024,
    "journal_name": "Nature Medicine",
    "journal_issn": "1546-170X",
    "authors": [
      {
        "name": "John Smith",
        "affiliations": ["MIT", "Harvard Medical School"],
        "country": "US",
        "orcid": "0000-0001-2345-6789"
      }
    ],
    "mesh_terms": ["Machine Learning", "Healthcare", "Artificial Intelligence"],
    "keywords": ["deep learning", "clinical decision support"],
    "cited_by_count": 42,
    "reference_count": 35,
    "fields_of_study": ["Computer Science", "Medicine"],
    "url": "https://pubmed.ncbi.nlm.nih.gov/12345678/",
    "document_type": "Journal Article"
  }
]
```

### papers.csv (Backward-Compatible Flat View)

| NUM | TIAB | title | abstract | year | authors | author_countries | journal_name | doi | pmid | mesh_terms | ... |
|-----|------|-------|----------|------|---------|------------------|--------------|-----|------|------------|-----|
| 1 | "Machine Learning in Healthcare..." | Machine Learning in Healthcare: A Review | This paper reviews... | 2024 | John Smith, Jane Doe | US, GB | Nature Medicine | 10.1234/example | 12345678 | Machine Learning; Healthcare | ... |

**Note:** `TIAB` column contains `title + " " + abstract` for backward compatibility with downstream modules.

---

## Merge Priority

When the same paper is found in multiple sources, the metadata is merged with the following priority:

**PubMed > OpenAlex > Crossref > Semantic Scholar**

Example:
- PubMed provides: PMID, MeSH terms
- OpenAlex provides: Citations, ROR IDs
- Crossref provides: References

**Result:** Merged record contains all fields, with PubMed taking priority for conflicting fields.

---

## Rate Limiting

### Default Limits (No API Keys)

| Source | Rate Limit | Notes |
|--------|-----------|-------|
| PubMed | 3 req/s | NCBI standard limit |
| OpenAlex | 1 req/s | Standard API |
| Crossref | 1 req/s | Standard API |
| Semantic Scholar | 100 req/5 min | Standard limit |

### With API Keys/Emails

| Source | Rate Limit | Config Key |
|--------|-----------|------------|
| PubMed | 10 req/s | `pubmed_api_key` |
| OpenAlex | 10 req/s | `openalex_email` |
| Crossref | Faster | `crossref_email` |
| Semantic Scholar | Higher | `semantic_scholar_api_key` |

---

## Error Handling

The fetcher is resilient to source failures:

```python
# Example: PubMed fails, but other sources succeed
config = {
    "sources": ["pubmed", "openalex", "crossref"]
}

# If PubMed fails:
# ✓ OpenAlex papers fetched
# ✓ Crossref papers fetched
# ✓ Merging succeeds with available data
# ✗ PubMed error logged but doesn't stop execution
```

---

## Common Use Cases

### 1. Quick Literature Review (PubMed + OpenAlex)

```python
config = {
    "sources": ["pubmed", "openalex"],
    "max_papers": 200,
    "require_abstract": True
}

input_data = {
    "semantic_scholar_query": "your research topic",
    "year_range": {"start": 2022, "end": 2024}
}
```

### 2. Comprehensive Search (All Sources)

```python
config = {
    "sources": ["pubmed", "openalex", "crossref"],
    "max_papers": 1000,
    "pubmed_api_key": "your-key",
    "openalex_email": "you@example.com"
}
```

### 3. PubMed-Only (Medical/Biomedical Research)

```python
config = {
    "sources": ["pubmed"],
    "pubmed_api_key": "your-key"
}

input_data = {
    "semantic_scholar_query": "cancer immunotherapy",
    "pubmed_query": "cancer immunotherapy[MeSH] OR immunotherapy[Title/Abstract]"
}
```

### 4. Citation Network Analysis (OpenAlex + Crossref)

```python
config = {
    "sources": ["openalex", "crossref"],  # Best for citation/reference data
    "max_papers": 500,
    "openalex_email": "you@example.com"
}
```

---

## Tips & Best Practices

### 1. Start Small

```python
# Test with small dataset first
config = {"sources": ["pubmed"], "max_papers": 10}
```

### 2. Use Year Ranges

```python
# Avoid fetching too many papers
input_data = {
    "semantic_scholar_query": "machine learning",
    "year_range": {"start": 2023, "end": 2024}  # Recent papers only
}
```

### 3. Require Abstracts

```python
config = {
    "require_abstract": True  # Essential for topic modeling
}
```

### 4. Monitor Logs

Check logs to see:
- How many papers each source returned
- Deduplication statistics
- Which fields were merged
- Any errors or warnings

### 5. API Keys for Production

For production use, always set API keys to avoid rate limiting:
```python
config = {
    "pubmed_api_key": "...",
    "openalex_email": "...",
    "crossref_email": "..."
}
```

---

## Troubleshooting

### No Papers Found

**Cause:** Query too specific or no results in year range

**Solution:**
1. Broaden query
2. Remove year range
3. Check query syntax for PubMed

### Rate Limited (429 Errors)

**Cause:** Too many requests without API key

**Solution:**
1. Add API keys to config
2. Increase `api_delay` setting
3. Reduce `max_papers`

### Memory Errors

**Cause:** Fetching >5000 papers

**Solution:**
1. Reduce `max_papers`
2. Process in batches
3. Add more memory

### PubMed Returns No Abstracts

**Cause:** Some PubMed records lack abstracts

**Solution:**
1. Set `require_abstract: False`
2. Rely on OpenAlex/Crossref for abstracts
3. Filter later in pipeline

---

## Next Steps

1. **Run verification:** `python verify_upgrade.py`
2. **Test with small dataset:** 10-20 papers
3. **Scale up gradually:** Increase `max_papers`
4. **Add API keys:** For production use
5. **Integrate with pipeline:** Use in automated workflow

---

## Getting Help

- **Documentation:** `PAPER_FETCHER_UPGRADE.md`
- **Completion Report:** `UPGRADE_COMPLETION_REPORT.md`
- **API References:**
  - PubMed: https://www.ncbi.nlm.nih.gov/books/NBK25500/
  - OpenAlex: https://docs.openalex.org/
  - Crossref: https://api.crossref.org
