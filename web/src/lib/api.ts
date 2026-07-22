/* InkMind API 客户端 */

const BASE = "/api"

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? body.error?.message ?? `HTTP ${res.status}`)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

// ── 类型 ──

export interface Novel {
  id: string
  title: string
  metadata: {
    description: string
    word_count: number
    chapter_count: number
    status: string
  }
  created_at: string
  updated_at: string
}

export interface ChapterItem {
  id: string
  index: number
  title: string
  status: string
  summary: string
  version: number
  updated_at: string
  word_count: number
}

export interface VolumeItem {
  id: string
  novel_id: string
  volume_index: number
  title: string
  stage_goal: string
  main_line: string
  side_line: string
  volume_cliffhanger: string
  planned_size: number
  chapter_count: number
  created_at: string
  updated_at: string
}

export interface OutlineSpine {
  novel_id: string
  main_line: string
  core_conflict: string
  ending: string
  selling_points: string
  world_background: string
  golden_finger: string
  created_at: string
  updated_at: string
}

export interface ChapterOutlineItem {
  id: string
  chapter_index: number
  title: string
  status: string
  summary: string
  rhythm_marker: string | null
  pov: string
  involved: string[]
  volume_id?: string | null
}

export interface VolumeSpineResponse {
  volume: VolumeItem
  chapters: ChapterOutlineItem[]
}

// ── 素材类型 ──

export interface MaterialSource {
  id: string
  novel_id: string
  raw_text: string
  content_digest: string
  status: string
  word_count: number
  created_at: string
  chunk_count: number
  fragment_count: number
}

export interface MaterialChunk {
  id: string
  source_id: string
  chunk_index: number
  content: string
  content_digest: string
  status: string
  retry_count: number
  error_message: string | null
  fragment_count: number
}

export interface MaterialFragment {
  id: string
  source_id: string
  source_chunk_id: string
  title: string
  content: string
  type: string
  tags: string[]
  source: string
  source_quote: string | null
  reusability_note: string
  user_note: string
  user_edited: boolean
  created_at: string
}

export const FRAGMENT_TYPES = [
  "excerpt",
  "scene_idea",
  "character_seed",
  "setting_seed",
  "dialogue_sample",
  "style_sample",
  "technique",
  "misc",
] as const

// ── AI Planner Types (Issue #42) ──

export type PlanLevel = "spine" | "volume" | "chapter" | "split_volumes"

export interface RunItem {
  id: string
  novel_id: string
  chapter_id: string | null
  kind: string
  status: string
  phase: string
  partial_content: string
  llm_stats: Record<string, unknown>
  started_at: string | null
  completed_at: string | null
  created_at: string
  updated_at: string
}

export interface StartRunPlannerParams {
  level?: PlanLevel
  prompt?: string
  volume_count?: number
  confirm_overwrite?: boolean
  volume_id?: string
  chapter_count?: number
}

export interface RunSSEEvent {
  /** phase / token / verdict / result / done / error */
  event: string
  data: unknown
}

export type SSECallback = (event: RunSSEEvent) => void

// ── Settings Types ──

export interface ProviderItem {
  name: string
  protocol: string
  base_url: string
  api_key: string | null
  models: string[]
  max_concurrent: number
  max_keepalive: number
  max_calls_per_minute: number
}

export interface ModelBindingItem {
  agent_role: string
  primary_model: string
  fallback_models: string[]
}

export interface RetryConfigItem {
  max_retries: number
  base_delay_s: number
  non_retryable_statuses: number[]
}

export interface AppSettings {
  providers: Record<string, ProviderItem>
  model_router: { bindings: ModelBindingItem[] }
  retry: RetryConfigItem
  default_model: string
}

export interface ProviderModelsResponse {
  providers: Record<string, string[]>
  all_models: string[]
}

export type SettingsUpdateData = {
  providers: Record<string, Record<string, unknown>>
  model_router: { bindings: Record<string, unknown>[] }
  retry: Record<string, unknown>
  default_model: string
}

// ── Stats Types ──

export interface StatsOverview {
  total_calls: number
  total_tokens: number
  total_cost: number
  avg_latency_ms: number
  success_rate: number
  degradation_rate: number
}

export interface StatsBreakdownItem {
  provider?: string
  model?: string
  agent?: string
  error?: string
  calls: number
  total_tokens: number
  total_cost: number
  avg_latency_ms: number
  success_rate: number
}

export interface StatsRunItem {
  id: string
  novel_id: string
  chapter_id: string | null
  kind: string
  status: string
  phase: string
  created_at: string | null
  started_at: string | null
  completed_at: string | null
}

export interface CompressionTaskItem {
  task_id: string
  novel_id: string
  range_start: number
  range_end: number
  status: string
  error_message: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string | null
}

// ── API ──

