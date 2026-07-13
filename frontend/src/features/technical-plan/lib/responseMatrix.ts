/**
 * 模块：响应矩阵合并工具
 * 用途：从结构化招标分析派生矩阵行，按 sourceKey 保留用户映射；合并多批智能建议；
 *       多端 409 时对可编辑字段做无副作用三方合并（base/local/remote）。
 * 对接：useTechnicalPlanEditors、ResponseMatrixPanel、TechnicalPlanWorkspace 串行 response_match。
 * 二次开发：勿用 sourceIndex 作为主键；建议合并禁止字段级自动并集，整条按置信度择优；
 *       三方合并禁止 deep-merge/并集/静默覆盖；可编辑字段仅 notes/status/chapterIds/outlineNodeIds。
 */

import type {
  BidAnalysis,
  ChapterContent,
  OutlineNode,
  ResponseMatrixItem,
  ResponseMatrixKind,
  ResponseMatrixSuggestion,
  ResponseMatrixSuggestionStatus,
  ResponseMatrixStatus,
} from "../types";

/** 用途：三方合并可编辑字段（分析派生元数据不参与）。 */
export type ResponseMatrixEditableField =
  | "notes"
  | "status"
  | "chapterIds"
  | "outlineNodeIds";

/** 用途：用户对冲突字段的显式选择；未选不得默认覆盖侧。 */
export type ResponseMatrixConflictChoice = "local" | "remote";

/** 用途：单字段冲突描述，供预览 UI 对照 base/local/remote。 */
export type ResponseMatrixFieldConflict = {
  sourceKey: string;
  sourceText: string;
  field: ResponseMatrixEditableField;
  baseValue: string | string[];
  localValue: string | string[];
  remoteValue: string | string[];
};

/** 用途：行集冲突（base 有行，一端删除、另一端修改）。 */
export type ResponseMatrixRowConflict = {
  sourceKey: string;
  sourceText: string;
  base: ResponseMatrixItem | null;
  local: ResponseMatrixItem | null;
  remote: ResponseMatrixItem | null;
};

/**
 * 用途：三方合并纯结果；hasConflicts 为真时须全部字段/行冲突显式选择后才能应用。
 * 对接：useTechnicalPlanEditors 409 预览；apply 前用 choices 再 resolve。
 */
export type ResponseMatrixThreeWayMergeResult = {
  mergedMatrix: ResponseMatrixItem[];
  fieldConflicts: ResponseMatrixFieldConflict[];
  rowConflicts: ResponseMatrixRowConflict[];
  hasConflicts: boolean;
};

export type ResponseMatrixCoverage = {
  validChapterIds: string[];
  validOutlineNodeIds: string[];
  invalidCount: number;
};

type MatrixSource = {
  kind: ResponseMatrixKind;
  sourceIndex: number;
  sourceText: string;
  weight: string;
  sourceKey: string;
};

const STATUSES: ResponseMatrixStatus[] = [
  "uncovered",
  "partial",
  "covered",
  "waived",
];
const SUGGESTION_STATUSES: ResponseMatrixSuggestionStatus[] = [
  "uncovered",
  "partial",
  "covered",
];

function normalizeText(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLocaleLowerCase();
}

function stableId(sourceKey: string): string {
  let hash = 0;
  for (let i = 0; i < sourceKey.length; i += 1) {
    hash = (hash * 31 + sourceKey.charCodeAt(i)) >>> 0;
  }
  return `mx_${hash.toString(16).padStart(8, "0")}`;
}

export function makeResponseMatrixSourceKey(
  kind: ResponseMatrixKind,
  sourceText: string,
): string {
  /** 用途：生成矩阵来源稳定键，避免条目调序后错绑。 */
  return `${kind}:${normalizeText(sourceText)}`;
}

function uniqueStrings(values: unknown): string[] {
  if (!Array.isArray(values)) return [];
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    const text = String(value || "").trim();
    if (!text || seen.has(text)) continue;
    seen.add(text);
    result.push(text);
  }
  return result;
}

