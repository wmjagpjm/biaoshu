# 项目正文图片安全导出实施计划

> **协作约定：** Codex 逐项实施与验证；Grok 在协议冻结、差异和回归结果三个节点只读审查。本文以项目文件复用方案为 v1 默认，不创建独立图片库表。

**目标：** 让技术标和商务标正文可插入当前项目已上传的受控图片，并将其按导出模板的宽度、对齐和题注样式写入 Word，同时不访问网络或项目外文件。

**架构：** `project_files` 增加 `role=source|image`；既有 `/files` 和 parse 只处理 `source`，新 `/images` 只处理受限图片。章节正文仍是唯一事实源，图片使用独占行 Markdown `![替代文字](biaoshu-image://file_<16 位十六进制> "题注")`；导出按 `file_id + project_id + role=image` 查库后再安全解析磁盘路径。

**技术栈：** FastAPI、SQLAlchemy、SQLite、Pillow（图片真实性/像素校验）、python-docx、React、TypeScript、pytest、Vite。

---

## 已冻结的 v1 契约

- 采用复用表方案：不建 `ProjectImageRow`；`ProjectFileRow.role` 仅允许 `source` 与 `image`，旧行和空值一律视为 `source`。
- `POST/GET /api/projects/{id}/files` 仅接受/返回 `source`；`_run_parse` 只从 `source` 记录中选取最新文件，图片不能改变解析源。
- 新增 `POST/GET /api/projects/{id}/images`，以及受控预览 `GET /api/projects/{id}/images/{file_id}`；三者均按 workspace → project → 文件行校验。
- 图片只允许 PNG、JPEG、GIF，单文件最多 5 MiB、每项目最多 50 张；服务端以 Pillow `verify()` 读取真实格式和像素，拒绝空文件、伪装 MIME、损坏图片、超像素图片与 webp/svg/bmp。
- 唯一图片正文形式为独占行：

  ```markdown
  ![替代文字](biaoshu-image://file_0123456789abcdef)
  ![替代文字](biaoshu-image://file_0123456789abcdef "图 1 机房拓扑")
  ```

  `file_id` 必须符合 `^file_[0-9a-f]{16}$`；不支持远程 URL、`file:`、`data:`、相对路径、HTML `img`、列表内图片或行内混排。
- 导出遇到无效引用、跨项目引用、非图片角色、丢失文件或损坏图片时，写入明确的图片无效提示并在任务结果返回 `imageWarnings`，任务仍可成功；python-docx 本身不可用等基础设施故障才失败。
- 文件路径永远由数据库 `stored_name` 生成，必须在 `uploads/{project_id}` 的 `resolve()` 后目录内；不得从 Markdown 读取路径或发起网络请求。
- 删除项目时同步清理其 `uploads/{project_id}` 目录，避免图片、源文件和导出 Word 遗留磁盘。

## 任务 1：先写角色隔离失败测试

**文件：**

- 新建：`backend/tests/test_project_images.py`
- 修改：`backend/tests/test_parse_export.py`
- 参考：`backend/tests/test_export_list_table.py`

**步骤：**

1. 建立项目，先上传 `source` Markdown，再上传图片；写失败断言：parse 仍读取 Markdown，`GET /files` 不返回图片，`GET /images` 不返回 source。
2. 写失败断言：跨项目 `file_id`、`biaoshu-image://../`、伪造 PNG、损坏图、超限图、图片数超限均不能导出或读项目外文件。
3. 写成功断言：合法 PNG 写入 docx 图片关系，题注段落采用模板 image 配置，`imageWarnings` 与无效引用语义正确。
4. 运行：

   ```powershell
   cd C:\Users\Administrator\biaoshu\backend
   .\.venv\Scripts\python -m pytest tests\test_project_images.py tests\test_parse_export.py -q
   ```

   预期：图片 API、role、Markdown 图片写入尚不存在时失败。

## 任务 2：实现文件角色、图片校验和安全解析

**文件：**

- 修改：`backend/requirements.txt`
- 修改：`backend/app/core/config.py`
- 修改：`backend/app/core/database.py`
- 修改：`backend/app/models/entities.py`
- 修改：`backend/app/services/file_service.py`
- 修改：`backend/app/api/files.py`
- 修改：`backend/app/services/task_service.py`
- 修改：`backend/app/services/project_service.py`

**步骤：**

1. 在 requirements 增加 Pillow；新增独立图片大小、像素和数量配置，默认分别为 5 MiB、8192 像素边长、50 张。
2. 在 `project_files` 增加带默认值的 `role` 列；`ensure_schema_columns` 补 SQLite 轻量 ALTER，历史空值按 `source` 查询。
3. 将文件服务拆成 source/image 明确入口：源文件保持既有契约；图片服务验证真实格式、规范化扩展名、限制数量，并返回安全元数据。
4. 增加 `resolve_project_image()`：按 `id + project_id + role=image` 查行，`Path.resolve()` 校验仍在本项目上传目录；禁止接受由客户端提供的 stored_name。
5. 将 parse 改为只查询 source；删除项目后在数据库提交成功时清理项目上传目录。
6. 运行任务 1 测试直至通过；确认旧 parse/export 回归仍通过。

## 任务 3：实现 Markdown 图片到 Word 的受控转换

