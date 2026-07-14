/**
 * 模块：P10I 人员资质到期提示类型
 * 用途：对齐 GET /api/hr/credential-expiry 白名单投影，仅含契约字段。
 * 对接：hrCredentialExpiryApi；useHrCredentialExpiry；HrCredentialExpiryPage。
 * 二次开发：禁止扩展 remark/证件号/附件/workspace/创建人/路径；attention state 不含 valid。
 */

/** 用途：资质类别（与 P10D/后端枚举一致，仅展示标签映射）。 */
export type HrCredentialCategory =
  | "professional"
  | "safety"
  | "performance"
  | "other";

/**
 * 用途：关注项状态；仅 expired/expiring_soon/missing_expiry。
 * 注意：valid 只在服务端计数，不得出现在 attentionItems。
 */
export type HrCredentialExpiryAttentionState =
  | "expired"
  | "expiring_soon"
  | "missing_expiry";

/** 用途：关注列表单项（不含 remark/时间戳/工作空间）。 */
export type HrCredentialExpiryAttentionItem = {
  cardId: string;
  personName: string;
  category: HrCredentialCategory;
  credentialName: string;
  level: string;
  validUntil: string | null;
  state: HrCredentialExpiryAttentionState;
  daysRemaining: number | null;
};

/** 用途：GET /hr/credential-expiry 成功响应体。 */
export type HrCredentialExpirySummary = {
  asOfDate: string;
  windowDays: number;
  activeTotalCount: number;
  expiredCount: number;
  expiringSoonCount: number;
  validCount: number;
  missingExpiryCount: number;
  inactiveExcludedCount: number;
  attentionItems: HrCredentialExpiryAttentionItem[];
};
