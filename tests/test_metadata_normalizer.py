"""Tests for MetadataNormalizer — multi-source normalization and merging."""

import pytest

from modules.metadata_normalizer import MetadataNormalizer


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------


@pytest.fixture
def normalizer():
    """Create a MetadataNormalizer instance."""
    return MetadataNormalizer()


@pytest.fixture
def pubmed_raw():
    """Sample PubMed EFetch XML-parsed record."""
    return {
        "pmid": "12345678",
        "doi": "10.1234/test.2024.001",
        "pmcid": "PMC9876543",
        "title": "Test PubMed Article",
        "abstract": "This is a test abstract from PubMed.",
        "year": 2024,
        "journal_name": "Test Journal",
        "journal_issn": "1234-5678",
        "authors": [
            {
                "name": "John Smith",
                "affiliations": ["Department of Biology, MIT, Cambridge, USA"],
            },
            {
                "name": "Jane Doe",
                "affiliations": ["Institute of Physics, Oxford University, UK"],
            },
        ],
        "mesh_terms": ["Machine Learning", "Deep Learning"],
        "keywords": ["AI", "neural networks"],
        "author_keywords": ["test keyword"],
        "document_type": "Journal Article",
    }


@pytest.fixture
def openalex_raw():
    """Sample OpenAlex Works API record."""
    return {
        "id": "https://openalex.org/W1234567890",
        "doi": "https://doi.org/10.1234/test.2024.001",
        "title": "Test PubMed Article",  # Same as PubMed for dedup testing
        "abstract_inverted_index": {
            "This": [0],
            "is": [1],
            "a": [2],
            "test": [3],
            "abstract": [4],
            "from": [5],
            "OpenAlex": [6],
        },
        "publication_year": 2024,
        "primary_location": {
            "source": {
                "display_name": "Test Journal OpenAlex",
                "issn": ["1234-5678"],
                "issn_l": "1234-5678",
            }
        },
        "authorships": [
            {
                "author": {
                    "display_name": "John Smith",
                    "orcid": "https://orcid.org/0000-0001-2345-6789",
                },
                "institutions": [
                    {
                        "display_name": "Massachusetts Institute of Technology",
                        "ror_id": "https://ror.org/042nb2s44",
                        "country_code": "US",
                    }
                ],
            },
            {
                "author": {
                    "display_name": "Alice Johnson",
                    "orcid": "",
                },
                "institutions": [
                    {
                        "display_name": "Stanford University",
                        "country_code": "US",
                    }
                ],
            },
        ],
        "concepts": [
            {"display_name": "Computer Science", "level": 0},
            {"display_name": "Medicine", "level": 1},
        ],
        "referenced_works": [
            "https://openalex.org/W1111111111",
            "https://openalex.org/W2222222222",
        ],
        "cited_by_count": 42,
        "referenced_works_count": 15,
        "type": "article",
        "ids": {"pmid": "12345678", "pmcid": "PMC9876543"},
    }


@pytest.fixture
def crossref_raw():
    """Sample Crossref Works API record."""
    return {
        "DOI": "10.1234/test.2024.001",
        "title": ["Test PubMed Article"],
        "abstract": "<p>This is a test abstract from Crossref.</p>",
        "published": {"date-parts": [[2024]]},
        "container-title": ["Test Journal Crossref"],
        "ISSN": ["1234-5678"],
        "author": [
            {
                "given": "John",
                "family": "Smith",
                "affiliation": [{"name": "MIT, Cambridge, USA"}],
            },
            {
                "given": "Bob",
                "family": "Brown",
                "affiliation": [{"name": "Harvard University, Boston, USA"}],
            },
        ],
        "reference": [
            {"DOI": "10.5678/ref.001"},
            {"DOI": "10.5678/ref.002"},
        ],
        "is-referenced-by-count": 50,
        "references-count": 20,
        "subject": ["Computer Science", "Engineering"],
        "type": "journal-article",
    }


