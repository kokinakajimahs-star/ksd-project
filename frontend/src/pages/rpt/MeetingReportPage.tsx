// UI-RPT-001 定例会議レポート（設計書 7.1 のルート /projects/:projectId/status-reports/:reportId/meeting。7.4）。RPT-01。
// 定例会議用レポートのデータ（API-RPT-001。RULE-RPT-001）を A3 横の 1 枚に並べる：上段にガント＋イナズマ線（報告日の前後 4 週）、
// 下段に遅延一覧・マイルストーンの状況・主要 KPI。印刷用の CSS（@page A3 landscape）を持つ。
// 共有リンク（API-RPT-002・003。RPT-03）の部品は share.manage を持つ人にだけ出す（7.2。サーバでも必ず判定する）
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { hasProjectPermission } from "../../api/cmn";
import { toErrorView, type ErrorView } from "../../api/errors";
import { getMeetingReport, type MeetingReport } from "../../api/rpt";
import ErrorAlert from "../../components/ErrorAlert";
import type { GanttUnit } from "../../components/gantt/scale";
import { useMe } from "../chg/useMe";
import { parseProjectId } from "../prj/ProjectSettingsPage";
import MeetingReportView, { UnitSelect } from "./MeetingReportView";
import ShareLinkPanel from "./ShareLinkPanel";

export default function MeetingReportPage() {
  const params = useParams<{ projectId: string; reportId: string }>();
  const projectId = parseProjectId(params.projectId);
  // 報告日の ID もプロジェクトの ID と同じ形（正の整数）
  const reportId = parseProjectId(params.reportId);
  // ID の形が正しくなければ、権限も読まない（画面は「見つかりません」だけを出す）
  const { me, meError } = useMe(reportId === null ? null : projectId);
  const [report, setReport] = useState<MeetingReport | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);
  const [unit, setUnit] = useState<GanttUnit>("day");

  useEffect(() => {
    if (projectId === null || reportId === null) {
      return;
    }
    let cancelled = false;
    setError(null);
    getMeetingReport(projectId, reportId).then(
      (result) => {
        if (!cancelled) {
          setReport(result);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setReport(null);
          setError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, reportId]);

  if (projectId === null || reportId === null) {
    return (
      <section data-screen-id="UI-RPT-001" className="meeting-report-page">
        <h2>定例会議レポート</h2>
        <p role="alert">{projectId === null ? "プロジェクトが見つかりません" : "報告日が見つかりません"}</p>
        <p>
          <Link to="/projects">プロジェクト一覧へ</Link>
        </p>
      </section>
    );
  }

  const canShare = me !== null && hasProjectPermission(me, projectId, "share.manage");

  return (
    <section data-screen-id="UI-RPT-001" className="meeting-report-page">
      <h2>定例会議レポート</h2>
      <nav aria-label="行き先" className="page-links no-print">
        <Link to={`/projects/${projectId}/status-reports/${reportId}`}>進捗入力へ</Link>
        <Link to={`/projects/${projectId}/status-reports/${reportId}/delays`}>遅延分析</Link>
        <Link to={`/projects/${projectId}/gantt`}>ガントチャート</Link>
      </nav>
      <div role="group" aria-label="表示の設定" className="report-controls no-print">
        <UnitSelect unit={unit} onChange={setUnit} />
        <button type="button" onClick={() => window.print()} disabled={report === null}>
          印刷する
        </button>
      </div>
      <ErrorAlert error={error ?? meError} />

      {report !== null && <MeetingReportView report={report} unit={unit} />}

      {canShare && <ShareLinkPanel projectId={projectId} />}
    </section>
  );
}
