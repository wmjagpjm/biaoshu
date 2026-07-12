/**
 * 模块：模板/卡片融合建议（M3-A/M3-B）类型、规范化与基线校验
 * 用途：解析 content_fuse 任务 result；M3-B 用纯同步 SHA-1 校验 base 并构造确认写入正文。
 * 对接：ContentFuseDialog；useProjectPipeline type=content_fuse；editors.replaceChapterBody。
 * 二次开发：禁止新增后端 API；哈希失败不得放行；写入仅经 Dialog 确认后逐条 replaceChapterBody。
 */

export type ContentFuseSourceRef = {
  kind: "template" | "card";
  id: string;
  /** 服务端从实际入 prompt 的模板/卡片补齐；展示优先用 title */
  title: string;
};

export type ContentFuseChapterBase = {
  bodyHash: string;
  bodyLength: number;
  title: string;
};

export type ContentFuseSuggestion = {
  suggestionId: string;
  targetChapterId: string;
  targetTitle: string;
  action: string;
  confidence: number;
  reason: string;
  sourceRefs: ContentFuseSourceRef[];
  base: ContentFuseChapterBase;
  currentPreview: string;
  proposedMarkdown: string;
  diffSummary: string;
};

export type ContentFuseSkippedSource = {
  kind: string;
  id: string;
  reason: string;
};

export type ContentFuseQuota = {
  templatesSelected: number;
  cardsSelected: number;
  targetsSelected: number;
  templatesUsed?: number;
  cardsUsed?: number;
  promptChars?: number;
  maxPromptChars?: number;
};

export type ContentFuseResult = {
  suggestions: ContentFuseSuggestion[];
  model: string;
  skippedSources: ContentFuseSkippedSource[];
  skippedInvalidCount: number;
  baseEditorUpdatedAt: string | null;
  quota: ContentFuseQuota;
  mode: string;
};

export type ContentFusePayload = {
  templateIds: string[];
  cardIds: string[];
  targetChapterIds: string[];
  mode: "merge_suggest";
};

/** 配额常量（与后端 fuse_context_service 对齐） */
export const CONTENT_FUSE_LIMITS = {
  maxTemplates: 3,
  maxCards: 8,
  maxSourcesTotal: 10,
  maxTargets: 5,
} as const;

/** base 哈希前缀与长度（对齐 Python sha1(...).hexdigest()[:20]） */
const BODY_HASH_PREFIX = "bh_";
const BODY_HASH_HEX_LEN = 20;
const BODY_HASH_RE = /^bh_[0-9a-f]{20}$/i;

function asString(value: unknown): string {
  return typeof value === "string" ? value : String(value ?? "");
}

function asNumber(value: unknown, fallback = 0): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

/**
 * 用途：纯同步 SHA-1，输出小写 hex；禁止依赖 Web Crypto（异步）。
 * 对接：computeChapterBase bodyHash。
 */
function sha1HexSync(bytes: Uint8Array): string {
  // 标准 SHA-1（FIPS 180-1）纯实现，供 M3-B 与 Python hashlib.sha1 对齐
  const K = [
    0x5a827999, 0x6ed9eba1, 0x8f1bbcdc, 0xca62c1d6,
  ] as const;

  const ml = bytes.length;
  const bitLenHi = Math.floor(ml / 0x20000000);
  const bitLenLo = (ml << 3) >>> 0;
  const totalLen = ((ml + 9 + 63) & ~63) >>> 0;
  const buf = new Uint8Array(totalLen);
  buf.set(bytes);
  buf[ml] = 0x80;
  const view = new DataView(buf.buffer);
  view.setUint32(totalLen - 8, bitLenHi, false);
  view.setUint32(totalLen - 4, bitLenLo, false);

  let h0 = 0x67452301;
  let h1 = 0xefcdab89;
  let h2 = 0x98badcfe;
  let h3 = 0x10325476;
  let h4 = 0xc3d2e1f0;

  const w = new Int32Array(80);
  for (let i = 0; i < totalLen; i += 64) {
    for (let j = 0; j < 16; j++) {
      w[j] = view.getInt32(i + j * 4, false);
    }
    for (let j = 16; j < 80; j++) {
      const x = w[j - 3] ^ w[j - 8] ^ w[j - 14] ^ w[j - 16];
      w[j] = (x << 1) | (x >>> 31);
    }

    let a = h0;
    let b = h1;
    let c = h2;
    let d = h3;
    let e = h4;

    for (let j = 0; j < 80; j++) {
      let f: number;
      let k: number;
      if (j < 20) {
        f = (b & c) | (~b & d);
        k = K[0];
      } else if (j < 40) {
        f = b ^ c ^ d;
        k = K[1];
      } else if (j < 60) {
        f = (b & c) | (b & d) | (c & d);
        k = K[2];
      } else {
        f = b ^ c ^ d;
        k = K[3];
      }
      const temp = (((a << 5) | (a >>> 27)) + f + e + k + w[j]) | 0;
      e = d;
      d = c;
      c = ((b << 30) | (b >>> 2)) | 0;
      b = a;
      a = temp;
    }

    h0 = (h0 + a) | 0;
    h1 = (h1 + b) | 0;
    h2 = (h2 + c) | 0;
    h3 = (h3 + d) | 0;
    h4 = (h4 + e) | 0;
  }

  const out = new Uint8Array(20);
  const outView = new DataView(out.buffer);
  outView.setUint32(0, h0 >>> 0, false);
  outView.setUint32(4, h1 >>> 0, false);
  outView.setUint32(8, h2 >>> 0, false);
  outView.setUint32(12, h3 >>> 0, false);
  outView.setUint32(16, h4 >>> 0, false);
  let hex = "";
  for (let i = 0; i < out.length; i++) {
    hex += out[i]!.toString(16).padStart(2, "0");
  }
  return hex;
}

