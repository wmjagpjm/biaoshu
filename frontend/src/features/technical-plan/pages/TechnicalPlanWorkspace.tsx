/**
 * 模块：技术标分步工作区（含 P13-B/C/D2/H3 版本、P13-F2 近期成员、P13-G2 章节意图）
 * 用途：技术标流水线工作区；标题区展示版本与项目近期成员；content 步展示章节处理意图提示；
 *       薄挂载 EditorStateEventUpdatePanel 做远端版本变化提示。
 * 对接：useTechnicalPlanEditors、EditorStateVersionFreshness、ProjectPresencePanel
 *       （testid=technical-project-presence）、ChapterEditIntentPanel
 *       （testid=technical-chapter-edit-intent，仅 content 薄挂载）、
 *       EditorStateEventUpdatePanel（testid=technical-editor-state-event-update）。
 * 二次开发：禁止改 editor-state 保存/冲突/任务路由；presence/意图/事件提示仅薄挂载；
 *       意图不是强制锁，不得禁用 ChapterEditor/按钮/autosave；不得灌入 editor Hook。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import {
  CheckCircle2,
  Download,
  FileStack,
  FileText,
  Info,
  Pause,
  Play,
  RefreshCw,
  Upload,
} from "lucide-react";
import { AiFeedbackPanel } from "../../../shared/components/AiFeedbackPanel/AiFeedbackPanel";
import {
  ExportImageWarnings,
  normalizeExportImageWarnings,
} from "../../../shared/components/ExportImageWarnings";
import { LoadingBlock } from "../../../shared/components/LoadingBlock/LoadingBlock";
import type { Project } from "../../../shared/types/workspace";
import { SaveAsTemplateDialog } from "../../bid-templates/components/SaveAsTemplateDialog";
import { BidWriterTeamRecommendationPanel } from "../../hr-team-recommendation/components/BidWriterTeamRecommendationPanel";
import {
  ParseStrategyChoiceDialog,
  type ParseStrategyChoice,
} from "../../parse-strategy/components/ParseStrategyChoiceDialog";
import { useWorkspaceParseStrategy } from "../../parse-strategy/hooks/useWorkspaceParseStrategy";
import { ChapterEditor } from "../components/ChapterEditor";
import { ContentFuseDialog } from "../components/ContentFuseDialog";
import { EditorStateEventUpdatePanel } from "../../editor-state-collaboration/EditorStateEventUpdatePanel";
import { EditorStateVersionFreshness } from "../../editor-state-collaboration/EditorStateVersionFreshness";
import { ChapterEditIntentPanel } from "../../editor-state-collaboration/ChapterEditIntentPanel";
import { ProjectPresencePanel } from "../../editor-state-collaboration/ProjectPresencePanel";
import { EditorStateCheckpointPanel } from "../../editor-state-checkpoints/EditorStateCheckpointPanel";
import { EditorStateRevisionPanel } from "../../editor-state-revisions/EditorStateRevisionPanel";
import { FactsEditor } from "../components/FactsEditor";
import { OutlineStepWorkspace } from "../components/OutlineStepWorkspace";
import { ProjectGuidanceCard } from "../components/ProjectGuidanceCard";
import { ResponseMatrixPanel } from "../components/ResponseMatrixPanel";
import { StepStepper } from "../components/StepStepper";
import { useProjectGuidance } from "../hooks/useProjectGuidance";
import { useProjectPipeline } from "../hooks/useProjectPipeline";
import {
  factsToText,
  outlineToMarkdown,
  useTechnicalPlanEditors,
} from "../hooks/useTechnicalPlanEditors";
import { markdownToOutline } from "../lib/outlineTree";
import {
  mergeResponseMatrixSuggestions,
  normalizeResponseMatrixSuggestions,
} from "../lib/responseMatrix";
import {
  getPendingFileNames,
  getProjectAsync,
} from "../lib/projectStore";
import type { ResponseMatrixSuggestion, TechnicalPlanStepId } from "../types";
import { serializeBidAnalysis } from "../types";
import "./TechnicalPlan.css";

/** 用途：从任务 result 提取知识库引用摘要文案。 */
function formatKbCitationsTip(task: {
  result?: Record<string, unknown> | null;
}): string {
  const raw = task.result?.kbCitations;
  if (!Array.isArray(raw) || raw.length === 0) return "";
  const names = raw
    .map((c) => {
      if (c && typeof c === "object" && "docName" in c) {
        return String((c as { docName?: string }).docName || "");
      }
      return "";
    })
    .filter(Boolean);
  const uniq = [...new Set(names)];
  if (!uniq.length) return `已引用知识库 ${raw.length} 条片段`;
  const shown = uniq.slice(0, 3).join("、");
  const more = uniq.length > 3 ? ` 等 ${uniq.length} 篇` : "";
  return `已引用知识库：${shown}${more}（${raw.length} 条）`;
}

/** 用途：联调展示最近一次 revise 正文；文本步可一键替换。 */
function RevisePreviewPanel(props: {
  text: string | null;
  canApply: boolean;
  applyLabel?: string;
  onApply?: () => void;
  onClear: () => void;
}) {
  if (!props.text) return null;
  return (
    <div className="tp-revise-preview" role="region" aria-label="修订结果预览">
      <div className="tp-revise-preview__head">
        <strong>修订结果预览</strong>
        <span style={{ color: "var(--text-secondary)", flex: 1 }}>
          {props.canApply
            ? "可应用到当前编辑区"
            : "当前为预览；结构化内容可在支持写回的步骤一键应用"}
        </span>
        {props.canApply && props.onApply && (
          <button type="button" className="btn btn-primary btn-sm" onClick={props.onApply}>
            {props.applyLabel ?? "应用到编辑器"}
          </button>
        )}
        <button type="button" className="btn btn-ghost btn-sm" onClick={props.onClear}>
          关闭
        </button>
      </div>
      <pre className="tp-revise-preview__body mono">{props.text}</pre>
    </div>
  );
}

const STEP_IDS: TechnicalPlanStepId[] = [
  "document",
  "analysis",
  "outline",
  "facts",
  "content",
  "export",
];

/**
 * 模块：技术方案工作区（P11C 服务端编辑态权威 + P13-B/C/D2 版本时间、来源与操作者）
 * 用途：六步流水线 + 异步任务 + 反馈修订 + 知识库引用展示。
 *   - 取消任务、大纲 revise「应用到大纲树」、生成后展示 kbCitations
 *   - 响应矩阵智能建议：外层来源页 × 内层候选批串行、本地累计、禁止自动写 editor-state
 *   - 文档解析入口按工作空间 parseStrategy 决策 light/local/ask（P8B）
 *   - 严格 bid_writer 可按需查看人力团队推荐投影（P10F，disabled 不展示）
 *   - P11C：editor-state 加载失败固定卡；项目详情绑定 requestProjectId；A→B 首帧不渲染旧项目
 *   - P13-B/C/D2：标题区展示当前已载入版本 UTC 更新时间、修订来源与操作者用户名（共享组件，零额外请求）
 * 对接：
 *   - useProjectPipeline / useTechnicalPlanEditors / useProjectGuidance
 *   - useWorkspaceParseStrategy / ParseStrategyChoiceDialog
 *   - BidWriterTeamRecommendationPanel
 *   - EditorStateVersionFreshness（testid=technical-editor-version-freshness /
 *     technical-editor-version-source / technical-editor-version-actor）
 *   - editor-state、POST .../tasks（response_match payload.sourceBatchIndex + candidateBatchIndex）、POST .../revise
 * 二次开发：勿在此堆业务；任务与持久化进 hooks；分批合并用 mergeResponseMatrixSuggestions；禁止字段级合并；轻量路径必须 engine=lightweight。
 *       禁止生产演示入口（填入演示数据/伪抽取/示例目录）；M3-D 对话框打开时不得用 loadError 卡提前卸载；
 *       版本时间/来源/操作者文案不得改称远端最新/实时/在线/最后由；用户名只作文本节点。
 */

