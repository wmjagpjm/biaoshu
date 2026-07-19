/**
 * 模块：P12B-D2 / P12G / P12H / P12I / P12J-B 双工作区共用检查点折叠面板
 * 用途：展开后 list 元数据；保存服务器当前版本；内联二次确认后 restore；
 *       内联命名保存/覆盖/清除（成功原位更新，失败保值）；
 *       内联二次确认后单条 DELETE（成功原位移除，失败保值可重试）；
 *       显式名称/内容搜索（输入零请求；按钮/Enter 才 POST；同值零重发）；
 *       单条固定/取消固定（成功原位更新 isPinned，失败保值）。
 * 对接：editorStateCheckpointApi；技术/商务 hook 的 create/restore 回调。
 * 二次开发：
 *   - 不渲染 checkpointId/stateVersion；不请求详情 snapshot
 *   - 项目切换/折叠/卸载用会话代次隔离迟到 list/search/create/restore/name/delete/pin
 *   - 搜索/名称/删除/固定与 list/create/restore/toggle/其它行意图互斥；await 前同步 ref 单飞
 *   - 删除/固定不依赖 props.disabled；成功零 list/editor-state 重载
 *   - active search 下刷新/创建/恢复重发同一 POST；清除恰好一次 GET；固定仅原位
 *   - 固定中文脱敏；禁止 console/存储/URL/Cookie/剪贴板/下载/轮询/外网
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  assertValidSearchQuery,
  deleteEditorStateCheckpoint,
  formatCheckpointBytes,
  formatCheckpointTime,
  listEditorStateCheckpoints,
  normalizeDisplayNameForSave,
  searchEditorStateCheckpoints,
  setEditorStateCheckpointDisplayName,
  setEditorStateCheckpointPin,
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
/** P12I 搜索固定中文 */
const MSG_SEARCH_QUERY_INVALID =
  "搜索关键词需为 1 至 64 个字符，且不能含首尾空白或控制字符";
