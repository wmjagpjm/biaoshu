<!--
模块：V1-I 创建页招标文件摄入真值契约
用途：让技术类创建入口持有真实 File，并在真实项目创建后完成 multipart 上传再进入工作区。
对接：CreatePage、projectStore、TechnicalPlanWorkspace、项目文件上传 API 与 V1-I/P11A E2E。
二次开发：禁止演示文件名、pending 假上传、重复创建项目、服务端错误透传和后端扩围。
-->

# V1-I 创建页招标文件摄入真值契约

> **状态：已完成 failure-first、生产实现、Codex 独立验收、提交与推送。**
> **分支：** 仅 `collab/grok-code-codex-review`，严禁操作 `main`。
> **提交链：** 冻结=`c1cde54`；首轮 test-only=`35b0f6b`；test-only 假红修正=`b8c9776`；生产实现=`4e7a3c3`。

## 1. 问题真值

当前 `/create` 不是诚实的文件摄入入口：

1. 点击上传区会直接生成固定“招标文件-正式稿.pdf”和虚构大小，不打开真实文件选择器；
2. 拖放只保存第一个文件名，真实 `File` 和字节立即丢失；
3. `createProjectAsync()` 只 POST 项目，再把文件名写入 `sessionStorage`，没有 multipart 上传；
4. 技术工作区在服务端 `pipeline.files=[]` 时回退显示 pending 文件名，看起来像已上传，但解析按钮仍因服务端零文件而禁用；
5. 无文件创建也会写入固定演示文件名，形成跨页面假真值。

Grok B 首轮只读审计 task/review=`msg_5794431341264df7bb9607233dd9bbb1`/`msg_f1ecc8f473d94012bc2e226cc05a59a5`。本轮双路复核：Grok A task/review=`msg_edf9182bc8434d7ead9a1dcf230ac941`/`msg_9c8b6a765c4a4cee9a743a8966e07fce`，Grok B task/review=`msg_66c4a635fb5c445f8311c7e92e790090`/`msg_61d0bc3cfe7546a194905e7e990a726e`。三方结论一致：后端单文件上传链已经足够，缺口仅在创建页编排与前端真值展示；旧 P11A pending 绿测必须 test-only 改写。

## 2. 产品裁定

1. **只覆盖技术类入口。** `business` 与 `business-list` 继续导航 `/business-bid`，本包不改商务项目创建/上传流程。
2. **真实选择。** 点击或键盘激活上传区必须打开隐藏的真实 file input；拖放必须保留全部真实 `File`。允许多文件，界面只显示浏览器提供的文件名和真实大小，不读取或展示文件内容。
3. **先创建、后上传、再导航。** 技术类点击开始后先精确一次 POST `/projects`；成功且有 N 个文件时，按选择顺序串行发出 N 次 multipart POST `/projects/{真实 projectId}/files`，字段名固定 `file`。全部成功后才进入对应技术工作区。
4. **无文件诚实创建。** 未选择文件时允许创建项目并直接进入工作区；文件 POST 精确为 0，工作区显示服务端空态“尚未上传文件”。禁止任何演示文件名或 pending 写入。
5. **创建失败零上传。** 项目 POST 失败时停留 `/create`，保留真实选择，显示固定“项目创建失败，请稍后重试”；项目文件 POST 为 0，零导航、零本地假 ID、零存储写入。
6. **部分上传可恢复。** 第 N 个文件失败时立即停止本轮后续上传，停留 `/create`，显示固定“文件上传失败，请重试”。内存保留真实 projectId、已成功项和失败/未尝试项；重试不得再 POST `/projects`，不得重传已成功项，只按原顺序上传失败及未尝试项。
7. **同步单飞。** 同拍双击、Enter/点击或重试连击只能启动一个 create/upload 流；不能依赖 React 下一帧 `disabled` 冒充同步互斥。
8. **失败后锁定项目语义。** 一旦项目创建成功，在本页恢复上传期间不得切换创建能力、增删文件或换用另一个项目；刷新/离开页面可丢失内存 File，但既有真实项目仍可从项目列表进入并在工作区上传。
9. **服务端文件唯一真值。** 技术工作区只显示 `pipeline.files`；必须删除 pending 读取和回退。历史 sessionStorage pending 键不读取、不迁移、不删除，也不得影响 UI 或解析门。

## 3. 最小实现协议

### 3.1 CreatePage 内存状态机

状态只保存在 React/ref 内存，不进入 localStorage、sessionStorage、IndexedDB、URL、Cookie、日志或剪贴板：

```text
idle -> creating -> uploading -> navigating
          |            |
          v            v
      create_failed  upload_failed -> uploading（同一 projectId，仅剩余文件）
```

