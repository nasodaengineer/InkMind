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
}
