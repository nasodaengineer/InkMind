# LLM 多供应商策略 — 技术调研报告

> **日期**: 2026-07-16  
> **范围**: Python LLM客户端库、供应商API差异、降级策略、流式输出、成本控制

## 1. Python LLM 客户端库生态

| 方案 | 定位 | 包体积 | 抽象层级 | 推荐场景 |
|------|------|--------|----------|----------|
| **LiteLLM** | 多供应商SDK | 轻量 | 中等 | ⭐ 推荐 — 内置降级/限流/重试 |
| OpenAI SDK | 官方SDK | 极轻 | 低 | 仅OpenAI兼容 |
| httpx直接调用 | 无依赖 | 无 | 无 | 完全可控 |
| LangChain | 框架 | 重量 | 高 | **不推荐** — 过度抽象 |

**推荐: LiteLLM** — 统一 `acompletion()` 接口，内置 fallbacks 链、速率限制、重试、超时，对 InkMind 场景开箱即用

## 2. 供应商API差异

### OpenAI 兼容族 (DeepSeek/OpenAI/OpenRouter/Together/Ollama)
- 请求格式统一: `POST /chat/completions` + `messages: [{role, content}]`
- DeepSeek V4 特殊: 默认开启 thinking 模式，可通过 `extra_body={"thinking": {"type": "disabled"}}` 关闭
- **DeepSeek 模型名迁移**: `deepseek-chat`/`deepseek-reasoner` 将废弃 → 迁移至 `deepseek-v4-flash`/`deepseek-v4-pro`

### Anthropic Claude (Message API)
| 维度 | OpenAI | Anthropic |
|------|---------|-----------|
| 端点 | `/chat/completions` | `/messages` |
| 认证 | `Authorization: Bearer` | `x-api-key` + version header |
| 系统消息 | 在 messages 中 | 独立顶层 `system` 字段 |
| 流式格式 | SSE `data: {...}` | 多事件类型 (message_start → content_block_delta → ...) |

### 本地模型 Ollama
- Ollama v0.1.17+ 内置 `/v1/chat/completions` OpenAI 兼容端点
- 只需 `base_url='http://localhost:11434/v1'`, `api_key='ollama'` (占位)

## 3. 自动降级策略

### 推荐降级链
```
① 主供应商 (DeepSeek) — 指数退避+Jitter, 3次重试, 总预算15秒
    ↓ 失败
② 备用供应商 (OpenAI/OpenRouter) — 简单线性退避, 2次重试, 总预算10秒
    ↓ 失败
③ 缓存/静默降级 — 返回友好错误, 不清除用户输入
```

### 故障分类
| 故障 | HTTP | 可重试 | 处理 |
|------|------|--------|------|
| 瞬态容量 | 429, 529, 503 | 是 | 指数退避+Jitter |
| 速率限制 | 429 | 策略性 | 直接降级 |
| 上下文超长 | 400 | 否 | 截断或换模型 |
| 认证错误 | 401, 403 | 否 | 通知用户 |
| 内容过滤 | 400 policy | 否 | 返回结构化错误 |

### 断路器模式
- Closed: 正常转发，统计错误率
- Open: 拒绝请求，30-60秒冷却
- Half-Open: 允许探测请求，成功→Closed，失败→Open

## 4. 流式输出方案

**推荐: SSE (Server-Sent Events)**

优于 WebSocket 的原因: 单向推送匹配LLM流式模式、HTTP兼容性好、浏览器 EventSource 原生自动重连、FastAPI `StreamingResponse` 零额外依赖

架构: FastAPI POST端点 → 异步迭代LLM流 → 通过 SSE 向浏览器推送 token/done/error 事件

## 5. 配置管理建议

```json
{
  "providers": {
    "deepseek": { "model": "deepseek-v4-flash", "api_key_env": "DEEPSEEK_API_KEY", "priority": 1 },
    "openai":   { "model": "gpt-4o-mini",      "api_key_env": "OPENAI_API_KEY",   "priority": 2 },
    "ollama":   { "model": "llama3.2",          "api_key_env": "",                 "priority": 3 }
  },
  "task_routing": {
    "writing":    { "provider": "deepseek", "temperature": 0.8, "max_tokens": 4096 },
    "editing":    { "provider": "openai",   "temperature": 0.3, "max_tokens": 2048 },
    "compression":{ "provider": "deepseek", "temperature": 0.5, "max_tokens": 1024 }
  },
  "fallback": { "enabled": true, "max_retries": 3, "timeout_seconds": 60 }
}
```

## 原始引用

1. [LiteLLM Docs](https://docs.litellm.ai/)
2. [DeepSeek API Docs](https://api-docs.deepseek.com/)
3. [Ollama OpenAI Compatibility](https://docs.ollama.com/api/openai-compatibility)
4. [Anthropic Message API](https://docs.anthropic.com/en/api/messages)
5. [FastAPI + SSE Streaming](https://dev.to/ayinedjimi-consultants/how-to-build-a-streaming-chatbot-api-in-python-with-fastapi-and-sse-4f7o)
