/**
 * 模块：P12B-D2 / P12G / P12H 双工作区共用检查点折叠面板
 * 用途：展开后 list 元数据；保存服务器当前版本；内联二次确认后 restore；
 *       内联命名保存/覆盖/清除（成功原位更新，失败保值）；
 *       内联二次确认后单条 DELETE（成功原位移除，失败保值可重试）。
 * 对接：editorStateCheckpointApi；技术/商务 hook 的 create/restore 回调。
 * 二次开发：
 *   - 不渲染 checkpointId/stateVersion；不请求详情 snapshot
 *   - 项目切换/折叠/卸载用会话代次隔离迟到 list/create/restore/name/delete
 *   - 名称/删除与 list/create/restore/toggle/其它行意图互斥；await 前同步 ref 单飞
 *   - 删除不依赖 props.disabled；成功零 list/editor-state 重载
 *   - 固定中文脱敏；禁止 console/存储/URL/Cookie/剪贴板/下载/轮询/外网
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteEditorStateCheckpoint,
  formatCheckpointBytes,
  formatCheckpointTime,
  listEditorStateCheckpoints,
  normalizeDisplayNameForSave,
  setEditorStateCheckpointDisplayName,
  type EditorStateCheckpointMeta,
} from "./editorStateCheckpointApi";

/** 恢复前内联确认固定文案（契约 §5） */
export const CHECKPOINT_RESTORE_CONFIRM_TEXT =
  "当前服务器内容会先自动保存为安全检查点，恢复会替换全部技术标和商务标编辑态";

/** 删除前内联确认固定文案（P12H） */
export const CHECKPOINT_DELETE_CONFIRM_TEXT =
  "删除后无法恢复。当前编辑内容、修订历史和其它检查点不会改变，确定删除这条检查点吗？";

const MSG_LIST_FAIL = "检查点列表加载失败，请稍后重试";
const MSG_CREATE_OK = "已保存服务器当前版本为检查点";
const MSG_CREATE_FAIL = "保存检查点失败，请确认后重试";
const MSG_CREATE_BLOCKED = "当前无法保存检查点，请先处理版本冲突或重新载入";
const MSG_RESTORE_OK = "已恢复到所选检查点";
const MSG_RESTORE_FAIL = "恢复检查点失败，本地内容已保留";
const MSG_RESTORE_RELOAD_FAIL =
  "恢复已完成，但刷新失败，请重新载入远端内容";