/**
 * 用途：与后端 compute_chapter_base 对齐的章节基线。
 * 规则：UTF-8 SHA-1 前 20 hex + bh_ 前缀；bodyLength=Array.from(body).length；title=trim。
 * 异常：哈希过程抛错时向上抛出，调用方不得放行。
 */
export function computeChapterBase(
  title: string,
  body: string,
): ContentFuseChapterBase {
  const safeTitle = (title || "").trim();
  const safeBody = body ?? "";
  const bytes = new TextEncoder().encode(safeBody);
  const digest = sha1HexSync(bytes).slice(0, BODY_HASH_HEX_LEN);
  if (digest.length !== BODY_HASH_HEX_LEN) {
    throw new Error("章节基线哈希计算失败");
  }
  return {
    bodyHash: `${BODY_HASH_PREFIX}${digest}`,
    bodyLength: Array.from(safeBody).length,
    title: safeTitle,
  };
}

export type FuseBaseMatchOk = { ok: true };
export type FuseBaseMatchFail = { ok: false; reason: string };
export type FuseBaseMatchResult = FuseBaseMatchOk | FuseBaseMatchFail;

/**
 * 用途：M3-B 实时 base 全匹配；仅 id 存在且 bodyHash/bodyLength/title 全一致才可勾选/写入。
 * 对接：ContentFuseDialog 勾选禁用与确认写入再校验。
 */
export function matchFuseSuggestionBase(
  chapter:
    | { id: string; title: string; body?: string }
    | null
    | undefined,
  base: ContentFuseChapterBase,
): FuseBaseMatchResult {
  if (!chapter) {
    return { ok: false, reason: "目标章节已删除或不存在" };
  }
  const baseHash = asString(base?.bodyHash).trim();
  if (!baseHash || !BODY_HASH_RE.test(baseHash)) {
    return { ok: false, reason: "基线哈希无效或缺失" };
  }
  let live: ContentFuseChapterBase;
  try {
    live = computeChapterBase(chapter.title || "", chapter.body || "");
  } catch {
    // 哈希失败不得放行
    return { ok: false, reason: "基线哈希计算失败" };
  }
  if (live.title !== asString(base.title).trim()) {
    return { ok: false, reason: "标题已变更，基线不匹配" };
  }
  if (
    live.bodyLength !== asNumber(base.bodyLength) ||
    live.bodyHash !== baseHash
  ) {
    return { ok: false, reason: "正文已变更，基线不匹配" };
  }
  return { ok: true };
}

/**
 * 用途：按 action 构造确认写入后的章节正文；空 proposedMarkdown 永远返回 null。
 * 规则：expand 追加（非空旧正文用双换行）；其余规范 action 替换。
 */
export function buildAppliedChapterBody(
  action: string,
  currentBody: string,
  proposedMarkdown: string,
): string | null {
  const proposed = proposedMarkdown ?? "";
  if (!proposed) return null;
  if (action === "expand") {
    return currentBody ? `${currentBody}\n\n${proposed}` : proposed;
  }
  return proposed;
}

/**
 * 用途：把任务 result 收敛为前端结构；非法字段丢弃。
 * 对接：ContentFuseDialog 成功态展示。
 */
