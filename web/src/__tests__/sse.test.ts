/* SSE 客户端单元测试 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { connectRunSSE } from "@/lib/sse"

// ── 辅助：模拟 fetch SSE 流 ────────────────────────────────

function mockFetchStream(
  chunks: string[],
  contentType = "text/event-stream",
): void {
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    async start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk))
      }
      controller.close()
    },
  })
  vi.mocked(fetch).mockResolvedValueOnce({
    ok: true,
    headers: new Map(Object.entries({ "content-type": contentType })),
    body: stream,
    text: vi.fn(),
  } as unknown as Response)
}

function mockFetchTextResponse(text: string): void {
  vi.mocked(fetch).mockResolvedValueOnce({
    ok: true,
    headers: new Map(Object.entries({ "content-type": "text/plain" })),
    body: null,
    text: vi.fn().mockResolvedValue(text),
  } as unknown as Response)
}

// ── 全局 mock ──────────────────────────────────────────────

beforeEach(() => {
  vi.useFakeTimers()
  vi.spyOn(globalThis, "fetch").mockImplementation(
    () => Promise.resolve(new Response()),
  )
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

// ── 测试 ──────────────────────────────────────────────────

describe("connectRunSSE", () => {
  it("应当解析 token 事件并调用 onToken", async () => {
    const tokens: string[] = []
    const onToken = (t: string) => tokens.push(t)

    mockFetchStream([
      `data: "第一章"\n\n`,
      `data: " 开始"\n\n`,
      `data: "写作"\n\n`,
      `event: done\ndata: {"status":"completed"}\n\n`,
    ])

    const conn = connectRunSSE("novel-1", "run-1", {
      onToken,
      onDone: () => {},
    })

    // 等待异步 fetch 完成
    await vi.waitFor(() => {
      expect(tokens).toEqual(["第一章", " 开始", "写作"])
    })

    conn.close()
  })

  it("应当解析 phase 事件并调用 onPhase", async () => {
    const phases: string[] = []
    const onPhase = (p: string) => phases.push(p)

    mockFetchStream([
      `event: phase\ndata: {"phase":"planning"}\n\n`,
      `event: phase\ndata: {"phase":"writing"}\n\n`,
      `event: phase\ndata: {"phase":"reviewing"}\n\n`,
      `event: done\ndata: {"status":"completed"}\n\n`,
    ])

    connectRunSSE("novel-1", "run-1", {
      onPhase,
      onDone: () => {},
    })

    await vi.waitFor(() => {
      expect(phases).toEqual(["planning", "writing", "reviewing"])
    })
  })

  it("应当解析 verdict 事件并调用 onVerdict", async () => {
    const verdicts: string[] = []

    mockFetchStream([
      `event: verdict\ndata: {"verdict":"approve"}\n\n`,
      `event: done\ndata: {"status":"completed"}\n\n`,
    ])

    connectRunSSE("novel-1", "run-1", {
      onVerdict: (v) => verdicts.push(v),
      onDone: () => {},
    })

    await vi.waitFor(() => {
      expect(verdicts).toEqual(["approve"])
    })
  })

  it("应当解析 done 事件并调用 onDone", async () => {
    const doneStatuses: string[] = []

    mockFetchStream([
      `event: done\ndata: {"status":"awaiting_human"}\n\n`,
    ])

    connectRunSSE("novel-1", "run-1", {
      onDone: (s) => doneStatuses.push(s),
    })

    await vi.waitFor(() => {
      expect(doneStatuses).toEqual(["awaiting_human"])
    })
  })

  it("应当解析 error 事件并调用 onError", async () => {
    const errors: string[] = []

    mockFetchStream([
      `event: error\ndata: {"message":"LLM 调用失败"}\n\n`,
    ])

    connectRunSSE("novel-1", "run-1", {
      onError: (m) => errors.push(m),
    })

    await vi.waitFor(() => {
      expect(errors).toEqual(["LLM 调用失败"])
    })
  })

  it("快照恢复 — 非流式响应应当解析文本中的 SSE 事件", async () => {
    const phases: string[] = []
    const doneStatuses: string[] = []

    mockFetchTextResponse(
      `event: phase\ndata: {"phase":"writing"}\n\nevent: done\ndata: {"status":"completed"}\n\n`,
    )

    connectRunSSE("novel-1", "run-1", {
      onPhase: (p) => phases.push(p),
      onDone: (s) => doneStatuses.push(s),
    })

    await vi.waitFor(() => {
      expect(phases).toEqual(["writing"])
      expect(doneStatuses).toEqual(["completed"])
    })
  })

  it("断线重连 — 失败后应自动重连", async () => {
    const tokens: string[] = []
    let callCount = 0

    // 第一次调用失败，第二次成功
    vi.mocked(fetch).mockImplementation(() => {
      callCount++
      if (callCount === 1) {
        return Promise.reject(new Error("Network error"))
      }
      const encoder = new TextEncoder()
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode(`data: "reconnected"\n\n`))
          controller.enqueue(
            encoder.encode(
              `event: done\ndata: {"status":"completed"}\n\n`,
            ),
          )
          controller.close()
        },
      })
      return Promise.resolve({
        ok: true,
        headers: new Map(
          Object.entries({ "content-type": "text/event-stream" }),
        ),
        body: stream,
        text: vi.fn(),
      } as unknown as Response)
    })

    connectRunSSE("novel-1", "run-1", {
      onToken: (t) => tokens.push(t),
      onDone: () => {},
    })

    // 等待重连（指数退避：第一次 100ms）
    await vi.advanceTimersByTimeAsync(200)

    await vi.waitFor(() => {
      expect(tokens).toEqual(["reconnected"])
      expect(callCount).toBe(2)
    })
  })

  it("重连上限 — 超过 MAX_RETRIES 后应停止", async () => {
    const errors: string[] = []

    // 所有调用都失败
    vi.mocked(fetch).mockRejectedValue(new Error("Network error"))

    connectRunSSE("novel-1", "run-1", {
      onError: (m) => errors.push(m),
    })

    // 累加退避时间：100 + 200 + 400 + 800 + 1600 + 3200 + 6400 + 12800 + 25600 + 30000 ≈ 最多重试 10 次
    for (let i = 0; i < 12; i++) {
      await vi.advanceTimersByTimeAsync(30000)
    }

    await vi.waitFor(() => {
      expect(errors).toContain("连接失败：已达最大重试次数")
    })
  })

  it("close() 后应停止重连", async () => {
    const errors: string[] = []
    let callCount = 0

    vi.mocked(fetch).mockImplementation(() => {
      callCount++
      return Promise.reject(new Error("Network error"))
    })

    const conn = connectRunSSE("novel-1", "run-1", {
      onError: (m) => errors.push(m),
    })

    // 在首次 fetch 异步失败 + 重连定时器触发之前立即关闭
    conn.close()

    // 推进足够时间，确保不会重连
    await vi.advanceTimersByTimeAsync(10000)

    // fetch 只被调用了 1 次（close 后 catch 不重试）
    expect(callCount).toBe(1)
  })

  it("同一条 SSE 响应中应正确处理多条记录", async () => {
    const phases: string[] = []
    const tokens: string[] = []

    mockFetchStream([
      `event: phase\ndata: {"phase":"writing"}\n\ndata: "Hello"\n\ndata: " World"\n\nevent: phase\ndata: {"phase":"reviewing"}\n\n` +
        `event: verdict\ndata: {"verdict":"approve"}\n\nevent: done\ndata: {"status":"completed"}\n\n`,
    ])

    connectRunSSE("novel-1", "run-1", {
      onPhase: (p) => phases.push(p),
      onToken: (t) => tokens.push(t),
      onVerdict: () => {},
      onDone: () => {},
    })

    await vi.waitFor(() => {
      expect(phases).toEqual(["writing", "reviewing"])
      expect(tokens).toEqual(["Hello", " World"])
    })
  })
})