const MSG_RESTORE_BLOCKED = "当前无法恢复，请先处理版本冲突或重新载入";
/** P12G 命名固定中文 */
const MSG_NAME_SAVING = "保存名称中…";
const MSG_NAME_OK = "已保存检查点名称";
const MSG_NAME_CLEARED = "已清除检查点名称";
const MSG_NAME_FAIL = "保存检查点名称失败，请稍后重试";
/** P12H 删除固定中文 */
const MSG_DELETE_SAVING = "删除中…";
const MSG_DELETE_OK = "已删除所选检查点";
const MSG_DELETE_FAIL = "删除检查点失败，当前列表已保留";

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
  /** P12G：进入内联命名的检查点 id（仅内存） */
  const [pendingNameId, setPendingNameId] = useState<string | null>(null);
  /** P12G：命名输入草稿（仅内存） */
  const [nameDraft, setNameDraft] = useState("");
  /** P12G：命名 PATCH 在途 */
  const [nameBusy, setNameBusy] = useState(false);
  /** P12H：进入删除确认的检查点 id（仅内存，不渲染） */
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  /** P12H：删除 DELETE 在途 */
  const [deleteBusy, setDeleteBusy] = useState(false);

  /**
   * 项目会话代次：projectId 变化或折叠时递增，隔离迟到 list/create/restore/name/delete。
   */
  const sessionRef = useRef(0);
  const mountedRef = useRef(true);
  const projectIdRef = useRef(projectId);
  /** P12G 命名请求代次：项目切换/折叠/另一行命名递增 */
  const nameGenRef = useRef(0);
  /**
   * P12G 在途命名 checkpointId 同步镜像（仅内存）；
   * 进入编辑态即写入，不能单独充当 PATCH 在途门。
   */
  const pendingNameIdRef = useRef<string | null>(null);
  /**
   * P12G 真同步在途 token：save/clear 在任何 await 前原子占用；
   * catch/finally 仅清理同一 token，旧 A 永远不能清掉 B 新 token。
   */
  const nameFlightTokenRef = useRef(0);
  const nameFlightActiveRef = useRef<number | null>(null);
  /** P12H 删除请求代次：项目切换/折叠/另一轮删除递增 */
  const deleteGenRef = useRef(0);
  /**
   * P12H 在途删除 checkpointId 同步镜像（仅内存）；
   * 进入确认态即写入，不能单独充当 DELETE 在途门。
   */
  const pendingDeleteIdRef = useRef<string | null>(null);
  /**
   * P12H 真同步在途 token：confirm delete 在任何 await 前原子占用；
   * catch/finally 仅清理同一 token，旧 A 永远不能清掉 B 新 token。
   */
  const deleteFlightTokenRef = useRef(0);
  const deleteFlightActiveRef = useRef<number | null>(null);
  /** 列表项同步镜像，供清除路径读取当前 displayName */
  const itemsRef = useRef<ListItem[]>([]);

  // C. 项目围栏：render 同步镜像，关闭 commit→effect 之间的旧请求污染窗口
  projectIdRef.current = projectId;
  // 列表项同步镜像（render 同步，避免 effect 滞后）
  itemsRef.current = items;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // 项目切换：重置面板，作废在途（含命名/删除 flight token）
  useEffect(() => {
    sessionRef.current += 1;
    nameGenRef.current += 1;
    nameFlightTokenRef.current += 1;
    nameFlightActiveRef.current = null;
    pendingNameIdRef.current = null;
    deleteGenRef.current += 1;
    deleteFlightTokenRef.current += 1;
    deleteFlightActiveRef.current = null;
    pendingDeleteIdRef.current = null;
    setExpanded(false);
    setItems([]);
    setListError(null);
    setStatusMessage(null);
    setStatusTone(null);
    setListLoading(false);
    setCreateBusy(false);
    setRestoreBusy(false);
    setPendingRestoreId(null);
    setPendingNameId(null);
    setNameDraft("");
    setNameBusy(false);
    setPendingDeleteId(null);
    setDeleteBusy(false);
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
      // 折叠：递增会话，丢弃迟到 list/create/restore/name/delete 对 UI 的写入
      sessionRef.current += 1;
      nameGenRef.current += 1;
      nameFlightTokenRef.current += 1;
      nameFlightActiveRef.current = null;
      pendingNameIdRef.current = null;
      deleteGenRef.current += 1;
      deleteFlightTokenRef.current += 1;
      deleteFlightActiveRef.current = null;
      pendingDeleteIdRef.current = null;
      setExpanded(false);
      setPendingRestoreId(null);
      setPendingNameId(null);
      setNameDraft("");
      setNameBusy(false);
      setPendingDeleteId(null);
      setDeleteBusy(false);
      setListLoading(false);
      setCreateBusy(false);
      setRestoreBusy(false);
      return;
    }
    // 展开时若命名/删除在途则拒绝（互斥）
    if (nameBusy || pendingNameId != null || deleteBusy || pendingDeleteId != null) {
      return;
    }
    const session = sessionRef.current;
    setExpanded(true);
    setStatusMessage(null);
    setStatusTone(null);
    setPendingRestoreId(null);
    void loadList(session);
  }, [expanded, loadList, nameBusy, pendingNameId, deleteBusy, pendingDeleteId]);

  const handleRefresh = useCallback(() => {
    if (
      !expanded ||
      listLoading ||
      createBusy ||
      restoreBusy ||
      nameBusy ||
      pendingNameId != null ||
      deleteBusy ||
      pendingDeleteId != null
    ) {
      return;
    }
    const session = sessionRef.current;
    setStatusMessage(null);
    setStatusTone(null);
    setPendingRestoreId(null);
    void loadList(session);
  }, [
    expanded,
    listLoading,
    createBusy,
    restoreBusy,
    nameBusy,
    pendingNameId,
    deleteBusy,
    pendingDeleteId,
    loadList,
  ]);

  const handleCreate = useCallback(async () => {
    if (
      disabled ||
      createBusy ||
      restoreBusy ||
      nameBusy ||
      pendingNameId != null ||
      deleteBusy ||
      pendingDeleteId != null ||
      !expanded
    ) {
      return;
    }
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
    nameBusy,
    pendingNameId,
    deleteBusy,
    pendingDeleteId,
    expanded,
    createCheckpoint,
    loadList,
  ]);

  const handleRestoreClick = useCallback(
    (checkpointId: string) => {
      if (
        disabled ||
        createBusy ||
        restoreBusy ||
        nameBusy ||
        pendingNameId != null ||
        deleteBusy ||
        pendingDeleteId != null
      ) {
        return;
      }
      setPendingRestoreId(checkpointId);
      setStatusMessage(null);
      setStatusTone(null);
    },
    [
      disabled,
      createBusy,
      restoreBusy,
      nameBusy,
      pendingNameId,
      deleteBusy,
      pendingDeleteId,
    ],
  );

  const handleConfirmRestore = useCallback(async () => {
    if (
      disabled ||
      createBusy ||
      restoreBusy ||
      nameBusy ||
      pendingNameId != null ||
      deleteBusy ||
      pendingDeleteId != null ||
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
    nameBusy,
    pendingNameId,
    deleteBusy,
    pendingDeleteId,
    pendingRestoreId,
    expanded,
    restoreCheckpoint,
    loadList,
  ]);

  const handleCancelRestore = useCallback(() => {
    if (restoreBusy || deleteBusy) return;
    setPendingRestoreId(null);
  }, [restoreBusy, deleteBusy]);

  /**
   * 用途：进入内联命名；清除恢复/删除意图；输入零请求。
   */
  const handleNameClick = useCallback(
    (checkpointId: string, currentName: string | null) => {
      if (
        !expanded ||
        listLoading ||
        createBusy ||
        restoreBusy ||
        nameBusy ||
        pendingRestoreId != null ||
        deleteBusy ||
        pendingDeleteId != null
      ) {
        return;
      }
      nameGenRef.current += 1;
      pendingNameIdRef.current = checkpointId;
      setPendingNameId(checkpointId);
      setNameDraft(currentName ?? "");
      setNameBusy(false);
      setPendingRestoreId(null);
      setPendingDeleteId(null);
      pendingDeleteIdRef.current = null;
      setStatusMessage(null);
      setStatusTone(null);
    },
    [
      expanded,
      listLoading,
      createBusy,
      restoreBusy,
      nameBusy,
      pendingRestoreId,
      deleteBusy,
      pendingDeleteId,
    ],
  );

  const handleNameCancel = useCallback(() => {
    if (nameBusy) return;
    nameGenRef.current += 1;
    pendingNameIdRef.current = null;
    setPendingNameId(null);
    setNameDraft("");
  }, [nameBusy]);

  /**
   * 用途：保存合法非空名称；非法零请求；success/catch/finally 含 checkpointId + flight token 围栏。
   */
  const handleNameSave = useCallback(async () => {
    if (
      !expanded ||
      !projectId ||
      !pendingNameId ||
      nameBusy ||
      listLoading ||
      createBusy ||
      restoreBusy ||
      deleteBusy ||
      pendingDeleteId != null
    ) {
      return;
    }
    // B. 真同步单飞：await 前原子检查并占用独立 flight token（nameBusy 不能挡同事件循环双击）
    if (nameFlightActiveRef.current != null) {
      return;
    }
    const normalized = normalizeDisplayNameForSave(nameDraft);
    if (normalized === null) {
      // 前端可判定非法：零 PATCH
      return;
    }
    const session = sessionRef.current;
    const myGen = ++nameGenRef.current;
    const myFlight = ++nameFlightTokenRef.current;
    nameFlightActiveRef.current = myFlight;
    const checkpointId = pendingNameId;
    const projectAtStart = projectId;
    // 在途期间显式绑定本轮 checkpointId
    pendingNameIdRef.current = checkpointId;
    setNameBusy(true);
    setStatusMessage(MSG_NAME_SAVING);
    setStatusTone(null);
    const stillCurrent = () =>
      mountedRef.current &&
      session === sessionRef.current &&
      myGen === nameGenRef.current &&
      myFlight === nameFlightTokenRef.current &&
      nameFlightActiveRef.current === myFlight &&
      projectIdRef.current === projectAtStart &&
      pendingNameIdRef.current === checkpointId;
    const releaseOwnFlight = () => {
      // 仅清理同一 token：旧 A 永远不能清掉 B 新 token
      if (nameFlightActiveRef.current === myFlight) {
        nameFlightActiveRef.current = null;
      }
    };
    try {
      const saved = await setEditorStateCheckpointDisplayName(
        projectAtStart,
        checkpointId,
        normalized,
      );
      if (!stillCurrent()) return;
      // 成功：先清 busy 再收口 pending
      setNameBusy(false);
      releaseOwnFlight();
      setItems((prev) =>
        prev.map((it) =>
          it.checkpointId === checkpointId
            ? { ...it, displayName: saved }
            : it,
        ),
      );
      pendingNameIdRef.current = null;
      setPendingNameId(null);
      setNameDraft("");
      setStatusMessage(MSG_NAME_OK);
      setStatusTone("ok");
    } catch {
      if (!stillCurrent()) return;
      setStatusMessage(MSG_NAME_FAIL);
      setStatusTone("err");
      // 失败保值：保留原 displayName 与草稿
    } finally {
      if (stillCurrent()) {
        setNameBusy(false);
        releaseOwnFlight();
      } else {
        // 代次已作废时仍仅释放本 token（若尚未被项目切换清掉）
        releaseOwnFlight();
      }
    }
  }, [
    expanded,
    projectId,
    pendingNameId,
    nameBusy,
    listLoading,
    createBusy,
    restoreBusy,
    deleteBusy,
    pendingDeleteId,
    nameDraft,
  ]);

  /**
   * 用途：清除已有名称（仅已有名称可用）；发送 null。
   */
  const handleNameClear = useCallback(async () => {
    if (
      !expanded ||
      !projectId ||
      !pendingNameId ||
      nameBusy ||
      listLoading ||
      createBusy ||
      restoreBusy ||
      deleteBusy ||
      pendingDeleteId != null
    ) {
      return;
    }
    // B. 真同步单飞
    if (nameFlightActiveRef.current != null) {
      return;
    }
    const existing = itemsRef.current.find(
      (it) => it.checkpointId === pendingNameId,
    );
    if (!existing || existing.displayName == null) {
      return;
    }
    const session = sessionRef.current;
    const myGen = ++nameGenRef.current;
    const myFlight = ++nameFlightTokenRef.current;
    nameFlightActiveRef.current = myFlight;
    const checkpointId = pendingNameId;
    const projectAtStart = projectId;
    pendingNameIdRef.current = checkpointId;
    setNameBusy(true);
    setStatusMessage(MSG_NAME_SAVING);
    setStatusTone(null);
    const stillCurrent = () =>
      mountedRef.current &&
      session === sessionRef.current &&
      myGen === nameGenRef.current &&
      myFlight === nameFlightTokenRef.current &&
      nameFlightActiveRef.current === myFlight &&
      projectIdRef.current === projectAtStart &&
      pendingNameIdRef.current === checkpointId;
    const releaseOwnFlight = () => {
      if (nameFlightActiveRef.current === myFlight) {
        nameFlightActiveRef.current = null;
      }
    };
    try {
      const saved = await setEditorStateCheckpointDisplayName(
        projectAtStart,
        checkpointId,
        null,
      );
      if (!stillCurrent()) return;
      setNameBusy(false);
      releaseOwnFlight();
      setItems((prev) =>
        prev.map((it) =>
          it.checkpointId === checkpointId
            ? { ...it, displayName: saved }
            : it,
        ),
      );
      pendingNameIdRef.current = null;
      setPendingNameId(null);
      setNameDraft("");
      setStatusMessage(MSG_NAME_CLEARED);
      setStatusTone("ok");
    } catch {
      if (!stillCurrent()) return;
      setStatusMessage(MSG_NAME_FAIL);
      setStatusTone("err");
    } finally {
      if (stillCurrent()) {
        setNameBusy(false);
        releaseOwnFlight();
      } else {
        releaseOwnFlight();
      }
    }
  }, [
    expanded,
    projectId,
    pendingNameId,
    nameBusy,
    listLoading,
    createBusy,
    restoreBusy,
    deleteBusy,
    pendingDeleteId,
  ]);

  /**
   * 用途：进入单条删除确认；清恢复/命名意图；零 DELETE。
   * 约束：不依赖 props.disabled；受列表/创建/恢复/命名/删除在途阻断。
   */
  const handleDeleteClick = useCallback(
    (checkpointId: string) => {
      if (
        !expanded ||
        listLoading ||
        createBusy ||
        restoreBusy ||
        nameBusy ||
        pendingNameId != null ||
        pendingRestoreId != null ||
        deleteBusy ||
        pendingDeleteId != null
      ) {
        return;
      }
      // 进入删除确认前清理本行可能残留的恢复/命名意图
      nameGenRef.current += 1;
      pendingNameIdRef.current = null;
      setPendingNameId(null);
      setNameDraft("");
      setNameBusy(false);
      setPendingRestoreId(null);
      pendingDeleteIdRef.current = checkpointId;
      setPendingDeleteId(checkpointId);
      setStatusMessage(null);
      setStatusTone(null);
    },
    [
      expanded,
      listLoading,
      createBusy,
      restoreBusy,
      nameBusy,
      pendingNameId,
      pendingRestoreId,
      deleteBusy,
      pendingDeleteId,
    ],
  );

  /**
   * 用途：确认删除；精确一次 DELETE；success 原位移除且零重载；failure 保留确认可重试。
   */
  const handleConfirmDelete = useCallback(async () => {
    if (
      !expanded ||
      !projectId ||
      !pendingDeleteId ||
      deleteBusy ||
      listLoading ||
      createBusy ||
      restoreBusy ||
      nameBusy ||
      pendingNameId != null
    ) {
      return;
    }
    // 真同步单飞：await 前原子检查并占用独立 flight token
    if (deleteFlightActiveRef.current != null) {
      return;
    }
    const session = sessionRef.current;
    const myGen = ++deleteGenRef.current;
    const myFlight = ++deleteFlightTokenRef.current;
    deleteFlightActiveRef.current = myFlight;
    const checkpointId = pendingDeleteId;
    const projectAtStart = projectId;
    pendingDeleteIdRef.current = checkpointId;
    setDeleteBusy(true);
    setStatusMessage(MSG_DELETE_SAVING);
    setStatusTone(null);
    const stillCurrent = () =>
      mountedRef.current &&
      session === sessionRef.current &&
      myGen === deleteGenRef.current &&
      myFlight === deleteFlightTokenRef.current &&
      deleteFlightActiveRef.current === myFlight &&
      projectIdRef.current === projectAtStart &&
      pendingDeleteIdRef.current === checkpointId;
    const releaseOwnFlight = () => {
      if (deleteFlightActiveRef.current === myFlight) {
        deleteFlightActiveRef.current = null;
      }
    };
    try {
      await deleteEditorStateCheckpoint(projectAtStart, checkpointId);
      if (!stillCurrent()) return;
      setDeleteBusy(false);
      releaseOwnFlight();
      // 成功：仅原位移除目标；零 list/editor-state 重载
      setItems((prev) => prev.filter((it) => it.checkpointId !== checkpointId));
      pendingDeleteIdRef.current = null;
      setPendingDeleteId(null);
      setStatusMessage(MSG_DELETE_OK);
      setStatusTone("ok");
    } catch {
      if (!stillCurrent()) return;
      setStatusMessage(MSG_DELETE_FAIL);
      setStatusTone("err");
      // 失败保值：保留 items 与确认态，允许显式重试/取消
    } finally {
      if (stillCurrent()) {
        setDeleteBusy(false);
        releaseOwnFlight();
      } else {
        releaseOwnFlight();
      }
    }
  }, [
    expanded,
    projectId,
    pendingDeleteId,
    deleteBusy,
    listLoading,
    createBusy,
    restoreBusy,
    nameBusy,
    pendingNameId,
  ]);

  const handleCancelDelete = useCallback(() => {
    if (deleteBusy) return;
    pendingDeleteIdRef.current = null;
    setPendingDeleteId(null);
  }, [deleteBusy]);

  const nameUiLocked = pendingNameId != null || nameBusy;
  const deleteUiLocked = pendingDeleteId != null || deleteBusy;
  const actionsDisabled =
    disabled ||
    createBusy ||
    restoreBusy ||
    listLoading ||
    nameBusy ||
    pendingNameId != null ||
    deleteBusy ||
    pendingDeleteId != null;
  /** 删除入口：不依赖 props.disabled，但仍受其它操作互斥（含恢复确认） */
  const deleteEntryDisabled =
    listLoading ||
    createBusy ||
    restoreBusy ||
    nameBusy ||
    pendingNameId != null ||
    pendingRestoreId != null ||
    deleteBusy ||
    pendingDeleteId != null;

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
          disabled={(nameUiLocked || deleteUiLocked) && expanded}
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
              const naming = pendingNameId === item.checkpointId;
              const deleting = pendingDeleteId === item.checkpointId;
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
                    {item.displayName != null ? (
                      <span
                        data-testid={`editor-state-checkpoint-display-name-${index}`}
                        style={{ fontWeight: 600 }}
                      >
                        {item.displayName}
                      </span>
                    ) : null}
                  </div>
                  {naming ? (
                    <div
                      data-testid={`editor-state-checkpoint-name-wrap-${index}`}
                      style={{ marginTop: 8 }}
                    >
                      <input
                        type="text"
                        data-testid={`editor-state-checkpoint-name-input-${index}`}
                        value={nameDraft}
                        disabled={nameBusy || deleteBusy}
                        maxLength={80}
                        onChange={(e) => setNameDraft(e.target.value)}
                        style={{
                          width: "100%",
                          maxWidth: 320,
                          boxSizing: "border-box",
                          marginBottom: 8,
                        }}
                      />
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        <button
                          type="button"
                          className="btn btn-primary btn-sm"
                          data-testid={`editor-state-checkpoint-name-save-${index}`}
                          disabled={nameBusy || listLoading || deleteBusy}
                          onClick={() => {
                            void handleNameSave();
                          }}
                        >
                          {nameBusy ? MSG_NAME_SAVING : "保存"}
                        </button>
                        {item.displayName != null ? (
                          <button
                            type="button"
                            className="btn btn-soft btn-sm"
                            data-testid={`editor-state-checkpoint-name-clear-${index}`}
                            disabled={nameBusy || listLoading || deleteBusy}
                            onClick={() => {
                              void handleNameClear();
                            }}
                          >
                            清除
                          </button>
                        ) : null}
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          data-testid={`editor-state-checkpoint-name-cancel-${index}`}
                          disabled={nameBusy || deleteBusy}
                          onClick={handleNameCancel}
                        >
                          取消
                        </button>
                      </div>
                    </div>
                  ) : deleting ? (
                    <div
                      data-testid={`editor-state-checkpoint-delete-confirm-${index}`}
                      style={{ marginTop: 8 }}
                    >
                      <p
                        style={{
                          margin: "0 0 8px",
                          fontSize: 13,
                          color: "var(--danger)",
                        }}
                      >
                        {CHECKPOINT_DELETE_CONFIRM_TEXT}
                      </p>
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        <button
                          type="button"
                          className="btn btn-primary btn-sm"
                          data-testid={`editor-state-checkpoint-confirm-delete-${index}`}
                          disabled={deleteBusy || listLoading || createBusy || restoreBusy || nameBusy}
                          onClick={() => {
                            void handleConfirmDelete();
                          }}
                        >
                          {deleteBusy ? MSG_DELETE_SAVING : "确认删除"}
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          data-testid={`editor-state-checkpoint-cancel-delete-${index}`}
                          disabled={deleteBusy}
                          onClick={handleCancelDelete}
                        >
                          取消
                        </button>
                      </div>
                    </div>
                  ) : confirming ? (
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
                          disabled={restoreBusy || deleteBusy}
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
                        className="btn btn-soft btn-sm"
                        data-testid={`editor-state-checkpoint-restore-${index}`}
                        disabled={actionsDisabled}
                        onClick={() => handleRestoreClick(item.checkpointId)}
                      >
                        恢复
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        data-testid={`editor-state-checkpoint-name-${index}`}
                        disabled={actionsDisabled}
                        onClick={() =>
                          handleNameClick(item.checkpointId, item.displayName)
                        }
                      >
                        {item.displayName != null ? "重命名" : "命名"}
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        data-testid={`editor-state-checkpoint-delete-${index}`}
                        disabled={deleteEntryDisabled}
                        onClick={() => handleDeleteClick(item.checkpointId)}
                      >
                        删除
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
