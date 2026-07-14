/**
 * 模块：P10D 人员资质素材卡页
 * 用途：严格 hr 下的列表摘要、点选详情、新建与编辑/启停；无删除/附件/导出。
 * 对接：useHrCredentialCards；仅 /hr/credential-cards*；错误固定中文脱敏。
 * 二次开发：禁止浏览器持久化卡片数据；列表不含 remark；写后须重读服务端。
 */

import { useEffect, useState } from "react";
import { IdCard, RefreshCw } from "lucide-react";
import { EmptyState } from "../../../shared/components/EmptyState/EmptyState";
import { LoadingBlock } from "../../../shared/components/LoadingBlock/LoadingBlock";
import {
  useHrCredentialCards,
  type HrCardFormInput,
} from "../hooks/useHrCredentialCards";
import type {
  HrCredentialCardDetail,
  HrCredentialCardSummary,
  HrCredentialCategory,
} from "../types";
import "./HrCredentialCardsPage.css";

/** 类别中文标签；不直接展示内部英文枚举。 */
const CATEGORY_LABELS: Record<HrCredentialCategory, string> = {
  professional: "专业资质",
  safety: "安全资质",
  performance: "业绩证明",
  other: "其他",
};

const EMPTY_FORM: HrCardFormInput = {
  personName: "",
  category: "professional",
  credentialName: "",
  level: "",
  validUntil: "",
  remark: "",
  isActive: true,
};

