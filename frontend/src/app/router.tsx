import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { CreatePage } from "../features/create/pages/CreatePage";
import { TechnicalPlanListPage } from "../features/technical-plan/pages/TechnicalPlanListPage";
import { TechnicalPlanNewPage } from "../features/technical-plan/pages/TechnicalPlanNewPage";
import { TechnicalPlanWorkspace } from "../features/technical-plan/pages/TechnicalPlanWorkspace";
import { KnowledgeBasePage } from "../features/knowledge-base/pages/KnowledgeBasePage";
import { ResourcesPage } from "../features/resources/pages/ResourcesPage";
import { DuplicateCheckPage } from "../features/duplicate-check/pages/DuplicateCheckPage";
import { RejectionCheckPage } from "../features/rejection-check/pages/RejectionCheckPage";
import { BusinessBidPage } from "../features/business-bid/pages/BusinessBidPage";
import { BusinessBidWorkspace } from "../features/business-bid/pages/BusinessBidWorkspace";
import { BidOpportunityPage } from "../features/bid-opportunity/pages/BidOpportunityPage";
import { BidTemplatesPage } from "../features/bid-templates/pages/BidTemplatesPage";
import { LocalParserPage } from "../features/local-parser/pages/LocalParserPage";
import { ExportFormatPage } from "../features/export-format/pages/ExportFormatPage";
import { MyTemplatesPage } from "../features/export-format/pages/MyTemplatesPage";
import { TemplateEditorPage } from "../features/export-format/pages/TemplateEditorPage";
import { SettingsPage } from "../features/settings/pages/SettingsPage";
import { LoginPage } from "../features/auth/pages/LoginPage";
import { FinanceQuotePage } from "../features/finance/pages/FinanceQuotePage";
import { HrCredentialCardsPage } from "../features/hr/pages/HrCredentialCardsPage";
import { HrTeamRecommendationsPage } from "../features/hr-team-recommendation/pages/HrTeamRecommendationsPage";
import { BidderCompliancePreviewPage } from "../features/bidder/pages/BidderCompliancePreviewPage";
import {
  authRoleLabel,
  useAuthSession,
} from "../features/auth/hooks/useAuthSession";
import type { ReactNode } from "react";
import "../features/auth/pages/LoginPage.css";

/**
 * 模块：前端路由
 * 用途：对齐 C 端模块地图；按认证模式门禁业务壳；非 bid_writer 重定向受限页；
 *       严格 finance 可进 /finance 只读报价页；严格 hr 可进 /hr 人员资质与
 *       /hr/team-recommendations 团队推荐页；严格 bidder 可进 /bidder 匿名合规预览页。
 * 对接：AuthProvider；页面均挂 AppShell（登录页除外）。
 * 二次开发：导航隐藏不替代后端鉴权；disabled 保持既有业务路径但不开放财务/人力/投标人入口。
 */

/** 用途：加载中占位（握手未完成）。 */
function AuthBootSplash() {
  return (
    <div className="auth-restricted" data-testid="auth-loading">
      <div className="auth-restricted__card">
        <h1 className="auth-restricted__title">正在检查登录状态</h1>
        <p className="auth-restricted__body">请稍候…</p>
      </div>
    </div>
  );
}

/**
 * 用途：bootstrap-status 握手失败门控页。
 * 保守非业务态：不渲染业务壳，避免误判为 disabled。
 */
function AuthHandshakeErrorPage() {
  const { errorMessage, refresh } = useAuthSession();
  return (
    <div className="auth-restricted" data-testid="auth-handshake-error">
      <div className="auth-restricted__card">
        <h1 className="auth-restricted__title">无法确认认证模式</h1>
        <p className="auth-restricted__body">
          {errorMessage ??
            "暂时无法连接认证握手接口。为避免在未验证模式下误开业务功能，当前不进入工作台。请确认后端已启动后重试。"}
        </p>
        <button
          type="button"
          className="btn btn-soft btn-sm auth-shell__retry"
          data-testid="auth-handshake-retry"
          onClick={() => void refresh()}
        >
          重新检查
        </button>
      </div>
    </div>
  );
}

