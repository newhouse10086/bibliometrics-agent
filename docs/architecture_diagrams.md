# Bibliometrics Agent 系统架构图

## 1. 总体系统架构

```mermaid
graph TB
    subgraph "用户层"
        Browser[浏览器<br/>static/index.html]
    end

    subgraph "Web服务层"
        FastAPI[FastAPI Server<br/>web_api.py<br/>:8001]
        WS[WebSocket<br/>/ws/{project_id}]
        CM[ConnectionManager<br/>HTTP连接管理]
        Hub[CommunicationHub<br/>消息路由中心]
    end

    subgraph "业务逻辑层"
        Runner[PipelineRunner<br/>全局单例<br/>active_runs字典]
        Orchestrator[PipelineOrchestrator<br/>模块执行编排]
        GuardianSoul[GuardianSoul<br/>LLM错误恢复Agent<br/>最大50步]
        TuningAgent[TuningAgent<br/>LLM调优Agent<br/>最大30步]
    end

    subgraph "数据持久层"
        StateMgr[StateManager<br/>state.json持久化]
        WorkspaceMgr[WorkspaceManager<br/>项目工作空间隔离]
        Logger[ProjectLogger<br/>日志系统]
    end

    subgraph "模块层"
        Registry[ModuleRegistry<br/>模块自动发现]
        subgraph "12个分析模块"
            M1[query_generator]
            M2[paper_fetcher]
            M3[country_analyzer]
            M4[bibliometrics_analyzer]
            M5[preprocessor]
            M6[frequency_analyzer]
            M7[topic_modeler]
            M8[burst_detector]
            M9[tsr_ranker]
            M10[network_analyzer]
            M11[visualizer]
            M12[report_generator]
        end
        M13[paper_generator<br/>v2.0.0]
    end

    subgraph "LLM服务"
        LLMProvider[OpenAIProvider<br/>OpenRouter API<br/>qwen/qwen3.6-plus]
        LLMConfig[configs/default.yaml]
    end

    subgraph "外部数据源"
        PubMed[PubMed API]
        OpenAlex[OpenAlex API]
        Crossref[Crossref API]
        SemScholar[Semantic Scholar]
    end

    %% 用户交互
    Browser -->|HTTP请求| FastAPI
    Browser <-.->|WebSocket| WS
    WS --> Hub
    FastAPI --> CM

    %% API路由
    FastAPI --> Runner
    FastAPI --> StateMgr

    %% Pipeline执行
    Runner -->|start_pipeline| Orchestrator
    Runner -->|active_runs| Orchestrator
    Orchestrator --> Registry

    %% 模块执行流程
    Orchestrator --> M1
    M1 --> M2
    M2 --> M3
    M3 --> M4
    M4 --> M5
    M5 --> M6
    M6 --> M7
    M7 --> M8
    M8 --> M9
    M9 --> M10
    M10 --> M11
    M11 --> M12

    %% 数据获取
    M2 --> PubMed
    M2 --> OpenAlex
    M2 --> Crossref
    M2 --> SemScholar

    %% 错误处理
    Orchestrator -->|on error| GuardianSoul
    GuardianSoul --> LLMProvider

    %% 调优
    FastAPI -->|POST /tune| TuningAgent
    TuningAgent --> LLMProvider
    TuningAgent -->|rerun_module| Orchestrator

    %% 论文生成
    FastAPI -->|POST /generate-paper| M13
    M13 --> LLMProvider

    %% 状态持久化
    Orchestrator --> StateMgr
    Runner --> StateMgr
    StateMgr -->|state.json| WorkspaceMgr

    %% 消息广播
    Orchestrator -->|broadcast_progress| Hub
    GuardianSoul -.->|run_coroutine_threadsafe| Hub
    TuningAgent -.->|run_coroutine_threadsafe| Hub
    Hub -->|WebSocket| Browser

    %% 配置
    LLMConfig --> LLMProvider
    LLMConfig --> Orchestrator

    style Browser fill:#e1f5ff
    style FastAPI fill:#fff4e1
    style Orchestrator fill:#ffe1f5
    style GuardianSoul fill:#ffe1e1
    style TuningAgent fill:#e1ffe1
    style LLMProvider fill:#f0e1ff
```

