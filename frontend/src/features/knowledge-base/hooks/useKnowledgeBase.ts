/**
 * 模块：知识库文档状态（V1-O 服务端真值）
 * 用途：文件夹树 + 文档列表/筛选/批量移动/上传索引/重试 + P9C 语义索引状态刷新/重建/轮询。
 * 对接：GET|POST /api/knowledge/*；GET|POST /api/knowledge/semantic-index*；页面 KnowledgeBasePage。
 * 二次开发：文档主状态仅 loading|ready|error；禁止 local 成功态、假 ID、旧键读写删迁；
 *          语义索引禁止写入 localStorage 伪就绪；图片/素材卡片走 useKnowledgeCards。
 *
 * 旧键策略（biaoshu.knowledgeBase.docs.v1 及同族）：
 * 旧键混有演示种子和可能的历史用户数据，无法可信区分；自动迁移有数据完整性和隐私风险，
 * 故旧键族不读、不写、不删、不迁移、不上传。仅以服务端 GET/写后对账为唯一真值。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiFetch } from "../../../shared/lib/api";
import type {
  DocParseStatus,
  KbFolder,
  KnowledgeDoc,
  SemanticIndex,
} from "../types";
import {
  isSemanticIndexBuilding,
  KB_FOLDER_ALL,
  normalizeSemanticIndex,
  SEMANTIC_REBUILD_FAILED_MSG,
  SEMANTIC_STATUS_UNAVAILABLE_MSG,
} from "../types";

/** 语义索引构建中轮询间隔（毫秒） */
const SEMANTIC_POLL_MS = 2000;

/** 主加载失败固定文案（禁止透传 err.message） */
const LOAD_ERROR = "知识库文档加载失败，请稍后重试";
const CREATE_FOLDER_ERR = "创建文件夹失败，请稍后重试";
const UPLOAD_ERR = "文档上传失败，请稍后重试";
const MOVE_ERR = "移动文档失败，请稍后重试";
const DELETE_ERR = "删除文档失败，请稍后重试";
const REINDEX_ERR = "重新索引失败，请稍后重试";

const DOC_STATUS_VALUES: readonly DocParseStatus[] = [
  "ready",
  "parsing",
  "indexing",
  "failed",
  "pending",
];

/** 文档主状态机 */
export type KbDocStatus = "loading" | "ready" | "error";

/**
 * Hook 内规范化类型：statusMessage/sizeLabel 容纳 null（types 仍为可选 string）。
 */
type NormalizedFolder = {
  id: string;
  name: string;
  parentId: string | null;
};

type NormalizedDoc = {
  id: string;
  name: string;
  tags: string[];
  chunks: number;
  updated: string;
  updatedAt: string;
  category: string;
  folderId: string;
  status: DocParseStatus;
  statusMessage: string | null;
  sizeLabel: string | null;
};

function trimNonEmptyString(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const t = raw.trim();
  return t.length > 0 ? t : null;
}

function parseParentId(raw: unknown, hasKey: boolean): string | null | undefined {
  // 缺失或 null → null；否则必须为 trim 后非空字符串；其它类型非法 → undefined 表示失败
  if (!hasKey || raw === null || raw === undefined) return null;
  if (typeof raw !== "string") return undefined;
  const t = raw.trim();
  if (!t) return undefined;
  return t;
}

function parseNullableStringField(
  obj: Record<string, unknown>,
  key: string,
): string | null | undefined {
  // 缺失/null → null；否则必须 string；错型 → undefined 表示失败
  if (!(key in obj) || obj[key] === null || obj[key] === undefined) return null;
  if (typeof obj[key] !== "string") return undefined;
  return obj[key] as string;
}

function parseFolderObject(raw: unknown): NormalizedFolder | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = trimNonEmptyString(o.id);
  const name = trimNonEmptyString(o.name);
  if (!id || !name) return null;
  const parentId = parseParentId(o.parentId, "parentId" in o);
  if (parentId === undefined) return null;
  return { id, name, parentId };
}

