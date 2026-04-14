"""Test script to verify research domain extraction from existing projects."""

import json
from pathlib import Path

def test_research_domain_extraction():
    """Test extracting research domain from existing projects."""
    checkpoint_dir = Path("checkpoints")

    if not checkpoint_dir.exists():
        print("No checkpoints directory found")
        return

    for run_dir in sorted(checkpoint_dir.iterdir()):
        if not run_dir.is_dir():
            continue

        state_file = run_dir / "state.json"
        if not state_file.exists():
            continue

        print(f"\n{'='*60}")
        print(f"Folder Name: {run_dir.name}")
        print(f"{'='*60}")

        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            pipeline_config = state.get("pipeline_config", {})

            # Extract project name
            project_name = pipeline_config.get("project_name", "")
            run_id = state.get("run_id", run_dir.name)

            # Construct display name
            display_name = f"{project_name}_{run_id}" if project_name else run_id
            print(f"Display Name: {display_name}")

            # Try different sources for research domain
            research_domain = pipeline_config.get("research_domain", "")

            if not research_domain:
                query_output_file = run_dir / "query_generator" / "output.json"
                if query_output_file.exists():
                    try:
                        query_output = json.loads(query_output_file.read_text(encoding="utf-8"))
                        query_str = query_output.get("semantic_scholar_query", "")
                        keywords = query_output.get("keywords", [])

                        if query_str:
                            research_domain = query_str.strip('"').strip("'")
                        elif keywords:
                            research_domain = keywords[0] if isinstance(keywords, list) else str(keywords)
                    except Exception as e:
                        print(f"  Error reading query output: {e}")

            if not research_domain:
                research_domain = pipeline_config.get("project_name", "")

            print(f"Research Domain: {research_domain or '(not found)'}")
            print(f"Project Name: {project_name or '(none)'}")
            print(f"Run ID: {run_id}")
            print(f"Status: {state.get('status', 'unknown')}")
            print(f"Mode: {state.get('mode', 'automated')}")

        except Exception as e:
            print(f"  Error reading state: {e}")

if __name__ == "__main__":
    test_research_domain_extraction()
