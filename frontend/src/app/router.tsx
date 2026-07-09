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
import { MyTemplatesPage } from "../features/export-format/pages/MyTemplatesPage";
import { TemplateEditorPage } from "../features/export-format/pages/TemplateEditorPage";
import { SettingsPage } from "../features/settings/pages/SettingsPage";

/**
 * 前端路由
 * 对齐 C 端模块：创建、技术方案、知识库、查重、废标、商务标、导出模板、设置等。
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

          {/* 导出模板：设置 / 我的模板 / 新建 / 查看 / 编辑 */}
          <Route path="export-format" element={<ExportFormatPage />} />
          <Route path="export-format/my-templates" element={<MyTemplatesPage />} />
          <Route path="export-format/new" element={<TemplateEditorPage mode="new" />} />
          <Route
            path="export-format/:templateId/edit"
            element={<TemplateEditorPage mode="edit" />}
          />
          <Route
            path="export-format/:templateId"
            element={<TemplateEditorPage mode="view" />}
          />

          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/create" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
