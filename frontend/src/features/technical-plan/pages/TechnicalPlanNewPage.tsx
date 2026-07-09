import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { createProjectAsync } from "../lib/projectStore";
import "./TechnicalPlan.css";

type LocationState = {
  fromOpportunity?: boolean;
  title?: string;
  oppId?: string;
};

/**
 * 模块：新建技术方案项目页
 * 用途：表单收集名称/行业/备注 → createProjectAsync → 进入文档解析步。
 * 对接：
 *   - createProjectAsync → POST /api/projects
 *   - 标讯页 navigate state：fromOpportunity / title / oppId 预填
 * 二次开发：备注目前仅展示，未入库；若需持久化请扩展后端 Project 字段。
 */
export function TechnicalPlanNewPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const state = (location.state ?? {}) as LocationState;

  const [name, setName] = useState(state.title ?? "");
  const [industry, setIndustry] = useState("智慧城市");
  const [note, setNote] = useState(
    state.fromOpportunity && state.oppId
      ? `来自标讯 ${state.oppId}`
      : "",
  );

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const project = await createProjectAsync({
      name: name.trim() || "未命名技术标项目",
      industry,
      featureId: "core",
      fileNames: note ? undefined : undefined,
      technicalPlanStep: 1,
      status: "draft",
    });
    navigate(`/technical-plan/${project.id}/document`);
  }

  return (
    <div className="page" style={{ maxWidth: 640 }}>
      <header className="page-header">
        <div>
          <h1>新建项目</h1>
          <p>创建一个技术标工作区。文件与生成结果将归属当前账号工作空间。</p>
        </div>
        <Link to="/technical-plan" className="btn btn-ghost">
          <ArrowLeft size={16} /> 返回
        </Link>
      </header>

      <form className="card card-pad" onSubmit={handleSubmit}>
        <div style={{ display: "grid", gap: 14 }}>
          <div className="field">
            <label htmlFor="name">项目名称</label>
            <input
              id="name"
              name="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：某某智慧平台技术标"
              required
            />
          </div>
          <div className="field">
            <label htmlFor="industry">行业</label>
            <select
              id="industry"
              name="industry"
              value={industry}
              onChange={(e) => setIndustry(e.target.value)}
            >
              <option>智慧城市</option>
              <option>医疗信息化</option>
              <option>能源环保</option>
              <option>教育</option>
              <option>工程建设</option>
              <option>其他</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="note">备注（可选）</label>
            <textarea
              id="note"
              name="note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="招标编号、截止时间等"
            />
          </div>
          <div className="tp-toolbar" style={{ marginBottom: 0 }}>
            <div className="tp-toolbar__spacer" />
            <button type="submit" className="btn btn-primary">
              创建并开始解析
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