/** 用途：时间本地化；无效值显示「—」。 */
function formatDateTime(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** 用途：日期或占位。 */
function formatDate(value: string | null | undefined): string {
  if (value == null) return "—";
  const t = String(value).trim();
  return t ? t : "—";
}

/** 用途：文本单元格空值占位。 */
function textOrDash(value: string | null | undefined): string {
  if (value == null) return "—";
  const t = String(value).trim();
  return t ? t : "—";
}

/** 用途：类别码转中文；未知不回显英文内部码。 */
function categoryLabel(category: string): string {
  if (category in CATEGORY_LABELS) {
    return CATEGORY_LABELS[category as HrCredentialCategory];
  }
  return "—";
}

/** 用途：详情回填编辑表单（不落存储）。 */
function detailToForm(detail: HrCredentialCardDetail): HrCardFormInput {
  return {
    personName: detail.personName ?? "",
    category: detail.category,
    credentialName: detail.credentialName ?? "",
    level: detail.level ?? "",
    validUntil: detail.validUntil ?? "",
    remark: detail.remark ?? "",
    isActive: Boolean(detail.isActive),
  };
}

function CardFormFields({
  form,
  disabled,
  onChange,
  idPrefix,
}: {
  form: HrCardFormInput;
  disabled: boolean;
  onChange: (next: HrCardFormInput) => void;
  idPrefix: string;
}) {
  return (
    <div className="hc-form__fields">
      <label className="hc-form__field" htmlFor={`${idPrefix}-person`}>
        <span>人员姓名</span>
        <input
          id={`${idPrefix}-person`}
          data-testid={`${idPrefix}-person`}
          type="text"
          maxLength={80}
          value={form.personName}
          disabled={disabled}
          autoComplete="off"
          placeholder="协作显示名，勿填证件号"
          onChange={(e) => onChange({ ...form, personName: e.target.value })}
        />
      </label>
      <label className="hc-form__field" htmlFor={`${idPrefix}-category`}>
        <span>资质类别</span>
        <select
          id={`${idPrefix}-category`}
          data-testid={`${idPrefix}-category`}
          value={form.category}
          disabled={disabled}
          onChange={(e) =>
            onChange({
              ...form,
              category: e.target.value as HrCredentialCategory,
            })
          }
        >
          <option value="professional">专业资质</option>
          <option value="safety">安全资质</option>
          <option value="performance">业绩证明</option>
          <option value="other">其他</option>
        </select>
      </label>
      <label className="hc-form__field" htmlFor={`${idPrefix}-credential`}>
        <span>资质名称</span>
        <input
          id={`${idPrefix}-credential`}
          data-testid={`${idPrefix}-credential`}
          type="text"
          maxLength={120}
          value={form.credentialName}
          disabled={disabled}
          autoComplete="off"
          placeholder="证书或资质名称，勿填证件号码"
          onChange={(e) =>
            onChange({ ...form, credentialName: e.target.value })
          }
        />
      </label>
      <label className="hc-form__field" htmlFor={`${idPrefix}-level`}>
        <span>级别（可选）</span>
        <input
          id={`${idPrefix}-level`}
          data-testid={`${idPrefix}-level`}
          type="text"
          maxLength={80}
          value={form.level}
          disabled={disabled}
          autoComplete="off"
          placeholder="如 一级 / 中级"
          onChange={(e) => onChange({ ...form, level: e.target.value })}
        />
      </label>
      <label className="hc-form__field" htmlFor={`${idPrefix}-valid`}>
        <span>有效期至（可选）</span>
        <input
          id={`${idPrefix}-valid`}
          data-testid={`${idPrefix}-valid`}
          type="date"
          value={form.validUntil}
          disabled={disabled}
          onChange={(e) => onChange({ ...form, validUntil: e.target.value })}
        />
      </label>
      <label className="hc-form__check" htmlFor={`${idPrefix}-active`}>
        <input
          id={`${idPrefix}-active`}
          data-testid={`${idPrefix}-active`}
          type="checkbox"
          checked={form.isActive}
          disabled={disabled}
          onChange={(e) => onChange({ ...form, isActive: e.target.checked })}
        />
        <span>启用中</span>
      </label>
      <label
        className="hc-form__field hc-form__field--full"
        htmlFor={`${idPrefix}-remark`}
      >
        <span>备注（可选，最多 500 字）</span>
        <textarea
          id={`${idPrefix}-remark`}
          data-testid={`${idPrefix}-remark`}
          maxLength={500}
          value={form.remark}
          disabled={disabled}
          placeholder="勿填写证件号、手机号或住址"
          onChange={(e) => onChange({ ...form, remark: e.target.value })}
        />
      </label>
    </div>
  );
}

function ListItemButton({
  item,
  active,
  onSelect,
}: {
  item: HrCredentialCardSummary;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className={`hc-list__item${active ? " is-active" : ""}`}
      data-testid="hr-list-item"
      data-card-id={item.id}
      onClick={onSelect}
    >
      <span className="hc-list__name">{textOrDash(item.personName)}</span>
      <span className="hc-list__meta">
        <span>
          类别 <strong>{categoryLabel(item.category)}</strong>
        </span>
        <span>
          资质 <strong>{textOrDash(item.credentialName)}</strong>
        </span>
        <span>
          级别 <strong>{textOrDash(item.level)}</strong>
        </span>
        <span
          className={`hc-badge${item.isActive ? " hc-badge--on" : " hc-badge--off"}`}
          data-testid="hr-list-item-status"
        >
          {item.isActive ? "启用" : "停用"}
        </span>
        <span>更新 {formatDateTime(item.updatedAt)}</span>
      </span>
    </button>
  );
}

/**
 * 用途：P10D 人员资质素材卡主页面。
 */
export function HrCredentialCardsPage() {
  const {
    items,
    listLoading,
    listError,
    selectedId,
    detail,
    detailLoading,
    detailError,
    submitting,
    writeError,
    selectCard,
    clearWriteError,
    reloadList,
    createCard,
    updateCard,
    setCardActive,
  } = useHrCredentialCards();

  const [createForm, setCreateForm] = useState<HrCardFormInput>(EMPTY_FORM);
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState<HrCardFormInput>(EMPTY_FORM);
  const [showCreate, setShowCreate] = useState(false);

  // 切换选中时退出编辑态
  useEffect(() => {
    setEditing(false);
    setEditForm(EMPTY_FORM);
  }, [selectedId]);

  const startEdit = () => {
    if (!detail) return;
    clearWriteError();
    setEditForm(detailToForm(detail));
    setEditing(true);
  };

  const cancelEdit = () => {
    clearWriteError();
    setEditing(false);
    setEditForm(EMPTY_FORM);
  };

  const onCreate = async () => {
    const ok = await createCard(createForm);
    if (ok) {
      setCreateForm(EMPTY_FORM);
      setShowCreate(false);
    }
  };

  const onSaveEdit = async () => {
    if (!selectedId) return;
    const ok = await updateCard(selectedId, editForm);
    if (ok) {
      setEditing(false);
      setEditForm(EMPTY_FORM);
    }
  };

  const onToggleActive = async () => {
    if (!selectedId || !detail) return;
    clearWriteError();
    await setCardActive(selectedId, !detail.isActive);
  };

  return (
    <div className="hc-layout" data-testid="hr-credential-page">
      <header className="page-header">
        <div>
          <h1>人员资质</h1>
          <p>
            当前工作空间内的人员资质素材卡登记与查看。仅协作显示名与资质描述；不收集证件号码、手机、住址、照片或附件。
          </p>
        </div>
        <div className="hc-form__actions">
          <button
            type="button"
            className="btn btn-soft btn-sm"
            data-testid="hr-reload-list"
            disabled={listLoading || submitting}
            onClick={() => {
              clearWriteError();
              reloadList();
            }}
          >
            <RefreshCw size={14} />
            刷新列表
          </button>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            data-testid="hr-show-create"
            disabled={submitting}
            onClick={() => {
              clearWriteError();
              setShowCreate(true);
            }}
          >
            新建资质卡
          </button>
        </div>
      </header>

      {listError ? (
        <div className="hc-alert" role="alert" data-testid="hr-list-error">
          {listError}
        </div>
      ) : null}

      {writeError ? (
        <div className="hc-alert" role="alert" data-testid="hr-write-error">
          {writeError}
        </div>
      ) : null}

      <div className="hc-grid">
        <section className="hc-panel" aria-label="资质卡列表">
          <div className="hc-panel__head">
            <h2 className="hc-panel__title">资质卡列表</h2>
          </div>
          <p className="hc-panel__hint">
            列表仅为摘要，不含备注。点击条目后加载详情。
          </p>
          {listLoading ? (
            <LoadingBlock label="正在加载人员资质…" />
          ) : items.length === 0 ? (
            <EmptyState
              icon={<IdCard size={28} />}
              title="暂无人员资质卡"
              description="可点击「新建资质卡」登记本空间协作人员资质。"
            />
          ) : (
            <ul className="hc-list" data-testid="hr-list">
              {items.map((item) => (
                <li key={item.id}>
                  <ListItemButton
                    item={item}
                    active={selectedId === item.id}
                    onSelect={() => selectCard(item.id)}
                  />
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="hc-panel" aria-label="资质卡详情">
          <div className="hc-panel__head">
            <h2 className="hc-panel__title">详情与编辑</h2>
          </div>

          {showCreate ? (
            <div className="hc-form" data-testid="hr-create-form">
              <h3 className="hc-section-title">新建人员资质卡</h3>
              <CardFormFields
                form={createForm}
                disabled={submitting}
                onChange={setCreateForm}
                idPrefix="hr-create"
              />
              <div className="hc-form__actions">
                <button
                  type="button"
                  className="btn btn-soft"
                  data-testid="hr-create-cancel"
                  disabled={submitting}
                  onClick={() => {
                    clearWriteError();
                    setShowCreate(false);
                    setCreateForm(EMPTY_FORM);
                  }}
                >
                  取消
                </button>
                <button
                  type="button"
                  className="btn btn-primary"
                  data-testid="hr-create-submit"
                  disabled={submitting}
                  onClick={() => void onCreate()}
                >
                  {submitting ? "提交中…" : "创建"}
                </button>
              </div>
            </div>
          ) : null}

          {!selectedId ? (
            <div className="hc-placeholder" data-testid="hr-detail-empty">
              <strong>尚未选择资质卡</strong>
              <span>请从左侧列表选择一条，或新建后自动打开详情。</span>
            </div>
          ) : detailLoading ? (
            <LoadingBlock label="正在加载详情…" />
          ) : detailError ? (
            <div
              className="hc-alert"
              role="alert"
              data-testid="hr-detail-error"
            >
              {detailError}
            </div>
          ) : detail ? (
            <>
              {!editing ? (
                <div data-testid="hr-detail">
                  <div className="hc-detail-actions">
                    <button
                      type="button"
                      className="btn btn-soft btn-sm"
                      data-testid="hr-edit-btn"
                      disabled={submitting}
                      onClick={startEdit}
                    >
                      编辑
                    </button>
                    <button
                      type="button"
                      className="btn btn-soft btn-sm"
                      data-testid="hr-toggle-active"
                      disabled={submitting}
                      onClick={() => void onToggleActive()}
                    >
                      {detail.isActive ? "停用" : "启用"}
                    </button>
                  </div>
                  <div className="hc-detail__grid">
                    <div className="hc-detail__field">
                      <span className="hc-detail__label">人员姓名</span>
                      <span
                        className="hc-detail__value"
                        data-testid="hr-detail-person"
                      >
                        {textOrDash(detail.personName)}
                      </span>
                    </div>
                    <div className="hc-detail__field">
                      <span className="hc-detail__label">资质类别</span>
                      <span
                        className="hc-detail__value"
                        data-testid="hr-detail-category"
                      >
                        {categoryLabel(detail.category)}
                      </span>
                    </div>
                    <div className="hc-detail__field">
                      <span className="hc-detail__label">资质名称</span>
                      <span
                        className="hc-detail__value"
                        data-testid="hr-detail-credential"
                      >
                        {textOrDash(detail.credentialName)}
                      </span>
                    </div>
                    <div className="hc-detail__field">
                      <span className="hc-detail__label">级别</span>
                      <span
                        className="hc-detail__value"
                        data-testid="hr-detail-level"
                      >
                        {textOrDash(detail.level)}
                      </span>
                    </div>
                    <div className="hc-detail__field">
                      <span className="hc-detail__label">有效期至</span>
                      <span
                        className="hc-detail__value"
                        data-testid="hr-detail-valid"
                      >
                        {formatDate(detail.validUntil)}
                      </span>
                    </div>
                    <div className="hc-detail__field">
                      <span className="hc-detail__label">状态</span>
                      <span
                        className="hc-detail__value"
                        data-testid="hr-detail-status"
                      >
                        {detail.isActive ? "启用" : "停用"}
                      </span>
                    </div>
                    <div className="hc-detail__field">
                      <span className="hc-detail__label">更新时间</span>
                      <span className="hc-detail__value">
                        {formatDateTime(detail.updatedAt)}
                      </span>
                    </div>
                  </div>
                  <div className="hc-detail__field">
                    <span className="hc-detail__label">备注</span>
                    <div
                      className="hc-detail__remark"
                      data-testid="hr-detail-remark"
                    >
                      {textOrDash(detail.remark)}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="hc-form" data-testid="hr-edit-form">
                  <h3 className="hc-section-title">编辑资质卡</h3>
                  <CardFormFields
                    form={editForm}
                    disabled={submitting}
                    onChange={setEditForm}
                    idPrefix="hr-edit"
                  />
                  <div className="hc-form__actions">
                    <button
                      type="button"
                      className="btn btn-soft"
                      data-testid="hr-edit-cancel"
                      disabled={submitting}
                      onClick={cancelEdit}
                    >
                      取消
                    </button>
                    <button
                      type="button"
                      className="btn btn-primary"
                      data-testid="hr-edit-submit"
                      disabled={submitting}
                      onClick={() => void onSaveEdit()}
                    >
                      {submitting ? "保存中…" : "保存"}
                    </button>
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="hc-placeholder" data-testid="hr-detail-missing">
              <strong>暂无详情</strong>
              <span>请重新选择列表项。</span>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
