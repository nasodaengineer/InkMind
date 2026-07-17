# 07 — Provider 观测层

**What to build:** 为所有 LLM Provider 调用添加可观测性——Token 用量统计、请求延迟、失败计数、成本估算、速率限制。新增 `ProviderMetrics` 数据类，在各 Provider 实现中埋点，`LLMClient` 聚合暴露，CLI `--json` 模式可输出 metrics。

**Blocked by:** None — can start immediately.

**Status:** ✅ done

## Acceptance criteria

- [x] `ProviderMetrics` → 实际实现为 `ProviderStats` 数据类（字段：total_requests、successful_requests、failed_requests、total_prompt_tokens、total_completion_tokens、total_latency_ms、total_cost）
- [x] OpenAI / Anthropic / 本地 Provider 各自埋点：每次调用更新 stats
- [x] `LLMClient` 提供 `get_stats()` 方法返回聚合数据
- [x] Provider 自动降级时记录 `did_degrade` 并计入 stats
- [x] CLI `inkmind <cmd> --json` 输出中包含 stats 块
- [x] 新增测试：mock provider 调用后验证 stats 计数正确
