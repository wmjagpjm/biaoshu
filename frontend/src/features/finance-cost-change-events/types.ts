/**
 * 模块：P10J 财务个人成本变更记录类型
 * 用途：对齐 GET /api/finance/cost-change-events 白名单投影，仅含契约字段。
 * 对接：financeCostChangeEventsApi；useFinanceCostChangeEvents；FinanceCostChangeEventsPage。
 * 二次开发：禁止扩展 actor/workspace/项目/金额/备注/变更前后值/内部 action 或审计事件 ID。
 */

/** 用途：服务端映射后的动作枚举；仅 create/update/delete。 */
export type FinanceCostChangeAction = "create" | "update" | "delete";

/**
 * 用途：单条个人成本变更记录。
 * 注意：entryId 为不透明 fce_*，不得反查成本正文；occurredAt 仅作展示。
 */
export type FinanceCostChangeEventItem = {
  action: FinanceCostChangeAction;
  entryId: string;
  occurredAt: string;
};

/** 用途：GET /finance/cost-change-events 成功响应体。 */
export type FinanceCostChangeEventsResponse = {
  items: FinanceCostChangeEventItem[];
};
