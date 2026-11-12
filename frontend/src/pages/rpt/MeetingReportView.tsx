// 定例会議レポートの本体（設計書 7.4）。UI-RPT-001 と、REPORT の共有ビュー（UI-RPT-002）が同じものを出す。
// 上段：ガント＋イナズマ線（報告日の前後 4 週）、下段左：遅延一覧（ずれの大きい順・遅延理由・完了見込み）、
// 下段中：マイルストーンの状況（予定日・見込み日・ずれ）、下段右：主要 KPI。値の並び・計算はサーバ（RULE-RPT-001）のまま出す
import type { Judgement } from "../../api/prg";
import type { MeetingReport, ReportKpi } from "../../api/rpt";
import GanttChart from "../../components/gantt/GanttChart";
import {
  HEADER_HEIGHT,
  judgementColor,
  JUDGEMENT_LABELS,
  ROW_HEIGHT,
  UNIT_LABELS,
  type GanttUnit,
} from "../../components/gantt/scale";
import { slipText } from "../prg/DelaysPage";
import { reportStatusLabel, signedDays } from "../prg/labels";
import { hoursText, MEETING_REPORT_CSS, percentText, signedHoursText, sourceLabel } from "./labels";

const UNITS: GanttUnit[] = ["day", "week", "month"];
/** 凡例に出す判定（数える上の判定。完了・未着手（予定内）は予定どおりの色。5.6・7.3） */
const LEGEND: Judgement[] = ["BEHIND", "ON_SCHEDULE", "AHEAD", "NOT_COMPUTED"];

/** ガントの表示の単位（7.3：1 日あたりの横幅だけを変える）。印刷には出さない */
export function UnitSelect({ unit, onChange }: { unit: GanttUnit; onChange: (unit: GanttUnit) => void }) {
  return (
    <label className="no-print">
      表示の単位
      <select value={unit} onChange={(e) => onChange(e.target.value as GanttUnit)}>
        {UNITS.map((u) => (
          <option key={u} value={u}>
            {UNIT_LABELS[u]}
          </option>
        ))}
      </select>
    </label>
  );
}

function KpiList({ kpi }: { kpi: ReportKpi }) {
  return (
    <dl aria-label="主要KPIの値" className="report-kpi">
      <dt>全体の進捗率（見積で重み付け）</dt>
      <dd data-kpi="overall_progress">{percentText(kpi.overall_progress)}</dd>
      <dt>遅れの件数</dt>
      <dd data-kpi="behind_count">{`${kpi.behind_count} 件`}</dd>
      <dt>EAC と基準の差</dt>
      <dd data-kpi="eac_variance_hours">
        {kpi.eac_variance_hours === null
          ? kpi.baseline_hours === null
            ? "基準が無いため求められない"
            : "求められない"
          : signedHoursText(kpi.eac_variance_hours)}
        {kpi.eac_hours !== null &&
          `（EAC ${hoursText(kpi.eac_hours)}${kpi.baseline_hours === null ? "" : ` ／ 基準 ${hoursText(kpi.baseline_hours)}`}）`}
      </dd>
      <dt>クリティカルパスの最小フロート</dt>
      <dd data-kpi="min_critical_float">
        {kpi.min_critical_float === null ? "クリティカルな活動が無い" : `${kpi.min_critical_float} 稼働日`}
      </dd>
      <dt>是正措置の期限超過</dt>
      <dd data-kpi="overdue_action_count">{`${kpi.overdue_action_count} 件`}</dd>
    </dl>
  );
}

export default function MeetingReportView({ report, unit }: { report: MeetingReport; unit: GanttUnit }) {
  const missing = report.lightning.missing_activity_ids.length;
  return (
    <div className="meeting-report-sheet">
      <style>{MEETING_REPORT_CSS}</style>
      <p className="report-meta">
        報告日 {report.report_date}（{reportStatusLabel(report.status)}）・値：{sourceLabel(report.source)}・表示の範囲{" "}
        {report.window.start}〜{report.window.end}
      </p>
      {report.restricted && <p className="note">自分の割当の活動だけを表示している（主要 KPI は出さない）</p>}

      <section aria-labelledby="report-gantt-heading" className="report-upper">
        <h3 id="report-gantt-heading">ガント・イナズマ線（報告日の前後 4 週）</h3>
        {/* 紙の 1 枚に全ての行を載せるため、縦の仮想化をせずに全ての行を描く */}
        <GanttChart
          gantt={report.gantt}
          unit={unit}
          reportLightning={report.lightning}
          fixedRange={report.window}
          viewportHeight={HEADER_HEIGHT + report.gantt.rows.length * ROW_HEIGHT}
        />
        <ul aria-label="イナズマ線の凡例" className="gantt-legend">
          {LEGEND.map((j) => (
            <li key={j}>
              <span style={{ color: judgementColor(j) }} aria-hidden="true">
                ●
              </span>{" "}
              {JUDGEMENT_LABELS[j]}
            </li>
          ))}
        </ul>
        {missing > 0 && <p className="note">{`今の計画に無い活動の頂点 ${missing} 件は描いていない`}</p>}
      </section>

      <div className="report-lower">
        <section aria-labelledby="report-delays-heading">
          <h3 id="report-delays-heading">遅延一覧</h3>
          {report.delays.length === 0 ? (
            <p>遅延している活動はありません</p>
          ) : (
            <table aria-label="遅延一覧">
              <thead>
                <tr>
                  <th scope="col">WBS ID</th>
                  <th scope="col">活動</th>
                  <th scope="col">ずれ（日）</th>
                  <th scope="col">進捗率</th>
                  <th scope="col">遅延理由</th>
                  <th scope="col">予定の終了</th>
                  <th scope="col">完了見込み</th>
                  <th scope="col">クリティカル</th>
                </tr>
              </thead>
              <tbody>
                {report.delays.map((d) => (
                  <tr key={d.activity_id} data-activity-id={d.activity_id}>
                    <td>{d.wbs_code}</td>
                    <td>{d.name}</td>
                    <td>{signedDays(d.deviation)}</td>
                    <td>{d.percent_complete === null ? "未算定" : `${d.percent_complete}%`}</td>
                    <td>{d.delay_reason ?? "未入力"}</td>
                    <td>{d.planned_finish}</td>
                    <td>{`${d.forecast_finish}（${d.forecast_entered ? "入力" : "計算"}）`}</td>
                    <td>{d.is_critical ? "はい" : "いいえ"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section aria-labelledby="report-milestones-heading">
          <h3 id="report-milestones-heading">マイルストーンの状況</h3>
          {report.milestones.length === 0 ? (
            <p>マイルストーンはありません</p>
          ) : (
            <table aria-label="マイルストーンの状況">
              <thead>
                <tr>
                  <th scope="col">マイルストーン</th>
                  <th scope="col">予定日</th>
                  <th scope="col">見込み日</th>
                  <th scope="col">ずれ</th>
                </tr>
              </thead>
              <tbody>
                {report.milestones.map((m) => (
                  <tr key={`${m.wbs_code}-${m.activity_id ?? m.name}`} data-activity-id={m.activity_id ?? undefined}>
                    <td>
                      ◆ {m.wbs_code} {m.name}
                    </td>
                    <td>{m.planned_date}</td>
                    <td>{m.forecast_date}</td>
                    <td>{slipText(m.slip_days)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section aria-labelledby="report-kpi-heading">
          <h3 id="report-kpi-heading">主要KPI</h3>
          {report.kpi === null ? <p>主要 KPI は表示できません</p> : <KpiList kpi={report.kpi} />}
        </section>
      </div>
    </div>
  );
}
