# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Bibliometrics Agent (文献综述智能体) — a fully automated bibliometric analysis system powered by LLM. Users enter a research domain and the system generates queries, fetches papers from multiple sources, performs analysis (country distribution, bibliometrics, topic modeling, burst detection, TSR ranking, network analysis), and generates visualizations and HTML reports.

## Quick Start

```bash
pip install -e .
python -m spacy download en_core_web_sm

# Set API key (required for LLM features)
export OPENAI_API_KEY="your-openrouter-key-here"

python run_web.py  # Starts on http://localhost:8001
```

**Configuration**: `configs/default.yaml` — LLM model, workspace dir, module parameters.
**Environment**: See `.env.example` for all variables. Primary: `OPENAI_API_KEY` (used with OpenRouter base URL). LLM model defaults to `qwen/qwen3.6-plus` via OpenRouter.

## Running Tests

```bash
# Quick validation (requires API keys)
python test_integration_quick.py

# Full integration test
python test_integration.py

# Test with cached data (no API calls needed for modules 3+)
python test_pipeline_cached.py

# Individual module tests
python tests/test_tsr_ranker.py
python tests/test_network_analyzer.py
python tests/test_metadata_normalizer.py

# Guardian/LLM tests
python test_guardian_soul.py
python test_llm_integration.py
python test_all_tools.py
```

Tests require real API keys. Set `OPENAI_API_KEY` before running.

## Architecture

### Fixed Pipeline + Guardian Agent

The pipeline is a fixed sequence of 12 modules. The orchestrator (`core/orchestrator.py`) runs them sequentially. On error, GuardianSoul (`core/guardian_soul.py`) activates — an LLM-driven agent loop that analyzes errors, calls tools (read_file, write_file, grep, run_command, web_search), and generates fixes.

**Pipeline order** (`DEFAULT_PIPELINE` in orchestrator):
```
query_generator → paper_fetcher → country_analyzer → bibliometrics_analyzer
→ preprocessor → frequency_analyzer → topic_modeler → burst_detector
→ tsr_ranker → network_analyzer → visualizer → report_generator
```

### Three Workflow Modes

1. **Automated** — Guardian auto-fixes; on failure, skip module and continue
2. **HITL** — Pause at user-selected checkpoints for human review
3. **Pluggable** — Custom module selection/ordering with preset save/load

Set via `pipeline_mode` at project creation. Cannot be changed after.

### Core Components

| Component | File | Role |
|---|---|---|
| Orchestrator | `core/orchestrator.py` | Sequential module execution, Guardian activation, HITL checkpoints, `run_single_module()` for tuning |
| GuardianSoul | `core/guardian_soul.py` | LLM agent loop for error recovery (max 50 steps) |
| TuningAgent | `core/tuning_agent.py` | LLM agent loop for post-pipeline optimization (13 tools, max 30 steps) |
| Communication Hub | `core/communication_hub.py` | WebSocket message routing for AI workflow visualization |
| State Manager | `core/state_manager.py` | Persists run state to `state.json`, caches module outputs, tuning_count/paper_status |
| Pipeline Runner | `core/pipeline_runner.py` | Async pipeline execution, manages `active_runs` dict |
| Workspace Manager | `core/workspace_manager.py` | Project workspace creation and isolation |
| Module Registry | `modules/registry.py` | Auto-discovers modules in `modules/` directory |
| LLM Provider | `core/llm/__init__.py` | OpenAI-compatible API abstraction (`OpenAIProvider`, `create_provider()`) |
| PDF Builder | `scripts/build_pdf.py` | Standalone Markdown→PDF via fpdf2 (CJK support) |
| Web API | `web_api.py` | FastAPI server, REST endpoints + WebSocket |

### Data Flow Between Modules

**Critical**: Orchestrator passes only the immediate predecessor's output as `input_data`. To access earlier module outputs, use `context.previous_outputs`:

```python
def process(self, input_data: dict, config: dict, context: RunContext) -> dict:
    # Immediate predecessor
    data = input_data.get("key")
    # Any earlier module
    if "preprocessor" in context.previous_outputs:
        vocab = context.previous_outputs["preprocessor"]["vocab_path"]
```

### Workspace Isolation

Each project gets an isolated workspace. GuardianSoul fixes are confined to `workspace/modules/` within the project directory — they never modify system source code. The orchestrator loads from `workspace/modules/{mod_name}.py` first, falling back to system modules.

```
workspaces/{project_name}_{run_id}/
├── checkpoints/
│   └── state.json          # Pipeline state (module statuses, tuning_count, paper_status)
├── outputs/
│   └── {module_name}/      # Each module writes here
└── workspace/              # Guardian-generated fixes
    ├── fixes/
    └── modules/
```

