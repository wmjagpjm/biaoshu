# V1-B 离线恢复与回滚演练实施计划

> **执行要求（For Claude）：** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task.

**目标：** 在不触碰真实业务数据的前提下，交付严格 v2 可恢复备份、显式离线恢复、恢复前备份点、journal 自动回滚和崩溃重入。

**架构：** V1-A 备份核心升级为带独立数据兼容版本和六根四态的 v2；新恢复核心先全量只读校验并创建恢复前 v2 备份，再在仓库同卷工作根 staging，通过持久 journal 驱动根级切换、校验和逆序回滚。根 bat/PowerShell 只负责显式路径、中文确认、UTF-8 输出和固定退出码。

**技术栈：** Python 标准库、SQLite、Windows PowerShell 5.1、bat、`unittest`/现有单文件测试框架、临时假仓。

---

> 状态：已完成实现、独立审查、串行验收与代码提交
> 契约：`docs/v1b-offline-restore-rollback-contract.md`
> 冻结前基线：`6382342`
> 契约冻结：`40d1852`
> 代码提交：`20a4a60`

## 0. 协作与禁止项

- Grok A/B 从本契约冻结提交建立全新独立 worktree；不得沿用只读审计 worktree 实现。
- A/B 通过本地自动路由领取任务，使用独立 leader socket；不得手工复制真实数据或消息。
- A/B 不得 `git add/commit/push/stash/reset/checkout`，不得修改白名单外文件。
- 同一 worktree 测试必须串行；所有数据库和数据树均由 `tempfile` 创建。
- 疑似问题先发 `question`；Codex 与对应 Grok 双方确认存在后才授权最小返修。
- 不运行后端全量、前端、Playwright 或整仓 E2E，不占用/终止真实 8000/5173，不读主仓真实 `biaoshu.db`/uploads/Key。

## 1. Task 1：Grok B 建立 v2 与恢复 failure-first

**文件：**

- 修改：`tools/v1-ops/test_biaoshu_backup.py`
- 新建：`tools/v1-ops/test_biaoshu_restore.py`

**Step 1：升级既有 60 项备份契约断言**

把预期 schema 改为精确 v2，新增 `data_compatibility_version`、顶层六键、六根四态及 `files` 聚合断言；保留 V1-A 的停机、源变化、SQLite、junction、敏感与 PS5.1 全部证据。

**Step 2：写恢复入口和 API surface 红测**

断言根 bat、UTF-8 BOM PS1、Python 模块、常量、异常和五个公开函数精确存在；当前生产缺失时真实失败。

**Step 3：写严格预检红测**

覆盖 v1/缺 compat/未知 compat、roots 缺键/非法状态/files 不一致、物理多余文件、逃逸、碰撞、篡改、损坏 DB、busy 服务、非默认数据路径和 reparse 链。

**Step 4：写事务与崩溃红测**

用固定故障注入点覆盖 PRE_BACKUP、STAGE、每根 CUTOVER intent/result、VERIFY、ROLLING_BACK、COMMIT 后 cleanup；断言成功/失败前后的全根哈希地图和 DB 完整性。

**Step 5：写包装和往返红测**

覆盖 PS1 BOM/Parser、精确中文确认、空格/中文路径、中文 stderr、严格 exit；完成假仓 `backup→污染→restore→pre-restore 回滚`。

**Step 6：串行运行 failure-first**

```powershell
& .\backend\.venv\Scripts\python.exe .\tools\v1-ops\test_biaoshu_backup.py
& .\backend\.venv\Scripts\python.exe .\tools\v1-ops\test_biaoshu_restore.py
```

预期：0 收集错误、0 skip/xfail；首个业务失败为 v2/恢复能力缺失。记录精确 passed/failed，不预设数量。

**Step 7：静态门与回执**

