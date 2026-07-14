/**
 * 模块：模板/卡片融合建议 M3-B/M3-D 原子确认 E2E
 * 用途：差异预览、勾选确认；服务端原子 POST；确认前零 editor-state PUT；
 *      成功强制 GET；失败固定中文；同章仅一条。
 * 对接：Playwright chromium；后端 8010 / 前端 5174；content_fuse + content-fuse-applications。
 * 二次开发：禁止真实云 Key；本文件内起本地 mock chat completions；
 *       禁止 or True/吞异常/宽泛路由成功；不测持久恢复（见 content-fuse-persistent-recovery）。
 */
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
  type Request,
} from "@playwright/test";
import http from "node:http";
import type { AddressInfo } from "node:net";
import { createHash } from "node:crypto";

const API = "http://127.0.0.1:8010/api";

const TITLE_A = "E2E融合章A 中文";
const TITLE_B = "E2E融合章B";
const BODY_A = "初始正文中文与emoji🚀保持";
const BODY_B = "第二章初始正文，不应被误写。";
const PROPOSED_A = "M3-D确认写入建议A（中文emoji✅）";
const PROPOSED_B = "M3-D确认写入建议B，仅勾选时写入。";
const SECRET_LEAK = "SECRET-LEAK-m3d-apply-detail";

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
  includeGhostInMock?: boolean;
  dualSameChapter?: boolean;
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
        if (opts?.dualSameChapter) {
          items.push(
            {
              targetChapterId: CHAP_A,
              action: "merge_suggest",
              confidence: 88,
              reason: "E2E 同章建议1",
              sourceRefs,
              proposedMarkdown: PROPOSED_A,
              diffSummary: "同章1",
            },
            {
              targetChapterId: CHAP_A,
              action: "merge_suggest",
              confidence: 70,
              reason: "E2E 同章建议2",
              sourceRefs,
              proposedMarkdown: `${PROPOSED_A}·第二条`,
              diffSummary: "同章2",
            },
          );
        } else {
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

async function selectChapterByTitle(page: Page, title: string, force = false) {
  const item = page
    .locator(".tp-content-nav-item")
    .filter({ hasText: title })
    .first();
  if (force) {
    await item.evaluate((el) => (el as HTMLButtonElement).click());
  } else {
    await item.click();
  }
}

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

type NetworkProbe = {
  /** 用途：有序请求日志，格式 kind:path；用于证明 create→editor GET→list GET 严格递增。 */
  orderLog: string[];
  editorPuts: Request[];
  editorGets: Request[];
  applyPosts: Array<{ url: string; body: unknown; headers: Record<string, string> }>;
  listGets: Request[];
  dispose: () => void;
};

/**
 * 用途：精确观测 editor-state PUT/GET 与 content-fuse-applications 请求，并维护有序日志。
 */
function installApplyNetworkProbe(page: Page): NetworkProbe {
  const orderLog: string[] = [];
  const editorPuts: Request[] = [];
  const editorGets: Request[] = [];
  const applyPosts: NetworkProbe["applyPosts"] = [];
  const listGets: Request[] = [];

  const onRequest = (req: Request) => {
    const url = req.url();
    const method = req.method().toUpperCase();
    if (!url.includes("/editor-state")) return;
    const path = new URL(url).pathname;
    if (method === "PUT") {
      editorPuts.push(req);
      orderLog.push(`editor-put:${path}`);
    }
    if (method === "GET") {
      editorGets.push(req);
      orderLog.push(`editor-get:${path}`);
    }
  };

  // 用 route 精确捕获 POST body 与列表 GET（避免重复计数）
  void page.route(
    "**/api/projects/**/content-fuse-applications**",
    async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const path = new URL(req.url()).pathname;
      const isListOrCreateRoot =
        /\/content-fuse-applications\/?$/.test(path) &&
        !path.includes("/consume");
      if (method === "POST" && isListOrCreateRoot) {
        let body: unknown = null;
        try {
          body = req.postDataJSON();
        } catch {
          body = req.postData();
        }
        applyPosts.push({ url: req.url(), body, headers: req.headers() });
        orderLog.push(`apply-post:${path}`);
      }
      if (method === "GET" && isListOrCreateRoot) {
        listGets.push(req);
        orderLog.push(`list-get:${path}`);
      }
      await route.continue();
    },
  );

  page.on("request", onRequest);

  return {
    orderLog,
    editorPuts,
    editorGets,
    applyPosts,
    listGets,
    dispose: () => {
      page.off("request", onRequest);
      void page.unroute("**/api/projects/**/content-fuse-applications**");
    },
  };
}

