/**
 * 模块：P12C-C3 / P12D-B 双工作区共用修订历史折叠面板
 * 用途：默认折叠零请求；展开 list；按需摘要；按需与当前对比；内联二次确认后 restore。
 * 对接：editorStateRevisionApi（含 comparison）；技术/商务 hook 的 restoreRevision 回调。
 * 二次开发：
 *   - 不渲染 revisionId/stateVersion/snapshot 正文/内部字段键/字段值
 *   - 项目切换/折叠/卸载用会话代次隔离迟到 list/detail/comparison/restore
 *   - 摘要、比较、恢复确认同一时刻只保留一个当前意图；交叉作废
 *   - 固定中文脱敏；禁止 console/存储/URL/Cookie/剪贴板/下载/轮询/外网
 *   - 无创建/删除/正文 diff/搜索/分页/自动批量比较
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  formatCanonicalFieldLabel,
  formatRevisionBytes,
  formatRevisionSourceLabel,
  formatRevisionTime,
  getEditorStateRevisionComparison,
  getEditorStateRevisionSummary,
  listEditorStateRevisions,
  type EditorStateRevisionComparison,
  type EditorStateRevisionMeta,
  type EditorStateRevisionSummary,
} from "./editorStateRevisionApi";

/** 恢复前内联确认固定文案（契约 §3） */
export const REVISION_RESTORE_CONFIRM_TEXT =
  "服务器当前内容会先保存为安全检查点，恢复替换技术标和商务标全部编辑态，尚未保存的本地修改不会写入。";

const MSG_LIST_FAIL = "修订历史加载失败，请稍后重试";
const MSG_DETAIL_FAIL = "修订摘要加载失败，请稍后重试";
const MSG_COMPARE_FAIL = "修订差异加载失败，请稍后重试";
const MSG_COMPARE_SAME = "与当前版本一致";
const MSG_COMPARE_DIFF = "与当前版本存在差异";
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
   * 列表/摘要/比较只读仍可刷新（比较不受 disabled 控制，但 restoreBusy 时禁用）。
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

