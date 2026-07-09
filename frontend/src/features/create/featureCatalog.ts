/**
 * 创建页功能目录（仅「开工 / 生成」能力）
 * 查重、废标、模板、本地解析等日常工具只在全局侧栏，避免入口重复。
 *
 * 概念区分：
 * - 技术标：技术方案主流程
 * - 商务标：资格、报价、授权等
 * - 完整投标：两册统一上下文
 * - 商务资料清单：只整理要交什么，不写正文
 */

export type FeatureColor =
  | "purple"
  | "blue"
  | "indigo"
  | "rose"
  | "orange"
  | "emerald"
  | "violet";

export type CreateFeature = {
  id: string;
  title: string;
  description: string;
  tags: string[];
  color: FeatureColor;
  highlights: string[];
  uploadTitle: string;
  uploadDesc: string;
  fileTypes: string;
  routeTo?: string;
  cta: string;
};

export type FeatureGroup = {
  title: string;
  features: CreateFeature[];
};

export const featureGroups: FeatureGroup[] = [
  {
    title: "方案生成类",
    features: [
      {
        id: "core",
        title: "技术标生成",
        description:
          "上传招标文件后，按评分点梳理、目录、正文到导出，完成技术方案全流程。",
        tags: ["技术标", "全流程"],
        color: "purple",
        highlights: [
          "评分点与招标要求对齐",
          "目录与正文分步可改",
          "支持长文档结构",
        ],
        uploadTitle: "拖拽或点击上传招标文件",
        uploadDesc: "解析完成后进入六步工作区，可随时返回修改。",
        fileTypes: "PDF / Word / 扫描件",
        cta: "开始生成技术标",
      },
      {
        id: "business",
        title: "商务标生成",
        description:
          "独立编写商务标：资格证明编排、商务响应、报价说明、授权与诚信承诺等，不强制同时写技术正文。",
        tags: ["资格文件", "报价说明", "商务响应"],
        color: "indigo",
        highlights: [
          "资格条件逐条响应",
          "商务目录与附件清单",
          "与技术标可分可合",
        ],
        uploadTitle: "上传招标文件，生成商务标",
        uploadDesc: "系统提取资格条件、递交要求与商务评分点，组织商务标目录与正文草稿。",
        fileTypes: "PDF / Word",
        cta: "开始生成商务标",
      },
      {
        id: "full-bid",
        title: "完整投标文件",
        description:
          "商务标与技术标一并规划，统一项目上下文后分册深化。",
        tags: ["商务标", "技术标", "整套交付"],
        color: "violet",
        highlights: ["统一项目上下文", "商务与技术分册", "统一导出"],
        uploadTitle: "上传招标文件，编制完整投标文件",
        uploadDesc: "同时规划商务标与技术标结构，后续可分别进入两册工作区。",
        fileTypes: "PDF / Word",
        cta: "开始编制完整标书",
      },
      {
        id: "engineering",
        title: "施工标专项",
        description: "面向施工组织设计，侧重工艺、进度与附表类内容组织。",
        tags: ["施工附表", "进度横道"],
        color: "blue",
        highlights: ["施工工艺响应", "进度与附表", "工程量关联"],
        uploadTitle: "上传施工类招标文件",
        uploadDesc: "可附加工程量清单、图纸说明等辅助资料。",
        fileTypes: "PDF / Word / Excel",
        cta: "开始编制施工标",
      },
      {
        id: "yibiaoxiebiao",
        title: "以标写标",
        description: "基于历史中标或参考方案复用，针对新项目替换与改写。",
        tags: ["历史方案", "差异改写"],
        color: "rose",
        highlights: ["引用知识库历史方案", "差异化改写", "控制重复表述"],
        uploadTitle: "上传新项目招标文件",
        uploadDesc: "建议同时在知识库中准备历史参考方案。",
        fileTypes: "PDF / Word",
        cta: "开始以标写标",
      },
      {
        id: "single-chapter",
        title: "单章节专项",
        description: "只编写或扩写某一章节，适合补强弱项或局部返工。",
        tags: ["单章", "局部返工"],
        color: "orange",
        highlights: ["指定章节", "字数可控", "便于返工"],
        uploadTitle: "上传招标文件或粘贴章节要求",
        uploadDesc: "也可先进入已有项目，对单章继续编写。",
        fileTypes: "PDF / Word / 文本",
        cta: "编写单章节",
      },
    ],
  },
  {
    title: "资料辅助类",
    features: [
      {
        id: "framework",
        title: "投标文件框架提取",
        description: "从招标文件提取大纲框架，便于人工确认后再写正文。",
        tags: ["大纲框架", "目录结构"],
        color: "emerald",
        highlights: ["一级目录对齐", "评分项映射", "可编辑大纲"],
        uploadTitle: "上传招标文件，提取框架",
        uploadDesc: "仅整理目录结构，不直接写正文。",
        fileTypes: "PDF / Word",
        cta: "提取标书框架",
      },
      {
        id: "business-list",
        title: "商务资料清单整理",
        description:
          "只整理要交哪些商务材料，不做商务标正文。清单侧重勾选，生成侧重写内容。",
        tags: ["资料清单", "递交检查"],
        color: "violet",
        highlights: ["资格文件清单", "盖章材料", "递交检查"],
        uploadTitle: "上传招标文件，整理资料清单",
        uploadDesc: "解析资格条件与递交要求，输出勾选式清单。",
        fileTypes: "PDF / Word",
        cta: "整理资料清单",
      },
    ],
  },
];

export function findFeature(id: string): CreateFeature {
  for (const g of featureGroups) {
    const f = g.features.find((x) => x.id === id);
    if (f) return f;
  }
  return featureGroups[0].features[0];
}
