# 项目日志系统说明

## 新增功能

### 1. 项目独立的日志文件

每个项目现在都拥有独立的日志文件，保存在项目工作区目录下：

```
workspaces/
└── {project_name}_{project_id}/
    ├── logs/
    │   └── pipeline_{timestamp}.log    # 项目流水线日志
    ├── data/
    ├── checkpoints/
    ├── outputs/
    ├── visualizations/
    └── reports/
```

**日志文件特点：**
- 自动创建：项目启动时自动创建 logs 目录和日志文件
- 完整记录：记录所有模块执行过程、错误信息、AI分析过程
- UTF-8 编码：正确显示中英文内容
- 持久化保存：项目结束后日志文件仍保留在工作区

### 2. Web端错误日志显示

在右侧 AI 监控面板新增了"错误日志"标签页：

**功能：**
- **实时标记**：有错误时，标签页显示红色错误标记
- **快速查看**：一键切换到错误日志视图
- **详细记录**：显示所有 ERROR 和 CRITICAL 级别的日志
- **时间戳**：每条日志包含精确的时间信息

**使用方法：**
1. 选择或创建项目
2. 点击右侧面板顶部的"⚠️ 错误日志"标签
3. 系统自动加载该项目的错误日志
4. 错误日志实时更新

### 3. API 端点

新增两个日志相关的 API 端点：

#### 获取项目完整日志
```
GET /api/projects/{project_id}/logs?lines=100
```

**返回示例：**
```json
{
  "success": true,
  "logs": [
    "2026-04-09 00:10:15 - core.orchestrator - INFO - Running module: query_generator (1/10)",
    "2026-04-09 00:10:16 - modules.query_generator - INFO - Generating search queries for domain: cancer",
    ...
  ],
  "log_file": "D:/文件综述智能体/bibliometrics-agent/workspaces/test01_abc123/logs/pipeline_20260409_001015.log"
}
```

#### 获取项目错误日志
```
GET /api/projects/{project_id}/error-logs
```

**返回示例：**
```json
{
  "success": true,
  "errors": [
    "2026-04-09 00:10:20 - modules.frequency_analyzer - ERROR - list index out of range",
    "2026-04-09 00:10:21 - core.orchestrator - ERROR - Module frequency_analyzer failed: list index out of range"
  ],
  "log_file": "D:/文件综述智能体/bibliometrics-agent/workspaces/test01_abc123/logs/pipeline_20260409_001015.log"
}
```

## 技术实现

### 核心组件

#### 1. ProjectLogManager (`core/project_logger.py`)
- 单例模式管理所有项目日志
- 为每个项目创建独立的文件处理器
- 支持读取日志和过滤错误日志

#### 2. WorkspaceManager 增强
- 自动创建 logs 子目录
- 确保项目工作区结构完整

#### 3. PipelineRunner 集成
- 在 `start_pipeline()` 时初始化项目日志
- 所有后续日志自动写入项目日志文件

#### 4. Web 界面增强
- 标签页切换组件
- 实时错误标记
- 自动滚动到最新日志

### 日志流程

```
用户创建项目
    ↓
PipelineRunner.start_pipeline()
    ↓
WorkspaceManager.create_workspace()  # 创建 logs 目录
    ↓
ProjectLogManager.setup_project_logger()  # 创建日志文件
    ↓
添加 FileHandler 到 root logger
    ↓
所有模块执行日志 → 项目日志文件
    ↓
用户可通过 Web UI 或 API 查看日志
```

## 优势

### 相比之前的改进

**之前：**
- ❌ 所有项目共享同一个日志文件
- ❌ 项目重启后日志丢失
- ❌ Web 端无法查看完整错误日志
- ❌ 难以定位特定项目的问题

**现在：**
- ✅ 每个项目独立日志文件
- ✅ 日志持久化保存在工作区
- ✅ Web 端实时查看错误日志
- ✅ 便于问题追踪和审计

## 使用示例

### 命令行查看日志
```bash
# 查看特定项目的最新日志
tail -f workspaces/test01_abc123/logs/pipeline_*.log

# 搜索错误信息
grep "ERROR" workspaces/test01_abc123/logs/pipeline_*.log
```

### API 调用示例
```bash
# 获取最近 200 行日志
curl http://localhost:8003/api/projects/abc123/logs?lines=200

# 获取错误日志
curl http://localhost:8003/api/projects/abc123/error-logs
```

### Python 代码中访问
```python
from core.project_logger import get_log_manager

# 获取日志管理器
log_manager = get_log_manager()

# 读取项目日志
logs = log_manager.read_recent_logs("abc123", lines=100)

# 读取错误日志
errors = log_manager.read_error_logs("abc123")

# 获取日志文件路径
log_file = log_manager.get_log_file_path("abc123")
```

## 注意事项

1. **日志文件命名**：格式为 `pipeline_{timestamp}.log`，每次运行创建新文件
2. **日志级别**：文件日志记录 DEBUG 及以上级别，控制台日志记录 INFO 及以上
3. **磁盘空间**：长时间运行的项目可能积累较多日志，建议定期清理旧工作区
4. **编码问题**：所有日志文件使用 UTF-8 编码，确保中英文正常显示

## 未来改进

- [ ] 日志文件轮转（自动压缩/归档旧日志）
- [ ] 日志搜索功能（按关键词、时间范围筛选）
- [ ] 日志下载功能
- [ ] 日志统计（错误数量、警告数量等）
- [ ] 日志级别配置（允许用户调整日志详细程度）
