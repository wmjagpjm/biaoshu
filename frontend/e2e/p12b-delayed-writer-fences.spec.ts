/**
 * 模块：P12B-C1 延迟写入围栏前端 E2E（商务 revise 队列）
 * 用途：证明 revise 进入 saveChainRef、执行时读最新 expected、
 *       成功后单次 refresh；409/缺版本阻断自动 PUT 且保留本地；
 *       冲突/缺版本路径零 pageerror/unhandledrejection。
 * 对接：useBusinessBidWorkspace；Playwright chromium headless workers=1。
 * 二次开发：禁止 .or(...)、waitForTimeout 作完成证据、宽泛状态码、toBeTruthy 版本断言。
 * 时钟：必须 page.clock.install + fastForward 跨过 600ms autosave 防抖，禁止 poll 假绿。
 */
import {
  expect,
  test,
  type Page,
  type Route,
} from "@playwright/test";

const REAL_BIZ = "proj_e2e_p12bc1_biz";
const REAL_MARKDOWN = "P12B_C1_SERVER_BIZ_MARKDOWN";
const FULL_STATE_CONFLICT_MSG =
  "编辑内容已被其他操作更新，已停止自动保存。重新载入远端内容将替换当前未保存修改。";
const STATE_VERSION_RE = /^esv_[0-9a-f]{32}$/;
/** 商务编辑 autosave 防抖为 600ms；时钟推进须严格超过该窗口 */
const AUTOSAVE_DEBOUNCE_MS = 600;
const AUTOSAVE_ADVANCE_MS = AUTOSAVE_DEBOUNCE_MS + 100;

type EditorState = {
  projectId: string;
  parsedMarkdown: string;
  businessQualify: unknown[];
  businessToc: unknown[];
  businessQuote: { rows: unknown[]; notes: string };
  businessCommit: unknown[];
  outline: unknown[];
  chapters: unknown[];
  mode: string;
  stateVersion: string;
};

type ProbeState = {
  editor: EditorState;
  getLog: string[];
  putLog: Array<{ body: Record<string, unknown>; responseVersion: string | null }>;
  reviseLog: Array<{
    body: Record<string, unknown>;
    responseVersion: string | null;
  }>;
  versionSeq: number;
  reviseMode:
    | { kind: "ok" }
    | { kind: "ok_no_version" }
    | { kind: "conflict" }
    | {
        kind: "gate";
        gate: {
          wait: () => Promise<void>;
          release: () => void;
        };
        then: "ok" | "conflict" | "ok_no_version";
      };
  putMode: { kind: "ok" } | { kind: "hold" };
  /** 首个 PUT 仍挂起时若 revise 到达则记 true（顺序 gate 违例） */
  reviseArrivedWhilePutHeld: boolean;
};

function isValidStateVersion(value: unknown): value is string {
  return typeof value === "string" && STATE_VERSION_RE.test(value);
}

function seedStateVersion(n: number): string {
  return `esv_${n.toString(16).padStart(32, "0")}`;
}

function allocateVersion(state: ProbeState): string {
  state.versionSeq += 1;
  return seedStateVersion(state.versionSeq);
}

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

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function realEditor(projectId: string, markdown: string, version: string): EditorState {
  return {
    projectId,
    parsedMarkdown: markdown,
    businessQualify: [],
    businessToc: [],
    businessQuote: { rows: [], notes: "" },
    businessCommit: [],
    outline: [],
    chapters: [],
    mode: "ALIGNED",
    stateVersion: version,
  };
}

/**
 * 用途：安装 pageerror / unhandledrejection 观测，冲突路径必须为零。
 * 必须 async 并 await addInitScript，避免 init 脚本安装竞态。
 */
async function installRuntimeErrorGuards(page: Page) {
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => {
    pageErrors.push(String(err?.message || err));
  });
  // 双证据：pageerror + window unhandledrejection（禁止仅依赖 console）
  await page.addInitScript(() => {
    window.addEventListener("unhandledrejection", (ev) => {
      const reason = (ev as PromiseRejectionEvent).reason;
      const text =
        reason instanceof Error
          ? reason.message
          : typeof reason === "string"
            ? reason
            : String(reason);
      (window as unknown as { __p12bc1Unhandled?: string[] }).__p12bc1Unhandled =
        (window as unknown as { __p12bc1Unhandled?: string[] }).__p12bc1Unhandled ||
        [];
      (window as unknown as { __p12bc1Unhandled: string[] }).__p12bc1Unhandled.push(
        text,
      );
    });
  });
  return {
    pageErrors,
    async readPageUnhandled(): Promise<string[]> {
      return page.evaluate(() => {
        return (
          (window as unknown as { __p12bc1Unhandled?: string[] }).__p12bc1Unhandled ||
          []
        );
      });
    },
  };
}

