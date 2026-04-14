# PaperFetcher Multi-Source Upgrade - Implementation Summary

## Overview

The `paper_fetcher.py` module has been successfully upgraded to fetch academic papers from **four data sources**:
1. **PubMed** (E-utilities API) - Highest priority
2. **OpenAlex** (Works API) - Second priority
3. **Crossref** (Works API) - Third priority
4. **Semantic Scholar** (existing implementation) - Fallback

## Key Features

### 1. PubMed E-utilities API Integration

**Flow:**
1. **ESearch** - Search PubMed database, retrieve PMIDs matching query
2. **EFetch** - Batch fetch full XML records (50 PMIDs per batch)
3. **XML Parsing** - Extract structured metadata using `xml.etree.ElementTree`

**Extracted Fields:**
- PMID, DOI, PMCID
- Title, Abstract (with labeled sections)
- Publication Year
- Journal Name, ISSN
- Authors with affiliations
- MeSH Terms
- Keywords
- Document Type

**Rate Limiting:**
- Without API key: 3 requests/second
- With API key: 10 requests/second
- Configurable via `pubmed_api_key` and `pubmed_email`

**Pagination:**
- Uses WebEnv/QueryKey for large result sets
- Respects `pubmed_retmax` (default 200 papers per ESearch call)
- Automatically paginates to fetch up to `max_papers`

**Error Handling:**
- Individual batch failures logged but don't stop entire fetch
- XML parsing errors caught per-article
- Network timeouts handled gracefully

### 2. OpenAlex API Integration

**Features:**
- Cursor-based pagination for large result sets
- Abstract reconstruction from inverted index format
- Author affiliations with ROR IDs and country codes
- Concepts/Fields of Study (top-level only)
- Referenced works (OpenAlex work IDs)
- Citation counts

**Abstract Reconstruction:**
```python
# OpenAlex returns abstracts as {word: [position1, position2, ...]}
# MetadataNormalizer._reconstruct_abstract() sorts by position and joins
```

**Rate Limiting:**
- Default: ~1 request/second
- Polite pool (with `openalex_email`): 10 requests/second

### 3. Crossref API Integration

**Features:**
- Cursor-based pagination
- Reference list extraction (DOIs of cited papers)
- Citation count (`is-referenced-by-count`)
- Journal/ISSN metadata
- Author affiliations
- Subjects (treated as keywords_plus)

**Rate Limiting:**
- Default: ~1 request/second
- Polite pool (with `crossref_email`): faster responses

### 4. Semantic Scholar (Existing)

Kept as fallback source with existing implementation.

## Metadata Normalization & Deduplication

### MetadataNormalizer Integration

All raw records from different APIs are normalized to a unified schema via `MetadataNormalizer`:

```python
from modules.metadata_normalizer import MetadataNormalizer

# Source-specific normalizers
normalizer._normalize_pubmed(raw_xml_dict)
normalizer._normalize_openalex(raw_api_response)
normalizer._normalize_crossref(raw_api_response)
normalizer._normalize_semantic_scholar(raw_api_response)
```

### Deduplication Strategy

**Primary Key:** DOI (lowercased)
**Secondary Key:** PMID (if no DOI)

**Merge Priority:** PubMed > OpenAlex > Crossref > Semantic Scholar

**Merge Rules:**
- **Priority Fields** (first non-null wins): title, abstract, year, journal, DOI, PMID, etc.
- **Merge Fields** (union): MeSH terms, keywords, references, fields_of_study
- **Max Fields** (take maximum): cited_by_count, reference_count
- **Authors** (merge by name): combine affiliations, ORCID, ROR ID, country

### Output Format

**papers.json** - Canonical unified records:
```json
[
  {
    "pmid": "12345678",
    "doi": "10.1234/example.2024",
    "title": "...",
    "abstract": "...",
    "year": 2024,
    "journal_name": "Nature",
    "authors": [
      {
        "name": "John Smith",
        "affiliations": ["MIT", "Harvard"],
        "country": "US",
        "orcid": "0000-0001-2345-6789"
      }
    ],
    "mesh_terms": ["Machine Learning", "Healthcare"],
    "cited_by_count": 42,
    ...
  }
]
```

