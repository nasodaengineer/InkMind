/**
 * CommentStore — 浏览器端批注状态管理。
 *
 * 核心能力：
 * - W3C TextQuoteSelector 指纹生成（exact + prefix/suffix 各 64 字符）
 * - 加权模糊重定位：三档阈值（≥0.9 精确 / 0.5–0.9 模糊 / <0.5 orphan）
 * - 扫描对账：遍历 DOM 中 comment mark，与 store 中 threads 比对
 * - 保存时指纹刷新：编辑后重新计算 pos_hint + exact
 */

export interface AnchorFingerprint {
  exact: string
  prefix: string
  suffix: string
  pos_hint_start: number
  pos_hint_end: number
  anchored_version: number
  chapter_digest: string
  relocate_score: number | null
}

export interface CommentItem {
  id: string
  author: "user" | "llm"
  body: string
  created_at: string
}

export type ThreadStatus =
  | "open"
  | "pending_relocate"
  | "relocated_fuzzy"
  | "orphaned"
  | "resolved"

export type CommentIntent = "note" | "question" | "instruction" | "reference"

export interface ThreadData {
  id: string
  chapter_id: string
  novel_id: string
  intent: CommentIntent
  status: ThreadStatus
  anchor: AnchorFingerprint | null
  comments: CommentItem[]
  created_at: string
  updated_at: string
  resolved_at: string | null
}

export type RelocateTier = "exact" | "fuzzy" | "orphan"

// ── W3C TextQuoteSelector 指纹生成 ──

const CONTEXT_LENGTH = 64

export function createFingerprint(
  fullText: string,
  start: number,
  end: number,
  version: number,
  chapterDigest: string,
): AnchorFingerprint {
  const exact = fullText.slice(start, end)
  const prefixStart = Math.max(0, start - CONTEXT_LENGTH)
  const suffixEnd = Math.min(fullText.length, end + CONTEXT_LENGTH)

  return {
    exact,
    prefix: fullText.slice(prefixStart, start),
    suffix: fullText.slice(end, suffixEnd),
    pos_hint_start: start,
    pos_hint_end: end,
    anchored_version: version,
    chapter_digest: chapterDigest,
    relocate_score: null,
  }
}

// ── 加权模糊重定位 ──

function longestCommonSubstring(a: string, b: string): number {
  if (!a || !b) return 0
  const m = a.length
  const n = b.length
  let max = 0
  const dp: number[] = new Array(n + 1).fill(0)

  for (let i = 1; i <= m; i++) {
    let prev = 0
    for (let j = 1; j <= n; j++) {
      const temp = dp[j]
      if (a[i - 1] === b[j - 1]) {
        dp[j] = prev + 1
        if (dp[j] > max) max = dp[j]
      } else {
        dp[j] = 0
      }
      prev = temp
    }
  }
  return max
}

export function computeRelocateScore(
  anchor: AnchorFingerprint,
  currentText: string,
): number {
  const { exact, prefix, suffix, pos_hint_start } = anchor

  // 快路径：pos_hint 位置精确匹配
  const hintSlice = currentText.slice(pos_hint_start, pos_hint_start + exact.length)
  if (hintSlice === exact) return 1.0

  // 精确子串搜索
  const idx = currentText.indexOf(exact)
  if (idx !== -1) {
    // 验证上下文
    const ctxPrefix = currentText.slice(Math.max(0, idx - CONTEXT_LENGTH), idx)
    const ctxSuffix = currentText.slice(idx + exact.length, idx + exact.length + CONTEXT_LENGTH)
    const prefixMatch = prefix ? longestCommonSubstring(prefix, ctxPrefix) / prefix.length : 1
    const suffixMatch = suffix ? longestCommonSubstring(suffix, ctxSuffix) / suffix.length : 1
    return 0.9 + 0.1 * ((prefixMatch + suffixMatch) / 2)
  }

  // 模糊匹配：滑动窗口找最佳位置
  const windowSize = exact.length
  if (windowSize === 0 || currentText.length === 0) return 0

  let bestScore = 0
  const step = Math.max(1, Math.floor(windowSize / 4))

  for (let i = 0; i <= currentText.length - windowSize; i += step) {
    const candidate = currentText.slice(i, i + windowSize)
    const lcs = longestCommonSubstring(exact, candidate)
    const score = lcs / windowSize

    if (score > bestScore) {
      bestScore = score
      // 加入上下文权重
      const ctxPrefix = currentText.slice(Math.max(0, i - CONTEXT_LENGTH), i)
      const ctxSuffix = currentText.slice(i + windowSize, i + windowSize + CONTEXT_LENGTH)
      const prefixBonus = prefix ? (longestCommonSubstring(prefix, ctxPrefix) / prefix.length) * 0.1 : 0
      const suffixBonus = suffix ? (longestCommonSubstring(suffix, ctxSuffix) / suffix.length) * 0.1 : 0
      bestScore = Math.min(1, bestScore * 0.8 + prefixBonus + suffixBonus)
    }
  }

  return bestScore
}

