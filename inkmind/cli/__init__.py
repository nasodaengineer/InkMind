"""InkMind CLI — 用户命令行交互层。

指令架构:
  inkmind init              → 初始化新小说
  inkmind write <title>     → 启动 1 × Writer 写作
  inkmind plan [N]          → 启动 1 × Planner 规划前 N 章
  inkmind review            → 对当前最新章节启动 1 × Editor 评审
  inkmind next              → 1 轮完整协作
  inkmind status            → 显示小说/章节当前状态
  inkmind shell             → 进入交互式 REPL
  inkmind commit            → 导出 JSON 快照
  inkmind restore <file>    → 从快照恢复
  inkmind version           → 版本信息
"""