async function installRoutes(page: Page, state: ProbeState) {
  await page.route("**/*", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method().toUpperCase();

    // 仅放行本用例需要的精确路径
    const allowed =
      path === "/api/health" ||
      path === "/api/auth/bootstrap-status" ||
      path === "/api/auth/me" ||
      path === "/api/auth/csrf" ||
      path === "/api/workspace" ||
      path === "/api/settings" ||
      path === "/api/projects" ||
      path === `/api/projects/${REAL_BIZ}` ||
      path === `/api/projects/${REAL_BIZ}/editor-state` ||
      path === `/api/projects/${REAL_BIZ}/artifacts/workspace/revise` ||
      path.startsWith(`/api/projects/${REAL_BIZ}/tasks`) ||
      path.startsWith(`/api/projects/${REAL_BIZ}/files`);

    if (!path.startsWith("/api/")) {
      await route.continue();
      return;
    }
    if (!allowed) {
      await json(route, { detail: "forbidden" }, 404);
      return;
    }

    if (path === "/api/health" && method === "GET") {
      await json(route, { status: "ok", service: "biaoshu" });
      return;
    }
    if (path === "/api/auth/bootstrap-status" && method === "GET") {
      await json(route, { mode: "disabled", bootstrapped: true });
      return;
    }
    if (path === "/api/auth/me" && method === "GET") {
      await json(route, { id: "user_e2e", name: "e2e" });
      return;
    }
    if (path === "/api/auth/csrf" && method === "GET") {
      await json(route, { csrfToken: "e2e-csrf" });
      return;
    }
    if (path === "/api/workspace" && method === "GET") {
      await json(route, { id: "ws_e2e", name: "E2E" });
      return;
    }
    if (path === "/api/settings" && method === "GET") {
      await json(route, {});
      return;
    }
    if (path === "/api/projects" && method === "GET") {
      await json(route, [
        {
          id: REAL_BIZ,
          workspaceId: "ws_e2e",
          name: "P12B-C1商务",
          industry: "政务",
          status: "draft",
          updatedAt: "2026-07-15T00:00:00.000Z",
          technicalPlanStep: 1,
          wordCount: 0,
          kind: "business",
        },
      ]);
      return;
    }
    if (path === `/api/projects/${REAL_BIZ}` && method === "GET") {
      await json(route, {
        id: REAL_BIZ,
        workspaceId: "ws_e2e",
        name: "P12B-C1商务",
        industry: "政务",
        status: "draft",
        updatedAt: "2026-07-15T00:00:00.000Z",
        technicalPlanStep: 1,
        wordCount: 0,
        kind: "business",
      });
      return;
    }

    if (path === `/api/projects/${REAL_BIZ}/editor-state` && method === "GET") {
      state.getLog.push("get");
      await json(route, state.editor);
      return;
    }

    if (path === `/api/projects/${REAL_BIZ}/editor-state` && method === "PUT") {
      if (state.putMode.kind === "hold") {
        // 阻断期不应到达；若到达则记入日志
        const raw = req.postData() || "{}";
        const body = JSON.parse(raw) as Record<string, unknown>;
        state.putLog.push({ body, responseVersion: null });
        await json(
          route,
          {
            detail: {
              code: "editor_state_version_conflict",
              message: "blocked",
              currentStateVersion: state.editor.stateVersion,
            },
          },
          409,
        );
        return;
      }
      const raw = req.postData() || "{}";
      const body = JSON.parse(raw) as Record<string, unknown>;
      const nextVersion = allocateVersion(state);
      if (typeof body.parsedMarkdown === "string") {
        state.editor = {
          ...state.editor,
          parsedMarkdown: body.parsedMarkdown,
          stateVersion: nextVersion,
        };
      } else {
        state.editor = { ...state.editor, stateVersion: nextVersion };
      }
      state.putLog.push({ body, responseVersion: nextVersion });
      await json(route, state.editor);
      return;
    }

    if (
      path === `/api/projects/${REAL_BIZ}/artifacts/workspace/revise` &&
      method === "POST"
    ) {
      const raw = req.postData() || "{}";
      const body = JSON.parse(raw) as Record<string, unknown>;
      let mode = state.reviseMode;
      if (mode.kind === "gate") {
        await mode.gate.wait();
        mode = { kind: mode.then };
      }
      if (mode.kind === "conflict") {
        state.reviseLog.push({ body, responseVersion: null });
        await json(
          route,
          {
            detail: {
              code: "editor_state_version_conflict",
              message: "编辑内容已被其他操作更新，请重新载入后再保存",
              currentStateVersion: state.editor.stateVersion,
            },
          },
          409,
        );
        return;
      }
      if (mode.kind === "ok_no_version") {
        state.reviseLog.push({ body, responseVersion: null });
        await json(route, {
          id: "fb_e2e",
          stage: body.stage,
          message: body.message,
          status: "applied",
          resultSummary: "缺版本成功伪响应",
          revisedContent: "不应被采用",
        });
        return;
      }
      // ok
      const nextVersion = allocateVersion(state);
      state.editor = {
        ...state.editor,
        parsedMarkdown: `${REAL_MARKDOWN}\n修订后远端正文`,
        stateVersion: nextVersion,
      };
      state.reviseLog.push({ body, responseVersion: nextVersion });
      await json(route, {
        id: "fb_e2e",
        stage: body.stage,
        message: body.message,
        status: "applied",
        resultSummary: "修订完成",
        revisedContent: "修订后远端正文",
        stateVersion: nextVersion,
      });
      return;
    }

    if (path.startsWith(`/api/projects/${REAL_BIZ}/tasks`)) {
      await json(route, { id: "task_stub", status: "success", progress: 100 });
      return;
    }

    await json(route, { detail: "unhandled" }, 404);
  });
}

