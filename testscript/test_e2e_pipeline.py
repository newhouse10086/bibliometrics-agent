"""End-to-end pipeline test using cached paper data."""
import logging
import json
import sys
import multiprocessing
from pathlib import Path

multiprocessing.freeze_support()

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")

from modules.registry import ModuleRegistry
from modules.base import RunContext, HardwareSpec

if __name__ == '__main__':
    reg = ModuleRegistry()
    reg.auto_discover()

    # Setup
    test_dir = Path("checkpoints/_test_e2e")
    test_dir.mkdir(parents=True, exist_ok=True)

    # Use cached papers
    papers_csv = Path("checkpoints/d14847af/paper_fetcher/papers.csv")
    assert papers_csv.exists(), "Need cached papers.csv from d14847af"

    def make_context(module_name):
        out = test_dir / module_name
        out.mkdir(parents=True, exist_ok=True)
        return RunContext(
            project_dir=test_dir,
            run_id="_test_e2e",
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
            # Save output for next module
            (test_dir / name / "output.json").write_text(
                json.dumps(output, ensure_ascii=False, indent=2, default=str)
            )
            return output, ctx
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return None, None

    # --- Step 1: query_generator (mock output) ---
    qg_output = {
        "semantic_scholar_query": '"AI in healthcare"',
        "pubmed_query": "AI in healthcare",
        "keywords": ["artificial intelligence", "healthcare"],
    }

    # --- Step 2: paper_fetcher (use cached) ---
    print("\n=== Skipping paper_fetcher, using cached data ===")
    pf_output = {
        "papers_csv_path": str(papers_csv),
        "papers_json_path": "",
        "num_papers": 88,
    }

    # --- Step 3: country_analyzer ---
    ca_input = {"papers_json_path": pf_output["papers_json_path"]}
    ca_output, ca_ctx = run_module("country_analyzer", ca_input)

    # --- Step 4: bibliometrics_analyzer ---
    ba_input = {"papers_json_path": pf_output["papers_json_path"]}
    ba_output, ba_ctx = run_module("bibliometrics_analyzer", ba_input)

    # --- Step 5: preprocessor ---
    pp_input = {
        "documents": str(papers_csv),
        "format": "csv",
        "papers_csv_path": str(papers_csv),
    }
    pp_output, pp_ctx = run_module("preprocessor", pp_input)

    # --- Step 6: frequency_analyzer ---
    if pp_output:
        fa_input = {
            "papers_csv_path": str(papers_csv),
            "dtm_path": pp_output.get("dtm_path", ""),
            "doc_labels_path": pp_output.get("doc_labels_path", ""),
            "vocab_path": pp_output.get("vocab_path", ""),
        }
        fa_output, fa_ctx = run_module("frequency_analyzer", fa_input)
    else:
        fa_output = None
        print("\n=== SKIPPING frequency_analyzer (preprocessor failed) ===")

    # --- Step 7: topic_modeler ---
    if pp_output:
        tm_input = {
            "dtm_path": pp_output.get("dtm_path", ""),
            "doc_labels_path": pp_output.get("doc_labels_path", ""),
            "vocab_path": pp_output.get("vocab_path", ""),
        }
        # Build context with previous outputs
        tm_ctx = make_context("topic_modeler")
        if pp_ctx:
            tm_ctx.previous_outputs = pp_ctx.previous_outputs.copy() if hasattr(pp_ctx, 'previous_outputs') else {}
        tm_output, tm_ctx2 = run_module("topic_modeler", tm_input)
    else:
        tm_output = None
        print("\n=== SKIPPING topic_modeler (preprocessor failed) ===")

    # --- Step 8: burst_detector ---
    if fa_output:
        bd_input = {
            "keyword_year_matrix_path": fa_output.get("keyword_year_matrix_path", ""),
        }
        bd_output, bd_ctx = run_module("burst_detector", bd_input)
    else:
        bd_output = None
        print("\n=== SKIPPING burst_detector (frequency_analyzer failed) ===")

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
        print("\n=== SKIPPING tsr_ranker (upstream failed) ===")

    # --- Step 10: network_analyzer ---
    net_ctx = make_context("network_analyzer")
    net_ctx.previous_outputs = {
        "paper_fetcher": pf_output,
    }
    net_output, net_ctx2 = run_module("network_analyzer", {
        "papers_csv_path": str(papers_csv),
        "papers_json_path": pf_output.get("papers_json_path", ""),
    }, context=net_ctx)

    # --- Summary ---
    print("\n" + "="*60)
    print("PIPELINE TEST SUMMARY")
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
    for name, output in modules:
        status = "OK" if output else "FAIL"
        print(f"  [{status}] {name}")