export function normalizeResponseMatrix(
  raw: unknown,
): ResponseMatrixItem[] {
  /** 用途：规范 API/localStorage 中的响应矩阵，坏行丢弃，状态降级为未覆盖。 */
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item, index) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Partial<ResponseMatrixItem>;
    if (row.kind !== "requirement" && row.kind !== "scoring") return [];
    const sourceText = String(row.sourceText || "").trim();
    if (!sourceText) return [];
    const sourceKey =
      String(row.sourceKey || "").trim() ||
      makeResponseMatrixSourceKey(row.kind, sourceText);
    const status = STATUSES.includes(row.status as ResponseMatrixStatus)
      ? (row.status as ResponseMatrixStatus)
      : "uncovered";
    return [
      {
        id: String(row.id || "").trim() || stableId(sourceKey),
        kind: row.kind,
        sourceKey,
        sourceIndex: Number.isFinite(Number(row.sourceIndex))
          ? Math.max(0, Number(row.sourceIndex))
          : index,
        sourceText,
        weight: String(row.weight || ""),
        chapterIds: uniqueStrings(row.chapterIds),
        outlineNodeIds: uniqueStrings(row.outlineNodeIds),
        status,
        notes: String(row.notes || ""),
      },
    ];
  });
}

export function normalizeResponseMatrixSuggestions(
  raw: unknown,
): ResponseMatrixSuggestion[] {
  /** 用途：规范任务结果中的待确认建议，不把异常数据带入人工应用流程。 */
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const value = item as Partial<ResponseMatrixSuggestion>;
    const sourceKey = String(value.sourceKey || "").trim();
    if (!sourceKey || seen.has(sourceKey)) return [];
    const status = SUGGESTION_STATUSES.includes(
      value.status as ResponseMatrixSuggestionStatus,
    )
      ? (value.status as ResponseMatrixSuggestionStatus)
      : "uncovered";
    const base = value.base;
    if (!base || typeof base !== "object") return [];
    seen.add(sourceKey);
    return [
      {
        sourceKey,
        chapterIds: uniqueStrings(value.chapterIds),
        outlineNodeIds: uniqueStrings(value.outlineNodeIds),
        status,
        confidence: Math.max(0, Math.min(100, Number(value.confidence) || 0)),
        reason: String(value.reason || "").trim().slice(0, 500),
        base: {
          chapterIds: uniqueStrings(base.chapterIds),
          outlineNodeIds: uniqueStrings(base.outlineNodeIds),
          status: STATUSES.includes(base.status as ResponseMatrixStatus)
            ? (base.status as ResponseMatrixStatus)
            : "uncovered",
        },
      },
    ];
  });
}

function suggestionLinkCount(item: ResponseMatrixSuggestion): number {
  return item.chapterIds.length + item.outlineNodeIds.length;
}

function isBetterSuggestion(
  candidate: ResponseMatrixSuggestion,
  current: ResponseMatrixSuggestion,
): boolean {
  /** 用途：对齐后端去重：confidence 高者优先，平手时关联 ID 更多者优先。 */
  if (candidate.confidence !== current.confidence) {
    return candidate.confidence > current.confidence;
  }
  return suggestionLinkCount(candidate) > suggestionLinkCount(current);
}

export function mergeResponseMatrixSuggestions(
  existing: ResponseMatrixSuggestion[],
  incoming: ResponseMatrixSuggestion[],
): ResponseMatrixSuggestion[] {
  /**
   * 用途：跨批累计待确认建议；同 sourceKey 整条择优，禁止字段级合并。
   * 对接：TechnicalPlanWorkspace 串行 response_match；不写 editor-state。
   */
  const normalizedExisting = normalizeResponseMatrixSuggestions(existing);
  const normalizedIncoming = normalizeResponseMatrixSuggestions(incoming);
  const byKey = new Map(
    normalizedExisting.map((item) => [item.sourceKey, item] as const),
  );
  const order = normalizedExisting.map((item) => item.sourceKey);
  for (const item of normalizedIncoming) {
    const previous = byKey.get(item.sourceKey);
    if (!previous) {
      byKey.set(item.sourceKey, item);
      order.push(item.sourceKey);
      continue;
    }
    if (isBetterSuggestion(item, previous)) {
      byKey.set(item.sourceKey, item);
    }
  }
  return order
    .map((key) => byKey.get(key))
    .filter((item): item is ResponseMatrixSuggestion => Boolean(item));
}