export function normalizeContentFuseResult(
  raw: Record<string, unknown> | null | undefined,
): ContentFuseResult | null {
  if (!raw || typeof raw !== "object") return null;
  const suggestionsRaw = Array.isArray(raw.suggestions) ? raw.suggestions : [];
  const suggestions: ContentFuseSuggestion[] = [];
  for (const item of suggestionsRaw) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    const baseRaw =
      row.base && typeof row.base === "object"
        ? (row.base as Record<string, unknown>)
        : {};
    const refsRaw = Array.isArray(row.sourceRefs) ? row.sourceRefs : [];
    const sourceRefs: ContentFuseSourceRef[] = [];
    for (const ref of refsRaw) {
      if (!ref || typeof ref !== "object") continue;
      const r = ref as Record<string, unknown>;
      const kind = asString(r.kind);
      const id = asString(r.id).trim();
      if ((kind === "template" || kind === "card") && id) {
        // 保留服务端 title；缺失时退回空串，UI 再回退到 kind:id
        sourceRefs.push({
          kind,
          id,
          title: asString(r.title).trim(),
        });
      }
    }
    suggestions.push({
      suggestionId: asString(row.suggestionId) || `local_${suggestions.length}`,
      targetChapterId: asString(row.targetChapterId),
      targetTitle: asString(row.targetTitle),
      action: asString(row.action) || "merge_suggest",
      confidence: Math.max(0, Math.min(100, Math.round(asNumber(row.confidence)))),
      reason: asString(row.reason).slice(0, 60),
      sourceRefs,
      base: {
        bodyHash: asString(baseRaw.bodyHash),
        bodyLength: asNumber(baseRaw.bodyLength),
        title: asString(baseRaw.title),
      },
      currentPreview: asString(row.currentPreview).slice(0, 400),
      proposedMarkdown: asString(row.proposedMarkdown).slice(0, 12_000),
      diffSummary: asString(row.diffSummary).slice(0, 200),
    });
  }

  const skippedRaw = Array.isArray(raw.skippedSources) ? raw.skippedSources : [];
  const skippedSources: ContentFuseSkippedSource[] = skippedRaw
    .filter((s): s is Record<string, unknown> => !!s && typeof s === "object")
    .map((s) => ({
      kind: asString(s.kind),
      id: asString(s.id),
      reason: asString(s.reason),
    }));

  const quotaRaw =
    raw.quota && typeof raw.quota === "object"
      ? (raw.quota as Record<string, unknown>)
      : {};

  return {
    suggestions,
    model: asString(raw.model),
    skippedSources,
    skippedInvalidCount: Math.max(0, Math.round(asNumber(raw.skippedInvalidCount))),
    baseEditorUpdatedAt:
      raw.baseEditorUpdatedAt == null ? null : asString(raw.baseEditorUpdatedAt),
    quota: {
      templatesSelected: asNumber(quotaRaw.templatesSelected),
      cardsSelected: asNumber(quotaRaw.cardsSelected),
      targetsSelected: asNumber(quotaRaw.targetsSelected),
      templatesUsed: asNumber(quotaRaw.templatesUsed),
      cardsUsed: asNumber(quotaRaw.cardsUsed),
      promptChars: asNumber(quotaRaw.promptChars),
      maxPromptChars: asNumber(quotaRaw.maxPromptChars, 24_000),
    },
    mode: asString(raw.mode) || "merge_suggest",
  };
}

/**
 * 用途：构造 content_fuse 请求 payload；前端做软校验提示。
 */
export function buildContentFusePayload(input: {
  templateIds: string[];
  cardIds: string[];
  targetChapterIds: string[];
}): ContentFusePayload {
  return {
    templateIds: [...new Set(input.templateIds.filter(Boolean))],
    cardIds: [...new Set(input.cardIds.filter(Boolean))],
    targetChapterIds: [...new Set(input.targetChapterIds.filter(Boolean))],
    mode: "merge_suggest",
  };
}

/** 用途：配额文案。 */
export function formatFuseQuotaTip(payload: ContentFusePayload): string {
  const t = payload.templateIds.length;
  const c = payload.cardIds.length;
  const g = payload.targetChapterIds.length;
  return `模板 ${t}/${CONTENT_FUSE_LIMITS.maxTemplates} · 卡片 ${c}/${CONTENT_FUSE_LIMITS.maxCards} · 合计 ${t + c}/${CONTENT_FUSE_LIMITS.maxSourcesTotal} · 目标章 ${g}/${CONTENT_FUSE_LIMITS.maxTargets}`;
}

/**
 * 用途：来源芯片展示文案；优先 title，缺省回退 kind:短 id。
 * 对接：ContentFuseDialog 建议列表。
 */
export function formatFuseSourceRefLabel(ref: ContentFuseSourceRef): string {
  const title = (ref.title || "").trim();
  if (title) return title;
  return `${ref.kind}:${ref.id.slice(0, 12)}`;
}
