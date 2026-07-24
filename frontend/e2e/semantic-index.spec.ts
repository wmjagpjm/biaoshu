/**
 * 模块：P9C 离线语义索引状态面板 E2E
 * 用途：用 Playwright route 拦截本机 /api/knowledge/semantic-index*，验收未构建/构建中/已就绪/失败降级、固定模型展示与受控错误；不启动真实模型、不触网。
 * 对接：Playwright chromium；前端 5174 + Vite proxy /api；npm run test:e2e:semantic-index。
 * 二次开发：禁止真实模型下载、外网 host、localStorage 伪就绪；勿改 cards/matrix 等既有 spec。
 * 说明（V1-O）：文档 folders/docs 失败不得再以 “local 回退成功” 绿测；末例断言显式文档失败与零 rebuild。
 *       未知 /api/knowledge* 与外网 fail-closed 的权威证明由 knowledge-doc-server-truth.spec.ts 承担，本文件不扩改 route 框架。
 * Q10-TEST-FIX：追加 test-first 红门——同代 GET A/B 逆序、同 tick 双 rebuild 精确一 POST、
 *       旧 GET 不得覆盖 rebuild building、building 后轮询 503 保 building+固定错误、
 *       finishedAt 非法 marker 精确显示「—」。
 * Q11-TEST-FIX：① 确定业务路径先 building 再 hold 同代两 poll GET（禁依赖 StrictMode 第二请求）；
 *       ② fulfill B 后先用 B 唯一 DOM 证已提交再释 A，终态仍 B；
 *       ③ 真正 hold rebuild 前已到达的 semantic GET，building 已提交后再释放；
 *       ④ finishedAt 全公开面探针由 knowledge 主 spec preparePage/assertPrivacyClean 权威承担，
 *          本文件保留精确「—」；503 仅宣称 panel/body 实际覆盖。Playwright 由 Codex 执行；Grok 仅静态。
 * Q12-TEST-FIX（最终）：① 同代 A/B：B DOM 9/9 commit 后 hold 全部后续 poll（含第 3+）；
 *       释 A 前 arm 精确 terminal；等 A terminal+业务 continuation 后以 page.evaluate 一次性
 *       读取 status/counts/degrade 快照，精确仍 B（ready、9/9、非 not_built/building）；
 *       finally 释放/abort 全部 held。② rebuild 旧 GET：POST 后 poll 全 hold；释放 pre-rebuild
 *       旧 GET 并等各 terminal+continuation；以 held post poll>0 证明未提交窗口；
 *       page.evaluate 一次性 snapshot 仍 building/rebuild disabled；finally 释放全部；
 *       删除 postRebuildGets>=0 恒真。不得弱化 finishedAt/19/T1/双 rebuild/503/隐私。
 * P2-TEST（独立红门，不弱化 Q12）：folders/docs ready 后 hold click 前首个 semantic GET A；
 *       点击重建并 hold POST；POST 仍 hold 时让 A 以 503+合成敏感 detail 结束并证固定错误出现；
 *       再释 POST=202 building；POST 后 poll 全 hold 且至少一条 arrived 未提交；
 *       一次性 snapshot：status=构建中、重建 disabled、semantic-index-error 精确 0、
 *       panel/body 无旧错误与合成 detail。预期 production 成功分支未清错 → 第 6 步业务红。
 */
import { expect, test, type Page, type Route } from "@playwright/test";

/** 后端语义索引读模型（与 /api/knowledge/semantic-index 对齐） */
type SemanticIndexPayload = {
  id: string | null;
  workspaceId: string | null;
  status: string;
  provider: "offline_bge";
  modelId: string;
  modelFingerprint: string | null;
  dimension: number;
  totalChunks: number;
  embeddedChunks: number;
  chunkCount: number;
  errorCode: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  createdAt: string | null;
  updatedAt: string | null;
};

const FIXED_MODEL = "BAAI/bge-small-zh-v1.5";
/** 固定中文：与前端 SEMANTIC_STATUS_UNAVAILABLE_MSG 对齐 */
const STATUS_UNAVAILABLE_MSG = "语义索引状态不可用";
/** 固定中文：与前端 SEMANTIC_REBUILD_FAILED_MSG 对齐 */
const REBUILD_FAILED_MSG = "启动语义索引构建失败";

function baseIndex(
  overrides: Partial<SemanticIndexPayload> = {},
): SemanticIndexPayload {
  return {
    id: null,
    workspaceId: "ws_e2e",
    status: "index_not_built",
    provider: "offline_bge",
    modelId: FIXED_MODEL,
    modelFingerprint: null,
    dimension: 512,
    totalChunks: 0,
    embeddedChunks: 0,
    chunkCount: 0,
    errorCode: "index_not_built",
    startedAt: null,
    finishedAt: null,
    createdAt: null,
    updatedAt: null,
    ...overrides,
  };
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

/**
 * 用途：挂载知识库相关 API 的本地 route 桩；仅响应 /api 与本地 Vite。
 * 说明：semantic-index 状态由 getState/setState 控制；rebuild 触发构建态。
 */
async function installKbRoutes(
  page: Page,
  opts: {
    getState: () => SemanticIndexPayload;
    setState: (next: SemanticIndexPayload) => void;
    /** 点击重建后立刻返回的状态（默认 running） */
    afterRebuild?: SemanticIndexPayload;
    /** 若干次 GET 后切到的终态（模拟轮询） */
    finalAfterPolls?: SemanticIndexPayload;
    finalAfterPollCount?: number;
    /** 语义索引 GET 失败（含敏感 detail） */
    semanticGetError?: {
      status: number;
      detail: unknown;
    };
    /** 语义索引 rebuild 失败（含敏感 detail） */
    semanticRebuildError?: {
      status: number;
      detail: unknown;
    };
    /** folders/docs 失败：文档主链 error（非 local 成功回退） */
    knowledgeDocsFail?: boolean;
  },
) {
  let pollGets = 0;
  const externalHosts: string[] = [];
  let rebuildHits = 0;

  await page.route("**/*", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const host = url.hostname;
    if (host !== "127.0.0.1" && host !== "localhost") {
      externalHosts.push(url.href);
      await route.abort("failed");
      return;
    }

    const path = url.pathname;
    const method = req.method();

    // 语义索引
    if (path === "/api/knowledge/semantic-index/rebuild" && method === "POST") {
      rebuildHits += 1;
      if (opts.semanticRebuildError) {
        await json(
          route,
          { detail: opts.semanticRebuildError.detail },
          opts.semanticRebuildError.status,
        );
        return;
      }
      const building =
        opts.afterRebuild ??
        baseIndex({
          id: "idx_building",
          status: "running",
          errorCode: "index_building",
          totalChunks: 12,
          embeddedChunks: 3,
          chunkCount: 3,
          startedAt: "2026-07-14T10:00:00+00:00",
        });
      opts.setState(building);
      pollGets = 0;
      await json(route, building, 202);
      return;
    }
    if (
      (path === "/api/knowledge/semantic-index" ||
        path.startsWith("/api/knowledge/semantic-index/")) &&
      method === "GET"
    ) {
      if (opts.semanticGetError) {
        await json(
          route,
          { detail: opts.semanticGetError.detail },
          opts.semanticGetError.status,
        );
        return;
      }
      pollGets += 1;
      if (
        opts.finalAfterPolls &&
        pollGets >= (opts.finalAfterPollCount ?? 2)
      ) {
        opts.setState(opts.finalAfterPolls);
      }
      await json(route, opts.getState());
      return;
    }

    // 知识库文档/文件夹：失败时由前端进入文档主 error（禁止 local 成功绿路径）
    if (path === "/api/knowledge/folders" && method === "GET") {
      if (opts.knowledgeDocsFail) {
        await json(
          route,
          {
            detail:
              "folders failed at C:\\\\Users\\\\secret\\\\db.sqlite apiKey=sk-leaked",
          },
          503,
        );
        return;
      }
      await json(route, [
        { id: "fld_inbox", name: "收件箱", parentId: null },
      ]);
      return;
    }
    if (path === "/api/knowledge/docs" && method === "GET") {
      if (opts.knowledgeDocsFail) {
        await json(
          route,
          {
            detail: "docs failed https://evil.example/apiKey/path",
          },
          503,
        );
        return;
      }
      await json(route, []);
      return;
    }
    if (path.startsWith("/api/cards") && method === "GET") {
      // listCards 返回数组（非 {items} 包装）
      await json(route, []);
      return;
    }
    if (path === "/api/health") {
      await json(route, {
        status: "ok",
        service: "biaoshu-e2e",
        workspaceId: "ws_e2e",
      });
      return;
    }
    // 其余本机资源（Vite/HMR）放行
    await route.continue();
  });

  return {
    getExternalHosts: () => externalHosts.slice(),
    getRebuildHits: () => rebuildHits,
  };
}

