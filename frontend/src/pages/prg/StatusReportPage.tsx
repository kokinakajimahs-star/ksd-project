// UI-PRG-001 進捗入力（報告日）（設計書 7.1 のルート /projects/:projectId/status-reports/:reportId）。
// PRG-01・02・03・05・17、EFF-06。報告日の一覧と作成（API-PRG-001・002）、報告日の活動の進捗の一覧（API-PRG-006 の行）と
// 入力（API-PRG-003）・算定方式（API-PRG-005）・算定項目（API-PRG-004）、報告日の確定（API-PRG-007）を 1 つの画面に並べる。
// 権限の無い操作の部品は出さない（7.2。GET /api/v1/me の権限で決める。サーバでも必ず判定する）
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { getMe, hasProjectPermission, type Me } from "../../api/cmn";
import { toErrorView, type ErrorView } from "../../api/errors";
import {
  finalizeStatusReport,
  getLightningLine,
  listStatusReports,
  type LightningLine,
  type LightningRow,
  type StatusReport,
} from "../../api/prg";
import ErrorAlert from "../../components/ErrorAlert";
import { JUDGEMENT_LABELS, judgementColor } from "../../components/gantt/scale";
import { formatDateTime } from "../../lib/format";
import { parseProjectId } from "../prj/ProjectSettingsPage";
import CreateReportForm from "./CreateReportForm";
import EntryEditor from "./EntryEditor";
import { newestFirst, progressMethodLabel, reportStatusLabel, signedDays } from "./labels";
import { LIST_LIMIT_MAX } from "./limits";

/** 残工数の表示：入力した値か自動（BR-10。EFF-06：自動か入力かを区別して出す） */
export function remainingText(hours: number | null): string {
  return hours === null ? "自動" : `${hours} 時間（入力）`;
}

function isActivityRow(row: LightningRow): row is LightningRow & { activity_id: number } {
  return row.activity_id !== null;
}

