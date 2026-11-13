// UI-SCH-001 タスクリスト（設計書 7.1 のルート /projects/:projectId/activities。SCH-01・02・03・04・09、EFF-01、EST-01）。
// 活動の一覧（API-SCH-001）を表で出し、日付・期間・先行・担当をまとめて編集して 1 回で保存する（API-SCH-004：全件か無しか）。
// 活動を足す（API-SCH-002）・消す（API-SCH-005）・依存（API-SCH-006・007）・担当と割当工数（API-EFF-001）も同じ画面に置く。
// 活動の見積（人時・人日、手法・前提・参照資料）は API-EST-001 の ACTIVITY で書く（7.2）。
// 変える部品は schedule.edit、見積は estimate.edit を持つ人にだけ出す（7.2。`GET /api/v1/me` で決め、サーバでも必ず判定する）
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getMe, hasProjectPermission, type Me } from "../../api/cmn";
import { toErrorView, type ErrorView } from "../../api/errors";
import type { EstimateWritten } from "../../api/est";
import { listMembers, type Member } from "../../api/prj";
import {
  bulkUpdateActivities,
  deleteActivity,
  listActivities,
  type Activity,
  type ActivityList,
} from "../../api/sch";
import { getWbsTree, type WbsElement } from "../../api/wbs";
import ErrorAlert from "../../components/ErrorAlert";
import Pager from "../../components/Pager";
import { parseProjectId } from "../prj/ProjectSettingsPage";
import EstimateForm from "../wbs/EstimateForm";
import AddActivityForm from "./activities/AddActivityForm";
import AssignmentEditor from "./activities/AssignmentEditor";
import { collectChanges, draftOf, type ActivityDraft } from "./activities/bulkEdit";
import DependencySection from "./activities/DependencySection";
import { ACTIVITIES_PAGE_SIZE, ACTIVITY_NAME_MAX, LIST_LIMIT_MAX } from "./limits";

function text(value: string | number | null): string {
  return value === null ? "—" : String(value);
}

function hoursText(value: number | null): string {
  return value === null ? "未見積" : `${value} 人時`;
}

/** 先行の表示。先行の無い活動は並行して進められる（SCH-04） */
function predecessorText(activity: Activity): string {
  return activity.predecessor_ids.length === 0 ? "なし（並行可）" : activity.predecessor_ids.join(";");
}

