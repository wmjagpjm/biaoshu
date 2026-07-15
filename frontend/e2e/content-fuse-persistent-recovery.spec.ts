/**
 * 模块：融合写入持久恢复批次 M3-D E2E
 * 用途：跨刷新 active 批次仍可恢复；完整/部分/零恢复；一次消费；
 *      二次确认取消；项目/关闭迟到隔离；未知 API/外网阻断；零浏览器存储。
 * 对接：Playwright chromium；后端 8010 / 前端 5174；content-fuse-applications。
 * 二次开发：禁止 or True、宽泛 status 集合、吞异常、filter(Boolean) 藏空名；
 *       探针安装失败必须失败；不得只断言 DOM 残留代替服务端状态。
 */
import {
  expect,
  test,
  type APIRequestContext,
  type ConsoleMessage,
  type Page,
} from "@playwright/test";
import http from "node:http";
import type { AddressInfo } from "node:net";

const API = "http://127.0.0.1:8010/api";

const TITLE_A = "E2E恢复章A 中文";
const TITLE_B = "E2E恢复章B";
const BODY_A = "恢复测初始A正文emoji🔒";
const BODY_B = "恢复测初始B正文。";
const PROPOSED_A = "恢复测建议A写入后✅";
const PROPOSED_B = "恢复测建议B写入后。";
const SECRET = "SECRET-LEAK-m3d-recovery";

const CHAP_A = "chap_e2e_rec_a";
const CHAP_B = "chap_e2e_rec_b";

async function startMockLlmServer(): Promise<{
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
          /* 原文 */
        }
        const sourceRefs: Array<{ kind: string; id: string; title: string }> =
          [];
        const tplMatch = /模板 id=([^\s]+) title=([^\n]+)/.exec(promptText);
        if (tplMatch) {
          sourceRefs.push({
            kind: "template",
            id: tplMatch[1],
            title: "tpl",
          });
        }
        const cardMatch = /卡片 id=([^\s]+) type=\S+ title=([^\n]+)/.exec(
          promptText,
        );
        if (cardMatch) {
          sourceRefs.push({
            kind: "card",
            id: cardMatch[1],
            title: "card",
          });
        }
        const items: Array<Record<string, unknown>> = [];
        if (promptText.includes(CHAP_A)) {
          items.push({
            targetChapterId: CHAP_A,
            action: "merge_suggest",
            confidence: 90,
            reason: "rec-A",
            sourceRefs,
            proposedMarkdown: PROPOSED_A,
            diffSummary: "A",
          });
        }
        if (promptText.includes(CHAP_B)) {
          items.push({
            targetChapterId: CHAP_B,
            action: "merge_suggest",
            confidence: 85,
            reason: "rec-B",
            sourceRefs,
            proposedMarkdown: PROPOSED_B,
            diffSummary: "B",
          });
        }
        if (items.length === 0) {
          items.push({
            targetChapterId: CHAP_A,
            action: "merge_suggest",
            confidence: 70,
            reason: "fallback",
            sourceRefs,
            proposedMarkdown: PROPOSED_A,
            diffSummary: "fb",
          });
        }
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(
          JSON.stringify({
            id: "chatcmpl-e2e-fuse-rec",
            object: "chat.completion",
            model: "e2e-mock-fuse-rec",
            choices: [
              {
                index: 0,
                message: {
                  role: "assistant",
                  content: JSON.stringify(items),
                },
                finish_reason: "stop",
              },
            ],
          }),
        );
      });
      return;
    }
    res.writeHead(404);
    res.end("not found");
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
  const addr = server.address() as AddressInfo;
  return {
    baseUrl: `http://127.0.0.1:${addr.port}/v1`,
    close: () =>
      new Promise<void>((resolve, reject) => {
        server.close((e) => (e ? reject(e) : resolve()));
      }),
  };
}

/**
 * 用途：可变服务端状态桩 —— 在真实 editor-state / 批次 API 之上叠加可观测计数与延迟。
 * 跨 reload 仍由真实后端保持 active 批次（不是 DOM 残留）。
 * 网络默认拒绝：仅 method + 精确路径/受控正则白名单；未知 /api 记 forbiddenHits 并 403。
 */
type RecoveryProbe = {
  applyPosts: Array<{ path: string; body: unknown }>;
  consumePosts: Array<{ path: string; body: string | null }>;
  editorGets: string[];
  listGets: string[];
  forbiddenHits: string[];
  externalHits: string[];
  orderLog: string[];
  dispose: () => Promise<void>;
};

function isLegacyFontUrl(url: string): boolean {
  return (
    url.includes("fonts.googleapis.com") || url.includes("fonts.gstatic.com")
  );
}

/**
 * 用途：本用例真实需要的 API 白名单（method + 路径正则）；禁止宽放 /api/projects 前缀。
 * 项目段仅允许服务端真实 id 形如 proj_*，故 /api/projects/unknown-m3d-probe 不匹配并被阻断。
 */
function isAllowedM3dApi(method: string, path: string): boolean {
  // 服务端项目 id：proj_{8hex}_{4hex}；任务/批次 id 为不透明串
  const pid = "proj_[a-f0-9]+_[a-f0-9]+";
  const rules: Array<{ methods: string[]; path: RegExp }> = [
    { methods: ["GET"], path: /^\/api\/health\/?$/ },
    { methods: ["GET", "POST"], path: /^\/api\/auth(\/|$)/ },
    { methods: ["GET"], path: /^\/api\/workspace\/?$/ },
    { methods: ["GET"], path: /^\/api\/workspaces(\/|$)/ },
    { methods: ["GET", "PUT"], path: /^\/api\/settings\/?$/ },
    { methods: ["GET"], path: /^\/api\/templates(\/|$)/ },
    { methods: ["GET"], path: /^\/api\/cards(\/|$)/ },
    // 项目列表（侧栏可能触发）与详情
    { methods: ["GET"], path: /^\/api\/projects\/?$/ },
    { methods: ["GET"], path: new RegExp(`^/api/projects/${pid}/?$`) },
    {
      methods: ["GET", "PUT"],
      path: new RegExp(`^/api/projects/${pid}/editor-state/?$`),
    },
    {
      methods: ["POST"],
      path: new RegExp(`^/api/projects/${pid}/tasks/?$`),
    },
    {
      methods: ["GET", "POST"],
      path: new RegExp(
        `^/api/projects/${pid}/tasks/[^/]+(/(events|cancel))?/?$`,
      ),
    },
    {
      methods: ["GET", "POST"],
      path: new RegExp(
        `^/api/projects/${pid}/content-fuse-applications/?$`,
      ),
    },
    {
      methods: ["POST"],
      path: new RegExp(
        `^/api/projects/${pid}/content-fuse-applications/[^/]+/consume/?$`,
      ),
    },
  ];
  return rules.some(
    (r) => r.methods.includes(method) && r.path.test(path),
  );
}

