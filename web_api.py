"""
Bibliometrics Agent Web API

提供 Web 界面来控制和监控 bibliometrics 分析流程。

核心功能：
1. 项目管理（创建、暂停、恢复、重置）
2. 进度追踪（实时状态、模块进度）
3. 手动控制（选择执行模块、跳过模块）
4. 实时日志（WebSocket 推送）
5. 结果可视化（图表、数据表）
6. 配置管理（参数调整）
"""

# Load environment variables from .env file
import multiprocessing
multiprocessing.freeze_support()

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from pathlib import Path
import os
import asyncio
import json
import logging
from datetime import datetime
import uuid

from core.pipeline_runner import get_runner
from core.communication_hub import get_communication_hub, MessageType
from core.project_logger import get_log_manager

# 创建 FastAPI 应用
app = FastAPI(
    title="Bibliometrics Agent API",
    description="Web API for controlling and monitoring bibliometric analysis",
    version="1.0.0"
)

# CORS 支持
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  数据模型
# ---------------------------------------------------------------------------

class Project(BaseModel):
    """项目模型."""
    id: str
    name: str
    research_domain: str
    status: str = "created"  # created, running, paused, completed, failed
    created_at: str
    updated_at: str
    config: Dict[str, Any] = {}
    tuning_count: int = 0
    paper_status: str = ""  # "" | "draft" | "pdf_ready"


class ModuleStatus(BaseModel):
    """模块状态."""
    name: str
    status: str  # pending, running, completed, failed, skipped
    progress: float = 0.0
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    error: Optional[str] = None
    output_files: List[str] = []


class ProjectProgress(BaseModel):
    """项目进度."""
    project_id: str
    current_stage: str
    modules: List[ModuleStatus]
    total_progress: float
    logs: List[Dict[str, str]] = []


class CreateProjectRequest(BaseModel):
    """创建项目请求."""
    name: str
    research_domain: str
    max_papers: int = 100
    pipeline_mode: str = "automated"  # "automated" | "hitl" | "pluggable"
    pipeline_order: Optional[List[str]] = None
    hitl_checkpoints: Optional[List[str]] = None
    plugin_dir: Optional[str] = None
    preset_name: Optional[str] = None
    config: Optional[Dict[str, Any]] = None


class ModuleExecutionRequest(BaseModel):
    """手动执行模块请求."""
    project_id: str
    module_name: str
    input_data: Optional[Dict[str, Any]] = None


class PaperGenerationRequest(BaseModel):
    """论文生成请求."""
    language: str = "zh"       # "zh" | "en"
    title: str = ""
    compile_pdf: bool = True


class TuningRequest(BaseModel):
    """调优请求."""
    message: str = ""  # 初始指令
    new_session: bool = False  # 是否开启新会话（False 则续接历史）


# ---------------------------------------------------------------------------
#  内存存储（生产环境应使用数据库）
# ---------------------------------------------------------------------------

projects_db: Dict[str, Project] = {}
progress_db: Dict[str, ProjectProgress] = {}
active_tuning_sessions: Dict[str, Any] = {}  # project_id -> TuningAgent
active_connections: List[WebSocket] = []


# ---------------------------------------------------------------------------
#  WebSocket 连接管理
# ---------------------------------------------------------------------------

