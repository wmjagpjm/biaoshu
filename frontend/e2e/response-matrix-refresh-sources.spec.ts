/**
 * 模块：响应矩阵「刷新来源」E2E
 * 用途：验证刷新来源按 sourceKey 保留人工 chapter/outline/status/notes，并随 analysis 增删行。
 * 对接：Playwright chromium；后端 8010 / 前端 5174；mergeResponseMatrix / editor-state。
 * 二次开发：禁止 sleep、真实 Key/LLM/route stub；种子与增删源走真实 API；勿依赖日用库。
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API = "http://127.0.0.1:8010/api";

const SOURCE_JIA = "E2E要求甲";
const SOURCE_YI = "E2E要求乙";
const SOURCE_BING = "E2E要求丙";

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
  analysis?: { techRequirements?: string[] };
  responseMatrix?: MatrixRow[];
  responseMatrixVersion?: string;
};

/** 用途：与前端 makeResponseMatrixSourceKey 保持一致的小写规范化键。 */
function requirementSourceKey(sourceText: string): string {
  return `requirement:${sourceText.trim().replace(/\s+/g, " ").toLocaleLowerCase()}`;
}

function matrixItem(page: Page, sourceText: string) {
  return page
    .locator("article.response-matrix__item")
    .filter({ hasText: sourceText });
}

async function seedRefreshProject(request: APIRequestContext): Promise<{
  projectId: string;
  matrix: MatrixRow[];
}> {
  const created = await request.post(`${API}/projects`, {
    data: { name: "E2E 刷新来源" },
  });
  expect(created.ok()).toBeTruthy();
  const project = (await created.json()) as { id: string };

  const matrix: MatrixRow[] = [
    {
      id: "mx_e2e_jia",
      kind: "requirement",
      sourceKey: requirementSourceKey(SOURCE_JIA),
      sourceIndex: 0,
      sourceText: SOURCE_JIA,
      weight: "",
      chapterIds: ["chap_a"],
      outlineNodeIds: ["node_a"],
      status: "covered",
      notes: "人工甲",
    },
    {
      id: "mx_e2e_yi",
      kind: "requirement",
      sourceKey: requirementSourceKey(SOURCE_YI),
      sourceIndex: 1,
      sourceText: SOURCE_YI,
      weight: "",
      chapterIds: ["chap_b"],
      // 乙仅人工章节，无 outline，覆盖「可无 outline」分支
      outlineNodeIds: [],
      status: "partial",
      notes: "人工乙",
    },
  ];

  const put = await request.put(`${API}/projects/${project.id}/editor-state`, {
    data: {
      outline: [
        { id: "node_a", title: "大纲A", children: [] },
        { id: "node_b", title: "大纲B", children: [] },
      ],
      chapters: [
        { id: "chap_a", title: "章节A" },
        { id: "chap_b", title: "章节B" },
      ],
      analysis: {
        overview: "E2E 刷新来源概述",
        techRequirements: [SOURCE_JIA, SOURCE_YI],
        rejectionRisks: [],
        scoringPoints: [],
      },
      responseMatrix: matrix,
    },
  });
  expect(put.ok()).toBeTruthy();
  const body = (await put.json()) as EditorState;
  expect(Array.isArray(body.responseMatrix) && body.responseMatrix.length).toBe(2);
  return { projectId: project.id, matrix };
}

async function openMatrixPage(page: Page, projectId: string) {
  await page.goto(`/technical-plan/${projectId}/analysis`);
  await expect(
    page.getByRole("region", { name: "响应矩阵" }).or(
      page.locator("section.response-matrix"),
    ),
  ).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(SOURCE_JIA)).toBeVisible();
  await expect(page.getByText(SOURCE_YI)).toBeVisible();
}

/** 用途：断言指定来源行的人工映射、状态与备注完整保留。 */
async function expectManualMapping(
  page: Page,
  sourceText: string,
  opts: {
    status: string;
    notes: string;
    chapterTitle: string;
    outlineTitle?: string | null;
  },
) {
  const row = matrixItem(page, sourceText);
  await expect(row).toBeVisible();
  await expect(row.getByLabel("响应状态")).toHaveValue(opts.status);
  await expect(row.getByLabel("响应备注")).toHaveValue(opts.notes);
  await expect(
    row
      .locator("label.response-matrix__check")
      .filter({ hasText: opts.chapterTitle })
      .locator('input[type="checkbox"]'),
  ).toBeChecked();
  if (opts.outlineTitle) {
    await expect(
      row
        .locator("label.response-matrix__check")
        .filter({ hasText: opts.outlineTitle })
        .locator('input[type="checkbox"]'),
    ).toBeChecked();
  }
}

