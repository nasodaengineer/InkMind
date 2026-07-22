import { useState, useEffect, useCallback, type FormEvent } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import {
  Box,
  Plus,
  Trash2,
  Upload,
  Loader2,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Edit3,
  RotateCcw,
  FileText,
} from "lucide-react"
import { api, FRAGMENT_TYPES, type MaterialSource, type MaterialChunk, type MaterialFragment } from "@/lib/api"
import { cn } from "@/lib/utils"

// ── 常量 ──

const STATUS_LABELS: Record<string, string> = {
  pending: "待处理",
  processing: "处理中",
  done: "已完成",
  failed: "失败",
}

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400",
  processing: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
  done: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
  failed: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
  low_quality: "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400",
}

const CHUNK_ICONS: Record<string, typeof CheckCircle2> = {
  done: CheckCircle2,
  failed: XCircle,
  low_quality: AlertTriangle,
  pending: Loader2,
}

// ── 标签推荐（Top 50 复用概念） ──

const SUGGESTED_TAGS = [
  "对话", "描写", "叙事", "抒情", "议论", "说明", "倒叙", "插叙", "对比",
  "象征", "隐喻", "铺垫", "伏笔", "悬念", "反转", "高潮", "结尾", "开头",
  "人物", "主角", "配角", "反派", "群像", "对话", "独白", "心理描写",
  "动作描写", "神态描写", "环境描写", "场景", "战斗", "爱情", "友情",
  "亲情", "冒险", "悬疑", "推理", "奇幻", "科幻", "现实", "历史",
  "战争", "政治", "商战", "校园", "都市", "田园", "旅行", "美食",
  "节奏", "情绪", "氛围",
]

// ── 主组件 ──

