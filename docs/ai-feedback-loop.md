# 人工反馈 — AI 调整（核心交互）

## 问题

仅有「手动修改」和「整段重新生成」时：

- 手动改：难保持整体连贯；
- 重生成：近似重试，无法按用户具体意见定向优化。

## 目标

将 **文字反馈 → 基于原结果的定向修订** 作为全流程一等能力，覆盖：

| 阶段 | 用户可反馈什么 |
|------|----------------|
| 文档解析 | 识别错误、表格错位、漏段 |
| 招标分析 | 概述/评分/废标点偏差 |
| **项目生成要求** | 字数、章节侧重点、格式强制项（注入后续任务） |
| 目录大纲 | 层级、重点、对齐招标目录、篇幅 |
| 全局事实 | 增删改冲突事实 |
| 正文（按章） | 扩写、语气、补指标、去套话 |

## 三种干预方式（并列）

1. **手动编辑**：用户直接改文本/节点  
2. **按反馈调整（推荐）**：`原产物 + 用户意见 +（可选）保留结构` → AI 修订  
3. **整段重生成**：不携带本次意见的全量重试（次要按钮）

## 前端实现

- 类型：`frontend/src/shared/types/aiFeedback.ts`
- 组件：`AiFeedbackPanel`（各步复用）
- 项目约束：`ProjectGuidanceCard` + `useProjectGuidance`（localStorage 演示，可换 API）
- 接入：`TechnicalPlanWorkspace` 解析 / 分析 / 大纲 / 事实 / 正文

## 后端建议（后续）

```http
POST /api/projects/{projectId}/artifacts/{artifactId}/revise
{
  "stage": "outline",
  "message": "一级目录对齐招标文件",
  "preserve_structure": true,
  "base_version": 3,
  "guidance": { "target_word_count": 80000, "chapter_focus": "..." }
}
```

- 创建异步 task，SSE 推送进度  
- Prompt 必须包含：**当前产物全文/结构 + 历史反馈摘要 + 项目 guidance**  
- 版本化产物，支持回滚  

## 设计原则

- 反馈是一等公民，不是某一页的附加按钮  
- 项目级 guidance 在分析步写入，大纲/正文默认带上  
- 默认「尽量保留结构」，避免每次调整打散连贯性  