/**
 * 用途：捕获生成阶段真实 content_fuse 任务 ID（POST 或轮询 GET 成功响应）。
 */
function installTaskIdCapture(page: Page): {
  getTaskId: () => string | null;
  dispose: () => void;
} {
  let taskId: string | null = null;
  const onResponse = async (response: import("@playwright/test").Response) => {
    try {
      const url = response.url();
      if (!url.includes("/tasks") || url.includes("/events")) return;
      if (!response.ok()) return;
      const ct = response.headers()["content-type"] || "";
      if (!ct.includes("application/json")) return;
      const json = (await response.json()) as {
        id?: string;
        type?: string;
        status?: string;
      };
      if (
        typeof json.id === "string" &&
        json.id &&
        (json.type === "content_fuse" || json.status === "success" || json.status === "pending" || json.status === "running")
      ) {
        // 仅在明确 content_fuse 或已有 id 时更新；优先保留非空
        if (json.type === "content_fuse" || taskId === null) {
          taskId = json.id;
        }
        if (json.type === "content_fuse") {
          taskId = json.id;
        }
      }
    } catch {
      /* 非 JSON 忽略 */
    }
  };
  page.on("response", onResponse);
  return {
    getTaskId: () => taskId,
    dispose: () => page.off("response", onResponse),
  };
}

async function fetchBatchCount(
  request: APIRequestContext,
  projectId: string,
): Promise<number> {
  const res = await request.get(
    `${API}/projects/${projectId}/content-fuse-applications`,
  );
  if (!res.ok()) return -1;
  const json = (await res.json()) as { items?: unknown[] };
  return Array.isArray(json.items) ? json.items.length : -1;
}

/** 用途：从勾选框所在建议行读取 data-suggestion-id。 */
async function readSuggestionIdFromDom(
  dialog: import("@playwright/test").Locator,
  title: string,
): Promise<string> {
  const row = dialog
    .locator("[data-suggestion-id]")
    .filter({ has: dialog.page().getByLabel(`勾选写入建议 ${title}`) })
    .first();
  const id = await row.getAttribute("data-suggestion-id");
  expect(id, "DOM 必须暴露 data-suggestion-id").toBeTruthy();
  return id as string;
}