export const api = {
  health: () => request<{ status: string }>("/health"),

  novels: {
    list: () => request<Novel[]>("/novels"),
    get: (id: string) => request<Novel>(`/novels/${id}`),
    create: (title: string) =>
      request<Novel>("/novels", {
        method: "POST",
        body: JSON.stringify({ title }),
      }),
    delete: (id: string) =>
      request<void>(`/novels/${id}`, { method: "DELETE" }),
  },

  chapters: {
    list: (novelId: string) =>
      request<ChapterItem[]>(`/novels/${novelId}/chapters`),

    patch: (
      novelId: string,
      chapterIndex: number,
      data: {
        title?: string
        summary?: string
        key_events?: string[]
        rhythm_marker?: string | null
        pov?: string
        involved?: string[]
      },
    ) =>
      request<ChapterOutlineItem>(
        `/novels/${novelId}/chapters/${chapterIndex}`,
        { method: "PATCH", body: JSON.stringify(data) },
      ),
  },

  volumes: {
    list: (novelId: string) =>
      request<VolumeItem[]>(`/novels/${novelId}/volumes`),

    create: (novelId: string, data: { title: string; planned_size?: number }) =>
      request<VolumeItem>(`/novels/${novelId}/volumes`, {
        method: "POST",
        body: JSON.stringify(data),
      }),

    get: (novelId: string, volumeIndex: number) =>
      request<VolumeItem>(`/novels/${novelId}/volumes/${volumeIndex}`),

    update: (novelId: string, volumeIndex: number, data: Partial<VolumeItem>) =>
      request<VolumeItem>(`/novels/${novelId}/volumes/${volumeIndex}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),

    delete: (novelId: string, volumeIndex: number) =>
      request<void>(`/novels/${novelId}/volumes/${volumeIndex}`, {
        method: "DELETE",
      }),

    spines: (novelId: string, volumeIndex: number) =>
      request<VolumeSpineResponse>(
        `/novels/${novelId}/volumes/${volumeIndex}/spines`,
      ),
  },

  spine: {
    get: (novelId: string) =>
      request<OutlineSpine>(`/novels/${novelId}/spine`),

    update: (novelId: string, data: Partial<OutlineSpine>) =>
      request<OutlineSpine>(`/novels/${novelId}/spine`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
  },

  materials: {
    importSource: (novelId: string, rawText: string) =>
      request<{ source: MaterialSource; is_duplicate: boolean }>(
        `/novels/${novelId}/materials/sources`,
        { method: "POST", body: JSON.stringify({ raw_text: rawText }) },
      ),

    listSources: (novelId: string) =>
      request<MaterialSource[]>(`/novels/${novelId}/materials/sources`),

    getSource: (novelId: string, sourceId: string) =>
      request<{
        source: MaterialSource
        chunks: MaterialChunk[]
        fragments: MaterialFragment[]
      }>(`/novels/${novelId}/materials/sources/${sourceId}`),

    deleteSource: (novelId: string, sourceId: string) =>
      request<void>(`/novels/${novelId}/materials/sources/${sourceId}`, {
        method: "DELETE",
      }),

    startDecompose: (novelId: string, sourceId: string) =>
      request<{ status: string; source_id: string; decomposed?: number; failed?: number; total?: number }>(
        `/novels/${novelId}/materials/sources/${sourceId}/decompose`,
        { method: "POST" },
      ),

    getDecomposeProgress: (novelId: string, sourceId: string) =>
      request<{
        source_id: string
        status: string
        total: number
        done: number
        failed: number
        low_quality: number
        pending: number
        chunks: Array<{ id: string; index: number; status: string; error_message: string | null; retry_count: number }>
      }>(`/novels/${novelId}/materials/sources/${sourceId}/decompose/progress`),

    rerunFailed: (novelId: string, sourceId: string) =>
      request<{ status: string; rerun_count: number }>(
        `/novels/${novelId}/materials/sources/${sourceId}/rerun-failed`,
        { method: "POST" },
      ),

    listFragments: (novelId: string, params?: { type?: string; tag?: string; offset?: number; limit?: number }) => {
      const qs = new URLSearchParams()
      if (params?.type) qs.set("type", params.type)
      if (params?.tag) qs.set("tag", params.tag)
      if (params?.offset) qs.set("offset", String(params.offset))
      if (params?.limit) qs.set("limit", String(params.limit))
      const q = qs.toString()
      return request<MaterialFragment[]>(
        `/novels/${novelId}/materials/fragments${q ? "?" + q : ""}`,
      )
    },

    updateFragment: (novelId: string, fragmentId: string, body: Record<string, unknown>) =>
      request<MaterialFragment>(
        `/novels/${novelId}/materials/fragments/${fragmentId}`,
        { method: "PATCH", body: JSON.stringify(body) },
      ),

    deleteFragment: (novelId: string, fragmentId: string) =>
      request<void>(`/novels/${novelId}/materials/fragments/${fragmentId}`, {
        method: "DELETE",
      }),

    createFragment: (novelId: string, body: Record<string, unknown>) =>
      request<MaterialFragment>(
        `/novels/${novelId}/materials/fragments`,
        { method: "POST", body: JSON.stringify(body) },
      ),
  },

  settings: {
    get: () => request<AppSettings>("/settings"),
    save: (data: SettingsUpdateData) =>
      request<AppSettings>("/settings", {
        method: "PUT",
        body: JSON.stringify(data),
      }),
    getProviderModels: () =>
      request<ProviderModelsResponse>("/settings/provider-models"),
  },

  stats: {
    overview: (window: "today" | "7d" | "all" = "all") =>
      request<StatsOverview>(`/stats/overview?window=${window}`),

    breakdown: (window: "today" | "7d" | "all" = "all", dimension: "provider" | "model" | "agent" | "error" = "provider") =>
      request<StatsBreakdownItem[]>(`/stats/breakdown?window=${window}&dimension=${dimension}`),

    runs: (window: "today" | "7d" | "all" = "all") =>
      request<StatsRunItem[]>(`/stats/runs?window=${window}`),

    compressionTasks: () =>
      request<CompressionTaskItem[]>("/stats/compression-tasks"),
  },

  // ── AI Planner (Issue #42) ──

  planner: {
    /** 起草总纲 */
    draftSpine: (novelId: string, params?: { prompt?: string; confirm_overwrite?: boolean }) =>
      api.runs.startPlan(novelId, {
        kind: "plan",
        level: "spine",
        ...params,
      }),

    /** 填补单卷 */
    draftVolume: (novelId: string, volumeId: string, params?: { prompt?: string; confirm_overwrite?: boolean }) =>
      api.runs.startPlan(novelId, {
        kind: "plan",
        level: "volume",
        volume_id: volumeId,
        ...params,
      }),

    /** 全书拆卷 */
    splitVolumes: (novelId: string, volumeCount: number, params?: { prompt?: string }) =>
      api.runs.startPlan(novelId, {
        kind: "plan",
        level: "split_volumes",
        volume_count: volumeCount,
        ...params,
      }),

    /** 卷内批量排章 */
    planChapters: (novelId: string, volumeId: string, chapterCount: number, params?: { prompt?: string }) =>
      api.runs.startPlan(novelId, {
        kind: "plan",
        level: "chapter",
        volume_id: volumeId,
        chapter_count: chapterCount,
        ...params,
      }),
  },

  // ── Runs (Issue #42: 含 planner 参数) ──

  runs: {
    list: (novelId: string) =>
      request<{ runs: RunItem[] }>(`/novels/${novelId}/runs`),

    get: (novelId: string, runId: string) =>
      request<RunItem>(`/novels/${novelId}/runs/${runId}`),

    /** 启动 run（含 planner 参数） */
    startPlan: (novelId: string, params: StartRunPlannerParams & { kind: string; chapter_id?: string }) =>
      request<RunItem>(`/novels/${novelId}/runs`, {
        method: "POST",
        body: JSON.stringify({
          kind: "plan",
          chapter_id: params.chapter_id || null,
          level: params.level || "chapter",
          prompt: params.prompt || null,
          volume_count: params.volume_count || null,
          confirm_overwrite: params.confirm_overwrite || false,
          volume_id: params.volume_id || null,
          chapter_count: params.chapter_count || null,
        }),
      }),

    cancel: (novelId: string, runId: string) =>
      request<RunItem>(`/novels/${novelId}/runs/${runId}/cancel`, {
        method: "POST",
      }),
  },
}

// ── SSE ──

/**
 * 连接到 Run SSE 流。
 * 返回一个 abort 函数用于断开连接。
 *
 * 事件类型:
 *   - phase:    阶段变更 { phase: "planning|writing|reviewing|complete" }
 *   - token:    流式 token 分片
 *   - verdict:  评审结论 { verdict: "approve|needs_revision" }
 *   - result:   规划结果 { level, data }
 *   - done:     执行完成 { status: "completed|failed|cancelled|awaiting_human" }
 *   - error:    执行错误 { message }
 */
export function connectRunStream(
  novelId: string,
  runId: string,
  callbacks: {
    onPhase?: (phase: string) => void
    onToken?: (token: string) => void
    onVerdict?: (verdict: string) => void
    onResult?: (result: unknown) => void
    onDone?: (status: string) => void
    onError?: (message: string) => void
  },
): () => void {
  const url = `${BASE}/novels/${novelId}/runs/${runId}/stream`
  const source = new EventSource(url)

  source.addEventListener("phase", (e: MessageEvent) => {
    const data = JSON.parse(e.data)
    callbacks.onPhase?.(data.phase)
  })

  source.addEventListener("verdict", (e: MessageEvent) => {
    const data = JSON.parse(e.data)
    callbacks.onVerdict?.(data.verdict)
  })

  source.addEventListener("result", (e: MessageEvent) => {
    const data = JSON.parse(e.data)
    callbacks.onResult?.(data)
  })

  source.addEventListener("done", (e: MessageEvent) => {
    const data = JSON.parse(e.data)
    callbacks.onDone?.(data.status)
    source.close()
  })

  source.addEventListener("error", (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data)
      callbacks.onError?.(data.message || "未知错误")
    } catch {
      callbacks.onError?.("SSE 连接错误")
    }
    source.close()
  })

  // 处理 token 事件（普通消息，无 event 字段）
  source.onmessage = (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data)
      if (typeof data === "string") {
        callbacks.onToken?.(data)
      }
    } catch {
      // 不是 JSON（如心跳），忽略
    }
  }

  return () => source.close()
}