@pytest.fixture
def semantic_scholar_raw():
    """Sample Semantic Scholar API record."""
    return {
        "paperId": "abc123def456",
        "externalIds": {"DOI": "10.1234/test.2024.001", "PubMed": "12345678"},
        "title": "Test PubMed Article",
        "abstract": "This is a test abstract from Semantic Scholar.",
        "year": 2024,
        "authors": [
            {"name": "John Smith"},
            {"name": "Carol White"},
        ],
        "fieldsOfStudy": ["Computer Science", "Biology"],
        "url": "https://semanticscholar.org/paper/abc123",
        "citationCount": 60,
        "referenceCount": 25,
        "journal": {"name": "Test Journal SS"},
    }


# -------------------------------------------------------------------
# Test normalize() for each source
# -------------------------------------------------------------------


def test_normalize_pubmed(normalizer, pubmed_raw):
    """Test PubMed record normalization."""
    record = normalizer.normalize(pubmed_raw, source="pubmed")

    assert record["pmid"] == "12345678"
    assert record["doi"] == "10.1234/test.2024.001"
    assert record["pmcid"] == "PMC9876543"
    assert record["title"] == "Test PubMed Article"
    assert record["abstract"] == "This is a test abstract from PubMed."
    assert record["year"] == 2024
    assert record["journal_name"] == "Test Journal"
    assert record["journal_issn"] == "1234-5678"

    # Check authors
    assert len(record["authors"]) == 2
    assert record["authors"][0]["name"] == "John Smith"
    assert "MIT" in record["authors"][0]["affiliations"][0]
    assert record["authors"][0]["country"] == "US"  # Resolved from affiliation

    assert record["authors"][1]["name"] == "Jane Doe"
    assert record["authors"][1]["country"] == "GB"  # Resolved from UK

    # Check MeSH terms
    assert "Machine Learning" in record["mesh_terms"]
    assert "Deep Learning" in record["mesh_terms"]

    assert record["document_type"] == "Journal Article"
    assert record["_source"] == "pubmed"


def test_normalize_openalex(normalizer, openalex_raw):
    """Test OpenAlex record normalization."""
    record = normalizer.normalize(openalex_raw, source="openalex")

    assert record["pmid"] == "12345678"
    assert record["doi"] == "10.1234/test.2024.001"
    assert record["pmcid"] == "PMC9876543"
    assert record["title"] == "Test PubMed Article"

    # Check abstract reconstruction from inverted index
    assert record["abstract"] == "This is a test abstract from OpenAlex"

    assert record["year"] == 2024
    assert record["journal_name"] == "Test Journal OpenAlex"
    assert record["journal_issn"] == "1234-5678"

    # Check authors
    assert len(record["authors"]) == 2
    assert record["authors"][0]["name"] == "John Smith"
    assert record["authors"][0]["orcid"] == "0000-0001-2345-6789"  # ORCID stripped
    assert record["authors"][0]["ror_id"] == "https://ror.org/042nb2s44"
    assert record["authors"][0]["country"] == "US"  # From institution country_code

    assert record["authors"][1]["name"] == "Alice Johnson"
    assert record["authors"][1]["country"] == "US"

    # Check fields of study
    assert "Computer Science" in record["fields_of_study"]
    assert "Medicine" in record["fields_of_study"]

    # Check references
    assert len(record["references_dois"]) == 2

    assert record["cited_by_count"] == 42
    assert record["reference_count"] == 15
    assert record["document_type"] == "article"
    assert record["_source"] == "openalex"


def test_normalize_crossref(normalizer, crossref_raw):
    """Test Crossref record normalization."""
    record = normalizer.normalize(crossref_raw, source="crossref")

    assert record["doi"] == "10.1234/test.2024.001"
    assert record["title"] == "Test PubMed Article"

    # Check abstract HTML stripping
    assert record["abstract"] == "This is a test abstract from Crossref."
    assert "<p>" not in record["abstract"]

    assert record["year"] == 2024
    assert record["journal_name"] == "Test Journal Crossref"
    assert record["journal_issn"] == "1234-5678"

    # Check authors
    assert len(record["authors"]) == 2
    assert record["authors"][0]["name"] == "John Smith"
    assert record["authors"][0]["country"] == "US"

    assert record["authors"][1]["name"] == "Bob Brown"
    assert record["authors"][1]["country"] == "US"

    # Check references
    assert len(record["references_dois"]) == 2
    assert "10.5678/ref.001" in record["references_dois"]

    assert record["cited_by_count"] == 50
    assert record["reference_count"] == 20

    # Check fields of study
    assert "Computer Science" in record["fields_of_study"]
    assert "Engineering" in record["fields_of_study"]

    assert record["document_type"] == "journal-article"
    assert record["_source"] == "crossref"