export default function MaterialsPage() {
  const [activeNovelId, setActiveNovelId] = useState<string>("")
  const [view, setView] = useState<"ledger" | "import" | "preview">("ledger")
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null)

  // 查询小说列表以获取当前选定小说
  const { data: novels } = useQuery({
    queryKey: ["novels"],
    queryFn: () => api.novels.list(),
  })

  useEffect(() => {
    if (novels && novels.length > 0 && !activeNovelId) {
      setActiveNovelId(novels[0].id)
    }
  }, [novels, activeNovelId])

  if (!activeNovelId) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <h1 className="text-2xl font-semibold mb-4">素材</h1>
        <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
          <Box className="w-12 h-12 mb-4 opacity-30" />
          <p>请先创建或选择一部小说</p>
        </div>
      </div>
    )
  }

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold">素材</h1>
        <div className="flex gap-2">
          <button
            onClick={() => setView("ledger")}
            className={cn(
              "px-3 py-1.5 text-sm rounded-md transition-colors",
              view === "ledger"
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            台账
          </button>
          <button
            onClick={() => setView("import")}
            className={cn(
              "px-3 py-1.5 text-sm rounded-md transition-colors",
              view === "import"
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            导入
          </button>
          <button
            onClick={() => { setView("preview"); setSelectedSourceId(null) }}
            className={cn(
              "px-3 py-1.5 text-sm rounded-md transition-colors",
              view === "preview"
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            预览提交
          </button>
        </div>
      </div>

      {view === "import" && (
        <ImportWizard
          novelId={activeNovelId}
          onComplete={(sourceId) => {
            setSelectedSourceId(sourceId)
            setView("preview")
          }}
        />
      )}

      {view === "ledger" && (
        <SourceLedger
          novelId={activeNovelId}
          onSelectSource={(id) => { setSelectedSourceId(id); setView("preview") }}
        />
      )}

      {view === "preview" && (
        <PreviewSubmit
          novelId={activeNovelId}
          sourceId={selectedSourceId}
        />
      )}
    </div>
  )
}

// ── 导入向导 ──

function ImportWizard({
  novelId,
  onComplete,
}: {
  novelId: string
  onComplete: (sourceId: string) => void
}) {
  const [rawText, setRawText] = useState("")
  const [importing, setImporting] = useState(false)
  const [decomposing, setDecomposing] = useState(false)
  const [sourceId, setSourceId] = useState<string | null>(null)
  const [progress, setProgress] = useState<{
    total: number
    done: number
    failed: number
    low_quality: number
    pending: number
    chunks: Array<{ id: string; index: number; status: string; error_message: string | null }>
  } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isDuplicate, setIsDuplicate] = useState(false)
  const queryClient = useQueryClient()

  const importMutation = useMutation({
    mutationFn: (text: string) => api.materials.importSource(novelId, text),
    onSuccess: (data) => {
      setSourceId(data.source.id)
      setIsDuplicate(data.is_duplicate)
      setImporting(false)
      queryClient.invalidateQueries({ queryKey: ["sources", novelId] })
    },
    onError: (err: Error) => {
      setError(err.message)
      setImporting(false)
    },
  })

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!rawText.trim()) return
    setError(null)
    setImporting(true)
    importMutation.mutate(rawText.trim())
  }

  const handleDecompose = async () => {
    if (!sourceId) return
    setDecomposing(true)
    setError(null)
    try {
      await api.materials.startDecompose(novelId, sourceId)
      // 轮询进度
      const poll = setInterval(async () => {
        try {
          const p = await api.materials.getDecomposeProgress(novelId, sourceId)
          setProgress(p)
          if (p.done + p.failed === p.total || p.status === "done" || p.status === "failed") {
            clearInterval(poll)
            setDecomposing(false)
            queryClient.invalidateQueries({ queryKey: ["sources", novelId] })
            queryClient.invalidateQueries({ queryKey: ["fragments", novelId] })
          }
        } catch {
          clearInterval(poll)
          setDecomposing(false)
        }
      }, 1500)
    } catch (err: any) {
      setError(err.message)
      setDecomposing(false)
    }
  }

  return (
    <div className="space-y-6">
      {!sourceId ? (
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1">粘贴素材原文</label>
            <textarea
              value={rawText}
              onChange={(e) => setRawText(e.target.value)}
              rows={15}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm resize-y"
              placeholder="在此粘贴小说素材原文...&#10;&#10;支持任意文本格式，系统将自动按段落切块处理。"
              disabled={importing}
            />
            <p className="text-xs text-muted-foreground mt-1">
              共 {rawText.length} 字 | 上限 100,000 字
            </p>
          </div>

          {error && (
            <div className="bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 rounded-md p-3 text-sm text-red-700 dark:text-red-400">
              {error}
            </div>
          )}

          <div className="flex gap-2">
            <button
              type="submit"
              disabled={importing || !rawText.trim()}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
            >
              {importing ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Upload className="w-4 h-4" />
              )}
              {importing ? "导入中..." : "导入"}
            </button>
          </div>
        </form>
      ) : (
        <div className="space-y-4">
          {isDuplicate && (
            <div className="bg-yellow-50 dark:bg-yellow-950/30 border border-yellow-200 dark:border-yellow-800 rounded-md p-3 text-sm text-yellow-700 dark:text-yellow-400">
              检测到重复内容，已返回已有素材记录
            </div>
          )}

          {!decomposing && progress && progress.done + progress.failed === progress.total && (
            <div className="bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 rounded-md p-3 text-sm text-green-700 dark:text-green-400">
              拆解完成！已完成 {progress.done} 块，失败 {progress.failed} 块
            </div>
          )}

          {error && (
            <div className="bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 rounded-md p-3 text-sm text-red-700 dark:text-red-400">
              {error}
            </div>
          )}

          {/* 进度条 */}
          {progress && (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-sm">
                <span className="text-muted-foreground">拆解进度</span>
                <span className="font-medium">{progress.done + progress.failed}/{progress.total}</span>
              </div>
              <div className="flex gap-1 h-2 rounded-full overflow-hidden bg-muted">
                {progress.done > 0 && (
                  <div
                    className="bg-green-500 transition-all"
                    style={{ width: `${(progress.done / progress.total) * 100}%` }}
                  />
                )}
                {progress.low_quality > 0 && (
                  <div
                    className="bg-orange-500 transition-all"
                    style={{ width: `${(progress.low_quality / progress.total) * 100}%` }}
                  />
                )}
                {progress.failed > 0 && (
                  <div
                    className="bg-red-500 transition-all"
                    style={{ width: `${(progress.failed / progress.total) * 100}%` }}
                  />
                )}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {progress.chunks.map((chunk) => {
                  const Icon = CHUNK_ICONS[chunk.status] || Loader2
                  const colorClass = STATUS_COLORS[chunk.status] || "bg-gray-100 text-gray-800"
                  return (
                    <span
                      key={chunk.id}
                      title={chunk.error_message || chunk.status}
                      className={cn(
                        "inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium",
                        colorClass,
                      )}
                    >
                      <Icon className="w-2.5 h-2.5" />
                      {chunk.index + 1}
                    </span>
                  )
                })}
              </div>
            </div>
          )}

          <div className="flex gap-2">
            {!decomposing && !progress && (
              <button
                onClick={handleDecompose}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90"
              >
                <Loader2 className="w-4 h-4" />
                开始拆解
              </button>
            )}

            {progress && progress.done + progress.failed === progress.total && (
              <button
                onClick={() => onComplete(sourceId)}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90"
              >
                <FileText className="w-4 h-4" />
                预览提交
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ── 来源台账 ──

function SourceLedger({
  novelId,
  onSelectSource,
}: {
  novelId: string
  onSelectSource: (id: string) => void
}) {
  const { data: sources, isLoading } = useQuery({
    queryKey: ["sources", novelId],
    queryFn: () => api.materials.listSources(novelId),
  })

  if (isLoading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (!sources || sources.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
        <Box className="w-12 h-12 mb-4 opacity-30" />
        <p>暂无素材，请先导入</p>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {sources.map((source) => (
        <SourceCard
          key={source.id}
          source={source}
          novelId={novelId}
          onSelect={() => onSelectSource(source.id)}
        />
      ))}
    </div>
  )
}

function SourceCard({
  source,
  novelId,
  onSelect,
}: {
  source: MaterialSource
  novelId: string
  onSelect: () => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [chunks, setChunks] = useState<MaterialChunk[]>([])
  const [fragments, setFragments] = useState<MaterialFragment[]>([])
  const [loadingDetail, setLoadingDetail] = useState(false)
  const queryClient = useQueryClient()

  const loadDetail = useCallback(async () => {
    if (expanded) return
    setLoadingDetail(true)
    try {
      const detail = await api.materials.getSource(novelId, source.id)
      setChunks(detail.chunks)
      setFragments(detail.fragments)
    } finally {
      setLoadingDetail(false)
    }
  }, [novelId, source.id, expanded])

  const handleToggle = () => {
    if (!expanded) loadDetail()
    setExpanded(!expanded)
  }

  const deleteMutation = useMutation({
    mutationFn: () => api.materials.deleteSource(novelId, source.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources", novelId] })
    },
  })

  const rerunMutation = useMutation({
    mutationFn: () => api.materials.rerunFailed(novelId, source.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources", novelId] })
    },
  })

  const colorClass = STATUS_COLORS[source.status] || "bg-gray-100 text-gray-800"

  return (
    <div className="border rounded-lg bg-card">
      <div
        className="flex items-center gap-3 p-3 cursor-pointer hover:bg-accent/50"
        onClick={handleToggle}
      >
        <button className="text-muted-foreground">
          {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        </button>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium truncate">
              素材 #{source.id.slice(0, 8)}
            </span>
            <span className={cn("px-1.5 py-0.5 rounded text-[10px] font-medium", colorClass)}>
              {STATUS_LABELS[source.status] || source.status}
            </span>
          </div>
          <div className="flex gap-3 mt-0.5 text-xs text-muted-foreground">
            <span>{source.word_count} 字</span>
            <span>{source.chunk_count} 块</span>
            <span>{source.fragment_count} 片段</span>
            <span>{new Date(source.created_at).toLocaleDateString("zh-CN")}</span>
          </div>
        </div>
        <div className="flex gap-1">
          <button
            onClick={(e) => { e.stopPropagation(); onSelect() }}
            className="p-1.5 rounded-md hover:bg-accent text-muted-foreground hover:text-foreground"
            title="预览"
          >
            <FileText className="w-4 h-4" />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); rerunMutation.mutate() }}
            className="p-1.5 rounded-md hover:bg-accent text-muted-foreground hover:text-foreground"
            title="重跑失败块"
          >
            <RotateCcw className="w-4 h-4" />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); if (confirm("确定删除此素材？")) deleteMutation.mutate() }}
            className="p-1.5 rounded-md hover:bg-red-100 dark:hover:bg-red-900/30 text-muted-foreground hover:text-red-600"
            title="删除"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {expanded && (
        <div className="border-t px-3 py-2 space-y-2">
          {loadingDetail ? (
            <div className="flex justify-center py-4">
              <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <>
              {/* 块列表 */}
              {chunks.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-muted-foreground mb-1">
                    拆解块 ({chunks.length})
                  </h4>
                  <div className="grid grid-cols-8 sm:grid-cols-12 md:grid-cols-16 gap-1">
                    {chunks.map((chunk) => {
                      const Icon = CHUNK_ICONS[chunk.status] || Loader2
                      const color = STATUS_COLORS[chunk.status] || "bg-gray-100"
                      return (
                        <span
                          key={chunk.id}
                          title={chunk.error_message || `${chunk.content.slice(0, 50)}...`}
                          className={cn(
                            "inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium",
                            color,
                          )}
                        >
                          <Icon className="w-2.5 h-2.5" />
                          {chunk.chunk_index + 1}
                        </span>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* 片段摘要表 */}
              {fragments.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-muted-foreground mb-1">
                    片段 ({fragments.length})
                  </h4>
                  <div className="max-h-40 overflow-y-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="text-muted-foreground border-b">
                          <th className="text-left py-1 pr-2">标题</th>
                          <th className="text-left py-1 pr-2">类型</th>
                          <th className="text-left py-1 pr-2">标签</th>
                          <th className="text-right py-1">编辑</th>
                        </tr>
                      </thead>
                      <tbody>
                        {fragments.map((f) => (
                          <tr key={f.id} className="border-b border-muted/30">
                            <td className="py-1 pr-2 max-w-[120px] truncate">{f.title}</td>
                            <td className="py-1 pr-2">{f.type}</td>
                            <td className="py-1 pr-2 max-w-[100px] truncate">
                              {f.tags.slice(0, 3).join(", ")}
                            </td>
                            <td className="py-1 text-right">
                              {f.user_edited && (
                                <span className="text-[10px] text-blue-500">已编辑</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── 预览提交页 ──

function PreviewSubmit({
  novelId,
  sourceId,
}: {
  novelId: string
  sourceId: string | null
}) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [editingId, setEditingId] = useState<string | null>(null)
  const [showNewForm, setShowNewForm] = useState(false)
  const queryClient = useQueryClient()

  // 片段查询
  const { data: fragments, isLoading } = useQuery({
    queryKey: ["fragments", novelId, sourceId],
    queryFn: () => {
      if (sourceId) {
        return api.materials.getSource(novelId, sourceId).then((r) => r.fragments)
      }
      return api.materials.listFragments(novelId)
    },
  })

  // 全选/取消
  useEffect(() => {
    if (fragments) {
      setSelectedIds(new Set(fragments.map((f) => f.id)))
    }
  }, [fragments])

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleAll = () => {
    if (!fragments) return
    if (selectedIds.size === fragments.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(fragments.map((f) => f.id)))
    }
  }

  if (isLoading) {
    return <div className="flex justify-center py-12"><Loader2 className="w-4 h-4 animate-spin text-muted-foreground" /></div>
  }

  if (!fragments || fragments.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
        <Box className="w-12 h-12 mb-4 opacity-30" />
        <p>暂无片段素材</p>
        <button
          onClick={() => setShowNewForm(true)}
          className="mt-4 inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-primary text-primary-foreground text-sm"
        >
          <Plus className="w-4 h-4" />
          新建片段
        </button>
        {showNewForm && (
          <NewFragmentForm
            novelId={novelId}
            onDone={() => { setShowNewForm(false); queryClient.invalidateQueries({ queryKey: ["fragments", novelId] }) }}
          />
        )}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* 操作栏 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-sm">
            <input
              type="checkbox"
              checked={fragments.length > 0 && selectedIds.size === fragments.length}
              onChange={toggleAll}
              className="rounded"
            />
            全选 ({fragments.length})
          </label>
          <button
            onClick={() => setShowNewForm(true)}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs font-medium bg-primary text-primary-foreground hover:bg-primary/90"
          >
            <Plus className="w-3 h-3" />
            新建
          </button>
        </div>
        <div className="flex gap-2">
          <span className="text-xs text-muted-foreground">
            已选 {selectedIds.size} 条
          </span>
        </div>
      </div>

      {showNewForm && (
        <NewFragmentForm
          novelId={novelId}
          onDone={() => { setShowNewForm(false); queryClient.invalidateQueries({ queryKey: ["fragments", novelId] }) }}
        />
      )}

      {/* 片段列表 */}
      <div className="space-y-2">
        {fragments.map((fragment) => (
          <FragmentCard
            key={fragment.id}
            fragment={fragment}
            novelId={novelId}
            selected={selectedIds.has(fragment.id)}
            onToggleSelect={() => toggleSelect(fragment.id)}
            isEditing={editingId === fragment.id}
            onEdit={() => setEditingId(editingId === fragment.id ? null : fragment.id)}
            onDone={() => {
              setEditingId(null)
              queryClient.invalidateQueries({ queryKey: ["fragments", novelId] })
              queryClient.invalidateQueries({ queryKey: ["sources", novelId] })
            }}
          />
        ))}
      </div>
    </div>
  )
}

// ── 片段卡片 ──

function FragmentCard({
  fragment,
  novelId,
  selected,
  onToggleSelect,
  isEditing,
  onEdit,
  onDone,
}: {
  fragment: MaterialFragment
  novelId: string
  selected: boolean
  onToggleSelect: () => void
  isEditing: boolean
  onEdit: () => void
  onDone: () => void
}) {
  const queryClient = useQueryClient()

  const deleteMutation = useMutation({
    mutationFn: () => api.materials.deleteFragment(novelId, fragment.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["fragments", novelId] })
    },
  })

  if (isEditing) {
    return (
      <FragmentEditForm
        fragment={fragment}
        novelId={novelId}
        onDone={onDone}
      />
    )
  }

  return (
    <div
      className={cn(
        "border rounded-lg p-3 transition-colors",
        selected ? "border-primary/50 bg-accent/30" : "bg-card",
      )}
    >
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggleSelect}
          className="mt-1 rounded"
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-sm truncate">{fragment.title}</span>
            <span className={cn(
              "px-1.5 py-0.5 rounded text-[10px] font-medium",
              "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
            )}>
              {fragment.type}
            </span>
            {fragment.user_edited && (
              <span className="text-[10px] text-blue-500 font-medium">已编辑</span>
            )}
          </div>
          <p className="text-sm text-muted-foreground mt-1 line-clamp-2">{fragment.content}</p>
          {fragment.tags.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1.5">
              {fragment.tags.slice(0, 5).map((tag) => (
                <span
                  key={tag}
                  className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] bg-muted text-muted-foreground"
                >
                  {tag}
                </span>
              ))}
            </div>
          )}
          {fragment.reusability_note && (
            <p className="text-xs text-muted-foreground italic mt-1">
              {fragment.reusability_note}
            </p>
          )}
          {fragment.source_quote && (
            <p className="text-xs text-muted-foreground/60 mt-0.5 border-l-2 pl-2">
              "{fragment.source_quote}"
            </p>
          )}
        </div>
        <div className="flex gap-1 shrink-0">
          <button
            onClick={onEdit}
            className="p-1.5 rounded-md hover:bg-accent text-muted-foreground hover:text-foreground"
            title="编辑"
          >
            <Edit3 className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => {
              if (fragment.user_edited) {
                alert("已编辑片段不能删除")
              } else {
                deleteMutation.mutate()
              }
            }}
            className="p-1.5 rounded-md hover:bg-red-100 dark:hover:bg-red-900/30 text-muted-foreground hover:text-red-600"
            title="删除"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>
  )
}

// ── 片段编辑表单 ──

function FragmentEditForm({
  fragment,
  novelId,
  onDone,
}: {
  fragment: MaterialFragment
  novelId: string
  onDone: () => void
}) {
  const [title, setTitle] = useState(fragment.title)
  const [content, setContent] = useState(fragment.content)
  const [type, setType] = useState(fragment.type)
  const [tags, setTags] = useState<string[]>(fragment.tags)
  const [reusabilityNote, setReusabilityNote] = useState(fragment.reusability_note)
  const [sourceQuote, setSourceQuote] = useState(fragment.source_quote || "")
  const [tagInput, setTagInput] = useState("")
  const [showTagSuggestions, setShowTagSuggestions] = useState(false)
  const queryClient = useQueryClient()

  const updateMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      api.materials.updateFragment(novelId, fragment.id, data),
    onSuccess: () => {
      onDone()
      queryClient.invalidateQueries({ queryKey: ["fragments", novelId] })
    },
  })

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    updateMutation.mutate({
      title,
      content,
      type,
      tags,
      reusability_note: reusabilityNote,
      source_quote: sourceQuote || null,
    })
  }

  const addTag = (tag: string) => {
    const t = tag.trim()
    if (t && !tags.includes(t)) {
      setTags([...tags, t])
    }
    setTagInput("")
    setShowTagSuggestions(false)
  }

  const removeTag = (tag: string) => {
    setTags(tags.filter((t) => t !== tag))
  }

  const filteredSuggestions = SUGGESTED_TAGS.filter(
    (t) => t.includes(tagInput) && !tags.includes(t),
  ).slice(0, 6)

  return (
    <form onSubmit={handleSubmit} className="border rounded-lg p-4 space-y-3 bg-card">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium mb-1">标题（≤20 字）</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value.slice(0, 20))}
            className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium mb-1">类型</label>
          <select
            value={type}
            onChange={(e) => setType(e.target.value)}
            className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
          >
            {FRAGMENT_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <label className="block text-xs font-medium mb-1">内容（≤2000 字）</label>
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value.slice(0, 2000))}
          rows={4}
          className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm resize-y"
        />
      </div>

      <div>
        <label className="block text-xs font-medium mb-1">标签（建议 3-6 个）</label>
        <div className="flex flex-wrap gap-1 mb-1.5">
          {tags.map((tag) => (
            <span
              key={tag}
              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-primary/10 text-primary"
            >
              {tag}
              <button type="button" onClick={() => removeTag(tag)} className="hover:text-red-500">&times;</button>
            </span>
          ))}
        </div>
        <div className="relative">
          <input
            value={tagInput}
            onChange={(e) => { setTagInput(e.target.value); setShowTagSuggestions(true) }}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addTag(tagInput) } }}
            onFocus={() => setShowTagSuggestions(true)}
            onBlur={() => setTimeout(() => setShowTagSuggestions(false), 200)}
            placeholder="输入标签..."
            className="w-full rounded-md border border-input bg-background px-2 py-1 text-xs"
          />
          {showTagSuggestions && filteredSuggestions.length > 0 && (
            <div className="absolute z-10 mt-1 w-full rounded-md border bg-popover shadow-md">
              {filteredSuggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  onMouseDown={() => addTag(s)}
                  className="block w-full text-left px-2 py-1 text-xs hover:bg-accent"
                >
                  {s}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div>
        <label className="block text-xs font-medium mb-1">原文引用（8-50字，可选）</label>
        <input
          value={sourceQuote}
          onChange={(e) => setSourceQuote(e.target.value.slice(0, 50))}
          className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
        />
      </div>

      <div>
        <label className="block text-xs font-medium mb-1">复用说明</label>
        <textarea
          value={reusabilityNote}
          onChange={(e) => setReusabilityNote(e.target.value.slice(0, 500))}
          rows={2}
          className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm resize-y"
        />
      </div>

      <div className="flex gap-2 justify-end">
        <button
          type="button"
          onClick={onDone}
          className="px-3 py-1.5 rounded-md text-sm border hover:bg-accent"
        >
          取消
        </button>
        <button
          type="submit"
          disabled={updateMutation.isPending}
          className="inline-flex items-center gap-1 px-3 py-1.5 rounded-md text-sm bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {updateMutation.isPending && <Loader2 className="w-3 h-3 animate-spin" />}
          保存
        </button>
      </div>
    </form>
  )
}

// ── 新建片段表单 ──

function NewFragmentForm({
  novelId,
  onDone,
}: {
  novelId: string
  onDone: () => void
}) {
  const [title, setTitle] = useState("")
  const [content, setContent] = useState("")
  const [type, setType] = useState<string>("misc")
  const [tags, setTags] = useState<string[]>([])
  const [tagInput, setTagInput] = useState("")
  const [reusabilityNote, setReusabilityNote] = useState("")
  const queryClient = useQueryClient()

  const createMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      api.materials.createFragment(novelId, data),
    onSuccess: () => {
      onDone()
      queryClient.invalidateQueries({ queryKey: ["fragments", novelId] })
      queryClient.invalidateQueries({ queryKey: ["sources", novelId] })
    },
  })

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    createMutation.mutate({
      title,
      content,
      type,
      tags,
      reusability_note: reusabilityNote,
    })
  }

  return (
    <form onSubmit={handleSubmit} className="border rounded-lg p-4 space-y-3 bg-card mt-2">
      <h3 className="text-sm font-medium">新建片段</h3>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium mb-1">标题</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value.slice(0, 20))}
            required
            className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium mb-1">类型</label>
          <select
            value={type}
            onChange={(e) => setType(e.target.value)}
            className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
          >
            {FRAGMENT_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
      </div>
      <div>
        <label className="block text-xs font-medium mb-1">内容（≤2000 字）</label>
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value.slice(0, 2000))}
          rows={4}
          required
          className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
        />
      </div>
      <div>
        <label className="block text-xs font-medium mb-1">标签（逗号分隔）</label>
        <input
          value={tagInput}
          onChange={(e) => { setTagInput(e.target.value); setTags(e.target.value.split(",").map(s => s.trim()).filter(Boolean)) }}
          placeholder="输入标签，用逗号分隔"
          className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
        />
        {tags.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {tags.map((tag) => (
              <span key={tag} className="px-1.5 py-0.5 rounded text-xs bg-primary/10 text-primary">{tag}</span>
            ))}
          </div>
        )}
      </div>
      <div>
        <label className="block text-xs font-medium mb-1">复用说明</label>
        <input
          value={reusabilityNote}
          onChange={(e) => setReusabilityNote(e.target.value.slice(0, 500))}
          className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
        />
      </div>
      <div className="flex gap-2 justify-end">
        <button type="button" onClick={onDone} className="px-3 py-1.5 rounded-md text-sm border hover:bg-accent">
          取消
        </button>
        <button
          type="submit"
          disabled={createMutation.isPending}
          className="inline-flex items-center gap-1 px-3 py-1.5 rounded-md text-sm bg-primary text-primary-foreground hover:bg-primary/90"
        >
          {createMutation.isPending && <Loader2 className="w-3 h-3 animate-spin" />}
          创建
        </button>
      </div>
    </form>
  )
}