- 每个选择项至少持有稳定本地 id、真实 `File` 和 `pending|uploaded|failed` 状态。
- `createdProject` 只在真实 POST 成功后设置；失败时保持空。
- 上传串行、遇首个失败停止；已成功状态必须在重试前持久于组件内存。
- 页面错误只能取两个固定中文常量，不拼接异常、响应 `detail/code`、文件路径、项目 ID 或文件内容。
- 项目名可继续取第一个真实文件名去扩展名；无文件时取能力标题。项目 JSON 不得新增 `fileNames` 或文件内容。

### 3.2 projectStore 门面

- `createProjectAsync()` 继续只负责项目 JSON POST，不接收 `fileNames`，不读写任何 pending 键。
- 新增薄上传门面，内部复用既有 `apiUploadFile()`，只构造 `/projects/{encodeURIComponent(projectId)}/files`；CreatePage 不散落 FormData 或 API 基址。
- 不修改 `shared/lib/api.ts`：它已经正确为 FormData 保留浏览器 boundary 并附加现有 CSRF。

### 3.3 工作区展示

- 删除 `getPendingFileNames` 导入和调用。
- `displayFiles` 只能由 `pipeline.files.map(filename)` 产生；零服务端文件时为空。
- 解析按钮继续严格依赖 `pipeline.files.length`，本包不改变解析任务、状态或错误协议。

## 4. 严格文件白名单

生产代码：

1. `frontend/src/features/create/pages/CreatePage.tsx`；
2. `frontend/src/features/technical-plan/lib/projectStore.ts`；
3. `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`。

测试代码：

4. 新增 `frontend/e2e/create-file-intake-truth.spec.ts`；
5. `frontend/e2e/core-project-data-truth.spec.ts`，仅 test-only 把演示文件/pending 成功依据改为真实文件、真实 multipart 和零 pending；其它 P11A 真值不得放宽。

禁止修改 `CreatePage.css`、`shared/lib/api.ts`、`useProjectPipeline.ts`、`package.json`、商务标、TechnicalPlanNewPage、后端、上传服务、解析器、LLM、H2、数据库、迁移、配置或依赖。扩围必须先走 Codex question 与 Grok 只读确认。

## 5. failure-first 与反假绿矩阵

新专项使用受控 route 和合成 File 字节，不触碰真实数据库、uploads 或用户文件：

1. 点击上传区只触发真实 input，零演示 chip；input 选择两个合成文件后，chip 名称/真实大小正确，create → file1 → file2 顺序精确，两个 multipart 的 projectId、字段名、filename 和独立字节锚点正确，最后导航真实 ID。
2. DataTransfer 拖入真实 File 后，multipart 保留该 filename 与字节锚点；空 drop 零演示文件。
3. create 500/网络失败：一次 create、零 file POST、零导航；固定创建错误，不泄露受控 secret；选择仍可重试。
4. 第二个文件首次上传失败：create 始终一次；首文件精确一次，第二文件重试后精确两次，未尝试的后续文件只在重试轮一次；重试全绿后才导航。
5. 延迟 create 响应期间同步触发两次主操作：create POST 精确一次；上传和导航也不得重复。
6. 无文件创建：一次 create、零 file POST、零演示名、零 pending，进入工作区后显示服务端空态。
7. 预置历史 pending 假名：服务端 files 空时假名不可见；服务端返回文件时只显示服务端 filename，刷新保持。
8. 成功、创建失败、上传失败全路径精确核对 local/session storage、IndexedDB、Cookie、clipboard、console、URL、未知 API 与外网；文件字节锚点只允许存在于对应 multipart 请求体。

旧 P11A 受影响用例必须保留项目 JSON 五键、真实 ID、失败不导航、零本地项目键、商务入口和其它既有断言；不得用删除整个用例、跳过或不运行规避冲突。

禁止 `skip/xfail`、固定 sleep、源码扫描、宽泛 `or`、宽路径放行、仅检查请求非空、仅按 filename 冒充字节保留、吞路由异常或条件假绿。生产未改时新增行为和改写后的 P11A 必须真实失败，首红数字如实记录。

## 6. 分级验收

严格串行、单 worker、零重试：

```powershell
cd C:\Users\Administrator\biaoshu-v1i-create-file-intake-impl\frontend
npx playwright test e2e/create-file-intake-truth.spec.ts --workers=1 --retries=0
npx playwright test e2e/core-project-data-truth.spec.ts --workers=1 --retries=0
npx playwright test e2e/technical-editor-state-truth.spec.ts --workers=1 --retries=0
npm run lint
npm run build
git -C .. diff --check
```

E2E 只能使用各自 worktree 相对 `backend/data/biaoshu-e2e.db`；8010/5174 预检为空后才能启动，结束必须清理。禁止整仓 318 E2E、后端 pytest、并发 Playwright、联网安装或真实业务数据。

## 7. 安全与非目标