async function installRecoveryProbes(
  page: Page,
  opts?: {
    delayListMs?: number;
    delayApplyMs?: number;
    delayConsumeMs?: number;
  },
): Promise<RecoveryProbe> {
  const applyPosts: RecoveryProbe["applyPosts"] = [];
  const consumePosts: RecoveryProbe["consumePosts"] = [];
  const editorGets: string[] = [];
  const listGets: string[] = [];
  const forbiddenHits: string[] = [];
  const externalHits: string[] = [];
  const orderLog: string[] = [];

  // 外网与未知本机 API：可观测阻断（字体排除）
  await page.route("**/*", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const host = url.hostname;
    const path = url.pathname;
    const method = req.method().toUpperCase();

    if (isLegacyFontUrl(url.href)) {
      await route.continue();
      return;
    }

    if (host !== "127.0.0.1" && host !== "localhost") {
      externalHits.push(`${method} ${url.href}`);
      await route.abort("failed");
      return;
    }

    if (!path.startsWith("/api")) {
      await route.continue();
      return;
    }

    if (!isAllowedM3dApi(method, path)) {
      forbiddenHits.push(`${method} ${path}`);
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({
          detail: { code: "role_forbidden", message: "未授权业务接口" },
        }),
      });
      return;
    }

    // editor-state GET 观测
    if (method === "GET" && /\/editor-state\/?$/.test(path)) {
      editorGets.push(path);
      orderLog.push(`editor-get:${path}`);
    }

    // 列表 GET
    if (
      method === "GET" &&
      /\/content-fuse-applications\/?$/.test(path)
    ) {
      listGets.push(path);
      orderLog.push(`list-get:${path}`);
      if (opts?.delayListMs && opts.delayListMs > 0) {
        await new Promise((r) => setTimeout(r, opts.delayListMs));
      }
    }

    // 原子确认 POST
    if (
      method === "POST" &&
      /\/content-fuse-applications\/?$/.test(path) &&
      !path.includes("/consume")
    ) {
      let body: unknown = null;
      try {
        body = req.postDataJSON();
      } catch {
        body = req.postData();
      }
      applyPosts.push({ path, body });
      orderLog.push(`apply-post:${path}`);
      if (opts?.delayApplyMs && opts.delayApplyMs > 0) {
        await new Promise((r) => setTimeout(r, opts.delayApplyMs));
      }
    }

    // consume POST
    if (
      method === "POST" &&
      /\/content-fuse-applications\/[^/]+\/consume\/?$/.test(path)
    ) {
      consumePosts.push({ path, body: req.postData() });
      orderLog.push(`consume-post:${path}`);
      if (opts?.delayConsumeMs && opts.delayConsumeMs > 0) {
        await new Promise((r) => setTimeout(r, opts.delayConsumeMs));
      }
    }

    await route.continue();
  });

  return {
    applyPosts,
    consumePosts,
    editorGets,
    listGets,
    forbiddenHits,
    externalHits,
    orderLog,
    dispose: async () => {
      await page.unroute("**/*");
    },
  };
}

/**
 * 用途：相对 orderBefore 断言 consume → 唯一一次 editor-state GET → list GET。
 * 索引均 >=0 且严格递增；editor-state GET 精确 1 次，禁止双次 GET 掩盖更新失败。
 */
function assertRestoreOrder(orderLog: string[], orderBefore: number) {
  const after = orderLog.slice(orderBefore);
  const consumeIdx = after.findIndex((x) => x.startsWith("consume-post:"));
  const editorIdx = after.findIndex((x) => x.startsWith("editor-get:"));
  const listIdx = after.findIndex((x) => x.startsWith("list-get:"));
  const editorGetCount = after.filter((x) => x.startsWith("editor-get:")).length;
  expect(consumeIdx, `consume 索引应>=0，after=${JSON.stringify(after)}`).toBeGreaterThanOrEqual(0);
  expect(editorIdx, `editor GET 索引应>=0，after=${JSON.stringify(after)}`).toBeGreaterThanOrEqual(0);
  expect(listIdx, `list GET 索引应>=0，after=${JSON.stringify(after)}`).toBeGreaterThanOrEqual(0);
  expect(consumeIdx).toBeLessThan(editorIdx);
  expect(editorIdx).toBeLessThan(listIdx);
  expect(
    editorGetCount,
    `editor GET 必须精确 1 次，after=${JSON.stringify(after)}`,
  ).toBe(1);
}

async function seedRecoveryFixtures(
  request: APIRequestContext,
  mockBase: string,
  opts?: { name?: string },
) {
  const settings = await request.put(`${API}/settings`, {
    data: {
      provider: "openai-compatible",
      apiBaseUrl: mockBase,
      apiKey: "e2e-local-mock",
      model: "e2e-mock-fuse-rec",
    },
  });
  expect(settings.ok()).toBeTruthy();

  const source = await request.post(`${API}/projects`, {
    data: { name: "E2E 恢复模板源", kind: "technical", industry: "政务" },
  });
  expect(source.ok()).toBeTruthy();
  const sourceProject = (await source.json()) as { id: string };
  expect(
    (
      await request.put(`${API}/projects/${sourceProject.id}/editor-state`, {
        data: {
          outline: [{ id: "n", title: TITLE_A, children: [] }],
          chapters: [{ id: "c", title: TITLE_A, body: "模板正文" }],
          mode: "ALIGNED",
        },
      })
    ).ok(),
  ).toBeTruthy();

  const tpl = await request.post(`${API}/templates/from-project`, {
    data: {
      projectId: sourceProject.id,
      title: `E2E恢复模板-${Date.now()}`,
      tags: ["E2E"],
    },
  });
  expect(tpl.ok()).toBeTruthy();
  const template = (await tpl.json()) as { id: string; title: string };

  const card = await request.post(`${API}/cards`, {
    data: {
      type: "document",
      title: `E2E恢复卡片-${Date.now()}`,
      bodyMarkdown: "恢复卡片正文",
      tags: ["E2E"],
      sourceLabel: "E2E",
    },
  });
  expect(card.ok()).toBeTruthy();
  const cardBody = (await card.json()) as { id: string; title: string };

  const target = await request.post(`${API}/projects`, {
    data: {
      name: opts?.name ?? "E2E 融合恢复目标项目",
      kind: "technical",
      industry: "政务",
    },
  });
  expect(target.ok()).toBeTruthy();
  const project = (await target.json()) as { id: string };
  expect(
    (
      await request.put(`${API}/projects/${project.id}/editor-state`, {
        data: {
          outline: [
            { id: "node_a", title: TITLE_A, children: [] },
            { id: "node_b", title: TITLE_B, children: [] },
          ],
          chapters: [
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
          ],
          mode: "ALIGNED",
        },
      })
    ).ok(),
  ).toBeTruthy();

  return {
    projectId: project.id,
    templateTitle: template.title,
    cardTitle: cardBody.title,
  };
}

async function openContent(page: Page, projectId: string, heading: string) {
  await page.goto(`/technical-plan/${projectId}/content`);
  await expect(page.getByRole("heading", { name: heading })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByLabel(`正文：${TITLE_A}`)).toBeVisible({
    timeout: 15_000,
  });
}

async function generateAndApplyBoth(
  page: Page,
  templateTitle: string,
  cardTitle: string,
) {
  await page.getByRole("button", { name: "模板卡片融合建议" }).click();
  const dialog = page.getByRole("dialog", { name: "模板卡片融合建议" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel(`模板 ${templateTitle}`).check();
  await dialog.getByLabel(`卡片 ${cardTitle}`).check();
  await dialog.getByLabel(`目标章节 ${TITLE_A}`).check();
  await dialog.getByLabel(`目标章节 ${TITLE_B}`).check();
  await dialog.getByRole("button", { name: "生成只读融合建议" }).click();
  await expect(dialog.getByText(/已生成 \d+ 条只读建议/)).toBeVisible({
    timeout: 30_000,
  });
  await dialog.getByLabel(`勾选写入建议 ${TITLE_A}`).check();
  await dialog.getByLabel(`勾选写入建议 ${TITLE_B}`).check();
  await dialog.getByRole("button", { name: "确认写入所选" }).click();
  await expect(dialog.getByTestId("content-fuse-apply-summary")).toContainText(
    /已写入 2 章/,
    { timeout: 20_000 },
  );
  return dialog;
}

async function selectChapterByTitle(page: Page, title: string, force = false) {
  const item = page
    .locator(".tp-content-nav-item")
    .filter({ hasText: title })
    .first();
  // force：Dialog 打开时 backdrop 拦截 pointer，用原生 click 绕过（仅断言路径）
  if (force) {
    await item.evaluate((el) => (el as HTMLButtonElement).click());
  } else {
    await item.click();
  }
}

type BatchList = {
  items: Array<{
    batchId: string;
    chapterCount: number;
    state: string;
    createdAt: string;
    consumedAt: string | null;
  }>;
};

async function fetchBatches(
  request: APIRequestContext,
  projectId: string,
): Promise<BatchList> {
  const res = await request.get(
    `${API}/projects/${projectId}/content-fuse-applications`,
  );
  expect(res.ok()).toBeTruthy();
  return (await res.json()) as BatchList;
}

async function fetchEditorState(
  request: APIRequestContext,
  projectId: string,
) {
  const res = await request.get(
    `${API}/projects/${projectId}/editor-state`,
  );
  expect(res.ok()).toBeTruthy();
  return (await res.json()) as {
    chapters: Array<{ id: string; title: string; body: string; status?: string }>;
  };
}

/**
 * 用途：读取 localStorage 完整键集（排序）；键用 key(i)??""，禁止 if(key) 隐藏空键。
 */
async function readLocalStorageKeySet(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const keys: string[] = [];
    for (let i = 0; i < window.localStorage.length; i += 1) {
      keys.push(window.localStorage.key(i) ?? "");
    }
    return keys.sort();
  });
}

