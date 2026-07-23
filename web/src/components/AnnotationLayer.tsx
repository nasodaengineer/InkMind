/**
 * AnnotationLayer — 批注 UI 组件。
 *
 * - 选段浮动层（四类 intent 按钮）
 * - 正文流内批注卡（锚点展开）
 * - 总评卡固定文首
 * - 失效批注沉章末未定位区
 * - 已解决可开关
 */

import { useState, useCallback } from "react"
import type { CommentIntent, ThreadData } from "@/lib/comment-store"

const INTENT_LABELS: Record<CommentIntent, string> = {
  note: "笔记",
  question: "疑问",
  instruction: "批示",
  reference: "引用",
}

const INTENT_COLORS: Record<CommentIntent, string> = {
  note: "bg-yellow-100 border-yellow-300",
  question: "bg-blue-100 border-blue-300",
  instruction: "bg-red-100 border-red-300",
  reference: "bg-green-100 border-green-300",
}

interface FloatingToolbarProps {
  position: { top: number; left: number }
  onCreate: (intent: CommentIntent) => void
  onClose: () => void
}

export function FloatingToolbar({ position, onCreate, onClose }: FloatingToolbarProps) {
  return (
    <div
      className="absolute z-50 flex gap-1 p-1.5 bg-white border rounded-lg shadow-lg"
      style={{ top: position.top, left: position.left }}
    >
      {(Object.keys(INTENT_LABELS) as CommentIntent[]).map((intent) => (
        <button
          key={intent}
          className={`px-2 py-1 text-xs rounded border ${INTENT_COLORS[intent]} hover:opacity-80`}
          onClick={() => onCreate(intent)}
        >
          {INTENT_LABELS[intent]}
        </button>
      ))}
      <button className="px-1.5 py-1 text-xs text-gray-400 hover:text-gray-600" onClick={onClose}>
        ✕
      </button>
    </div>
  )
}

interface CommentCardProps {
  thread: ThreadData
  onResolve: (id: string) => void
  onReopen: (id: string) => void
  onDelete: (id: string) => void
  onAddComment: (id: string, body: string) => void
}

export function CommentCard({ thread, onResolve, onReopen, onDelete, onAddComment }: CommentCardProps) {
  const [reply, setReply] = useState("")

  return (
    <div className={`border rounded-md p-3 mb-2 text-sm ${INTENT_COLORS[thread.intent]}`}>
      <div className="flex items-center justify-between mb-1">
        <span className="font-medium text-xs uppercase">{INTENT_LABELS[thread.intent]}</span>
        <div className="flex gap-1">
          {thread.status === "resolved" ? (
            <button className="text-xs underline" onClick={() => onReopen(thread.id)}>
              恢复
            </button>
          ) : (
            <button className="text-xs underline" onClick={() => onResolve(thread.id)}>
              解决
            </button>
          )}
          <button className="text-xs text-red-500 underline" onClick={() => onDelete(thread.id)}>
            删除
          </button>
        </div>
      </div>

      {thread.anchor && (
        <p className="text-xs text-gray-500 italic mb-1 truncate">
          “{thread.anchor.exact.slice(0, 40)}{thread.anchor.exact.length > 40 ? "…" : ""}”
        </p>
      )}

      {thread.comments.map((c) => (
        <p key={c.id} className="mb-1">
          <span className="font-medium">{c.author === "user" ? "我" : "AI"}</span>
          ：{c.body}
        </p>
      ))}

      {thread.status !== "resolved" && (
        <div className="flex gap-1 mt-2">
          <input
            className="flex-1 px-2 py-0.5 border rounded text-xs"
            placeholder="回复…"
            value={reply}
            onChange={(e) => setReply(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && reply.trim()) {
                onAddComment(thread.id, reply.trim())
                setReply("")
              }
            }}
          />
        </div>
      )}
    </div>
  )
}

interface AnnotationLayerProps {
  threads: ThreadData[]
  showResolved: boolean
  onResolve: (id: string) => void
  onReopen: (id: string) => void
  onDelete: (id: string) => void
  onAddComment: (id: string, body: string) => void
}

export function AnnotationLayer({
  threads,
  showResolved,
  onResolve,
  onReopen,
  onDelete,
  onAddComment,
}: AnnotationLayerProps) {
  const summaryThreads = threads.filter((t) => t.anchor === null)
  const anchoredThreads = threads.filter(
    (t) => t.anchor !== null && t.status !== "orphaned" && (showResolved || t.status !== "resolved"),
  )
  const orphanedThreads = threads.filter((t) => t.status === "orphaned")

  return (
    <div className="space-y-4">
      {/* 总评卡 — 固定文首 */}
      {summaryThreads.length > 0 && (
        <section>
          <h4 className="text-xs font-semibold text-gray-500 mb-1">章节总评</h4>
          {summaryThreads.map((t) => (
            <CommentCard
              key={t.id}
              thread={t}
              onResolve={onResolve}
              onReopen={onReopen}
              onDelete={onDelete}
              onAddComment={onAddComment}
            />
          ))}
        </section>
      )}

      {/* 正文流内批注卡 */}
      {anchoredThreads.length > 0 && (
        <section>
          <h4 className="text-xs font-semibold text-gray-500 mb-1">行内批注</h4>
          {anchoredThreads.map((t) => (
            <CommentCard
              key={t.id}
              thread={t}
              onResolve={onResolve}
              onReopen={onReopen}
              onDelete={onDelete}
              onAddComment={onAddComment}
            />
          ))}
        </section>
      )}

      {/* 失效批注 — 沉章末未定位区 */}
      {orphanedThreads.length > 0 && (
        <section className="border-t pt-2">
          <h4 className="text-xs font-semibold text-red-400 mb-1">未定位批注</h4>
          {orphanedThreads.map((t) => (
            <CommentCard
              key={t.id}
              thread={t}
              onResolve={onResolve}
              onReopen={onReopen}
              onDelete={onDelete}
              onAddComment={onAddComment}
            />
          ))}
        </section>
      )}
    </div>
  )
}

export function useAnnotationSelection(onSelect: (text: string, rect: DOMRect) => void) {
  const handleMouseUp = useCallback(() => {
    const selection = window.getSelection()
    if (!selection || selection.isCollapsed) return
    const text = selection.toString().trim()
    if (!text || text.length > 500) return
    const range = selection.getRangeAt(0)
    const rect = range.getBoundingClientRect()
    onSelect(text, rect)
  }, [onSelect])

  return { handleMouseUp }
}