function parseDocObject(
  raw: unknown,
  folderIds: ReadonlySet<string>,
): NormalizedDoc | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = trimNonEmptyString(o.id);
  const name = trimNonEmptyString(o.name);
  const updated = trimNonEmptyString(o.updated);
  const updatedAt = trimNonEmptyString(o.updatedAt);
  const category = trimNonEmptyString(o.category);
  const folderId = trimNonEmptyString(o.folderId);
  if (!id || !name || !updated || !updatedAt || !category || !folderId) {
    return null;
  }
  if (!folderIds.has(folderId)) return null;

  if (!Array.isArray(o.tags)) return null;
  const tags: string[] = [];
  for (const t of o.tags) {
    if (typeof t !== "string") return null;
    tags.push(t);
  }

  if (
    typeof o.chunks !== "number" ||
    !Number.isSafeInteger(o.chunks) ||
    o.chunks < 0
  ) {
    return null;
  }

  if (
    typeof o.status !== "string" ||
    !(DOC_STATUS_VALUES as readonly string[]).includes(o.status)
  ) {
    return null;
  }

  const statusMessage = parseNullableStringField(o, "statusMessage");
  if (statusMessage === undefined) return null;
  const sizeLabel = parseNullableStringField(o, "sizeLabel");
  if (sizeLabel === undefined) return null;

  return {
    id,
    name,
    tags,
    chunks: o.chunks,
    updated,
    updatedAt,
    category,
    folderId,
    status: o.status as DocParseStatus,
    statusMessage,
    sizeLabel,
  };
}

function folderGraphInvalid(folders: NormalizedFolder[]): boolean {
  const ids = new Set<string>();
  for (const f of folders) {
    if (ids.has(f.id)) return true;
    ids.add(f.id);
  }
  for (const f of folders) {
    if (f.parentId === null) continue;
    if (f.parentId === f.id) return true;
    if (!ids.has(f.parentId)) return true;
  }
  // 任意深度 cycle
  const parentOf = new Map(folders.map((f) => [f.id, f.parentId]));
  for (const f of folders) {
    const visited = new Set<string>();
    let cur: string | null = f.id;
    while (cur !== null) {
      if (visited.has(cur)) return true;
      visited.add(cur);
      const p = parentOf.get(cur);
      if (p === undefined) break;
      cur = p;
    }
  }
  return false;
}

/** 从 unknown 严格解析 folders 整批；非法返回 null（零半列表） */
function parseFoldersBatch(raw: unknown): NormalizedFolder[] | null {
  if (!Array.isArray(raw)) return null;
  const out: NormalizedFolder[] = [];
  for (const item of raw) {
    const f = parseFolderObject(item);
    if (!f) return null;
    out.push(f);
  }
  if (folderGraphInvalid(out)) return null;
  return out;
}

/** 从 unknown 严格解析 docs 整批；非法返回 null */
function parseDocsBatch(
  raw: unknown,
  folders: NormalizedFolder[],
): NormalizedDoc[] | null {
  if (!Array.isArray(raw)) return null;
  const folderIds = new Set(folders.map((f) => f.id));
  const out: NormalizedDoc[] = [];
  const ids = new Set<string>();
  for (const item of raw) {
    const d = parseDocObject(item, folderIds);
    if (!d) return null;
    if (ids.has(d.id)) return null;
    ids.add(d.id);
    out.push(d);
  }
  return out;
}

/** move 响应 moved：严格 number、integer、非 -0，且精确等于期望 */
function isValidMovedCount(raw: unknown, expected: number): boolean {
  if (!raw || typeof raw !== "object") return false;
  const moved = (raw as Record<string, unknown>).moved;
  if (typeof moved !== "number") return false;
  if (!Number.isInteger(moved)) return false;
  if (Object.is(moved, -0)) return false;
  return moved === expected;
}

