/**
 * 模块：国能 e 招计划追踪面板 E2E
 * 用途：验收标讯页上传计划表、受控同步轮询、命中展示、人工加入本地标讯与既有 CSV 导入。
 * 对接：Playwright chromium；后端 8010（biaoshu-e2e.db + MockTransport）/ 前端 5174；/api/opportunity-watch。
 * 二次开发：禁止 sleep 作同步；禁止真实外网；勿改 response-matrix 等既有 spec。
 */
import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..");
const backendPython = path.join(
  repoRoot,
  "backend",
  ".venv",
  "Scripts",
  "python.exe",
);

function makeWatchPlanXlsx(): string {
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), "biaoshu-watch-"));
  const outPath = path.join(outDir, "e2e-watch-plans.xlsx");
  const script = `
from openpyxl import Workbook
wb = Workbook()
ws = wb.active
ws.append(["说明行：E2E 国能计划追踪"])
ws.append(["招标计划名称", "招标人", "范围", "计划工期", "预计发布公告时间", "备注"])
ws.append(["E2E可解析计划", "国能招标人甲", "甲供范围", "6个月", "2026年7月", ""])
ws.append(["E2E待复核计划", "国能招标人乙", "乙供范围", "3个月", "2026年7月", ""])
wb.save(r"${outPath.replace(/\\/g, "\\\\")}")
print(r"${outPath.replace(/\\/g, "\\\\")}")
`;
  execFileSync(backendPython, ["-c", script], {
    cwd: path.join(repoRoot, "backend"),
    encoding: "utf-8",
  });
  return outPath;
}

function makeLocalCsv(): string {
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), "biaoshu-opp-csv-"));
  const outPath = path.join(outDir, "e2e-local-opps.csv");
  const csv = [
    "标题,采购人,地区,预算,截止日期,标签,摘要,来源,来源键",
    "E2E本地CSV标讯,采购人CSV,华北,100万,2026-12-31,E2E,CSV摘要,CSV导入,e2e-csv-001",
  ].join("\n");
  fs.writeFileSync(outPath, "\uFEFF" + csv, "utf-8");
  return outPath;
}

async function openBidOpportunity(page: Page) {
  await page.goto("/bid-opportunity");
  await expect(page.getByRole("heading", { name: "标讯", exact: true })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByTestId("opportunity-watch-panel")).toBeVisible();
  await expect(
    page.getByText("国能 e 招候选公告，需人工确认；不会自动创建项目"),
  ).toBeVisible();
}

test.describe("国能计划追踪面板", () => {
  test("上传计划、同步命中、人工接受与既有导入", async ({ page }) => {
    const xlsxPath = makeWatchPlanXlsx();
    const csvPath = makeLocalCsv();

    await openBidOpportunity(page);

    // 1) 上传 .xlsx 计划表并显示计数
    await page.getByTestId("watch-plan-file").setInputFiles(xlsxPath);
    await page.getByTestId("watch-plan-import").click();
    await expect(page.getByTestId("watch-import-result")).toContainText(
      "导入 2 条",
      { timeout: 20_000 },
    );
    await expect(page.getByTestId("watch-plan-count")).toContainText("2");

    // 2) 同步：进入进行中（Mock 下可能极快），轮询终态后展示命中与北京时间
    const syncBtn = page.getByTestId("watch-sync");
    await expect(syncBtn).toBeEnabled();
    await syncBtn.click();
    await expect(page.getByTestId("watch-run-status")).toContainText(
      /正在同步|已完成|部分完成/,
      { timeout: 30_000 },
    );
    await expect(page.getByTestId("watch-run-status")).toContainText(/已完成|部分完成/, {
      timeout: 30_000,
    });

    const hitList = page.getByTestId("watch-hit-list");
    await expect(hitList.getByText("E2E 可解析招标公告")).toBeVisible({
      timeout: 15_000,
    });
    await expect(hitList.getByText("E2E 待复核招标公告")).toBeVisible();

    const resolvedHit = page.locator(
      '[data-testid^="watch-hit-"][data-extraction-status="resolved"]',
    ).first();
    const reviewHit = page.locator(
      '[data-testid^="watch-hit-"][data-extraction-status="needs_review"]',
    ).first();
    await expect(resolvedHit).toBeVisible();
    await expect(reviewHit).toBeVisible();

    const resolvedId = (await resolvedHit.getAttribute("data-testid"))?.replace(
      "watch-hit-",
      "",
    );
    const reviewId = (await reviewHit.getAttribute("data-testid"))?.replace(
      "watch-hit-",
      "",
    );
    expect(resolvedId).toBeTruthy();
    expect(reviewId).toBeTruthy();

    await expect(
      page.getByTestId(`watch-hit-deadline-${resolvedId}`),
    ).toContainText("2026-07-29 09:00:00（北京时间）");
    await expect(
      page.getByTestId(`watch-hit-status-${resolvedId}`),
    ).toContainText("待人工确认");

    // 外链安全属性
    const noticeLink = page.getByTestId(`watch-hit-link-${resolvedId}`);
    await expect(noticeLink).toHaveAttribute("target", "_blank");
    await expect(noticeLink).toHaveAttribute("rel", "noreferrer");
    const href = await noticeLink.getAttribute("href");
    expect(href).toMatch(
      /^https:\/\/www\.chnenergybidding\.com\.cn\/bidweb\//,
    );

    // 3) 仅 resolved 显示接受按钮；needs_review 不显示
    const acceptBtn = page.getByTestId(`watch-hit-accept-${resolvedId}`);
    await expect(acceptBtn).toBeVisible();
    await expect(
      page.getByTestId(`watch-hit-accept-${reviewId}`),
    ).toHaveCount(0);

    // 4) 加入本地标讯；重复点击不得重复
    await acceptBtn.click();
    await expect(
      page.getByTestId(`watch-hit-accepted-${resolvedId}`),
    ).toBeVisible({ timeout: 15_000 });
    await expect(
      page.getByTestId(`watch-hit-accept-${resolvedId}`),
    ).toHaveCount(0);

    const localList = page.getByTestId("local-opportunity-list");
    await expect(localList.getByText("E2E 可解析招标公告")).toBeVisible({
      timeout: 15_000,
    });
    await expect(localList.getByText("国能 e 招计划追踪")).toBeVisible();
    const localCards = localList.locator("article.opp-card");
    await expect(localCards).toHaveCount(1);

    // 已接受后无接受按钮；仪表盘仍只一条 resolved 本地标讯
    await expect(
      page.getByTestId(`watch-hit-accept-${resolvedId}`),
    ).toHaveCount(0);
    await expect(localCards).toHaveCount(1);

    // 5) 既有 CSV 导入入口仍可用
    await page.getByRole("button", { name: "导入标讯" }).click();
    const importDialog = page.getByRole("dialog", { name: "导入标讯" });
    await expect(importDialog).toBeVisible();
    await importDialog.getByTestId("local-import-file").setInputFiles(csvPath);
    await importDialog.getByRole("button", { name: "导入", exact: true }).click();
    await expect(importDialog.getByText(/导入 1 条/)).toBeVisible({
      timeout: 15_000,
    });
    await importDialog.getByRole("button", { name: "取消" }).click();
    await expect(localList.getByText("E2E本地CSV标讯")).toBeVisible({
      timeout: 15_000,
    });
    await expect(localCards).toHaveCount(2);

    // 面板不得出现任意 URL 输入框
    await expect(
      page.locator('#opportunity-watch-panel input[type="url"], [data-testid="opportunity-watch-panel"] input[name*="url" i]'),
    ).toHaveCount(0);
  });
});
