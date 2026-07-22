/**
 * 模块：创建投标方案页（V1-I 真实文件摄入）
 * 用途：选开工能力 → 持有真实 File → 技术类一次 create 后串行 multipart 上传 → 再进入工作区。
 * 布局：左侧功能入口轨 + 右侧标题/亮点/上传区 + 底部主操作（对齐喜鹊式交互）。
 * 对接：
 *   - createProjectAsync（技术标等）→ 仅 POST /api/projects，失败固定中文且不导航
 *   - uploadProjectFileAsync → POST /projects/{id}/files，字段 file；串行、遇首失败停止
 *   - 商务类能力直接 navigate 到 /business-bid
 * 二次开发：禁止演示文件名/pending/本地假 ID；错误仅固定中文常量；上传经 projectStore 薄门面。
 */
import {
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type ReactNode,
} from "react";
import { useNavigate } from "react-router-dom";
import {
  Briefcase,
  Check,
  ClipboardList,
  FileStack,
  FolderTree,
  HardHat,
  Layers3,
  RefreshCcw,
  ShieldCheck,
  Sparkles,
  Upload,
  X,
} from "lucide-react";
import {
  createProjectAsync,
  industryFromFeature,
  uploadProjectFileAsync,
} from "../../technical-plan/lib/projectStore";
import {
  featureGroups,
  findFeature,
  type CreateFeature,
  type FeatureColor,
} from "../featureCatalog";
import "./CreatePage.css";

/** 仅开工类能力图标（质检工具见全局侧栏） */
const iconMap: Record<string, ReactNode> = {
  core: <Sparkles size={20} />,
  business: <Briefcase size={20} />,
  "full-bid": <FileStack size={20} />,
  engineering: <HardHat size={20} />,
  yibiaoxiebiao: <RefreshCcw size={20} />,
  "single-chapter": <Layers3 size={20} />,
  framework: <FolderTree size={20} />,
  "business-list": <ClipboardList size={20} />,
};

function FeatureIcon({ id, color }: { id: string; color: FeatureColor }) {
  return (
    <span className={`feature-item__icon color-${color}`}>
      {iconMap[id] ?? <Sparkles size={20} />}
    </span>
  );
}

/** 与知识库上传一致的真实大小展示（B / x.x KB / x.x MB） */
function formatSizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** 单文件本地项：持有真实 File 与上传状态（仅内存） */
type LocalFile = {
  id: string;
  file: File;
  name: string;
  sizeLabel: string;
  status: "pending" | "uploaded" | "failed";
};

const CREATE_ERROR = "项目创建失败，请稍后重试";
const UPLOAD_ERROR = "文件上传失败，请重试";

function makeLocalFile(file: File): LocalFile {
  return {
    id: `f_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`,
    file,
    name: file.name,
    sizeLabel: formatSizeLabel(file.size),
    status: "pending",
  };
}

