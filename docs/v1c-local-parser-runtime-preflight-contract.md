<!--
模块：V1-C 本机解析运行时预检契约
用途：冻结 MinerU 默认路径与 Docling 可选路径的诚实预检、零回调 dry-run、合成样本真值门和固定诊断码。
对接：tools/local-parser/runtime_preflight.py、P8D/P8E 外置解析助手、V1 本机/内网发布门。
二次开发：禁止自动安装、下载模型、读取真实标书、签发或消费回调票据；真实运行只能由用户显式选择合成样本门。
-->

# V1-C 本机解析运行时预检契约

> **状态：已完成、独立验收并推送。** 冻结=`6e7aafb`，实现=`21d3213`。
> **分支：** 仅 `collab/grok-code-codex-review`，严禁操作 `main`。
> **基线：** V1-B 闭环提交 `8c92228`；本机审计时 `mineru`、`docling`、`docling-tools` 均不存在。

## 1. 目标与诚实口径

V1-C 不负责安装解析器，也不宣称真实解析已可用。它只交付一个标准库预检入口，使管理员能在不接触业务数据、不签票、不回调的前提下得到可机器判断的结论：

1. MinerU 默认路径的安全可执行文件是否存在；
2. Docling 可选路径的安全可执行文件和本地 artifacts 目录是否存在；
3. 静态检查通过是否仅代表“可进入运行验收”，而非模型或解析质量已验证；
4. 用户显式选择时，已安装解析器能否在强制离线边界内解析仓库代码生成的合成 DOCX，并找回固定锚点；
5. 任何失败都有固定诊断码，且不输出绝对路径、解析正文、第三方原始日志或敏感环境。

历史 MinerU `54 passed`、Docling `46 passed` 只证明假 CLI 下的包装安全边界；它们不是本机 CLI、模型或解析质量的生产证据。

## 2. 产品选择

- V1 默认解析器为 **MinerU**。
- Docling 代码继续保留，作为管理员显式选择的第二解析器；V1 不要求安装。
- 后端 `lightweight` 与 P8B/P8C/P8D/P8E 既有协议不变。
- 本包不修复 lightweight 对 DOCX 表格丢失的问题；该质量缺口必须另立能力包。

## 3. 唯一入口与模式

新增：`tools/local-parser/runtime_preflight.py`。

CLI 必须显式选择且只能选择一个模式：

```powershell
backend\.venv\Scripts\python.exe tools\local-parser\runtime_preflight.py --engine mineru --dry-run
backend\.venv\Scripts\python.exe tools\local-parser\runtime_preflight.py --engine mineru --synthetic-check
backend\.venv\Scripts\python.exe tools\local-parser\runtime_preflight.py --engine docling --artifacts-path "D:\models\docling" --dry-run
backend\.venv\Scripts\python.exe tools\local-parser\runtime_preflight.py --engine docling --artifacts-path "D:\models\docling" --synthetic-check
```

参数契约：

- `--engine` 仅允许精确 `mineru|docling`，默认值为 `mineru`。
- `--dry-run` 与 `--synthetic-check` 互斥且必选其一。
- Docling 必须提供 `--artifacts-path`；MinerU 提供该参数必须拒绝，禁止含混忽略。
- 不提供 input、ticket、origin、下载、联网、跳过、宽松可执行文件、自定义命令或额外 CLI 参数。
- 参数错误不得回显用户原始值或绝对路径。

## 4. `dry-run` 边界

`dry-run` 只允许：

- 复用 P8D/P8E 的可执行文件解析规则；Windows 只接受普通非符号链接 `.exe`；
- Docling 复用 artifacts 目录校验；
- 在内存中构造脱敏后的固定命令形态并验证参数契约；
- 输出有界 JSON 结果。

`dry-run` 严禁：