**state.json fields**: `status`, `modules`, `pipeline_config`, `tuning_count` (int), `paper_status` (""|"draft"|"pdf_ready"). These last two are synced to `projects_db` via `_sync_progress_from_state()`.

### Thread-Safe Async Communication

GuardianSoul runs in a thread pool but broadcasts via WebSocket (async). Uses `asyncio.run_coroutine_threadsafe(coro, event_loop)` — the orchestrator passes the main event loop to GuardianSoul.

### Pause Mechanism

Pause is async: `comm_hub.steer("PAUSE")` queues a command. The orchestrator checks at each module boundary via `comm_hub.get_steer()`. State is persisted to `state.json` immediately. The web API's `_sync_progress_from_state()` reads `state.json` on each GET request.

## Web API (Port 8001)

### REST Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Web interface |
| GET | `/api/projects` | List all projects |
| POST | `/api/projects` | Create project (supports `pipeline_mode`, `pipeline_order`, `hitl_checkpoints`) |
| GET | `/api/projects/{id}` | Get project details + progress |
| POST | `/api/projects/{id}/start` | Start pipeline |
| POST | `/api/projects/{id}/pause` | Pause (async, takes effect at next module boundary) |
| POST | `/api/projects/{id}/resume` | Resume from paused state |
| POST | `/api/projects/{id}/reset` | Reset project (clear workspace) |
| POST | `/api/projects/{id}/broadcast-progress` | Internal: receive progress from orchestrator |
| POST | `/api/projects/{id}/stop-guardian` | Stop active GuardianSoul |
| POST | `/api/projects/{id}/execute-module` | Manually execute a single module |
| POST | `/api/projects/{id}/skip-module/{name}` | Skip a module |
| POST | `/api/projects/{id}/generate-paper` | Generate paper (Markdown+LaTeX+PDF) |
| POST | `/api/projects/{id}/tune` | Start tuning agent session |
| POST | `/api/projects/{id}/stop-tuning` | Stop active tuning session |
| GET | `/api/projects/{id}/tuning-status` | Get tuning session status |
| GET | `/api/projects/{id}/logs` | Get project logs |
| GET | `/api/projects/{id}/error-logs` | Get error logs |
| GET | `/api/projects/{id}/chat-history` | Get AI chat history |
| POST | `/api/projects/{id}/checkpoint-review` | HITL checkpoint review decision |
| POST | `/api/projects/{id}/inject-module` | Inject module into running pipeline |
| GET | `/api/existing-projects` | Scan workspaces for existing runs |
| POST | `/api/import-project` | Import existing workspace as project |
| GET | `/api/modules` | List available modules |
| GET | `/api/presets` | List saved presets |
| POST | `/api/presets` | Save a preset |

### WebSocket

`WS /ws/{project_id}` — Real-time updates. Message types: `progress_update`, `project_status_update`, `ai_thinking`, `ai_tool_call`, `ai_tool_result`, `ai_decision`, `user_message`.

### Key Web API Internals

- `_sync_progress_from_state()`: Reads `state.json` and updates in-memory `projects_db`. Called by GET endpoints and `broadcast_progress`. Includes zombie detection (state.json says "running" but project not in `active_runs`).
- `projects_db`: In-memory dict. Not the source of truth — `state.json` is. `projects_db` is refreshed from `state.json` via `_sync_progress_from_state()`.

## Module Development

Create a module by inheriting `BaseModule`:

```python
from modules.base import BaseModule, RunContext

class MyModule(BaseModule):
    @property
    def name(self) -> str:
        return "my_module"

    def process(self, input_data: dict, config: dict, context: RunContext) -> dict:
        return {"result": "..."}

    def input_schema(self) -> dict: ...
    def output_schema(self) -> dict: ...
    def config_schema(self) -> dict: ...
```

Place in `modules/` for auto-discovery. Add config to `configs/default.yaml` under `modules.my_module`.

### Existing Modules

