/**
 * Annotations API 客户端。
 */

import type { AnchorFingerprint, CommentIntent, ThreadData } from "./comment-store"

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

export const annotationsApi = {
  list: (novelId: string, chapterId: string, includeResolved = false) =>
    request<ThreadData[]>(
      `/novels/${novelId}/chapters/${chapterId}/annotations?include_resolved=${includeResolved}`,
    ),

  create: (
    novelId: string,
    chapterId: string,
    data: { intent: CommentIntent; body: string; anchor: AnchorFingerprint | null },
  ) =>
    request<ThreadData>(`/novels/${novelId}/chapters/${chapterId}/annotations`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  update: (
    novelId: string,
    chapterId: string,
    threadId: string,
    data: { status?: string; anchor?: AnchorFingerprint },
  ) =>
    request<ThreadData>(
      `/novels/${novelId}/chapters/${chapterId}/annotations/${threadId}`,
      { method: "PATCH", body: JSON.stringify(data) },
    ),

  delete: (novelId: string, chapterId: string, threadId: string) =>
    request<void>(`/novels/${novelId}/chapters/${chapterId}/annotations/${threadId}`, {
      method: "DELETE",
    }),

  addComment: (novelId: string, chapterId: string, threadId: string, body: string) =>
    request<{ id: string; author: string; body: string; created_at: string }>(
      `/novels/${novelId}/chapters/${chapterId}/annotations/${threadId}/comments`,
      { method: "POST", body: JSON.stringify({ body }) },
    ),

  relocate: (
    novelId: string,
    chapterId: string,
    items: Array<{ thread_id: string; anchor: AnchorFingerprint; score: number }>,
  ) =>
    request<{ relocated: number; fuzzy: number; orphaned: number }>(
      `/novels/${novelId}/chapters/${chapterId}/annotations/relocate`,
      { method: "POST", body: JSON.stringify({ items }) },
    ),
}