/** 用途：导航前安装确定性时钟，确保后续 setTimeout(600) 可被 fastForward。 */
async function installDeterministicClock(page: Page) {
  await page.clock.install();
}

async function openBusinessParse(page: Page) {
  await page.goto(`/business-bid/${REAL_BIZ}/parse`);
  await expect(page.getByTestId("business-editor-workspace")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByLabel("商务条款解析 Markdown")).toHaveValue(
    REAL_MARKDOWN,
  );
}

/**
 * 用途：触发编辑后的 600ms autosave 防抖，并等待 PUT 次数达到至少 minPuts。
 * 禁止 waitForTimeout / 伪 sleep；仅用 clock.fastForward 推进定时器。
 */
async function advanceAutosaveDebounce(page: Page) {
  await page.clock.fastForward(AUTOSAVE_ADVANCE_MS);
}

/**
 * 用途：阻断期将 putMode 切为 hold，再跨过至少两个 600ms 防抖窗口后精确断言 PUT 次数不变。
 * 禁止：waitForTimeout、evaluate(setTimeout)、sleep、立即成功的 poll 伪装时间证据。
 */
async function assertNoFurtherPuts(
  page: Page,
  state: ProbeState,
  expectedPuts: number,
) {
  state.putMode = { kind: "hold" };
  // 第一个防抖窗口：若错误实现仍调度 autosave，此处必写出 putLog
  await page.clock.fastForward(AUTOSAVE_ADVANCE_MS);
  expect(state.putLog.length).toBe(expectedPuts);
  // 第二个防抖窗口：防止单次 tick 侥幸
  await page.clock.fastForward(AUTOSAVE_ADVANCE_MS);
  expect(state.putLog.length).toBe(expectedPuts);
}