## 2. Pipeline执行流程（详细）

```mermaid
sequenceDiagram
    participant User as 用户浏览器
    participant API as FastAPI
    participant Runner as PipelineRunner
    participant WS as WebSocket
    participant Orch as Orchestrator
    participant State as StateManager
    participant Module as 当前模块
    participant Hub as CommunicationHub

    User->>API: POST /api/projects (创建项目)
    API->>State: create_run() → state.json

    User->>API: POST /api/projects/{id}/start
    API->>Runner: start_pipeline()
    Runner->>State: 加载配置
    Runner->>Orch: 创建Orchestrator

    Runner->>WS: WebSocket连接建立
    WS->>Hub: register connection

    loop 每个模块
        Orch->>Hub: 检查steer命令 (PAUSE/SKIP)
        Orch->>Hub: 检查user_messages

        Orch->>Module: process(input_data, config, context)
        Module-->>Orch: output dict

        Orch->>State: save_module_output() → outputs/{module}/output.json
        Orch->>State: update_module_status(COMPLETED)

        Orch->>API: POST /broadcast-progress
        API->>Hub: broadcast(progress_update)
        Hub->>WS: WebSocket消息
        WS->>User: 更新前端模块卡片

        alt 模块失败
            Orch->>Orch: _handle_module_error()
            Orch->>Hub: broadcast(GUARDIAN_ACTIVATED)

            Note over Orch,Hub: GuardianSoul激活 (在ThreadPool中运行)

            loop Agent循环 (最大50步)
                Orch->>Hub: broadcast(ai_thinking)
                Hub->>WS: WebSocket消息
                Orch->>Hub: broadcast(ai_tool_call)
                Orch->>Hub: broadcast(ai_tool_result)
            end

            Orch->>Hub: broadcast(ai_decision)
        end
    end

    Orch->>State: set_status(COMPLETED)
    Orch->>Hub: broadcast(PIPELINE_COMPLETE)
    Hub->>WS: WebSocket消息
    WS->>User: 显示完成状态
```

## 3. GuardianSoul Agent循环

```mermaid
stateDiagram-v2
    [*] --> 激活: 模块错误

    激活 --> 构建上下文: 加载project context<br/>错误报告

    构建上下文 --> Agent循环: 初始messages

    state Agent循环 {
        [*] --> 检查停止请求

        检查停止请求 --> 广播步骤: stop_requested?
        广播步骤 --> 检查用户消息: broadcast(step_start)
        检查用户消息 --> LLM调用: 读取user_messages队列

        LLM调用 --> 处理响应: llm.chat(messages, tools)

        处理响应 --> 广播思考: content
        广播思考 --> 执行工具调用: tool_calls?

        执行工具调用 --> 广播工具: 执行10个工具之一
        广播工具 --> 检查finish: broadcast(tool_result)

        检查finish --> Agent循环: 继续循环
        检查finish --> 返回决策: finish决策

        广播工具 --> 达到最大步数: 步数 < 50
        达到最大步数 --> 返回决策
    }

    返回决策 --> [*]: GuardianDecision

    note right of Agent循环
        10个工具:
        1. read_file
        2. read_project_file
        3. write_file
        4. search_files
        5. grep_content
        6. run_command
        7. generate_fix
        8. finish
        9. create_module
        10. add_to_pipeline
    end note
```

## 4. 数据流和状态持久化

