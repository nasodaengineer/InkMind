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

// ── Settings Types ──

export interface ProviderItem {
  name: string
  protocol: string
  base_url: string
  api_key: string | null // "已设置" or null
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
}
