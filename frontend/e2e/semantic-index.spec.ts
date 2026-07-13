/**
 * 模块：P9C 离线语义索引状态面板 E2E
 * 用途：用 Playwright route 拦截本机 /api/knowledge/semantic-index*，验收未构建/构建中/已就绪/失败降级、固定模型展示、受控错误与 local 回退；不启动真实模型、不触网。
 * 对接：Playwright chromium；前端 5174 + Vite proxy /api；npm run test:e2e:semantic-index。
 * 二次开发：禁止真实模型下载、外网 host、localStorage 伪就绪；勿改 cards/matrix 等既有 spec。
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
    /** folders/docs 失败以进入 local 回退 */
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

    // 知识库文档/文件夹：失败时进入 source=local
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

  test("folders/docs API 失败进入 local：不可构建且无伪就绪 localStorage", async ({
    page,
  }) => {
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

    // 本地演示 / 不可构建
    await expect(panel.getByTestId("semantic-index-status")).toContainText(
      /本地演示|不可构建/,
    );
    await expect(panel.getByTestId("semantic-index-degrade")).toContainText(
      /本地演示|无法构建/,
    );
    await expect(panel.getByTestId("semantic-index-model")).toHaveText(
      FIXED_MODEL,
    );

    const buildBtn = panel.getByTestId("semantic-index-rebuild");
    await expect(buildBtn).toBeDisabled();

    // disabled 按钮即使用 force 点击，也不应发起 rebuild（无 sleep，可观测断言）
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

    const docsCacheRaw = await page.evaluate(() =>
      localStorage.getItem("biaoshu.knowledgeBase.docs.v1"),
    );
    if (docsCacheRaw) {
      expect(docsCacheRaw).not.toMatch(/semanticIndex|semanticStatus|idx_should_not_persist/i);
    }

    // 既有文档本地入口仍可见
    await expect(
      page.getByRole("button", { name: /上传文档/ }),
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: "知识库" })).toBeVisible();
  });
});
