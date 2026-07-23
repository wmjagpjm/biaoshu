/**
 * 模块：商务标分步工作区（含 P13-B/C/D2/H3 版本展示、P13-F2 近期成员、P13-I3/I4 任务事件提示、V1-G 任务成功刷新围栏）
 * 用途：六步流水线；上传/解析/biz_* 生成/导出接 project/task/editor-state；
 *       标题区展示已载入版本 UTC 时间/来源/操作者，以及项目近期成员短租约快照；
 *       薄挂载 EditorStateEventUpdatePanel 做远端版本变化提示；
 *       薄挂载 ProjectTaskEventPanel 做项目任务事件安全提示（不自动请求详情）；
 *       P13-I4 经 onSafeTaskEvent 接入 useProjectPipeline.reconcileCurrentTaskStatus，仅做当前任务安全状态对账；
 *       V1-G：runBizTask 以 startedProjectId+generation 门禁 success 后 refresh/步进/setProject。
 * 对接：useProjectPipeline（含 reconcileCurrentTaskStatus）、useBusinessBidWorkspace、GET project、
 *       useWorkspaceParseStrategy、EditorStateVersionFreshness（testid=business-editor-version-freshness 等）、
 *       ProjectPresencePanel（testid=business-project-presence）、
 *       EditorStateEventUpdatePanel（testid=business-editor-state-event-update）、
 *       ProjectTaskEventPanel（testid=business-project-task-event-update，onSafeTaskEvent）。
 * 二次开发：勿大改步骤信息架构；新任务类型扩在 pipeline TaskType；解析入口统一 handleParse（light|managed|local|ask）。
 *       项目详情只认 GET /api/projects/{id}，禁止 mockBusinessProjects 复活。
 *       P11B：editor-state 加载失败显示固定失败卡，禁止挂步骤/表格/编辑控件。
 *       版本/presence 文案不得称远端最新/实时/在线/正在编辑；用户名只作文本节点。
 *       任务事件安全对账仅限 reconcileCurrentTaskStatus，不自动拉详情；presence 提示不进入 editor Hook。
 *       export 保持独立围栏，不得并回 runBizTask 副作用链；
 *       managed 失败不得 fallback lightweight，固定中文 + 人工本地回传入口。
 *       A13：runBizTask 统一消费 pipeline.runTask transport rejection，保留 pipeline.error，不 rethrow。
 *       A2/A3/A10：策略读/项目详情/上传续体均以 projectId+generation 门禁迟到副作用。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import {
  CheckCircle2,
  Download,
  Info,
  Loader2,
  RefreshCw,
  Square,
  Upload,
} from "lucide-react";
import { AiFeedbackPanel } from "../../../shared/components/AiFeedbackPanel/AiFeedbackPanel";
import {
  ExportImageWarnings,
  normalizeExportImageWarnings,
} from "../../../shared/components/ExportImageWarnings";
import type { Project } from "../../../shared/types/workspace";
import {
  ParseStrategyChoiceDialog,
  type ParseStrategyChoice,
} from "../../parse-strategy/components/ParseStrategyChoiceDialog";
import {
  PARSE_STRATEGY_ERROR_MESSAGE,
  useWorkspaceParseStrategy,
} from "../../parse-strategy/hooks/useWorkspaceParseStrategy";
import {
  isCurrentManagedParseFailure,
  MANAGED_PARSE_LOCAL_FALLBACK_LINK_LABEL,
  MANAGED_PARSE_UNAVAILABLE_MESSAGE,
} from "../../parse-strategy/lib/managedParseTask";
import { useProjectPipeline } from "../../technical-plan/hooks/useProjectPipeline";
import {
  getProjectAsync,
  updateProjectAsync,
} from "../../technical-plan/lib/projectStore";
import {
  BusinessStepStepper,
  BUSINESS_STEPS,
} from "../components/BusinessStepStepper";
import { useBusinessBidWorkspace } from "../hooks/useBusinessBidWorkspace";
import { EditorStateEventUpdatePanel } from "../../editor-state-collaboration/EditorStateEventUpdatePanel";
import { ProjectTaskEventPanel } from "../../project-task-events/ProjectTaskEventPanel";
import { EditorStateVersionFreshness } from "../../editor-state-collaboration/EditorStateVersionFreshness";
import { ProjectPresencePanel } from "../../editor-state-collaboration/ProjectPresencePanel";
import { EditorStateCheckpointPanel } from "../../editor-state-checkpoints/EditorStateCheckpointPanel";
import { EditorStateRevisionPanel } from "../../editor-state-revisions/EditorStateRevisionPanel";
import type { BusinessBidStepId, QualifyItemStatus } from "../types";
import "./BusinessBid.css";

const STEP_IDS: BusinessBidStepId[] = BUSINESS_STEPS.map((s) => s.id);

/** 任务成功后的六步进度（与后端 technical_plan_step 对齐） */
const STEP_BY_TASK: Record<string, number> = {
  parse: 1,
  biz_qualify: 2,
  biz_toc: 3,
  biz_quote: 4,
  biz_commit: 5,
  export: 6,
};

function qualifyStatusLabel(s: QualifyItemStatus): string {
  if (s === "matched") return "已响应";
  if (s === "partial") return "待确认";
  if (s === "missing") return "缺材料";
  return "待处理";
}

function nextStepPath(
  projectId: string,
  active: BusinessBidStepId,
): string | null {
  const idx = STEP_IDS.indexOf(active);
  if (idx < 0 || idx >= STEP_IDS.length - 1) return null;
  return `/business-bid/${projectId}/${STEP_IDS[idx + 1]}`;
}

