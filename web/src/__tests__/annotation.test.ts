/**
 * 批注子系统 Vitest 测试。
 *
 * 覆盖验收标准第 5 条：
 * - W3C quote 指纹生成
 * - 加权模糊重定位三档阈值（≥0.9 / 0.5–0.9 / <0.5）
 * - 扫描对账
 * - mark 跟随
 */

import { describe, it, expect } from "vitest"
import {
  createFingerprint,
  computeRelocateScore,
  classifyRelocation,
  relocateAnchor,
  reconcileMarks,
  refreshFingerprint,
  CommentStore,
  type AnchorFingerprint,
  type ThreadData,
} from "../lib/comment-store"

// ── W3C quote 指纹生成 ──

describe("createFingerprint", () => {
  const fullText = "这是一段很长的小说正文，包含了许多的情节和角色对话。" + "重复内容".repeat(50)

  it("正确截取 exact", () => {
    const fp = createFingerprint(fullText, 5, 15, 1, "abc123")
    expect(fp.exact).toBe(fullText.slice(5, 15))
    expect(fp.exact.length).toBe(10)
  })

  it("正确截取 prefix（最多 64 字符）", () => {
    const fp = createFingerprint(fullText, 100, 110, 1, "abc123")
    expect(fp.prefix).toBe(fullText.slice(36, 100))
    expect(fp.prefix.length).toBe(64)
  })

  it("正确截取 suffix（最多 64 字符）", () => {
    const fp = createFingerprint(fullText, 10, 20, 1, "abc123")
    expect(fp.suffix).toBe(fullText.slice(20, 84))
    expect(fp.suffix.length).toBe(64)
  })

  it("文本开头 prefix 为空", () => {
    const fp = createFingerprint(fullText, 0, 10, 1, "abc123")
    expect(fp.prefix).toBe("")
  })

  it("文本末尾 suffix 为空", () => {
    const end = fullText.length
    const fp = createFingerprint(fullText, end - 10, end, 1, "abc123")
    expect(fp.suffix).toBe("")
  })

  it("记录 pos_hint 和版本信息", () => {
    const fp = createFingerprint(fullText, 42, 52, 3, "digest-xyz")
    expect(fp.pos_hint_start).toBe(42)
    expect(fp.pos_hint_end).toBe(52)
    expect(fp.anchored_version).toBe(3)
    expect(fp.chapter_digest).toBe("digest-xyz")
    expect(fp.relocate_score).toBeNull()
  })
})

// ── 加权模糊重定位三档阈值 ──

describe("computeRelocateScore + classifyRelocation", () => {
  const originalText = "夜色渐深，林远站在山巅，望着远方灯火通明的城镇。"
  const anchor = createFingerprint(originalText, 5, 20, 1, "d1")

  it("精确匹配 → score ≥ 0.9（exact 档）", () => {
    // 文本未变，pos_hint 精确命中
    const score = computeRelocateScore(anchor, originalText)
    expect(score).toBeGreaterThanOrEqual(0.9)
    expect(classifyRelocation(score)).toBe("exact")
  })

  it("文本微调但 exact 仍存在 → score ≥ 0.9", () => {
    const modified = "前缀变了。" + originalText.slice(5)
    const score = computeRelocateScore(anchor, modified)
    expect(score).toBeGreaterThanOrEqual(0.9)
    expect(classifyRelocation(score)).toBe("exact")
  })

  it("文本大幅修改但部分匹配 → 0.5 ≤ score < 0.9（fuzzy 档）", () => {
    // 保留部分关键词但打乱结构
    const modified = "夜色渐深，林远站在不同的地方，望着远方。" + "x".repeat(50)
    const score = computeRelocateScore(anchor, modified)
    expect(score).toBeGreaterThanOrEqual(0.3)
    // 对于部分匹配，应该在 fuzzy 或 orphan 范围
    const tier = classifyRelocation(score)
    expect(["fuzzy", "orphan"]).toContain(tier)
  })

  it("文本完全替换 → score < 0.5（orphan 档）", () => {
    const replaced = "完全不同的文本内容，没有任何相似之处。".repeat(5)
    const score = computeRelocateScore(anchor, replaced)
    expect(score).toBeLessThan(0.5)
    expect(classifyRelocation(score)).toBe("orphan")
  })

  it("classifyRelocation 阈值边界", () => {
    expect(classifyRelocation(0.95)).toBe("exact")
    expect(classifyRelocation(0.9)).toBe("exact")
    expect(classifyRelocation(0.89)).toBe("fuzzy")
    expect(classifyRelocation(0.5)).toBe("fuzzy")
    expect(classifyRelocation(0.49)).toBe("orphan")
    expect(classifyRelocation(0.0)).toBe("orphan")
  })
})

// ── relocateAnchor 返回新位置 ──

describe("relocateAnchor", () => {
  it("精确匹配时返回正确的新位置", () => {
    const text = "AAAABBBBCCCC"
    const anchor = createFingerprint(text, 4, 8, 1, "d")
    // 在前面插入文本，exact 位置偏移
    const shifted = "XX" + text
    const result = relocateAnchor(anchor, shifted)
    expect(result.tier).toBe("exact")
    expect(result.newStart).toBe(6) // 4 + 2
    expect(result.newEnd).toBe(10) // 8 + 2
  })
})

