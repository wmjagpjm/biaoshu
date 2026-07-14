/**
 * 模块：P10H 人员业绩素材卡页
 * 用途：严格 hr 下的列表摘要、点选详情、新建与编辑/启停；无删除/附件/导出。
 * 对接：useHrPerformanceCards；仅 /hr/performance-cards*；错误固定中文脱敏。
 * 二次开发：禁止浏览器持久化；列表不含 performanceSummary/remark；写后须重读服务端。
 */

import { useEffect, useState } from "react";
import { Award, RefreshCw } from "lucide-react";
import { EmptyState } from "../../../shared/components/EmptyState/EmptyState";
import { LoadingBlock } from "../../../shared/components/LoadingBlock/LoadingBlock";
import {
  useHrPerformanceCards,
  type HrPerformanceFormInput,
} from "../hooks/useHrPerformanceCards";
import type {
  HrPerformanceCardDetail,
  HrPerformanceCardSummary,
} from "../types";
import "./HrPerformanceCardsPage.css";

const EMPTY_FORM: HrPerformanceFormInput = {
  personName: "",
  projectName: "",
  projectRole: "",
  completedYear: "",
  performanceSummary: "",
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

/** 用途：文本单元格空值占位。 */
function textOrDash(value: string | null | undefined): string {
  if (value == null) return "—";
  const t = String(value).trim();
  return t ? t : "—";
}

/** 用途：完成年份展示。 */
function yearOrDash(value: number | null | undefined): string {
  if (value == null) return "—";
  return String(value);
}

/** 用途：详情回填编辑表单（不落存储）。 */
function detailToForm(detail: HrPerformanceCardDetail): HrPerformanceFormInput {
  return {
    personName: detail.personName ?? "",
    projectName: detail.projectName ?? "",
    projectRole: detail.projectRole ?? "",
    completedYear:
      detail.completedYear == null ? "" : String(detail.completedYear),
    performanceSummary: detail.performanceSummary ?? "",
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
  form: HrPerformanceFormInput;
  disabled: boolean;
  onChange: (next: HrPerformanceFormInput) => void;
  idPrefix: string;
}) {
  return (
    <div className="hp-form__fields">
      <label className="hp-form__field" htmlFor={`${idPrefix}-person`}>
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
      <label className="hp-form__field" htmlFor={`${idPrefix}-project`}>
        <span>项目名称</span>
        <input
          id={`${idPrefix}-project`}
          data-testid={`${idPrefix}-project`}
          type="text"
          maxLength={120}
          value={form.projectName}
          disabled={disabled}
          autoComplete="off"
          placeholder="人工录入项目名称"
          onChange={(e) => onChange({ ...form, projectName: e.target.value })}
        />
      </label>
      <label className="hp-form__field" htmlFor={`${idPrefix}-role`}>
        <span>项目角色（可选）</span>
        <input
          id={`${idPrefix}-role`}
          data-testid={`${idPrefix}-role`}
          type="text"
          maxLength={80}
          value={form.projectRole}
          disabled={disabled}
          autoComplete="off"
          placeholder="如 项目经理"
          onChange={(e) => onChange({ ...form, projectRole: e.target.value })}
        />
      </label>
      <label className="hp-form__field" htmlFor={`${idPrefix}-year`}>
        <span>完成年份（可选）</span>
        <input
          id={`${idPrefix}-year`}
          data-testid={`${idPrefix}-year`}
          type="text"
          inputMode="numeric"
          maxLength={10}
          value={form.completedYear}
          disabled={disabled}
          autoComplete="off"
          placeholder="1900–2100，可空"
          onChange={(e) =>
            onChange({ ...form, completedYear: e.target.value })
          }
        />
      </label>
      <label className="hp-form__check" htmlFor={`${idPrefix}-active`}>
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
        className="hp-form__field hp-form__field--full"
        htmlFor={`${idPrefix}-summary`}
      >
        <span>业绩摘要（1–1000 字）</span>
        <textarea
          id={`${idPrefix}-summary`}
          data-testid={`${idPrefix}-summary`}
          maxLength={1000}
          value={form.performanceSummary}
          disabled={disabled}
          placeholder="人工概述项目业绩，勿填合同金额或客户联系方式"
          onChange={(e) =>
            onChange({ ...form, performanceSummary: e.target.value })
          }
        />
      </label>
      <label
        className="hp-form__field hp-form__field--full"
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
  item: HrPerformanceCardSummary;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className={`hp-list__item${active ? " is-active" : ""}`}
      data-testid="hp-list-item"
      data-card-id={item.id}
      onClick={onSelect}
    >
      <span className="hp-list__name">{textOrDash(item.personName)}</span>
      <span className="hp-list__meta">
        <span>
          项目 <strong>{textOrDash(item.projectName)}</strong>
        </span>
        <span>
          角色 <strong>{textOrDash(item.projectRole)}</strong>
        </span>
        <span>
          年份 <strong>{yearOrDash(item.completedYear)}</strong>
        </span>
        <span
          className={`hp-badge${item.isActive ? " hp-badge--on" : " hp-badge--off"}`}
          data-testid="hp-list-item-status"
        >
          {item.isActive ? "启用" : "停用"}
        </span>
        <span>更新 {formatDateTime(item.updatedAt)}</span>
      </span>
    </button>
  );
}

/**
 * 模块：HrPerformanceCardsPage
 * 用途：P10H 人员业绩素材卡主页面。
 * 对接：useHrPerformanceCards；RequireHr 路由门禁。
 * 二次开发：不得挂载 P10D 资质/P10F 团队推荐接口；错误不得回显后端 detail。
 */
export function HrPerformanceCardsPage() {
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
  } = useHrPerformanceCards();

  const [createForm, setCreateForm] = useState<HrPerformanceFormInput>(EMPTY_FORM);
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState<HrPerformanceFormInput>(EMPTY_FORM);
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
    <div className="hp-layout" data-testid="hr-performance-page">
      <header className="page-header">
        <div>
          <h1>人员业绩</h1>
          <p>
            当前工作空间内的人员业绩素材卡登记与查看。仅协作显示名与人工录入的项目经历；不收集证件号码、手机、合同金额、附件或外链。
          </p>
        </div>
        <div className="hp-form__actions">
          <button
            type="button"
            className="btn btn-soft btn-sm"
            data-testid="hp-reload-list"
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
            data-testid="hp-show-create"
            disabled={submitting}
            onClick={() => {
              clearWriteError();
              setShowCreate(true);
            }}
          >
            新建业绩卡
          </button>
        </div>
      </header>

      {listError ? (
        <div className="hp-alert" role="alert" data-testid="hp-list-error">
          {listError}
        </div>
      ) : null}

      {writeError ? (
        <div className="hp-alert" role="alert" data-testid="hp-write-error">
          {writeError}
        </div>
      ) : null}

      <div className="hp-grid">
        <section className="hp-panel" aria-label="业绩卡列表">
          <div className="hp-panel__head">
            <h2 className="hp-panel__title">业绩卡列表</h2>
          </div>
          <p className="hp-panel__hint">
            列表仅为摘要，不含业绩概述与备注。点击条目后加载详情。
          </p>
          {listLoading ? (
            <LoadingBlock label="正在加载人员业绩…" />
          ) : items.length === 0 ? (
            <EmptyState
              icon={<Award size={28} />}
              title="暂无人员业绩卡"
              description="可点击「新建业绩卡」登记本空间协作人员项目业绩。"
            />
          ) : (
            <ul className="hp-list" data-testid="hp-list">
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

        <section className="hp-panel" aria-label="业绩卡详情">
          <div className="hp-panel__head">
            <h2 className="hp-panel__title">详情与编辑</h2>
          </div>

          {showCreate ? (
            <div className="hp-form" data-testid="hp-create-form">
              <h3 className="hp-section-title">新建人员业绩卡</h3>
              <CardFormFields
                form={createForm}
                disabled={submitting}
                onChange={setCreateForm}
                idPrefix="hp-create"
              />
              <div className="hp-form__actions">
                <button
                  type="button"
                  className="btn btn-soft"
                  data-testid="hp-create-cancel"
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
                  data-testid="hp-create-submit"
                  disabled={submitting}
                  onClick={() => void onCreate()}
                >
                  {submitting ? "提交中…" : "创建"}
                </button>
              </div>
            </div>
          ) : null}

          {!selectedId ? (
            <div className="hp-placeholder" data-testid="hp-detail-empty">
              <strong>尚未选择业绩卡</strong>
              <span>请从左侧列表选择一条，或新建后自动打开详情。</span>
            </div>
          ) : detailLoading ? (
            <LoadingBlock label="正在加载详情…" />
          ) : detailError ? (
            <div
              className="hp-alert"
              role="alert"
              data-testid="hp-detail-error"
            >
              {detailError}
            </div>
          ) : detail ? (
            <>
              {!editing ? (
                <div data-testid="hp-detail">
                  <div className="hp-detail-actions">
                    <button
                      type="button"
                      className="btn btn-soft btn-sm"
                      data-testid="hp-edit-btn"
                      disabled={submitting}
                      onClick={startEdit}
                    >
                      编辑
                    </button>
                    <button
                      type="button"
                      className="btn btn-soft btn-sm"
                      data-testid="hp-toggle-active"
                      disabled={submitting}
                      onClick={() => void onToggleActive()}
                    >
                      {detail.isActive ? "停用" : "启用"}
                    </button>
                  </div>
                  <div className="hp-detail__grid">
                    <div className="hp-detail__field">
                      <span className="hp-detail__label">人员姓名</span>
                      <span
                        className="hp-detail__value"
                        data-testid="hp-detail-person"
                      >
                        {textOrDash(detail.personName)}
                      </span>
                    </div>
                    <div className="hp-detail__field">
                      <span className="hp-detail__label">项目名称</span>
                      <span
                        className="hp-detail__value"
                        data-testid="hp-detail-project"
                      >
                        {textOrDash(detail.projectName)}
                      </span>
                    </div>
                    <div className="hp-detail__field">
                      <span className="hp-detail__label">项目角色</span>
                      <span
                        className="hp-detail__value"
                        data-testid="hp-detail-role"
                      >
                        {textOrDash(detail.projectRole)}
                      </span>
                    </div>
                    <div className="hp-detail__field">
                      <span className="hp-detail__label">完成年份</span>
                      <span
                        className="hp-detail__value"
                        data-testid="hp-detail-year"
                      >
                        {yearOrDash(detail.completedYear)}
                      </span>
                    </div>
                    <div className="hp-detail__field">
                      <span className="hp-detail__label">状态</span>
                      <span
                        className="hp-detail__value"
                        data-testid="hp-detail-status"
                      >
                        {detail.isActive ? "启用" : "停用"}
                      </span>
                    </div>
                    <div className="hp-detail__field">
                      <span className="hp-detail__label">更新时间</span>
                      <span className="hp-detail__value">
                        {formatDateTime(detail.updatedAt)}
                      </span>
                    </div>
                  </div>
                  <div className="hp-detail__field">
                    <span className="hp-detail__label">业绩摘要</span>
                    <div
                      className="hp-detail__block"
                      data-testid="hp-detail-summary"
                    >
                      {textOrDash(detail.performanceSummary)}
                    </div>
                  </div>
                  <div className="hp-detail__field">
                    <span className="hp-detail__label">备注</span>
                    <div
                      className="hp-detail__block"
                      data-testid="hp-detail-remark"
                    >
                      {textOrDash(detail.remark)}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="hp-form" data-testid="hp-edit-form">
                  <h3 className="hp-section-title">编辑业绩卡</h3>
                  <CardFormFields
                    form={editForm}
                    disabled={submitting}
                    onChange={setEditForm}
                    idPrefix="hp-edit"
                  />
                  <div className="hp-form__actions">
                    <button
                      type="button"
                      className="btn btn-soft"
                      data-testid="hp-edit-cancel"
                      disabled={submitting}
                      onClick={cancelEdit}
                    >
                      取消
                    </button>
                    <button
                      type="button"
                      className="btn btn-primary"
                      data-testid="hp-edit-submit"
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
            <div className="hp-placeholder" data-testid="hp-detail-missing">
              <strong>暂无详情</strong>
              <span>请重新选择列表项。</span>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