def test_normalize_semantic_scholar(normalizer, semantic_scholar_raw):
    """Test Semantic Scholar record normalization."""
    record = normalizer.normalize(semantic_scholar_raw, source="semantic_scholar")

    assert record["pmid"] == "12345678"
    assert record["doi"] == "10.1234/test.2024.001"
    assert record["title"] == "Test PubMed Article"
    assert record["abstract"] == "This is a test abstract from Semantic Scholar."
    assert record["year"] == 2024
    assert record["journal_name"] == "Test Journal SS"

    # Check authors
    assert len(record["authors"]) == 2
    assert record["authors"][0]["name"] == "John Smith"
    assert record["authors"][1]["name"] == "Carol White"

    # Check fields of study
    assert "Computer Science" in record["fields_of_study"]
    assert "Biology" in record["fields_of_study"]

    assert record["cited_by_count"] == 60
    assert record["reference_count"] == 25
    assert record["_source"] == "semantic_scholar"


def test_normalize_unknown_source(normalizer, pubmed_raw):
    """Test normalization with unknown source returns empty record."""
    record = normalizer.normalize(pubmed_raw, source="unknown")

    assert record["pmid"] == ""
    assert record["doi"] == ""
    assert record["_source"] == "unknown"


# -------------------------------------------------------------------
# Test merge_records()
# -------------------------------------------------------------------


def test_merge_records_single(normalizer, pubmed_raw):
    """Test merging a single record returns it unchanged (except _source)."""
    records = [normalizer.normalize(pubmed_raw, source="pubmed")]
    merged = normalizer.merge_records(records)

    assert merged["pmid"] == "12345678"
    assert merged["doi"] == "10.1234/test.2024.001"
    assert "_source" not in merged  # Should be stripped


def test_merge_records_multiple_same_paper(
    normalizer, pubmed_raw, openalex_raw, crossref_raw, semantic_scholar_raw
):
    """Test merging multiple records for the same paper (same DOI)."""
    records = [
        normalizer.normalize(pubmed_raw, source="pubmed"),
        normalizer.normalize(openalex_raw, source="openalex"),
        normalizer.normalize(crossref_raw, source="crossref"),
        normalizer.normalize(semantic_scholar_raw, source="semantic_scholar"),
    ]

    merged = normalizer.merge_records(records)

    # Should use PubMed data (highest priority)
    assert merged["title"] == "Test PubMed Article"
    assert merged["abstract"] == "This is a test abstract from PubMed."
    assert merged["journal_name"] == "Test Journal"

    # Should merge authors (union across sources)
    author_names = {a["name"] for a in merged["authors"]}
    assert "John Smith" in author_names
    assert "Jane Doe" in author_names  # From PubMed
    assert "Alice Johnson" in author_names  # From OpenAlex
    assert "Bob Brown" in author_names  # From Crossref
    assert "Carol White" in author_names  # From Semantic Scholar

    # John Smith should have ORCID from OpenAlex
    john = next(a for a in merged["authors"] if a["name"] == "John Smith")
    assert john.get("orcid") == "0000-0001-2345-6789"
    assert john.get("ror_id") == "https://ror.org/042nb2s44"

    # Should merge mesh_terms (PubMed only)
    assert "Machine Learning" in merged["mesh_terms"]

    # Should merge fields_of_study (union)
    assert "Computer Science" in merged["fields_of_study"]
    assert "Medicine" in merged["fields_of_study"]
    assert "Engineering" in merged["fields_of_study"]
    assert "Biology" in merged["fields_of_study"]

    # Should take max citation count
    assert merged["cited_by_count"] == 60  # Max of 42, 50, 60

    # Should merge references (union)
    # Note: OpenAlex references are work IDs, Crossref are DOIs
    assert len(merged["references_dois"]) >= 2


