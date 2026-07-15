/**
 * 模块：P12B-D2 双工作区共用检查点折叠面板
 * 用途：展开后 list 元数据；保存服务器当前版本；内联二次确认后 restore。
 * 对接：editorStateCheckpointApi；技术/商务 hook 的 create/restore 回调。
 * 二次开发：
 *   - 不渲染 checkpointId/stateVersion；不请求详情 snapshot
 *   - 项目切换/折叠/卸载用会话代次隔离迟到结果
 *   - 固定中文脱敏；禁止 console/存储/URL/Cookie/剪贴板/下载/轮询/外网
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  formatCheckpointBytes,
  formatCheckpointTime,
  listEditorStateCheckpoints,
  type EditorStateCheckpointMeta,
} from "./editorStateCheckpointApi";

/** 恢复前内联确认固定文案（契约 §5） */
export const CHECKPOINT_RESTORE_CONFIRM_TEXT =
  "当前服务器内容会先自动保存为安全检查点，恢复会替换全部技术标和商务标编辑态";

const MSG_LIST_FAIL = "检查点列表加载失败，请稍后重试";
const MSG_CREATE_OK = "已保存服务器当前版本为检查点";
const MSG_CREATE_FAIL = "保存检查点失败，请确认后重试";
const MSG_CREATE_BLOCKED = "当前无法保存检查点，请先处理版本冲突或重新载入";
const MSG_RESTORE_OK = "已恢复到所选检查点";
const MSG_RESTORE_FAIL = "恢复检查点失败，本地内容已保留";
const MSG_RESTORE_RELOAD_FAIL =
  "恢复已完成，但刷新失败，请重新载入远端内容";
const MSG_RESTORE_BLOCKED = "当前无法恢复，请先处理版本冲突或重新载入";

/** 创建回调结果 */
export type CheckpointCreateOutcome =
  | { status: "success" }
  | { status: "failed" }
  | { status: "blocked" };

/** 恢复回调结果（与版本化外部写 runner 对齐） */
export type CheckpointRestoreOutcome =
  | { status: "success" }
  | { status: "reload_failed" }
  | { status: "post_failed" }
  | { status: "blocked" };

export type EditorStateCheckpointPanelProps = {
  projectId: string;
  /**
   * 全状态阻断、初始加载失败、版本未知或 apiReady=false 时禁用创建/恢复。
   */
  disabled: boolean;
  /** 强制即时 PUT 后 POST {} 创建；由 hook 进入既有保存链 */
  createCheckpoint: () => Promise<CheckpointCreateOutcome>;
  /** 进入既有串行链 POST restore + 唯一 editor-state GET */
  restoreCheckpoint: (checkpointId: string) => Promise<CheckpointRestoreOutcome>;
};

/**
 * 用途：内存列表项；checkpointId 仅作 key/请求参数，不渲染。
 */
type ListItem = EditorStateCheckpointMeta;

