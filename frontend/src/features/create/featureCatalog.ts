/**
 * 创建页功能目录
 * 用途：对齐喜鹊 /create 左侧信息架构，并补齐独立「商务标生成」入口。
 *
 * 概念区分：
 * - 技术标生成：只做技术响应（方案/实施/运维等）
 * - 商务标生成：资格、报价、承诺、授权等商务文件
 * - 完整投标文件：商务 + 技术一体化打包
 * - 商务资料清单：只整理要交哪些材料，不生成正文
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
  badge?: "new" | "free";
  badgeText?: string;
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
          "即技术标 / 技术方案。根据招标文件生成技术响应内容：解析评分点 → 大纲 → 正文 → 导出，对应 C 端「技术方案」主流程。",
        tags: ["技术标", "全行业通用", "图文并茂"],
        color: "purple",
        highlights: [
          "智能解析评分标准",
          "大纲与正文分步可编辑",
          "支持长文档与图文排版",
        ],
        uploadTitle: "上传招标文件，生成技术标",
        uploadDesc: "拖拽或点击选择文件。解析后进入技术标六步工作流（分析 → 大纲 → 事实 → 正文 → 导出）。",
        fileTypes: "PDF / Word / 图片扫描件",
        cta: "开始生成技术标",
      },
      {
        id: "business",
        title: "商务标生成",
        description:
          "独立生成商务标部分：资格证明编排、商务响应、报价说明、授权与诚信承诺等，不强制同时写技术正文。",
        tags: ["资格文件", "报价说明", "商务响应"],
        color: "indigo",
        badge: "new",
        badgeText: "NEW",
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
          "商务标 + 技术标一体化生成，一次出整套投标文件。适合「两册都要、统一项目上下文」的场景。",
        tags: ["商务标", "技术标", "整套交付"],
        color: "violet",
        highlights: ["一套项目上下文", "商务与技术同步规划", "统一导出"],
        uploadTitle: "上传招标文件，生成完整投标文件",
        uploadDesc: "将同时规划商务标与技术标结构，后续可分别进入两册工作区深化。",
        fileTypes: "PDF / Word",
        cta: "开始生成完整标书",
      },
      {
        id: "engineering",
        title: "施工标专项",
        description: "面向施工组织设计场景，强调工艺、进度与附表类内容组织（偏技术标细分）。",
        tags: ["带施工附表", "带横道图"],
        color: "blue",
        highlights: ["施工工艺响应", "进度与附表", "工程量关联"],
        uploadTitle: "上传施工类招标文件",
        uploadDesc: "可附加工程量清单、图纸说明等辅助资料（后续支持）。",
        fileTypes: "PDF / Word / Excel",
        cta: "开始生成施工标",
      },
      {
        id: "yibiaoxiebiao",
        title: "以标写标",
        description: "基于历史中标/参考方案精准复用，针对新项目做替换与改写。",
        tags: ["精准复用", "针对性替换"],
        color: "rose",
        highlights: ["引用知识库历史方案", "差异化改写", "降低重复风险"],
        uploadTitle: "上传新项目招标文件",
        uploadDesc: "建议同时在知识库中准备历史参考方案，以提升复用质量。",
        fileTypes: "PDF / Word",
        cta: "开始以标写标",
      },
      {
        id: "single-chapter",
        title: "单章节专项",
        description: "只生成或扩写某一个章节，适合补强弱项或局部返工（技术/商务章节均可）。",
        tags: ["生成单个章节", "灵活输入"],
        color: "orange",
        highlights: ["指定章节", "字数可控", "快速迭代"],
        uploadTitle: "上传招标文件或粘贴章节要求",
        uploadDesc: "也可先进入已有项目，对单章继续生成（后续对接）。",
        fileTypes: "PDF / Word / 文本",
        cta: "生成单章节",
      },
    ],
  },
  {
    title: "资料辅助类",
    features: [
      {
        id: "framework",
        title: "投标文件框架提取",
        description: "快速从招标文件提取完整大纲框架，便于人工确认后再扩写正文。",
        tags: ["快速生成完整标书大纲"],
        color: "emerald",
        badge: "free",
        badgeText: "限免",
        highlights: ["一级目录对齐", "评分项映射", "可编辑大纲"],
        uploadTitle: "上传招标文件，提取框架",
        uploadDesc: "仅生成目录结构，不消耗正文生成额度（策略可配置）。",
        fileTypes: "PDF / Word",
        cta: "提取标书框架",
      },
      {
        id: "business-list",
        title: "商务资料清单整理",
        description:
          "只整理「要交哪些商务材料」，不做商务标正文撰写。与「商务标生成」不同：清单=目录勾选，生成=写内容。",
        tags: ["一键整理所需资料"],
        color: "violet",
        highlights: ["资格文件清单", "盖章材料", "递交检查"],
        uploadTitle: "上传招标文件，整理资料清单",
        uploadDesc: "解析资格条件与递交要求，输出勾选式清单（非正文）。",
        fileTypes: "PDF / Word",
        cta: "整理资料清单",
      },
    ],
  },
  {
    title: "质检与工具",
    features: [
      {
        id: "duplicate",
        title: "标书查重",
        description: "检测与知识库、历史稿的重复表达，辅助改写降重。",
        tags: ["相似度", "段落定位"],
        color: "blue",
        highlights: ["章节级定位", "知识库对比", "改写建议占位"],
        uploadTitle: "选择项目或上传待查正文",
        uploadDesc: "也可从「我的项目」进入已有正文查重。",
        fileTypes: "Word / Markdown",
        routeTo: "/duplicate-check",
        cta: "前往查重",
      },
      {
        id: "rejection",
        title: "废标项检查",
        description: "对照硬性条款与★号要求，输出废标风险清单。",
        tags: ["形式评审", "★号条款"],
        color: "rose",
        highlights: ["风险分级", "条款对照", "修改建议"],
        uploadTitle: "上传招标与投标文件进行检查",
        uploadDesc: "前端阶段为演示交互，后端接入后跑真实规则。",
        fileTypes: "PDF / Word",
        routeTo: "/rejection-check",
        cta: "前往废标检查",
      },
      {
        id: "local-parser",
        title: "本地解析插件",
        description: "MinerU 本机解析，结果回传工作空间，服务器保持轻量。",
        tags: ["易用外壳", "自备算力"],
        color: "emerald",
        highlights: ["安装即用", "Token 绑定", "自动回传"],
        uploadTitle: "配置本地解析助手",
        uploadDesc: "无需在此上传；请下载助手后在本机解析。",
        fileTypes: "—",
        routeTo: "/local-parser",
        cta: "查看插件说明",
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
