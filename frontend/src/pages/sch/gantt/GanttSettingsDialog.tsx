// UI-SCH-002 の表示の設定のダイアログ（設計書 7.3。CR-027）。行き先のリンクの並びの「表示の設定」から開く。
// 表示の単位・比べる基準（API-SCH-012 の版）・報告日・重ねる過去の線（確定済みの報告日。API-PRG-008）を選ぶと、その場で図に効く。
// 報告日を作る（API-PRG-001）は status_report.manage、日程を計算し直す（API-SCH-008）は schedule.edit を持つ人にだけ出す
// （7.2。サーバでも必ず判定する）
import { useState } from "react";

import { toErrorView, type ErrorView } from "../../../api/errors";
import type { StatusReport } from "../../../api/prg";
import { calculateSchedule, type ScheduleBaseline, type ScheduleResult } from "../../../api/sch";
import Dialog from "../../../components/Dialog";
import ErrorAlert from "../../../components/ErrorAlert";
import { UNIT_LABELS, type GanttUnit } from "../../../components/gantt/scale";
import CreateReportForm from "../../prg/CreateReportForm";

const UNITS: GanttUnit[] = ["day", "week", "month"];

export interface GanttSettingsDialogProps {
  projectId: number;
  unit: GanttUnit;
  onUnit: (unit: GanttUnit) => void;
  baselines: ScheduleBaseline[];
  /** 比べる基準の版。null はサーバの既定（最新の版） */
  baselineId: number | null;
  onBaseline: (id: number | null) => void;
  reports: StatusReport[];
  reportId: number | null;
  onReport: (id: number | null) => void;
  pastReportIds: number[];
  onTogglePast: (id: number, checked: boolean) => void;
  canManageReports: boolean;
  /** 作った報告日（画面が一覧に足して選ぶ） */
  onReportCreated: (report: StatusReport) => void;
  canEditSchedule: boolean;
  /** 日程を計算し直した（画面がガントを読み直す） */
  onCalculated: () => void;
  onClose: () => void;
}

export default function GanttSettingsDialog({
  projectId,
  unit,
  onUnit,
  baselines,
  baselineId,
  onBaseline,
  reports,
  reportId,
  onReport,
  pastReportIds,
  onTogglePast,
  canManageReports,
  onReportCreated,
  canEditSchedule,
  onCalculated,
  onClose,
}: GanttSettingsDialogProps) {
  const [created, setCreated] = useState<StatusReport | null>(null);
  const [calculating, setCalculating] = useState(false);
  const [calculated, setCalculated] = useState<ScheduleResult | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);
  const finalized = reports.filter((r) => r.status === "FINALIZED");

  async function handleCalculate() {
    setCalculating(true);
    setError(null);
    try {
      setCalculated(await calculateSchedule(projectId));
      onCalculated();
    } catch (e) {
      setError(toErrorView(e));
    } finally {
      setCalculating(false);
    }
  }

  return (
    <Dialog title="表示の設定" onClose={onClose}>
      <div className="settings-fields">
        <label>
          表示の単位
          <select value={unit} onChange={(e) => onUnit(e.target.value as GanttUnit)}>
            {UNITS.map((u) => (
              <option key={u} value={u}>
                {UNIT_LABELS[u]}
              </option>
            ))}
          </select>
        </label>
        <label>
          比べる基準
          <select
            value={baselineId === null ? "" : String(baselineId)}
            onChange={(e) => onBaseline(e.target.value === "" ? null : Number(e.target.value))}
          >
            <option value="">最新の版（既定）</option>
            {baselines.map((b) => (
              <option key={b.id} value={b.id}>
                {`第 ${b.version} 版 ${b.name}`}
              </option>
            ))}
          </select>
        </label>
        <label>
          報告日
          <select
            value={reportId === null ? "" : String(reportId)}
            onChange={(e) => onReport(e.target.value === "" ? null : Number(e.target.value))}
          >
            <option value="">（イナズマ線を出さない）</option>
            {reports.map((r) => (
              <option key={r.id} value={r.id}>
                {`${r.report_date}${r.status === "FINALIZED" ? "（確定）" : ""}`}
              </option>
            ))}
          </select>
        </label>
      </div>

      {finalized.length > 0 && (
        <fieldset className="past-lines">
          <legend>重ねる過去の線（確定済みの報告日）</legend>
          {finalized.map((r) => (
            <label key={r.id}>
              <input
                type="checkbox"
                checked={pastReportIds.includes(r.id)}
                onChange={(e) => onTogglePast(r.id, e.target.checked)}
              />
              {r.report_date}
            </label>
          ))}
        </fieldset>
      )}

      {canManageReports && (
        <CreateReportForm
          projectId={projectId}
          onCreated={(report) => {
            setCreated(report);
            onReportCreated(report);
          }}
        />
      )}
      {created !== null && <p role="status">報告日 {created.report_date} を作って選んだ。進捗入力で進捗を入れる</p>}

      {canEditSchedule && (
        <div className="settings-actions">
          <button type="button" onClick={() => void handleCalculate()} disabled={calculating}>
            日程を計算し直す
          </button>
        </div>
      )}
      <ErrorAlert error={error} />
      {calculated !== null && (
        <p role="status">
          日程を計算し直した。プロジェクトの終了日 {calculated.project_finish_date ?? "—"}・最小のフロート{" "}
          {calculated.min_total_float ?? "—"} 日・クリティカルな活動 {calculated.critical_path.length} 件
        </p>
      )}
    </Dialog>
  );
}
