/**
 * 模块：商务标工作区状态（P11B 服务端权威 + P12B 全状态 CAS + P13-B/C/D2/H3）
 * 用途：分步编辑数据只认 GET|PUT /api/projects/{id}/editor-state；真实空态保持空；
 *       全部 editor-state PUT 携带 expectedStateVersion；同项目串行保存链；
 *       P13-B/C/D2：合法 stateVersion 被当前会话接受时同步 versionUpdatedAt、
 *       currentRevisionSourceKind 与 currentRevisionActorUsername 供标题区展示；
 *       P13-H3：同步导出 currentStateVersion（与 ref 镜像），供事件版本提示等值判断。
 * 对接：
 *   - GET|PUT /api/projects/{id}/editor-state（businessQualify/Toc/Quote/Commit、parsedMarkdown）
 *   - POST /api/projects/{id}/artifacts/workspace/revise（stage=business_*）
 *   - EditorStateVersionFreshness（只读展示，零额外请求）
 *   - EditorStateEventUpdatePanel（仅消费 currentStateVersion + refreshFromApi）
 *   - parseRevisionSourceKind / parseRevisionActorUsername（唯一校验）
 * 明确非目标：
 *   - 禁止读写/删除/迁移 biaoshu.businessBid.workspace.*（旧键忽略并保值）
 *   - biaoshu.businessBid.feedback.{projectId} 仅作 AI 反馈历史本地存储，
 *     绝不参与 workspace 水合、API 成功判定或加载失败回退
 *   - 禁止本地计算 stateVersion；禁止版本落盘
 *   - versionUpdatedAt / currentRevisionSourceKind / currentRevisionActorUsername
 *     禁止参与 CAS/保存队列/缓存键
 *   - currentStateVersion 仅镜像已接受版本，禁止参与 CAS/保存链/自动请求
 * 二次开发：形状保持 BusinessBidWorkspaceState；不得复活 createDemoWorkspace 生产路径；
 *       全状态 409 阻断后仅允许显式全量 GET 恢复；切项目须立即清空时间、来源、操作者与 currentStateVersion。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createEditorStateCheckpoint as postCreateEditorStateCheckpoint,
  isCheckpointCreateStateVersionError,
  restoreEditorStateCheckpoint as postRestoreEditorStateCheckpoint,
} from "../../editor-state-checkpoints/editorStateCheckpointApi";
import type {
  CheckpointCreateOutcome,
  CheckpointRestoreOutcome,
} from "../../editor-state-checkpoints/EditorStateCheckpointPanel";
import {
  parseRevisionActorUsername,
  parseRevisionSourceKind,
  restoreEditorStateRevision as postRestoreEditorStateRevision,
  type RevisionSourceKind,
} from "../../editor-state-revisions/editorStateRevisionApi";
import type { RevisionRestoreOutcome } from "../../editor-state-revisions/EditorStateRevisionPanel";
import { apiFetch } from "../../../shared/lib/api";
import type {
  AiFeedbackRecord,
  FeedbackStage,
} from "../../../shared/types/aiFeedback";
import { createEmptyWorkspace } from "../mock";
import type {
  BusinessBidWorkspaceState,
  CommitBlock,
  QualifyItem,
  QuoteRow,
  TocItem,
} from "../types";

/** 固定加载失败文案（脱敏，不得拼接后端原文） */
export const BUSINESS_EDITOR_LOAD_ERROR =
  "商务标工作区加载失败，请稍后重试";

/** 固定保存失败文案（脱敏，不得拼接后端原文） */
export const BUSINESS_EDITOR_SAVE_ERROR =
  "商务标工作区保存失败，请稍后重试";

/**
 * 固定全状态版本冲突文案。
 * 用途：P12B CAS 冲突固定中文；禁止拼接服务端 message/version/正文。
 */
export const BUSINESS_EDITOR_STATE_CONFLICT_MESSAGE =
  "编辑内容已被其他操作更新，已停止自动保存。重新载入远端内容将替换当前未保存修改。";

/** 服务端 stateVersion 精确格式 */
const STATE_VERSION_RE = /^esv_[0-9a-f]{32}$/;

/** 用途：校验服务端 stateVersion。 */
function isValidStateVersion(value: unknown): value is string {
  return typeof value === "string" && STATE_VERSION_RE.test(value);
}

/** 用途：版本化外部写（检查点 restore）结果；与技术标 runner 语义对齐。 */
export type BusinessVersionedExternalWriteOutcome<T> =
  | { status: "success"; data: T }
  | { status: "post_failed"; blocked: boolean }
  | { status: "reload_failed"; data: T };

/**
 * V1-E 导出前保存准备门三态（冻结契约，与技术标同语义）。
 * ready：可创建 export；
 * blocked：冲突/保存错误/非法版本/新 pending 或 generation 变化等保守阻断；
 * failed：会话/项目/写代次/PUT stale 等公共失效（内部 ImmediatePutStatus 仍可 stale）。
 */
export type ExportSaveGateResult = "ready" | "blocked" | "failed";

/**
 * 反馈历史 localStorage 键。
 * 非 editor-state 权威；仅保留既有 AI 反馈记录语义。
 */
const feedbackKey = (projectId: string) =>
  `biaoshu.businessBid.feedback.${projectId}`;