/**
 * 用途：断言 M3-D 未新增 localStorage 键，且未把 task/batch 写入任何存储。
 * 说明：localStorage 键集必须与打开 M3-D Dialog 前的基线精确全等（排序后）；
 *      session/IndexedDB/Cookie 必须精确空；值不得含 ID/API 路径/秘密串。
 * 二次开发：IndexedDB 不可用或 databases 非函数必须失败，禁止 catch 伪装 []。
 */
async function assertNoSensitiveStorage(
  page: Page,
  forbiddenSnippets: string[],
  localKeyBaseline: string[],
) {
  const storage = await page.evaluate(async () => {
    const dump = (store: Storage) => {
      const out: Record<string, string> = {};
      const keys: string[] = [];
      for (let i = 0; i < store.length; i += 1) {
        const key = store.key(i) ?? "";
        keys.push(key);
        out[key] = store.getItem(key) ?? "";
      }
      return { map: out, keys };
    };
    if (typeof indexedDB === "undefined") {
      throw new Error("indexedDB 不可用");
    }
    if (typeof indexedDB.databases !== "function") {
      throw new Error("indexedDB.databases 必须为函数");
    }
    const dbs = await indexedDB.databases();
    // 保留全部 name??""，禁止 filter(Boolean)
    const idbNames = dbs.map((d) => d.name ?? "");
    const local = dump(window.localStorage);
    const session = dump(window.sessionStorage);
    return {
      localMap: local.map,
      localKeys: local.keys.sort(),
      sessionKeys: session.keys.sort(),
      sessionMap: session.map,
      cookies: document.cookie,
      idbNames,
    };
  });

  // localStorage 键集与基线精确全等（排序后）
  expect(storage.localKeys).toEqual([...localKeyBaseline].sort());

  // session / cookie / idb 必须精确空
  expect(storage.sessionKeys).toEqual([]);
  expect(storage.cookies.trim()).toBe("");
  expect(storage.idbNames).toEqual([]);

  for (const [key, value] of Object.entries(storage.localMap)) {
    for (const snip of forbiddenSnippets) {
      expect(
        value.includes(snip),
        `localStorage[${key}] 含敏感片段 ${snip}`,
      ).toBe(false);
    }
    expect(value).not.toContain("content-fuse-applications");
    expect(value).not.toMatch(/cfab_/);
    expect(value).not.toMatch(/"taskId"/);
    expect(value).not.toMatch(/"batchId"/);
  }
}

async function installClipboardProbe(page: Page) {
  await page.addInitScript(() => {
    const w = window as unknown as {
      __m3dClipRead?: number;
      __m3dClipWrite?: number;
      __m3dClipInstalled?: boolean;
    };
    w.__m3dClipRead = 0;
    w.__m3dClipWrite = 0;
    w.__m3dClipInstalled = false;
    const clip = navigator.clipboard ?? ({} as Clipboard);
    try {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: clip,
      });
    } catch {
      /* 已存在 */
    }
    const target = navigator.clipboard;
    if (!target) {
      w.__m3dClipInstalled = false;
      return;
    }
    target.readText = async () => {
      w.__m3dClipRead = (w.__m3dClipRead ?? 0) + 1;
      return "";
    };
    target.writeText = async () => {
      w.__m3dClipWrite = (w.__m3dClipWrite ?? 0) + 1;
    };
    w.__m3dClipInstalled = true;
  });
}

/**
 * 用途：去掉浏览器对 4xx/5xx/外网失败的网络层日志；应用 console.error/warn 仍须精确 []。
 * 说明：Chromium 会对失败 fetch 自动打 "Failed to load resource"，不含路径/ID/秘密串。
 */
function appConsoleErrors(lines: string[]): string[] {
  return lines.filter((line) => {
    if (/^error: Failed to load resource:/.test(line)) return false;
    return true;
  });
}

async function assertClipboardAndConsole(
  page: Page,
  consoleErrWarn: string[],
  forbiddenSnippets: string[],
) {
  const clip = await page.evaluate(() => {
    const w = window as unknown as {
      __m3dClipRead?: number;
      __m3dClipWrite?: number;
      __m3dClipInstalled?: boolean;
    };
    return {
      installed: w.__m3dClipInstalled === true,
      read: w.__m3dClipRead ?? -1,
      write: w.__m3dClipWrite ?? -1,
    };
  });
  expect(clip.installed, "剪贴板探针必须安装成功").toBe(true);
  expect(clip.read).toBe(0);
  expect(clip.write).toBe(0);

  // 应用层 console error/warning 必须精确 []（排除浏览器网络层噪声）
  const appLines = appConsoleErrors(consoleErrWarn);
  expect(appLines).toEqual([]);
  const joined = consoleErrWarn.join("\n");
  for (const s of forbiddenSnippets) {
    expect(joined).not.toContain(s);
  }
}

