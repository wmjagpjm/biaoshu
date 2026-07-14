/**
 * 模块：模板/卡片融合建议 M3-B/M3-C E2E
 * 用途：差异预览、勾选确认写入、base 漂移/删除跳过；M3-C 最近批次一次性撤销与漂移跳过。
 * 对接：Playwright chromium；后端 8010 / 前端 5174；content_fuse + replaceChapterBody。
 * 二次开发：禁止真实云 Key；本文件内起本地 mock chat completions；勿改业务 API；
 *       撤销仅走编辑器内存与既有 PUT，禁止新增网络端点或浏览器存储。
 */
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import http from "node:http";
import type { AddressInfo } from "node:net";
import { createHash } from "node:crypto";

const API = "http://127.0.0.1:8010/api";

const TITLE_A = "E2E融合章A 中文";
const TITLE_B = "E2E融合章B";
const BODY_A = "初始正文中文与emoji🚀保持";
const BODY_B = "第二章初始正文，不应被误写。";
const PROPOSED_A = "M3-B确认写入建议A（中文emoji✅）";
const PROPOSED_B = "M3-B确认写入建议B，仅勾选时写入。";

const CHAP_A = "chap_e2e_fuse_a";
const CHAP_B = "chap_e2e_fuse_b";

function bodyHash(body: string): string {
  const digest = createHash("sha1").update(body, "utf8").digest("hex").slice(0, 20);
  return `bh_${digest}`;
}

function bodyLength(body: string): number {
  return Array.from(body).length;
}

