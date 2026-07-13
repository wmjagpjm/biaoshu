/**
 * 模块：响应矩阵字段级三方合并 E2E
 * 用途：验证 409 后无冲突可安全合并、同字段冲突须显式选择、应用合并 PUT 仅含矩阵、
 *       合并成功后无全量回写、二次 409 清空旧预览且禁止复用；应用前不写库；
 *       项目切换后旧项目异步合并响应不得污染新项目编辑器状态。
 * 对接：Playwright chromium；后端 8010 / 前端 5174；editor-state 版本锁与合并预览。
 * 二次开发：禁止无业务意义的 fixed sleep；真实双浏览器上下文；勿依赖日用库或真实 Key。
 */
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
  type Request,
  type Route,
} from "@playwright/test";

const API = "http://127.0.0.1:8010/api";

const SOURCE_TEXT = "E2E字段合并要求";
const SOURCE_KEY = `requirement:${SOURCE_TEXT.trim().replace(/\s+/g, " ").toLocaleLowerCase()}`;

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
  analysis?: unknown;
  outline?: unknown;
  chapters?: unknown;
};

async function seedProject(
  request: APIRequestContext,
  options?: { name?: string; notes?: string; rowId?: string },
): Promise<string> {
  const created = await request.post(`${API}/projects`, {
    data: { name: options?.name ?? "E2E 字段级三方合并" },
  });
  expect(created.ok()).toBeTruthy();
  const project = (await created.json()) as { id: string };
  const notes = options?.notes ?? "初始备注";
  const rowId = options?.rowId ?? "mx_e2e_merge_1";

  const matrix: MatrixRow[] = [
    {
      id: rowId,
      kind: "requirement",
      sourceKey: SOURCE_KEY,
      sourceIndex: 0,
      sourceText: SOURCE_TEXT,
      weight: "",
      chapterIds: [],
      outlineNodeIds: [],
      status: "uncovered",
      notes,
    },
  ];

  const put = await request.put(`${API}/projects/${project.id}/editor-state`, {
    data: {
      outline: [
        { id: "node_e2e_merge", title: "E2E 大纲节点", children: [] },
      ],
      chapters: [
        { id: "chap_e2e_a", title: "E2E 章节甲" },
        { id: "chap_e2e_b", title: "E2E 章节乙" },
      ],
      analysis: {
        overview: "E2E 字段合并概述",
        techRequirements: [SOURCE_TEXT],
        rejectionRisks: [],
        scoringPoints: [],
      },
      responseMatrix: matrix,
    },
  });
  expect(put.ok()).toBeTruthy();
  return project.id;
}

async function openMatrixPage(page: Page, projectId: string) {
  await page.goto(`/technical-plan/${projectId}/analysis`);
  await expect(
    page.getByRole("region", { name: "响应矩阵" }).or(
      page.locator("section.response-matrix"),
    ),
  ).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(SOURCE_TEXT)).toBeVisible();
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
  await notesBox.blur();
  const res = await responsePromise;
  expect(res.status()).toBe(expectStatus);
  return res;
}

async function toggleChapterAndWaitPut(
  page: Page,
  projectId: string,
  chapterTitle: string,
  expectStatus: number,
) {
  const item = page.locator("article.response-matrix__item").filter({
    hasText: SOURCE_TEXT,
  });
  const checkbox = item.getByRole("checkbox", { name: chapterTitle });
  const responsePromise = page.waitForResponse(
    (res) =>
      res.url().includes(`/api/projects/${projectId}/editor-state`) &&
      res.request().method() === "PUT" &&
      res.status() === expectStatus,
    { timeout: 20_000 },
  );
  await checkbox.click();
  const res = await responsePromise;
  expect(res.status()).toBe(expectStatus);
  return res;
}

test.describe.configure({ mode: "serial" });