async function openKnowledgeBase(page: Page) {
  await page.goto("/knowledge-base");
  await expect(page.getByRole("heading", { name: "知识库" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByTestId("semantic-index-panel")).toBeVisible({
    timeout: 15_000,
  });
}

/** 语义索引 GET 路径（不含 rebuild POST） */
function isSemanticIndexGetUrl(url: string): boolean {
  try {
    const path = new URL(url).pathname;
    if (path.includes("/rebuild")) return false;
    return (
      path === "/api/knowledge/semantic-index" ||
      path.startsWith("/api/knowledge/semantic-index/")
    );
  } catch {
    return false;
  }
}

/**
 * 释放 route 前 arm 精确 browser terminal（仅 response；本文件 stub 均为 fulfill）。
 * 必须在 fulfill 之前安装；禁止仅依赖双 RAF 冒充 settle。
 */
function armSemanticGetResponseTerminal(
  page: Page,
  timeoutMs = 12_000,
): Promise<"response"> {
  return page
    .waitForEvent("response", {
      predicate: (r) =>
        isSemanticIndexGetUrl(r.url()) && r.request().method() === "GET",
      timeout: timeoutMs,
    })
    .then(() => "response" as const);
}

/** 释放 rebuild POST 前 arm 精确 browser terminal（仅 response；stub 均为 fulfill） */
function armSemanticRebuildResponseTerminal(
  page: Page,
  timeoutMs = 12_000,
): Promise<"response"> {
  return page
    .waitForEvent("response", {
      predicate: (r) => {
        try {
          const path = new URL(r.url()).pathname;
          return (
            path === "/api/knowledge/semantic-index/rebuild" &&
            r.request().method() === "POST"
          );
        } catch {
          return false;
        }
      },
      timeout: timeoutMs,
    })
    .then(() => "response" as const);
}

/** 业务 catch/finally 可观测 continuation：terminal 后再跑双 RAF + microtask */
async function businessContinuationBarrier(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        requestAnimationFrame(() => {
          requestAnimationFrame(() => resolve());
        });
      }),
  );
  await page.evaluate(() => Promise.resolve());
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        requestAnimationFrame(() => {
          requestAnimationFrame(() => resolve());
        });
      }),
  );
  await page.evaluate(() => Promise.resolve().then(() => Promise.resolve()));
}

/** 尝试 fulfill 已 hold 的 route；已完成则忽略（finally 收口用） */
async function safeFulfillHeld(
  route: Route,
  body: unknown,
  status = 200,
): Promise<void> {
  try {
    await json(route, body, status);
  } catch {
    // 已 fulfill / 已 abort / page 关闭：finally 不得再抛
  }
}

/**
 * Q12：一次性 DOM 快照（page.evaluate，禁止 auto-retry expect 等待后续 poll 掩蔽）。
 * 读取 status / counts / degrade / rebuild disabled。
 */
async function readSemanticPanelDomSnapshot(page: Page): Promise<{
  status: string;
  counts: string;
  degrade: string;
  rebuildDisabled: boolean | null;
}> {
  return page.evaluate(() => {
    const textOf = (testId: string) => {
      const el = document.querySelector(`[data-testid="${testId}"]`);
      return (el?.textContent ?? "").replace(/\s+/g, " ").trim();
    };
    const rebuild = document.querySelector(
      '[data-testid="semantic-index-rebuild"]',
    ) as HTMLButtonElement | null;
    return {
      status: textOf("semantic-index-status"),
      counts: textOf("semantic-index-counts"),
      degrade: textOf("semantic-index-degrade"),
      rebuildDisabled: rebuild ? Boolean(rebuild.disabled) : null,
    };
  });
}

/**
 * P2：一次性 DOM 快照（含 error 精确计数 + panel/body 文本）。
 * 禁止 auto-retry expect 等待后续 poll 掩蔽；禁止 errorCount>=0 恒真。
 */
async function readSemanticPanelErrorClearSnapshot(page: Page): Promise<{
  status: string;
  rebuildDisabled: boolean | null;
  errorCount: number;
  errorText: string;
  panelText: string;
  bodyText: string;
}> {
  return page.evaluate(() => {
    const textOf = (testId: string) => {
      const el = document.querySelector(`[data-testid="${testId}"]`);
      return (el?.textContent ?? "").replace(/\s+/g, " ").trim();
    };
    const rebuild = document.querySelector(
      '[data-testid="semantic-index-rebuild"]',
    ) as HTMLButtonElement | null;
    const panel = document.querySelector(
      '[data-testid="semantic-index-panel"]',
    );
    const errors = document.querySelectorAll(
      '[data-testid="semantic-index-error"]',
    );
    return {
      status: textOf("semantic-index-status"),
      rebuildDisabled: rebuild ? Boolean(rebuild.disabled) : null,
      errorCount: errors.length,
      errorText: Array.from(errors)
        .map((el) => (el.textContent ?? "").replace(/\s+/g, " ").trim())
        .join("|"),
      panelText: (panel?.textContent ?? "").replace(/\s+/g, " ").trim(),
      bodyText: (document.body?.textContent ?? "")
        .replace(/\s+/g, " ")
        .trim(),
    };
  });
}

