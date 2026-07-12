/**
 * 模块：模板/卡片融合建议 M3-A E2E
 * 用途：真实 UI 选择来源与目标章 → 获得只读建议；关闭后刷新章节正文不变。
 * 对接：Playwright chromium；后端 8010 / 前端 5174；content_fuse 任务。
 * 二次开发：禁止真实云 Key；本文件内起本地 mock chat completions；勿改既有 E2E spec。
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
const CHAPTER_TITLE = "E2E融合章节";
const CHAPTER_BODY = "E2E初始章节正文，融合后仍应保持。";
const PROPOSED = "E2E只读融合建议正文，不得写入章节。";

async function startMockLlmServer(): Promise<{
  baseUrl: string;
  close: () => Promise<void>;
}> {
  const server = http.createServer((req, res) => {
    if (req.method === "POST" && (req.url || "").includes("chat/completions")) {
      const chunks: Buffer[] = [];
      req.on("data", (c) => chunks.push(c));
      req.on("end", () => {
        // 从 prompt 中提取实际出现的模板/卡片 id，保证非空 sourceRefs（M3-A 无来源建议会被整条丢弃）
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
        const body = JSON.stringify({
          id: "chatcmpl-e2e-fuse",
          object: "chat.completion",
          model: "e2e-mock-fuse",
          choices: [
            {
              index: 0,
              message: {
                role: "assistant",
                content: JSON.stringify(
                  [
                    {
                      targetChapterId: "chap_e2e_fuse",
                      action: "merge_suggest",
                      confidence: 86,
                      reason: "E2E mock 融合理由",
                      sourceRefs,
                      proposedMarkdown: PROPOSED,
                      diffSummary: "E2E差异摘要",
                    },
                  ],
                  null,
                  0,
                ),
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

async function seedFuseFixtures(request: APIRequestContext, mockBase: string) {
  const settings = await request.put(`${API}/settings`, {
    data: {
      provider: "openai-compatible",
      apiBaseUrl: mockBase,
      apiKey: "e2e-local-mock",
      model: "e2e-mock-fuse",
    },
  });
  expect(settings.ok()).toBeTruthy();

  const source = await request.post(`${API}/projects`, {
    data: { name: "E2E 融合模板源", kind: "technical", industry: "政务" },
  });
  expect(source.ok()).toBeTruthy();
  const sourceProject = (await source.json()) as { id: string };
  const seedState = await request.put(
    `${API}/projects/${sourceProject.id}/editor-state`,
    {
      data: {
        outline: [
          { id: "node_src", title: CHAPTER_TITLE, children: [] },
        ],
        chapters: [
          {
            id: "chap_src",
            title: CHAPTER_TITLE,
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
      title: `E2E融合模板-${Date.now()}`,
      tags: ["E2E", "融合"],
    },
  });
  expect(tpl.ok()).toBeTruthy();
  const template = (await tpl.json()) as { id: string; title: string };

  const card = await request.post(`${API}/cards`, {
    data: {
      type: "document",
      title: `E2E融合卡片-${Date.now()}`,
      bodyMarkdown: "E2E 卡片参考段落，用于融合上下文。",
      tags: ["E2E"],
      sourceLabel: "E2E",
    },
  });
  expect(card.ok()).toBeTruthy();
  const cardBody = (await card.json()) as { id: string; title: string };

  const target = await request.post(`${API}/projects`, {
    data: { name: "E2E 融合目标项目", kind: "technical", industry: "政务" },
  });
  expect(target.ok()).toBeTruthy();
  const project = (await target.json()) as { id: string };
  const put = await request.put(`${API}/projects/${project.id}/editor-state`, {
    data: {
      outline: [{ id: "node_e2e_fuse", title: CHAPTER_TITLE, children: [] }],
      chapters: [
        {
          id: "chap_e2e_fuse",
          title: CHAPTER_TITLE,
          body: CHAPTER_BODY,
        },
      ],
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
    page.getByRole("heading", { name: "E2E 融合目标项目" }),
  ).toBeVisible({ timeout: 20_000 });
  await expect(page.getByLabel(`正文：${CHAPTER_TITLE}`)).toBeVisible({
    timeout: 15_000,
  });
}

test.describe("模板卡片融合建议 M3-A", () => {
  test("选择来源与目标章后获得只读建议，关闭刷新正文不变", async ({
    page,
    request,
  }) => {
    const mock = await startMockLlmServer();
    try {
      const { projectId, templateTitle, cardTitle } = await seedFuseFixtures(
        request,
        mock.baseUrl,
      );

      await openContentStep(page, projectId);
      const body = page.getByLabel(`正文：${CHAPTER_TITLE}`);
      await expect(body).toHaveValue(CHAPTER_BODY);

      await page.getByRole("button", { name: "模板卡片融合建议" }).click();
      const dialog = page.getByRole("dialog", { name: "模板卡片融合建议" });
      await expect(dialog).toBeVisible();

      await dialog.getByLabel(`模板 ${templateTitle}`).check();
      await dialog.getByLabel(`卡片 ${cardTitle}`).check();
      await dialog.getByLabel(`目标章节 ${CHAPTER_TITLE}`).check();

      await dialog.getByRole("button", { name: "生成只读融合建议" }).click();
      await expect(
        dialog.getByText(/已生成 \d+ 条只读建议/),
      ).toBeVisible({ timeout: 30_000 });
      await expect(dialog.getByText(PROPOSED)).toBeVisible();
      await expect(dialog.getByText("E2E mock 融合理由")).toBeVisible();
      // 来源芯片优先展示服务端 title（非模型伪造、非纯 kind:id）
      await expect(
        dialog.getByText(new RegExp(`来源：.*${templateTitle}.*${cardTitle}`)),
      ).toBeVisible();
      await expect(dialog.getByText("模型伪造标题-模板")).toHaveCount(0);
      await expect(dialog.getByText("模型伪造标题-卡片")).toHaveCount(0);

      // M3-A 不得出现写入动作
      await expect(
        dialog.getByRole("button", { name: /应用|保存到章节|复制到章节/ }),
      ).toHaveCount(0);

      await dialog.getByRole("button", { name: "关闭", exact: true }).click();
      await expect(dialog).toBeHidden();

      // 关闭后章节正文仍为初始值
      await expect(body).toHaveValue(CHAPTER_BODY);

      // 刷新后 editor-state 仍未写入建议
      await page.reload();
      await expect(
        page.getByRole("heading", { name: "E2E 融合目标项目" }),
      ).toBeVisible({ timeout: 20_000 });
      await expect(page.getByLabel(`正文：${CHAPTER_TITLE}`)).toHaveValue(
        CHAPTER_BODY,
        { timeout: 15_000 },
      );
      await expect(page.getByLabel(`正文：${CHAPTER_TITLE}`)).not.toHaveValue(
        PROPOSED,
      );
    } finally {
      await mock.close();
    }
  });
});
