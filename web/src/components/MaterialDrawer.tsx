/* 写作台素材旁路面板（右侧抽屉）。

   在 workspace 页面中展示，支持：
   - 搜索框 + chip 过滤
   - 卡片列表（展开看四字段：title/content/type/tags）
   - "插入正文"按钮 → 通过回调写入编辑器光标位置
*/

import { useState, useEffect, useCallback } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  X,
  Search,
  Loader2,
  ChevronDown,
  ChevronRight,
} from "lucide-react"
import { api, FRAGMENT_TYPES, type MaterialFragment } from "@/lib/api"
import { cn } from "@/lib/utils"

export interface MaterialDrawerProps {
  novelId: string
  open: boolean
  onClose: () => void
  onInsert: (content: string) => void
}

export default function MaterialDrawer({
  novelId,
  open,
  onClose,
  onInsert,
}: MaterialDrawerProps) {
  const [query, setQuery] = useState("")
  const [debouncedQuery, setDebouncedQuery] = useState("")
  const [selectedTypes, setSelectedTypes] = useState<string[]>([])
  const [selectedTag, setSelectedTag] = useState<string | null>(null)
  const [tagInput, setTagInput] = useState("")
  const [tagSuggestions, setTagSuggestions] = useState<{ tag: string; count: number }[]>([])
  const [showTagDropdown, setShowTagDropdown] = useState(false)

  // 防抖
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), 400)
    return () => clearTimeout(timer)
  }, [query])

  // 标签建议
  useEffect(() => {
    if (!open) return
    api.materials
      .tagAutocomplete(novelId, tagInput || undefined)
      .then(setTagSuggestions)
      .catch(() => {})
  }, [novelId, tagInput, open])

  // 搜索
  const { data, isLoading } = useQuery({
    queryKey: ["materialDrawer", novelId, debouncedQuery, selectedTypes, selectedTag],
    queryFn: () =>
      api.materials.searchFragments(novelId, {
        q: debouncedQuery || undefined,
        type: selectedTypes.length === 1 ? selectedTypes[0] : undefined,
        tag: selectedTag || undefined,
        page: 1,
        per_page: 30,
      }),
    enabled: open,
  })

  const toggleType = useCallback((t: string) => {
    setSelectedTypes((prev) =>
      prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t],
    )
  }, [])

  if (!open) return null

  return (
    <div className="fixed inset-y-0 right-0 w-96 z-50 flex">
      {/* 遮罩 */}
      <div className="fixed inset-0 bg-black/20" onClick={onClose} />

      {/* 抽屉面板 */}
      <div className="relative w-full bg-background border-l shadow-xl flex flex-col">
        {/* 标题栏 */}
        <div className="flex items-center justify-between px-4 py-3 border-b">
          <h2 className="text-sm font-semibold">素材库</h2>
          <button
            onClick={onClose}
            className="p-1 rounded-md hover:bg-accent text-muted-foreground hover:text-foreground"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* 搜索栏 */}
        <div className="px-4 pt-3 pb-2 space-y-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索素材..."
              className="w-full pl-8 pr-2 py-1.5 rounded-md border border-input bg-background text-xs"
            />
          </div>

          {/* 类型 chip */}
          <div className="flex flex-wrap gap-1">
            {FRAGMENT_TYPES.slice(0, 6).map((t) => (
              <button
                key={t}
                onClick={() => toggleType(t)}
                className={cn(
                  "px-1.5 py-0.5 rounded-full text-[10px] font-medium transition-colors",
                  selectedTypes.includes(t)
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-muted-foreground hover:bg-accent",
                )}
              >
                {t}
              </button>
            ))}
          </div>

          {/* 标签输入 */}
          <div className="relative">
            <div className="flex flex-wrap gap-1 items-center">
              {selectedTag && (
                <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium bg-primary/20 text-primary">
                  #{selectedTag}
                  <button onClick={() => setSelectedTag(null)} className="hover:text-red-500">&times;</button>
                </span>
              )}
              <input
                value={tagInput}
                onChange={(e) => setTagInput(e.target.value)}
                onFocus={() => setShowTagDropdown(true)}
                onBlur={() => setTimeout(() => setShowTagDropdown(false), 200)}
                placeholder={selectedTag ? "切换..." : "标签..."}
                className="flex-1 px-1.5 py-0.5 rounded border border-input bg-background text-[10px] min-w-[60px]"
              />
            </div>
            {showTagDropdown && tagSuggestions.length > 0 && (
              <div className="absolute z-10 mt-1 left-0 w-full max-h-40 overflow-y-auto rounded-md border bg-popover shadow-md">
                {tagSuggestions.map((s) => (
                  <button
                    key={s.tag}
                    type="button"
                    onMouseDown={() => {
                      setSelectedTag(selectedTag === s.tag ? null : s.tag)
                      setShowTagDropdown(false)
                      setTagInput("")
                    }}
                    className={cn(
                      "block w-full text-left px-2 py-1 text-xs hover:bg-accent",
                      selectedTag === s.tag && "bg-accent font-medium",
                    )}
                  >
                    {s.tag} <span className="text-muted-foreground">({s.count})</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* 结果列表 */}
        <div className="flex-1 overflow-y-auto px-4 pb-4">
          {isLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
            </div>
          ) : data && data.items.length > 0 ? (
            <div className="space-y-1.5 pt-2">
              {data.items.map((fragment) => (
                <DrawerCard
                  key={fragment.id}
                  fragment={fragment}
                  onInsert={onInsert}
                />
              ))}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Search className="w-8 h-8 mb-2 opacity-30" />
              <p className="text-xs">
                {debouncedQuery || selectedTypes.length > 0 || selectedTag
                  ? "未找到匹配片段"
                  : "输入关键词搜索素材"}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function DrawerCard({
  fragment,
  onInsert,
}: {
  fragment: MaterialFragment
  onInsert: (content: string) => void
}) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="border rounded-md bg-card">
      <div
        className="flex items-start gap-2 p-2 cursor-pointer hover:bg-accent/30"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-xs font-medium truncate">{fragment.title}</span>
            <span className="shrink-0 px-1 py-0.5 rounded text-[9px] font-medium bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
              {fragment.type}
            </span>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">
            {fragment.content}
          </p>
          {fragment.tags.length > 0 && (
            <div className="flex flex-wrap gap-0.5 mt-1">
              {fragment.tags.slice(0, 3).map((tag) => (
                <span
                  key={tag}
                  className="inline-flex items-center px-1 py-0 rounded text-[9px] bg-muted text-muted-foreground"
                >
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="shrink-0 text-muted-foreground">
          {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        </div>
      </div>

      {/* 展开详情 */}
      {expanded && (
        <div className="px-2 pb-2 space-y-1.5 border-t pt-1.5">
          <div>
            <span className="text-[10px] font-medium text-muted-foreground">内容</span>
            <p className="text-xs mt-0.5">{fragment.content}</p>
          </div>

          {fragment.tags.length > 0 && (
            <div>
              <span className="text-[10px] font-medium text-muted-foreground">标签</span>
              <div className="flex flex-wrap gap-0.5 mt-0.5">
                {fragment.tags.map((tag) => (
                  <span
                    key={tag}
                    className="inline-flex items-center px-1 py-0 rounded text-[9px] bg-muted text-muted-foreground"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </div>
          )}

          {fragment.source_quote && (
            <div>
              <span className="text-[10px] font-medium text-muted-foreground">原文引用</span>
              <p className="text-[10px] text-muted-foreground/80 mt-0.5 border-l-2 pl-1.5 italic">
                "{fragment.source_quote}"
              </p>
            </div>
          )}

          {fragment.reusability_note && (
            <div>
              <span className="text-[10px] font-medium text-muted-foreground">复用说明</span>
              <p className="text-[10px] text-muted-foreground/80 mt-0.5">{fragment.reusability_note}</p>
            </div>
          )}

          {/* 插入按钮 */}
          <button
            onClick={(e) => {
              e.stopPropagation()
              onInsert(fragment.content)
            }}
            className="w-full mt-1 px-2 py-1 rounded text-[10px] font-medium bg-primary text-primary-foreground hover:bg-primary/90"
          >
            插入正文
          </button>
        </div>
      )}
    </div>
  )
}
