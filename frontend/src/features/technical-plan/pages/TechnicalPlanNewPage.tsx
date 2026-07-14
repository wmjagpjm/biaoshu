/**
 * 模块：新建技术方案项目页
 * 用途：表单收集名称/行业/备注 → 真实 POST createProjectAsync → 进入文档解析步。
 * 对接：createProjectAsync → POST /api/projects；标讯页 navigate state 预填。
 * 二次开发：失败停留本页、固定中文错误、不导航假工作区；备注目前仅展示未入库。
 */

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

const CREATE_ERROR = "项目创建失败，请稍后重试";

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
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const project = await createProjectAsync({
        name: name.trim() || "未命名技术标项目",
        industry,
        featureId: "core",
        technicalPlanStep: 1,
        status: "draft",
      });
      navigate(`/technical-plan/${project.id}/document`);
    } catch {
      setError(CREATE_ERROR);
    } finally {
      setSubmitting(false);
    }
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

      <form className="card card-pad" onSubmit={(e) => void handleSubmit(e)}>
        <div style={{ display: "grid", gap: 14 }}>
          {error ? (
            <div role="alert" style={{ color: "var(--danger)", fontSize: 14 }}>
              {error}
            </div>
          ) : null}
          <div className="field">
            <label htmlFor="name">项目名称</label>
            <input
              id="name"
              name="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：某某智慧平台技术标"
              required
              disabled={submitting}
            />
          </div>
          <div className="field">
            <label htmlFor="industry">行业</label>
            <select
              id="industry"
              name="industry"
              value={industry}
              onChange={(e) => setIndustry(e.target.value)}
              disabled={submitting}
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
              disabled={submitting}
            />
          </div>
          <div className="tp-toolbar" style={{ marginBottom: 0 }}>
            <div className="tp-toolbar__spacer" />
            <button
              type="submit"
              className="btn btn-primary"
              disabled={submitting}
            >
              {submitting ? "创建中…" : "创建并开始解析"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
