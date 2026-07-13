<!--
模块：阶段4包9A Word 最小标题左栏方案
用途：冻结 Word 精细版式首个独立交付的视觉规则、配置语义、文件边界与验收标准。
对接：export_service、导出格式模板、TemplatePreview、阶段4路线图与联调清单。
二次开发：本包只实现叶子标题左侧强调线；不得误称整章页框，structure 字段不得在本包接线。
-->

# Word 精细版式：最小标题左栏（阶段 4 功能包 9A）

> **状态（2026-07-13）**：规划已冻结，**尚未开始实现**。
> **基线提交**：`6b4205f`（文档：冻结包9交付增强规划）。
> **分支**：`collab/grok-code-codex-review`。
> **顺序**：P9A → P9B → P9C；P9B 与 P9C 在本包验收前均不启动。

## 1. 目标与视觉契约

本包只新增一个可控、可预览、可导出的“最小标题左栏”版式效果，解决长篇技术标中末级小标题不够醒目的问题。

| 项 | 冻结规则 |
|---|---|
| 作用对象 | 每个标题分支中**没有下级标题的叶子标题**；技术标大纲、技术标章节 Markdown、商务标 Markdown 均按各自实际标题层级判定 |
| 开关 | 仅当 `heading_border.enabled=true` 且 `heading_border.min_heading_left_enabled=true` 时生效 |
| Word 效果 | 标题段落左侧写入实线强调边框，颜色复用现有 `heading_border.border_color`；同时保留现有标题描边与分级底色 |
| 固定尺寸 | 左侧边框宽度为 2.25 pt，标题文字与左边框间距为 6 pt；首行缩进、字体、字号、编号、前后间距仍完全沿用对应标题级配置 |
| 前端预览 | 与 Word 同样显示 2.25 pt 左侧强调线；主题色和开关即时反映 |
| 一级标题 | 一级标题继续遵循既有 `heading_level1_page_break_before`；本包不额外改变分页 |
| 封面、目录、页眉页脚 | 不应用左栏 |
| 页级版式 | **不创建**页面边框、文本框、分栏、节级页框或整章背景；本包不是“整章布局/页框”功能 |

“最小标题”是文档内容结构上的末级标题，而不是固定的第六级标题：只有三级标题的文档可作用于三级叶子标题；出现四级标题时该分支的四级标题才作用。标题后无正文也可视作叶子标题。实现必须在写入 Word 前完成层级判定，禁止因流式写入把所有标题都误加左栏。

## 2. 现状与取舍

| 现状 | 结论 |
|---|---|
| 前端 `HeadingBorderConfig` 已有 `min_heading_left_enabled` | 复用该既有配置，不新增数据库字段或 API |
| 前端版面预设已能把该字段设为 `true` | 保持预设兼容；补充表单开关和预览，避免“配置已写入但用户无从理解” |
| 后端 `_heading_border_cfg`、`_apply_heading_border` 仅处理四边描边与底色 | 扩展为读取开关、判定叶子标题、写入左侧段落边框 |
| `heading_border.structure` 已存在但未映射 | 本包保持未接线；不得显示为可用的“上下/左右结构”能力 |
| 现有标题边框测试 | 在其基础上增加 OOXML、叶子判定与无页框回归，不重写导出主链 |

## 3. 实现边界

### 允许改动

| 文件 | 职责 |
|---|---|
| `backend/app/services/export_service.py` | 清洗开关；在大纲和 Markdown 标题写入前判定叶子标题；以规范 OOXML 写入左侧段落边框与间距 |
| `backend/tests/test_export_heading_border.py` | 覆盖配置兼容、技术/商务导出、叶子与非叶子标题、禁用回归、无 `w:pgBorders` |
| `frontend/src/features/export-format/components/TemplateForm.tsx` | 在标题边框已启用时暴露“最小标题左栏”开关，并明确其只修饰叶子标题 |
| `frontend/src/features/export-format/components/TemplatePreview.tsx` | 按配置展示叶子标题左侧强调线 |
| `frontend/src/features/export-format/components/TemplatePreview.css` | 实现预览样式，尺寸与 Word 契约一致 |
| `docs/plans/2026-07-13-p9a-word-layout-plan.md` | 本计划及实施/验收结果 |
| `docs/plans/2026-07-12-bid-writer-roadmap.md`、`docs/HANDOFF-next.md`、`docs/integration-checklist.md` | 实施完成后的文档闭环 |

### 明确不做

- `heading_border.structure` 的任何语义、控件或 Word 映射。
- 整章页框、封面/目录重排、分节、左右栏、文本框、背景水印、页边距自动重算。
- 新建导出格式 API、数据库迁移、依赖升级或改动既有模板数据。
- P9B 外部标讯、P9C embedding、解析引擎、响应矩阵和多角色功能。

## 4. 实现步骤

1. 在后端把 `min_heading_left_enabled` 规范化为布尔值，并保持 snake_case/camelCase 配置兼容。
2. 从大纲树和单段 Markdown 的完整标题序列计算叶子标题；将判定结果显式传给标题写入函数，避免标题函数自行猜测上下文。
3. 在既有标题段落属性顺序规则内写入 `w:pBdr/w:left` 与 `w:ind`；关闭任一开关时不得写入该左栏属性。
4. 将模板表单开关接至现有配置字段；预览只对示例中的叶子标题添加 CSS 左边框。
5. 补齐定向测试、全量后端回归、前端 lint/build，并导出一份技术标和一份商务标进行人工 Word 打开检查。
6. Codex 完成独立审查与验收后，才允许 Grok 中文提交；随后单独提交文档闭环并推送协作分支。

## 5. 测试矩阵

| 场景 | 期望 |
|---|---|
| camelCase 与 snake_case 开关 | 都能正确启用/关闭；非法值不意外启用 |
| 开关均开启，三级标题为末级 | 三级标题有 `w:left`；一级、二级等含子标题的标题没有左栏 |
| 四级标题存在 | 该分支四级叶子标题有左栏，三级父标题没有 |
| 只有一级标题 | 一级叶子标题可有左栏；既有分页开关仍按原规则生效 |
| 仅开启标题边框或仅开启左栏字段 | 不出现左栏；原有标题描边/底色语义不变 |
| 技术标与商务标 | 两条导出路径均按契约生效 |
| Word 页面属性 | 导出文档不含 `w:pgBorders`，不产生页面级边框 |
| 前端 | 开关可操作、预览同步；`npm run lint` 与 `npm run build` 通过 |

验收命令：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_export_heading_border.py
.\.venv\Scripts\python.exe -m pytest -q

cd ..\frontend
npm run lint
npm run build

cd ..
git diff --check
```

## 6. 验收门槛与风险

- 必须同时核验 `.docx` 的 OOXML 和 Microsoft Word/兼容编辑器中的实际打开效果；只看浏览器预览不算通过。
- 不同 Word 版本对段落边框的渲染可能略有差异，因此采用标准段落边框而非文本框或浮动形状，优先保证可编辑与跨版本稳定。
- 没有用户新的效果图时，本文件第 1 节是唯一实现依据。任何“整章页框”“左右结构”诉求必须另开 P9A-扩展计划，不得悄然扩大本包。