function collectSources(analysis: BidAnalysis): MatrixSource[] {
  const sources: MatrixSource[] = [];
  analysis.techRequirements.forEach((text, index) => {
    const sourceText = String(text || "").trim();
    if (!sourceText) return;
    sources.push({
      kind: "requirement",
      sourceIndex: index,
      sourceText,
      weight: "",
      sourceKey: makeResponseMatrixSourceKey("requirement", sourceText),
    });
  });
  analysis.scoringPoints.forEach((point, index) => {
    const sourceText = String(point.name || "").trim();
    if (!sourceText) return;
    sources.push({
      kind: "scoring",
      sourceIndex: index,
      sourceText,
      weight: String(point.weight || ""),
      sourceKey: makeResponseMatrixSourceKey("scoring", sourceText),
    });
  });
  return sources;
}

export function mergeResponseMatrix(
  analysis: BidAnalysis,
  previous: ResponseMatrixItem[],
): ResponseMatrixItem[] {
  /**
   * 用途：按当前 analysis 生成矩阵；同 sourceKey 的旧行继承映射、状态和备注。
   * 对接：分析步保存、AI 分析后 reloadFromApi、手动刷新响应矩阵。
   */
  const bySource = new Map(
    normalizeResponseMatrix(previous).map((item) => [item.sourceKey, item]),
  );
  return collectSources(analysis).map((source) => {
    const old = bySource.get(source.sourceKey);
    return {
      id: old?.id || stableId(source.sourceKey),
      kind: source.kind,
      sourceKey: source.sourceKey,
      sourceIndex: source.sourceIndex,
      sourceText: source.sourceText,
      weight: source.weight,
      chapterIds: old?.chapterIds ?? [],
      outlineNodeIds: old?.outlineNodeIds ?? [],
      status: old?.status ?? "uncovered",
      notes: old?.notes ?? "",
    };
  });
}

export function collectOutlineOptions(nodes: OutlineNode[]) {
  /** 用途：把大纲树展开为可勾选选项，保留层级展示。 */
  const options: Array<{ id: string; title: string; level: number }> = [];
  const walk = (items: OutlineNode[]) => {
    for (const item of items) {
      options.push({ id: item.id, title: item.title, level: item.level });
      if (item.children?.length) walk(item.children);
    }
  };
  walk(nodes);
  return options;
}

export function reconcileResponseMatrixLinks(
  items: ResponseMatrixItem[],
  chapters: ChapterContent[],
  outline: OutlineNode[],
): ResponseMatrixItem[] {
  /**
   * 用途：移除已不存在的章节/大纲引用；无有效引用时降级覆盖状态。
   * 对接：大纲替换、章节回读、矩阵保存前的状态收敛。
   */
  const chapterSet = new Set(chapters.map((chapter) => chapter.id));
  const outlineSet = new Set(collectOutlineOptions(outline).map((node) => node.id));
  return normalizeResponseMatrix(items).map((item) => {
    const chapterIds = item.chapterIds.filter((id) => chapterSet.has(id));
    const outlineNodeIds = item.outlineNodeIds.filter((id) => outlineSet.has(id));
    const hasValidLink = chapterIds.length + outlineNodeIds.length > 0;
    return {
      ...item,
      chapterIds,
      outlineNodeIds,
      status:
        hasValidLink || item.status === "waived"
          ? item.status
          : "uncovered",
    };
  });
}

export function getResponseMatrixCoverage(
  item: ResponseMatrixItem,
  chapters: ChapterContent[],
  outline: OutlineNode[],
): ResponseMatrixCoverage {
  /** 用途：计算矩阵行的有效引用和失效引用数量，避免死 id 被算作已覆盖。 */
  const chapterSet = new Set(chapters.map((chapter) => chapter.id));
  const outlineSet = new Set(collectOutlineOptions(outline).map((node) => node.id));
  const validChapterIds = item.chapterIds.filter((id) => chapterSet.has(id));
  const validOutlineNodeIds = item.outlineNodeIds.filter((id) => outlineSet.has(id));
  return {
    validChapterIds,
    validOutlineNodeIds,
    invalidCount:
      item.chapterIds.length +
      item.outlineNodeIds.length -
      validChapterIds.length -
      validOutlineNodeIds.length,
  };
}

