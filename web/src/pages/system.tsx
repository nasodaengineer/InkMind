import { useCallback, useEffect, useState } from "react"
import {
  Settings,
  Plus,
  Trash2,
  Save,
  Loader2,
  AlertTriangle,
  Check,
  X,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { api, type AppSettings, type ProviderItem, type ModelBindingItem } from "@/lib/api"
import { cn } from "@/lib/utils"

// ── Tab 定义 ──

type Tab = "providers" | "bindings" | "advanced"

const TABS: { key: Tab; label: string }[] = [
  { key: "providers", label: "Provider" },
  { key: "bindings", label: "模型路由" },
  { key: "advanced", label: "高级" },
]

// ── 空 Provider 模板 ──

function emptyProvider(name = ""): ProviderItem {
  return {
    name,
    protocol: "openai",
    base_url: "",
    api_key: null,
    models: [],
    max_concurrent: 3,
    max_keepalive: 10,
    max_calls_per_minute: 0,
  }
}

// ── 空 Binding 模板 ──

function emptyBinding(agent_role = ""): ModelBindingItem {
  return {
    agent_role,
    primary_model: "",
    fallback_models: [],
  }
}

// ── 将 AppSettings 转为 PUT 请求体（剥离 api_key 敏感字段） ──

function toSaveData(settings: AppSettings) {
  const providers: Record<string, Record<string, unknown>> = {}
  for (const [name, p] of Object.entries(settings.providers)) {
    providers[name] = {
      name: p.name,
      protocol: p.protocol,
      base_url: p.base_url,
      // api_key 仅在前端显示"已设置"标记，不传回后端存储
      models: p.models,
      max_concurrent: p.max_concurrent,
      max_keepalive: p.max_keepalive,
      max_calls_per_minute: p.max_calls_per_minute,
    }
  }
  return {
    providers,
    model_router: settings.model_router,
    retry: settings.retry,
    default_model: settings.default_model,
  }
}

export default function SystemPage() {
  const [activeTab, setActiveTab] = useState<Tab>("providers")
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [allModels, setAllModels] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<{ type: "success" | "error"; message: string } | null>(null)
  const [fakeMode, setFakeMode] = useState(false)

  // ── 加载数据 ──

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [s, pm] = await Promise.all([
        api.settings.get(),
        api.settings.getProviderModels(),
      ])
      setSettings(s)
      setAllModels(pm.all_models)
    } catch (e: unknown) {
      setToast({
        type: "error",
        message: `加载失败: ${e instanceof Error ? e.message : String(e)}`,
      })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    // 检测虚拟模式：通过尝试 fetch 一个已知不存在环境变量的 provider 来推断
    // 简单方式：检查 settings 中所有 provider 的 api_key 是否都为空
  }, [load])

  // ── Provider 操作 ──

  const updateProvider = (name: string, patch: Partial<ProviderItem>) => {
    if (!settings) return
    setSettings({
      ...settings,
      providers: {
        ...settings.providers,
        [name]: { ...settings.providers[name], ...patch },
      },
    })
  }

  const addProvider = () => {
    if (!settings) return
    const baseName = "new-provider"
    let name = baseName
    let i = 1
    while (settings.providers[name]) {
      name = `${baseName}-${i}`
      i++
    }
    setSettings({
      ...settings,
      providers: {
        ...settings.providers,
        [name]: { ...emptyProvider(name), models: [], api_key: null },
      },
    })
  }

  const removeProvider = (name: string) => {
    if (!settings) return
    const { [name]: _, ...rest } = settings.providers
    setSettings({ ...settings, providers: rest })
  }

  // ── Binding 操作 ──

  const updateBinding = (idx: number, patch: Partial<ModelBindingItem>) => {
    if (!settings) return
    const bindings = [...settings.model_router.bindings]
    bindings[idx] = { ...bindings[idx], ...patch }
    setSettings({
      ...settings,
      model_router: { bindings },
    })
  }

  const addBinding = () => {
    if (!settings) return
    const roles = ["planner", "writer", "editor", "memory-keeper", "designer"]
    const used = new Set(settings.model_router.bindings.map((b) => b.agent_role))
    const next = roles.find((r) => !used.has(r)) || `custom-${settings.model_router.bindings.length + 1}`
    setSettings({
      ...settings,
      model_router: {
        bindings: [...settings.model_router.bindings, emptyBinding(next)],
      },
    })
  }

  const removeBinding = (idx: number) => {
    if (!settings) return
    const bindings = settings.model_router.bindings.filter((_, i) => i !== idx)
    setSettings({ ...settings, model_router: { bindings } })
  }

  // ── 保存 ──

  const handleSave = async () => {
    if (!settings) return
    setSaving(true)
    setToast(null)
    try {
      const data = toSaveData(settings)
      await api.settings.save(data as any)
      setToast({ type: "success", message: "配置已保存，下一 run 生效" })
      // 刷新以获取最新
      await load()
    } catch (e: unknown) {
      setToast({
        type: "error",
        message: `保存失败: ${e instanceof Error ? e.message : String(e)}`,
      })
    } finally {
      setSaving(false)
    }
  }

  // ── 渲染 ──

  if (loading) {
    return (
      <div className="p-6 max-w-5xl mx-auto flex items-center justify-center py-20">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        <span className="ml-2 text-muted-foreground">加载设置中...</span>
      </div>
    )
  }

  if (!settings) {
    return (
      <div className="p-6 max-w-5xl mx-auto text-center py-20 text-muted-foreground">
        <AlertTriangle className="w-12 h-12 mx-auto mb-4 opacity-30" />
        <p>加载设置失败</p>
        <Button variant="outline" className="mt-4" onClick={load}>
          重试
        </Button>
      </div>
    )
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold">系统设置</h1>
        <div className="flex items-center gap-2">
          {fakeMode && (
            <span className="text-xs bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200 px-2 py-1 rounded flex items-center gap-1">
              <AlertTriangle className="w-3 h-3" />
              虚拟模式，配置不生效
            </span>
          )}
          <Button onClick={handleSave} disabled={saving}>
            {saving ? (
              <Loader2 className="w-4 h-4 animate-spin mr-1" />
            ) : (
              <Save className="w-4 h-4 mr-1" />
            )}
            保存
          </Button>
        </div>
      </div>

      {/* Toast */}
      {toast && (
        <div
          className={cn(
            "mb-4 px-4 py-3 rounded-md text-sm flex items-center gap-2",
            toast.type === "success"
              ? "bg-green-50 text-green-800 dark:bg-green-900/30 dark:text-green-300"
              : "bg-red-50 text-red-800 dark:bg-red-900/30 dark:text-red-300",
          )}
        >
          {toast.type === "success" ? (
            <Check className="w-4 h-4" />
          ) : (
            <X className="w-4 h-4" />
          )}
          {toast.message}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 mb-6 border-b">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={cn(
              "px-4 py-2 text-sm font-medium border-b-2 transition-colors",
              activeTab === tab.key
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Provider Tab ── */}
      {activeTab === "providers" && (
        <div className="space-y-4">
          {Object.entries(settings.providers).map(([name, p]) => (
            <ProviderCard
              key={name}
              provider={p}
              allModels={allModels}
              onChange={(patch) => updateProvider(name, patch)}
              onRemove={() => removeProvider(name)}
            />
          ))}
          <Button variant="outline" onClick={addProvider} className="w-full">
            <Plus className="w-4 h-4 mr-1" />
            添加 Provider
          </Button>
        </div>
      )}

      {/* ── Bindings Tab ── */}
      {activeTab === "bindings" && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground mb-2">
            将 Agent 角色映射到具体模型。模型名必须在 Provider 的 models 列表中。
          </p>
          {settings.model_router.bindings.map((b, idx) => (
            <BindingCard
              key={idx}
              binding={b}
              allModels={allModels}
              onChange={(patch) => updateBinding(idx, patch)}
              onRemove={() => removeBinding(idx)}
            />
          ))}
          <Button variant="outline" onClick={addBinding} className="w-full">
            <Plus className="w-4 h-4 mr-1" />
            添加绑定
          </Button>

          {/* 兜底默认模型 */}
          <div className="mt-6 pt-4 border-t">
            <label className="block text-sm font-medium mb-1">兜底默认模型</label>
            <select
              value={settings.default_model}
              onChange={(e) =>
                setSettings({ ...settings, default_model: e.target.value })
              }
              className="w-full max-w-xs rounded-md border border-input bg-background px-3 py-2 text-sm"
            >
              {allModels.length === 0 && (
                <option value="">(无可用模型)</option>
              )}
              {allModels.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground mt-1">
              当 Agent 角色无绑定或无对应 Provider 时的兜底模型
            </p>
          </div>
        </div>
      )}

      {/* ── Advanced Tab ── */}
      {activeTab === "advanced" && (
        <div className="space-y-6 max-w-lg">
          {/* 重试策略（只读） */}
          <div>
            <h3 className="text-sm font-medium mb-2">重试策略（只读）</h3>
            <div className="rounded-md border bg-muted/30 p-4 space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">最大重试次数</span>
                <span className="font-mono">{settings.retry.max_retries}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">重试间隔 (s)</span>
                <span className="font-mono">{settings.retry.base_delay_s}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">不重试状态码</span>
                <span className="font-mono">
                  {settings.retry.non_retryable_statuses.join(", ")}
                </span>
              </div>
            </div>
          </div>

          {/* 全局默认模型 */}
          <div>
            <h3 className="text-sm font-medium mb-2">默认模型</h3>
            <p className="text-sm text-muted-foreground">
              当前: <span className="font-mono">{settings.default_model}</span>
            </p>
          </div>
        </div>
      )}
    </div>
  )
}

