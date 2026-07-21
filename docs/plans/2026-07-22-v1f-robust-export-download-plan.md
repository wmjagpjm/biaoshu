<!--
模块：V1-F 稳健 Word 下载与人读文件名实施计划
用途：把后端文件名红测、前端真实 download 红测、最小生产实现和 Codex 独立验收拆为顺序任务。
对接：V1-F 契约、Grok B failure-first、Grok A 生产实现、独立 worktree。
二次开发：严格十文件；测试先行；疑似问题双方确认后才返修；Grok 禁止 Git 写入。
-->

# V1-F 稳健 Word 下载与人读文件名实施计划

> **完成状态：** 冻结=`5df1114`，契约收紧=`ee97701`，实现=`65fe5e6`；Grok 最终 review=`msg_b955c06cc8994560b801cddcec8250e7`，Codex 独立验收通过。
> **执行要求：** 使用 `executing-plans`；所有 pytest/Playwright 串行，Playwright 单 worker、零重试。

**目标：** export 成功后不依赖弹窗许可即可在原页触发一次可判定 Word 下载，并以安全项目名保存。

**架构：** 后端用唯一函数生成任务结果和 `Content-Disposition` 的人读文件名，随机 storedName 继续只作磁盘定位；前端统一二进制同源请求，在项目代次仍有效时用 Blob URL 与临时 anchor 下载。技术标/商务标复用 `downloadExport`，V1-E/P9D 围栏保持。

**技术栈：** FastAPI `FileResponse`、Python pytest、React/TypeScript、Fetch/Blob/Object URL、Playwright download 事件。

## 任务 1：冻结与独立 worktree

1. Codex 提交并推送契约/计划，记录冻结提交。
2. 从冻结点创建 `C:\Users\Administrator\biaoshu-v1f-robust-download-impl`，分支 `collab/v1f-robust-download-impl`。
3. 为该 worktree 固定独立前端 E2E 数据库/端口；不得复用日用 8000/5173 或真实数据。
4. Grok A/B 路由切到新 worktree；B 写 failure-first 时 A 只读等待。

## 任务 2：Grok B 后端与前端 failure-first

**唯一可写：** 契约 §6 的四个测试文件；生产六文件只读。

1. 新建后端专项，先锁定中文/空格/非法字符/重复扩展/空名/保留名的 `Content-Disposition`，并证明 URL/磁盘仍用随机 storedName。
2. 新建前端专项，以真实导出页、blocked `window.open` 和 Playwright download 事件证明当前生产无法稳健下载。
3. 覆盖技术/商务、错误响应/错误 MIME/空体、非法结果、快速双击和下载 GET 期间 A→B。
4. 机械更新 P9D/V1-E 两个既有 E2E 的下载观察点，不放宽保存顺序、告警或迟到隔离。
5. 串行运行后端新专项与前端新专项；报告真实 failed/passed/did-not-run、首红、四测试哈希、暂存区和端口清理。

## 任务 3：Codex 审查并冻结红测

1. 核对红点来自随机 `Content-Disposition`、异步 `window.open` 或缺少原页错误，而非夹具、登录、端口或下载事件误用。
2. 核对 download 由浏览器事件证明；禁止只断言 fetch/anchor 函数被调用。
3. 核对 A→B 先证明 A 下载响应已交付，再否定 download；B 后续成功防止假绿。
4. 核对 P9D/V1-E 原断言强度未下降；疑似问题先 question，确认后才授权 test-only 返修。
5. 冻结测试哈希后，向 Grok A 下发 production-only 任务。

## 任务 4：Grok A 最小生产实现

**唯一可写：** 契约 §6 的六个生产文件；冻结四测试只读。

1. 在 export service 增加唯一安全人读文件名函数，`build_docx_bytes` 与下载路由共同使用；项目改名竞态以下载时响应头为准。
2. 下载路由保持 workspace/project/basename/随机磁盘路径校验，只把 `FileResponse.filename` 改为安全项目名。
3. 统一 HTTP 客户端增加同源 Blob GET 与安全 `Content-Disposition` 文件名解析；不复制 Cookie/CSRF，不向 UI 泄漏 detail。
4. `useProjectPipeline.downloadExport` 严格解析结果、构造项目内路径、下载 Blob、复核项目代次、创建并清理 anchor/Object URL，固定失败提示。
5. 技术/商务页面都 await 统一入口；保持告警先于下载、V1-E token/项目围栏和单飞。
6. 串行运行新专项、P9D、V1-E、必要后端回归、lint/build/diff，发送 review_request；不得 Git 写入。

## 任务 5：Codex 独立验收

1. 核对严格十文件、冻结测试哈希、暂存区为空和无依赖/配置变化。
2. 静态追踪 filename 来源、storedName 路径、Cookie/CSRF、Blob URL 生命周期、下载中项目切换和旧 finally。
3. 独立复跑契约 §8；按 diff 增加代表导出回归与 truth，不机械重复整仓全量。
4. 检查网络/DOM/storage/日志无 detail、路径、正文、token 泄漏；8010/5174 无残留监听。
5. 发现问题按 question→confirm→task→review_request 闭环。

## 任务 6：提交、推送与文档闭环

1. Codex 中文实现提交：`实现：提供稳健Word下载和人读文件名`。
2. 只快进并推送 `collab/grok-code-codex-review`，核对本地、上游、GitHub 一致。
3. 更新契约、计划、HANDOFF-next、路线图和联调清单，写入真实红绿数字、消息链、十文件哈希与未运行项。
4. 中文文档提交：`文档：闭环V1F稳健Word下载`，推送后主仓与实现 worktree 均干净。

## 任务 7：继续 V1 主线

V1-F 后优先冻结已双确认的 V1-G writer 任务迟到 success sticky loading；多章内容质量门随后评估。复杂版式、OCR、真实解析器安装和 V2/V3 继续后置。

## 执行结果

1. Grok B 首轮真实红测为后端 **16 failed / 1 passed**、新下载 E2E **13 failed / 0 passed**、P9D **3 failed / 1 passed**、V1-E **3 failed / 15 passed**。
2. B1-B4、C1-C4 和 C6 U+0085 均严格走 question→confirm→task→review_request；SOH 因 XML 层先红被替换为 TAB，纯空格项目名按创建层权威默认名修正，U+0085 最终真实 **1 failed / 19 passed**。
3. Grok A 的 C5/C6 生产边界也经双确认后才修复；最终严格十文件，四测试哈希冻结，Grok 未暂存、提交或推送。
4. Grok 与 Codex 独立串行均通过后端 **20/1 passed**，前端 **14/4/18/28/18 passed**，lint/build/diff-check/空暂存/端口门通过；未运行后端全量或整仓 318 E2E。
5. Codex 以中文提交 `65fe5e6` 完成实现；远端同步以协作分支最终 Git 真值为准。