```powershell
& .\backend\.venv\Scripts\python.exe -m compileall -q .\tools\v1-ops\test_biaoshu_backup.py .\tools\v1-ops\test_biaoshu_restore.py
git diff --check
git status --short
```

发送 `review_request`，列文件、哈希、failure-first、反假绿点和未做项；不提交。

## 2. Task 2：Grok A 升级 v2 备份格式

**文件：**

- 修改：`tools/v1-ops/biaoshu_backup.py`

**Step 1：冻结常量和根状态模型**

增加 `BACKUP_SCHEMA_VERSION=v2` 与 `DATA_COMPATIBILITY_VERSION=biaoshu-data-v1`；保留 V1-A 公共函数签名。

**Step 2：构建六根状态**

对 db、四个 canonical/legacy 根和 semantic 根生成 `present|empty|absent|not_included`，计算精确文件数/总字节；semantic 未启用时只能为 `not_included`。

**Step 3：写严格 v2 manifest**

输出精确六键，排序稳定；强校验 roots/files 聚合和敏感门。canonical 根内出现会被静默遗漏的未知、被排除或非普通文件时固定失败。

**Step 4：只用临时假仓自测**

不得查看 B 测试。使用 TEMP 假仓证明 present/empty/absent/not_included、v2 常量、SQLite 和现有 V1-A 关键路径。

**Step 5：静态门与回执**

```powershell
& .\backend\.venv\Scripts\python.exe -m compileall -q .\tools\v1-ops\biaoshu_backup.py
git diff --check
git status --short
```

发送 `status` 或 `review_request`；不提交。

## 3. Task 3：Grok A 实现恢复核心

**文件：**

- 新建：`tools/v1-ops/biaoshu_restore.py`

**Step 1：实现严格只读加载**

按契约验证 v2/compat、UTC/git 类型、六根状态、files、物理集合、路径、大小/哈希、DB integrity 和敏感门；v1 固定拒绝。

**Step 2：实现默认布局与服务门**

绝对锚定六根；只检查 `DATABASE_URL/UPLOAD_DIR` 是否默认，不记录值；复用 V1-A 服务停止语义。

**Step 3：实现恢复前 v2 备份**

调用 `create_offline_backup`；依据目标 semantic 状态决定是否纳入当前 semantic。失败零 live 写入。

**Step 4：实现独占 journal 与 staging**

工作根必须仓库外、同卷、无 reparse；journal 原子持久化 intent/result，所有名字由 operation UUID 和逻辑根派生。

**Step 5：实现根级切换和 VERIFY**

按固定顺序 move live→hold、stage→live 或保持 absent；每步可重入。最终验证全根地图、not_included 不变、无 reparse 和 DB integrity。

**Step 6：实现逆序回滚和崩溃恢复**

提交前任何失败逆序恢复 hold；有效未完成 journal 下次先收敛，损坏/不可判定 journal fail-closed；提交后只清理。

**Step 7：实现 main 与固定输出**

只接受显式备份目录和明确 apply；不提供任何 skip/force。成功返回恢复前备份路径和固定摘要，失败只输出固定首行。

**Step 8：临时故障注入自测与回执**

只在 TEMP 假仓运行最小正路、每根故障、回滚故障和重入；`compileall`、diff-check 后发 `review_request`，不提交。

## 4. Task 4：Grok A 实现 Windows 包装

**文件：**

- 新建：`Restore-Biaoshu.bat`
- 新建：`tools/v1-ops/Restore-Biaoshu.ps1`

**Step 1：根 bat 固定转发**

只转发备份目录到固定 PS1，不内嵌恢复业务，不提供默认最近备份。

**Step 2：PS1 显式确认**

任何 Python apply 前要求输入精确 `恢复`；取消固定零写入。参数和 stdout/stderr 处理兼容 PowerShell 5.1、空格/中文路径。

**Step 3：编码与解析**

把 PS1 保存为 UTF-8 BOM；Parser 必须 0 错误。失败中文不可乱码，输出不得泄漏业务文件或参数。

