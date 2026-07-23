/* 写作台：分栏布局 + TipTap 编辑器 + SSE 流式生成 + 状态条 + 裁决 UI */

import { useState, useRef, useEffect, useCallback } from "react"
import { useParams, useNavigate } from "react-router"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api, type ChapterItem } from "@/lib/api"
import { connectRunSSE, type RunSSEConnection } from "@/lib/sse"
import Editor, { type EditorHandle } from "@/components/Editor"
import StatusBar from "@/components/StatusBar"
import MaterialDrawer from "@/components/MaterialDrawer"
import { Button } from "@/components/ui/button"
import { Plus, Trash2, BookOpen, Play, Loader2, Check, RefreshCw } from "lucide-react"

// ── Toast 组件 ──────────────────────────────────────

function Toast({
  message,
  type,
  onDismiss,
}: {
  message: string
  type: "error" | "info"
  onDismiss: () => void
}) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, 4000)
    return () => clearTimeout(timer)
  }, [onDismiss])

  return (
    <div
      className={`fixed top-4 right-4 z-50 px-4 py-2 rounded-md shadow-lg text-sm font-medium transition-all ${
        type === "error"
          ? "bg-red-600 text-white"
          : "bg-blue-600 text-white"
      }`}
    >
      {message}
      <button
        className="ml-3 text-white/80 hover:text-white"
        onClick={onDismiss}
      >
        &times;
      </button>
    </div>
  )
}

// ── 裁决对话框 ──────────────────────────────────────

function VerdictDialog({
  onKeep,
  onDiscard,
}: {
  onKeep: () => void
  onDiscard: () => void
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="bg-background rounded-lg shadow-xl border p-6 w-96">
        <h3 className="text-base font-semibold mb-2">生成中断</h3>
        <p className="text-sm text-muted-foreground mb-4">
          章节生成已被中断，当前已生成的部分内容如何处理？
        </p>
        <div className="flex gap-3 justify-end">
          <Button variant="outline" onClick={onDiscard}>
            丢弃
          </Button>
          <Button onClick={onKeep}>保留</Button>
        </div>
      </div>
    </div>
  )
}

// ── 人工门底栏 ─────────────────────────────────────

function HumanBar({
  chapterIndex,
  novelId,
  onAction,
}: {
  chapterIndex: number
  novelId: string
  onAction: () => void
}) {
  const queryClient = useQueryClient()
  const [finalizing, setFinalizing] = useState(false)

  const handleFinalize = async () => {
    setFinalizing(true)
    try {
      await api.chapters.finalize(novelId, chapterIndex)
      queryClient.invalidateQueries({ queryKey: ["chapters", novelId] })
      onAction()
    } catch {
      // 静默失败
    }
    setFinalizing(false)
  }

  const handleRevise = async () => {
    try {
      await api.chapters.patch(novelId, chapterIndex, {
        status: "revising",
      })
      queryClient.invalidateQueries({ queryKey: ["chapters", novelId] })
      onAction()
    } catch {
      // 静默失败
    }
  }

  return (
    <div className="flex items-center gap-3 px-4 py-3 border-t bg-amber-50 dark:bg-amber-950/20">
      <span className="text-sm font-medium text-amber-800 dark:text-amber-300">
        等待人工确认
      </span>
      <div className="flex-1" />
      <Button variant="outline" size="sm" onClick={handleRevise}>
        <RefreshCw className="w-3.5 h-3.5 mr-1" />
        批示再修
      </Button>
      <Button
        variant="default"
        size="sm"
        onClick={handleFinalize}
        disabled={finalizing}
      >
        {finalizing ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" />
        ) : (
          <Check className="w-3.5 h-3.5 mr-1" />
        )}
        提交定稿
      </Button>
    </div>
  )
}

// ── 结论卡 ─────────────────────────────────────────

