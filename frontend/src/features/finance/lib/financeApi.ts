/**
 * 模块：P10B/P10C 财务报价与成本草案 API 封装
 * 用途：仅调用财务专用端点；金额元→分用字符串拆分，禁止浮点乘法。
 * 对接：apiFetch；GET/POST/PATCH/DELETE /finance/business-bids*；Hook 与页面。
 * 二次开发：禁止回退到 /projects、/editor-state、/settings、/files 或外部地址。
 */

import { apiFetch } from "../../../shared/lib/api";
import type {
  FinanceBusinessBidDetail,
  FinanceBusinessBidListResponse,
  FinanceBusinessBidSummary,
  FinanceCostDraft,
  FinanceCostEntry,
  FinanceCostEntryCreateBody,
  FinanceCostEntryUpdateBody,
} from "../types";

/** 分金额合法闭区间（与后端一致）。 */
export const AMOUNT_FEN_MIN = 1;
export const AMOUNT_FEN_MAX = 999_999_999_999;

/**
 * 用途：加载当前工作空间商务标报价列表。
 * 对接：GET /finance/business-bids。
 */
export async function fetchFinanceBusinessBids(): Promise<
  FinanceBusinessBidSummary[]
> {
  const res = await apiFetch<FinanceBusinessBidListResponse>(
    "/finance/business-bids",
  );
  return Array.isArray(res?.items) ? res.items : [];
}

/**
 * 用途：加载单项目商务标报价明细。
 * 对接：GET /finance/business-bids/{projectId}。
 */
export async function fetchFinanceBusinessBidDetail(
  projectId: string,
): Promise<FinanceBusinessBidDetail> {
  const id = encodeURIComponent(projectId);
  return apiFetch<FinanceBusinessBidDetail>(`/finance/business-bids/${id}`);
}

/**
 * 用途：加载成本草案与毛利快照。
 * 对接：GET /finance/business-bids/{projectId}/cost-draft。
 */
export async function fetchFinanceCostDraft(
  projectId: string,
): Promise<FinanceCostDraft> {
  const id = encodeURIComponent(projectId);
  return apiFetch<FinanceCostDraft>(
    `/finance/business-bids/${id}/cost-draft`,
  );
}

/**
 * 用途：新建成本条目。
 * 对接：POST /finance/business-bids/{projectId}/cost-entries（CSRF 由 apiFetch）。
 */
export async function createFinanceCostEntry(
  projectId: string,
  body: FinanceCostEntryCreateBody,
): Promise<FinanceCostEntry> {
  const id = encodeURIComponent(projectId);
  return apiFetch<FinanceCostEntry>(
    `/finance/business-bids/${id}/cost-entries`,
    {
      method: "POST",
      body: JSON.stringify(body),
    },
  );
}

/**
 * 用途：更新成本条目。
 * 对接：PATCH /finance/business-bids/{projectId}/cost-entries/{entryId}。
 */
export async function updateFinanceCostEntry(
  projectId: string,
  entryId: string,
  body: FinanceCostEntryUpdateBody,
): Promise<FinanceCostEntry> {
  const pid = encodeURIComponent(projectId);
  const eid = encodeURIComponent(entryId);
  return apiFetch<FinanceCostEntry>(
    `/finance/business-bids/${pid}/cost-entries/${eid}`,
    {
      method: "PATCH",
      body: JSON.stringify(body),
    },
  );
}

/**
 * 用途：删除成本条目。
 * 对接：DELETE /finance/business-bids/{projectId}/cost-entries/{entryId}。
 */
export async function deleteFinanceCostEntry(
  projectId: string,
  entryId: string,
): Promise<void> {
  const pid = encodeURIComponent(projectId);
  const eid = encodeURIComponent(entryId);
  await apiFetch<void>(`/finance/business-bids/${pid}/cost-entries/${eid}`, {
    method: "DELETE",
  });
}

/**
 * 用途：将「元，最多两位小数」纯文本转为正整数分；非法时不发请求。
 * 对接：成本新建/编辑表单；禁止 Number 浮点乘法与 parseFloat。
 * @returns ok 时 fen 为 1..999999999999；否则固定中文错误。
 */
export function yuanTextToFen(
  input: string,
): { ok: true; fen: number } | { ok: false; error: string } {
  const raw = String(input ?? "").trim();
  if (!raw) {
    return { ok: false, error: "请输入金额" };
  }
  // 仅允许非负十进制，整数或最多两位小数；禁止科学计数、符号、千分位
  if (!/^(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(raw)) {
    return { ok: false, error: "金额须为正数，最多两位小数" };
  }
  const parts = raw.split(".");
  const yuanDigits = parts[0] ?? "0";
  const centDigits = (parts[1] ?? "").padEnd(2, "0").slice(0, 2);
  // 字符串拼接后按整数解析：元部分后补两位分
  const fenDigits = `${yuanDigits}${centDigits}`.replace(/^0+(?=\d)/, "") || "0";
  // 长度过长直接拒绝（避免超大整数中间态）
  if (fenDigits.length > 12) {
    return { ok: false, error: "金额超出允许范围" };
  }
  let fen = 0;
  for (let i = 0; i < fenDigits.length; i += 1) {
    fen = fen * 10 + (fenDigits.charCodeAt(i) - 48);
  }
  if (fen < AMOUNT_FEN_MIN) {
    return { ok: false, error: "金额须大于零" };
  }
  if (fen > AMOUNT_FEN_MAX) {
    return { ok: false, error: "金额超出允许范围" };
  }
  return { ok: true, fen };
}

/**
 * 用途：将整数分格式化为「¥1,234.56」；全程整数拆分，不输出 float 金额。
 * 对接：成本草案与毛利快照展示。
 */
export function formatFenAsYuan(fen: number): string {
  if (typeof fen !== "number" || !Number.isFinite(fen) || !Number.isInteger(fen)) {
    return "—";
  }
  const negative = fen < 0;
  let abs = fen < 0 ? -fen : fen;
  const cents = abs % 100;
  const yuan = (abs - cents) / 100;
  const yuanText = yuan.toLocaleString("zh-CN");
  const centText = String(cents).padStart(2, "0");
  return `${negative ? "-" : ""}¥${yuanText}.${centText}`;
}

/**
 * 用途：将毛利基点展示为百分比（例 3477→34.77%）；null 固定文案。
 * 对接：毛利快照；不得用报价/成本重新推算。
 */
export function formatMarginBasisPoints(
  bps: number | null | undefined,
): string {
  if (bps == null) {
    return "—（报价合计不大于零）";
  }
  if (typeof bps !== "number" || !Number.isFinite(bps) || !Number.isInteger(bps)) {
    return "—";
  }
  const negative = bps < 0;
  const abs = bps < 0 ? -bps : bps;
  const whole = Math.floor(abs / 100);
  const frac = abs % 100;
  return `${negative ? "-" : ""}${whole}.${String(frac).padStart(2, "0")}%`;
}
