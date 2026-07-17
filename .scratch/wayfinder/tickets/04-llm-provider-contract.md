---
title: LLM 供应商合约与配置策略
type: wayfinder:research
status: research-complete
resolved_at: 2026-07-16
research_findings: '.scratch/research/llm-provider.md'
created: 2026-07-16
blocked_by: [01]
labels: [provider, wayfinder]
---

## Question

如何设计统一 LLM 客户端接口，支持多供应商切换与自动降级？

需要研究的决策点：

1. **接口抽象**：统一的 `LLMClient` 接口应该提供哪些方法？
   - `generate(prompt, context, params) → str`
   - `generate_stream(prompt, context, params) → AsyncIterator[str]`
   - 是否需要 `chat()` 风格的多轮对话接口？

2. **供应商适配器**：
   - OpenAI 兼容 API（DeepSeek、OpenAI、OpenRouter、Together AI 等）
   - Anthropic Claude API
   - 本地模型（Ollama、llama.cpp）
   - 各供应商的请求/响应格式差异如何桥接？

3. **自动降级策略**：
   - 主模型：DeepSeek（写作质量）
   - 降级1：OpenAI GPT-4o-mini（成本优化）
   - 降级2：本地模型（离线可用）
   - 降级触发条件：API 错误、超时、速率限制、上下文超长？

4. **配置管理**：
   - 用户如何管理多个 API Key？环境变量？Web UI 配置面板？
   - 每个供应商的模型映射（如 DeepSeek V3 / DeepSeek R1）
   - 按任务类型分配模型（写作用高质量模型，审校用低成本模型）

5. **成本控制**：
   - Token 用量统计与计费预估
   - 任务级 Token 预算

6. **流式输出**：Web 前端需要 SSE/WebSocket 流式显示 LLM 输出，后端如何支持？

请输出包含 Python 接口定义、适配器模式类图和配置 JSON Schema 的技术方案。
