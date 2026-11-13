// UI-CHG-001 変更要求（作成・影響分析・提出・反映）（設計書 7.1 のルート /projects/:projectId/change-requests。
// CHG-01〜04）。一覧（API-CHG-002。状態で絞る）・起票と手直し（API-CHG-001・003。DRAFT だけ）・影響分析の試算
// （API-CHG-004。本計画は変えない）・提出（API-CHG-005。承認申請を作る）・取り下げ（API-CHG-011。提出済みを下書きに戻し、
// 承認申請を消す）・反映と再基準化（API-CHG-006。承認済みだけ）。
// 承認・却下は承認待ち一覧（UI-CMN-003）で行う。操作のボタンは権限と状態（5.10）で出し分ける（7.2）
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  analyzeChangeRequest,
  applyChangeRequest,
  createChangeRequest,
  listChangeRequests,
  submitChangeRequest,
  updateChangeRequest,
  withdrawChangeRequest,
  type ChangeRequest,
  type ChangeRequestInput,
  type ChangeRequestList,
  type ChangeRequestStatus,
  type Impact,
} from "../../api/chg";
import { hasProjectPermission } from "../../api/cmn";
import { toErrorView, type ErrorView } from "../../api/errors";
import { fetchAllPages } from "../../api/pages";
import { getProject } from "../../api/prj";
import { listActivities, type Activity } from "../../api/sch";
import ErrorAlert from "../../components/ErrorAlert";
import Pager from "../../components/Pager";
import { formatDateTime } from "../../lib/format";
import { wbsStatusLabel } from "../prj/labels";
import { parseProjectId } from "../prj/ProjectSettingsPage";
import ChangeRequestForm from "./ChangeRequestForm";
import ImpactView from "./ImpactView";
import { CHANGE_STATUS_LABELS, CHANGE_STATUSES, CHG_PAGE_SIZE, userLabel } from "./labels";
import { describeOperation } from "./operations";
import { useMe } from "./useMe";
import { useProjectMembers } from "./useProjectMembers";

/** 影響分析を流せる状態（反映済み・却下はサーバが 409 を返す。API-CHG-004） */
export function canAnalyze(status: ChangeRequestStatus): boolean {
  return status !== "APPLIED" && status !== "REJECTED";
}

/**
 * 反映（API-CHG-006）を押せる WBS の状態。サーバは WBS が基準化済み（BASELINED）のときだけ反映し、
 * それ以外は 409 `INVALID_STATE` で何も書かない（5.10・A-47 の 2）。状態を読めなかった（null）ときは押させ、サーバに判断させる
 */
export function wbsAllowsApply(wbsStatus: string | null): boolean {
  return wbsStatus === null || wbsStatus === "BASELINED";
}

type FormState = { mode: "create" } | { mode: "edit"; target: ChangeRequest } | null;

