# Workspace Architecture Refactoring Summary

## Refactoring Date
2026-04-14

## Problem
The original architecture had **scattered project data** across two separate locations:
- `workspaces/{project_name}_{id}/` - only for uploaded data
- `checkpoints/{run_id}/` - for module outputs and state

This made project management confusing and created unnecessary path complexity.

## Solution
**Unified workspace architecture** - all project data now lives under a single workspace directory per project.

## Changes Made

### 1. StateManager Refactoring (`core/state_manager.py`)
**Before:**
```python
def __init__(self, checkpoint_dir: Path):
    self.checkpoint_dir = checkpoint_dir / run_id / "checkpoints"
```

**After:**
```python
def __init__(self, workspace_dir: Path):
    self.workspace_dir = workspace_dir
    self.checkpoint_dir = workspace_dir / "checkpoints"
    self.outputs_dir = workspace_dir / "outputs"
```

**Impact:**
- Module outputs: `workspace/outputs/{module_name}/`
- State files: `workspace/checkpoints/state.json`
- Module checkpoints: `workspace/checkpoints/{module_name}/`
- No more `run_id` subdirectories

### 2. PipelineRunner Refactoring (`core/pipeline_runner.py`)
**Before:**
```python
self.state_manager = StateManager(checkpoint_dir)  # Global
```

**After:**
```python
self.state_managers: Dict[str, StateManager] = {}  # Per-project

# In start_pipeline:
state_manager = StateManager(workspace_dir)
self.state_managers[project_id] = state_manager
```

**Impact:**
- Each project has its own StateManager instance
- StateManagers are created from workspace directory, not checkpoint directory
- Resume operations recreate StateManager from saved workspace

### 3. WorkspaceManager Simplification (`core/workspace_manager.py`)
**Before:**
```python
(workspace_dir / "outputs").mkdir(exist_ok=True)
(workspace_dir / "checkpoints").mkdir(exist_ok=True)
(workspace_dir / "logs").mkdir(exist_ok=True)
# etc.
```

**After:**
```python
workspace_dir.mkdir(parents=True, exist_ok=True)
(workspace_dir / "data").mkdir(exist_ok=True)
# Note: outputs/, checkpoints/, logs/ will be created by StateManager
```

**Impact:**
- WorkspaceManager only creates `data/` directory
- Other directories created on-demand by StateManager
- Cleaner separation of concerns

### 4. Web API Updates (`web_api.py`)
**Key Changes:**
- `list_presets()`: Uses global presets path instead of `runner.state_manager`
- `save_preset()`: Saves to global `presets/` directory
- `checkpoint_review()`: Uses `runner.state_managers.get(project_id)`
- `_sync_progress_from_state()`: Uses per-project state managers
- `import_existing_project()`: Creates StateManager from workspace
- UI message: Updated from "checkpoints/" to "workspaces/"

**Impact:**
- All endpoints now use unified workspace paths
- Presets are global (not per-project)
- Per-project StateManager access pattern consistent throughout

### 5. Orchestrator Updates (`core/orchestrator.py`)
**Before:**
```python
project_dir = self.state.checkpoint_dir  # Wrong
```

**After:**
```python
project_dir = self.state.workspace_dir  # Correct
```

**Impact:**
- RunContext receives correct project directory
- Modules can access workspace root for relative paths

## Directory Structure (New Architecture)

```
workspaces/
├── {project_name}_{run_id}/          # Single workspace per project
│   ├── workspace.json                # Project metadata
│   ├── data/                         # User uploads
│   ├── outputs/                      # Module outputs
│   │   ├── paper_fetcher/
│   │   │   └── output.json
│   │   ├── preprocessor/
│   │   │   └── output.json
│   │   └── ... (other modules)
│   ├── checkpoints/                  # State and checkpoints
│   │   ├── state.json                # Pipeline state
│   │   ├── query_generator/
│   │   │   └── checkpoint.json
│   │   └── ... (module checkpoints)
│   ├── logs/                         # Project logs
│   ├── fixes/                        # GuardianSoul fixes
│   ├── reports/                      # Generated reports
│   └── visualizations/               # Plots and charts
└── presets/                          # Global presets (NEW)
    ├── preset_1.json
    └── preset_2.json
```

## Testing

**Verified:**
- ✅ Workspace creation with correct directory structure
- ✅ StateManager creates `checkpoints/` and `outputs/` on initialization
- ✅ Module outputs saved to `workspace/outputs/{module_name}/`
- ✅ State file saved to `workspace/checkpoints/state.json`
- ✅ Per-project StateManager instances work correctly
- ✅ Workspace cleanup (delete) works correctly
- ✅ Web API endpoints use unified paths
- ✅ No remaining references to old architecture

**Not Tested (requires API keys):**
- Full end-to-end pipeline run through web interface
- Resume from checkpoint with new architecture

## Backward Compatibility

**Existing Projects:**
- Old checkpoints in `checkpoints/{run_id}/` still exist on disk
- `import_existing_project()` can load old projects by creating workspace from checkpoint
- Test files (`test_pipeline_cached.py`) can still use old checkpoint paths for historical data

**Migration Path:**
- No automatic migration needed
- Users can manually move old checkpoints to workspace structure if desired
- Or just keep old projects in `checkpoints/` and new projects in `workspaces/`

## Benefits

1. **Simplified Architecture**: Single source of truth per project
2. **Clearer Paths**: All paths relative to workspace root
3. **Easier Project Management**: Delete workspace = delete all project data
4. **Better Isolation**: Each project is self-contained
5. **Global Presets**: Configuration presets work across all projects
6. **Cleaner Code**: No more `run_id` subdirectory everywhere

## Files Modified

- `core/state_manager.py` - Major refactoring
- `core/workspace_manager.py` - Simplified directory creation
- `core/pipeline_runner.py` - Per-project StateManager instances
- `core/orchestrator.py` - Updated project directory reference
- `web_api.py` - Fixed all endpoints to use unified paths
- `static/index.html` - Updated UI message

## Files Not Modified

- Test files (use historical checkpoint data)
- Module files (no changes needed)
- Configuration files (no changes needed)

## Conclusion

The refactoring successfully unified the workspace architecture, making project data management clearer and more intuitive. All outputs are now consolidated under a single workspace directory per project, eliminating the confusion of having data split across multiple locations.
