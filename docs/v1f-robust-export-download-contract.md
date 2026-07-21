<!--
模块：V1-F 稳健 Word 下载与人读文件名契约
用途：把技术标/商务标导出交付从异步 window.open 收口为同源可判定下载，并使用安全项目名。
对接：export 任务结果、统一 HTTP 客户端、项目流水线、两个工作区导出页、后端下载路由。
二次开发：禁止回退 V1-E 保存/项目围栏、P9D 图片告警顺序、exports 随机磁盘名和工作空间鉴权。
-->

# V1-F 稳健 Word 下载与人读文件名契约

> **状态：已完成实现、Codex 独立验收、提交并推送。** 冻结=`5df1114`，契约收紧=`ee97701`，实现=`65fe5e6`。
> **分支：** 仅 `collab/grok-code-codex-review`，严禁操作 `main`。
> **基线：** `f88352f`；V1-E 已完成、独立验收并推送。

## 1. 问题真值与优先级

技术标与商务标当前都在 export 异步任务结束后才调用 `window.open`。此时已脱离用户点击手势，Chrome/Edge 可拦截新窗口；返回值未检查，技术标仍显示“正在下载”，商务标甚至可能静默无文件。即使成功，下载路由也把内部随机名 `export_<8hex>.docx` 写入 `Content-Disposition`，而任务结果中已经存在的项目人读 `filename` 完全未被下载链消费。

Grok B 只读审计=`msg_a509ff3d0a1b4dd1b56787ac47a9585f`，Codex 独立追踪确认并回执=`msg_a24cbfbee52445c1a2270728d6a2ebb7`。双方均确认这是每次最终交付都可能触发的 V1 断点。

并行候选“writer 任务切项目后迟到 success 造成 sticky loading”也已确认存在：Grok A=`msg_3f88ad6456844026b60521319644994b`，Codex ack=`msg_efa669f7e26e44a6957520297102ac68`。它登记为后续 V1-G，不得混入本包。多章空正文导出质量门也继续后置。

## 2. 方案裁定

1. **仅改后端 `Content-Disposition`：拒绝。** 能改善文件名，不能关闭异步弹窗拦截和原页无失败态。
2. **点击时同步预开空白页、任务成功后导航：拒绝。** 会留下空标签页，下载失败时仍可能展示 API JSON，项目切换清理更复杂。
3. **同源 fetch → Blob → 临时 `<a download>`：采用。** 不依赖弹窗许可，可在原页判定 HTTP/MIME/空体失败，并能在触发下载前再次执行项目代次围栏。

DOCX 在本机/内网 V1 中允许一次性进入浏览器内存；流式保存、下载历史和大文件断点续传另包处理。

## 3. 下载协议

### 3.1 统一入口

- 技术标与商务标成功 export 都必须调用 `useProjectPipeline.downloadExport(task)`；商务标不得继续直接消费任意 `downloadPath` 拼接 URL。
- 入口只接受当前项目、`status=success`、精确匹配 `^export_[0-9a-f]{8}\.docx$`（大小写不敏感）的 `storedName` 和可选安全 `filename`；路径必须由当前 `projectId + storedName` 本地构造并逐段编码。
- `storedName` 只用于定位磁盘随机文件，不作为用户保存名；不得信任任务结果中的绝对 URL、查询参数或文件系统路径。
- 页面必须 `await` 下载完成；同一次导出 token 在下载判定结束前保持占用，快速重入不得重复 GET/下载。

### 3.2 同源二进制请求

- 统一 HTTP 客户端新增最小二进制 GET 能力，继续使用 `API_BASE` 与 `credentials: same-origin`。
- GET 不附 CSRF，不读 Cookie，不把会话、workspace、token 或 filename 写入 query。
- 仅接受 2xx、DOCX MIME 与非空 Blob；失败统一抛出内部可判定错误，页面只展示固定中文“下载失败，请重试”。
- 不向 UI 传播服务端 detail、响应正文、路径、项目 ID、storedName、Cookie 或堆栈。

### 3.3 浏览器保存

- 成功响应转为 Blob URL，创建不可见临时 `<a download>`，触发后立即移除并在 `finally` 撤销 URL。
- 禁止 `window.open`、新标签页、外部导航、data URL、base64 持久化或浏览器 storage。
- 用户可见文件名优先取同源响应 `Content-Disposition` 的安全解析结果，其次取任务结果中的安全 `filename`，非法/缺失时固定回退 `标书.docx`；任何来源都必须再次收敛。
- 下载 GET 等待期间若项目/session/run 变化、组件卸载或 V1-E 导出 token 失效，旧结果必须零 anchor click、零下载、零新项目提示；允许中止请求。

## 4. 人读文件名