def test_merge_records_priority_fields(normalizer):
    """Test that priority fields use highest-priority source."""
    # Create records with conflicting priority fields
    pubmed_rec = normalizer.normalize(
        {
            "pmid": "123",
            "doi": "10.1234/test",
            "title": "PubMed Title",
            "abstract": "PubMed Abstract",
            "year": 2024,
        },
        source="pubmed",
    )

    openalex_rec = normalizer.normalize(
        {
            "id": "W123",
            "doi": "https://doi.org/10.1234/test",
            "title": "OpenAlex Title",
            "publication_year": 2023,
        },
        source="openalex",
    )

    records = [pubmed_rec, openalex_rec]
    merged = normalizer.merge_records(records)

    # Should use PubMed values (higher priority)
    assert merged["title"] == "PubMed Title"
    assert merged["abstract"] == "PubMed Abstract"
    assert merged["year"] == 2024


def test_merge_records_fill_missing(normalizer):
    """Test that missing fields are filled from lower-priority sources."""
    # PubMed record missing abstract
    pubmed_rec = normalizer.normalize(
        {
            "pmid": "123",
            "doi": "10.1234/test",
            "title": "Test Title",
            "abstract": "",  # Missing
        },
        source="pubmed",
    )

    # OpenAlex has abstract
    openalex_rec = normalizer.normalize(
        {
            "id": "W123",
            "doi": "https://doi.org/10.1234/test",
            "title": "Test Title",
            "abstract_inverted_index": {"Test": [0], "abstract": [1]},
        },
        source="openalex",
    )

    records = [pubmed_rec, openalex_rec]
    merged = normalizer.merge_records(records)

    # Should fill abstract from OpenAlex
    assert merged["abstract"] == "Test abstract"


def test_merge_records_merge_lists(normalizer):
    """Test that list fields are merged (union)."""
    pubmed_rec = normalizer.normalize(
        {
            "pmid": "123",
            "doi": "10.1234/test",
            "mesh_terms": ["Term A", "Term B"],
        },
        source="pubmed",
    )

    openalex_rec = normalizer.normalize(
        {
            "id": "W123",
            "doi": "https://doi.org/10.1234/test",
            "concepts": [{"display_name": "Term B", "level": 0}, {"display_name": "Term C", "level": 0}],
        },
        source="openalex",
    )

    records = [pubmed_rec, openalex_rec]
    merged = normalizer.merge_records(records)

    # Should union terms
    assert "Term A" in merged["mesh_terms"]
    assert "Term B" in merged["mesh_terms"]
    assert "Term B" in merged["fields_of_study"]
    assert "Term C" in merged["fields_of_study"]


def test_merge_records_max_numeric(normalizer):
    """Test that numeric fields take the maximum."""
    pubmed_rec = normalizer.normalize(
        {
            "pmid": "123",
            "doi": "10.1234/test",
        },
        source="pubmed",
    )

    openalex_rec = normalizer.normalize(
        {
            "id": "W123",
            "doi": "https://doi.org/10.1234/test",
            "cited_by_count": 50,
            "referenced_works_count": 10,
        },
        source="openalex",
    )

    crossref_rec = normalizer.normalize(
        {
            "DOI": "10.1234/test",
            "is-referenced-by-count": 75,
            "references-count": 20,
        },
        source="crossref",
    )

    records = [pubmed_rec, openalex_rec, crossref_rec]
    merged = normalizer.merge_records(records)

    # Should take max
    assert merged["cited_by_count"] == 75
    assert merged["reference_count"] == 20


# -------------------------------------------------------------------
# Test resolve_country()
# -------------------------------------------------------------------


def test_resolve_country_ror_data(normalizer):
    """Test country resolution with ROR data (highest priority)."""
    ror_data = {"country_code": "US", "country_name": "United States"}
    country = normalizer.resolve_country(
        "Department of Biology, MIT, Cambridge, USA", ror_data=ror_data
    )
    assert country == "US"


