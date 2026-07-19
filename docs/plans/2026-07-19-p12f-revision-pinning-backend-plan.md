# P12F-J-A 修订固定与裁剪保护后端实施计划

> **执行者：Grok**：严格九文件；先形成真实固定入口/列/裁剪红测，再实现后端列、迁移、服务与 PATCH；第九文件只允许一行机械字段清单同步；只自测并通过消息箱请求审查，不暂存、不提交、不推送。
> **状态：** 2026-07-19 已完成实现、独立验收、提交与推送；冻结=`2f03b8c`，实现=`a7021c4`，Grok review_request=`msg_88f4752ef1cf4a929c6b194df00d9398`，Codex ack=`msg_c630805296ac48d6941809bbca957b7f`。

**目标：** 为自动修订账本增加服务端固定状态，使已固定行不被自动裁剪，同时提供单条受限 PATCH；不改现有六键历史响应和前端。

## 1. 实施顺序

1. 第一阶段只改 `backend/tests/test_p12f_revision_pin.py` 与 `backend/tests/test_editor_state_revisions.py`：真实 ASGI PATCH 期待 200、固定/取消幂等、5 条/10 MiB 上限、固定旧行保护、20/20 MiB 非固定裁剪、坏元数据整次失败、跨项目/required/CSRF/零写与迁移列证据。生产六文件哈希必须保持冻结。
2. 第二阶段实现 `is_pinned` ORM 列和 SQLite 幂等迁移，所有存量默认 0；新增行默认 0，不改现有历史序列。
3. 第三阶段把 `_trim_revisions` 改为“先完整验证 → 固定集合全保留 → 最新非固定前缀补足 → 一次限定 DELETE”，并证明固定上限预留空间不会阻断既有 transition。
4. 第四阶段新增 pin service 与精确 PATCH 路由/Schema，复用现有 workspace、bid_writer、CSRF、no-store 和固定错误映射模式；禁止引入前端 API 或历史响应键变化。

## 2. 受限审查重点

1. 检查新增列真实为 `BOOLEAN NOT NULL DEFAULT 0` 且有 0/1 CHECK，旧八来源 SQLite 迁移、二次启动和失败回滚不丢行/索引/FK。
2. 检查服务锁后读取目标和固定集合；同值请求不扩大配额；超限 409 前后固定集合、editor-state、检查点、任务和项目域零变化。
3. 检查裁剪查询不投影 `snapshot_json`，先校验全部 `snapshot_bytes/is_pinned`，固定旧行形成空洞时仍保持固定集合和总配额；禁止 OFFSET/COUNT/LIKE/JSON SQL/N+1/跨作用域 DELETE。
4. 检查 PATCH query/body 精确边界、错误优先级、响应一键、required 鉴权/CSRF、跨项目脱敏；禁止 ID/版本/正文/路径/异常原文/请求体泄漏。
5. 检查现有 list/page/search/detail 仍精确六键，`display_name`、游标、来源/时间、删除、恢复和前端 parser 未被改动。

## 3. 串行验收与交付

Grok 完成后发送 `review_request`，报告真实 failure-first、精确九文件、固定上限/裁剪 SQL/锁/回滚/鉴权证据、测试数字/耗时、哈希、风险与未做项。Codex 先逐文件静态审查，再严格串行运行契约第 6 节专项、回归、全量、编译和静态门；全部通过后才中文提交实现、推送，并更新契约/计划/交接/路线图/联调清单。

首轮后端全量为 **2 failed / 1160 passed**：历史测试的裸 INSERT 因 ORM 缺少服务端默认失败，Grok 在原生产白名单内补上 `server_default=0`；剩余唯一失败是旧删除测试字段清单，按冻结 SHA-256 `E1CE8CBA925022EC6202146879557DC570DE87FB73ADE78A68705BAC7CD1529E` 仅增加 `is_pinned` 一项。随后发现并修复 SQLite Boolean 把原始非法 `2` 转成 `True` 的严格校验缺陷，新增坏元数据/execute/迁移中途回滚证据。最终 Grok 串行 **16/96/1/1165 passed**，Codex 独立串行 **16/96/1/1165 passed**；py_compile、diff-check、九文件边界、原始 `type_coerce(Integer)`、无正文投影、无 `is_(True)` 绕过均通过。

## 4. 交付后下一包边界

P12F-J-A 完成后，固定状态仍不会显示在 list/page/search/detail，前端不能操作固定；下一包必须另立 P12F-J-B 契约，扩展七键元数据、API parser、技术/商务共用固定按钮、加载/失败/迟到隔离和 E2E。不得在 J-A 中顺手加入批量固定、固定排序、检查点命名或裁剪配额改写。