// ── ProviderCard ──

function ProviderCard({
  provider,
  allModels,
  onChange,
  onRemove,
}: {
  provider: ProviderItem
  allModels: string[]
  onChange: (patch: Partial<ProviderItem>) => void
  onRemove: () => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [modelsInput, setModelsInput] = useState(provider.models.join(", "))

  const handleModelsBlur = () => {
    const list = modelsInput
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
    onChange({ models: list })
  }

  return (
    <div className="rounded-md border p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-sm font-medium hover:text-foreground/80"
          >
            {provider.name || "(未命名)"}
          </button>
          <span className="text-xs bg-muted px-2 py-0.5 rounded">
            {provider.protocol}
          </span>
          <span className="text-xs text-muted-foreground">
            {provider.models.length} 模型
          </span>
        </div>
        <div className="flex items-center gap-2">
          {provider.api_key ? (
            <span className="text-xs text-green-600 dark:text-green-400">
              已设置
            </span>
          ) : (
            <span className="text-xs text-muted-foreground">无 API Key</span>
          )}
          <button
            onClick={onRemove}
            className="text-muted-foreground hover:text-destructive transition-colors"
            title="删除 Provider"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {expanded && (
        <div className="mt-3 space-y-3 pt-3 border-t">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-muted-foreground mb-1">名称</label>
              <input
                type="text"
                value={provider.name}
                onChange={(e) => onChange({ name: e.target.value })}
                className="w-full rounded border border-input bg-background px-2 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">协议</label>
              <select
                value={provider.protocol}
                onChange={(e) => onChange({ protocol: e.target.value })}
                className="w-full rounded border border-input bg-background px-2 py-1.5 text-sm"
              >
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
                <option value="ollama">Ollama</option>
              </select>
            </div>
            <div className="col-span-2">
              <label className="block text-xs text-muted-foreground mb-1">Base URL</label>
              <input
                type="text"
                value={provider.base_url}
                onChange={(e) => onChange({ base_url: e.target.value })}
                className="w-full rounded border border-input bg-background px-2 py-1.5 text-sm font-mono"
                placeholder="https://api.deepseek.com"
              />
            </div>
            <div className="col-span-2">
              <label className="block text-xs text-muted-foreground mb-1">
                模型列表（逗号分隔）
              </label>
              <input
                type="text"
                value={modelsInput}
                onChange={(e) => setModelsInput(e.target.value)}
                onBlur={handleModelsBlur}
                className="w-full rounded border border-input bg-background px-2 py-1.5 text-sm font-mono"
                placeholder="deepseek-v4-pro, deepseek-v4-flash"
              />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">最大并发</label>
              <input
                type="number"
                min={1}
                value={provider.max_concurrent}
                onChange={(e) => onChange({ max_concurrent: Math.max(1, Number(e.target.value)) })}
                className="w-full rounded border border-input bg-background px-2 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">每分钟调用上限</label>
              <input
                type="number"
                min={0}
                value={provider.max_calls_per_minute}
                onChange={(e) => onChange({ max_calls_per_minute: Math.max(0, Number(e.target.value)) })}
                className="w-full rounded border border-input bg-background px-2 py-1.5 text-sm"
              />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── BindingCard ──

function BindingCard({
  binding,
  allModels,
  onChange,
  onRemove,
}: {
  binding: ModelBindingItem
  allModels: string[]
  onChange: (patch: Partial<ModelBindingItem>) => void
  onRemove: () => void
}) {
  const [fallbackInput, setFallbackInput] = useState(binding.fallback_models.join(", "))

  const handleFallbackBlur = () => {
    const list = fallbackInput
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
    onChange({ fallback_models: list })
  }

  return (
    <div className="rounded-md border p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-medium">{binding.agent_role}</span>
        <button
          onClick={onRemove}
          className="text-muted-foreground hover:text-destructive transition-colors"
          title="删除绑定"
        >
          <Trash2 className="w-4 h-4" />
        </button>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs text-muted-foreground mb-1">角色</label>
          <input
            type="text"
            value={binding.agent_role}
            onChange={(e) => onChange({ agent_role: e.target.value })}
            className="w-full rounded border border-input bg-background px-2 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs text-muted-foreground mb-1">主模型</label>
          <select
            value={binding.primary_model}
            onChange={(e) => onChange({ primary_model: e.target.value })}
            className="w-full rounded border border-input bg-background px-2 py-1.5 text-sm"
          >
            <option value="">(无)</option>
            {allModels.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
        <div className="col-span-2">
          <label className="block text-xs text-muted-foreground mb-1">
            降级模型（逗号分隔）
          </label>
          <input
            type="text"
            value={fallbackInput}
            onChange={(e) => setFallbackInput(e.target.value)}
            onBlur={handleFallbackBlur}
            className="w-full rounded border border-input bg-background px-2 py-1.5 text-sm font-mono"
            placeholder="deepseek-v4-flash"
          />
        </div>
      </div>
    </div>
  )
}
