// UI-CHG-002 是正措置（設計書 7.1 のルート /projects/:projectId/corrective-actions。CHG-06）。
// 一覧（API-CHG-008。状態・期限超過・WBS ID で絞る）・登録（API-CHG-007）・状態と効果の更新（API-CHG-009。
// RULE-CHG-005 の遷移だけを選ばせる）。書く操作は action.manage を持つ人にだけ出す（7.2）
import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import {
  createCorrectiveAction,
  listCorrectiveActions,
  updateCorrectiveAction,
  type CorrectiveAction,
  type CorrectiveActionInput,
  type CorrectiveActionList,
  type CorrectiveActionStatus,
} from "../../api/chg";
import { hasProjectPermission } from "../../api/cmn";
import { toErrorView, type ErrorView } from "../../api/errors";
import { fetchAllPages } from "../../api/pages";
import { listStatusReports, type StatusReport } from "../../api/prg";
import { listActivities, type Activity } from "../../api/sch";
import { getWbsTree, type WbsElement } from "../../api/wbs";
import ErrorAlert from "../../components/ErrorAlert";
import Pager from "../../components/Pager";
import { optionalText } from "../../lib/form";
import { parseProjectId } from "../prj/ProjectSettingsPage";
import CorrectiveActionForm from "./CorrectiveActionForm";
import {
  ACTION_STATUS_LABELS,
  ACTION_STATUSES,
  CHG_PAGE_SIZE,
  nextActionStatuses,
  userLabel,
  WBS_CODE_MAX,
} from "./labels";
import { useMe } from "./useMe";
import { useProjectMembers } from "./useProjectMembers";

interface Filters {
  status: CorrectiveActionStatus | "";
  overdueOnly: boolean;
  wbsCode: string;
}

/** 紐付けの候補（登録の欄の選択肢）：WBS の要素・活動（全件）・報告日（全件） */
interface LinkOptions {
  elements: WbsElement[];
  activities: Activity[];
  reports: StatusReport[];
}

