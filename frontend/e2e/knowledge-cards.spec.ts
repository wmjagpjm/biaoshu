/**
 * 模块：卡片化知识与素材库 E2E
 * 用途：真实 UI 验证「新建文本卡 → 章节插入 → 刷新后正文保持」；图片卡后端预览。
 * 对接：Playwright chromium；后端 8010 / 前端 5174；/api/cards；章节编辑器。
 * 二次开发：禁止 sleep、真实 Key/LLM、route stub；勿改 conflict/refresh/templates spec。
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API = "http://127.0.0.1:8010/api";

const CHAPTER_TITLE = "E2E卡片章节";
const CARD_TITLE = `E2E架构卡片-${Date.now()}`;
const CARD_BODY = "E2E分层微服务架构正文，用于验证卡片插入与刷新保持。";
const SOURCE_LABEL = "E2E手工来源";

async function seedProjectWithChapter(
  request: APIRequestContext,
): Promise<{ projectId: string }> {
  const created = await request.post(`${API}/projects`, {
    data: { name: "E2E 卡片插入项目", kind: "technical", industry: "政务" },
  });
  expect(created.ok()).toBeTruthy();
  const project = (await created.json()) as { id: string };

  const put = await request.put(`${API}/projects/${project.id}/editor-state`, {
    data: {
      outline: [
        {
          id: "node_e2e_card",
          title: CHAPTER_TITLE,
          children: [],
        },
      ],
      chapters: [
        {
          id: "chap_e2e_card",
          title: CHAPTER_TITLE,
          body: "初始章节正文。\n",
        },
      ],
      mode: "ALIGNED",
    },
  });
  expect(put.ok()).toBeTruthy();
  return { projectId: project.id };
}

async function openContentStep(page: Page, projectId: string) {
  await page.goto(`/technical-plan/${projectId}/content`);
  await expect(
    page.getByRole("heading", { name: "E2E 卡片插入项目" }),
  ).toBeVisible({ timeout: 20_000 });
  await expect(page.getByLabel(`正文：${CHAPTER_TITLE}`)).toBeVisible({
    timeout: 15_000,
  });
}

test.describe("知识卡片库", () => {
  test("新建文本卡后可插入章节并在刷新后保持", async ({ page, request }) => {
    const { projectId } = await seedProjectWithChapter(request);

    // 知识库新建文本卡
    await page.goto("/knowledge-base");
    await expect(page.getByRole("heading", { name: "知识库" })).toBeVisible({
      timeout: 15_000,
    });
    await page.getByRole("button", { name: "素材卡片" }).click();
    await page.getByRole("button", { name: "新建文本卡片" }).click();
    const dialog = page.getByRole("dialog", { name: "新建文本卡片" });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("卡片标题").fill(CARD_TITLE);
    await dialog.getByLabel("卡片正文").fill(CARD_BODY);
    await dialog.getByLabel("卡片标签").fill("E2E,架构");
    await dialog.getByLabel("卡片来源").fill(SOURCE_LABEL);
    await dialog.getByRole("button", { name: "创建卡片" }).click();
    await expect(dialog).toBeHidden({ timeout: 15_000 });
    await expect(
      page.getByRole("listitem", { name: `卡片 ${CARD_TITLE}` }),
    ).toBeVisible({ timeout: 15_000 });

    // 章节插入卡片
    await openContentStep(page, projectId);
    await page.getByRole("button", { name: "插入知识卡片" }).click();
    const insertDialog = page.getByRole("dialog", { name: "插入知识卡片" });
    await expect(insertDialog).toBeVisible();
    await insertDialog.getByLabel("检索卡片").fill(CARD_TITLE);
    const cardRow = insertDialog.getByRole("listitem", {
      name: `卡片 ${CARD_TITLE}`,
    });
    await expect(cardRow).toBeVisible({ timeout: 15_000 });
    await cardRow.getByRole("button", { name: `插入卡片 ${CARD_TITLE}` }).click();
    await expect(insertDialog).toBeHidden({ timeout: 15_000 });

    const body = page.getByLabel(`正文：${CHAPTER_TITLE}`);
    await expect(body).toContainText(CARD_TITLE, { timeout: 10_000 });
    await expect(body).toContainText(SOURCE_LABEL);
    await expect(body).toContainText(CARD_BODY);

    // 刷新后 editor-state 保持（自动保存后轮询 GET）
    await expect
      .poll(
        async () => {
          const res = await request.get(
            `${API}/projects/${projectId}/editor-state`,
          );
          if (!res.ok()) return "";
          const state = (await res.json()) as {
            chapters?: Array<{ body?: string }>;
          };
          return state.chapters?.[0]?.body || "";
        },
        { timeout: 20_000 },
      )
      .toContain(CARD_BODY);

    await page.reload();
    await expect(page.getByLabel(`正文：${CHAPTER_TITLE}`)).toContainText(
      CARD_BODY,
      { timeout: 20_000 },
    );
    await expect(page.getByLabel(`正文：${CHAPTER_TITLE}`)).toContainText(
      SOURCE_LABEL,
    );
  });
});
