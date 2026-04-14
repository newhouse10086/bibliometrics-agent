# PubMed Fetcher Module

## Overview

`pubmed_fetcher.py` is an **optional standalone module** that fetches academic papers from PubMed using the NCBI E-utilities API. It provides a focused PubMed-only alternative to the main `paper_fetcher` module (which uses multiple sources).

## Key Features

- **ESearch + EFetch Workflow**: Searches PubMed for PMIDs, then fetches full paper details
- **Rate Limiting**: Respects NCBI limits (3 req/s without API key, 10 req/s with key)
- **XML Parsing**: Uses lxml (if available) for faster parsing, falls back to stdlib ElementTree
- **Country Resolution**: Extracts country from author affiliations using pycountry
- **Compatible Output**: Generates papers.csv and papers.json compatible with downstream modules

## Usage

### As a Module

```python
from pathlib import Path
from modules.pubmed_fetcher import PubMedFetcher
from modules.base import RunContext

# Create fetcher instance
fetcher = PubMedFetcher()

# Create context
context = RunContext(
    project_dir=Path("my_project"),
    run_id="run_001",
    checkpoint_dir=Path("my_project/checkpoints"),
    hardware_info={},
    previous_outputs={}
)

# Fetch papers
result = fetcher.process(
    input_data={
        "query": "machine learning healthcare",
        "max_papers": 100
    },
    config={
        "max_papers": 100,
        "pubmed_api_key": "your-api-key",  # Optional
        "pubmed_email": "your@email.com"   # Optional
    },
    context=context
)

print(f"Fetched {result['paper_count']} papers")
print(f"CSV: {result['papers_csv_path']}")
print(f"JSON: {result['papers_json_path']}")
```

### Configuration Options

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_papers` | int | 100 | Maximum number of papers to fetch |
| `pubmed_api_key` | string | None | NCBI API key for higher rate limits |
| `pubmed_email` | string | None | Email for NCBI identification |
| `batch_size` | int | 50 | PMIDs per EFetch call (max 200) |
| `retmax` | int | 200 | PMIDs per ESearch call |

### Input Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | Yes | PubMed search query |
| `max_papers` | int | No | Override config max_papers |
| `year_range` | dict | No | Filter by year: `{"start": 2020, "end": 2024}` |

### Output Format

#### papers.csv (Flat View)

```
NUM,TIAB,title,abstract,year,authors,author_countries,author_affiliations,journal_name,journal_issn,doi,pmid,pmcid,mesh_terms,keywords,url,document_type
1,"Title Abstract text...",Title,Abstract,2024,"Author1; Author2",USA,"Affiliation 1; Affiliation 2",Journal Name,1234-5678,10.1234/...,12345678,PMC1234567,"MeSH1; MeSH2","keyword1; keyword2",https://pubmed.ncbi.nlm.nih.gov/12345678/,Journal Article
```

#### papers.json (Extended Nested Structure)

```json
[
  {
    "pmid": "12345678",
    "doi": "10.1234/example",
    "pmcid": "PMC1234567",
    "title": "Paper Title",
    "abstract": "Abstract text...",
    "year": 2024,
    "authors": [
      {
        "name": "John Doe",
        "affiliations": ["Harvard University, Boston, USA"],
        "country": "US"
      }
    ],
    "journal": {
      "name": "Nature",
      "issn": "0028-0836"
    },
    "mesh_terms": ["Immunotherapy", "Neoplasms"],
    "keywords": ["cancer", "treatment"],
    "url": "https://pubmed.ncbi.nlm.nih.gov/12345678/",
    "document_type": "Journal Article"
  }
]
```

## Rate Limits

NCBI E-utilities has rate limits:

- **Without API key**: 3 requests per second
- **With API key**: 10 requests per second

### Getting an API Key

1. Create an NCBI account at https://www.ncbi.nlm.nih.gov/account/
2. Go to Settings → API Key Management
3. Create an API key
4. Pass the key to the module:

```python
config = {
    "pubmed_api_key": "your-api-key-here"
}
```

## Testing

Run the test script to validate the module:

```bash
python test_pubmed_fetcher.py
```

This will:
1. Validate module instantiation and schemas
2. Test with a small PubMed query (5 papers)

**Note**: The test makes real API calls and requires an internet connection.

## Dependencies

The module uses these packages (already in `pyproject.toml`):

- `requests` - HTTP requests to E-utilities API
- `pycountry>=24.6` - Country code resolution
- `lxml>=5.0` - Faster XML parsing (optional, falls back to stdlib)
- `pandas` - CSV output

## Differences from paper_fetcher

| Feature | pubmed_fetcher | paper_fetcher |
|---------|---------------|---------------|
| Data Sources | PubMed only | PubMed, OpenAlex, Crossref, Semantic Scholar |
| Complexity | Simple, focused | Multi-source merge/dedup |
| Use Case | PubMed-specific projects | General-purpose literature review |
| Output Format | Same | Same |
| Dependencies | lxml (optional) | lxml (optional) |

## Integration with Pipeline

The module can be used as a drop-in replacement for `paper_fetcher` in the pipeline:

```yaml
# In pipeline configuration
modules:
  - name: pubmed_fetcher
    config:
      max_papers: 500
      pubmed_api_key: ${PUBMED_API_KEY}