type MatrixMatchProgress = {
  /** 当前候选批（1-based 展示） */
  current: number;
  /** 候选批总数 */
  total: number;
  /** 当前来源页（1-based 展示） */
  sourceCurrent: number;
  /** 来源页总数 */
  sourceTotal: number;
  accumulated: number;
  status: "idle" | "running" | "success" | "failed" | "cancelled";
};

function readBatchMeta(result: Record<string, unknown> | null | undefined) {
  /** 用途：从 response_match 任务结果读取候选/来源分页元数据；旧后端缺 source 字段时视为单页。 */
  const indexRaw = Number(result?.candidateBatchIndex);
  const countRaw = Number(result?.candidateBatchCount);
  const index = Number.isFinite(indexRaw) && indexRaw >= 0 ? Math.floor(indexRaw) : 0;
  const total =
    Number.isFinite(countRaw) && countRaw >= 1 ? Math.floor(countRaw) : 1;
  const isLast =
    result?.isLastCandidateBatch === true || index + 1 >= total;

  const sourceIndexRaw = Number(result?.sourceBatchIndex);
  const sourceCountRaw = Number(result?.sourceBatchCount);
  // 旧后端无 source 元数据：兼容为单页（与仅 candidateBatchIndex 的历史语义一致）
  const hasSourceMeta =
    result != null &&
    (result.sourceBatchIndex !== undefined ||
      result.sourceBatchCount !== undefined ||
      result.isLastSourceBatch !== undefined);
  const sourceIndex = hasSourceMeta
    ? Number.isFinite(sourceIndexRaw) && sourceIndexRaw >= 0
      ? Math.floor(sourceIndexRaw)
      : 0
    : 0;
  const sourceTotal = hasSourceMeta
    ? Number.isFinite(sourceCountRaw) && sourceCountRaw >= 1
      ? Math.floor(sourceCountRaw)
      : 1
    : 1;
  const isLastSource =
    !hasSourceMeta ||
    result?.isLastSourceBatch === true ||
    sourceIndex + 1 >= sourceTotal;

  return {
    index,
    total,
    isLast,
    sourceIndex,
    sourceTotal,
    isLastSource,
  };
}

function formatMatchProgressLabel(progress: MatrixMatchProgress): string {
  const sourcePart = `来源页 ${Math.max(progress.sourceCurrent, 1)}/${Math.max(progress.sourceTotal, 1)}`;
  const candPart = `候选批次 ${Math.max(progress.current, 1)}/${Math.max(progress.total, 1)}`;
  return `${sourcePart} · ${candPart} · 已累计 ${progress.accumulated} 条待确认`;
}

