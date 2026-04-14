"""Metadata Normalizer — multi-source record normalization and merging.

Standalone utility class (NOT a BaseModule). Called inside paper_fetcher.process()
to normalize records from different APIs (PubMed, OpenAlex, Crossref, Semantic Scholar)
into a unified schema, then merge/deduplicate them.

Merge priority: PubMed > OpenAlex > Crossref > Semantic Scholar
Country resolution: ROR country_code > affiliation regex > pycountry fallback
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ISO 3166-1 alpha-2 country codes — common mapping for affiliation parsing
_COUNTRY_MAP: dict[str, str] = {
    # English names
    "united states": "US", "united states of america": "US", "usa": "US", "u.s.a.": "US",
    "united kingdom": "GB", "uk": "GB", "u.k.": "GB", "england": "GB",
    "scotland": "GB", "wales": "GB", "northern ireland": "GB",
    "china": "CN", "people's republic of china": "CN", "prc": "CN",
    "japan": "JP", "germany": "DE", "france": "FR", "italy": "IT",
    "spain": "ES", "canada": "CA", "australia": "AU", "brazil": "BR",
    "india": "IN", "south korea": "KR", "korea": "KR", "republic of korea": "KR",
    "netherlands": "NL", "sweden": "SE", "switzerland": "CH", "norway": "NO",
    "denmark": "DK", "finland": "FI", "belgium": "BE", "austria": "AT",
    "portugal": "PT", "poland": "PL", "russia": "RU", "russian federation": "RU",
    "turkey": "TR", "israel": "IL", "iran": "IR", "egypt": "EG",
    "mexico": "MX", "argentina": "AR", "singapore": "SG", "taiwan": "TW",
    "hong kong": "HK", "new zealand": "NZ", "ireland": "IE", "czech republic": "CZ",
    "greece": "GR", "hungary": "HU", "thailand": "TH", "malaysia": "MY",
    "south africa": "ZA", "saudi arabia": "SA", "pakistan": "PK",
    "indonesia": "ID", "philippines": "PH", "colombia": "CO", "chile": "CL",
    # Chinese names
    "中国": "CN", "美国": "US", "英国": "GB", "日本": "JP", "德国": "DE",
    "法国": "FR", "加拿大": "CA", "澳大利亚": "AU", "韩国": "KR",
}

# Regex patterns for extracting country from affiliation strings
_COUNTRY_PATTERNS: list[re.Pattern] = [
    # Email TLD: .cn, .uk, .de, .jp, etc.
    re.compile(r'\b([a-z]{2})\s*$', re.IGNORECASE),  # Last 2-letter word (country code)
    # Common pattern: "City, STATE, Country" or "City, Country"
    re.compile(r',\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*$'),
    # "University of ..., Country" pattern
    re.compile(r'(?:University|Institute|College|Hospital|Center)\s+of\s+\w+[,,\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)'),
]


class MetadataNormalizer:
    """Normalize and merge paper records from multiple academic APIs.

    Usage:
        normalizer = MetadataNormalizer()
        # Normalize single-source record
        pubmed_record = normalizer.normalize(raw_pubmed_data, source="pubmed")
        # Merge multi-source records (dedup by DOI/PMID)
        merged = normalizer.merge_records([pubmed_record, openalex_record, crossref_record])
    """

    # Field priority per source (higher index = lower priority during merge)
    SOURCE_PRIORITY = ["pubmed", "openalex", "crossref", "semantic_scholar"]

    # Fields that use priority-based resolution (first non-null wins)
    _PRIORITY_FIELDS = [
        "title", "abstract", "year", "journal_name", "journal_issn",
        "doi", "pmid", "pmcid", "url", "document_type",
    ]

    # Fields that get merged (union / concatenation)
    _MERGE_FIELDS = [
        "mesh_terms", "keywords", "author_keywords", "keywords_plus",
        "fields_of_study", "references_dois",
    ]

    # Numeric fields that take the maximum value
    _MAX_FIELDS = ["cited_by_count", "reference_count"]

    # Author fields: merge by name matching
    _AUTHOR_MERGE_FIELDS = ["orcid", "ror_id", "affiliations", "country"]

    def normalize(self, raw: dict, source: str) -> dict:
        """Normalize a raw API record into the unified schema.

        Args:
            raw: Raw record from an API response.
            source: One of "pubmed", "openalex", "crossref", "semantic_scholar".

        Returns:
            Record conforming to the unified papers.json schema.
        """
        source = source.lower()
        normalizer_map = {
            "pubmed": self._normalize_pubmed,
            "openalex": self._normalize_openalex,
            "crossref": self._normalize_crossref,
            "semantic_scholar": self._normalize_semantic_scholar,
        }

        fn = normalizer_map.get(source)
        if not fn:
            logger.warning("Unknown source '%s', returning empty record", source)
            return self._empty_record()

        try:
            record = fn(raw)
        except Exception as e:
            logger.error("Failed to normalize %s record: %s", source, e)
            record = self._empty_record()

        record["_source"] = source
        return record

    def merge_records(self, records: list[dict]) -> dict:
        """Merge multiple normalized records into a single deduplicated record.

        Deduplication is based on DOI (primary) or PMID (secondary).
        Merge priority: PubMed > OpenAlex > Crossref > Semantic Scholar.

        Args:
            records: List of normalized records (each has "_source" field).

        Returns:
            Single merged record with all available metadata.
        """
        if not records:
            return self._empty_record()

        if len(records) == 1:
            record = records[0].copy()
            record.pop("_source", None)
            return record

        # Sort by source priority (highest priority first)
        def _priority(rec: dict) -> int:
            src = rec.get("_source", "")
            try:
                return self.SOURCE_PRIORITY.index(src)
            except ValueError:
                return len(self.SOURCE_PRIORITY)

        sorted_records = sorted(records, key=_priority)

        # Start with highest-priority record as base
        merged = sorted_records[0].copy()
        merged.pop("_source", None)

        # Merge in lower-priority records
        for rec in sorted_records[1:]:
            src = rec.get("_source", "")
            self._merge_into(merged, rec, src)

        return merged

    def merge_paper_lists(
        self,
        paper_lists: dict[str, list[dict]],
    ) -> list[dict]:
        """Merge multiple lists of normalized papers, deduplicating by DOI/PMID.

        Args:
            paper_lists: Dict mapping source name to list of normalized records.
                         e.g. {"pubmed": [...], "openalex": [...], ...}

        Returns:
            Deduplicated list of merged paper records.
        """
        # Index papers by DOI and PMID for deduplication
        doi_index: dict[str, list[dict]] = {}
        pmid_index: dict[str, list[dict]] = {}
        no_id: list[dict] = []

        for source, papers in paper_lists.items():
            for raw in papers:
                record = self.normalize(raw, source)
                doi = record.get("doi", "")
                pmid = record.get("pmid", "")

                if doi:
                    doi_index.setdefault(doi.lower(), []).append(record)
                elif pmid:
                    pmid_index.setdefault(pmid, []).append(record)
                else:
                    no_id.append(record)

        # Merge deduplicated groups
        result = []

        for group in doi_index.values():
            result.append(self.merge_records(group))

        # PMID-only records (not already merged via DOI)
        merged_dois = {r.get("doi", "").lower() for r in result if r.get("doi")}
        for pmid, group in pmid_index.items():
            # Check if any record in this group has a DOI that was already merged
            already_merged = any(
                r.get("doi", "").lower() in merged_dois for r in group
            )
            if not already_merged:
                result.append(self.merge_records(group))

        # Papers with no DOI or PMID — add as-is (stripped of _source)
        for rec in no_id:
            rec.pop("_source", None)
            result.append(rec)

        logger.info(
            "Merged %d source lists into %d deduplicated papers",
            sum(len(v) for v in paper_lists.values()),
            len(result),
        )
        return result

    def resolve_country(self, affiliation: str, ror_data: dict | None = None) -> str:
        """Resolve country from author affiliation string.

        Priority:
        1. ROR country_code (if ror_data provided)
        2. Affiliation string regex matching against _COUNTRY_MAP
        3. pycountry fallback (if available)

        Args:
            affiliation: Raw affiliation string (e.g. "Dept of CS, MIT, Cambridge, USA")
            ror_data: Optional dict with "country_code" from ROR lookup

        Returns:
            ISO 3166-1 alpha-2 country code, or empty string if unresolved.
        """
        if not affiliation:
            return ""

        # 1. ROR country_code (highest confidence)
        if ror_data and ror_data.get("country_code"):
            cc = ror_data["country_code"].upper()
            if len(cc) == 2:
                return cc

        # 2. Affiliation string matching
        aff_lower = affiliation.lower().strip()

        # Direct country name match in affiliation
        for name, code in _COUNTRY_MAP.items():
            if name in aff_lower:
                return code

        # Try extracting last segment after comma
        parts = [p.strip() for p in affiliation.split(",")]
        if parts:
            last_part = parts[-1].strip().lower()
            if last_part in _COUNTRY_MAP:
                return _COUNTRY_MAP[last_part]
            # Check if last part is a 2-letter country code
            if len(last_part) == 2 and last_part.isalpha():
                return last_part.upper()

        # 3. pycountry fallback
        try:
            import pycountry
            # Try matching last part of affiliation
            for part in reversed(parts):
                part = part.strip()
                # Try as country name
                country = pycountry.countries.get(name=part)
                if country:
                    return country.alpha_2
                # Try fuzzy match (common_name / official_name)
                for c in pycountry.countries:
                    if (hasattr(c, "common_name") and c.common_name.lower() == part.lower()):
                        return c.alpha_2
                    if (hasattr(c, "official_name") and c.official_name.lower() == part.lower()):
                        return c.alpha_2
        except ImportError:
            pass

        return ""

    # ------------------------------------------------------------------
    # Source-specific normalizers
    # ------------------------------------------------------------------

    def _normalize_pubmed(self, raw: dict) -> dict:
        """Normalize a PubMed EFetch XML-parsed record.

        Expected raw keys (from ElementTree parsing):
            pmid, doi, pmcid, title, abstract, year, journal_name, journal_issn,
            authors (list of dicts with name/affiliations), mesh_terms, keywords,
            author_keywords, document_type
        """
        authors = []
        for a in raw.get("authors", []):
            author = {
                "name": a.get("name", ""),
                "affiliations": a.get("affiliations", []),
            }
            # Resolve country from first affiliation
            if author["affiliations"]:
                country = self.resolve_country(author["affiliations"][0])
                if country:
                    author["country"] = country
            authors.append(author)

        return {
            "pmid": raw.get("pmid", ""),
            "doi": raw.get("doi", ""),
            "pmcid": raw.get("pmcid", ""),
            "title": raw.get("title", ""),
            "abstract": raw.get("abstract", ""),
            "year": raw.get("year"),
            "journal_name": raw.get("journal_name", ""),
            "journal_issn": raw.get("journal_issn", ""),
            "authors": authors,
            "mesh_terms": raw.get("mesh_terms", []),
            "keywords": raw.get("keywords", []),
            "author_keywords": raw.get("author_keywords", []),
            "keywords_plus": [],
            "references_dois": [],
            "cited_by_count": None,
            "reference_count": None,
            "fields_of_study": [],
            "url": raw.get("url", "") or (f"https://pubmed.ncbi.nlm.nih.gov/{raw.get('pmid', '')}/" if raw.get("pmid") else ""),
            "document_type": raw.get("document_type", ""),
        }

    def _normalize_openalex(self, raw: dict) -> dict:
        """Normalize an OpenAlex Works API record.

        Expected raw keys:
            id, doi, title, abstract_inverted_index, publication_year,
            primary_location (source.display_name, source.issn),
            authorships (author.display_name, institutions with ror_id/country_code),
            concepts, referenced_works, cited_by_count, type, ids (pmid, pmcid)
        """
        # Reconstruct abstract from inverted index
        abstract = self._reconstruct_abstract(raw.get("abstract_inverted_index"))

        # Extract authors with affiliations and country
        authors = []
        for authorship in raw.get("authorships", []):
            author = {
                "name": authorship.get("author", {}).get("display_name", ""),
                "orcid": (authorship.get("author", {}).get("orcid", "") or "").replace("https://orcid.org/", ""),
            }
            institutions = authorship.get("institutions", [])
            affiliations = []
            ror_id = ""
            country = ""

            for inst in institutions:
                inst_name = inst.get("display_name", "")
                if inst_name:
                    affiliations.append(inst_name)
                if inst.get("ror_id"):
                    ror_id = inst.get("ror_id", "")
                if inst.get("country_code") and not country:
                    country = inst.get("country_code", "").upper()

            author["affiliations"] = affiliations
            if ror_id:
                author["ror_id"] = ror_id
            if country:
                author["country"] = country
            elif affiliations:
                # Try resolving from affiliation text
                resolved = self.resolve_country(affiliations[0])
                if resolved:
                    author["country"] = resolved

            authors.append(author)

        # Extract journal info from primary_location
        journal_name = ""
        journal_issn = ""
        primary_loc = raw.get("primary_location") or {}
        source = primary_loc.get("source") or {}
        if source:
            journal_name = source.get("display_name", "")
            issn_list = source.get("issn") or []
            # Prefer ISSN-L or first ISSN
            issn_l = source.get("issn_l", "")
            journal_issn = issn_l or (issn_list[0] if issn_list else "")

        # Extract PMID/PMCID from ids
        ids = raw.get("ids", {})
        pmid = ids.get("pmid", "") or ""
        pmcid = ids.get("pmcid", "") or ""
        doi = raw.get("doi", "") or ""
        # OpenAlex DOIs are URLs — extract just the DOI
        if doi.startswith("https://doi.org/"):
            doi = doi[len("https://doi.org/"):]

        # Extract referenced works DOIs
        references_dois = []
        for ref in raw.get("referenced_works", []):
            # OpenAlex work IDs are like "https://openalex.org/W1234"
            # We can only get DOIs if they're embedded; store work IDs as-is
            if ref.startswith("https://openalex.org/"):
                references_dois.append(ref)
            else:
                references_dois.append(ref)

        # Extract fields of study from concepts
        fields = []
        for concept in raw.get("concepts", []):
            name = concept.get("display_name", "")
            if name and concept.get("level", 99) <= 2:  # Top-level concepts only
                fields.append(name)

        return {
            "pmid": str(pmid) if pmid else "",
            "doi": doi,
            "pmcid": pmcid,
            "title": raw.get("title", "") or "",
            "abstract": abstract,
            "year": raw.get("publication_year"),
            "journal_name": journal_name,
            "journal_issn": journal_issn,
            "authors": authors,
            "mesh_terms": [],
            "keywords": [],
            "author_keywords": [],
            "keywords_plus": [],
            "references_dois": references_dois,
            "cited_by_count": raw.get("cited_by_count"),
            "reference_count": raw.get("referenced_works_count"),
            "fields_of_study": fields,
            "url": raw.get("id", ""),
            "document_type": raw.get("type", ""),
        }

    def _normalize_crossref(self, raw: dict) -> dict:
        """Normalize a Crossref Works API record.

        Expected raw keys:
            DOI, title, abstract, published (date-parts), container-title,
            ISSN, author (given/family/affiliation), reference,
            is-referenced-by-count, subject, type
        """
        # Extract year from published date
        year = None
        published = raw.get("published") or raw.get("published-print") or raw.get("published-online") or {}
        date_parts = published.get("date-parts", [[]])
        if date_parts and date_parts[0]:
            year = date_parts[0][0] if date_parts[0] else None

        # Extract authors
        authors = []
        for a in raw.get("author", []):
            given = a.get("given", "")
            family = a.get("family", "")
            name = f"{given} {family}".strip()
            affiliations = a.get("affiliation", [])
            aff_names = [aff.get("name", "") for aff in affiliations if aff.get("name")]

            author = {
                "name": name,
                "affiliations": aff_names,
            }
            if aff_names:
                country = self.resolve_country(aff_names[0])
                if country:
                    author["country"] = country
            authors.append(author)

        # Extract DOI (strip URL prefix if present)
        doi = raw.get("DOI", "") or ""
        if doi.startswith("https://doi.org/"):
            doi = doi[len("https://doi.org/"):]

        # Extract references DOIs
        references_dois = []
        for ref in raw.get("reference", []):
            ref_doi = ref.get("DOI", "")
            if ref_doi:
                references_dois.append(ref_doi)

        # Journal info
        container_titles = raw.get("container-title", [])
        journal_name = container_titles[0] if container_titles else ""
        issn_list = raw.get("ISSN", [])
        journal_issn = issn_list[0] if issn_list else ""

        # Subjects → fields_of_study
        fields = raw.get("subject", []) or []

        # Crossref abstract may contain HTML tags — strip them
        abstract = raw.get("abstract", "") or ""
        if abstract:
            abstract = re.sub(r'<[^>]+>', '', abstract)

        return {
            "pmid": "",
            "doi": doi,
            "pmcid": "",
            "title": (raw.get("title", [""]) or [""])[0] if raw.get("title") else "",
            "abstract": abstract,
            "year": year,
            "journal_name": journal_name,
            "journal_issn": journal_issn,
            "authors": authors,
            "mesh_terms": [],
            "keywords": [],
            "author_keywords": [],
            "keywords_plus": raw.get("subject", []) or [],  # Crossref subjects ~= keywords_plus
            "references_dois": references_dois,
            "cited_by_count": raw.get("is-referenced-by-count"),
            "reference_count": raw.get("references-count"),
            "fields_of_study": fields,
            "url": f"https://doi.org/{doi}" if doi else "",
            "document_type": raw.get("type", ""),
        }

    def _normalize_semantic_scholar(self, raw: dict) -> dict:
        """Normalize a Semantic Scholar API record.

        Expected raw keys:
            paperId, externalIds (DOI/PMID), title, abstract, year,
            authors (name), fieldsOfStudy, url, citationCount, referenceCount
        """
        external_ids = raw.get("externalIds", {}) or {}
        doi = external_ids.get("DOI", "") or ""
        pmid = external_ids.get("PubMed", "") or ""

        authors = []
        for a in raw.get("authors", []):
            authors.append({
                "name": a.get("name", ""),
                "affiliations": [],
            })

        return {
            "pmid": str(pmid) if pmid else "",
            "doi": doi,
            "pmcid": "",
            "title": raw.get("title", "") or "",
            "abstract": raw.get("abstract", "") or "",
            "year": raw.get("year"),
            "journal_name": raw.get("journal", {}).get("name", "") if isinstance(raw.get("journal"), dict) else "",
            "journal_issn": "",
            "authors": authors,
            "mesh_terms": [],
            "keywords": [],
            "author_keywords": [],
            "keywords_plus": [],
            "references_dois": [],
            "cited_by_count": raw.get("citationCount"),
            "reference_count": raw.get("referenceCount"),
            "fields_of_study": raw.get("fieldsOfStudy", []) or [],
            "url": raw.get("url", ""),
            "document_type": "",
        }

    # ------------------------------------------------------------------
    # Merge helpers
    # ------------------------------------------------------------------

    def _merge_into(self, merged: dict, other: dict, source: str) -> None:
        """Merge 'other' record into 'merged' record in-place.

        Priority fields: first non-null wins (already set by highest-priority source).
        Merge fields: union of values.
        Max fields: take maximum.
        Authors: merge by name matching.
        """
        # Priority fields — only fill if merged value is empty/None
        for field in self._PRIORITY_FIELDS:
            if not merged.get(field) and other.get(field):
                merged[field] = other[field]

        # Merge fields — union of lists (deduplicated)
        for field in self._MERGE_FIELDS:
            existing = set(merged.get(field, []) or [])
            new_vals = other.get(field, []) or []
            merged[field] = list(existing | set(new_vals))

        # Max fields — take maximum
        for field in self._MAX_FIELDS:
            val_a = merged.get(field)
            val_b = other.get(field)
            if val_b is not None:
                if val_a is None or val_b > val_a:
                    merged[field] = val_b

        # Merge authors by name matching
        self._merge_authors(merged, other)

    def _merge_authors(self, merged: dict, other: dict) -> None:
        """Merge author lists by matching on name.

        For each author in 'other', find a matching author in 'merged' by name
        and fill in missing fields (affiliations, country, orcid, ror_id).
        If no match, append as new author.
        """
        existing_authors = merged.get("authors", [])
        other_authors = other.get("authors", [])

        if not other_authors:
            return

        # Build name → index map (case-insensitive)
        name_map: dict[str, int] = {}
        for i, a in enumerate(existing_authors):
            name_lower = a.get("name", "").lower().strip()
            if name_lower:
                name_map[name_lower] = i

        for other_a in other_authors:
            name_lower = other_a.get("name", "").lower().strip()
            if not name_lower:
                continue

            if name_lower in name_map:
                # Merge into existing author
                idx = name_map[name_lower]
                existing = existing_authors[idx]

                # Fill missing fields
                for field in self._AUTHOR_MERGE_FIELDS:
                    if not existing.get(field) and other_a.get(field):
                        existing[field] = other_a[field]

                # Merge affiliations (union)
                if other_a.get("affiliations"):
                    existing_affs = set(existing.get("affiliations", []))
                    new_affs = set(other_a["affiliations"]) - existing_affs
                    existing["affiliations"] = existing.get("affiliations", []) + list(new_affs)
            else:
                # New author — append
                existing_authors.append(other_a.copy())
                name_map[name_lower] = len(existing_authors) - 1

        merged["authors"] = existing_authors

    # ------------------------------------------------------------------
    # Utility methods
    # ------------------------------------------------------------------

    @staticmethod
    def _reconstruct_abstract(inverted_index: dict | None) -> str:
        """Reconstruct abstract text from OpenAlex inverted index format.

        OpenAlex returns abstracts as {word: [position1, position2, ...]}.
        We need to sort by position and join words back into text.
        """
        if not inverted_index:
            return ""

        word_positions = []
        for word, positions in inverted_index.items():
            for pos in positions:
                word_positions.append((pos, word))

        word_positions.sort(key=lambda x: x[0])
        return " ".join(w for _, w in word_positions)

    @staticmethod
    def _empty_record() -> dict:
        """Return an empty record conforming to the unified schema."""
        return {
            "pmid": "",
            "doi": "",
            "pmcid": "",
            "title": "",
            "abstract": "",
            "year": None,
            "journal_name": "",
            "journal_issn": "",
            "authors": [],
            "mesh_terms": [],
            "keywords": [],
            "author_keywords": [],
            "keywords_plus": [],
            "references_dois": [],
            "cited_by_count": None,
            "reference_count": None,
            "fields_of_study": [],
            "url": "",
            "document_type": "",
        }

    @staticmethod
    def to_flat_dict(record: dict, index: int = 0) -> dict:
        """Convert a unified record to a flat dict suitable for CSV output.

        Flattens authors, mesh_terms, etc. into comma-separated strings.
        Adds NUM and TIAB columns for backward compatibility.

        Args:
            record: Unified paper record.
            index: Row number for NUM field.

        Returns:
            Flat dict suitable for pandas DataFrame.
        """
        authors = record.get("authors", [])
        author_names = ", ".join(a.get("name", "") for a in authors)
        author_countries = ", ".join(
            a.get("country", "") for a in authors if a.get("country")
        )
        author_affiliations = "; ".join(
            ", ".join(a.get("affiliations", [])) for a in authors if a.get("affiliations")
        )

        mesh_terms = record.get("mesh_terms", [])
        keywords = record.get("keywords", [])
        author_keywords = record.get("author_keywords", [])

        return {
            "NUM": index + 1,
            "TIAB": f"{record.get('title', '')} {record.get('abstract', '')}".strip(),
            "title": record.get("title", ""),
            "abstract": record.get("abstract", ""),
            "year": record.get("year"),
            "authors": author_names,
            "author_countries": author_countries,
            "author_affiliations": author_affiliations,
            "journal_name": record.get("journal_name", ""),
            "journal_issn": record.get("journal_issn", ""),
            "doi": record.get("doi", ""),
            "pmid": record.get("pmid", ""),
            "pmcid": record.get("pmcid", ""),
            "mesh_terms": "; ".join(mesh_terms),
            "keywords": "; ".join(keywords),
            "author_keywords": "; ".join(author_keywords),
            "keywords_plus": "; ".join(record.get("keywords_plus", [])),
            "fields_of_study": "; ".join(record.get("fields_of_study", [])),
            "cited_by_count": record.get("cited_by_count"),
            "reference_count": record.get("reference_count"),
            "url": record.get("url", ""),
            "document_type": record.get("document_type", ""),
        }
