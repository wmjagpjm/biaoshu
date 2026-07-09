import { Briefcase } from "lucide-react";

/**
 * 商务标工作区
 * 用途：对齐 C 端 business-bid 模块入口；详细表单与生成二期实现。
 */
export function BusinessBidPage() {
  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>商务标</h1>
          <p>商务文件清单、报价说明与资格证明材料编排。当前为可二开占位页。</p>
        </div>
      </header>
      <div className="card empty-state">
        <Briefcase size={32} color="var(--text-muted)" style={{ margin: "0 auto 12px" }} />
        <strong>商务标流水线待接入</strong>
        将支持：资质目录、报价表、授权与诚信承诺等模板化生成。
      </div>
    </div>
  );
}
