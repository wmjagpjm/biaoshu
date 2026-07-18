/**
 * 模块：P12C-C3 / P12D-B / P12E-A / P12E-C / P12F-C / P12F-D 双工作区共用修订历史折叠面板
 * 用途：默认折叠零请求；展开游标页；可选来源筛选；手动加载更多至最多 20 条；按需摘要；
 *       按需与当前对比；按需正文差异；内存双侧选择与双修订正文差异；内联二次确认后 restore。
 * 对接：editorStateRevisionApi（含 page/comparison/body-diff/pair）；技术/商务 hook 的 restoreRevision 回调。
 * 二次开发：
 *   - 不渲染 revisionId/stateVersion/cursor/snapshot 正文/内部字段键/字段值/op 原值
 *   - 项目切换/折叠/卸载用会话代次隔离迟到 page/load-more/detail/comparison/body-diff/pair/restore
 *   - 摘要、比较、正文差异、双修订差异、恢复确认同一时刻只保留一个当前意图；交叉作废
 *   - 固定中文脱敏；禁止 console/存储/URL/Cookie/剪贴板/下载/轮询/外网
 *   - 无创建/删除/搜索/自动分页/预取；双修订选择仅内存；游标仅内存 + 规定 API 查询
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  formatBodyDiffKindLabel,
  formatCanonicalFieldLabel,
  formatRevisionBytes,
  formatRevisionSourceLabel,
  formatRevisionTime,
  getEditorStateRevisionBodyDiff,
  getEditorStateRevisionComparison,
  getEditorStateRevisionPairBodyDiff,
  getEditorStateRevisionSummary,
  listEditorStateRevisionPage,
  MAX_RETAINED_REVISIONS,
  REVISION_SOURCE_KINDS,
  REVISION_SOURCE_LABELS,
  type BodyDiffOp,
  type EditorStateRevisionBodyDiff,
  type EditorStateRevisionComparison,
  type EditorStateRevisionMeta,
  type EditorStateRevisionPairBodyDiff,
  type EditorStateRevisionSummary,
  type RevisionSourceKind,
} from "./editorStateRevisionApi";

/** 恢复前内联确认固定文案（契约 §3） */
export const REVISION_RESTORE_CONFIRM_TEXT =
  "服务器当前内容会先保存为安全检查点，恢复替换技术标和商务标全部编辑态，尚未保存的本地修改不会写入。";

const MSG_LIST_FAIL = "修订历史加载失败，请稍后重试";
const MSG_LOAD_MORE_FAIL = "更多修订加载失败，请稍后重试";
const MSG_DETAIL_FAIL = "修订摘要加载失败，请稍后重试";
const MSG_COMPARE_FAIL = "修订差异加载失败，请稍后重试";
const MSG_COMPARE_SAME = "与当前版本一致";
const MSG_COMPARE_DIFF = "与当前版本存在差异";
const MSG_BODY_DIFF_FAIL = "正文差异加载失败，请稍后重试";
const MSG_BODY_DIFF_SAME = "章节正文无变化";
const MSG_BODY_DIFF_TRUNCATED = "差异内容较长，仅显示有界片段";
const MSG_PAIR_BODY_DIFF_FAIL = "双修订差异加载失败，请稍后重试";
const MSG_PAIR_BODY_DIFF_SAME = "两条修订正文一致";
const MSG_PAIR_BODY_DIFF_TRUNCATED = "差异内容较长，仅显示有界片段";
const MSG_RESTORE_OK = "已恢复到所选修订";
const MSG_RESTORE_FAIL = "恢复修订失败，本地内容已保留";
const MSG_RESTORE_RELOAD_FAIL =
  "恢复已完成，但刷新失败，请重新载入远端内容";
const MSG_RESTORE_BLOCKED = "当前无法恢复，请先处理版本冲突或重新载入";

/** 恢复回调结果（与版本化外部写 runner 对齐） */
export type RevisionRestoreOutcome =
  | { status: "success" }
  | { status: "reload_failed" }
  | { status: "post_failed" }
  | { status: "blocked" };

export type EditorStateRevisionPanelProps = {
  projectId: string;
  /**
   * 全状态阻断、初始加载失败、版本未知或 apiReady=false 时禁用恢复。
   * 列表/摘要/比较/正文差异/双修订差异只读仍可刷新（比较与正文差异不受 disabled 控制，但 restoreBusy 时禁用）。
   */
  disabled: boolean;
  /** 进入既有串行链 POST restore + 唯一 editor-state GET */
  restoreRevision: (revisionId: string) => Promise<RevisionRestoreOutcome>;
};

type ListItem = EditorStateRevisionMeta;

/**
 * 用途：渲染两侧六项摘要行（仅数字与固定中文，无内部键）。
 */
function renderSummaryLine(summary: EditorStateRevisionSummary): string {
  return [
    `大纲节点 ${summary.outlineNodeCount}`,
    `章节 ${summary.chapterCount}`,
    `事实 ${summary.factCount}`,
    `矩阵行 ${summary.responseMatrixRowCount}`,
    `商务条目 ${summary.businessEntryTotal}`,
    summary.hasParsedMarkdown ? "含解析正文" : "无解析正文",
  ].join(" · ");
}

/**
 * 用途：hunk op → 固定中文，不暴露 equal/delete/insert 原值。
 */
function formatHunkOpLabel(op: BodyDiffOp): string {
  if (op === "equal") return "保留";
  if (op === "delete") return "删除";
  return "新增";
}

