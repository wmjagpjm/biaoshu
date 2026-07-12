/**
 * 模块：响应矩阵双浏览器上下文 E2E
 * 用途：验证 A 保存后 B 真实 409、本地不静默覆盖、显式载入远端后再保存。
 * 对接：Playwright chromium；后端 8010 / 前端 5174；editor-state 版本锁。
 * 二次开发：禁止 sleep 硬等；种子数据走 API；勿依赖日用库。
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API = "http://127.0.0.1:8010/api";

type EditorState = {
  responseMatrix: Array<{ notes?: string; sourceText?: string }>;
  responseMatrixVersion: string;
};

async function seedProject(request: APIRequestContext): Promise<string> {
  const created = await request.post(`${API}/projects`, {
    data: { name: "E2E 响应矩阵冲突" },
  });
  expect(created.ok()).toBeTruthy();
  const project = (await created.json()) as { id: string };
  // sourceKey 须与前端 makeResponseMatrixSourceKey 一致（小写规范化），
  // 且 analysis.techRequirements 非空，否则 mergeResponseMatrix 会得到 0 行。
  const sourceText = "E2E等保三级";
  const sourceKey = `requirement:${sourceText.trim().replace(/\s+/g, " ").toLocaleLowerCase()}`;
  const matrix = [
    {
      id: "mx_e2e_1",
      kind: "requirement",
      sourceKey,
      sourceIndex: 0,
      sourceText,
      weight: "",
      chapterIds: [],
      outlineNodeIds: [],
      status: "uncovered",
      notes: "初始备注",
    },
  ];
  const put = await request.put(`${API}/projects/${project.id}/editor-state`, {
    data: {
      outline: [{ id: "node_e2e", title: "E2E 大纲", children: [] }],
      chapters: [{ id: "chap_e2e", title: "E2E 章节" }],
      analysis: {
        overview: "E2E 概述",
        techRequirements: [sourceText],
        rejectionRisks: [],
        scoringPoints: [],
      },
      responseMatrix: matrix,
    },
  });
  expect(put.ok()).toBeTruthy();
  const body = (await put.json()) as { responseMatrix?: unknown[] };
  expect(Array.isArray(body.responseMatrix) && body.responseMatrix.length).toBeTruthy();
  return project.id;
}

async function openMatrixPage(page: Page, projectId: string) {
  await page.goto(`/technical-plan/${projectId}/analysis`);
  await expect(page.getByRole("region", { name: "响应矩阵" }).or(
    page.locator("section.response-matrix"),
  )).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("E2E等保三级")).toBeVisible();
  await expect(page.getByLabel("响应备注")).toBeVisible();
}

async function setNotesAndWaitPut(
  page: Page,
  projectId: string,
  notes: string,
  expectStatus: number,
) {
  const notesBox = page.getByLabel("响应备注");
  await notesBox.fill(notes);
  const responsePromise = page.waitForResponse(
    (res) =>
      res.url().includes(`/api/projects/${projectId}/editor-state`) &&
      res.request().method() === "PUT" &&
      res.status() === expectStatus,
    { timeout: 20_000 },
  );
  // 触发 React onChange 后的 800ms 防抖保存
  await notesBox.blur();
  const res = await responsePromise;
  expect(res.status()).toBe(expectStatus);
  return res;
}

test.describe.configure({ mode: "serial" });

test("双浏览器上下文：409 冲突条、本地保留、显式载入远端后再保存", async ({
  browser,
  request,
}) => {
  const projectId = await seedProject(request);

  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const pageB = await contextB.newPage();

  try {
    await openMatrixPage(pageA, projectId);
    await openMatrixPage(pageB, projectId);

    await setNotesAndWaitPut(pageA, projectId, "A-保存", 200);
    await expect(pageA.getByLabel("响应备注")).toHaveValue("A-保存");

    // B 未刷新，仍持旧 version；本地改为 B 后应真实 409
    await setNotesAndWaitPut(pageB, projectId, "B-本地", 409);
    await expect(pageB.getByRole("alert")).toContainText("矩阵保存冲突");
    await expect(pageB.getByLabel("响应备注")).toHaveValue("B-本地");

    await pageB.getByRole("button", { name: "重新载入远端矩阵" }).click();
    await expect(pageB.getByRole("alert")).toHaveCount(0);
    await expect(pageB.getByLabel("响应备注")).toHaveValue("A-保存");

    await setNotesAndWaitPut(pageB, projectId, "B-最终", 200);
    await expect(pageB.getByLabel("响应备注")).toHaveValue("B-最终");

    await expect
      .poll(
        async () => {
          const got = await request.get(
            `${API}/projects/${projectId}/editor-state`,
          );
          expect(got.ok()).toBeTruthy();
          const body = (await got.json()) as EditorState;
          return body.responseMatrix[0]?.notes ?? "";
        },
        { timeout: 15_000 },
      )
      .toBe("B-最终");
  } finally {
    await contextA.close();
    await contextB.close();
  }
});