/**
 * 用途：受限角色说明页（非财务访问 /finance、非人力访问 /hr、非投标人访问 /bidder，或 finance/hr/bidder 访问业务页等）。
 */
function RestrictedAccessPage({ reason }: { reason?: string }) {
  const { activeMembership, me } = useAuthSession();
  const role = activeMembership?.role;
  return (
    <div className="auth-restricted" data-testid="auth-restricted">
      <div className="auth-restricted__card">
        <h1 className="auth-restricted__title">当前账号无权访问该功能</h1>
        <p className="auth-restricted__body">
          {reason ??
            `账号「${me?.user.username ?? "未知"}」在本工作空间的角色为「${authRoleLabel(
              role,
            )}」。P10A 阶段仅标书制作者可使用既有业务功能；P10B 起严格财务角色可进入「财务报价」只读页；P10D 起严格人力角色可进入「人员资质」页；P10E 起严格投标人角色可进入「合规预览」页。权限以服务端校验为准，本页仅作体验分流。`}
        </p>
      </div>
    </div>
  );
}

/** 用途：要求 bid_writer 才渲染业务页，否则重定向受限说明。 */
function RequireBusiness({ children }: { children: ReactNode }) {
  const { phase, canAccessBusiness } = useAuthSession();
  if (phase === "disabled") return <>{children}</>;
  if (!canAccessBusiness) {
    return <Navigate to="/restricted" replace />;
  }
  return <>{children}</>;
}

/** 用途：要求当前空间所有者才进设置。 */
function RequireOwner({ children }: { children: ReactNode }) {
  const { phase, canAccessSettings } = useAuthSession();
  if (phase === "disabled") return <>{children}</>;
  if (!canAccessSettings) {
    return <Navigate to="/restricted" replace />;
  }
  return <>{children}</>;
}

/**
 * 用途：要求严格 finance 才渲染财务报价页。
 * 约束：disabled / 其他角色不渲染业务页、不重定向到 /create，只显示受限说明。
 */
function RequireFinance({ children }: { children: ReactNode }) {
  const { canAccessFinance } = useAuthSession();
  if (!canAccessFinance) {
    return (
      <RestrictedAccessPage reason="仅财务角色可查看财务报价只读页。个人版兼容模式与所有者、标书制作者、人力、投标人均不可通过本入口访问。" />
    );
  }
  return <>{children}</>;
}

/**
 * 用途：要求严格 hr 才渲染人员资质/团队推荐页。
 * 约束：disabled / 其他角色不渲染 HR 页、不发 HR 请求，只显示受限说明。
 */
function RequireHr({ children }: { children: ReactNode }) {
  const { canAccessHr } = useAuthSession();
  if (!canAccessHr) {
    return (
      <RestrictedAccessPage reason="仅人力角色可管理本空间人员资质与团队推荐。个人版兼容模式与所有者、标书制作者、财务、投标人均不可通过本入口访问。" />
    );
  }
  return <>{children}</>;
}

/**
 * 用途：要求严格 bidder 才渲染匿名合规预览页。
 * 约束：disabled / 其他角色不渲染预览页、不发预览请求，只显示受限说明。
 */
function RequireBidder({ children }: { children: ReactNode }) {
  const { canAccessBidder } = useAuthSession();
  if (!canAccessBidder) {
    return (
      <RestrictedAccessPage reason="仅投标人角色可查看匿名合规预览。个人版兼容模式与所有者、标书制作者、财务、人力均不可通过本入口访问。" />
    );
  }
  return <>{children}</>;
}

/**
 * 用途：按认证阶段选择登录页或业务壳路由树。
 */
