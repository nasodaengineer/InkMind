import { Settings } from "lucide-react"

export default function SystemPage() {
  return (
    <div className="p-6 max-w-5xl mx-auto">
      <h1 className="text-2xl font-semibold mb-4">系统</h1>
      <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
        <Settings className="w-12 h-12 mb-4 opacity-30" />
        <p>系统设置（#47）与观测面板（#46）将在后续实现</p>
      </div>
    </div>
  )
}
