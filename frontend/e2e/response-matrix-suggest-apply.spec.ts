/**
 * 模块：响应矩阵智能建议人工确认 E2E
 * 用途：验证 response_match 只读产出待确认建议，勾选应用后才持久化映射；部分勾选、notes 保护与 base 漂移跳过。
 * 对接：Playwright chromium；后端 8010 / 前端 5174；本文件内 OpenAI-compatible mock LLM。
 * 二次开发：禁止 fixed sleep、真实 Key/外网；不测多批分页、取消中断、409 与建议交叉；勿改业务 src/backend。
 */
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import http from "node:http";
import type { AddressInfo } from "node:net";

const API = "http://127.0.0.1:8010/api";

const SOURCE_A = "E2E建议要求甲";
const SOURCE_B = "E2E建议要求乙";
const SOURCE_C = "E2E建议要求丙";

const NOTES_A = "备注甲-必须保留";
const NOTES_B = "备注乙-必须保留";
const NOTES_C = "备注丙-必须保留";

const CHAP_A = "chap_suggest_a";
const CHAP_B = "chap_suggest_b";
const NODE_A = "node_suggest_a";
const NODE_B = "node_suggest_b";

type MatrixRow = {
  id: string;
  kind: string;
  sourceKey: string;
  sourceIndex: number;
  sourceText: string;
  weight: string;
  chapterIds: string[];
  outlineNodeIds: string[];
  status: string;
  notes: string;
};

type EditorState = {
  responseMatrix?: MatrixRow[];
  responseMatrixVersion?: string;
};

/** 用途：与前端 makeResponseMatrixSourceKey 保持一致的小写规范化键。 */
function requirementSourceKey(sourceText: string): string {
  return `requirement:${sourceText.trim().replace(/\s+/g, " ").toLocaleLowerCase()}`;
}

const KEY_A = requirementSourceKey(SOURCE_A);
const KEY_B = requirementSourceKey(SOURCE_B);
const KEY_C = requirementSourceKey(SOURCE_C);

/**
 * 用途：本机 OpenAI 兼容 mock，按固定 sourceKey 返回可应用映射建议。
 * 对接：PUT /api/settings apiBaseUrl；response_match → chat/completions。
 */
async function startMockLlmServer(): Promise<{
  baseUrl: string;
  close: () => Promise<void>;
}> {
  const server = http.createServer((req, res) => {
    if (req.method === "POST" && (req.url || "").includes("chat/completions")) {
      const chunks: Buffer[] = [];
      req.on("data", (c) => chunks.push(c));
      req.on("end", () => {
        const body = JSON.stringify({
          id: "chatcmpl-e2e-matrix-suggest",
          object: "chat.completion",
          model: "e2e-mock-matrix",
          choices: [
            {
              index: 0,
              message: {
                role: "assistant",
                content: JSON.stringify(
                  [
                    {
                      sourceKey: KEY_A,
                      chapterIds: [CHAP_A],
                      outlineNodeIds: [NODE_A],
                      status: "covered",
                      confidence: 91,
                      reason: "E2E mock：甲映射架构章",
                    },
                    {
                      sourceKey: KEY_B,
                      chapterIds: [CHAP_B],
                      outlineNodeIds: [NODE_B],
                      status: "covered",
                      confidence: 87,
                      reason: "E2E mock：乙映射安全章",
                    },
                    {
                      sourceKey: KEY_C,
                      chapterIds: [CHAP_A],
                      outlineNodeIds: [NODE_A],
                      status: "partial",
                      confidence: 82,
                      reason: "E2E mock：丙映射架构（供 base 漂移）",
                    },
                  ],
                  null,
                  0,
                ),
              },
              finish_reason: "stop",
            },
          ],
        });
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(body);
      });
      return;
    }
    res.writeHead(404);
    res.end("not found");
  });

  await new Promise<void>((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve());
  });
  const addr = server.address() as AddressInfo;
  const baseUrl = `http://127.0.0.1:${addr.port}/v1`;
  return {
    baseUrl,
    close: () =>
      new Promise<void>((resolve, reject) => {
        server.close((err) => (err ? reject(err) : resolve()));
      }),
  };
}

function matrixItem(page: Page, sourceText: string) {
  return page
    .locator("article.response-matrix__item")
    .filter({ hasText: sourceText });
}

function snapshotMatrix(rows: MatrixRow[] | undefined) {
  return (rows ?? [])
    .map((r) => ({
      sourceText: r.sourceText,
      status: r.status,
      notes: r.notes,
      chapterIds: [...(r.chapterIds || [])].sort(),
      outlineNodeIds: [...(r.outlineNodeIds || [])].sort(),
    }))
    .sort((a, b) => a.sourceText.localeCompare(b.sourceText));
}

async function getMatrixSnapshot(
  request: APIRequestContext,
  projectId: string,
) {
  const got = await request.get(`${API}/projects/${projectId}/editor-state`);
  expect(got.ok()).toBeTruthy();
  const body = (await got.json()) as EditorState;
  return snapshotMatrix(body.responseMatrix);
}