```mermaid
graph LR
    subgraph "输入数据流"
        Domain[研究领域<br/>用户输入]
    end

    subgraph "Query Generator"
        Q1[语义查询]
        Q2[PubMed查询]
        Q3[关键词]
        Q4[MeSH词]
    end

    subgraph "Paper Fetcher"
        P1[PubMed API]
        P2[OpenAlex API]
        P3[Crossref API]
        P4[Semantic Scholar]
        P5[MetadataNormalizer<br/>去重和规范化]
    end

    subgraph "分析模块链"
        A1[country_analyzer<br/>国家分布]
        A2[bibliometrics_analyzer<br/>描述性统计]
        A3[preprocessor<br/>文本预处理]
        A4[frequency_analyzer<br/>关键词频率]
        A5[topic_modeler<br/>LDA主题建模]
        A6[burst_detector<br/>爆发检测]
        A7[tsr_ranker<br/>主题显著性]
        A8[network_analyzer<br/>5种网络]
        A9[visualizer<br/>可视化图表]
        A10[report_generator<br/>HTML报告]
    end

    subgraph "输出产物"
        O1[papers.json<br/>papers.csv]
        O2[country_*.csv]
        O3[descriptive_stats.json]
        O4[corpus.pkl<br/>dtm.csv<br/>vocab.txt]
        O5[keyword_year_matrix.csv]
        O6[topic_word.csv<br/>doc_topic.csv<br/>pyLDAvis.html]
        O7[burst_results.csv<br/>plots]
        O8[tsr_scores.csv]
        O9[*.graphml<br/>centrality.csv]
        O10[figures/*.png]
        O11[report.html]
    end

    Domain --> Q1
    Domain --> Q2
    Domain --> Q3
    Domain --> Q4

    Q1 --> P4
    Q2 --> P1
    Q3 --> P2
    Q3 --> P3

    P1 --> P5
    P2 --> P5
    P3 --> P5
    P4 --> P5

    P5 --> O1
    O1 --> A1
    O1 --> A2

    A1 --> O2
    A2 --> O3

    O1 --> A3
    A3 --> O4

    O4 --> A4
    O4 --> A5
    O4 --> A6

    A4 --> O5
    A5 --> O6
    A6 --> O7

    O6 --> A7
    A7 --> O8

    O1 --> A8
    A8 --> O9

    O2 --> A9
    O3 --> A9
    O5 --> A9
    O6 --> A9
    O8 --> A9
    O9 --> O10

    O1 --> A10
    O2 --> A10
    O3 --> A10
    O5 --> A10
    O6 --> A10
    O7 --> A10
    O8 --> A10
    O9 --> A10
    O10 --> A10
    A10 --> O11

    style Domain fill:#e1f5ff
    style P5 fill:#fff4e1
    style O11 fill:#e1ffe1
```

## 5. 工作空间隔离结构

```mermaid
graph TB
    subgraph "workspaces/"
        subgraph "project1_run001/"
            WS1[workspace.json<br/>元数据]
            CP1[checkpoints/]
            OUT1[outputs/]
            DATA1[data/]
            FIX1[workspace/]

            subgraph "checkpoints/"
                ST1[state.json<br/>管道状态源数据]
            end

            subgraph "outputs/"
                O1[query_generator/]
                O2[paper_fetcher/]
                O3[country_analyzer/]
                O4[bibliometrics_analyzer/]
                O5[preprocessor/]
                O6[frequency_analyzer/]
                O7[topic_modeler/]
                O8[burst_detector/]
                O9[tsr_ranker/]
                O10[network_analyzer/]
                O11[visualizer/]
                O12[report_generator/]
                O13[paper_generator/]
            end

            subgraph "workspace/"
                F1[fixes/<br/>Guardian修复代码]
                M1[modules/<br/>动态模块覆盖]
            end
        end

        subgraph "project2_run002/"
            WS2[workspace.json]
            CP2[checkpoints/]
            OUT2[outputs/]
            DATA2[data/]
            FIX2[workspace/]
        end
    end

    style ST1 fill:#ffe1e1
    style O13 fill:#e1ffe1
    style F1 fill:#fff4e1
```