**Step 4：临时假 core 自测与回执**

用 TEMP 中假 Python core 验证 argv、确认、退出码和中文；不得调用真实恢复。发送最终 A `review_request`，不提交。

## 5. Task 5：Codex 组合、独立审查和双方确认返修

**文件组合：** 严格六个代码/测试文件，任何额外文件先拒绝。

**Step 1：核对 A/B worktree**

检查基线、白名单、哈希、状态和消息链；只把精确交付文件复制到主工作区，不合并分支历史。

**Step 2：先运行 B 的 failure-first 证据复核**

failure-first 必须来自无生产实现的 B worktree；不能用组合后的绿测替代。

**Step 3：静态独立审查**

逐项审查格式/数据版本分离、根状态、物理集合、非默认路径、pre-backup、journal intent/result、提交点、回滚、崩溃重入、PS 确认与脱敏。

**Step 4：串行专项**

```powershell
& .\backend\.venv\Scripts\python.exe .\tools\v1-ops\test_biaoshu_backup.py
& .\backend\.venv\Scripts\python.exe .\tools\v1-ops\test_biaoshu_restore.py
```

**Step 5：反假绿手工夹具**

在全新 TEMP 组合目录执行：同大小篡改、v1、错误 compat、roots 混合态、每根第 N 次故障、回滚自身故障、崩溃 journal、semantic not_included、非默认 `.env`、祖先 junction、中文/空格 PS。

**Step 6：疑似问题确认流程**

Codex 只发 `question`；Grok 独立回复存在/不存在。双方确认存在后，Codex 才按文件级白名单发 `task` 授权最小返修。返修后重新运行受影响专项，不自动扩大测试。

## 6. Task 6：Codex 最终门、提交与文档闭环

**Step 1：最终静态门**

```powershell
& .\backend\.venv\Scripts\python.exe -m compileall -q .\tools\v1-ops\biaoshu_backup.py .\tools\v1-ops\biaoshu_restore.py .\tools\v1-ops\test_biaoshu_backup.py .\tools\v1-ops\test_biaoshu_restore.py
git diff --check
git status --short
```

另验证 Restore PS1 `EF-BB-BF` 和 Parser 0 错误；主工作区不得有临时备份、恢复工作根或真实数据副本。

**Step 2：只暂存六个代码/测试文件并中文提交**

建议提交信息：`实现：完成V1B离线恢复与回滚演练`

**Step 3：更新生产文档**

更新契约状态、计划结果、联调清单、路线图和主交接；记录 failure-first、双方问题确认、最终测试、文件哈希和明确未运行项。

**Step 4：文档提交并推送**

建议提交信息：`文档：闭环V1B离线恢复与回滚演练`

只推送 `collab/grok-code-codex-review`，核对本地 HEAD=远端且工作区/暂存区为空；严禁操作 `main`。

## 7. 最终执行结果

- failure-first：备份 `56 passed / 9 failed`，恢复 `1 passed / 41 failed`；0 收集错误、0 skipped、0 xfail。
- 双确认返修：生产 A1-A15、测试 B1-B12 全部按 `question -> confirm -> task -> review_request` 闭环；Grok A/B 全程未暂存、提交或推送。
- 最终主仓：备份 `65/65 passed`，恢复 `81/81 passed`；Python 四文件编译、Restore PS1 BOM/Parser、diff-check 均通过。
- Codex 独立探针：A13 restore 后/result 前重入、A14 hold intent 未生效、A15 不一致 installed 终态均通过。
- 代码提交：`20a4a60 实现：完成V1B离线恢复与回滚演练`。
- 未运行：后端全量、前端、Playwright、整仓 E2E；未触碰真实业务库、uploads、密钥或真实服务进程。
- 下一步：重新只读审计并冻结 V1 自动解析生产可部署性与真实 TEMP 样本验收；不得沿用 V1-B 六文件白名单。
