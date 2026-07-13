import { useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  authRoleLabel,
  useAuthSession,
} from "../../features/auth/hooks/useAuthSession";

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
  "/restricted": "权限说明",
};

function resolveTitle(pathname: string): string {
  if (pathname.startsWith("/technical-plan")) return "技术方案";
  return titleMap[pathname] ?? "标书";
}

/**
 * 顶栏（遗留/可选）
 * 用途：展示当前路由标题、用户、角色、工作空间与退出。
 * 对接：useAuthSession；与 AppShell 用户条语义一致。
 * 二次开发：禁止展示 API Key、Cookie、CSRF 或会话摘要。
 */
export function TopBar() {
  const { pathname } = useLocation();
  const title = useMemo(() => resolveTitle(pathname), [pathname]);
  const {
    phase,
    me,
    activeMembership,
    logout,
  } = useAuthSession();
  const [loggingOut, setLoggingOut] = useState(false);

  const username =
    phase === "disabled" ? "本机用户" : (me?.user.username ?? "未登录");
  const roleLabel =
    phase === "disabled" ? "个人版" : authRoleLabel(activeMembership?.role);
  const workspaceLabel =
    phase === "disabled"
      ? "个人工作空间"
      : (activeMembership?.name ?? activeMembership?.id ?? "未选择空间");

  async function onLogout() {
    if (phase !== "authenticated" || loggingOut) return;
    setLoggingOut(true);
    try {
      await logout();
    } finally {
      setLoggingOut(false);
    }
  }

  return (
    <header className="topbar">
      <div className="topbar__crumb">
        <span>工作空间</span>
        <span aria-hidden>/</span>
        <strong>{title}</strong>
      </div>
      <div className="topbar__right">
        <div
          className="workspace-chip"
          title={`${workspaceLabel} · ${roleLabel}`}
          data-testid="topbar-workspace"
        >
          <span className="workspace-chip__dot" />
          {workspaceLabel}
        </div>
        <div className="user-chip" data-testid="topbar-user-chip">
          {username}
          <span className="user-chip__avatar" aria-hidden>
            {username.slice(0, 1)}
          </span>
          <span style={{ marginLeft: 6, fontSize: 12, opacity: 0.75 }}>
            {roleLabel}
          </span>
        </div>
        {phase === "authenticated" && (
          <button
            type="button"
            className="btn btn-soft btn-sm"
            data-testid="topbar-logout-legacy"
            onClick={() => void onLogout()}
            disabled={loggingOut}
          >
            {loggingOut ? "退出中…" : "退出"}
          </button>
        )}
      </div>
    </header>
  );
}
