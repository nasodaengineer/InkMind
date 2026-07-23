/* 生成状态条 — 显示当前 phase + 脉冲动画 + ■ 停止按钮 */

import { Button } from "@/components/ui/button"
import { Square, Loader2 } from "lucide-react"

export interface StatusBarProps {
  phase: string
  onCancel: () => void
  cancelPending?: boolean
}

const PHASE_LABELS: Record<string, string> = {
  planning: "规划中...",
  writing: "写作中...",
  reviewing: "评审中...",
  revising: "修订中...",
  complete: "已完成",
  awaiting_human: "待人工确认",
}

export default function StatusBar({
  phase,
  onCancel,
  cancelPending = false,
}: StatusBarProps) {
  const label = PHASE_LABELS[phase] || phase || "处理中..."

  return (
    <div className="flex items-center gap-3 px-4 py-2 border-t bg-background">
      {/* 脉冲动画 */}
      <span className="relative flex h-2.5 w-2.5">
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
        <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-blue-500" />
      </span>

      {/* 阶段标签 */}
      <span className="text-sm text-muted-foreground">{label}</span>

      {/* 弹性间距 */}
      <div className="flex-1" />

      {/* 停止按钮 */}
      <Button
        variant="destructive"
        size="sm"
        onClick={onCancel}
        disabled={cancelPending}
      >
        {cancelPending ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
        ) : (
          <Square className="w-3.5 h-3.5 fill-current" />
        )}
        <span>停止</span>
      </Button>
    </div>
  )
}