/** ids 首次出现顺序去重 */
function dedupeIdsPreserveOrder(ids: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const id of ids) {
    if (seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out;
}

function toKbFolder(f: NormalizedFolder): KbFolder {
  return { id: f.id, name: f.name, parentId: f.parentId };
}

function toKnowledgeDoc(d: NormalizedDoc): KnowledgeDoc {
  // statusMessage 仅在 schema 解析阶段校验；提交到页面数据面时剥离，
  // 防止原文进入 React 状态进而泄漏到 text/title/aria 等出口。
  const row: KnowledgeDoc = {
    id: d.id,
    name: d.name,
    tags: d.tags,
    chunks: d.chunks,
    updated: d.updated,
    updatedAt: d.updatedAt,
    category: d.category,
    folderId: d.folderId,
    status: d.status,
  };
  if (d.sizeLabel !== null) row.sizeLabel = d.sizeLabel;
  return row;
}

/**
 * 用途：multipart 上传知识库文档（含 folderId）。
 */
async function uploadKbDoc(file: File, folderId?: string): Promise<unknown> {
  const form = new FormData();
  form.append("file", file);
  if (folderId) form.append("folderId", folderId);
  return apiFetch<unknown>("/knowledge/docs/upload", {
    method: "POST",
    body: form,
  });
}

export function useKnowledgeBase() {
  const [docStatus, setDocStatus] = useState<KbDocStatus>("loading");
  const [folders, setFolders] = useState<KbFolder[]>([]);
  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedFolderId, setSelectedFolderId] =
    useState<string>(KB_FOLDER_ALL);
  const [docQuery, setDocQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<DocParseStatus | "all">(
    "all",
  );
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  // P9C：仅内存态，禁止 localStorage 伪造语义就绪
  const [semanticIndex, setSemanticIndex] = useState<SemanticIndex | null>(
    null,
  );
  const [semanticError, setSemanticError] = useState<string | null>(null);
  const [semanticBusy, setSemanticBusy] = useState(false);

  const semanticPollRef = useRef<number | null>(null);
  const mountedRef = useRef(true);
  /** 主文档拉取/对账代次：refresh 递增使旧 success/catch/finally 失效 */
  const generationRef = useRef(0);
  /**
   * 语义代次：loading/error/unmount 时递增，使旧 semantic 回调整代失效。
   * 与下方 request seq/owner 正交：代次管跨代，seq 管同代内并发 GET/rebuild。
   */
  const semanticGenRef = useRef(0);
  /** 同一 semantic generation 内单调请求序号（每次 semantic GET / rebuild 成功写前分配） */
  const semanticReqSeqRef = useRef(0);
  /**
   * 当前可写 semanticIndex/error/busy 的 owner 序号。
   * 同代内后发请求认领 owner；仅 seq===owner 的 success/catch/finally 可写。
   */
  const semanticOwnerSeqRef = useRef(0);
  /** rebuild 同步锁：任何 await/setState 前抢占，同 tick 双 click 精确一 POST */
  const rebuildLockRef = useRef(false);
  /** 共享同步写锁：先抢锁再 setBusy，禁止仅依赖 React busy */
  const writeLockRef = useRef(false);
  const docStatusRef = useRef<KbDocStatus>("loading");
  const foldersRef = useRef<KbFolder[]>([]);
  /** 同步可读的语义索引：building 失败保值时不得依赖过期闭包 */
  const semanticIndexRef = useRef<SemanticIndex | null>(null);

  useEffect(() => {
    docStatusRef.current = docStatus;
  }, [docStatus]);

  useEffect(() => {
    foldersRef.current = folders;
  }, [folders]);

  useEffect(() => {
    semanticIndexRef.current = semanticIndex;
  }, [semanticIndex]);

  const clearSemanticPoll = useCallback(() => {
    if (semanticPollRef.current != null) {
      window.clearInterval(semanticPollRef.current);
      semanticPollRef.current = null;
    }
  }, []);

  /** 当前回调是否仍为可写 owner（mounted + 代次 + seq + ready） */
  const isSemanticWriteOwner = useCallback(
    (sgen: number, seq: number): boolean => {
      return (
        mountedRef.current &&
        semanticGenRef.current === sgen &&
        semanticOwnerSeqRef.current === seq &&
        docStatusRef.current === "ready"
      );
    },
    [],
  );

  /** 清空语义态并使旧语义请求代次失效 */
  const invalidateSemantic = useCallback(() => {
    semanticGenRef.current += 1;
    // 抬高 owner，阻断同代残留回调在代次竞态窗口误写
    semanticOwnerSeqRef.current = ++semanticReqSeqRef.current;
    clearSemanticPoll();
    semanticIndexRef.current = null;
    setSemanticIndex(null);
    setSemanticError(null);
    setSemanticBusy(false);
  }, [clearSemanticPoll]);

  /**
   * 用途：拉取当前工作空间语义索引状态；仅 docStatus=ready 允许。
   * 说明：错误文案固定中文，禁止透传 apiFetch/代理 detail。
   * 同代并发：每次 GET 分配单调 seq 并认领 owner；仅 owner 可写 state。
   * building 中 poll 失败/503：保留 last-known building，不清 semanticIndex。
   */
  const refreshSemanticIndex = useCallback(async () => {
    if (docStatusRef.current !== "ready") {
      return null;
    }
    const sgen = semanticGenRef.current;
    const seq = ++semanticReqSeqRef.current;
    // 认领 owner：后发 GET 使先前 in-flight GET 的 success/catch 失效
    semanticOwnerSeqRef.current = seq;
    try {
      const raw = await apiFetch<unknown>("/knowledge/semantic-index");
      if (!isSemanticWriteOwner(sgen, seq)) {
        return null;
      }
      const row = normalizeSemanticIndex(raw);
      semanticIndexRef.current = row;
      setSemanticIndex(row);
      setSemanticError(null);
      return row;
    } catch {
      if (!isSemanticWriteOwner(sgen, seq)) {
        return null;
      }
      // 固定脱敏错误；building 保值：不清 last-known（含构建中进度）
      setSemanticError(SEMANTIC_STATUS_UNAVAILABLE_MSG);
      if (!isSemanticIndexBuilding(semanticIndexRef.current)) {
        // 非 building：初始失败等路径保持 null（未构建 UI）
        semanticIndexRef.current = null;
        setSemanticIndex(null);
      }
      // building：保留 semanticIndex，轮询 effect 因仍 building 继续下一轮
      return null;
    }
  }, [isSemanticWriteOwner]);

  /** 进入 loading：立即清空列表/选择/语义，旧列表不得残留 */
  const enterLoading = useCallback(() => {
    setDocStatus("loading");
    docStatusRef.current = "loading";
    setFolders([]);
    foldersRef.current = [];
    setDocs([]);
    setSelectedFolderId(KB_FOLDER_ALL);
    setSelectedIds([]);
    setError(null);
    invalidateSemantic();
  }, [invalidateSemantic]);

  /** 进入 error：固定主加载文案，清空列表/选择/语义 */
  const enterError = useCallback(
    (gen: number) => {
      if (!mountedRef.current || generationRef.current !== gen) return;
      setDocStatus("error");
      docStatusRef.current = "error";
      setFolders([]);
      foldersRef.current = [];
      setDocs([]);
      setSelectedFolderId(KB_FOLDER_ALL);
      setSelectedIds([]);
      setError(LOAD_ERROR);
      invalidateSemantic();
    },
    [invalidateSemantic],
  );

  /**
   * 合法 ready 原子提交：替换 folders/docs；选择清理（folder 回 ALL；ids 仅滤不存在）。
   */
  const commitReady = useCallback(
    (
      gen: number,
      nextFolders: NormalizedFolder[],
      nextDocs: NormalizedDoc[],
      opError: string | null | undefined,
    ) => {
      if (!mountedRef.current || generationRef.current !== gen) return false;
      const folderRows = nextFolders.map(toKbFolder);
      const docRows = nextDocs.map(toKnowledgeDoc);
      setFolders(folderRows);
      foldersRef.current = folderRows;
      setDocs(docRows);
      setDocStatus("ready");
      docStatusRef.current = "ready";
      if (opError === undefined) {
        // 主 refresh 成功：清错误
        setError(null);
      } else {
        // 写后对账：写成功清操作错误；写失败保留固定操作错误
        setError(opError);
      }
      setSelectedFolderId((prev) => {
        if (prev === KB_FOLDER_ALL) return prev;
        if (nextFolders.some((f) => f.id === prev)) return prev;
        return KB_FOLDER_ALL;
      });
      setSelectedIds((prev) =>
        prev.filter((id) => nextDocs.some((d) => d.id === id)),
      );
      return true;
    },
    [],
  );

  /** 双 GET + 整批严格解析；调用方负责代次与状态提交 */
  const fetchFoldersAndDocs = useCallback(async (): Promise<
    | { ok: true; folders: NormalizedFolder[]; docs: NormalizedDoc[] }
    | { ok: false }
  > => {
    try {
      const [fRaw, dRaw] = await Promise.all([
        apiFetch<unknown>("/knowledge/folders"),
        apiFetch<unknown>("/knowledge/docs"),
      ]);
      const parsedFolders = parseFoldersBatch(fRaw);
      if (!parsedFolders) return { ok: false };
      const parsedDocs = parseDocsBatch(dRaw, parsedFolders);
      if (!parsedDocs) return { ok: false };
      return { ok: true, folders: parsedFolders, docs: parsedDocs };
    } catch {
      return { ok: false };
    }
  }, []);

  /**
   * 用途：主刷新（挂载/用户 refresh）。先失效旧代次，loading 清空，双 GET 原子 ready/error。
   */
  const refresh = useCallback(async () => {
    const gen = ++generationRef.current;
    if (mountedRef.current) {
      enterLoading();
    }
    const result = await fetchFoldersAndDocs();
    if (!mountedRef.current || generationRef.current !== gen) return false;
    if (!result.ok) {
      enterError(gen);
      return false;
    }
    const committed = commitReady(gen, result.folders, result.docs, undefined);
    if (committed) {
      // ready 原子提交后只触发一次语义状态读取；旧 refresh 迟到不得触发
      void refreshSemanticIndex();
    }
    return committed;
  }, [enterLoading, enterError, commitReady, fetchFoldersAndDocs, refreshSemanticIndex]);

  /**
   * 写后对账：精确一次双 GET；合法则原子替换且 docStatus 仍 ready；
   * 对账失败则主 error（覆盖操作错误）。
   * ownerGeneration 必须在抢写锁时冻结传入；禁止在此重新读取 generation 冒充 owner。
   * 双 GET 前后均校验 mounted / owner / ready；失配零 GET、零 semantic、零列表/错误/选择写。
   */
  const reconcileAfterWrite = useCallback(
    async (ownerGeneration: number, opError: string | null) => {
      // 前门：失配则零对账（不发起 folders/docs 双 GET）
      if (
        !mountedRef.current ||
        generationRef.current !== ownerGeneration ||
        docStatusRef.current !== "ready"
      ) {
        return;
      }
      const result = await fetchFoldersAndDocs();
      // 后门：双 GET 返回后再门；失配不得 enterError/commitReady
      if (
        !mountedRef.current ||
        generationRef.current !== ownerGeneration ||
        docStatusRef.current !== "ready"
      ) {
        return;
      }
      if (!result.ok) {
        enterError(ownerGeneration);
        return;
      }
      commitReady(ownerGeneration, result.folders, result.docs, opError);
    },
    [fetchFoldersAndDocs, enterError, commitReady],
  );

  useEffect(() => {
    mountedRef.current = true;
    void refresh();
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
      semanticGenRef.current += 1;
      semanticOwnerSeqRef.current = ++semanticReqSeqRef.current;
      clearSemanticPoll();
      // rebuild 锁在 unmount 后亦释放，避免悬挂；in-flight finally 仍靠 gen 围栏零写
      rebuildLockRef.current = false;
    };
    // 仅挂载一次；refresh 稳定引用内含最新闭包
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 构建中轮询；仅 ready 且 building（失败保值不清 index → 继续 poll）
  useEffect(() => {
    if (docStatus !== "ready" || !isSemanticIndexBuilding(semanticIndex)) {
      clearSemanticPoll();
      return;
    }
    if (semanticPollRef.current != null) return;
    const sgen = semanticGenRef.current;
    semanticPollRef.current = window.setInterval(() => {
      if (
        !mountedRef.current ||
        semanticGenRef.current !== sgen ||
        docStatusRef.current !== "ready"
      ) {
        clearSemanticPoll();
        return;
      }
      void refreshSemanticIndex();
    }, SEMANTIC_POLL_MS);
    return () => {
      clearSemanticPoll();
    };
  }, [docStatus, semanticIndex, refreshSemanticIndex, clearSemanticPoll]);

  /**
   * 用途：触发 POST /knowledge/semantic-index/rebuild；仅 docStatus=ready。
   * 非 ready 时零 POST。
   * 同步 rebuildLockRef：任何 await/setState 前抢占；同 tick 双 click 精确一 POST；
   * POST 成功返回 building 时同步抬高 owner，使所有 pre-rebuild GET 失效。
   */
  const rebuildSemanticIndex = useCallback(async () => {
    if (docStatusRef.current !== "ready") {
      return;
    }
    // 同步 ref 锁：先于任何 await / React setState
    if (rebuildLockRef.current) {
      return;
    }
    rebuildLockRef.current = true;
    const sgen = semanticGenRef.current;
    let isLockOwner = true;
    setSemanticBusy(true);
    setSemanticError(null);
    try {
      const raw = await apiFetch<unknown>("/knowledge/semantic-index/rebuild", {
        method: "POST",
      });
      if (
        !mountedRef.current ||
        semanticGenRef.current !== sgen ||
        docStatusRef.current !== "ready"
      ) {
        return;
      }
      // 成功：使所有 pre-rebuild GET owner 失效，再认领并写入 building
      const seq = ++semanticReqSeqRef.current;
      semanticOwnerSeqRef.current = seq;
      const row = normalizeSemanticIndex(raw);
      semanticIndexRef.current = row;
      setSemanticIndex(row);
      // POST 成功提交本代 building 时必须清旧 semanticError，
      // 避免 click 前失败 GET 在 hold 窗口写的旧错误与「构建中」并存。
      setSemanticError(null);
    } catch {
      if (
        !mountedRef.current ||
        semanticGenRef.current !== sgen ||
        docStatusRef.current !== "ready"
      ) {
        return;
      }
      await refreshSemanticIndex();
      if (
        !mountedRef.current ||
        semanticGenRef.current !== sgen ||
        docStatusRef.current !== "ready"
      ) {
        return;
      }
      // 认领 error 写入权（refresh 已可能更新 owner/index）
      const seq = ++semanticReqSeqRef.current;
      semanticOwnerSeqRef.current = seq;
      setSemanticError(SEMANTIC_REBUILD_FAILED_MSG);
    } finally {
      // 仅本锁 owner 解锁；busy 仅在仍本代且 ready 时清
      if (isLockOwner) {
        rebuildLockRef.current = false;
        isLockOwner = false;
      }
      if (
        mountedRef.current &&
        semanticGenRef.current === sgen &&
        docStatusRef.current === "ready"
      ) {
        setSemanticBusy(false);
      }
    }
  }, [refreshSemanticIndex]);

  /**
   * 尝试获取写锁；成功时立即冻结并返回 ownerGeneration，
   * 非 ready 或已有锁返回 null（零写）。
   */
  const tryAcquireWriteLock = useCallback((): number | null => {
    if (docStatusRef.current !== "ready") return null;
    if (writeLockRef.current) return null;
    writeLockRef.current = true;
    setBusy(true);
    // 抢到锁的瞬间冻结该动作 owner，禁止写结束后再读 generation
    return generationRef.current;
  }, []);

  const releaseWriteLock = useCallback(() => {
    writeLockRef.current = false;
    if (mountedRef.current) setBusy(false);
  }, []);

  const folderCounts = useMemo(() => {
    const map = new Map<string, number>();
    for (const d of docs) {
      map.set(d.folderId, (map.get(d.folderId) ?? 0) + 1);
    }
    return map;
  }, [docs]);

  const filteredDocs = useMemo(() => {
    const q = docQuery.trim().toLowerCase();
    return docs.filter((d) => {
      if (
        selectedFolderId !== KB_FOLDER_ALL &&
        d.folderId !== selectedFolderId
      ) {
        return false;
      }
      if (statusFilter !== "all" && d.status !== statusFilter) return false;
      if (!q) return true;
      // 禁止 statusMessage 参与搜索（隐私：服务端原文不得进检索出口）
      return (
        d.name.toLowerCase().includes(q) ||
        d.tags.some((t) => t.toLowerCase().includes(q)) ||
        d.category.toLowerCase().includes(q)
      );
    });
  }, [docs, docQuery, selectedFolderId, statusFilter]);

  const createFolder = useCallback(
    async (name: string) => {
      const trimmed = name.trim();
      if (!trimmed) return;
      const ownerGeneration = tryAcquireWriteLock();
      if (ownerGeneration === null) return;
      let opError: string | null = null;
      try {
        const raw = await apiFetch<unknown>("/knowledge/folders", {
          method: "POST",
          body: JSON.stringify({ name: trimmed }),
        });
        const folder = parseFolderObject(raw);
        if (!folder) {
          opError = CREATE_FOLDER_ERR;
        } else if (
          folder.parentId !== null &&
          !foldersRef.current.some((f) => f.id === folder.parentId)
        ) {
          opError = CREATE_FOLDER_ERR;
        }
      } catch {
        opError = CREATE_FOLDER_ERR;
      } finally {
        try {
          await reconcileAfterWrite(ownerGeneration, opError);
        } finally {
          // 最外层无条件释放写锁；stale 对账零 GET 也不得泄漏锁
          releaseWriteLock();
        }
      }
    },
    [tryAcquireWriteLock, releaseWriteLock, reconcileAfterWrite],
  );

  const moveDocs = useCallback(
    async (ids: string[], folderId: string) => {
      const uniqueIds = dedupeIdsPreserveOrder(ids);
      if (!uniqueIds.length) return;
      const ownerGeneration = tryAcquireWriteLock();
      if (ownerGeneration === null) return;
      let opError: string | null = null;
      try {
        const raw = await apiFetch<unknown>("/knowledge/docs/move", {
          method: "POST",
          body: JSON.stringify({ ids: uniqueIds, folderId }),
        });
        if (!isValidMovedCount(raw, uniqueIds.length)) {
          opError = MOVE_ERR;
        }
      } catch {
        opError = MOVE_ERR;
      } finally {
        try {
          await reconcileAfterWrite(ownerGeneration, opError);
        } finally {
          releaseWriteLock();
        }
      }
    },
    [tryAcquireWriteLock, releaseWriteLock, reconcileAfterWrite],
  );

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  }, []);

  const toggleSelectAllFiltered = useCallback(() => {
    setSelectedIds((prev) => {
      const ids = filteredDocs.map((d) => d.id);
      const allOn = ids.length > 0 && ids.every((id) => prev.includes(id));
      return allOn
        ? prev.filter((id) => !ids.includes(id))
        : [...new Set([...prev, ...ids])];
    });
  }, [filteredDocs]);

  const clearSelection = useCallback(() => setSelectedIds([]), []);

  const deleteDocs = useCallback(
    async (ids: string[]) => {
      const uniqueIds = dedupeIdsPreserveOrder(ids);
      if (!uniqueIds.length) return;
      const ownerGeneration = tryAcquireWriteLock();
      if (ownerGeneration === null) return;
      let opError: string | null = null;
      try {
        // 串行 DELETE；首败立即停止后续
        for (const id of uniqueIds) {
          try {
            await apiFetch(`/knowledge/docs/${encodeURIComponent(id)}`, {
              method: "DELETE",
            });
          } catch {
            opError = DELETE_ERR;
            break;
          }
        }
      } finally {
        try {
          await reconcileAfterWrite(ownerGeneration, opError);
        } finally {
          releaseWriteLock();
        }
      }
    },
    [tryAcquireWriteLock, releaseWriteLock, reconcileAfterWrite],
  );

  /**
   * 用途：重试解析/索引（POST reindex）；响应仅结构校验，列表以对账 GET 为准。
   */
  const retryParse = useCallback(
    async (ids: string[]) => {
      const uniqueIds = dedupeIdsPreserveOrder(ids);
      if (!uniqueIds.length) return;
      const ownerGeneration = tryAcquireWriteLock();
      if (ownerGeneration === null) return;
      let opError: string | null = null;
      try {
        const folderIds = new Set(foldersRef.current.map((f) => f.id));
        for (const id of uniqueIds) {
          try {
            const raw = await apiFetch<unknown>(
              `/knowledge/docs/${encodeURIComponent(id)}/reindex`,
              { method: "POST" },
            );
            if (!parseDocObject(raw, folderIds)) {
              opError = REINDEX_ERR;
              break;
            }
          } catch {
            opError = REINDEX_ERR;
            break;
          }
        }
      } finally {
        try {
          await reconcileAfterWrite(ownerGeneration, opError);
        } finally {
          releaseWriteLock();
        }
      }
    },
    [tryAcquireWriteLock, releaseWriteLock, reconcileAfterWrite],
  );

  /**
   * 用途：逐文件 multipart 上传；整批最后只对账一次。
   */
  const uploadFiles = useCallback(
    async (files: FileList | File[]) => {
      const list = Array.from(files);
      if (!list.length) return;
      const ownerGeneration = tryAcquireWriteLock();
      if (ownerGeneration === null) return;
      let opError: string | null = null;
      try {
        const folderId =
          selectedFolderId === KB_FOLDER_ALL
            ? foldersRef.current[0]?.id
            : selectedFolderId;
        const folderIds = new Set(foldersRef.current.map((f) => f.id));
        for (const file of list) {
          try {
            const raw = await uploadKbDoc(file, folderId);
            if (!parseDocObject(raw, folderIds)) {
              opError = UPLOAD_ERR;
              break;
            }
          } catch {
            opError = UPLOAD_ERR;
            break;
          }
        }
      } finally {
        try {
          await reconcileAfterWrite(ownerGeneration, opError);
        } finally {
          releaseWriteLock();
        }
      }
    },
    [
      selectedFolderId,
      tryAcquireWriteLock,
      releaseWriteLock,
      reconcileAfterWrite,
    ],
  );

  const semanticBuilding = isSemanticIndexBuilding(semanticIndex);
  const docsWritable = docStatus === "ready";

  return {
    folders,
    docs,
    folderCounts,
    filteredDocs,
    selectedFolderId,
    setSelectedFolderId,
    docQuery,
    setDocQuery,
    statusFilter,
    setStatusFilter,
    selectedIds,
    toggleSelect,
    toggleSelectAllFiltered,
    clearSelection,
    createFolder,
    moveDocs,
    deleteDocs,
    retryParse,
    uploadFiles,
    refresh,
    busy,
    error,
    setError,
    /** 文档主状态：loading|ready|error */
    docStatus,
    docsWritable,
    totalDocCount: docs.length,
    // P9C 语义索引面板
    semanticIndex,
    semanticError,
    semanticBusy,
    semanticBuilding,
    refreshSemanticIndex,
    rebuildSemanticIndex,
  };
}