```

Downstream modules (preprocessor, topic_modeler, etc.) work with either fetcher since both produce compatible papers.csv/papers.json files.

## Troubleshooting

### "PubMed ESearch failed: ..."

**Cause**: Network error or rate limiting

**Solution**:
- Check internet connection
- Wait and retry
- Add `pubmed_api_key` for higher rate limits
- Reduce `max_papers` to avoid long queries

### "Failed to parse PubMed article: ..."

**Cause**: Unexpected XML structure

**Solution**:
- Check PubMed API status
- Report issue with specific PMID
- Module will skip malformed articles and continue

### Empty results (0 papers)

**Cause**: Query returned no matches

**Solution**:
- Try broader query terms
- Check year_range filter
- Test query directly on pubmed.ncbi.nlm.nih.gov

## Advanced Usage

### Year Range Filtering

```python
result = fetcher.process(
    input_data={
        "query": "artificial intelligence",
        "max_papers": 100,
        "year_range": {"start": 2020, "end": 2024}
    },
    config={},
    context=context
)
```

### Large Datasets (WebEnv Pagination)

For queries returning >10,000 results, the module automatically uses WebEnv-based pagination:

```python
# Fetch up to 5000 papers (will use multiple ESearch calls)
result = fetcher.process(
    input_data={
        "query": "cancer",
        "max_papers": 5000
    },
    config={
        "retmax": 200  # 200 PMIDs per ESearch call
    },
    context=context
)
```

### Custom Batch Size

Adjust batch size for EFetch (larger batches are faster but may timeout):

```python
config = {
    "batch_size": 100  # Fetch 100 PMIDs per EFetch call
}
```

## API Reference

### PubMedFetcher Class

```python
class PubMedFetcher(BaseModule):
    @property
    def name(self) -> str
    @property
    def version(self) -> str

    def input_schema(self) -> dict
    def output_schema(self) -> dict
    def config_schema(self) -> dict
    def get_hardware_requirements(self, config: dict) -> HardwareSpec

    def process(self, input_data: dict, config: dict, context: RunContext) -> dict
```

### Private Methods

- `_search_pubmed()` - ESearch API call to get PMIDs
- `_fetch_papers()` - EFetch API calls to get paper details
- `_fetch_batch()` - Single batch of EFetch
- `_parse_pubmed_article()` - Parse XML to dict
- `_resolve_country()` - Extract country from affiliation
- `_save_papers()` - Save to CSV and JSON
- `_paper_to_flat_dict()` - Convert nested dict to flat CSV row

## License

Part of the bibliometrics-agent project. See main LICENSE file.