class ConnectionManager:
    """管理 WebSocket 连接."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.project_connections: Dict[str, List[WebSocket]] = {}  # project_id -> connections

    async def connect(self, websocket: WebSocket, project_id: str = None):
        await websocket.accept()
        self.active_connections.append(websocket)
        if project_id:
            if project_id not in self.project_connections:
                self.project_connections[project_id] = []
            self.project_connections[project_id].append(websocket)
        logger.info(f"WebSocket connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket, project_id: str = None):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        if project_id and project_id in self.project_connections:
            if websocket in self.project_connections[project_id]:
                self.project_connections[project_id].remove(websocket)
        logger.info(f"WebSocket disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        disconnected = []
        for connection in self.active_connections:
            try:
                await asyncio.wait_for(connection.send_json(message), timeout=5.0)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            if conn in self.active_connections:
                self.active_connections.remove(conn)

    async def broadcast_to_project(self, project_id: str, message: dict):
        """Broadcast message to all connections for a specific project."""
        if project_id in self.project_connections:
            disconnected = []
            for connection in self.project_connections[project_id]:
                try:
                    await asyncio.wait_for(connection.send_json(message), timeout=5.0)
                except Exception as e:
                    logger.error(f"Failed to broadcast to project {project_id}: {e}")
                    disconnected.append(connection)
            # Clean up disconnected
            for conn in disconnected:
                if conn in self.project_connections[project_id]:
                    self.project_connections[project_id].remove(conn)


manager = ConnectionManager()


# ---------------------------------------------------------------------------
#  API 端点
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def root():
    """返回前端页面."""
    html_file = Path(__file__).parent / "static" / "index.html"
    if html_file.exists():
        return HTMLResponse(content=html_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Bibliometrics Agent API</h1><p>Frontend not found</p>")


@app.post("/api/projects")
async def create_project(request: CreateProjectRequest):
    """创建新项目."""
    # 检查同名项目
    for existing in projects_db.values():
        if existing.name == request.name:
            return {"success": False, "error": f"项目 '{request.name}' 已存在"}

    project_id = str(uuid.uuid4())[:8]
    timestamp = datetime.now().isoformat()

    # Build config with mode info
    project_config = request.config or {
        "max_papers": request.max_papers,
        "require_abstract": True,
    }
    project_config["pipeline_mode"] = request.pipeline_mode
    project_config["research_domain"] = request.research_domain
    project_config["project_name"] = request.name
    if request.pipeline_order:
        project_config["pipeline_order"] = request.pipeline_order
    if request.hitl_checkpoints:
        project_config.setdefault("hitl", {})["checkpoints"] = request.hitl_checkpoints
    if request.plugin_dir:
        project_config["plugin_dir"] = request.plugin_dir

    project = Project(
        id=project_id,
        name=request.name,
        research_domain=request.research_domain,
        status="created",
        created_at=timestamp,
        updated_at=timestamp,
        config=project_config,
    )

    projects_db[project_id] = project

    # Initialize progress with appropriate modules
    modules = request.pipeline_order or [
        "query_generator", "paper_fetcher", "country_analyzer",
        "bibliometrics_analyzer", "preprocessor", "frequency_analyzer",
        "topic_modeler", "burst_detector", "tsr_ranker", "network_analyzer",
        "visualizer", "report_generator"
    ]

    progress_db[project_id] = ProjectProgress(
        project_id=project_id,
        current_stage="初始化",
        modules=[ModuleStatus(name=m, status="pending") for m in modules],
        total_progress=0.0,
        logs=[]
    )

    logger.info(f"Created project {project_id}: {request.name}")

    return {
        "success": True,
        "project_id": project_id,
        "project": project
    }


@app.get("/api/projects")
async def list_projects():
    """列出所有项目."""
    # Sync project statuses from state.json before returning
    for project_id in list(projects_db.keys()):
        _sync_progress_from_state(project_id)

    return {
        "success": True,
        "projects": list(projects_db.values())
    }


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    """获取项目详情."""
    if project_id not in projects_db:
        return {"success": False, "error": "Project not found"}

    # Sync module statuses from state.json (filesystem) into progress_db (memory)
    _sync_progress_from_state(project_id)

    return {
        "success": True,
        "project": projects_db[project_id],
        "progress": progress_db.get(project_id)
    }


def _sync_progress_from_state(project_id: str):
    """Sync module statuses and project status from state.json into in-memory stores.

    This fixes the bug where completed modules/projects show stale status after
    a server restart or pipeline completion, because progress_db/projects_db are
    in-memory only while state.json persists on disk.
    """
    if project_id not in progress_db:
        return

    try:
        runner = get_runner()
        state_manager = runner.state_managers.get(project_id)
        if not state_manager:
            # Try to find workspace and create state manager
            workspace_dir = runner.workspace_manager.get_workspace(project_id)
            # Fallback: scan workspace folders for one ending with _{project_id}
            if not workspace_dir:
                workspace_base = runner.workspace_manager.base_dir
                for ws_dir in workspace_base.iterdir():
                    if ws_dir.is_dir() and ws_dir.name.endswith(f"_{project_id}"):
                        workspace_dir = ws_dir
                        break
            if workspace_dir and (workspace_dir / "checkpoints" / "state.json").exists():
                state_manager = StateManager.__new__(StateManager)
                state_manager.workspace_dir = workspace_dir
                state_manager.checkpoint_dir = workspace_dir / "checkpoints"
                state_manager.outputs_dir = workspace_dir / "outputs"
                runner.state_managers[project_id] = state_manager
            else:
                return
        state = state_manager.get_run_state(project_id)
        state_modules = state.get("modules", {})

        # Update each module's status in progress_db from state.json
        for mod_status in progress_db[project_id].modules:
            mod_name = mod_status.name
            if mod_name in state_modules:
                new_status = state_modules[mod_name].get("status", "pending")
                if new_status != mod_status.status:
                    mod_status.status = new_status

        # Sync top-level project status from state.json into projects_db
        top_level_status = state.get("status")
        if top_level_status and project_id in projects_db:
            # Detect zombie runs: state.json says "running" but no active task exists
            if top_level_status == "running" and project_id not in runner.active_runs:
                logger.warning(f"Detected zombie run for {project_id} — state.json says running but no active task. Marking as stopped.")
                top_level_status = "stopped"
                # Also fix state.json on disk so it stays consistent
                try:
                    import time
                    state["status"] = "stopped"
                    state["updated_at"] = time.time()
                    state_path = state_manager.checkpoint_dir / "state.json"
                    with open(state_path, "w", encoding="utf-8") as f:
                        json.dump(state, f, ensure_ascii=False, indent=2)
                    # Fix any module stuck in "running" to "pending" so resume works
                    for mod_name, mod_data in state.get("modules", {}).items():
                        if mod_data.get("status") == "running":
                            mod_data["status"] = "pending"
                    with open(state_path, "w", encoding="utf-8") as f:
                        json.dump(state, f, ensure_ascii=False, indent=2)
                except Exception as fix_err:
                    logger.warning(f"Failed to fix zombie state.json: {fix_err}")

            if projects_db[project_id].status != top_level_status:
                projects_db[project_id].status = top_level_status
                projects_db[project_id].updated_at = datetime.now().isoformat()

        # Also sync module-level "running" statuses for zombie runs
        for mod_status in progress_db[project_id].modules:
            if mod_status.status == "running" and project_id not in runner.active_runs:
                # Module was running when pipeline died — reset to pending
                mod_status.status = "pending"

        # Sync tuning_count and paper_status from state.json
        projects_db[project_id].tuning_count = state.get("tuning_count", 0)
        projects_db[project_id].paper_status = state.get("paper_status", "")

    except FileNotFoundError:
        # No state.json yet (project created but never started)
        pass
    except Exception as e:
        logger.debug(f"Failed to sync progress from state: {e}")


@app.post("/api/projects/{project_id}/start")
async def start_project(project_id: str):
    """启动项目."""
    if project_id not in projects_db:
        return {"success": False, "error": "Project not found"}

    project = projects_db[project_id]
    project.status = "running"
    project.updated_at = datetime.now().isoformat()

    # Start actual pipeline execution
    runner = get_runner()
    success = await runner.start_pipeline(
        project_id=project_id,
        project_name=project.name,
        research_domain=project.research_domain,
        config=project.config,
    )

    if success:
        logger.info(f"Started pipeline for project {project_id}")
        # Broadcast update via WebSocket
        await manager.broadcast({
            "type": "project_started",
            "project_id": project_id,
            "project": project.dict(),
        })
        return {"success": True, "project": project}
    else:
        project.status = "failed"
        logger.error(f"Failed to start pipeline for project {project_id}")
        return {"success": False, "error": "Failed to start pipeline"}


@app.post("/api/projects/{project_id}/broadcast-progress")
async def broadcast_progress(project_id: str):
    """广播项目进度更新 (called by orchestrator)."""
    if project_id not in progress_db:
        return {"success": False, "error": "Project not in progress_db"}

    # Sync from state.json (updates both module statuses and project status)
    _sync_progress_from_state(project_id)

    # Broadcast module-level progress
    await manager.broadcast_to_project(project_id, {
        "type": "progress_update",
        "data": progress_db[project_id].dict()
    })

    # Broadcast project-level status change if project exists
    if project_id in projects_db:
        await manager.broadcast_to_project(project_id, {
            "type": "project_status_update",
            "project_id": project_id,
            "status": projects_db[project_id].status,
        })

    return {"success": True}


@app.post("/api/projects/{project_id}/pause")
async def pause_project(project_id: str):
    """暂停项目."""
    if project_id not in projects_db:
        return {"success": False, "error": "Project not found"}

    # Pause actual pipeline via steer command
    runner = get_runner()
    success = await runner.pause_pipeline(project_id)

    if success:
        logger.info(f"Sent pause command for project {project_id}")
        return {"success": True, "message": "Pause command sent"}
    else:
        return {"success": False, "error": "Project is not running"}


@app.post("/api/projects/{project_id}/stop-guardian")
async def stop_guardian(project_id: str):
    """Stop the active GuardianSoul for a project."""
    if project_id not in projects_db:
        return {"success": False, "error": "Project not found"}

    # Send STOP_GUARDIAN steer command
    if project_id in comm_hub.steer_queues:
        comm_hub.set_steer(project_id, "STOP_GUARDIAN")
        logger.info(f"Sent STOP_GUARDIAN command to project {project_id}")
        return {"success": True, "message": "Stop command sent to GuardianSoul"}
    else:
        return {"success": False, "error": "No active GuardianSoul for this project"}


@app.post("/api/projects/{project_id}/resume")
async def resume_project(project_id: str):
    """恢复项目."""
    if project_id not in projects_db:
        return {"success": False, "error": "Project not found"}

    project = projects_db[project_id]
    project.status = "running"
    project.updated_at = datetime.now().isoformat()

    # Resume actual pipeline
    runner = get_runner()
    success = await runner.resume_pipeline(project_id, project.config)

    logger.info(f"Resumed project {project_id}")

    return {"success": True, "project": project}


@app.post("/api/projects/{project_id}/reset")
async def reset_project(project_id: str):
    """重置项目（清空 workspace）."""
    if project_id not in projects_db:
        return {"success": False, "error": "Project not found"}

    project = projects_db[project_id]
    project.status = "created"
    project.updated_at = datetime.now().isoformat()

    # Reset pipeline and clear workspace
    runner = get_runner()
    await runner.reset_pipeline(project_id)

    # 重置进度
    if project_id in progress_db:
        for module in progress_db[project_id].modules:
            module.status = "pending"
            module.progress = 0.0
            module.start_time = None
            module.end_time = None
            module.error = None
        progress_db[project_id].total_progress = 0.0
        progress_db[project_id].logs = []

    logger.info(f"Reset project {project_id}")

    return {"success": True, "project": project}


@app.post("/api/projects/{project_id}/execute-module")
async def execute_module(project_id: str, request: ModuleExecutionRequest):
    """手动执行指定模块."""
    if project_id not in projects_db:
        return {"success": False, "error": "Project not found"}

    # TODO: 实际执行模块

    logger.info(f"Execute module {request.module_name} for project {project_id}")

    return {
        "success": True,
        "message": f"Module {request.module_name} execution started"
    }


@app.post("/api/projects/{project_id}/generate-paper")
async def generate_paper(project_id: str, request: PaperGenerationRequest):
    """Generate a LaTeX/PDF paper from pipeline results."""
    if project_id not in projects_db:
        return {"success": False, "error": "Project not found"}

    runner = get_runner()
    comm_hub = get_communication_hub()

    # Get workspace and state
    state_manager = runner.state_managers.get(project_id)
    if not state_manager:
        workspace_base = runner.workspace_manager.base_dir
        for ws_dir in workspace_base.iterdir():
            if ws_dir.is_dir() and ws_dir.name.endswith(f"_{project_id}"):
                state_manager = StateManager.__new__(StateManager)
                state_manager.workspace_dir = ws_dir
                state_manager.checkpoint_dir = ws_dir / "checkpoints"
                state_manager.outputs_dir = ws_dir / "outputs"
                runner.state_managers[project_id] = state_manager
                break

    if not state_manager:
        return {"success": False, "error": "Workspace not found"}

    state = state_manager.get_run_state(project_id)
    modules_state = state.get("modules", {})

    # Build previous_outputs from completed modules
    previous_outputs = {}
    for mod_name, mod_data in modules_state.items():
        if mod_data.get("status") == "completed" and mod_data.get("output_path"):
            output_path = Path(mod_data["output_path"])
            if output_path.exists():
                try:
                    with open(output_path, "r", encoding="utf-8") as f:
                        previous_outputs[mod_name] = json.load(f)
                except Exception:
                    previous_outputs[mod_name] = {}

    async def _run_paper_gen():
        try:
            await comm_hub.ai_thinking(project_id, "Starting paper generation...")

            from modules.base import RunContext

            # Get or create paper_generator module
            paper_gen = runner.registry.get("paper_generator")
            if not paper_gen:
                await comm_hub.ai_decision(project_id, "paper_generator module not found in registry", confidence=0.0)
                return

            workspace_dir = state_manager.workspace_dir
            output_base = workspace_dir / "outputs"

            context = RunContext(
                project_dir=workspace_dir,
                run_id=project_id,
                checkpoint_dir=state_manager.checkpoint_dir,
                hardware_info={},
                previous_outputs=previous_outputs,
            )

            # Build config
            paper_config = {
                "language": request.language,
                "title": request.title,
                "compile_pdf": request.compile_pdf,
                "max_papers_in_refs": 50,
            }

            # Add LLM config (only if no project-specific config exists)
            llm_state_config = state_manager.get_llm_config(project_id)
            if not llm_state_config:
                api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
                base_url = os.environ.get("OPENROUTER_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
                if os.environ.get("OPENROUTER_API_KEY"):
                    base_url = base_url or "https://openrouter.ai/api/v1"
                model = os.environ.get("LLM_MODEL", "qwen/qwen3.6-plus")
                if api_key:
                    paper_config["llm"] = {
                        "provider": "openai",
                        "api_key": api_key,
                        "base_url": base_url or "https://openrouter.ai/api/v1",
                        "model": model,
                    }

            await comm_hub.ai_thinking(project_id, f"Generating {request.language.upper()} paper...")

            result = await asyncio.get_event_loop().run_in_executor(
                None,
                paper_gen.process,
                {},
                paper_config,
                context,
            )

            # Save output
            state_manager.save_module_output(project_id, "paper_generator", result)

            # Persist paper_status badge
            paper_status = "pdf_ready" if result.get("pdf_path") else "draft"
            state_manager.set_paper_status(project_id, paper_status)
            if project_id in projects_db:
                projects_db[project_id].paper_status = paper_status

            stats = result.get("stats", {})
            msg = (
                f"Paper generation complete! "
                f"{stats.get('n_sections', 0)} sections, "
                f"{stats.get('n_figures', 0)} figures, "
                f"{stats.get('n_references', 0)} references."
            )
            if result.get("pdf_path"):
                msg += f" PDF: {result['pdf_path']}"
            else:
                msg += " PDF compilation skipped/failed."
            await comm_hub.ai_decision(project_id, msg, confidence=0.9)

            # Broadcast status update
            await manager.broadcast_to_project(project_id, {
                "type": "project_status_update",
                "status": "completed",
                "paper_result": result,
            })

        except Exception as e:
            logger.error(f"Paper generation failed: {e}")
            await comm_hub.ai_decision(
                project_id,
                f"Paper generation failed: {str(e)[:300]}",
                confidence=0.0,
            )

    asyncio.create_task(_run_paper_gen())

    return {
        "success": True,
        "message": "Paper generation started. Watch the AI chat panel for progress.",
    }


@app.post("/api/projects/{project_id}/skip-module/{module_name}")
async def skip_module(project_id: str, module_name: str):
    """跳过指定模块."""
    if project_id not in projects_db:
        return {"success": False, "error": "Project not found"}

    if project_id in progress_db:
        for module in progress_db[project_id].modules:
            if module.name == module_name:
                module.status = "skipped"
                break

    logger.info(f"Skipped module {module_name} for project {project_id}")

    return {"success": True}


@app.get("/api/projects/{project_id}/logs")
async def get_logs(project_id: str, lines: int = 100):
    """获取项目日志（从文件读取）."""
    log_manager = get_log_manager()

    # 读取文件日志
    recent_logs = log_manager.read_recent_logs(project_id, lines)

    return {
        "success": True,
        "logs": recent_logs,
        "log_file": str(log_manager.get_log_file_path(project_id))
    }


@app.get("/api/projects/{project_id}/error-logs")
async def get_error_logs(project_id: str):
    """获取项目错误日志."""
    log_manager = get_log_manager()

    # 读取错误级别的日志
    error_logs = log_manager.read_error_logs(project_id)

    return {
        "success": True,
        "errors": error_logs,
        "log_file": str(log_manager.get_log_file_path(project_id))
    }


@app.get("/api/projects/{project_id}/chat-history")
async def get_chat_history(project_id: str, limit: int = 200):
    """Get AI chat message history for a project."""
    comm_hub = get_communication_hub()
    history = comm_hub.get_history(project_id, limit)

    return {
        "success": True,
        "history": history,
        "count": len(history)
    }


@app.delete("/api/projects/{project_id}/chat-history")
async def clear_chat_history(project_id: str):
    """Clear chat history for a project."""
    comm_hub = get_communication_hub()
    if project_id in comm_hub.history:
        comm_hub.history[project_id].clear()
    if project_id in comm_hub.user_messages:
        # Clear the user message queue
        while not comm_hub.user_messages[project_id].empty():
            try:
                comm_hub.user_messages[project_id].get_nowait()
            except asyncio.QueueEmpty:
                break
    logger.info(f"Cleared chat history for project {project_id}")
    return {"success": True}


@app.delete("/api/projects/{project_id}/chat-message/{timestamp}")
async def delete_chat_message(project_id: str, timestamp: str):
    """Delete a specific chat message by timestamp."""
    comm_hub = get_communication_hub()
    if project_id in comm_hub.history:
        comm_hub.history[project_id] = [
            msg for msg in comm_hub.history[project_id]
            if msg.timestamp != timestamp
        ]
    logger.info(f"Deleted chat message {timestamp} for project {project_id}")
    return {"success": True}


async def _respond_directly(project_id: str, user_message: str, mode: str = "auto", image_data: str = None, image_name: str = ""):
    """Respond to user message with tool-calling agent when pipeline is not running.

    Args:
        project_id: Project ID
        user_message: User's message content
        mode: Interaction mode - "auto" (detect), "chat" (simple), "agent" (with tools)
        image_data: Optional base64 image data
        image_name: Optional image filename
    """
    from core.communication_hub import get_communication_hub as _get_hub
    from core.tools import create_default_registry
    from core.llm import ToolDef
    hub = _get_hub()

    def detect_mode(message: str) -> str:
        """Auto-detect which mode to use based on message content."""
        message_lower = message.lower()
        agent_keywords = [
            # Chinese keywords
            "读取", "查看", "分析", "执行", "运行", "文件", "目录", "输出", "日志",
            # English keywords
            "read", "execute", "run", "file", "output", "analyze", "analysis"
        ]
        if any(kw in message_lower for kw in agent_keywords):
            return "agent"
        return "chat"

    # Auto-detect mode if needed
    if mode == "auto":
        mode = detect_mode(user_message)

    try:
        # Broadcast thinking state
        await hub.ai_thinking(project_id, "Processing your message with AI agent...")

        # Try to initialize LLM
        try:
            from core.llm import create_provider, Message
            import os

            api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
            if not api_key:
                await hub.ai_decision(
                    project_id,
                    "No LLM API key configured. Please set OPENROUTER_API_KEY or OPENAI_API_KEY.",
                    confidence=0.0
                )
                return

            # Use OpenAI-compatible provider (OpenRouter is OpenAI-compatible)
            base_url = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
            if os.getenv("OPENROUTER_API_KEY"):
                base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
                api_key = os.getenv("OPENROUTER_API_KEY")

            model = os.getenv("LLM_MODEL", "qwen/qwen3.6-plus")

            llm = create_provider({
                "provider": "openai",
                "api_key": api_key,
                "base_url": base_url,
                "model": model,
            })

            # Chat mode: Simple conversation, no tools
            if mode == "chat":
                system_prompt = """You are a helpful AI assistant for a bibliometric analysis platform.

