/**
 * 模块：标讯 mock 列表
 */

import type { BidOpportunity } from "./types";

export const mockBidOpportunities: BidOpportunity[] = [
  {
    id: "opp_1",
    title: "某市智慧交通综合管理平台软件采购",
    buyer: "某市公安局交警支队",
    region: "华东",
    budgetLabel: "约 680 万",
    deadline: "2026-07-28",
    status: "closing_soon",
    tags: ["智慧交通", "软件", "信创"],
    summary:
      "建设信号优化、视频汇聚与指挥调度一体化平台，要求等保三级与国产化适配，实施周期 180 天。",
    sourceLabel: "演示数据源 · 省级公共资源",
  },
  {
    id: "opp_2",
    title: "医院信息集成平台改造项目",
    buyer: "某三甲医院",
    region: "华北",
    budgetLabel: "约 420 万",
    deadline: "2026-08-15",
    status: "open",
    tags: ["医疗", "集成", "HIS"],
    summary:
      "打通 HIS/LIS/PACS 数据互通，建设统一集成平台与患者主索引，含实施与三年维保。",
    sourceLabel: "演示数据源 · 卫健委招标网",
  },
  {
    id: "opp_3",
    title: "园区能耗监测系统采购",
    buyer: "某高新区管委会",
    region: "华南",
    budgetLabel: "约 210 万",
    deadline: "2026-07-20",
    status: "closing_soon",
    tags: ["能源", "物联网", "双碳"],
    summary:
      "园区级电水气热监测与能耗分析大屏，边缘采集 + 云端分析，支持报表与告警。",
    sourceLabel: "演示数据源 · 市政采购",
  },
  {
    id: "opp_4",
    title: "数据中心基础设施运维服务（三年）",
    buyer: "某省政务云中心",
    region: "西南",
    budgetLabel: "约 950 万",
    deadline: "2026-09-01",
    status: "open",
    tags: ["IDC", "运维", "SLA"],
    summary:
      "机房值守、巡检、备件与应急响应，要求 7×24 与等保合规运维文档交付。",
    sourceLabel: "演示数据源 · 省级采购中心",
  },
  {
    id: "opp_5",
    title: "教育城域网安全防护升级",
    buyer: "某市教育局",
    region: "华东",
    budgetLabel: "约 180 万",
    deadline: "2026-06-30",
    status: "closed",
    tags: ["教育", "安全", "等保"],
    summary: "城域网边界防护、态势感知与等保整改咨询（已截止，仅作归档演示）。",
    sourceLabel: "演示数据源 · 教育装备网",
  },
  {
    id: "opp_6",
    title: "智慧园区一卡通与门禁改造",
    buyer: "某产业园运营公司",
    region: "华北",
    budgetLabel: "约 150 万",
    deadline: "2026-08-05",
    status: "open",
    tags: ["园区", "一卡通", "门禁"],
    summary: "替换旧门禁与消费系统，统一身份中台，开放 API 对接物业与考勤。",
    sourceLabel: "演示数据源 · 企业采购",
  },
];

export const bidRegions = ["全部", "华东", "华北", "华南", "西南", "其他"];