async function seedSuggestProject(
  request: APIRequestContext,
  mockBase: string,
): Promise<string> {
  const settings = await request.put(`${API}/settings`, {
    data: {
      provider: "openai-compatible",
      apiBaseUrl: mockBase,
      apiKey: "e2e-local-mock",
      model: "e2e-mock-matrix",
    },
  });
  expect(settings.ok()).toBeTruthy();

  const created = await request.post(`${API}/projects`, {
    data: { name: "E2E 智能建议确认", kind: "technical" },
  });
  expect(created.ok()).toBeTruthy();
  const project = (await created.json()) as { id: string };

  const matrix: MatrixRow[] = [
    {
      id: "mx_suggest_a",
      kind: "requirement",
      sourceKey: KEY_A,
      sourceIndex: 0,
      sourceText: SOURCE_A,
      weight: "",
      chapterIds: [],
      outlineNodeIds: [],
      status: "uncovered",
      notes: NOTES_A,
    },
    {
      id: "mx_suggest_b",
      kind: "requirement",
      sourceKey: KEY_B,
      sourceIndex: 1,
      sourceText: SOURCE_B,
      weight: "",
      chapterIds: [],
      outlineNodeIds: [],
      status: "uncovered",
      notes: NOTES_B,
    },
    {
      id: "mx_suggest_c",
      kind: "requirement",
      sourceKey: KEY_C,
      sourceIndex: 2,
      sourceText: SOURCE_C,
      weight: "",
      chapterIds: [],
      outlineNodeIds: [],
      status: "uncovered",
      notes: NOTES_C,
    },
  ];

  const put = await request.put(`${API}/projects/${project.id}/editor-state`, {
    data: {
      outline: [
        { id: NODE_A, title: "大纲架构", children: [] },
        { id: NODE_B, title: "大纲安全", children: [] },
      ],
      chapters: [
        { id: CHAP_A, title: "章节架构" },
        { id: CHAP_B, title: "章节安全" },
      ],
      analysis: {
        overview: "E2E 智能建议概述",
        techRequirements: [SOURCE_A, SOURCE_B, SOURCE_C],
        rejectionRisks: [],
        scoringPoints: [],
      },
      responseMatrix: matrix,
    },
  });
  expect(put.ok()).toBeTruthy();
  const body = (await put.json()) as EditorState;
  expect(Array.isArray(body.responseMatrix) && body.responseMatrix.length).toBe(
    3,
  );
  return project.id;
}

async function openMatrixPage(page: Page, projectId: string) {
  await page.goto(`/technical-plan/${projectId}/analysis`);
  await expect(
    page.getByRole("region", { name: "响应矩阵" }).or(
      page.locator("section.response-matrix"),
    ),
  ).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(SOURCE_A)).toBeVisible();
  await expect(page.getByText(SOURCE_B)).toBeVisible();
  await expect(page.getByText(SOURCE_C)).toBeVisible();
}

/** 用途：条件轮询 GET editor-state，直到断言通过（禁止 fixed sleep）。 */
async function waitForEditorMatrix(
  request: APIRequestContext,
  projectId: string,
  assertion: (rows: MatrixRow[]) => boolean | void,
  timeout = 20_000,
) {
  await expect
    .poll(
      async () => {
        const got = await request.get(
          `${API}/projects/${projectId}/editor-state`,
        );
        expect(got.ok()).toBeTruthy();
        const body = (await got.json()) as EditorState;
        const rows = body.responseMatrix ?? [];
        try {
          const ok = assertion(rows);
          return ok === false ? null : "ok";
        } catch {
          return null;
        }
      },
      { timeout },
    )
    .toBe("ok");
}

test.describe.configure({ mode: "serial" });