export function EditorStateRevisionPanel({
  projectId,
  disabled,
  restoreRevision,
}: EditorStateRevisionPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [items, setItems] = useState<ListItem[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [comparisonError, setComparisonError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [statusTone, setStatusTone] = useState<"ok" | "err" | null>(null);
  const [listLoading, setListLoading] = useState(false);
  /** 仅绑定当前在途详情 revision（允许挂起时点另一项/刷新/恢复） */
  const [detailLoadingId, setDetailLoadingId] = useState<string | null>(null);
  /** 仅绑定当前在途 comparison revision */
  const [comparisonLoadingId, setComparisonLoadingId] = useState<string | null>(
    null,
  );
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

  /**
   * 项目会话代次：projectId 变化或折叠时递增，隔离迟到 list/restore。
   */
  const sessionRef = useRef(0);
  /**
   * 详情请求代次：项目切换/折叠/刷新/另一项/再次点击/恢复/比较均递增；
   * 旧 detail 的 try/catch/finally 不得写 summary/error/loading。
   */
  const detailGenRef = useRef(0);
  /**
   * 比较请求代次：与 detail 交叉作废；项目切换/折叠/刷新/恢复/列表重载/另一项均递增。
   */
  const comparisonGenRef = useRef(0);
  const mountedRef = useRef(true);

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

  const invalidateDetail = useCallback(() => {
    detailGenRef.current += 1;
    setDetailLoadingId(null);
  }, []);

  const invalidateComparison = useCallback(() => {
    comparisonGenRef.current += 1;
    setComparisonLoadingId(null);
  }, []);

  // 项目切换：重置面板，作废在途 list/detail/comparison/restore
  useEffect(() => {
    sessionRef.current += 1;
    detailGenRef.current += 1;
    comparisonGenRef.current += 1;
    setExpanded(false);
    setItems([]);
    setListError(null);
    setDetailError(null);
    setComparisonError(null);
    setStatusMessage(null);
    setStatusTone(null);
    setListLoading(false);
    setDetailLoadingId(null);
    setComparisonLoadingId(null);
    setRestoreBusy(false);
    setPendingRestoreId(null);
    setSummaryRevisionId(null);
    setSummary(null);
    setComparisonRevisionId(null);
    setComparison(null);
  }, [projectId]);

  const loadList = useCallback(
    async (session: number) => {
      if (!projectId) return;
      // 刷新/重载列表作废在途 detail/comparison，避免迟到结果覆盖新会话
      detailGenRef.current += 1;
      comparisonGenRef.current += 1;
      setDetailLoadingId(null);
      setComparisonLoadingId(null);
      setListLoading(true);
      setListError(null);
      setPendingRestoreId(null);
      clearSummaryState();
      clearComparisonState();
      try {
        const next = await listEditorStateRevisions(projectId);
        if (!mountedRef.current || session !== sessionRef.current) return;
        setItems(next);
      } catch {
        if (!mountedRef.current || session !== sessionRef.current) return;
        setListError(MSG_LIST_FAIL);
        setItems([]);
      } finally {
        if (mountedRef.current && session === sessionRef.current) {
          setListLoading(false);
        }
      }
    },
    [projectId, clearSummaryState, clearComparisonState],
  );

  const handleToggle = useCallback(() => {
    if (expanded) {
      sessionRef.current += 1;
      detailGenRef.current += 1;
      comparisonGenRef.current += 1;
      setExpanded(false);
      setListLoading(false);
      setDetailLoadingId(null);
      setComparisonLoadingId(null);
      setRestoreBusy(false);
      setPendingRestoreId(null);
      clearSummaryState();
      clearComparisonState();
      return;
    }
    const session = sessionRef.current;
    setExpanded(true);
    setStatusMessage(null);
    setStatusTone(null);
    setPendingRestoreId(null);
    clearSummaryState();
    clearComparisonState();
    void loadList(session);
  }, [expanded, loadList, clearSummaryState, clearComparisonState]);

  const handleRefresh = useCallback(() => {
    // 允许详情/比较挂起时刷新：loadList 会递增代次作废旧结果
    if (!expanded || listLoading || restoreBusy) return;
    const session = sessionRef.current;
    setStatusMessage(null);
    setStatusTone(null);
    void loadList(session);
  }, [expanded, listLoading, restoreBusy, loadList]);

  const handleSummaryClick = useCallback(
    async (item: ListItem) => {
      if (!expanded || restoreBusy) return;
      // 点击摘要：作废在途比较并清除比较结果
      invalidateComparison();
      clearComparisonState();
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
    ],
  );

  const handleComparisonClick = useCallback(
    async (item: ListItem) => {
      // 比较不受 disabled 控制，但恢复执行期间禁用
      if (!expanded || restoreBusy) return;
      // 点击比较：作废在途摘要、清除摘要与恢复确认
      invalidateDetail();
      clearSummaryState();
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
    ],
  );

  const handleRestoreClick = useCallback(
    (revisionId: string) => {
      if (disabled || restoreBusy) return;
      // 恢复：立即清摘要/比较/detail error 并作废在途 detail 与 comparison
      invalidateDetail();
      invalidateComparison();
      clearSummaryState();
      clearComparisonState();
      setPendingRestoreId(revisionId);
      setStatusMessage(null);
      setStatusTone(null);
    },
    [
      disabled,
      restoreBusy,
      invalidateDetail,
      invalidateComparison,
      clearSummaryState,
      clearComparisonState,
    ],
  );

  const handleConfirmRestore = useCallback(async () => {
    if (
      disabled ||
      restoreBusy ||
      !pendingRestoreId ||
      !expanded
    ) {
      return;
    }
    const session = sessionRef.current;
    const revisionId = pendingRestoreId;
    // 确认恢复：作废在途 detail/comparison，清摘要/比较/确认相关态
    invalidateDetail();
    invalidateComparison();
    clearSummaryState();
    clearComparisonState();
    setRestoreBusy(true);
    setStatusMessage(null);
    setStatusTone(null);
    try {
      const outcome = await restoreRevision(revisionId);
      if (!mountedRef.current || session !== sessionRef.current) return;
      setPendingRestoreId(null);
      clearSummaryState();
      clearComparisonState();
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
    pendingRestoreId,
    expanded,
    restoreRevision,
    loadList,
    invalidateDetail,
    invalidateComparison,
    clearSummaryState,
    clearComparisonState,
  ]);

  const handleCancelRestore = useCallback(() => {
    if (restoreBusy) return;
    setPendingRestoreId(null);
  }, [restoreBusy]);

  const restoreDisabled = disabled || restoreBusy || listLoading;
  /** 比较不受 disabled 控制，仅 restoreBusy 期间禁用 */
  const compareDisabled = restoreBusy;

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
              disabled={listLoading || restoreBusy}
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
        </div>
      ) : null}
    </div>
  );
}
