"""FastAPI main entry point for bibliometrics-agent web UI."""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from omegaconf import OmegaConf

from app.i18n import get_translations
from core.orchestrator import PipelineOrchestrator
from core.state_manager import StateManager
from core.workspace_manager import WorkspaceManager
from modules.registry import ModuleRegistry

logger = logging.getLogger(__name__)

# Global instances
registry: ModuleRegistry | None = None
state_manager: StateManager | None = None
config: dict = {}
workspace_manager: WorkspaceManager | None = None

# Orchestrator instances for different pipelines
orchestrator_uploaded: PipelineOrchestrator | None = None  # For user-uploaded data
orchestrator_domain: PipelineOrchestrator | None = None     # For research domain queries


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the application on startup."""
    global registry, state_manager, config, workspace_manager, orchestrator_uploaded, orchestrator_domain

    # Load config
    config_path = Path(__file__).parent.parent / "configs" / "default.yaml"
    if config_path.exists():
        config = OmegaConf.to_container(OmegaConf.load(config_path), resolve=True)
    else:
        config = {}

    # Initialize workspace manager
    workspace_base_dir = Path(__file__).parent.parent / config.get("workspace", {}).get("base_dir", "workspaces")
    workspace_manager = WorkspaceManager(workspace_base_dir)

    # Initialize state manager
    checkpoint_dir = Path(__file__).parent.parent / ".pipeline" / "runs"
    state_manager = StateManager(checkpoint_dir)

    # Initialize module registry and auto-discover modules
    registry = ModuleRegistry()
    registry.auto_discover()

    # Create orchestrator for user-uploaded data pipeline
    orchestrator_uploaded = PipelineOrchestrator(
        registry=registry,
        state_manager=state_manager,
        config=config,
        pipeline_order=[
            "data_cleaning_agent",
            "frequency_analyzer",
            "topic_modeler",
            "burst_detector",
        ],
    )

    # Create orchestrator for research domain pipeline
    orchestrator_domain = PipelineOrchestrator(
        registry=registry,
        state_manager=state_manager,
        config=config,
        pipeline_order=[
            "query_generator",
            "paper_fetcher",
            "preprocessor",
            "frequency_analyzer",
            "topic_modeler",
            "burst_detector",
        ],
    )

    yield


app = FastAPI(
    title="Bibliometrics Agent",
    description="LLM-powered bibliometric analysis system",
    version="0.1.0",
    lifespan=lifespan,
)

# Static files and templates
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def index(lang: str = "zh"):
    """Home page with workspace selection."""
    translations = get_translations(lang)

    # Get all workspaces
    workspaces = []
    if workspace_manager:
        workspaces = workspace_manager.list_workspaces()

    return templates.TemplateResponse("index.html", {
        "request": {},
        "t": translations,
        "lang": lang,
        "workspaces": workspaces,
    })


@app.get("/workspaces", response_class=HTMLResponse)
async def list_workspaces(lang: str = "zh"):
    """List all workspaces."""
    translations = get_translations(lang)

    if not workspace_manager:
        raise HTTPException(500, "Workspace manager not initialized")

    workspaces = workspace_manager.list_workspaces()

    # Add stats for each workspace
    for ws in workspaces:
        stats = workspace_manager.get_workspace_stats(ws["safe_name"])
        ws["stats"] = stats

    return templates.TemplateResponse("workspaces.html", {
        "request": {},
        "t": translations,
        "lang": lang,
        "workspaces": workspaces,
    })


@app.post("/workspaces/create")
async def create_workspace(
    name: str = Form(...),
    description: str = Form(""),
    lang: str = Form("zh"),
):
    """Create a new workspace."""
    if not workspace_manager:
        raise HTTPException(500, "Workspace manager not initialized")

    try:
        workspace_dir = workspace_manager.create_workspace(name, description)
        logger.info(f"Created workspace: {name}")

        return RedirectResponse(f"/workspaces?lang={lang}", status_code=303)

    except Exception as e:
        logger.error(f"Failed to create workspace: {e}")
        raise HTTPException(500, f"Failed to create workspace: {str(e)}")


@app.post("/workspaces/delete")
async def delete_workspace(
    name: str = Form(...),
    lang: str = Form("zh"),
):
    """Delete a workspace."""
    if not workspace_manager:
        raise HTTPException(500, "Workspace manager not initialized")

    try:
        success = workspace_manager.delete_workspace(name)
        if success:
            logger.info(f"Deleted workspace: {name}")
            return RedirectResponse(f"/workspaces?lang={lang}", status_code=303)
        else:
            raise HTTPException(404, "Workspace not found")

    except Exception as e:
        logger.error(f"Failed to delete workspace: {e}")
        raise HTTPException(500, f"Failed to delete workspace: {str(e)}")


@app.get("/workspace/{workspace_name}", response_class=HTMLResponse)
async def workspace_dashboard(workspace_name: str, lang: str = "zh"):
    """Show workspace dashboard."""
    translations = get_translations(lang)

    if not workspace_manager:
        raise HTTPException(500, "Workspace manager not initialized")

    workspace_dir = workspace_manager.get_workspace(workspace_name)
    if not workspace_dir:
        raise HTTPException(404, "Workspace not found")

    # Load workspace metadata
    meta_path = workspace_dir / "workspace.json"
    workspace_meta = {}
    if meta_path.exists():
        import json
        with open(meta_path, "r", encoding="utf-8") as f:
            workspace_meta = json.load(f)

    # Get workspace stats
    stats = workspace_manager.get_workspace_stats(workspace_name)

    return templates.TemplateResponse("workspace_dashboard.html", {
        "request": {},
        "t": translations,
        "lang": lang,
        "workspace_name": workspace_name,
        "workspace": workspace_meta,
        "stats": stats,
    })


@app.post("/analyze", response_class=HTMLResponse)
async def automated_analysis(
    research_domain: str = Form(...),
    max_papers: int = Form(500),
    min_topics: int = Form(1),
    max_topics: int = Form(20),
):
    """Start automated analysis from research domain."""
    if orchestrator_domain is None:
        raise HTTPException(500, "Orchestrator not initialized")

    run_id = f"run_{uuid.uuid4().hex[:8]}"

    logger.info("Starting automated analysis for: %s (run_id: %s)", research_domain, run_id)

    # Prepare initial input for query generator
    initial_input = {
        "research_domain": research_domain,
    }

    # Configure modules
    module_config = {
        "paper_fetcher": {
            "max_papers": max_papers,
        },
        "topic_modeler": {
            "min_topics": min_topics,
            "max_topics": max_topics,
        }
    }

    try:
        # Run research domain pipeline: query_generator → paper_fetcher → preprocessor → frequency_analyzer → ...
        result = orchestrator_domain.run(
            run_id=run_id,
            initial_input=initial_input,
            mode="batch",
            resume=False,
        )

        # Copy result files to static directory
        run_output_dir = state_manager.checkpoint_dir / run_id / "topic_modeler"
        static_run_dir = Path(__file__).parent / "static" / run_id
        static_run_dir.mkdir(parents=True, exist_ok=True)

        import shutil
        for file_name in ["ldavis.html", "topic_word_distribution.csv", "doc_topic_distribution.csv"]:
            src = run_output_dir / file_name
            if src.exists():
                shutil.copy(src, static_run_dir / file_name)

        logger.info("Analysis complete, redirecting to results")
        return RedirectResponse(f"/results/{run_id}", status_code=303)

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"Analysis failed: {str(e)}")


@app.post("/upload", response_class=HTMLResponse)
async def upload_file(
    file: UploadFile = File(...),
    workspace_name: str = Form(...),
    lang: str = Form("zh"),
):
    """Handle data file upload and redirect to run page."""
    if not workspace_manager:
        raise HTTPException(500, "Workspace manager not initialized")

    if not file.filename:
        raise HTTPException(400, "No file provided")

    # Check supported file formats
    supported_formats = [".csv", ".xlsx", ".xls", ".json", ".md", ".txt"]
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in supported_formats:
        raise HTTPException(400, f"Unsupported file format. Supported: {', '.join(supported_formats)}")

    # Get workspace directory
    workspace_dir = workspace_manager.get_workspace(workspace_name)
    if not workspace_dir:
        raise HTTPException(404, f"Workspace '{workspace_name}' not found")

    # Save uploaded file to workspace data directory
    data_dir = workspace_dir / "data"
    data_dir.mkdir(exist_ok=True)

    # Use timestamp to avoid filename conflicts
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = data_dir / f"input_{timestamp}{file_ext}"
    content = await file.read()
    file_path.write_bytes(content)

    logger.info(f"Uploaded file to workspace '{workspace_name}': {file_path}")

    # Redirect to run page with workspace context
    return RedirectResponse(f"/run/{workspace_name}/{timestamp}?lang={lang}", status_code=303)


@app.get("/run/{run_id}", response_class=HTMLResponse)
async def run_page(run_id: str, lang: str = "zh"):
    """Show run configuration page."""
    translations = get_translations(lang)
    return templates.TemplateResponse("run.html", {
        "request": {},
        "run_id": run_id,
        "t": translations,
        "lang": lang,
    })


@app.post("/start/{run_id}", response_class=HTMLResponse)
async def start_pipeline(
    run_id: str,
    min_topics: int = Form(1),
    max_topics: int = Form(20),
):
    """Start the analysis pipeline."""
    if orchestrator_uploaded is None:
        raise HTTPException(500, "Orchestrator not initialized")

    data_dir = Path(__file__).parent.parent / "data" / run_id

    # Find uploaded file (could have different extensions)
    uploaded_file = None
    for ext in [".csv", ".xlsx", ".xls", ".json", ".md", ".txt"]:
        potential_file = data_dir / f"input{ext}"
        if potential_file.exists():
            uploaded_file = potential_file
            break

    if not uploaded_file:
        raise HTTPException(404, "Upload file not found")

    # Run pipeline starting with data_cleaning_agent
    initial_input = {
        "data_file_path": str(uploaded_file),
        "target_output": "papers_csv",
    }

    pipeline_config = {
        "modules": {
            "data_cleaning_agent": {
                "auto_execute": True,
            },
            "topic_modeler": {
                "min_topics": min_topics,
                "max_topics": max_topics,
            }
        }
    }

    try:
        # Run uploaded data pipeline: data_cleaning_agent → frequency_analyzer → ...
        result = orchestrator_uploaded.run(
            run_id=run_id,
            initial_input=initial_input,
            mode="batch",
            resume=False,
        )

        # Copy result files to static directory for web access
        run_output_dir = state_manager.checkpoint_dir / run_id / "topic_modeler"
        static_run_dir = Path(__file__).parent / "static" / run_id
        static_run_dir.mkdir(parents=True, exist_ok=True)

        import shutil
        for file_name in ["ldavis.html", "topic_word_distribution.csv", "doc_topic_distribution.csv"]:
            src = run_output_dir / file_name
            if src.exists():
                shutil.copy(src, static_run_dir / file_name)

        # Redirect to results page
        return RedirectResponse(f"/results/{run_id}", status_code=303)

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"Pipeline failed: {e}")


@app.get("/results/{run_id}", response_class=HTMLResponse)
async def results_page(run_id: str, lang: str = "zh"):
    """Show analysis results."""
    if state_manager is None:
        raise HTTPException(500, "State manager not initialized")

    run_dir = state_manager.checkpoint_dir / run_id
    if not run_dir.exists():
        raise HTTPException(404, "Run not found")

    # Load run state
    state = state_manager.get_run_state(run_id)
    translations = get_translations(lang)

    # Load topic modeler stats
    try:
        output = state_manager.load_module_output(run_id, "topic_modeler")
        stats = output.get("stats", {})
    except FileNotFoundError:
        stats = {}

    return templates.TemplateResponse(
        "results.html",
        {
            "request": {},
            "run_id": run_id,
            "state": state,
            "stats": stats,
            "t": translations,
            "lang": lang,
        },
    )


@app.get("/api/status/{run_id}")
async def api_status(run_id: str):
    """API endpoint to check run status."""
    if state_manager is None:
        raise HTTPException(500, "State manager not initialized")

    state = state_manager.get_run_state(run_id)
    return {"run_id": run_id, "state": state}