const MSG_SEARCH_EMPTY = "没有匹配名称或内容的检查点";
const MSG_SEARCH_FAIL = "检查点名称或内容搜索失败，请稍后重试";
const MSG_SEARCH_ACTIVE = "当前为名称或内容搜索结果";
const LABEL_SEARCH = "名称或内容搜索";
/** P12J-B 固定固定中文 */
const MSG_PIN_SAVING = "保存固定状态中…";
const MSG_PIN_OK = "检查点已固定";
const MSG_PIN_UNPIN_OK = "已取消固定";
const MSG_PIN_FAIL = "保存检查点固定状态失败，当前状态已保留";

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
  /** P12I：搜索输入草稿（仅内存） */
  const [searchDraft, setSearchDraft] = useState("");
  /** P12I：已应用关键词（仅内存；非 null 表示搜索态） */
  const [appliedSearch, setAppliedSearch] = useState<string | null>(null);
  /** P12I：关键词本地校验错误 */
  const [searchError, setSearchError] = useState<string | null>(null);
  /** P12J-B：固定 PATCH 在途 */
  const [pinBusy, setPinBusy] = useState(false);

  /**
   * 项目会话代次：projectId 变化或折叠时递增，隔离迟到 list/search/create/restore/name/delete/pin。
   */
  const sessionRef = useRef(0);
  const mountedRef = useRef(true);
  const projectIdRef = useRef(projectId);
  /** P12I 已应用关键词同步镜像；render 同步，避免 effect 滞后 */
  const appliedSearchRef = useRef<string | null>(null);
  /**
   * P12I 真同步在途 token：search 在任何 await 前原子占用；
   * catch/finally 仅清理同一 token，旧 A 永远不能清掉 B 新 token。
   */
  const searchFlightTokenRef = useRef(0);
  const searchFlightActiveRef = useRef<number | null>(null);
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
  /**
   * P12J-B 固定请求代次：项目切换/折叠/卸载递增；
   * success/catch/finally 同时核对 mounted/session/gen/project/checkpoint。
   */
  const pinGenRef = useRef(0);
  /** P12J-B 全局单飞：await 前同步关门 */
  const pinInFlightRef = useRef(false);
  /**
   * P12J-B 在途固定 checkpointId 同步镜像（仅内存）；
   */
  const pinCheckpointIdRef = useRef<string | null>(null);
  /**
   * P12J-B 在途固定发起时的项目 ID；与 projectIdRef 交叉核对。
   */
  const pinProjectIdRef = useRef<string | null>(null);
  /** 列表项同步镜像，供清除路径读取当前 displayName */
  const itemsRef = useRef<ListItem[]>([]);

  // C. 项目围栏：render 同步镜像，关闭 commit→effect 之间的旧请求污染窗口
  projectIdRef.current = projectId;
  // 列表项同步镜像（render 同步，避免 effect 滞后）
  itemsRef.current = items;
  appliedSearchRef.current = appliedSearch;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // 项目切换：重置面板，作废在途（含搜索/命名/删除/固定 flight token）
  useEffect(() => {
    sessionRef.current += 1;
    searchFlightTokenRef.current += 1;
    searchFlightActiveRef.current = null;
    nameGenRef.current += 1;
    nameFlightTokenRef.current += 1;
    nameFlightActiveRef.current = null;
    pendingNameIdRef.current = null;
    deleteGenRef.current += 1;
    deleteFlightTokenRef.current += 1;
    deleteFlightActiveRef.current = null;
    pendingDeleteIdRef.current = null;
    pinGenRef.current += 1;
    pinInFlightRef.current = false;
    pinCheckpointIdRef.current = null;
    pinProjectIdRef.current = null;
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
    setPinBusy(false);
    setSearchDraft("");
    setAppliedSearch(null);
    appliedSearchRef.current = null;
    setSearchError(null);
  }, [projectId]);

  /**
   * 用途：在第一个 await 前同步占用 search flight；已在途则返回 null。
   * 所有 search POST 路径（apply/refresh/create/restore 重载）共用。
   */
  const tryBeginSearchFlight = useCallback((): number | null => {
    if (searchFlightActiveRef.current != null) {
      return null;
    }
    const token = ++searchFlightTokenRef.current;
    searchFlightActiveRef.current = token;
    return token;
  }, []);

  /**
   * 用途：统一首屏/刷新加载；有已应用关键词走 search POST，否则 list GET。
   * 二次开发：
   *   - 凡 search POST 必须在调用前同步占用 flight 并传入 flightToken；
   *   - finally 仅释放自己的 token，旧 A 不得清掉 B。
   */
  const loadList = useCallback(
    async (session: number, flightToken: number | null = null) => {
      if (!projectId) {
        // 调用方已占用 flight 时，入口失败也必须释放自己的 token
        if (
          flightToken != null &&
          searchFlightActiveRef.current === flightToken
        ) {
          searchFlightActiveRef.current = null;
        }
        return;
      }
      const projectAtStart = projectId;
      const searchQ = appliedSearchRef.current;
      // search POST 必须带 flight；缺省视为调用方未占位，拒绝发请求防双飞
      if (searchQ != null && flightToken == null) {
        return;
      }
      const ownedFlight = flightToken;
      setListLoading(true);
      setListError(null);
      try {
        const next = searchQ
          ? await searchEditorStateCheckpoints(projectAtStart, searchQ)
          : await listEditorStateCheckpoints(projectAtStart);
        if (
          !mountedRef.current ||
          session !== sessionRef.current ||
          projectIdRef.current !== projectAtStart ||
          appliedSearchRef.current !== searchQ
        ) {
          return;
        }
        if (
          ownedFlight != null &&
          searchFlightActiveRef.current !== ownedFlight
        ) {
          return;
        }
        setItems(next);
      } catch {
        if (
          !mountedRef.current ||
          session !== sessionRef.current ||
          projectIdRef.current !== projectAtStart ||
          appliedSearchRef.current !== searchQ
        ) {
          return;
        }
        if (
          ownedFlight != null &&
          searchFlightActiveRef.current !== ownedFlight
        ) {
          return;
        }
        setListError(searchQ ? MSG_SEARCH_FAIL : MSG_LIST_FAIL);
        // 失败保值：搜索态保留已应用结果；普通 list 失败清空
        if (!searchQ) {
          setItems([]);
        }
      } finally {
        // 仅释放自己的 token：旧 A 迟到 finally 不得解锁 B
        if (
          ownedFlight != null &&
          searchFlightActiveRef.current === ownedFlight
        ) {
          searchFlightActiveRef.current = null;
        }
        if (
          mountedRef.current &&
          session === sessionRef.current &&
          projectIdRef.current === projectAtStart
        ) {
          setListLoading(false);
        }
      }
    },
    [projectId],
  );

  const handleToggle = useCallback(() => {
    if (expanded) {
      // 折叠：递增会话，丢弃迟到 list/search/create/restore/name/delete/pin 对 UI 的写入
      sessionRef.current += 1;
      searchFlightTokenRef.current += 1;
      searchFlightActiveRef.current = null;
      nameGenRef.current += 1;
      nameFlightTokenRef.current += 1;
      nameFlightActiveRef.current = null;
      pendingNameIdRef.current = null;
      deleteGenRef.current += 1;
      deleteFlightTokenRef.current += 1;
      deleteFlightActiveRef.current = null;
      pendingDeleteIdRef.current = null;
      pinGenRef.current += 1;
      pinInFlightRef.current = false;
      pinCheckpointIdRef.current = null;
      pinProjectIdRef.current = null;
      setExpanded(false);
      setPendingRestoreId(null);
      setPendingNameId(null);
      setNameDraft("");
      setNameBusy(false);
      setPendingDeleteId(null);
      setDeleteBusy(false);
      setPinBusy(false);
      setListLoading(false);
      setCreateBusy(false);
      setRestoreBusy(false);
      setSearchDraft("");
      setAppliedSearch(null);
      appliedSearchRef.current = null;
      setSearchError(null);
      return;
    }
    // 展开时若命名/删除/搜索/固定在途则拒绝（互斥）
    if (
      nameBusy ||
      pendingNameId != null ||
      deleteBusy ||
      pendingDeleteId != null ||
      pinBusy ||
      pinInFlightRef.current ||
      searchFlightActiveRef.current != null
    ) {
      return;
    }
    const session = sessionRef.current;
    setExpanded(true);
    setStatusMessage(null);
    setStatusTone(null);
    setPendingRestoreId(null);
    void loadList(session);
  }, [
    expanded,
    loadList,
    nameBusy,
    pendingNameId,
    deleteBusy,
    pendingDeleteId,
    pinBusy,
  ]);

  const handleRefresh = useCallback(() => {
    if (
      !expanded ||
      listLoading ||
      createBusy ||
      restoreBusy ||
      nameBusy ||
      pendingNameId != null ||
      deleteBusy ||
      pendingDeleteId != null ||
      pinBusy ||
      pinInFlightRef.current ||
      searchFlightActiveRef.current != null
    ) {
      return;
    }
    const session = sessionRef.current;
    setStatusMessage(null);
    setStatusTone(null);
    setPendingRestoreId(null);
    // active search 下重发同一 POST（await 前同步占 flight）；否则 GET
    if (appliedSearchRef.current != null) {
      const myFlight = tryBeginSearchFlight();
      if (myFlight == null) {
        return;
      }
      void loadList(session, myFlight);
      return;
    }
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
    pinBusy,
    loadList,
    tryBeginSearchFlight,
  ]);

  /**
   * 用途：搜索草稿变更；输入零请求。
   */
  const handleSearchDraftChange = useCallback((value: string) => {
    setSearchDraft(value);
    setSearchError(null);
  }, []);

  /**
   * 用途：显式应用搜索（按钮/Enter）；不 trim；非法零请求保值；同值零重发；同步单飞。
   */
  const handleSearchApply = useCallback(() => {
    if (
      !expanded ||
      disabled ||
      listLoading ||
      createBusy ||
      restoreBusy ||
      nameBusy ||
      pendingNameId != null ||
      deleteBusy ||
      pendingDeleteId != null ||
      pendingRestoreId != null ||
      pinBusy ||
      pinInFlightRef.current
    ) {
      return;
    }
    // 真同步单飞：await 前原子占用
    if (searchFlightActiveRef.current != null) {
      return;
    }
    const raw = searchDraft;
    try {
      assertValidSearchQuery(raw);
    } catch {
      setSearchError(MSG_SEARCH_QUERY_INVALID);
      return;
    }
    setSearchError(null);
    // 同值零重发：仅拦截「上次同关键词已成功且无当前搜索错误」；
    // HTTP/解析失败后同一关键词必须可重试（失败结果与输入保值，重试开始清旧错误）
    if (raw === appliedSearchRef.current && listError == null) {
      return;
    }
    const myFlight = tryBeginSearchFlight();
    if (myFlight == null) {
      return;
    }
    const session = sessionRef.current;
    appliedSearchRef.current = raw;
    setAppliedSearch(raw);
    setListError(null);
    setStatusMessage(null);
    setStatusTone(null);
    setPendingRestoreId(null);
    void loadList(session, myFlight);
  }, [
    expanded,
    disabled,
    listLoading,
    createBusy,
    restoreBusy,
    nameBusy,
    pendingNameId,
    deleteBusy,
    pendingDeleteId,
    pendingRestoreId,
    pinBusy,
    searchDraft,
    listError,
    loadList,
    tryBeginSearchFlight,
  ]);

  /**
   * 用途：清除搜索草稿与已应用态；有过应用态则恰好一次 list GET。
   */
  const handleSearchClear = useCallback(() => {
    if (
      !expanded ||
      listLoading ||
      createBusy ||
      restoreBusy ||
      nameBusy ||
      pendingNameId != null ||
      deleteBusy ||
      pendingDeleteId != null ||
      pendingRestoreId != null ||
      pinBusy ||
      pinInFlightRef.current ||
      searchFlightActiveRef.current != null
    ) {
      return;
    }
    const hadApplied = appliedSearchRef.current != null;
    const hadDraft = searchDraft !== "";
    setSearchDraft("");
    setSearchError(null);
    appliedSearchRef.current = null;
    setAppliedSearch(null);
    if (!hadApplied && !hadDraft) {
      return;
    }
    if (!hadApplied) {
      // 仅草稿：零请求
      return;
    }
    setListError(null);
    setStatusMessage(null);
    setStatusTone(null);
    const session = sessionRef.current;
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
    pendingRestoreId,
    pinBusy,
    searchDraft,
    loadList,
  ]);

  /** active search 下重载：await 前同步占 flight；GET 路径 flight=null */
  const reloadVisibleList = useCallback(
    async (session: number) => {
      if (appliedSearchRef.current != null) {
        const myFlight = tryBeginSearchFlight();
        if (myFlight == null) {
          return;
        }
        await loadList(session, myFlight);
        return;
      }
      await loadList(session);
    },
    [loadList, tryBeginSearchFlight],
  );

  const handleCreate = useCallback(async () => {
    if (
      disabled ||
      createBusy ||
      restoreBusy ||
      nameBusy ||
      pendingNameId != null ||
      deleteBusy ||
      pendingDeleteId != null ||
      pinBusy ||
      pinInFlightRef.current ||
      searchFlightActiveRef.current != null ||
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
        // active search 下重发同一 POST；否则 GET
        await reloadVisibleList(session);
        return;
      }
      if (outcome.status === "blocked") {
        setStatusMessage(MSG_CREATE_BLOCKED);
        setStatusTone("err");
        await reloadVisibleList(session);
        return;
      }
      setStatusMessage(MSG_CREATE_FAIL);
      setStatusTone("err");
      await reloadVisibleList(session);
    } catch {
      if (!mountedRef.current || session !== sessionRef.current) return;
      setStatusMessage(MSG_CREATE_FAIL);
      setStatusTone("err");
      await reloadVisibleList(session);
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
    pinBusy,
    expanded,
    createCheckpoint,
    reloadVisibleList,
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
        pendingDeleteId != null ||
        pinBusy ||
        pinInFlightRef.current ||
        listLoading ||
        searchFlightActiveRef.current != null
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
      pinBusy,
      listLoading,
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
      pinBusy ||
      pinInFlightRef.current ||
      searchFlightActiveRef.current != null ||
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
        // active search 下重发同一 POST；否则 GET 显示安全检查点
        await reloadVisibleList(session);
        return;
      }
      if (outcome.status === "reload_failed") {
        setStatusMessage(MSG_RESTORE_RELOAD_FAIL);
        setStatusTone("err");
        await reloadVisibleList(session);
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
    pinBusy,
    pendingRestoreId,
    expanded,
    restoreCheckpoint,
    reloadVisibleList,
  ]);

  const handleCancelRestore = useCallback(() => {
    if (restoreBusy || deleteBusy || pinBusy) return;
    setPendingRestoreId(null);
  }, [restoreBusy, deleteBusy, pinBusy]);

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
        pendingDeleteId != null ||
        pinBusy ||
        pinInFlightRef.current ||
        searchFlightActiveRef.current != null
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
      pinBusy,
    ],
  );

  const handleNameCancel = useCallback(() => {
    if (nameBusy || pinBusy) return;
    nameGenRef.current += 1;
    pendingNameIdRef.current = null;
    setPendingNameId(null);
    setNameDraft("");
  }, [nameBusy, pinBusy]);

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
      pendingDeleteId != null ||
      pinBusy ||
      pinInFlightRef.current
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
    pinBusy,
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
      pendingDeleteId != null ||
      pinBusy ||
      pinInFlightRef.current
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
    pinBusy,
  ]);

  /**
   * 用途：进入单条删除确认；清恢复/命名意图；零 DELETE。
   * 约束：不依赖 props.disabled；受列表/创建/恢复/命名/删除/固定在途阻断。
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
        pendingDeleteId != null ||
        pinBusy ||
        pinInFlightRef.current ||
        searchFlightActiveRef.current != null
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
      pinBusy,
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
      pendingNameId != null ||
      pinBusy ||
      pinInFlightRef.current
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
    pinBusy,
  ]);

  const handleCancelDelete = useCallback(() => {
    if (deleteBusy || pinBusy) return;
    pendingDeleteIdRef.current = null;
    setPendingDeleteId(null);
  }, [deleteBusy, pinBusy]);

  /**
   * 用途：单击固定/取消固定；全局单飞；成功仅原位更新 isPinned。
   * 约束：await 前同步关门；success/catch/finally 核对 mounted/session/gen/project/checkpoint。
   * 固定入口不依赖 props.disabled，但与全部检查点操作真实互斥。
   */
  const handlePinClick = useCallback(
    async (checkpointId: string, currentlyPinned: boolean) => {
      if (
        !expanded ||
        !projectId ||
        listLoading ||
        createBusy ||
        restoreBusy ||
        nameBusy ||
        deleteBusy ||
        pinBusy ||
        pinInFlightRef.current ||
        pendingDeleteId != null ||
        pendingNameId != null ||
        searchFlightActiveRef.current != null
      ) {
        return;
      }
      // 同步单飞：await 前关门，双击/另一行只产生一次 PATCH
      pinInFlightRef.current = true;
      const session = sessionRef.current;
      const myGen = ++pinGenRef.current;
      const projectAtStart = projectId;
      const desired = !currentlyPinned;
      pinCheckpointIdRef.current = checkpointId;
      pinProjectIdRef.current = projectAtStart;
      // 开始固定：作废其它行操作意图
      nameGenRef.current += 1;
      pendingNameIdRef.current = null;
      setPendingNameId(null);
      setNameDraft("");
      setNameBusy(false);
      setPendingRestoreId(null);
      pendingDeleteIdRef.current = null;
      setPendingDeleteId(null);
      setPinBusy(true);
      setStatusMessage(MSG_PIN_SAVING);
      setStatusTone(null);
      const stillCurrent = () =>
        mountedRef.current &&
        session === sessionRef.current &&
        myGen === pinGenRef.current &&
        projectIdRef.current === projectAtStart &&
        pinProjectIdRef.current === projectAtStart &&
        pinCheckpointIdRef.current === checkpointId;
      try {
        const saved = await setEditorStateCheckpointPin(
          projectAtStart,
          checkpointId,
          desired,
        );
        if (!stillCurrent()) return;
        // 成功：仅原位替换目标 isPinned；零 list/search/detail 重载
        setItems((prev) =>
          prev.map((it) =>
            it.checkpointId === checkpointId ? { ...it, isPinned: saved } : it,
          ),
        );
        setStatusMessage(desired ? MSG_PIN_OK : MSG_PIN_UNPIN_OK);
        setStatusTone("ok");
      } catch {
        if (!stillCurrent()) return;
        // 失败保值：不清 items
        setStatusMessage(MSG_PIN_FAIL);
        setStatusTone("err");
      } finally {
        if (stillCurrent()) {
          setPinBusy(false);
          pinInFlightRef.current = false;
          pinCheckpointIdRef.current = null;
          pinProjectIdRef.current = null;
        }
      }
    },
    [
      expanded,
      projectId,
      listLoading,
      createBusy,
      restoreBusy,
      nameBusy,
      deleteBusy,
      pinBusy,
      pendingDeleteId,
      pendingNameId,
    ],
  );

  const nameUiLocked = pendingNameId != null || nameBusy;
  const deleteUiLocked = pendingDeleteId != null || deleteBusy;
  const pinUiLocked = pinBusy;
  const searchActive = appliedSearch != null;
  const actionsDisabled =
    disabled ||
    createBusy ||
    restoreBusy ||
    listLoading ||
    nameBusy ||
    pendingNameId != null ||
    deleteBusy ||
    pendingDeleteId != null ||
    pinBusy;
  /** 搜索控件：与 list/create/restore/name/delete/pin/确认态互斥；真实传 disabled */
  const searchControlsDisabled =
    disabled ||
    listLoading ||
    createBusy ||
    restoreBusy ||
    nameBusy ||
    pendingNameId != null ||
    deleteBusy ||
    pendingDeleteId != null ||
    pendingRestoreId != null ||
    pinBusy;
  /** 删除入口：不依赖 props.disabled，但仍受其它操作互斥（含恢复确认/固定） */
  const deleteEntryDisabled =
    listLoading ||
    createBusy ||
    restoreBusy ||
    nameBusy ||
    pendingNameId != null ||
    pendingRestoreId != null ||
    deleteBusy ||
    pendingDeleteId != null ||
    pinBusy;
  /** 命名入口：受 list/create/restore/delete/pin 互斥 */
  const nameEntryDisabled =
    listLoading ||
    createBusy ||
    restoreBusy ||
    nameBusy ||
    pendingNameId != null ||
    pendingRestoreId != null ||
    deleteBusy ||
    pendingDeleteId != null ||
    pinBusy;
  /** 固定入口：不依赖 props.disabled；受 list/create/restore/name/delete/pin 互斥 */
  const pinEntryDisabled =
    listLoading ||
    createBusy ||
    restoreBusy ||
    nameBusy ||
    pendingNameId != null ||
    pendingRestoreId != null ||
    deleteBusy ||
    pendingDeleteId != null ||
    pinBusy;

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
          disabled={(nameUiLocked || deleteUiLocked || pinUiLocked) && expanded}
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
          <div
            data-testid="editor-state-checkpoint-search-row"
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 8,
              alignItems: "center",
              marginBottom: 8,
            }}
          >
            <label
              htmlFor="editor-state-checkpoint-search-input"
              style={{
                fontSize: 13,
                color: "var(--text-muted, #4b5563)",
              }}
            >
              {LABEL_SEARCH}
            </label>
            <input
              id="editor-state-checkpoint-search-input"
              type="text"
              data-testid="editor-state-checkpoint-search-input"
              value={searchDraft}
              disabled={searchControlsDisabled}
              onChange={(e) => {
                handleSearchDraftChange(e.target.value);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  handleSearchApply();
                }
              }}
              style={{
                fontSize: 13,
                maxWidth: "100%",
                minWidth: 140,
                padding: "4px 8px",
              }}
            />
            <button
              type="button"
              className="btn btn-soft btn-sm"
              data-testid="editor-state-checkpoint-search-apply"
              disabled={searchControlsDisabled}
              onClick={handleSearchApply}
            >
              搜索
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              data-testid="editor-state-checkpoint-search-clear"
              disabled={searchControlsDisabled}
              onClick={handleSearchClear}
            >
              清除
            </button>
          </div>
          {searchError ? (
            <p
              data-testid="editor-state-checkpoint-search-error"
              style={{ margin: "0 0 8px", color: "var(--danger)" }}
            >
              {searchError}
            </p>
          ) : null}
          {searchActive ? (
            <p
              data-testid="editor-state-checkpoint-search-active"
              style={{
                margin: "0 0 8px",
                fontSize: 13,
                color: "var(--text-muted, #4b5563)",
              }}
            >
              {MSG_SEARCH_ACTIVE}
            </p>
          ) : null}
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
              {searchActive ? "搜索检查点…" : "加载检查点列表…"}
            </p>
          ) : null}
          {!listLoading && !listError && items.length === 0 ? (
            <p
              data-testid="editor-state-checkpoint-empty"
              style={{ margin: 0, color: "var(--text-muted, #6b7280)" }}
            >
              {searchActive ? MSG_SEARCH_EMPTY : "暂无检查点"}
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
                    {item.isPinned ? (
                      <span
                        data-testid={`editor-state-checkpoint-pinned-badge-${index}`}
                        style={{ fontWeight: 600, color: "var(--text-muted, #4b5563)" }}
                      >
                        已固定
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
                          disabled={nameBusy || listLoading || deleteBusy || pinBusy}
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
                            disabled={nameBusy || listLoading || deleteBusy || pinBusy}
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
                          disabled={nameBusy || deleteBusy || pinBusy}
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
                          disabled={deleteBusy || listLoading || createBusy || restoreBusy || nameBusy || pinBusy}
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
                          disabled={deleteBusy || pinBusy}
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
                          disabled={restoreBusy || deleteBusy || pinBusy}
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
                        disabled={nameEntryDisabled || disabled}
                        onClick={() =>
                          handleNameClick(item.checkpointId, item.displayName)
                        }
                      >
                        {item.displayName != null ? "重命名" : "命名"}
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        data-testid={`editor-state-checkpoint-pin-${index}`}
                        disabled={pinEntryDisabled}
                        onClick={() => {
                          void handlePinClick(item.checkpointId, item.isPinned);
                        }}
                      >
                        {item.isPinned ? "取消固定" : "固定"}
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