test("智能建议：待确认后部分应用，notes 保留且 base 漂移跳过", async ({
  page,
  request,
}) => {
  const mock = await startMockLlmServer();
  try {
    const projectId = await seedSuggestProject(request, mock.baseUrl);
    const baseline = await getMatrixSnapshot(request, projectId);

    await openMatrixPage(page, projectId);

    // 1) 生成待确认建议（真实 response_match + 本机 mock LLM）
    await page.getByRole("button", { name: "智能建议" }).click();

    const rowA = matrixItem(page, SOURCE_A);
    const rowB = matrixItem(page, SOURCE_B);
    const rowC = matrixItem(page, SOURCE_C);

    // 以矩阵区内建议卡与工具栏为准（串行批次成功文案为「已累计 N 条待确认」）
    await expect(rowA.locator(".response-matrix__suggestion")).toBeVisible({
      timeout: 30_000,
    });
    await expect(
      page
        .locator(".response-matrix__suggestion-actions")
        .getByText(/已累计 3 条待确认/),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: /应用已选建议（3）/ }),
    ).toBeVisible();
    await expect(rowA.getByText("91%")).toBeVisible();
    await expect(rowA.getByText("E2E mock：甲映射架构章")).toBeVisible();
    await expect(rowA.getByText(/正文：章节架构/)).toBeVisible();
    await expect(rowA.getByText(/大纲：大纲架构/)).toBeVisible();

    await expect(rowB.getByText("87%")).toBeVisible();
    await expect(rowB.getByText("E2E mock：乙映射安全章")).toBeVisible();
    await expect(rowC.getByText("82%")).toBeVisible();
    await expect(
      rowC.getByText("E2E mock：丙映射架构（供 base 漂移）"),
    ).toBeVisible();

    // 2) 任务本身不得写 editor-state：应用前 GET 与基线一致
    const afterSuggest = await getMatrixSnapshot(request, projectId);
    expect(afterSuggest).toEqual(baseline);

    // UI 行仍为未覆盖、无勾选章节
    await expect(rowA.getByLabel("响应状态")).toHaveValue("uncovered");
    await expect(rowB.getByLabel("响应状态")).toHaveValue("uncovered");
    await expect(rowC.getByLabel("响应状态")).toHaveValue("uncovered");
    await expect(rowA.getByLabel("响应备注")).toHaveValue(NOTES_A);
    await expect(rowB.getByLabel("响应备注")).toHaveValue(NOTES_B);
    await expect(rowC.getByLabel("响应备注")).toHaveValue(NOTES_C);

    // 3) 部分取消：取消乙；默认仍勾选甲/丙
    await rowB
      .locator(".response-matrix__suggestion input[type='checkbox']")
      .uncheck();
    await expect(
      page.getByRole("button", { name: /应用已选建议（2）/ }),
    ).toBeVisible();

    // 4) 人工改丙：勾选安全章，造成相对建议 base 的漂移
    await rowC
      .locator("label.response-matrix__check")
      .filter({ hasText: "章节安全" })
      .locator('input[type="checkbox"]')
      .check();

    // 等防抖 PUT 落库，确认人工修改已持久化
    await waitForEditorMatrix(request, projectId, (rows) => {
      const c = rows.find((r) => r.sourceText === SOURCE_C);
      expect(c?.chapterIds || []).toEqual([CHAP_B]);
      expect(c?.status).toBe("uncovered");
      expect(c?.notes).toBe(NOTES_C);
    });

    // 5) 应用已选（甲 + 丙）；丙应因 base 不匹配被跳过
    await page.getByRole("button", { name: /应用已选建议/ }).click();

    await waitForEditorMatrix(request, projectId, (rows) => {
      const a = rows.find((r) => r.sourceText === SOURCE_A);
      const b = rows.find((r) => r.sourceText === SOURCE_B);
      const c = rows.find((r) => r.sourceText === SOURCE_C);
      expect(a).toBeTruthy();
      expect(b).toBeTruthy();
      expect(c).toBeTruthy();
      // 已选且 base 匹配：甲应写入建议映射与状态
      expect([...(a!.chapterIds || [])].sort()).toEqual([CHAP_A]);
      expect([...(a!.outlineNodeIds || [])].sort()).toEqual([NODE_A]);
      expect(a!.status).toBe("covered");
      expect(a!.notes).toBe(NOTES_A);
      // 未选：乙完全不变
      expect(b!.chapterIds || []).toEqual([]);
      expect(b!.outlineNodeIds || []).toEqual([]);
      expect(b!.status).toBe("uncovered");
      expect(b!.notes).toBe(NOTES_B);
      // base 漂移：丙保留人工章节勾选，不被建议覆盖
      expect([...(c!.chapterIds || [])].sort()).toEqual([CHAP_B]);
      expect(c!.outlineNodeIds || []).toEqual([]);
      expect(c!.status).toBe("uncovered");
      expect(c!.notes).toBe(NOTES_C);
    });

    // UI 同步断言
    await expect(rowA.getByLabel("响应状态")).toHaveValue("covered");
    await expect(
      rowA
        .locator("label.response-matrix__check")
        .filter({ hasText: "章节架构" })
        .locator('input[type="checkbox"]'),
    ).toBeChecked();
    await expect(
      rowA
        .locator("label.response-matrix__check")
        .filter({ hasText: "大纲架构" })
        .locator('input[type="checkbox"]'),
    ).toBeChecked();
    await expect(rowA.getByLabel("响应备注")).toHaveValue(NOTES_A);

    await expect(rowB.getByLabel("响应状态")).toHaveValue("uncovered");
    await expect(
      rowB.locator(
        'label.response-matrix__check input[type="checkbox"]:checked',
      ),
    ).toHaveCount(0);
    await expect(rowB.getByLabel("响应备注")).toHaveValue(NOTES_B);

    await expect(rowC.getByLabel("响应状态")).toHaveValue("uncovered");
    await expect(
      rowC
        .locator("label.response-matrix__check")
        .filter({ hasText: "章节安全" })
        .locator('input[type="checkbox"]'),
    ).toBeChecked();
    await expect(
      rowC
        .locator("label.response-matrix__check")
        .filter({ hasText: "章节架构" })
        .locator('input[type="checkbox"]'),
    ).not.toBeChecked();
    await expect(rowC.getByLabel("响应备注")).toHaveValue(NOTES_C);
  } finally {
    await mock.close();
  }
});
