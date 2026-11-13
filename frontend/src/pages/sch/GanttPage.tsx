// UI-SCH-002 ガントチャート・イナズマ線（設計書 7.1 のルート /projects/:projectId/gantt。7.3 の描画）。
// SCH-06・10・11・12・13、PRG-01・08・12・13・18。ガント（API-SCH-010）に、選んだ報告日のイナズマ線（API-PRG-006）と
// 確定済みの過去の線（API-PRG-008）を重ねる。比べる基準は版の一覧（API-SCH-012）から選ぶ（既定は最新の版）。
// 非稼働日はサーバから受け取る：描く範囲（バーの範囲を報告日線・頂点・過去の線で広げたもの）が受け取った範囲に収まらなければ、
// 描く範囲を range_start・range_end に渡して読み直す（7.3）。
// 日程の再計算（API-SCH-008）のボタンは schedule.edit、報告日を作る部品（API-PRG-001。報告日が無くても出す）は
// status_report.manage を持つ人にだけ出す（7.2。サーバでも必ず判定する）
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getMe, hasProjectPermission, type Me } from "../../api/cmn";
import { toErrorView, type ErrorView } from "../../api/errors";
import { fetchAllPages } from "../../api/pages";
import {
  getLightningLine,
  getLightningLines,
  listStatusReports,
  type Judgement,
  type LightningLine,
  type OverlaidLine,
  type StatusReport,
} from "../../api/prg";
import {
  calculateSchedule,
  getGantt,
  listScheduleBaselines,
  type Gantt,
  type ScheduleBaseline,
  type ScheduleResult,
} from "../../api/sch";
import ErrorAlert from "../../components/ErrorAlert";
import GanttChart from "../../components/gantt/GanttChart";
import {
  chartExtraDates,
  judgementColor,
  JUDGEMENT_LABELS,
  rangeToRefetch,
  UNIT_LABELS,
  type DateRange,
  type GanttUnit,
} from "../../components/gantt/scale";
import CreateReportForm from "../prg/CreateReportForm";
import { parseProjectId } from "../prj/ProjectSettingsPage";
import { LIST_LIMIT_MAX } from "./limits";

const UNITS: GanttUnit[] = ["day", "week", "month"];
const JUDGEMENTS: Judgement[] = ["BEHIND", "ON_SCHEDULE", "AHEAD", "COMPLETED", "NOT_STARTED", "NOT_COMPUTED"];

/** 既定で開く報告日：報告日の最も新しいもの。無ければ null */
export function latestReport(reports: StatusReport[]): StatusReport | null {
  let latest: StatusReport | null = null;
  for (const report of reports) {
    if (latest === null || report.report_date > latest.report_date) {
      latest = report;
    }
  }
  return latest;
}

/** 報告日の一覧の順（サーバと同じ報告日の古い順）に、作った報告日を入れる */
export function withReport(reports: StatusReport[], created: StatusReport): StatusReport[] {
  return [...reports.filter((r) => r.id !== created.id), created].sort((a, b) =>
    a.report_date < b.report_date ? -1 : a.report_date > b.report_date ? 1 : 0,
  );
}

function deviationText(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  return rounded > 0 ? `+${rounded}` : String(rounded);
}