/** 用途：点击刷新来源并等待 editor-state 防抖 PUT 200。 */
async function clickRefreshAndWaitPut(page: Page, projectId: string) {
  const responsePromise = page.waitForResponse(
    (res) =>
      res.url().includes(`/api/projects/${projectId}/editor-state`) &&
      res.request().method() === "PUT" &&
      res.status() === 200,
    { timeout: 20_000 },
  );
  await page.getByRole("button", { name: "刷新来源" }).click();
  const res = await responsePromise;
  expect(res.status()).toBe(200);
}

test.describe.configure({ mode: "serial" });

test("刷新来源：保留人工映射，并随分析增删行", async ({ page, request }) => {
  const { projectId, matrix } = await seedRefreshProject(request);

  await openMatrixPage(page, projectId);

  await expectManualMapping(page, SOURCE_JIA, {
    status: "covered",
    notes: "人工甲",
    chapterTitle: "章节A",
    outlineTitle: "大纲A",
  });
  await expectManualMapping(page, SOURCE_YI, {
    status: "partial",
    notes: "人工乙",
    chapterTitle: "章节B",
    outlineTitle: null,
  });

  // 同源再刷新：甲/乙映射、status、notes 必须完整保留
  await clickRefreshAndWaitPut(page, projectId);
  await expectManualMapping(page, SOURCE_JIA, {
    status: "covered",
    notes: "人工甲",
    chapterTitle: "章节A",
    outlineTitle: "大纲A",
  });
  await expectManualMapping(page, SOURCE_YI, {
    status: "partial",
    notes: "人工乙",
    chapterTitle: "章节B",
    outlineTitle: null,
  });

  // API 将分析改为甲/丙，故意保留旧矩阵（仍含乙），验证刷新按 analysis 收敛
  const replace = await request.put(
    `${API}/projects/${projectId}/editor-state`,
    {
      data: {
        outline: [
          { id: "node_a", title: "大纲A", children: [] },
          { id: "node_b", title: "大纲B", children: [] },
        ],
        chapters: [
          { id: "chap_a", title: "章节A" },
          { id: "chap_b", title: "章节B" },
        ],
        analysis: {
          overview: "E2E 刷新来源概述",
          techRequirements: [SOURCE_JIA, SOURCE_BING],
          rejectionRisks: [],
          scoringPoints: [],
        },
        // 不带 version：旧客户端路径写矩阵，保留含乙的旧行供刷新收敛
        responseMatrix: matrix,
      },
    },
  );
  expect(replace.ok()).toBeTruthy();

  await page.reload();
  await expect(
    page.getByRole("region", { name: "响应矩阵" }).or(
      page.locator("section.response-matrix"),
    ),
  ).toBeVisible({ timeout: 20_000 });
  // 刷新前仍可能展示旧矩阵中的乙；以「刷新来源」后收敛结果为准
  await clickRefreshAndWaitPut(page, projectId);

  await expect(matrixItem(page, SOURCE_YI)).toHaveCount(0);
  await expectManualMapping(page, SOURCE_JIA, {
    status: "covered",
    notes: "人工甲",
    chapterTitle: "章节A",
    outlineTitle: "大纲A",
  });

  const bing = matrixItem(page, SOURCE_BING);
  await expect(bing).toBeVisible();
  await expect(bing.getByLabel("响应状态")).toHaveValue("uncovered");
  await expect(bing.getByLabel("响应备注")).toHaveValue("");
  await expect(
    bing.locator('label.response-matrix__check input[type="checkbox"]:checked'),
  ).toHaveCount(0);

  await expect
    .poll(
      async () => {
        const got = await request.get(
          `${API}/projects/${projectId}/editor-state`,
        );
        expect(got.ok()).toBeTruthy();
        const body = (await got.json()) as EditorState;
        const rows = body.responseMatrix ?? [];
        const texts = rows.map((r) => r.sourceText).sort();
        const jia = rows.find((r) => r.sourceText === SOURCE_JIA);
        const yi = rows.find((r) => r.sourceText === SOURCE_YI);
        const bingRow = rows.find((r) => r.sourceText === SOURCE_BING);
        return {
          texts,
          jia: jia
            ? {
                status: jia.status,
                notes: jia.notes,
                chapterIds: [...jia.chapterIds].sort(),
                outlineNodeIds: [...jia.outlineNodeIds].sort(),
              }
            : null,
          yiExists: Boolean(yi),
          bing: bingRow
            ? {
                status: bingRow.status,
                notes: bingRow.notes,
                chapterIds: bingRow.chapterIds,
                outlineNodeIds: bingRow.outlineNodeIds,
              }
            : null,
        };
      },
      { timeout: 15_000 },
    )
    .toEqual({
      texts: [SOURCE_JIA, SOURCE_BING].sort(),
      jia: {
        status: "covered",
        notes: "人工甲",
        chapterIds: ["chap_a"],
        outlineNodeIds: ["node_a"],
      },
      yiExists: false,
      bing: {
        status: "uncovered",
        notes: "",
        chapterIds: [],
        outlineNodeIds: [],
      },
    });
});