## 6. WebSocket消息类型和流向

```mermaid
graph LR
    subgraph "前端发送"
        F1[user_message<br/>用户聊天]
        F2[steer PAUSE<br/>暂停管道]
        F3[steer SKIP:module<br/>跳过模块]
        F4[checkpoint_review<br/>HITL审查决策]
        F5[steer INJECT_APPROVE<br/>批准模块注入]
    end

    subgraph "CommunicationHub"
        UM[user_messages队列]
        SQ[steer_queues队列]
        MH[message_handlers]
        HIST[history<br/>最近1000条]
    end

    subgraph "后端广播"
        B1[progress_update<br/>模块状态更新]
        B2[project_status_update<br/>项目整体状态]
        B3[ai_thinking<br/>AI思考过程]
        B4[ai_tool_call<br/>工具调用]
        B5[ai_tool_result<br/>工具结果]
        B6[ai_decision<br/>AI决策]
        B7[ai_error<br/>AI错误]
        B8[MODULE_INJECTION_REQUEST<br/>请求注入模块]
    end

    subgraph "消费方"
        Orch[Orchestrator<br/>读取steer队列]
        Guard[GuardianSoul<br/>读取user_messages]
        Tune[TuningAgent<br/>读取user_messages]
        WS[WebSocket连接]
    end

    F1 --> UM
    F2 --> SQ
    F3 --> SQ
    F4 --> SQ
    F5 --> SQ

    UM --> Guard
    UM --> Tune
    SQ --> Orch

    B1 --> HIST
    B2 --> HIST
    B3 --> HIST
    B4 --> HIST
    B5 --> HIST
    B6 --> HIST
    B7 --> HIST
    B8 --> HIST

    HIST --> WS

    style UM fill:#e1f5ff
    style SQ fill:#ffe1e1
    style HIST fill:#fff4e1
```

## 7. 线程和异步模式

```mermaid
graph TB
    subgraph "主事件循环 (uvicorn)"
        EV1[FastAPI请求处理]
        EV2[WebSocket连接]
        EV3[asyncio.Task<br/>_run_pipeline_async]
    end

    subgraph "ThreadPoolExecutor"
        TP1[PipelineOrchestrator.run<br/>同步管道执行]
        TP2[GuardianSoul.activate<br/>Agent循环]
        TP3[TuningAgent.activate<br/>调优循环]
    end

    subgraph "守护线程"
        DT1[threading.Thread<br/>用户消息响应]
    end

    EV3 -->|loop.run_in_executor| TP1
    TP1 -->|错误时激活| TP2

    EV3 -->|POST /tune| TP3

    TP1 -.->|run_coroutine_threadsafe| EV1
    TP2 -.->|run_coroutine_threadsafe| EV1
    TP3 -.->|run_coroutine_threadsafe| EV1

    TP1 -->|检查user_messages时| DT1

    EV1 -->|WebSocket广播| EV2

    style EV1 fill:#e1f5ff
    style TP1 fill:#ffe1f5
    style TP2 fill:#ffe1e1
    style TP3 fill:#e1ffe1
```

## 8. Paper Generator v2.0.0 工作流程

