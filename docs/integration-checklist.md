# 前后端联调清单

> 目标：验证 health / 项目 / 设置 / revise / editor-state 已闭环。  
> Key **明文**存储与回显（保密机决策）。

## 1. 一键启动

```text
仓库根目录双击：Start-Biaoshu-Dev.bat
```

若双击「无反应」或窗口一闪就关：

1. 确认点的是 **`Start-Biaoshu-Dev.bat`**（不是 `.url` 快捷方式）
2. 右键 bat → **以管理员身份运行**（一般不需要）
3. 或用备用脚本：右键 **`Start-Biaoshu-Dev.ps1`** → 使用 PowerShell 运行  
   （若提示禁止脚本：在 PowerShell 执行  
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`）
4. 仍失败时在资源管理器地址栏输入 `cmd` 回车，再执行：  
   `cd /d C:\Users\Administrator\biaoshu`  
   `Start-Biaoshu-Dev.bat`  
   看窗口报错文字

成功时会**额外弹出两个黑窗口**（Biaoshu-API / Biaoshu-Vite），启动器窗口也会 `pause` 停住。

或分别启动：

```powershell
# 后端
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# 前端
cd C:\Users\Administrator\biaoshu\frontend
npm run dev
```

- 前端：http://127.0.0.1:5173/create  
- 后端探活：http://127.0.0.1:8000/api/health  
- 开发代理：Vite 将 `/api` → `8000`（无需配置 CORS 也可）

## 2. 界面观测

| 检查点 | 期望 |
|--------|------|
| 左侧栏底部 API 状态点 | 绿 = 在线；红 = 离线 |
| 设置页保存 | 刷新后 Key **明文**仍在 |
| 设置「测试模型连通」 | 成功回显模型回复，或明确错误 detail |
| 我的项目 | 数据来源条显示「后端 API」 |
| 工作区标题旁 | 编辑持久化：后端 |

联调纯列表时，可在 `frontend/.env.local`：

```env
VITE_MERGE_MOCK_PROJECTS=false
```

## 3. 冒烟脚本（无外网 LLM）

```powershell
# 先起 uvicorn，再：
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python scripts\smoke_e2e.py
```

期望输出末尾：`OK smoke e2e`。

## 4. 手工验收路径

1. **创建** → 创建技术标项目 → 进入工作区  
2. **刷新**「我的项目」→ 新项目仍在  
3. **分析步**改概述 → 刷新页面 → 概述仍在（editor-state）  
4. **设置**填真实 Key → 测试连通  
5. **分析步**反馈面板提交意见 → history 有摘要 → 「修订结果预览」→ 可替换概述  
6. **停掉后端** → 状态变红；列表提示本地兜底，不白屏  

## 5. 自动化测试

```powershell
cd backend
.\.venv\Scripts\python -m pytest -q

cd ..\frontend
npm run build
```

## 6. 已接 API 一览

| 方法 | 路径 |
|------|------|
| GET | `/api/health` |
| GET/POST | `/api/projects` |
| GET/PATCH/DELETE | `/api/projects/{id}` |
| GET/PUT | `/api/projects/{id}/editor-state` |
| GET/PUT | `/api/settings` |
| POST | `/api/llm/test` |
| POST | `/api/projects/{id}/artifacts/{artifactId}/revise` |

## 7. 本机日用主链路（目标 A，已接）

| 步骤 | 操作 |
|------|------|
| 上传 | document 步选择 PDF/DOCX/TXT |
| 解析 | 「轻量解析」→ `POST .../tasks` type=parse |
| 分析 | 「AI 招标分析」（需设置页 Key） |
| 大纲 | 「AI 生成大纲」 |
| 正文 | 选章 → 「AI 生成本章」 |
| 导出 | 「生成并下载 Word」 |

## 8. 仍未接（后续）

异步 SSE/Worker、MinerU 回传、知识库 RAG、商务标/查重/废标 API、多用户鉴权。
