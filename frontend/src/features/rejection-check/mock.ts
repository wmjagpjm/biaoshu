/**
 * 模块：废标检查 mock
 */

import type { RejectionItem } from "./types";

export const mockRejectionItems: RejectionItem[] = [
  {
    id: "r1",
    level: "high",
    title: "目录未完全对齐招标文件一级章节",
    tenderClause:
      "投标文件技术部分一级目录须包含：项目理解、总体架构、功能设计、实施保障、运维培训，顺序不得擅自合并。",
    currentStatus:
      "当前大纲将「实施保障」合并进第四章「实施方案与进度计划」，未见独立一级「实施保障」。",
    suggestion: "在大纲编辑步将实施保障拆为一级，或按招标规定重命名对齐。",
    relatedLabel: "大纲编辑",
    relatedTo: "/technical-plan/proj_01/outline",
  },
  {
    id: "r2",
    level: "medium",
    title: "★号条款响应不完整",
    tenderClause:
      "★ 关键组件须提供信创适配证明，并在投标文件中设独立说明或附件索引。",
    currentStatus:
      "正文「安全与等保方案」章节尚未生成；全局事实含信创约束，但无独立小节标题与附件编号。",
    suggestion: "增加「信创适配」小节，并在附件清单中索引证明材料。",
    relatedLabel: "正文生成",
    relatedTo: "/technical-plan/proj_01/content",
  },
  {
    id: "r3",
    level: "low",
    title: "售后响应时间表述不一致",
    tenderClause: "（非硬性废标，但影响评分一致性）售后现场响应时间以投标承诺为准。",
    currentStatus:
      "全局事实为「主城区 4 小时」；第五章草稿出现「工作日 8 小时内」。",
    suggestion: "统一为 4 小时，并全篇检索替换冲突表述。",
    relatedLabel: "全局事实",
    relatedTo: "/technical-plan/proj_01/facts",
  },
  {
    id: "r4",
    level: "high",
    title: "★ 社保缴纳人数证明不足",
    tenderClause:
      "★ 须提供近 6 个月社保缴纳证明，参保人数不少于 15 人。",
    currentStatus:
      "商务标资格响应中该条状态为「缺材料」，清单人数不足 15 人。",
    suggestion: "补齐社保扫描件并更新商务标资格响应与附件清单。",
    relatedLabel: "商务标·资格",
    relatedTo: "/business-bid/bb_01/qualify",
  },
  {
    id: "r5",
    level: "medium",
    title: "投标有效期表述缺失",
    tenderClause: "投标有效期不少于 90 日历天。",
    currentStatus: "商务标解析结果已识别 90 天，授权承诺正文未写明有效期。",
    suggestion: "在投标函/承诺中明确「自递交之日起 90 日历天」。",
    relatedLabel: "商务标·承诺",
    relatedTo: "/business-bid/bb_01/commit",
  },
];
