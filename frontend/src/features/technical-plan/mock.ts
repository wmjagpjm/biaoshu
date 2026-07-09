import type { ChapterContent, GlobalFact, OutlineNode } from "./types";

export const mockAnalysis = {
  projectName: "某市智慧交通综合管理平台",
  overview:
    "建设覆盖城市主干路网的智慧交通综合管理平台，实现信号优化、违法抓拍汇聚、运行监测与指挥调度一体化。要求国产化适配、等保三级，并与现有交警业务系统对接。",
  scoringPoints: [
    { name: "总体架构与技术路线", weight: "20%" },
    { name: "功能模块完整性", weight: "25%" },
    { name: "实施与运维保障", weight: "15%" },
    { name: "业绩与团队", weight: "15%" },
    { name: "售后与培训", weight: "10%" },
    { name: "报价合理性", weight: "15%" },
  ],
  techRequirements: [
    "支持视频流接入不少于 2000 路，可横向扩展",
    "提供开放 API 与消息总线对接现有指挥平台",
    "关键组件支持信创环境部署",
    "提供完整的权限、审计与备份恢复方案",
  ],
  rejectionRisks: [
    "未按招标文件规定目录编制",
    "未响应★号关键条款",
    "业绩证明材料不齐",
  ],
};

export const mockOutline: OutlineNode[] = [
  {
    id: "c1",
    title: "项目理解与建设目标",
    level: 1,
    children: [
      {
        id: "c1-1",
        title: "招标需求解读",
        level: 2,
        targetWords: 1500,
        children: [
          { id: "c1-1-1", title: "业务痛点与建设范围", level: 3, targetWords: 800 },
          { id: "c1-1-2", title: "建设目标与成功标准", level: 3, targetWords: 700 },
        ],
      },
      {
        id: "c1-2",
        title: "总体技术路线",
        level: 2,
        targetWords: 1800,
      },
    ],
  },
  {
    id: "c2",
    title: "系统总体架构设计",
    level: 1,
    children: [
      { id: "c2-1", title: "逻辑架构", level: 2, targetWords: 2000 },
      { id: "c2-2", title: "部署架构与高可用", level: 2, targetWords: 2200 },
      { id: "c2-3", title: "安全与等保方案", level: 2, targetWords: 2000 },
    ],
  },
  {
    id: "c3",
    title: "功能模块设计",
    level: 1,
    children: [
      { id: "c3-1", title: "感知接入与数据治理", level: 2, targetWords: 2500 },
      { id: "c3-2", title: "信号优化与运行监测", level: 2, targetWords: 2500 },
      { id: "c3-3", title: "指挥调度与可视化", level: 2, targetWords: 2200 },
    ],
  },
  {
    id: "c4",
    title: "实施方案与进度计划",
    level: 1,
    children: [
      { id: "c4-1", title: "实施阶段划分", level: 2, targetWords: 1600 },
      { id: "c4-2", title: "风险与质量控制", level: 2, targetWords: 1400 },
    ],
  },
  {
    id: "c5",
    title: "运维保障与培训",
    level: 1,
    children: [
      { id: "c5-1", title: "运维体系", level: 2, targetWords: 1500 },
      { id: "c5-2", title: "培训计划", level: 2, targetWords: 1200 },
    ],
  },
];

export const mockFacts: GlobalFact[] = [
  {
    id: "f1",
    category: "项目周期",
    content: "合同签订后 180 日历天内完成建设并通过初验。",
    source: "tender",
  },
  {
    id: "f2",
    category: "技术约束",
    content: "数据库与中间件需支持国产化替代，提供信创适配证明。",
    source: "tender",
  },
  {
    id: "f3",
    category: "接入规模",
    content: "视频接入规模按 2000 路设计，预留扩展至 5000 路。",
    source: "tender",
  },
  {
    id: "f4",
    category: "服务承诺",
    content: "质保期 3 年，7×24 响应，现场 4 小时到达（主城区）。",
    source: "manual",
  },
  {
    id: "f5",
    category: "知识库引用",
    content: "同类项目采用微服务 + 数据中台双总线架构，已在三地落地。",
    source: "knowledge",
  },
];

export const mockChapters: ChapterContent[] = [
  {
    id: "c1-1",
    title: "招标需求解读",
    wordCount: 1620,
    status: "done",
    preview:
      "本项目面向城市交通治理数字化升级，核心在于打通感知、分析与指挥闭环……",
  },
  {
    id: "c1-2",
    title: "总体技术路线",
    wordCount: 1880,
    status: "done",
    preview: "采用云边协同架构，边缘侧完成视频结构化，中心侧承担研判与调度……",
  },
  {
    id: "c2-1",
    title: "逻辑架构",
    wordCount: 2105,
    status: "done",
    preview: "逻辑上划分为接入层、能力层、业务层与展示层，层间通过 API 网关解耦……",
  },
  {
    id: "c2-2",
    title: "部署架构与高可用",
    wordCount: 980,
    status: "generating",
    preview: "正在生成：双机房部署、容器编排与故障切换策略……",
  },
  {
    id: "c2-3",
    title: "安全与等保方案",
    wordCount: 0,
    status: "pending",
    preview: "等待生成",
  },
  {
    id: "c3-1",
    title: "感知接入与数据治理",
    wordCount: 0,
    status: "pending",
    preview: "等待生成",
  },
];
