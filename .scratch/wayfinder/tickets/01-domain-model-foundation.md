---
title: 领域模型基础设计
type: wayfinder:grilling
status: needs-triage
created: 2026-07-16
blocks: [02, 03, 04, 05, 06, 07]
labels: [domain-model, wayfinder]
---

## Question

InkMind 系统的核心领域模型应该包含哪些实体、值对象、关系和不变量？

具体需要讨论的决策点：

1. **核心实体层次**：Novel → Volume（卷）→ Part（部）→ Chapter（章）→ Scene（场景）→ Paragraph（段）？还是 Novel → Chapter 的扁平结构？超长篇（100万+字）需要怎样的层次化组织？

2. **角色模型**：Character 作为一个独立实体应该包含哪些属性（姓名、年龄、外貌、性格、背景、动机、角色弧）？如何建模角色关系图谱？

3. **世界观模型**：World 实体应该包含哪些维度（地理、历史、文化、魔法/科技体系、种族/组织）？世界观与情节如何关联？

4. **情节结构**：PlotArc / PlotLine 实体如何设计？支持多线叙事、伏笔、Callback？

5. **版本与状态**：每章是否需要 Draft / Editing / Final 状态？是否需要 Git-like 的版本历史？

6. **写作元数据**：写作时间、字数统计、写作 Session、AI 生成 vs 人工写作的标记？

请输出 Pydantic / SQLModel 风格的类图（或等效的 Python 伪代码），标注关键关系和约束。

## 参考

- AGENTS.md 中的 @domain-model Skill 定义
- ainovel-cli 的 Phase/Flow 状态机设计
- MuMuAINovel 的角色与伏笔管理
- Openwrite_skill 的单一真源(src/data隔离)