**papers.csv** - Flat backward-compatible view (TIAB format):
- NUM, TIAB, title, abstract, year, authors, ...
- Suitable for downstream analysis modules (LDA, burst detection, etc.)

## Configuration

### New Config Fields

```yaml
paper_fetcher:
  # API Keys (optional, for higher rate limits)
  pubmed_api_key: null  # 10 req/s instead of 3
  pubmed_email: null
  openalex_email: null  # Polite pool (10 req/s)
  crossref_email: null

  # Source Selection
  sources:
    - pubmed      # Highest priority
    - openalex    # Second priority
    - crossref    # Third priority
    # - semantic_scholar  # Fallback (disabled by default)

  # PubMed-specific
  pubmed_retmax: 200  # Max PMIDs per ESearch call

  # Common
  api_delay: 1.0
  batch_size: 100
  require_abstract: true
```

### Input Schema

```json
{
  "semantic_scholar_query": "machine learning healthcare",
  "pubmed_query": "machine learning healthcare",  // Optional, defaults to semantic_scholar_query
  "max_papers": 1000,
  "year_range": {
    "start": 2020,
    "end": 2024
  }
}
```

## Error Handling

### Source-Level Resilience

Each data source fetch is wrapped in try-except:
- If PubMed fails → Continue with OpenAlex, Crossref, Semantic Scholar
- If OpenAlex fails → Continue with PubMed, Crossref, Semantic Scholar
- etc.

**Result:** Partial data is better than no data.

### Batch-Level Resilience

For PubMed EFetch (batched XML retrieval):
- Each batch of 50 PMIDs fetched independently
- Failed batches logged, other batches succeed
- Maximizes data retrieval even with intermittent errors

### Logging

Comprehensive logging at each stage:
- Source selection
- API call success/failure
- Number of papers fetched per source
- Deduplication statistics
- Abstract filtering results

## Rate Limiting

### Automatic Rate Limiting

```python
# PubMed
if api_key:
    min_delay = 0.11  # ~9 req/s
else:
    min_delay = 0.35  # ~3 req/s

# OpenAlex / Crossref
if email:
    delay = max(api_delay, 0.11)  # Polite pool
else:
    delay = max(api_delay, 0.5)   # Default
```

### Configurable Delays

Users can increase delays via `api_delay` config if they encounter rate limit errors.

## Testing

### Quick Validation

Run test script to verify implementation:

```bash
python test_paper_fetcher_upgrade.py
```

Tests:
- ✓ Import success
- ✓ Config schema completeness
- ✓ Input/output schema validation
- ✓ MetadataNormalizer integration

### Integration Test

```bash
# Requires API keys for full test
export PUBMED_API_KEY="your-key"
export OPENALEX_EMAIL="your-email@example.com"

python test_integration_quick.py
```

## Dependencies

### Required (already in requirements.txt)

- `requests` - HTTP client for all APIs
- `pandas` - DataFrame creation for CSV output
- `xml.etree.ElementTree` - Standard library XML parsing

### Optional

- `lxml` - Faster XML parsing (fallback to ElementTree if not installed)
- `pycountry` - Country name resolution (used by MetadataNormalizer)

**Note:** `pycountry` is used by MetadataNormalizer for country code resolution from affiliation strings. Install with:
```bash
pip install pycountry
```

## Performance Considerations

### Batch Sizes

- **PubMed ESearch:** 200 PMIDs per call (configurable via `pubmed_retmax`)
- **PubMed EFetch:** 50 PMIDs per batch (fixed for optimal XML size)
- **OpenAlex:** 50 papers per page (cursor pagination)
- **Crossref:** 100 papers per page (cursor pagination)

### Parallel Fetching

