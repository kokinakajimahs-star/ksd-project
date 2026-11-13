// UI-RPT-002 共有ビュー（ログイン不要）（設計書 7.1 のルート /shared/:token）。RPT-03。
// 共有リンク（API-RPT-004。認証を要らない唯一の API。6.1）で、リンクの範囲だけを読むだけで見せる：
// GANTT は最新の進捗・最新の基準のガント、REPORT は最新の報告日の定例会議レポート（7.4。報告日が無ければ無い旨）。
// 無い・失効した・期限の切れたリンクはサーバがどれも同じ 404 で返す（どれに当たったかは返さない）。画面もサーバの文言と
// 1 つの案内だけを出し、理由を見分けない。書き込みの部品は置かない。
// リンクは転送されうるので、サーバは利用者の表示名などを返さず、ガントの担当者は所属（組織／チーム）で返す（CR-019）。
// 画面はそれをバーの横にそのまま出し、担当を所属で示していることを書き添える
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { ApiError } from "../../api/client";
import { toErrorView, type ErrorView } from "../../api/errors";
import { getSharedView, type SharedView } from "../../api/rpt";
import ErrorAlert from "../../components/ErrorAlert";
import GanttChart from "../../components/gantt/GanttChart";
import type { GanttUnit } from "../../components/gantt/scale";
import { formatDateTime } from "../../lib/format";
import MeetingReportView, { UnitSelect } from "./MeetingReportView";
import { SHARE_SCOPE_LABELS, SHARE_UNAVAILABLE_HINT } from "./labels";

/** 共有リンクで閲覧できない（API-RPT-004 の 404。無い・失効・期限切れのどれかで、どれかは見分けない） */
export function isShareUnavailable(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

function toSharedError(error: unknown): ErrorView {
  const view = toErrorView(error);
  return isShareUnavailable(error) ? { message: view.message, details: [SHARE_UNAVAILABLE_HINT] } : view;
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
          <p className="note" data-note="assignee-affiliation">
            担当は所属（組織／チーム）で示す（共有ビューでは個人の名前を出さない）
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