async function startMockLlmServer(opts?: {
  /** 额外注入一条「幽灵章节」建议（后端不会产出；用于删除路径时改由前端路由注入） */
  includeGhostInMock?: boolean;
}): Promise<{
  baseUrl: string;
  close: () => Promise<void>;
}> {
  const server = http.createServer((req, res) => {
    if (req.method === "POST" && (req.url || "").includes("chat/completions")) {
      const chunks: Buffer[] = [];
      req.on("data", (c) => chunks.push(c));
      req.on("end", () => {
        const rawReq = Buffer.concat(chunks).toString("utf8");
        let promptText = rawReq;
        try {
          const parsed = JSON.parse(rawReq) as {
            messages?: Array<{ content?: string }>;
          };
          promptText = (parsed.messages || [])
            .map((m) => m.content || "")
            .join("\n");
        } catch {
          /* 保持原文 */
        }
        const sourceRefs: Array<{ kind: string; id: string; title: string }> =
          [];
        const tplMatch = /模板 id=([^\s]+) title=([^\n]+)/.exec(promptText);
        if (tplMatch) {
          sourceRefs.push({
            kind: "template",
            id: tplMatch[1],
            title: "模型伪造标题-模板",
          });
        }
        const cardMatch = /卡片 id=([^\s]+) type=\S+ title=([^\n]+)/.exec(
          promptText,
        );
        if (cardMatch) {
          sourceRefs.push({
            kind: "card",
            id: cardMatch[1],
            title: "模型伪造标题-卡片",
          });
        }

        const items: Array<Record<string, unknown>> = [];
        if (promptText.includes(CHAP_A)) {
          items.push({
            targetChapterId: CHAP_A,
            action: "merge_suggest",
            confidence: 88,
            reason: "E2E mock 融合A",
            sourceRefs,
            proposedMarkdown: PROPOSED_A,
            diffSummary: "写入A",
          });
        }
        if (promptText.includes(CHAP_B)) {
          items.push({
            targetChapterId: CHAP_B,
            action: "merge_suggest",
            confidence: 80,
            reason: "E2E mock 融合B",
            sourceRefs,
            proposedMarkdown: PROPOSED_B,
            diffSummary: "写入B",
          });
        }
        if (items.length === 0) {
          items.push({
            targetChapterId: CHAP_A,
            action: "merge_suggest",
            confidence: 70,
            reason: "E2E fallback",
            sourceRefs,
            proposedMarkdown: PROPOSED_A,
            diffSummary: "fallback",
          });
        }
        if (opts?.includeGhostInMock) {
          items.push({
            targetChapterId: "chap_ghost_deleted",
            action: "merge_suggest",
            confidence: 60,
            reason: "幽灵章",
            sourceRefs,
            proposedMarkdown: "不应出现",
            diffSummary: "ghost",
          });
        }

        const body = JSON.stringify({
          id: "chatcmpl-e2e-fuse-apply",
          object: "chat.completion",
          model: "e2e-mock-fuse-apply",
          choices: [
            {
              index: 0,
              message: {
                role: "assistant",
                content: JSON.stringify(items, null, 0),
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

async function seedFuseApplyFixtures(
  request: APIRequestContext,
  mockBase: string,
  opts?: { twoChapters?: boolean },
) {
  const settings = await request.put(`${API}/settings`, {
    data: {
      provider: "openai-compatible",
      apiBaseUrl: mockBase,
      apiKey: "e2e-local-mock",
      model: "e2e-mock-fuse-apply",
    },
  });
  expect(settings.ok()).toBeTruthy();

  const source = await request.post(`${API}/projects`, {
    data: { name: "E2E 融合写入模板源", kind: "technical", industry: "政务" },
  });
  expect(source.ok()).toBeTruthy();
  const sourceProject = (await source.json()) as { id: string };
  const seedState = await request.put(
    `${API}/projects/${sourceProject.id}/editor-state`,
    {
      data: {
        outline: [{ id: "node_src", title: TITLE_A, children: [] }],
        chapters: [
          {
            id: "chap_src",
            title: TITLE_A,
            body: "模板侧架构参考正文。",
          },
        ],
        mode: "ALIGNED",
      },
    },
  );
  expect(seedState.ok()).toBeTruthy();

  const tpl = await request.post(`${API}/templates/from-project`, {
    data: {
      projectId: sourceProject.id,
      title: `E2E融合写入模板-${Date.now()}`,
      tags: ["E2E", "融合写入"],
    },
  });
  expect(tpl.ok()).toBeTruthy();
  const template = (await tpl.json()) as { id: string; title: string };

  const card = await request.post(`${API}/cards`, {
    data: {
      type: "document",
      title: `E2E融合写入卡片-${Date.now()}`,
      bodyMarkdown: "E2E 卡片参考段落，用于融合写入。",
      tags: ["E2E"],
      sourceLabel: "E2E",
    },
  });
  expect(card.ok()).toBeTruthy();
  const cardBody = (await card.json()) as { id: string; title: string };

  const two = opts?.twoChapters !== false;
  const outline = two
    ? [
        { id: "node_a", title: TITLE_A, children: [] },
        { id: "node_b", title: TITLE_B, children: [] },
      ]
    : [{ id: "node_a", title: TITLE_A, children: [] }];
  const chapters = two
    ? [
        {
          id: CHAP_A,
          title: TITLE_A,
          body: BODY_A,
          status: "pending",
          wordCount: 0,
          preview: "",
        },
        {
          id: CHAP_B,
          title: TITLE_B,
          body: BODY_B,
          status: "pending",
          wordCount: 0,
          preview: "",
        },
      ]
    : [
        {
          id: CHAP_A,
          title: TITLE_A,
          body: BODY_A,
          status: "pending",
          wordCount: 0,
          preview: "",
        },
      ];

  const target = await request.post(`${API}/projects`, {
    data: { name: "E2E 融合写入目标项目", kind: "technical", industry: "政务" },
  });
  expect(target.ok()).toBeTruthy();
  const project = (await target.json()) as { id: string };
  const put = await request.put(`${API}/projects/${project.id}/editor-state`, {
    data: {
      outline,
      chapters,
      mode: "ALIGNED",
    },
  });
  expect(put.ok()).toBeTruthy();

  return {
    projectId: project.id,
    templateTitle: template.title,
    cardTitle: cardBody.title,
  };
}

async function openContentStep(page: Page, projectId: string) {
  await page.goto(`/technical-plan/${projectId}/content`);
  await expect(
    page.getByRole("heading", { name: "E2E 融合写入目标项目" }),
  ).toBeVisible({ timeout: 20_000 });
  await expect(page.getByLabel(`正文：${TITLE_A}`)).toBeVisible({
    timeout: 15_000,
  });
}

type EditorStateChapter = {
  id?: string;
  title?: string;
  body?: string;
  status?: string;
};

type EditorStateSnapshot = {
  chapters?: EditorStateChapter[];
};

/**
 * 用途：轮询 GET editor-state，确认 debounce PUT 已持久化后再 reload。
 * 禁止 fixed sleep；超时默认 20s，失败时 expect.poll 会带出最后一次断言失败。
 */
async function waitForEditorState(
  request: APIRequestContext,
  projectId: string,
  assertion: (state: EditorStateSnapshot) => boolean,
  options?: { timeout?: number },
): Promise<EditorStateSnapshot> {
  let last: EditorStateSnapshot = {};
  await expect
    .poll(
      async () => {
        const res = await request.get(
          `${API}/projects/${projectId}/editor-state`,
        );
        if (!res.ok()) return false;
        last = (await res.json()) as EditorStateSnapshot;
        return assertion(last);
      },
      {
        timeout: options?.timeout ?? 20_000,
        message: `editor-state 未在超时内满足条件（projectId=${projectId}）`,
      },
    )
    .toBe(true);
  return last;
}

function findChapter(
  state: EditorStateSnapshot,
  chapterId: string,
): EditorStateChapter | undefined {
  return state.chapters?.find((c) => c.id === chapterId);
}

/** 用途：侧栏点选章节（正文 textarea 仅渲染当前选中章）。 */
async function selectChapterByTitle(page: Page, title: string, force = false) {
  const item = page.locator(".tp-content-nav-item").filter({ hasText: title }).first();
  if (force) {
    // 融合对话框遮罩时，用原生 click 确保 React onSelect 触发
    await item.evaluate((el) => (el as HTMLButtonElement).click());
  } else {
    await item.click();
  }
}

/** 用途：在遮罩打开时改当前选中章标题。 */
async function forceSetChapterTitle(page: Page, title: string) {
  await page.locator(".tp-content-title-input").evaluate((el, value) => {
    const input = el as HTMLInputElement;
    const proto = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value",
    );
    proto?.set?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }, title);
}

/** 用途：在遮罩打开时改当前选中章正文。 */
async function forceSetChapterBody(page: Page, body: string) {
  await page.locator("textarea.tp-content-body").evaluate((el, value) => {
    const area = el as HTMLTextAreaElement;
    const proto = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype,
      "value",
    );
    proto?.set?.call(area, value);
    area.dispatchEvent(new Event("input", { bubbles: true }));
    area.dispatchEvent(new Event("change", { bubbles: true }));
  }, body);
}

/** 用途：侧栏章节状态徽章文案（待生成/待审等）。 */
function chapterNavItem(page: Page, title: string) {
  return page.locator(".tp-content-nav-item").filter({ hasText: title }).first();
}

async function generateSuggestions(
  page: Page,
  templateTitle: string,
  cardTitle: string,
  targetTitles: string[],
) {
  await page.getByRole("button", { name: "模板卡片融合建议" }).click();
  const dialog = page.getByRole("dialog", { name: "模板卡片融合建议" });
  await expect(dialog).toBeVisible();

  await dialog.getByLabel(`模板 ${templateTitle}`).check();
  await dialog.getByLabel(`卡片 ${cardTitle}`).check();
  for (const title of targetTitles) {
    await dialog.getByLabel(`目标章节 ${title}`).check();
  }

  await dialog.getByRole("button", { name: "生成只读融合建议" }).click();
  await expect(dialog.getByText(/已生成 \d+ 条只读建议/)).toBeVisible({
    timeout: 30_000,
  });
  return dialog;
}

test.describe("模板卡片融合确认写入 M3-B", () => {
  test("中文emoji基线匹配：仅勾选一章写入，另一章不变，刷新保持", async ({
    page,
    request,
  }) => {
    const mock = await startMockLlmServer();
    try {
      const { projectId, templateTitle, cardTitle } =
        await seedFuseApplyFixtures(request, mock.baseUrl, {
          twoChapters: true,
        });

      await openContentStep(page, projectId);
      await expect(page.getByLabel(`正文：${TITLE_A}`)).toHaveValue(BODY_A);

      const dialog = await generateSuggestions(page, templateTitle, cardTitle, [
        TITLE_A,
        TITLE_B,
      ]);

      // 双栏预览可见
      await expect(dialog.getByText("当前正文").first()).toBeVisible();
      await expect(dialog.getByText("建议正文").first()).toBeVisible();
      await expect(dialog.getByText(PROPOSED_A)).toBeVisible();
      await expect(dialog.getByText(BODY_A)).toBeVisible();

      // 默认不勾选
      const checkA = dialog.getByLabel(`勾选写入建议 ${TITLE_A}`);
      const checkB = dialog.getByLabel(`勾选写入建议 ${TITLE_B}`);
      await expect(checkA).not.toBeChecked();
      await expect(checkB).not.toBeChecked();
      await expect(checkA).toBeEnabled();
      await expect(checkB).toBeEnabled();

      // 仅勾选 A
      await checkA.check();
      await dialog.getByRole("button", { name: "确认写入所选" }).click();
      await expect(
        dialog.getByTestId("content-fuse-apply-summary"),
      ).toContainText(/已写入 1 条/);

      await dialog.getByRole("button", { name: "关闭", exact: true }).click();
      await expect(dialog).toBeHidden();

      await selectChapterByTitle(page, TITLE_A);
      await expect(page.getByLabel(`正文：${TITLE_A}`)).toHaveValue(PROPOSED_A);
      await selectChapterByTitle(page, TITLE_B);
      await expect(page.getByLabel(`正文：${TITLE_B}`)).toHaveValue(BODY_B);

      // 刷新后保持：先条件轮询 debounce PUT 已落库
      await waitForEditorState(request, projectId, (state) => {
        const a = findChapter(state, CHAP_A);
        const b = findChapter(state, CHAP_B);
        return a?.body === PROPOSED_A && b?.body === BODY_B;
      });
      await page.reload();
      await expect(
        page.getByRole("heading", { name: "E2E 融合写入目标项目" }),
      ).toBeVisible({ timeout: 20_000 });
      await selectChapterByTitle(page, TITLE_A);
      await expect(page.getByLabel(`正文：${TITLE_A}`)).toHaveValue(
        PROPOSED_A,
        { timeout: 15_000 },
      );
      await selectChapterByTitle(page, TITLE_B);
      await expect(page.getByLabel(`正文：${TITLE_B}`)).toHaveValue(BODY_B);
    } finally {
      await mock.close();
    }
  });

  test("生成后改正文/改标题均不可写入；关闭未确认刷新不变", async ({
    page,
    request,
  }) => {
    const mock = await startMockLlmServer();
    try {
      const { projectId, templateTitle, cardTitle } =
        await seedFuseApplyFixtures(request, mock.baseUrl, {
          twoChapters: true,
        });

      await openContentStep(page, projectId);
      const dialog = await generateSuggestions(page, templateTitle, cardTitle, [
        TITLE_A,
        TITLE_B,
      ]);

      // 生成后改正文（遮罩下用原生 input 事件）
      await selectChapterByTitle(page, TITLE_A, true);
      await forceSetChapterBody(page, `${BODY_A}·已漂移`);
      await expect(
        dialog.getByText("正文已变更，基线不匹配").first(),
      ).toBeVisible({ timeout: 5_000 });
      await expect(dialog.getByLabel(`勾选写入建议 ${TITLE_A}`)).toBeDisabled();

      // 改 B 标题
      await selectChapterByTitle(page, TITLE_B, true);
      await forceSetChapterTitle(page, `${TITLE_B}·改名`);
      await expect(
        dialog.getByText("标题已变更，基线不匹配").first(),
      ).toBeVisible({ timeout: 5_000 });
      await expect(dialog.getByLabel(`勾选写入建议 ${TITLE_B}`)).toBeDisabled();

      // 未确认关闭；手工改动应保留，建议正文不得写入
      await dialog.getByRole("button", { name: "关闭", exact: true }).click();
      const driftedBody = `${BODY_A}·已漂移`;
      const renamedTitleB = `${TITLE_B}·改名`;
      // 先确认手工 drift/改名已 debounce 落库，再 reload（避免 sleep 过短丢改动）
      await waitForEditorState(request, projectId, (state) => {
        const a = findChapter(state, CHAP_A);
        const b = findChapter(state, CHAP_B);
        return (
          a?.body === driftedBody &&
          a?.body !== PROPOSED_A &&
          b?.title === renamedTitleB &&
          b?.body === BODY_B &&
          b?.body !== PROPOSED_B
        );
      });
      await page.reload();
      await expect(
        page.getByRole("heading", { name: "E2E 融合写入目标项目" }),
      ).toBeVisible({ timeout: 20_000 });
      await selectChapterByTitle(page, TITLE_A);
      await expect(page.getByLabel(`正文：${TITLE_A}`)).toHaveValue(
        driftedBody,
        { timeout: 15_000 },
      );
      await expect(page.getByLabel(`正文：${TITLE_A}`)).not.toHaveValue(
        PROPOSED_A,
      );
      // B 标题已改名，正文仍为初始
      await selectChapterByTitle(page, renamedTitleB);
      await expect(page.getByLabel(`正文：${renamedTitleB}`)).toHaveValue(
        BODY_B,
      );
      await expect(page.getByLabel(`正文：${renamedTitleB}`)).not.toHaveValue(
        PROPOSED_B,
      );
    } finally {
      await mock.close();
    }
  });

  test("目标章删除路径：幽灵建议禁用；关闭未确认不写", async ({
    page,
    request,
  }) => {
    const mock = await startMockLlmServer();
    try {
      const { projectId, templateTitle, cardTitle } =
        await seedFuseApplyFixtures(request, mock.baseUrl, {
          twoChapters: false,
        });

      // 阻断 SSE，强制走 GET 轮询，便于注入「已删除章节」建议
      await page.route("**/api/projects/**/tasks/**/events**", (route) =>
        route.abort(),
      );
      await page.route(
        /\/api\/projects\/[^/]+\/tasks\/[^/]+(?:\?.*)?$/,
        async (route) => {
          if (route.request().method() !== "GET") {
            await route.continue();
            return;
          }
          const response = await route.fetch();
          const ct = response.headers()["content-type"] || "";
          if (!ct.includes("application/json")) {
            await route.fulfill({ response });
            return;
          }
          const json = (await response.json()) as {
            status?: string;
            result?: {
              suggestions?: Array<Record<string, unknown>>;
            };
          };
          if (
            json.status === "success" &&
            Array.isArray(json.result?.suggestions)
          ) {
            const refs =
              (json.result!.suggestions![0]?.sourceRefs as unknown[]) || [];
            json.result!.suggestions!.push({
              suggestionId: "sug_e2e_deleted",
              targetChapterId: "chap_was_deleted",
              targetTitle: "已删除目标章",
              action: "merge_suggest",
              confidence: 77,
              reason: "E2E删除章",
              sourceRefs: refs,
              base: {
                bodyHash: bodyHash("x"),
                bodyLength: bodyLength("x"),
                title: "已删除目标章",
              },
              currentPreview: "",
              proposedMarkdown: "删除章不应写入",
              diffSummary: "deleted-target",
            });
          }
          await route.fulfill({
            status: response.status(),
            headers: {
              ...response.headers(),
              "content-type": "application/json",
            },
            body: JSON.stringify(json),
          });
        },
      );

      await openContentStep(page, projectId);
      const dialog = await generateSuggestions(page, templateTitle, cardTitle, [
        TITLE_A,
      ]);

      await expect(dialog.getByText("已删除目标章")).toBeVisible({
        timeout: 5_000,
      });
      await expect(
        dialog.getByText("目标章节已删除或不存在").first(),
      ).toBeVisible();
      await expect(
        dialog.getByLabel("勾选写入建议 已删除目标章"),
      ).toBeDisabled();

      // 合法章可勾选但本用例不确认，关闭后正文不变
      await dialog.getByRole("button", { name: "关闭", exact: true }).click();
      // 条件确认 editor-state 仍为初始正文（未误写 proposed）后再 reload
      await waitForEditorState(request, projectId, (state) => {
        const a = findChapter(state, CHAP_A);
        return a?.body === BODY_A && a?.body !== PROPOSED_A;
      });
      await page.reload();
      await expect(
        page.getByRole("heading", { name: "E2E 融合写入目标项目" }),
      ).toBeVisible({ timeout: 20_000 });
      await expect(page.getByLabel(`正文：${TITLE_A}`)).toHaveValue(BODY_A, {
        timeout: 15_000,
      });
      await expect(page.getByLabel(`正文：${TITLE_A}`)).not.toHaveValue(
        PROPOSED_A,
      );
    } finally {
      await mock.close();
    }
  });

  test("M3-C：多章写入后撤销恢复正文与原状态，刷新保持", async ({
    page,
    request,
  }) => {
    const mock = await startMockLlmServer();
    try {
      const { projectId, templateTitle, cardTitle } =
        await seedFuseApplyFixtures(request, mock.baseUrl, {
          twoChapters: true,
        });

      await openContentStep(page, projectId);
      await expect(chapterNavItem(page, TITLE_A)).toContainText("待生成");
      await expect(chapterNavItem(page, TITLE_B)).toContainText("待生成");

      const dialog = await generateSuggestions(page, templateTitle, cardTitle, [
        TITLE_A,
        TITLE_B,
      ]);
      await dialog.getByLabel(`勾选写入建议 ${TITLE_A}`).check();
      await dialog.getByLabel(`勾选写入建议 ${TITLE_B}`).check();
      await dialog.getByRole("button", { name: "确认写入所选" }).click();
      await expect(
        dialog.getByTestId("content-fuse-apply-summary"),
      ).toContainText(/已写入 2 条/);

      await expect(chapterNavItem(page, TITLE_A)).toContainText("待审");
      await expect(chapterNavItem(page, TITLE_B)).toContainText("待审");

      const undoBtn = dialog.getByRole("button", { name: "撤销本次写入" });
      await expect(undoBtn).toBeVisible();
      await undoBtn.click();
      await expect(
        dialog.getByTestId("content-fuse-undo-summary"),
      ).toContainText("已撤销 2 章，跳过 0 章");
      await expect(undoBtn).toBeHidden();

      await dialog.getByRole("button", { name: "关闭", exact: true }).click();
      await selectChapterByTitle(page, TITLE_A);
      await expect(page.getByLabel(`正文：${TITLE_A}`)).toHaveValue(BODY_A);
      await selectChapterByTitle(page, TITLE_B);
      await expect(page.getByLabel(`正文：${TITLE_B}`)).toHaveValue(BODY_B);
      await expect(chapterNavItem(page, TITLE_A)).toContainText("待生成");
      await expect(chapterNavItem(page, TITLE_B)).toContainText("待生成");

      await waitForEditorState(request, projectId, (state) => {
        const a = findChapter(state, CHAP_A);
        const b = findChapter(state, CHAP_B);
        return (
          a?.body === BODY_A &&
          b?.body === BODY_B &&
          a?.status === "pending" &&
          b?.status === "pending"
        );
      });
      await page.reload();
      await expect(
        page.getByRole("heading", { name: "E2E 融合写入目标项目" }),
      ).toBeVisible({ timeout: 20_000 });
      await selectChapterByTitle(page, TITLE_A);
      await expect(page.getByLabel(`正文：${TITLE_A}`)).toHaveValue(BODY_A, {
        timeout: 15_000,
      });
      await selectChapterByTitle(page, TITLE_B);
      await expect(page.getByLabel(`正文：${TITLE_B}`)).toHaveValue(BODY_B);
      await expect(chapterNavItem(page, TITLE_A)).toContainText("待生成");
      await expect(chapterNavItem(page, TITLE_B)).toContainText("待生成");
    } finally {
      await mock.close();
    }
  });

  test("M3-C：写入后手工改一章，撤销仅恢复未漂移章并消费快照", async ({
    page,
    request,
  }) => {
    const mock = await startMockLlmServer();
    try {
      const { projectId, templateTitle, cardTitle } =
        await seedFuseApplyFixtures(request, mock.baseUrl, {
          twoChapters: true,
        });

      await openContentStep(page, projectId);
      const dialog = await generateSuggestions(page, templateTitle, cardTitle, [
        TITLE_A,
        TITLE_B,
      ]);
      await dialog.getByLabel(`勾选写入建议 ${TITLE_A}`).check();
      await dialog.getByLabel(`勾选写入建议 ${TITLE_B}`).check();
      await dialog.getByRole("button", { name: "确认写入所选" }).click();
      await expect(
        dialog.getByTestId("content-fuse-apply-summary"),
      ).toContainText(/已写入 2 条/);

      const driftedBody = `${PROPOSED_A}·手工漂移`;
      await selectChapterByTitle(page, TITLE_A, true);
      await forceSetChapterBody(page, driftedBody);

      const undoBtn = dialog.getByRole("button", { name: "撤销本次写入" });
      await expect(undoBtn).toBeVisible();
      await undoBtn.click();
      await expect(
        dialog.getByTestId("content-fuse-undo-summary"),
      ).toContainText("已撤销 1 章，跳过 1 章");
      await expect(undoBtn).toBeHidden();

      await dialog.getByRole("button", { name: "关闭", exact: true }).click();
      await selectChapterByTitle(page, TITLE_A);
      await expect(page.getByLabel(`正文：${TITLE_A}`)).toHaveValue(driftedBody);
      await selectChapterByTitle(page, TITLE_B);
      await expect(page.getByLabel(`正文：${TITLE_B}`)).toHaveValue(BODY_B);
      await expect(chapterNavItem(page, TITLE_B)).toContainText("待生成");

      await waitForEditorState(request, projectId, (state) => {
        const a = findChapter(state, CHAP_A);
        const b = findChapter(state, CHAP_B);
        return a?.body === driftedBody && b?.body === BODY_B;
      });
      await page.reload();
      await expect(
        page.getByRole("heading", { name: "E2E 融合写入目标项目" }),
      ).toBeVisible({ timeout: 20_000 });
      await selectChapterByTitle(page, TITLE_A);
      await expect(page.getByLabel(`正文：${TITLE_A}`)).toHaveValue(
        driftedBody,
        { timeout: 15_000 },
      );
      await selectChapterByTitle(page, TITLE_B);
      await expect(page.getByLabel(`正文：${TITLE_B}`)).toHaveValue(BODY_B);
    } finally {
      await mock.close();
    }
  });

  test("M3-C：关闭对话框再打开无撤销入口", async ({ page, request }) => {
    const mock = await startMockLlmServer();
    try {
      const { projectId, templateTitle, cardTitle } =
        await seedFuseApplyFixtures(request, mock.baseUrl, {
          twoChapters: true,
        });

      await openContentStep(page, projectId);
      const dialog = await generateSuggestions(page, templateTitle, cardTitle, [
        TITLE_A,
      ]);
      await dialog.getByLabel(`勾选写入建议 ${TITLE_A}`).check();
      await dialog.getByRole("button", { name: "确认写入所选" }).click();
      await expect(
        dialog.getByTestId("content-fuse-apply-summary"),
      ).toContainText(/已写入 1 条/);
      await expect(
        dialog.getByRole("button", { name: "撤销本次写入" }),
      ).toBeVisible();

      await dialog.getByRole("button", { name: "关闭", exact: true }).click();
      await expect(dialog).toBeHidden();

      await page.getByRole("button", { name: "模板卡片融合建议" }).click();
      const reopened = page.getByRole("dialog", { name: "模板卡片融合建议" });
      await expect(reopened).toBeVisible();
      await expect(
        reopened.getByRole("button", { name: "撤销本次写入" }),
      ).toHaveCount(0);
    } finally {
      await mock.close();
    }
  });
});
