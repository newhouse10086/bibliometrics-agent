"""Paper Fetcher module — multi-source academic paper acquisition.

Primary source: PubMed (E-utilities: ESearch + EFetch with XML parsing)
Supplementary:  OpenAlex (cursor pagination, abstract inverted index)
                Crossref (cursor pagination, citation/reference data)
Fallback:       Semantic Scholar (existing implementation)

All records are normalized via MetadataNormalizer into the unified schema,
deduplicated by DOI/PMID, and output as both papers.json (canonical) and
papers.csv (backward-compatible flat view).
"""

from __future__ import annotations

import json
import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional

import requests

from modules.base import BaseModule, HardwareSpec, RunContext
from modules.metadata_normalizer import MetadataNormalizer

logger = logging.getLogger(__name__)

# PubMed E-utilities base URLs
_PUBMED_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_PUBMED_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# OpenAlex API
_OPENALEX_WORKS_URL = "https://api.openalex.org/works"

# Crossref API
_CROSSREF_WORKS_URL = "https://api.crossref.org/works"


class PaperFetcher(BaseModule):
    """Fetch papers from PubMed, OpenAlex, Crossref, and Semantic Scholar."""

    @property
    def name(self) -> str:
        return "paper_fetcher"

    @property
    def version(self) -> str:
        return "2.0.0"

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "required": ["semantic_scholar_query"],
            "properties": {
                "semantic_scholar_query": {
                    "type": "string",
                    "description": "Search query string"
                },
                "pubmed_query": {
                    "type": "string",
                    "description": "PubMed search query (optional, defaults to semantic_scholar_query)"
                },
                "max_papers": {
                    "type": "integer",
                    "default": 1000,
                    "description": "Maximum number of papers to fetch"
                },
                "year_range": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "integer"},
                        "end": {"type": "integer"}
                    }
                }
            }
        }

    def output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "papers_json_path": {"type": "string", "description": "Path to papers.json (canonical unified records)"},
                "papers_csv_path": {"type": "string", "description": "Path to papers.csv (flat backward-compatible view)"},
                "num_papers": {"type": "integer"},
                "fields_covered": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "year_distribution": {
                    "type": "object",
                    "description": "Dict of {year: count}"
                }
            }
        }

    def config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "api_delay": {
                    "type": "number",
                    "default": 1.0,
                    "description": "Delay between API calls (seconds)"
                },
                "batch_size": {
                    "type": "integer",
                    "default": 100,
                    "description": "Papers per API call"
                },
                "require_abstract": {
                    "type": "boolean",
                    "default": True,
                    "description": "Only fetch papers with abstracts"
                },
                "semantic_scholar_api_key": {
                    "type": "string",
                    "description": "Optional API key for higher SS rate limits"
                },
                "pubmed_api_key": {
                    "type": "string",
                    "description": "Optional NCBI API key (10 req/s instead of 3)"
                },
                "pubmed_email": {
                    "type": "string",
                    "description": "Email for NCBI API identification"
                },
                "openalex_email": {
                    "type": "string",
                    "description": "Email for OpenAlex polite pool (10 req/s)"
                },
                "crossref_email": {
                    "type": "string",
                    "description": "Email for Crossref polite pool"
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["pubmed", "openalex", "crossref", "semantic_scholar"]},
                    "default": ["pubmed", "openalex", "crossref"],
                    "description": "Data sources to query (in order of priority)"
                },
                "pubmed_retmax": {
                    "type": "integer",
                    "default": 200,
                    "description": "Max papers per PubMed ESearch call"
                }
            }
        }

    def get_hardware_requirements(self, config: dict) -> HardwareSpec:
        max_papers = config.get("max_papers", 1000)
        return HardwareSpec(
            min_memory_gb=0.5,
            recommended_memory_gb=1.0,
            cpu_cores=1,
            estimated_runtime_seconds=30 + max_papers * 0.05
        )

    def __init__(self):
        self._normalizer = MetadataNormalizer()

    def process(self, input_data: dict, config: dict, context: RunContext) -> dict:
        """Fetch papers from multiple academic APIs and merge into unified records."""
        import pandas as pd

        query = input_data["semantic_scholar_query"]
        pubmed_query = input_data.get("pubmed_query", query)
        max_papers = input_data.get("max_papers", 1000)
        year_range = input_data.get("year_range", {})

        # Which sources to use
        sources = config.get("sources", ["pubmed", "openalex", "crossref"])

        logger.info("Fetching papers for query: %s (max: %d, sources: %s)", query, max_papers, sources)

        # Fetch from each source
        paper_lists: dict[str, list[dict]] = {}

        if "pubmed" in sources:
            logger.info("Fetching from PubMed...")
            pubmed_papers = self._fetch_pubmed(pubmed_query, max_papers, year_range, config)
            paper_lists["pubmed"] = pubmed_papers
            logger.info("PubMed: %d raw papers", len(pubmed_papers))

        if "openalex" in sources:
            logger.info("Fetching from OpenAlex...")
            openalex_papers = self._fetch_openalex(query, max_papers, year_range, config)
            paper_lists["openalex"] = openalex_papers
            logger.info("OpenAlex: %d raw papers", len(openalex_papers))

        if "crossref" in sources:
            logger.info("Fetching from Crossref...")
            crossref_papers = self._fetch_crossref(query, max_papers, year_range, config)
            paper_lists["crossref"] = crossref_papers
            logger.info("Crossref: %d raw papers", len(crossref_papers))

        if "semantic_scholar" in sources:
            logger.info("Fetching from Semantic Scholar...")
            ss_papers = self._fetch_semantic_scholar(query, max_papers, year_range, config)
            paper_lists["semantic_scholar"] = ss_papers
            logger.info("Semantic Scholar: %d raw papers", len(ss_papers))

        # Merge and deduplicate
        merged_papers = self._normalizer.merge_paper_lists(paper_lists)
        logger.info("After deduplication: %d unique papers", len(merged_papers))

        # Filter by abstract requirement
        require_abstract = config.get("require_abstract", True)
        if require_abstract:
            before = len(merged_papers)
            merged_papers = [p for p in merged_papers if p.get("abstract")]
            logger.info("Filtered %d papers without abstracts (%d → %d)",
                        before - len(merged_papers), before, len(merged_papers))

        # Limit to max_papers
        merged_papers = merged_papers[:max_papers]

        # Save outputs
        output_dir = context.get_output_path(self.name, "")
        output_dir.mkdir(parents=True, exist_ok=True)

        # papers.json — canonical unified records
        papers_json_path = output_dir / "papers.json"
        with open(papers_json_path, "w", encoding="utf-8") as f:
            json.dump(merged_papers, f, ensure_ascii=False, indent=2)

        # papers.csv — backward-compatible flat view
        papers_csv_path = output_dir / "papers.csv"
        if merged_papers:
            flat_rows = [self._normalizer.to_flat_dict(p, index=i) for i, p in enumerate(merged_papers)]
            df = pd.DataFrame(flat_rows)
            df.to_csv(papers_csv_path, index=False, encoding="utf-8")

            # Compute statistics
            year_dist = {}
            for p in merged_papers:
                y = p.get("year")
                if y:
                    year_dist[y] = year_dist.get(y, 0) + 1

            fields_set = set()
            for p in merged_papers:
                for f in (p.get("fields_of_study") or []):
                    fields_set.add(f)
            fields = list(fields_set)
        else:
            pd.DataFrame(columns=["NUM", "TIAB", "title", "abstract", "year", "authors"]).to_csv(
                papers_csv_path, index=False
            )
            year_dist = {}
            fields = []

        logger.info("Saved %d papers to %s", len(merged_papers), output_dir)

        return {
            "papers_json_path": str(papers_json_path),
            "papers_csv_path": str(papers_csv_path),
            "num_papers": len(merged_papers),
            "fields_covered": fields[:10],
            "year_distribution": year_dist,
        }

    # ------------------------------------------------------------------
    # PubMed E-utilities
    # ------------------------------------------------------------------

    def _fetch_pubmed(
        self,
        query: str,
        max_papers: int,
        year_range: dict,
        config: dict,
    ) -> list[dict]:
        """Fetch papers from PubMed using ESearch + EFetch.

        Flow:
        1. ESearch: get list of PMIDs matching query
        2. EFetch: fetch XML for batches of PMIDs
        3. Parse XML to extract structured metadata
        """
        api_key = config.get("pubmed_api_key") or ""
        email = config.get("pubmed_email") or ""
        retmax = config.get("pubmed_retmax", 200)
        api_delay = config.get("api_delay", 1.0)

        # Rate limit: 3/s without key, 10/s with key
        if api_key:
            min_delay = 0.11  # ~9/s
        else:
            min_delay = 0.35  # ~3/s
        actual_delay = max(api_delay, min_delay)

        # Step 1: ESearch — get PMIDs
        pmids = self._pubmed_esearch(query, max_papers, year_range, api_key, email, retmax, actual_delay)
        if not pmids:
            logger.warning("PubMed ESearch returned no PMIDs for query: %s", query)
            return []

        logger.info("PubMed ESearch returned %d PMIDs", len(pmids))

        # Step 2: EFetch — get full records in batches of 50
        batch_size = 50
        raw_papers = []

        for i in range(0, len(pmids), batch_size):
            batch_pmids = pmids[i:i + batch_size]
            logger.info("PubMed EFetch batch %d/%d (%d PMIDs)...",
                        i // batch_size + 1, (len(pmids) + batch_size - 1) // batch_size,
                        len(batch_pmids))

            try:
                batch_papers = self._pubmed_efetch(batch_pmids, api_key, email, actual_delay)
                raw_papers.extend(batch_papers)
            except Exception as e:
                logger.error("PubMed EFetch batch failed: %s", e)

            if i + batch_size < len(pmids):
                time.sleep(actual_delay)

        return raw_papers

    def _pubmed_esearch(
        self,
        query: str,
        max_papers: int,
        year_range: dict,
        api_key: str,
        email: str,
        retmax: int,
        delay: float,
    ) -> list[str]:
        """Run PubMed ESearch to get PMIDs."""
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": min(retmax, max_papers),
            "retmode": "json",
            "usehistory": "y",
        }

        if api_key:
            params["api_key"] = api_key
        if email:
            params["email"] = email

        # Add year filter
        if year_range:
            year_filters = []
            if "start" in year_range:
                year_filters.append(f"{year_range['start']}:{year_range.get('end', 3000)}[pdat]")
            if not year_filters and "end" in year_range:
                year_filters.append(f"0:{year_range['end']}[pdat]")
            if year_filters:
                params["term"] = f"{query} AND {' AND '.join(year_filters)}"

        try:
            response = requests.get(_PUBMED_ESEARCH_URL, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            result = data.get("esearchresult", {})
            pmids = result.get("idlist", [])
            total_count = int(result.get("count", 0))

            logger.info("PubMed ESearch: %d total results, fetched %d PMIDs", total_count, len(pmids))

            # If there are more results and we need them, use WebEnv for pagination
            if total_count > retmax and len(pmids) < max_papers:
                webenv = result.get("webenv")
                query_key = result.get("querykey")
                if webenv and query_key:
                    remaining = min(max_papers, total_count) - len(pmids)
                    offset = len(pmids)
                    while remaining > 0:
                        batch = min(retmax, remaining)
                        time.sleep(delay)
                        params2 = {
                            "db": "pubmed",
                            "query_key": query_key,
                            "WebEnv": webenv,
                            "retstart": offset,
                            "retmax": batch,
                            "retmode": "json",
                        }
                        if api_key:
                            params2["api_key"] = api_key

                        r2 = requests.get(_PUBMED_ESEARCH_URL, params=params2, timeout=30)
                        r2.raise_for_status()
                        d2 = r2.json().get("esearchresult", {})
                        new_pmids = d2.get("idlist", [])
                        if not new_pmids:
                            break
                        pmids.extend(new_pmids)
                        offset += len(new_pmids)
                        remaining -= len(new_pmids)

            return pmids

        except requests.exceptions.RequestException as e:
            logger.error("PubMed ESearch failed: %s", e)
            return []

    def _pubmed_efetch(
        self,
        pmids: list[str],
        api_key: str,
        email: str,
        delay: float,
    ) -> list[dict]:
        """Fetch and parse PubMed XML for a batch of PMIDs."""
        params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
            "rettype": "abstract",
        }
        if api_key:
            params["api_key"] = api_key
        if email:
            params["email"] = email

        response = requests.get(_PUBMED_EFETCH_URL, params=params, timeout=60)
        response.raise_for_status()

        # Parse XML
        root = ET.fromstring(response.text)
        papers = []

        for article in root.findall(".//PubmedArticle"):
            try:
                paper = self._parse_pubmed_article(article)
                if paper:
                    papers.append(paper)
            except Exception as e:
                pmid = article.findtext(".//PMID", "")
                logger.warning("Failed to parse PubMed article (PMID=%s): %s", pmid, e)

        return papers

    def _parse_pubmed_article(self, article: ET.Element) -> dict:
        """Parse a single PubmedArticle XML element into raw dict for normalizer."""
        medline = article.find("MedlineCitation")
        if medline is None:
            return None

        # PMID
        pmid_el = medline.find("PMID")
        pmid = pmid_el.text if pmid_el is not None else ""

        # Article
        art = medline.find("Article")
        if art is None:
            return None

        # Title
        title_el = art.find("ArticleTitle")
        title = "".join(title_el.itertext()) if title_el is not None else ""

        # Abstract
        abstract_parts = []
        abstract_el = art.find("Abstract")
        if abstract_el is not None:
            for abs_text in abstract_el.findall("AbstractText"):
                label = abs_text.get("Label", "")
                text = "".join(abs_text.itertext())
                if label:
                    abstract_parts.append(f"{label}: {text}")
                else:
                    abstract_parts.append(text)
        abstract = " ".join(abstract_parts)

        # Year
        year = None
        journal_el = art.find("Journal")
        if journal_el is not None:
            pub_date = journal_el.find("JournalIssue/PubDate")
            if pub_date is None:
                pub_date = journal_el.find(".//PubDate")
            if pub_date is not None:
                year_el = pub_date.find("Year")
                if year_el is not None and year_el.text:
                    try:
                        year = int(year_el.text)
                    except ValueError:
                        pass
                elif pub_date.find("MedlineDate") is not None:
                    # MedlineDate format: "2019 Jan-Feb" or "2019 Winter"
                    md_text = pub_date.find("MedlineDate").text or ""
                    try:
                        year = int(md_text.split()[0])
                    except (ValueError, IndexError):
                        pass

        # Journal
        journal_name = ""
        journal_issn = ""
        if journal_el is not None:
            title_el = journal_el.find("Title")
            if title_el is not None:
                journal_name = title_el.text or ""
            issn_el = journal_el.find("ISSN")
            if issn_el is not None:
                journal_issn = issn_el.text or ""

        # Authors with affiliations
        authors = []
        author_list = art.find("AuthorList")
        if author_list is not None:
            for author_el in author_list.findall("Author"):
                last = author_el.findtext("LastName", "")
                fore = author_el.findtext("ForeName", "")
                name = f"{fore} {last}".strip() if fore else last

                affiliations = []
                for aff in author_el.findall(".//AffiliationInfo/Affiliation"):
                    if aff.text:
                        affiliations.append(aff.text.strip())

                author = {"name": name, "affiliations": affiliations}
                authors.append(author)

        # MeSH terms
        mesh_terms = []
        mesh_list = medline.find("MeshHeadingList")
        if mesh_list is not None:
            for heading in mesh_list.findall("MeshHeading"):
                descriptor = heading.find("DescriptorName")
                if descriptor is not None and descriptor.text:
                    mesh_terms.append(descriptor.text)

        # Keywords
        keywords = []
        keyword_list = medline.find("KeywordList")
        if keyword_list is not None:
            for kw in keyword_list.findall("Keyword"):
                if kw.text:
                    keywords.append(kw.text)

        # DOI and PMCID from ArticleIdList
        doi = ""
        pmcid = ""
        pubmed_data = article.find("PubmedData")
        if pubmed_data is not None:
            id_list = pubmed_data.find("ArticleIdList")
            if id_list is not None:
                for art_id in id_list.findall("ArticleId"):
                    id_type = art_id.get("IdType", "")
                    if id_type == "doi" and art_id.text:
                        doi = art_id.text
                    elif id_type == "pmc" and art_id.text:
                        pmcid = art_id.text

        # Document type
        doc_type = ""
        pub_types = art.find("PublicationTypeList")
        if pub_types is not None:
            pt = pub_types.find("PublicationType")
            if pt is not None and pt.text:
                doc_type = pt.text

        return {
            "pmid": pmid,
            "doi": doi,
            "pmcid": pmcid,
            "title": title,
            "abstract": abstract,
            "year": year,
            "journal_name": journal_name,
            "journal_issn": journal_issn,
            "authors": authors,
            "mesh_terms": mesh_terms,
            "keywords": keywords,
            "author_keywords": [],  # PubMed doesn't have author keywords separately
            "document_type": doc_type,
        }

    # ------------------------------------------------------------------
    # OpenAlex
    # ------------------------------------------------------------------

    def _fetch_openalex(
        self,
        query: str,
        max_papers: int,
        year_range: dict,
        config: dict,
    ) -> list[dict]:
        """Fetch papers from OpenAlex Works API with cursor pagination."""
        email = config.get("openalex_email") or ""
        api_delay = config.get("api_delay", 1.0)

        params = {
            "search": query,
            "per_page": 50,
            "cursor": "*",
            "select": "id,doi,title,abstract_inverted_index,publication_year,"
                      "primary_location,authorships,concepts,referenced_works,"
                      "cited_by_count,referenced_works_count,type,ids",
        }

        if email:
            params["mailto"] = email

        # Add year filter
        if year_range:
            filters = []
            if "start" in year_range:
                filters.append(f"publication_year>{year_range['start']-1}")
            if "end" in year_range:
                filters.append(f"publication_year<{year_range['end']+1}")
            if filters:
                params["filter"] = ",".join(filters)

        headers = {}
        if email:
            headers["User-Agent"] = f"BibliometricsAgent/2.0 (mailto:{email})"

        all_papers = []
        delay = max(api_delay, 0.11) if email else max(api_delay, 0.5)

        while len(all_papers) < max_papers:
            try:
                logger.info("OpenAlex: fetching page (cursor=%s)...", params.get("cursor", "*")[:20])
                response = requests.get(_OPENALEX_WORKS_URL, params=params, headers=headers, timeout=30)
                response.raise_for_status()

                data = response.json()
                results = data.get("results", [])

                if not results:
                    break

                all_papers.extend(results)

                # Update cursor for next page
                next_cursor = data.get("meta", {}).get("next_cursor")
                if not next_cursor:
                    break
                params["cursor"] = next_cursor

                time.sleep(delay)

            except requests.exceptions.RequestException as e:
                logger.error("OpenAlex request failed: %s", e)
                break

        return all_papers[:max_papers]

    # ------------------------------------------------------------------
    # Crossref
    # ------------------------------------------------------------------

    def _fetch_crossref(
        self,
        query: str,
        max_papers: int,
        year_range: dict,
        config: dict,
    ) -> list[dict]:
        """Fetch papers from Crossref Works API with cursor pagination."""
        email = config.get("crossref_email") or ""
        api_delay = config.get("api_delay", 1.0)

        params = {
            "query": query,
            "rows": 100,
            "cursor": "*",
            "select": "DOI,title,abstract,published,published-print,published-online,"
                      "container-title,ISSN,author,reference,is-referenced-by-count,"
                      "references-count,subject,type",
        }

        if year_range:
            filters = []
            if "start" in year_range:
                filters.append(f"from-pub-date:{year_range['start']}")
            if "end" in year_range:
                filters.append(f"until-pub-date:{year_range['end']}")
            if filters:
                params["filter"] = ",".join(filters)

        headers = {}
        if email:
            headers["User-Agent"] = f"BibliometricsAgent/2.0 (mailto:{email})"

        all_papers = []
        delay = max(api_delay, 0.11) if email else max(api_delay, 0.5)

        while len(all_papers) < max_papers:
            try:
                logger.info("Crossref: fetching page (cursor=%s)...", params.get("cursor", "*")[:20])
                response = requests.get(_CROSSREF_WORKS_URL, params=params, headers=headers, timeout=30)
                response.raise_for_status()

                data = response.json()
                message = data.get("message", {})
                items = message.get("items", [])

                if not items:
                    break

                all_papers.extend(items)

                # Update cursor
                next_cursor = message.get("next-cursor")
                if not next_cursor:
                    break
                params["cursor"] = next_cursor

                time.sleep(delay)

            except requests.exceptions.RequestException as e:
                logger.error("Crossref request failed: %s", e)
                break

        return all_papers[:max_papers]

    # ------------------------------------------------------------------
    # Semantic Scholar (existing, kept as fallback)
    # ------------------------------------------------------------------

    def _fetch_semantic_scholar(
        self,
        query: str,
        max_papers: int,
        year_range: dict,
        config: dict,
    ) -> list[dict]:
        """Fetch papers from Semantic Scholar API (fallback source)."""
        api_key = config.get("semantic_scholar_api_key")
        batch_size = config.get("batch_size", 100)
        api_delay = config.get("api_delay", 1.0)
        require_abstract = config.get("require_abstract", True)

        base_url = "https://api.semanticscholar.org/graph/v1/paper/search"

        headers = {}
        if api_key:
            headers["x-api-key"] = api_key

        all_papers = []
        offset = 0

        while len(all_papers) < max_papers:
            params = {
                "query": query,
                "limit": min(batch_size, max_papers - len(all_papers)),
                "offset": offset,
                "fields": "title,abstract,year,authors,fieldsOfStudy,url,externalIds,"
                          "citationCount,referenceCount,journal"
            }

            if year_range:
                year_filters = []
                if "start" in year_range:
                    year_filters.append(f"year>={year_range['start']}")
                if "end" in year_range:
                    year_filters.append(f"year<={year_range['end']}")
                if year_filters:
                    params["year"] = ",".join(year_filters)

            try:
                logger.info("Semantic Scholar: fetching offset %d...", offset)
                response = requests.get(base_url, params=params, headers=headers, timeout=30)
                response.raise_for_status()

                data = response.json()
                papers_batch = data.get("data", [])

                if not papers_batch:
                    break

                all_papers.extend(papers_batch)

                if len(papers_batch) < batch_size:
                    break

                offset += len(papers_batch)
                time.sleep(api_delay)

            except requests.exceptions.RequestException as e:
                logger.error("Semantic Scholar request failed: %s", e)
                break

        return all_papers[:max_papers]
