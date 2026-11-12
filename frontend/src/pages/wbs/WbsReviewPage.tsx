// UI-WBS-003 WBSの検証・レビュー・承認（設計書 7.1 のルート /projects/:projectId/wbs-review。WBS-13・16・20・21・22）。
// 検査（API-WBS-012：100% ルールの 3 つの判定と、成果物・完了条件の欠けた WP）、レビュー（ReviewPanel）、
// 承認の申請（API-WBS-018。前提を満たさなければ 422 WBS_NOT_READY の前提ごとの件数を出す）、
// このプロジェクトの WBS の承認待ちへの判断（API-WBS-020 の一覧から。API-WBS-019）、スコープベースライン（ScopeBaselinesPanel）を並べる。
// 申請は wbs.edit、判断は wbs.approve を持つ人にだけ出す（7.2）。本人の申請の判断はサーバが 403 で拒む
import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { ApiError } from "../../api/client";
import {
  decideApproval,
  getMe,
  hasProjectPermission,
  listPendingApprovals,
  type Decision,
  type Me,
  type PendingApproval,
} from "../../api/cmn";
import { toErrorView, type ErrorView } from "../../api/errors";
import { listScopeItems, type ScopeItem } from "../../api/prj";
import { getValidation, getWbsTree, requestWbsApproval, type Validation, type WbsTree } from "../../api/wbs";
import ErrorAlert from "../../components/ErrorAlert";
import { optionalText } from "../../lib/form";
import { formatDateTime } from "../../lib/format";
import { LIST_LIMIT_MAX } from "../chg/labels";
import { useProjectMembers } from "../chg/useProjectMembers";
import { parseProjectId } from "../prj/ProjectSettingsPage";
import {
  COMPLETION_FIELD_LABELS,
  FINDING_CODE_LABELS,
  FINDING_LEVEL_LABELS,
  READINESS_LABELS,
  WBS_STATUS_LABELS,
  hoursText,
  labelOf,
} from "./labels";
import ReviewPanel from "./ReviewPanel";
import ScopeBaselinesPanel from "./ScopeBaselinesPanel";

/** 合計の差異の許容幅（5.3：|子の集計 − 自分の見積| / 自分の見積 と比べる割合）を百分率で出す */
export function toleranceText(tolerance: number): string {
  return `${Math.round(tolerance * 10000) / 100}%`;
}

/** 422 WBS_NOT_READY の details（`{condition, count}`）を「前提：件数」の行にする。他のエラーは文言の details のまま */
export function describeApprovalError(error: unknown): ErrorView {
  const view = toErrorView(error);
  if (error instanceof ApiError && error.code === "WBS_NOT_READY") {
    const details: string[] = [];
    for (const detail of error.details) {
      if (typeof detail === "object" && detail !== null && "condition" in detail && "count" in detail) {
        const { condition, count } = detail as { condition: unknown; count: unknown };
        details.push(`${labelOf(READINESS_LABELS, String(condition))}：${String(count)} 件`);
      }
    }
    return { message: view.message, details };
  }
  return view;
}

function ApprovalRow({
  approval,
  onDecide,
  busy,
}: {
  approval: PendingApproval;
  onDecide: (approval: PendingApproval, decision: Decision, comment: string) => void;
  busy: boolean;
}) {
  const [comment, setComment] = useState("");
  return (
    <tr>
      <td>#{approval.id}</td>
      <td>{approval.requested_by_name}</td>
      <td>{formatDateTime(approval.requested_at)}</td>
      <td>
        <input aria-label={`申請 #${approval.id} へのコメント`} value={comment} onChange={(e) => setComment(e.target.value)} />
      </td>
      <td>
        <button type="button" disabled={busy} onClick={() => onDecide(approval, "APPROVE", comment)}>
          承認
        </button>
        <button type="button" disabled={busy} onClick={() => onDecide(approval, "REJECT", comment)}>
          差戻し
        </button>
      </td>
    </tr>
  );
}