test.describe("模板卡片融合原子确认 M3-D", () => {
  test("原子确认：确认前零 PUT、POST 精确 1、body 精确值、严格重读顺序", async ({
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

      const taskCapture = installTaskIdCapture(page);
      const dialog = await generateSuggestions(page, templateTitle, cardTitle, [
        TITLE_A,
        TITLE_B,
      ]);
      await expect
        .poll(() => taskCapture.getTaskId(), { timeout: 15_000 })
        .toBeTruthy();
      const capturedTaskId = taskCapture.getTaskId() as string;

      await expect(dialog.getByText("当前正文").first()).toBeVisible();
      await expect(dialog.getByText("建议正文").first()).toBeVisible();
      await expect(dialog.getByText(PROPOSED_A)).toBeVisible();
      await expect(dialog.getByText(BODY_A)).toBeVisible();

      const checkA = dialog.getByLabel(`勾选写入建议 ${TITLE_A}`);
      const checkB = dialog.getByLabel(`勾选写入建议 ${TITLE_B}`);
      await expect(checkA).not.toBeChecked();
      await expect(checkB).not.toBeChecked();

      const suggestionIdA = await readSuggestionIdFromDom(dialog, TITLE_A);

      const probe = installApplyNetworkProbe(page);
      const putsBeforeSelect = probe.editorPuts.length;
      await checkA.check();

      // 确认点击前 PUT 不得增加（相对安装探针后）
      expect(probe.editorPuts.length).toBe(putsBeforeSelect);
      expect(probe.applyPosts.length).toBe(0);

      const putsAtClick = probe.editorPuts.length;
      const orderAtClick = probe.orderLog.length;

      await dialog.getByRole("button", { name: "确认写入所选" }).click();

      await expect(
        dialog.getByTestId("content-fuse-apply-summary"),
      ).toContainText(/已写入 1 章/, { timeout: 20_000 });

      // POST 精确 1 次，键集与值精确
      expect(probe.applyPosts.length).toBe(1);
      const postBody = probe.applyPosts[0].body as Record<string, unknown>;
      expect(Object.keys(postBody).sort()).toEqual(
        ["suggestionIds", "taskId"].sort(),
      );
      expect(postBody.taskId).toBe(capturedTaskId);
      expect(postBody.suggestionIds).toEqual([suggestionIdA]);
      // 不得含客户端伪造字段
      expect(postBody).not.toHaveProperty("title");
      expect(postBody).not.toHaveProperty("proposedMarkdown");
      expect(postBody).not.toHaveProperty("base");
      expect(postBody).not.toHaveProperty("action");

      // 在途与确认期间不得因确认路径触发 PUT
      expect(probe.editorPuts.length).toBe(putsAtClick);

      // 相对确认点击后：create POST → 唯一一次 editor-state GET → 列表 GET，严格递增
      await expect
        .poll(
          () => {
            const after = probe.orderLog.slice(orderAtClick);
            const applyIdx = after.findIndex((x) => x.startsWith("apply-post:"));
            const editorIdx = after.findIndex((x) =>
              x.startsWith("editor-get:"),
            );
            const listIdx = after.findIndex((x) => x.startsWith("list-get:"));
            const editorGetCount = after.filter((x) =>
              x.startsWith("editor-get:"),
            ).length;
            return (
              applyIdx >= 0 &&
              editorIdx >= 0 &&
              listIdx >= 0 &&
              applyIdx < editorIdx &&
              editorIdx < listIdx &&
              editorGetCount === 1
            );
          },
          { timeout: 15_000 },
        )
        .toBe(true);
      {
        const after = probe.orderLog.slice(orderAtClick);
        const applyIdx = after.findIndex((x) => x.startsWith("apply-post:"));
        const editorIdx = after.findIndex((x) => x.startsWith("editor-get:"));
        const listIdx = after.findIndex((x) => x.startsWith("list-get:"));
        const editorGetCount = after.filter((x) =>
          x.startsWith("editor-get:"),
        ).length;
        expect(applyIdx).toBeGreaterThanOrEqual(0);
        expect(editorIdx).toBeGreaterThanOrEqual(0);
        expect(listIdx).toBeGreaterThanOrEqual(0);
        expect(applyIdx).toBeLessThan(editorIdx);
        expect(editorIdx).toBeLessThan(listIdx);
        // 锁死单次实际重载：禁止探测 GET + reload GET 双次掩盖失败
        expect(editorGetCount).toBe(1);
        expect(probe.editorGets.length).toBe(1);
      }

      await dialog.getByRole("button", { name: "关闭", exact: true }).click();
      await expect(dialog).toBeHidden();

      await selectChapterByTitle(page, TITLE_A);
      await expect(page.getByLabel(`正文：${TITLE_A}`)).toHaveValue(PROPOSED_A);
      await selectChapterByTitle(page, TITLE_B);
      await expect(page.getByLabel(`正文：${TITLE_B}`)).toHaveValue(BODY_B);

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

      taskCapture.dispose();
      probe.dispose();
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

      await selectChapterByTitle(page, TITLE_A, true);
      await forceSetChapterBody(page, `${BODY_A}·已漂移`);
      await expect(
        dialog.getByText("正文已变更，基线不匹配").first(),
      ).toBeVisible({ timeout: 5_000 });
      await expect(dialog.getByLabel(`勾选写入建议 ${TITLE_A}`)).toBeDisabled();

      await selectChapterByTitle(page, TITLE_B, true);
      await forceSetChapterTitle(page, `${TITLE_B}·改名`);
      await expect(
        dialog.getByText("标题已变更，基线不匹配").first(),
      ).toBeVisible({ timeout: 5_000 });
      await expect(dialog.getByLabel(`勾选写入建议 ${TITLE_B}`)).toBeDisabled();

      await dialog.getByRole("button", { name: "关闭", exact: true }).click();
      const driftedBody = `${BODY_A}·已漂移`;
      const renamedTitleB = `${TITLE_B}·改名`;
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

      await dialog.getByRole("button", { name: "关闭", exact: true }).click();
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
    } finally {
      await mock.close();
    }
  });

  test("POST 409/500：正文与 PUT 不变，批次 0，console 精确空，body 键值正确", async ({
    page,
    request,
  }) => {
    const mock = await startMockLlmServer();
    try {
      const { projectId, templateTitle, cardTitle } =
        await seedFuseApplyFixtures(request, mock.baseUrl, {
          twoChapters: false,
        });

      const consoleErrWarn: string[] = [];
      page.on("console", (msg) => {
        if (msg.type() === "error" || msg.type() === "warning") {
          consoleErrWarn.push(`${msg.type()}: ${msg.text()}`);
        }
      });

      await openContentStep(page, projectId);
      const taskCapture = installTaskIdCapture(page);
      const dialog = await generateSuggestions(page, templateTitle, cardTitle, [
        TITLE_A,
      ]);
      await expect
        .poll(() => taskCapture.getTaskId(), { timeout: 15_000 })
        .toBeTruthy();
      const capturedTaskId = taskCapture.getTaskId() as string;
      const suggestionIdA = await readSuggestionIdFromDom(dialog, TITLE_A);
      await dialog.getByLabel(`勾选写入建议 ${TITLE_A}`).check();

      const batchesBefore = await fetchBatchCount(request, projectId);
      expect(batchesBefore).toBe(0);

      // 先装观测探针，再装失败桩（后注册优先，避免 continue 打到真后端）
      const probe = installApplyNetworkProbe(page);
      let failStatus = 409;
      await page.route(
        "**/api/projects/**/content-fuse-applications**",
        async (route) => {
          const method = route.request().method().toUpperCase();
          const path = new URL(route.request().url()).pathname;
          if (
            method === "POST" &&
            /\/content-fuse-applications\/?$/.test(path) &&
            !path.includes("/consume")
          ) {
            let body: unknown = null;
            try {
              body = route.request().postDataJSON();
            } catch {
              body = route.request().postData();
            }
            probe.applyPosts.push({
              url: route.request().url(),
              body,
              headers: route.request().headers(),
            });
            probe.orderLog.push(`apply-post:${path}`);
            await route.fulfill({
              status: failStatus,
              contentType: "application/json",
              body: JSON.stringify({
                detail: {
                  code:
                    failStatus === 409
                      ? "content_fuse_apply_conflict"
                      : "internal_error",
                  message: SECRET_LEAK,
                },
              }),
            });
            return;
          }
          await route.continue();
        },
      );

      const putsBefore = probe.editorPuts.length;

      await dialog.getByRole("button", { name: "确认写入所选" }).click();
      await expect(dialog.getByTestId("content-fuse-local-error")).toHaveText(
        "融合确认失败，请刷新后重试",
        { timeout: 10_000 },
      );

      expect(probe.applyPosts.length).toBe(1);
      const body409 = probe.applyPosts[0].body as Record<string, unknown>;
      expect(Object.keys(body409).sort()).toEqual(
        ["suggestionIds", "taskId"].sort(),
      );
      expect(body409.taskId).toBe(capturedTaskId);
      expect(body409.suggestionIds).toEqual([suggestionIdA]);
      expect(probe.editorPuts.length).toBe(putsBefore);
      await expect(page.getByLabel(`正文：${TITLE_A}`)).toHaveValue(BODY_A);
      expect(await fetchBatchCount(request, projectId)).toBe(0);

      const pageText = await page.locator("body").innerText();
      expect(pageText).not.toContain(SECRET_LEAK);
      expect(pageText).not.toContain("content_fuse_apply_conflict");
      expect(pageText).not.toContain("/content-fuse-applications");
      expect(pageText).not.toContain(projectId);
      expect(pageText).not.toContain(capturedTaskId);
      expect(pageText).not.toContain(suggestionIdA);
      // 应用层 console error/warning 必须精确 []（排除浏览器网络层 4xx 噪声）
      const appConsole = (lines: string[]) =>
        lines.filter((l) => !/^error: Failed to load resource:/.test(l));
      expect(appConsole(consoleErrWarn)).toEqual([]);
      for (const line of consoleErrWarn) {
        expect(line).not.toContain(SECRET_LEAK);
        expect(line).not.toContain(projectId);
        expect(line).not.toContain(capturedTaskId);
        expect(line).not.toContain(suggestionIdA);
        expect(line).not.toContain("/content-fuse-applications");
        expect(line).not.toContain("content_fuse_apply_conflict");
      }

      // 再测 500
      failStatus = 500;
      const putsBefore500 = probe.editorPuts.length;
      const postsBefore500 = probe.applyPosts.length;
      await dialog.getByRole("button", { name: "确认写入所选" }).click();
      await expect(dialog.getByTestId("content-fuse-local-error")).toHaveText(
        "融合确认失败，请刷新后重试",
        { timeout: 10_000 },
      );
      expect(probe.applyPosts.length).toBe(postsBefore500 + 1);
      const body500 = probe.applyPosts[postsBefore500]
        .body as Record<string, unknown>;
      expect(body500.taskId).toBe(capturedTaskId);
      expect(body500.suggestionIds).toEqual([suggestionIdA]);
      expect(probe.editorPuts.length).toBe(putsBefore500);
      await expect(page.getByLabel(`正文：${TITLE_A}`)).toHaveValue(BODY_A);
      expect(await fetchBatchCount(request, projectId)).toBe(0);
      expect(appConsole(consoleErrWarn)).toEqual([]);

      taskCapture.dispose();
      probe.dispose();
    } finally {
      await mock.close();
    }
  });

  test("POST 成功但 editor-state 重读失败：已写入提示、批次已建、禁止二次 create", async ({
    page,
    request,
  }) => {
    const mock = await startMockLlmServer();
    try {
      const { projectId, templateTitle, cardTitle } =
        await seedFuseApplyFixtures(request, mock.baseUrl, {
          twoChapters: false,
        });

      const consoleErrWarn: string[] = [];
      page.on("console", (msg) => {
        if (msg.type() === "error" || msg.type() === "warning") {
          consoleErrWarn.push(`${msg.type()}: ${msg.text()}`);
        }
      });

      await openContentStep(page, projectId);
      const taskCapture = installTaskIdCapture(page);
      const dialog = await generateSuggestions(page, templateTitle, cardTitle, [
        TITLE_A,
      ]);
      await expect
        .poll(() => taskCapture.getTaskId(), { timeout: 15_000 })
        .toBeTruthy();
      const capturedTaskId = taskCapture.getTaskId() as string;
      const suggestionIdA = await readSuggestionIdFromDom(dialog, TITLE_A);
      await dialog.getByLabel(`勾选写入建议 ${TITLE_A}`).check();

      const probe = installApplyNetworkProbe(page);
      let createSucceeded = false;
      // create 成功后阻断唯一一次实际 editor-state GET（onReloadFromApi）
      let blockEditorGetAfterCreate = false;
      let blockedEditorGetCount = 0;

      // 放行 create POST，成功后阻断后续 editor-state GET
      await page.route(
        "**/api/projects/**/content-fuse-applications**",
        async (route) => {
          const method = route.request().method().toUpperCase();
          const path = new URL(route.request().url()).pathname;
          if (
            method === "POST" &&
            /\/content-fuse-applications\/?$/.test(path) &&
            !path.includes("/consume")
          ) {
            const response = await route.fetch();
            createSucceeded = response.status() === 201;
            if (createSucceeded) {
              blockEditorGetAfterCreate = true;
            }
            let body: unknown = null;
            try {
              body = route.request().postDataJSON();
            } catch {
              body = route.request().postData();
            }
            probe.applyPosts.push({
              url: route.request().url(),
              body,
              headers: route.request().headers(),
            });
            probe.orderLog.push(`apply-post:${path}`);
            await route.fulfill({ response });
            return;
          }
          await route.continue();
        },
      );
      await page.route("**/api/projects/**/editor-state**", async (route) => {
        const method = route.request().method().toUpperCase();
        const path = new URL(route.request().url()).pathname;
        if (method === "GET" && blockEditorGetAfterCreate) {
          blockedEditorGetCount += 1;
          probe.orderLog.push(`editor-get-fail:${path}`);
          await route.fulfill({
            status: 500,
            contentType: "application/json",
            body: JSON.stringify({
              detail: { code: "internal_error", message: SECRET_LEAK },
            }),
          });
          return;
        }
        await route.continue();
      });

      await dialog.getByRole("button", { name: "确认写入所选" }).click();
      await expect(dialog.getByTestId("content-fuse-local-error")).toHaveText(
        "融合已写入，但刷新失败，请关闭后重新打开",
        { timeout: 15_000 },
      );
      // 不得谎报业务失败
      await expect(dialog.getByText("融合确认失败，请刷新后重试")).toHaveCount(
        0,
      );

      expect(probe.applyPosts.length).toBe(1);
      const postBody = probe.applyPosts[0].body as Record<string, unknown>;
      expect(postBody.taskId).toBe(capturedTaskId);
      expect(postBody.suggestionIds).toEqual([suggestionIdA]);

      // 服务端批次已变化
      await expect
        .poll(async () => fetchBatchCount(request, projectId), {
          timeout: 10_000,
        })
        .toBe(1);

      // 唯一一次失败 GET（探针 request 事件 + 阻断路由各记一条，合计仅一轮）
      expect(blockedEditorGetCount).toBe(1);
      expect(
        probe.orderLog.filter((x) => x.startsWith("editor-get-fail:")).length,
      ).toBe(1);
      // request 监听也会记 editor-get；必须精确 1，禁止双次实际重载
      expect(probe.editorGets.length).toBe(1);
      expect(
        probe.orderLog.filter((x) => x.startsWith("editor-get:")).length,
      ).toBe(1);

      // 已应用：确认按钮应禁用（无可选勾选），再点不得二次 create
      const postsAfter = probe.applyPosts.length;
      const confirmBtn = dialog.getByRole("button", { name: /确认写入所选/ });
      await expect(confirmBtn).toBeDisabled();
      // 尝试再次点击（disabled 应无请求）
      await confirmBtn.click({ force: true }).catch(() => undefined);
      expect(probe.applyPosts.length).toBe(postsAfter);
      expect(blockedEditorGetCount).toBe(1);
      expect(probe.editorGets.length).toBe(1);

      const pageText = await page.locator("body").innerText();
      expect(pageText).not.toContain(SECRET_LEAK);
      expect(pageText).not.toContain(projectId);
      expect(pageText).not.toContain(capturedTaskId);
      expect(pageText).not.toContain(suggestionIdA);
      expect(pageText).not.toContain("/content-fuse-applications");
      expect(pageText).not.toContain("internal_error");
      const appConsoleReload = consoleErrWarn.filter(
        (l) => !/^error: Failed to load resource:/.test(l),
      );
      expect(appConsoleReload).toEqual([]);
      for (const line of consoleErrWarn) {
        expect(line).not.toContain(SECRET_LEAK);
        expect(line).not.toContain(projectId);
        expect(line).not.toContain(capturedTaskId);
        expect(line).not.toContain(suggestionIdA);
        expect(line).not.toContain("internal_error");
      }

      taskCapture.dispose();
      probe.dispose();
    } finally {
      await mock.close();
    }
  });

  test("同章双建议：第二条不能选，必须见固定中文提示", async ({
    page,
    request,
  }) => {
    const mock = await startMockLlmServer();
    try {
      const { projectId, templateTitle, cardTitle } =
        await seedFuseApplyFixtures(request, mock.baseUrl, {
          twoChapters: false,
        });

      // 阻断 SSE，强制轮询；注入同章第二条建议（后端任务结果可能折叠同章）
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
            Array.isArray(json.result?.suggestions) &&
            json.result!.suggestions!.length >= 1
          ) {
            const first = json.result!.suggestions![0];
            json.result!.suggestions!.push({
              ...first,
              suggestionId: "sug_e2e_same_chapter_2",
              reason: "E2E 同章第二条",
              proposedMarkdown: `${PROPOSED_A}·第二条`,
              diffSummary: "同章2",
              confidence: 70,
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

      // 同章两条建议均可见且可勾（不能靠隐藏规避）
      const checks = dialog.getByLabel(new RegExp(`勾选写入建议 ${TITLE_A}`));
      await expect(checks).toHaveCount(2);
      await expect(checks.nth(0)).toBeEnabled();
      await expect(checks.nth(1)).toBeEnabled();

      await checks.nth(0).check();
      await expect(checks.nth(0)).toBeChecked();

      // 试图勾第二条：用 click（check 会因未选中而失败）；保持未选 + 固定中文
      await checks.nth(1).click();
      await expect(checks.nth(1)).not.toBeChecked();
      await expect(dialog.getByTestId("content-fuse-local-error")).toHaveText(
        "同一目标章节只能选择一条建议",
      );
      // 第一条仍选中，确认按钮可见（不靠隐藏规避）
      await expect(checks.nth(0)).toBeChecked();
      await expect(
        dialog.getByRole("button", { name: /确认写入所选/ }),
      ).toBeVisible();
      await expect(
        dialog.getByRole("button", { name: /确认写入所选/ }),
      ).toBeEnabled();
    } finally {
      await mock.close();
    }
  });
});