**文件：**

- 修改：`backend/app/services/export_service.py`
- 修改：`backend/app/services/task_service.py`
- 测试：`backend/tests/test_project_images.py`

**步骤：**

1. 为 `write_markdown_body()` 增加可选的项目图片解析器与 warnings 收集器；在表格、列表、标题之前优先识别独占图片行。
2. 将 `build_docx_bytes()` 绑定当前项目的受控解析器，读取 `template.image`：按可用版心宽度和 `max_width_percent` 等比缩放、应用段落对齐、写入可选题注字体/字号/粗斜体/对齐。
3. 无效/缺失/跨项目图片写明确提示段并加入 warnings；未知标准 Markdown 图片仍按普通文本，绝不加载 URL 或磁盘路径。
4. export 任务把 `imageWarnings` 合并到任务结果，不影响原有 `storedName` 和下载语义。
5. 用 `python-docx` 重开导出结果，断言图片关系、尺寸约束、题注和 warning 占位。

## 任务 4：补齐正文编辑器最小交互

**文件：**

- 修改：`frontend/src/features/technical-plan/hooks/useProjectPipeline.ts`
- 修改：`frontend/src/features/technical-plan/components/ChapterEditor.tsx`
- 修改：`frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
- 视需要修改：`frontend/src/features/technical-plan/types.ts`

**步骤：**

1. 在共用流水线增加图片上传和图片列表 API 调用；不把图片混入现有招标文件列表。
2. 正文编辑器增加图标按钮与受控隐藏 file input；成功上传后在当前章节 body 插入唯一 Markdown 引用，默认 alt 使用文件名，不创建远程粘贴入口。
3. 编辑预览仅展示受控图片 URL 或协议提示；导出页提示“仅项目已上传图片可导出”。
4. 保持商务标不强行添加上传入口，但其 Markdown 若含受控引用可复用导出器；不要改变页面信息架构。
5. 运行 `npm run build`；浏览器检查上传、插入、编辑态持久化和导出下载。

## 任务 5：安全回归、Grok 复审和交接

**文件：**

- 修改：`docs/HANDOFF-next.md`
- 修改：`docs/integration-checklist.md`
- 更新：本文执行结果

**步骤：**

1. 完整运行后端测试与前端构建：

   ```powershell
   cd C:\Users\Administrator\biaoshu\backend
   .\.venv\Scripts\python -m pytest -q

   cd ..\frontend
   npm run build
   ```

2. 浏览器使用本地测试图片验证：source parse 不受图片影响、正文插入引用、Word 实际包含图片、无效引用出现提示而非请求网络。
3. 提供完整 diff 和验收输出给 Grok；优先采纳可复现的越权、路径、格式、删除清理、导出兼容问题。
4. 更新交接的注释齐备表、图片协议、基线和剩余边界；不得提交 `.env`、`uploads/`、`data/`、真实 Key 或测试数据库。

## 执行记录（2026-07-10）

- 已完成角色隔离与图片 API：`project_files.role=source|image`、`/files` 默认只返回 source、`/images` 独立上传/列表/受控读取；parse 因此只会选择 source。
- 已完成服务端防护：Pillow 验证 PNG/JPEG/GIF 的真实格式、字节与像素限制；SQLite 在项目行写锁内串行化计数和保存，提交失败会清理刚写入的孤儿文件，清理失败也会记录服务端告警；`resolve_project_image()` 按 workspace、项目、role 和安全 `stored_name` 解析；删除项目会清理其上传目录，失败时记录服务端告警。
- 已完成 Word 导出：独占行 `biaoshu-image://` 引用按模板宽度/对齐/题注插图；无效、跨项目、source 角色和路径逃逸引用都写可见 warning，并写入 export 任务 `imageWarnings`。
- 已完成正文交互：工具栏图标上传后在原章节、原光标位置写入受控引用；上传期间重新编辑或切换章节时，使用最新章节内容避免旧闭包覆盖；特殊文件名会清洗 Markdown 方括号和换行。
- 自动验证：`backend` 全量 `pytest -q` 为 **70 passed**，图片专项为 **14 passed**；`frontend npm run build` 通过。浏览器已确认项目工作区、图片插入按钮和正文编辑器渲染；内置浏览器不支持本地文件上传，因此真实上传/导出由 FastAPI 集成测试覆盖，人工路径见联调清单。
- Grok 只读复审结论：无 P0/P1；据其建议补齐了前端插入竞态、文件名 Markdown 污染、跨项目/role/路径逃逸、并发数量、格式/限额、活跃 Session 锁、提交失败孤儿文件及其清理告警回归。剩余非阻塞项为锁内大图解码，以及 SQLite 以外数据库的等价并发控制。
- 本轮未创建 Git 提交，未写入 API Key、`.env`、数据库或 `uploads/`。

## 实施边界

- 本轮不引入 Redis、对象存储、独立图片表、远程抓图、URL 下载、Base64 章节正文、图片 OCR、富文本块编辑器、图片版本控制或多用户权限系统。
- 现有 `source` 文件格式白名单和“多个 source 文件如何选主文件”是独立债务；本轮只确保图片不会污染已有 parse 语义。
- 未获用户明确要求时不创建 Git 提交；提交信息若需要，必须使用简体中文。
