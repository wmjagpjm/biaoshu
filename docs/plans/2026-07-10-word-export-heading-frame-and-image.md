# Word 导出标题页框与图片管线实施计划

> **给 Codex 与 Grok：** 先完成本文的决策门，再按任务顺序实施；Codex 负责改码与验证，Grok 负责计划、差异和测试结果的独立审查。

**目标：** 补齐导出模板中 `heading_border` 的前端配置、实时预览和 Word 导出闭环，并为正文图片样式建立可安全实施的数据协议。

**架构：** 标题页框保持现有 `ExportFormatConfig` 为唯一配置来源，前端负责编辑和近似预览，后端用 `python-docx`/OOXML 输出可检查的真实 Word 样式。图片能力分成独立阶段：先定义受控图片资源引用，再由 Markdown 导出器加载本项目内图片；禁止导出时任意请求远程 URL。

**技术栈：** React、TypeScript、Vite、FastAPI、Python、python-docx、pytest。

---

## 已确认现状

- `git status --short --branch` 为 `main...origin/main`，工作区初始状态干净。
- 前端已有 `heading_border` 与 `image` 类型、默认值和预设，但标题页框只显示启用复选框，实时预览未消费已生成的页框 CSS 变量。
- 后端 `backend/app/services/export_service.py` 尚未读取 `heading_border` 或 `image`。
- 章节正文只有 Markdown 字符串 `chapters[].body`，仓库没有正文图片资源结构、题注协议或导出时图片加载安全规则。
- Grok CLI 能识别仓库，但访问 `https://cli-chat-proxy.grok.com/v1/models` 时 TCP 连接超时；恢复网络后必须补做独立审查。

## 决策门

**决策结果（2026-07-10）：** 用户确认按 Codex 建议实施。本轮 `heading_border` 仅表示“标题段落描边＋按标题级别设置底色”，正文保持普通排版。

- 本轮支持：`enabled`、`border_color`、`level_cell_colors`。
- 本轮冻结：`structure`、`min_heading_left_enabled`，不在界面和交接文档中声称已完成。
- 整章内容容器、上下/左右结构和最小标题左栏如需实现，必须另行提供布局规则或效果图。

该决策保证前端预览和 Word 输出采用同一语义，并避免把标题段落功能误称为整章页框。

## 执行记录（2026-07-10）

- 标题段落边框后端映射、前端控件和实时预览已完成。
- Grok 独立审查发现 OOXML 属性绝对顺序和字符串布尔值风险；Codex 已按意见修复。
- 已补技术标、商务标、Markdown 小标题、四边、分级底色及 `CT_PPr` 顺序测试。
- 验证结果：后端 `51 passed`；前端 `npm run build` 通过；浏览器实测边框与底色计算样式生效。
- 图片资源协议保持独立任务，本轮未实现。

## 任务 1：锁定标题页框验收语义

**文件：**

- 参考：`frontend/src/features/export-format/model/exportFormat.ts`
- 参考：`frontend/src/features/export-format/model/exportFormatPresets.ts`
- 记录：`docs/plans/2026-07-10-word-export-heading-frame-and-image.md`

**步骤 1：** 用户已确认采用“标题段落边框”。

**步骤 2：** Codex 已将确认结果写入本节，并冻结本轮不做的字段。

**步骤 3：** 网络恢复后执行 Grok 只读评审，要求检查实现边界、Word 兼容性和遗漏测试。

**验收：** 计划中不存在同一个字段的两种解释，前端预览和 Word 输出采用同一语义。

## 任务 2：先写标题页框失败测试

**文件：**

- 新建：`backend/tests/test_export_heading_border.py`
- 参考：`backend/tests/test_heading_numbering.py`
- 参考：`backend/tests/test_export_list_table.py`

**步骤 1：** 新测试文件先添加“模块 / 用途 / 对接”文件顶注释。

**步骤 2：** 添加启用场景：保存含 `enabled`、`border_color`、`level_cell_colors` 的默认导出格式，生成技术标 Word，解包或读取段落 OOXML，断言目标标题存在 `w:pBdr` 和对应 `w:shd`。

**步骤 3：** 添加关闭场景：`enabled=false` 时目标标题不得出现页框样式。

**步骤 4：** 添加兼容场景：camelCase、缺失颜色、非法颜色不应中断导出。