| Module | File | Key Output |
|---|---|---|
| query_generator | `query_generator.py` | Search queries for paper fetching |
| paper_fetcher | `paper_fetcher.py` | `papers.json` + `papers.csv` (from PubMed, OpenAlex, Crossref, SS) |
| country_analyzer | `country_analyzer.py` | `country_counts.csv`, `country_year_matrix.csv` |
| bibliometrics_analyzer | `bibliometrics_analyzer.py` | `descriptive_stats.json`, top authors/institutions/journals |
| preprocessor | `preprocessor.py` | DTM, vocab, corpus files |
| frequency_analyzer | `frequency_analyzer.py` | Keyword frequencies with multi-source priority |
| topic_modeler | `topic_modeler.py` | Topic-word/doc-topic distributions (numeric + summary) |
| burst_detector | `burst_detector.py` | Burst detection plots and timelines |
| tsr_ranker | `tsr_ranker.py` | Topic significance ranking scores |
| network_analyzer | `network_analyzer.py` | 5 network types (author/institution/country collab, co-citation, bibliographic coupling) |
| visualizer | `visualizer.py` | Publication-style charts (matplotlib + plotly) |
| report_generator | `report_generator.py` | Self-contained HTML report (Jinja2) |
| paper_generator | `paper_generator.py` | Markdown+LaTeX paper + PDF (fpdf2) |

Utility modules (not in pipeline): `metadata_normalizer.py` (called by paper_fetcher), `data_cleaning_agent.py`.

### Module-Specific Notes

**topic_modeler**: Saves two versions of distributions — numeric matrices for downstream modules, formatted summaries for humans. Downstream modules (tsr_ranker) need the numeric matrices, not summaries.

**burst_detector**: On Windows, must create parent directories before `plt.savefig()`:
```python
plot_path.parent.mkdir(parents=True, exist_ok=True)
plt.savefig(plot_path)
```

**tsr_ranker**: Needs `total_words` from preprocessor output. Falls back to computing from DTM if not available.

**paper_fetcher**: Multi-source with merge — PubMed (primary), OpenAlex, Crossref, Semantic Scholar (fallback). Uses `MetadataNormalizer` for deduplication via DOI matching.

**paper_generator**: LLM outputs Markdown, which is auto-converted to LaTeX via `_md_to_latex()`. The `_escape_latex()` method preserves existing LaTeX commands (does not escape backslashes). PDF is built by `scripts/build_pdf.py` via subprocess.

## Tuning Agent

`core/tuning_agent.py` — Post-pipeline optimization agent. Similar architecture to GuardianSoul (LLM agent loop in thread pool + WebSocket broadcast) but with different system prompt and tool set.

**13 tools**: read_file, read_project_file, write_file, search_files, grep_content, run_command, list_project_outputs, read_module_output, get_module_config, adjust_config, rerun_module, write_analysis_report, finish_tuning.

**Key method**: `TuningToolExecutor._rerun_module()` delegates to `PipelineOrchestrator.run_single_module()` which loads all upstream outputs, backs up current output, and re-runs a single module with config overrides.

**User interaction**: WebSocket `user_message` is routed to active tuning agent via `active_tuning_sessions` dict in `web_api.py`. Tuning count is persisted to `state.json` on session end.

## Paper Generator

`modules/paper_generator.py` (v2.0.0) — Generates bibliometric analysis papers.

**Flow**: LLM generates Markdown sections → converts to LaTeX → builds PDF via fpdf2.

**Dual output**: `main.tex` (editable LaTeX) + `main.pdf` (fpdf2-rendered). The LaTeX is auto-generated from Markdown via `_md_to_latex()` conversion.

**Output structure**:
```
outputs/paper_generator/
├── title.txt, sections/*.md, main.tex, main.pdf
├── refs/references.bib, refs/references.txt
└── figures/*.png
```

**PDF generation**: `scripts/build_pdf.py` is a standalone script using fpdf2. CJK font strategy: NotoSansSC (bundled) → Windows SimHei/SimSun → Helvetica fallback.

**LLM config**: Uses OpenRouter with `qwen/qwen3.6-plus`. API key from `OPENROUTER_API_KEY` or `OPENAI_API_KEY`.

## Frontend

Single-page app at `static/index.html` (pure HTML/CSS/JS, no framework).

**Architecture**: Three-column layout — project sidebar | main workspace (module timeline) | AI chat panel (workflow/logs/errors tabs).

**Key patterns**:
- `currentProjectStatus` global tracks the current project's status without needing API lookups
- `_logRefreshTimer` auto-refreshes logs/errors tabs every 2s when project is running
- `selectProject()` is the only function that connects WebSocket — action handlers (pause/resume/start/reset) must NOT call it to avoid WS reconnection and status loss
- `updateModuleCardsFromProgress()` does incremental DOM updates for `progress_update` messages; only triggers full re-render when pipeline completes/fails, and skips re-render when status is paused/pausing
- WebSocket messages handled in order: `project_status_update` → `progress_update` → chat messages

## Windows-Specific Issues

Always create parent directories before saving files:
```python
output_path.parent.mkdir(parents=True, exist_ok=True)
```

This applies to all modules on Windows — `plt.savefig()`, `pd.to_csv()`, and `Path.write_text()` all fail if the parent directory doesn't exist.
