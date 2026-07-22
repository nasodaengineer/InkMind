import { BookOpen } from "lucide-react"

export default function OutlinePage() {
  return (
    <div className="p-6 max-w-5xl mx-auto">
      <h1 className="text-2xl font-semibold mb-4">大纲</h1>
      <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
        <BookOpen className="w-12 h-12 mb-4 opacity-30" />
        <p>大纲规划视图（#35）将在后续实现</p>
      </div>
    </div>
  )
}
