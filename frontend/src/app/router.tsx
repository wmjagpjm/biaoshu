import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { HomePage } from "../features/home/pages/HomePage";
import { TechnicalPlanListPage } from "../features/technical-plan/pages/TechnicalPlanListPage";
import { TechnicalPlanNewPage } from "../features/technical-plan/pages/TechnicalPlanNewPage";
import { TechnicalPlanWorkspace } from "../features/technical-plan/pages/TechnicalPlanWorkspace";
import { KnowledgeBasePage } from "../features/knowledge-base/pages/KnowledgeBasePage";
import { DuplicateCheckPage } from "../features/duplicate-check/pages/DuplicateCheckPage";
import { RejectionCheckPage } from "../features/rejection-check/pages/RejectionCheckPage";
import { BusinessBidPage } from "../features/business-bid/pages/BusinessBidPage";
import { BidOpportunityPage } from "../features/bid-opportunity/pages/BidOpportunityPage";
import { LocalParserPage } from "../features/local-parser/pages/LocalParserPage";
import { ExportFormatPage } from "../features/export-format/pages/ExportFormatPage";
import { SettingsPage } from "../features/settings/pages/SettingsPage";

/**
 * 前端路由表
 * 用途：对齐 C 端功能模块的信息架构；后续可按权限裁剪路由。
 */
export function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<HomePage />} />
          <Route path="technical-plan" element={<TechnicalPlanListPage />} />
          <Route path="technical-plan/new" element={<TechnicalPlanNewPage />} />
          <Route path="technical-plan/:projectId" element={<TechnicalPlanWorkspace />} />
          <Route
            path="technical-plan/:projectId/:step"
            element={<TechnicalPlanWorkspace />}
          />
          <Route path="knowledge-base" element={<KnowledgeBasePage />} />
          <Route path="duplicate-check" element={<DuplicateCheckPage />} />
          <Route path="rejection-check" element={<RejectionCheckPage />} />
          <Route path="business-bid" element={<BusinessBidPage />} />
          <Route path="bid-opportunity" element={<BidOpportunityPage />} />
          <Route path="local-parser" element={<LocalParserPage />} />
          <Route path="export-format" element={<ExportFormatPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