export default function ActivityListPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = parseProjectId(params.projectId);
  const [me, setMe] = useState<Me | null>(null);
  const [list, setList] = useState<ActivityList | null>(null);
  const [offset, setOffset] = useState(0);
  const [reloadKey, setReloadKey] = useState(0);
  const [workPackages, setWorkPackages] = useState<WbsElement[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [drafts, setDrafts] = useState<Record<number, ActivityDraft>>({});
  const [assigning, setAssigning] = useState<Activity | null>(null);
  const [estimating, setEstimating] = useState<Activity | null>(null);
  const [removing, setRemoving] = useState<Activity | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<ErrorView | null>(null);
  const [saveError, setSaveError] = useState<ErrorView | null>(null);

  useEffect(() => {
    if (projectId === null) {
      return;
    }
    let cancelled = false;
    getMe().then(
      (result) => {
        if (!cancelled) {
          setMe(result);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setLoadError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  useEffect(() => {
    if (projectId === null) {
      return;
    }
    let cancelled = false;
    listActivities(projectId, { limit: ACTIVITIES_PAGE_SIZE, offset }).then(
      (result) => {
        if (!cancelled) {
          setList(result);
          setDrafts({});
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setList(null);
          setLoadError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, offset, reloadKey]);

  const canEdit = projectId !== null && me !== null && hasProjectPermission(me, projectId, "schedule.edit");
  const canEstimate = projectId !== null && me !== null && hasProjectPermission(me, projectId, "estimate.edit");
  const showActions = canEdit || canEstimate;

  // 変える人だけが要る：置き場の WP（API-WBS-001）と担当の候補（API-PRJ-009）
  useEffect(() => {
    if (projectId === null || !canEdit) {
      return;
    }
    let cancelled = false;
    Promise.all([getWbsTree(projectId), listMembers(projectId, { limit: LIST_LIMIT_MAX })]).then(
      ([tree, memberList]) => {
        if (!cancelled) {
          setWorkPackages(tree.elements.filter((e) => e.kind === "WORK_PACKAGE"));
          setMembers(memberList.items);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setLoadError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, canEdit]);

  const memberName = useMemo(() => new Map(members.map((m) => [m.user_id, m.display_name])), [members]);
  const dirty = Object.keys(drafts).length > 0;

  if (projectId === null) {
    return (
      <section data-screen-id="UI-SCH-001" className="activity-list-page">
        <h2>タスクリスト</h2>
        <p role="alert">プロジェクトが見つかりません</p>
        <p>
          <Link to="/projects">プロジェクト一覧へ</Link>
        </p>
      </section>
    );
  }

  function reload(message: string | null = null) {
    setNotice(message);
    setReloadKey((k) => k + 1);
  }

  function edit(activity: Activity, patch: Partial<ActivityDraft>) {
    setDrafts((current) => ({ ...current, [activity.id]: { ...(current[activity.id] ?? draftOf(activity)), ...patch } }));
  }

  async function handleSaveAll() {
    if (projectId === null || list === null) {
      return;
    }
    setSaveError(null);
    setNotice(null);
    const { changes, errors } = collectChanges(list.items, drafts);
    if (errors.length > 0) {
      setSaveError({ message: "入力に誤りがあります（何も保存していません）", details: errors });
      return;
    }
    if (changes.length === 0) {
      setDrafts({});
      return;
    }
    setSubmitting(true);
    try {
      const result = await bulkUpdateActivities(projectId, changes);
      reload(`${result.items.length} 件の活動を保存し、日程を計算し直した`);
    } catch (e) {
      setSaveError(toErrorView(e));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRemove(activity: Activity) {
    if (projectId === null) {
      return;
    }
    setSaveError(null);
    setSubmitting(true);
    try {
      await deleteActivity(projectId, activity.id);
      setRemoving(null);
      if (assigning?.id === activity.id) {
        setAssigning(null);
      }
      if (estimating?.id === activity.id) {
        setEstimating(null);
      }
      reload(`活動 ${activity.id}（${activity.name}）を消した`);
    } catch (e) {
      setSaveError(toErrorView(e));
    } finally {
      setSubmitting(false);
    }
  }

  // 見積を書いたら一覧を読み直す（活動の estimate_hours が変わる）
  function handleEstimateSaved(activity: Activity, result: EstimateWritten) {
    setEstimating(null);
    reload(
      `活動 ${activity.id}（${activity.name}）の見積を ${hoursText(result.current_hours)}にした（プロジェクトの総見積 ${hoursText(result.project_estimate_hours)}）`,
    );
  }

  const activities = list?.items ?? [];

  return (
    <section data-screen-id="UI-SCH-001" className="activity-list-page">
      <h2>タスクリスト</h2>
      <nav aria-label="行き先" className="page-links">
        <Link to="/projects">プロジェクト一覧へ</Link>
        <Link to={`/projects/${projectId}/gantt`}>ガント</Link>
      </nav>
      <ErrorAlert error={loadError} />
      {notice !== null && <p role="status">{notice}</p>}

      {canEdit && (
        <AddActivityForm
          projectId={projectId}
          workPackages={workPackages}
          onAdded={(a) => reload(`活動 ${a.id}（${a.name}）を足し、日程を計算し直した`)}
        />
      )}

      <section aria-labelledby="activity-list-heading">
        <h3 id="activity-list-heading">活動の一覧</h3>
        {list !== null && list.restricted && <p className="note">自分の割当の活動だけを表示している</p>}
        {list !== null && activities.length === 0 && <p>活動はまだありません</p>}
        {activities.length > 0 && (
          <table aria-label="活動の一覧">
            <thead>
              <tr>
                <th scope="col">ID</th>
                <th scope="col">WBS ID</th>
                <th scope="col">名称</th>
                <th scope="col">希望開始日</th>
                <th scope="col">期間（稼働日）</th>
                <th scope="col">開始日</th>
                <th scope="col">終了日</th>
                <th scope="col">先行（活動 ID）</th>
                <th scope="col">担当</th>
                <th scope="col">見積（人時）</th>
                <th scope="col">余裕（日）</th>
                {showActions && <th scope="col">操作</th>}
              </tr>
            </thead>
            <tbody>
              {activities.map((a) => {
                const draft = drafts[a.id] ?? draftOf(a);
                return (
                  <tr key={a.id} data-critical={a.is_critical ? "true" : undefined}>
                    <td>{a.id}</td>
                    <td title={a.work_package_name}>{a.wbs_code}</td>
                    <td>
                      {a.is_milestone && <span aria-label="マイルストーン">◆ </span>}
                      {canEdit ? (
                        <input
                          aria-label={`${a.id} の名称`}
                          value={draft.name}
                          maxLength={ACTIVITY_NAME_MAX}
                          onChange={(e) => edit(a, { name: e.target.value })}
                        />
                      ) : (
                        a.name
                      )}
                    </td>
                    <td>
                      {canEdit ? (
                        <input
                          type="date"
                          aria-label={`${a.id} の希望開始日`}
                          value={draft.requestedStart}
                          onChange={(e) => edit(a, { requestedStart: e.target.value })}
                        />
                      ) : (
                        text(a.requested_start)
                      )}
                    </td>
                    <td>
                      {canEdit ? (
                        <input
                          aria-label={`${a.id} の期間`}
                          inputMode="numeric"
                          size={5}
                          value={draft.duration}
                          onChange={(e) => edit(a, { duration: e.target.value })}
                        />
                      ) : (
                        a.duration_days
                      )}
                    </td>
                    <td>{text(a.start_date)}</td>
                    <td>{text(a.finish_date)}</td>
                    <td>
                      {canEdit ? (
                        <input
                          aria-label={`${a.id} の先行`}
                          size={8}
                          placeholder="なし（並行可）"
                          value={draft.predecessors}
                          onChange={(e) => edit(a, { predecessors: e.target.value })}
                        />
                      ) : (
                        predecessorText(a)
                      )}
                    </td>
                    <td>
                      {canEdit ? (
                        <select
                          multiple
                          aria-label={`${a.id} の担当`}
                          value={draft.assigneeIds.map(String)}
                          onChange={(e) =>
                            edit(a, { assigneeIds: Array.from(e.target.selectedOptions, (o) => Number(o.value)) })
                          }
                        >
                          {members
                            .filter((m) => m.is_active || draft.assigneeIds.includes(m.user_id))
                            .map((m) => (
                              <option key={m.user_id} value={m.user_id}>
                                {m.display_name}
                              </option>
                            ))}
                        </select>
                      ) : (
                        a.assignees.map((x) => memberName.get(x.user_id) ?? x.display_name).join("、") || "—"
                      )}
                    </td>
                    <td>{text(a.estimate_hours)}</td>
                    <td>
                      {text(a.total_float)}
                      {a.is_critical && <span className="critical">（クリティカル）</span>}
                    </td>
                    {showActions && (
                      <td>
                        {canEstimate && (
                          <button type="button" aria-label={`${a.id} の見積`} onClick={() => setEstimating(a)}>
                            見積
                          </button>
                        )}
                        {canEdit && (
                          <>
                            <button type="button" aria-label={`${a.id} の担当と割当工数`} onClick={() => setAssigning(a)}>
                              担当と工数
                            </button>
                            <button
                              type="button"
                              aria-label={`${a.id} を消す`}
                              onClick={() => setRemoving(a)}
                              disabled={submitting}
                            >
                              消す
                            </button>
                          </>
                        )}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {list !== null && <Pager total={list.total} limit={list.limit} offset={list.offset} onChange={setOffset} />}

        {canEdit && removing !== null && (
          <div role="group" aria-label="活動を消すことの確かめ">
            <p>
              活動 {removing.id}（{removing.name}）を消します。依存と割当も消え、日程は計算し直されます。
            </p>
            <button type="button" onClick={() => void handleRemove(removing)} disabled={submitting}>
              消す
            </button>
            <button type="button" onClick={() => setRemoving(null)}>
              やめる
            </button>
          </div>
        )}

        {canEdit && activities.length > 0 && (
          <div className="bulk-actions">
            <p className="hint">変えた行をまとめて保存する。1 件でも不正なら、どの行も保存しない。</p>
            <ErrorAlert error={saveError} />
            <button type="button" onClick={() => void handleSaveAll()} disabled={submitting || !dirty}>
              まとめて保存する
            </button>
            <button type="button" onClick={() => setDrafts({})} disabled={submitting || !dirty}>
              編集を取り消す
            </button>
          </div>
        )}
      </section>

      {canEstimate && estimating !== null && (
        <section aria-label={`活動 ${estimating.id} の見積`} className="activity-estimate">
          <h3>
            活動の見積：{estimating.id} {estimating.name}
          </h3>
          <p>今の見積：{hoursText(estimating.estimate_hours)}</p>
          <EstimateForm
            key={estimating.id}
            projectId={projectId}
            targetType="ACTIVITY"
            targetId={estimating.id}
            label={`活動 ${estimating.id} ${estimating.name}`}
            onSaved={(result) => handleEstimateSaved(estimating, result)}
          />
          <button type="button" onClick={() => setEstimating(null)}>
            閉じる
          </button>
        </section>
      )}

      {canEdit && assigning !== null && (
        <AssignmentEditor
          key={assigning.id}
          projectId={projectId}
          activity={assigning}
          members={members}
          onSaved={() => reload(`活動 ${assigning.id} の担当を保存した`)}
          onClose={() => setAssigning(null)}
        />
      )}

      {list !== null && (
        <DependencySection
          projectId={projectId}
          activities={activities}
          dependencies={list.dependencies}
          canEdit={canEdit}
          onChanged={() => reload("依存を変え、日程を計算し直した")}
        />
      )}
    </section>
  );
}
