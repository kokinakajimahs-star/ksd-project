// UI-RPT-002 共有ビュー（ログイン不要）（設計書 7.1 のルート /shared/:token）。RPT-03。
// 共有リンク（API-RPT-004。認証を要らない唯一の API。6.1）で、リンクの範囲だけを読むだけで見せる：
// GANTT は最新の進捗・最新の基準のガント、REPORT は最新の報告日の定例会議レポート（7.4。報告日が無ければ無い旨）。
// 無い・失効した・期限の切れたリンクはサーバが 404 で返し、その理由（details の reason）を出す。書き込みの部品は置かない
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { ApiError } from "../../api/client";
import { toErrorView, type ErrorView } from "../../api/errors";
import { getSharedView, type SharedView, type ShareLinkFailure } from "../../api/rpt";
import ErrorAlert from "../../components/ErrorAlert";
import GanttChart from "../../components/gantt/GanttChart";
import type { GanttUnit } from "../../components/gantt/scale";
import { formatDateTime } from "../../lib/format";
import MeetingReportView, { UnitSelect } from "./MeetingReportView";
import { SHARE_FAILURE_LABELS, SHARE_SCOPE_LABELS } from "./labels";

/** 閲覧できない理由（404 の details の reason）を読む。無ければ null */
export function shareFailureReason(error: unknown): ShareLinkFailure | null {
  if (!(error instanceof ApiError) || error.status !== 404) {
    return null;
  }
  for (const detail of error.details) {
    if (typeof detail === "object" && detail !== null && "reason" in detail) {
      const reason = (detail as { reason: unknown }).reason;
      if (reason === "NOT_FOUND" || reason === "REVOKED" || reason === "EXPIRED") {
        return reason;
      }
    }
  }
  return null;
}

function toSharedError(error: unknown): ErrorView {
  const reason = shareFailureReason(error);
  const view = toErrorView(error);
  return reason === null ? view : { message: view.message, details: [SHARE_FAILURE_LABELS[reason]] };
}

export default function SharedViewPage() {
  const { token } = useParams<{ token: string }>();
  const [view, setView] = useState<SharedView | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);
  const [unit, setUnit] = useState<GanttUnit>("day");

  useEffect(() => {
    if (token === undefined || token === "") {
      return;
    }
    let cancelled = false;
    getSharedView(token).then(
      (result) => {
        if (!cancelled) {
          setView(result);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setView(null);
          setError(toSharedError(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [token]);

  return (
    <section data-screen-id="UI-RPT-002" className="shared-view-page">
      <h2>共有ビュー</h2>
      <ErrorAlert error={error} />

      {view !== null && (
        <>
          <p className="shared-meta">
            {view.project.code} {view.project.name}・{SHARE_SCOPE_LABELS[view.scope]}・有効期限{" "}
            {formatDateTime(view.expires_at)}（読むだけ）
          </p>
          <div role="group" aria-label="表示の設定" className="no-print">
            <UnitSelect unit={unit} onChange={setUnit} />
          </div>

          {view.scope === "GANTT" &&
            (view.gantt === null ? (
              <p>ガントのデータがありません</p>
            ) : (
              <section aria-labelledby="shared-gantt-heading">
                <h3 id="shared-gantt-heading">ガントチャート</h3>
                <GanttChart gantt={view.gantt} unit={unit} />
              </section>
            ))}

          {view.scope === "REPORT" &&
            (view.meeting_report === null ? (
              <p>報告日がまだ無いため、定例会議レポートはありません</p>
            ) : (
              <MeetingReportView report={view.meeting_report} unit={unit} />
            ))}
        </>
      )}
    </section>
  );
}