export function classifyRelocation(score: number): RelocateTier {
  if (score >= 0.9) return "exact"
  if (score >= 0.5) return "fuzzy"
  return "orphan"
}

export function relocateAnchor(
  anchor: AnchorFingerprint,
  currentText: string,
): { score: number; tier: RelocateTier; newStart: number; newEnd: number } {
  const score = computeRelocateScore(anchor, currentText)
  const tier = classifyRelocation(score)

  let newStart = anchor.pos_hint_start
  let newEnd = anchor.pos_hint_end

  if (tier === "exact") {
    const idx = currentText.indexOf(anchor.exact)
    if (idx !== -1) {
      newStart = idx
      newEnd = idx + anchor.exact.length
    }
  } else if (tier === "fuzzy") {
    // 找最佳匹配位置
    const windowSize = anchor.exact.length
    let bestIdx = 0
    let bestScore = 0
    const step = Math.max(1, Math.floor(windowSize / 4))
    for (let i = 0; i <= currentText.length - windowSize; i += step) {
      const candidate = currentText.slice(i, i + windowSize)
      const lcs = longestCommonSubstring(anchor.exact, candidate)
      if (lcs > bestScore) {
        bestScore = lcs
        bestIdx = i
      }
    }
    newStart = bestIdx
    newEnd = bestIdx + windowSize
  }

  return { score, tier, newStart, newEnd }
}

// ── 扫描对账 ──

export interface ReconcileResult {
  matched: string[]
  orphaned: string[]
  missing: string[]
}

export function reconcileMarks(
  domThreadIds: Set<string>,
  storeThreads: ThreadData[],
): ReconcileResult {
  const storeIds = new Set(storeThreads.map((t) => t.id))
  const matched: string[] = []
  const orphaned: string[] = []
  const missing: string[] = []

  for (const id of domThreadIds) {
    if (storeIds.has(id)) {
      matched.push(id)
    } else {
      orphaned.push(id)
    }
  }

  for (const t of storeThreads) {
    if (t.anchor && !domThreadIds.has(t.id) && t.status !== "resolved") {
      missing.push(t.id)
    }
  }

  return { matched, orphaned, missing }
}

// ── 指纹刷新 ──

export function refreshFingerprint(
  anchor: AnchorFingerprint,
  currentText: string,
  newStart: number,
  newEnd: number,
  version: number,
  chapterDigest: string,
): AnchorFingerprint {
  return createFingerprint(currentText, newStart, newEnd, version, chapterDigest)
}

// ── CommentStore 类 ──

export class CommentStore {
  private threads: Map<string, ThreadData> = new Map()

  loadThreads(threads: ThreadData[]): void {
    this.threads.clear()
    for (const t of threads) {
      this.threads.set(t.id, t)
    }
  }

  getThread(id: string): ThreadData | undefined {
    return this.threads.get(id)
  }

  getAllThreads(): ThreadData[] {
    return Array.from(this.threads.values())
  }

  getActiveThreads(): ThreadData[] {
    return this.getAllThreads().filter((t) => t.status !== "resolved")
  }

  getOrphanedThreads(): ThreadData[] {
    return this.getAllThreads().filter((t) => t.status === "orphaned")
  }

  getChapterSummaryThreads(): ThreadData[] {
    return this.getAllThreads().filter((t) => t.anchor === null)
  }

  addThread(thread: ThreadData): void {
    this.threads.set(thread.id, thread)
  }

  removeThread(id: string): void {
    this.threads.delete(id)
  }

  updateStatus(id: string, status: ThreadStatus): void {
    const thread = this.threads.get(id)
    if (thread) {
      thread.status = status
      if (status === "resolved") {
        thread.resolved_at = new Date().toISOString()
      } else if (status === "open") {
        thread.resolved_at = null
      }
    }
  }

  reconcile(domThreadIds: Set<string>): ReconcileResult {
    return reconcileMarks(domThreadIds, this.getAllThreads())
  }

  relocateAll(currentText: string): Map<string, { score: number; tier: RelocateTier }> {
    const results = new Map<string, { score: number; tier: RelocateTier }>()
    for (const [id, thread] of this.threads) {
      if (!thread.anchor || thread.status === "resolved") continue
      const { score, tier } = relocateAnchor(thread.anchor, currentText)
      results.set(id, { score, tier })
      thread.anchor.relocate_score = score
    }
    return results
  }
}
