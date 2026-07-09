import { Link, useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import "./TechnicalPlan.css";

/**
 * 新建技术方案项目
 * 用途：收集项目基础信息后进入文档解析步骤（当前 mock 跳转到演示项目）。
 */
export function TechnicalPlanNewPage() {
  const navigate = useNavigate();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    // 后端就绪后改为 POST /api/projects
    navigate("/technical-plan/proj_01/document");
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
            <input id="name" name="name" placeholder="例如：某某智慧平台技术标" required />
          </div>
          <div className="field">
            <label htmlFor="industry">行业</label>
            <select id="industry" name="industry" defaultValue="智慧城市">
              <option>智慧城市</option>
              <option>医疗信息化</option>
              <option>能源环保</option>
              <option>教育</option>
              <option>其他</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="note">备注（可选）</label>
            <textarea id="note" name="note" placeholder="招标编号、截止时间等" />
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
