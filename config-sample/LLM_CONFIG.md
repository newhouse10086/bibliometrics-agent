# 当前 LLM 配置

## 已配置平台

**OpenRouter + Qwen 3.6 Plus**

- 平台: OpenRouter (多模型聚合平台)
- 模型: qwen/qwen3.6-plus
- API 端点: https://openrouter.ai/api/v1
- 配置文件: `bibliometrics-agent/.env`

## 使用方法

### 方式1: 自动加载 (推荐)

系统会自动从 `.env` 文件加载配置，无需手动设置环境变量。

```bash
# 直接运行测试
python test_openrouter.py

# 运行完整接口测试
python test_openai_interface.py

# 运行自动化流程测试
python test_automated_pipeline.py
```

### 方式2: Web 服务

```bash
cd bibliometrics-agent
uvicorn app.main:app --reload --port 8000
```

Web 界面会自动使用配置好的 Qwen 模型。

### 方式3: Python 代码直接调用

```python
from llm.openai_completion import OpenAICompletion

# 自动读取环境变量中的配置
client = OpenAICompletion(model="qwen/qwen3.6-plus")

response = client.completion(
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response["choices"][0]["message"]["content"])
```

## 配置文件位置

```
bibliometrics-agent/
├── .env                          # 环境变量配置 (已配置)
├── configs/
│   └── default.yaml              # 默认配置 (已更新模型为 qwen/qwen3.6-plus)
└── config/
    └── openai_providers.sh.example # 其他平台配置示例
```

## 验证配置

运行快速测试：

```bash
python test_openrouter.py
```

预期输出：
```
[OK] OpenRouter connection successful!
Model: qwen/qwen3.6-plus-04-02
Response: Hello from Qwen!
Tokens used: 236
```

## 成本估算

OpenRouter 价格 (2026-04):

- **Qwen 3.6 Plus**:
  - 输入: $0.15 / 1M tokens
  - 输出: $0.60 / 1M tokens

一次完整的文献计量分析流程预计消耗：
- Query Generation: ~1000 tokens
- Topic Interpretation: ~1000 tokens
- Burst Interpretation: ~800 tokens
- Report Generation: ~2000 tokens

**总计**: ~5000 tokens (约 $0.003 USD)

## 切换其他模型

编辑 `bibliometrics-agent/.env`:

```bash
# 切换到 Claude 3.5 Sonnet
# OPENAI_API_KEY 保持不变
# 只需修改代码中的模型名:
# model="anthropic/claude-3.5-sonnet"

# 切换到 DeepSeek
# OPENAI_BASE_URL=https://api.deepseek.com/v1
# OPENAI_API_KEY=your-deepseek-key
# model="deepseek-chat"
```

完整模型列表: https://openrouter.ai/models
