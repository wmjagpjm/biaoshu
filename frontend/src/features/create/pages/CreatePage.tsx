import { useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import {
  Briefcase,
  Check,
  ClipboardList,
  FileStack,
  HardHat,
  FileSearch,
  FileWarning,
  FolderTree,
  Layers3,
  Plug,
  RefreshCcw,
  ShieldCheck,
  Sparkles,
  Upload,
  X,
} from "lucide-react";
import {
  featureGroups,
  findFeature,
  type CreateFeature,
  type FeatureColor,
} from "../featureCatalog";
import "./CreatePage.css";

const iconMap: Record<string, ReactNode> = {
  core: <Sparkles size={20} />,
  business: <Briefcase size={20} />,
  "full-bid": <FileStack size={20} />,
  engineering: <HardHat size={20} />,
  yibiaoxiebiao: <RefreshCcw size={20} />,
  "single-chapter": <Layers3 size={20} />,
  framework: <FolderTree size={20} />,
  "business-list": <ClipboardList size={20} />,
  duplicate: <FileSearch size={20} />,
  rejection: <FileWarning size={20} />,
  "local-parser": <Plug size={20} />,
};

function FeatureIcon({ id, color }: { id: string; color: FeatureColor }) {
  return <span className={`feature-item__icon color-${color}`}>{iconMap[id] ?? <Sparkles size={20} />}</span>;
}

type LocalFile = { id: string; name: string; sizeLabel: string };

/**
 * 创建投标方案页
 * 用途：对齐喜鹊标书 https://www.xiquebiaoshu.com/create
 * 布局：左侧功能入口轨 + 右侧标题/亮点/上传区 + 底部主操作。
 */
export function CreatePage() {
  const navigate = useNavigate();
  const [activeId, setActiveId] = useState("core");
  const [files, setFiles] = useState<LocalFile[]>([]);
  const [dragOver, setDragOver] = useState(false);

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

  function handleStart() {
    if (feature.routeTo) {
      navigate(feature.routeTo);
      return;
    }
    // 按能力类型进入对应工作区（前端 mock）
    if (feature.id === "business" || feature.id === "business-list") {
      navigate("/business-bid");
      return;
    }
    if (feature.id === "full-bid") {
      // 完整标书：先进入技术标演示流，商务册入口在商务标页继续
      navigate("/technical-plan/proj_01/document");
      return;
    }
    // 技术标 / 施工 / 以标写标 / 单章 / 框架 → 技术方案工作流
    navigate("/technical-plan/proj_01/document");
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
                            {f.badge === "new" && (
                              <span className="badge badge-new">{f.badgeText ?? "NEW"}</span>
                            )}
                            {f.badge === "free" && (
                              <span className="badge badge-free">{f.badgeText ?? "限免"}</span>
                            )}
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
                  {iconMap[feature.id] ?? <Sparkles size={24} />}
                </div>
                <div>
                  <h1 className="content-title">{feature.title}</h1>
                  <p className="content-desc">{feature.description}</p>
                </div>
              </div>
              <div className="highlights">
                {feature.highlights.map((h) => (
                  <div className="highlight" key={h}>
                    <span className="highlight__dot">
                      <Check size={14} strokeWidth={3} />
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
                onClick={() => {
                  if (!feature.routeTo) pickDemoFile();
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    if (!feature.routeTo) pickDemoFile();
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
                  if (feature.routeTo) return;
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
                文件仅用于当前工作空间解析与生成，API Key 与文档不会用于公开训练（策略可配置）。
              </div>
            </div>
          </div>

          <footer className="create-footer">
            <div className="create-footer__tip">
              {feature.routeTo
                ? "该能力将跳转到对应工具页"
                : files.length > 0
                  ? `已选择 ${files.length} 个文件 · 前端演示将进入技术方案工作流`
                  : "也可不上传，直接进入演示项目查看完整流程"}
            </div>
            <button type="button" className="btn btn-primary btn-lg" onClick={handleStart}>
              {feature.cta}
              <Sparkles size={16} />
            </button>
          </footer>
        </section>
      </div>
    </div>
  );
}
