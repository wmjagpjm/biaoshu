import { useEffect, useState } from "react";
import { BookOpen, Lock, Unlock } from "lucide-react";
import { apiFetch } from "../../../shared/lib/api";
import type { ProjectGenerationGuidance } from "../../../shared/types/aiFeedback";

type KbFolderOpt = { id: string; name: string };

type Props = {
  guidance: ProjectGenerationGuidance;
  onChange: (patch: Partial<ProjectGenerationGuidance>) => void;
  /** compact：后续步骤只读摘要 */
  mode?: "edit" | "summary";
};

/**
 * 项目级生成要求
 * 用途：招标分析后编辑字数、侧重点、格式与知识库范围，并向下传递给大纲/正文 AI。
 * 对接：editor-state.guidance；生成时 task_service 读 kbFolderIds / kbEnabled
 */
export function ProjectGuidanceCard({ guidance, onChange, mode = "edit" }: Props) {
  const [folders, setFolders] = useState<KbFolderOpt[]>([]);

  useEffect(() => {
    if (mode !== "edit") return;
    let cancelled = false;
    void apiFetch<KbFolderOpt[]>("/knowledge/folders")
      .then((list) => {
        if (!cancelled && Array.isArray(list)) {
          setFolders(list.map((f) => ({ id: f.id, name: f.name })));
        }
      })
      .catch(() => {
        if (!cancelled) setFolders([]);
      });
    return () => {
      cancelled = true;
    };
  }, [mode]);

  const selectedFolders = guidance.kbFolderIds ?? [];
  const kbOn = guidance.kbEnabled !== false;

  if (mode === "summary") {
    const chips: string[] = [];
    if (guidance.targetWordCount) chips.push(`目标约 ${guidance.targetWordCount.toLocaleString()} 字`);
    if (guidance.chapterFocus?.trim()) chips.push("已设章节侧重点");
    if (guidance.formatRequirements?.trim()) chips.push("已设格式要求");
    if (guidance.extraRequirements?.trim()) chips.push("已有补充要求");
    if (kbOn) {
      if (selectedFolders.length > 0) {
        chips.push(`知识库 ${selectedFolders.length} 个文件夹`);
      } else {
        chips.push("知识库全库检索");
      }
    } else {
      chips.push("知识库已关闭");
    }

    return (
      <div className="card card-pad" style={{ marginBottom: 12, background: "var(--primary-soft)", borderColor: "rgba(100,56,255,0.15)" }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <strong style={{ color: "var(--primary-deep)" }}>已注入后续生成的项目要求</strong>
          {chips.length === 0 ? (
            <span style={{ fontSize: "var(--fs-sm)", color: "var(--text-secondary)" }}>
              尚未填写（可在「招标分析」步补充）
            </span>
          ) : (
            chips.map((c) => (
              <span key={c} className="badge badge-primary">
                {c}
              </span>
            ))
          )}
        </div>
        {(guidance.chapterFocus || guidance.formatRequirements || guidance.extraRequirements) && (
          <div style={{ marginTop: 10, fontSize: "var(--fs-sm)", color: "var(--text-body)", lineHeight: 1.6 }}>
            {guidance.chapterFocus ? <div>· 侧重点：{guidance.chapterFocus}</div> : null}
            {guidance.formatRequirements ? <div>· 格式：{guidance.formatRequirements}</div> : null}
            {guidance.extraRequirements ? <div>· 其它：{guidance.extraRequirements}</div> : null}
          </div>
        )}
      </div>
    );
  }

  function toggleFolder(id: string) {
    const set = new Set(selectedFolders);
    if (set.has(id)) set.delete(id);
    else set.add(id);
    onChange({ kbFolderIds: [...set] });
  }

  return (
    <div className="card card-pad" style={{ marginTop: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 12 }}>
        <div>
          <strong style={{ fontSize: "var(--fs-md)" }}>生成要求（将带入大纲 / 正文 AI）</strong>
          <p style={{ margin: "6px 0 0", fontSize: "var(--fs-sm)", color: "var(--text-secondary)" }}>
            解析结果可人工修订；这里补充字数、侧重点、格式与知识库范围。
          </p>
        </div>
        <button
          type="button"
          className={`btn btn-sm ${guidance.lockedForNextStage ? "btn-soft" : "btn-ghost"}`}
          onClick={() =>
            onChange({ lockedForNextStage: !guidance.lockedForNextStage })
          }
        >
          {guidance.lockedForNextStage ? (
            <>
              <Lock size={14} /> 已确认带入
            </>
          ) : (
            <>
              <Unlock size={14} /> 标记为已确认
            </>
          )}
        </button>
      </div>

      <div style={{ display: "grid", gap: 12 }}>
        <div className="field">
          <label htmlFor="gw-words">目标总字数（约）</label>
          <input
            id="gw-words"
            type="number"
            min={5000}
            step={1000}
            value={guidance.targetWordCount ?? ""}
            onChange={(e) =>
              onChange({
                targetWordCount: e.target.value ? Number(e.target.value) : undefined,
              })
            }
            placeholder="例如 80000"
          />
        </div>
        <div className="field">
          <label htmlFor="gw-focus">章节侧重点 / 必须展开的内容</label>
          <textarea
            id="gw-focus"
            value={guidance.chapterFocus ?? ""}
            onChange={(e) => onChange({ chapterFocus: e.target.value })}
            placeholder="例如：重点写信创适配与等保三级；实施进度按 180 天里程碑展开；弱化通用产品介绍…"
          />
        </div>
        <div className="field">
          <label htmlFor="gw-format">特殊格式 / 目录强制要求</label>
          <textarea
            id="gw-format"
            value={guidance.formatRequirements ?? ""}
            onChange={(e) => onChange({ formatRequirements: e.target.value })}
            placeholder="例如：一级目录必须与招标文件一致；需含横道图说明位；正文字体与导出模板按政务投标…"
          />
        </div>
        <div className="field">
          <label htmlFor="gw-extra">其它补充要求</label>
          <textarea
            id="gw-extra"
            value={guidance.extraRequirements ?? ""}
            onChange={(e) => onChange({ extraRequirements: e.target.value })}
            placeholder="例如：避免空话套话；业绩只写同城同类；运维承诺与全局事实一致…"
          />
        </div>

        <div
          className="field"
          style={{
            borderTop: "1px solid var(--border)",
            paddingTop: 12,
            marginTop: 4,
          }}
        >
          <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <BookOpen size={16} />
            知识库检索范围
          </label>
          <p style={{ margin: "4px 0 8px", fontSize: "var(--fs-sm)", color: "var(--text-secondary)" }}>
            大纲/正文生成时关键词检索注入参考片段。不勾选任何文件夹 = 全库。
          </p>
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 8,
              fontSize: 13,
            }}
          >
            <input
              type="checkbox"
              checked={kbOn}
              onChange={(e) => onChange({ kbEnabled: e.target.checked })}
            />
            启用知识库注入
          </label>
          {kbOn && (
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: 8,
                opacity: kbOn ? 1 : 0.5,
              }}
            >
              {folders.length === 0 ? (
                <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>
                  暂无文件夹（请先在「知识库」建文件夹/上传文档，或后端未启动）
                </span>
              ) : (
                folders.map((f) => {
                  const checked = selectedFolders.includes(f.id);
                  return (
                    <label
                      key={f.id}
                      className="badge"
                      style={{
                        cursor: "pointer",
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 6,
                        background: checked
                          ? "var(--primary-soft)"
                          : "var(--bg-elevated, var(--surface))",
                        border: "1px solid var(--border)",
                        padding: "6px 10px",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleFolder(f.id)}
                      />
                      {f.name}
                    </label>
                  );
                })
              )}
              {selectedFolders.length > 0 && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => onChange({ kbFolderIds: [] })}
                >
                  清空选择（改回全库）
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