export function EditorStateCheckpointPanel({
  projectId,
  disabled,
  createCheckpoint,
  restoreCheckpoint,
}: EditorStateCheckpointPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [items, setItems] = useState<ListItem[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [statusTone, setStatusTone] = useState<"ok" | "err" | null>(null);
  const [listLoading, setListLoading] = useState(false);
  const [createBusy, setCreateBusy] = useState(false);
  const [restoreBusy, setRestoreBusy] = useState(false);
  /** 进入确认态的检查点 id（仅内存，不渲染） */
  const [pendingRestoreId, setPendingRestoreId] = useState<string | null>(null);

  /**
   * 项目会话代次：projectId 变化或折叠时递增，隔离迟到 list/create/restore。
   */
  const sessionRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // 项目切换：重置面板，作废在途
  useEffect(() => {
    sessionRef.current += 1;
    setExpanded(false);
    setItems([]);
    setListError(null);
    setStatusMessage(null);
    setStatusTone(null);
    setListLoading(false);
    setCreateBusy(false);
    setRestoreBusy(false);
    setPendingRestoreId(null);
  }, [projectId]);

  const loadList = useCallback(
    async (session: number) => {
      if (!projectId) return;
      setListLoading(true);
      setListError(null);
      try {
        const next = await listEditorStateCheckpoints(projectId);
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
    [projectId],
  );

  const handleToggle = useCallback(() => {
    if (expanded) {
      // 折叠：递增会话，丢弃迟到 list/create/restore 对 UI 的写入
      sessionRef.current += 1;
      setExpanded(false);
      setPendingRestoreId(null);
      setListLoading(false);
      setCreateBusy(false);
      setRestoreBusy(false);
      return;
    }
    const session = sessionRef.current;
    setExpanded(true);
    setStatusMessage(null);
    setStatusTone(null);
    setPendingRestoreId(null);
    void loadList(session);
  }, [expanded, loadList]);

  const handleRefresh = useCallback(() => {
    if (!expanded || listLoading || createBusy || restoreBusy) return;
    const session = sessionRef.current;
    setStatusMessage(null);
    setStatusTone(null);
    setPendingRestoreId(null);
    void loadList(session);
  }, [expanded, listLoading, createBusy, restoreBusy, loadList]);

  const handleCreate = useCallback(async () => {
    if (disabled || createBusy || restoreBusy || !expanded) return;
    const session = sessionRef.current;
    setCreateBusy(true);
    setStatusMessage(null);
    setStatusTone(null);
    setPendingRestoreId(null);
    try {
      const outcome = await createCheckpoint();
      if (!mountedRef.current || session !== sessionRef.current) return;
      if (outcome.status === "success") {
        setStatusMessage(MSG_CREATE_OK);
        setStatusTone("ok");
        await loadList(session);
        return;
      }
      if (outcome.status === "blocked") {
        setStatusMessage(MSG_CREATE_BLOCKED);
        setStatusTone("err");
        // 仍刷新列表供确认
        await loadList(session);
        return;
      }
      setStatusMessage(MSG_CREATE_FAIL);
      setStatusTone("err");
      await loadList(session);
    } catch {
      if (!mountedRef.current || session !== sessionRef.current) return;
      setStatusMessage(MSG_CREATE_FAIL);
      setStatusTone("err");
      await loadList(session);
    } finally {
      if (mountedRef.current && session === sessionRef.current) {
        setCreateBusy(false);
      }
    }
  }, [
    disabled,
    createBusy,
    restoreBusy,
    expanded,
    createCheckpoint,
    loadList,
  ]);

  const handleRestoreClick = useCallback(
    (checkpointId: string) => {
      if (disabled || createBusy || restoreBusy) return;
      setPendingRestoreId(checkpointId);
      setStatusMessage(null);
      setStatusTone(null);
    },
    [disabled, createBusy, restoreBusy],
  );

  const handleConfirmRestore = useCallback(async () => {
    if (
      disabled ||
      createBusy ||
      restoreBusy ||
      !pendingRestoreId ||
      !expanded
    ) {
      return;
    }
    const session = sessionRef.current;
    const checkpointId = pendingRestoreId;
    setRestoreBusy(true);
    setStatusMessage(null);
    setStatusTone(null);
    try {
      const outcome = await restoreCheckpoint(checkpointId);
      if (!mountedRef.current || session !== sessionRef.current) return;
      // 无论结果，离开确认态（业务成功或失败均不再二次 POST）
      setPendingRestoreId(null);
      if (outcome.status === "success") {
        setStatusMessage(MSG_RESTORE_OK);
        setStatusTone("ok");
        // 列表 GET 显示新安全检查点；不计入唯一 editor-state GET
        await loadList(session);
        return;
      }
      if (outcome.status === "reload_failed") {
        setStatusMessage(MSG_RESTORE_RELOAD_FAIL);
        setStatusTone("err");
        // 业务已成功：仍可尝试刷新列表展示安全检查点
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
      setStatusMessage(MSG_RESTORE_FAIL);
      setStatusTone("err");
    } finally {
      if (mountedRef.current && session === sessionRef.current) {
        setRestoreBusy(false);
      }
    }
  }, [
    disabled,
    createBusy,
    restoreBusy,
    pendingRestoreId,
    expanded,
    restoreCheckpoint,
    loadList,
  ]);

  const handleCancelRestore = useCallback(() => {
    if (restoreBusy) return;
    setPendingRestoreId(null);
  }, [restoreBusy]);

  const actionsDisabled = disabled || createBusy || restoreBusy || listLoading;

  return (
    <div
      data-testid="editor-state-checkpoint-panel"
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
          data-testid="editor-state-checkpoint-toggle"
          aria-expanded={expanded}
          onClick={handleToggle}
        >
          {expanded ? "收起版本检查点" : "版本检查点"}
        </button>
        {expanded ? (
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              className="btn btn-soft btn-sm"
              data-testid="editor-state-checkpoint-create"
              disabled={actionsDisabled}
              onClick={() => {
                void handleCreate();
              }}
            >
              {createBusy ? "保存中…" : "保存服务器当前版本"}
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              data-testid="editor-state-checkpoint-refresh"
              disabled={actionsDisabled}
              onClick={handleRefresh}
            >
              刷新
            </button>
          </div>
        ) : null}
      </div>

      {expanded ? (
        <div
          data-testid="editor-state-checkpoint-body"
          style={{ marginTop: 10 }}
        >
          {statusMessage ? (
            <p
              data-testid="editor-state-checkpoint-status"
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
              data-testid="editor-state-checkpoint-list-error"
              style={{ margin: "0 0 8px", color: "var(--danger)" }}
            >
              {listError}
            </p>
          ) : null}
          {listLoading && items.length === 0 ? (
            <p
              data-testid="editor-state-checkpoint-list-loading"
              style={{ margin: 0, color: "var(--text-muted, #6b7280)" }}
            >
              加载检查点列表…
            </p>
          ) : null}
          {!listLoading && !listError && items.length === 0 ? (
            <p
              data-testid="editor-state-checkpoint-empty"
              style={{ margin: 0, color: "var(--text-muted, #6b7280)" }}
            >
              暂无检查点
            </p>
          ) : null}
          <ul
            data-testid="editor-state-checkpoint-list"
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
              const confirming = pendingRestoreId === item.checkpointId;
              return (
                <li
                  key={item.checkpointId}
                  data-testid={`editor-state-checkpoint-item-${index}`}
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
                    <span data-testid={`editor-state-checkpoint-time-${index}`}>
                      {formatCheckpointTime(item.createdAt)}
                    </span>
                    <span>
                      大纲节点 {item.outlineNodeCount}
                    </span>
                    <span>章节 {item.chapterCount}</span>
                    <span>{formatCheckpointBytes(item.snapshotBytes)}</span>
                  </div>
                  {confirming ? (
                    <div
                      data-testid={`editor-state-checkpoint-confirm-${index}`}
                      style={{ marginTop: 8 }}
                    >
                      <p
                        style={{
                          margin: "0 0 8px",
                          fontSize: 13,
                          color: "var(--danger)",
                        }}
                      >
                        {CHECKPOINT_RESTORE_CONFIRM_TEXT}
                      </p>
                      <div style={{ display: "flex", gap: 8 }}>
                        <button
                          type="button"
                          className="btn btn-primary btn-sm"
                          data-testid={`editor-state-checkpoint-confirm-restore-${index}`}
                          disabled={actionsDisabled}
                          onClick={() => {
                            void handleConfirmRestore();
                          }}
                        >
                          {restoreBusy ? "恢复中…" : "确认恢复"}
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          data-testid={`editor-state-checkpoint-cancel-restore-${index}`}
                          disabled={restoreBusy}
                          onClick={handleCancelRestore}
                        >
                          取消
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div style={{ marginTop: 8 }}>
                      <button
                        type="button"
                        className="btn btn-soft btn-sm"
                        data-testid={`editor-state-checkpoint-restore-${index}`}
                        disabled={actionsDisabled}
                        onClick={() => handleRestoreClick(item.checkpointId)}
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