export function CreatePage() {
  const navigate = useNavigate();
  const [activeId, setActiveId] = useState("core");
  const [files, setFiles] = useState<LocalFile[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** 真实 POST 成功后的 projectId；create 失败保持空 */
  const [createdProjectId, setCreatedProjectId] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  /** 同步单飞：同拍双击不得依赖 React 下一帧 disabled */
  const inflightRef = useRef(false);
  const filesRef = useRef<LocalFile[]>([]);
  const createdProjectIdRef = useRef<string | null>(null);

  const feature: CreateFeature = useMemo(() => findFeature(activeId), [activeId]);

  /** 项目已创建后锁定能力/文件集合（上传失败可重试，不可改选） */
  const selectionLocked = createdProjectId != null;

  function syncFiles(next: LocalFile[]) {
    filesRef.current = next;
    setFiles(next);
  }

  function appendFiles(fileList: File[]) {
    if (selectionLocked || fileList.length === 0) return;
    const additions = fileList.map(makeLocalFile);
    syncFiles([...filesRef.current, ...additions]);
  }

  function removeFile(id: string) {
    if (selectionLocked) return;
    syncFiles(filesRef.current.filter((f) => f.id !== id));
  }

  function openFilePicker() {
    if (selectionLocked || inflightRef.current) return;
    fileInputRef.current?.click();
  }

  function onFileInputChange(e: ChangeEvent<HTMLInputElement>) {
    if (selectionLocked) {
      e.target.value = "";
      return;
    }
    const list = e.target.files;
    if (list && list.length > 0) {
      appendFiles(Array.from(list));
    }
    // 允许再次选择相同文件
    e.target.value = "";
  }

  async function handleStart() {
    // 商务类：进入商务标工作区入口（不创建技术项目、不上传）
    if (feature.id === "business" || feature.id === "business-list") {
      navigate("/business-bid");
      return;
    }
    // 同步单飞：不依赖 submitting 状态帧
    if (inflightRef.current) return;
    inflightRef.current = true;
    setSubmitting(true);
    setError(null);

    try {
      const currentFiles = filesRef.current;
      const baseName =
        currentFiles[0]?.name.replace(/\.[^.]+$/, "") ||
        feature.title.replace(/生成$/, "");

      // 无已有项目时精确一次 create；失败零上传、选择保留
      let projectId = createdProjectIdRef.current;
      if (!projectId) {
        try {
          const project = await createProjectAsync({
            name: `${baseName} · ${feature.title}`,
            industry: industryFromFeature(feature.id),
            featureId: feature.id,
            technicalPlanStep: feature.id === "framework" ? 3 : 1,
            status: "draft",
          });
          projectId = project.id;
          createdProjectIdRef.current = project.id;
          setCreatedProjectId(project.id);
        } catch {
          setError(CREATE_ERROR);
          return;
        }
      }

      // 有文件：按稳定顺序串行上传 failed+未尝试；uploaded 不重传；遇首失败停止
      for (const item of filesRef.current) {
        if (item.status === "uploaded") continue;
        try {
          await uploadProjectFileAsync(projectId, item.file);
          const next = filesRef.current.map((f) =>
            f.id === item.id ? { ...f, status: "uploaded" as const } : f,
          );
          syncFiles(next);
        } catch {
          const next = filesRef.current.map((f) =>
            f.id === item.id ? { ...f, status: "failed" as const } : f,
          );
          syncFiles(next);
          setError(UPLOAD_ERROR);
          return;
        }
      }

      // 全部成功（或无文件零 upload）后才导航
      const step = feature.id === "framework" ? "outline" : "document";
      navigate(`/technical-plan/${projectId}/${step}`);
    } finally {
      inflightRef.current = false;
      setSubmitting(false);
    }
  }

  return (
    <div className="create-page">
      <div className="create-body">
        {/* 左侧功能轨 */}
        <aside className="feature-rail" aria-label="功能入口">
          <div className="feature-rail__scroll">
            {featureGroups.map((group) => (
              <div className="feature-group" key={group.title}>
                <div className="feature-group__title">{group.title}</div>
                <div className="feature-group__items">
                  {group.features.map((f) => {
                    const active = f.id === activeId;
                    return (
                      <button
                        type="button"
                        key={f.id}
                        className={`feature-item${active ? " is-active" : ""}`}
                        disabled={selectionLocked}
                        onClick={() => {
                          if (selectionLocked) return;
                          setActiveId(f.id);
                        }}
                      >
                        <span
                          className={`feature-item__indicator color-${f.color}`}
                        />
                        <FeatureIcon id={f.id} color={f.color} />
                        <div className="feature-item__body">
                          <div className="feature-item__title-row">
                            <span className="feature-item__title">{f.title}</span>
                          </div>
                          <div className="feature-item__tags">
                            {f.tags.map((t) => (
                              <span className="feature-tag" key={t}>
                                {t}
                              </span>
                            ))}
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </aside>

        {/* 右侧面板 */}
        <section className="create-panel">
          <div className="create-panel__scroll">
            <header className="content-header">
              <div className="content-heading">
                <div className="content-heading__icon">
                  {iconMap[feature.id] ?? <Sparkles size={22} />}
                </div>
                <div>
                  <h1 className="content-title">{feature.title}</h1>
                  <p className="content-desc">{feature.description}</p>
                </div>
              </div>

              {/* 技术标六步示意 */}
              {(feature.id === "core" ||
                feature.id === "full-bid" ||
                feature.id === "engineering" ||
                feature.id === "yibiaoxiebiao" ||
                feature.id === "framework") && (
                <div className="flow-steps" aria-label="工作流程">
                  {[
                    "文档解析",
                    "招标分析",
                    "目录生成",
                    "全局事实",
                    "正文撰写",
                    "导出交付",
                  ].map((label, i, arr) => (
                    <span
                      key={label}
                      style={{ display: "inline-flex", alignItems: "center" }}
                    >
                      <span className="flow-step">
                        <span className="flow-step__n">{i + 1}</span>
                        {label}
                      </span>
                      {i < arr.length - 1 ? (
                        <span className="flow-step__arrow" aria-hidden>
                          →
                        </span>
                      ) : null}
                    </span>
                  ))}
                </div>
              )}

              <div className="highlights">
                {feature.highlights.map((h) => (
                  <div className="highlight" key={h}>
                    <span className="highlight__dot">
                      <Check size={12} strokeWidth={2.5} />
                    </span>
                    <span>{h}</span>
                  </div>
                ))}
              </div>
            </header>

            <div className="upload-stage">
              {/* 隐藏真实多选 input；点击/键盘上传区触发；accept 仅为选择器软过滤 */}
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".pdf,.docx,.txt,.md,.markdown,application/pdf"
                style={{ display: "none" }}
                tabIndex={-1}
                disabled={selectionLocked}
                onChange={onFileInputChange}
              />
              <div
                className={`upload-card${dragOver ? " is-drag" : ""}`}
                role="button"
                tabIndex={selectionLocked ? -1 : 0}
                // 为 role=button 的上传区提供明确可访问名称
                aria-label={feature.uploadTitle}
                aria-disabled={selectionLocked ? true : undefined}
                onClick={() => openFilePicker()}
                onKeyDown={(e) => {
                  if (selectionLocked) return;
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    openFilePicker();
                  }
                }}
                onDragOver={(e) => {
                  e.preventDefault();
                  if (!selectionLocked) setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOver(false);
                  if (selectionLocked) return;
                  const list = e.dataTransfer.files
                    ? Array.from(e.dataTransfer.files)
                    : [];
                  // 空 drop 零动作，不得注入演示文件；类型/大小仍交后端权威校验
                  if (list.length === 0) return;
                  appendFiles(list);
                }}
              >
                <div className="upload-card__icon">
                  <Upload size={28} />
                </div>
                <h3 className="upload-card__title">{feature.uploadTitle}</h3>
                <p className="upload-card__desc">{feature.uploadDesc}</p>
                <div className="upload-card__types">
                  支持格式：{feature.fileTypes} · 单文件默认 ≤ 50MB
                </div>
              </div>

              {/* chip 行迁出 role=button 的 upload-card，仍为 upload-stage 直接子级 */}
              {files.length > 0 && (
                <div className="file-chip-row">
                  {files.map((f) => (
                    <span className="file-chip" key={f.id}>
                      <span>{f.name}</span>
                      <span style={{ color: "var(--text-tertiary)" }}>
                        {f.sizeLabel}
                      </span>
                      <button
                        type="button"
                        aria-label="移除"
                        disabled={selectionLocked}
                        onClick={() => removeFile(f.id)}
                      >
                        <X size={14} />
                      </button>
                    </span>
                  ))}
                </div>
              )}

              <div className="privacy-bar">
                <ShieldCheck size={14} color="var(--primary)" />
                文件仅用于当前工作空间解析与编写，不会用于公开模型训练。
              </div>
            </div>
          </div>

          <footer className="create-footer">
            <div className="create-footer__tip">
              {error ? (
                <span role="alert" style={{ color: "var(--danger)" }}>
                  {error}
                </span>
              ) : files.length > 0 ? (
                `已选择 ${files.length} 个文件，将创建项目并进入工作区`
              ) : (
                "可不上传文件，直接进入工作区"
              )}
            </div>
            <button
              type="button"
              className="btn btn-primary btn-lg"
              onClick={() => void handleStart()}
              disabled={submitting}
            >
              {submitting ? "创建中…" : feature.cta}
            </button>
          </footer>
        </section>
      </div>
    </div>
  );
}
