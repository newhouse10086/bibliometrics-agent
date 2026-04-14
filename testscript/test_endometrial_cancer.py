"""End-to-end pipeline test: Endometrial cancer, PubMed, 20 papers."""
import logging
import json
import multiprocessing
from pathlib import Path

multiprocessing.freeze_support()

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")

from modules.registry import ModuleRegistry
from modules.base import RunContext, HardwareSpec

if __name__ == '__main__':
    reg = ModuleRegistry()
    reg.auto_discover()

    test_dir = Path("checkpoints/_test_endometrial")
    test_dir.mkdir(parents=True, exist_ok=True)

    def make_context(module_name):
        out = test_dir / module_name
        out.mkdir(parents=True, exist_ok=True)
        return RunContext(
            project_dir=test_dir,
            run_id="_test_endometrial",
            checkpoint_dir=test_dir,
            hardware_info=HardwareSpec(min_memory_gb=4, recommended_memory_gb=8, cpu_cores=4, estimated_runtime_seconds=60),
        )

    def run_module(name, input_data, config=None, context=None):
        print(f"\n{'='*60}")
        print(f"MODULE: {name}")
        print(f"{'='*60}")
        module = reg.get(name)
        cfg = config or {}
        ctx = context or make_context(name)
        try:
            output = module.process(input_data, cfg, ctx)
            print(f"  SUCCESS - output keys: {list(output.keys())}")
            (test_dir / name / "output.json").write_text(
                json.dumps(output, ensure_ascii=False, indent=2, default=str)
            )
            return output, ctx
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return None, None

    # --- Step 1: query_generator (mock - skip LLM, directly provide queries) ---
    print("\n=== Step 1: query_generator (manual) ===")
    qg_output = {
        "semantic_scholar_query": '"endometrial cancer"',
        "pubmed_query": "endometrial cancer",
        "keywords": ["endometrial cancer", "endometrial neoplasm"],
    }
    print(f"  Query: {qg_output['pubmed_query']}")

    # --- Step 2: paper_fetcher (PubMed, 20 papers) ---
    pf_config = {
        "sources": ["pubmed"],
        "max_papers_per_source": 20,
        "pubmed_batch_size": 50,
    }
    pf_input = {
        "semantic_scholar_query": qg_output["semantic_scholar_query"],
        "pubmed_query": qg_output["pubmed_query"],
        "max_papers": 20,
    }
    pf_output, pf_ctx = run_module("paper_fetcher", pf_input, pf_config)

    if not pf_output:
        print("\n!!! paper_fetcher FAILED - aborting pipeline !!!")
        exit(1)

    print(f"\n  Fetched {pf_output.get('num_papers', 0)} papers")
    print(f"  papers_csv: {pf_output.get('papers_csv_path', '')}")
    print(f"  papers_json: {pf_output.get('papers_json_path', '')}")

    papers_json_path = pf_output.get("papers_json_path", "")
    papers_csv_path = pf_output.get("papers_csv_path", "")

    # --- Step 3: country_analyzer ---
    ca_ctx = make_context("country_analyzer")
    ca_output, ca_ctx = run_module("country_analyzer",
        {"papers_json_path": papers_json_path})

    # --- Step 4: bibliometrics_analyzer ---
    ba_ctx = make_context("bibliometrics_analyzer")
    ba_output, ba_ctx = run_module("bibliometrics_analyzer",
        {"papers_json_path": papers_json_path})

    # --- Step 5: preprocessor ---
    pp_input = {
        "documents": papers_csv_path,
        "format": "csv",
        "papers_csv_path": papers_csv_path,
    }
    pp_output, pp_ctx = run_module("preprocessor", pp_input)

    # --- Step 6: frequency_analyzer ---
    if pp_output:
        fa_input = {
            "papers_csv_path": papers_csv_path,
            "dtm_path": pp_output.get("dtm_path", ""),
            "doc_labels_path": pp_output.get("doc_labels_path", ""),
            "vocab_path": pp_output.get("vocab_path", ""),
        }
        fa_output, fa_ctx = run_module("frequency_analyzer", fa_input)
    else:
        fa_output = None
        print("\n=== SKIPPING frequency_analyzer ===")

    # --- Step 7: topic_modeler ---
    if pp_output:
        tm_ctx = make_context("topic_modeler")
        tm_input = {
            "dtm_path": pp_output.get("dtm_path", ""),
            "doc_labels_path": pp_output.get("doc_labels_path", ""),
            "vocab_path": pp_output.get("vocab_path", ""),
        }
        tm_output, tm_ctx = run_module("topic_modeler", tm_input)
    else:
        tm_output = None
        print("\n=== SKIPPING topic_modeler ===")

    # --- Step 8: burst_detector ---
    if fa_output:
        bd_input = {
            "keyword_year_matrix_path": fa_output.get("keyword_year_matrix_path", ""),
        }
        bd_output, bd_ctx = run_module("burst_detector", bd_input)
    else:
        bd_output = None
        print("\n=== SKIPPING burst_detector ===")

    # --- Step 9: tsr_ranker ---
    if tm_output and pp_output:
        tsr_ctx = make_context("tsr_ranker")
        tsr_ctx.previous_outputs = {
            "preprocessor": pp_output,
            "topic_modeler": tm_output,
        }
        tsr_output, tsr_ctx2 = run_module("tsr_ranker", {}, context=tsr_ctx)
    else:
        tsr_output = None
        print("\n=== SKIPPING tsr_ranker ===")

    # --- Step 10: network_analyzer ---
    net_ctx = make_context("network_analyzer")
    net_ctx.previous_outputs = {
        "paper_fetcher": pf_output,
    }
    net_output, net_ctx2 = run_module("network_analyzer", {
        "papers_csv_path": papers_csv_path,
        "papers_json_path": papers_json_path,
    }, context=net_ctx)

    # --- Summary ---
    print("\n" + "="*60)
    print("PIPELINE TEST SUMMARY — Endometrial Cancer (PubMed, 20 papers)")
    print("="*60)
    modules = [
        ("query_generator", qg_output),
        ("paper_fetcher", pf_output),
        ("country_analyzer", ca_output),
        ("bibliometrics_analyzer", ba_output),
        ("preprocessor", pp_output),
        ("frequency_analyzer", fa_output),
        ("topic_modeler", tm_output),
        ("burst_detector", bd_output),
        ("tsr_ranker", tsr_output),
        ("network_analyzer", net_output),
    ]
    passed = 0
    failed = 0
    for name, output in modules:
        status = "OK" if output else "FAIL"
        if output:
            passed += 1
        else:
            failed += 1
        print(f"  [{status}] {name}")
    print(f"\n  Total: {passed} passed, {failed} failed")

    # Show paper_fetcher stats
    if pf_output:
        print(f"\n  Papers fetched: {pf_output.get('num_papers', 'N/A')}")
        if pf_output.get('fields_covered'):
            print(f"  Fields: {pf_output.get('fields_covered')}")
        if pf_output.get('year_distribution'):
            print(f"  Years: {pf_output.get('year_distribution')}")

    # Show bibliometrics summary
    if ba_output and ba_output.get('summary'):
        s = ba_output['summary']
        print(f"\n  Bibliometrics:")
        print(f"    Total papers: {s.get('total_papers')}")
        print(f"    Year range: {s.get('year_range')}")
        print(f"    Unique authors: {s.get('unique_authors')}")
        print(f"    Unique journals: {s.get('unique_journals')}")
        print(f"    Papers with DOI: {s.get('papers_with_doi')}")
        print(f"    Papers with MeSH: {s.get('papers_with_mesh')}")
        print(f"    Mean citations: {s.get('mean_citations_per_paper')}")

    # Show country stats
    if ca_output and ca_output.get('top_countries'):
        print(f"\n  Top countries:")
        for c in ca_output['top_countries'][:5]:
            print(f"    {c['country']}: {c['paper_count']} papers")

    # Show topic modeler stats
    if tm_output and tm_output.get('stats'):
        s = tm_output['stats']
        print(f"\n  Topic modeling:")
        print(f"    Best K: {s.get('best_k', s.get('n_topics'))}")
        print(f"    Best metric: {s.get('best_metric')}")
        print(f"    Coherence: {s.get('coherence')}")
