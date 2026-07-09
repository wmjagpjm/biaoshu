import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { CreatePage } from "../features/create/pages/CreatePage";
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
 * 前端路由
 * 默认进入 /create（喜鹊风格创建页）；技术方案工作流与其它工具页保留。
 */
export function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/create" replace />} />
          <Route path="create" element={<CreatePage />} />
          <Route path="projects" element={<TechnicalPlanListPage />} />
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
          <Route path="*" element={<Navigate to="/create" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