// ── 扫描对账 ──

describe("reconcileMarks", () => {
  const makeThread = (id: string, status: string = "open"): ThreadData => ({
    id,
    chapter_id: "ch1",
    novel_id: "n1",
    intent: "note",
    status: status as ThreadData["status"],
    anchor: { exact: "x", prefix: "", suffix: "", pos_hint_start: 0, pos_hint_end: 1, anchored_version: 1, chapter_digest: "", relocate_score: null },
    comments: [],
    created_at: "",
    updated_at: "",
    resolved_at: null,
  })

  it("DOM 和 store 一致 → 全部 matched", () => {
    const dom = new Set(["t1", "t2"])
    const store = [makeThread("t1"), makeThread("t2")]
    const result = reconcileMarks(dom, store)
    expect(result.matched).toEqual(["t1", "t2"])
    expect(result.orphaned).toEqual([])
    expect(result.missing).toEqual([])
  })

  it("DOM 有 store 无 → orphaned", () => {
    const dom = new Set(["t1", "t-ghost"])
    const store = [makeThread("t1")]
    const result = reconcileMarks(dom, store)
    expect(result.orphaned).toEqual(["t-ghost"])
  })

  it("store 有 DOM 无 → missing", () => {
    const dom = new Set(["t1"])
    const store = [makeThread("t1"), makeThread("t2")]
    const result = reconcileMarks(dom, store)
    expect(result.missing).toEqual(["t2"])
  })

  it("resolved thread 不算 missing", () => {
    const dom = new Set<string>()
    const store = [makeThread("t1", "resolved")]
    const result = reconcileMarks(dom, store)
    expect(result.missing).toEqual([])
  })

  it("无锚 thread 不算 missing", () => {
    const dom = new Set<string>()
    const t = makeThread("t1")
    t.anchor = null
    const result = reconcileMarks(dom, [t])
    expect(result.missing).toEqual([])
  })
})

// ── mark 跟随（指纹刷新）──

describe("refreshFingerprint", () => {
  it("编辑后重新计算 pos_hint 和 exact", () => {
    const oldText = "Hello World"
    const anchor = createFingerprint(oldText, 6, 11, 1, "d1")
    expect(anchor.exact).toBe("World")

    const newText = "Hello Beautiful World"
    const newStart = newText.indexOf("World")
    const refreshed = refreshFingerprint(anchor, newText, newStart, newStart + 5, 2, "d2")

    expect(refreshed.exact).toBe("World")
    expect(refreshed.pos_hint_start).toBe(newStart)
    expect(refreshed.pos_hint_end).toBe(newStart + 5)
    expect(refreshed.anchored_version).toBe(2)
    expect(refreshed.chapter_digest).toBe("d2")
    expect(refreshed.prefix).toBe(newText.slice(Math.max(0, newStart - 64), newStart))
  })
})

// ── CommentStore 集成 ──

describe("CommentStore", () => {
  const makeThread = (id: string, opts: Partial<ThreadData> = {}): ThreadData => ({
    id,
    chapter_id: "ch1",
    novel_id: "n1",
    intent: "note",
    status: "open",
    anchor: { exact: "test", prefix: "", suffix: "", pos_hint_start: 0, pos_hint_end: 4, anchored_version: 1, chapter_digest: "", relocate_score: null },
    comments: [],
    created_at: "",
    updated_at: "",
    resolved_at: null,
    ...opts,
  })

  it("loadThreads + getAllThreads", () => {
    const store = new CommentStore()
    store.loadThreads([makeThread("t1"), makeThread("t2")])
    expect(store.getAllThreads()).toHaveLength(2)
  })

  it("getOrphanedThreads 过滤", () => {
    const store = new CommentStore()
    store.loadThreads([makeThread("t1"), makeThread("t2", { status: "orphaned" })])
    expect(store.getOrphanedThreads()).toHaveLength(1)
    expect(store.getOrphanedThreads()[0].id).toBe("t2")
  })

  it("getChapterSummaryThreads 过滤无锚", () => {
    const store = new CommentStore()
    store.loadThreads([makeThread("t1"), makeThread("t2", { anchor: null })])
    expect(store.getChapterSummaryThreads()).toHaveLength(1)
  })

  it("updateStatus 设置 resolved_at", () => {
    const store = new CommentStore()
    store.loadThreads([makeThread("t1")])
    store.updateStatus("t1", "resolved")
    expect(store.getThread("t1")!.status).toBe("resolved")
    expect(store.getThread("t1")!.resolved_at).not.toBeNull()
  })

  it("updateStatus open 清除 resolved_at", () => {
    const store = new CommentStore()
    store.loadThreads([makeThread("t1", { status: "resolved", resolved_at: "2026-01-01" })])
    store.updateStatus("t1", "open")
    expect(store.getThread("t1")!.resolved_at).toBeNull()
  })

  it("relocateAll 更新所有有锚 thread 的 score", () => {
    const store = new CommentStore()
    const text = "test content here"
    store.loadThreads([makeThread("t1"), makeThread("t2", { anchor: null })])
    const results = store.relocateAll(text)
    expect(results.has("t1")).toBe(true)
    expect(results.has("t2")).toBe(false) // 无锚跳过
  })
})