You can answer general questions about bibliometrics, research methodology, and data analysis.
For tasks that require reading files or executing code, users should switch to Agent mode.

Respond in the same language as the user. Be concise and helpful."""

                messages = [Message(role="system", content=system_prompt)]
                if image_data:
                    user_message += f"\n\n[User attached image: {image_name}]"
                messages.append(Message(role="user", content=user_message))

                response = llm.chat(messages, temperature=0.7, max_tokens=2000)
                await hub.ai_decision(project_id, response.content or "No response generated", confidence=0.8)
                return

            # Agent mode: Continue with tool-calling capabilities
            # Build rich context
            project = projects_db.get(project_id)
            project_info = ""
            if project:
                # Handle created_at - may be string or datetime
                created_str = "N/A"
                if project.created_at:
                    if isinstance(project.created_at, str):
                        created_str = project.created_at
                    else:
                        created_str = project.created_at.strftime('%Y-%m-%d %H:%M:%S')

                project_info = f"""Project: {project.name}
Domain: {project.research_domain}
Status: {project.status}
Created: {created_str}
Pipeline Mode: {project.pipeline_mode if hasattr(project, 'pipeline_mode') else 'automated'}
Tuning Count: {project.tuning_count if hasattr(project, 'tuning_count') else 0}
Paper Status: {project.paper_status if hasattr(project, 'paper_status') else 'None'}"""

                # Add module status info from progress_db
                if project_id in progress_db:
                    modules = progress_db[project_id].modules
                    completed = sum(1 for m in modules if m.status == "completed")
                    total = len(modules)
                    project_info += f"\nModule Progress: {completed}/{total} completed"

                    # Show current module if running
                    current = next((m.name for m in modules if m.status == "running"), None)
                    if current:
                        project_info += f"\nCurrent Module: {current}"

            # Get or create workspace directory
            runner = get_runner()
            state_manager = runner.state_managers.get(project_id)
            workspace_dir = None
            if state_manager:
                workspace_dir = state_manager.workspace_dir
            else:
                # Try to find workspace
                workspace_base = runner.workspace_manager.base_dir
                for ws_dir in workspace_base.iterdir():
                    if ws_dir.is_dir() and ws_dir.name.endswith(f"_{project_id}"):
                        workspace_dir = ws_dir
                        break

            # Fallback to temp directory if workspace not found
            if not workspace_dir:
                workspace_dir = Path(tempfile.mkdtemp(prefix=f"chat_{project_id}_"))

            # Create or load conversation context with persistence
            from core.context import ConversationContext

            # Load config for preserve_recent_messages
            import yaml
            config_path = Path("configs/default.yaml")
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                preserve_recent = config.get("default", {}).get("modules", {}).get("chat_agent", {}).get("preserve_recent_messages", 50)
                max_history = config.get("default", {}).get("modules", {}).get("chat_agent", {}).get("max_history_files", 10)
            else:
                preserve_recent = 50
                max_history = 10

            context_file = workspace_dir / "chat_context.jsonl"
            chat_context = ConversationContext(
                context_file=context_file,
                max_history_files=max_history,
                preserve_recent_messages=preserve_recent,
            )

            # Create tool registry with workspace directory
            tool_registry = create_default_registry(workspace_dir)

            # Define chat tools (subset of tuning tools)
            CHAT_TOOL_DEFS = [
                ToolDef(
                    name="read_file",
                    description="Read any file from the system. Use to inspect project outputs, configs, or logs.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Absolute file path or relative to workspace"},
                            "encoding": {"type": "string", "description": "Encoding, default utf-8", "default": "utf-8"},
                        },
                        "required": ["path"],
                    },
                ),
                ToolDef(
                    name="search_files",
                    description="Search for files matching a pattern in the project workspace.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "description": "Glob pattern, e.g. *.csv, **/*.json"},
                            "directory": {"type": "string", "description": "Search directory (default: workspace root)"},
                        },
                        "required": ["pattern"],
                    },
                ),
                ToolDef(
                    name="grep_content",
                    description="Search file contents with regex pattern.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "description": "Regex pattern to search"},
                            "directory": {"type": "string", "description": "Search directory (optional)"},
                            "file_pattern": {"type": "string", "description": "File pattern, default *", "default": "*"},
                        },
                        "required": ["pattern"],
                    },
                ),
                ToolDef(
                    name="run_command",
                    description="Execute a shell command (with caution). Use for running Python scripts or analysis tools.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "command": {"type": "string", "description": "Command to execute"},
                            "timeout": {"type": "integer", "description": "Timeout in seconds, default 60", "default": 60},
                        },
                        "required": ["command"],
                    },
                ),
                ToolDef(
                    name="list_project_outputs",
                    description="List all output files in the project workspace with sizes.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "directory": {"type": "string", "description": "Subdirectory to list (default: outputs/)", "default": "outputs"},
                        },
                    },
                ),
            ]

            system_prompt = f"""You are an AI assistant for a bibliometric analysis platform with TOOL-CALLING capabilities.