type EditorStateApi = {
  projectId?: string;
  parsedMarkdown?: string | null;
  businessQualify?: QualifyItem[] | null;
  businessToc?: TocItem[] | null;
  businessQuote?: { rows?: QuoteRow[]; notes?: string } | null;
  businessCommit?: CommitBlock[] | null;
  /** P12B：全状态版本；仅接受 ^esv_[0-9a-f]{32}$ */
  stateVersion?: string | null;
  /** P13-B：服务端权威更新时间；仅展示，不参与 CAS */
  updatedAt?: string | null;
  /** P13-C：当前版本修订来源；仅展示，须经 parseRevisionSourceKind */
  currentRevisionSourceKind?: string | null;
  /** P13-D2：当前版本操作者用户名；仅展示，须经 parseRevisionActorUsername */
  currentRevisionActorUsername?: string | null;
};

/** 用途：读取反馈历史；失败返回 []，不抛原文。 */
function loadHistory(projectId: string): AiFeedbackRecord[] {
  try {
    const raw = localStorage.getItem(feedbackKey(projectId));
    if (!raw) return [];
    return JSON.parse(raw) as AiFeedbackRecord[];
  } catch {
    return [];
  }
}

/**
 * 用途：远端 editor-state → 工作区；空数组/空字段保留为空，不回填演示 mock。
 */
function fromApi(
  projectId: string,
  remote: EditorStateApi,
): BusinessBidWorkspaceState {
  const empty = createEmptyWorkspace(projectId);
  const quote = remote.businessQuote;
  return {
    projectId,
    parseMarkdown:
      remote.parsedMarkdown != null ? String(remote.parsedMarkdown) : "",
    qualifyItems: Array.isArray(remote.businessQualify)
      ? remote.businessQualify
      : empty.qualifyItems,
    tocItems: Array.isArray(remote.businessToc)
      ? remote.businessToc
      : empty.tocItems,
    quoteRows:
      quote && Array.isArray(quote.rows) ? quote.rows : empty.quoteRows,
    quoteNotes:
      quote && typeof quote.notes === "string" ? quote.notes : empty.quoteNotes,
    commitBlocks: Array.isArray(remote.businessCommit)
      ? remote.businessCommit
      : empty.commitBlocks,
  };
}