export function TechnicalPlanWorkspace() {
  const { projectId = "", step } = useParams<{ projectId: string; step?: string }>();
  const navigate = useNavigate();
  /**
   * 项目详情与请求 id 绑定，避免 SPA A→B 首帧复用旧 project 对象。
   * status=loading：仍在拉取；ready 且 requestProjectId 匹配当前路由才可渲染。
   */
  const [projectLoad, setProjectLoad] = useState<{
    requestProjectId: string;
    status: "loading" | "ready";
    project: Project | null;
  }>({ requestProjectId: projectId, status: "loading", project: null });
  // Hook/管线始终绑定当前路由 projectId，禁止用旧 project.id 驱动
  // P12B：先技术主 hook（含权威 guidance），再注入 useProjectGuidance（仅 history/revise）
  const editors = useTechnicalPlanEditors(projectId);
  const { history, submitRevise } = useProjectGuidance(
    projectId,
    editors.guidance,
  );
  const pipeline = useProjectPipeline(projectId);
  const parseStrategy = useWorkspaceParseStrategy();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [revisePreview, setRevisePreview] = useState<string | null>(null);
  /**
   * P13-H3：用户确认刷新失败时，blocking 重载会卸载工作区进入 loadError 页；
   * 在此保留失败旗标，于错误页同 testid 展示固定重载失败文案。
   * 与 projectId 同步的 ref：await 后仅当仍是请求项目才写旗标，防 A→B 迟到污染。
   */
  const [eventReloadFailed, setEventReloadFailed] = useState(false);
  const eventReloadProjectIdRef = useRef(projectId);
  // 渲染同步当前项目，不依赖 effect 清零顺序
  eventReloadProjectIdRef.current = projectId;
  const [revisePreviewStep, setRevisePreviewStep] = useState<TechnicalPlanStepId | null>(
    null,
  );
  const [taskTip, setTaskTip] = useState("");
  const [matrixSuggestions, setMatrixSuggestions] = useState<
    ResponseMatrixSuggestion[]
  >([]);
  const [matchProgress, setMatchProgress] = useState<MatrixMatchProgress | null>(
    null,
  );
  /** 中标内容模板：沉淀对话框开关 */
  const [saveTemplateOpen, setSaveTemplateOpen] = useState(false);
  const [saveTemplateTip, setSaveTemplateTip] = useState("");
  /** 阶段3 M3-A：模板/卡片只读融合建议对话框 */
  const [contentFuseOpen, setContentFuseOpen] = useState(false);
  /** P8B：ask 策略一次性选择框 */
  const [parseChoiceOpen, setParseChoiceOpen] = useState(false);
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
  /** 代次：项目切换、重入智能建议或取消后，丢弃迟到的串行批结果 */
  const matchSessionRef = useRef(0);

  useEffect(() => {
    // 递增代次使飞行中的旧导出闭包无法再写入告警；下载语义保持既有行为
    exportImageWarningGenRef.current += 1;
    setExportImageWarningState(null);
  }, [projectId]);

  const exportImageWarnings =
    exportImageWarningState?.projectId === projectId
      ? exportImageWarningState.warnings
      : [];

  /**
   * 模块：runLightweightParse
   * 用途：以 engine=lightweight 创建 parse 任务并刷新编辑态。
   * 对接：pipeline.runTask；editors.reloadFromApi。
   * 二次开发：禁止传入 local/ask/mineru 等引擎名。
   */
  const runLightweightParse = useCallback(async () => {
    try {
      const t = await pipeline.runTask("parse", { engine: "lightweight" });
      if (t.status === "success") {
        const ok = await editors.reloadFromApi({ blocking: true });
        if (ok) setTaskTip("解析完成，请查看右侧预览");
      }
    } catch {
      /* error 已在 pipeline */
    }
  }, [pipeline, editors]);

  /**
   * 模块：goLocalParser
   * 用途：跳转本地回传页并携带编码后的项目 ID。
   * 对接：/local-parser?projectId=；不创建任务。
   * 二次开发：项目 ID 为空时不得导航。
   */
  const goLocalParser = useCallback(() => {
    const pid = (projectId || "").trim();
    if (!pid) return;
    navigate(`/local-parser?projectId=${encodeURIComponent(pid)}`);
  }, [navigate, projectId]);

  /**
   * 模块：handleDocumentParse
   * 用途：点击解析时 refresh 策略并决策 light/local/ask。
   * 对接：useWorkspaceParseStrategy；ParseStrategyChoiceDialog。
   * 二次开发：读取失败/取消/繁忙均不得创建任务；不得静默降级为 light。
   */
  const handleDocumentParse = useCallback(async () => {
    if (pipeline.busy || parseStrategy.loading) return;
    const pid = (projectId || "").trim();
    if (!pid) return;
    setTaskTip("正在读取解析策略");
    const result = await parseStrategy.refresh();
    if (!result.ok) {
      setTaskTip(result.error);
      return;
    }
    if (result.strategy === "light") {
      await runLightweightParse();
      return;
    }
    if (result.strategy === "local") {
      setTaskTip("");
      goLocalParser();
      return;
    }
    setTaskTip("");
    setParseChoiceOpen(true);
  }, [
    pipeline.busy,
    parseStrategy,
    projectId,
    runLightweightParse,
    goLocalParser,
  ]);

  /**
   * 模块：onParseChoice
   * 用途：处理 ask 选择框的一次选择。
   * 对接：runLightweightParse / goLocalParser。
   * 二次开发：不得回写工作空间 parseStrategy。
   */
  const onParseChoice = useCallback(
    (choice: ParseStrategyChoice) => {
      setParseChoiceOpen(false);
      if (choice === "light") {
        void runLightweightParse();
        return;
      }
      goLocalParser();
    },
    [runLightweightParse, goLocalParser],
  );

  useEffect(() => {
    if (projectId) {
      void pipeline.refreshFiles();
      void pipeline.refreshTasks();
    }
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let cancelled = false;
    const requestProjectId = projectId;
    setProjectLoad({
      requestProjectId,
      status: "loading",
      project: null,
    });
    void getProjectAsync(requestProjectId).then((p) => {
      if (cancelled) return;
      setProjectLoad({
        requestProjectId,
        status: "ready",
        project: p ?? null,
      });
    });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  useEffect(() => {
    matchSessionRef.current += 1;
    setMatrixSuggestions([]);
    setMatchProgress(null);
    setContentFuseOpen(false);
    setParseChoiceOpen(false);
    setEventReloadFailed(false);
  }, [projectId]);

  // 渲染顺序：项目/editor loading → 不存在跳列表 → loadError（ContentFuse 打开时例外）→ 工作区
  const projectReady =
    projectLoad.status === "ready" &&
    projectLoad.requestProjectId === projectId;
  const project = projectReady ? projectLoad.project : null;

  if (
    !projectReady ||
    projectLoad.status === "loading" ||
    editors.loading
  ) {
    return (
      <div className="page" data-testid="technical-editor-loading">
        <LoadingBlock label="加载项目…" />
      </div>
    );
  }

  if (!project) {
    return <Navigate to="/technical-plan" replace />;
  }

  // 全状态阻断时保留本地内容，不得因重载失败卸载工作区
  if (editors.loadError && !contentFuseOpen && !editors.fullStateConflict) {
    return (
      <div
        className="page"
        data-testid="technical-editor-load-error"
      >
        <p style={{ color: "var(--danger)" }}>{editors.loadError}</p>
        {eventReloadFailed ? (
          <div
            data-testid="technical-editor-state-event-update"
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
            data-testid="technical-editor-retry"
            onClick={() => {
              void editors.reloadFromApi({ blocking: true });
            }}
          >
            重试
          </button>
          <Link to="/technical-plan" className="btn btn-ghost">
            返回列表
          </Link>
        </div>
      </div>
    );
  }

  // 解析正文：优先服务端 parsedMarkdown；空态为纯 UI「尚未解析」说明，不写 editor-state
  const documentPreviewMd =
    editors.parsedMarkdown?.trim() ||
    `# 招标公告（尚未解析）

请上传 PDF/DOCX/TXT 后点击「轻量解析」。
项目：${project.name}
`;

  const pendingFiles = getPendingFileNames(project.id);
  const displayFiles =
    pipeline.files.length > 0
      ? pipeline.files.map((f) => f.filename)
      : pendingFiles.length > 0
        ? pendingFiles
        : [];

  if (!step) {
    const defaultStep =
      STEP_IDS[Math.max(0, project.technicalPlanStep - 1)] ?? "document";
    return (
      <Navigate to={`/technical-plan/${project.id}/${defaultStep}`} replace />
    );
  }

  if (!STEP_IDS.includes(step as TechnicalPlanStepId)) {
    return <Navigate to={`/technical-plan/${project.id}/document`} replace />;
  }

  const active = step as TechnicalPlanStepId;
  const selectedChapter = editors.selectedChapter;

  async function runRevise(
    stepId: TechnicalPlanStepId,
    payload: {
      stage: Parameters<typeof submitRevise>[0]["stage"];
      message: string;
      preserveStructure: boolean;
      targetId?: string;
      targetLabel?: string;
      baseContent?: string;
    },
  ) {
    const res = await submitRevise(payload);
    if (res.ok && res.revisedContent) {
      setRevisePreview(res.revisedContent);
      setRevisePreviewStep(stepId);
    } else if (res.ok && res.resultSummary) {
      setRevisePreview(res.resultSummary);
      setRevisePreviewStep(stepId);
    }
  }

  async function requestResponseMatrixSuggestions() {
    const session = ++matchSessionRef.current;
    let accumulated: ResponseMatrixSuggestion[] = [];
    setMatrixSuggestions([]);
    setMatchProgress({
      current: 1,
      total: 1,
      sourceCurrent: 1,
      sourceTotal: 1,
      accumulated: 0,
      status: "running",
    });
    setTaskTip("正在串行拉取来源页与候选批次建议…");

    try {
      // 外层来源页 → 内层候选批；仅当「当前来源末页且当前候选末批」才停止
      for (let sourceBatchIndex = 0; ; sourceBatchIndex += 1) {
        for (let batchIndex = 0; ; batchIndex += 1) {
          if (matchSessionRef.current !== session) return;

          const task = await pipeline.runTask("response_match", {
            sourceBatchIndex,
            candidateBatchIndex: batchIndex,
          });

          if (matchSessionRef.current !== session) return;

          if (task.status === "cancelled") {
            setMatchProgress({
              current: batchIndex + 1,
              total: Math.max(batchIndex + 1, 1),
              sourceCurrent: sourceBatchIndex + 1,
              sourceTotal: Math.max(sourceBatchIndex + 1, 1),
              accumulated: accumulated.length,
              status: "cancelled",
            });
            setTaskTip(
              accumulated.length > 0
                ? `已取消；保留已成功批次的 ${accumulated.length} 条待确认建议`
                : "智能建议已取消",
            );
            return;
          }

          if (task.status !== "success") {
            setMatchProgress({
              current: batchIndex + 1,
              total: Math.max(batchIndex + 1, 1),
              sourceCurrent: sourceBatchIndex + 1,
              sourceTotal: Math.max(sourceBatchIndex + 1, 1),
              accumulated: accumulated.length,
              status: "failed",
            });
            setTaskTip(
              accumulated.length > 0
                ? `来源页 ${sourceBatchIndex + 1} · 候选批次 ${batchIndex + 1} 失败，已停止后续分页；保留已累计 ${accumulated.length} 条建议`
                : task.error || task.message || "智能建议失败",
            );
            return;
          }

          const batchSuggestions = normalizeResponseMatrixSuggestions(
            task.result?.suggestions,
          );
          accumulated = mergeResponseMatrixSuggestions(
            accumulated,
            batchSuggestions,
          );
          setMatrixSuggestions(accumulated);

          const meta = readBatchMeta(task.result);
          const done = meta.isLast && meta.isLastSource;
          setMatchProgress({
            current: meta.index + 1,
            total: meta.total,
            sourceCurrent: meta.sourceIndex + 1,
            sourceTotal: meta.sourceTotal,
            accumulated: accumulated.length,
            status: done ? "success" : "running",
          });
          setTaskTip(
            `来源页 ${meta.sourceIndex + 1}/${meta.sourceTotal} · 候选批次 ${meta.index + 1}/${meta.total} · 已累计 ${accumulated.length} 条待确认`,
          );

          if (meta.isLast) {
            if (meta.isLastSource) {
              if (accumulated.length === 0) {
                setTaskTip("未生成可用映射建议，请人工维护响应矩阵");
              }
              return;
            }
            // 当前来源页的候选批已走完，进入下一来源页（重置候选批从 0）
            break;
          }
        }
      }
    } catch {
      if (matchSessionRef.current !== session) return;
      setMatchProgress((prev) =>
        prev
          ? { ...prev, status: "failed", accumulated: accumulated.length }
          : {
              current: 0,
              total: 1,
              sourceCurrent: 0,
              sourceTotal: 1,
              accumulated: accumulated.length,
              status: "failed",
            },
      );
      if (accumulated.length > 0) {
        setTaskTip(
          `智能建议中断；保留已累计 ${accumulated.length} 条待确认建议`,
        );
      }
      /* 错误文案已在 pipeline */
    }
  }

  function applyResponseMatrixSuggestions(sourceKeys: string[]) {
    const selected = matrixSuggestions.filter((item) =>
      sourceKeys.includes(item.sourceKey),
    );
    if (selected.length === 0) return;
    editors.applyResponseMatrixSuggestions(selected);
    setMatrixSuggestions((current) =>
      current.filter((item) => !sourceKeys.includes(item.sourceKey)),
    );
    setMatchProgress((prev) =>
      prev
        ? {
            ...prev,
            accumulated: Math.max(0, prev.accumulated - selected.length),
          }
        : prev,
    );
    setTaskTip("已应用所选建议；人工修改过或不响应的条目会保持原样");
  }

  const suggestionBusy =
    matchProgress?.status === "running" ||
    (pipeline.busy && pipeline.lastTask?.type === "response_match");
  const matchProgressLabel =
    matchProgress && matchProgress.status !== "idle"
      ? formatMatchProgressLabel({
          ...matchProgress,
          accumulated: matrixSuggestions.length,
        })
      : null;

  return (
    <div className="page tp-layout" data-testid="technical-editor-workspace">
      <header className="page-header">
        <div>
          <h1>{project.name}</h1>
          <p>
            {project.industry} · 服务端编辑态
            {pipeline.busy
              ? " · 任务执行中…"
              : pipeline.lastTask
                ? ` · 最近任务 ${pipeline.lastTask.type}/${pipeline.lastTask.status}`
                : ""}
          </p>
          <EditorStateVersionFreshness
            updatedAt={editors.versionUpdatedAt}
            sourceKind={editors.currentRevisionSourceKind}
            actorUsername={editors.currentRevisionActorUsername}
            testId="technical-editor-version-freshness"
            sourceTestId="technical-editor-version-source"
            actorTestId="technical-editor-version-actor"
          />
          <ProjectPresencePanel
            projectId={projectId}
            testId="technical-project-presence"
          />
          <EditorStateEventUpdatePanel
            projectId={projectId}
            stateVersion={editors.currentStateVersion}
            onReload={async () => {
              // 页面级 project 守卫：捕获请求项目；await/异常后仅同项目可写旗标。
              // 面板 generation 只保护面板 phase，不得冒充本页 guard。
              const requestProjectId = projectId;
              try {
                const ok = await editors.reloadFromApi({ blocking: true });
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
            testId="technical-editor-state-event-update"
          />
          {editors.saveError ? (
            <p
              data-testid="technical-editor-save-error"
              style={{ color: "var(--danger)", margin: "6px 0 0" }}
            >
              {editors.saveError}
            </p>
          ) : null}
          {editors.fullStateConflict ? (
            <div
              data-testid="technical-editor-state-conflict"
              style={{
                marginTop: 8,
                padding: "10px 12px",
                borderRadius: 8,
                background: "var(--danger-soft, #fff1f0)",
                color: "var(--danger)",
              }}
            >
              <p style={{ margin: "0 0 8px" }}>
                {editors.fullStateConflictMessage}
              </p>
              {editors.loadError ? (
                <p style={{ margin: "0 0 8px" }}>{editors.loadError}</p>
              ) : null}
              <button
                type="button"
                className="btn btn-primary btn-sm"
                data-testid="technical-editor-state-reload"
                onClick={() => {
                  void editors.reloadFromApi({ blocking: true });
                }}
              >
                重新载入远端内容
              </button>
            </div>
          ) : null}
        </div>
        <div className="page-actions">
          <Link to="/technical-plan" className="btn btn-ghost">
            项目列表
          </Link>
          <Link to="/bid-templates" className="btn btn-ghost">
            中标模板库
          </Link>
          <button
            type="button"
            className="btn btn-ghost"
            aria-label="沉淀为中标内容模板"
            title="将当前大纲与章节深拷贝为工作空间内独立模板快照"
            onClick={() => {
              setSaveTemplateTip("");
              setSaveTemplateOpen(true);
            }}
          >
            <FileStack size={16} /> 沉淀为模板
          </button>
          <button
            type="button"
            className="btn btn-ghost"
            disabled={!pipeline.canCancel}
            title={
              pipeline.canCancel
                ? "取消当前进行中的任务（章间/步骤检查点生效）"
                : "无进行中的可取消任务"
            }
            onClick={() => {
              void (async () => {
                try {
                  // 先作废串行批会话，避免取消后迟到结果污染；保留已累计建议
                  if (matchProgress?.status === "running") {
                    matchSessionRef.current += 1;
                    setMatchProgress((prev) =>
                      prev
                        ? {
                            ...prev,
                            status: "cancelled",
                            accumulated: matrixSuggestions.length,
                          }
                        : prev,
                    );
                    setTaskTip(
                      matrixSuggestions.length > 0
                        ? `已取消；保留已成功批次的 ${matrixSuggestions.length} 条待确认建议`
                        : "智能建议已取消",
                    );
                  }
                  const t = await pipeline.cancelTask();
                  if (t?.status === "cancelled" && matchProgress?.status !== "running") {
                    setTaskTip("任务已取消");
                  }
                } catch {
                  /* error 已在 pipeline */
                }
              })();
            }}
          >
            <Pause size={16} /> 取消任务
          </button>
        </div>
      </header>

      <EditorStateCheckpointPanel
        projectId={project.id}
        disabled={
          !editors.apiReady ||
          Boolean(editors.loadError) ||
          editors.fullStateConflict
        }
        createCheckpoint={editors.createCheckpoint}
        restoreCheckpoint={editors.restoreCheckpoint}
      />

      <EditorStateRevisionPanel
        projectId={project.id}
        disabled={
          !editors.apiReady ||
          Boolean(editors.loadError) ||
          editors.fullStateConflict
        }
        restoreRevision={editors.restoreRevision}
      />

      {saveTemplateTip && (
        <div className="tp-source-banner is-api" role="status">
          {saveTemplateTip}
        </div>
      )}

      <BidWriterTeamRecommendationPanel projectId={project.id} />

      <SaveAsTemplateDialog
        open={saveTemplateOpen}
        projectId={project.id}
        defaultTitle={`${project.name} · 模板`}
        onClose={() => setSaveTemplateOpen(false)}
        onSaved={(tpl) => {
          setSaveTemplateTip(
            `已沉淀模板「${tpl.title}」，可在中标模板库查看或从模板新建项目。`,
          );
        }}
      />

      <ParseStrategyChoiceDialog
        open={parseChoiceOpen}
        onChoose={onParseChoice}
        onCancel={() => setParseChoiceOpen(false)}
      />

      {(pipeline.error || taskTip) && (
        <div
          className={`tp-source-banner ${pipeline.error ? "is-local" : "is-api"}`}
          role="status"
        >
          {pipeline.error || taskTip}
          {pipeline.error &&
          /Key|配置|模型|API/i.test(pipeline.error) ? (
            <>
              {" · "}
              <Link to="/settings">去设置页检查 Key</Link>
            </>
          ) : null}
        </div>
      )}

      {(pipeline.busy || pipeline.lastTask) && (
        <div className="card card-pad" style={{ padding: "10px 14px" }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              fontSize: 13,
            }}
          >
            <strong>
              {pipeline.busy ? "任务进行中" : "最近任务"}
            </strong>
            <span style={{ color: "var(--text-secondary)" }}>
              {pipeline.lastTask
                ? `${pipeline.lastTask.type} · ${pipeline.lastTask.status} · ${pipeline.lastTask.progress}% · ${pipeline.lastTask.message}`
                : "…"}
            </span>
          </div>
          <div
            style={{
              marginTop: 8,
              height: 6,
              borderRadius: 4,
              background: "var(--border)",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                height: "100%",
                width: `${pipeline.lastTask?.progress ?? (pipeline.busy ? 8 : 0)}%`,
                background: "var(--primary)",
                transition: "width 0.3s ease",
              }}
            />
          </div>
          {pipeline.recentTasks.length > 0 && (
            <details style={{ marginTop: 8, fontSize: 12 }}>
              <summary style={{ cursor: "pointer" }}>最近任务列表</summary>
              <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                {pipeline.recentTasks.map((t) => (
                  <li key={t.id}>
                    {t.type} / {t.status} / {t.progress}% — {t.message}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {(() => {
            const raw = pipeline.lastTask?.result?.kbCitations;
            if (!Array.isArray(raw) || raw.length === 0) return null;
            return (
              <details style={{ marginTop: 10, fontSize: 12 }} open>
                <summary style={{ cursor: "pointer" }}>
                  知识库引用（{raw.length}）
                </summary>
                <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                  {raw.map((c, i) => {
                    const item = c as {
                      docName?: string;
                      title?: string;
                      excerpt?: string;
                    };
                    return (
                      <li key={i} style={{ marginBottom: 4 }}>
                        <strong>{item.docName || "文档"}</strong>
                        {item.title ? ` · ${item.title}` : ""}
                        {item.excerpt ? (
                          <div
                            style={{
                              color: "var(--text-secondary)",
                              marginTop: 2,
                            }}
                          >
                            {item.excerpt}
                            {item.excerpt.length >= 160 ? "…" : ""}
                          </div>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              </details>
            );
          })()}
        </div>
      )}

      <StepStepper
        projectId={project.id}
        active={active}
        doneUntil={project.technicalPlanStep}
      />

      {active === "document" && (
        <section className="card card-pad">
          <div className="hint-banner">
            <Info size={16} />
            <span>
              上传后点「轻量解析」写入后端。扫描件请用
              <Link
                to={`/local-parser?projectId=${encodeURIComponent(project.id)}`}
                style={{ margin: "0 4px", textDecoration: "underline" }}
              >
                本地 MinerU
              </Link>
              。设置页需配置可用模型 Key（分析/生成步骤需要）。
            </span>
          </div>
          <div className="tp-panel two-col">
            <div>
              <div className="upload-zone">
                <div className="upload-zone__icon">
                  <Upload size={22} />
                </div>
                <h3>上传招标文件</h3>
                <p>支持 PDF / DOCX / TXT / MD（单文件默认 ≤ 50MB）。</p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf,.docx,.txt,.md,.markdown,application/pdf"
                  hidden
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (!f) return;
                    void (async () => {
                      try {
                        await pipeline.uploadFile(f);
                        setTaskTip(`已上传：${f.name}，可点击「轻量解析」`);
                      } catch {
                        /* error 已在 pipeline */
                      }
                      e.target.value = "";
                    })();
                  }}
                />
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={pipeline.busy}
                  onClick={() => fileInputRef.current?.click()}
                >
                  选择文件
                </button>
                <button
                  type="button"
                  className="btn btn-soft"
                  style={{ marginLeft: 8 }}
                  disabled={
                    pipeline.busy ||
                    parseStrategy.loading ||
                    pipeline.files.length === 0
                  }
                  onClick={() => {
                    void handleDocumentParse();
                  }}
                >
                  {parseStrategy.loading
                    ? "正在读取解析策略"
                    : pipeline.busy
                      ? "处理中…"
                      : "轻量解析"}
                </button>
              </div>
              <div style={{ marginTop: 14, display: "flex", gap: 8, flexWrap: "wrap" }}>
                {displayFiles.length === 0 ? (
                  <span className="file-chip">尚未上传文件</span>
                ) : (
                  displayFiles.map((name) => (
                    <span key={name} className="file-chip">
                      <FileText size={14} /> {name}
                    </span>
                  ))
                )}
                {editors.parsedMarkdown?.trim() ? (
                  <span className="badge badge-primary">已解析</span>
                ) : (
                  <span className="badge">未解析</span>
                )}
              </div>
            </div>
            <div className="card card-pad" style={{ background: "var(--surface-card)" }}>
              <h3 style={{ marginTop: 0, fontSize: "var(--fs-md)" }}>解析预览（Markdown）</h3>
              <pre
                className="mono"
                style={{
                  margin: 0,
                  whiteSpace: "pre-wrap",
                  fontSize: "var(--fs-sm)",
                  color: "var(--text-secondary)",
                  lineHeight: 1.65,
                  maxHeight: 280,
                  overflow: "auto",
                }}
              >
{documentPreviewMd}
              </pre>
              <div className="tp-toolbar" style={{ marginTop: 14, marginBottom: 0 }}>
                <div className="tp-toolbar__spacer" />
                <Link
                  to={`/technical-plan/${project.id}/analysis`}
                  className="btn btn-primary"
                >
                  下一步：招标分析
                </Link>
              </div>
            </div>
          </div>

          <AiFeedbackPanel
            stage="document_parse"
            targetLabel="当前解析文本"
            history={history}
            presets={[
              "表格识别错位，请按评分表重排",
              "补全缺失的废标条款段落",
              "合并重复的项目概况",
              "保留原文编号与★号标记",
            ]}
            placeholder="例如：第三章评分表第 3 行权重识别错误；请按 PDF 第 12 页修正…"
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              void runRevise("document", {
                stage: "document_parse",
                message,
                preserveStructure,
                targetId,
                targetLabel,
                baseContent: [
                  `文件：${displayFiles.join("、")}`,
                  "",
                  documentPreviewMd,
                ].join("\n"),
              })
            }
            onRegenerate={() => {
              /* 后端：重新跑解析任务 */
            }}
          />
          <RevisePreviewPanel
            text={revisePreviewStep === "document" ? revisePreview : null}
            canApply={false}
            onClear={() => setRevisePreview(null)}
          />
        </section>
      )}

      {active === "analysis" && (
        <div className="tp-layout">
          <section className="tp-panel two-col">
            <div className="card card-pad analysis-grid">
              <div className="analysis-block">
                <h3>项目概述（可编辑）</h3>
                <textarea
                  aria-label="项目概述"
                  data-testid="technical-analysis-overview"
                  value={editors.analysis.overview}
                  onChange={(e) => editors.setAnalysisOverview(e.target.value)}
                  placeholder="点击「AI 招标分析」或手动填写概述…"
                  style={{
                    width: "100%",
                    minHeight: 120,
                    border: "1px solid var(--border-strong)",
                    borderRadius: 10,
                    padding: 12,
                    fontSize: "var(--fs-md)",
                    lineHeight: 1.65,
                  }}
                />
              </div>
              <div className="analysis-block">
                <div className="tp-toolbar" style={{ marginBottom: 8 }}>
                  <h3 style={{ margin: 0 }}>技术要求摘录</h3>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() =>
                      editors.patchAnalysis({
                        techRequirements: [
                          ...editors.analysis.techRequirements,
                          "",
                        ],
                      })
                    }
                  >
                    添加
                  </button>
                </div>
                {editors.analysis.techRequirements.length === 0 ? (
                  <p style={{ fontSize: 13, color: "var(--text-secondary)" }}>
                    暂无。可 AI 分析或手动添加。
                  </p>
                ) : (
                  editors.analysis.techRequirements.map((t, i) => (
                    <div key={i} style={{ display: "flex", gap: 6, marginBottom: 6 }}>
                      <input
                        value={t}
                        onChange={(e) => {
                          const next = [...editors.analysis.techRequirements];
                          next[i] = e.target.value;
                          editors.patchAnalysis({ techRequirements: next });
                        }}
                        style={{ flex: 1 }}
                      />
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => {
                          const next = editors.analysis.techRequirements.filter(
                            (_, j) => j !== i,
                          );
                          editors.patchAnalysis({ techRequirements: next });
                        }}
                      >
                        删
                      </button>
                    </div>
                  ))
                )}
              </div>
              <div className="analysis-block">
                <div className="tp-toolbar" style={{ marginBottom: 8 }}>
                  <h3 style={{ margin: 0 }}>潜在废标风险</h3>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() =>
                      editors.patchAnalysis({
                        rejectionRisks: [...editors.analysis.rejectionRisks, ""],
                      })
                    }
                  >
                    添加
                  </button>
                </div>
                {editors.analysis.rejectionRisks.length === 0 ? (
                  <p style={{ fontSize: 13, color: "var(--text-secondary)" }}>
                    暂无。
                  </p>
                ) : (
                  editors.analysis.rejectionRisks.map((t, i) => (
                    <div key={i} style={{ display: "flex", gap: 6, marginBottom: 6 }}>
                      <input
                        value={t}
                        onChange={(e) => {
                          const next = [...editors.analysis.rejectionRisks];
                          next[i] = e.target.value;
                          editors.patchAnalysis({ rejectionRisks: next });
                        }}
                        style={{ flex: 1 }}
                      />
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => {
                          const next = editors.analysis.rejectionRisks.filter(
                            (_, j) => j !== i,
                          );
                          editors.patchAnalysis({ rejectionRisks: next });
                        }}
                      >
                        删
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>
            <div className="card card-pad">
              <div className="tp-toolbar">
                <strong>评分点</strong>
                <div className="tp-toolbar__spacer" />
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  disabled={pipeline.busy}
                  onClick={() => {
                    void (async () => {
                      try {
                        const t = await pipeline.runTask("analyze");
                        if (t.status === "success") {
                          const ok = await editors.reloadFromApi({
                            blocking: true,
                          });
                          if (ok) {
                            setTaskTip(
                              t.message || "招标分析已写入结构化结果",
                            );
                          }
                        }
                      } catch {
                        /* */
                      }
                    })();
                  }}
                >
                  <RefreshCw size={14} /> {pipeline.busy ? "分析中…" : "AI 招标分析"}
                </button>
              </div>
              <table className="score-table">
                <thead>
                  <tr>
                    <th>评分项</th>
                    <th>权重</th>
                    <th style={{ width: 56 }} />
                  </tr>
                </thead>
                <tbody>
                  {editors.analysis.scoringPoints.length === 0 ? (
                    <tr>
                      <td colSpan={3} style={{ color: "var(--text-secondary)" }}>
                        暂无评分点，请 AI 分析或添加
                      </td>
                    </tr>
                  ) : (
                    editors.analysis.scoringPoints.map((s, i) => (
                      <tr key={i}>
                        <td>
                          <input
                            value={s.name}
                            onChange={(e) => {
                              const next = [...editors.analysis.scoringPoints];
                              next[i] = { ...next[i], name: e.target.value };
                              editors.patchAnalysis({ scoringPoints: next });
                            }}
                          />
                        </td>
                        <td>
                          <input
                            className="mono"
                            value={s.weight}
                            onChange={(e) => {
                              const next = [...editors.analysis.scoringPoints];
                              next[i] = { ...next[i], weight: e.target.value };
                              editors.patchAnalysis({ scoringPoints: next });
                            }}
                            style={{ width: 72 }}
                          />
                        </td>
                        <td>
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={() => {
                              const next = editors.analysis.scoringPoints.filter(
                                (_, j) => j !== i,
                              );
                              editors.patchAnalysis({ scoringPoints: next });
                            }}
                          >
                            删
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
              <div className="tp-toolbar" style={{ marginTop: 12, marginBottom: 0 }}>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() =>
                    editors.patchAnalysis({
                      scoringPoints: [
                        ...editors.analysis.scoringPoints,
                        { name: "", weight: "" },
                      ],
                    })
                  }
                >
                  添加评分点
                </button>
                <div className="tp-toolbar__spacer" />
                <Link to={`/technical-plan/${project.id}/outline`} className="btn btn-primary">
                  下一步：生成大纲
                </Link>
              </div>
            </div>
          </section>

          <ResponseMatrixPanel
            items={editors.responseMatrix}
            chapters={editors.chapters}
            outline={editors.outline}
            onRefresh={editors.refreshResponseMatrix}
            onPatch={editors.updateResponseMatrixItem}
            suggestions={matrixSuggestions}
            suggestionBusy={suggestionBusy}
            suggestionProgressLabel={matchProgressLabel}
            onRequestSuggestions={() => void requestResponseMatrixSuggestions()}
            onApplySuggestions={applyResponseMatrixSuggestions}
            onClearSuggestions={() => {
              matchSessionRef.current += 1;
              setMatrixSuggestions([]);
              setMatchProgress(null);
            }}
            conflictMessage={editors.responseMatrixConflict?.message ?? null}
            onReloadRemote={editors.reloadRemoteResponseMatrix}
            mergePreview={editors.responseMatrixMergeUi?.preview ?? null}
            mergeChoices={editors.responseMatrixMergeUi?.choices ?? {}}
            mergeApplyError={
              editors.responseMatrixMergeUi?.applyError ??
              editors.responseMatrixConflict?.applyError ??
              null
            }
            mergeApplying={editors.responseMatrixMergeUi?.applying ?? false}
            onMergeChoice={editors.setResponseMatrixMergeChoice}
            onApplyMerge={() => void editors.applyResponseMatrixMerge()}
          />

          <ProjectGuidanceCard
            guidance={editors.guidance}
            onChange={editors.updateGuidance}
            mode="edit"
          />

          <div className="card card-pad" style={{ paddingTop: 4, paddingBottom: 4 }}>
            <AiFeedbackPanel
              stage="bid_analysis"
              targetLabel="招标分析结果"
              history={history}
              presets={[
                "补充遗漏的★号条款",
                "评分权重与文件不一致，请核对",
                "概述写得太泛，紧扣项目名称与规模",
                "废标风险再列 2～3 条形式评审点",
              ]}
              placeholder="例如：评分表漏了「售后服务 10%」；技术要求应单独列出信创清单…"
              onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
                void runRevise("analysis", {
                  stage: "bid_analysis",
                  message,
                  preserveStructure,
                  targetId,
                  targetLabel,
                  baseContent: serializeBidAnalysis(editors.analysis),
                })
              }
              onRegenerate={() => undefined}
            />
            <RevisePreviewPanel
              text={revisePreviewStep === "analysis" ? revisePreview : null}
              canApply
              applyLabel="替换项目概述"
              onApply={() => {
                if (revisePreview) editors.setAnalysisOverview(revisePreview);
                setRevisePreview(null);
              }}
              onClear={() => setRevisePreview(null)}
            />
          </div>
        </div>
      )}

      {active === "outline" && (
        <>
          <div className="tp-toolbar">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={pipeline.busy}
              onClick={() => {
                void (async () => {
                  try {
                    const t = await pipeline.runTask("outline");
                    if (t.status === "success") {
                      const ok = await editors.reloadFromApi({
                        blocking: true,
                      });
                      if (ok) {
                        const cite = formatKbCitationsTip(t);
                        setTaskTip(
                          cite
                            ? `大纲与章节列表已生成 · ${cite}`
                            : "大纲与章节列表已生成",
                        );
                      }
                    }
                  } catch {
                    /* */
                  }
                })();
              }}
            >
              {pipeline.busy ? "生成中…" : "AI 生成大纲"}
            </button>
            <span style={{ fontSize: "var(--fs-sm)", color: "var(--text-secondary)" }}>
              将根据招标分析/解析文本调用模型，写入后端 editor-state
            </span>
          </div>
          <OutlineStepWorkspace
            projectId={project.id}
            outline={editors.outline}
            selectedId={editors.selectedOutlineId}
            moveFlags={editors.moveFlags}
            generating={pipeline.busy}
            progress={pipeline.lastTask?.type === "outline" ? pipeline.lastTask.progress : 100}
            onSelect={editors.setSelectedOutlineId}
            onPatch={editors.patchOutlineNode}
            onDelete={editors.deleteOutlineNode}
            onAddSibling={editors.addOutlineSibling}
            onAddChild={editors.addOutlineChild}
            onMove={editors.moveOutline}
          />
          <div className="card card-pad" style={{ paddingTop: 4, paddingBottom: 4 }}>
            <AiFeedbackPanel
              stage="outline"
              targetLabel="目录大纲"
              history={history}
              presets={[
                "一级目录对齐招标文件",
                "压缩重复小节",
                "突出评分高的章节",
              ]}
              onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
                void runRevise("outline", {
                  stage: "outline",
                  message,
                  preserveStructure,
                  targetId,
                  targetLabel,
                  baseContent: outlineToMarkdown(editors.outline),
                })
              }
            />
            <RevisePreviewPanel
              text={revisePreviewStep === "outline" ? revisePreview : null}
              canApply={!!revisePreview}
              applyLabel="应用到大纲树"
              onApply={() => {
                if (!revisePreview) return;
                const tree = markdownToOutline(revisePreview);
                if (!tree.length) {
                  setTaskTip("无法从修订结果解析出大纲标题（需含 # 标题）");
                  return;
                }
                editors.replaceOutline(tree);
                setRevisePreview(null);
                setTaskTip(`已写回大纲（${tree.length} 个一级节点），可继续编辑`);
              }}
              onClear={() => setRevisePreview(null)}
            />
          </div>
        </>
      )}

      {active === "facts" && (
        <section className="card card-pad">
          <ProjectGuidanceCard
            guidance={editors.guidance}
            onChange={editors.updateGuidance}
            mode="summary"
          />
          <div className="hint-banner">
            <Info size={16} />
            <span>
              全局事实将用于后续各章编写约束。可手动增删改，也可在下方填写修改意见后修订。
            </span>
          </div>

          <FactsEditor
            facts={editors.facts}
            onAdd={editors.addFact}
            onUpdate={editors.updateFact}
            onRemove={editors.removeFact}
          />

          <AiFeedbackPanel
            stage="global_facts"
            targetLabel="全局事实"
            history={history}
            presets={[
              "统一售后响应时间为 4 小时",
              "补充信创软硬件清单事实",
              "删除与招标冲突的承诺",
              "增加建设周期与里程碑事实",
            ]}
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              void runRevise("facts", {
                stage: "global_facts",
                message,
                preserveStructure,
                targetId,
                targetLabel,
                baseContent: factsToText(editors.facts),
              })
            }
          />
          <RevisePreviewPanel
            text={revisePreviewStep === "facts" ? revisePreview : null}
            canApply={false}
            onClear={() => setRevisePreview(null)}
          />

          <div className="tp-toolbar" style={{ marginTop: 16, marginBottom: 0 }}>
            <div className="tp-toolbar__spacer" />
            <Link to={`/technical-plan/${project.id}/content`} className="btn btn-primary">
              下一步：正文生成
            </Link>
          </div>
        </section>
      )}

      {active === "content" && (
        <div className="tp-layout">
          <ProjectGuidanceCard
            guidance={editors.guidance}
            onChange={editors.updateGuidance}
            mode="summary"
          />
          <div className="tp-toolbar">
            <span className="badge badge-primary">
              {pipeline.busy ? "生成中…" : "可生成章节"}
            </span>
            <span style={{ fontSize: "var(--fs-sm)", color: "var(--text-secondary)" }}>
              左侧选章，右侧编辑；点「AI 生成本章」调用模型
            </span>
            <div className="tp-toolbar__spacer" />
            <button
              type="button"
              className="btn btn-soft btn-sm"
              disabled={pipeline.busy}
              aria-label="模板卡片融合建议"
              onClick={() => setContentFuseOpen(true)}
            >
              <FileStack size={14} /> 模板/卡片融合
            </button>
            <button
              type="button"
              className="btn btn-soft btn-sm"
              disabled={pipeline.busy}
              onClick={() => {
                void (async () => {
                  try {
                    const t = await pipeline.runTask("chapters", {
                      onlyEmpty: true,
                    });
                    if (t.status === "success") {
                      const ok = await editors.reloadFromApi({
                        blocking: true,
                      });
                      if (ok) {
                        const cite = formatKbCitationsTip(t);
                        setTaskTip(
                          `全书空章生成完成（${String(t.result?.generated ?? "")} 章）` +
                            (cite ? ` · ${cite}` : ""),
                        );
                      }
                    }
                  } catch {
                    /* */
                  }
                })();
              }}
            >
              {pipeline.busy ? "批量生成中…" : "生成全部空章节"}
            </button>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={pipeline.busy || !selectedChapter}
              onClick={() => {
                void (async () => {
                  try {
                    const t = await pipeline.runTask("chapter", {
                      chapterId: selectedChapter?.id,
                    });
                    if (t.status === "success") {
                      const ok = await editors.reloadFromApi({
                        blocking: true,
                      });
                      if (ok) {
                        const cite = formatKbCitationsTip(t);
                        setTaskTip(
                          `章节已生成：${selectedChapter?.title ?? ""}` +
                            (cite ? ` · ${cite}` : ""),
                        );
                      }
                    }
                  } catch {
                    /* */
                  }
                })();
              }}
            >
              <Play size={14} /> {pipeline.busy ? "生成中…" : "AI 生成本章"}
            </button>
          </div>

          <ContentFuseDialog
            open={contentFuseOpen}
            projectId={projectId}
            chapters={editors.chapters}
            busy={pipeline.busy && pipeline.lastTask?.type === "content_fuse"}
            onClose={() => setContentFuseOpen(false)}
            onRun={(payload) => pipeline.runTask("content_fuse", payload)}
            onCancelTask={() => pipeline.cancelTask()}
            onVersionedExternalWrite={editors.runVersionedExternalWrite}
          />

          <ChapterEditIntentPanel
            projectId={projectId}
            chapterId={editors.selectedChapterId}
          />

          <ChapterEditor
            chapters={editors.chapters}
            selectedId={editors.selectedChapterId}
            onSelect={editors.setSelectedChapterId}
            onChangeBody={editors.updateChapterBody}
            onChangeTitle={editors.updateChapterTitle}
            onUploadImage={pipeline.uploadImage}
            imageBusy={pipeline.busy}
            projectId={projectId}
          />

          {selectedChapter && (
            <div className="card card-pad" style={{ paddingTop: 4, paddingBottom: 4 }}>
              <AiFeedbackPanel
                stage="chapter_content"
                targetId={selectedChapter.id}
                targetLabel={`章节：${selectedChapter.title}`}
                history={history}
                presets={[
                  "扩写到目标字数，少套话",
                  "紧扣全局事实，删冲突表述",
                  "增加可落地的步骤与指标",
                  "语气更正式、偏政务标书",
                  "补充图表占位说明",
                ]}
                placeholder={`针对「${selectedChapter.title}」提出修改意见，例如：补充双机房切换流程；压缩产品软文…`}
                onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
                  void runRevise("content", {
                    stage: "chapter_content",
                    message,
                    preserveStructure,
                    targetId,
                    targetLabel,
                    baseContent: selectedChapter.body || selectedChapter.title,
                  })
                }
                onRegenerate={() => undefined}
              />
              <RevisePreviewPanel
                text={revisePreviewStep === "content" ? revisePreview : null}
                canApply={!!selectedChapter}
                applyLabel="替换当前章节正文"
                onApply={() => {
                  if (revisePreview && selectedChapter) {
                    editors.replaceChapterBody(selectedChapter.id, revisePreview);
                  }
                  setRevisePreview(null);
                }}
                onClear={() => setRevisePreview(null)}
              />
            </div>
          )}

          <div className="tp-toolbar" style={{ marginTop: 0, marginBottom: 0 }}>
            <div className="tp-toolbar__spacer" />
            <Link to={`/technical-plan/${project.id}/export`} className="btn btn-primary">
              下一步：导出
            </Link>
          </div>
        </div>
      )}

      {active === "export" && (
        <section className="card card-pad" style={{ maxWidth: 720 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
            <CheckCircle2 size={28} color="var(--success)" />
            <div>
              <strong style={{ fontSize: "var(--fs-lg)" }}>准备导出 Word</strong>
              <p style={{ margin: "4px 0 0", color: "var(--text-secondary)", fontSize: "var(--fs-sm)" }}>
                将合并大纲、正文与配图；导出样式可在模板设置中调整。
              </p>
            </div>
          </div>
          <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 12 }}>
            将使用「模板设置」中同步到后端的<strong>默认导出模板</strong>核心样式（字体/标题/页边距）。
            未配置时使用内置默认。
          </p>
          <div className="tp-toolbar" style={{ marginBottom: 0 }}>
            <Link to="/export-format" className="btn btn-ghost">
              管理模板 / 设为默认
            </Link>
            <div className="tp-toolbar__spacer" />
            <button
              type="button"
              className="btn btn-primary"
              disabled={pipeline.busy}
              onClick={() => {
                void (async () => {
                  try {
                    // 捕获启动时项目与代次；成功返回后仅当前代次可写告警
                    const startedProjectId = projectId;
                    const gen = ++exportImageWarningGenRef.current;
                    // 每次导出开始前清空旧告警，避免短暂展示上一轮结果
                    setExportImageWarningState(null);
                    const t = await pipeline.runTask("export");
                    if (t.status === "success") {
                      setTaskTip("Word 已生成，正在下载…");
                      // 契约：成功且代次仍匹配时先写告警，再始终继续既有下载；
                      // 旧任务迟到仍下载但不写告警，避免污染新项目页面
                      if (exportImageWarningGenRef.current === gen) {
                        setExportImageWarningState({
                          projectId: startedProjectId,
                          warnings: normalizeExportImageWarnings(
                            t.result?.imageWarnings,
                          ),
                        });
                      }
                      pipeline.downloadExport(t);
                    }
                  } catch {
                    /* */
                  }
                })();
              }}
            >
              <Download size={16} />{" "}
              {pipeline.busy ? "导出中…" : "生成并下载 Word"}
            </button>
          </div>
          <ExportImageWarnings warnings={exportImageWarnings} />
        </section>
      )}
    </div>
  );
}