test("无冲突：不同字段三方合并后同时保留 notes 与 chapterIds", async ({
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

    await setNotesAndWaitPut(pageA, projectId, "A-备注保留", 200);
    await expect(pageA.getByLabel("响应备注")).toHaveValue("A-备注保留");

    // B 持旧版本，只改 chapterIds → 409 + 可安全合并
    await toggleChapterAndWaitPut(pageB, projectId, "E2E 章节甲", 409);
    await expect(pageB.getByRole("alert")).toContainText("矩阵保存冲突");
    await expect(pageB.getByTestId("response-matrix-merge-safe")).toContainText(
      "可安全合并",
    );
    const applyBtn = pageB.getByTestId("response-matrix-apply-merge");
    await expect(applyBtn).toBeEnabled();

    // 应用前服务端仍只有 A 的 notes，无 B 的章节
    const before = (await (
      await request.get(`${API}/projects/${projectId}/editor-state`)
    ).json()) as EditorState;
    expect(before.responseMatrix?.[0]?.notes).toBe("A-备注保留");
    expect(before.responseMatrix?.[0]?.chapterIds ?? []).toEqual([]);

    // 收集应用合并起的全部 editor-state PUT，断言无 analysis/outline/chapters/facts 回写
    const mergeRelatedPuts: Record<string, unknown>[] = [];
    const onPutRequest = (req: Request) => {
      if (
        req.method() === "PUT" &&
        req.url().includes(`/api/projects/${projectId}/editor-state`)
      ) {
        try {
          mergeRelatedPuts.push(req.postDataJSON() as Record<string, unknown>);
        } catch {
          mergeRelatedPuts.push({});
        }
      }
    };
    pageB.on("request", onPutRequest);

    const applyRequestPromise = pageB.waitForRequest(
      (req) =>
        req.url().includes(`/api/projects/${projectId}/editor-state`) &&
        req.method() === "PUT",
      { timeout: 20_000 },
    );
    const applyResponsePromise = pageB.waitForResponse(
      (res) =>
        res.url().includes(`/api/projects/${projectId}/editor-state`) &&
        res.request().method() === "PUT" &&
        res.status() === 200,
      { timeout: 20_000 },
    );
    const mergeStartedAt = Date.now();
    await applyBtn.click();
    const applyReq = await applyRequestPromise;
    const applyBody = applyReq.postDataJSON() as Record<string, unknown>;
    expect(applyBody).toHaveProperty("responseMatrix");
    expect(applyBody).toHaveProperty("responseMatrixVersion");
    expect(applyBody).not.toHaveProperty("analysis");
    expect(applyBody).not.toHaveProperty("outline");
    expect(applyBody).not.toHaveProperty("chapters");
    expect(applyBody).not.toHaveProperty("facts");
    await applyResponsePromise;

    await expect(pageB.getByRole("alert")).toHaveCount(0);
    await expect(pageB.getByLabel("响应备注")).toHaveValue("A-备注保留");
    await expect(
      pageB
        .locator("article.response-matrix__item")
        .filter({ hasText: SOURCE_TEXT })
        .getByRole("checkbox", { name: "E2E 章节甲" }),
    ).toBeChecked();

    await expect
      .poll(
        async () => {
          const got = await request.get(
            `${API}/projects/${projectId}/editor-state`,
          );
          expect(got.ok()).toBeTruthy();
          const body = (await got.json()) as EditorState;
          const row = body.responseMatrix?.[0];
          return {
            notes: row?.notes ?? "",
            chapterIds: [...(row?.chapterIds ?? [])].sort(),
          };
        },
        { timeout: 15_000 },
      )
      .toEqual({ notes: "A-备注保留", chapterIds: ["chap_e2e_a"] });

    // P1-1：超过普通防抖保存窗口（800ms）后，合并相关 PUT 仍只能是矩阵体
    await expect
      .poll(
        () => {
          const pastWindow = Date.now() - mergeStartedAt >= 1200;
          const allMatrixOnly =
            mergeRelatedPuts.length >= 1 &&
            mergeRelatedPuts.every((body) => {
              const keys = Object.keys(body);
              return (
                keys.includes("responseMatrix") &&
                keys.includes("responseMatrixVersion") &&
                !keys.includes("analysis") &&
                !keys.includes("outline") &&
                !keys.includes("chapters") &&
                !keys.includes("facts")
              );
            });
          return pastWindow && allMatrixOnly;
        },
        { timeout: 5_000 },
      )
      .toBe(true);
    pageB.off("request", onPutRequest);
  } finally {
    await contextA.close();
    await contextB.close();
  }
});