test.describe("P12B-C1 商务 revise 版本围栏", () => {
  test("revise 携带执行时 expected；成功后单次 refresh 且解除阻断", async ({
    page,
  }) => {
    const v0 = seedStateVersion(10);
    const state: ProbeState = {
      editor: realEditor(REAL_BIZ, REAL_MARKDOWN, v0),
      getLog: [],
      putLog: [],
      reviseLog: [],
      versionSeq: 10,
      reviseMode: { kind: "ok" },
      putMode: { kind: "ok" },
      reviseArrivedWhilePutHeld: false,
    };
    await installDeterministicClock(page);
    await installRoutes(page, state);
    await openBusinessParse(page);

    const getsBefore = state.getLog.length;
    await page
      .getByRole("region", { name: "商务标·条款解析 修改意见" })
      .getByRole("textbox")
      .fill("请强化条款表述");
    await page.getByRole("button", { name: "按意见修改" }).click();

    await expect
      .poll(() => state.reviseLog.length, { timeout: 8_000 })
      .toBe(1);
    expect(state.reviseLog[0].body.expectedStateVersion).toBe(v0);
    expect(isValidStateVersion(state.reviseLog[0].body.expectedStateVersion)).toBe(
      true,
    );

    // 成功后恰好多一次 GET（单次 refresh）
    await expect
      .poll(() => state.getLog.length, { timeout: 8_000 })
      .toBe(getsBefore + 1);
    await expect(page.getByLabel("商务条款解析 Markdown")).toHaveValue(
      `${REAL_MARKDOWN}\n修订后远端正文`,
    );
    await expect(
      page.getByTestId("business-editor-state-conflict"),
    ).toHaveCount(0);

    // 解除阻断后编辑可再 PUT，expected 为 revise 成功新版本
    const putsBefore = state.putLog.length;
    await page
      .getByLabel("商务条款解析 Markdown")
      .fill(`${REAL_MARKDOWN}\n修订后远端正文\n继续编辑`);
    await advanceAutosaveDebounce(page);
    await expect
      .poll(() => state.putLog.length, { timeout: 5_000 })
      .toBe(putsBefore + 1);
    expect(state.putLog[putsBefore].body.expectedStateVersion).toBe(
      state.reviseLog[0].responseVersion,
    );
    expect(
      isValidStateVersion(state.putLog[putsBefore].body.expectedStateVersion),
    ).toBe(true);
  });

  test("revise 409：保留本地、固定冲突 UI、零后续 PUT、零 unhandled", async ({
    page,
  }) => {
    const v0 = seedStateVersion(20);
    const state: ProbeState = {
      editor: realEditor(REAL_BIZ, REAL_MARKDOWN, v0),
      getLog: [],
      putLog: [],
      reviseLog: [],
      versionSeq: 20,
      reviseMode: { kind: "conflict" },
      putMode: { kind: "ok" },
      reviseArrivedWhilePutHeld: false,
    };
    await installDeterministicClock(page);
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await openBusinessParse(page);

    const localText = `${REAL_MARKDOWN}\n本地未保存-冲突场景`;
    await page.getByLabel("商务条款解析 Markdown").fill(localText);
    // 用 clock 跨过 autosave 防抖，禁止 poll 假绿
    await advanceAutosaveDebounce(page);
    await expect
      .poll(() => state.putLog.length, { timeout: 5_000 })
      .toBeGreaterThanOrEqual(1);
    const putsAtReviseStart = state.putLog.length;

    await page
      .getByRole("region", { name: "商务标·条款解析 修改意见" })
      .getByRole("textbox")
      .fill("触发冲突");
    await page.getByRole("button", { name: "按意见修改" }).click();

    await expect
      .poll(() => state.reviseLog.length, { timeout: 8_000 })
      .toBe(1);
    const expected = state.reviseLog[0].body.expectedStateVersion;
    expect(isValidStateVersion(expected)).toBe(true);
    // expected 须精确等于上一成功 PUT 响应版本，或初始 v0（若无 PUT 则 v0）
    const lastPutVersion =
      state.putLog.length > 0
        ? state.putLog[state.putLog.length - 1].responseVersion
        : v0;
    expect(expected).toBe(lastPutVersion);

    const conflictBanner = page.getByTestId("business-editor-state-conflict");
    await expect(conflictBanner).toBeVisible({ timeout: 5_000 });
    await expect(conflictBanner.getByText(FULL_STATE_CONFLICT_MSG)).toBeVisible();
    // 本地保留
    await expect(page.getByLabel("商务条款解析 Markdown")).toHaveValue(
      localText,
    );

    await page
      .getByLabel("商务条款解析 Markdown")
      .fill(`${localText}\n继续改也不应 PUT`);
    await assertNoFurtherPuts(page, state, putsAtReviseStart);

    const pageUnhandled = await guards.readPageUnhandled();
    expect(guards.pageErrors).toEqual([]);
    expect(pageUnhandled).toEqual([]);
  });

  test("revise 200 缺 stateVersion：阻断且零后续 PUT、零 unhandled", async ({
    page,
  }) => {
    const v0 = seedStateVersion(30);
    const state: ProbeState = {
      editor: realEditor(REAL_BIZ, REAL_MARKDOWN, v0),
      getLog: [],
      putLog: [],
      reviseLog: [],
      versionSeq: 30,
      reviseMode: { kind: "ok_no_version" },
      putMode: { kind: "ok" },
      reviseArrivedWhilePutHeld: false,
    };
    await installDeterministicClock(page);
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await openBusinessParse(page);

    const localText = `${REAL_MARKDOWN}\n本地-缺版本`;
    await page.getByLabel("商务条款解析 Markdown").fill(localText);
    await advanceAutosaveDebounce(page);
    await expect
      .poll(() => state.putLog.length, { timeout: 5_000 })
      .toBeGreaterThanOrEqual(1);
    const putsBefore = state.putLog.length;
    const lastPutVersion = state.putLog[putsBefore - 1].responseVersion;

    await page
      .getByRole("region", { name: "商务标·条款解析 修改意见" })
      .getByRole("textbox")
      .fill("缺版本响应");
    await page.getByRole("button", { name: "按意见修改" }).click();

    await expect
      .poll(() => state.reviseLog.length, { timeout: 8_000 })
      .toBe(1);
    expect(state.reviseLog[0].body.expectedStateVersion).toBe(lastPutVersion);
    expect(
      isValidStateVersion(state.reviseLog[0].body.expectedStateVersion),
    ).toBe(true);
    await expect(
      page.getByTestId("business-editor-state-conflict"),
    ).toBeVisible({ timeout: 5_000 });
    await expect(page.getByLabel("商务条款解析 Markdown")).toHaveValue(
      localText,
    );

    await page
      .getByLabel("商务条款解析 Markdown")
      .fill(`${localText}\n仍不 PUT`);
    await assertNoFurtherPuts(page, state, putsBefore);

    const pageUnhandled = await guards.readPageUnhandled();
    expect(guards.pageErrors).toEqual([]);
    expect(pageUnhandled).toEqual([]);
  });

  test("串行：revise 使用前一 PUT 成功响应版本作 expected", async ({
    page,
  }) => {
    const v0 = seedStateVersion(40);
    const gate = createHoldGate();
    const state: ProbeState = {
      editor: realEditor(REAL_BIZ, REAL_MARKDOWN, v0),
      getLog: [],
      putLog: [],
      reviseLog: [],
      versionSeq: 40,
      reviseMode: { kind: "ok" },
      putMode: { kind: "ok" },
      reviseArrivedWhilePutHeld: false,
    };

    await installDeterministicClock(page);

    // 在 install 内嵌 gate：首个 PUT 挂起，revise 必须排队
    let putHoldPending = true;
    await page.route("**/*", async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      const path = url.pathname;
      const method = req.method().toUpperCase();

      if (!path.startsWith("/api/")) {
        await route.continue();
        return;
      }

      if (path === "/api/health" && method === "GET") {
        await json(route, { status: "ok", service: "biaoshu" });
        return;
      }
      if (path === "/api/auth/bootstrap-status" && method === "GET") {
        await json(route, { mode: "disabled", bootstrapped: true });
        return;
      }
      if (path === "/api/auth/me" && method === "GET") {
        await json(route, { id: "user_e2e", name: "e2e" });
        return;
      }
      if (path === "/api/auth/csrf" && method === "GET") {
        await json(route, { csrfToken: "e2e-csrf" });
        return;
      }
      if (path === "/api/workspace" && method === "GET") {
        await json(route, { id: "ws_e2e", name: "E2E" });
        return;
      }
      if (path === "/api/settings" && method === "GET") {
        await json(route, {});
        return;
      }
      if (path === "/api/projects" && method === "GET") {
        await json(route, [
          {
            id: REAL_BIZ,
            workspaceId: "ws_e2e",
            name: "P12B-C1商务",
            industry: "政务",
            status: "draft",
            updatedAt: "2026-07-15T00:00:00.000Z",
            technicalPlanStep: 1,
            wordCount: 0,
            kind: "business",
          },
        ]);
        return;
      }
      if (path === `/api/projects/${REAL_BIZ}` && method === "GET") {
        await json(route, {
          id: REAL_BIZ,
          workspaceId: "ws_e2e",
          name: "P12B-C1商务",
          industry: "政务",
          status: "draft",
          updatedAt: "2026-07-15T00:00:00.000Z",
          technicalPlanStep: 1,
          wordCount: 0,
          kind: "business",
        });
        return;
      }
      if (path === `/api/projects/${REAL_BIZ}/editor-state` && method === "GET") {
        state.getLog.push("get");
        await json(route, state.editor);
        return;
      }
      if (path === `/api/projects/${REAL_BIZ}/editor-state` && method === "PUT") {
        if (putHoldPending) {
          putHoldPending = false;
          await gate.wait();
        }
        const raw = req.postData() || "{}";
        const body = JSON.parse(raw) as Record<string, unknown>;
        const nextVersion = allocateVersion(state);
        if (typeof body.parsedMarkdown === "string") {
          state.editor = {
            ...state.editor,
            parsedMarkdown: body.parsedMarkdown,
            stateVersion: nextVersion,
          };
        } else {
          state.editor = { ...state.editor, stateVersion: nextVersion };
        }
        state.putLog.push({ body, responseVersion: nextVersion });
        await json(route, state.editor);
        return;
      }
      if (
        path === `/api/projects/${REAL_BIZ}/artifacts/workspace/revise` &&
        method === "POST"
      ) {
        // 精确违例标志：首个 PUT 未释放时 revise 不得到达
        if (!gate.released) {
          state.reviseArrivedWhilePutHeld = true;
        }
        const raw = req.postData() || "{}";
        const body = JSON.parse(raw) as Record<string, unknown>;
        const nextVersion = allocateVersion(state);
        state.editor = {
          ...state.editor,
          parsedMarkdown: `${REAL_MARKDOWN}\n修订后远端正文`,
          stateVersion: nextVersion,
        };
        state.reviseLog.push({ body, responseVersion: nextVersion });
        await json(route, {
          id: "fb_e2e",
          stage: body.stage,
          message: body.message,
          status: "applied",
          resultSummary: "修订完成",
          revisedContent: "修订后远端正文",
          stateVersion: nextVersion,
        });
        return;
      }
      await json(route, { detail: "unhandled" }, 404);
    });

    await openBusinessParse(page);

    await page
      .getByLabel("商务条款解析 Markdown")
      .fill(`${REAL_MARKDOWN}\n第一波 PUT`);
    // 用 clock 触发 autosave，确认 PUT 已发出但仍挂起（putLog 尚未记录）
    await advanceAutosaveDebounce(page);
    await expect
      .poll(() => (putHoldPending ? 0 : 1), { timeout: 5_000 })
      .toBe(1);
    expect(state.putLog.length).toBe(0);

    // PUT 挂起时提交 revise，应排队在保存链后
    await page
      .getByRole("region", { name: "商务标·条款解析 修改意见" })
      .getByRole("textbox")
      .fill("排队修订");
    await page.getByRole("button", { name: "按意见修改" }).click();

    // 明确 UI 完成标志：history applying →「调整中」
    // 注意：页面 onRevise 以 void 调用 submitRevise，按钮「提交中…」不会稳定停留
    const feedbackRegion = page.getByRole("region", {
      name: "商务标·条款解析 修改意见",
    });
    await expect(feedbackRegion.getByText("调整中", { exact: true })).toBeVisible({
      timeout: 5_000,
    });

    // 在 release 前推进足够时钟：旁路实现若绕过 saveChain 会在此窗口打到 revise
    await page.clock.fastForward(AUTOSAVE_ADVANCE_MS * 2);
    expect(state.reviseArrivedWhilePutHeld).toBe(false);
    expect(state.reviseLog.length).toBe(0);

    gate.release();
    await expect
      .poll(() => state.putLog.length, { timeout: 8_000 })
      .toBe(1);
    await expect
      .poll(() => state.reviseLog.length, { timeout: 8_000 })
      .toBe(1);

    // 释放后标志仍须为 false（违例只记「未释放时到达」）
    expect(state.reviseArrivedWhilePutHeld).toBe(false);

    const putVersion = state.putLog[0].responseVersion;
    expect(isValidStateVersion(putVersion)).toBe(true);
    expect(state.reviseLog[0].body.expectedStateVersion).toBe(putVersion);
    expect(
      isValidStateVersion(state.reviseLog[0].body.expectedStateVersion),
    ).toBe(true);
  });
});