export default function ChangeRequestsPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = parseProjectId(params.projectId);
  const { me, meError } = useMe(projectId);
  const { names } = useProjectMembers(projectId);
  const [statusFilter, setStatusFilter] = useState<ChangeRequestStatus | "">("");
  const [offset, setOffset] = useState(0);
  const [reloadKey, setReloadKey] = useState(0);
  const [page, setPage] = useState<ChangeRequestList | null>(null);
  const [selected, setSelected] = useState<ChangeRequest | null>(null);
  const [form, setForm] = useState<FormState>(null);
  /** 変更内容の依存の端に選べる今ある活動（起票・手直しの部品を開いたときに全件を読む。OQ-38 (2)） */
  const [activities, setActivities] = useState<Activity[] | null>(null);
  const [activitiesError, setActivitiesError] = useState<ErrorView | null>(null);
  const [impact, setImpact] = useState<Impact | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);
  const [actionError, setActionError] = useState<ErrorView | null>(null);
  /** 承認済みを開いたときに読む WBS の状態（反映を押せるかを決める。読めなければ null） */
  const [wbsStatus, setWbsStatus] = useState<string | null>(null);
  /** 変更要求を開いた回数。開き直すたびに WBS の状態を読み直す（別の画面で基準化されたのを拾う） */
  const [openCount, setOpenCount] = useState(0);

  useEffect(() => {
    if (projectId === null) {
      return;
    }
    let cancelled = false;
    listChangeRequests(projectId, {
      status: statusFilter === "" ? undefined : statusFilter,
      limit: CHG_PAGE_SIZE,
      offset,
    }).then(
      (result) => {
        if (!cancelled) {
          setPage(result);
          setError(null);
          // 開いている変更要求は、読み直した値に置き換える
          setSelected((current) =>
            current === null ? null : (result.items.find((item) => item.id === current.id) ?? current),
          );
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, statusFilter, offset, reloadKey]);

  const formOpen = form !== null;
  useEffect(() => {
    if (projectId === null || !formOpen) {
      return;
    }
    let cancelled = false;
    setActivities(null);
    setActivitiesError(null);
    fetchAllPages((page) => listActivities(projectId, page)).then(
      (items) => {
        if (!cancelled) {
          setActivities(items);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setActivitiesError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, formOpen]);

  const needsWbsStatus =
    projectId !== null &&
    selected?.status === "APPROVED" &&
    me !== null &&
    hasProjectPermission(me, projectId, "change.apply");
  useEffect(() => {
    if (projectId === null || !needsWbsStatus) {
      return;
    }
    let cancelled = false;
    setWbsStatus(null);
    getProject(projectId).then(
      (project) => {
        if (!cancelled) {
          setWbsStatus(project.wbs_status);
        }
      },
      () => {
        // 読めなければ反映を押させ、サーバの判断（409 なら文言）を出す
        if (!cancelled) {
          setWbsStatus(null);
        }
      },
    );
    return () => {
      cancelled = true;
    };
    // 開き直したとき・一覧を読み直したとき（page が変わる）にも読み直す
  }, [projectId, needsWbsStatus, openCount, page]);

  if (projectId === null) {
    return (
      <section data-screen-id="UI-CHG-001" className="change-requests-page">
        <h2>変更要求</h2>
        <p role="alert">プロジェクトが見つかりません</p>
        <p>
          <Link to="/projects">プロジェクト一覧へ</Link>
        </p>
      </section>
    );
  }

  const canCreate = me !== null && hasProjectPermission(me, projectId, "change.create");
  const canApply = me !== null && hasProjectPermission(me, projectId, "change.apply");

  function open(item: ChangeRequest) {
    setSelected(item);
    setOpenCount((n) => n + 1);
    setImpact(null);
    setActionError(null);
    setForm(null);
  }

  function reload() {
    setReloadKey((k) => k + 1);
  }

  async function handleSave(input: ChangeRequestInput) {
    if (projectId === null || form === null) {
      return;
    }
    const saved =
      form.mode === "create"
        ? await createChangeRequest(projectId, input)
        : await updateChangeRequest(projectId, form.target.id, input);
    setNotice(
      form.mode === "create"
        ? `変更要求 #${saved.number}「${saved.title}」を下書きとして作った`
        : `変更要求 #${saved.number} を保存した`,
    );
    setForm(null);
    setSelected(saved);
    setImpact(null);
    reload();
  }

  async function run(action: (target: ChangeRequest) => Promise<void>) {
    if (selected === null) {
      return;
    }
    setBusy(true);
    setActionError(null);
    setNotice(null);
    try {
      await action(selected);
    } catch (e) {
      setActionError(toErrorView(e));
    } finally {
      setBusy(false);
    }
  }

  const handleAnalyze = () =>
    run(async (target) => {
      setImpact(await analyzeChangeRequest(projectId, target.id));
    });

  const handleSubmit = () =>
    run(async (target) => {
      const submitted = await submitChangeRequest(projectId, target.id);
      setSelected(submitted);
      setImpact(null);
      setNotice(`変更要求 #${submitted.number} を提出した。承認待ち一覧で承認者が判断する`);
      reload();
    });

  const handleWithdraw = () =>
    run(async (target) => {
      const withdrawn = await withdrawChangeRequest(projectId, target.id);
      setSelected(withdrawn);
      setImpact(null);
      setNotice(`変更要求 #${withdrawn.number} の提出を取り下げた（下書きに戻した。承認申請は取り消した）`);
      reload();
    });

  const handleApply = () =>
    run(async (target) => {
      const applied = await applyChangeRequest(projectId, target.id);
      setSelected(applied.change_request);
      setImpact(null);
      setNotice(
        `変更要求 #${applied.change_request.number} を反映した。` +
          `スコープ基準の第 ${applied.scope_baseline.version} 版とスケジュール基準の第 ${applied.schedule_baseline.version} 版を作った（旧版は残る）`,
      );
      reload();
    });

  return (
    <section data-screen-id="UI-CHG-001" className="change-requests-page">
      <h2>変更要求</h2>
      <nav aria-label="行き先" className="page-links">
        <Link to="/projects">プロジェクト一覧へ</Link>
        <Link to="/approvals">承認待ち</Link>
        <Link to={`/projects/${projectId}/baselines`}>ベースライン</Link>
        <Link to={`/projects/${projectId}/audit-logs`}>変更履歴</Link>
      </nav>
      <ErrorAlert error={meError ?? error} />
      {notice !== null && <p role="status">{notice}</p>}

      {canCreate && form === null && (
        <p>
          <button type="button" onClick={() => setForm({ mode: "create" })}>
            変更要求を作る
          </button>
        </p>
      )}
      {form !== null && (
        <ChangeRequestForm
          key={form.mode === "create" ? "create" : `edit-${form.target.id}`}
          initial={form.mode === "create" ? null : form.target}
          activities={activities}
          activitiesError={activitiesError}
          onSave={handleSave}
          onCancel={() => setForm(null)}
        />
      )}

      <section aria-labelledby="change-request-list-heading">
        <h3 id="change-request-list-heading">変更要求の一覧</h3>
        <label>
          状態で絞り込む
          <select
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value as ChangeRequestStatus | "");
              setOffset(0);
            }}
          >
            <option value="">すべて</option>
            {CHANGE_STATUSES.map((s) => (
              <option key={s} value={s}>
                {CHANGE_STATUS_LABELS[s]}
              </option>
            ))}
          </select>
        </label>
        {page !== null && page.items.length === 0 && <p>変更要求はありません</p>}
        {page !== null && page.items.length > 0 && (
          <>
            <table aria-label="変更要求">
              <thead>
                <tr>
                  <th scope="col">番号</th>
                  <th scope="col">件名</th>
                  <th scope="col">状態</th>
                  <th scope="col">対象の WBS ID</th>
                  <th scope="col">作成者</th>
                  <th scope="col">提出した日時</th>
                  <th scope="col">操作</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((item) => (
                  <tr
                    key={item.id}
                    data-status={item.status}
                    className={selected?.id === item.id ? "selected" : undefined}
                  >
                    <td>#{item.number}</td>
                    <td>{item.title}</td>
                    <td>{CHANGE_STATUS_LABELS[item.status]}</td>
                    <td>{item.target_codes.join(", ")}</td>
                    <td>{userLabel(names, item.created_by)}</td>
                    <td>{item.submitted_at === null ? "—" : formatDateTime(item.submitted_at)}</td>
                    <td>
                      <button type="button" aria-label={`変更要求 #${item.number} を開く`} onClick={() => open(item)}>
                        開く
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pager total={page.total} limit={page.limit} offset={page.offset} onChange={setOffset} />
          </>
        )}
      </section>

      {selected !== null && (
        <section aria-labelledby="change-request-detail-heading" className="change-request-detail">
          <h3 id="change-request-detail-heading">
            変更要求 #{selected.number}「{selected.title}」
          </h3>
          <dl>
            <dt>状態</dt>
            <dd>{CHANGE_STATUS_LABELS[selected.status]}</dd>
            <dt>理由</dt>
            <dd>{selected.reason}</dd>
            <dt>対象の WBS ID</dt>
            <dd>{selected.target_codes.join(", ")}</dd>
            <dt>作成者</dt>
            <dd>{userLabel(names, selected.created_by)}</dd>
            <dt>提出した日時</dt>
            <dd>{selected.submitted_at === null ? "—" : formatDateTime(selected.submitted_at)}</dd>
            {selected.applied_at !== null && (
              <>
                <dt>反映</dt>
                <dd>
                  {formatDateTime(selected.applied_at)}（{userLabel(names, selected.applied_by)}）
                </dd>
              </>
            )}
          </dl>

          <h4>変更内容</h4>
          {selected.operations === null ? (
            <p>変更内容と影響分析は表示しない（委託先には返さない）</p>
          ) : selected.operations.length === 0 ? (
            <p>変更内容はまだありません</p>
          ) : (
            <ol aria-label="変更内容">
              {selected.operations.map((operation, index) => (
                <li key={index}>{describeOperation(operation)}</li>
              ))}
            </ol>
          )}

          {selected.status === "SUBMITTED" && (
            <p>
              承認待ち。承認・却下は <Link to="/approvals">承認待ち一覧</Link> で行う（提出した人は判断できない）。
            </p>
          )}

          <ErrorAlert error={actionError} />
          <div className="actions" role="group" aria-label="変更要求の操作">
            {selected.status === "DRAFT" && canCreate && (
              <>
                {/* 変更内容を受け取っていない（委託先）ときは、保存で変更内容を空にしてしまうので直させない（CR-016） */}
                {selected.operations !== null && (
                  <button type="button" disabled={busy} onClick={() => setForm({ mode: "edit", target: selected })}>
                    直す
                  </button>
                )}
                <button type="button" disabled={busy} onClick={() => void handleSubmit()}>
                  提出する
                </button>
              </>
            )}
            {selected.status === "SUBMITTED" && canCreate && (
              <button type="button" disabled={busy} onClick={() => void handleWithdraw()}>
                提出を取り下げる
              </button>
            )}
            {/* 委託先（変更内容を受け取っていない）にはサーバが 403 を返すので出さない（CR-016） */}
            {canAnalyze(selected.status) && selected.operations !== null && (
              <button type="button" disabled={busy} onClick={() => void handleAnalyze()}>
                影響分析を流す
              </button>
            )}
            {selected.status === "APPROVED" && canApply && (
              <button
                type="button"
                disabled={busy || !wbsAllowsApply(wbsStatus)}
                onClick={() => void handleApply()}
              >
                反映する（再基準化）
              </button>
            )}
          </div>
          {selected.status === "APPROVED" && canApply && !wbsAllowsApply(wbsStatus) && wbsStatus !== null && (
            <p className="hint">
              反映は WBS が基準化済みのときだけできる（いまの WBS の状態：{wbsStatusLabel(wbsStatus)}）。
              WBS のレビューと承認で基準化してから反映する。
            </p>
          )}

          {impact !== null && <ImpactView impact={impact} title="影響分析（試算。本計画は変えない）" />}
          {impact === null && selected.impact !== null && (
            <ImpactView impact={selected.impact} title="提出のときの影響分析" />
          )}
        </section>
      )}
    </section>
  );
}