You have access to the following tools:
- read_file: Read any file from the project (outputs, configs, logs)
- search_files: Find files matching patterns
- grep_content: Search file contents with regex
- run_command: Execute shell commands (Python scripts, etc.)
- list_project_outputs: List all output files in the project

## Current Project Status
{project_info if project_info else "No project selected"}

## Workspace Location
{workspace_dir if workspace_dir else "Workspace not found"}

## Available Data
- Check outputs/ directory for module results (CSV, JSON, figures)
- Check checkpoints/state.json for detailed pipeline state
- Check outputs/logs/ for execution logs

## How to Use Tools
1. **Understanding the project**: Use `list_project_outputs` to see what data is available
2. **Inspecting results**: Use `read_file` to view CSV/JSON outputs, logs, or state
3. **Searching data**: Use `grep_content` to search across multiple files
4. **Running analysis**: Use `run_command` to execute Python scripts or custom analysis

## Important
- You CAN execute code, read files, and run commands
- When the user asks "can you execute code", answer YES and demonstrate it
- Always start by exploring the workspace to understand what data is available
- Provide specific insights based on actual project data, not generic responses
- Respond in the same language as the user
- Be concise but thorough"""

            # Initialize context with system prompt if empty
            if len(chat_context) == 0:
                chat_context.append_message(Message(role="system", content=system_prompt))

            # Add user message to context
            user_prompt = user_message
            if image_data:
                user_prompt += f"\n\n[User attached image: {image_name}]"

            chat_context.append_message(Message(role="user", content=user_prompt))

            # Get messages from persistent context
            messages = chat_context.history

            # Agent loop with tool calling
            # Load max_steps from config
            import yaml
            config_path = Path("configs/default.yaml")
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                max_steps = config.get("default", {}).get("modules", {}).get("chat_agent", {}).get("max_steps", 10)
            else:
                max_steps = 10

            # Handle unlimited steps (-1)
            if max_steps == -1:
                max_steps = 999999  # Very large number for "unlimited"

            step = 0

            while step < max_steps:
                step += 1

                # Call LLM with tools
                response = llm.chat(messages, tools=CHAT_TOOL_DEFS)

                # If no tool calls, we're done
                if not response.tool_calls:
                    # Save final assistant response to context
                    if response.content:
                        chat_context.append_message(Message(
                            role="assistant",
                            content=response.content
                        ))
                    # Broadcast final response
                    await hub.ai_decision(project_id, response.content, confidence=0.8)
                    break

                # Add assistant message to context (persists automatically)
                chat_context.append_message(Message(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=response.tool_calls
                ))

                # Broadcast thinking
                if response.content:
                    await hub.ai_thinking(project_id, response.content)

                # Execute tool calls
                for tool_call in response.tool_calls:
                    tool_name = tool_call.name
                    # Parse arguments if it's a JSON string
                    tool_args = tool_call.arguments
                    if isinstance(tool_args, str):
                        import json
                        try:
                            tool_args = json.loads(tool_args)
                        except json.JSONDecodeError:
                            tool_args = {}

                    # Broadcast tool call
                    await hub.ai_tool_call(project_id, tool_name, tool_args)

                    try:
                        # Execute tool
                        if tool_name == "read_file":
                            file_path = tool_args.get("path", "")
                            encoding = tool_args.get("encoding", "utf-8")

                            # Resolve relative path
                            if workspace_dir and not Path(file_path).is_absolute():
                                file_path = str(workspace_dir / file_path)

                            result = tool_registry.get("ReadFile").run(file_path=file_path, encoding=encoding)
                            tool_result = result.output if result.success else f"Error: {result.error}"

                        elif tool_name == "search_files":
                            pattern = tool_args.get("pattern", "")
                            directory = tool_args.get("directory")
                            if workspace_dir and directory is None:
                                directory = str(workspace_dir)
                            result = tool_registry.get("Glob").run(pattern=pattern, directory=directory)
                            tool_result = result.output if result.success else f"Error: {result.error}"

                        elif tool_name == "grep_content":
                            pattern = tool_args.get("pattern", "")
                            directory = tool_args.get("directory")
                            file_pattern = tool_args.get("file_pattern", "*")
                            if workspace_dir and directory is None:
                                directory = str(workspace_dir)
                            result = tool_registry.get("Grep").run(
                                pattern=pattern,
                                directory=directory,
                                file_pattern=file_pattern
                            )
                            tool_result = result.output if result.success else f"Error: {result.error}"

                        elif tool_name == "run_command":
                            command = tool_args.get("command", "")
                            timeout = tool_args.get("timeout", 60)
                            result = tool_registry.get("Shell").run(command=command, timeout=timeout)
                            tool_result = result.output if result.success else f"Error: {result.error}"

                        elif tool_name == "list_project_outputs":
                            directory = tool_args.get("directory", "outputs")
                            if workspace_dir:
                                target_dir = workspace_dir / directory
                                if target_dir.exists():
                                    import os
                                    files = []
                                    for root, dirs, filenames in os.walk(target_dir):
                                        for filename in filenames:
                                            filepath = Path(root) / filename
                                            size = filepath.stat().st_size
                                            relpath = filepath.relative_to(workspace_dir)
                                            files.append(f"{relpath} ({size} bytes)")
                                    tool_result = "\n".join(files[:100]) if files else "No files found"
                                else:
                                    tool_result = f"Directory {directory} does not exist"
                            else:
                                tool_result = "No workspace found"

                        else:
                            tool_result = f"Unknown tool: {tool_name}"

                    except Exception as e:
                        tool_result = f"Error executing {tool_name}: {str(e)}"

                    # Broadcast tool result
                    await hub.ai_tool_result(project_id, tool_name, tool_result[:500])  # Truncate long results

                    # Add tool result to messages
                    # Add tool result to context (persists automatically)
                    chat_context.append_message(Message(
                        role="tool",
                        content=tool_result,
                        name=tool_name,
                        tool_call_id=tool_call.id
                    ))

            if step >= max_steps:
                await hub.ai_decision(project_id, "I've reached the maximum number of steps. Let me know if you need more help!", confidence=0.7)

        except Exception as llm_error:
            logger.error(f"LLM response failed: {llm_error}")
            await hub.ai_decision(
                project_id,
                f"Sorry, I couldn't process your message. Error: {str(llm_error)[:200]}",
                confidence=0.0
            )

    except Exception as e:
        logger.error(f"Direct response failed: {e}")


# ---------------------------------------------------------------------------
#  调优端点
# ---------------------------------------------------------------------------

@app.post("/api/projects/{project_id}/tune")
async def start_tuning(project_id: str, request: TuningRequest):
    """Start a tuning session for the project."""
    if project_id not in projects_db:
        return {"success": False, "error": "Project not found"}

    if project_id in active_tuning_sessions:
        return {"success": False, "error": "Tuning session already active"}

    runner = get_runner()
    comm_hub = get_communication_hub()

    # Get workspace
    state_manager = runner.state_managers.get(project_id)
    if not state_manager:
        workspace_base = runner.workspace_manager.base_dir
        for ws_dir in workspace_base.iterdir():
            if ws_dir.is_dir() and ws_dir.name.endswith(f"_{project_id}"):
                state_manager = StateManager.__new__(StateManager)
                state_manager.workspace_dir = ws_dir
                state_manager.checkpoint_dir = ws_dir / "checkpoints"
                state_manager.outputs_dir = ws_dir / "outputs"
                runner.state_managers[project_id] = state_manager
                break

    if not state_manager:
        return {"success": False, "error": "Workspace not found"}

    workspace_dir = state_manager.workspace_dir

    # Check for project-specific LLM config
    llm_config = state_manager.get_llm_config(project_id)

    from core.llm import create_provider as _create_provider

    if llm_config:
        # Use project-specific config
        llm = _create_provider(llm_config_from_state=llm_config)
    else:
        # Fallback to environment variables
        api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
        base_url = os.environ.get("OPENROUTER_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        if os.environ.get("OPENROUTER_API_KEY"):
            base_url = base_url or "https://openrouter.ai/api/v1"
        model = os.environ.get("LLM_MODEL", "qwen/qwen3.6-plus")

        if not api_key:
            return {"success": False, "error": "No LLM API key configured"}

        llm = _create_provider({
            "provider": "openai",
            "api_key": api_key,
            "base_url": base_url or "https://openrouter.ai/api/v1",
            "model": model,
        })

    from core.tuning_agent import TuningAgent
    loop = asyncio.get_event_loop()

    # Read max_steps and max_history_files from config
    import yaml
    config_path = Path("configs/default.yaml")
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        tuning_config = config.get("default", {}).get("modules", {}).get("tuning_agent", {})
        max_steps = tuning_config.get("max_steps", 30)
        max_history_files = tuning_config.get("max_history_files", 10)
        preserve_recent_messages = tuning_config.get("preserve_recent_messages", 20)
    else:
        max_steps = 30
        max_history_files = 10
        preserve_recent_messages = 20

    if max_steps == -1:
        max_steps = 999999  # Very large number for "unlimited"

    agent = TuningAgent(
        llm=llm,
        workspace_dir=workspace_dir,
        project_id=project_id,
        event_loop=loop,
        max_steps=max_steps,
        temperature=0.5,
        max_history_files=max_history_files,
        preserve_recent_messages=preserve_recent_messages,
    )

    active_tuning_sessions[project_id] = agent

    async def _run_tuning():
        try:
            result = await loop.run_in_executor(
                None,
                agent.activate,
                request.message,
                request.new_session,  # Pass new_session parameter
            )
            logger.info(f"Tuning session completed for {project_id}: {result.summary[:200]}")
        except Exception as e:
            logger.error(f"Tuning session failed for {project_id}: {e}")
            await comm_hub.ai_decision(
                project_id,
                f"Tuning session failed: {str(e)[:200]}",
                confidence=0.0,
            )
        finally:
            # Persist tuning count
            sm = runner.state_managers.get(project_id) if runner else None
            if sm:
                new_count = sm.increment_tuning_count(project_id)
                if project_id in projects_db:
                    projects_db[project_id].tuning_count = new_count
            if project_id in active_tuning_sessions:
                del active_tuning_sessions[project_id]

    asyncio.create_task(_run_tuning())

    return {
        "success": True,
        "message": "Tuning session started. Watch the AI chat panel for real-time analysis.",
    }


@app.post("/api/projects/{project_id}/stop-tuning")
async def stop_tuning(project_id: str):
    """Stop the active tuning session."""
    agent = active_tuning_sessions.get(project_id)
    if not agent:
        return {"success": False, "error": "No active tuning session"}

    agent.request_stop()
    return {"success": True, "message": "Stop signal sent to tuning agent"}


@app.get("/api/projects/{project_id}/tuning-status")
async def tuning_status(project_id: str):
    """Get the current tuning session status."""
    agent = active_tuning_sessions.get(project_id)
    if not agent:
        return {"success": True, "active": False}
    return {
        "success": True,
        "active": True,
        "messages_count": len(agent.messages),
    }


@app.get("/api/projects/{project_id}/context-status")
async def context_status(project_id: str):
    """获取 GuardianSoul/TuningAgent 的上下文使用情况."""
    # Check tuning agent first (has priority if active)
    tuning_agent = active_tuning_sessions.get(project_id)
    if tuning_agent and hasattr(tuning_agent, "context"):
        context = tuning_agent.context
        return {
            "success": True,
            "active_session": "tuning",
            "estimated_tokens": context.estimated_tokens,
            "max_tokens": context.max_context_tokens,
            "usage_ratio": context.estimated_tokens / context.max_context_tokens,
            "message_count": len(context),
            "should_compact": context.should_compact(),
        }

    # Check if there's a guardian context file
    from core.context import ConversationContext
    runner = get_runner()
    workspace_base = runner.workspace_manager.base_dir

    # Find workspace directory for this project
    workspace_dir = None
    for ws_dir in workspace_base.iterdir():
        if ws_dir.is_dir() and ws_dir.name.endswith(f"_{project_id}"):
            workspace_dir = ws_dir
            break

    if not workspace_dir:
        return {
            "success": False,
            "error": "Workspace not found",
        }

    guardian_context_file = workspace_dir / "workspace" / "guardian_context.jsonl"
    tuning_context_file = workspace_dir / "workspace" / "tuning_context.jsonl"

    # Prefer tuning context if exists (most recent session)
    context_file = tuning_context_file if tuning_context_file.exists() else guardian_context_file
    session_type = "tuning" if tuning_context_file.exists() else "guardian"

    if context_file.exists():
        context = ConversationContext(context_file=context_file)
        return {
            "success": True,
            "active_session": session_type,
            "estimated_tokens": context.estimated_tokens,
            "max_tokens": context.max_context_tokens,
            "usage_ratio": context.estimated_tokens / context.max_context_tokens,
            "message_count": len(context),
            "should_compact": context.should_compact(),
        }

    return {
        "success": True,
        "active_session": None,
        "estimated_tokens": 0,
        "max_tokens": 128000,
        "usage_ratio": 0.0,
        "message_count": 0,
        "should_compact": False,
    }


@app.post("/api/projects/{project_id}/compact-context")
async def compact_context(project_id: str):
    """手动触发上下文压缩."""
    from core.context import ConversationContext
    from core.llm import create_provider

    runner = get_runner()
    workspace_base = runner.workspace_manager.base_dir

    # Find workspace directory for this project
    workspace_dir = None
    for ws_dir in workspace_base.iterdir():
        if ws_dir.is_dir() and ws_dir.name.endswith(f"_{project_id}"):
            workspace_dir = ws_dir
            break

    if not workspace_dir:
        return {"success": False, "error": "Workspace not found"}

    guardian_context_file = workspace_dir / "workspace" / "guardian_context.jsonl"
    tuning_context_file = workspace_dir / "workspace" / "tuning_context.jsonl"

    # Determine which context to compact (prefer tuning if active)
    tuning_agent = active_tuning_sessions.get(project_id)
    if tuning_agent and hasattr(tuning_agent, "context"):
        context = tuning_agent.context
        session_type = "tuning"
    elif tuning_context_file.exists():
        context = ConversationContext(context_file=tuning_context_file)
        session_type = "tuning"
    elif guardian_context_file.exists():
        context = ConversationContext(context_file=guardian_context_file)
        session_type = "guardian"
    else:
        return {"success": False, "error": "No context file found"}

    if not context.should_compact():
        return {
            "success": True,
            "compacted": False,
            "message": "Context usage is below threshold, no need to compact",
            "estimated_tokens": context.estimated_tokens,
            "usage_ratio": context.estimated_tokens / context.max_context_tokens,
        }

    # Get LLM provider with project-specific config
    state_manager = get_state_manager(project_id)
    llm_config = state_manager.get_llm_config(project_id)
    llm = create_provider(llm_config_from_state=llm_config)

    try:
        saved = context.compact(llm, preserve_recent=15)
        return {
            "success": True,
            "compacted": True,
            "tokens_saved": saved,
            "new_token_count": context.estimated_tokens,
            "new_usage_ratio": context.estimated_tokens / context.max_context_tokens,
            "message_count": len(context),
        }
    except Exception as e:
        logger.error(f"Failed to compact context for {project_id}: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/projects/{project_id}/llm-config")
async def get_llm_config(project_id: str):
    """获取项目的 LLM 配置."""
    try:
        state_manager = get_state_manager(project_id)
        llm_config = state_manager.get_llm_config(project_id)

        # Mask API key for security (show only last 4 characters)
        if "api_key" in llm_config and llm_config["api_key"]:
            api_key = llm_config["api_key"]
            llm_config["api_key"] = f"...{api_key[-4:]}" if len(api_key) > 4 else "****"

        return {
            "success": True,
            "llm_config": llm_config,
        }
    except Exception as e:
        logger.error(f"Failed to get LLM config for {project_id}: {e}")
        return {"success": False, "error": str(e)}


@app.post("/api/projects/{project_id}/llm-config")
async def set_llm_config(project_id: str, request: dict):
    """设置项目的 LLM 配置."""
    try:
        state_manager = get_state_manager(project_id)

        # Validate required fields
        provider = request.get("provider", "openai")
        if provider not in ["openai", "mock"]:
            return {"success": False, "error": "Invalid provider. Must be 'openai' or 'mock'"}

        llm_config = {
            "provider": provider,
            "api_key": request.get("api_key", ""),
            "base_url": request.get("base_url", ""),
            "model": request.get("model", "gpt-4o"),
        }

        # Remove empty fields
        llm_config = {k: v for k, v in llm_config.items() if v}

        state_manager.set_llm_config(project_id, llm_config)

        return {
            "success": True,
            "message": "LLM config updated successfully",
            "llm_config": llm_config,
        }
    except Exception as e:
        logger.error(f"Failed to set LLM config for {project_id}: {e}")
        return {"success": False, "error": str(e)}


@app.post("/api/projects/{project_id}/test-llm-config")
async def test_llm_config(project_id: str, request: dict):
    """测试 LLM 配置是否有效."""
    from core.llm import create_provider, Message

    try:
        llm_config = {
            "provider": request.get("provider", "openai"),
            "api_key": request.get("api_key", ""),
            "base_url": request.get("base_url", ""),
            "model": request.get("model", "gpt-4o"),
        }

        # Create provider with test config
        llm = create_provider(llm_config_from_state=llm_config)

        # Send a simple test message
        response = llm.chat(
            messages=[Message(role="user", content="Say 'OK' if you can hear me.")],
            temperature=0.1,
            max_tokens=50,
        )

        return {
            "success": True,
            "message": "LLM connection successful",
            "response_preview": response.content[:100] if response.content else "",
            "usage": response.usage,
        }
    except Exception as e:
        logger.error(f"LLM config test failed for {project_id}: {e}")
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
        }


# ---------------------------------------------------------------------------
#  模块和预设管理端点
# ---------------------------------------------------------------------------

@app.get("/api/modules")
async def list_available_modules():
    """列出所有可用模块（内置+插件）."""
    runner = get_runner()
    module_names = runner.registry.list_modules()
    modules = []
    for name in module_names:
        mod = runner.registry.get(name)
        modules.append({
            "name": name,
            "version": mod.version,
            "input_schema": mod.input_schema(),
            "output_schema": mod.output_schema(),
            "config_schema": mod.config_schema(),
        })
    return {"success": True, "modules": modules}


@app.get("/api/presets")
async def list_presets():
    """列出已保存的预设."""
    # Presets are global, stored in workspaces/presets/
    from core.workspace_manager import get_default_workspace_dir
    from core.state_manager import StateManager
    presets_base = get_default_workspace_dir().parent / "presets"
    if not presets_base.exists():
        return {"success": True, "presets": []}

    result = []
    for preset_file in presets_base.glob("*.json"):
        try:
            preset = json.loads(preset_file.read_text(encoding="utf-8"))
            result.append(preset)
        except Exception:
            continue
    return {"success": True, "presets": result}


class SavePresetRequest(BaseModel):
    name: str
    pipeline_order: List[str]
    module_configs: Optional[Dict[str, Any]] = None


@app.post("/api/presets")
async def save_preset(request: SavePresetRequest):
    """保存模块配置预设到全局预设目录."""
    from core.workspace_manager import get_default_workspace_dir
    from core.state_manager import StateManager
    import time

    # 使用全局预设目录
    presets_base = get_default_workspace_dir().parent / "presets"
    presets_base.mkdir(parents=True, exist_ok=True)

    preset_file = presets_base / f"{request.name}.json"
    preset_data = {
        "name": request.name,
        "pipeline_order": request.pipeline_order,
        "module_configs": request.module_configs or {},
        "saved_at": time.time(),
    }

    preset_file.write_text(
        json.dumps(preset_data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    logger.info(f"Saved preset: {request.name}")
    return {"success": True}


class CheckpointReviewRequest(BaseModel):
    module_name: str
    decision: str  # "approve", "reject", "adjust"
    adjusted_params: Optional[Dict[str, Any]] = None


class InjectModuleRequest(BaseModel):
    module_name: str
    insert_after: Optional[str] = None
    insert_before: Optional[str] = None


@app.post("/api/projects/{project_id}/checkpoint-review")
async def checkpoint_review(project_id: str, request: CheckpointReviewRequest):
    """提交 HITL 检查点审核决定."""
    comm_hub = get_communication_hub()
    comm_hub.steer(project_id, f"{request.decision.upper()}:{request.module_name}")

    # Save decision to state
    runner = get_runner()
    state_manager = runner.state_managers.get(project_id)
    if not state_manager:
        # Create state manager from workspace
        workspace_dir = runner.workspace_manager.get_workspace(project_id)
        if workspace_dir:
            state_manager = StateManager(workspace_dir)
            runner.state_managers[project_id] = state_manager

    if state_manager:
        state_manager.resolve_checkpoint(
            project_id, request.module_name, request.decision, request.adjusted_params
        )
        logger.info(f"Checkpoint review for {project_id}/{request.module_name}: {request.decision}")
        return {"success": True}
    else:
        logger.error(f"No state manager found for project {project_id}")
        return {"success": False, "error": "State manager not found"}


@app.post("/api/projects/{project_id}/inject-module")
async def inject_module(project_id: str, request: InjectModuleRequest):
    """Programmatically inject a module into a running pipeline (no WebSocket confirmation needed)."""
    runner = get_runner()
    orchestrator = runner.active_orchestrators.get(project_id)
    if not orchestrator:
        return {"success": False, "error": "No active pipeline for this project"}
    success = orchestrator.inject_module(
        module_name=request.module_name,
        insert_after=request.insert_after,
        insert_before=request.insert_before,
        run_id=project_id,
    )
    return {"success": success}


@app.get("/api/existing-projects")
async def list_existing_projects():
    """Scan workspaces/ directory for existing project runs."""
    runner = get_runner()
    workspace_dir = runner.workspace_manager.base_dir
    projects = []

    if not workspace_dir.exists():
        return {"success": True, "projects": []}

    # Scan all workspace subdirectories
    for ws_dir in sorted(workspace_dir.iterdir()):
        if not ws_dir.is_dir():
            continue

        state_file = ws_dir / "checkpoints" / "state.json"
        if not state_file.exists():
            continue

        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            modules = state.get("modules", {})
            completed = sum(1 for m in modules.values() if m.get("status") == "completed")
            total = len(modules) or 0
            pipeline_config = state.get("pipeline_config", {})

            # Extract project name from workspace folder name: {project_name}_{run_id}
            folder_name = ws_dir.name
            parts = folder_name.rsplit("_", 1)
            if len(parts) == 2:
                project_name, run_id = parts
            else:
                project_name = ""
                run_id = folder_name

            # Extract research domain
            research_domain = pipeline_config.get("research_domain", "")

            # Fallback: Try to extract from query_generator/output.json
            if not research_domain:
                query_output_file = ws_dir / "outputs" / "query_generator" / "output.json"
                if query_output_file.exists():
                    try:
                        query_output = json.loads(query_output_file.read_text(encoding="utf-8"))
                        query_str = query_output.get("semantic_scholar_query", "")
                        keywords = query_output.get("keywords", [])

                        if query_str:
                            research_domain = query_str.strip('"').strip("'")
                        elif keywords:
                            research_domain = keywords[0] if isinstance(keywords, list) else str(keywords)
                    except Exception:
                        pass

            # Construct display name
            display_name = f"{project_name}_{run_id}" if project_name else run_id

            module_names = list(modules.keys())
            created_ts = state.get("created_at", 0)
            created_str = ""
            if created_ts:
                from datetime import datetime as _dt
                try:
                    created_str = _dt.fromtimestamp(created_ts).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    created_str = str(created_ts)

            # Detect zombie runs: state.json says "running" but no active task
            status = state.get("status", "unknown")
            if status == "running" and run_id not in runner.active_runs:
                status = "stopped"
                # Fix state.json on disk
                try:
                    state["status"] = "stopped"
                    import time
                    state["updated_at"] = time.time()
                    # Fix modules stuck in "running" to "pending"
                    for mod_name, mod_data in state.get("modules", {}).items():
                        if mod_data.get("status") == "running":
                            mod_data["status"] = "pending"
                    with open(state_file, "w", encoding="utf-8") as f:
                        json.dump(state, f, ensure_ascii=False, indent=2)
                    logger.info(f"Fixed zombie state for {run_id}: running → stopped")
                except Exception as fix_err:
                    logger.warning(f"Failed to fix zombie state.json for {run_id}: {fix_err}")
                # Recalculate completed count after fixing
                completed = sum(1 for m in state.get("modules", {}).values() if m.get("status") == "completed")

            projects.append({
                "run_id": run_id,
                "folder_name": folder_name,
                "display_name": display_name,
                "project_name": project_name,
                "research_domain": research_domain,
                "status": status,
                "mode": state.get("mode", "automated"),
                "module_count": total,
                "completed_count": completed,
                "module_names": module_names,
                "created_at": created_str,
            })
        except Exception:
            continue

    return {"success": True, "projects": projects}


class ImportProjectRequest(BaseModel):
    run_id: str


@app.post("/api/import-project")
async def import_existing_project(request: ImportProjectRequest):
    """Import an existing workspace as a project."""
    run_id = request.run_id
    runner = get_runner()

    # Find workspace directory
    workspace_dir = runner.workspace_manager.get_workspace(run_id)
    if not workspace_dir:
        # Try to find by scanning workspace folders
        workspace_base = runner.workspace_manager.base_dir
        for ws_dir in workspace_base.iterdir():
            if ws_dir.is_dir() and ws_dir.name.endswith(f"_{run_id}"):
                workspace_dir = ws_dir
                break

    if not workspace_dir or not (workspace_dir / "checkpoints" / "state.json").exists():
        return {"success": False, "error": f"Run {run_id} not found"}

    # Create state manager for this workspace
    from core.state_manager import StateManager
    state_manager = StateManager(workspace_dir)
    runner.state_managers[run_id] = state_manager

    try:
        state = state_manager.get_run_state(run_id)
    except FileNotFoundError:
        return {"success": False, "error": f"State not found for run {run_id}"}

    pipeline_config = state.get("pipeline_config", {})
    project_id = run_id

    # Detect zombie runs: state.json says "running" but no active task
    import_status = state.get("status", "created")
    if import_status == "running" and project_id not in runner.active_runs:
        import_status = "stopped"
        # Fix state.json on disk
        try:
            state["status"] = "stopped"
            import time
            state["updated_at"] = time.time()
            for mod_name, mod_data in state.get("modules", {}).items():
                if mod_data.get("status") == "running":
                    mod_data["status"] = "pending"
            state_path = workspace_dir / "checkpoints" / "state.json"
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            logger.info(f"Fixed zombie state for {project_id}: running → stopped")
        except Exception as fix_err:
            logger.warning(f"Failed to fix zombie state.json for {project_id}: {fix_err}")

    # Create project in memory
    timestamp = datetime.now().isoformat()
    project = Project(
        id=project_id,
        name=pipeline_config.get("project_name", run_id),
        research_domain=pipeline_config.get("research_domain", "unknown"),
        status=import_status,
        created_at=timestamp,
        updated_at=timestamp,
        config=pipeline_config,
    )
    projects_db[project_id] = project

    # Initialize progress with actual module statuses from state
    modules = state.get("modules", {})
    pipeline_order = state.get("pipeline_order") or [
        "query_generator", "paper_fetcher", "country_analyzer",
        "bibliometrics_analyzer", "preprocessor",
        "frequency_analyzer", "topic_modeler", "burst_detector",
        "tsr_ranker", "network_analyzer", "visualizer", "report_generator"
    ]
    progress_db[project_id] = ProjectProgress(
        project_id=project_id,
        current_stage="imported",
        modules=[
            ModuleStatus(name=m, status=modules.get(m, {}).get("status", "pending"))
            for m in pipeline_order
        ],
        total_progress=0.0,
        logs=[]
    )

    logger.info(f"Imported existing project: {project_id}")
    return {"success": True, "project_id": project_id}


@app.websocket("/ws/{project_id}")
async def websocket_endpoint(websocket: WebSocket, project_id: str):
    """WebSocket 端点，用于实时推送进度和日志."""
    await manager.connect(websocket, project_id=project_id)
    comm_hub = get_communication_hub()

    # Register with communication hub for AI workflow messages
    await comm_hub.connect(project_id, websocket)

    # Send initial project state on connect
    try:
        if project_id in progress_db:
            # Sync from state.json first
            _sync_progress_from_state(project_id)

            # Send initial progress
            await websocket.send_json({
                "type": "progress_update",
                "data": progress_db[project_id].dict()
            })
            logger.info(f"Sent initial progress for {project_id}")
    except Exception as e:
        logger.error(f"Failed to send initial progress: {e}")

    try:
        while True:
            # 接收客户端消息
            data = await websocket.receive_text()

            try:
                message = json.loads(data)
                # Handle different message types
                if message.get("type") == "user_message":
                    # User sent a message to AI
                    user_text = message.get("content", "")
                    mode = message.get("mode", "auto")  # "auto", "chat", or "agent"
                    image_data = message.get("image")  # base64 data URL
                    image_name = message.get("image_name", "")

                    await comm_hub.send_user_message(project_id, user_text)
                    logger.info(f"User message for {project_id}: {user_text[:100]} (mode={mode})")

                    # Route to tuning agent if active, else respond directly
                    if project_id in active_tuning_sessions:
                        active_tuning_sessions[project_id].inject_user_message(user_text)
                    else:
                        # If pipeline is not running, respond directly via LLM
                        project_obj = projects_db.get(project_id)
                        project_status = project_obj.status if project_obj else None
                        if project_status != "running":
                            asyncio.create_task(_respond_directly(
                                project_id, user_text,
                                mode=mode,  # Pass mode parameter
                                image_data=image_data, image_name=image_name
                        ))

                elif message.get("type") == "steer":
                    # User sent a steer command (e.g., "PAUSE", "SKIP:module_name", "INJECT_APPROVE:mod")
                    command = message.get("content", "")
                    comm_hub.steer(project_id, command)
                    logger.info(f"Steer command for {project_id}: {command}")

                elif message.get("type") == "pause":
                    # Shortcut for pause command
                    comm_hub.steer(project_id, "PAUSE")
                    logger.info(f"Pause command for {project_id}")

                elif message.get("type") == "skip_module":
                    # Shortcut for skip module command
                    module_name = message.get("module_name", "")
                    if module_name:
                        comm_hub.steer(project_id, f"SKIP:{module_name}")
                        logger.info(f"Skip module command for {project_id}: {module_name}")

                elif message.get("type") == "checkpoint_review":
                    # HITL checkpoint review decision
                    decision = message.get("decision", "approve")
                    module_name = message.get("module_name", "")
                    params = message.get("params", {})
                    comm_hub.steer(project_id, f"{decision.upper()}:{module_name}")
                    runner = get_runner()
                    state_manager = runner.state_managers.get(project_id)
                    if not state_manager:
                        workspace_dir = runner.workspace_manager.get_workspace(project_id)
                        if workspace_dir:
                            state_manager = StateManager(workspace_dir)
                            runner.state_managers[project_id] = state_manager

                    if state_manager:
                        state_manager.resolve_checkpoint(
                            project_id, module_name, decision, params
                        )
                        logger.info(f"Checkpoint review via WS for {project_id}/{module_name}: {decision}")

                else:
                    # Legacy: broadcast progress update
                    if project_id in progress_db:
                        await websocket.send_json({
                            "type": "progress_update",
                            "data": progress_db[project_id].dict()
                        })
            except json.JSONDecodeError:
                # Plain text message (user input)
                await comm_hub.send_user_message(project_id, data)
                logger.info(f"User message (text) for {project_id}: {data[:100]}")

    except WebSocketDisconnect:
        manager.disconnect(websocket, project_id=project_id)
        comm_hub.disconnect(project_id, websocket)


# ---------------------------------------------------------------------------
#  静态文件服务
# ---------------------------------------------------------------------------

# 创建静态文件目录
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