export default function WbsReviewPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = parseProjectId(params.projectId);
  const [searchParams, setSearchParams] = useSearchParams();
  const reviewId = parseProjectId(searchParams.get("review") ?? undefined);
  const [me, setMe] = useState<Me | null>(null);
  const [tree, setTree] = useState<WbsTree | null>(null);
  const [validation, setValidation] = useState<Validation | null>(null);
  const [scopeItems, setScopeItems] = useState<ScopeItem[]>([]);
  const [pending, setPending] = useState<PendingApproval[]>([]);
  const [reloadKey, setReloadKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);
  const [approvalError, setApprovalError] = useState<ErrorView | null>(null);
  const { members, names } = useProjectMembers(projectId);

  const canApprove = projectId !== null && me !== null && hasProjectPermission(me, projectId, "wbs.approve");

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
          setError(toErrorView(e));
        }
      },
    );
    listScopeItems(projectId, { limit: LIST_LIMIT_MAX, offset: 0 }).then(
      (page) => {
        if (!cancelled) {
          setScopeItems(page.items);
        }
      },
      () => {
        // スコープ項目の名前が読めなければ #ID で出す
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
    Promise.all([getWbsTree(projectId), getValidation(projectId)]).then(
      ([treeResult, validationResult]) => {
        if (!cancelled) {
          setTree(treeResult);
          setValidation(validationResult);
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
  }, [projectId, reloadKey]);

  useEffect(() => {
    if (projectId === null || !canApprove) {
      setPending([]);
      return;
    }
    let cancelled = false;
    listPendingApprovals({ limit: LIST_LIMIT_MAX, offset: 0 }).then(
      (page) => {
        if (!cancelled) {
          setPending(page.items.filter((a) => a.project_id === projectId && a.target_type === "WBS"));
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setApprovalError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, canApprove, reloadKey]);

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  if (projectId === null) {
    return (
      <section data-screen-id="UI-WBS-003" className="wbs-review-page">
        <h2>WBSの検証・レビュー・承認</h2>
        <p role="alert">プロジェクトが見つかりません</p>
        <p>
          <Link to="/projects">プロジェクト一覧へ</Link>
        </p>
      </section>
    );
  }

  const canEdit = me !== null && hasProjectPermission(me, projectId, "wbs.edit");
  const canReview = me !== null && hasProjectPermission(me, projectId, "wbs.review");
  const scopeTitles = new Map(scopeItems.map((s) => [s.id, s.title]));
  const elements = tree?.elements ?? [];

  async function handleRequest() {
    if (projectId === null) {
      return;
    }
    setApprovalError(null);
    setNotice(null);
    setBusy(true);
    try {
      const submitted = await requestWbsApproval(projectId);
      setNotice(`承認を申請した（申請 #${submitted.id}。WBS の状態：${labelOf(WBS_STATUS_LABELS, submitted.wbs_status)}）`);
      reload();
    } catch (e) {
      setApprovalError(describeApprovalError(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleDecide(approval: PendingApproval, decision: Decision, comment: string) {
    setApprovalError(null);
    setNotice(null);
    setBusy(true);
    try {
      await decideApproval(approval.id, decision, optionalText(comment));
      setNotice(decision === "APPROVE" ? `申請 #${approval.id} を承認した（スコープベースラインの版を作った）` : `申請 #${approval.id} を差し戻した`);
    } catch (e) {
      setApprovalError(toErrorView(e));
    } finally {
      setBusy(false);
      // 判断の後も、判断の失敗（403 自分の申請・409 判断済み）の後も読み直す
      reload();
    }
  }

  return (
    <section data-screen-id="UI-WBS-003" className="wbs-review-page">
      <h2>WBSの検証・レビュー・承認</h2>
      <nav aria-label="行き先" className="page-links">
        <Link to={`/projects/${projectId}/wbs`}>WBS へ戻る</Link>
        <Link to="/approvals">承認待ち一覧</Link>
      </nav>
      <ErrorAlert error={error} />
      {notice !== null && <p role="status">{notice}</p>}
      {tree !== null && <p>WBS の状態：{labelOf(WBS_STATUS_LABELS, tree.wbs_status)}</p>}

      {validation !== null && (
        <section aria-labelledby="validation-heading">
          <h3 id="validation-heading">検証</h3>
          <p>
            エラー {validation.counts.errors} 件・警告 {validation.counts.warnings} 件・完了条件の欠け{" "}
            {validation.counts.missing_completion} 件（合計の差異の許容幅 {toleranceText(validation.tolerance)}）
          </p>
          <h4>100% ルール</h4>
          {validation.findings.length === 0 && <p>100% ルールの指摘はありません</p>}
          {validation.findings.length > 0 && (
            <table aria-label="100% ルールの検査の結果">
              <thead>
                <tr>
                  <th scope="col">重さ</th>
                  <th scope="col">判定</th>
                  <th scope="col">WBS ID</th>
                  <th scope="col">スコープ項目</th>
                  <th scope="col">自分の見積</th>
                  <th scope="col">下位の合計</th>
                </tr>
              </thead>
              <tbody>
                {validation.findings.map((f, i) => (
                  <tr key={`${f.code}-${f.element_id ?? "x"}-${f.scope_item_id ?? "x"}-${i}`} data-level={f.level}>
                    <td>{labelOf(FINDING_LEVEL_LABELS, f.level)}</td>
                    <td>{labelOf(FINDING_CODE_LABELS, f.code)}</td>
                    <td>
                      {f.element_id === null ? (
                        "—"
                      ) : (
                        <Link to={`/projects/${projectId}/wbs/${f.element_id}`}>{f.wbs_code ?? `#${f.element_id}`}</Link>
                      )}
                    </td>
                    <td>{f.scope_item_id === null ? "—" : (scopeTitles.get(f.scope_item_id) ?? `#${f.scope_item_id}`)}</td>
                    <td>{hoursText(f.own_estimate_hours)}</td>
                    <td>{hoursText(f.lower_hours)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <h4>成果物・完了条件の欠けたワークパッケージ</h4>
          {validation.missing_completion.length === 0 && <p>欠けたワークパッケージはありません</p>}
          {validation.missing_completion.length > 0 && (
            <ul aria-label="成果物・完了条件の欠けたワークパッケージ">
              {validation.missing_completion.map((m) => (
                <li key={m.element_id}>
                  <Link to={`/projects/${projectId}/wbs/${m.element_id}`}>{m.wbs_code}</Link>：
                  {m.missing.map((field) => labelOf(COMPLETION_FIELD_LABELS, field)).join("・")}が未定義
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      <ReviewPanel
        projectId={projectId}
        reviewId={reviewId}
        onOpen={(id) => setSearchParams({ review: String(id) })}
        elements={elements}
        members={members}
        names={names}
        canEdit={canEdit}
        canReview={canReview}
        onChanged={reload}
      />

      <section aria-labelledby="approval-heading">
        <h3 id="approval-heading">承認</h3>
        <ErrorAlert error={approvalError} />
        {canEdit && (
          <>
            <p>
              申請の前提：成果物・完了条件の欠けた WP・未対応の指摘・100% ルールのエラーが 0 件。申請すると開いているレビューは終わる。
            </p>
            <button type="button" disabled={busy} onClick={() => void handleRequest()}>
              承認を申請する
            </button>
          </>
        )}
        {canApprove && pending.length === 0 && <p>このプロジェクトの WBS で判断を待つ申請はありません</p>}
        {canApprove && pending.length > 0 && (
          <table aria-label="判断を待つ WBS の申請">
            <thead>
              <tr>
                <th scope="col">申請</th>
                <th scope="col">申請者</th>
                <th scope="col">申請した日時</th>
                <th scope="col">コメント</th>
                <th scope="col">判断</th>
              </tr>
            </thead>
            <tbody>
              {pending.map((a) => (
                <ApprovalRow key={a.id} approval={a} busy={busy} onDecide={(x, d, c) => void handleDecide(x, d, c)} />
              ))}
            </tbody>
          </table>
        )}
      </section>

      <ScopeBaselinesPanel projectId={projectId} reloadKey={reloadKey} />
    </section>
  );
}
