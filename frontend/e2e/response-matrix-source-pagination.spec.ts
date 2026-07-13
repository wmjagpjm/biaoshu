/**
 * 模块：响应矩阵智能建议来源分页 E2E
 * 用途：验证 81+ 非 waived 来源时 response_match 按 80 分页串行覆盖第 2 页唯一来源，
 *       应用前不写 editor-state；UI 展示来源页进度并逐步累计建议。
 * 对接：Playwright chromium；后端 8010 / 前端 5174；本文件内 OpenAI-compatible mock LLM。
 * 二次开发：禁止 fixed sleep、真实 Key/外网；不测字段级合并/取消中断/409 交叉；勿改业务 src/backend。
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

const SOURCE_PAGE2 = "E2E分页来源第81条-唯一";
const CHAP_A = "chap_page_a";
const NODE_A = "node_page_a";

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

const KEY_PAGE2 = requirementSourceKey(SOURCE_PAGE2);

/**
 * 用途：本机 OpenAI 兼容 mock；根据 prompt 中实际出现的 sourceKey 返回建议。
 * 对接：PUT /api/settings apiBaseUrl；response_match → chat/completions。
 */
async function startMockLlmServer(): Promise<{
  baseUrl: string;
  close: () => Promise<void>;
  callCount: () => number;
}> {
  let calls = 0;
  const server = http.createServer((req, res) => {
    if (req.method === "POST" && (req.url || "").includes("chat/completions")) {
      const chunks: Buffer[] = [];
      req.on("data", (c) => chunks.push(c));
      req.on("end", () => {
        calls += 1;
        const raw = Buffer.concat(chunks).toString("utf8");
        // 解析 OpenAI messages，从本页 prompt 提取实际 sourceKey（证明第 2 页未被截断）
        let promptText = raw;
        try {
          const parsed = JSON.parse(raw) as {
            messages?: Array<{ content?: string }>;
          };
          promptText = (parsed.messages ?? [])
            .map((m) => m?.content ?? "")
            .join("\n");
        } catch {
          /* 保留原文兜底 */
        }
        const keys = new Set<string>();
        for (const m of promptText.matchAll(/sourceKey=([^；\n]+)/g)) {
          const key = (m[1] || "").trim();
          if (key) keys.add(key);
        }
        const suggestions = [...keys].map((sourceKey, i) => ({
          sourceKey,
          chapterIds: [CHAP_A],
          outlineNodeIds: [NODE_A],
          status: "covered",
          confidence: 70 + (i % 20),
          reason:
            sourceKey === KEY_PAGE2
              ? "E2E mock：第2页唯一来源映射"
              : `E2E mock：来源分页建议${i + 1}`,
        }));
        const body = JSON.stringify({
          id: "chatcmpl-e2e-matrix-source-page",
          object: "chat.completion",
          model: "e2e-mock-matrix-source",
          choices: [
            {
              index: 0,
              message: {
                role: "assistant",
                content: JSON.stringify(suggestions, null, 0),
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
    callCount: () => calls,
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

async function seedSourcePaginationProject(
  request: APIRequestContext,
  mockBase: string,
): Promise<string> {
  const settings = await request.put(`${API}/settings`, {
    data: {
      provider: "openai-compatible",
      apiBaseUrl: mockBase,
      apiKey: "e2e-local-mock",
      model: "e2e-mock-matrix-source",
    },
  });
  expect(settings.ok()).toBeTruthy();

  const created = await request.post(`${API}/projects`, {
    data: { name: "E2E 来源分页智能建议", kind: "technical" },
  });
  expect(created.ok()).toBeTruthy();
  const project = (await created.json()) as { id: string };

  // 80 条页1 + 1 条页2唯一来源；再加 1 条 waived 不计入分页
  const matrix: MatrixRow[] = [];
  for (let i = 0; i < 80; i += 1) {
    const text = `E2E分页来源${String(i).padStart(3, "0")}`;
    matrix.push({
      id: `mx_page_${i}`,
      kind: "requirement",
      sourceKey: requirementSourceKey(text),
      sourceIndex: i,
      sourceText: text,
      weight: "",
      chapterIds: [],
      outlineNodeIds: [],
      status: "uncovered",
      notes: `备注${i}`,
    });
  }
  matrix.push({
    id: "mx_page_80",
    kind: "requirement",
    sourceKey: KEY_PAGE2,
    sourceIndex: 80,
    sourceText: SOURCE_PAGE2,
    weight: "",
    chapterIds: [],
    outlineNodeIds: [],
    status: "uncovered",
    notes: "备注第81",
  });
  matrix.push({
    id: "mx_page_waived",
    kind: "requirement",
    sourceKey: requirementSourceKey("E2E分页已放弃"),
    sourceIndex: 81,
    sourceText: "E2E分页已放弃",
    weight: "",
    chapterIds: [],
    outlineNodeIds: [],
    status: "waived",
    notes: "不响应",
  });

  const techRequirements = matrix
    .filter((r) => r.status !== "waived")
    .map((r) => r.sourceText);

  const put = await request.put(`${API}/projects/${project.id}/editor-state`, {
    data: {
      outline: [{ id: NODE_A, title: "大纲分页", children: [] }],
      chapters: [{ id: CHAP_A, title: "章节分页" }],
      analysis: {
        overview: "E2E 来源分页概述",
        techRequirements,
        rejectionRisks: [],
        scoringPoints: [],
      },
      responseMatrix: matrix,
    },
  });
  expect(put.ok()).toBeTruthy();
  const body = (await put.json()) as EditorState;
  expect(body.responseMatrix?.length).toBe(82);
  return project.id;
}

async function openMatrixPage(page: Page, projectId: string) {
  await page.goto(`/technical-plan/${projectId}/analysis`);
  await expect(
    page.getByRole("region", { name: "响应矩阵" }).or(
      page.locator("section.response-matrix"),
    ),
  ).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(SOURCE_PAGE2)).toBeVisible({ timeout: 20_000 });
}

test.describe.configure({ mode: "serial" });

test("智能建议：81 条来源分页覆盖第 2 页唯一来源，应用前不写库", async ({
  page,
  request,
}) => {
  const mock = await startMockLlmServer();
  try {
    const projectId = await seedSourcePaginationProject(request, mock.baseUrl);
    const baseline = await getMatrixSnapshot(request, projectId);

    await openMatrixPage(page, projectId);

    await page.getByRole("button", { name: "智能建议" }).click();

    // 进度文案应出现来源页维度（串行 2 页）
    await expect
      .poll(
        async () => {
          const label = page
            .locator(".response-matrix__suggestion-actions")
            .getByText(/来源页/);
          return (await label.count()) > 0 ? "seen" : null;
        },
        { timeout: 45_000 },
      )
      .toBe("seen");

    // 完成后累计 81 条（waived 不计）；第 2 页唯一来源必须出现在待确认卡
    await expect(
      page
        .locator(".response-matrix__suggestion-actions")
        .getByText(/已累计 81 条待确认/),
    ).toBeVisible({ timeout: 60_000 });
    await expect(
      page.getByRole("button", { name: /应用已选建议（81）/ }),
    ).toBeVisible();

    const rowPage2 = matrixItem(page, SOURCE_PAGE2);
    await expect(rowPage2.locator(".response-matrix__suggestion")).toBeVisible();
    await expect(rowPage2.getByText("E2E mock：第2页唯一来源映射")).toBeVisible();
    await expect(rowPage2.getByText(/正文：章节分页/)).toBeVisible();

    // 至少完成 2 次模型调用（2 个来源页 × 1 候选批）
    expect(mock.callCount()).toBeGreaterThanOrEqual(2);

    // 应用前 editor-state 不变
    const afterSuggest = await getMatrixSnapshot(request, projectId);
    expect(afterSuggest).toEqual(baseline);
    await expect(rowPage2.getByLabel("响应状态")).toHaveValue("uncovered");
  } finally {
    await mock.close();
  }
});
