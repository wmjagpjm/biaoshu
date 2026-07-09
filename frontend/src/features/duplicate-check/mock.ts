/**
 * 模块：查重 mock 命中
 * 用途：对照视图演示数据。
 */

import type { DupHit } from "./types";

export const mockDupHits: DupHit[] = [
  {
    id: "d1",
    chapter: "2.1 逻辑架构",
    chapterId: "c2-1",
    similarity: 0.86,
    currentText:
      "系统采用分层解耦设计，接入层、业务层与数据层通过标准接口交互；网关统一鉴权与限流，业务服务无状态水平扩展，数据层支持主从与读写分离。",
    sourceText:
      "平台整体采用分层解耦，接入层、业务层与数据层经标准接口互通。统一 API 网关负责鉴权限流，业务微服务无状态扩展，数据库主从与读写分离。",
    sourceLabel: "知识库 · 微服务高可用部署白皮书",
    suggestion: "改写层次命名与项目专用组件（如视频接入网关），避免与白皮书句式同构。",
  },
  {
    id: "d2",
    chapter: "4.2 风险与质量控制",
    chapterId: "c4-2",
    similarity: 0.72,
    currentText:
      "建立周例会与里程碑评审机制，重大风险 24 小时内升级；质量门禁覆盖代码评审、联调验收与初验清单。",
    sourceText:
      "项目实行周例会与里程碑评审，重大风险须在 24 小时内上报升级，质量门禁包括评审、联调与验收检查表。",
    sourceLabel: "历史项目 · 园区能耗监测系统",
    suggestion: "补充本项目特有风险（路口过饱和、信创替换窗口），弱化通用管理套话。",
  },
  {
    id: "d3",
    chapter: "5.1 运维体系",
    chapterId: "c5-1",
    similarity: 0.64,
    currentText:
      "提供 7×24 热线与远程支持，主城区 4 小时现场响应；质保期 3 年，含季度巡检与年度演练。",
    sourceText:
      "运维承诺 7×24 服务热线及远程支持，主城 4 小时抵达现场；质保三年，按季度巡检并组织年度应急演练。",
    sourceLabel: "知识库 · 运维 SLA 与培训大纲",
    suggestion: "与全局事实对齐后改写语序，并写明本项目值班编制与备件库位置。",
  },
  {
    id: "d4",
    chapter: "1.2 总体技术路线",
    chapterId: "c1-2",
    similarity: 0.58,
    currentText:
      "采用云边协同架构，边缘侧完成视频结构化，中心侧承担研判与调度，关键组件支持信创环境部署。",
    sourceText:
      "方案采用云边协同，边缘完成结构化处理，中心负责分析研判与指挥调度，核心件兼容信创。",
    sourceLabel: "知识库 · 智慧交通同类业绩汇编",
    suggestion: "点明本项目 2000 路接入与现有指挥平台对接接口，降低泛化表述。",
  },
  {
    id: "d5",
    chapter: "3.1 感知接入",
    chapterId: "c3-1",
    similarity: 0.91,
    currentText:
      "视频流经流媒体网关接入，支持国标与私有协议转换，统一进入消息总线供下游订阅。",
    sourceText:
      "视频流经流媒体网关接入，支持国标与私有协议转换，统一进入消息总线供下游订阅。",
    sourceLabel: "本文内部 · 与 2.1 节表述高度重合",
    suggestion: "合并重复段或交叉引用，避免同文档复制粘贴。",
  },
];
