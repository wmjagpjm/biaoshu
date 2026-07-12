/**
 * 模块：中标内容模板沉淀与复用 E2E
 * 用途：真实 UI 验证「沉淀为模板 → 模板库 → 从模板新建 → 新项目大纲/章节独立副本」。
 * 对接：Playwright chromium；后端 8010 / 前端 5174；/api/templates；技术标工作区。
 * 二次开发：禁止 sleep、真实 Key/LLM、route stub；勿改 conflict/refresh 矩阵 spec。
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API = "http://127.0.0.1:8010/api";

const OUTLINE_TITLE = "E2E总体架构";
const CHAPTER_BODY = "E2E分层微服务架构正文，用于验证模板深拷贝。";

async function seedProjectWithContent(
  request: APIRequestContext,
): Promise<{ projectId: string }> {
  const created = await request.post(`${API}/projects`, {
    data: { name: "E2E 中标模板源项目", kind: "technical", industry: "政务" },
  });
  expect(created.ok()).toBeTruthy();
  const project = (await created.json()) as { id: string };

  const put = await request.put(`${API}/projects/${project.id}/editor-state`, {
    data: {
      outline: [
        {
          id: "node_e2e_arch",
          title: OUTLINE_TITLE,
          children: [{ id: "node_e2e_sub", title: "E2E分层", children: [] }],
        },
      ],
      chapters: [
        {
          id: "chap_e2e_arch",
          title: OUTLINE_TITLE,
          body: CHAPTER_BODY,
        },
      ],
      mode: "ALIGNED",
    },
  });
  expect(put.ok()).toBeTruthy();
  return { projectId: project.id };
}

async function openWorkspace(page: Page, projectId: string) {
  await page.goto(`/technical-plan/${projectId}/outline`);
  await expect(
    page.getByRole("heading", { name: "E2E 中标模板源项目" }),
  ).toBeVisible({ timeout: 20_000 });
  await expect(
    page.getByRole("button", { name: "沉淀为中标内容模板" }),
  ).toBeVisible();
}

test.describe("中标内容模板复用", () => {
  test("沉淀为模板后可从模板新建并保留大纲章节副本", async ({
    page,
    request,
  }) => {
    const { projectId } = await seedProjectWithContent(request);
    const templateTitle = `E2E中标模板-${Date.now()}`;

    await openWorkspace(page, projectId);

    await page.getByRole("button", { name: "沉淀为中标内容模板" }).click();
    const dialog = page.getByRole("dialog", { name: "沉淀为中标内容模板" });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("模板名称").fill(templateTitle);
    await dialog.getByLabel("模板标签").fill("E2E，政务");
    await dialog.getByRole("button", { name: "确认沉淀为模板" }).click();
    await expect(dialog).toBeHidden({ timeout: 15_000 });
    await expect(page.getByText(/已沉淀模板/)).toBeVisible();

    await page.goto("/bid-templates");
    await expect(
      page.getByRole("heading", { name: "中标内容模板" }),
    ).toBeVisible({ timeout: 15_000 });
    const card = page.getByRole("listitem", {
      name: `模板 ${templateTitle}`,
    });
    await expect(card).toBeVisible();
    await expect(card.locator(".bid-tpl-tag", { hasText: "E2E" })).toBeVisible();

    await card.getByRole("button", { name: `从模板新建 ${templateTitle}` }).click();

    await expect(page).toHaveURL(/\/technical-plan\/proj_[^/]+\/outline/, {
      timeout: 20_000,
    });
    const newUrl = page.url();
    const match = newUrl.match(/\/technical-plan\/(proj_[^/]+)/);
    expect(match?.[1]).toBeTruthy();
    const newProjectId = match![1];
    expect(newProjectId).not.toBe(projectId);

    // 新项目 editor-state 含独立副本
    const stateRes = await request.get(
      `${API}/projects/${newProjectId}/editor-state`,
    );
    expect(stateRes.ok()).toBeTruthy();
    const state = (await stateRes.json()) as {
      outline?: Array<{ title?: string }>;
      chapters?: Array<{ body?: string }>;
    };
    expect(state.outline?.[0]?.title).toBe(OUTLINE_TITLE);
    expect(state.chapters?.[0]?.body).toBe(CHAPTER_BODY);

    // 修改新项目不回写模板
    const putNew = await request.put(
      `${API}/projects/${newProjectId}/editor-state`,
      {
        data: {
          outline: [{ id: "n_changed", title: "已改大纲", children: [] }],
          chapters: [{ id: "c_changed", title: "已改", body: "已改正文" }],
        },
      },
    );
    expect(putNew.ok()).toBeTruthy();

    // 列表仅摘要：无完整 snapshot，但有章节数/大纲标题
    const listRes = await request.get(
      `${API}/templates?q=${encodeURIComponent(templateTitle)}`,
    );
    expect(listRes.ok()).toBeTruthy();
    const templates = (await listRes.json()) as Array<{
      id: string;
      title: string;
      chapterCount?: number;
      outlineTitles?: string[];
      snapshot?: unknown;
    }>;
    const tpl = templates.find((item) => item.title === templateTitle);
    expect(tpl).toBeTruthy();
    expect(tpl!.snapshot).toBeUndefined();
    expect(tpl!.chapterCount).toBe(1);
    expect(tpl!.outlineTitles?.[0]).toBe(OUTLINE_TITLE);

    // 详情可取完整快照，且未被新项目修改污染
    const detailRes = await request.get(`${API}/templates/${tpl!.id}`);
    expect(detailRes.ok()).toBeTruthy();
    const detail = (await detailRes.json()) as {
      snapshot: {
        outline?: Array<{ title?: string }>;
        chapters?: Array<{ body?: string }>;
      };
    };
    expect(detail.snapshot.outline?.[0]?.title).toBe(OUTLINE_TITLE);
    expect(detail.snapshot.chapters?.[0]?.body).toBe(CHAPTER_BODY);
  });
});
