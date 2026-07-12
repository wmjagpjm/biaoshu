/**
 * 模块：Playwright E2E 配置
 * 用途：单 worker headless Chromium；启动隔离后端 8010 + 前端 5174。
 * 对接：npm run test:e2e / test:e2e:matrix；backend/scripts/e2e_reset_db.py
 * 二次开发：禁止默认污染 8000/5173 与日用/pytest 库；勿用固定 sleep 作同步。
 */
import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const backendRoot = path.join(repoRoot, "backend");
const backendPython = path.join(backendRoot, ".venv", "Scripts", "python.exe");

const e2eDbUrl = "sqlite:///./data/biaoshu-e2e.db";
const backendEnv = {
  ...process.env,
  DATABASE_URL: e2eDbUrl,
  DEFAULT_WORKSPACE_ID: "ws_e2e",
  DEFAULT_WORKSPACE_NAME: "E2E 工作空间",
  DEFAULT_OWNER_USER_ID: "user_e2e",
  SEED_SAMPLE_OPPORTUNITIES: "false",
};

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  outputDir: "test-results",
  use: {
    baseURL: "http://127.0.0.1:5174",
    headless: true,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      name: "backend-e2e",
      command: `"${backendPython}" scripts/e2e_reset_db.py && "${backendPython}" -m uvicorn app.main:app --host 127.0.0.1 --port 8010`,
      cwd: backendRoot,
      url: "http://127.0.0.1:8010/api/health",
      // 必须新建进程，否则可能复用日用 8010/错误库或未注入 E2E 环境变量
      reuseExistingServer: false,
      timeout: 120_000,
      env: backendEnv,
    },
    {
      name: "frontend-e2e",
      command: "npx vite --host 127.0.0.1 --port 5174 --strictPort",
      cwd: __dirname,
      url: "http://127.0.0.1:5174",
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ...process.env,
        VITE_API_PROXY_TARGET: "http://127.0.0.1:8010",
      },
    },
  ],
});