function ActionEditor({
  target,
  members,
  onSave,
  onCancel,
}: {
  target: CorrectiveAction;
  members: { user_id: number; display_name: string }[];
  onSave: (id: number, status: CorrectiveActionStatus, effect: string | null, owner: number | null, due: string | null) => Promise<void>;
  onCancel: () => void;
}) {
  const [status, setStatus] = useState<CorrectiveActionStatus>(target.status);
  const [effect, setEffect] = useState(target.effect ?? "");
  const [owner, setOwner] = useState(target.owner_user_id === null ? "" : String(target.owner_user_id));
  const [due, setDue] = useState(target.due_date ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ErrorView | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await onSave(target.id, status, optionalText(effect), owner === "" ? null : Number(owner), optionalText(due));
    } catch (e) {
      setError(toErrorView(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form aria-label={`是正措置 #${target.id} を更新する`} onSubmit={(e) => void handleSubmit(e)}>
      <h3>是正措置 #{target.id} を更新する</h3>
      <label>
        状態
        <select value={status} onChange={(e) => setStatus(e.target.value as CorrectiveActionStatus)}>
          {nextActionStatuses(target.status).map((s) => (
            <option key={s} value={s}>
              {ACTION_STATUS_LABELS[s]}
            </option>
          ))}
        </select>
      </label>
      <label>
        効果
        <textarea value={effect} onChange={(e) => setEffect(e.target.value)} />
      </label>
      <label>
        担当
        <select value={owner} onChange={(e) => setOwner(e.target.value)}>
          <option value="">未定</option>
          {members.map((m) => (
            <option key={m.user_id} value={m.user_id}>
              {m.display_name}
            </option>
          ))}
        </select>
      </label>
      <label>
        期限
        <input type="date" value={due} onChange={(e) => setDue(e.target.value)} />
      </label>
      <ErrorAlert error={error} />
      <button type="submit" disabled={saving}>
        更新する
      </button>
      <button type="button" onClick={onCancel} disabled={saving}>
        やめる
      </button>
    </form>
  );
}

export default function CorrectiveActionsPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = parseProjectId(params.projectId);
  const { me, meError } = useMe(projectId);
  const { members, names } = useProjectMembers(projectId);
  const [filters, setFilters] = useState<Filters>({ status: "", overdueOnly: false, wbsCode: "" });
  const [wbsInput, setWbsInput] = useState("");
  const [offset, setOffset] = useState(0);
  const [reloadKey, setReloadKey] = useState(0);
  const [page, setPage] = useState<CorrectiveActionList | null>(null);
  const [options, setOptions] = useState<LinkOptions | null>(null);
  const [editing, setEditing] = useState<CorrectiveAction | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);

  const canManage = me !== null && projectId !== null && hasProjectPermission(me, projectId, "action.manage");

  useEffect(() => {
    if (projectId === null) {
      return;
    }
    let cancelled = false;
    listCorrectiveActions(projectId, {
      status: filters.status === "" ? undefined : filters.status,
      overdue: filters.overdueOnly ? true : undefined,
      wbs_code: filters.wbsCode === "" ? undefined : filters.wbsCode,
      limit: CHG_PAGE_SIZE,
      offset,
    }).then(
      (result) => {
        if (!cancelled) {
          setPage(result);
          setError(null);
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
  }, [projectId, filters, offset, reloadKey]);

  // 登録の欄の選択肢は、書ける人にだけ読む
  useEffect(() => {
    if (projectId === null || !canManage) {
      return;
    }
    let cancelled = false;
    Promise.all([
      getWbsTree(projectId),
      fetchAllPages((page) => listActivities(projectId, page)),
      fetchAllPages((page) => listStatusReports(projectId, page)),
    ]).then(
      ([tree, activities, reports]) => {
        if (!cancelled) {
          setOptions({ elements: tree.elements, activities, reports });
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
  }, [projectId, canManage]);

  if (projectId === null) {
    return (
      <section data-screen-id="UI-CHG-002" className="corrective-actions-page">
        <h2>是正措置</h2>
        <p role="alert">プロジェクトが見つかりません</p>
        <p>
          <Link to="/projects">プロジェクト一覧へ</Link>
        </p>
      </section>
    );
  }

  function handleFilter(event: FormEvent) {
    event.preventDefault();
    setOffset(0);
    setFilters((f) => ({ ...f, wbsCode: wbsInput.trim() }));
  }

  async function handleCreate(input: CorrectiveActionInput) {
    if (projectId === null) {
      return;
    }
    const created = await createCorrectiveAction(projectId, input);
    setNotice(`是正措置 #${created.id} を登録した（${ACTION_STATUS_LABELS[created.status]}）`);
    setReloadKey((k) => k + 1);
  }

  async function handleUpdate(
    id: number,
    status: CorrectiveActionStatus,
    effect: string | null,
    owner: number | null,
    due: string | null,
  ) {
    if (projectId === null) {
      return;
    }
    const updated = await updateCorrectiveAction(projectId, id, {
      status,
      effect,
      owner_user_id: owner,
      due_date: due,
    });
    setNotice(`是正措置 #${updated.id} を更新した（${ACTION_STATUS_LABELS[updated.status]}）`);
    setEditing(null);
    setReloadKey((k) => k + 1);
  }

  return (
    <section data-screen-id="UI-CHG-002" className="corrective-actions-page">
      <h2>是正措置</h2>
      <nav aria-label="行き先" className="page-links">
        <Link to="/projects">プロジェクト一覧へ</Link>
        <Link to={`/projects/${projectId}/gantt`}>ガントチャート</Link>
        <Link to={`/projects/${projectId}/change-requests`}>変更要求</Link>
      </nav>
      <ErrorAlert error={meError ?? error} />
      {notice !== null && <p role="status">{notice}</p>}

      {canManage && options !== null && (
        <CorrectiveActionForm
          elements={options.elements}
          activities={options.activities}
          reports={options.reports}
          members={members}
          onSave={handleCreate}
        />
      )}

      {editing !== null && (
        <ActionEditor
          key={editing.id}
          target={editing}
          members={members}
          onSave={handleUpdate}
          onCancel={() => setEditing(null)}
        />
      )}

      <section aria-labelledby="corrective-action-list-heading">
        <h3 id="corrective-action-list-heading">是正措置の一覧</h3>
        <form aria-label="是正措置を絞り込む" onSubmit={handleFilter} className="filters">
          <label>
            状態
            <select
              value={filters.status}
              onChange={(e) => {
                setOffset(0);
                setFilters((f) => ({ ...f, status: e.target.value as CorrectiveActionStatus | "" }));
              }}
            >
              <option value="">すべて</option>
              {ACTION_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {ACTION_STATUS_LABELS[s]}
                </option>
              ))}
            </select>
          </label>
          <label>
            <input
              type="checkbox"
              checked={filters.overdueOnly}
              onChange={(e) => {
                setOffset(0);
                setFilters((f) => ({ ...f, overdueOnly: e.target.checked }));
              }}
            />
            期限超過だけ
          </label>
          <label>
            WBS ID（旧 ID も可。配下を含む）
            <input value={wbsInput} maxLength={WBS_CODE_MAX} onChange={(e) => setWbsInput(e.target.value)} />
          </label>
          <button type="submit">絞り込む</button>
        </form>

        {page !== null && page.items.length === 0 && <p>是正措置はありません</p>}
        {page !== null && page.items.length > 0 && (
          <>
            <table aria-label="是正措置">
              <thead>
                <tr>
                  <th scope="col">WBS ID</th>
                  <th scope="col">原因</th>
                  <th scope="col">対策</th>
                  <th scope="col">担当</th>
                  <th scope="col">期限</th>
                  <th scope="col">状態</th>
                  <th scope="col">効果</th>
                  {canManage && <th scope="col">操作</th>}
                </tr>
              </thead>
              <tbody>
                {page.items.map((a) => (
                  <tr key={a.id} data-status={a.status} data-overdue={a.overdue ? "true" : undefined}>
                    <td>{a.wbs_code ?? "—"}</td>
                    <td>{a.cause}</td>
                    <td>{a.action}</td>
                    <td>{userLabel(names, a.owner_user_id)}</td>
                    <td>
                      {a.due_date ?? "—"}
                      {a.overdue && <strong className="overdue">（期限超過）</strong>}
                    </td>
                    <td>{ACTION_STATUS_LABELS[a.status]}</td>
                    <td>{a.effect ?? "—"}</td>
                    {canManage && (
                      <td>
                        <button
                          type="button"
                          aria-label={`是正措置 #${a.id} を更新する`}
                          onClick={() => {
                            setNotice(null);
                            setEditing(a);
                          }}
                        >
                          更新
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            <Pager total={page.total} limit={page.limit} offset={page.offset} onChange={setOffset} />
          </>
        )}
      </section>
    </section>
  );
}