```mermaid
graph TB
    subgraph "输入数据"
        I1[visualizer图表<br/>figures/*.png]
        I2[分析结果<br/>CSV/JSON]
        I3[research_domain<br/>研究领域]
    end

    subgraph "PaperGenerator"
        LLM[OpenAIProvider<br/>qwen/qwen3.6-plus<br/>temp=0.4]

        subgraph "章节生成"
            S1[生成abstract.md]
            S2[生成introduction.md]
            S3[生成data_methods.md]
            S4[生成results.md]
            S5[生成discussion.md]
            S6[生成conclusion.md]
        end

        MD2TEX[_md_to_latex<br/>Markdown→LaTeX转换]
        REFS[_generate_references<br/>生成参考文献]

        subgraph "输出文件"
            O1[sections/*.md<br/>原始Markdown]
            O2[title.txt<br/>论文标题]
            O3[main.tex<br/>LaTeX源文件]
            O4[main.pdf<br/>最终PDF]
            O5[refs/references.bib<br/>BibTeX格式]
            O6[refs/references.txt<br/>纯文本格式]
        end
    end

    subgraph "scripts/build_pdf.py"
        PDF[fpdf2 PDF渲染器]
        FONT[字体fallback链<br/>NotoSansSC→SimHei→Helvetica]
    end

    I1 --> LLM
    I2 --> LLM
    I3 --> LLM

    LLM --> S1
    LLM --> S2
    LLM --> S3
    LLM --> S4
    LLM --> S5
    LLM --> S6

    S1 --> O1
    S2 --> O1
    S3 --> O1
    S4 --> O1
    S5 --> O1
    S6 --> O1

    LLM --> O2
    LLM --> REFS

    O1 --> MD2TEX
    MD2TEX --> O3

    REFS --> O5
    REFS --> O6

    O1 --> PDF
    O2 --> PDF
    FONT --> PDF
    PDF --> O4

    style LLM fill:#f0e1ff
    style PDF fill:#e1ffe1
    style O3 fill:#fff4e1
    style O4 fill:#e1ffe1
```

## 9. Tuning Agent 工具集

```mermaid
graph TB
    subgraph "TuningAgent"
        Agent[Agent循环<br/>最大30步<br/>temp=0.5]

        subgraph "13个工具"
            T1[read_file<br/>读取任意文件]
            T2[read_project_file<br/>读取项目文件]
            T3[write_file<br/>写入文件]
            T4[search_files<br/>Glob搜索]
            T5[grep_content<br/>正则搜索]
            T6[run_command<br/>Shell命令]
            T7[list_project_outputs<br/>列出输出文件]
            T8[read_module_output<br/>读取模块输出]
            T9[get_module_config<br/>获取模块配置]
            T10[adjust_config<br/>调整配置参数]
            T11[rerun_module<br/>重跑单个模块]
            T12[write_analysis_report<br/>写入调优报告]
            T13[finish_tuning<br/>结束调优]
        end
    end

    subgraph "调用目标"
        STATE[state.json<br/>配置持久化]
        ORCH[Orchestrator<br/>run_single_module]
        FS[文件系统]
    end

    Agent --> T10
    T10 --> STATE

    Agent --> T11
    T11 --> ORCH

    Agent --> T8
    Agent --> T9
    T8 --> FS
    T9 --> STATE

    Agent --> T12
    T12 --> FS

    style Agent fill:#e1ffe1
    style T10 fill:#fff4e1
    style T11 fill:#ffe1e1
```

## 10. 状态同步机制

```mermaid
sequenceDiagram
    participant State as state.json
    participant SM as StateManager
    participant API as web_api.py
    participant DB as projects_db<br/>(内存缓存)
    participant Runner as PipelineRunner

    Note over State,DB: 创建项目
    API->>SM: create_run()
    SM->>State: 写入初始state
    State-->>SM: state dict
    SM-->>API: run_id
    API->>DB: projects_db[run_id] = Project(...)

    Note over State,DB: 管道执行中
    Runner->>SM: update_module_status()
    SM->>State: 更新state.json
    API->>SM: get_run_state()
    SM->>State: 读取state.json
    State-->>SM: state dict
    SM-->>API: state
    API->>DB: 更新progress_db

    Note over State,DB: GET请求同步
    API->>API: _sync_progress_from_state()
    API->>SM: get_run_state()
    SM->>State: 读取
    State-->>SM: state
    API->>DB: 同步tuning_count, paper_status

    Note over State,DB: 僵尸检测
    API->>DB: state.status == "running"?
    API->>Runner: 检查active_runs
    Runner-->>API: 无此project_id
    API->>State: 修正status = "stopped"
    API->>DB: 更新projects_db
```