- 启动任何解析器子进程；
- 创建或读取业务输入文件；
- 读取票据、Cookie、数据库、uploads、`.env` 或模型内容；
- 发起 HTTP 请求或回调；
- 宣称模型、OCR、表格、版式或解析质量通过。

成功结果必须含 `runtimeVerified=false`，中文消息明确为“静态检查通过，尚未运行解析器”。

## 5. `synthetic-check` 真值门

只有用户显式传入 `--synthetic-check` 才能启动真实解析器。流程固定为：

1. 在系统 TEMP 创建 `biaoshu-parser-preflight-*` 临时根；
2. 仅用 Python 标准库生成最小合法 DOCX，正文含固定 ASCII 锚点 `SYNTH_BID_SAMPLE_V1`；
3. 复用对应助手的离线环境、固定 argv、超时与直接子进程终止逻辑；
4. 输出只允许写入该临时根；
5. 复用唯一 Markdown、有界 UTF-8、输出树上限校验；
6. Markdown 必须包含精确锚点，否则固定失败；
7. 无论成功、失败、超时或中断都清理临时根；
8. 全流程不读取票据、不调用 `post_callback`、不发网络请求。

成功只表示“本机当前 CLI 与本地模型能解析此合成 DOCX 并保留锚点”，不等于真实 PDF、扫描件、表格或整本标书质量已验收。

## 6. 固定诊断码与退出码

JSON 顶层仅允许固定字段：`ok`、`engine`、`mode`、`diagnosticCode`、`message`、`runtimeVerified`。不得包含绝对路径、命令全文、异常类名、第三方 stdout/stderr 或 Markdown。

| `diagnosticCode` | 场景 | 退出码 |
|---|---|---:|
| `static_ready` | dry-run 静态检查通过 | 0 |
| `synthetic_passed` | 合成样本解析并命中锚点 | 0 |
| `argument_invalid` | 参数组合或枚举非法 | 2 |
| `cli_missing` | CLI 不存在或安全类型不合格 | 2 |
| `artifacts_invalid` | Docling artifacts 缺失或非法 | 2 |
| `parser_failed` | 子进程启动或返回失败 | 2 |
| `parser_timeout` | 子进程超时 | 2 |
| `output_invalid` | Markdown 零个、多个、越界或非法 | 2 |
| `sample_marker_missing` | 合成输出不含固定锚点 | 2 |
| `interrupted` | 用户中断 | 130 |
| `internal_error` | 未预期受控兜底 | 1 |

固定映射必须在调用边界完成，禁止根据第三方错误字符串猜测或把原异常拼入响应。

## 7. 隐私、离线与副作用

- 预检只处理代码生成的合成 DOCX，绝不接受真实输入路径。
- 不访问后端，不签发、不读取、不消费一次性票据，不写 editor-state、任务、修订或审计表。
- 继承 P8D/P8E 的代理/API Key/Token 剥离和 HF/Transformers 离线变量。
- 无操作系统级网络沙箱、CPU/内存硬配额、孙进程完整回收保证；JSON 与 README 必须保持这一残余风险口径。
- 不自动安装、升级或下载 MinerU、Docling、模型、Python 包、GPU 驱动或 CUDA。
- 不新增配置文件，不把模型、缓存、临时输出或诊断日志提交进 Git。

## 8. 严格文件白名单

实现阶段只允许：

1. 新增 `tools/local-parser/runtime_preflight.py`；
2. 新增 `tools/local-parser/test_runtime_preflight.py`；
3. 修改 `tools/local-parser/README.md`。

冻结阶段只允许新增本契约和 `docs/plans/2026-07-21-v1c-local-parser-runtime-preflight-plan.md`。

禁止修改既有两个助手、后端、前端、数据库、启动脚本、依赖清单、Git 忽略规则和历史测试。若实现证明必须触达既有助手，先发 `question`，双方确认问题存在并修订契约后才能授权。

## 9. 测试与反假绿门

新专项必须使用标准库 `unittest`、TEMP 和注入/假 CLI，至少证明：

