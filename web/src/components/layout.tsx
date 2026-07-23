import { type ReactNode } from "react"
import { useLocation, useNavigate } from "react-router"
import { cn } from "@/lib/utils"
import { BookOpen, Pencil, Box, Settings } from "lucide-react"

const NAV_ITEMS = [
  { path: "/workspace", label: "工作区", icon: Pencil },
  { path: "/outline", label: "大纲", icon: BookOpen },
  { path: "/materials", label: "素材", icon: Box },
  { path: "/system", label: "系统", icon: Settings },
] as const

interface LayoutProps {
  children: ReactNode
}

export function Layout({ children }: LayoutProps) {
  const location = useLocation()
  const navigate = useNavigate()

  return (
    <div className="flex h-screen overflow-hidden">
      {/* 窄图标轨 */}
      <nav className="flex flex-col items-center gap-2 py-3 px-1.5 border-r bg-background w-14 shrink-0">
        {NAV_ITEMS.map((item) => {
          const active = location.pathname.startsWith(item.path)
          const Icon = item.icon
          return (
            <button
              key={item.path}
              onClick={() => navigate(item.path)}
              title={item.label}
              className={cn(
                "flex items-center justify-center w-10 h-10 rounded-md transition-colors",
                active
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent/50",
              )}
            >
              <Icon className="w-5 h-5" />
            </button>
          )
        })}
      </nav>

      {/* 主内容区 */}
      <main className="flex-1 overflow-auto">
        {children}
      </main>
    </div>
  )
}