test.describe("P9C 离线语义索引状态面板", () => {
  test("未构建时显示固定模型、关键词降级与构建按钮", async ({ page }) => {
    let state = baseIndex();
    const net = await installKbRoutes(page, {
      getState: () => state,
      setState: (n) => {
        state = n;
      },
    });

    await openKnowledgeBase(page);

    const panel = page.getByTestId("semantic-index-panel");
    await expect(panel.getByText("离线语义索引（本机）")).toBeVisible();
    await expect(panel.getByText(FIXED_MODEL)).toBeVisible();
    await expect(panel.getByTestId("semantic-index-status")).toContainText(
      /未构建|关键词降级/,
    );
    await expect(panel.getByTestId("semantic-index-degrade")).toContainText(
      "关键词",
    );
    const buildBtn = panel.getByRole("button", { name: "构建语义索引" });
    await expect(buildBtn).toBeVisible();
    await expect(buildBtn).toBeEnabled();

    // 无敏感配置入口
    await expect(page.locator('input[type="password"]')).toHaveCount(0);
    await expect(
      page.getByLabel(/模型\s*URL|Token|缓存路径|模型名称|供应商/i),
    ).toHaveCount(0);
    await expect(page.getByPlaceholder(/http|token|cache|huggingface/i)).toHaveCount(
      0,
    );

    // 既有入口仍可用
    await expect(
      page.getByRole("button", { name: /上传文档/ }),
    ).toBeVisible();
    await expect(page.getByText("收件箱")).toBeVisible();
    await page.getByRole("button", { name: /素材卡片/ }).click();
    await expect(
      page.getByRole("button", { name: /新建文本卡片|新建卡片/ }),
    ).toBeVisible();
    await page.getByRole("button", { name: /图片卡片/ }).click();
    await expect(
      page.getByRole("button", { name: /拖拽图片到此处/ }),
    ).toBeVisible();

    // 无模型站点/供应商请求（允许既有页面字体样式表的尝试被 route 拦截）
    const blocked = net.getExternalHosts();
    expect(
      blocked.filter(
        (u) =>
          !u.includes("fonts.googleapis.com") &&
          !u.includes("fonts.gstatic.com"),
      ),
    ).toEqual([]);
    expect(
      blocked.some((u) =>
        /huggingface|openai\.com|modelscope|cohere|api\.openai/i.test(u),
      ),
    ).toBe(false);
  });

  test("点击构建后进入构建中并轮询至已就绪", async ({ page }) => {
    let state = baseIndex();
    const finishedAt = "2026-07-14T10:05:00+00:00";
    const ready = baseIndex({
      id: "idx_ready",
      status: "active",
      errorCode: null,
      dimension: 512,
      totalChunks: 12,
      embeddedChunks: 12,
      chunkCount: 12,
      modelFingerprint: "fp_e2e_stub",
      startedAt: "2026-07-14T10:00:00+00:00",
      finishedAt,
    });

    await installKbRoutes(page, {
      getState: () => state,
      setState: (n) => {
        state = n;
      },
      afterRebuild: baseIndex({
        id: "idx_building",
        status: "running",
        errorCode: "index_building",
        totalChunks: 12,
        embeddedChunks: 4,
        chunkCount: 4,
        startedAt: "2026-07-14T10:00:00+00:00",
      }),
      finalAfterPolls: ready,
      finalAfterPollCount: 2,
    });

    await openKnowledgeBase(page);
    const panel = page.getByTestId("semantic-index-panel");
    const buildBtn = panel.getByRole("button", { name: "构建语义索引" });
    await buildBtn.click();

    await expect(panel.getByTestId("semantic-index-status")).toContainText(
      "构建中",
      { timeout: 10_000 },
    );
    await expect(panel.getByTestId("semantic-index-rebuild")).toBeDisabled();

    await expect(panel.getByTestId("semantic-index-status")).toContainText(
      /已就绪|就绪/,
      { timeout: 15_000 },
    );
    await expect(panel.getByTestId("semantic-index-dimension")).toContainText(
      "512",
    );
    await expect(panel.getByTestId("semantic-index-counts")).toContainText(
      /12/,
    );
    await expect(panel.getByTestId("semantic-index-finished")).toBeVisible();
    await expect(panel.getByTestId("semantic-index-rebuild")).toBeEnabled();
  });

  test("model_unavailable/failed 显示固定中文说明与重试，不泄露敏感信息", async ({
    page,
  }) => {
    let state = baseIndex({
      id: "idx_failed",
      status: "failed",
      errorCode: "model_unavailable",
      totalChunks: 8,
      embeddedChunks: 0,
      chunkCount: 0,
      finishedAt: "2026-07-14T09:00:00+00:00",
    });

    await installKbRoutes(page, {
      getState: () => state,
      setState: (n) => {
        state = n;
      },
    });

    await openKnowledgeBase(page);
    const panel = page.getByTestId("semantic-index-panel");
    await expect(panel.getByTestId("semantic-index-status")).toContainText(
      /模型不可用|失败/,
    );
    await expect(panel.getByTestId("semantic-index-degrade")).toContainText(
      /模型未就绪|关键词|不可用/,
    );
    await expect(
      panel.getByRole("button", { name: "重试构建" }),
    ).toBeVisible();
    await expect(panel).not.toContainText(/api[_-]?key|sk-|Bearer |C:\\|\/home\//i);
    await expect(panel).not.toContainText(/huggingface\.co|openai\.com/i);

    // 切换到 index_failed
    state = baseIndex({
      id: "idx_failed2",
      status: "failed",
      errorCode: "index_failed",
      finishedAt: "2026-07-14T09:10:00+00:00",
    });
    await page.reload();
    await expect(page.getByTestId("semantic-index-panel")).toBeVisible();
    await expect(
      page.getByTestId("semantic-index-status"),
    ).toContainText(/构建失败|失败/);
    await expect(
      page.getByRole("button", { name: "重试构建" }),
    ).toBeVisible();
  });

  test("active + model_unavailable 显示模型不可用与关键词降级，不显示已就绪", async ({
    page,
  }) => {
    // 模拟：库内索引 active，但进程内模型未就绪（状态 API 临时 errorCode）
    let state = baseIndex({
      id: "idx_active_no_model",
      status: "active",
      errorCode: "model_unavailable",
      dimension: 512,
      totalChunks: 10,
      embeddedChunks: 10,
      chunkCount: 10,
      modelFingerprint: "fp_persisted",
      finishedAt: "2026-07-14T08:00:00+00:00",
    });

    await installKbRoutes(page, {
      getState: () => state,
      setState: (n) => {
        state = n;
      },
    });

    await openKnowledgeBase(page);
    const panel = page.getByTestId("semantic-index-panel");
    await expect(panel.getByTestId("semantic-index-status")).toContainText(
      "模型不可用",
    );
    await expect(panel.getByTestId("semantic-index-status")).not.toContainText(
      "已就绪",
    );
    await expect(panel.getByTestId("semantic-index-degrade")).toContainText(
      /模型未就绪|关键词/,
    );
    await expect(
      panel.getByRole("button", { name: "重试构建" }),
    ).toBeVisible();
    await expect(panel.getByTestId("semantic-index-dimension")).toContainText(
      "512",
    );
    await expect(panel.getByTestId("semantic-index-model")).toHaveText(
      FIXED_MODEL,
    );
  });

  test("浏览器仅访问本机 Vite 与 /api，无模型站点请求", async ({ page }) => {
    let state = baseIndex();
    const external: string[] = [];
    page.on("request", (req) => {
      try {
        const u = new URL(req.url());
        if (u.hostname !== "127.0.0.1" && u.hostname !== "localhost") {
          external.push(req.url());
        }
      } catch {
        /* ignore */
      }
    });

    await installKbRoutes(page, {
      getState: () => state,
      setState: (n) => {
        state = n;
      },
    });
    await openKnowledgeBase(page);
    await page.getByRole("button", { name: "构建语义索引" }).click();
    await expect(page.getByTestId("semantic-index-status")).toContainText(
      /构建中|未构建|就绪/,
      { timeout: 10_000 },
    );
    // 语义索引构建不得触发模型站点/外发 API；既有 Google Fonts 链接不计入
    const nonFontExternal = external.filter(
      (u) =>
        !u.includes("fonts.googleapis.com") &&
        !u.includes("fonts.gstatic.com"),
    );
    expect(nonFontExternal).toEqual([]);
    expect(
      external.some((u) =>
        /huggingface|openai\.com|modelscope|cohere|api\.openai/i.test(u),
      ),
    ).toBe(false);
  });

  test("服务端返回非固定 modelId 时面板仍只展示固定模型", async ({ page }) => {
    let state = baseIndex({
      modelId: "openai/text-embedding-3-large",
      status: "active",
      errorCode: null,
      id: "idx_dirty_model",
      dimension: 512,
      totalChunks: 4,
      embeddedChunks: 4,
      chunkCount: 4,
      finishedAt: "2026-07-14T11:00:00+00:00",
    });

    await installKbRoutes(page, {
      getState: () => state,
      setState: (n) => {
        state = n;
      },
    });

    await openKnowledgeBase(page);
    const panel = page.getByTestId("semantic-index-panel");
    const modelCell = panel.getByTestId("semantic-index-model");
    await expect(modelCell).toHaveText(FIXED_MODEL);
    await expect(modelCell).not.toContainText("openai");
    await expect(modelCell).not.toContainText("text-embedding");
    await expect(panel).not.toContainText("openai/text-embedding-3-large");
  });

  test("服务端返回脏维度 1536 时面板仍只显示 512", async ({ page }) => {
    let state = baseIndex({
      id: "idx_dirty_dim",
      status: "active",
      errorCode: null,
      dimension: 1536,
      totalChunks: 6,
      embeddedChunks: 6,
      chunkCount: 6,
      finishedAt: "2026-07-14T11:30:00+00:00",
    });

    await installKbRoutes(page, {
      getState: () => state,
      setState: (n) => {
        state = n;
      },
    });

    await openKnowledgeBase(page);
    const panel = page.getByTestId("semantic-index-panel");
    const dimCell = panel.getByTestId("semantic-index-dimension");
    await expect(dimCell).toHaveText("512");
    await expect(dimCell).not.toContainText("1536");
    await expect(panel).not.toContainText("1536");
  });

  test("状态/重建接口敏感 detail 不回显，只显示固定中文错误", async ({
    page,
  }) => {
    let state = baseIndex();
    const sensitiveDetail = {
      message:
        "load failed at C:\\\\Users\\\\Administrator\\\\.cache\\\\bge apiKey=sk-leaked-secret url=https://api.openai.com/v1/embeddings",
      path: "C:\\\\Users\\\\Administrator\\\\models\\\\bge",
      apiKey: "sk-leaked-secret",
    };

    await installKbRoutes(page, {
      getState: () => state,
      setState: (n) => {
        state = n;
      },
      semanticGetError: { status: 500, detail: sensitiveDetail },
    });

    await openKnowledgeBase(page);
    const panel = page.getByTestId("semantic-index-panel");
    const err = panel.getByTestId("semantic-index-error");
    await expect(err).toBeVisible({ timeout: 10_000 });
    await expect(err).toHaveText(STATUS_UNAVAILABLE_MSG);
    await expect(panel).not.toContainText(/C:\\\\|apiKey|sk-leaked|openai\.com/i);
    await expect(panel).not.toContainText("Administrator");
    await expect(panel).not.toContainText("https://api.openai.com");

    // 重建失败路径：先恢复 GET，再让 rebuild 返回敏感 detail
    await page.unroute("**/*");
    let state2 = baseIndex();
    await installKbRoutes(page, {
      getState: () => state2,
      setState: (n) => {
        state2 = n;
      },
      semanticRebuildError: { status: 500, detail: sensitiveDetail },
    });
    await page.reload();
    await expect(page.getByTestId("semantic-index-panel")).toBeVisible({
      timeout: 15_000,
    });
    const rebuildBtn = page.getByRole("button", { name: "构建语义索引" });
    await expect(rebuildBtn).toBeEnabled({ timeout: 10_000 });
    await rebuildBtn.click();
    const rebuildErr = page.getByTestId("semantic-index-error");
    await expect(rebuildErr).toBeVisible({ timeout: 10_000 });
    await expect(rebuildErr).toHaveText(REBUILD_FAILED_MSG);
    await expect(page.getByTestId("semantic-index-panel")).not.toContainText(
      /C:\\\\|apiKey|sk-leaked|openai\.com/i,
    );
  });

  test("folders/docs API 失败：显式文档失败、语义不可构建、零伪就绪、旧 docs 键原值不变", async ({
    page,
  }) => {
    const DOCS_LS_KEY = "biaoshu.knowledgeBase.docs.v1";
    const LOAD_ERROR = "知识库文档加载失败，请稍后重试";
    const PRESET_DOCS_VALUE = JSON.stringify({
      folders: [{ id: "fld_preset", name: "预置夹", parentId: null }],
      docs: [
        {
          id: "kb_preset",
          name: "PRESET_DOCS_KEY_MUST_STAY.txt",
          tags: ["preset"],
          chunks: 1,
          updated: "preset",
          updatedAt: "2020-01-01T00:00:00.000Z",
          category: "preset",
          folderId: "fld_preset",
          status: "ready",
        },
      ],
    });

    await page.addInitScript(
      ({ key, value }) => {
        window.localStorage.setItem(key, value);
      },
      { key: DOCS_LS_KEY, value: PRESET_DOCS_VALUE },
    );

    let state = baseIndex({
      status: "active",
      errorCode: null,
      id: "idx_should_not_persist",
    });
    const net = await installKbRoutes(page, {
      getState: () => state,
      setState: (n) => {
        state = n;
      },
      knowledgeDocsFail: true,
    });

    await openKnowledgeBase(page);
    const panel = page.getByTestId("semantic-index-panel");

    // V1-O：文档主失败固定文案；禁止 local 成功暗示与 mock 文档真值
    await expect(page.getByText(LOAD_ERROR)).toBeVisible({ timeout: 15_000 });
    await expect(page.locator("body")).not.toContainText("离线本地演示");
    await expect(page.locator("body")).not.toContainText("本地演示");
    await expect(page.locator("body")).not.toContainText(
      "PRESET_DOCS_KEY_MUST_STAY",
    );
    await expect(page.locator("body")).not.toContainText(
      "智慧交通同类业绩汇编",
    );
    await expect(page.getByText("知识库暂无文档")).toHaveCount(0);

    // 语义不可构建：零 rebuild；状态不得伪造成已就绪；上传等写入口不得依赖 local
    await expect(panel.getByTestId("semantic-index-status")).not.toContainText(
      /已就绪|就绪/,
    );
    await expect(panel.getByTestId("semantic-index-model")).toHaveText(
      FIXED_MODEL,
    );

    const buildBtn = panel.getByTestId("semantic-index-rebuild");
    await expect(buildBtn).toBeDisabled();
    await buildBtn.click({ force: true });
    await expect.poll(() => net.getRebuildHits()).toBe(0);

    // 不向 localStorage 写入语义伪就绪字段
    const storageKeys = await page.evaluate(() => {
      const keys: string[] = [];
      for (let i = 0; i < localStorage.length; i += 1) {
        const k = localStorage.key(i);
        if (k) keys.push(k);
      }
      return keys;
    });
    expect(
      storageKeys.filter((k) =>
        /semanticIndex|semanticStatus|semantic-index|semantic_index/i.test(k),
      ),
    ).toEqual([]);

    // 旧 docs 键原值精确不变（不读不写不删不迁移不上传）
    const docsCacheRaw = await page.evaluate(
      (key) => localStorage.getItem(key),
      DOCS_LS_KEY,
    );
    expect(docsCacheRaw).toBe(PRESET_DOCS_VALUE);
    expect(docsCacheRaw).not.toMatch(
      /semanticIndex|semanticStatus|idx_should_not_persist/i,
    );

    // 敏感 folders/docs detail 不得进入 DOM
    await expect(page.locator("body")).not.toContainText("sk-leaked");
    await expect(page.locator("body")).not.toContainText("evil.example");

    await expect(page.getByRole("heading", { name: "知识库" })).toBeVisible();
  });

  // ---------------------------------------------------------------------------
  // Q10/Q11-TEST-FIX：semantic 竞态 / finishedAt 隐私 test-first 红门（production 未改预期红）
  // ---------------------------------------------------------------------------

  /**
   * Q11+Q12 最终：确定业务路径先进入 building，再 hold 同代 poll GET（禁 StrictMode）；
   * fulfill B 后先用 B 唯一 DOM 9/9 证已提交；此后 hold 全部后续 poll（含第 3+）；
   * 释 A 前 arm 精确 terminal；等 A terminal+业务 continuation 后 page.evaluate 一次性
   * snapshot（status/counts/degrade）精确仍 B；finally 释放全部 held。
   */
  test("同代 poll GET A/B 逆序：先证 B 提交再释 A，终态保持 B", async ({
    page,
  }) => {
    const notBuilt = baseIndex();
    const building = baseIndex({
      id: "idx_building",
      status: "running",
      errorCode: "index_building",
      totalChunks: 12,
      embeddedChunks: 3,
      chunkCount: 3,
      startedAt: "2026-07-14T10:00:00+00:00",
    });
    // A=旧代 poll 结果（未构建）；B=新代唯一就绪态（分块 9 为 DOM 唯一指纹）
    const payloadA = baseIndex({
      id: "idx_old_a",
      status: "index_not_built",
      errorCode: "index_not_built",
      totalChunks: 0,
      embeddedChunks: 0,
      chunkCount: 0,
    });
    const payloadB = baseIndex({
      id: "idx_new_b",
      status: "active",
      errorCode: null,
      dimension: 512,
      totalChunks: 9,
      embeddedChunks: 9,
      chunkCount: 9,
      modelFingerprint: "fp_new_b",
      finishedAt: "2026-07-14T12:00:00+00:00",
    });
    type Held = { route: Route; body: SemanticIndexPayload; released: boolean };
    const held: Held[] = [];
    let pollSeq = 0;
    /**
     * bootstrap：初始 GET 立即 not_built
     * building-hold：rebuild 后全部 poll hold（含第 3+ 次，禁止即时 fulfill 掩蔽）
     */
    let phase: "bootstrap" | "building-hold" = "bootstrap";
    let rebuildHits = 0;

    await page.route("**/*", async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
        await route.abort("failed");
        return;
      }
      const path = url.pathname;
      const method = req.method();
      if (path === "/api/knowledge/semantic-index/rebuild" && method === "POST") {
        rebuildHits += 1;
        phase = "building-hold";
        await json(route, building, 202);
        return;
      }
      if (
        (path === "/api/knowledge/semantic-index" ||
          path.startsWith("/api/knowledge/semantic-index/")) &&
        method === "GET" &&
        !path.includes("/rebuild")
      ) {
        if (phase === "bootstrap") {
          await json(route, notBuilt);
          return;
        }
        // Q12：building 后全部 poll hold（第 1=A、第 2+=B 指纹；禁止 length>=2 后即时 fulfill）
        pollSeq += 1;
        const body = pollSeq === 1 ? payloadA : payloadB;
        held.push({ route, body, released: false });
        return;
      }
      if (path === "/api/knowledge/folders" && method === "GET") {
        await json(route, [{ id: "fld_inbox", name: "收件箱", parentId: null }]);
        return;
      }
      if (path === "/api/knowledge/docs" && method === "GET") {
        await json(route, []);
        return;
      }
      if (path.startsWith("/api/cards") && method === "GET") {
        await json(route, []);
        return;
      }
      if (path === "/api/health") {
        await json(route, {
          status: "ok",
          service: "biaoshu-e2e",
          workspaceId: "ws_e2e",
        });
        return;
      }
      await route.continue();
    });

    try {
      await openKnowledgeBase(page);
      const panel = page.getByTestId("semantic-index-panel");
      const buildBtn = panel.getByRole("button", { name: "构建语义索引" });
      await expect(buildBtn).toBeEnabled({ timeout: 10_000 });
      // 确定业务路径：先进入 building（POST 已提交），再收集同代 poll
      await buildBtn.click();
      await expect(panel.getByTestId("semantic-index-status")).toContainText(
        "构建中",
        { timeout: 10_000 },
      );
      expect(rebuildHits).toBe(1);

      // 等待 building 后同代双 poll 均 arrived（hold 中；轮询间隔约 2s）
      await expect
        .poll(() => held.length, { timeout: 20_000 })
        .toBeGreaterThanOrEqual(2);
      const a = held[0]!;
      const b = held[1]!;
      expect(a.body.id).toBe("idx_old_a");
      expect(b.body.id).toBe("idx_new_b");

      // 先 fulfill B，并用 B 唯一状态在 DOM 证明已提交（9/9）
      await json(b.route, b.body);
      b.released = true;
      await expect(panel.getByTestId("semantic-index-status")).toContainText(
        /已就绪|就绪/,
        { timeout: 10_000 },
      );
      await expect(panel.getByTestId("semantic-index-counts")).toContainText(
        /9\s*\/\s*9|9\/9/,
      );
      await expect(
        panel.getByTestId("semantic-index-status"),
      ).not.toContainText(/未构建|构建中/);

      // Q12：B DOM 9/9 commit 后继续 hold 全部后续 poll（含 held[2+]；不得即时 fulfill）
      // 后续 poll 全挂起：除 a/b 已处理外，其余 must remain unreleased
      expect(
        held.slice(2).every((h) => !h.released),
        "B 提交后第 3+ poll 必须仍 hold",
      ).toBe(true);

      // 释放 A 前 arm 精确 response terminal
      const aTerminal = armSemanticGetResponseTerminal(page);
      await json(a.route, a.body);
      a.released = true;
      await aTerminal;
      await businessContinuationBarrier(page);

      // 后续 poll 仍全挂起窗口：page.evaluate 一次性 snapshot（禁止 expect auto-retry）
      expect(
        held.slice(2).every((h) => !h.released),
        "snapshot 前第 3+ poll 不得已提交",
      ).toBe(true);
      const snap = await readSemanticPanelDomSnapshot(page);
      // 精确仍为 B：ready、9/9、非 not_built/building
      expect(snap.status).toMatch(/已就绪|就绪/);
      expect(snap.status).not.toMatch(/未构建|构建中/);
      expect(snap.counts).toMatch(/9\s*\/\s*9|9\/9/);
      expect(snap.degrade).toMatch(/已就绪|混合关键词|本机向量/);
      expect(snap.degrade).not.toMatch(/未构建|构建中|关键词降级/);
    } finally {
      // 释放/收口全部仍 hold 的 route，避免挂起；不得在 snapshot 前泄露
      for (const h of held) {
        if (!h.released) {
          await safeFulfillHeld(h.route, h.body);
          h.released = true;
        }
      }
    }
  });

  test("同 tick 双 rebuild：精确一 POST", async ({ page }) => {
    let state = baseIndex();
    let rebuildHits = 0;
    await page.route("**/*", async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
        await route.abort("failed");
        return;
      }
      const path = url.pathname;
      const method = req.method();
      if (path === "/api/knowledge/semantic-index/rebuild" && method === "POST") {
        rebuildHits += 1;
        const building = baseIndex({
          id: "idx_building",
          status: "running",
          errorCode: "index_building",
          totalChunks: 12,
          embeddedChunks: 3,
          chunkCount: 3,
          startedAt: "2026-07-14T10:00:00+00:00",
        });
        state = building;
        await json(route, building, 202);
        return;
      }
      if (
        (path === "/api/knowledge/semantic-index" ||
          path.startsWith("/api/knowledge/semantic-index/")) &&
        method === "GET"
      ) {
        await json(route, state);
        return;
      }
      if (path === "/api/knowledge/folders" && method === "GET") {
        await json(route, [{ id: "fld_inbox", name: "收件箱", parentId: null }]);
        return;
      }
      if (path === "/api/knowledge/docs" && method === "GET") {
        await json(route, []);
        return;
      }
      if (path.startsWith("/api/cards") && method === "GET") {
        await json(route, []);
        return;
      }
      if (path === "/api/health") {
        await json(route, {
          status: "ok",
          service: "biaoshu-e2e",
          workspaceId: "ws_e2e",
        });
        return;
      }
      await route.continue();
    });

    await openKnowledgeBase(page);
    const panel = page.getByTestId("semantic-index-panel");
    const buildBtn = panel.getByRole("button", { name: "构建语义索引" });
    await expect(buildBtn).toBeEnabled({ timeout: 10_000 });
    // 同 tick 双 click：正确实现同步 ref 锁 → 精确 1 POST
    await buildBtn.evaluate((el) => {
      (el as HTMLButtonElement).click();
      (el as HTMLButtonElement).click();
    });
    await expect.poll(() => rebuildHits, { timeout: 10_000 }).toBeGreaterThan(0);
    // 允许微任务 drain 后再冻结终值
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => {
          requestAnimationFrame(() => {
            requestAnimationFrame(() => resolve());
          });
        }),
    );
    expect(rebuildHits).toBe(1);
  });

  /**
   * Q11+Q12 最终：真正 hold rebuild POST 之前已到达的 semantic GET；
   * POST 后 poll 全部 hold；building 后先等到 held post>0，再释放旧 GET 并等各 terminal+continuation；
   * 在 post 仍 hold 窗口 page.evaluate 一次性 snapshot 仍 building/disabled；
   * finally 释放全部 pre/post。禁止 POST 后 poll 冒充旧 GET；删除 >=0 恒真。
   */
  test("旧 GET 不得覆盖 rebuild 的 building", async ({ page }) => {
    const notBuilt = baseIndex();
    const building = baseIndex({
      id: "idx_building",
      status: "running",
      errorCode: "index_building",
      totalChunks: 12,
      embeddedChunks: 4,
      chunkCount: 4,
      startedAt: "2026-07-14T10:00:00+00:00",
    });
    let rebuildHits = 0;
    /** rebuild POST 是否已返回（用于分账：pre vs post） */
    let rebuildReturned = false;
    /** rebuild 前已到达并 hold 的 semantic GET（禁止用 POST 后 poll 冒充） */
    type HeldRoute = { route: Route; released: boolean };
    const preRebuildHeld: HeldRoute[] = [];
    /** POST 后到达的 poll：全程 hold，直至 finally 释放 */
    const postRebuildHeld: HeldRoute[] = [];

    await page.route("**/*", async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
        await route.abort("failed");
        return;
      }
      const path = url.pathname;
      const method = req.method();
      if (path === "/api/knowledge/semantic-index/rebuild" && method === "POST") {
        rebuildHits += 1;
        rebuildReturned = true;
        await json(route, building, 202);
        return;
      }
      if (
        (path === "/api/knowledge/semantic-index" ||
          path.startsWith("/api/knowledge/semantic-index/")) &&
        method === "GET"
      ) {
        if (!rebuildReturned) {
          // rebuild 前到达：全部 hold（含 ready 后触发的 refreshSemanticIndex）
          preRebuildHeld.push({ route, released: false });
          return;
        }
        // Q12：POST 后 poll 全 hold（禁止即时 fulfill building 掩蔽旧 GET 覆盖）
        postRebuildHeld.push({ route, released: false });
        return;
      }
      if (path === "/api/knowledge/folders" && method === "GET") {
        await json(route, [{ id: "fld_inbox", name: "收件箱", parentId: null }]);
        return;
      }
      if (path === "/api/knowledge/docs" && method === "GET") {
        await json(route, []);
        return;
      }
      if (path.startsWith("/api/cards") && method === "GET") {
        await json(route, []);
        return;
      }
      if (path === "/api/health") {
        await json(route, {
          status: "ok",
          service: "biaoshu-e2e",
          workspaceId: "ws_e2e",
        });
        return;
      }
      await route.continue();
    });

    try {
      await page.goto("/knowledge-base");
      await expect(page.getByRole("heading", { name: "知识库" })).toBeVisible({
        timeout: 20_000,
      });
      // folders/docs ready 后 hook 会发起 semantic GET；hold 之，仍可点重建（docsReady）
      await expect
        .poll(() => preRebuildHeld.length, { timeout: 15_000 })
        .toBeGreaterThanOrEqual(1);
      expect(rebuildReturned).toBe(false);
      // 前置不变量：rebuild 前至少一条 semantic GET 已 arrived 并 hold（click 前证明）
      expect(
        preRebuildHeld.length,
        "rebuild 前必须已有 semantic GET hold",
      ).toBeGreaterThanOrEqual(1);

      const panel = page.getByTestId("semantic-index-panel");
      const buildBtn = panel.getByTestId("semantic-index-rebuild");
      await expect(buildBtn).toBeEnabled({ timeout: 10_000 });
      const preCountAtClick = preRebuildHeld.length;
      await buildBtn.click();
      // rebuild 返回 building 并已提交到 DOM
      await expect(panel.getByTestId("semantic-index-status")).toContainText(
        "构建中",
        { timeout: 10_000 },
      );
      expect(rebuildHits).toBe(1);
      expect(rebuildReturned).toBe(true);
      // 旧 GET 集合在 click 前已冻结；POST 后 poll 不得并入 preRebuildHeld
      expect(preRebuildHeld.length).toBe(preCountAtClick);

      // Q12：先在 building 仍有效窗口等待 held post poll >0（禁止 empty.every / >=0 恒真）
      // 必须先于释放旧 GET：否则 notBuilt 可停表导致永远等不到 post poll
      await expect
        .poll(() => postRebuildHeld.length, { timeout: 20_000 })
        .toBeGreaterThan(0);
      expect(
        postRebuildHeld.every((h) => !h.released),
        "释放旧 GET 前 post poll 必须全 hold",
      ).toBe(true);

      // building 已提交且 post 已 arrived：逐条释放 pre-rebuild 旧 GET，各 arm terminal + continuation
      for (const held of preRebuildHeld) {
        const terminal = armSemanticGetResponseTerminal(page);
        await json(held.route, notBuilt);
        held.released = true;
        await terminal;
        await businessContinuationBarrier(page);
      }

      // post poll 未提交窗口：held post >0 仍成立 + page.evaluate 一次性仍 building/disabled
      const heldPostCount = postRebuildHeld.filter((h) => !h.released).length;
      expect(heldPostCount, "held post poll 必须 >0").toBeGreaterThan(0);
      expect(
        postRebuildHeld.every((h) => !h.released),
        "snapshot 前 post poll 不得已提交",
      ).toBe(true);
      const snap = await readSemanticPanelDomSnapshot(page);
      expect(snap.status).toMatch(/构建中/);
      expect(snap.status).not.toMatch(/未构建|已就绪|就绪/);
      expect(snap.rebuildDisabled).toBe(true);
    } finally {
      // 释放全部 pre/post routes，避免挂起
      for (const held of preRebuildHeld) {
        if (!held.released) {
          await safeFulfillHeld(held.route, notBuilt);
          held.released = true;
        }
      }
      for (const held of postRebuildHeld) {
        if (!held.released) {
          await safeFulfillHeld(held.route, building);
          held.released = true;
        }
      }
    }
  });

  /**
   * P2 独立红门（不改写/弱化 Q12 成功迟到门）：
   * 1) folders/docs ready 后 hold click 前首个 semantic GET A，明确 A arrived；
   * 2) 点击重建并 hold POST，明确 POST arrived 未返回；
   * 3) POST 仍 hold 时让 A（及所有仍 hold 的 pre-GET）以 503+合成敏感 detail 结束，
   *    等 response terminal + 业务 continuation，先证固定错误「语义索引状态不可用」真实出现；
   * 4) 再释放 POST 为 202 building，等 POST terminal + continuation，DOM 进入「构建中」；
   * 5) POST 后全部 poll GET 必须 hold，且至少一条 arrived 未提交，排除后续成功 poll 清错；
   * 6) 一次 DOM snapshot：status=构建中、重建 disabled、semantic-index-error 精确 0、
   *    panel/body 不含旧错误与合成 detail；
   * 7) finally 释放/abort 全部 held，禁止悬挂。
   * 当前 production 成功分支写 building 时未再 setSemanticError(null) → 第 6 步稳定业务红。
   */
  test("rebuild 成功须清除 POST hold 窗口旧 GET 错误", async ({ page }) => {
    const building = baseIndex({
      id: "idx_building",
      status: "running",
      errorCode: "index_building",
      totalChunks: 12,
      embeddedChunks: 4,
      chunkCount: 4,
      startedAt: "2026-07-14T10:00:00+00:00",
    });
    /** 合成敏感 detail：仅服务端 body，禁止进入 DOM */
    const SENSITIVE_DETAIL =
      "pre-rebuild get failed at C:\\\\Users\\\\secret\\\\bge apiKey=sk-pre-rebuild-get-leak path=/var/secret/pre";
    const SENSITIVE_MARKERS = [
      "sk-pre-rebuild-get-leak",
      "Users\\\\secret",
      "/var/secret/pre",
      "apiKey=",
    ] as const;

    type HeldRoute = { route: Route; released: boolean };
    /** click 前（及 POST 未返回前）到达的 semantic GET；首个为 A */
    const preRebuildHeld: HeldRoute[] = [];
    /** POST 返回后的 poll：全程 hold 至 finally */
    const postRebuildHeld: HeldRoute[] = [];
    let rebuildHits = 0;
    /** POST 是否已进入 route（arrived） */
    let postArrived = false;
    /** POST 是否已 fulfill（returned） */
    let postReleased = false;
    let heldPost: HeldRoute | null = null;

    await page.route("**/*", async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
        await route.abort("failed");
        return;
      }
      const path = url.pathname;
      const method = req.method();
      if (path === "/api/knowledge/semantic-index/rebuild" && method === "POST") {
        rebuildHits += 1;
        postArrived = true;
        heldPost = { route, released: false };
        // hold：不 fulfill，明确 arrived 未返回
        return;
      }
      if (
        (path === "/api/knowledge/semantic-index" ||
          path.startsWith("/api/knowledge/semantic-index/")) &&
        method === "GET" &&
        !path.includes("/rebuild")
      ) {
        if (!postReleased) {
          // click 前 / POST hold 窗口：全部 hold（首个为 A）
          preRebuildHeld.push({ route, released: false });
          return;
        }
        // POST 后 poll 全 hold（禁止即时成功 fulfill 清错掩蔽）
        postRebuildHeld.push({ route, released: false });
        return;
      }
      if (path === "/api/knowledge/folders" && method === "GET") {
        await json(route, [{ id: "fld_inbox", name: "收件箱", parentId: null }]);
        return;
      }
      if (path === "/api/knowledge/docs" && method === "GET") {
        await json(route, []);
        return;
      }
      if (path.startsWith("/api/cards") && method === "GET") {
        await json(route, []);
        return;
      }
      if (path === "/api/health") {
        await json(route, {
          status: "ok",
          service: "biaoshu-e2e",
          workspaceId: "ws_e2e",
        });
        return;
      }
      await route.continue();
    });

    try {
      await page.goto("/knowledge-base");
      await expect(page.getByRole("heading", { name: "知识库" })).toBeVisible({
        timeout: 20_000,
      });

      // 1) folders/docs ready 后 hold click 前首个 semantic GET A
      await expect
        .poll(() => preRebuildHeld.length, { timeout: 15_000 })
        .toBeGreaterThanOrEqual(1);
      const a = preRebuildHeld[0]!;
      expect(a.released, "A 必须 arrived 且未提交").toBe(false);
      expect(postArrived, "click 前 POST 不得已到达").toBe(false);
      expect(rebuildHits).toBe(0);

      const panel = page.getByTestId("semantic-index-panel");
      const buildBtn = panel.getByTestId("semantic-index-rebuild");
      await expect(buildBtn).toBeEnabled({ timeout: 10_000 });
      const preCountAtClick = preRebuildHeld.length;

      // 2) 点击重建并 hold POST
      await buildBtn.click();
      await expect
        .poll(() => postArrived && heldPost != null, { timeout: 10_000 })
        .toBe(true);
      expect(rebuildHits).toBe(1);
      expect(postReleased).toBe(false);
      expect(heldPost!.released, "POST 必须 arrived 未返回").toBe(false);
      // click 前 A 集合冻结口径：pre 在 POST 返回前可继续 arrived，但 A 仍是首个
      expect(preRebuildHeld[0]).toBe(a);
      expect(preRebuildHeld.length).toBeGreaterThanOrEqual(preCountAtClick);

      // 3) POST 仍 hold：让全部 pre-GET（含 A）以 503 + 敏感 detail 结束
      expect(postReleased, "释 A 前 POST 仍必须 hold").toBe(false);
      for (const held of preRebuildHeld) {
        if (held.released) continue;
        const terminal = armSemanticGetResponseTerminal(page);
        await json(held.route, { detail: SENSITIVE_DETAIL }, 503);
        held.released = true;
        await terminal;
        await businessContinuationBarrier(page);
      }
      // 先证固定错误真实出现（不得跳过）
      const err = panel.getByTestId("semantic-index-error");
      await expect(err).toBeVisible({ timeout: 10_000 });
      await expect(err).toHaveText(STATUS_UNAVAILABLE_MSG);
      await expect(panel).not.toContainText(/sk-pre-rebuild-get-leak|apiKey=/i);
      await expect(page.locator("body")).not.toContainText(
        "sk-pre-rebuild-get-leak",
      );

      // 4) 释放 POST=202 building；等 terminal + continuation；DOM 构建中
      expect(heldPost, "POST held 句柄必须存在").not.toBeNull();
      const postTerminal = armSemanticRebuildResponseTerminal(page);
      await json(heldPost!.route, building, 202);
      heldPost!.released = true;
      postReleased = true;
      await postTerminal;
      await businessContinuationBarrier(page);
      await expect(panel.getByTestId("semantic-index-status")).toContainText(
        "构建中",
        { timeout: 10_000 },
      );

      // 5) POST 后 poll 全 hold，至少一条 arrived 未提交
      await expect
        .poll(() => postRebuildHeld.length, { timeout: 20_000 })
        .toBeGreaterThan(0);
      expect(
        postRebuildHeld.every((h) => !h.released),
        "snapshot 前 post poll 必须全 hold",
      ).toBe(true);
      const heldPostPollCount = postRebuildHeld.filter((h) => !h.released)
        .length;
      expect(heldPostPollCount, "held post poll 必须 >0").toBeGreaterThan(0);

      // 6) 一次 snapshot：构建中 + rebuild disabled + error 精确 0 + 无旧错/敏感
      const snap = await readSemanticPanelErrorClearSnapshot(page);
      expect(snap.status).toMatch(/构建中/);
      expect(snap.status).not.toMatch(/未构建|已就绪|就绪/);
      expect(snap.rebuildDisabled).toBe(true);
      // 业务红点：成功 building 后旧 GET 错误必须清除（errorCount 精确 0）
      expect(snap.errorCount, "semantic-index-error 必须精确 0").toBe(0);
      expect(snap.errorText).toBe("");
      expect(snap.panelText).not.toContain(STATUS_UNAVAILABLE_MSG);
      expect(snap.bodyText).not.toContain(STATUS_UNAVAILABLE_MSG);
      for (const marker of SENSITIVE_MARKERS) {
        expect(snap.panelText).not.toContain(marker);
        expect(snap.bodyText).not.toContain(marker);
      }
    } finally {
      // 7) 释放/收口全部 held，禁止悬挂
      for (const held of preRebuildHeld) {
        if (!held.released) {
          await safeFulfillHeld(held.route, { detail: SENSITIVE_DETAIL }, 503);
          held.released = true;
        }
      }
      if (heldPost && !heldPost.released) {
        await safeFulfillHeld(heldPost.route, building, 202);
        heldPost.released = true;
        postReleased = true;
      }
      for (const held of postRebuildHeld) {
        if (!held.released) {
          await safeFulfillHeld(held.route, building);
          held.released = true;
        }
      }
    }
  });

  test("building 后轮询 GET 503：保留 building、继续轮询、仅固定安全错误", async ({
    page,
  }) => {
    const building = baseIndex({
      id: "idx_building",
      status: "running",
      errorCode: "index_building",
      totalChunks: 12,
      embeddedChunks: 5,
      chunkCount: 5,
      startedAt: "2026-07-14T10:00:00+00:00",
    });
    let state: SemanticIndexPayload = baseIndex();
    let rebuildDone = false;
    let pollGetsAfterRebuild = 0;
    const sensitiveDetail =
      "poll failed at C:\\\\Users\\\\secret\\\\bge apiKey=sk-poll-leak path=/var/secret";

    await page.route("**/*", async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
        await route.abort("failed");
        return;
      }
      const path = url.pathname;
      const method = req.method();
      if (path === "/api/knowledge/semantic-index/rebuild" && method === "POST") {
        rebuildDone = true;
        state = building;
        await json(route, building, 202);
        return;
      }
      if (
        (path === "/api/knowledge/semantic-index" ||
          path.startsWith("/api/knowledge/semantic-index/")) &&
        method === "GET"
      ) {
        if (rebuildDone) {
          pollGetsAfterRebuild += 1;
          // 轮询全部 503（敏感 detail）
          await json(route, { detail: sensitiveDetail }, 503);
          return;
        }
        await json(route, state);
        return;
      }
      if (path === "/api/knowledge/folders" && method === "GET") {
        await json(route, [{ id: "fld_inbox", name: "收件箱", parentId: null }]);
        return;
      }
      if (path === "/api/knowledge/docs" && method === "GET") {
        await json(route, []);
        return;
      }
      if (path.startsWith("/api/cards") && method === "GET") {
        await json(route, []);
        return;
      }
      if (path === "/api/health") {
        await json(route, {
          status: "ok",
          service: "biaoshu-e2e",
          workspaceId: "ws_e2e",
        });
        return;
      }
      await route.continue();
    });

    await openKnowledgeBase(page);
    const panel = page.getByTestId("semantic-index-panel");
    await panel.getByRole("button", { name: "构建语义索引" }).click();
    await expect(panel.getByTestId("semantic-index-status")).toContainText(
      "构建中",
      { timeout: 10_000 },
    );

    // 至少两轮轮询 503 后仍须保留 building，并只显示固定安全错误
    await expect
      .poll(() => pollGetsAfterRebuild, { timeout: 20_000 })
      .toBeGreaterThanOrEqual(2);
    const pollsAtAssert = pollGetsAfterRebuild;
    await expect(panel.getByTestId("semantic-index-status")).toContainText(
      "构建中",
    );
    await expect(panel.getByTestId("semantic-index-status")).not.toContainText(
      /未构建|已就绪/,
    );
    const err = panel.getByTestId("semantic-index-error");
    await expect(err).toBeVisible({ timeout: 10_000 });
    await expect(err).toHaveText(STATUS_UNAVAILABLE_MSG);
    // Q11：本用例仅宣称 panel/body 实际覆盖（全公开面探针见 knowledge 主 spec）
    await expect(panel).not.toContainText(/sk-poll-leak|C:\\\\Users\\\\secret|apiKey/i);
    await expect(page.locator("body")).not.toContainText("sk-poll-leak");
    await expect(page.locator("body")).not.toContainText("Users\\\\secret");
    // 继续轮询：断言后再增至少 1 次（未因 catch 清 null 停表）
    await expect
      .poll(() => pollGetsAfterRebuild, { timeout: 15_000 })
      .toBeGreaterThan(pollsAtAssert);
  });

  /**
   * Q4（本文件）：非法 finishedAt 精确「—」+ panel/body 窄面。
   * 全公开面（request/console args drain/DOM 历史/Storage/IDB/Cookie）由
   * knowledge-doc-server-truth.spec.ts 复用 preparePage/assertPrivacyClean 权威证明。
   */
  test("finishedAt 非法路径/apiKey：精确显示 —（窄面；全公开面见 knowledge 主 spec）", async ({
    page,
  }) => {
    const FINISHED_MARKER_PATH = "C:\\Users\\secret\\models\\bge-cache";
    const FINISHED_MARKER_KEY = "apiKey=sk-finished-at-leak-v1o";
    const illegalFinishedAt = `${FINISHED_MARKER_PATH} ${FINISHED_MARKER_KEY}`;
    let state = baseIndex({
      id: "idx_dirty_finished",
      status: "active",
      errorCode: null,
      dimension: 512,
      totalChunks: 4,
      embeddedChunks: 4,
      chunkCount: 4,
      finishedAt: illegalFinishedAt,
    });

    await installKbRoutes(page, {
      getState: () => state,
      setState: (n) => {
        state = n;
      },
    });
    await openKnowledgeBase(page);
    const panel = page.getByTestId("semantic-index-panel");
    const finished = panel.getByTestId("semantic-index-finished");
    await expect(finished).toBeVisible({ timeout: 10_000 });
    // 非法 finishedAt 不得原样回显；精确固定安全占位「—」
    await expect(finished).toHaveText("—");
    await expect(finished).not.toContainText(FINISHED_MARKER_PATH);
    await expect(finished).not.toContainText("sk-finished-at-leak-v1o");
    await expect(finished).not.toContainText("apiKey");
    await expect(panel).not.toContainText("sk-finished-at-leak-v1o");
    await expect(panel).not.toContainText("Users\\secret");
    await expect(page.locator("body")).not.toContainText(
      "sk-finished-at-leak-v1o",
    );
    await expect(page.locator("body")).not.toContainText(
      "C:\\Users\\secret\\models",
    );
  });
});
