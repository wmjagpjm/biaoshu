/**
 * 创建页功能目录
 * 用途：对齐喜鹊 /create 左侧「方案生成类 / 资料辅助类」信息架构。
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
  /** 主区亮点 */
  highlights: string[];
  /** 上传区文案 */
  uploadTitle: string;
  uploadDesc: string;
  fileTypes: string;
  /** 可选：跳到其它路由而非上传流 */
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
        title: "技术方案生成",
        description:
          "根据招标文件生成完整技术方案，支持长文档智能解析，自动匹配评分点并规划大纲与正文。",
        tags: ["全行业通用", "图文并茂"],
        color: "purple",
        highlights: [
          "智能解析评分标准",
          "支持上千页长文档",
          "自动生成图文并茂排版",
        ],
        uploadTitle: "点击上传招标文件",
        uploadDesc: "拖拽文件到此处，或点击选择。解析后进入大纲与正文工作流。",
        fileTypes: "PDF / Word / 图片扫描件",
        cta: "开始生成技术方案",
      },
      {
        id: "business-bid",
        title: "完整投标文件",
        description: "覆盖商务标与技术方案的一体化生成入口，适合需要整套投标文件的场景。",
        tags: ["商务标", "技术方案"],
        color: "indigo",
        badge: "new",
        badgeText: "NEW",
        highlights: ["商务+技术一体", "资料清单联动", "导出 Word"],
        uploadTitle: "上传招标文件，生成完整投标文件",
        uploadDesc: "系统将按评分与资格要求组织商务与技术响应结构。",
        fileTypes: "PDF / Word",
        cta: "开始生成完整标书",
      },
      {
        id: "engineering",
        title: "施工标专项",
        description: "面向施工组织设计场景，强调工艺、进度与附表类内容组织。",
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
        description: "只生成或扩写某一个章节，适合补强弱项或局部返工。",
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
        description: "一键整理投标所需商务资料清单，减少漏交废标。",
        tags: ["一键整理所需资料"],
        color: "violet",
        highlights: ["资格文件清单", "盖章材料", "递交检查"],
        uploadTitle: "上传招标文件，整理资料清单",
        uploadDesc: "解析资格条件与递交要求，输出勾选式清单。",
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