test("同字段冲突：须显式选择后应用，最终值严格对应选择", async ({
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

    await setNotesAndWaitPut(pageA, projectId, "远端-A备注", 200);
    await setNotesAndWaitPut(pageB, projectId, "本地-B备注", 409);

    await expect(pageB.getByRole("alert")).toContainText("矩阵保存冲突");
    await expect(pageB.getByTestId("response-matrix-merge-conflicts")).toBeVisible();
    await expect(pageB.getByTestId("merge-field-conflict-notes")).toBeVisible();
    await expect(pageB.getByTestId("merge-local-value-notes")).toContainText(
      "本地-B备注",
    );
    await expect(pageB.getByTestId("merge-remote-value-notes")).toContainText(
      "远端-A备注",
    );

    const applyBtn = pageB.getByTestId("response-matrix-apply-merge");
    await expect(applyBtn).toBeDisabled();

    // 采用远端
    await pageB.getByTestId("merge-choose-remote-notes").click();
    await expect(applyBtn).toBeEnabled();

    const applyResponsePromise = pageB.waitForResponse(
      (res) =>
        res.url().includes(`/api/projects/${projectId}/editor-state`) &&
        res.request().method() === "PUT" &&
        res.status() === 200,
      { timeout: 20_000 },
    );
    await applyBtn.click();
    await applyResponsePromise;

    await expect(pageB.getByLabel("响应备注")).toHaveValue("远端-A备注");
    await expect
      .poll(
        async () => {
          const got = await request.get(
            `${API}/projects/${projectId}/editor-state`,
          );
          const body = (await got.json()) as EditorState;
          return body.responseMatrix?.[0]?.notes ?? "";
        },
        { timeout: 15_000 },
      )
      .toBe("远端-A备注");
  } finally {
    await contextA.close();
    await contextB.close();
  }
});

test("应用合并再次 409 不自动循环；应用前不写库", async ({
  browser,
  request,
}) => {
  const projectId = await seedProject(request);
  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const pageB = await contextB.newPage();
  let mergePutCount = 0;

  try {
    await openMatrixPage(pageA, projectId);
    await openMatrixPage(pageB, projectId);

    await setNotesAndWaitPut(pageA, projectId, "A-用于二次409", 200);
    await toggleChapterAndWaitPut(pageB, projectId, "E2E 章节乙", 409);
    await expect(pageB.getByTestId("response-matrix-merge-safe")).toBeVisible();

    // 拦截「应用合并」的仅矩阵 PUT，强制第二次冲突
    await pageB.route(`**/api/projects/${projectId}/editor-state`, async (route: Route) => {
      const req = route.request();
      if (req.method() !== "PUT") {
        await route.continue();
        return;
      }
      const raw = req.postData() || "{}";
      let body: Record<string, unknown> = {};
      try {
        body = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        body = {};
      }
      const keys = Object.keys(body);
      const isMatrixOnly =
        keys.includes("responseMatrix") &&
        keys.includes("responseMatrixVersion") &&
        !keys.includes("analysis") &&
        !keys.includes("outline") &&
        !keys.includes("chapters");
      if (!isMatrixOnly) {
        await route.continue();
        return;
      }
      mergePutCount += 1;
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            message: "模拟二次冲突",
            responseMatrix: [
              {
                id: "mx_e2e_merge_1",
                kind: "requirement",
                sourceKey: SOURCE_KEY,
                sourceIndex: 0,
                sourceText: SOURCE_TEXT,
                weight: "",
                chapterIds: [],
                outlineNodeIds: [],
                status: "uncovered",
                notes: "A-用于二次409",
              },
            ],
            currentResponseMatrixVersion: "forced-conflict-version",
          },
        }),
      });
    });

    const applyBtn = pageB.getByTestId("response-matrix-apply-merge");
    await applyBtn.click();

    // P1-2：二次 409 后旧预览失效，「应用合并」不可用；须提示重新载入
    await expect(pageB.getByTestId("response-matrix-merge-apply-error")).toContainText(
      "409",
    );
    await expect(pageB.getByTestId("response-matrix-merge-apply-error")).toContainText(
      "重新载入远端矩阵",
    );
    await expect(pageB.getByTestId("response-matrix-merge-preview")).toHaveCount(0);
    await expect(pageB.getByTestId("response-matrix-apply-merge")).toHaveCount(0);
    // 不自动循环：仅一次应用 PUT
    await expect.poll(() => mergePutCount, { timeout: 5_000 }).toBe(1);

    // 服务端仍是应用前的 A 状态（拦截未真正写入）
    const after = (await (
      await request.get(`${API}/projects/${projectId}/editor-state`)
    ).json()) as EditorState;
    expect(after.responseMatrix?.[0]?.notes).toBe("A-用于二次409");
    expect(after.responseMatrix?.[0]?.chapterIds ?? []).toEqual([]);
  } finally {
    await pageB.unroute(`**/api/projects/${projectId}/editor-state`).catch(() => undefined);
    await contextA.close();
    await contextB.close();
  }
});