- 参数矩阵与静默错误；
- dry-run 零 `Popen`、零 `post_callback`、零票据读取；
- MinerU/Docling 缺 CLI 与 Docling artifacts 的固定码；
- 合成 DOCX 是合法 ZIP/OpenXML，锚点只来自生成器；
- 两引擎命中、缺锚点、零/多 Markdown、非零、超时、中断的映射；
- 子进程环境保持离线且不含代理、API Key、票据哨兵；
- JSON 精确键集合、退出码与无路径/无正文/无异常类名；
- 所有临时根在成功和每类失败后消失；
- 不通过 `skip`、`xfail`、环境缺失分支或宽松 `or` 把真实缺口变绿。

failure-first 只能先新增测试文件并运行；在生产脚本不存在时必须出现真实失败。不得伪造计数或补跑后改写原始证据。

## 10. 验收命令

所有命令串行执行，不得并发：

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_runtime_preflight.py" -v
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_mineru_callback_helper.py" -v
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_docling_callback_helper.py" -v
backend\.venv\Scripts\python.exe -m py_compile tools\local-parser\runtime_preflight.py
git diff --check
```

当前机器还必须独立运行：

```powershell
backend\.venv\Scripts\python.exe tools\local-parser\runtime_preflight.py --engine mineru --dry-run
```

因本机未安装 MinerU，预期是退出码 2、`diagnosticCode=cli_missing`；这项失败是正确生产真值，不是验收失败。禁止运行真实 `--synthetic-check`，除非用户另行明确授权并已人工准备 CLI/模型。

## 11. 完成定义与后续

V1-C 完成必须同时满足：严格三文件实现、专项与两个既有助手回归通过、当前机器缺 CLI 诚实失败、Codex 独立静态审查和验收、中文提交推送、五份生产文档闭环。

V1-C 完成后，下一包优先处理 lightweight DOCX 表格/标题结构和真实 PDF/DOCX 兜底质量，再进入标书内容制作与 Word 整章导出。真实解析器安装与授权样本 E2E 仍是现场管理员动作，不得由 Agent 夜间自动执行。

## 12. 实际完成证据

- Grok A/B 审计一致确认本机无 `mineru`、`docling`、`docling-tools`；审计回执=`msg_c0cc46b16eda49658fa39f7f7134a95a` / `msg_f69e01e47a0a4d8eac19dc11352686fb`。
- 原始 failure-first=`msg_df22e0b8ec6240cb904a12ff46496411`：生产脚本不存在，`0 passed / 1 import error / 24 did-not-run`。测试 B1-B7 均按 Codex question、Grok 确认、Codex task 后返修，未篡改原始红测。
- A1 精确 engine 枚举问题按 `msg_f70cf412d5074e629326fefdb7463857` / `msg_97548e7f2fea4232ae5d1b4f62a4d4ec` 双确认；新增红测为其余 `25 passed`、两个空白包围 subTest 失败，生产返修=`msg_82edfb16b4534366b6a5f1d820af4fba`，最终回执=`msg_44e4f19c9c7c4accac86d9507ff0692c`。
- Codex 独立串行通过新专项/MinerU/Docling `26/54/46 passed`，`py_compile`、`git diff --check`、严格三文件、空暂存与 SHA-256 门通过。
- 当前机器真实 `--engine mineru --dry-run` 返回 `diagnosticCode=cli_missing`、退出码 2；这是正确生产真值。未运行真实 `--synthetic-check`，未安装或下载 CLI/模型。
- 最终 SHA-256：README=`164740CFBCE0556D523683FF21B6BFBB95512160D4828601A685AAB60665A5DA`；runtime=`2B4A74AF57FF166865067369E2AAE0B6B220E3ACDFF1F743819CB9A4D6A7AD3F`；test=`1CA561C44F19E88C6F5A19A1231432BD05ABCC40143DC74A24085CEFCC158299`。