function VerdictCard({
  verdict,
  issues,
}: {
  verdict: "approve" | "needs_revision"
  issues: string[]
}) {
  return (
    <div
      className={`mx-4 mt-3 p-3 rounded-md border text-sm ${
        verdict === "approve"
          ? "border-green-300 bg-green-50 dark:bg-green-950/20 dark:border-green-800"
          : "border-amber-300 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800"
      }`}
    >
      <div className="flex items-center gap-2 font-medium">
        {verdict === "approve" ? (
          <Check className="w-4 h-4 text-green-600" />
        ) : (
          <RefreshCw className="w-4 h-4 text-amber-600" />
        )}
        <span>{verdict === "approve" ? "评审通过" : "需要修订"}</span>
      </div>
      {issues.length > 0 && (
        <ul className="mt-2 ml-6 list-disc text-muted-foreground space-y-0.5">
          {issues.map((issue, i) => (
            <li key={i}>{issue}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

// ── 版本选择器 ─────────────────────────────────────

function VersionSelector({
  novelId,
  chapterId,
  currentVersion,
}: {
  novelId: string
  chapterId: string
  currentVersion: number
}) {
  const [open, setOpen] = useState(false)
  const [versions, setVersions] = useState<
    import("@/lib/api").VersionItem[]
  >([])
  const [diffResult, setDiffResult] =
    useState<import("@/lib/api").VersionDiffResponse | null>(null)
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null)

  const loadVersions = async () => {
    try {
      const res = await api.chapters.versions(novelId, chapterId)
      setVersions(res.versions)
      setOpen(true)
    } catch {
      // ignore
    }
  }

  const loadDiff = async (fromV: number) => {
    try {
      const res = await api.chapters.diff(novelId, chapterId, fromV, currentVersion)
      setDiffResult(res)
      setSelectedVersion(fromV)
    } catch {
      // ignore
    }
  }

  if (currentVersion <= 1) return null

  return (
    <div className="relative">
      <button
        className="text-xs px-2 py-1 rounded border hover:bg-accent transition-colors"
        onClick={() => (open ? setOpen(false) : loadVersions())}
      >
        v{currentVersion}▾
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1 z-50 w-64 bg-background border rounded-md shadow-lg">
          <div className="p-2 border-b text-xs font-medium text-muted-foreground">
            历史版本
          </div>
          <div className="max-h-48 overflow-auto">
            <button
              className="w-full text-left px-3 py-2 text-sm hover:bg-accent/50 flex items-center justify-between"
              onClick={() => {
                setDiffResult(null)
                setSelectedVersion(null)
                setOpen(false)
              }}
            >
              <span>v{currentVersion}（当前）</span>
              <span className="text-[10px] text-green-600">最新</span>
            </button>
            {versions.map((v) => (
              <button
                key={v.id}
                className="w-full text-left px-3 py-2 text-sm hover:bg-accent/50 flex items-center justify-between"
                onClick={() => loadDiff(v.version)}
              >
                <span>
                  v{v.version} · {v.word_count}字
                </span>
                <span className="text-[10px] text-muted-foreground">
                  {v.source_trace || "ai"}
                </span>
              </button>
            ))}
          </div>

          {diffResult && selectedVersion !== null && (
            <div className="border-t p-2 max-h-64 overflow-auto">
              <div className="text-xs font-medium text-muted-foreground mb-1">
                v{selectedVersion} → v{currentVersion} 差异
              </div>
              <div className="text-xs space-y-0.5 font-mono">
                {diffResult.paragraphs.map((para, i) => (
                  <div key={i}>
                    {para.map((line, j) => (
                      <div
                        key={j}
                        className={
                          line.tag === "insert"
                            ? "text-green-700 bg-green-50 dark:bg-green-950/30"
                            : line.tag === "delete"
                              ? "text-red-700 bg-red-50 dark:bg-red-950/30 line-through"
                              : "text-muted-foreground"
                        }
                      >
                        {line.tag === "insert" ? "+ " : line.tag === "delete" ? "- " : "  "}
                        {line.text.slice(0, 80)}
                        {line.text.length > 80 ? "…" : ""}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── 主组件 ──────────────────────────────────────────

export default function WorkspacePage() {
  const { novelId } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  // Refs
  const editorRef = useRef<EditorHandle>(null)
  const sseRef = useRef<RunSSEConnection | null>(null)
  const preGenContentRef = useRef("") // 生成前内容副本，用于丢弃时恢复

  // 状态
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(null)
  const [selectedChapterIndex, setSelectedChapterIndex] = useState<number | null>(null)
  const [isGenerating, setIsGenerating] = useState(false)
  const [phase, setPhase] = useState("")
  const [runId, setRunId] = useState<string | null>(null)
  const [cancelPending, setCancelPending] = useState(false)
  const [showVerdictDialog, setShowVerdictDialog] = useState(false)
  const [showHumanBar, setShowHumanBar] = useState(false)
  const [toast, setToast] = useState<{ message: string; type: "error" | "info" } | null>(null)
  const [materialDrawerOpen, setMaterialDrawerOpen] = useState(false)
  const [pendingRunId, setPendingRunId] = useState(false)
  const [mode, setMode] = useState<"write" | "review">("write")
  const [lastVerdict, setLastVerdict] = useState<{
    verdict: "approve" | "needs_revision"
    issues: string[]
  } | null>(null)

  // ── Queries ──────────────────────────────────────

  const novels = useQuery({
    queryKey: ["novels"],
    queryFn: api.novels.list,
  })

  const chapters = useQuery({
    queryKey: ["chapters", novelId],
    queryFn: () => api.chapters.list(novelId!),
    enabled: !!novelId,
  })

  const runsQuery = useQuery({
    queryKey: ["runs", novelId],
    queryFn: () => api.runs.list(novelId!),
    enabled: !!novelId,
  })

  // 当前选中的章节详情
  const [chapterContentCache, setChapterContentCache] = useState<Record<string, string>>({})

  // Mutations
  const createNovel = useMutation({
    mutationFn: () => api.novels.create(title),
    onSuccess: (novel) => {
      setTitle("")
      setShowCreate(false)
      queryClient.invalidateQueries({ queryKey: ["novels"] })
      navigate(`/workspace/${novel.id}`)
    },
  })

  const deleteNovel = useMutation({
    mutationFn: (id: string) => api.novels.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["novels"] })
      if (novelId) navigate("/workspace")
    },
  })

  // ── 自动恢复：检测运行中的 Run ─────────────────────

  useEffect(() => {
    if (!runsQuery.data || !novelId || runId) return

    const runningRuns = runsQuery.data.runs.filter(
      (r) => r.status === "RUNNING",
    )
    if (runningRuns.length === 0) return

    // 取第一个运行中的 run（通常只有一个）
    const activeRun = runningRuns[0]
    setRunId(activeRun.id)
    setIsGenerating(true)
    setPhase(activeRun.phase || "writing")

    // 如果有关联章节，选中它
    if (activeRun.chapter_id && activeRun.chapter_id !== selectedChapterId) {
      const ch = chapters.data?.find((c) => c.id === activeRun.chapter_id)
      if (ch) {
        setSelectedChapterId(ch.id)
        setSelectedChapterIndex(ch.index)
        // 加载已有内容
        if (activeRun.partial_content) {
          editorRef.current?.setContent(activeRun.partial_content)
        }
      }
    }

    // 重连 SSE
    connectSSE(activeRun.id)
  }, [runsQuery.data, novelId])

  // ── SSE 连接管理 ──────────────────────────────────

  const connectSSE = useCallback(
    (id: string) => {
      if (!novelId) return

      sseRef.current?.close()

      sseRef.current = connectRunSSE(novelId, id, {
        onPhase: (p) => {
          setPhase(p)
        },
        onToken: (token) => {
          editorRef.current?.appendContent(token)
        },
        onVerdict: (verdict) => {
          setLastVerdict({
            verdict: verdict === "approve" ? "approve" : "needs_revision",
            issues: [],
          })
          setMode("review")
        },
        onDone: (status) => {
          setIsGenerating(false)
          sseRef.current = null

          if (status === "awaiting_human") {
            setShowHumanBar(true)
            setPhase("awaiting_human")
          } else if (
            status === "cancelled" ||
            status === "interrupted" ||
            status === "failed"
          ) {
            setShowVerdictDialog(true)
          }
        },
        onError: (msg) => {
          setToast({ message: msg, type: "error" })
          setIsGenerating(false)
          setShowVerdictDialog(true)
        },
      })
    },
    [novelId],
  )

  // ── 章节选择 ───────────────────────────────────────

  const selectChapter = useCallback(
    async (ch: ChapterItem) => {
      if (isGenerating) return // 生成中禁止切换

      setSelectedChapterId(ch.id)
      setSelectedChapterIndex(ch.index)
      setShowHumanBar(false)
      setShowVerdictDialog(false)
      setMode("write")
      setLastVerdict(null)

      // 从缓存或 API 加载内容
      if (chapterContentCache[ch.id]) {
        editorRef.current?.setContent(chapterContentCache[ch.id])
      } else {
        try {
          const detail = await api.chapters.get(novelId!, ch.id)
          const html = detail.content || ""
          editorRef.current?.setContent(html)
          setChapterContentCache((prev) => ({ ...prev, [ch.id]: html }))
          preGenContentRef.current = html
          if (detail.status === "awaiting_human") {
            setShowHumanBar(true)
          }
        } catch {
          editorRef.current?.setContent("")
        }
      }
    },
    [isGenerating, novelId, chapterContentCache],
  )

  // ── 开始生成 ───────────────────────────────────────

  const startGeneration = useCallback(async () => {
    if (!selectedChapterId || !novelId || isGenerating || pendingRunId) return

    // 先检查是否有已存在的运行中 run
    const runs = await api.runs.list(novelId)
    const existingRun = runs.runs.find(
      (r) =>
        r.status === "RUNNING" &&
        r.chapter_id === selectedChapterId,
    )
    if (existingRun) {
      setToast({
        message: "该章节已有正在执行的生成任务",
        type: "error",
      })
      return
    }

    setPendingRunId(true)
    setIsGenerating(true)

    // 保存当前内容用于丢弃
    if (!chapterContentCache[selectedChapterId]) {
      try {
        const detail = await api.chapters.get(novelId, selectedChapterId)
        preGenContentRef.current = detail.content || ""
      } catch {
        preGenContentRef.current = ""
      }
    } else {
      preGenContentRef.current = chapterContentCache[selectedChapterId]
    }

    // 清空编辑器
    editorRef.current?.clearContent()

    try {
      const run = await api.runs.start(novelId, {
        kind: "generate",
        chapter_id: selectedChapterId,
      })

      setRunId(run.id)
      setPhase(run.phase || "writing")
      setShowHumanBar(false)
      setShowVerdictDialog(false)

      connectSSE(run.id)
    } catch (err) {
      setIsGenerating(false)
      setPendingRunId(false)
      const message =
        err instanceof Error ? err.message : "启动生成失败"

      if (
        typeof message === "string" &&
        message.includes("已有正在执行的 Run")
      ) {
        setToast({ message: "该章节已有正在执行的生成任务", type: "error" })
      } else if (
        typeof message === "string" &&
        message.includes("409")
      ) {
        setToast({ message: "该章节已有正在执行的生成任务", type: "error" })
      } else {
        setToast({ message: message as string, type: "error" })
      }
    } finally {
      setPendingRunId(false)
    }
  }, [
    selectedChapterId,
    novelId,
    isGenerating,
    pendingRunId,
    chapterContentCache,
    connectSSE,
  ])

  // ── 取消生成 ───────────────────────────────────────

  const cancelGeneration = useCallback(async () => {
    if (!runId || !novelId) return
    setCancelPending(true)
    try {
      await api.runs.cancel(novelId, runId)
      // SSE 会收到 cancelled done 事件，触发 verdict dialog
    } catch {
      setCancelPending(false)
    }
  }, [runId, novelId])

  // ── 裁决 ───────────────────────────────────────────

  const handleVerdictKeep = useCallback(() => {
    setShowVerdictDialog(false)
    setIsGenerating(false)
    setRunId(null)
  }, [])

  const handleVerdictDiscard = useCallback(() => {
    editorRef.current?.setContent(preGenContentRef.current)
    setShowVerdictDialog(false)
    setIsGenerating(false)
    setRunId(null)
  }, [])

  // ── AWAITING_HUMAN ────────────────────────────────

  const handleHumanAction = useCallback(() => {
    setShowHumanBar(false)
    setPhase("")
    setRunId(null)
    queryClient.invalidateQueries({ queryKey: ["chapters", novelId] })
    queryClient.invalidateQueries({ queryKey: ["runs", novelId] })
  }, [novelId, queryClient])

  // ── 素材插入 ───────────────────────────────────────

  const handleMaterialInsert = useCallback(
    (content: string) => {
      editorRef.current?.appendContent("\n\n" + content)
      setMaterialDrawerOpen(false)
    },
    [],
  )

  // ── 清理 ───────────────────────────────────────────

  useEffect(() => {
    return () => {
      sseRef.current?.close()
    }
  }, [])

  // ── Toast dismiss ─────────────────────────────────

  const dismissToast = useCallback(() => setToast(null), [])

  // ── 新建小说 ───────────────────────────────────────

  const [showCreate, setShowCreate] = useState(false)
  const [title, setTitle] = useState("")

  // ── 当前小说 ───────────────────────────────────────

  const currentNovel = novels.data?.find((n) => n.id === novelId)
  const selectedChapter = chapters.data?.find(
    (c) => c.id === selectedChapterId,
  )

  // ── 状态标签 ───────────────────────────────────────

  const statusLabel: Record<string, string> = {
    PLANNED: "已规划",
    WRITING: "写作中",
    DRAFT_READY: "草稿就绪",
    REVIEWING: "评审中",
    REVISING: "修订中",
    APPROVED: "已通过",
    FINALIZED: "已定稿",
    AWAITING_HUMAN: "待人工",
    planned: "已规划",
    writing: "写作中",
    draft_ready: "草稿就绪",
    reviewing: "评审中",
    revising: "修订中",
    approved: "已通过",
    finalized: "已定稿",
    awaiting_human: "待人工",
  }

  // ── 渲染 ───────────────────────────────────────────

  return (
    <div className="flex flex-col h-full">
      {/* Toast */}
      {toast && (
        <Toast
          message={toast.message}
          type={toast.type}
          onDismiss={dismissToast}
        />
      )}

      {/* 裁决对话框 */}
      {showVerdictDialog && (
        <VerdictDialog
          onKeep={handleVerdictKeep}
          onDiscard={handleVerdictDiscard}
        />
      )}

      {/* 标题行 */}
      <div className="flex items-center justify-between px-6 py-3 border-b shrink-0">
        <div className="flex items-center gap-3">
          {novelId && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => navigate("/workspace")}
            >
              &larr; 所有小说
            </Button>
          )}
          <h1 className="text-lg font-semibold">
            {currentNovel ? currentNovel.title : "工作区"}
          </h1>
        </div>
        {!novelId && (
          <Button size="sm" onClick={() => setShowCreate(!showCreate)}>
            <Plus className="w-4 h-4 mr-1" />
            新建小说
          </Button>
        )}
      </div>

      {/* 主内容区 */}
      <div className="flex flex-1 overflow-hidden">
        {!novelId ? (
          // ── 小说列表 ──────────────────────────────
          <div className="flex-1 overflow-auto p-6">
            {showCreate && (
              <div className="flex gap-2 mb-4 p-3 border rounded-md">
                <input
                  className="flex-1 px-3 py-1.5 border rounded text-sm"
                  placeholder="输入小说标题"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  onKeyDown={(e) =>
                    e.key === "Enter" &&
                    title.trim() &&
                    createNovel.mutate()
                  }
                />
                <Button
                  size="sm"
                  disabled={!title.trim() || createNovel.isPending}
                  onClick={() => createNovel.mutate()}
                >
                  {createNovel.isPending ? "创建中..." : "创建"}
                </Button>
              </div>
            )}
            <div className="grid gap-3">
              {novels.isLoading && (
                <p className="text-muted-foreground">加载中...</p>
              )}
              {novels.data?.length === 0 && (
                <p className="text-muted-foreground">
                  暂无小说，点击上方按钮创建
                </p>
              )}
              {novels.data?.map((novel) => (
                <div
                  key={novel.id}
                  className="flex items-center justify-between p-3 border rounded-md cursor-pointer hover:bg-accent/50 transition-colors"
                  onClick={() => navigate(`/workspace/${novel.id}`)}
                >
                  <div>
                    <p className="font-medium">{novel.title}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {novel.metadata.chapter_count} 章 &middot;{" "}
                      {novel.metadata.word_count} 字
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={(e) => {
                      e.stopPropagation()
                      if (confirm("确认删除？"))
                        deleteNovel.mutate(novel.id)
                    }}
                  >
                    <Trash2 className="w-4 h-4" />
                  </Button>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <>
            {/* ── 左侧章节列表 ──────────────────────── */}
            <div className="w-72 shrink-0 border-r overflow-auto bg-muted/20">
              <div className="p-3 border-b flex items-center justify-between">
                <span className="text-xs font-medium text-muted-foreground">
                  章节
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setMaterialDrawerOpen(true)}
                >
                  <BookOpen className="w-3.5 h-3.5 mr-1" />
                  素材
                </Button>
              </div>

              {chapters.isLoading && (
                <div className="flex justify-center py-8">
                  <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
                </div>
              )}

              {chapters.data?.length === 0 && (
                <p className="text-xs text-muted-foreground p-4">
                  暂无章节，请先通过 CLI 或大纲页规划章节
                </p>
              )}

              <div className="py-1">
                {chapters.data?.map((ch) => (
                  <button
                    key={ch.id}
                    onClick={() => selectChapter(ch)}
                    disabled={isGenerating}
                    className={`w-full text-left px-3 py-2.5 border-b border-border/50 last:border-b-0 transition-colors ${
                      selectedChapterId === ch.id
                        ? "bg-accent text-accent-foreground"
                        : "hover:bg-accent/50 text-muted-foreground hover:text-foreground"
                    } ${isGenerating ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground shrink-0 w-6">
                        #{ch.index}
                      </span>
                      <span className="text-sm font-medium truncate flex-1">
                        {ch.title || `第 ${ch.index} 章`}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 mt-0.5 ml-8">
                      <span
                        className={`text-[10px] px-1.5 py-0.5 rounded-full border ${
                          ch.status === "FINALIZED" || ch.status === "finalized"
                            ? "text-green-600 border-green-300 bg-green-50 dark:bg-green-950/20"
                            : ch.status === "APPROVED" || ch.status === "approved"
                              ? "text-blue-600 border-blue-300 bg-blue-50 dark:bg-blue-950/20"
                              : ch.status === "AWAITING_HUMAN" || ch.status === "awaiting_human"
                                ? "text-purple-600 border-purple-300 bg-purple-50 dark:bg-purple-950/20"
                                : ch.status === "PLANNED" || ch.status === "planned"
                                  ? "text-gray-500 border-gray-300"
                                  : "text-amber-600 border-amber-300 bg-amber-50 dark:bg-amber-950/20"
                        }`}
                      >
                        {statusLabel[ch.status] || ch.status}
                      </span>
                      <span className="text-[10px] text-muted-foreground">
                        {ch.word_count} 字
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            </div>

            {/* ── 右侧编辑器 ──────────────────────── */}
            <div className="flex-1 flex flex-col overflow-hidden">
              {/* 编辑器工具栏 */}
              <div className="flex items-center gap-2 px-4 py-2 border-b shrink-0">
                <span className="text-sm font-medium">
                  {selectedChapter
                    ? `#${selectedChapter.index} ${selectedChapter.title || ""}`
                    : "选择章节"}
                </span>

                {/* 版本选择器 */}
                {selectedChapter && selectedChapter.version > 1 && novelId && (
                  <VersionSelector
                    novelId={novelId}
                    chapterId={selectedChapter.id}
                    currentVersion={selectedChapter.version}
                  />
                )}

                <div className="flex-1" />

                {/* 模式 tab */}
                {selectedChapter && (showHumanBar || selectedChapter.status === "awaiting_human") && (
                  <div className="flex items-center border rounded-md overflow-hidden text-xs">
                    <button
                      className={`px-2.5 py-1 ${mode === "write" ? "bg-accent font-medium" : "text-muted-foreground hover:bg-accent/50"}`}
                      onClick={() => setMode("write")}
                    >
                      写作
                    </button>
                    <button
                      className={`px-2.5 py-1 border-l ${mode === "review" ? "bg-accent font-medium" : "text-muted-foreground hover:bg-accent/50"}`}
                      onClick={() => setMode("review")}
                    >
                      评审
                    </button>
                  </div>
                )}

                {selectedChapterId && !isGenerating && !showHumanBar && (
                  <Button
                    size="sm"
                    onClick={startGeneration}
                    disabled={pendingRunId}
                  >
                    {pendingRunId ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" />
                    ) : (
                      <Play className="w-3.5 h-3.5 mr-1" />
                    )}
                    开始生成
                  </Button>
                )}
              </div>

              {/* 结论卡（评审模式） */}
              {mode === "review" && lastVerdict && (
                <VerdictCard verdict={lastVerdict.verdict} issues={lastVerdict.issues} />
              )}

              {/* 编辑器内容 */}
              <div className="flex-1 overflow-auto p-4">
                {selectedChapterId ? (
                  <Editor
                    ref={editorRef}
                    readonly={isGenerating || mode === "review"}
                    placeholder="选择章节开始编辑..."
                  />
                ) : (
                  <div className="flex items-center justify-center h-full text-muted-foreground">
                    <p className="text-sm">从左侧选择章节开始编辑</p>
                  </div>
                )}
              </div>

              {/* 状态条 */}
              {isGenerating && (
                <StatusBar
                  phase={phase}
                  onCancel={cancelGeneration}
                  cancelPending={cancelPending}
                />
              )}

              {/* AWAITING_HUMAN 人工门底栏 */}
              {showHumanBar && selectedChapterIndex !== null && (
                <HumanBar
                  chapterIndex={selectedChapterIndex}
                  novelId={novelId}
                  onAction={handleHumanAction}
                />
              )}
            </div>
          </>
        )}
      </div>

      {/* 素材抽屉 */}
      {novelId && (
        <MaterialDrawer
          novelId={novelId}
          open={materialDrawerOpen}
          onClose={() => setMaterialDrawerOpen(false)}
          onInsert={handleMaterialInsert}
        />
      )}
    </div>
  )
}
