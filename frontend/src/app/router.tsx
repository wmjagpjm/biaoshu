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

/**
 * 前端路由
 * 用途：对齐 C 端模块地图；商务标含分步工作区；中标内容模板库独立于导出版式模板。
 * 对接：页面均挂 AppShell；后端就绪后无需改路径形状。
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
          <Route path="resources" element={<ResourcesPage />} />
          <Route path="bid-templates" element={<BidTemplatesPage />} />
          <Route path="duplicate-check" element={<DuplicateCheckPage />} />
          <Route path="rejection-check" element={<RejectionCheckPage />} />

          {/* 商务标：入口列表 + 分步工作区 */}
          <Route path="business-bid" element={<BusinessBidPage />} />
          <Route path="business-bid/:projectId" element={<BusinessBidWorkspace />} />
          <Route
            path="business-bid/:projectId/:step"
            element={<BusinessBidWorkspace />}
          />

          <Route path="bid-opportunity" element={<BidOpportunityPage />} />
          <Route path="local-parser" element={<LocalParserPage />} />

          {/* 导出模板：设置 / 我的模板 / 新建 / 查看 / 编辑（Word 版式，非中标内容） */}
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
