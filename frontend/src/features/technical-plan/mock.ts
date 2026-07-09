/**
 * 模块：技术方案 mock 数据
 * 用途：前端六步工作区演示；后端就绪后删除本文件。
 */

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
        description: "痛点、范围与成功标准",
        children: [
          {
            id: "c1-1-1",
            title: "业务痛点与建设范围",
            level: 3,
            targetWords: 800,
          },
          {
            id: "c1-1-2",
            title: "建设目标与成功标准",
            level: 3,
            targetWords: 700,
          },
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
      {
        id: "c2-2",
        title: "部署架构与高可用",
        level: 2,
        targetWords: 2200,
      },
      { id: "c2-3", title: "安全与等保方案", level: 2, targetWords: 2000 },
    ],
  },
  {
    id: "c3",
    title: "功能模块设计",
    level: 1,
    children: [
      {
        id: "c3-1",
        title: "感知接入与数据治理",
        level: 2,
        targetWords: 2500,
      },
      {
        id: "c3-2",
        title: "信号优化与运行监测",
        level: 2,
        targetWords: 2500,
      },
      {
        id: "c3-3",
        title: "指挥调度与可视化",
        level: 2,
        targetWords: 2200,
      },
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

function bodyOf(title: string, paragraphs: string[]): { body: string; preview: string; wordCount: number } {
  const body = [`# ${title}`, "", ...paragraphs].join("\n");
  const preview = paragraphs[0]?.slice(0, 80) ?? "";
  const wordCount = body.replace(/\s/g, "").length;
  return { body, preview, wordCount };
}

const c11 = bodyOf("招标需求解读", [
  "本项目面向城市交通治理数字化升级，核心在于打通感知、分析与指挥闭环，解决路口过饱和、违法取证分散、指挥调度依赖经验等问题。",
  "",
  "## 建设范围",
  "- 主干路网视频与信号机接入",
  "- 运行监测与研判分析",
  "- 指挥调度与可视化一张图",
  "",
  "## 成功标准",
  "按招标文件完成等保三级与信创适配验收，视频接入规模不低于 2000 路，关键接口与现有指挥平台联调通过。",
]);

const c12 = bodyOf("总体技术路线", [
  "采用云边协同架构：边缘侧完成视频结构化与协议适配，中心侧承担研判、调度与持久化。",
  "",
  "技术选型遵循「开放、可演进、信创优先」原则，业务服务微服务化，数据面通过消息总线与数据中台对接，展示层支持 PC 与大屏双端。",
]);

const c21 = bodyOf("逻辑架构", [
  "逻辑上划分为接入层、能力层、业务层与展示层，层间通过 API 网关与统一鉴权解耦。",
  "",
  "| 层次 | 职责 |",
  "| --- | --- |",
  "| 接入层 | 视频、信号、第三方系统接入 |",
  "| 能力层 | 识别、研判、检索、消息 |",
  "| 业务层 | 监测、优化、调度、运维 |",
  "| 展示层 | 工作台、一张图、报表 |",
  "",
  "跨层调用统一经过网关，审计与限流策略在边缘统一实施。",
]);

export const mockChapters: ChapterContent[] = [
  {
    id: "c1-1",
    title: "招标需求解读",
    status: "done",
    ...c11,
  },
  {
    id: "c1-2",
    title: "总体技术路线",
    status: "done",
    ...c12,
  },
  {
    id: "c2-1",
    title: "逻辑架构",
    status: "done",
    ...c21,
  },
  {
    id: "c2-2",
    title: "部署架构与高可用",
    wordCount: 120,
    status: "generating",
    preview: "正在生成：双机房部署、容器编排与故障切换策略……",
    body: `# 部署架构与高可用

> 生成中草稿

拟采用双可用区部署，核心服务多副本，数据库主从 + 定时备份……`,
  },
  {
    id: "c2-3",
    title: "安全与等保方案",
    wordCount: 0,
    status: "pending",
    preview: "等待生成",
    body: "",
  },
  {
    id: "c3-1",
    title: "感知接入与数据治理",
    wordCount: 0,
    status: "pending",
    preview: "等待生成",
    body: "",
  },
];