export function useBusinessBidWorkspace(projectId: string) {
  const [workspace, setWorkspace] = useState<BusinessBidWorkspaceState>(() =>
    createEmptyWorkspace(projectId),
  );
  const [history, setHistory] = useState<AiFeedbackRecord[]>(() =>
    loadHistory(projectId),
  );
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [apiReady, setApiReady] = useState(false);
  /** 全状态 CAS 冲突/版本未知阻断 */
  const [fullStateConflict, setFullStateConflict] = useState(false);
  /**
   * P13-B：当前项目会话已接受的服务端 updatedAt（仅展示）。
   * 切项目立即清空；仅在合法 stateVersion 被接受时更新。
   */
  const [versionUpdatedAt, setVersionUpdatedAt] = useState<string | null>(null);
  /**
   * P13-C：当前项目会话已接受的修订来源（仅展示）。
   * 与 versionUpdatedAt 同一合法 stateVersion 门；切项目立即清空。
   */
  const [currentRevisionSourceKind, setCurrentRevisionSourceKind] =
    useState<RevisionSourceKind | null>(null);
  const [currentRevisionActorUsername, setCurrentRevisionActorUsername] =
    useState<string | null>(null);
  /**
   * P13-H3：当前项目会话已接受的 stateVersion（与 stateVersionRef 镜像，仅导出供提示）。
   * 合法接受路径同步更新；切项目/非法/既有清空路径同步 null；不参与 CAS/保存。
   */
  const [currentStateVersion, setCurrentStateVersion] = useState<string | null>(
    null,
  );

  /** 跳过水合后的下一次防抖 PUT */
  const skipNextSave = useRef(true);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /**
   * V1-E：autosave 调度/编辑代次（纯内存，与技术标同语义）。
   * 每次真正计划新防抖保存时单调递增；timer 触发/清除不得回退。
   * flush 在调用瞬间捕获；ready 前精确比较；generation 增长返回 blocked。
   */
  const autosaveGenerationRef = useRef(0);
  /** 项目会话代次：切项目或重置时递增，作废所有飞行中回调 */
  const sessionRef = useRef(0);
  /** 同项目写入代次：重载时递增，旧代次回调不得覆盖新 GET */
  const writeEpochRef = useRef(0);
  /** 当前活跃项目 id（与 session 成对校验） */
  const activeProjectRef = useRef(projectId);
  /** 最新 workspace 引用，供队列执行时读取 */
  const workspaceRef = useRef(workspace);
  workspaceRef.current = workspace;
  /** 当前内存服务端全状态版本 */
  const stateVersionRef = useRef<string | null>(null);
  /** 全状态阻断后禁止自动 PUT */
  const fullStateBlockedRef = useRef(false);
  /**
   * 创建/恢复操作 token（项目绑定）：
   * - 同项目连点：token 已属本项目 → 拒绝第二请求
   * - 不同项目：允许新项目启动并把 ref 覆盖为新 token
   * - finally 仅当 projectId+token 仍匹配时清空，绝不能误清新项目 token
   * 禁止：跨项目共享 boolean，或切项目时简单 boolean=false
   */
  const checkpointOpTokenRef = useRef<{
    projectId: string;
    token: number;
  } | null>(null);
  const checkpointOpTokenSeqRef = useRef(0);
  /** 同项目串行保存链 */
  const saveChainRef = useRef(Promise.resolve());
  /**
   * V1-E：最近一次可判定的保存/水合结果，供导出准备门只读判断。
   * 切项目重置；水合成功 / 即时 PUT / 外部写成功时更新。
   */
  const lastSaveStatusRef = useRef<
    | "ok"
    | "stale"
    | "blocked"
    | "full_conflict"
    | "error"
    | "invalid_version"
    | null
  >(null);

  /** 用途：判断回调是否仍属于当前项目会话。 */
  const isCurrentSession = useCallback(
    (session: number, pid: string) =>
      sessionRef.current === session &&
      activeProjectRef.current === pid &&
      pid === projectId,
    [projectId],
  );

  const isCurrentWriteEpoch = useCallback(
    (session: number, pid: string, epoch: number) =>
      isCurrentSession(session, pid) && writeEpochRef.current === epoch,
    [isCurrentSession],
  );

  const enterFullStateBlock = useCallback(() => {
    fullStateBlockedRef.current = true;
    setFullStateConflict(true);
    setSaveError(null);
  }, []);

  /**
   * 用途：P13-B 在合法 stateVersion 已被接受后，同步同一响应的 updatedAt 供展示。
   * 对接：仅 string 原样接受；null/缺失/非字符串记为 null（组件显示未知）。
   */
  const acceptVersionUpdatedAt = useCallback((updatedAt: unknown) => {
    setVersionUpdatedAt(typeof updatedAt === "string" ? updatedAt : null);
  }, []);

  /**
   * 用途：P13-C 在合法 stateVersion 已被接受后，同步同一响应的来源供展示。
   * 对接：parseRevisionSourceKind 唯一九类精确匹配；非法一律 null。
   */
  const acceptCurrentRevisionSourceKind = useCallback((value: unknown) => {
    setCurrentRevisionSourceKind(parseRevisionSourceKind(value));
  }, []);

  /**
   * 用途：P13-D2 在合法 stateVersion 已被接受后，同步同一响应的操作者用户名。
   * 对接：parseRevisionActorUsername 安全文本门；非法/缺失一律 null。
   */
  const acceptCurrentRevisionActorUsername = useCallback((value: unknown) => {
    setCurrentRevisionActorUsername(parseRevisionActorUsername(value));
  }, []);

  /**
   * 用途：仅接受合法服务端 stateVersion 写入内存 ref，并镜像到 currentStateVersion。
   * 对接：P13-H3 导出等值判断；非法/清空时 ref 与 React 状态同步 null。
   */
  const applyStateVersion = useCallback((version: unknown) => {
    if (!isValidStateVersion(version)) {
      stateVersionRef.current = null;
      setCurrentStateVersion(null);
      return false;
    }
    stateVersionRef.current = version;
    setCurrentStateVersion(version);
    return true;
  }, []);

  /**
   * 用途：从 editor-state 刷新当前项目。
   * V1-G：入口先校验闭包项目/会话仍当前，失效则零 writeEpoch/timer/loading/GET 并返回 false。
   * 成功：水合真实字段、接受合法 stateVersion、清冲突、apiReady=true。
   * 失败：见全状态阻断分支；不抛原文。
   * 二次开发：silent 检查点恢复语义不得改；初始 GET/P11 冲突失败卡保持既有。
   */
  const refreshFromApi = useCallback(async (options?: { silent?: boolean }): Promise<boolean> => {
    const pid = projectId;
    if (!pid) {
      setLoading(false);
      return false;
    }
    // V1-G：入口早退——闭包项目已非当前活跃项目/会话时，零 writeEpoch/timer/loading/GET。
    // 捕获入口 session；与 active 不一致说明切项目后旧闭包迟到，直接 false。
    const session = sessionRef.current;
    if (
      activeProjectRef.current !== pid ||
      !isCurrentSession(session, pid)
    ) {
      return false;
    }
    const silent = options?.silent === true;
    // 同项目重载：递增写入代次并清未发送 timer
    writeEpochRef.current += 1;
    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    // silent：检查点 restore 唯一 GET，避免 loading 卸载工作区导致面板状态丢失
    if (!silent) setLoading(true);
    try {
      const remote = await apiFetch<EditorStateApi>(
        `/projects/${encodeURIComponent(pid)}/editor-state`,
      );
      if (!isCurrentSession(session, pid)) return false;
      if (!isValidStateVersion(remote.stateVersion)) {
        if (fullStateBlockedRef.current) {
          // 保持本地与阻断；不卸载正文
          setLoadError(BUSINESS_EDITOR_LOAD_ERROR);
          return false;
        }
        skipNextSave.current = true;
        setWorkspace(createEmptyWorkspace(pid));
        applyStateVersion(null);
        setApiReady(false);
        lastSaveStatusRef.current = null;
        setLoadError(BUSINESS_EDITOR_LOAD_ERROR);
        setSaveError(null);
        setFullStateConflict(false);
        fullStateBlockedRef.current = false;
        return false;
      }
      skipNextSave.current = true;
      setWorkspace(fromApi(pid, remote));
      applyStateVersion(remote.stateVersion);
      acceptVersionUpdatedAt(remote.updatedAt);
      acceptCurrentRevisionSourceKind(remote.currentRevisionSourceKind);
      acceptCurrentRevisionActorUsername(remote.currentRevisionActorUsername);
      setApiReady(true);
      // V1-E：合法水合成功视为最近结果可用，无待保存时导出门可 ready 且零 PUT
      lastSaveStatusRef.current = "ok";
      setLoadError(null);
      setSaveError(null);
      setFullStateConflict(false);
      fullStateBlockedRef.current = false;
      return true;
    } catch {
      if (!isCurrentSession(session, pid)) return false;
      if (fullStateBlockedRef.current) {
        setLoadError(BUSINESS_EDITOR_LOAD_ERROR);
        return false;
      }
      skipNextSave.current = true;
      setWorkspace(createEmptyWorkspace(pid));
      applyStateVersion(null);
      setApiReady(false);
      lastSaveStatusRef.current = null;
      setLoadError(BUSINESS_EDITOR_LOAD_ERROR);
      setSaveError(null);
      setFullStateConflict(false);
      fullStateBlockedRef.current = false;
      return false;
    } finally {
      if (!silent && isCurrentSession(session, pid)) {
        setLoading(false);
      }
    }
  }, [
    acceptVersionUpdatedAt,
    acceptCurrentRevisionSourceKind,
    acceptCurrentRevisionActorUsername,
    applyStateVersion,
    isCurrentSession,
    projectId,
  ]);

  // 切项目：立即作废旧会话、清计时器/错误/版本/链，重置空 workspace，再拉真实 GET
  useEffect(() => {
    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    sessionRef.current += 1;
    writeEpochRef.current += 1;
    // V1-E：切项目递增 generation，旧 flush 不得与新项目代次偶然相等后放行
    autosaveGenerationRef.current += 1;
    activeProjectRef.current = projectId;
    saveChainRef.current = Promise.resolve();
    // V1-E：切项目重置最近保存结果，禁止旧项目 ok 误放行新项目导出
    lastSaveStatusRef.current = null;
    skipNextSave.current = true;
    // P13-H3：切项目立即清空版本镜像，禁止旧项目版本参与新提示
    applyStateVersion(null);
    // P13-B/C/D2：切项目立即清空，禁止短暂显示旧项目时间/来源/操作者
    setVersionUpdatedAt(null);
    setCurrentRevisionSourceKind(null);
    setCurrentRevisionActorUsername(null);
    fullStateBlockedRef.current = false;
    setApiReady(false);
    setLoadError(null);
    setSaveError(null);
    setFullStateConflict(false);
    setWorkspace(createEmptyWorkspace(projectId));
    setHistory(loadHistory(projectId));
    setLoading(true);
    void refreshFromApi();
  }, [projectId, refreshFromApi, applyStateVersion]);

  // 仅 feedback 历史落盘；禁止写入 workspace 键或版本
  useEffect(() => {
    if (!projectId) return;
    try {
      localStorage.setItem(feedbackKey(projectId), JSON.stringify(history));
    } catch {
      // 存储失败静默；不得 console 敏感内容
    }
  }, [projectId, history]);


  /**
   * 用途：共享「构造最新 body + 执行 PUT + 接受版本/冲突处理」执行器。
   * 对接：普通防抖 autosave 与显式创建检查点强制即时 PUT 共用；禁止第二套 body。
   */
  type ImmediatePutStatus =
    | "ok"
    | "stale"
    | "blocked"
    | "full_conflict"
    | "error"
    | "invalid_version";

  const executeImmediateEditorStatePut = useCallback(
    async (
      session: number,
      pid: string,
      epoch: number,
    ): Promise<ImmediatePutStatus> => {
      /** 用途：仅当前写代次回写最近结果，供 V1-E 导出门判断。 */
      const finish = (status: ImmediatePutStatus): ImmediatePutStatus => {
        if (isCurrentWriteEpoch(session, pid, epoch)) {
          lastSaveStatusRef.current = status;
        }
        return status;
      };
      if (!isCurrentWriteEpoch(session, pid, epoch)) return "stale";
      if (fullStateBlockedRef.current) return finish("blocked");
      const expected = stateVersionRef.current;
      if (!isValidStateVersion(expected)) {
        enterFullStateBlock();
        return finish("invalid_version");
      }
      const ws = workspaceRef.current;
      try {
        const saved = await apiFetch<EditorStateApi>(
          `/projects/${encodeURIComponent(pid)}/editor-state`,
          {
            method: "PUT",
            body: JSON.stringify({
              parsedMarkdown: ws.parseMarkdown,
              businessQualify: ws.qualifyItems,
              businessToc: ws.tocItems,
              businessQuote: {
                rows: ws.quoteRows,
                notes: ws.quoteNotes,
              },
              businessCommit: ws.commitBlocks,
              expectedStateVersion: expected,
            }),
          },
        );
        if (!isCurrentWriteEpoch(session, pid, epoch)) return "stale";
        if (!isValidStateVersion(saved.stateVersion)) {
          enterFullStateBlock();
          return finish("invalid_version");
        }
        applyStateVersion(saved.stateVersion);
        acceptVersionUpdatedAt(saved.updatedAt);
        acceptCurrentRevisionSourceKind(saved.currentRevisionSourceKind);
        acceptCurrentRevisionActorUsername(saved.currentRevisionActorUsername);
        setSaveError(null);
        return finish("ok");
      } catch (err) {
        if (!isCurrentWriteEpoch(session, pid, epoch)) return "stale";
        const status = (err as { status?: number })?.status;
        const code = (err as { code?: string })?.code;
        if (status === 409 && code === "editor_state_version_conflict") {
          enterFullStateBlock();
          return finish("full_conflict");
        }
        setSaveError(BUSINESS_EDITOR_SAVE_ERROR);
        return finish("error");
      }
    },
    [
      acceptVersionUpdatedAt,
      acceptCurrentRevisionSourceKind,
      acceptCurrentRevisionActorUsername,
      applyStateVersion,
      enterFullStateBlock,
      isCurrentWriteEpoch,
    ],
  );

  // 防抖入队：真正执行时读取 workspaceRef + stateVersionRef 最新值
  useEffect(() => {
    if (!apiReady || !projectId) return;
    if (skipNextSave.current) {
      skipNextSave.current = false;
      return;
    }
    if (fullStateBlockedRef.current) {
      return;
    }
    if (saveTimer.current) clearTimeout(saveTimer.current);
    // V1-E：真正计划新防抖保存时单调递增；timer 触发/清除不得回退
    autosaveGenerationRef.current += 1;
    const session = sessionRef.current;
    const pid = projectId;
    const epoch = writeEpochRef.current;
    saveTimer.current = setTimeout(() => {
      // 定时器已触发：立即清空，避免误判为仍有 pending timer（generation 不回退）
      saveTimer.current = null;
      const runSave = async () => {
        await executeImmediateEditorStatePut(session, pid, epoch);
      };
      saveChainRef.current = saveChainRef.current
        .catch(() => undefined)
        .then(runSave);
    }, 600);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [
    apiReady,
    executeImmediateEditorStatePut,
    projectId,
    workspace,
  ]);

  const setParseMarkdown = useCallback((parseMarkdown: string) => {
    setWorkspace((prev) => ({ ...prev, parseMarkdown }));
  }, []);

  const updateQualifyItem = useCallback(
    (id: string, patch: Partial<QualifyItem>) => {
      setWorkspace((prev) => ({
        ...prev,
        qualifyItems: prev.qualifyItems.map((item) =>
          item.id === id ? { ...item, ...patch } : item,
        ),
      }));
    },
    [],
  );

  const toggleTocItem = useCallback((id: string) => {
    setWorkspace((prev) => ({
      ...prev,
      tocItems: prev.tocItems.map((item) =>
        item.id === id ? { ...item, checked: !item.checked } : item,
      ),
    }));
  }, []);

  const updateTocItem = useCallback((id: string, patch: Partial<TocItem>) => {
    setWorkspace((prev) => ({
      ...prev,
      tocItems: prev.tocItems.map((item) =>
        item.id === id ? { ...item, ...patch } : item,
      ),
    }));
  }, []);

  const updateQuoteRow = useCallback((id: string, patch: Partial<QuoteRow>) => {
    setWorkspace((prev) => ({
      ...prev,
      quoteRows: prev.quoteRows.map((row) =>
        row.id === id ? { ...row, ...patch } : row,
      ),
    }));
  }, []);

  const setQuoteNotes = useCallback((quoteNotes: string) => {
    setWorkspace((prev) => ({ ...prev, quoteNotes }));
  }, []);

  const updateCommitBlock = useCallback(
    (id: string, patch: Partial<CommitBlock>) => {
      setWorkspace((prev) => ({
        ...prev,
        commitBlocks: prev.commitBlocks.map((block) =>
          block.id === id ? { ...block, ...patch } : block,
        ),
      }));
    },
    [],
  );

  const submitRevise = useCallback(
    async (input: {
      stage: FeedbackStage;
      message: string;
      preserveStructure: boolean;
      targetId?: string;
      targetLabel?: string;
    }) => {
      const id = `bb_fb_${Date.now()}`;
      const applying: AiFeedbackRecord = {
        id,
        stage: input.stage,
        message: input.message,
        targetId: input.targetId,
        targetLabel: input.targetLabel,
        createdAt: new Date().toISOString(),
        status: "applying",
      };
      setHistory((prev) => [applying, ...prev]);

      const writesState =
        input.stage === "business_parse" ||
        input.stage === "business_qualify" ||
        input.stage === "business_toc" ||
        input.stage === "business_quote" ||
        input.stage === "business_commit";

      /**
       * 用途：商务 revise 进入既有 saveChainRef；真正执行时读最新 expected；
       * 成功后阻断旧本地写并单次 refresh；409/非法版本/网络不确定均保留本地并阻断。
       */
      const runRevise = async () => {
        const session = sessionRef.current;
        const pid = projectId;
        if (!isCurrentSession(session, pid)) {
          throw new Error("修订已取消");
        }

        const ws = workspaceRef.current;
        let baseContent = ws.parseMarkdown;
        if (input.stage === "business_qualify") {
          baseContent = JSON.stringify(ws.qualifyItems, null, 2);
        } else if (input.stage === "business_toc") {
          baseContent = JSON.stringify(ws.tocItems, null, 2);
        } else if (input.stage === "business_quote") {
          baseContent = JSON.stringify(
            { rows: ws.quoteRows, notes: ws.quoteNotes },
            null,
            2,
          );
        } else if (input.stage === "business_commit") {
          baseContent = JSON.stringify(ws.commitBlocks, null, 2);
        }

        const body: Record<string, unknown> = {
          stage: input.stage,
          message: input.message,
          preserveStructure: input.preserveStructure,
          baseContent,
          targetId: input.targetId,
          targetLabel: input.targetLabel,
        };

        if (writesState) {
          if (fullStateBlockedRef.current) {
            const err = new Error(BUSINESS_EDITOR_STATE_CONFLICT_MESSAGE) as Error & {
              status?: number;
              code?: string;
            };
            err.status = 409;
            err.code = "editor_state_version_conflict";
            throw err;
          }
          const expected = stateVersionRef.current;
          if (!isValidStateVersion(expected)) {
            enterFullStateBlock();
            throw new Error(BUSINESS_EDITOR_STATE_CONFLICT_MESSAGE);
          }
          body.expectedStateVersion = expected;
        }

        let res: {
          resultSummary?: string;
          revisedContent?: string | null;
          status?: string;
          stateVersion?: string | null;
        };
        try {
          res = await apiFetch(
            `/projects/${encodeURIComponent(pid)}/artifacts/workspace/revise`,
            {
              method: "POST",
              body: JSON.stringify(body),
            },
          );
        } catch (err) {
          if (!isCurrentSession(session, pid)) throw err;
          if (writesState) {
            const status = (err as { status?: number })?.status;
            const code = (err as { code?: string })?.code;
            if (
              status === 409 &&
              code === "editor_state_version_conflict"
            ) {
              // 陈旧：保留本地，阻断旧自动 PUT
              enterFullStateBlock();
            } else {
              // 网络不确定 / 其它失败：保守阻断，禁止旧 UI 自动保存
              enterFullStateBlock();
            }
          }
          throw err;
        }

        if (!isCurrentSession(session, pid)) return res;

        if (writesState) {
          // 成功响应必须带合法新版本；否则阻断且不自动覆盖本地
          if (!isValidStateVersion(res.stateVersion)) {
            enterFullStateBlock();
            setHistory((prev) =>
              prev.map((h) =>
                h.id === id
                  ? {
                      ...h,
                      status: "failed" as const,
                      resultSummary: "修订响应缺少有效版本，已停止自动保存",
                    }
                  : h,
              ),
            );
            throw new Error(BUSINESS_EDITOR_STATE_CONFLICT_MESSAGE);
          }
          // 先接受服务端新版本并阻断，再单次 refresh；成功才解除阻断
          applyStateVersion(res.stateVersion);
          fullStateBlockedRef.current = true;
          setFullStateConflict(true);
          const ok = await refreshFromApi();
          if (!isCurrentSession(session, pid)) return res;
          if (!ok) {
            // 重读失败：保持本地与阻断
            enterFullStateBlock();
          }
        }

        setHistory((prev) =>
          prev.map((h) =>
            h.id === id
              ? {
                  ...h,
                  status: "applied" as const,
                  resultSummary:
                    res.resultSummary ||
                    res.revisedContent?.slice(0, 120) ||
                    "已修订",
                }
              : h,
          ),
        );
        return res;
      };

      const queued = saveChainRef.current
        .catch(() => undefined)
        .then(runRevise);
      // 后续保存等待 revise 完成；revise 失败不卡死整条链（链上仅 void）
      saveChainRef.current = queued
        .then(() => undefined)
        .catch(() => undefined);

      try {
        return await queued;
      } catch {
        // 页面以 void 调用 submitRevise；此处不得再抛，避免 unhandled rejection
        // 固定脱敏失败摘要，禁止把异常正文写入 UI/history/console
        const msg =
          writesState && fullStateBlockedRef.current
            ? BUSINESS_EDITOR_STATE_CONFLICT_MESSAGE
            : "修订失败";
        setHistory((prev) =>
          prev.map((h) =>
            h.id === id
              ? { ...h, status: "failed" as const, resultSummary: msg }
              : h,
          ),
        );
        return undefined;
      }
    },
    [
      applyStateVersion,
      enterFullStateBlock,
      isCurrentSession,
      projectId,
      refreshFromApi,
    ],
  );


  /**
   * 用途：P12B-D2 版本化外部写 runner（检查点 restore）；与技术标语义对齐。
   * 约束：进入 saveChainRef；执行时读最新 expected；成功唯一 refreshFromApi；零自动重试。
   */
  const runVersionedExternalWrite = useCallback(
    async <T extends { stateVersion: string }>(
      execute: (expectedStateVersion: string) => Promise<T>,
    ): Promise<BusinessVersionedExternalWriteOutcome<T>> => {
      const requestSession = sessionRef.current;
      const requestPid = projectId;
      const requestEpoch = writeEpochRef.current;

      const run = async (): Promise<BusinessVersionedExternalWriteOutcome<T>> => {
        if (!isCurrentWriteEpoch(requestSession, requestPid, requestEpoch)) {
          return { status: "post_failed", blocked: false };
        }
        if (fullStateBlockedRef.current) {
          return { status: "post_failed", blocked: true };
        }
        const expected = stateVersionRef.current;
        if (!isValidStateVersion(expected)) {
          enterFullStateBlock();
          return { status: "post_failed", blocked: true };
        }

        let data: T;
        try {
          data = await execute(expected);
        } catch {
          if (!isCurrentWriteEpoch(requestSession, requestPid, requestEpoch)) {
            return { status: "post_failed", blocked: false };
          }
          enterFullStateBlock();
          return { status: "post_failed", blocked: true };
        }

        if (!isCurrentWriteEpoch(requestSession, requestPid, requestEpoch)) {
          return { status: "post_failed", blocked: false };
        }
        if (!isValidStateVersion(data.stateVersion)) {
          enterFullStateBlock();
          return { status: "post_failed", blocked: true };
        }

        applyStateVersion(data.stateVersion);
        fullStateBlockedRef.current = true;
        setFullStateConflict(true);
        if (saveTimer.current) {
          clearTimeout(saveTimer.current);
          saveTimer.current = null;
        }

        const reloaded = await refreshFromApi({ silent: true });
        if (!isCurrentSession(requestSession, requestPid)) {
          return { status: "reload_failed", data };
        }
        if (!reloaded) {
          enterFullStateBlock();
          return { status: "reload_failed", data };
        }
        // 禁止在此将 skipNextSave 置 false：
        // refreshFromApi 水合已设 skipNextSave=true，须由 effect 只吞一次水合触发的
        // 自动保存；若此处清零会形成恢复后旧 UI 自动 PUT。
        // 用户下一次真实编辑时 skip 已消费完毕，PUT 正常发出。
        // V1-E：外部写+重读成功，最近结果可用
        lastSaveStatusRef.current = "ok";
        return { status: "success", data };
      };

      const queued = saveChainRef.current.catch(() => undefined).then(run);
      saveChainRef.current = queued
        .then(() => undefined)
        .catch(() => undefined);
      return queued;
    },
    [
      projectId,
      isCurrentWriteEpoch,
      isCurrentSession,
      applyStateVersion,
      enterFullStateBlock,
      refreshFromApi,
    ],
  );

  /**
   * 用途：P12B-D2 显式创建检查点——清 timer、串行链内强制即时 PUT，再 POST 精确 {}。
   */
  const createCheckpoint = useCallback(async (): Promise<CheckpointCreateOutcome> => {
    if (!projectId) return { status: "blocked" };
    if (fullStateBlockedRef.current) return { status: "blocked" };
    if (!isValidStateVersion(stateVersionRef.current)) return { status: "blocked" };
    const requestPid = projectId;
    const existingOp = checkpointOpTokenRef.current;
    if (existingOp && existingOp.projectId === requestPid) {
      return { status: "failed" };
    }
    const myToken = ++checkpointOpTokenSeqRef.current;
    checkpointOpTokenRef.current = { projectId: requestPid, token: myToken };

    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }

    const requestSession = sessionRef.current;

    const run = async (): Promise<CheckpointCreateOutcome> => {
      try {
        if (!isCurrentSession(requestSession, requestPid)) {
          return { status: "failed" };
        }
        if (fullStateBlockedRef.current) return { status: "blocked" };
        if (!isValidStateVersion(stateVersionRef.current)) {
          return { status: "blocked" };
        }

        const epoch = writeEpochRef.current;
        const putStatus = await executeImmediateEditorStatePut(
          requestSession,
          requestPid,
          epoch,
        );
        if (!isCurrentSession(requestSession, requestPid)) {
          return { status: "failed" };
        }
        if (putStatus === "ok") {
          const putVersion = stateVersionRef.current;
          if (!isValidStateVersion(putVersion)) {
            enterFullStateBlock();
            return { status: "blocked" };
          }
          try {
            const meta = await postCreateEditorStateCheckpoint(requestPid);
            if (!isCurrentSession(requestSession, requestPid)) {
              return { status: "failed" };
            }
            // POST 版本不等于已接受 PUT 版本：远端期间可能已变，全状态阻断
            // （缺失/空白/非法 stateVersion 由 parse 专用错误在 catch 中阻断）
            if (
              !isValidStateVersion(meta.stateVersion) ||
              meta.stateVersion !== putVersion
            ) {
              enterFullStateBlock();
              return { status: "blocked" };
            }
            return { status: "success" };
          } catch (err) {
            if (!isCurrentSession(requestSession, requestPid)) {
              return { status: "failed" };
            }
            // 仅 create 成功体 stateVersion 语义失败 → 全量阻断；
            // 网络/HTTP/额外字段等普通 shape 失败仍 failed，禁止扩大阻断语义
            if (isCheckpointCreateStateVersionError(err)) {
              enterFullStateBlock();
              return { status: "blocked" };
            }
            return { status: "failed" };
          }
        }
        if (
          putStatus === "full_conflict" ||
          putStatus === "invalid_version" ||
          putStatus === "blocked"
        ) {
          return { status: "blocked" };
        }
        // forced-create：网络 abort / 非 409 HTTP / 不可判定失败 → 保守全状态阻断
        // 普通防抖 PUT 的 error 语义不变（executeImmediateEditorStatePut 仅 setSaveError）
        if (putStatus === "error") {
          enterFullStateBlock();
          return { status: "blocked" };
        }
        return { status: "failed" };
      } finally {
        const cur = checkpointOpTokenRef.current;
        if (cur && cur.projectId === requestPid && cur.token === myToken) {
          checkpointOpTokenRef.current = null;
        }
      }
    };

    const queued = saveChainRef.current.catch(() => undefined).then(run);
    saveChainRef.current = queued
      .then(() => undefined)
      .catch(() => undefined);
    return queued;
  }, [
    projectId,
    isCurrentSession,
    executeImmediateEditorStatePut,
    enterFullStateBlock,
  ]);

  /**
   * 用途：V1-E 导出前保存准备门——等待/落盘最新 editor-state，三态结果供页面消费。
   * 规则与技术标同语义：
   *   - 调用瞬间捕获 project/session/writeEpoch/generation，run 内不得重新吸收；
   *   - pending timer 原子清除后仅追加一次即时 PUT；无 timer 只等链零额外 PUT；
   *   - ready 前精确比较 session/project/epoch/generation 未变且 timer null；
   *   - generation 增长 → blocked；session/epoch/put stale → failed。
   */
  const flushPendingSaveForExport =
    useCallback(async (): Promise<ExportSaveGateResult> => {
      if (!projectId) return "blocked";
      // 调用瞬间捕获 fence，禁止 run 内重新吸收新 epoch/generation
      const requestPid = projectId;
      const requestSession = sessionRef.current;
      const requestEpoch = writeEpochRef.current;
      const requestGeneration = autosaveGenerationRef.current;

      let hadPendingTimer = false;
      if (saveTimer.current != null) {
        clearTimeout(saveTimer.current);
        saveTimer.current = null;
        hadPendingTimer = true;
      }

      /** ready 前统一复核；任一 fence 变化不得放行 */
      const fenceBeforeReady = (): ExportSaveGateResult | null => {
        if (!isCurrentSession(requestSession, requestPid)) {
          return "failed";
        }
        if (writeEpochRef.current !== requestEpoch) {
          return "failed";
        }
        if (autosaveGenerationRef.current !== requestGeneration) {
          return "blocked";
        }
        if (saveTimer.current != null) {
          return "blocked";
        }
        return null;
      };

      const run = async (): Promise<ExportSaveGateResult> => {
        const early = fenceBeforeReady();
        if (early) return early;

        if (hadPendingTimer) {
          if (fullStateBlockedRef.current) {
            return "blocked";
          }
          if (!isValidStateVersion(stateVersionRef.current)) {
            enterFullStateBlock();
            lastSaveStatusRef.current = "invalid_version";
            return "blocked";
          }
          const putStatus = await executeImmediateEditorStatePut(
            requestSession,
            requestPid,
            requestEpoch,
          );
          const afterPut = fenceBeforeReady();
          if (afterPut) return afterPut;
          if (putStatus === "ok") {
            if (!isValidStateVersion(stateVersionRef.current)) {
              return "blocked";
            }
            return "ready";
          }
          // 内部 ImmediatePutStatus.stale → 公共 failed
          if (putStatus === "stale") {
            return "failed";
          }
          return "blocked";
        }

        // 无 pending：只读最近结果，不发新 PUT
        if (fullStateBlockedRef.current) {
          return "blocked";
        }
        if (!isValidStateVersion(stateVersionRef.current)) {
          return "blocked";
        }
        const last = lastSaveStatusRef.current;
        if (last === "ok") {
          return "ready";
        }
        if (last === "stale") {
          return "failed";
        }
        return "blocked";
      };

      const queued = saveChainRef.current.catch(() => undefined).then(run);
      saveChainRef.current = queued
        .then(() => undefined)
        .catch(() => undefined);
      return queued;
    }, [
      projectId,
      isCurrentSession,
      executeImmediateEditorStatePut,
      enterFullStateBlock,
    ]);

  /**
   * 用途：P12B-D2 检查点安全恢复。
   */
  const restoreCheckpoint = useCallback(
    async (checkpointId: string): Promise<CheckpointRestoreOutcome> => {
      if (!projectId) return { status: "blocked" };
      if (fullStateBlockedRef.current) return { status: "blocked" };
      if (!isValidStateVersion(stateVersionRef.current)) {
        return { status: "blocked" };
      }
      const requestPid = projectId;
      const existingOp = checkpointOpTokenRef.current;
      if (existingOp && existingOp.projectId === requestPid) {
        return { status: "post_failed" };
      }
      const myToken = ++checkpointOpTokenSeqRef.current;
      checkpointOpTokenRef.current = { projectId: requestPid, token: myToken };
      if (saveTimer.current) {
        clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }

      try {
        const outcome = await runVersionedExternalWrite((expectedStateVersion) =>
          postRestoreEditorStateCheckpoint(
            requestPid,
            checkpointId,
            expectedStateVersion,
          ),
        );
        if (outcome.status === "success") return { status: "success" };
        if (outcome.status === "reload_failed") {
          return { status: "reload_failed" };
        }
        return outcome.blocked
          ? { status: "blocked" }
          : { status: "post_failed" };
      } finally {
        const cur = checkpointOpTokenRef.current;
        if (cur && cur.projectId === requestPid && cur.token === myToken) {
          checkpointOpTokenRef.current = null;
        }
      }
    },
    [projectId, runVersionedExternalWrite],
  );

  /**
   * 用途：P12C-C3 修订受限恢复——复用检查点操作令牌与版本化外部写 runner。
   * 约束：执行时读最新 expected；成功唯一 GET；零自动重试。
   */
  const restoreRevision = useCallback(
    async (revisionId: string): Promise<RevisionRestoreOutcome> => {
      if (!projectId) return { status: "blocked" };
      if (fullStateBlockedRef.current) return { status: "blocked" };
      if (!isValidStateVersion(stateVersionRef.current)) {
        return { status: "blocked" };
      }
      const requestPid = projectId;
      const existingOp = checkpointOpTokenRef.current;
      if (existingOp && existingOp.projectId === requestPid) {
        return { status: "post_failed" };
      }
      const myToken = ++checkpointOpTokenSeqRef.current;
      checkpointOpTokenRef.current = { projectId: requestPid, token: myToken };
      if (saveTimer.current) {
        clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }

      try {
        const outcome = await runVersionedExternalWrite((expectedStateVersion) =>
          postRestoreEditorStateRevision(
            requestPid,
            revisionId,
            expectedStateVersion,
          ),
        );
        if (outcome.status === "success") return { status: "success" };
        if (outcome.status === "reload_failed") {
          return { status: "reload_failed" };
        }
        return outcome.blocked
          ? { status: "blocked" }
          : { status: "post_failed" };
      } finally {
        const cur = checkpointOpTokenRef.current;
        if (cur && cur.projectId === requestPid && cur.token === myToken) {
          checkpointOpTokenRef.current = null;
        }
      }
    },
    [projectId, runVersionedExternalWrite],
  );

  return {
    workspace,
    history,
    loading,
    loadError,
    saveError,
    apiReady,
    fullStateConflict,
    fullStateConflictMessage: fullStateConflict
      ? BUSINESS_EDITOR_STATE_CONFLICT_MESSAGE
      : null,
    /** P13-B：当前已载入版本的服务端 updatedAt（仅展示） */
    versionUpdatedAt,
    /** P13-C：当前已载入版本的修订来源（仅展示） */
    currentRevisionSourceKind,
    /** P13-D2：当前已载入版本的操作者用户名（仅展示） */
    currentRevisionActorUsername,
    /** P13-H3：当前已载入 stateVersion（与 ref 镜像，仅供事件提示等值判断） */
    currentStateVersion,
    refreshFromApi,
    createCheckpoint,
    /** V1-E：导出前保存准备门（ready/blocked/failed） */
    flushPendingSaveForExport,
    restoreCheckpoint,
    restoreRevision,
    setParseMarkdown,
    updateQualifyItem,
    toggleTocItem,
    updateTocItem,
    updateQuoteRow,
    setQuoteNotes,
    updateCommitBlock,
    submitRevise,
  };
}