/** 用途：规范化 ID 列表后再比较；顺序无关，禁止把并集当相等。 */
export function sameResponseMatrixIds(left: string[], right: string[]): boolean {
  const a = uniqueStrings(left).sort();
  const b = uniqueStrings(right).sort();
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

/** 用途：notes 全字符串比较，不 trim。 */
function sameNotes(left: string, right: string): boolean {
  return left === right;
}

function sameStatus(
  left: ResponseMatrixStatus,
  right: ResponseMatrixStatus,
): boolean {
  return left === right;
}

/**
 * 用途：比较两行可编辑字段是否一致（数组去重排序；notes 不 trim）。
 * 对接：三方合并与 409 后「请求后本地是否变化」检测。
 */
export function sameResponseMatrixEditableFields(
  left: ResponseMatrixItem,
  right: ResponseMatrixItem,
): boolean {
  return (
    sameNotes(left.notes, right.notes) &&
    sameStatus(left.status, right.status) &&
    sameResponseMatrixIds(left.chapterIds, right.chapterIds) &&
    sameResponseMatrixIds(left.outlineNodeIds, right.outlineNodeIds)
  );
}

/**
 * 用途：按 sourceKey 比较两份矩阵的可编辑字段是否一致（忽略行序）。
 * 对接：保存请求发出后、409 回包时判断本地是否又改过。
 */
export function sameResponseMatrixEditableSnapshot(
  left: ResponseMatrixItem[],
  right: ResponseMatrixItem[],
): boolean {
  const a = normalizeResponseMatrix(left);
  const b = normalizeResponseMatrix(right);
  if (a.length !== b.length) return false;
  const byKey = new Map(b.map((item) => [item.sourceKey, item] as const));
  for (const item of a) {
    const other = byKey.get(item.sourceKey);
    if (!other || !sameResponseMatrixEditableFields(item, other)) return false;
    byKey.delete(item.sourceKey);
  }
  return byKey.size === 0;
}

/** 用途：深拷贝矩阵行，供 base 快照与合并预览隔离引用。 */
export function cloneResponseMatrix(
  items: ResponseMatrixItem[],
): ResponseMatrixItem[] {
  return normalizeResponseMatrix(items).map((item) => ({
    ...item,
    chapterIds: [...item.chapterIds],
    outlineNodeIds: [...item.outlineNodeIds],
  }));
}

function editableFieldValue(
  item: ResponseMatrixItem,
  field: ResponseMatrixEditableField,
): string | string[] {
  if (field === "notes") return item.notes;
  if (field === "status") return item.status;
  if (field === "chapterIds") return [...uniqueStrings(item.chapterIds)].sort();
  return [...uniqueStrings(item.outlineNodeIds)].sort();
}

function fieldEquals(
  field: ResponseMatrixEditableField,
  left: ResponseMatrixItem,
  right: ResponseMatrixItem,
): boolean {
  if (field === "notes") return sameNotes(left.notes, right.notes);
  if (field === "status") return sameStatus(left.status, right.status);
  if (field === "chapterIds") {
    return sameResponseMatrixIds(left.chapterIds, right.chapterIds);
  }
  return sameResponseMatrixIds(left.outlineNodeIds, right.outlineNodeIds);
}

function pickShellRow(
  preferred: ResponseMatrixItem | null | undefined,
  fallback: ResponseMatrixItem | null | undefined,
  base: ResponseMatrixItem | null | undefined,
): ResponseMatrixItem | null {
  const shell = preferred || fallback || base;
  if (!shell) return null;
  return {
    id: shell.id,
    kind: shell.kind,
    sourceKey: shell.sourceKey,
    sourceIndex: shell.sourceIndex,
    sourceText: shell.sourceText,
    weight: shell.weight,
    chapterIds: [],
    outlineNodeIds: [],
    status: "uncovered",
    notes: "",
  };
}

function rowPresent(item: ResponseMatrixItem | null | undefined): item is ResponseMatrixItem {
  return Boolean(item);
}

function rowModifiedFromBase(
  current: ResponseMatrixItem | null | undefined,
  base: ResponseMatrixItem | null | undefined,
): boolean {
  if (!rowPresent(current) && !rowPresent(base)) return false;
  if (!rowPresent(current) || !rowPresent(base)) return true;
  return !sameResponseMatrixEditableFields(current, base);
}

const EDITABLE_FIELDS: ResponseMatrixEditableField[] = [
  "notes",
  "status",
  "chapterIds",
  "outlineNodeIds",
];

/**
 * 用途：对单行四个可编辑字段做原子三方比较；同字段双端不同值记冲突。
 * 对接：threeWayMergeResponseMatrix；冲突时 merged 暂用 local 占位，须 resolve 后覆盖。
 */
function mergeEditableFieldsThreeWay(
  base: ResponseMatrixItem | null,
  local: ResponseMatrixItem | null,
  remote: ResponseMatrixItem | null,
  shell: ResponseMatrixItem,
): {
  merged: ResponseMatrixItem;
  conflicts: ResponseMatrixFieldConflict[];
} {
  const conflicts: ResponseMatrixFieldConflict[] = [];
  const merged: ResponseMatrixItem = {
    ...shell,
    notes: shell.notes,
    status: shell.status,
    chapterIds: [...shell.chapterIds],
    outlineNodeIds: [...shell.outlineNodeIds],
  };

  // 无 base 时：仅一端有值取该端；两端都有且不同则冲突
  for (const field of EDITABLE_FIELDS) {
    const baseItem = base;
    const localItem = local;
    const remoteItem = remote;

    if (!baseItem) {
      if (localItem && remoteItem) {
        if (fieldEquals(field, localItem, remoteItem)) {
          applyField(merged, field, localItem);
        } else {
          applyField(merged, field, localItem);
          conflicts.push({
            sourceKey: shell.sourceKey,
            sourceText: shell.sourceText,
            field,
            baseValue: field === "notes" || field === "status" ? "" : [],
            localValue: editableFieldValue(localItem, field),
            remoteValue: editableFieldValue(remoteItem, field),
          });
        }
      } else if (localItem) {
        applyField(merged, field, localItem);
      } else if (remoteItem) {
        applyField(merged, field, remoteItem);
      }
      continue;
    }

    const localSame = localItem
      ? fieldEquals(field, localItem, baseItem)
      : true;
    const remoteSame = remoteItem
      ? fieldEquals(field, remoteItem, baseItem)
      : true;
    // 一端缺失视为相对 base 的删除，对可编辑字段按「回到空/uncovered」处理不在此；
    // 行级删除由 rowConflicts 处理；此处两端均在时走标准三方。
    if (!localItem || !remoteItem) {
      if (localItem) applyField(merged, field, localItem);
      else if (remoteItem) applyField(merged, field, remoteItem);
      else applyField(merged, field, baseItem);
      continue;
    }

    if (localSame && remoteSame) {
      applyField(merged, field, baseItem);
    } else if (localSame && !remoteSame) {
      applyField(merged, field, remoteItem);
    } else if (!localSame && remoteSame) {
      applyField(merged, field, localItem);
    } else if (fieldEquals(field, localItem, remoteItem)) {
      applyField(merged, field, localItem);
    } else {
      applyField(merged, field, localItem);
      conflicts.push({
        sourceKey: shell.sourceKey,
        sourceText: shell.sourceText,
        field,
        baseValue: editableFieldValue(baseItem, field),
        localValue: editableFieldValue(localItem, field),
        remoteValue: editableFieldValue(remoteItem, field),
      });
    }
  }

  return { merged, conflicts };
}

function applyField(
  target: ResponseMatrixItem,
  field: ResponseMatrixEditableField,
  source: ResponseMatrixItem,
): void {
  if (field === "notes") target.notes = source.notes;
  else if (field === "status") target.status = source.status;
  else if (field === "chapterIds") {
    target.chapterIds = uniqueStrings(source.chapterIds);
  } else {
    target.outlineNodeIds = uniqueStrings(source.outlineNodeIds);
  }
}

/**
 * 用途：base/local/remote 三方合并；仅 sourceKey 对齐；输出预合并矩阵与冲突清单。
 * 对接：409 合并预览；应用前 resolveResponseMatrixThreeWayChoices。
 * 二次开发：禁止并集/deep-merge；行冲突与字段冲突均须显式选择。
 */
export function threeWayMergeResponseMatrix(
  base: ResponseMatrixItem[],
  local: ResponseMatrixItem[],
  remote: ResponseMatrixItem[],
): ResponseMatrixThreeWayMergeResult {
  const baseNorm = normalizeResponseMatrix(base);
  const localNorm = normalizeResponseMatrix(local);
  const remoteNorm = normalizeResponseMatrix(remote);
  const baseByKey = new Map(baseNorm.map((item) => [item.sourceKey, item] as const));
  const localByKey = new Map(localNorm.map((item) => [item.sourceKey, item] as const));
  const remoteByKey = new Map(remoteNorm.map((item) => [item.sourceKey, item] as const));

  const keys: string[] = [];
  const seen = new Set<string>();
  for (const list of [localNorm, remoteNorm, baseNorm]) {
    for (const item of list) {
      if (seen.has(item.sourceKey)) continue;
      seen.add(item.sourceKey);
      keys.push(item.sourceKey);
    }
  }

  const mergedMatrix: ResponseMatrixItem[] = [];
  const fieldConflicts: ResponseMatrixFieldConflict[] = [];
  const rowConflicts: ResponseMatrixRowConflict[] = [];

  for (const sourceKey of keys) {
    const baseRow = baseByKey.get(sourceKey) ?? null;
    const localRow = localByKey.get(sourceKey) ?? null;
    const remoteRow = remoteByKey.get(sourceKey) ?? null;

    // base 有行：一端删除 + 另一端修改 → 行冲突
    if (baseRow) {
      const localDeleted = !localRow;
      const remoteDeleted = !remoteRow;
      const localMod = rowModifiedFromBase(localRow, baseRow);
      const remoteMod = rowModifiedFromBase(remoteRow, baseRow);

      if ((localDeleted && remoteMod && remoteRow) || (remoteDeleted && localMod && localRow)) {
        const shell =
          pickShellRow(localRow, remoteRow, baseRow) || baseRow;
        rowConflicts.push({
          sourceKey,
          sourceText: shell.sourceText,
          base: baseRow,
          local: localRow,
          remote: remoteRow,
        });
        // 占位用 local 优先，待用户选择整行
        mergedMatrix.push({
          ...(localRow || remoteRow || baseRow),
          chapterIds: [...(localRow || remoteRow || baseRow).chapterIds],
          outlineNodeIds: [...(localRow || remoteRow || baseRow).outlineNodeIds],
        });
        continue;
      }

      if (localDeleted && remoteDeleted) {
        // 两端都删：不保留行
        continue;
      }
      if (localDeleted && !remoteMod) {
        // 仅本地删且远端未改 → 接受删除
        continue;
      }
      if (remoteDeleted && !localMod) {
        continue;
      }
    } else {
      // base 无此行：仅一端新增保留该端；两端新增不同则按字段三方
      if (localRow && !remoteRow) {
        mergedMatrix.push({
          ...localRow,
          chapterIds: [...localRow.chapterIds],
          outlineNodeIds: [...localRow.outlineNodeIds],
        });
        continue;
      }
      if (remoteRow && !localRow) {
        mergedMatrix.push({
          ...remoteRow,
          chapterIds: [...remoteRow.chapterIds],
          outlineNodeIds: [...remoteRow.outlineNodeIds],
        });
        continue;
      }
      if (!localRow && !remoteRow) continue;
    }

    const shell = pickShellRow(localRow, remoteRow, baseRow);
    if (!shell) continue;
    const { merged, conflicts } = mergeEditableFieldsThreeWay(
      baseRow,
      localRow,
      remoteRow,
      shell,
    );
    mergedMatrix.push(merged);
    fieldConflicts.push(...conflicts);
  }

  return {
    mergedMatrix: normalizeResponseMatrix(mergedMatrix),
    fieldConflicts,
    rowConflicts,
    hasConflicts: fieldConflicts.length > 0 || rowConflicts.length > 0,
  };
}

/**
 * 用途：冲突选择键（行级冲突 field 用 "*"）。
 * 对接：ResponseMatrixPanel 显式「采用本地/远端」。
 */
export function responseMatrixConflictChoiceKey(
  sourceKey: string,
  field: ResponseMatrixEditableField | "*",
): string {
  return `${sourceKey}::${field}`;
}

/**
 * 用途：把用户显式选择套入三方结果，生成可 PUT 的最终矩阵。
 * 对接：apply 合并；未对全部冲突作选择时返回 null。
 * 二次开发：不得为未选项预填 local/remote。
 */
export function resolveResponseMatrixThreeWayChoices(
  merge: ResponseMatrixThreeWayMergeResult,
  choices: Record<string, ResponseMatrixConflictChoice>,
): ResponseMatrixItem[] | null {
  for (const conflict of merge.rowConflicts) {
    const key = responseMatrixConflictChoiceKey(conflict.sourceKey, "*");
    if (choices[key] !== "local" && choices[key] !== "remote") return null;
  }
  for (const conflict of merge.fieldConflicts) {
    const key = responseMatrixConflictChoiceKey(
      conflict.sourceKey,
      conflict.field,
    );
    if (choices[key] !== "local" && choices[key] !== "remote") return null;
  }

  const byKey = new Map(
    cloneResponseMatrix(merge.mergedMatrix).map(
      (item) => [item.sourceKey, item] as const,
    ),
  );

  for (const conflict of merge.rowConflicts) {
    const key = responseMatrixConflictChoiceKey(conflict.sourceKey, "*");
    const side = choices[key];
    const picked =
      side === "local" ? conflict.local : conflict.remote;
    if (!picked) {
      byKey.delete(conflict.sourceKey);
      continue;
    }
    byKey.set(conflict.sourceKey, {
      ...picked,
      chapterIds: [...picked.chapterIds],
      outlineNodeIds: [...picked.outlineNodeIds],
    });
  }

  for (const conflict of merge.fieldConflicts) {
    const key = responseMatrixConflictChoiceKey(
      conflict.sourceKey,
      conflict.field,
    );
    const side = choices[key];
    const row = byKey.get(conflict.sourceKey);
    if (!row) continue;
    if (conflict.field === "notes") {
      row.notes = String(
        side === "local" ? conflict.localValue : conflict.remoteValue,
      );
    } else if (conflict.field === "status") {
      const value = String(
        side === "local" ? conflict.localValue : conflict.remoteValue,
      ) as ResponseMatrixStatus;
      row.status = STATUSES.includes(value) ? value : "uncovered";
    } else if (conflict.field === "chapterIds") {
      const raw =
        side === "local" ? conflict.localValue : conflict.remoteValue;
      row.chapterIds = uniqueStrings(raw);
    } else {
      const raw =
        side === "local" ? conflict.localValue : conflict.remoteValue;
      row.outlineNodeIds = uniqueStrings(raw);
    }
  }

  // 保持合并时的 key 顺序
  const order = merge.mergedMatrix.map((item) => item.sourceKey);
  // 行冲突选择删除后可能缺 key
  const extra = [...byKey.keys()].filter((key) => !order.includes(key));
  return normalizeResponseMatrix(
    [...order, ...extra]
      .map((key) => byKey.get(key))
      .filter((item): item is ResponseMatrixItem => Boolean(item)),
  );
}

/** 用途：字段名中文标签，供合并预览 UI。 */
export function responseMatrixEditableFieldLabel(
  field: ResponseMatrixEditableField,
): string {
  if (field === "notes") return "备注";
  if (field === "status") return "响应状态";
  if (field === "chapterIds") return "章节关联";
  return "大纲关联";
}

/** 用途：把字段值格式化为可读中文预览。 */
export function formatResponseMatrixFieldPreview(
  field: ResponseMatrixEditableField,
  value: string | string[],
  chapterTitles?: Map<string, string>,
  outlineTitles?: Map<string, string>,
): string {
  if (field === "notes") {
    const text = String(value ?? "");
    return text.length > 0 ? text : "（空）";
  }
  if (field === "status") {
    const map: Record<string, string> = {
      uncovered: "未覆盖",
      partial: "部分覆盖",
      covered: "已覆盖",
      waived: "不响应",
    };
    return map[String(value)] || String(value || "（空）");
  }
  const ids = Array.isArray(value) ? value : [];
  if (ids.length === 0) return "（无）";
  const titles =
    field === "chapterIds" ? chapterTitles : outlineTitles;
  return ids
    .map((id) => titles?.get(id) || id)
    .join("、");
}
