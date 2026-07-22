/* SSE 流客户端 — 指数退避重连 + 快照恢复 */

const BASE = "/api"

// 重连配置
const MAX_RETRIES = 10
const INITIAL_RETRY_MS = 100
const MAX_RETRY_MS = 30_000

export interface RunSSEHandlers {
  onPhase?: (phase: string) => void
  onToken?: (token: string) => void
  onVerdict?: (verdict: string) => void
  onDone?: (status: string) => void
  onError?: (message: string) => void
}

export interface RunSSEConnection {
  close: () => void
}

/**
 * 连接到 Run SSE 流。
 *
 * - 自动重连：指数退避 100ms → 30s，最多 10 次
 * - 快照恢复：重连后服务器发 phase + done，客户端恢复状态
 * - close() 断开后不再重连
 */
export function connectRunSSE(
  novelId: string,
  runId: string,
  handlers: RunSSEHandlers,
): RunSSEConnection {
  let closed = false
  let retryCount = 0
  let abortController: AbortController | null = null

  const url = `${BASE}/novels/${novelId}/runs/${runId}/stream`

  async function connect(): Promise<void> {
    if (closed) return

    abortController = new AbortController()

    try {
      const response = await fetch(url, {
        signal: abortController.signal,
      })

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }

      // 重连成功，重置计数器
      retryCount = 0

      const contentType = response.headers.get("content-type") || ""

      if (!contentType.includes("text/event-stream")) {
        // 非流式响应 — 快照（已完成/已中断的 run）
        const text = await response.text()
        parseSSEChunk(text, handlers)
        return
      }

      const reader = response.body?.getReader()
      if (!reader) {
        throw new Error("Response body is not readable")
      }

      const decoder = new TextDecoder()
      let buffer = ""

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        // 按 SSE 分隔符拆分
        const parts = buffer.split("\n\n")
        buffer = parts.pop() ?? "" // 保留可能不完整的最后一块

        for (const part of parts) {
          if (part.trim()) {
            parseSSEEvent(part, handlers)
          }
        }
      }
    } catch (err) {
      if (closed) return

      // AbortError = 手动关闭，不重连
      if (err instanceof DOMException && err.name === "AbortError") {
        return
      }

      retryCount++

      if (retryCount > MAX_RETRIES) {
        handlers.onError?.("连接失败：已达最大重试次数")
        return
      }

      // 指数退避
      const delay = Math.min(
        INITIAL_RETRY_MS * Math.pow(2, retryCount - 1),
        MAX_RETRY_MS,
      )

      await new Promise((resolve) => setTimeout(resolve, delay))
      connect()
    }
  }

  connect()

  return {
    close: () => {
      closed = true
      abortController?.abort()
    },
  }
}

/**
 * 解析单条 SSE 事件（不含末尾的 \n\n 分隔符）。
 *
 * 支持 event: 名称 + data: 负载 两行格式。
 * 无 event 字段时视为 token 事件（data 为纯字符串）。
 */
function parseSSEEvent(raw: string, handlers: RunSSEHandlers): void {
  const lines = raw.split("\n")
  let eventType = ""
  let dataStr = ""
  // 多行 data 拼接
  let inData = false

  for (const line of lines) {
    if (line.startsWith("event: ")) {
      eventType = line.slice(7).trim()
      inData = false
    } else if (line.startsWith("data: ")) {
      dataStr += line.slice(6)
      inData = true
    } else if (inData && line.startsWith("data:")) {
      // data: without trailing space
      dataStr += line.slice(5)
    } else if (inData && !line.startsWith("data:")) {
      // continuation line (SSE spec allows data across multiple lines)
      dataStr += "\n" + line
    }
  }

  if (!dataStr) return

  try {
    const data = JSON.parse(dataStr)

    switch (eventType) {
      case "phase":
        handlers.onPhase?.(data.phase)
        break
      case "verdict":
        handlers.onVerdict?.(data.verdict)
        break
      case "done":
        handlers.onDone?.(data.status)
        break
      case "error":
        handlers.onError?.(data.message ?? "未知错误")
        break
      default:
        // 无 event 字段 = token 事件
        if (typeof data === "string") {
          handlers.onToken?.(data)
        }
        break
    }
  } catch {
    // JSON 解析失败，静默忽略
  }
}

/**
 * 解析一整个 SSE 文本块（含多个 \n\n 分隔的事件）。
 */
function parseSSEChunk(text: string, handlers: RunSSEHandlers): void {
  const parts = text.split("\n\n")
  for (const part of parts) {
    if (part.trim()) {
      parseSSEEvent(part, handlers)
    }
  }
}