- 文件内容只从浏览器 File 进入同源 multipart；不得进入项目 JSON、DOM、storage、URL、console、剪贴板或消息箱。
- 允许显示用户选择的 basename；禁止显示浏览器本地路径、服务端 storedName、异常原文、Cookie、CSRF 或身份信息。
- 本包不自动解析、不自动调用 LLM、不安装 MinerU/Docling、不改变 50MB 后端上限、不做文件删除/去重/续传/并发上传、不清理创建后上传失败的真实项目。
- 不修改商务标创建、工作区手工上传、解析、导出、备份、协作、V2 或 V3 能力。

## 8. 完成记录

### 8.1 测试与实现消息链

- Grok B 测试任务/review=`msg_fe1d6d3ffbfc462ea550a0c726bdefbb`/`msg_e93f42a99f9b4cbd9e269ae1e87245e0`。首轮真实 failure-first 为新专项 **8 failed / 0 passed**、P11A **1 failed / 9 passed**。
- 五项反假绿缺口经 Codex question=`msg_7fd86a54be8b4aeca4443d971e8506ad`、Grok B YES=`msg_1fe1d5bce6034bc4ae864fd08a84d31a` 确认后，才以 task/review=`msg_dbcd49f789ae4b15816b3d8b5aa5e6ac`/`msg_c185905baf9d48f49f651f299cad5868` 收紧路由、pending、锁定、探针集合和 multipart 全 part 断言。
- A 首轮绿测暴露三类测试假红；Codex question=`msg_1f9523b1649b4eea8f605bca37afd399`、Grok B YES=`msg_87b0bc894db04c8aad6368c07864085f` 后，test-only task/review=`msg_6e9412ee3cbe476c8865de3053b5e30d`/`msg_0a4ad572a2e24ef4979f1c74e0f5637b`。修正后生产未实现基线仍为 **8 failed / 0 passed**、**1 failed / 9 passed**，证明未靠放宽变绿。
- Grok A 生产任务=`msg_0138fd5cba934815ba7b2c11f7c4b962`。50MB 文案、input accept 与嵌套交互问题经 question/YES=`msg_737b232ebcde4b1ca3b33611c96a3508`/`msg_b8942008f0b54ae6995dbc6ff2cd3b18` 确认后，production-only task/review=`msg_d652fb78464f44d7a12c31432209d1b4`/`msg_827ab86431ad49db83eaa26dadb55d71`。
- chip 迁出后的过时注释也严格走 question/YES/task/review=`msg_8e8d1842ddda490c917ab3084668b368`/`msg_f827e0fe2af34af9b189e89546a05fe6`/`msg_b630328326b048289466134741288ee8`/`msg_6c3138cad41546eaaec57556764bf86b`；只改一行中文注释，运行代码不变。

### 8.2 最终验收

Grok A 与 Codex 均严格串行、单 worker、零重试通过：V1-I 新专项 **8/8 passed**、P11A **10/10 passed**、技术 editor-state truth **28/28 passed**。Codex 的实测耗时分别约 14.8s、14.9s、56.8s；lint 与 build 通过，lint 只有 4 条既有 warning，build 只有既有 chunk 体积提示。`git diff --check`、严格五文件、空暂存、8010/5174 清理和无 Playwright 产物均通过。

未运行后端 pytest、整仓 318 E2E、并发 Playwright、联网安装、真实数据库、真实 uploads 或真实标书；这些未运行项不得冒充通过。

### 8.3 五文件真值

稳定 Git blob（不受 Windows `core.autocrlf` 检出换行影响）：

- `create-file-intake-truth.spec.ts`=`8ab484872bb2b1f80042c731040d3995ed4554e7`；
- `core-project-data-truth.spec.ts`=`e874c46ed755db223e1be39e9ad93e8524e1a620`；
- `CreatePage.tsx`=`013b4d9a27c9a0b60d5532a491e4dc85e5aec941`；
- `projectStore.ts`=`42665600b02967a12af681806ccc81af77238dc8`；
- `TechnicalPlanWorkspace.tsx`=`68f46b6aa4795c28c39791e1518e17b5909f14bd`。

主工作树最终字节 SHA-256 依次为 `3919F9CD18B52F8FF752E297F3F6DC26412B5F23AB667F19815BED67D7C40A47`、`CDD5E408909F17EA031A5E8F5595DD6CE79BFCEAC5957346847F1410E094F64B`、`06A40D6ACBD8A5AD3246B452BB7327E2269F87E185EC8DF67BF081BEC057C718`、`1E2B1C2220E0FB1B88C0C563EF9EA7C6932C414657ACABF9FA8ED283A9AF78CD`、`D9867A1FA61F8FCB6126419534FE826E9A62463A75436A0104B4847C091E520F`。独立 worktree 可能因 LF/CRLF 得到不同字节 SHA-256，判定未篡改时以提交 blob 和 `git diff` 为准。
