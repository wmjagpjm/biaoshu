import { useMemo, useState, type ReactNode } from "react";
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
  return <span className={`feature-item__icon color-${color}`}>{iconMap[id] ?? <Sparkles size={20} />}</span>;
}

type LocalFile = { id: string; name: string; sizeLabel: string };

const CREATE_ERROR = "项目创建失败，请稍后重试";

/**
 * 模块：创建投标方案页
 * 用途：选开工能力 → 选/模拟招标文件 → 真实 POST 创建项目并进入对应工作区。
 * 布局：左侧功能入口轨 + 右侧标题/亮点/上传区 + 底部主操作（对齐喜鹊式交互）。
 * 对接：
 *   - createProjectAsync（技术标等）→ 仅 POST /api/projects，失败固定中文且不导航
 *   - 商务类能力直接 navigate 到 /business-bid
 * 二次开发：真实文件上传改为 multipart + 后端解析任务，勿在页面散落 API；禁止本地假 ID。
 */
export function CreatePage() {
  const navigate = useNavigate();
  const [activeId, setActiveId] = useState("core");
  const [files, setFiles] = useState<LocalFile[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const feature: CreateFeature = useMemo(() => findFeature(activeId), [activeId]);

  function pickDemoFile() {
    setFiles((prev) => [
      ...prev,
      {
        id: `f_${Date.now()}`,
        name: "招标文件-正式稿.pdf",
        sizeLabel: "12.4 MB",
      },
    ]);
  }

  function removeFile(id: string) {
    setFiles((prev) => prev.filter((f) => f.id !== id));
  }

  async function handleStart() {
    // 商务类：进入商务标工作区入口
    if (feature.id === "business" || feature.id === "business-list") {
      navigate("/business-bid");
      return;
    }
    if (submitting) return;
    setSubmitting(true);
    setError(null);

    // 技术标类 / 完整投标 / 框架等：创建项目并进入文档解析步
    const fileNames = files.map((f) => f.name);
    const baseName =
      fileNames[0]?.replace(/\.[^.]+$/, "") ||
      feature.title.replace(/生成$/, "");
    try {
      const project = await createProjectAsync({
        name: `${baseName} · ${feature.title}`,
        industry: industryFromFeature(feature.id),
        featureId: feature.id,
        fileNames: fileNames.length
          ? fileNames
          : ["招标文件-正式稿.pdf"],
        technicalPlanStep: feature.id === "framework" ? 3 : 1,
        status: "draft",
      });
      const step = feature.id === "framework" ? "outline" : "document";
      navigate(`/technical-plan/${project.id}/${step}`);
    } catch {
      setError(CREATE_ERROR);
    } finally {
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
                        onClick={() => setActiveId(f.id)}
                      >
                        <span className={`feature-item__indicator color-${f.color}`} />
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
                    <span key={label} style={{ display: "inline-flex", alignItems: "center" }}>
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
              <div
                className={`upload-card${dragOver ? " is-drag" : ""}`}
                role="button"
                tabIndex={0}
                onClick={() => pickDemoFile()}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    pickDemoFile();
                  }
                }}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOver(false);
                  const name = e.dataTransfer.files?.[0]?.name;
                  if (name) {
                    setFiles((prev) => [
                      ...prev,
                      { id: `f_${Date.now()}`, name, sizeLabel: "本地文件" },
                    ]);
                  } else {
                    pickDemoFile();
                  }
                }}
              >
                <div className="upload-card__icon">
                  <Upload size={28} />
                </div>
                <h3 className="upload-card__title">{feature.uploadTitle}</h3>
                <p className="upload-card__desc">{feature.uploadDesc}</p>
                <div className="upload-card__types">
                  支持格式：{feature.fileTypes} · 单文件建议 ≤ 100MB
                </div>
                {files.length > 0 && (
                  <div className="file-chip-row" onClick={(e) => e.stopPropagation()}>
                    {files.map((f) => (
                      <span className="file-chip" key={f.id}>
                        <span>{f.name}</span>
                        <span style={{ color: "var(--text-tertiary)" }}>{f.sizeLabel}</span>
                        <button type="button" aria-label="移除" onClick={() => removeFile(f.id)}>
                          <X size={14} />
                        </button>
                      </span>
                    ))}
                  </div>
                )}
              </div>

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
