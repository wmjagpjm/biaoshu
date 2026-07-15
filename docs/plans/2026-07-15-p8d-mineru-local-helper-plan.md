<!--
模块：P8D 本机 MinerU 外置解析助手实施计划
用途：把 P8D 契约拆为三文件纯工具实现包，供 Grok 实现、Codex 独立审查与验收。
对接：docs/p8d-mineru-local-helper-contract.md；P8C 一次性回传票据；Grok-Codex 协作消息箱。
二次开发：严格三文件白名单；Grok 不提交推送；任何后端、前端、依赖或安装器需求必须先提问。
-->

# P8D 本机 MinerU 外置解析助手实施计划

> **状态**：只读审计完成，计划已冻结；尚未派发。
> **工作分支**：`collab/grok-code-codex-review`。
> **基线**：P11C 实现=`1441509`、文档闭环=`f4deade`；后端 487 passed，前端全量 E2E 184 passed。

## 1. 精确文件白名单

Grok 仅允许新增：

1. `tools/local-parser/mineru_callback_helper.py`
2. `tools/local-parser/test_mineru_callback_helper.py`
3. `tools/local-parser/README.md`

不得修改后端、前端、现有测试、requirements/锁文件、启动脚本、`.env.example`、P8/P8B/P8C 文档或 Git 配置。不得新增 PowerShell、批处理、安装器、依赖清单、二进制、模型、fixture 文档或输出产物。若三文件无法满足，先通过消息箱发 `question`。

## 2. 实现顺序

1. 先写失败单元测试，覆盖输入、Origin、进程、离线环境、输出发现、票据、回调、重定向和脱敏边界；测试不得访问真实网络、PATH 中真实 MinerU 或浏览器。
2. 将助手拆成可单测的纯函数：输入验证、Origin 归一化、MinerU 命令构造、受控子进程、Markdown 发现/读取、无代理无重定向回调，以及最薄的 `main()`。
3. `main()` 只解析 `--input` 与可选 `--backend-origin`；票据只由 `getpass.getpass` 读取，不能提供任何替代来源。
4. 子进程用参数数组和 `shell=False`；固定 PATH 命令、pipeline、30 分钟、离线环境、代理剥离、stdout/stderr 丢弃与 finally 清理。任何异常转为固定中文和非零退出码。
5. HTTP 仅标准库，固定回环 Origin/路径、无代理、无重定向、一次请求；成功前严格校验 P8C 响应，不打印 taskId、detail 或输入路径。
6. README 用中文写清前置人工安装/模型准备、P8C 签票、复制即用命令、离线边界、资源需求由官方文档决定、失败排查、临时文件和票据规则；不得承诺自动安装或百分百清理 MinerU 自建孙进程。

## 3. 反假绿要求

- fake MinerU 必须真实证明收到精确 argv、`shell=False` 语义、固定环境白名单与离线变量，并证明父进程代理/API Key/任意哨兵变量均未继承；不能只 mock 最终 Markdown。
- fake HTTP 必须观测 method、精确 URL、Header、UTF-8 JSON、请求次数；至少一个 302 指向非回环的场景必须证明零跟随。
- 票据哨兵必须同时检查 argv、子进程 env、临时目录文件名/内容、stdout/stderr、助手输出和异常文本。
- 超时与 KeyboardInterrupt 必须证明 terminate/kill 路径和临时目录清理；不得固定 sleep 等完成。
- 50 MiB 与 2 MiB 上下界用稀疏/构造数据，不在仓库落大 fixture；Markdown 码点上限和 JSON 字节上限分别断言。
- 禁止 `skip`、条件跳过、真实 MinerU 探测成功才测试、吞异常、宽泛 `assert truthy` 或只断言退出码。

## 4. Grok 自测与回报

```powershell
cd C:\Users\Administrator\biaoshu
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_*.py" -v
git diff --check
```

Grok 完成后只发 `review_request`，报告原任务 ID、精确三文件、失败先测证据、测试数、命令结果、网络/票据/临时文件/进程风险和明确未做项；不得 git add、commit 或 push。

## 5. Codex 独立验收

```powershell
cd C:\Users\Administrator\biaoshu
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_*.py" -v

cd backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_local_parser_callback_tickets.py tests/test_async_and_callback.py tests/test_parse_engines.py tests/test_parse_strategy_read.py

cd ..\frontend
npm run lint
npm run build
npm run test:e2e:local-parser-callback-ticket -- --workers=1
npm run test:e2e:parse-strategy -- --workers=1

cd ..
git diff --check
```

Codex 必须逐条审查契约 §3～§5，尤其是票据来源、Origin 解析、代理/重定向、子进程参数、超时终止、原始输出脱敏和临时目录逃逸。实现通过后单独中文提交并推送，再更新路线图、联调清单和主交接；P8D 不改变后端 487/前端 184 的全量基线，除非独立验收实际重跑全量。

## 6. 明确后续

Docling 不能在本包借用 `source=mineru`。若 P8D 通过，后续 P8E 才可只读审计 P8C source 枚举扩展、Docling CLI 的本地/插件/远程服务关闭参数、输出格式与独立测试，不得提前合包。
