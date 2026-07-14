/**
 * 模块：P10D 人员资质素材卡类型
 * 用途：对齐 GET/POST/PATCH /api/hr/credential-cards* 白名单投影，仅含契约字段。
 * 对接：hrCredentialApi；useHrCredentialCards；HrCredentialCardsPage。
 * 二次开发：禁止扩展证件号/手机/附件/URL/workspace/createdBy 等越界键。
 */

/** 用途：资质类别枚举（与后端一致）。 */
export type HrCredentialCategory =
  | "professional"
  | "safety"
  | "performance"
  | "other";

/** 用途：列表摘要（不含 remark）。 */
export type HrCredentialCardSummary = {
  id: string;
  personName: string;
  category: HrCredentialCategory;
  credentialName: string;
  level: string;
  validUntil: string | null;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
};

/** 用途：单卡详情（摘要 + remark）。 */
export type HrCredentialCardDetail = HrCredentialCardSummary & {
  remark: string;
};

/** 用途：列表接口响应包装。 */
export type HrCredentialCardListResponse = {
  items: HrCredentialCardSummary[];
};

/** 用途：新建人员资质卡写入体。 */
export type HrCredentialCardCreateBody = {
  personName: string;
  category: HrCredentialCategory;
  credentialName: string;
  level?: string;
  validUntil?: string | null;
  remark?: string;
  isActive?: boolean;
};

/** 用途：更新/启停（至少一个可改字段）。 */
export type HrCredentialCardUpdateBody = {
  personName?: string;
  category?: HrCredentialCategory;
  credentialName?: string;
  level?: string;
  validUntil?: string | null;
  remark?: string;
  isActive?: boolean;
};
