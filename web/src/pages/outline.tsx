/* 三级书脊树 · 大纲规划视图（Issue #42 AI 大纲规划） */

import React, { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  api,
  OutlineSpine,
  VolumeItem,
  ChapterOutlineItem,
  RunItem,
  connectRunStream,
} from "../lib/api";

// ── 类型 ──

type ToastKind = "info" | "error";

interface Toast {
  id: number;
  message: string;
  kind: ToastKind;
}

interface ConfirmDialogState {
  open: boolean;
  title: string;
  message: string;
  onConfirm: () => void;
  onCancel?: () => void;
}

interface RunPanelState {
  visible: boolean;
  runId: string | null;
  phase: string;
  status: string;
  error: string | null;
}

// ── Toast Hook ──

let toastId = 0;

// ── 组件 ──

export default function OutlinePage() {
  const { novelId } = useParams<{ novelId: string }>();
  const queryClient = useQueryClient();
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [collapsedVolumes, setCollapsedVolumes] = useState<Set<number>>(new Set());
  const [editingSpine, setEditingSpine] = useState(false);
  const [draftSpine, setDraftSpine] = useState<OutlineSpine | null>(null);

  // Issue #42: AI 规划状态
  const [confirmDialog, setConfirmDialog] = useState<ConfirmDialogState>({
    open: false,
    title: "",
    message: "",
    onConfirm: () => {},
  });
  const [runPanel, setRunPanel] = useState<RunPanelState>({
    visible: false,
    runId: null,
    phase: "",
    status: "",
    error: null,
  });
  const abortRef = useRef<(() => void) | null>(null);

  const toast = useCallback((message: string, kind: ToastKind = "info") => {
    const id = ++toastId;
    setToasts((prev) => [...prev, { id, message, kind }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 3000);
  }, []);

  // ── AI 规划辅助函数 (Issue #42) ──

  /** 启动 AI 规划 run，连接到 SSE 流 */
  const startAI = useCallback(
    async (
      plannerCall: Promise<RunItem>,
      _label: string,
    ) => {
      try {
        const run = await plannerCall;
        setRunPanel({
          visible: true,
          runId: run.id,
          phase: "starting",
          status: "running",
          error: null,
        });

        const abort = connectRunStream(novelId!, run.id, {
          onPhase: (phase) => {
            setRunPanel((prev) => ({ ...prev, phase }));
          },
          onResult: (result: any) => {
            if (result?.level === "spine") {
              // 总纲生成完毕，刷新 spine
              queryClient.invalidateQueries({ queryKey: ["spine", novelId] });
              toast("总纲起草完成");
            } else if (result?.level === "split_volumes") {
              queryClient.invalidateQueries({ queryKey: ["volumes", novelId] });
              toast(`拆卷完成，共 ${result.data?.count} 卷`);
            } else if (result?.level === "volume") {
              queryClient.invalidateQueries({ queryKey: ["volumes", novelId] });
              toast("卷纲已更新");
            } else if (result?.level === "plan_chapters") {
              queryClient.invalidateQueries({ queryKey: ["volumes", novelId] });
              toast(`排章完成，共 ${result.data?.count} 章`);
            }
          },
          onDone: (status) => {
            setRunPanel((prev) => ({
              ...prev,
              status,
              phase: status === "completed" ? "complete" : prev.phase,
            }));
            // 刷新数据
            queryClient.invalidateQueries({ queryKey: ["spine", novelId] });
            queryClient.invalidateQueries({ queryKey: ["volumes", novelId] });
            // 3 秒后隐藏面板
            setTimeout(() => {
              setRunPanel((prev) => ({ ...prev, visible: false }));
            }, 3000);
          },
          onError: (message) => {
            setRunPanel((prev) => ({
              ...prev,
              status: "failed",
              error: message,
            }));
            toast(`AI 规划失败: ${message}`, "error");
          },
        });
        abortRef.current = abort;
      } catch (err: any) {
        // 处理 confirm_overwrite 等 400 错误
        const msg = String(err?.message || err);
        if (msg.includes("confirm_overwrite")) {
          toast("需确认覆盖已存在内容", "error");
        } else if (msg.includes("spine_required")) {
          toast("请先起草总纲", "error");
        } else if (msg.includes("zero_volume_409")) {
          toast("已有卷存在，无法批量拆卷", "error");
        } else {
          toast(`启动失败: ${msg}`, "error");
        }
      }
    },
    [novelId, queryClient, toast],
  );

  /** 取消当前 AI 规划 run */
  const cancelAI = useCallback(async () => {
    if (abortRef.current) {
      abortRef.current();
      abortRef.current = null;
    }
    if (runPanel.runId) {
      try {
        await api.runs.cancel(novelId!, runPanel.runId);
      } catch {
        // 忽略取消失败
      }
    }
    setRunPanel((prev) => ({
      ...prev,
      visible: false,
    }));
  }, [novelId, runPanel.runId]);

  // ── 查询 ──

  const { data: spine, isLoading: spineLoading } = useQuery({
    queryKey: ["spine", novelId],
    queryFn: () => api.spine.get(novelId!),
    enabled: !!novelId,
  });

  const { data: volumes = [], isLoading: volsLoading } = useQuery({
    queryKey: ["volumes", novelId],
    queryFn: () => api.volumes.list(novelId!),
    enabled: !!novelId,
  });

  // 初始化 draftSpine
  useEffect(() => {
    if (spine && !draftSpine) {
      setDraftSpine(spine);
    }
  }, [spine]);

  // ── 总纲编辑 ──

  const saveSpineMutation = useMutation({
    mutationFn: (data: Partial<OutlineSpine>) => api.spine.update(novelId!, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["spine", novelId] });
      setEditingSpine(false);
      toast("总纲已保存");
    },
    onError: (err) => toast(`保存失败: ${err}`, "error"),
  });

  const handleSpineSave = () => {
    if (!draftSpine) return;
    saveSpineMutation.mutate(draftSpine);
  };

  // ── 卷操作 ──

  const [showCreateVolume, setShowCreateVolume] = useState(false);
  const [newVolTitle, setNewVolTitle] = useState("");
  const [newVolSize, setNewVolSize] = useState(10);

  const createVolMutation = useMutation({
    mutationFn: () =>
      api.volumes.create(novelId!, {
        title: newVolTitle || `第 ${volumes.length + 1} 卷`,
        planned_size: newVolSize,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["volumes", novelId] });
      setShowCreateVolume(false);
      setNewVolTitle("");
      setNewVolSize(10);
      toast("卷已创建");
    },
    onError: (err) => toast(`创建失败: ${err}`, "error"),
  });

  // ── 计算卷的派生区间 ──

  function getVolumeRange(v: VolumeItem, _all: VolumeItem[]): string {
    return `${v.start_index}–${v.end_index} 章`;
  }

  if (!novelId) {
    return <div className="p-8 text-gray-500">缺少小说 ID</div>;
  }

  if (spineLoading || volsLoading) {
    return <div className="p-8 text-gray-500">加载中...</div>;
  }

  return (
    <div className="h-screen flex flex-col">
      {/* 顶栏 */}
      <header className="flex-none border-b border-gray-200 bg-amber-50 px-4 py-2 text-xs text-amber-800 flex gap-4 items-baseline">
        <b className="tracking-wider">INKMIND</b>
        <span>大纲规划视图</span>
        <span className="text-gray-400">
          {volumes.length} 卷 ·{" "}
          {volumes.reduce((s, v) => s + v.chapter_count, 0)} 章
        </span>
      </header>

      {/* 主体 */}
      <div className="flex-1 min-h-0 flex">
        {/* 书脊树 */}
        <div className="flex-1 min-w-0 overflow-auto p-4 space-y-4">
          {/* ═══ 总纲卡片 ═══ */}
          {draftSpine && (
            <div className="border-2 border-dashed border-gray-300 rounded-lg bg-gray-50 p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-xs font-semibold tracking-wider text-gray-500 uppercase">
                  总纲（书脊）
                </h3>
                <div className="flex gap-2">
                  {!editingSpine ? (
                    <>
                      <button
                        className="text-xs px-3 py-1 border border-gray-400 rounded hover:bg-gray-100"
                        onClick={() => setEditingSpine(true)}
                      >
                        ✎ 编辑
                      </button>
                      <button
                        className="text-xs px-3 py-1 border border-gray-400 rounded hover:bg-gray-100 text-gray-500"
                        onClick={() => {
                          const hasContent = draftSpine && (
                            draftSpine.main_line ||
                            draftSpine.core_conflict ||
                            draftSpine.ending ||
                            draftSpine.selling_points ||
                            draftSpine.world_background ||
                            draftSpine.golden_finger
                          );
                          if (hasContent) {
                            setConfirmDialog({
                              open: true,
                              title: "确认覆盖总纲",
                              message: "总纲已有内容，AI 起草将覆盖现有内容。确认继续？",
                              onConfirm: () => {
                                setConfirmDialog((prev) => ({ ...prev, open: false }));
                                startAI(
                                  api.planner.draftSpine(novelId!, {
                                    confirm_overwrite: true,
                                  }),
                                  "起草总纲",
                                );
                              },
                            });
                          } else {
                            startAI(
                              api.planner.draftSpine(novelId!),
                              "起草总纲",
                            );
                          }
                        }}
                      >
                        ✦ AI 起草
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        className="text-xs px-3 py-1 bg-gray-800 text-white rounded hover:bg-gray-700"
                        onClick={handleSpineSave}
                      >
                        保存
                      </button>
                      <button
                        className="text-xs px-3 py-1 border border-gray-400 rounded hover:bg-gray-100"
                        onClick={() => {
                          setEditingSpine(false);
                          setDraftSpine(spine!);
                        }}
                      >
                        取消
                      </button>
                    </>
                  )}
                </div>
              </div>

              {/* 六字段 */}
              <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
                <SpineField
                  label="主线"
                  value={draftSpine.main_line}
                  editing={editingSpine}
                  onChange={(v) => setDraftSpine({ ...draftSpine, main_line: v })}
                />
                <SpineField
                  label="核心矛盾"
                  value={draftSpine.core_conflict}
                  editing={editingSpine}
                  onChange={(v) =>
                    setDraftSpine({ ...draftSpine, core_conflict: v })
                  }
                />
                <SpineField
                  label="结局"
                  value={draftSpine.ending}
                  editing={editingSpine}
                  onChange={(v) => setDraftSpine({ ...draftSpine, ending: v })}
                />
                <SpineField
                  label="卖点"
                  value={draftSpine.selling_points}
                  editing={editingSpine}
                  onChange={(v) =>
                    setDraftSpine({ ...draftSpine, selling_points: v })
                  }
                />
                <SpineField
                  label="世界观背景"
                  value={draftSpine.world_background}
                  editing={editingSpine}
                  onChange={(v) =>
                    setDraftSpine({ ...draftSpine, world_background: v })
                  }
                />
                <SpineField
                  label="金手指"
                  value={draftSpine.golden_finger}
                  editing={editingSpine}
                  onChange={(v) =>
                    setDraftSpine({ ...draftSpine, golden_finger: v })
                  }
                />
              </div>
            </div>
          )}

          {/* ═══ 卷列表 ═══ */}
          {volumes.map((vol) => (
            <VolumeNode
              key={vol.volume_index}
              volume={vol}
              novelId={novelId}
              collapsed={collapsedVolumes.has(vol.volume_index)}
              onToggle={() => {
                setCollapsedVolumes((prev) => {
                  const next = new Set(prev);
                  if (next.has(vol.volume_index)) {
                    next.delete(vol.volume_index);
                  } else {
                    next.add(vol.volume_index);
                  }
                  return next;
                });
              }}
              rangeLabel={getVolumeRange(vol, volumes)}
              toast={toast}
              startAI={startAI}
              setConfirmDialog={setConfirmDialog}
            />
          ))}

          {/* ═══ AI 拆卷 + 添加卷 ═══ */}
          <div className="flex gap-2">
            <button
              className="flex-1 py-3 border-2 border-dashed border-gray-300 rounded-lg text-sm text-indigo-400 hover:text-indigo-600 hover:border-indigo-300 transition-colors"
              onClick={() => {
                if (volumes.length > 0) {
                  toast("已有卷存在，拆卷仅支持零卷冷启动", "error");
                  return;
                }
                setConfirmDialog({
                  open: true,
                  title: "AI 全书拆卷",
                  message: `AI 将根据总纲将全书拆分为卷。当前零卷，确认开始拆卷？`,
                  onConfirm: () => {
                    setConfirmDialog((prev) => ({ ...prev, open: false }));
                    startAI(
                      api.planner.splitVolumes(novelId!, 5),
                      "全书拆卷",
                    );
                  },
                });
              }}
            >
              ✦ AI 拆卷（2-20卷）
            </button>
            <button
              className="flex-1 py-3 border-2 border-dashed border-gray-300 rounded-lg text-sm text-gray-400 hover:text-gray-600 hover:border-gray-400 transition-colors"
              onClick={() => setShowCreateVolume(true)}
            >
              ＋ 添加卷
            </button>
          </div>
          {showCreateVolume && (
            <div className="border-2 border-dashed border-gray-300 rounded-lg p-4 bg-gray-50">
              <div className="flex items-center gap-3">
                <input
                  className="flex-1 border border-gray-300 rounded px-3 py-1.5 text-sm"
                  placeholder="卷标题（可选）"
                  value={newVolTitle}
                  onChange={(e) => setNewVolTitle(e.target.value)}
                />
                <label className="text-xs text-gray-500">
                  预计
                  <input
                    type="number"
                    className="w-16 border border-gray-300 rounded px-2 py-1.5 text-sm ml-1"
                    min={1}
                    max={200}
                    value={newVolSize}
                    onChange={(e) =>
                      setNewVolSize(Math.max(1, Number(e.target.value)))
                    }
                  />
                  章
                </label>
                <button
                  className="px-3 py-1.5 bg-gray-800 text-white text-sm rounded hover:bg-gray-700"
                  onClick={() => createVolMutation.mutate()}
                >
                  创建
                </button>
                <button
                  className="px-3 py-1.5 border border-gray-400 text-sm rounded hover:bg-gray-100"
                  onClick={() => setShowCreateVolume(false)}
                >
                  取消
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ═══ Run 面板 (Issue #42) ═══ */}
      {runPanel.visible && (
        <RunPanel
          phase={runPanel.phase}
          status={runPanel.status}
          error={runPanel.error}
          onCancel={cancelAI}
        />
      )}

      {/* ═══ 确认弹窗 (Issue #42) ═══ */}
      {confirmDialog.open && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-[60]">
          <div className="bg-white rounded-xl shadow-2xl p-6 max-w-sm w-full mx-4">
            <h3 className="text-sm font-semibold mb-2">{confirmDialog.title}</h3>
            <p className="text-sm text-gray-600 mb-4">{confirmDialog.message}</p>
            <div className="flex gap-2 justify-end">
              <button
                className="px-3 py-1.5 border border-gray-300 text-sm rounded hover:bg-gray-100"
                onClick={() => {
                  confirmDialog.onCancel?.();
                  setConfirmDialog((prev) => ({ ...prev, open: false }));
                }}
              >
                取消
              </button>
              <button
                className="px-3 py-1.5 bg-red-600 text-white text-sm rounded hover:bg-red-500"
                onClick={confirmDialog.onConfirm}
              >
                确认覆盖
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Toast 容器 */}
      <div className="fixed left-4 bottom-4 flex flex-col gap-2 z-50 max-w-xs">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`px-3 py-2 rounded-lg text-sm shadow-lg animate-[tin_0.2s_ease-out] ${
              t.kind === "error"
                ? "bg-red-600 text-white"
                : "bg-gray-800 text-white"
            }`}
          >
            {t.message}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── 总纲字段组件 ──

function SpineField({
  label,
  value,
  editing,
  onChange,
}: {
  label: string;
  value: string;
  editing: boolean;
  onChange: (v: string) => void;
}) {
  if (editing) {
    return (
      <div>
        <label className="block text-xs text-gray-500 font-medium mb-0.5">
          {label}
        </label>
        <textarea
          className="w-full border border-gray-300 rounded px-2 py-1 text-sm resize-y"
          rows={2}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
    );
  }
  return (
    <div className="cursor-text rounded px-1 py-0.5 hover:bg-gray-100 hover:outline-dashed hover:outline-1 hover:outline-gray-300">
      <span className="text-xs text-gray-500 font-medium">{label}</span>
      <p className="text-sm whitespace-pre-wrap break-words">
        {value || <span className="text-gray-300 italic">空</span>}
      </p>
    </div>
  );
}

// ── 卷节点 ──

function VolumeNode({
  volume,
  novelId,
  collapsed,
  onToggle,
  rangeLabel,
  toast,
  startAI,
  setConfirmDialog,
}: {
  volume: VolumeItem;
  novelId: string;
  collapsed: boolean;
  onToggle: () => void;
  rangeLabel: string;
  toast: (msg: string, kind?: ToastKind) => void;
  startAI: (call: Promise<RunItem>, label: string) => Promise<void>;
  setConfirmDialog: React.Dispatch<React.SetStateAction<ConfirmDialogState>>;
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<VolumeItem>(volume);
  const [showDelete, setShowDelete] = useState(false);
  const [showPlan, setShowPlan] = useState(false);
  const [planCount, setPlanCount] = useState(10);

  // 查询卷内章节
  const { data: spineData } = useQuery({
    queryKey: ["volume-spines", novelId, volume.volume_index],
    queryFn: () => api.volumes.spines(novelId, volume.volume_index),
    enabled: !collapsed,
  });

  const chapters = spineData?.chapters ?? [];

  // 更新卷
  const updateVolMutation = useMutation({
    mutationFn: (data: Partial<VolumeItem>) =>
      api.volumes.update(novelId, volume.volume_index, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["volumes", novelId] });
      setEditing(false);
      toast("卷已更新");
    },
    onError: (err) => toast(`更新失败: ${err}`, "error"),
  });

  const handleSave = () => {
    updateVolMutation.mutate({
      title: draft.title,
      stage_goal: draft.stage_goal,
      main_line: draft.main_line,
      side_line: draft.side_line,
      volume_cliffhanger: draft.volume_cliffhanger,
      planned_size: draft.planned_size,
    });
  };

  // 删除卷
  const deleteVolMutation = useMutation({
    mutationFn: () => api.volumes.delete(novelId, volume.volume_index),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["volumes", novelId] });
      setShowDelete(false);
      toast("卷已删除");
    },
    onError: (err) => {
      const msg = String(err);
      if (msg.includes("409")) {
        toast("卷非空，无法删除", "error");
      } else {
        toast(`删除失败: ${err}`, "error");
      }
    },
  });

  // 章节 PATCH
  const patchChapterMutation = useMutation({
    mutationFn: ({
      idx,
      data,
    }: {
      idx: number;
      data: Partial<ChapterOutlineItem>;
    }) => api.chapters.patchOutline(novelId, idx, data),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["volume-spines", novelId, volume.volume_index],
      });
    },
    onError: (err) => toast(`更新章纲失败: ${err}`, "error"),
  });

  return (
    <div className="border border-gray-200 rounded-lg bg-white shadow-sm">
      {/* 卷头 */}
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-gray-50 select-none"
        onClick={onToggle}
      >
        <span className="text-gray-400 text-sm transition-transform duration-200">
          {collapsed ? "▶" : "▼"}
        </span>
        <span className="text-xs font-bold text-gray-500 bg-gray-100 px-2 py-0.5 rounded">
          V{volume.volume_index}
        </span>
        <span className="font-medium text-sm">{volume.title}</span>
        <span className="text-xs text-gray-400">{rangeLabel}</span>
        <span className="text-xs text-gray-400 ml-auto">
          {volume.chapter_count}/{volume.planned_size} 章
        </span>
      </div>

      {/* 卷纲编辑区 */}
      {editing && (
        <div className="px-4 pb-3 space-y-2">
          <EditField label="标题" value={draft.title} onChange={(v) => setDraft({ ...draft, title: v })} />
          <EditField label="阶段目标" value={draft.stage_goal} onChange={(v) => setDraft({ ...draft, stage_goal: v })} />
          <EditField label="主线" value={draft.main_line} onChange={(v) => setDraft({ ...draft, main_line: v })} />
          <EditField label="支线" value={draft.side_line} onChange={(v) => setDraft({ ...draft, side_line: v })} />
          <EditField label="卷末悬念" value={draft.volume_cliffhanger} onChange={(v) => setDraft({ ...draft, volume_cliffhanger: v })} />
          <div className="flex items-center gap-2">
            <label className="text-xs text-gray-500 w-16">预计章数</label>
            <input
              type="number"
              className="w-20 border border-gray-300 rounded px-2 py-1 text-sm"
              min={1}
              value={draft.planned_size}
              onChange={(e) =>
                setDraft({ ...draft, planned_size: Math.max(1, Number(e.target.value)) })
              }
            />
            <span className="text-xs text-gray-400">（已有 {volume.chapter_count} 章）</span>
          </div>
        </div>
      )}

      {/* 操作栏 */}
      <div className="px-4 pb-2 flex gap-2 flex-wrap">
        {!editing ? (
          <button
            className="text-xs px-2 py-1 border border-gray-300 rounded hover:bg-gray-100"
            onClick={(e) => {
              e.stopPropagation();
              setDraft(volume);
              setEditing(true);
            }}
          >
            ✎ 编辑卷纲
          </button>
        ) : (
          <>
            <button
              className="text-xs px-2 py-1 bg-gray-800 text-white rounded hover:bg-gray-700"
              onClick={(e) => {
                e.stopPropagation();
                handleSave();
              }}
            >
              保存
            </button>
            <button
              className="text-xs px-2 py-1 border border-gray-300 rounded hover:bg-gray-100"
              onClick={(e) => {
                e.stopPropagation();
                setEditing(false);
              }}
            >
              取消
            </button>
          </>
        )}
        <button
          className="text-xs px-2 py-1 border border-gray-300 rounded hover:bg-gray-100 text-gray-500"
          onClick={(e) => {
            e.stopPropagation();
            // AI 起草卷纲
            const hasContent = volume.stage_goal || volume.main_line ||
              volume.side_line || volume.volume_cliffhanger;
            if (hasContent) {
              setConfirmDialog({
                open: true,
                title: "确认覆盖卷纲",
                message: `「${volume.title}」已有内容，确认覆盖吗？`,
                onConfirm: () => {
                  setConfirmDialog((prev) => ({ ...prev, open: false }));
                  startAI(
                    api.planner.draftVolume(novelId, volume.id, {
                      confirm_overwrite: true,
                    }),
                    "填补卷纲",
                  );
                },
              });
            } else {
              startAI(
                api.planner.draftVolume(novelId, volume.id),
                "填补卷纲",
              );
            }
          }}
        >
          ✦ AI 起草
        </button>
        {showDelete ? (
          <span className="flex gap-2 items-center">
            <span className="text-xs text-red-500">确认删除？</span>
            <button
              className="text-xs px-2 py-1 border border-red-400 text-red-600 rounded hover:bg-red-50"
              onClick={(e) => {
                e.stopPropagation();
                deleteVolMutation.mutate();
              }}
            >
              确认
            </button>
            <button
              className="text-xs px-2 py-1 border border-gray-300 rounded hover:bg-gray-100"
              onClick={(e) => {
                e.stopPropagation();
                setShowDelete(false);
              }}
            >
              取消
            </button>
          </span>
        ) : (
          <button
            className="text-xs px-2 py-1 border border-gray-300 rounded hover:bg-gray-100 text-red-400"
            onClick={(e) => {
              e.stopPropagation();
              setShowDelete(true);
            }}
          >
            删除卷
          </button>
        )}
      </div>

      {/* 章节点列表 */}
      {!collapsed && (
        <div className="border-t border-gray-100">
          {chapters.map((ch) => (
            <ChapterRow
              key={ch.chapter_index}
              chapter={ch}
              onPatch={(data) =>
                patchChapterMutation.mutate({ idx: ch.chapter_index, data })
              }
            />
          ))}

          {/* Ghost 行：批量规划 */}
          {!showPlan ? (
            <button
              className="w-full py-2.5 text-sm text-gray-400 italic hover:bg-gray-50 hover:text-gray-600 transition-colors border-t border-gray-50"
              onClick={() => setShowPlan(true)}
            >
              ＋ 批量规划 5–50 章
            </button>
          ) : (
            <div className="px-4 py-2 bg-gray-50 border-t border-gray-100 flex items-center gap-3">
              <label className="text-xs text-gray-500">规划</label>
              <input
                type="number"
                className="w-20 border border-gray-300 rounded px-2 py-1 text-sm"
                min={5}
                max={50}
                value={planCount}
                onChange={(e) =>
                  setPlanCount(Math.min(50, Math.max(5, Number(e.target.value))))
                }
              />
              <span className="text-xs text-gray-500">章</span>
              <button
                className="px-3 py-1 bg-gray-800 text-white text-xs rounded hover:bg-gray-700"
                onClick={() => {
                  setShowPlan(false);
                  startAI(
                    api.planner.planChapters(novelId, volume.id, planCount),
                    `批量排章 (${planCount} 章)`,
                  );
                }}
              >
                开始规划
              </button>
              <button
                className="px-3 py-1 border border-gray-300 text-xs rounded hover:bg-gray-100"
                onClick={() => setShowPlan(false)}
              >
                取消
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── 章节点行 ──

function ChapterRow({
  chapter,
  onPatch,
}: {
  chapter: ChapterOutlineItem;
  onPatch: (data: Partial<ChapterOutlineItem>) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(chapter.title);
  const [draftSummary, setDraftSummary] = useState(chapter.summary);
  const [draftRhythm, setDraftRhythm] = useState(chapter.rhythm_marker);

  const statusDot =
    chapter.status === "approved" || chapter.status === "finished"
      ? "●"
      : chapter.status === "active" || chapter.status === "draft_ready"
        ? "●"
        : "●";

  const statusClass =
    chapter.status === "approved" || chapter.status === "finished"
      ? "dot-finished"
      : chapter.status === "active" || chapter.status === "draft_ready"
        ? "dot-active"
        : "dot-planned";

  const rhythmBadge = chapter.rhythm_marker === "big_climax"
    ? "★"
    : chapter.rhythm_marker === "climax"
      ? "▲"
      : null;

  const rhythmClass = chapter.rhythm_marker === "big_climax"
    ? "beat-star"
    : "beat";

  const statusBadge = (() => {
    switch (chapter.status) {
      case "approved":
      case "finished":
        return <span className="sbadge sb-done">定稿</span>;
      case "draft_ready":
        return <span className="sbadge sb-partial">草稿</span>;
      case "planned":
        return <span className="sbadge sb-planned">已规划</span>;
      default:
        return <span className="sbadge sb-planned">{chapter.status}</span>;
    }
  })();

  const handleSave = () => {
    onPatch({
      title: draftTitle,
      summary: draftSummary,
      rhythm_marker: draftRhythm,
    });
    setEditing(false);
  };

  if (editing) {
    return (
      <div className="px-4 py-2 border-t border-gray-50 bg-blue-50/30">
        <div className="flex items-center gap-3 mb-2">
          <span className="text-xs text-gray-400 w-6">#{chapter.chapter_index}</span>
          <input
            className="flex-1 border border-gray-300 rounded px-2 py-1 text-sm"
            value={draftTitle}
            onChange={(e) => setDraftTitle(e.target.value)}
          />
          <select
            className="border border-gray-300 rounded px-2 py-1 text-xs"
            value={draftRhythm ?? ""}
            onChange={(e) => setDraftRhythm(e.target.value || null)}
          >
            <option value="">无节奏</option>
            <option value="climax">▲ 小高潮</option>
            <option value="big_climax">★ 大高潮</option>
          </select>
        </div>
        <textarea
          className="w-full border border-gray-300 rounded px-2 py-1 text-sm mb-2 resize-y"
          rows={2}
          placeholder="摘要"
          value={draftSummary}
          onChange={(e) => setDraftSummary(e.target.value)}
        />
        <div className="flex gap-2">
          <button
            className="px-2 py-1 bg-gray-800 text-white text-xs rounded"
            onClick={handleSave}
          >
            保存
          </button>
          <button
            className="px-2 py-1 border border-gray-300 text-xs rounded"
            onClick={() => setEditing(false)}
          >
            取消
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      className="flex items-center gap-3 px-4 py-2 border-t border-gray-50 hover:bg-gray-50/50 group cursor-pointer text-sm"
      onClick={() => {
        setDraftTitle(chapter.title);
        setDraftSummary(chapter.summary);
        setDraftRhythm(chapter.rhythm_marker);
        setEditing(true);
      }}
    >
      <span className={`text-xs font-mono ${statusClass}`}>{statusDot}</span>
      <span className="text-xs text-gray-400 w-8">#{chapter.chapter_index}</span>
      {rhythmBadge && (
        <span className={`text-xs font-bold ${rhythmClass}`}>{rhythmBadge}</span>
      )}
      {(chapter.foreshadowing_count ?? 0) > 0 && (
        <span className="text-xs px-1 py-0.5 rounded bg-purple-100 text-purple-600 font-medium" title="待回收伏笔">
          伏{chapter.foreshadowing_count}
        </span>
      )}
      <span className="flex-1 truncate">{chapter.title}</span>
      <span className="text-xs text-gray-400 truncate max-w-40 hidden sm:block">
        {chapter.summary}
      </span>
      {chapter.pov && (
        <span className="text-xs text-gray-400 hidden md:block">【{chapter.pov}】</span>
      )}
      <span className="flex-none">{statusBadge}</span>
      <span className="text-xs text-gray-300 opacity-0 group-hover:opacity-100 transition-opacity">
        ✎
      </span>
    </div>
  );
}

// ── 编辑字段组件（卷纲用） ──

function EditField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex items-start gap-2">
      <label className="text-xs text-gray-500 w-16 pt-1.5">{label}</label>
      <textarea
        className="flex-1 border border-gray-300 rounded px-2 py-1 text-sm resize-y"
        rows={2}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

// ── RunPanel 组件 (Issue #42) ──

function RunPanel({
  phase,
  status,
  error,
  onCancel,
}: {
  phase: string;
  status: string;
  error: string | null;
  onCancel: () => void;
}) {
  const phaseLabel: Record<string, string> = {
    starting: "准备中...",
    planning: "AI 规划中...",
    writing: "写作中...",
    reviewing: "评审中...",
    revising: "修订中...",
    complete: "完成",
  };

  const isRunning = status === "running";
  const isDone = status === "completed" || status === "cancelled" || status === "failed";

  return (
    <div className="fixed right-4 top-20 z-50 w-72 bg-white rounded-xl shadow-2xl border border-gray-200 overflow-hidden">
      <div className="px-4 py-3 bg-gray-50 border-b border-gray-200 flex items-center justify-between">
        <span className="text-xs font-semibold text-gray-600 uppercase tracking-wider">
          AI 规划进度
        </span>
        {isRunning && (
          <button
            className="text-xs px-2 py-1 border border-red-400 text-red-500 rounded hover:bg-red-50"
            onClick={onCancel}
          >
            取消
          </button>
        )}
      </div>
      <div className="px-4 py-3 space-y-2">
        {/* 阶段指示 */}
        <div className="flex items-center gap-2">
          {isRunning ? (
            <span className="w-2 h-2 bg-amber-500 rounded-full animate-pulse" />
          ) : isDone ? (
            <span className="w-2 h-2 bg-green-500 rounded-full" />
          ) : (
            <span className="w-2 h-2 bg-gray-300 rounded-full" />
          )}
          <span className="text-sm text-gray-700">
            {phaseLabel[phase] || phase}
          </span>
        </div>

        {/* 进度条 */}
        {isRunning && (
          <div className="w-full bg-gray-100 rounded-full h-1.5">
            <div
              className="bg-amber-500 h-1.5 rounded-full animate-pulse"
              style={{ width: phase === "planning" ? "40%" : phase === "writing" ? "60%" : "80%" }}
            />
          </div>
        )}

        {/* 错误信息 */}
        {error && (
          <div className="text-xs text-red-600 bg-red-50 rounded px-2 py-1">
            {error}
          </div>
        )}
      </div>
    </div>
  );
}
