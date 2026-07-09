import { useMemo } from "react";
import { useLocation } from "react-router-dom";
import { currentWorkspace } from "../../shared/mock/projects";

const titleMap: Record<string, string> = {
  "/": "工作台",
  "/technical-plan": "技术方案",
  "/knowledge-base": "知识库",
  "/duplicate-check": "标书查重",
  "/rejection-check": "废标项检查",
  "/business-bid": "商务标",
  "/bid-opportunity": "标讯",
  "/local-parser": "本地解析插件",
  "/export-format": "导出格式",
  "/settings": "设置",
};

function resolveTitle(pathname: string): string {
  if (pathname.startsWith("/technical-plan")) return "技术方案";
  return titleMap[pathname] ?? "标书工坊";
}

export function TopBar() {
  const { pathname } = useLocation();
  const title = useMemo(() => resolveTitle(pathname), [pathname]);

  return (
    <header className="topbar">
      <div className="topbar__crumb">
        <span>工作空间</span>
        <span aria-hidden>/</span>
        <strong>{title}</strong>
      </div>
      <div className="topbar__right">
        <div className="workspace-chip" title="个人版：账号与工作空间 1:1">
          <span className="workspace-chip__dot" />
          {currentWorkspace.name}
        </div>
        <div className="user-chip">
          演示用户
          <span className="user-chip__avatar">演</span>
        </div>
      </div>
    </header>
  );
}