后端提供唯一收敛函数；任务结果 `filename` 与下载响应 `Content-Disposition` 都只从调用时服务端权威 `project.name` 生成并使用相同规则。若两次调用之间项目被改名，下载响应以下载时权威名称为准，不新增文件元数据或历史表：

1. 基础名来自当前 workspace/project 的 `project.name`，不信任客户端提交的下载名；
2. 移除控制字符和 Windows 非法字符 `< > : " / \\ | ? *`，折叠首尾空白并移除尾部点/空格；
3. 去掉重复 `.docx` 后缀，基础名限制为 100 个 Unicode 码点；
4. 去掉扩展名后的整个基础名若大小写不敏感地匹配 Windows 保留名 `CON/PRN/AUX/NUL/COM1..9/LPT1..9`，必须在基础名尾部追加单个 `_` 后再加扩展名，例如 `CON_.docx`；
5. 空结果回退“标书”，最后只追加一次 `.docx`；
6. 磁盘文件仍为 `export_<8hex>.docx`；前端任务结果只接受该精确生成形态，下载路由继续保持既有 `export_*.docx` basename 防穿越门，绝不按人读名访问磁盘。

FastAPI/Starlette 负责标准 `Content-Disposition` 编码；中文/空格必须有真实后端测试，禁止手拼未转义响应头。

## 5. 页面语义与既有契约

1. V1-E 的 `flushPendingSaveForExport`、项目 token、保存 generation/epoch 与迟到 success 零下载全部保持。
2. P9D 成功顺序保持：先接受并显示有限 `imageWarnings`，再发起下载；图片告警不因下载失败丢失。
3. 技术标只有 Blob 下载已触发后才显示固定成功提示；商务标复用同一错误/成功语义。
4. 下载失败不把 export 任务改成 failed；任务已成功生成文件，页面只陈述本次客户端下载失败并允许用户再次显式点击导出。
5. 项目切换后旧下载失败/成功不得覆盖 B 项目状态，也不得由旧 `finally` 清理 B 的 token。

## 6. 严格文件白名单

生产：

1. `frontend/src/shared/lib/api.ts`；
2. `frontend/src/features/technical-plan/hooks/useProjectPipeline.ts`；
3. `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`；
4. `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`；
5. `backend/app/services/export_service.py`；
6. `backend/app/api/export.py`。

测试：

7. 新增 `frontend/e2e/export-robust-download.spec.ts`；
8. 新增 `backend/tests/test_export_download_filename.py`；
9. `frontend/e2e/export-image-warnings.spec.ts`，只把成功下载/迟到零下载观察点从 `window.open` 改为新协议；
10. `frontend/e2e/export-latest-editor-state.spec.ts`，只保持 V1-E 的成功一次下载和迟到零下载断言。

禁止修改任务 Schema/result 字段、数据库、迁移、鉴权、共享 router、Word 正文/版式、editor-state hooks、依赖、配置或其它测试。证据要求扩围时必须先 question、双方确认并修订冻结文档。

## 7. failure-first 与反假绿矩阵

前端新专项必须使用真实技术/商务导出页；允许路由桩返回合成 DOCX 字节，但不得调用私有函数、读源码、固定 sleep 或访问外网。

1. 技术标：把 `window.open` 固定为 blocked/null，export success 后仍出现一次真实 Playwright download；GET 精确一次，建议文件名为安全项目名，window.open 精确 0。
2. 商务标：同构证明走统一 `downloadExport`，不消费恶意/外部 `downloadPath`；GET 按当前 project/storedName 构造。
3. 401/403/404/500、网络失败、错误 MIME、空 Blob：零 download，出现固定脱敏错误；服务端 detail、路径和锚点不得出现在 DOM/storage。
4. export 结果缺失/非法 storedName：零下载 GET、零 anchor、固定错误。
5. 下载 GET 挂起时 A→B：释放 A 200 后 B 零下载、零 A 提示；B 后续导出可成功，A finally 不得清 B。
6. 快速双击/重复 success：同一导出精确一个下载 GET 和一个 download 事件。
7. P9D：当前项目告警先显示后下载；跨项目迟到 success 仍零告警、零下载；受控 storedName 夹具也必须使用 `export_<8hex>.docx` 真实生成形态。
8. V1-E：PUT/save gate 顺序和 18 项语义不变；观察下载方式改变不得放宽 PUT/export 断言。
9. 后端：中文、空格、非法字符、重复扩展名、空名、保留名均产生安全人读 `Content-Disposition`；磁盘名与 URL 仍随机 basename。
10. 反假绿：禁止 `waitForTimeout/setTimeout/sleep`、skip/fixme/only、宽松 `or`、真实外网、源码读取；下载必须由 `page.waitForEvent("download")` 或等价事件证明，不能只数函数调用。

## 8. 分级验收

严格串行，前端单 worker、零重试：