def test_resolve_country_affiliation_string(normalizer):
    """Test country resolution from affiliation string."""
    # USA
    assert normalizer.resolve_country("MIT, Cambridge, USA") == "US"
    assert normalizer.resolve_country("Harvard University, Boston, United States") == "US"

    # UK
    assert normalizer.resolve_country("Oxford University, UK") == "GB"
    assert normalizer.resolve_country("Cambridge, United Kingdom") == "GB"

    # China
    assert normalizer.resolve_country("Tsinghua University, Beijing, China") == "CN"

    # Germany
    assert normalizer.resolve_country("Max Planck Institute, Germany") == "DE"


def test_resolve_country_last_segment(normalizer):
    """Test country resolution from last segment after comma."""
    assert normalizer.resolve_country("University of Something, Canada") == "CA"
    assert normalizer.resolve_country("Some Institute, Japan") == "JP"


def test_resolve_country_two_letter_code(normalizer):
    """Test country resolution from 2-letter country code."""
    assert normalizer.resolve_country("Some Place, FR") == "FR"
    assert normalizer.resolve_country("Institute, DE") == "DE"


def test_resolve_country_pycountry(normalizer):
    """Test country resolution using pycountry fallback."""
    # Try with a country name that requires pycountry
    # (not in our simple map but valid country name)
    try:
        import pycountry

        # Test with a country not in our simple map
        # e.g., "Argentina" should work via pycountry
        country = normalizer.resolve_country("University of Buenos Aires, Argentina")
        assert country == "AR"
    except ImportError:
        # pycountry not installed, skip this test
        pytest.skip("pycountry not installed")


def test_resolve_country_empty(normalizer):
    """Test country resolution with empty affiliation."""
    assert normalizer.resolve_country("") == ""
    assert normalizer.resolve_country(None) == ""


def test_resolve_country_unknown(normalizer):
    """Test country resolution with unresolvable string."""
    # Should return empty string (not crash)
    assert normalizer.resolve_country("Some random text without country") == ""


# -------------------------------------------------------------------
# Test merge_paper_lists()
# -------------------------------------------------------------------


def test_merge_paper_lists(normalizer, pubmed_raw, openalex_raw, crossref_raw):
    """Test merging multiple paper lists with deduplication."""
    # Create overlapping records (same DOI)
    pubmed_papers = [pubmed_raw]

    openalex_papers = [
        openalex_raw,  # Same as PubMed (DOI match)
        {
            "id": "W999",
            "doi": "https://doi.org/10.9999/different",
            "title": "Different Paper",
            "publication_year": 2023,
        },  # Different paper
    ]

    crossref_papers = [
        crossref_raw,  # Same as PubMed (DOI match)
    ]

    paper_lists = {
        "pubmed": pubmed_papers,
        "openalex": openalex_papers,
        "crossref": crossref_papers,
    }

    merged = normalizer.merge_paper_lists(paper_lists)

    # Should deduplicate by DOI
    # 3 papers in lists, but 2 unique DOIs
    assert len(merged) == 2

    # Check that we have both papers
    dois = {p["doi"] for p in merged}
    assert "10.1234/test.2024.001" in dois
    assert "10.9999/different" in dois


def test_merge_paper_lists_pmid_dedup(normalizer):
    """Test deduplication by PMID when DOI is missing."""
    # Papers without DOI, only PMID
    pubmed_papers = [
        {
            "pmid": "123",
            "title": "Paper 1",
        }
    ]

    openalex_papers = [
        {
            "id": "W123",
            "ids": {"pmid": "123"},  # Same PMID
            "title": "Paper 1 OpenAlex",
        }
    ]

    paper_lists = {
        "pubmed": pubmed_papers,
        "openalex": openalex_papers,
    }

    merged = normalizer.merge_paper_lists(paper_lists)

    # Should deduplicate by PMID
    assert len(merged) == 1
    assert merged[0]["title"] == "Paper 1"  # PubMed has priority


def test_merge_paper_lists_no_id(normalizer):
    """Test handling papers with no DOI or PMID."""
    papers = [
        {
            "title": "Paper without IDs",
            "abstract": "Test abstract",
        }
    ]

    paper_lists = {"pubmed": papers}
    merged = normalizer.merge_paper_lists(paper_lists)

    assert len(merged) == 1
    assert merged[0]["title"] == "Paper without IDs"