## 11. HITL (Human-in-the-Loop) 检查点流程

```mermaid
sequenceDiagram
    participant User as 用户
    participant WS as WebSocket
    participant Hub as CommunicationHub
    participant Orch as Orchestrator
    participant State as StateManager

    Orch->>Orch: 执行到HITL检查点模块

    Orch->>State: pause()
    State->>State: 更新state.json (status=paused)

    Orch->>Hub: broadcast(CHECKPOINT_REACHED)
    Hub->>WS: WebSocket消息
    WS->>User: 显示检查点审查界面

    User->>WS: 提交审查决策
    WS->>Hub: steer(CHECKPOINT_REVIEW:approve/modify/reject)

    loop 轮询steer队列
        Orch->>Hub: get_steer()
    end

    Hub-->>Orch: CHECKPOINT_REVIEW决策

    alt approve
        Orch->>State: resolve_checkpoint()
        Orch->>Orch: 继续下一个模块
    else reject
        Orch->>Orch: 暂停管道
        Orch->>Hub: broadcast(PIPELINE_PAUSED)
    end
```

## 12. 模块注入流程

```mermaid
sequenceDiagram
    participant Guard as GuardianSoul
    participant Hub as CommunicationHub
    participant User as 用户
    participant Orch as Orchestrator
    participant State as StateManager

    Guard->>Guard: create_module工具
    Guard->>Guard: add_to_pipeline工具

    Guard->>Hub: broadcast(MODULE_INJECTION_REQUEST)
    Hub->>User: 显示注入请求

    User->>Hub: steer(INJECT_APPROVE)
    Hub-->>Orch: 从steer队列读取

    Orch->>Orch: inject_module()

    Orch->>State: update_pipeline_order()
    State->>State: 更新state.json

    Orch->>Orch: 在指定位置插入模块

    Orch->>Hub: broadcast(MODULE_INJECTION_RESULT)
    Hub->>User: 显示注入成功
```

---

## 关键设计决策

### 1. 三层架构
- **Web层**: FastAPI + WebSocket 处理HTTP请求和实时通信
- **业务层**: PipelineRunner + Orchestrator + Agents 处理管道执行和智能决策
- **数据层**: StateManager + WorkspaceManager 保证状态持久化和工作空间隔离

### 2. 混合同步/异步模式
- **主事件循环**: 异步处理HTTP和WebSocket
- **ThreadPoolExecutor**: 同步管道执行避免阻塞主循环
- **跨线程通信**: `run_coroutine_threadsafe` 实现Agent到WebSocket的广播

### 3. 状态一致性
- **源数据**: `state.json` 是唯一可信源
- **内存缓存**: `projects_db` 和 `progress_db` 仅用于快速访问,必须通过 `_sync_progress_from_state()` 同步
- **僵尸检测**: 定期检查 `state.json` 说运行但 `active_runs` 中不存在的项目

### 4. Guardian覆盖机制
- **workspace/modules/{module}.py**: Guardian生成的修复代码
- **动态加载**: `_get_module_with_workspace_override()` 优先加载workspace版本
- **隔离性**: 修复代码不修改系统源码,只在项目工作空间内生效

### 5. 消息路由中心
- **CommunicationHub**: 单例模式,管理所有WebSocket连接和消息队列
- **队列分离**: `user_messages` (用户聊天) 和 `steer_queues` (控制命令) 分离
- **历史记录**: 保留最近1000条消息,超过500条时修剪

### 6. 错误恢复策略
- **Strategy 1**: GuardianSoul (LLM驱动,50步限制,10个工具)
- **Strategy 2**: Template GuardianAgent (模板驱动,离线修复)
- **降级机制**: LLM不可用时自动降级到模板修复