Current implementation fetches sources **sequentially**:
- PubMed → OpenAlex → Crossref → Semantic Scholar

**Future Enhancement:** Parallel fetching with `asyncio` or `multiprocessing` could reduce total fetch time.

### Memory Usage

- All papers loaded into memory during fetch
- MetadataNormalizer processes in-memory lists
- For very large datasets (>10,000 papers), consider streaming/chunked processing

## API Coverage Comparison

| Field | PubMed | OpenAlex | Crossref | Semantic Scholar |
|-------|--------|----------|----------|------------------|
| PMID | ✓ | ✓ | - | ✓ |
| DOI | ✓ | ✓ | ✓ | ✓ |
| Title | ✓ | ✓ | ✓ | ✓ |
| Abstract | ✓ | ✓ (inverted) | ✓ | ✓ |
| Authors | ✓ | ✓ | ✓ | ✓ |
| Affiliations | ✓ | ✓ | ✓ | - |
| Country | Regex | ✓ (ROR) | Regex | - |
| Journal | ✓ | ✓ | ✓ | ✓ |
| MeSH Terms | ✓ | - | - | - |
| Keywords | ✓ | - | Subjects | - |
| Citations | - | ✓ | ✓ | ✓ |
| References | - | Work IDs | DOIs | - |
| Fields of Study | - | ✓ | Subjects | ✓ |

## Migration Notes

### Backward Compatibility

**Input Schema:**
- Old: `query` field
- New: `semantic_scholar_query` field (required), `pubmed_query` (optional)

**Output Format:**
- Unchanged: `papers.json` and `papers.csv` formats remain compatible
- New fields added: `pmid`, `pmcid`, `mesh_terms`, etc.

### Configuration Changes

**Old Config:**
```yaml
paper_fetcher:
  semantic_scholar_api_key: "..."
  max_papers: 1000
```

**New Config:**
```yaml
paper_fetcher:
  semantic_scholar_api_key: "..."
  pubmed_api_key: "..."
  openalex_email: "..."
  crossref_email: "..."
  sources: ["pubmed", "openalex", "crossref"]
  max_papers: 1000
```

**Note:** Old configs will still work (Semantic Scholar only mode).

## Future Enhancements

1. **Parallel Fetching:** Use `asyncio.aiohttp` for concurrent API calls
2. **Caching:** Cache API responses to avoid re-fetching on resume
3. **Provenance Tracking:** Record which source provided each field
4. **Quality Scoring:** Score papers by metadata completeness
5. **arXiv Integration:** Add preprint server support
6. **Full Text Links:** Extract PDF links from Unpaywall/Crossref

## Troubleshooting

### PubMed Returns No Results

**Cause:** Query too specific or year range mismatch

**Solution:** Try broader query, check year filter syntax

### OpenAlex Abstract Missing

**Cause:** OpenAlex uses inverted index, may be empty for recent papers

**Solution:** Check `abstract_inverted_index` field, rely on PubMed/Crossref for abstracts

### Crossref Rate Limited

**Cause:** Missing `crossref_email` in config

**Solution:** Add email to config for polite pool access

### Memory Errors

**Cause:** Fetching >5000 papers in one run

**Solution:** Reduce `max_papers` or process in batches

## References

- PubMed E-utilities: https://www.ncbi.nlm.nih.gov/books/NBK25500/
- OpenAlex API: https://docs.openalex.org/
- Crossref API: https://api.crossref.org
- Semantic Scholar API: https://api.semanticscholar.org/

## Changelog

**v2.0.0** (2026-04-14)
- Added PubMed E-utilities API support (ESearch + EFetch)
- Added OpenAlex Works API support (cursor pagination)
- Added Crossref Works API support (cursor pagination)
- Integrated MetadataNormalizer for multi-source merging
- Added configurable source selection
- Enhanced error handling and rate limiting
- Updated config schema with new API fields
- Maintained backward compatibility with Semantic Scholar
