// UI-CMN-003 承認待ち一覧（設計書 7.1 のルート /approvals。API-WBS-020・019・CMN-008）。
// 自分が判断できる承認待ち（WBS と変更要求）を申請の古い順に全件並べ（A-49 の 2：total まで頁を読み足す）、その場で承認・差戻し（却下）する。
// 判断の権限（WBS は wbs.approve、変更要求は change.approve）が無ければ判断の部品を出さない（7.2。サーバでも判定する）
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  canDecide,
  decideApproval,
  getMe,
  listPendingApprovals,
  type ApprovalTargetType,
  type Decision,
  type Me,
  type PendingApproval,
} from "../../api/cmn";
import { describeError } from "../../api/errors";
import { fetchAllPages } from "../../api/pages";
import { formatDateTime } from "../../lib/format";

interface TargetView {
  label: string;
  /** 差戻しの呼び名（WBS-21 は差戻し、CHG-03 は却下） */
  rejectLabel: string;
  /** 申請の中身を見る画面（UI-WBS-003・UI-CHG-001） */
  path: (projectId: number) => string;
}

const TARGETS: Record<ApprovalTargetType, TargetView> = {
  WBS: { label: "WBS", rejectLabel: "差戻し", path: (projectId) => `/projects/${projectId}/wbs-review` },
  CHANGE_REQUEST: {
    label: "変更要求",
    rejectLabel: "却下",
    path: (projectId) => `/projects/${projectId}/change-requests`,
  },
};

function DecisionCell({
  approval,
  busy,
  onDecide,
}: {
  approval: PendingApproval;
  busy: boolean;
  onDecide: (decision: Decision, comment: string | null) => void;
}) {
  const [comment, setComment] = useState("");
  const target = TARGETS[approval.target_type];
  const commentValue = comment.trim() === "" ? null : comment;
  return (
    <td>
      <input
        aria-label={`申請 ${approval.id} のコメント`}
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        disabled={busy}
      />
      <button type="button" disabled={busy} onClick={() => onDecide("APPROVE", commentValue)}>
        承認
      </button>
      <button type="button" disabled={busy} onClick={() => onDecide("REJECT", commentValue)}>
        {target.rejectLabel}
      </button>
    </td>
  );
}

export default function ApprovalsPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [approvals, setApprovals] = useState<PendingApproval[] | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    Promise.all([getMe(), fetchAllPages((page) => listPendingApprovals(page))]).then(
      ([meResult, items]) => {
        if (!cancelled) {
          setMe(meResult);
          setApprovals(items);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setApprovals(null);
          setLoadError(describeError(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  async function handleDecide(approval: PendingApproval, decision: Decision, comment: string | null) {
    const target = TARGETS[approval.target_type];
    setBusyId(approval.id);
    setNotice(null);
    setActionError(null);
    try {
      await decideApproval(approval.id, decision, comment);
      const verb = decision === "APPROVE" ? "承認" : target.rejectLabel;
      setNotice(`${approval.project_code} の${target.label}の申請を${verb}しました`);
    } catch (e: unknown) {
      setActionError(describeError(e));
    } finally {
      setBusyId(null);
      // 判断した申請・他の人が先に判断した申請（409）を一覧から外すため、読み直す
      setReloadKey((key) => key + 1);
    }
  }

  return (
    <section data-screen-id="UI-CMN-003" className="approvals-page">
      <h2>承認待ち一覧</h2>
      {notice !== null && <p role="status">{notice}</p>}
      {loadError !== null && <p role="alert">{loadError}</p>}
      {actionError !== null && <p role="alert">{actionError}</p>}
      {approvals !== null && me !== null && approvals.length === 0 && <p>承認待ちはありません</p>}
      {approvals !== null && me !== null && approvals.length > 0 && (
        <>
          <p>{`全 ${approvals.length} 件`}</p>
          <table aria-label="承認待ちの一覧">
            <thead>
              <tr>
                <th scope="col">プロジェクト</th>
                <th scope="col">対象</th>
                <th scope="col">申請者</th>
                <th scope="col">申請日時</th>
                <th scope="col">判断</th>
              </tr>
            </thead>
            <tbody>
              {approvals.map((approval) => {
                const target = TARGETS[approval.target_type];
                return (
                  <tr key={approval.id}>
                    <td>
                      {approval.project_code} {approval.project_name}
                    </td>
                    <td>
                      <Link to={target.path(approval.project_id)}>{target.label}</Link>
                    </td>
                    <td>{approval.requested_by_name}</td>
                    <td>{formatDateTime(approval.requested_at)}</td>
                    {canDecide(me, approval) ? (
                      <DecisionCell
                        approval={approval}
                        busy={busyId !== null}
                        onDecide={(decision, comment) => void handleDecide(approval, decision, comment)}
                      />
                    ) : (
                      <td>判断の権限がありません</td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