test.describe("融合写入持久恢复 M3-D", () => {
  test("跨刷新完整恢复：active 批次仍在，consume 一次后不可再恢复", async ({
    page,
    request,
  }) => {
    const mock = await startMockLlmServer();
    const consoleErrWarn: string[] = [];
    page.on("console", (msg: ConsoleMessage) => {
      if (msg.type() === "error" || msg.type() === "warning") {
        consoleErrWarn.push(`${msg.type()}: ${msg.text()}`);
      }
    });
    await installClipboardProbe(page);
    try {
      const { projectId, templateTitle, cardTitle } =
        await seedRecoveryFixtures(request, mock.baseUrl);

      const probe = await installRecoveryProbes(page);
      await openContent(page, projectId, "E2E 融合恢复目标项目");
      // 首次打开技术标、尚未打开 M3-D Dialog 时记录 localStorage 键集基线
      const localKeyBaseline = await readLocalStorageKeySet(page);

      const dialog = await generateAndApplyBoth(
        page,
        templateTitle,
        cardTitle,
      );

      // 从 create body 纳入禁写检查
      expect(probe.applyPosts.length).toBeGreaterThanOrEqual(1);
      const createBody = probe.applyPosts[0].body as {
        taskId?: string;
        suggestionIds?: string[];
      };
      const createTaskId = createBody.taskId ?? "";
      const createSuggestionIds = Array.isArray(createBody.suggestionIds)
        ? createBody.suggestionIds
        : [];
      expect(createTaskId).toBeTruthy();

      // 服务端批次 active（非 DOM 残留）
      let batches = await fetchBatches(request, projectId);
      expect(batches.items.length).toBeGreaterThanOrEqual(1);
      expect(batches.items[0].state).toBe("active");
      expect(batches.items[0].chapterCount).toBe(2);
      const batchId = batches.items[0].batchId;

      await dialog.getByRole("button", { name: "关闭", exact: true }).click();

      // 页面 reload 后服务端仍 active
      await page.reload();
      await expect(
        page.getByRole("heading", { name: "E2E 融合恢复目标项目" }),
      ).toBeVisible({ timeout: 20_000 });
      batches = await fetchBatches(request, projectId);
      const still = batches.items.find((b) => b.batchId === batchId);
      expect(still, "reload 后服务端批次必须仍存在").toBeTruthy();
      expect(still!.state).toBe("active");

      // 打开对话框可见可恢复
      await page.getByRole("button", { name: "模板卡片融合建议" }).click();
      const reopened = page.getByRole("dialog", { name: "模板卡片融合建议" });
      await expect(reopened).toBeVisible();
      await expect(
        reopened
          .getByTestId("content-fuse-batches")
          .getByText("最多保留最近 20 批，不是完整版本历史"),
      ).toBeVisible();
      await expect(reopened.getByText("可恢复").first()).toBeVisible();
      // 不展示 batchId
      await expect(reopened.getByText(batchId)).toHaveCount(0);

      const orderBefore = probe.orderLog.length;
      await reopened.getByTestId("content-fuse-restore-start").first().click();
      await expect(reopened.getByTestId("content-fuse-restore-confirm")).toBeVisible();
      await reopened.getByTestId("content-fuse-restore-yes").click();
      await expect(
        reopened.getByTestId("content-fuse-restore-summary"),
      ).toContainText("已恢复 2 章，跳过 0 章", { timeout: 20_000 });

      // consume 精确 1、路径精确、body 仅 expectedStateVersion
      expect(probe.consumePosts.length).toBe(1);
      expect(probe.consumePosts[0].path).toBe(
        `/api/projects/${projectId}/content-fuse-applications/${batchId}/consume`,
      );
      const consumeBodyRaw = probe.consumePosts[0].body;
      expect(typeof consumeBodyRaw).toBe("string");
      const consumeBody = JSON.parse(consumeBodyRaw as string) as Record<
        string,
        unknown
      >;
      expect(Object.keys(consumeBody).sort()).toEqual(["expectedStateVersion"]);
      expect(consumeBody.expectedStateVersion as string).toMatch(
        /^esv_[0-9a-f]{32}$/,
      );

      // 完整恢复：consume POST → 唯一 editor-state GET → 列表 GET，严格递增
      await expect
        .poll(
          () => {
            const after = probe.orderLog.slice(orderBefore);
            const c = after.findIndex((x) => x.startsWith("consume-post:"));
            const e = after.findIndex((x) => x.startsWith("editor-get:"));
            const l = after.findIndex((x) => x.startsWith("list-get:"));
            const editorGets = after.filter((x) =>
              x.startsWith("editor-get:"),
            ).length;
            return (
              c >= 0 &&
              e >= 0 &&
              l >= 0 &&
              c < e &&
              e < l &&
              editorGets === 1
            );
          },
          { timeout: 15_000 },
        )
        .toBe(true);
      assertRestoreOrder(probe.orderLog, orderBefore);
      expect(
        probe.orderLog
          .slice(orderBefore)
          .filter((x) => x.startsWith("editor-get:")).length,
      ).toBe(1);

      // 服务端 consumed
      batches = await fetchBatches(request, projectId);
      const consumed = batches.items.find((b) => b.batchId === batchId);
      expect(consumed!.state).toBe("consumed");

      // UI 已消费，不可再触发；二次不得发 consume
      await expect(reopened.getByText("已消费").first()).toBeVisible();
      await expect(
        reopened.getByTestId("content-fuse-restore-start"),
      ).toHaveCount(0);
      const consumeCount = probe.consumePosts.length;
      await expect(
        reopened.getByRole("button", { name: "恢复此批次" }),
      ).toHaveCount(0);
      expect(probe.consumePosts.length).toBe(consumeCount);

      // 正文恢复为 before
      await reopened.getByRole("button", { name: "关闭", exact: true }).click();
      await selectChapterByTitle(page, TITLE_A);
      await expect(page.getByLabel(`正文：${TITLE_A}`)).toHaveValue(BODY_A);
      await selectChapterByTitle(page, TITLE_B);
      await expect(page.getByLabel(`正文：${TITLE_B}`)).toHaveValue(BODY_B);

      const state = await fetchEditorState(request, projectId);
      expect(state.chapters.find((c) => c.id === CHAP_A)?.body).toBe(BODY_A);
      expect(state.chapters.find((c) => c.id === CHAP_B)?.body).toBe(BODY_B);

      // M3-D 业务流程结束后、主动探针前：存储/剪贴板/console 必须干净
      const forbiddenSnippets = [
        SECRET,
        batchId,
        createTaskId,
        ...createSuggestionIds,
        "/content-fuse-applications",
      ];
      await assertNoSensitiveStorage(page, forbiddenSnippets, localKeyBaseline);
      await assertClipboardAndConsole(page, consoleErrWarn, forbiddenSnippets);

      // 页面文案不含 ID / 秘密串
      const bodyText = await page.locator("body").innerText();
      expect(bodyText).not.toContain(batchId);
      expect(bodyText).not.toContain(SECRET);
      expect(bodyText).not.toContain(createTaskId);

      // 主动探测未知 API（含 projects 前缀下未知路径）与外网；浏览器会记 403 资源失败，不并入业务流程 console 断言
      const forbiddenBeforeProbe = probe.forbiddenHits.length;
      const externalBeforeProbe = probe.externalHits.length;
      await page.evaluate(async () => {
        try {
          await fetch("/api/unknown-m3d-probe", { method: "GET" });
        } catch {
          /* 阻断 */
        }
        try {
          await fetch("/api/projects/unknown-m3d-probe", { method: "GET" });
        } catch {
          /* 阻断 */
        }
        try {
          await fetch("https://example.invalid/m3d-probe", { method: "GET" });
        } catch {
          /* 阻断 */
        }
      });
      await expect
        .poll(
          () =>
            probe.forbiddenHits.length >= forbiddenBeforeProbe + 2 &&
            probe.externalHits.length >= externalBeforeProbe + 1,
          { timeout: 10_000 },
        )
        .toBe(true);
      expect(
        probe.forbiddenHits.some((h) => h.includes("/api/unknown-m3d-probe")),
      ).toBe(true);
      expect(
        probe.forbiddenHits.some((h) =>
          h.includes("/api/projects/unknown-m3d-probe"),
        ),
      ).toBe(true);
      expect(
        probe.externalHits.some(
          (h) => h.includes("example.invalid") && h.includes("m3d-probe"),
        ),
      ).toBe(true);

      await probe.dispose();
    } finally {
      await mock.close();
    }
  });

  test("部分恢复与零恢复：漂移章跳过，批次仍一次消费", async ({
    page,
    request,
  }) => {
    const mock = await startMockLlmServer();
    try {
      // —— 部分恢复 ——
      const partial = await seedRecoveryFixtures(request, mock.baseUrl, {
        name: "E2E 部分恢复项目",
      });
      await openContent(page, partial.projectId, "E2E 部分恢复项目");
      let dialog = await generateAndApplyBoth(
        page,
        partial.templateTitle,
        partial.cardTitle,
      );
      await dialog.getByRole("button", { name: "关闭", exact: true }).click();

      // 漂移 A，B 保持 after
      const afterApply = await fetchEditorState(request, partial.projectId);
      const chapB = afterApply.chapters.find((c) => c.id === CHAP_B)!;
      expect(chapB.body).toBe(PROPOSED_B);
      const putDrift = await request.put(
        `${API}/projects/${partial.projectId}/editor-state`,
        {
          data: {
            outline: [
              { id: "node_a", title: TITLE_A, children: [] },
              { id: "node_b", title: TITLE_B, children: [] },
            ],
            chapters: [
              {
                id: CHAP_A,
                title: TITLE_A,
                body: `${PROPOSED_A}·手工漂移`,
                status: "needs_review",
                wordCount: 0,
                preview: "",
              },
              {
                id: CHAP_B,
                title: TITLE_B,
                body: PROPOSED_B,
                status: chapB.status || "needs_review",
                wordCount: 0,
                preview: "",
              },
            ],
            mode: "ALIGNED",
          },
        },
      );
      expect(putDrift.ok()).toBeTruthy();

      await page.reload();
      await expect(
        page.getByRole("heading", { name: "E2E 部分恢复项目" }),
      ).toBeVisible({ timeout: 20_000 });

      const probePartial = await installRecoveryProbes(page);
      await page.getByRole("button", { name: "模板卡片融合建议" }).click();
      dialog = page.getByRole("dialog", { name: "模板卡片融合建议" });
      await expect(dialog.getByText("可恢复").first()).toBeVisible({
        timeout: 10_000,
      });
      const orderBefore = probePartial.orderLog.length;
      await dialog.getByTestId("content-fuse-restore-start").first().click();
      await dialog.getByTestId("content-fuse-restore-yes").click();
      await expect(
        dialog.getByTestId("content-fuse-restore-summary"),
      ).toContainText(/已恢复 1 章，跳过 1 章/, { timeout: 20_000 });
      expect(probePartial.consumePosts.length).toBe(1);
      {
        const raw = probePartial.consumePosts[0].body;
        expect(typeof raw).toBe("string");
        const body = JSON.parse(raw as string) as Record<string, unknown>;
        expect(Object.keys(body).sort()).toEqual(["expectedStateVersion"]);
        expect(body.expectedStateVersion as string).toMatch(
          /^esv_[0-9a-f]{32}$/,
        );
      }
      // partial：consume → 唯一 editor GET → list GET，严格递增（禁止 -1 < 0 假绿）
      await expect
        .poll(
          () => {
            const after = probePartial.orderLog.slice(orderBefore);
            const c = after.findIndex((x) => x.startsWith("consume-post:"));
            const e = after.findIndex((x) => x.startsWith("editor-get:"));
            const l = after.findIndex((x) => x.startsWith("list-get:"));
            const editorGets = after.filter((x) =>
              x.startsWith("editor-get:"),
            ).length;
            return (
              c >= 0 &&
              e >= 0 &&
              l >= 0 &&
              c < e &&
              e < l &&
              editorGets === 1
            );
          },
          { timeout: 15_000 },
        )
        .toBe(true);
      assertRestoreOrder(probePartial.orderLog, orderBefore);
      expect(
        probePartial.orderLog
          .slice(orderBefore)
          .filter((x) => x.startsWith("editor-get:")).length,
      ).toBe(1);

      const batchesPartial = await fetchBatches(request, partial.projectId);
      expect(batchesPartial.items[0].state).toBe("consumed");
      // 不可二次发送
      await expect(dialog.getByTestId("content-fuse-restore-start")).toHaveCount(
        0,
      );
      const consumePartialCount = probePartial.consumePosts.length;
      expect(probePartial.consumePosts.length).toBe(consumePartialCount);
      const statePartial = await fetchEditorState(request, partial.projectId);
      expect(statePartial.chapters.find((c) => c.id === CHAP_A)?.body).toBe(
        `${PROPOSED_A}·手工漂移`,
      );
      expect(statePartial.chapters.find((c) => c.id === CHAP_B)?.body).toBe(
        BODY_B,
      );
      await dialog.getByRole("button", { name: "关闭", exact: true }).click();
      await probePartial.dispose();

      // —— 零恢复 ——
      const zero = await seedRecoveryFixtures(request, mock.baseUrl, {
        name: "E2E 零恢复项目",
      });
      await openContent(page, zero.projectId, "E2E 零恢复项目");
      dialog = await generateAndApplyBoth(
        page,
        zero.templateTitle,
        zero.cardTitle,
      );
      await dialog.getByRole("button", { name: "关闭", exact: true }).click();

      const putBoth = await request.put(
        `${API}/projects/${zero.projectId}/editor-state`,
        {
          data: {
            outline: [
              { id: "node_a", title: TITLE_A, children: [] },
              { id: "node_b", title: TITLE_B, children: [] },
            ],
            chapters: [
              {
                id: CHAP_A,
                title: TITLE_A,
                body: "双漂移A",
                status: "needs_review",
                wordCount: 0,
                preview: "",
              },
              {
                id: CHAP_B,
                title: TITLE_B,
                body: "双漂移B",
                status: "needs_review",
                wordCount: 0,
                preview: "",
              },
            ],
            mode: "ALIGNED",
          },
        },
      );
      expect(putBoth.ok()).toBeTruthy();
      await page.reload();
      await expect(
        page.getByRole("heading", { name: "E2E 零恢复项目" }),
      ).toBeVisible({ timeout: 20_000 });

      const probeZero = await installRecoveryProbes(page);
      await page.getByRole("button", { name: "模板卡片融合建议" }).click();
      dialog = page.getByRole("dialog", { name: "模板卡片融合建议" });
      const orderBeforeZero = probeZero.orderLog.length;
      await dialog.getByTestId("content-fuse-restore-start").first().click();
      await dialog.getByTestId("content-fuse-restore-yes").click();
      await expect(
        dialog.getByTestId("content-fuse-restore-summary"),
      ).toContainText("已恢复 0 章，跳过 2 章", { timeout: 20_000 });
      expect(probeZero.consumePosts.length).toBe(1);
      {
        const raw = probeZero.consumePosts[0].body;
        expect(typeof raw).toBe("string");
        const body = JSON.parse(raw as string) as Record<string, unknown>;
        expect(Object.keys(body).sort()).toEqual(["expectedStateVersion"]);
        expect(body.expectedStateVersion as string).toMatch(
          /^esv_[0-9a-f]{32}$/,
        );
      }
      // zero：同样要求 consume → 唯一 editor GET → list GET 严格递增
      await expect
        .poll(
          () => {
            const after = probeZero.orderLog.slice(orderBeforeZero);
            const c = after.findIndex((x) => x.startsWith("consume-post:"));
            const e = after.findIndex((x) => x.startsWith("editor-get:"));
            const l = after.findIndex((x) => x.startsWith("list-get:"));
            const editorGets = after.filter((x) =>
              x.startsWith("editor-get:"),
            ).length;
            return (
              c >= 0 &&
              e >= 0 &&
              l >= 0 &&
              c < e &&
              e < l &&
              editorGets === 1
            );
          },
          { timeout: 15_000 },
        )
        .toBe(true);
      assertRestoreOrder(probeZero.orderLog, orderBeforeZero);
      expect(
        probeZero.orderLog
          .slice(orderBeforeZero)
          .filter((x) => x.startsWith("editor-get:")).length,
      ).toBe(1);
      const batchesZero = await fetchBatches(request, zero.projectId);
      expect(batchesZero.items[0].state).toBe("consumed");
      await expect(dialog.getByTestId("content-fuse-restore-start")).toHaveCount(
        0,
      );
      const stateZero = await fetchEditorState(request, zero.projectId);
      expect(stateZero.chapters.find((c) => c.id === CHAP_A)?.body).toBe(
        "双漂移A",
      );
      expect(stateZero.chapters.find((c) => c.id === CHAP_B)?.body).toBe(
        "双漂移B",
      );
      await probeZero.dispose();
    } finally {
      await mock.close();
    }
  });

  test("二次确认取消：零 consume 请求", async ({ page, request }) => {
    const mock = await startMockLlmServer();
    try {
      const { projectId, templateTitle, cardTitle } =
        await seedRecoveryFixtures(request, mock.baseUrl, {
          name: "E2E 取消恢复项目",
        });
      const probe = await installRecoveryProbes(page);
      await openContent(page, projectId, "E2E 取消恢复项目");
      const dialog = await generateAndApplyBoth(
        page,
        templateTitle,
        cardTitle,
      );
      // 批次已存在，直接对列表二次确认后取消
      await expect(dialog.getByText("可恢复").first()).toBeVisible();
      await dialog.getByTestId("content-fuse-restore-start").first().click();
      await expect(dialog.getByTestId("content-fuse-restore-confirm")).toBeVisible();
      await dialog.getByTestId("content-fuse-restore-no").click();
      await expect(dialog.getByTestId("content-fuse-restore-confirm")).toHaveCount(
        0,
      );
      expect(probe.consumePosts.length).toBe(0);
      const batches = await fetchBatches(request, projectId);
      expect(batches.items[0].state).toBe("active");
      await probe.dispose();
    } finally {
      await mock.close();
    }
  });

  test("项目 A→B 迟到列表与关闭后迟到 apply/consume 不得污染", async ({
    page,
    request,
  }) => {
    const mock = await startMockLlmServer();
    try {
      const projectA = await seedRecoveryFixtures(request, mock.baseUrl, {
        name: "E2E 迟到隔离项目A",
      });
      const projectB = await seedRecoveryFixtures(request, mock.baseUrl, {
        name: "E2E 迟到隔离项目B",
      });

      // 先在 A 写入一批，确保 A 有 active 批次
      await openContent(page, projectA.projectId, "E2E 迟到隔离项目A");
      let dialog = await generateAndApplyBoth(
        page,
        projectA.templateTitle,
        projectA.cardTitle,
      );
      await dialog.getByRole("button", { name: "关闭", exact: true }).click();

      // 打开 A 对话框时延迟列表，再切换到 B 并打开 B Dialog
      let listGate: (() => void) | null = null;
      let aListReleased = false;
      const listWait = new Promise<void>((resolve) => {
        listGate = () => {
          aListReleased = true;
          resolve();
        };
      });
      await page.route(
        "**/api/projects/**/content-fuse-applications**",
        async (route) => {
          const req = route.request();
          const method = req.method().toUpperCase();
          const path = new URL(req.url()).pathname;
          if (
            method === "GET" &&
            path.includes(projectA.projectId) &&
            /\/content-fuse-applications\/?$/.test(path)
          ) {
            await listWait;
            await route.continue();
            return;
          }
          await route.continue();
        },
      );

      await page.getByRole("button", { name: "模板卡片融合建议" }).click();
      dialog = page.getByRole("dialog", { name: "模板卡片融合建议" });
      // 不等 A 列表完成，直接切到 B
      await page.goto(`/technical-plan/${projectB.projectId}/content`);
      await expect(
        page.getByRole("heading", { name: "E2E 迟到隔离项目B" }),
      ).toBeVisible({ timeout: 20_000 });

      // 在释放 A 响应前打开 B 的 Dialog，证明 B 为自己的空批次
      await page.getByRole("button", { name: "模板卡片融合建议" }).click();
      const dialogB = page.getByRole("dialog", { name: "模板卡片融合建议" });
      await expect(dialogB).toBeVisible();
      await expect(
        dialogB.getByTestId("content-fuse-batches").getByText("暂无恢复批次"),
      ).toBeVisible({ timeout: 10_000 });
      await expect(dialogB.getByText("可恢复")).toHaveCount(0);

      // 释放 A 的迟到列表后，B 仍保持空批次、无 A 的“可恢复”消息
      listGate?.();
      await expect
        .poll(() => aListReleased, { timeout: 10_000 })
        .toBe(true);
      await expect(
        dialogB.getByTestId("content-fuse-batches").getByText("暂无恢复批次"),
      ).toBeVisible();
      await expect(dialogB.getByText("可恢复")).toHaveCount(0);
      await expect(dialogB.getByTestId("content-fuse-restore-summary")).toHaveCount(
        0,
      );
      await expect(page.getByText("E2E 迟到隔离项目A")).toHaveCount(0);
      await dialogB.getByRole("button", { name: "关闭", exact: true }).click();

      // —— 关闭后迟到 apply ——
      await page.unroute("**/api/projects/**/content-fuse-applications**");
      let applyGate: (() => void) | null = null;
      let applyReleased = false;
      const applyWait = new Promise<void>((resolve) => {
        applyGate = () => {
          applyReleased = true;
          resolve();
        };
      });
      await page.route(
        "**/api/projects/**/content-fuse-applications**",
        async (route) => {
          const req = route.request();
          const method = req.method().toUpperCase();
          const path = new URL(req.url()).pathname;
          if (
            method === "POST" &&
            path.includes(projectB.projectId) &&
            /\/content-fuse-applications\/?$/.test(path) &&
            !path.includes("/consume")
          ) {
            await applyWait;
            await route.continue();
            return;
          }
          await route.continue();
        },
      );

      // page-origin editor-state GET 与列表 GET 精确计数
      let editorGetAfterClose = 0;
      let listGetAfterClose = 0;
      let countingClosed = false;
      const onOriginRequest = (req: import("@playwright/test").Request) => {
        if (!countingClosed) return;
        const method = req.method().toUpperCase();
        const path = new URL(req.url()).pathname;
        if (method === "GET" && /\/editor-state\/?$/.test(path)) {
          editorGetAfterClose += 1;
        }
        if (
          method === "GET" &&
          /\/content-fuse-applications\/?$/.test(path)
        ) {
          listGetAfterClose += 1;
        }
      };
      page.on("request", onOriginRequest);

      await page.getByRole("button", { name: "模板卡片融合建议" }).click();
      dialog = page.getByRole("dialog", { name: "模板卡片融合建议" });
      await dialog.getByLabel(`模板 ${projectB.templateTitle}`).check();
      await dialog.getByLabel(`卡片 ${projectB.cardTitle}`).check();
      await dialog.getByLabel(`目标章节 ${TITLE_A}`).check();
      await dialog.getByRole("button", { name: "生成只读融合建议" }).click();
      await expect(dialog.getByText(/已生成 \d+ 条只读建议/)).toBeVisible({
        timeout: 30_000,
      });
      await dialog.getByLabel(`勾选写入建议 ${TITLE_A}`).check();
      await dialog.getByRole("button", { name: "确认写入所选" }).click();
      // 立即关闭
      await dialog.getByRole("button", { name: "关闭", exact: true }).click();
      await expect(dialog).toBeHidden();
      countingClosed = true;
      const editorGetsAtClose = editorGetAfterClose;
      const listGetsAtClose = listGetAfterClose;
      applyGate?.();
      await expect
        .poll(() => applyReleased, { timeout: 15_000 })
        .toBe(true);
      // 精确证明服务端 B active 批次已创建（禁止条件跳过）
      await expect
        .poll(
          async () => {
            const b = await fetchBatches(request, projectB.projectId);
            return b.items.some((x) => x.state === "active");
          },
          { timeout: 15_000 },
        )
        .toBe(true);
      // P12B-C3：runner 在 POST 成功后可有唯一 editor-state GET（版本水线），
      // 但 Dialog 会话已失效：不得 list GET、不得重开、不得旧消息。
      expect(editorGetAfterClose).toBeLessThanOrEqual(editorGetsAtClose + 1);
      expect(listGetAfterClose).toBe(listGetsAtClose);
      await expect(
        page.getByRole("dialog", { name: "模板卡片融合建议" }),
      ).toHaveCount(0);
      await expect(page.getByTestId("content-fuse-apply-summary")).toHaveCount(
        0,
      );
      await expect(page.getByText("融合确认失败")).toHaveCount(0);
      await expect(page.getByText("融合已写入")).toHaveCount(0);

      // —— 关闭后迟到 consume（无条件执行）——
      await page.unroute("**/api/projects/**/content-fuse-applications**");
      const batchesB = await fetchBatches(request, projectB.projectId);
      const activeB = batchesB.items.find((b) => b.state === "active");
      expect(activeB, "迟到 apply 后 B 必须有 active 批次").toBeTruthy();
      const batchIdB = activeB!.batchId;

      let consumeGate: (() => void) | null = null;
      let consumeReleased = false;
      const consumeWait = new Promise<void>((resolve) => {
        consumeGate = () => {
          consumeReleased = true;
          resolve();
        };
      });
      await page.route(
        "**/api/projects/**/content-fuse-applications/**/consume",
        async (route) => {
          await consumeWait;
          await route.continue();
        },
      );
      await page.getByRole("button", { name: "模板卡片融合建议" }).click();
      dialog = page.getByRole("dialog", { name: "模板卡片融合建议" });
      await expect(dialog.getByText("可恢复").first()).toBeVisible({
        timeout: 10_000,
      });
      await dialog.getByTestId("content-fuse-restore-start").first().click();
      await dialog.getByTestId("content-fuse-restore-yes").click();
      await dialog.getByRole("button", { name: "关闭", exact: true }).click();
      await expect(dialog).toBeHidden();
      const editorGetsBeforeConsumeRelease = editorGetAfterClose;
      const listGetsBeforeConsumeRelease = listGetAfterClose;
      consumeGate?.();
      await expect
        .poll(() => consumeReleased, { timeout: 15_000 })
        .toBe(true);
      await expect
        .poll(
          async () => {
            const b = await fetchBatches(request, projectB.projectId);
            const item = b.items.find((x) => x.batchId === batchIdB);
            return item?.state === "consumed";
          },
          { timeout: 15_000 },
        )
        .toBe(true);
      // P12B-C3：runner 可有唯一 editor GET；Dialog 会话失效后不得 list GET/UI 污染
      expect(editorGetAfterClose).toBeLessThanOrEqual(
        editorGetsBeforeConsumeRelease + 1,
      );
      expect(listGetAfterClose).toBe(listGetsBeforeConsumeRelease);
      await expect(
        page.getByRole("dialog", { name: "模板卡片融合建议" }),
      ).toHaveCount(0);
      await expect(
        page.getByTestId("content-fuse-restore-summary"),
      ).toHaveCount(0);
      await expect(page.getByText("恢复已完成")).toHaveCount(0);
      await expect(page.getByText("恢复失败")).toHaveCount(0);

      page.off("request", onOriginRequest);
    } finally {
      await mock.close();
    }
  });

  test("consume POST 成功但 editor-state 重读失败：已完成提示、批次 consumed、禁止二次 consume", async ({
    page,
    request,
  }) => {
    const mock = await startMockLlmServer();
    try {
      const consoleErrWarn: string[] = [];
      page.on("console", (msg: ConsoleMessage) => {
        if (msg.type() === "error" || msg.type() === "warning") {
          consoleErrWarn.push(`${msg.type()}: ${msg.text()}`);
        }
      });

      const { projectId, templateTitle, cardTitle } =
        await seedRecoveryFixtures(request, mock.baseUrl, {
          name: "E2E 恢复刷新失败项目",
        });
      await openContent(page, projectId, "E2E 恢复刷新失败项目");
      let dialog = await generateAndApplyBoth(
        page,
        templateTitle,
        cardTitle,
      );
      await dialog.getByRole("button", { name: "关闭", exact: true }).click();

      const batches = await fetchBatches(request, projectId);
      expect(batches.items[0].state).toBe("active");
      const batchId = batches.items[0].batchId;

      let consumeSucceeded = false;
      // consume 成功后阻断唯一一次实际 editor-state GET（onReloadFromApi）
      let blockEditorGetAfterConsume = false;
      let consumeCount = 0;
      let blockedEditorGetCount = 0;
      await page.route(
        "**/api/projects/**/content-fuse-applications/**/consume",
        async (route) => {
          const response = await route.fetch();
          consumeCount += 1;
          consumeSucceeded = response.ok();
          if (consumeSucceeded) {
            blockEditorGetAfterConsume = true;
          }
          await route.fulfill({ response });
        },
      );
      await page.route("**/api/projects/**/editor-state**", async (route) => {
        if (
          route.request().method().toUpperCase() === "GET" &&
          blockEditorGetAfterConsume
        ) {
          blockedEditorGetCount += 1;
          await route.fulfill({
            status: 500,
            contentType: "application/json",
            body: JSON.stringify({
              detail: { code: "internal_error", message: SECRET },
            }),
          });
          return;
        }
        await route.continue();
      });

      await page.getByRole("button", { name: "模板卡片融合建议" }).click();
      dialog = page.getByRole("dialog", { name: "模板卡片融合建议" });
      await expect(dialog.getByText("可恢复").first()).toBeVisible({
        timeout: 10_000,
      });
      await dialog.getByTestId("content-fuse-restore-start").first().click();
      await dialog.getByTestId("content-fuse-restore-yes").click();
      await expect(dialog.getByTestId("content-fuse-local-error")).toHaveText(
        "恢复已完成，但刷新失败，请关闭后重新打开",
        { timeout: 15_000 },
      );
      await expect(dialog.getByText("恢复失败，请刷新后重试")).toHaveCount(0);

      // 服务端已消费
      await expect
        .poll(async () => {
          const b = await fetchBatches(request, projectId);
          return b.items.find((x) => x.batchId === batchId)?.state;
        }, { timeout: 10_000 })
        .toBe("consumed");

      // 唯一一次失败 GET；不得再发第二次 editor GET / consume
      expect(blockedEditorGetCount).toBe(1);

      // 内存列表标为已消费，不可再发 consume
      await expect(dialog.getByText("已消费").first()).toBeVisible();
      await expect(dialog.getByTestId("content-fuse-restore-start")).toHaveCount(
        0,
      );
      const countAfter = consumeCount;
      expect(consumeCount).toBe(1);
      // 无恢复按钮可点
      expect(consumeCount).toBe(countAfter);
      expect(blockedEditorGetCount).toBe(1);

      const pageText = await page.locator("body").innerText();
      expect(pageText).not.toContain(SECRET);
      expect(pageText).not.toContain(batchId);
      expect(pageText).not.toContain(projectId);
      expect(pageText).not.toContain("/content-fuse-applications");
      expect(pageText).not.toContain("internal_error");
      expect(appConsoleErrors(consoleErrWarn)).toEqual([]);
      for (const line of consoleErrWarn) {
        expect(line).not.toContain(SECRET);
        expect(line).not.toContain(batchId);
        expect(line).not.toContain(projectId);
        expect(line).not.toContain("internal_error");
      }
    } finally {
      await mock.close();
    }
  });

  test("P12B-C3 队列：PUT 挂起时 consume POST 严格 0，释放后 expected 精确等于 PUT 响应版本", async ({
    page,
    request,
  }) => {
    const mock = await startMockLlmServer();
    try {
      const pageErrors: string[] = [];
      page.on("pageerror", (err) => {
        pageErrors.push(String(err?.message || err));
      });
      await page.addInitScript(() => {
        window.addEventListener("unhandledrejection", (ev) => {
          const reason = (ev as PromiseRejectionEvent).reason;
          const text =
            reason instanceof Error
              ? reason.message
              : typeof reason === "string"
                ? reason
                : String(reason);
          const w = window as unknown as { __p12bc3Unhandled?: string[] };
          w.__p12bc3Unhandled = w.__p12bc3Unhandled || [];
          w.__p12bc3Unhandled.push(text);
        });
      });

      const { projectId, templateTitle, cardTitle } =
        await seedRecoveryFixtures(request, mock.baseUrl, {
          name: "E2E 恢复队列项目",
        });
      await openContent(page, projectId, "E2E 恢复队列项目");
      let dialog = await generateAndApplyBoth(
        page,
        templateTitle,
        cardTitle,
      );
      await dialog.getByRole("button", { name: "关闭", exact: true }).click();

      const putGate = createHoldGate();
      let putEntered = false;
      let consumeArrivedWhilePutHeld = false;
      const putLog: Array<{
        body: Record<string, unknown>;
        responseVersion: string;
      }> = [];
      const consumeLog: Array<Record<string, unknown>> = [];

      await page.route("**/api/projects/**/editor-state**", async (route) => {
        const method = route.request().method().toUpperCase();
        if (method === "PUT") {
          putEntered = true;
          if (!putGate.released) {
            await putGate.wait();
          }
          const response = await route.fetch();
          const json = (await response.json()) as { stateVersion?: string };
          let body: Record<string, unknown> = {};
          try {
            body = route.request().postDataJSON() as Record<string, unknown>;
          } catch {
            body = {};
          }
          putLog.push({
            body,
            responseVersion: String(json.stateVersion || ""),
          });
          await route.fulfill({
            status: response.status(),
            contentType: "application/json",
            body: JSON.stringify(json),
          });
          return;
        }
        await route.continue();
      });
      await page.route(
        "**/api/projects/**/content-fuse-applications/**/consume**",
        async (route) => {
          if (route.request().method().toUpperCase() === "POST") {
            if (!putGate.released) {
              consumeArrivedWhilePutHeld = true;
            }
            let body: Record<string, unknown> = {};
            try {
              body = route.request().postDataJSON() as Record<string, unknown>;
            } catch {
              body = {};
            }
            consumeLog.push(body);
          }
          await route.continue();
        },
      );

      await page.clock.install();
      // 触发普通 editor PUT 并挂起
      await page.locator("textarea.tp-content-body").evaluate((el, value) => {
        const area = el as HTMLTextAreaElement;
        const proto = Object.getOwnPropertyDescriptor(
          window.HTMLTextAreaElement.prototype,
          "value",
        );
        proto?.set?.call(area, value);
        area.dispatchEvent(new Event("input", { bubbles: true }));
        area.dispatchEvent(new Event("change", { bubbles: true }));
      }, `${PROPOSED_A}\nconsume 队列挂起`);
      await page.clock.fastForward(TECH_AUTOSAVE_ADVANCE_MS);
      await expect.poll(() => (putEntered ? 1 : 0), { timeout: 5_000 }).toBe(1);
      expect(putLog.length).toBe(0);

      await page.getByRole("button", { name: "模板卡片融合建议" }).click();
      dialog = page.getByRole("dialog", { name: "模板卡片融合建议" });
      await expect(dialog.getByText("可恢复").first()).toBeVisible({
        timeout: 10_000,
      });
      await dialog.getByTestId("content-fuse-restore-start").first().click();
      await dialog.getByTestId("content-fuse-restore-yes").click();

      await page.clock.fastForward(TECH_AUTOSAVE_ADVANCE_MS * 2);
      expect(consumeArrivedWhilePutHeld).toBe(false);
      expect(consumeLog.length).toBe(0);

      putGate.release();
      await expect.poll(() => putLog.length, { timeout: 10_000 }).toBe(1);
      await expect.poll(() => consumeLog.length, { timeout: 15_000 }).toBe(1);
      expect(consumeArrivedWhilePutHeld).toBe(false);

      const putVersion = putLog[0].responseVersion;
      expect(putVersion).toMatch(/^esv_[0-9a-f]{32}$/);
      expect(Object.keys(consumeLog[0]).sort()).toEqual([
        "expectedStateVersion",
      ]);
      expect(consumeLog[0].expectedStateVersion).toBe(putVersion);

      const unhandled = await page.evaluate(
        () =>
          (window as unknown as { __p12bc3Unhandled?: string[] })
            .__p12bc3Unhandled || [],
      );
      expect(pageErrors).toEqual([]);
      expect(unhandled).toEqual([]);
    } finally {
      await mock.close();
    }
  });

  test("P12B-C3 consume 网络不确定与缺/非法/带空白 stateVersion：阻断、零重试、零 PUT、零 unhandled", async ({
    page,
    request,
  }) => {
    const mock = await startMockLlmServer();
    try {
      const pageErrors: string[] = [];
      page.on("pageerror", (err) => {
        pageErrors.push(String(err?.message || err));
      });
      await page.addInitScript(() => {
        window.addEventListener("unhandledrejection", (ev) => {
          const reason = (ev as PromiseRejectionEvent).reason;
          const text =
            reason instanceof Error
              ? reason.message
              : typeof reason === "string"
                ? reason
                : String(reason);
          const w = window as unknown as { __p12bc3Unhandled?: string[] };
          w.__p12bc3Unhandled = w.__p12bc3Unhandled || [];
          w.__p12bc3Unhandled.push(text);
        });
      });

      type Mode = "abort" | "missing" | "illegal" | "whitespace";
      // 跨导航：window 上 unhandled 会随 page.goto 丢失，必须逐轮在下次 goto 前读取断言
      // mock 200 非法响应仅拦截浏览器请求，不得改写服务端真实批次
      const modes: Mode[] = ["abort", "missing", "illegal", "whitespace"];

      const { projectId, templateTitle, cardTitle } =
        await seedRecoveryFixtures(request, mock.baseUrl, {
          name: "E2E 恢复不确定项目",
        });
      // 一个 active 批次足够：mock 不真正消费服务端
      await openContent(page, projectId, "E2E 恢复不确定项目");
      {
        const seeded = await generateAndApplyBoth(
          page,
          templateTitle,
          cardTitle,
        );
        await seeded.getByRole("button", { name: "关闭", exact: true }).click();
      }

      for (const m of modes) {
        const consumePosts: unknown[] = [];
        const putLog: unknown[] = [];
        // pageerror 挂在 Node 侧可跨导航累计；本轮用增量切片证明零错误
        const pageErrorAtStart = pageErrors.length;
        await page
          .unroute("**/api/projects/**/content-fuse-applications/**/consume**")
          .catch(() => undefined);
        await page
          .unroute("**/api/projects/**/editor-state**")
          .catch(() => undefined);

        await page.route(
          "**/api/projects/**/content-fuse-applications/**/consume**",
          async (route) => {
            if (route.request().method().toUpperCase() !== "POST") {
              await route.continue();
              return;
            }
            consumePosts.push(route.request().postData());
            if (m === "abort") {
              await route.abort("failed");
              return;
            }
            const base = {
              restoredChapterCount: 0,
              skippedChapterCount: 0,
              consumedAt: "2026-07-15T12:00:00.000Z",
            };
            const payload =
              m === "missing"
                ? base
                : m === "illegal"
                  ? { ...base, stateVersion: "not-a-version" }
                  : {
                      ...base,
                      stateVersion: " esv_0123456789abcdef0123456789abcdef",
                    };
            // 仅 mock 浏览器响应；服务端批次保持 active，测试不得改服务端
            await route.fulfill({
              status: 200,
              contentType: "application/json",
              body: JSON.stringify(payload),
            });
          },
        );
        await page.route("**/api/projects/**/editor-state**", async (route) => {
          if (route.request().method().toUpperCase() === "PUT") {
            putLog.push(route.request().postData());
          }
          await route.continue();
        });

        await page.goto(`/technical-plan/${projectId}/content`);
        await expect(
          page.getByRole("heading", { name: "E2E 恢复不确定项目" }),
        ).toBeVisible({ timeout: 20_000 });
        await expect(page.getByLabel(`正文：${TITLE_A}`)).toBeVisible({
          timeout: 15_000,
        });

        // 点击恢复前记录当前可见正文（应用后为 PROPOSED_*）；失败后必须精确保留
        await selectChapterByTitle(page, TITLE_A);
        const retainedBodyA = await page
          .getByLabel(`正文：${TITLE_A}`)
          .inputValue();
        await selectChapterByTitle(page, TITLE_B);
        const retainedBodyB = await page
          .getByLabel(`正文：${TITLE_B}`)
          .inputValue();
        expect(retainedBodyA).toBe(PROPOSED_A);
        expect(retainedBodyB).toBe(PROPOSED_B);

        await page.getByRole("button", { name: "模板卡片融合建议" }).click();
        const dialog = page.getByRole("dialog", { name: "模板卡片融合建议" });
        await expect(dialog.getByText("可恢复").first()).toBeVisible({
          timeout: 10_000,
        });

        await dialog.getByTestId("content-fuse-restore-start").first().click();
        await dialog.getByTestId("content-fuse-restore-yes").click();
        await expect(dialog.getByTestId("content-fuse-local-error")).toHaveText(
          "恢复失败，请刷新后重试",
          { timeout: 10_000 },
        );
        // 本 mode 闭环：固定错误 + POST 精确 1 + 本地正文保留
        // Dialog 打开时用 force 选章，避免 backdrop 拦截导致假超时
        expect(consumePosts.length).toBe(1);
        await selectChapterByTitle(page, TITLE_A, true);
        await expect(page.getByLabel(`正文：${TITLE_A}`)).toHaveValue(
          retainedBodyA,
        );
        await selectChapterByTitle(page, TITLE_B, true);
        await expect(page.getByLabel(`正文：${TITLE_B}`)).toHaveValue(
          retainedBodyB,
        );
        // 两防抖窗口内 PUT 计数保持 0（真实时间窗口 + poll，非 waitForTimeout）
        await expectStableCount(
          () => putLog.length,
          0,
          TECH_AUTOSAVE_ADVANCE_MS * 2,
        );
        // 阻断后再次确认不得再发 consume
        const starts = dialog.getByTestId("content-fuse-restore-start");
        if ((await starts.count()) > 0) {
          await starts.first().click();
          const yes = dialog.getByTestId("content-fuse-restore-yes");
          if ((await yes.count()) > 0) {
            await yes.click();
          }
        }
        expect(consumePosts.length).toBe(1);

        // 必须在下一次 page.goto 前读取本轮 unhandled（导航会丢 window 累加器）
        const unhandled = await page.evaluate(
          () =>
            (window as unknown as { __p12bc3Unhandled?: string[] })
              .__p12bc3Unhandled || [],
        );
        expect(unhandled).toEqual([]);
        expect(pageErrors.slice(pageErrorAtStart)).toEqual([]);
      }

      // pageerror 跨导航累计终检
      expect(pageErrors).toEqual([]);
    } finally {
      await mock.close();
    }
  });
});

const TECH_AUTOSAVE_DEBOUNCE_MS = 800;
const TECH_AUTOSAVE_ADVANCE_MS = TECH_AUTOSAVE_DEBOUNCE_MS + 100;

function createHoldGate() {
  let released = false;
  const waiters: Array<() => void> = [];
  return {
    wait: () =>
      released
        ? Promise.resolve()
        : new Promise<void>((resolve) => {
            waiters.push(resolve);
          }),
    release: () => {
      released = true;
      while (waiters.length > 0) {
        waiters.shift()?.();
      }
    },
    get released() {
      return released;
    },
  };
}

/** 用途：真实时间窗口内计数稳定；禁止 waitForTimeout。 */
async function expectStableCount(
  getCount: () => number,
  expected: number,
  windowMs: number,
) {
  const start = Date.now();
  await expect
    .poll(
      () => {
        if (getCount() !== expected) return "drift";
        return Date.now() - start >= windowMs ? "stable" : "waiting";
      },
      { timeout: windowMs + 5_000 },
    )
    .toBe("stable");
}