```powershell
cd C:\Users\Administrator\biaoshu-v1f-robust-download-impl\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_export_download_filename.py --tb=short

cd ..\frontend
npx playwright test e2e/export-robust-download.spec.ts --workers=1 --retries=0
npx playwright test e2e/export-image-warnings.spec.ts --workers=1 --retries=0
npx playwright test e2e/export-latest-editor-state.spec.ts --workers=1 --retries=0
npm run lint
npm run build
git -C .. diff --check
```

Codex 根据实际改动决定是否增加后端导出代表回归与技术/商务 truth；禁止并发 Playwright/pytest、后端全量、整仓 E2E、真实业务库/uploads/密钥或外网。

## 9. 非目标与后续

本包不做导出历史、断点续传、流式超大文件、服务端文件清理、多人下载权限细化、DOCX 内容/版式、空章节质量门、任务正文刷新、OCR、V2 协作或 V3 部署。V1-F 完成后优先回到已确认的 V1-G writer 迟到 success sticky loading。

## 10. 完成证据

Grok B 初始 failure-first 为后端 **16 failed / 1 passed**、新下载 E2E **13 failed / 0 passed**、P9D **3 failed / 1 passed**、V1-E **3 failed / 15 passed**，首轮回执=`msg_4edaeaa46db742898ed78f69483a82a9`。Codex 审查后，临时文件、保留名精度、P9D storedName 夹具和人读名优先级均经 `msg_b96c1ea24f794c3c8fa5d4f91efe55aa`/`msg_3a8b1c682aa940ce814d4c69fe3cb9f0` 双确认后修正。

冻结前继续关闭四类反假绿：控制字符覆盖、V1-E A2 的 201 交付同步、SOH 错层夹具和纯空格项目名的创建层默认值。对应确认链为 `msg_3626ad03df104f59a5ca11891c39dd6d`/`msg_3a56c97f52174536b9faf99125d24ac6`、`msg_3ff8a46e7ddf4b0fa1893d70cedf82d4`/`msg_62252b2547fc423e9bd50d89d73a7922`、`msg_52256035581a40feb7274144e78b6866`/`msg_0f166cc2e85c4409838424e73d9a5ecf`。C1 控制字符最终使用可穿过真实 DOCX 链的 TAB、DEL 与 U+0085；C4 修正后后端专项先达到 **19 passed**，随后 U+0085 得到真实 **1 failed / 19 passed**。

Grok A 实现后，Codex 又发现并与 A 双确认内部 `..` 被前端误拒绝、C1 控制字符 U+0080-U+009F 同向遗漏，question/确认=`msg_067f62cabbb746899f462b6e82afd5d7`/`msg_c21fd247e04c4dc8b98e7d836f5dcc64`；最终 review_request=`msg_b955c06cc8994560b801cddcec8250e7`。Grok 与 Codex 独立串行结果一致：后端文件名/代表导出 **20/1 passed**，新下载/P9D/V1-E/技术 truth/商务 truth **14/4/18/28/18 passed**；lint、build、diff-check、精确十文件、空暂存区和 8010/5174 端口清理通过。未运行后端全量或整仓 318 E2E。

最终 SHA-256：

- `backend/tests/test_export_download_filename.py`=`AEC45F6E725218F4A0FA382C2D6EE6C4BCC787DCAFC8FA1495122AA92F2527D4`
- `frontend/e2e/export-robust-download.spec.ts`=`BABCE3ADADD2487E2315806BBCFD7953606D3BC90A15C54110AE2E1A32AF8A8D`
- `frontend/e2e/export-image-warnings.spec.ts`=`AD6EC4FF88DBE503664A1374580BD2A4F972029D2C420C2569F2E109CAF2DF0A`
- `frontend/e2e/export-latest-editor-state.spec.ts`=`E836DEF01F948CC57E64AAC911C87A0D1ED56D682D4F07D50712D6B884751C54`
- `frontend/src/shared/lib/api.ts`=`F70FD1D5584B62B5929000C679981D05728743AD431464CB5B2ACBE5CC56C738`
- `frontend/src/features/technical-plan/hooks/useProjectPipeline.ts`=`34ABA5E987A5F61660D81F65B252FE668DD319F91DDAC0FDF159A873A902EA18`
- `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`=`31DFF8D147892ED014791FAA15E932137A178CBA6BC49D9CE4BF3656CCD37B36`
- `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`=`52B7ED4E606A10FE6E96C6D62D8F54E2DE1F5EF94D84844C50DF64A61B713C27`
- `backend/app/services/export_service.py`=`9FD43DD306C8E74E9562C7D58B2F753E908BC86FC118794C824922A7E8A4944B`
- `backend/app/api/export.py`=`15CF3DE1A915116EACDEBFEF302E797BEDEFBEFD76E404D6653A5328ACDE67D8`