# -------------------------------------------------------------------
# Test to_flat_dict()
# -------------------------------------------------------------------


def test_to_flat_dict(normalizer):
    """Test conversion to flat dict for CSV output."""
    record = {
        "title": "Test Paper",
        "abstract": "Test abstract",
        "year": 2024,
        "authors": [
            {
                "name": "John Smith",
                "affiliations": ["MIT", "Harvard"],
                "country": "US",
                "orcid": "0000-0001-2345-6789",
            },
            {
                "name": "Jane Doe",
                "affiliations": ["Oxford"],
                "country": "GB",
            },
        ],
        "journal_name": "Test Journal",
        "doi": "10.1234/test",
        "pmid": "12345678",
        "mesh_terms": ["Term A", "Term B"],
        "cited_by_count": 42,
    }

    flat = normalizer.to_flat_dict(record, index=5)

    assert flat["NUM"] == 6  # index + 1
    assert flat["TIAB"] == "Test Paper Test abstract"
    assert flat["title"] == "Test Paper"
    assert flat["abstract"] == "Test abstract"
    assert flat["year"] == 2024
    assert flat["authors"] == "John Smith, Jane Doe"
    assert flat["author_countries"] == "US, GB"
    assert "MIT" in flat["author_affiliations"]
    assert "Harvard" in flat["author_affiliations"]
    assert flat["journal_name"] == "Test Journal"
    assert flat["doi"] == "10.1234/test"
    assert flat["pmid"] == "12345678"
    assert flat["mesh_terms"] == "Term A; Term B"
    assert flat["cited_by_count"] == 42


# -------------------------------------------------------------------
# Test abstract reconstruction (OpenAlex)
# -------------------------------------------------------------------


def test_reconstruct_abstract(normalizer):
    """Test OpenAlex inverted index abstract reconstruction."""
    inverted = {
        "This": [0],
        "is": [1],
        "a": [2],
        "test": [3],
        "abstract": [4],
        "with": [5, 7],  # Word appears twice
        "multiple": [6],
        "positions": [8],
    }

    abstract = normalizer._reconstruct_abstract(inverted)
    assert abstract == "This is a test abstract with multiple with positions"


def test_reconstruct_abstract_empty(normalizer):
    """Test reconstruction with empty inverted index."""
    assert normalizer._reconstruct_abstract(None) == ""
    assert normalizer._reconstruct_abstract({}) == ""


# -------------------------------------------------------------------
# Edge cases and error handling
# -------------------------------------------------------------------


def test_normalize_malformed_data(normalizer):
    """Test normalization handles malformed data gracefully."""
    # Missing required fields
    malformed = {"title": "Test"}
    record = normalizer.normalize(malformed, source="pubmed")

    # Should not crash, just have empty values
    assert record["title"] == "Test"
    assert record["pmid"] == ""
    assert record["abstract"] == ""


def test_merge_records_empty_list(normalizer):
    """Test merging empty list returns empty record."""
    merged = normalizer.merge_records([])
    assert merged == normalizer._empty_record()


def test_author_name_case_insensitive_merge(normalizer):
    """Test author merging is case-insensitive."""
    pubmed_rec = normalizer.normalize(
        {
            "pmid": "123",
            "authors": [{"name": "John Smith", "affiliations": ["MIT"]}],
        },
        source="pubmed",
    )

    openalex_rec = normalizer.normalize(
        {
            "id": "W123",
            "authorships": [
                {
                    "author": {"display_name": "JOHN SMITH"},  # Different case
                    "institutions": [{"display_name": "Harvard", "country_code": "US"}],
                }
            ],
        },
        source="openalex",
    )

    records = [pubmed_rec, openalex_rec]
    merged = normalizer.merge_records(records)

    # Should merge authors (case-insensitive)
    assert len(merged["authors"]) == 1
    assert merged["authors"][0]["name"] == "John Smith"  # Keeps first name format
    # Should merge affiliations
    assert "MIT" in merged["authors"][0]["affiliations"]
    assert "Harvard" in merged["authors"][0]["affiliations"]
