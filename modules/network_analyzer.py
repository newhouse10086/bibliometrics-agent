"""Network Analysis Module.

Constructs and analyzes multiple network types from bibliometric data:

1. co_word: Keywords that appear together (from DTM)
2. author_collaboration: Authors connected by co-authored papers
3. institution_collaboration: Institutions connected by co-authored papers
4. country_collaboration: Countries connected by co-authored papers
5. co_citation: Papers that are cited together
6. bibliographic_coupling: Papers sharing references

Each network outputs GraphML + pyvis HTML visualization.
Centrality metrics and community detection are computed for all networks.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd

from modules.base import BaseModule, HardwareSpec, RunContext

logger = logging.getLogger(__name__)

# Network types supported
NETWORK_TYPES = [
    "co_word",
    "author_collaboration",
    "institution_collaboration",
    "country_collaboration",
    "co_citation",
    "bibliographic_coupling",
]


class NetworkAnalyzer(BaseModule):
    """Network analysis module for bibliometric data.

    Constructs multiple network types from paper metadata and DTM data.
    Provides network visualization, centrality metrics, and community detection.
    """

    @property
    def name(self) -> str:
        return "network_analyzer"

    @property
    def version(self) -> str:
        return "2.0.0"

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "dtm_path": {
                    "type": "string",
                    "description": "Path to document-term matrix CSV (for co-word network)",
                },
                "vocab_path": {
                    "type": "string",
                    "description": "Path to vocabulary list file",
                },
                "papers_json_path": {
                    "type": "string",
                    "description": "Path to papers.json from paper_fetcher (for collaboration/citation networks)",
                },
                "country_collaboration_path": {
                    "type": "string",
                    "description": "Path to country collaboration edges CSV from country_analyzer (optional)",
                },
            },
        }

    def output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "networks": {
                    "type": "object",
                    "description": "Per-network-type outputs",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "graphml_path": {"type": "string"},
                            "visualization_path": {"type": "string"},
                            "centrality_path": {"type": "string"},
                            "communities_path": {"type": "string"},
                        },
                    },
                },
                "stats": {
                    "type": "object",
                    "description": "Per-network-type statistics",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "n_nodes": {"type": "integer"},
                            "n_edges": {"type": "integer"},
                            "density": {"type": "number"},
                            "n_communities": {"type": "integer"},
                            "avg_degree": {"type": "number"},
                        },
                    },
                },
            },
        }

    def config_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "network_types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": NETWORK_TYPES,
                    },
                    "default": NETWORK_TYPES,
                    "description": "Which network types to construct",
                },
                "min_co_occurrence": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 2,
                    "description": "Minimum co-occurrence threshold for co-word edges",
                },
                "min_collaboration": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 1,
                    "description": "Minimum collaboration count for author/institution/country edges",
                },
                "min_co_citation": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 2,
                    "description": "Minimum co-citation count for edges",
                },
                "min_coupling": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 2,
                    "description": "Minimum shared references for bibliographic coupling edges",
                },
                "top_k_words": {
                    "type": "integer",
                    "minimum": 10,
                    "default": 100,
                    "description": "Top K words to include in co-word network",
                },
                "max_authors": {
                    "type": "integer",
                    "default": 200,
                    "description": "Max authors to include in collaboration network",
                },
                "max_institutions": {
                    "type": "integer",
                    "default": 100,
                    "description": "Max institutions to include in institution network",
                },
                "max_countries": {
                    "type": "integer",
                    "default": 50,
                    "description": "Max countries to include in country network",
                },
                "enable_visualization": {
                    "type": "boolean",
                    "default": True,
                    "description": "Generate HTML visualization for each network",
                },
                "visualization_height": {
                    "type": "string",
                    "default": "800px",
                    "description": "Height of network visualization",
                },
            },
        }

    def get_hardware_requirements(self, config: dict) -> HardwareSpec:
        return HardwareSpec(
            cpu_cores=2,
            min_memory_gb=4.0,
            recommended_memory_gb=8.0,
            gpu_required=False,
            estimated_runtime_seconds=300,
        )

    def process(self, input_data: dict, config: dict, context: RunContext) -> dict:
        """Execute network analysis for all configured network types."""
        logger.info("Starting network analysis (v2.0)...")

        # Resolve inputs from previous outputs
        dtm_path = input_data.get("dtm_path")
        vocab_path = input_data.get("vocab_path")
        papers_json_path = input_data.get("papers_json_path")

        if "preprocessor" in context.previous_outputs:
            prev = context.previous_outputs["preprocessor"]
            if not dtm_path:
                dtm_path = prev.get("dtm_path")
            if not vocab_path:
                vocab_path = prev.get("vocab_path")

        if "paper_fetcher" in context.previous_outputs:
            prev = context.previous_outputs["paper_fetcher"]
            if not papers_json_path:
                papers_json_path = prev.get("papers_json_path")

        # Also check country_analyzer output for country collaboration
        country_collab_path = input_data.get("country_collaboration_path")
        if "country_analyzer" in context.previous_outputs:
            prev = context.previous_outputs["country_analyzer"]
            if not country_collab_path:
                country_collab_path = prev.get("country_collaboration_path")

        # Load papers data if available
        papers = None
        if papers_json_path and Path(papers_json_path).exists():
            with open(papers_json_path, "r", encoding="utf-8") as f:
                papers = json.load(f)
            logger.info("Loaded %d papers from papers.json", len(papers))

        # Load DTM + vocab for co-word network
        dtm_df = None
        vocab = None
        if dtm_path and Path(dtm_path).exists():
            dtm_df = pd.read_csv(dtm_path, index_col=0)
            if vocab_path and Path(vocab_path).exists():
                vocab = Path(vocab_path).read_text(encoding="utf-8").strip().split("\n")
            else:
                vocab = list(dtm_df.columns)
            logger.info("DTM shape: %s, vocab size: %d", dtm_df.shape, len(vocab))

        # Load country collaboration edges if available
        country_edges_df = None
        if country_collab_path and Path(country_collab_path).exists():
            country_edges_df = pd.read_csv(country_collab_path)
            logger.info("Loaded %d country collaboration edges", len(country_edges_df))

        # Determine which networks to build
        network_types = config.get("network_types", NETWORK_TYPES)
        logger.info("Building networks: %s", network_types)

        # Output directory
        output_dir = context.get_output_path(self.name, "")
        output_dir.mkdir(parents=True, exist_ok=True)

        networks_output = {}
        stats_output = {}

        for net_type in network_types:
            logger.info("--- Building %s network ---", net_type)
            try:
                graph = self._build_network(
                    net_type, dtm_df, vocab, papers,
                    country_edges_df, config,
                )
            except Exception as e:
                logger.error("Failed to build %s network: %s", net_type, e)
                continue

            if graph is None or graph.number_of_nodes() == 0:
                logger.warning("%s network is empty, skipping", net_type)
                continue

            logger.info("%s network: %d nodes, %d edges",
                        net_type, graph.number_of_nodes(), graph.number_of_edges())

            # Compute metrics
            centrality_df = self._compute_centrality_metrics(graph)
            communities = self._detect_communities(graph)
            community_df = self._create_community_dataframe(communities, graph)

            # Save GraphML
            graphml_path = output_dir / f"{net_type}_network.graphml"
            nx.write_graphml(graph, graphml_path)

            # Save centrality
            centrality_path = output_dir / f"{net_type}_centrality.csv"
            centrality_df.to_csv(centrality_path, index=False)

            # Save communities
            community_path = output_dir / f"{net_type}_communities.csv"
            community_df.to_csv(community_path, index=False)

            # Visualization
            viz_path = None
            if config.get("enable_visualization", True):
                try:
                    viz_path = output_dir / f"{net_type}_network.html"
                    self._visualize_network_pyvis(
                        graph, communities, viz_path,
                        config.get("visualization_height", "800px"),
                        net_type,
                    )
                except ImportError:
                    logger.warning("pyvis not installed, skipping visualization for %s", net_type)

            # Stats
            net_stats = {
                "n_nodes": graph.number_of_nodes(),
                "n_edges": graph.number_of_edges(),
                "density": round(nx.density(graph), 4),
                "n_communities": len(set(communities.values())),
                "avg_degree": round(
                    sum(dict(graph.degree()).values()) / graph.number_of_nodes(), 2
                ) if graph.number_of_nodes() > 0 else 0,
            }
            stats_output[net_type] = net_stats

            net_output = {
                "graphml_path": str(graphml_path),
                "centrality_path": str(centrality_path),
                "communities_path": str(community_path),
            }
            if viz_path:
                net_output["visualization_path"] = str(viz_path)
            networks_output[net_type] = net_output

        logger.info("Network analysis complete: %d networks built", len(networks_output))

        return {
            "networks": networks_output,
            "stats": stats_output,
        }

    # ------------------------------------------------------------------
    # Network builders
    # ------------------------------------------------------------------

    def _build_network(
        self,
        net_type: str,
        dtm_df: pd.DataFrame | None,
        vocab: list[str] | None,
        papers: list[dict] | None,
        country_edges_df: pd.DataFrame | None,
        config: dict,
    ) -> nx.Graph | None:
        """Dispatch to the appropriate network builder."""
        if net_type == "co_word":
            if dtm_df is None or vocab is None:
                logger.warning("co_word: DTM/vocab not available")
                return None
            return self._build_co_word_network(
                dtm_df, vocab,
                min_co_occurrence=config.get("min_co_occurrence", 2),
                top_k=config.get("top_k_words", 100),
            )
        elif net_type == "author_collaboration":
            if papers is None:
                logger.warning("author_collaboration: papers.json not available")
                return None
            return self._build_author_collaboration_network(
                papers,
                min_collab=config.get("min_collaboration", 1),
                max_authors=config.get("max_authors", 200),
            )
        elif net_type == "institution_collaboration":
            if papers is None:
                logger.warning("institution_collaboration: papers.json not available")
                return None
            return self._build_institution_collaboration_network(
                papers,
                min_collab=config.get("min_collaboration", 1),
                max_institutions=config.get("max_institutions", 100),
            )
        elif net_type == "country_collaboration":
            return self._build_country_collaboration_network(
                papers, country_edges_df,
                min_collab=config.get("min_collaboration", 1),
                max_countries=config.get("max_countries", 50),
            )
        elif net_type == "co_citation":
            if papers is None:
                logger.warning("co_citation: papers.json not available")
                return None
            return self._build_co_citation_network(
                papers,
                min_co_citation=config.get("min_co_citation", 2),
            )
        elif net_type == "bibliographic_coupling":
            if papers is None:
                logger.warning("bibliographic_coupling: papers.json not available")
                return None
            return self._build_bibliographic_coupling_network(
                papers,
                min_coupling=config.get("min_coupling", 2),
            )
        else:
            logger.warning("Unknown network type: %s", net_type)
            return None

    def _build_co_word_network(
        self,
        dtm_df: pd.DataFrame,
        vocab: list[str],
        min_co_occurrence: int = 2,
        top_k: int = 100,
    ) -> nx.Graph:
        """Build co-word network from document-term matrix.

        Two words are connected if they appear together in the same document.
        Edge weight = number of co-occurrences.
        """
        dtm = dtm_df.values

        # Select top K words by frequency
        word_freq = dtm.sum(axis=0)
        top_indices = np.argsort(word_freq)[-top_k:][::-1]

        dtm_top = dtm[:, top_indices]
        vocab_top = [vocab[i] for i in top_indices]

        logger.info("Using top %d words for co-word network", len(vocab_top))

        # Co-occurrence matrix: C = DTM^T @ DTM
        co_occurrence_matrix = dtm_top.T @ dtm_top

        G = nx.Graph()
        for word in vocab_top:
            G.add_node(word)

        n_words = len(vocab_top)
        for i in range(n_words):
            for j in range(i + 1, n_words):
                weight = co_occurrence_matrix[i, j]
                if weight >= min_co_occurrence:
                    G.add_edge(vocab_top[i], vocab_top[j], weight=int(weight))

        # Remove isolated nodes
        isolated = list(nx.isolates(G))
        G.remove_nodes_from(isolated)
        logger.info("Removed %d isolated nodes", len(isolated))

        return G

    def _build_author_collaboration_network(
        self,
        papers: list[dict],
        min_collab: int = 1,
        max_authors: int = 200,
    ) -> nx.Graph:
        """Build author collaboration network.

        Two authors are connected if they co-authored a paper.
        Edge weight = number of co-authored papers.
        """
        # Count collaborations per author pair
        collab_counts: Counter = Counter()
        author_paper_counts: Counter = Counter()

        for paper in papers:
            authors = paper.get("authors", [])
            names = [a.get("name", "") for a in authors if a.get("name")]
            for name in names:
                author_paper_counts[name] += 1
            # Create edges for all pairs of co-authors
            for a1, a2 in combinations(sorted(names), 2):
                collab_counts[(a1, a2)] += 1

        if not collab_counts:
            logger.warning("No author collaboration data found")
            return nx.Graph()

        # Select top authors by paper count
        top_authors = {name for name, _ in author_paper_counts.most_common(max_authors)}

        G = nx.Graph()
        for name in top_authors:
            G.add_node(name, paper_count=author_paper_counts[name])

        for (a1, a2), count in collab_counts.items():
            if a1 in top_authors and a2 in top_authors and count >= min_collab:
                G.add_edge(a1, a2, weight=count)

        # Remove isolated nodes
        isolated = list(nx.isolates(G))
        G.remove_nodes_from(isolated)
        logger.info("Removed %d isolated author nodes", len(isolated))

        return G

    def _build_institution_collaboration_network(
        self,
        papers: list[dict],
        min_collab: int = 1,
        max_institutions: int = 100,
    ) -> nx.Graph:
        """Build institution collaboration network.

        Two institutions are connected if authors from both appear on the same paper.
        Edge weight = number of co-authored papers.
        """
        collab_counts: Counter = Counter()
        inst_paper_counts: Counter = Counter()

        for paper in papers:
            # Collect unique institutions per paper
            paper_insts = set()
            for author in paper.get("authors", []):
                for aff in author.get("affiliations", []):
                    if aff:
                        paper_insts.add(aff)

            for inst in paper_insts:
                inst_paper_counts[inst] += 1

            # Create edges for all pairs of institutions on the same paper
            for i1, i2 in combinations(sorted(paper_insts), 2):
                collab_counts[(i1, i2)] += 1

        if not collab_counts:
            logger.warning("No institution collaboration data found")
            return nx.Graph()

        # Select top institutions by paper count
        top_insts = {name for name, _ in inst_paper_counts.most_common(max_institutions)}

        G = nx.Graph()
        for name in top_insts:
            G.add_node(name, paper_count=inst_paper_counts[name])

        for (i1, i2), count in collab_counts.items():
            if i1 in top_insts and i2 in top_insts and count >= min_collab:
                G.add_edge(i1, i2, weight=count)

        isolated = list(nx.isolates(G))
        G.remove_nodes_from(isolated)
        logger.info("Removed %d isolated institution nodes", len(isolated))

        return G

    def _build_country_collaboration_network(
        self,
        papers: list[dict] | None,
        country_edges_df: pd.DataFrame | None,
        min_collab: int = 1,
        max_countries: int = 50,
    ) -> nx.Graph:
        """Build country collaboration network.

        Uses pre-computed edges from country_analyzer if available,
        otherwise computes from papers.json.
        """
        if country_edges_df is not None and not country_edges_df.empty:
            return self._build_country_network_from_edges(
                country_edges_df, min_collab, max_countries
            )

        # Fallback: compute from papers
        if papers is None:
            logger.warning("country_collaboration: no papers or edges data available")
            return nx.Graph()

        collab_counts: Counter = Counter()
        country_paper_counts: Counter = Counter()

        for paper in papers:
            paper_countries = set()
            for author in paper.get("authors", []):
                country = author.get("country", "")
                if country:
                    paper_countries.add(country)

            for country in paper_countries:
                country_paper_counts[country] += 1

            for c1, c2 in combinations(sorted(paper_countries), 2):
                collab_counts[(c1, c2)] += 1

        if not collab_counts:
            logger.warning("No country collaboration data found")
            return nx.Graph()

        top_countries = {name for name, _ in country_paper_counts.most_common(max_countries)}

        G = nx.Graph()
        for name in top_countries:
            G.add_node(name, paper_count=country_paper_counts[name])

        for (c1, c2), count in collab_counts.items():
            if c1 in top_countries and c2 in top_countries and count >= min_collab:
                G.add_edge(c1, c2, weight=count)

        isolated = list(nx.isolates(G))
        G.remove_nodes_from(isolated)
        logger.info("Removed %d isolated country nodes", len(isolated))

        return G

    def _build_country_network_from_edges(
        self,
        edges_df: pd.DataFrame,
        min_collab: int = 1,
        max_countries: int = 50,
    ) -> nx.Graph:
        """Build country collaboration network from pre-computed edges CSV."""
        G = nx.Graph()

        # Collect all countries and their total edge weights
        country_weights: Counter = Counter()
        for _, row in edges_df.iterrows():
            c1 = row.get("country_a", "")
            c2 = row.get("country_b", "")
            weight = int(row.get("weight", 1))
            if c1 and c2:
                country_weights[c1] += weight
                country_weights[c2] += weight

        # Filter to top countries
        top_countries = {name for name, _ in country_weights.most_common(max_countries)}

        for name in top_countries:
            G.add_node(name, paper_count=country_weights[name])

        for _, row in edges_df.iterrows():
            c1 = row.get("country_a", "")
            c2 = row.get("country_b", "")
            weight = int(row.get("weight", 1))
            if c1 in top_countries and c2 in top_countries and weight >= min_collab:
                G.add_edge(c1, c2, weight=weight)

        isolated = list(nx.isolates(G))
        G.remove_nodes_from(isolated)
        logger.info("Built country network from edges CSV: %d nodes, %d edges",
                     G.number_of_nodes(), G.number_of_edges())

        return G

    def _build_co_citation_network(
        self,
        papers: list[dict],
        min_co_citation: int = 2,
    ) -> nx.Graph:
        """Build co-citation network.

        Two references are connected if they appear together in the same paper's
        reference list. Edge weight = number of papers citing both.
        Requires papers with references_dois field.
        """
        # Map each reference DOI to the papers that cite it
        ref_cited_by: dict[str, list[str]] = {}
        ref_counts: Counter = Counter()

        for paper in papers:
            refs = paper.get("references_dois", [])
            if not refs:
                continue
            doi = paper.get("doi", "") or paper.get("pmid", "")
            for ref_doi in refs:
                ref_doi_norm = ref_doi.strip().lower()
                if ref_doi_norm:
                    ref_counts[ref_doi_norm] += 1
                    if ref_doi_norm not in ref_cited_by:
                        ref_cited_by[ref_doi_norm] = []
                    ref_cited_by[ref_doi_norm].append(doi)

        if not ref_cited_by:
            logger.warning("No reference data found for co-citation network")
            return nx.Graph()

        # Build co-citation pairs: two refs are co-cited if they appear in
        # the same paper's reference list
        co_cite_counts: Counter = Counter()

        for paper in papers:
            refs = paper.get("references_dois", [])
            if not refs or len(refs) < 2:
                continue
            # Normalize and deduplicate
            norm_refs = sorted(set(
                r.strip().lower() for r in refs if r and r.strip()
            ))
            for r1, r2 in combinations(norm_refs, 2):
                co_cite_counts[(r1, r2)] += 1

        if not co_cite_counts:
            logger.warning("No co-citation pairs found (need papers with ≥2 references)")
            return nx.Graph()

        # Filter by minimum co-citation count and select top references
        top_refs = {ref for ref, _ in ref_counts.most_common(200)}

        G = nx.Graph()
        for ref in top_refs:
            G.add_node(ref, cited_count=ref_counts[ref])

        for (r1, r2), count in co_cite_counts.items():
            if r1 in top_refs and r2 in top_refs and count >= min_co_citation:
                G.add_edge(r1, r2, weight=count)

        isolated = list(nx.isolates(G))
        G.remove_nodes_from(isolated)
        logger.info("Removed %d isolated co-citation nodes", len(isolated))

        return G

    def _build_bibliographic_coupling_network(
        self,
        papers: list[dict],
        min_coupling: int = 2,
    ) -> nx.Graph:
        """Build bibliographic coupling network.

        Two papers are connected if they share references.
        Edge weight = number of shared references.
        Requires papers with references_dois field.
        """
        # Build reference sets for each paper
        paper_refs: dict[str, set[str]] = {}

        for paper in papers:
            refs = paper.get("references_dois", [])
            if not refs:
                continue
            doi = paper.get("doi", "") or paper.get("pmid", "")
            if not doi:
                continue
            norm_refs = set(r.strip().lower() for r in refs if r and r.strip())
            if norm_refs:
                paper_refs[doi] = norm_refs

        if len(paper_refs) < 2:
            logger.warning("Not enough papers with references for bibliographic coupling")
            return nx.Graph()

        # Compute coupling strength for all pairs
        # For efficiency, use inverted index
        ref_to_papers: dict[str, list[str]] = {}
        for doi, refs in paper_refs.items():
            for ref in refs:
                if ref not in ref_to_papers:
                    ref_to_papers[ref] = []
                ref_to_papers[ref].append(doi)

        coupling_counts: Counter = Counter()
        for ref, paper_list in ref_to_papers.items():
            if len(paper_list) < 2:
                continue
            for p1, p2 in combinations(paper_list, 2):
                coupling_counts[(p1, p2)] += 1

        if not coupling_counts:
            logger.warning("No bibliographic coupling pairs found")
            return nx.Graph()

        G = nx.Graph()

        # Add nodes (only papers that have at least one coupling edge)
        coupled_papers = set()
        for (p1, p2), count in coupling_counts.items():
            if count >= min_coupling:
                coupled_papers.add(p1)
                coupled_papers.add(p2)

        for doi in coupled_papers:
            n_refs = len(paper_refs.get(doi, set()))
            G.add_node(doi, reference_count=n_refs)

        for (p1, p2), count in coupling_counts.items():
            if count >= min_coupling:
                G.add_edge(p1, p2, weight=count)

        isolated = list(nx.isolates(G))
        G.remove_nodes_from(isolated)
        logger.info("Removed %d isolated coupling nodes", len(isolated))

        return G

    # ------------------------------------------------------------------
    # Metrics and community detection
    # ------------------------------------------------------------------

    def _compute_centrality_metrics(self, G: nx.Graph) -> pd.DataFrame:
        """Compute centrality metrics for all nodes."""
        logger.info("Computing degree centrality...")
        degree_cent = nx.degree_centrality(G)

        logger.info("Computing betweenness centrality...")
        betweenness_cent = nx.betweenness_centrality(
            G, k=min(100, G.number_of_nodes())
        )

        logger.info("Computing eigenvector centrality...")
        try:
            eigenvector_cent = nx.eigenvector_centrality(G, max_iter=1000)
        except nx.NetworkXException:
            logger.warning("Eigenvector centrality did not converge, using zeros")
            eigenvector_cent = {node: 0.0 for node in G.nodes()}

        logger.info("Computing closeness centrality...")
        closeness_cent = nx.closeness_centrality(G)

        metrics_df = pd.DataFrame({
            "node": list(G.nodes()),
            "degree_centrality": [degree_cent[node] for node in G.nodes()],
            "betweenness_centrality": [betweenness_cent[node] for node in G.nodes()],
            "eigenvector_centrality": [eigenvector_cent[node] for node in G.nodes()],
            "closeness_centrality": [closeness_cent[node] for node in G.nodes()],
            "degree": [G.degree(node) for node in G.nodes()],
        })

        metrics_df = metrics_df.sort_values("degree_centrality", ascending=False)
        return metrics_df

    def _detect_communities(self, G: nx.Graph) -> dict[str, int]:
        """Detect communities using Louvain algorithm."""
        try:
            import community as community_louvain
            communities = community_louvain.best_partition(G)
            logger.info("Detected %d communities (Louvain)",
                        len(set(communities.values())))
            return communities
        except ImportError:
            logger.warning("python-louvain not installed, using greedy modularity")
            communities_gen = nx.community.greedy_modularity_communities(G)
            communities = {}
            for i, community in enumerate(communities_gen):
                for node in community:
                    communities[node] = i
            return communities

    def _create_community_dataframe(
        self,
        communities: dict[str, int],
        G: nx.Graph,
    ) -> pd.DataFrame:
        """Create DataFrame with community assignments and sizes."""
        community_nodes: dict[int, list[str]] = {}
        for node, comm_id in communities.items():
            if comm_id not in community_nodes:
                community_nodes[comm_id] = []
            community_nodes[comm_id].append(node)

        rows = []
        for comm_id, nodes in community_nodes.items():
            rows.append({
                "community_id": comm_id,
                "size": len(nodes),
                "members": ", ".join(sorted(nodes)[:10]),
            })

        if not rows:
            return pd.DataFrame(columns=["community_id", "size", "members"])

        return pd.DataFrame(rows).sort_values("size", ascending=False)

    # ------------------------------------------------------------------
    # Visualization
    # ------------------------------------------------------------------

    def _visualize_network_pyvis(
        self,
        G: nx.Graph,
        communities: dict[str, int],
        output_path: Path,
        height: str = "800px",
        net_type: str = "network",
    ) -> None:
        """Generate interactive HTML visualization using pyvis."""
        from pyvis.network import Network

        net = Network(height=height, width="100%", bgcolor="#ffffff", font_color="black")
        net.force_atlas_2based()

        unique_communities = list(set(communities.values()))
        colors = self._generate_colors(len(unique_communities))
        community_colors = {
            comm_id: colors[i] for i, comm_id in enumerate(unique_communities)
        }

        # Title templates per network type
        title_templates = {
            "co_word": "{node}<br>Degree: {degree}",
            "author_collaboration": "{node}<br>Papers: {paper_count}<br>Degree: {degree}",
            "institution_collaboration": "{node}<br>Papers: {paper_count}<br>Degree: {degree}",
            "country_collaboration": "{node}<br>Papers: {paper_count}<br>Degree: {degree}",
            "co_citation": "{node}<br>Cited: {cited_count}<br>Degree: {degree}",
            "bibliographic_coupling": "{node}<br>Refs: {reference_count}<br>Degree: {degree}",
        }
        title_tmpl = title_templates.get(net_type, "{node}<br>Degree: {degree}")

        for node in G.nodes():
            comm_id = communities.get(node, 0)
            degree = G.degree(node)
            node_data = G.nodes[node]

            title = title_tmpl.format(
                node=node,
                degree=degree,
                paper_count=node_data.get("paper_count", "?"),
                cited_count=node_data.get("cited_count", "?"),
                reference_count=node_data.get("reference_count", "?"),
            )

            net.add_node(
                node,
                label=node if len(str(node)) <= 30 else str(node)[:27] + "...",
                title=title,
                color=community_colors.get(comm_id, "#cccccc"),
                size=min(degree * 2, 30),
            )

        for u, v, data in G.edges(data=True):
            weight = data.get("weight", 1)
            net.add_edge(u, v, value=weight, title=f"Weight: {weight}")

        net.save_graph(str(output_path))

    def _generate_colors(self, n: int) -> list[str]:
        """Generate n distinct colors."""
        import colorsys

        colors = []
        for i in range(n):
            hue = i / n
            rgb = colorsys.hsv_to_rgb(hue, 0.7, 0.9)
            hex_color = "#{:02x}{:02x}{:02x}".format(
                int(rgb[0] * 255),
                int(rgb[1] * 255),
                int(rgb[2] * 255),
            )
            colors.append(hex_color)
        return colors