export default function StatusReportPage() {
  const params = useParams<{ projectId: string; reportId: string }>();
  const projectId = parseProjectId(params.projectId);
  // 報告日の ID もプロジェクトの ID と同じ形（正の整数）
  const reportId = parseProjectId(params.reportId);
  const navigate = useNavigate();
  const [me, setMe] = useState<Me | null>(null);
  const [reports, setReports] = useState<StatusReport[]>([]);
  const [line, setLine] = useState<LightningLine | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [error, setError] = useState<ErrorView | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [finalizing, setFinalizing] = useState(false);

  const valid = projectId !== null && reportId !== null;

  // 自分の権限（画面を開いたときに 1 回）
  useEffect(() => {
    if (projectId === null || !valid) {
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
    return () => {
      cancelled = true;
    };
  }, [projectId, valid]);

  // 報告日の一覧（作る・確定すると読み直す）
  useEffect(() => {
    if (projectId === null || !valid) {
      return;
    }
    let cancelled = false;
    listStatusReports(projectId, { limit: LIST_LIMIT_MAX }).then(
      (result) => {
        if (!cancelled) {
          setReports(result.items);
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
  }, [projectId, valid, reloadKey]);

  // 報告日の活動の行（API-PRG-006。OPEN は今の計画と入力から、確定済みはスナップショットから）
  useEffect(() => {
    if (projectId === null || reportId === null) {
      return;
    }
    let cancelled = false;
    getLightningLine(projectId, reportId).then(
      (result) => {
        if (!cancelled) {
          setLine(result);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setLine(null);
          setError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, reportId, reloadKey]);

  // 別の報告日へ移ったら、開いていた編集と確定の確認を閉じる
  useEffect(() => {
    setEditingId(null);
    setConfirming(false);
  }, [reportId]);

  const ordered = useMemo(() => newestFirst(reports), [reports]);
  const editingRow = useMemo(() => {
    if (line === null || editingId === null) {
      return null;
    }
    const row = line.rows.find((r) => r.activity_id === editingId);
    return row !== undefined && isActivityRow(row) ? row : null;
  }, [line, editingId]);

  if (projectId === null || reportId === null) {
    return (
      <section data-screen-id="UI-PRG-001" className="progress-page">
        <h2>進捗入力（報告日）</h2>
        <p role="alert">{projectId === null ? "プロジェクトが見つかりません" : "報告日が見つかりません"}</p>
        <p>
          <Link to={projectId === null ? "/projects" : `/projects/${projectId}/gantt`}>
            {projectId === null ? "プロジェクト一覧へ" : "ガントチャートへ"}
          </Link>
        </p>
      </section>
    );
  }

  // 別の報告日へ移った直後は、前の報告日の行を出さない
  const current = line !== null && line.report.id === reportId ? line : null;
  const report = current?.report ?? reports.find((r) => r.id === reportId) ?? null;
  const isOpen = report !== null && report.status === "OPEN";
  const can = (permission: string) => me !== null && hasProjectPermission(me, projectId, permission);
  const canManage = can("status_report.manage");
  const canEnterAll = can("progress.enter_all");
  const canEnter = canEnterAll || can("progress.enter_own");
  const canEditMethod = can("schedule.edit");
  const canEditRows = isOpen && (canEnter || canEditMethod);

  function saved(message: string) {
    setError(null);
    setNotice(message);
    setReloadKey((k) => k + 1);
  }

  function handleCreated(created: StatusReport) {
    setError(null);
    setNotice(`報告日 ${created.report_date} を作った`);
    setReloadKey((k) => k + 1);
    navigate(`/projects/${projectId}/status-reports/${created.id}`);
  }

  async function handleFinalize() {
    if (projectId === null || reportId === null) {
      return;
    }
    setFinalizing(true);
    setError(null);
    try {
      const result = await finalizeStatusReport(projectId, reportId);
      setConfirming(false);
      setEditingId(null);
      setNotice(`報告日 ${result.report.report_date} を確定し、スナップショット（活動 ${result.item_count} 件）を作った`);
      setReloadKey((k) => k + 1);
    } catch (err) {
      setError(toErrorView(err));
    } finally {
      setFinalizing(false);
    }
  }

  return (
    <section data-screen-id="UI-PRG-001" className="progress-page">
      <h2>進捗入力（報告日）</h2>
      <nav aria-label="行き先" className="page-links">
        <Link to="/projects">プロジェクト一覧へ</Link>
        <Link to={`/projects/${projectId}/gantt`}>ガントチャート</Link>
        <Link to={`/projects/${projectId}/status-reports/${reportId}/delays`}>遅延分析</Link>
        <Link to={`/projects/${projectId}/status-reports/${reportId}/meeting`}>定例会議レポート</Link>
      </nav>
      <ErrorAlert error={error} />
      {notice !== null && <p role="status">{notice}</p>}

      <section aria-label="報告日の操作" className="report-header">
        <label>
          報告日
          <select
            value={String(reportId)}
            onChange={(e) => navigate(`/projects/${projectId}/status-reports/${e.target.value}`)}
          >
            {!ordered.some((r) => r.id === reportId) && (
              <option value={String(reportId)}>
                {report === null ? "（読み込み中）" : `${report.report_date}（${reportStatusLabel(report.status)}）`}
              </option>
            )}
            {ordered.map((r) => (
              <option key={r.id} value={r.id}>
                {`${r.report_date}（${reportStatusLabel(r.status)}）`}
              </option>
            ))}
          </select>
        </label>
        {report !== null && (
          <p>
            状態：{reportStatusLabel(report.status)}
            {report.finalized_at !== null && `（${formatDateTime(report.finalized_at)} に確定）`}
            {current !== null && `／許容幅 ${current.tolerance_days} 日`}
          </p>
        )}
        {canManage && <CreateReportForm projectId={projectId} onCreated={handleCreated} />}
        {canManage && isOpen && !confirming && (
          <button type="button" onClick={() => setConfirming(true)}>
            報告日を確定する
          </button>
        )}
        {canManage && isOpen && confirming && (
          <div role="group" aria-label="確定の確認">
            <p>
              確定すると、全ての活動の値・進捗ポイント・判定をスナップショットに写し、この報告日へは入力できなくなる（戻せない）。
            </p>
            <button type="button" onClick={() => void handleFinalize()} disabled={finalizing}>
              確定する
            </button>
            <button type="button" onClick={() => setConfirming(false)} disabled={finalizing}>
              やめる
            </button>
          </div>
        )}
      </section>

      {current !== null && (
        <section aria-labelledby="entries-heading" className="entries">
          <h3 id="entries-heading">活動の進捗</h3>
          {current.restricted && <p className="note">自分の割当の活動だけを表示している</p>}
          {!isOpen && <p className="note">確定済みの報告日は入力できない（スナップショットの値を表示している）</p>}
          {isOpen && canEnter && !canEnterAll && (
            <p className="note">書けるのは自分の割当の活動の進捗だけ</p>
          )}
          {current.rows.length === 0 ? (
            <p>活動がありません</p>
          ) : (
            <table aria-label="活動の進捗">
              <thead>
                <tr>
                  <th scope="col">WBS ID</th>
                  <th scope="col">活動</th>
                  <th scope="col">算定方式</th>
                  <th scope="col">進捗率</th>
                  <th scope="col">実績開始日</th>
                  <th scope="col">実績終了日</th>
                  <th scope="col">残工数</th>
                  <th scope="col">遅延理由</th>
                  <th scope="col">完了見込み日</th>
                  <th scope="col">ずれ（日）</th>
                  <th scope="col">判定</th>
                  {canEditRows && <th scope="col">操作</th>}
                </tr>
              </thead>
              <tbody>
                {current.rows.map((row) => (
                  <tr key={`${row.row_index}-${row.activity_id ?? "x"}`} data-judgement={row.judgement}>
                    <td>{row.wbs_code}</td>
                    <td>{row.name}</td>
                    <td>{progressMethodLabel(row.progress_method)}</td>
                    <td>{row.percent_complete === null ? "未算定（入力が要る）" : `${row.percent_complete}%`}</td>
                    <td>{row.actual_start ?? "—"}</td>
                    <td>{row.actual_finish ?? "—"}</td>
                    <td>{remainingText(row.remaining_hours)}</td>
                    <td>{row.delay_reason ?? "—"}</td>
                    <td>{row.forecast_finish ?? "—"}</td>
                    <td>{signedDays(row.deviation)}</td>
                    <td>
                      <span style={{ color: judgementColor(row.judgement) }}>●</span> {JUDGEMENT_LABELS[row.judgement]}
                    </td>
                    {canEditRows && (
                      <td>
                        {isActivityRow(row) && (
                          <button type="button" onClick={() => setEditingId(row.activity_id)}>
                            {`${row.name} を入力する`}
                          </button>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {canEditRows && editingRow !== null && (
        <EntryEditor
          key={`${reportId}-${editingRow.activity_id}`}
          projectId={projectId}
          reportId={reportId}
          row={editingRow}
          canEnter={canEnter}
          canEditMethod={canEditMethod}
          onSaved={saved}
          onClose={() => setEditingId(null)}
        />
      )}
    </section>
  );
}
