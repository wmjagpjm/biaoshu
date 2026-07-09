/**
 * 模块：商务标 mock 数据
 * 用途：前端分步工作区演示；后端就绪后删除本文件，改走 API。
 */

import type {
  BusinessBidProject,
  BusinessBidWorkspaceState,
  CommitBlock,
  QualifyItem,
  QuoteRow,
  TocItem,
} from "./types";

export const mockBusinessProjects: BusinessBidProject[] = [
  {
    id: "bb_01",
    workspaceId: "ws_demo",
    name: "某市智慧交通综合管理平台 · 商务标",
    industry: "智慧城市",
    currentStep: 3,
    updatedAt: "2026-07-09T10:15:00+08:00",
    linkedTechnicalProjectId: "proj_01",
  },
  {
    id: "bb_02",
    workspaceId: "ws_demo",
    name: "医院信息集成平台改造 · 商务册",
    industry: "医疗信息化",
    currentStep: 1,
    updatedAt: "2026-07-07T14:30:00+08:00",
    linkedTechnicalProjectId: "proj_03",
  },
  {
    id: "bb_03",
    workspaceId: "ws_demo",
    name: "新建数据中心运维服务 · 商务标草稿",
    industry: "IDC / 运维",
    currentStep: 1,
    updatedAt: "2026-07-04T15:00:00+08:00",
  },
];

export const mockParseMarkdown = `# 商务与资格要求（解析预览）

## 一、投标人资格
1. 具备独立法人资格，营业执照在有效期内。
2. 近三年（2023—2025）完成不少于 2 个同类智慧交通或政务信息化项目。
3. 项目负责人具备高级工程师职称或 PMP 证书。
4. ★ 须提供近 6 个月社保缴纳证明（不少于 15 人）。

## 二、商务条款
- 付款方式：合同签订后 30% 预付，初验 40%，终验 25%，质保金 5%。
- 投标有效期：90 日历天。
- 履约保证金：中标价 5%，可用银行保函。
- 交货 / 实施周期：180 日历天。

## 三、报价与偏离
- 总价报价，含税（增值税 6% 或按适用税率）。
- 允许负偏离须在偏离表中逐条说明。
`;

export const mockQualifyItems: QualifyItem[] = [
  {
    id: "q1",
    requirement: "独立法人资格，营业执照有效",
    response: "我司为依法设立的有限责任公司，营业执照统一社会信用代码见附件 1，在有效期内。",
    evidence: "附件1-营业执照复印件.pdf",
    status: "matched",
  },
  {
    id: "q2",
    requirement: "近三年不少于 2 个同类业绩",
    response: "提供业绩：① 市交通局信号管控平台（2024）；② 区级智慧停车一期（2023）。合同与验收证明见附件 3。",
    evidence: "附件3-业绩合同与验收.zip",
    status: "matched",
  },
  {
    id: "q3",
    requirement: "项目负责人高级职称或 PMP",
    response: "拟派项目经理张某某，高级工程师（电子信息），证书编号待核验扫描件。",
    evidence: "附件4-项目经理证书.pdf",
    status: "partial",
  },
  {
    id: "q4",
    requirement: "★ 近 6 个月社保缴纳证明（≥15 人）",
    response: "拟附社保局盖章清单，当前扫描件人数 12 人，需补齐至 15 人。",
    evidence: "（待补）社保清单",
    status: "missing",
  },
];

export const mockTocItems: TocItem[] = [
  {
    id: "t1",
    title: "投标函及投标函附录",
    category: "法定文件",
    status: "required",
    checked: true,
  },
  {
    id: "t2",
    title: "法定代表人身份证明 / 授权委托书",
    category: "法定文件",
    status: "required",
    checked: true,
  },
  {
    id: "t3",
    title: "营业执照、资质证书复印件",
    category: "资格证明",
    status: "required",
    checked: true,
  },
  {
    id: "t4",
    title: "业绩合同与验收证明",
    category: "资格证明",
    status: "required",
    checked: false,
    note: "需两份完整扫描",
  },
  {
    id: "t5",
    title: "社保缴纳证明（★）",
    category: "资格证明",
    status: "required",
    checked: false,
  },
  {
    id: "t6",
    title: "报价一览表与分项报价表",
    category: "报价",
    status: "required",
    checked: false,
  },
  {
    id: "t7",
    title: "商务条款偏离表",
    category: "报价",
    status: "required",
    checked: false,
  },
  {
    id: "t8",
    title: "诚信承诺书 / 保密承诺",
    category: "承诺",
    status: "required",
    checked: true,
  },
  {
    id: "t9",
    title: "联合体协议（如有）",
    category: "可选",
    status: "optional",
    checked: false,
  },
];

export const mockQuoteRows: QuoteRow[] = [
  {
    id: "qr1",
    name: "平台软件开发与集成",
    unit: "项",
    quantity: "1",
    unitPrice: "1,280,000",
    amount: "1,280,000",
    remark: "含部署与联调",
  },
  {
    id: "qr2",
    name: "视频接入中间件授权",
    unit: "套",
    quantity: "1",
    unitPrice: "360,000",
    amount: "360,000",
    remark: "2000 路授权",
  },
  {
    id: "qr3",
    name: "实施与培训",
    unit: "人天",
    quantity: "120",
    unitPrice: "1,800",
    amount: "216,000",
    remark: "现场实施",
  },
  {
    id: "qr4",
    name: "三年维保",
    unit: "年",
    quantity: "3",
    unitPrice: "80,000",
    amount: "240,000",
    remark: "质保期外",
  },
];

export const mockCommitBlocks: CommitBlock[] = [
  {
    id: "c1",
    title: "法定代表人授权委托书",
    body: `本人（姓名）系（投标人名称）的法定代表人，现授权（被授权人姓名）以我方名义参加（项目名称）投标活动，全权处理投标文件签署、澄清、谈判等事宜。

授权期限：自签署之日起至投标有效期结束。`,
    needsStamp: true,
  },
  {
    id: "c2",
    title: "诚信投标承诺书",
    body: `我方承诺：所提交资质、业绩、人员证明真实有效；不串通投标、不弄虚作假；中标后按招标文件与投标文件履约。如有违反，愿承担相应法律责任及招标人损失。`,
    needsStamp: true,
  },
  {
    id: "c3",
    title: "商务条款响应说明",
    body: `我方完全响应招标文件关于付款方式、投标有效期、履约保证金及实施周期等商务条款；无负偏离。若存在文字表述差异，以招标文件为准。`,
    needsStamp: false,
  },
];

/** 按项目构造工作区初始状态 */
export function createInitialWorkspace(
  projectId: string,
): BusinessBidWorkspaceState {
  return {
    projectId,
    parseMarkdown: mockParseMarkdown,
    qualifyItems: mockQualifyItems.map((x) => ({ ...x })),
    tocItems: mockTocItems.map((x) => ({ ...x })),
    quoteRows: mockQuoteRows.map((x) => ({ ...x })),
    quoteNotes:
      "总价含税；付款节点与招标一致。偏离表如无负偏离可填「无」。",
    commitBlocks: mockCommitBlocks.map((x) => ({ ...x })),
  };
}