/**
 * 用途：在同一文档内切换 technical-plan 路由参数（软导航），
 * 避免 page.goto 整页卸载中止飞行中的 fetch，从而复现「异步返回污染新项目」竞态。
 */
async function softNavigateTechnicalPlan(page: Page, projectId: string, step = "analysis") {
  const url = `/technical-plan/${projectId}/${step}`;
  await page.evaluate((nextUrl) => {
    window.history.pushState({}, "", nextUrl);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, url);
}

test("项目切换：旧项目延迟合并响应不得污染新项目", async ({
  browser,
  request,
}) => {
  const projectA = await seedProject(request, {
    name: "E2E 合并隔离-旧项目A",
    notes: "初始备注A",
    rowId: "mx_e2e_iso_a",
  });
  const projectB = await seedProject(request, {
    name: "E2E 合并隔离-新项目B",
    notes: "项目B独立备注",
    rowId: "mx_e2e_iso_b",
  });

  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const pageB = await contextB.newPage();

  /** 挂起旧项目「应用合并」PUT，直到软切换到新项目后再 fulfill */
  let releaseMergePut: (() => void) | null = null;
  const mergePutGate = new Promise<void>((resolve) => {
    releaseMergePut = resolve;
  });
  let projectAMergePutSeen = 0;
  let projectBEditorPuts = 0;
  const projectBPutBodies: Record<string, unknown>[] = [];

  try {
    await openMatrixPage(pageA, projectA);
    await openMatrixPage(pageB, projectA);

    await setNotesAndWaitPut(pageA, projectA, "旧项目-远端备注污染探针", 200);
    await toggleChapterAndWaitPut(pageB, projectA, "E2E 章节甲", 409);
    await expect(pageB.getByTestId("response-matrix-merge-safe")).toBeVisible();
    const applyBtn = pageB.getByTestId("response-matrix-apply-merge");
    await expect(applyBtn).toBeEnabled();

    // 挂起 projectA 仅矩阵合并 PUT，稍后以 200 合成响应放行（不依赖真实写库时序）
    await pageB.route(
      `**/api/projects/${projectA}/editor-state`,
      async (route: Route) => {
        const req = route.request();
        if (req.method() !== "PUT") {
          await route.continue();
          return;
        }
        let body: Record<string, unknown> = {};
        try {
          body = JSON.parse(req.postData() || "{}") as Record<string, unknown>;
        } catch {
          body = {};
        }
        const keys = Object.keys(body);
        const isMatrixOnly =
          keys.includes("responseMatrix") &&
          keys.includes("responseMatrixVersion") &&
          !keys.includes("analysis") &&
          !keys.includes("outline") &&
          !keys.includes("chapters");
        if (!isMatrixOnly) {
          await route.continue();
          return;
        }
        projectAMergePutSeen += 1;
        await mergePutGate;
        const matrix = Array.isArray(body.responseMatrix)
          ? body.responseMatrix
          : [];
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            projectId: projectA,
            responseMatrix: matrix,
            responseMatrixVersion: "pollution-version-from-project-a",
            updatedAt: new Date().toISOString(),
          }),
        });
      },
    );

    pageB.on("request", (req: Request) => {
      if (
        req.method() === "PUT" &&
        req.url().includes(`/api/projects/${projectB}/editor-state`)
      ) {
        projectBEditorPuts += 1;
        try {
          projectBPutBodies.push(req.postDataJSON() as Record<string, unknown>);
        } catch {
          projectBPutBodies.push({});
        }
      }
    });

    const mergeRequestPromise = pageB.waitForRequest(
      (req) =>
        req.url().includes(`/api/projects/${projectA}/editor-state`) &&
        req.method() === "PUT",
      { timeout: 20_000 },
    );
    await applyBtn.click();
    await mergeRequestPromise;
    await expect.poll(() => projectAMergePutSeen, { timeout: 10_000 }).toBe(1);

    // 合并仍在挂起：同一文档内软切换到新项目（复用 hook 实例 / 触发 projectId effect）
    await softNavigateTechnicalPlan(pageB, projectB, "analysis");
    await expect(
      pageB.getByRole("region", { name: "响应矩阵" }).or(
        pageB.locator("section.response-matrix"),
      ),
    ).toBeVisible({ timeout: 20_000 });
    await expect(pageB.getByLabel("响应备注")).toHaveValue("项目B独立备注", {
      timeout: 20_000,
    });
    await expect(pageB.getByRole("alert")).toHaveCount(0);
    await expect(pageB.getByTestId("response-matrix-merge-preview")).toHaveCount(
      0,
    );
    await expect(pageB.getByTestId("response-matrix-apply-merge")).toHaveCount(0);

    const putsBeforeRelease = projectBEditorPuts;

    // 放行旧项目合并成功响应：不得污染新项目备注 / 冲突 UI / 版本写回
    releaseMergePut?.();

    // 给异步 then 调度时间（事件驱动：等到至少一轮 macrotask + 网络回调）
    await expect
      .poll(async () => {
        const notes = await pageB.getByLabel("响应备注").inputValue();
        const alerts = await pageB.getByRole("alert").count();
        const pollutionText = await pageB
          .getByText("旧项目-远端备注污染探针")
          .count();
        const mergePreview = await pageB
          .getByTestId("response-matrix-merge-preview")
          .count();
        return {
          notes,
          alerts,
          pollutionText,
          mergePreview,
          bPuts: projectBEditorPuts,
        };
      }, { timeout: 8_000 })
      .toEqual({
        notes: "项目B独立备注",
        alerts: 0,
        pollutionText: 0,
        mergePreview: 0,
        bPuts: putsBeforeRelease,
      });

    // 旧响应不得把 projectA 合并矩阵写进 projectB 的 PUT
    for (const body of projectBPutBodies) {
      const matrix = body.responseMatrix;
      if (!Array.isArray(matrix)) continue;
      for (const row of matrix) {
        const notes =
          row && typeof row === "object" && "notes" in row
            ? String((row as { notes?: string }).notes ?? "")
            : "";
        expect(notes).not.toBe("旧项目-远端备注污染探针");
      }
    }

    const afterB = (await (
      await request.get(`${API}/projects/${projectB}/editor-state`)
    ).json()) as EditorState;
    expect(afterB.responseMatrix?.[0]?.notes).toBe("项目B独立备注");
    expect(afterB.responseMatrix?.[0]?.chapterIds ?? []).toEqual([]);
  } finally {
    releaseMergePut?.();
    await pageB
      .unroute(`**/api/projects/${projectA}/editor-state`)
      .catch(() => undefined);
    await contextA.close();
    await contextB.close();
  }
});