export function EditorStateRevisionPanel({
  projectId,
  disabled,
  restoreRevision,
}: EditorStateRevisionPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [items, setItems] = useState<ListItem[]>([]);
  /**
   * 来源筛选：""=全部来源；其余为权威九类字面量。
   * 仅内存 + select 值；不写 URL/存储/Cookie。
   */
  const [sourceFilter, setSourceFilter] = useState<"" | RevisionSourceKind>("");
  /** 服务端 nextCursor；仅内存，不渲染 */
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [comparisonError, setComparisonError] = useState<string | null>(null);
  const [bodyDiffError, setBodyDiffError] = useState<string | null>(null);
  const [pairBodyDiffError, setPairBodyDiffError] = useState<string | null>(
    null,
  );
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [statusTone, setStatusTone] = useState<"ok" | "err" | null>(null);
  const [listLoading, setListLoading] = useState(false);
  const [loadMoreLoading, setLoadMoreLoading] = useState(false);
  /** 仅绑定当前在途详情 revision（允许挂起时点另一项/刷新/恢复） */
  const [detailLoadingId, setDetailLoadingId] = useState<string | null>(null);
  /** 仅绑定当前在途 comparison revision */
  const [comparisonLoadingId, setComparisonLoadingId] = useState<string | null>(
    null,
  );
  /** 仅绑定当前在途 body-diff revision */
  const [bodyDiffLoadingId, setBodyDiffLoadingId] = useState<string | null>(
    null,
  );
  /** 双修订比较是否在途（按钮文案；不暴露 ID） */
  const [pairBodyDiffLoading, setPairBodyDiffLoading] = useState(false);
  const [restoreBusy, setRestoreBusy] = useState(false);
  /** 进入确认态的修订 id（仅内存，不渲染） */
  const [pendingRestoreId, setPendingRestoreId] = useState<string | null>(null);
  /** 当前展开摘要的修订 id（仅内存） */
  const [summaryRevisionId, setSummaryRevisionId] = useState<string | null>(
    null,
  );
  const [summary, setSummary] = useState<EditorStateRevisionSummary | null>(
    null,
  );
  /** 当前展开比较的修订 id（仅内存） */
  const [comparisonRevisionId, setComparisonRevisionId] = useState<
    string | null
  >(null);
  const [comparison, setComparison] =
    useState<EditorStateRevisionComparison | null>(null);
  /** 当前展开正文差异的修订 id（仅内存） */
  const [bodyDiffRevisionId, setBodyDiffRevisionId] = useState<string | null>(
    null,
  );
  const [bodyDiff, setBodyDiff] =
    useState<EditorStateRevisionBodyDiff | null>(null);
  /** 双修订选择：仅内存保存 revisionId，禁止渲染到 DOM/URL/存储 */
  const [pairBeforeId, setPairBeforeId] = useState<string | null>(null);
  const [pairAfterId, setPairAfterId] = useState<string | null>(null);
  const [pairBodyDiff, setPairBodyDiff] =
    useState<EditorStateRevisionPairBodyDiff | null>(null);

  /**
   * 项目会话代次：projectId 变化或折叠时递增，隔离迟到 list/restore。
   */
  const sessionRef = useRef(0);
  /**
   * 详情请求代次：项目切换/折叠/刷新/另一项/再次点击/恢复/比较/正文差异/pair 均递增；
   * 旧 detail 的 try/catch/finally 不得写 summary/error/loading。
   */
  const detailGenRef = useRef(0);
  /**
   * 比较请求代次：与 detail/body-diff/pair 交叉作废；项目切换/折叠/刷新/恢复/列表重载/另一项均递增。
   */
  const comparisonGenRef = useRef(0);
  /**
   * 正文差异请求代次：与 detail/comparison/pair 交叉作废。
   */
  const bodyDiffGenRef = useRef(0);
  /**
   * 双修订正文差异请求代次：与 detail/comparison/body-diff 交叉作废；
   * 重选/清除/折叠/刷新/项目切换均递增。
   */
  const pairBodyDiffGenRef = useRef(0);
  /**
   * 加载更多请求代次：折叠/卸载/项目切换/首屏重载/恢复重载递增；
   * 旧 load-more 的 try/catch/finally 不得写 items/error/loading/cursor。
   */
  const loadMoreGenRef = useRef(0);
  /** 同步在途门：连续点击/双击不得产生第二个在途请求 */
  const loadMoreInFlightRef = useRef(false);
  /** items 同步镜像，供 load-more 合并校验（避免闭包过期） */
  const itemsRef = useRef<ListItem[]>([]);
  /** nextCursor 同步镜像 */
  const nextCursorRef = useRef<string | null>(null);
  /** sourceFilter 同步镜像，供 loadList/load-more 在 setState 后立即读取 */
  const sourceFilterRef = useRef<"" | RevisionSourceKind>("");
  const mountedRef = useRef(true);

  // 保持 ref 与 state 同步
  itemsRef.current = items;
  nextCursorRef.current = nextCursor;
  sourceFilterRef.current = sourceFilter;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const clearSummaryState = useCallback(() => {
    setSummaryRevisionId(null);
    setSummary(null);
    setDetailError(null);
  }, []);

  const clearComparisonState = useCallback(() => {
    setComparisonRevisionId(null);
    setComparison(null);
    setComparisonError(null);
    setComparisonLoadingId(null);
  }, []);

  const clearBodyDiffState = useCallback(() => {
    setBodyDiffRevisionId(null);
    setBodyDiff(null);
    setBodyDiffError(null);
    setBodyDiffLoadingId(null);
  }, []);

  const clearPairBodyDiffState = useCallback(() => {
    setPairBeforeId(null);
    setPairAfterId(null);
    setPairBodyDiff(null);
    setPairBodyDiffError(null);
    setPairBodyDiffLoading(false);
  }, []);

  const invalidateDetail = useCallback(() => {
    detailGenRef.current += 1;
    setDetailLoadingId(null);
  }, []);

  const invalidateComparison = useCallback(() => {
    comparisonGenRef.current += 1;
    setComparisonLoadingId(null);
  }, []);

  const invalidateBodyDiff = useCallback(() => {
    bodyDiffGenRef.current += 1;
    setBodyDiffLoadingId(null);
  }, []);

  const invalidatePairBodyDiff = useCallback(() => {
    pairBodyDiffGenRef.current += 1;
    setPairBodyDiffLoading(false);
  }, []);

  // 项目切换：重置面板（含筛选→全部来源），作废在途 page/load-more/detail/comparison/body-diff/pair/restore
  useEffect(() => {
    sessionRef.current += 1;
    detailGenRef.current += 1;
    comparisonGenRef.current += 1;
    bodyDiffGenRef.current += 1;
    pairBodyDiffGenRef.current += 1;
    loadMoreGenRef.current += 1;
    loadMoreInFlightRef.current = false;
    setExpanded(false);
    setItems([]);
    setSourceFilter("");
    sourceFilterRef.current = "";
    setNextCursor(null);
    setListError(null);
    setLoadMoreError(null);
    setDetailError(null);
    setComparisonError(null);
    setBodyDiffError(null);
    setPairBodyDiffError(null);
    setStatusMessage(null);
    setStatusTone(null);
    setListLoading(false);
    setLoadMoreLoading(false);
    setDetailLoadingId(null);
    setComparisonLoadingId(null);
    setBodyDiffLoadingId(null);
    setPairBodyDiffLoading(false);
    setRestoreBusy(false);
    setPendingRestoreId(null);
    setSummaryRevisionId(null);
    setSummary(null);
    setComparisonRevisionId(null);
    setComparison(null);
    setBodyDiffRevisionId(null);
    setBodyDiff(null);
    setPairBeforeId(null);
    setPairAfterId(null);
    setPairBodyDiff(null);
  }, [projectId]);

  const loadList = useCallback(
    async (session: number) => {
      if (!projectId) return;
      // 刷新/重载第一页：作废在途 detail/comparison/body-diff/pair/load-more
      detailGenRef.current += 1;
      comparisonGenRef.current += 1;
      bodyDiffGenRef.current += 1;
      pairBodyDiffGenRef.current += 1;
      loadMoreGenRef.current += 1;
      loadMoreInFlightRef.current = false;
      setDetailLoadingId(null);
      setComparisonLoadingId(null);
      setBodyDiffLoadingId(null);
      setPairBodyDiffLoading(false);
      setLoadMoreLoading(false);
      setLoadMoreError(null);
      setNextCursor(null);
      setListLoading(true);
      setListError(null);
      setPendingRestoreId(null);
      clearSummaryState();
      clearComparisonState();
      clearBodyDiffState();
      clearPairBodyDiffState();
      // 绑定当前筛选（ref 可在 setState 后立即读取）
      const filter = sourceFilterRef.current;
      try {
        // 首屏：全部来源无 query；有筛选仅 sourceKind
        const page = await listEditorStateRevisionPage(
          projectId,
          filter
            ? { sourceKind: filter }
            : undefined,
        );
        if (!mountedRef.current || session !== sessionRef.current) return;
        // 筛选在请求期间被切换时，以 ref 为准再校验一次
        if (sourceFilterRef.current !== filter) return;
        setItems(page.items);
        setNextCursor(page.nextCursor);
      } catch {
        if (!mountedRef.current || session !== sessionRef.current) return;
        if (sourceFilterRef.current !== filter) return;
        setListError(MSG_LIST_FAIL);
        setItems([]);
        setNextCursor(null);
      } finally {
        if (
          mountedRef.current &&
          session === sessionRef.current &&
          sourceFilterRef.current === filter
        ) {
          setListLoading(false);
        }
      }
    },
    [
      projectId,
      clearSummaryState,
      clearComparisonState,
      clearBodyDiffState,
      clearPairBodyDiffState,
    ],
  );

  /**
   * 用途：手动加载更多；同步在途门 + 代次；成功顺序追加；失败保值可重试。
   * 筛选第二页：当前 sourceKind + 服务端原 esrc2。
   */
  const handleLoadMore = useCallback(async () => {
    if (!expanded || !projectId) return;
    if (listLoading || restoreBusy || loadMoreLoading) return;
    const cursor = nextCursorRef.current;
    if (!cursor) return;
    // 同步单飞：双击/连点不得产生第二请求
    if (loadMoreInFlightRef.current) return;
    loadMoreInFlightRef.current = true;
    const myGen = ++loadMoreGenRef.current;
    const session = sessionRef.current;
    const filter = sourceFilterRef.current;
    setLoadMoreLoading(true);
    setLoadMoreError(null);
    try {
      const page = await listEditorStateRevisionPage(
        projectId,
        filter
          ? { cursor, sourceKind: filter }
          : { cursor },
      );
      if (
        !mountedRef.current ||
        myGen !== loadMoreGenRef.current ||
        session !== sessionRef.current ||
        sourceFilterRef.current !== filter
      ) {
        return;
      }
      const prev = itemsRef.current;
      const existingIds = new Set(prev.map((it) => it.revisionId));
      for (const it of page.items) {
        if (existingIds.has(it.revisionId)) {
          throw new Error("revision_load_more_duplicate");
        }
        existingIds.add(it.revisionId);
      }
      const merged = [...prev, ...page.items];
      // 超过保留上限，或满 20 仍带第三页游标：固定失败
      if (merged.length > MAX_RETAINED_REVISIONS) {
        throw new Error("revision_load_more_over_cap");
      }
      if (merged.length === MAX_RETAINED_REVISIONS && page.nextCursor != null) {
        throw new Error("revision_load_more_third_page");
      }
      setItems(merged);
      setNextCursor(page.nextCursor);
      setLoadMoreError(null);
    } catch {
      if (
        !mountedRef.current ||
        myGen !== loadMoreGenRef.current ||
        session !== sessionRef.current ||
        sourceFilterRef.current !== filter
      ) {
        return;
      }
      // 失败：保留原 items 与原 cursor，固定错误，可同 cursor 重试
      setLoadMoreError(MSG_LOAD_MORE_FAIL);
    } finally {
      if (myGen === loadMoreGenRef.current) {
        loadMoreInFlightRef.current = false;
      }
      if (
        mountedRef.current &&
        myGen === loadMoreGenRef.current &&
        session === sessionRef.current &&
        sourceFilterRef.current === filter
      ) {
        setLoadMoreLoading(false);
      }
    }
  }, [expanded, projectId, listLoading, restoreBusy, loadMoreLoading]);

  /**
   * 用途：切换来源筛选；同值不重发；立即清空旧列表/意图并加载新第一页。
   */
  const handleSourceFilterChange = useCallback(
    (raw: string) => {
      if (!expanded || listLoading || restoreBusy || loadMoreLoading) return;
      // 同值不重发
      if (raw === sourceFilter) return;
      let next: "" | RevisionSourceKind = "";
      if (raw === "") {
        next = "";
      } else if (
        (REVISION_SOURCE_KINDS as readonly string[]).includes(raw)
      ) {
        next = raw as RevisionSourceKind;
      } else {
        return;
      }
      // 同步写入 ref，保证随后 loadList 读到新筛选
      sourceFilterRef.current = next;
      setSourceFilter(next);
      // 切换筛选：清空旧列表与意图，失败不回退旧结果
      setItems([]);
      setNextCursor(null);
      setListError(null);
      setLoadMoreError(null);
      setStatusMessage(null);
      setStatusTone(null);
      const session = sessionRef.current;
      void loadList(session);
    },
    [
      expanded,
      listLoading,
      restoreBusy,
      loadMoreLoading,
      sourceFilter,
      loadList,
    ],
  );

  const handleToggle = useCallback(() => {
    if (expanded) {
      sessionRef.current += 1;
      detailGenRef.current += 1;
      comparisonGenRef.current += 1;
      bodyDiffGenRef.current += 1;
      pairBodyDiffGenRef.current += 1;
      loadMoreGenRef.current += 1;
      loadMoreInFlightRef.current = false;
      setExpanded(false);
      setListLoading(false);
      setLoadMoreLoading(false);
      setLoadMoreError(null);
      setNextCursor(null);
      setDetailLoadingId(null);
      setComparisonLoadingId(null);
      setBodyDiffLoadingId(null);
      setPairBodyDiffLoading(false);
      setRestoreBusy(false);
      setPendingRestoreId(null);
      clearSummaryState();
      clearComparisonState();
      clearBodyDiffState();
      clearPairBodyDiffState();
      return;
    }
    const session = sessionRef.current;
    setExpanded(true);
    setStatusMessage(null);
    setStatusTone(null);
    setPendingRestoreId(null);
    setLoadMoreError(null);
    clearSummaryState();
    clearComparisonState();
    clearBodyDiffState();
    clearPairBodyDiffState();
    void loadList(session);
  }, [
    expanded,
    loadList,
    clearSummaryState,
    clearComparisonState,
    clearBodyDiffState,
    clearPairBodyDiffState,
  ]);

  const handleRefresh = useCallback(() => {
    // 允许详情/比较/正文差异挂起时刷新：loadList 会递增代次作废旧结果
    // 加载更多在途时禁用刷新，避免列表替换与追加并发
    if (!expanded || listLoading || restoreBusy || loadMoreLoading) return;
    const session = sessionRef.current;
    setStatusMessage(null);
    setStatusTone(null);
    void loadList(session);
  }, [expanded, listLoading, restoreBusy, loadMoreLoading, loadList]);

  const handleSummaryClick = useCallback(
    async (item: ListItem) => {
      if (!expanded || restoreBusy) return;
      // 点击摘要：作废在途比较/正文差异/pair 并清除其结果
      invalidateComparison();
      clearComparisonState();
      invalidateBodyDiff();
      clearBodyDiffState();
      invalidatePairBodyDiff();
      clearPairBodyDiffState();
      // 再次点击同一项：清空摘要并作废在途
      if (summaryRevisionId === item.revisionId) {
        detailGenRef.current += 1;
        setDetailLoadingId(null);
        setSummaryRevisionId(null);
        setSummary(null);
        setDetailError(null);
        setPendingRestoreId(null);
        return;
      }
      // 独立详情代次：可在 A 挂起时点 B；旧 finally 不得清新请求
      const myGen = ++detailGenRef.current;
      setSummaryRevisionId(item.revisionId);
      setSummary(null);
      setDetailError(null);
      setPendingRestoreId(null);
      setStatusMessage(null);
      setStatusTone(null);
      setDetailLoadingId(item.revisionId);
      try {
        const next = await getEditorStateRevisionSummary(projectId, item);
        if (!mountedRef.current || myGen !== detailGenRef.current) return;
        setSummary(next);
      } catch {
        if (!mountedRef.current || myGen !== detailGenRef.current) return;
        setSummary(null);
        setDetailError(MSG_DETAIL_FAIL);
      } finally {
        if (mountedRef.current && myGen === detailGenRef.current) {
          setDetailLoadingId(null);
        }
      }
    },
    [
      expanded,
      restoreBusy,
      summaryRevisionId,
      projectId,
      invalidateComparison,
      clearComparisonState,
      invalidateBodyDiff,
      clearBodyDiffState,
      invalidatePairBodyDiff,
      clearPairBodyDiffState,
    ],
  );

  const handleComparisonClick = useCallback(
    async (item: ListItem) => {
      // 比较不受 disabled 控制，但恢复执行期间禁用
      if (!expanded || restoreBusy) return;
      // 点击比较：作废在途摘要/正文差异/pair、清除其结果与恢复确认
      invalidateDetail();
      clearSummaryState();
      invalidateBodyDiff();
      clearBodyDiffState();
      invalidatePairBodyDiff();
      clearPairBodyDiffState();
      setPendingRestoreId(null);
      setStatusMessage(null);
      setStatusTone(null);
      // 再次点击同一项：关闭结果并作废在途
      if (comparisonRevisionId === item.revisionId) {
        comparisonGenRef.current += 1;
        setComparisonLoadingId(null);
        setComparisonRevisionId(null);
        setComparison(null);
        setComparisonError(null);
        return;
      }
      const myGen = ++comparisonGenRef.current;
      const session = sessionRef.current;
      setComparisonRevisionId(item.revisionId);
      setComparison(null);
      setComparisonError(null);
      setComparisonLoadingId(item.revisionId);
      try {
        const next = await getEditorStateRevisionComparison(
          projectId,
          item.revisionId,
        );
        if (
          !mountedRef.current ||
          myGen !== comparisonGenRef.current ||
          session !== sessionRef.current
        ) {
          return;
        }
        setComparison(next);
      } catch {
        if (
          !mountedRef.current ||
          myGen !== comparisonGenRef.current ||
          session !== sessionRef.current
        ) {
          return;
        }
        setComparison(null);
        setComparisonError(MSG_COMPARE_FAIL);
      } finally {
        if (
          mountedRef.current &&
          myGen === comparisonGenRef.current &&
          session === sessionRef.current
        ) {
          setComparisonLoadingId(null);
        }
      }
    },
    [
      expanded,
      restoreBusy,
      comparisonRevisionId,
      projectId,
      invalidateDetail,
      clearSummaryState,
      invalidateBodyDiff,
      clearBodyDiffState,
      invalidatePairBodyDiff,
      clearPairBodyDiffState,
    ],
  );

  const handleBodyDiffClick = useCallback(
    async (item: ListItem) => {
      // 正文差异不受 disabled 控制，但恢复执行期间禁用
      if (!expanded || restoreBusy) return;
      // 点击正文差异：作废在途摘要/比较/pair、清除其结果与恢复确认
      invalidateDetail();
      clearSummaryState();
      invalidateComparison();
      clearComparisonState();
      invalidatePairBodyDiff();
      clearPairBodyDiffState();
      setPendingRestoreId(null);
      setStatusMessage(null);
      setStatusTone(null);
      // 再次点击同一项：关闭结果并作废在途
      if (bodyDiffRevisionId === item.revisionId) {
        bodyDiffGenRef.current += 1;
        setBodyDiffLoadingId(null);
        setBodyDiffRevisionId(null);
        setBodyDiff(null);
        setBodyDiffError(null);
        return;
      }
      const myGen = ++bodyDiffGenRef.current;
      const session = sessionRef.current;
      setBodyDiffRevisionId(item.revisionId);
      setBodyDiff(null);
      setBodyDiffError(null);
      setBodyDiffLoadingId(item.revisionId);
      try {
        const next = await getEditorStateRevisionBodyDiff(
          projectId,
          item.revisionId,
        );
        if (
          !mountedRef.current ||
          myGen !== bodyDiffGenRef.current ||
          session !== sessionRef.current
        ) {
          return;
        }
        setBodyDiff(next);
      } catch {
        if (
          !mountedRef.current ||
          myGen !== bodyDiffGenRef.current ||
          session !== sessionRef.current
        ) {
          return;
        }
        setBodyDiff(null);
        setBodyDiffError(MSG_BODY_DIFF_FAIL);
      } finally {
        if (
          mountedRef.current &&
          myGen === bodyDiffGenRef.current &&
          session === sessionRef.current
        ) {
          setBodyDiffLoadingId(null);
        }
      }
    },
    [
      expanded,
      restoreBusy,
      bodyDiffRevisionId,
      projectId,
      invalidateDetail,
      clearSummaryState,
      invalidateComparison,
      clearComparisonState,
      invalidatePairBodyDiff,
      clearPairBodyDiffState,
    ],
  );

  /**
   * 用途：选为差异前；仅内存；同项不得同时为后侧；选择本身不发请求。
   */
  const handlePairSelectBefore = useCallback(
    (revisionId: string) => {
      if (!expanded || restoreBusy) return;
      // 重选作废在途 pair，并清除旧结果
      pairBodyDiffGenRef.current += 1;
      setPairBodyDiff(null);
      setPairBodyDiffError(null);
      setPairBodyDiffLoading(false);
      setPairBeforeId(revisionId);
      // 同一项不得同时承担两侧
      setPairAfterId((prev) => (prev === revisionId ? null : prev));
    },
    [expanded, restoreBusy],
  );

  /**
   * 用途：选为差异后；仅内存；同项不得同时为前侧；选择本身不发请求。
   */
  const handlePairSelectAfter = useCallback(
    (revisionId: string) => {
      if (!expanded || restoreBusy) return;
      pairBodyDiffGenRef.current += 1;
      setPairBodyDiff(null);
      setPairBodyDiffError(null);
      setPairBodyDiffLoading(false);
      setPairAfterId(revisionId);
      setPairBeforeId((prev) => (prev === revisionId ? null : prev));
    },
    [expanded, restoreBusy],
  );

  /**
   * 用途：清除双侧选择与结果；只动内存，不发请求。
   */
  const handlePairClear = useCallback(() => {
    if (!expanded || restoreBusy) return;
    pairBodyDiffGenRef.current += 1;
    clearPairBodyDiffState();
  }, [expanded, restoreBusy, clearPairBodyDiffState]);

  /**
   * 用途：比较两条已选修订；精确一次 pair GET；与摘要/当前对比/单修订正文差异/恢复互斥。
   */
  const handlePairCompare = useCallback(async () => {
    if (!expanded || restoreBusy) return;
    if (!pairBeforeId || !pairAfterId || pairBeforeId === pairAfterId) return;
    // 启动 pair：作废其它意图结果
    invalidateDetail();
    clearSummaryState();
    invalidateComparison();
    clearComparisonState();
    invalidateBodyDiff();
    clearBodyDiffState();
    setPendingRestoreId(null);
    setStatusMessage(null);
    setStatusTone(null);

    const myGen = ++pairBodyDiffGenRef.current;
    const session = sessionRef.current;
    const beforeId = pairBeforeId;
    const afterId = pairAfterId;
    setPairBodyDiff(null);
    setPairBodyDiffError(null);
    setPairBodyDiffLoading(true);
    try {
      const next = await getEditorStateRevisionPairBodyDiff(
        projectId,
        beforeId,
        afterId,
      );
      if (
        !mountedRef.current ||
        myGen !== pairBodyDiffGenRef.current ||
        session !== sessionRef.current
      ) {
        return;
      }
      setPairBodyDiff(next);
    } catch {
      if (
        !mountedRef.current ||
        myGen !== pairBodyDiffGenRef.current ||
        session !== sessionRef.current
      ) {
        return;
      }
      setPairBodyDiff(null);
      setPairBodyDiffError(MSG_PAIR_BODY_DIFF_FAIL);
    } finally {
      if (
        mountedRef.current &&
        myGen === pairBodyDiffGenRef.current &&
        session === sessionRef.current
      ) {
        setPairBodyDiffLoading(false);
      }
    }
  }, [
    expanded,
    restoreBusy,
    pairBeforeId,
    pairAfterId,
    projectId,
    invalidateDetail,
    clearSummaryState,
    invalidateComparison,
    clearComparisonState,
    invalidateBodyDiff,
    clearBodyDiffState,
  ]);

  const handleRestoreClick = useCallback(
    (revisionId: string) => {
      if (disabled || restoreBusy || loadMoreLoading) return;
      // 恢复：立即清摘要/比较/正文差异/pair/detail error 并作废在途
      invalidateDetail();
      invalidateComparison();
      invalidateBodyDiff();
      invalidatePairBodyDiff();
      clearSummaryState();
      clearComparisonState();
      clearBodyDiffState();
      clearPairBodyDiffState();
      setPendingRestoreId(revisionId);
      setStatusMessage(null);
      setStatusTone(null);
    },
    [
      disabled,
      restoreBusy,
      loadMoreLoading,
      invalidateDetail,
      invalidateComparison,
      invalidateBodyDiff,
      invalidatePairBodyDiff,
      clearSummaryState,
      clearComparisonState,
      clearBodyDiffState,
      clearPairBodyDiffState,
    ],
  );

  const handleConfirmRestore = useCallback(async () => {
    if (
      disabled ||
      restoreBusy ||
      loadMoreLoading ||
      !pendingRestoreId ||
      !expanded
    ) {
      return;
    }
    const session = sessionRef.current;
    const revisionId = pendingRestoreId;
    // 确认恢复：作废在途 detail/comparison/body-diff/pair/load-more，清摘要/比较/正文差异/确认相关态
    invalidateDetail();
    invalidateComparison();
    invalidateBodyDiff();
    invalidatePairBodyDiff();
    loadMoreGenRef.current += 1;
    loadMoreInFlightRef.current = false;
    setLoadMoreLoading(false);
    setLoadMoreError(null);
    clearSummaryState();
    clearComparisonState();
    clearBodyDiffState();
    clearPairBodyDiffState();
    setRestoreBusy(true);
    setStatusMessage(null);
    setStatusTone(null);
    try {
      const outcome = await restoreRevision(revisionId);
      if (!mountedRef.current || session !== sessionRef.current) return;
      setPendingRestoreId(null);
      clearSummaryState();
      clearComparisonState();
      clearBodyDiffState();
      clearPairBodyDiffState();
      if (outcome.status === "success") {
        setStatusMessage(MSG_RESTORE_OK);
        setStatusTone("ok");
        await loadList(session);
        return;
      }
      if (outcome.status === "reload_failed") {
        setStatusMessage(MSG_RESTORE_RELOAD_FAIL);
        setStatusTone("err");
        await loadList(session);
        return;
      }
      if (outcome.status === "blocked") {
        setStatusMessage(MSG_RESTORE_BLOCKED);
        setStatusTone("err");
        return;
      }
      setStatusMessage(MSG_RESTORE_FAIL);
      setStatusTone("err");
    } catch {
      if (!mountedRef.current || session !== sessionRef.current) return;
      setPendingRestoreId(null);
      clearSummaryState();
      clearComparisonState();
      clearBodyDiffState();
      clearPairBodyDiffState();
      setStatusMessage(MSG_RESTORE_FAIL);
      setStatusTone("err");
    } finally {
      if (mountedRef.current && session === sessionRef.current) {
        setRestoreBusy(false);
      }
    }
  }, [
    disabled,
    restoreBusy,
    loadMoreLoading,
    pendingRestoreId,
    expanded,
    restoreRevision,
    loadList,
    invalidateDetail,
    invalidateComparison,
    invalidateBodyDiff,
    invalidatePairBodyDiff,
    clearSummaryState,
    clearComparisonState,
    clearBodyDiffState,
    clearPairBodyDiffState,
  ]);

  const handleCancelRestore = useCallback(() => {
    if (restoreBusy) return;
    setPendingRestoreId(null);
  }, [restoreBusy]);

  // 恢复：全状态阻断 / 首屏加载 / 加载更多在途时禁用
  const restoreDisabled =
    disabled || restoreBusy || listLoading || loadMoreLoading;
  /** 比较/正文差异/pair 不受 disabled 控制，仅 restoreBusy 期间禁用 */
  const compareDisabled = restoreBusy;
  const bodyDiffDisabled = restoreBusy;
  const pairSelectDisabled = restoreBusy;
  const pairCompareReady =
    !!pairBeforeId &&
    !!pairAfterId &&
    pairBeforeId !== pairAfterId &&
    !restoreBusy &&
    !pairBodyDiffLoading;
  const showLoadMore = nextCursor != null;
  const loadMoreDisabled =
    loadMoreLoading || listLoading || restoreBusy || !nextCursor;
  /** 列表/加载更多/恢复在途时禁用来源筛选器 */
  const sourceFilterDisabled =
    listLoading || loadMoreLoading || restoreBusy;

  return (
    <div
      data-testid="editor-state-revision-panel"
      style={{
        marginTop: 10,
        padding: "10px 12px",
        borderRadius: 8,
        border: "1px solid var(--border, #e5e7eb)",
        background: "var(--surface-soft, #fafafa)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          data-testid="editor-state-revision-toggle"
          aria-expanded={expanded}
          onClick={handleToggle}
        >
          {expanded ? "收起修订历史" : "修订历史"}
        </button>
        {expanded ? (
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              data-testid="editor-state-revision-refresh"
              disabled={listLoading || restoreBusy || loadMoreLoading}
              onClick={handleRefresh}
            >
              刷新
            </button>
          </div>
        ) : null}
      </div>

      {expanded ? (
        <div
          data-testid="editor-state-revision-body"
          style={{ marginTop: 10 }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 8,
              flexWrap: "wrap",
            }}
          >
            <label
              htmlFor="editor-state-revision-source-filter"
              style={{
                fontSize: 13,
                color: "var(--text-muted, #4b5563)",
              }}
            >
              来源
            </label>
            <select
              id="editor-state-revision-source-filter"
              data-testid="editor-state-revision-source-filter"
              value={sourceFilter}
              disabled={sourceFilterDisabled}
              onChange={(e) => {
                handleSourceFilterChange(e.target.value);
              }}
              style={{
                fontSize: 13,
                maxWidth: "100%",
                padding: "4px 8px",
                borderRadius: 6,
                border: "1px solid var(--border, #e5e7eb)",
                background: "var(--surface, #fff)",
                color: "var(--text, #111827)",
              }}
            >
              <option value="">全部来源</option>
              {REVISION_SOURCE_KINDS.map((kind) => (
                <option key={kind} value={kind}>
                  {REVISION_SOURCE_LABELS[kind]}
                </option>
              ))}
            </select>
          </div>
          {statusMessage ? (
            <p
              data-testid="editor-state-revision-status"
              style={{
                margin: "0 0 8px",
                color:
                  statusTone === "err"
                    ? "var(--danger)"
                    : "var(--text-muted, #4b5563)",
              }}
            >
              {statusMessage}
            </p>
          ) : null}
          {listError ? (
            <p
              data-testid="editor-state-revision-list-error"
              style={{ margin: "0 0 8px", color: "var(--danger)" }}
            >
              {listError}
            </p>
          ) : null}
          {loadMoreError ? (
            <p
              data-testid="editor-state-revision-load-more-error"
              style={{ margin: "0 0 8px", color: "var(--danger)" }}
            >
              {loadMoreError}
            </p>
          ) : null}
          {detailError ? (
            <p
              data-testid="editor-state-revision-detail-error"
              style={{ margin: "0 0 8px", color: "var(--danger)" }}
            >
              {detailError}
            </p>
          ) : null}
          {comparisonError ? (
            <p
              data-testid="editor-state-revision-comparison-error"
              style={{ margin: "0 0 8px", color: "var(--danger)" }}
            >
              {comparisonError}
            </p>
          ) : null}
          {bodyDiffError ? (
            <p
              data-testid="editor-state-revision-body-diff-error"
              style={{ margin: "0 0 8px", color: "var(--danger)" }}
            >
              {bodyDiffError}
            </p>
          ) : null}
          {pairBodyDiffError ? (
            <p
              data-testid="editor-state-revision-pair-error"
              style={{ margin: "0 0 8px", color: "var(--danger)" }}
            >
              {pairBodyDiffError}
            </p>
          ) : null}
          {listLoading && items.length === 0 ? (
            <p
              data-testid="editor-state-revision-list-loading"
              style={{ margin: 0, color: "var(--text-muted, #6b7280)" }}
            >
              加载修订历史…
            </p>
          ) : null}
          {!listLoading && !listError && items.length === 0 ? (
            <p
              data-testid="editor-state-revision-empty"
              style={{ margin: 0, color: "var(--text-muted, #6b7280)" }}
            >
              暂无修订记录
            </p>
          ) : null}
          {items.length > 0 ? (
            <div
              data-testid="editor-state-revision-pair-controls"
              style={{
                marginTop: 8,
                display: "flex",
                gap: 8,
                flexWrap: "wrap",
                alignItems: "center",
              }}
            >
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                data-testid="editor-state-revision-pair-compare"
                disabled={!pairCompareReady}
                onClick={() => {
                  void handlePairCompare();
                }}
              >
                {pairBodyDiffLoading ? "正在比较…" : "比较两条修订"}
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                data-testid="editor-state-revision-pair-clear"
                disabled={
                  restoreBusy ||
                  (!pairBeforeId &&
                    !pairAfterId &&
                    !pairBodyDiff &&
                    !pairBodyDiffError)
                }
                onClick={handlePairClear}
              >
                清除选择
              </button>
            </div>
          ) : null}
          {pairBodyDiff ? (
            <div
              data-testid="editor-state-revision-pair-result"
              style={{
                marginTop: 8,
                fontSize: 13,
                color: "var(--text-muted, #4b5563)",
              }}
            >
              <p
                style={{ margin: "0 0 6px", fontWeight: 600 }}
                data-testid="editor-state-revision-pair-labels"
              >
                差异前修订 → 差异后修订
              </p>
              <p
                data-testid="editor-state-revision-pair-meta"
                style={{ margin: "0 0 6px" }}
              >
                {`差异前章节 ${pairBodyDiff.beforeChapterCount} · 差异后章节 ${pairBodyDiff.afterChapterCount}`}
              </p>
              <p
                data-testid="editor-state-revision-pair-status"
                style={{ margin: "0 0 6px", fontWeight: 600 }}
              >
                {pairBodyDiff.sameBody
                  ? MSG_PAIR_BODY_DIFF_SAME
                  : `共 ${pairBodyDiff.changedChapterCount} 章正文有变化`}
              </p>
              {pairBodyDiff.truncated ? (
                <p
                  data-testid="editor-state-revision-pair-truncated"
                  style={{ margin: "0 0 6px" }}
                >
                  {MSG_PAIR_BODY_DIFF_TRUNCATED}
                </p>
              ) : null}
              {!pairBodyDiff.sameBody
                ? pairBodyDiff.items.map((diffItem) => (
                    <div
                      key={diffItem.ordinal}
                      data-testid={`editor-state-revision-pair-item-${diffItem.ordinal}`}
                      style={{
                        marginBottom: 8,
                        padding: "6px 8px",
                        borderRadius: 4,
                        border: "1px solid var(--border, #e5e7eb)",
                      }}
                    >
                      {formatBodyDiffKindLabel(diffItem.kind) ? (
                        <p
                          style={{ margin: "0 0 4px", fontWeight: 600 }}
                          data-testid={`editor-state-revision-pair-item-kind-${diffItem.ordinal}`}
                        >
                          {formatBodyDiffKindLabel(diffItem.kind)}
                        </p>
                      ) : null}
                      <p
                        style={{ margin: "0 0 4px" }}
                        data-testid={`editor-state-revision-pair-item-titles-${diffItem.ordinal}`}
                      >
                        {diffItem.beforeTitle
                          ? `差异前：${diffItem.beforeTitle}`
                          : "差异前：（无标题）"}
                        {" → "}
                        {diffItem.afterTitle
                          ? `差异后：${diffItem.afterTitle}`
                          : "差异后：（无标题）"}
                      </p>
                      <ul
                        style={{
                          listStyle: "none",
                          margin: 0,
                          padding: 0,
                        }}
                      >
                        {diffItem.hunks.map((hunk, hIdx) => (
                          <li
                            key={hIdx}
                            data-testid={`editor-state-revision-pair-hunk-${diffItem.ordinal}-${hIdx}`}
                            style={{
                              marginTop: 4,
                              whiteSpace: "pre-wrap",
                              wordBreak: "break-word",
                            }}
                          >
                            <span
                              data-testid={`editor-state-revision-pair-hunk-op-${diffItem.ordinal}-${hIdx}`}
                            >
                              {formatHunkOpLabel(hunk.op)}
                            </span>
                            {": "}
                            <span
                              data-testid={`editor-state-revision-pair-hunk-text-${diffItem.ordinal}-${hIdx}`}
                            >
                              {hunk.text}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))
                : null}
            </div>
          ) : null}
          <ul
            data-testid="editor-state-revision-list"
            style={{
              listStyle: "none",
              margin: items.length ? "8px 0 0" : 0,
              padding: 0,
              display: "flex",
              flexDirection: "column",
              gap: 8,
            }}
          >
            {items.map((item, index) => {
              const confirming = pendingRestoreId === item.revisionId;
              const showingSummary =
                summaryRevisionId === item.revisionId && summary != null;
              const showingComparison =
                comparisonRevisionId === item.revisionId && comparison != null;
              const showingBodyDiff =
                bodyDiffRevisionId === item.revisionId && bodyDiff != null;
              const isPairBefore = pairBeforeId === item.revisionId;
              const isPairAfter = pairAfterId === item.revisionId;
              return (
                <li
                  key={item.revisionId}
                  data-testid={`editor-state-revision-item-${index}`}
                  style={{
                    padding: "8px 10px",
                    borderRadius: 6,
                    border: "1px solid var(--border, #e5e7eb)",
                    background: "var(--surface, #fff)",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      flexWrap: "wrap",
                      gap: "6px 14px",
                      fontSize: 13,
                      color: "var(--text, #111827)",
                    }}
                  >
                    <span data-testid={`editor-state-revision-time-${index}`}>
                      {formatRevisionTime(item.createdAt)}
                    </span>
                    <span data-testid={`editor-state-revision-source-${index}`}>
                      {formatRevisionSourceLabel(item.sourceKind)}
                    </span>
                    <span>{formatRevisionBytes(item.snapshotBytes)}</span>
                  </div>
                  {showingSummary ? (
                    <div
                      data-testid={`editor-state-revision-summary-body-${index}`}
                      style={{
                        marginTop: 8,
                        fontSize: 13,
                        color: "var(--text-muted, #4b5563)",
                      }}
                    >
                      <span>大纲节点 {summary.outlineNodeCount}</span>
                      {" · "}
                      <span>章节 {summary.chapterCount}</span>
                      {" · "}
                      <span>事实 {summary.factCount}</span>
                      {" · "}
                      <span>矩阵行 {summary.responseMatrixRowCount}</span>
                      {" · "}
                      <span>商务条目 {summary.businessEntryTotal}</span>
                      {" · "}
                      <span>
                        {summary.hasParsedMarkdown
                          ? "含解析正文"
                          : "无解析正文"}
                      </span>
                    </div>
                  ) : null}
                  {showingComparison ? (
                    <div
                      data-testid={`editor-state-revision-comparison-${index}`}
                      style={{
                        marginTop: 8,
                        fontSize: 13,
                        color: "var(--text-muted, #4b5563)",
                      }}
                    >
                      <p
                        data-testid={`editor-state-revision-comparison-status-${index}`}
                        style={{ margin: "0 0 6px", fontWeight: 600 }}
                      >
                        {comparison.sameState
                          ? MSG_COMPARE_SAME
                          : MSG_COMPARE_DIFF}
                      </p>
                      {!comparison.sameState &&
                      comparison.changedFields.length > 0 ? (
                        <p
                          data-testid={`editor-state-revision-comparison-fields-${index}`}
                          style={{ margin: "0 0 6px" }}
                        >
                          {comparison.changedFields
                            .map((k) => formatCanonicalFieldLabel(k))
                            .join("、")}
                        </p>
                      ) : null}
                      <p
                        data-testid={`editor-state-revision-comparison-current-${index}`}
                        style={{ margin: "0 0 4px" }}
                      >
                        当前版本：
                        {renderSummaryLine(comparison.currentSummary)}
                      </p>
                      <p
                        data-testid={`editor-state-revision-comparison-target-${index}`}
                        style={{ margin: 0 }}
                      >
                        所选修订：
                        {renderSummaryLine(comparison.targetSummary)}
                      </p>
                    </div>
                  ) : null}
                  {showingBodyDiff ? (
                    <div
                      data-testid={`editor-state-revision-body-diff-result-${index}`}
                      style={{
                        marginTop: 8,
                        fontSize: 13,
                        color: "var(--text-muted, #4b5563)",
                      }}
                    >
                      <p
                        data-testid={`editor-state-revision-body-diff-status-${index}`}
                        style={{ margin: "0 0 6px", fontWeight: 600 }}
                      >
                        {bodyDiff.sameBody
                          ? MSG_BODY_DIFF_SAME
                          : `共 ${bodyDiff.changedChapterCount} 章正文有变化`}
                      </p>
                      {bodyDiff.truncated ? (
                        <p
                          data-testid={`editor-state-revision-body-diff-truncated-${index}`}
                          style={{ margin: "0 0 6px" }}
                        >
                          {MSG_BODY_DIFF_TRUNCATED}
                        </p>
                      ) : null}
                      {!bodyDiff.sameBody
                        ? bodyDiff.items.map((diffItem) => (
                            <div
                              key={diffItem.ordinal}
                              data-testid={`editor-state-revision-body-diff-item-${index}-${diffItem.ordinal}`}
                              style={{
                                marginBottom: 8,
                                padding: "6px 8px",
                                borderRadius: 4,
                                border: "1px solid var(--border, #e5e7eb)",
                              }}
                            >
                              {formatBodyDiffKindLabel(diffItem.kind) ? (
                                <p
                                  data-testid={`editor-state-revision-body-diff-item-kind-${index}-${diffItem.ordinal}`}
                                  style={{ margin: "0 0 4px", fontWeight: 600 }}
                                >
                                  {formatBodyDiffKindLabel(diffItem.kind)}
                                </p>
                              ) : null}
                              <p
                                style={{ margin: "0 0 4px" }}
                                data-testid={`editor-state-revision-body-diff-item-titles-${index}-${diffItem.ordinal}`}
                              >
                                {diffItem.beforeTitle
                                  ? `修订：${diffItem.beforeTitle}`
                                  : "修订：（无标题）"}
                                {" → "}
                                {diffItem.afterTitle
                                  ? `当前：${diffItem.afterTitle}`
                                  : "当前：（无标题）"}
                              </p>
                              <ul
                                style={{
                                  listStyle: "none",
                                  margin: 0,
                                  padding: 0,
                                }}
                              >
                                {diffItem.hunks.map((hunk, hIdx) => (
                                  <li
                                    key={hIdx}
                                    data-testid={`editor-state-revision-body-diff-hunk-${index}-${diffItem.ordinal}-${hIdx}`}
                                    style={{
                                      marginTop: 4,
                                      whiteSpace: "pre-wrap",
                                      wordBreak: "break-word",
                                    }}
                                  >
                                    <span
                                      data-testid={`editor-state-revision-body-diff-hunk-op-${index}-${diffItem.ordinal}-${hIdx}`}
                                    >
                                      {formatHunkOpLabel(hunk.op)}
                                    </span>
                                    {": "}
                                    <span
                                      data-testid={`editor-state-revision-body-diff-hunk-text-${index}-${diffItem.ordinal}-${hIdx}`}
                                    >
                                      {hunk.text}
                                    </span>
                                  </li>
                                ))}
                              </ul>
                            </div>
                          ))
                        : null}
                    </div>
                  ) : null}
                  {confirming ? (
                    <div
                      data-testid={`editor-state-revision-confirm-${index}`}
                      style={{ marginTop: 8 }}
                    >
                      <p
                        style={{
                          margin: "0 0 8px",
                          fontSize: 13,
                          color: "var(--danger)",
                        }}
                      >
                        {REVISION_RESTORE_CONFIRM_TEXT}
                      </p>
                      <div style={{ display: "flex", gap: 8 }}>
                        <button
                          type="button"
                          className="btn btn-primary btn-sm"
                          data-testid={`editor-state-revision-confirm-restore-${index}`}
                          disabled={restoreDisabled}
                          onClick={() => {
                            void handleConfirmRestore();
                          }}
                        >
                          {restoreBusy ? "恢复中…" : "确认恢复"}
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          data-testid={`editor-state-revision-cancel-restore-${index}`}
                          disabled={restoreBusy}
                          onClick={handleCancelRestore}
                        >
                          取消
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div
                      style={{
                        marginTop: 8,
                        display: "flex",
                        gap: 8,
                        flexWrap: "wrap",
                      }}
                    >
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        data-testid={`editor-state-revision-pair-select-before-${index}`}
                        disabled={pairSelectDisabled}
                        onClick={() => handlePairSelectBefore(item.revisionId)}
                      >
                        {isPairBefore ? "已选为差异前" : "选为差异前"}
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        data-testid={`editor-state-revision-pair-select-after-${index}`}
                        disabled={pairSelectDisabled}
                        onClick={() => handlePairSelectAfter(item.revisionId)}
                      >
                        {isPairAfter ? "已选为差异后" : "选为差异后"}
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        data-testid={`editor-state-revision-summary-${index}`}
                        disabled={restoreBusy}
                        onClick={() => {
                          void handleSummaryClick(item);
                        }}
                      >
                        {detailLoadingId === item.revisionId
                          ? "加载摘要…"
                          : "查看摘要"}
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        data-testid={`editor-state-revision-compare-${index}`}
                        disabled={compareDisabled}
                        onClick={() => {
                          void handleComparisonClick(item);
                        }}
                      >
                        {comparisonLoadingId === item.revisionId
                          ? "正在对比…"
                          : "与当前对比"}
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        data-testid={`editor-state-revision-body-diff-${index}`}
                        disabled={bodyDiffDisabled}
                        onClick={() => {
                          void handleBodyDiffClick(item);
                        }}
                      >
                        {bodyDiffLoadingId === item.revisionId
                          ? "加载正文差异…"
                          : "查看正文差异"}
                      </button>
                      <button
                        type="button"
                        className="btn btn-soft btn-sm"
                        data-testid={`editor-state-revision-restore-${index}`}
                        disabled={restoreDisabled}
                        onClick={() => handleRestoreClick(item.revisionId)}
                      >
                        恢复
                      </button>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          {showLoadMore ? (
            <div style={{ marginTop: 10 }}>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                data-testid="editor-state-revision-load-more"
                disabled={loadMoreDisabled}
                onClick={() => {
                  void handleLoadMore();
                }}
              >
                {loadMoreLoading ? "加载更多…" : "加载更多"}
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