export function BusinessBidWorkspace() {
  const { projectId = "", step } = useParams<{
    projectId: string;
    step?: string;
  }>();
  const navigate = useNavigate();

  /**
   * A3：项目详情与 requestProjectId/status 同步归属门。
   * ready 且 requestProjectId===当前路由才可渲染；A→B 首帧同步视空，不等 effect。
   */
  const [projectLoad, setProjectLoad] = useState<{
    requestProjectId: string;
    status: "loading" | "ready";
    project: Project | null;
  }>({ requestProjectId: projectId, status: "loading", project: null });
  const [strategyTip, setStrategyTip] = useState("");
  const [parseChoiceOpen, setParseChoiceOpen] = useState(false);
  /**
   * P13-H3：用户确认刷新失败时，非 silent 重载会卸载工作区进入 loadError 页；
   * 在此保留失败旗标，于错误页同 testid 展示固定重载失败文案。
   * 与 projectId 同步的 ref：await 后仅当仍是请求项目才写旗标，防 A→B 迟到污染。
   */
  const [eventReloadFailed, setEventReloadFailed] = useState(false);
  const eventReloadProjectIdRef = useRef(projectId);
  // 渲染同步当前项目，不依赖 effect 清零顺序
  eventReloadProjectIdRef.current = projectId;
  /**
   * P9D：导出图片告警与产生它的 projectId 绑定（仅内存）。
   * 渲染时 projectId 不匹配则同步视为空，避免切换首帧泄漏旧告警。
   */
  const [exportImageWarningState, setExportImageWarningState] = useState<{
    projectId: string;
    warnings: string[];
  } | null>(null);
  /** P9D：导出告警代次；项目切换或新导出启动时递增，丢弃迟到 setState */
  const exportImageWarningGenRef = useRef(0);
  /**
   * V1-E：导出准备单飞令牌（项目绑定）。
   * 同项目连点：同步拒绝第二次；切项目后旧完成不得作用于新项目。
   */
  const exportPrepareTokenRef = useRef<{
    projectId: string;
    token: number;
  } | null>(null);
  const exportPrepareTokenSeqRef = useRef(0);
  /**
   * V1-E：每次 render 立即同步当前 projectId。
   * gate/task await 后与下载/告警前必须读 ref 比较 startedProjectId，禁止闭包 projectId。
   */
  const currentProjectIdRef = useRef(projectId);
  currentProjectIdRef.current = projectId;
  /**
   * V1-G：任务成功刷新代次（纯内存）。
   * 项目切换与每次 runBizTask 启动均推进，锁 A→B→A 与同项目旧 run。
   */
  const taskRefreshGenerationRef = useRef(0);
  /**
   * A2：解析动作代次；项目切换与每次策略读取启动均推进。
   * refresh 后与每个 continuation 前复核，锁 A→B / ABA 迟到 POST/ask/导航/tip。
   */
  const parseActionGenerationRef = useRef(0);
  /**
   * A2/T3：项目作用域的策略读取飞行旗标。
   * 禁止用全局 parseStrategy.loading 驱动按钮文案/禁用——软切 B 时旧 GET 在途不得污染 B。
   */
  const strategyReadInFlightRef = useRef(false);
  /** V1-E：导出准备进行态（与 pipeline.busy 共同禁用按钮） */
  const [exportPreparing, setExportPreparing] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const parseStrategy = useWorkspaceParseStrategy();

  const {
    workspace,
    history,
    loading: wsLoading,
    loadError,
    saveError,
    apiReady,
    fullStateConflict,
    fullStateConflictMessage,
    versionUpdatedAt,
    currentRevisionSourceKind,
    currentRevisionActorUsername,
    currentStateVersion,
    refreshFromApi,
    setParseMarkdown,
    updateQualifyItem,
    toggleTocItem,
    updateQuoteRow,
    setQuoteNotes,
    updateCommitBlock,
    submitRevise,
    createCheckpoint,
    flushPendingSaveForExport,
    restoreCheckpoint,
    restoreRevision,
  } = useBusinessBidWorkspace(projectId);

  const pipeline = useProjectPipeline(projectId);

  /** A3：将 project 详情写入 projectLoad（仅 request 归属匹配时）。 */
  const applyProjectDetail = useCallback(
    (next: Project | null, requestProjectId: string) => {
      setProjectLoad((prev) => {
        if (prev.requestProjectId !== requestProjectId) return prev;
        return {
          requestProjectId,
          status: "ready",
          project: next,
        };
      });
    },
    [],
  );

  useEffect(() => {
    let cancelled = false;
    const requestProjectId = projectId;
    // 同步进入 loading 并清空旧 project，A→B 首帧不得渲染 A 详情
    setProjectLoad({
      requestProjectId,
      status: "loading",
      project: null,
    });
    void (async () => {
      // 只认服务端详情；404/失败不得用 mock 复活
      const remote = await getProjectAsync(requestProjectId);
      if (cancelled) return;
      applyProjectDetail(remote ?? null, requestProjectId);
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, applyProjectDetail]);

  useEffect(() => {
    void pipeline.refreshFiles();
    void pipeline.refreshTasks();
    // 仅 projectId 变化时刷新
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // P13-H3：项目切换清空事件重载失败旗标，禁止旧失败文案污染新项目
  useEffect(() => {
    setEventReloadFailed(false);
    // V1-G：切项目推进任务刷新代次，旧 success 不得 refresh/写步进/覆盖 project
    taskRefreshGenerationRef.current += 1;
    // A2/T3：切项目推进解析动作代次并清飞行旗标，旧 GET 在途不得锁/污染 B
    parseActionGenerationRef.current += 1;
    strategyReadInFlightRef.current = false;
  }, [projectId]);

  /**
   * 模块：runBizTask
   * 用途：商务 parse/biz_* 统一任务入口；V1-G 以 startedProjectId+generation 门禁 success 后副作用。
   * 对接：pipeline.runTask；refreshFromApi；updateProjectAsync/getProjectAsync/setProjectLoad。
   * 二次开发：export 保持独立围栏，禁止并回本 helper；stale 返回真实 task，不得改写 status。
   * A13：transport rejection 在本边界消费，保留 pipeline.error，返回 null，禁止 rethrow
   *      （void runBizTask 的 biz_* 与 parse 路径均不得产生 unhandledrejection）。
   */
  const runBizTask = useCallback(
    async (
      type:
        | "parse"
        | "biz_qualify"
        | "biz_toc"
        | "biz_quote"
        | "biz_commit"
        | "export",
      payload?: Record<string, unknown>,
    ) => {
      const startedProjectId = currentProjectIdRef.current;
      const startedGeneration = ++taskRefreshGenerationRef.current;
      const isCurrentOwner = () =>
        Boolean(startedProjectId) &&
        currentProjectIdRef.current === startedProjectId &&
        taskRefreshGenerationRef.current === startedGeneration;

      let t: Awaited<ReturnType<typeof pipeline.runTask>> | null = null;
      try {
        t = await pipeline.runTask(type, payload);
      } catch {
        // A13：pipeline 已写入固定安全 error；消费 rejection，禁止冒泡为 unhandledrejection
        // 正常 success/failed/cancelled 不走此分支，语义保持
        return null;
      }
      // runTask 后复核：已切项目或同项目新代次则零 refresh/tip/step/project 副作用
      if (!isCurrentOwner() || !t) {
        return t;
      }
      if (t.status === "success") {
        // 同项目 refresh 失败仍保持既有 P11：任务 success 不反转，后续步进逻辑继续
        await refreshFromApi();
        if (!isCurrentOwner()) {
          return t;
        }
        const nextStep = STEP_BY_TASK[type];
        if (nextStep) {
          const patched = await updateProjectAsync(startedProjectId, {
            technicalPlanStep: nextStep,
          });
          if (!isCurrentOwner()) {
            return t;
          }
          if (patched) {
            applyProjectDetail(patched, startedProjectId);
          } else {
            const remote = await getProjectAsync(startedProjectId);
            if (!isCurrentOwner()) {
              return t;
            }
            if (remote) applyProjectDetail(remote, startedProjectId);
          }
        } else {
          const remote = await getProjectAsync(startedProjectId);
          if (!isCurrentOwner()) {
            return t;
          }
          if (remote) applyProjectDetail(remote, startedProjectId);
        }
      }
      return t;
    },
    [pipeline, refreshFromApi, applyProjectDetail],
  );

  /**
   * 模块：runLightweightBizParse
   * 用途：商务标轻量 parse，payload 固定 engine=lightweight。
   * 对接：runBizTask("parse")。
   * 二次开发：禁止传入 local/ask 等非生产引擎名。
   */
  const runLightweightBizParse = useCallback(async () => {
    setStrategyTip("");
    await runBizTask("parse", { engine: "lightweight" });
  }, [runBizTask]);

  /**
   * 模块：runManagedBizParse
   * 用途：商务标 managed parse，payload 精确 engine=managed；成功走 runBizTask 水合/步进。
   * 对接：runBizTask("parse")。
   * 二次开发：禁止 managed 失败后再发 lightweight。
   */
  const runManagedBizParse = useCallback(async () => {
    setStrategyTip("");
    await runBizTask("parse", { engine: "managed" });
  }, [runBizTask]);

  /**
   * 模块：goLocalParser
   * 用途：跳转人工本地回传页；不创建 parse 任务。
   * 对接：/local-parser?projectId=。
   * 二次开发：项目 ID 为空时不得导航。
   */
  const goLocalParser = useCallback(() => {
    const pid = (projectId || "").trim();
    if (!pid) return;
    setStrategyTip("");
    navigate(`/local-parser?projectId=${encodeURIComponent(pid)}`);
  }, [navigate, projectId]);

  /**
   * 模块：handleParse
   * 用途：统一解析决策 light|managed|local|ask（上传后自动、整段重解析、反馈 regenerate）。
   * 对接：useWorkspaceParseStrategy；ParseStrategyChoiceDialog。
   * 二次开发：local 零任务；ask 不回写；读取失败固定中文且不建任务。
   * A2：refresh 前捕获 projectId+parseActionGeneration；refresh 后与 continuation 前复核。
   */
  const handleParse = useCallback(async () => {
    // 仅用项目作用域飞行旗标防重入；禁止 parseStrategy.loading 跨项目锁死 B
    if (pipeline.busy || strategyReadInFlightRef.current) return;
    const pid = (projectId || "").trim();
    if (!pid) return;
    const startedProjectId = currentProjectIdRef.current || pid;
    const startedGeneration = ++parseActionGenerationRef.current;
    const isCurrentParseAction = () =>
      Boolean(startedProjectId) &&
      currentProjectIdRef.current === startedProjectId &&
      parseActionGenerationRef.current === startedGeneration;

    strategyReadInFlightRef.current = true;
    setStrategyTip("正在读取解析策略");
    const result = await parseStrategy.refresh();
    if (!isCurrentParseAction()) {
      // A→B / ABA 迟到：零 POST、零 ask、零导航、零 tip/error 写回
      // 旗标由 projectId effect 清零；此处不回写 UI
      return;
    }
    // 当前动作结束策略读取阶段；后续 task 由 pipeline.busy 接管
    strategyReadInFlightRef.current = false;
    if (!result.ok) {
      setStrategyTip(result.error);
      return;
    }
    if (result.strategy === "light") {
      if (!isCurrentParseAction()) return;
      await runLightweightBizParse();
      return;
    }
    if (result.strategy === "managed") {
      if (!isCurrentParseAction()) return;
      await runManagedBizParse();
      return;
    }
    if (result.strategy === "local") {
      if (!isCurrentParseAction()) return;
      goLocalParser();
      return;
    }
    // ask：仅打开一次选择框，不写设置
    if (!isCurrentParseAction()) return;
    setStrategyTip("");
    setParseChoiceOpen(true);
  }, [
    pipeline,
    parseStrategy,
    projectId,
    runLightweightBizParse,
    runManagedBizParse,
    goLocalParser,
  ]);

  /**
   * 模块：onParseChoice
   * 用途：处理 ask 一次选择 light|managed|local。
   * 对接：runLightweightBizParse / runManagedBizParse / goLocalParser。
   * 二次开发：不得回写工作空间默认策略。
   */
  const onParseChoice = useCallback(
    (choice: ParseStrategyChoice) => {
      setParseChoiceOpen(false);
      if (choice === "light") {
        void runLightweightBizParse();
        return;
      }
      if (choice === "managed") {
        void runManagedBizParse();
        return;
      }
      goLocalParser();
    },
    [runLightweightBizParse, runManagedBizParse, goLocalParser],
  );

  /**
   * A10：上传捕获启动 projectId；迟到 success 不得再启动 handleParse/策略 GET/任务。
   */
  const onPickFile = useCallback(
    async (file: File | null) => {
      if (!file) return;
      const startedProjectId = currentProjectIdRef.current;
      try {
        await pipeline.uploadFile(file);
      } catch {
        // pipeline 已写 error 或迟到拒绝；不得继续解析
        return;
      }
      if (
        !startedProjectId ||
        currentProjectIdRef.current !== startedProjectId
      ) {
        return;
      }
      await handleParse();
    },
    [pipeline, handleParse],
  );

  useEffect(() => {
    setParseChoiceOpen(false);
    setStrategyTip("");
    // 递增代次使飞行中的旧导出闭包无法再写入告警；下载语义保持既有行为
    exportImageWarningGenRef.current += 1;
    setExportImageWarningState(null);
    // V1-E：显式作废旧导出准备令牌；不得只 setExportPreparing(false)
    exportPrepareTokenRef.current = null;
    setExportPreparing(false);
  }, [projectId]);

  const exportImageWarnings =
    exportImageWarningState?.projectId === projectId
      ? exportImageWarningState.warnings
      : [];

  const onRevise = useCallback(
    (
      stage:
        | "business_parse"
        | "business_qualify"
        | "business_toc"
        | "business_quote"
        | "business_commit",
      message: string,
      preserveStructure: boolean,
      targetId?: string,
      targetLabel?: string,
    ) => {
      void submitRevise({
        stage,
        message,
        preserveStructure,
        targetId,
        targetLabel,
      });
    },
    [submitRevise],
  );

  // A3：同步归属门——requestProjectId 不匹配或仍 loading 时视作未就绪
  const projectReady =
    projectLoad.status === "ready" &&
    projectLoad.requestProjectId === projectId;
  const project = projectReady ? projectLoad.project : null;

  if (!projectReady || wsLoading) {
    return (
      <div className="page bb-layout" data-testid="business-editor-loading">
        <p style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Loader2 size={18} /> 加载商务标工作区…
        </p>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="page bb-layout">
        <p>未找到项目。</p>
        <Link to="/business-bid" className="btn btn-primary">
          返回列表
        </Link>
      </div>
    );
  }

  // P11B：editor-state 加载失败固定卡；全状态阻断时保留本地内容不卸载
  if (loadError && !fullStateConflict) {
    return (
      <div className="page bb-layout" data-testid="business-editor-load-error">
        <p style={{ color: "var(--danger)" }}>{loadError}</p>
        {eventReloadFailed ? (
          <div
            data-testid="business-editor-state-event-update"
            style={{ margin: "6px 0 0", minHeight: 4 }}
          >
            <p style={{ margin: "4px 0 0", color: "var(--danger)" }}>
              重新载入失败，请稍后重试
            </p>
          </div>
        ) : null}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            className="btn btn-primary"
            data-testid="business-editor-retry"
            onClick={() => {
              void refreshFromApi();
            }}
          >
            重试
          </button>
          <Link to="/business-bid" className="btn btn-ghost">
            返回列表
          </Link>
        </div>
      </div>
    );
  }

  if (!step) {
    const defaultStep =
      STEP_IDS[Math.max(0, (project.technicalPlanStep || 1) - 1)] ?? "parse";
    return (
      <Navigate to={`/business-bid/${project.id}/${defaultStep}`} replace />
    );
  }

  if (!STEP_IDS.includes(step as BusinessBidStepId)) {
    return <Navigate to={`/business-bid/${project.id}/parse`} replace />;
  }

  const active = step as BusinessBidStepId;
  const nextPath = nextStepPath(project.id, active);
  const doneUntil = project.technicalPlanStep || 0;
  const busy = pipeline.busy;
  const lastTask = pipeline.lastTask;
  const checkedCount = workspace.tocItems.filter((t) => t.checked).length;
  const missingQualify = workspace.qualifyItems.filter(
    (q) => q.status === "missing" || q.status === "partial",
  ).length;

  // P2：当前项目本地策略读取中/失败 tip 门；仅隐藏旧任务 UI，不改 lastTask 真值
  // 禁止用全局 parseStrategy.loading 驱动（软切跨项目泄漏）
  const strategyGateActive =
    strategyTip === "正在读取解析策略" ||
    strategyTip === PARSE_STRATEGY_ERROR_MESSAGE;

  return (
    <div className="page bb-layout" data-testid="business-editor-workspace">
      <header className="page-header">
        <div>
          <h1>{project.name}</h1>
          <p>
            {project.industry} · 可手动编辑，也可填写修改意见后修订 · 与技术标分册
            {saveError ? (
              <span data-testid="business-editor-save-error">
                {` · ${saveError}`}
              </span>
            ) : null}
          </p>
          <EditorStateVersionFreshness
            updatedAt={versionUpdatedAt}
            sourceKind={currentRevisionSourceKind}
            actorUsername={currentRevisionActorUsername}
            testId="business-editor-version-freshness"
            sourceTestId="business-editor-version-source"
            actorTestId="business-editor-version-actor"
          />
          <ProjectPresencePanel
            projectId={projectId}
            testId="business-project-presence"
          />
          <EditorStateEventUpdatePanel
            projectId={projectId}
            stateVersion={currentStateVersion}
            onReload={async () => {
              // 将 refreshFromApi 的 false/异常转为固定重载失败旗标；
              // loadError 既有语义保留；工作区卸载后错误页同 testid 展示固定文案。
              // 页面级 project 守卫：捕获请求项目；await/异常后仅同项目可写旗标。
              // 面板 generation 只保护面板 phase，不得冒充本页 guard。
              const requestProjectId = projectId;
              try {
                const ok = await refreshFromApi();
                if (eventReloadProjectIdRef.current !== requestProjectId) {
                  return false;
                }
                setEventReloadFailed(!ok);
                return ok;
              } catch {
                if (eventReloadProjectIdRef.current !== requestProjectId) {
                  return false;
                }
                setEventReloadFailed(true);
                return false;
              }
            }}
            testId="business-editor-state-event-update"
          />
          <ProjectTaskEventPanel
            projectId={projectId}
            testId="business-project-task-event-update"
            onSafeTaskEvent={pipeline.reconcileCurrentTaskStatus}
          />
          {fullStateConflict ? (
            <div
              data-testid="business-editor-state-conflict"
              style={{
                marginTop: 8,
                padding: "10px 12px",
                borderRadius: 8,
                background: "var(--danger-soft, #fff1f0)",
                color: "var(--danger)",
              }}
            >
              <p style={{ margin: "0 0 8px" }}>{fullStateConflictMessage}</p>
              {loadError ? (
                <p style={{ margin: "0 0 8px" }}>{loadError}</p>
              ) : null}
              <button
                type="button"
                className="btn btn-primary btn-sm"
                data-testid="business-editor-state-reload"
                onClick={() => {
                  void refreshFromApi();
                }}
              >
                重新载入远端内容
              </button>
            </div>
          ) : null}
        </div>
        <div className="page-actions">
          <Link to="/business-bid" className="btn btn-ghost">
            项目列表
          </Link>
          {project.linkedProjectId && (
            <Link
              to={`/technical-plan/${project.linkedProjectId}`}
              className="btn btn-soft"
            >
              打开关联技术标
            </Link>
          )}
        </div>
      </header>

      <EditorStateCheckpointPanel
        projectId={project.id}
        disabled={!apiReady || Boolean(loadError) || fullStateConflict}
        createCheckpoint={createCheckpoint}
        restoreCheckpoint={restoreCheckpoint}
      />

      <EditorStateRevisionPanel
        projectId={project.id}
        disabled={!apiReady || Boolean(loadError) || fullStateConflict}
        restoreRevision={restoreRevision}
      />

      {(() => {
        // A4/P2：策略 gate 生效时仅展示 tip；隐藏旧 lastTask message/取消/managed failure
        // 不删除 lastTask 真值；gate 解除后恢复既有任务展示
        const managedFail =
          !strategyGateActive &&
          isCurrentManagedParseFailure(
            projectId,
            lastTask,
            pipeline.error,
          );
        if (
          !strategyGateActive &&
          !busy &&
          !lastTask &&
          !pipeline.error &&
          !strategyTip &&
          !managedFail
        ) {
          return null;
        }
        if (
          strategyGateActive &&
          !strategyTip
        ) {
          return null;
        }
        return (
        <div className="bb-hint" style={{ marginBottom: 12 }}>
          <Info size={16} />
          <div style={{ flex: 1 }}>
            {strategyGateActive ? (
              strategyTip ? (
                <div
                  style={{
                    color: strategyTip.includes("无法读取")
                      ? "var(--danger)"
                      : undefined,
                  }}
                >
                  {strategyTip}
                </div>
              ) : null
            ) : (
              <>
                {managedFail ? (
                  <div style={{ color: "var(--danger)" }}>
                    {MANAGED_PARSE_UNAVAILABLE_MESSAGE}
                    {" · "}
                    <Link
                      to={`/local-parser?projectId=${encodeURIComponent(projectId)}`}
                    >
                      {MANAGED_PARSE_LOCAL_FALLBACK_LINK_LABEL}
                    </Link>
                  </div>
                ) : (
                  pipeline.error && (
                    <div style={{ color: "var(--danger)" }}>
                      {pipeline.error}
                    </div>
                  )
                )}
                {strategyTip && (
                  <div
                    style={{
                      color: strategyTip.includes("无法读取")
                        ? "var(--danger)"
                        : undefined,
                    }}
                  >
                    {strategyTip}
                  </div>
                )}
                {lastTask && (
                  <div>
                    任务 <strong>{lastTask.type}</strong> · {lastTask.status} ·{" "}
                    {lastTask.progress}% · {lastTask.message}
                  </div>
                )}
              </>
            )}
          </div>
          {!strategyGateActive &&
            lastTask &&
            (lastTask.status === "pending" ||
              lastTask.status === "running") && (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => void pipeline.cancelTask()}
              >
                <Square size={14} /> 取消
              </button>
            )}
        </div>
        );
      })()}

      <ParseStrategyChoiceDialog
        open={parseChoiceOpen}
        onChoose={onParseChoice}
        onCancel={() => setParseChoiceOpen(false)}
      />

      <BusinessStepStepper
        projectId={project.id}
        active={active}
        doneUntil={doneUntil}
      />

      {active === "parse" && (
        <section className="card card-pad">
          <div className="bb-hint">
            <Info size={16} />
            <span>
              识别资格条件、付款/保证金、有效期等商务条款。复杂扫描件可走
              <Link
                to={`/local-parser?projectId=${encodeURIComponent(projectId)}`}
                style={{ margin: "0 4px", textDecoration: "underline" }}
              >
                人工本地回传
              </Link>
              。解析不准时用下方反馈定向修正。
            </span>
          </div>
          <div className="bb-two-col">
            <div>
              <div className="upload-zone">
                <div className="upload-zone__icon">
                  <Upload size={22} />
                </div>
                <h3>上传招标文件</h3>
                <p>支持 PDF / DOCX；上传后按工作空间解析策略处理。</p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf,.doc,.docx,.txt,.md"
                  hidden
                  onChange={(e) => {
                    const f = e.target.files?.[0] ?? null;
                    e.target.value = "";
                    void onPickFile(f);
                  }}
                />
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={busy}
                  onClick={() => fileInputRef.current?.click()}
                >
                  {busy ? "处理中…" : "选择文件"}
                </button>
              </div>
              <div
                style={{
                  marginTop: 12,
                  display: "flex",
                  gap: 8,
                  flexWrap: "wrap",
                }}
              >
                {pipeline.files.length === 0 ? (
                  <span className="badge badge-muted">尚未上传</span>
                ) : (
                  pipeline.files.map((f) => (
                    <span key={f.id} className="file-chip">
                      {f.filename}
                    </span>
                  ))
                )}
                {workspace.parseMarkdown.trim() ? (
                  <span className="badge badge-primary">已有解析文本</span>
                ) : null}
              </div>
            </div>
            <div>
              <div className="bb-toolbar">
                <strong>解析预览（可编辑）</strong>
                <div className="bb-toolbar__spacer" />
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={
                    busy ||
                    // T3：仅本项目 tip/飞行态，禁止全局 parseStrategy.loading 软切泄漏
                    strategyTip === "正在读取解析策略" ||
                    pipeline.files.length === 0
                  }
                  onClick={() => void handleParse()}
                >
                  <RefreshCw size={14} />{" "}
                  {strategyTip === "正在读取解析策略"
                    ? "正在读取解析策略"
                    : "整段重解析"}
                </button>
              </div>
              <textarea
                className="bb-parse-edit"
                value={workspace.parseMarkdown}
                onChange={(e) => setParseMarkdown(e.target.value)}
                aria-label="商务条款解析 Markdown"
              />
            </div>
          </div>

          <AiFeedbackPanel
            stage="business_parse"
            targetLabel="商务条款解析"
            history={history}
            presets={[
              "补全遗漏的★号资格条款",
              "付款节点拆成条目列表",
              "标出履约保证金与有效期",
              "保留原文编号与强制性用语",
            ]}
            placeholder="例如：社保人数要求识别有误，请按 PDF 修正…"
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              onRevise(
                "business_parse",
                message,
                preserveStructure,
                targetId,
                targetLabel,
              )
            }
            onRegenerate={() => void handleParse()}
          />

          {nextPath && (
            <div
              className="bb-toolbar"
              style={{ marginTop: 16, marginBottom: 0 }}
            >
              <div className="bb-toolbar__spacer" />
              <Link to={nextPath} className="btn btn-primary">
                下一步：资格响应
              </Link>
            </div>
          )}
        </section>
      )}

      {active === "qualify" && (
        <section className="card card-pad">
          <div className="bb-hint">
            <Info size={16} />
            <span>
              对照资格要求逐条填写。待确认/缺材料：
              <strong> {missingQualify} </strong>
              条。
            </span>
          </div>
          <div className="bb-toolbar" style={{ marginBottom: 12 }}>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy || !workspace.parseMarkdown.trim()}
              onClick={() => void runBizTask("biz_qualify")}
            >
              <RefreshCw size={14} /> 生成资格草稿
            </button>
          </div>
          <div className="bb-qualify-list">
            {workspace.qualifyItems.map((item) => (
              <div key={item.id} className="bb-qualify-item">
                <div className="bb-qualify-item__head">
                  <div className="bb-qualify-item__req">{item.requirement}</div>
                  <select
                    className={`bb-status-pill is-${item.status}`}
                    value={item.status}
                    onChange={(e) =>
                      updateQualifyItem(item.id, {
                        status: e.target.value as QualifyItemStatus,
                      })
                    }
                    aria-label="响应状态"
                    style={{
                      border: "none",
                      cursor: "pointer",
                      appearance: "auto",
                    }}
                  >
                    <option value="matched">已响应</option>
                    <option value="partial">待确认</option>
                    <option value="missing">缺材料</option>
                    <option value="pending">待处理</option>
                  </select>
                </div>
                <div className="field">
                  <label>响应说明</label>
                  <textarea
                    rows={3}
                    value={item.response}
                    onChange={(e) =>
                      updateQualifyItem(item.id, { response: e.target.value })
                    }
                  />
                </div>
                <div className="field">
                  <label>证明材料索引</label>
                  <input
                    value={item.evidence}
                    onChange={(e) =>
                      updateQualifyItem(item.id, { evidence: e.target.value })
                    }
                    placeholder="附件名或知识库文档"
                  />
                </div>
                <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
                  状态：{qualifyStatusLabel(item.status)}
                </div>
              </div>
            ))}
          </div>

          <AiFeedbackPanel
            stage="business_qualify"
            targetLabel="资格响应表"
            history={history}
            presets={[
              "缺材料条目补写可落地的响应模板",
              "统一业绩描述口径与年份",
              "★ 号条款单独加粗提示",
            ]}
            placeholder="例如：第 4 条社保人数按 15 人重写响应…"
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              onRevise(
                "business_qualify",
                message,
                preserveStructure,
                targetId,
                targetLabel,
              )
            }
            onRegenerate={() => void runBizTask("biz_qualify")}
          />

          {nextPath && (
            <div
              className="bb-toolbar"
              style={{ marginTop: 16, marginBottom: 0 }}
            >
              <div className="bb-toolbar__spacer" />
              <Link to={nextPath} className="btn btn-primary">
                下一步：目录清单
              </Link>
            </div>
          )}
        </section>
      )}

      {active === "toc" && (
        <section className="card card-pad">
          <div className="bb-hint">
            <Info size={16} />
            <span>
              勾选拟递交材料。已勾选 {checkedCount}/{workspace.tocItems.length}。
            </span>
          </div>
          <div className="bb-toolbar" style={{ marginBottom: 12 }}>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy || !workspace.parseMarkdown.trim()}
              onClick={() => void runBizTask("biz_toc")}
            >
              <RefreshCw size={14} /> 生成材料清单
            </button>
          </div>
          <div className="bb-toc-list">
            {workspace.tocItems.map((item) => (
              <label key={item.id} className="bb-toc-row">
                <input
                  type="checkbox"
                  checked={item.checked}
                  onChange={() => toggleTocItem(item.id)}
                  aria-label={item.title}
                />
                <div>
                  <div className="bb-toc-row__title">{item.title}</div>
                  {item.note && (
                    <div
                      style={{
                        fontSize: 12,
                        color: "var(--warning)",
                        marginTop: 4,
                      }}
                    >
                      {item.note}
                    </div>
                  )}
                </div>
                <span className="bb-toc-row__cat">{item.category}</span>
                <span
                  className={`bb-status-pill ${
                    item.status === "optional" ? "is-pending" : "is-matched"
                  }`}
                >
                  {item.status === "optional" ? "可选" : "必需"}
                </span>
              </label>
            ))}
          </div>

          <AiFeedbackPanel
            stage="business_toc"
            targetLabel="商务目录清单"
            history={history}
            presets={["按招标目录顺序重排", "合并重复的资格证明项"]}
            placeholder="例如：增加「项目团队社保证明」…"
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              onRevise(
                "business_toc",
                message,
                preserveStructure,
                targetId,
                targetLabel,
              )
            }
            onRegenerate={() => void runBizTask("biz_toc")}
          />

          {nextPath && (
            <div
              className="bb-toolbar"
              style={{ marginTop: 16, marginBottom: 0 }}
            >
              <div className="bb-toolbar__spacer" />
              <Link to={nextPath} className="btn btn-primary">
                下一步：报价说明
              </Link>
            </div>
          )}
        </section>
      )}

      {active === "quote" && (
        <section className="card card-pad">
          <div className="bb-hint">
            <Info size={16} />
            <span>分项报价表。金额可手改。</span>
          </div>
          <div className="bb-toolbar" style={{ marginBottom: 12 }}>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy || !workspace.parseMarkdown.trim()}
              onClick={() => void runBizTask("biz_quote")}
            >
              <RefreshCw size={14} /> 生成报价骨架
            </button>
          </div>
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table className="bb-quote-table">
              <thead>
                <tr>
                  <th>分项名称</th>
                  <th>单位</th>
                  <th>数量</th>
                  <th>单价（元）</th>
                  <th>合价（元）</th>
                  <th>备注</th>
                </tr>
              </thead>
              <tbody>
                {workspace.quoteRows.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <input
                        value={row.name}
                        onChange={(e) =>
                          updateQuoteRow(row.id, { name: e.target.value })
                        }
                      />
                    </td>
                    <td>
                      <input
                        value={row.unit}
                        onChange={(e) =>
                          updateQuoteRow(row.id, { unit: e.target.value })
                        }
                      />
                    </td>
                    <td>
                      <input
                        value={row.quantity}
                        onChange={(e) =>
                          updateQuoteRow(row.id, { quantity: e.target.value })
                        }
                      />
                    </td>
                    <td>
                      <input
                        value={row.unitPrice}
                        onChange={(e) =>
                          updateQuoteRow(row.id, { unitPrice: e.target.value })
                        }
                      />
                    </td>
                    <td>
                      <input
                        value={row.amount}
                        onChange={(e) =>
                          updateQuoteRow(row.id, { amount: e.target.value })
                        }
                      />
                    </td>
                    <td>
                      <input
                        value={row.remark}
                        onChange={(e) =>
                          updateQuoteRow(row.id, { remark: e.target.value })
                        }
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="field">
            <label>报价与偏离说明</label>
            <textarea
              rows={4}
              value={workspace.quoteNotes}
              onChange={(e) => setQuoteNotes(e.target.value)}
            />
          </div>

          <AiFeedbackPanel
            stage="business_quote"
            targetLabel="报价表与说明"
            history={history}
            presets={["备注写清是否含税", "补充「无负偏离」声明"]}
            placeholder="例如：维保单独列出备品备件…"
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              onRevise(
                "business_quote",
                message,
                preserveStructure,
                targetId,
                targetLabel,
              )
            }
            onRegenerate={() => void runBizTask("biz_quote")}
          />

          {nextPath && (
            <div
              className="bb-toolbar"
              style={{ marginTop: 16, marginBottom: 0 }}
            >
              <div className="bb-toolbar__spacer" />
              <Link to={nextPath} className="btn btn-primary">
                下一步：授权承诺
              </Link>
            </div>
          )}
        </section>
      )}

      {active === "commit" && (
        <section className="card card-pad">
          <div className="bb-hint">
            <Info size={16} />
            <span>固定格式文本可手动替换单位名称与人员。</span>
          </div>
          <div className="bb-toolbar" style={{ marginBottom: 12 }}>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy || !workspace.parseMarkdown.trim()}
              onClick={() => void runBizTask("biz_commit")}
            >
              <RefreshCw size={14} /> 生成授权承诺
            </button>
          </div>
          <div className="bb-commit-list">
            {workspace.commitBlocks.map((block) => (
              <div
                key={block.id}
                className="card card-pad"
                style={{ boxShadow: "none" }}
              >
                <div className="bb-toolbar" style={{ marginBottom: 8 }}>
                  <strong>{block.title}</strong>
                  <div className="bb-toolbar__spacer" />
                  {block.needsStamp ? (
                    <span className="badge badge-primary">需盖章/签字</span>
                  ) : (
                    <span className="badge badge-muted">正文响应</span>
                  )}
                </div>
                <textarea
                  value={block.body}
                  onChange={(e) =>
                    updateCommitBlock(block.id, { body: e.target.value })
                  }
                  aria-label={block.title}
                />
              </div>
            ))}
          </div>

          <AiFeedbackPanel
            stage="business_commit"
            targetLabel="授权与承诺正文"
            history={history}
            presets={["替换为正式公文语气", "补全授权期限与权限范围"]}
            placeholder="例如：授权委托书补上身份证号占位…"
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              onRevise(
                "business_commit",
                message,
                preserveStructure,
                targetId,
                targetLabel,
              )
            }
            onRegenerate={() => void runBizTask("biz_commit")}
          />

          {nextPath && (
            <div
              className="bb-toolbar"
              style={{ marginTop: 16, marginBottom: 0 }}
            >
              <div className="bb-toolbar__spacer" />
              <Link to={nextPath} className="btn btn-primary">
                下一步：导出
              </Link>
            </div>
          )}
        </section>
      )}

      {active === "export" && (
        <section className="card card-pad" style={{ maxWidth: 720 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              marginBottom: 16,
            }}
          >
            <CheckCircle2 size={28} color="var(--success)" />
            <div>
              <strong style={{ fontSize: "var(--fs-lg)" }}>
                准备导出商务标 Word
              </strong>
              <p
                style={{
                  margin: "4px 0 0",
                  color: "var(--text-secondary)",
                  fontSize: "var(--fs-sm)",
                }}
              >
                合并资格响应、目录清单、报价说明与授权承诺；使用工作区默认导出模板。
              </p>
            </div>
          </div>
          <div className="bb-toolbar" style={{ marginBottom: 0 }}>
            <Link to="/export-format" className="btn btn-ghost">
              管理模板
            </Link>
            <div className="bb-toolbar__spacer" />
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy || exportPreparing}
              onClick={() => {
                // V1-E：同步单飞——同项目第二次点击立即拒绝，不依赖 disabled 异步刷新
                const startedProjectId = projectId;
                const existing = exportPrepareTokenRef.current;
                if (existing && existing.projectId === startedProjectId) {
                  return;
                }
                const myToken = ++exportPrepareTokenSeqRef.current;
                exportPrepareTokenRef.current = {
                  projectId: startedProjectId,
                  token: myToken,
                };
                setExportPreparing(true);
                void (async () => {
                  try {
                    const gate = await flushPendingSaveForExport();
                    // 切项目/被新令牌覆盖：旧完成不得创建 export、写告警或下载
                    const cur = exportPrepareTokenRef.current;
                    if (
                      !cur ||
                      cur.projectId !== startedProjectId ||
                      cur.token !== myToken ||
                      currentProjectIdRef.current !== startedProjectId
                    ) {
                      return;
                    }
                    if (gate !== "ready") {
                      return;
                    }
                    // 捕获启动时代次；成功返回后仅当前代次可写告警
                    const gen = ++exportImageWarningGenRef.current;
                    // 每次导出开始前清空旧告警，避免短暂展示上一轮结果
                    setExportImageWarningState(null);
                    // 禁止走 runBizTask：其 success 后无条件 refreshFromApi，
                    // A 迟到会 setLoading(true) 且 finally 因会话不匹配永不清零，卡死 B。
                    const t = await pipeline.runTask("export", {
                      mode: "business",
                    });
                    if (t.status === "success") {
                      // 再次核对项目令牌 + 当前项目 ref，禁止 A 迟到 success 污染 B
                      const still = exportPrepareTokenRef.current;
                      if (
                        !still ||
                        still.projectId !== startedProjectId ||
                        still.token !== myToken ||
                        currentProjectIdRef.current !== startedProjectId
                      ) {
                        return;
                      }
                      // 同项目成功：保持既有 refresh + 步进语义（原 runBizTask 后半段）
                      await refreshFromApi();
                      if (
                        currentProjectIdRef.current !== startedProjectId ||
                        exportPrepareTokenRef.current?.token !== myToken
                      ) {
                        return;
                      }
                      const exportStep = STEP_BY_TASK.export;
                      const patched = await updateProjectAsync(
                        startedProjectId,
                        { technicalPlanStep: exportStep },
                      );
                      if (
                        currentProjectIdRef.current !== startedProjectId ||
                        exportPrepareTokenRef.current?.token !== myToken
                      ) {
                        return;
                      }
                      if (patched) {
                        applyProjectDetail(patched, startedProjectId);
                      } else {
                        const remote = await getProjectAsync(startedProjectId);
                        if (
                          currentProjectIdRef.current !== startedProjectId ||
                          exportPrepareTokenRef.current?.token !== myToken
                        ) {
                          return;
                        }
                        if (remote) {
                          applyProjectDetail(remote, startedProjectId);
                        }
                      }
                      // 契约：成功且代次仍匹配时先写 P9D 告警，再 await 统一 downloadExport；
                      // 旧任务迟到不得写告警/下载；禁止 downloadPath/window.open 旁路
                      if (exportImageWarningGenRef.current === gen) {
                        setExportImageWarningState({
                          projectId: startedProjectId,
                          warnings: normalizeExportImageWarnings(
                            t.result?.imageWarnings,
                          ),
                        });
                      }
                      await pipeline.downloadExport(t);
                    }
                  } finally {
                    // 必须同时核对 current project + started project + myToken；
                    // 绝不能 A finally 清 B 新 token
                    const cur = exportPrepareTokenRef.current;
                    if (
                      cur &&
                      cur.projectId === startedProjectId &&
                      cur.token === myToken &&
                      currentProjectIdRef.current === startedProjectId
                    ) {
                      exportPrepareTokenRef.current = null;
                      setExportPreparing(false);
                    }
                  }
                })();
              }}
            >
              <Download size={16} />{" "}
              {exportPreparing
                ? "正在准备导出…"
                : busy
                  ? "导出中…"
                  : "生成并下载 Word"}
            </button>
          </div>
          <ExportImageWarnings warnings={exportImageWarnings} />
        </section>
      )}
    </div>
  );
}
