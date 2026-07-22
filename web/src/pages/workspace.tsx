import { useState } from "react"
import { useParams, useNavigate } from "react-router"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Plus, Trash2 } from "lucide-react"

export default function WorkspacePage() {
  const { novelId } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [title, setTitle] = useState("")
  const [showCreate, setShowCreate] = useState(false)

  const novels = useQuery({ queryKey: ["novels"], queryFn: api.novels.list })

  const chapters = useQuery({
    queryKey: ["chapters", novelId],
    queryFn: () => api.chapters.list(novelId!),
    enabled: !!novelId,
  })

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

  const currentNovel = novels.data?.find((n) => n.id === novelId)

  const statusLabel: Record<string, string> = {
    PLANNED: "已规划",
    WRITING: "写作中",
    DRAFT_READY: "草稿就绪",
    REVIEWING: "评审中",
    REVISING: "修订中",
    APPROVED: "已通过",
    FINALIZED: "已定稿",
    AWAITING_HUMAN: "待人工",
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* 小说选择器 */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold">
          {currentNovel ? currentNovel.title : "工作区"}
        </h1>
        <Button size="sm" onClick={() => setShowCreate(!showCreate)}>
          <Plus className="w-4 h-4 mr-1" />
          新建小说
        </Button>
      </div>

      {/* 新建小说表单 */}
      {showCreate && (
        <div className="flex gap-2 mb-4 p-3 border rounded-md">
          <input
            className="flex-1 px-3 py-1.5 border rounded text-sm"
            placeholder="输入小说标题"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && title.trim() && createNovel.mutate()}
          />
          <Button
            size="sm"
            disabled={!title.trim() || createNovel.isPending}
            onClick={() => createNovel.mutate()}
          >
            {createNovel.isPending ? "创建中…" : "创建"}
          </Button>
        </div>
      )}

      {/* 小说列表 */}
      {!novelId && (
        <div className="grid gap-3">
          {novels.isLoading && <p className="text-muted-foreground">加载中…</p>}
          {novels.data?.length === 0 && (
            <p className="text-muted-foreground">暂无小说，点击上方按钮创建</p>
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
                  {novel.metadata.chapter_count} 章 · {novel.metadata.word_count} 字
                </p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                onClick={(e) => {
                  e.stopPropagation()
                  if (confirm("确认删除？")) deleteNovel.mutate(novel.id)
                }}
              >
                <Trash2 className="w-4 h-4" />
              </Button>
            </div>
          ))}
        </div>
      )}

      {/* 章节列表 */}
      {novelId && (
        <div>
          <div className="flex items-center gap-2 mb-4">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => navigate("/workspace")}
            >
              ← 所有小说
            </Button>
          </div>
          {chapters.isLoading && <p className="text-muted-foreground">加载中…</p>}
          {chapters.data?.length === 0 && (
            <p className="text-muted-foreground">暂无章节，请先通过 CLI 或大纲页规划章节</p>
          )}
          <div className="space-y-2">
            {chapters.data?.map((ch) => (
              <div
                key={ch.id}
                className="flex items-center gap-3 p-3 border rounded-md"
              >
                <span className="text-sm text-muted-foreground w-8 shrink-0">
                  #{ch.index}
                </span>
                <div className="flex-1 min-w-0">
                  <p className="font-medium truncate">{ch.title}</p>
                  <p className="text-xs text-muted-foreground truncate">
                    {ch.summary || "无摘要"}
                  </p>
                </div>
                <span className={`text-xs px-2 py-0.5 rounded-full border ${
                  ch.status === "FINALIZED" ? "text-green-600 border-green-300 bg-green-50" :
                  ch.status === "APPROVED" ? "text-blue-600 border-blue-300 bg-blue-50" :
                  ch.status === "PLANNED" ? "text-gray-500 border-gray-300" :
                  "text-amber-600 border-amber-300 bg-amber-50"
                }`}>
                  {statusLabel[ch.status] || ch.status}
                </span>
                <span className="text-xs text-muted-foreground w-16 text-right">
                  {ch.word_count} 字
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