**步骤 5：** 运行：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python -m pytest tests\test_export_heading_border.py -q
```

**预期：** 实现前失败，失败原因明确指向标题页框 OOXML 缺失。

## 任务 3：实现 Word 标题页框映射

**文件：**

- 修改：`backend/app/services/export_service.py`
- 测试：`backend/tests/test_export_heading_border.py`

**步骤 1：** 先更新服务文件顶四字段，删除“标题边框尚未映射”的过时说明，写清真实支持范围和扩展限制。

**步骤 2：** 增加私有配置读取函数，兼容 `heading_border`/`headingBorder`、snake_case/camelCase，并限制颜色为合法十六进制值。

**步骤 3：** 增加标题段落 OOXML 样式函数，集中设置边框颜色、边框宽度和按标题级别选择的底色；不得吞掉会掩盖逻辑错误的宽泛异常。

**步骤 4：** 将样式应用到自动生成的大纲标题、正文章节标题及 Markdown 小标题；关闭配置时保持当前 Word 输出不变。

**步骤 5：** 若任务 1 确认为整章页框，则以独立渲染器包装章节内容，不直接把现有所有段落迁入表格；先用一章一容器的测试验证分页、列表和表格不回归。

**步骤 6：** 运行标题页框测试和现有导出测试：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python -m pytest tests\test_export_heading_border.py tests\test_heading_numbering.py tests\test_export_list_table.py tests\test_parse_export.py -q
```

**预期：** 新测试通过，原有编号、列表、表格、下载链路保持通过。

## 任务 4：补齐前端配置与实时预览

**文件：**

- 修改：`frontend/src/features/export-format/components/TemplateForm.tsx`
- 修改：`frontend/src/features/export-format/components/TemplatePreview.tsx`
- 修改：`frontend/src/features/export-format/components/TemplatePreview.css`
- 按需修改：`frontend/src/features/export-format/model/exportFormatCss.ts`

**步骤 1：** 大改前先为三个触达文件补齐文件顶“模块 / 用途 / 对接 / 二次开发”中文注释。

**步骤 2：** 在标题配置区增加边框颜色、分级底色及已确认结构字段的控件；禁用页框时保持字段值但禁用从属控件。

**步骤 3：** 让预览实际渲染页框、分级底色及已确认布局；不要继续保留未使用的 CSS 变量。

**步骤 4：** 对长标题、窄视口、页框关闭状态做布局检查，确保文字不溢出或遮挡。

**步骤 5：** 运行：

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npm run build
```

**预期：** TypeScript 构建通过；模板编辑时预览变化与 Word 导出语义一致。

## 任务 5：单独设计正文图片资源协议

**文件：**

- 计划修改：`frontend/src/features/technical-plan/types.ts`
- 计划修改：`backend/app/api/schemas.py`
- 计划修改：`backend/app/services/editor_state_service.py`
- 计划修改：`backend/app/services/export_service.py`
- 计划新增：`backend/tests/test_export_images.py`

**步骤 1：** 定义图片引用最小结构：项目内资源标识、替代文字、题注；不在章节正文保存绝对路径、Base64 大对象或 API Key。

**步骤 2：** 确定图片来源只允许当前项目上传目录中的受控文件，解析后校验真实路径仍位于该目录；拒绝 `http://`、`https://`、UNC 路径和目录穿越。

**步骤 3：** 为 Markdown 图片语法或结构化图片节点确定唯一协议，避免同时维护两套来源。

**步骤 4：** 再实现 `max_width_percent`、图片对齐、题注字体/字号/对齐/粗体/斜体，并测试超宽图等比缩放。

**步骤 5：** 图片管线完成前，界面应明确该组配置只影响未来可导出的项目图片，不能假装当前 Word 已应用。

**验收：** 图片导出不访问任意网络、不读取项目外文件、不把密钥或本机绝对路径写入仓库或编辑态。

## 任务 6：全量验证、Grok 复审与交接

**文件：**

- 修改：`docs/integration-checklist.md`
- 修改：`docs/HANDOFF-next.md`

**步骤 1：** 运行后端全量测试：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python -m pytest -q
```

**步骤 2：** 运行前端构建：

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npm run build
```

**步骤 3：** 使用 `Start-Biaoshu-Dev.bat` 启动，手工验证模板配置、预览、保存默认模板和导出下载。

**步骤 4：** 将 `git diff`、测试输出和已知限制交给 Grok 做只读复审；Codex 只采纳可复现缺陷、兼容性问题和安全问题，纯风格偏好不扩大范围。

**步骤 5：** 更新 `docs/HANDOFF-next.md` 的已完成/未完成项、验证基线和“注释齐备表”；更新 `docs/integration-checklist.md` 的手工验收步骤。

**步骤 6：** 检查：

```powershell
git status --short
git diff --check
git diff -- . ':!*.lock'
```

**预期：** 不包含 `.env`、真实 Key、数据库、上传文件或本机绝对路径；所有新增/大改文件满足四字段注释要求。

## 提交建议

计划确认并实施后按主题拆分中文提交：

```text
测试：补充导出标题页框验收
功能：实现标题页框预览与 Word 导出
文档：更新导出能力与注释齐备表
```
