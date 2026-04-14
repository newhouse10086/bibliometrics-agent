# PubMed Fetcher - Quick Reference

## Installation

```bash
# Already included in pyproject.toml
pip install -e .
```

## Basic Usage

```python
from pathlib import Path
from modules.pubmed_fetcher import PubMedFetcher
from modules.base import RunContext

# Initialize
fetcher = PubMedFetcher()

# Setup context
context = RunContext(
    project_dir=Path("output"),
    run_id="001",
    checkpoint_dir=Path("output/checkpoints")
)

# Fetch papers
result = fetcher.process(
    input_data={"query": "your search query"},
    config={"max_papers": 100},
    context=context
)

print(f"Fetched {result['paper_count']} papers")
# Output files:
#   papers.csv: result['papers_csv_path']
#   papers.json: result['papers_json_path']
```

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_papers` | 100 | Maximum papers to fetch |
| `pubmed_api_key` | None | NCBI API key (10 req/s vs 3 req/s) |
| `pubmed_email` | None | Your email for NCBI identification |
| `batch_size` | 50 | PMIDs per EFetch call |
| `retmax` | 200 | PMIDs per ESearch call |

## Input Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `query` | ✓ Yes | PubMed search query |
| `max_papers` | No | Max papers (overrides config) |
| `year_range` | No | Filter: `{"start": 2020, "end": 2024}` |

## Output Files

### papers.csv
Flat format compatible with downstream modules:
```
NUM,TIAB,title,abstract,year,authors,author_countries,journal_name,doi,pmid,mesh_terms,keywords,...
```

### papers.json
Extended nested format:
```json
[{
  "pmid": "12345678",
  "title": "...",
  "authors": [{"name": "...", "affiliations": [...], "country": "US"}],
  "journal": {"name": "...", "issn": "..."},
  "mesh_terms": [...],
  ...
}]
```

## Rate Limits

- **Without API key**: 3 requests/second
- **With API key**: 10 requests/second

**Get API key**: https://www.ncbi.nlm.nih.gov/account/ → Settings → API Key

## Examples

### With Year Filter
```python
result = fetcher.process(
    input_data={
        "query": "cancer immunotherapy",
        "year_range": {"start": 2020, "end": 2024}
    },
    config={"max_papers": 200}
)
```

### With API Key
```python
result = fetcher.process(
    input_data={"query": "machine learning"},
    config={
        "max_papers": 500,
        "pubmed_api_key": "your-key-here"
    }
)
```

### Large Dataset (>10K results)
```python
# Automatically uses WebEnv pagination
result = fetcher.process(
    input_data={
        "query": "cancer",
        "max_papers": 5000
    }
)
```

## Test

```bash
# Quick validation
python test_pubmed_fetcher.py
```

## Integration with Pipeline

Use as replacement for `paper_fetcher`:
```yaml
modules:
  - name: pubmed_fetcher  # Instead of paper_fetcher
    config:
      max_papers: 500
```

## Troubleshooting

**"PubMed ESearch failed"**
- Check internet connection
- Try smaller `max_papers`
- Add `pubmed_api_key` for higher limits

**Empty results (0 papers)**
- Broaden query terms
- Check `year_range` filter
- Test query on pubmed.ncbi.nlm.nih.gov

**Slow performance**
- Add API key (3x faster)
- Reduce `max_papers`
- Expected: ~15-45 seconds for 100-1000 papers

## Full Documentation

See `docs/pubmed_fetcher.md` for complete API reference and advanced usage.