function AuthGate() {
  const { phase } = useAuthSession();

  if (phase === "loading") {
    return <AuthBootSplash />;
  }

  if (phase === "handshake_error") {
    return <AuthHandshakeErrorPage />;
  }

  if (phase === "unauthenticated") {
    return <LoginPage />;
  }

  // disabled | authenticated —— 仅成功握手后才进入业务壳
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/create" replace />} />
        <Route
          path="create"
          element={
            <RequireBusiness>
              <CreatePage />
            </RequireBusiness>
          }
        />
        <Route
          path="projects"
          element={
            <RequireBusiness>
              <TechnicalPlanListPage />
            </RequireBusiness>
          }
        />
        <Route
          path="technical-plan"
          element={
            <RequireBusiness>
              <TechnicalPlanListPage />
            </RequireBusiness>
          }
        />
        <Route
          path="technical-plan/new"
          element={
            <RequireBusiness>
              <TechnicalPlanNewPage />
            </RequireBusiness>
          }
        />
        <Route
          path="technical-plan/:projectId"
          element={
            <RequireBusiness>
              <TechnicalPlanWorkspace />
            </RequireBusiness>
          }
        />
        <Route
          path="technical-plan/:projectId/:step"
          element={
            <RequireBusiness>
              <TechnicalPlanWorkspace />
            </RequireBusiness>
          }
        />
        <Route
          path="knowledge-base"
          element={
            <RequireBusiness>
              <KnowledgeBasePage />
            </RequireBusiness>
          }
        />
        <Route
          path="resources"
          element={
            <RequireBusiness>
              <ResourcesPage />
            </RequireBusiness>
          }
        />
        <Route
          path="bid-templates"
          element={
            <RequireBusiness>
              <BidTemplatesPage />
            </RequireBusiness>
          }
        />
        <Route
          path="duplicate-check"
          element={
            <RequireBusiness>
              <DuplicateCheckPage />
            </RequireBusiness>
          }
        />
        <Route
          path="rejection-check"
          element={
            <RequireBusiness>
              <RejectionCheckPage />
            </RequireBusiness>
          }
        />
        <Route
          path="business-bid"
          element={
            <RequireBusiness>
              <BusinessBidPage />
            </RequireBusiness>
          }
        />
        <Route
          path="business-bid/:projectId"
          element={
            <RequireBusiness>
              <BusinessBidWorkspace />
            </RequireBusiness>
          }
        />
        <Route
          path="business-bid/:projectId/:step"
          element={
            <RequireBusiness>
              <BusinessBidWorkspace />
            </RequireBusiness>
          }
        />
        <Route
          path="bid-opportunity"
          element={
            <RequireBusiness>
              <BidOpportunityPage />
            </RequireBusiness>
          }
        />
        <Route
          path="local-parser"
          element={
            <RequireBusiness>
              <LocalParserPage />
            </RequireBusiness>
          }
        />
        <Route
          path="export-format"
          element={
            <RequireBusiness>
              <ExportFormatPage />
            </RequireBusiness>
          }
        />
        <Route
          path="export-format/my-templates"
          element={
            <RequireBusiness>
              <MyTemplatesPage />
            </RequireBusiness>
          }
        />
        <Route
          path="export-format/new"
          element={
            <RequireBusiness>
              <TemplateEditorPage mode="new" />
            </RequireBusiness>
          }
        />
        <Route
          path="export-format/:templateId/edit"
          element={
            <RequireBusiness>
              <TemplateEditorPage mode="edit" />
            </RequireBusiness>
          }
        />
        <Route
          path="export-format/:templateId"
          element={
            <RequireBusiness>
              <TemplateEditorPage mode="view" />
            </RequireBusiness>
          }
        />
        <Route
          path="settings"
          element={
            <RequireOwner>
              <SettingsPage />
            </RequireOwner>
          }
        />
        <Route
          path="finance"
          element={
            <RequireFinance>
              <FinanceQuotePage />
            </RequireFinance>
          }
        />
        <Route
          path="hr"
          element={
            <RequireHr>
              <HrCredentialCardsPage />
            </RequireHr>
          }
        />
        <Route
          path="hr/team-recommendations"
          element={
            <RequireHr>
              <HrTeamRecommendationsPage />
            </RequireHr>
          }
        />
        <Route
          path="bidder"
          element={
            <RequireBidder>
              <BidderCompliancePreviewPage />
            </RequireBidder>
          }
        />
        <Route path="restricted" element={<RestrictedAccessPage />} />
        <Route path="*" element={<Navigate to="/create" replace />} />
      </Route>
    </Routes>
  );
}

export function AppRouter() {
  return (
    <BrowserRouter>
      <AuthGate />
    </BrowserRouter>
  );
}