export default function GanttPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = parseProjectId(params.projectId);
  const [me, setMe] = useState<Me | null>(null);
  const [gantt, setGantt] = useState<Gantt | null>(null);
  /** 非稼働日を受け取る表示範囲として最後に要求した範囲。null は渡していない（バーの範囲） */
  const [requestedRange, setRequestedRange] = useState<DateRange | null>(null);
  const [baselines, setBaselines] = useState<ScheduleBaseline[]>([]);
  const [reports, setReports] = useState<StatusReport[]>([]);
  /** 比べる基準の版。null はサーバの既定（最新の版） */
  const [baselineId, setBaselineId] = useState<number | null>(null);
  const [reportId, setReportId] = useState<number | null>(null);
  const [lightning, setLightning] = useState<LightningLine | null>(null);
  const [pastReportIds, setPastReportIds] = useState<number[]>([]);
  const [pastLines, setPastLines] = useState<OverlaidLine[]>([]);
  const [unit, setUnit] = useState<GanttUnit>("day");
  const [judgementFilter, setJudgementFilter] = useState<Judgement | "">("");
  const [reloadKey, setReloadKey] = useState(0);
  const [error, setError] = useState<ErrorView | null>(null);
  const [calculating, setCalculating] = useState(false);
  const [calculated, setCalculated] = useState<ScheduleResult | null>(null);
  const [createdReport, setCreatedReport] = useState<StatusReport | null>(null);

  // 自分の権限・基準の版・報告日（全件。画面を開いたときに 1 回）
  useEffect(() => {
    if (projectId === null) {
      return;
    }
    let cancelled = false;
    Promise.all([
      getMe(),
      listScheduleBaselines(projectId, { limit: LIST_LIMIT_MAX }),
      fetchAllPages((page) => listStatusReports(projectId, page)),
    ]).then(
      ([meResult, baselineList, reportItems]) => {
        if (!cancelled) {
          setMe(meResult);
          setBaselines(baselineList.items);
          setReports(reportItems);
          setReportId(latestReport(reportItems)?.id ?? null);
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
  }, [projectId]);

  // ガントの描画データ（基準の版を選び直す・日程を計算し直す・表示範囲が広がると読み直す）
  useEffect(() => {
    if (projectId === null) {
      return;
    }
    let cancelled = false;
    getGantt(projectId, baselineId ?? undefined, requestedRange ?? undefined).then(
      (result) => {
        if (!cancelled) {
          setGantt(result);
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
  }, [projectId, baselineId, reloadKey, requestedRange]);

  // 選んだ報告日のイナズマ線（日程を計算し直すと OPEN の線も変わるので読み直す）
  useEffect(() => {
    if (projectId === null || reportId === null) {
      setLightning(null);
      return;
    }
    let cancelled = false;
    getLightningLine(projectId, reportId).then(
      (result) => {
        if (!cancelled) {
          setLightning(result);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setLightning(null);
          setError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, reportId, reloadKey]);

  // 重ねる過去の線（確定済みの報告日を複数選ぶ。PRG-18）
  useEffect(() => {
    if (projectId === null || pastReportIds.length === 0) {
      setPastLines([]);
      return;
    }
    let cancelled = false;
    getLightningLines(projectId, pastReportIds).then(
      (result) => {
        if (!cancelled) {
          setPastLines(result.lines);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setPastLines([]);
          setError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, pastReportIds, reloadKey]);

  // 描く範囲を広げる日付（GanttChart と同じ関数。7.3）
  const extraDates = useMemo(
    () => (gantt === null ? [] : chartExtraDates(gantt.rows, { lightning, pastLines })),
    [gantt, lightning, pastLines],
  );

  // 描く範囲が受け取った非稼働日の範囲に収まらなければ、描く範囲で読み直す。収まれば何もしない（読み直した後は
  // 要求した範囲＝描く範囲なので止まる）。基準の版・報告日・過去の線を選び直したときも同じ判定をする
  useEffect(() => {
    if (gantt === null) {
      return;
    }
    const next = rangeToRefetch(gantt, extraDates, requestedRange);
    if (next !== null) {
      setRequestedRange(next);
    }
  }, [gantt, extraDates, requestedRange]);

  const finalized = useMemo(() => reports.filter((r) => r.status === "FINALIZED"), [reports]);
  const listedRows = useMemo(() => {
    if (lightning === null) {
      return [];
    }
    return judgementFilter === "" ? lightning.rows : lightning.rows.filter((r) => r.judgement === judgementFilter);
  }, [lightning, judgementFilter]);

  if (projectId === null) {
    return (
      <section data-screen-id="UI-SCH-002" className="gantt-page">
        <h2>ガントチャート・イナズマ線</h2>
        <p role="alert">プロジェクトが見つかりません</p>
        <p>
          <Link to="/projects">プロジェクト一覧へ</Link>
        </p>
      </section>
    );
  }

  const canEditSchedule = me !== null && hasProjectPermission(me, projectId, "schedule.edit");
  const canManageReports = me !== null && hasProjectPermission(me, projectId, "status_report.manage");

  async function handleCalculate() {
    if (projectId === null) {
      return;
    }
    setCalculating(true);
    setError(null);
    try {
      setCalculated(await calculateSchedule(projectId));
      setReloadKey((k) => k + 1);
    } catch (e) {
      setError(toErrorView(e));
    } finally {
      setCalculating(false);
    }
  }

  // 作った報告日を一覧に足して選ぶ（行き先に進捗入力が出る。7.2）
  function handleReportCreated(created: StatusReport) {
    setReports((current) => withReport(current, created));
    setReportId(created.id);
    setCreatedReport(created);
  }

  function togglePast(id: number, checked: boolean) {
    setPastReportIds((ids) => (checked ? [...ids, id].sort((a, b) => a - b) : ids.filter((x) => x !== id)));
  }

  return (
    <section data-screen-id="UI-SCH-002" className="gantt-page">
      <h2>ガントチャート・イナズマ線</h2>
      <nav aria-label="行き先" className="page-links">
        <Link to="/projects">プロジェクト一覧へ</Link>
        <Link to={`/projects/${projectId}/activities`}>タスクリスト</Link>
        <Link to={`/projects/${projectId}/baselines`}>ベースラインと差異</Link>
        {reportId !== null && <Link to={`/projects/${projectId}/status-reports/${reportId}`}>進捗入力</Link>}
      </nav>
      <ErrorAlert error={error} />

      <div role="group" aria-label="表示の設定" className="gantt-controls">
        <label>
          表示の単位
          <select value={unit} onChange={(e) => setUnit(e.target.value as GanttUnit)}>
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
            onChange={(e) => setBaselineId(e.target.value === "" ? null : Number(e.target.value))}
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
            onChange={(e) => setReportId(e.target.value === "" ? null : Number(e.target.value))}
          >
            <option value="">（イナズマ線を出さない）</option>
            {reports.map((r) => (
              <option key={r.id} value={r.id}>
                {`${r.report_date}${r.status === "FINALIZED" ? "（確定）" : ""}`}
              </option>
            ))}
          </select>
        </label>
        {canEditSchedule && (
          <button type="button" onClick={() => void handleCalculate()} disabled={calculating}>
            日程を計算し直す
          </button>
        )}
      </div>

      {canManageReports && <CreateReportForm projectId={projectId} onCreated={handleReportCreated} />}
      {createdReport !== null && (
        <p role="status">報告日 {createdReport.report_date} を作った。進捗入力で進捗を入れる</p>
      )}

      {finalized.length > 0 && (
        <fieldset className="past-lines">
          <legend>重ねる過去の線（確定済みの報告日）</legend>
          {finalized.map((r) => (
            <label key={r.id}>
              <input
                type="checkbox"
                checked={pastReportIds.includes(r.id)}
                onChange={(e) => togglePast(r.id, e.target.checked)}
              />
              {r.report_date}
            </label>
          ))}
        </fieldset>
      )}

      {calculated !== null && (
        <p role="status">
          日程を計算し直した。プロジェクトの終了日 {calculated.project_finish_date ?? "—"}・最小のフロート{" "}
          {calculated.min_total_float ?? "—"} 日・クリティカルな活動 {calculated.critical_path.length} 件
        </p>
      )}

      {gantt !== null && (
        <>
          {gantt.restricted && <p className="note">自分の割当の活動だけを表示している</p>}
          <p className="gantt-legend">
            {gantt.baseline !== null ? `基準：第 ${gantt.baseline.version} 版 ${gantt.baseline.name}` : "基準の版はまだ無い"}
            {lightning !== null && `／報告日：${lightning.report.report_date}（許容幅 ${lightning.tolerance_days} 日）`}
          </p>
          <GanttChart gantt={gantt} unit={unit} lightning={lightning} pastLines={pastLines} />
        </>
      )}

      {lightning !== null && (
        <section aria-labelledby="judgement-heading" className="judgement-list">
          <h3 id="judgement-heading">判定の一覧</h3>
          <label>
            判定で絞り込む
            <select value={judgementFilter} onChange={(e) => setJudgementFilter(e.target.value as Judgement | "")}>
              <option value="">すべて</option>
              {JUDGEMENTS.map((j) => (
                <option key={j} value={j}>
                  {JUDGEMENT_LABELS[j]}
                </option>
              ))}
            </select>
          </label>
          <table aria-label="判定の一覧">
            <thead>
              <tr>
                <th scope="col">WBS ID</th>
                <th scope="col">活動</th>
                <th scope="col">進捗率</th>
                <th scope="col">進捗ポイント</th>
                <th scope="col">ずれ（日）</th>
                <th scope="col">判定</th>
              </tr>
            </thead>
            <tbody>
              {listedRows.map((row) => (
                <tr key={`${row.row_index}-${row.activity_id ?? "x"}`} data-judgement={row.judgement}>
                  <td>{row.wbs_code}</td>
                  <td>{row.name}</td>
                  <td>{row.percent_complete === null ? "—" : `${row.percent_complete}%`}</td>
                  <td>{row.display_date}</td>
                  <td>{deviationText(row.deviation)}</td>
                  <td>
                    <span style={{ color: judgementColor(row.judgement) }}>●</span> {JUDGEMENT_LABELS[row.judgement]}
                    {row.judgement === "NOT_COMPUTED" && "（進捗の入力が要る）"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {listedRows.length === 0 && <p>該当する活動はありません</p>}
        </section>
      )}
    </section>
  );
}
