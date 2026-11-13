// UI-EFF-002 工数の集計と差異（設計書 7.1 のルート /projects/:projectId/effort）。EFF-07〜10。
// 上段：多軸集計（API-EFF-005）。軸（WBS・担当者・組織・週・月）を選び、計画（今の見積）・基準・実績・ETC・EAC を別の列で並べる
//   （EFF-08）。WBS の軸は上位の要素が下位の合計になる（EFF-07）。どの軸でも合計の行は同じ値になる（EFF-09）。
// 下段：差異の分析（API-EFF-006）。差異率と絶対値の両方の閾値を超えた要素と基準に無い要素を出し、variance.note を持つ人は
//   差異の理由と是正措置（CHG-06）の紐付けを書ける（API-EFF-007。EFF-10）。
// 報告日（API-PRG-002）とスケジュールベースライン（API-SCH-012）を選べる。省けば最新の報告日の進捗率・全ての実績・最新の基準
import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import { listCorrectiveActions, type CorrectiveAction } from "../../api/chg";
import { hasProjectPermission } from "../../api/cmn";
import {
  getEffortSummary,
  getEffortVariance,
  putVarianceNote,
  type EffortAxis,
  type EffortBaselineRef,
  type EffortSummary,
  type EffortValues,
  type EffortVariance,
  type EffortVarianceRow,
  type SummaryRow,
} from "../../api/eff";
import { toErrorView, type ErrorView } from "../../api/errors";
import { fetchAllPages } from "../../api/pages";
import { listStatusReports, type StatusReport } from "../../api/prg";
import { listScheduleBaselines, type ScheduleBaseline } from "../../api/sch";
import ErrorAlert from "../../components/ErrorAlert";
import { useMe } from "../chg/useMe";
import { newestFirst, reportStatusLabel } from "../prg/labels";
import { parseProjectId } from "../prj/ProjectSettingsPage";
import {
  AXES,
  AXIS_LABELS,
  bucketLabel,
  depthOf,
  hoursText,
  LIST_LIMIT_MAX,
  rateText,
  REASON_MAX,
  signedHoursText,
  varianceStatusLabel,
} from "./labels";

/** 基準の版の表示 */
export function baselineText(baseline: EffortBaselineRef | null): string {
  if (baseline === null) {
    return "基準なし（スケジュールベースラインがありません）";
  }
  return baseline.name ? `第 ${baseline.version} 版（${baseline.name}）` : `第 ${baseline.version} 版`;
}

/** 進捗率・実績をどの報告日で取ったか（5.8） */
export function reportBasisText(summary: Pick<EffortSummary, "report" | "report_specified">): string {
  const { report, report_specified: specified } = summary;
  if (report === null) {
    return "報告日なし（進捗率は未入力、実績は全件）";
  }
  const label = `${report.report_date}（${reportStatusLabel(report.status === "FINALIZED" ? "FINALIZED" : "OPEN")}）`;
  return specified ? `報告日 ${label}の進捗率と、報告日までの実績` : `最新の報告日 ${label}の進捗率と、全ての実績`;
}

/** EAC − 基準（基準に無ければ null） */
function eacMinusBaseline(values: EffortValues): number | null {
  return values.baseline === null ? null : values.eac - values.baseline;
}

function ValueCells({ values }: { values: EffortValues }) {
  return (
    <>
      <td>{hoursText(values.planned)}</td>
      <td>{hoursText(values.baseline)}</td>
      <td>{hoursText(values.actual)}</td>
      <td>{hoursText(values.etc)}</td>
      <td>{hoursText(values.eac)}</td>
      <td>{signedHoursText(eacMinusBaseline(values))}</td>
    </>
  );
}

function SummaryTable({ summary }: { summary: EffortSummary }) {
  const axis = summary.axis;
  const isWbs = axis === "wbs";
  const depth = isWbs ? depthOf(summary.rows) : new Map<number, number>();
  const rowKey = (row: SummaryRow, index: number) =>
    isWbs ? `e${row.element_id ?? index}` : `${row.bucket_kind}-${row.user_id ?? row.name ?? row.period_start ?? index}`;
  return (
    <table aria-label={`工数の集計（${AXIS_LABELS[axis]}）`} className="effort-summary">
      <thead>
        <tr>
          {isWbs ? (
            <>
              <th scope="col">WBS ID</th>
              <th scope="col">名称</th>
            </>
          ) : (
            <th scope="col">{AXIS_LABELS[axis]}</th>
          )}
          <th scope="col">計画</th>
          <th scope="col">基準</th>
          <th scope="col">実績</th>
          <th scope="col">ETC（残）</th>
          <th scope="col">EAC</th>
          <th scope="col">EAC−基準</th>
        </tr>
      </thead>
      <tbody>
        {summary.rows.map((row, index) => (
          <tr key={rowKey(row, index)}>
            {isWbs ? (
              <>
                <td>{row.element_id === null ? "—" : row.wbs_code}</td>
                <td style={{ paddingLeft: `${(depth.get(row.element_id ?? -1) ?? 0) * 1.5}em` }}>
                  {row.element_id === null ? bucketLabel(axis, row) : row.name}
                </td>
              </>
            ) : (
              <td>{bucketLabel(axis, row)}</td>
            )}
            <ValueCells values={row.values} />
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr>
          <th scope="row" colSpan={isWbs ? 2 : 1}>
            合計
          </th>
          <ValueCells values={summary.total} />
        </tr>
      </tfoot>
    </table>
  );
}

function actionLabel(actions: readonly CorrectiveAction[], id: number | null): string {
  if (id === null) {
    return "なし";
  }
  const a = actions.find((x) => x.id === id);
  return a === undefined ? `#${id}` : `#${a.id} ${a.action}`;
}

interface Editing {
  row: EffortVarianceRow;
  reason: string;
  actionId: string;
}

export default function EffortPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = parseProjectId(params.projectId);
  const { me } = useMe(projectId);
  const canNote = me !== null && projectId !== null && hasProjectPermission(me, projectId, "variance.note");

  const [axis, setAxis] = useState<EffortAxis>("wbs");
  const [reportId, setReportId] = useState<number | null>(null);
  const [baselineId, setBaselineId] = useState<number | null>(null);
  const [reports, setReports] = useState<StatusReport[]>([]);
  const [baselines, setBaselines] = useState<ScheduleBaseline[]>([]);
  const [actions, setActions] = useState<CorrectiveAction[]>([]);

  const [summary, setSummary] = useState<EffortSummary | null>(null);
  const [summaryError, setSummaryError] = useState<ErrorView | null>(null);
  const [variance, setVariance] = useState<EffortVariance | null>(null);
  const [varianceError, setVarianceError] = useState<ErrorView | null>(null);

  const [editing, setEditing] = useState<Editing | null>(null);
  const [noteError, setNoteError] = useState<ErrorView | null>(null);
  const [savingNote, setSavingNote] = useState(false);
  const [noteSaved, setNoteSaved] = useState<string | null>(null);

  // 条件の選択肢（報告日・ベースライン）。読めなくても集計は既定の条件で出す
  useEffect(() => {
    if (projectId === null) {
      return;
    }
    let cancelled = false;
    fetchAllPages((page) => listStatusReports(projectId, page)).then(
      (items) => {
        if (!cancelled) {
          setReports(newestFirst(items));
        }
      },
      () => {
        if (!cancelled) {
          setReports([]);
        }
      },
    );
    listScheduleBaselines(projectId, { limit: LIST_LIMIT_MAX, offset: 0 }).then(
      (page) => {
        if (!cancelled) {
          setBaselines([...page.items].sort((a, b) => b.version - a.version));
        }
      },
      () => {
        if (!cancelled) {
          setBaselines([]);
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // 紐付けの選択肢（是正措置）。理由を書ける人にだけ読む
  useEffect(() => {
    if (projectId === null || !canNote) {
      return;
    }
    let cancelled = false;
    listCorrectiveActions(projectId, { limit: LIST_LIMIT_MAX, offset: 0 }).then(
      (page) => {
        if (!cancelled) {
          setActions(page.items);
        }
      },
      () => {
        if (!cancelled) {
          setActions([]);
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, canNote]);

  // 多軸集計
  useEffect(() => {
    if (projectId === null) {
      return;
    }
    let cancelled = false;
    setSummaryError(null);
    getEffortSummary(projectId, {
      axis,
      report_id: reportId ?? undefined,
      baseline_id: baselineId ?? undefined,
    }).then(
      (result) => {
        if (!cancelled) {
          setSummary(result);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setSummary(null);
          setSummaryError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, axis, reportId, baselineId]);

  // 差異の分析（軸に依らない）
  useEffect(() => {
    if (projectId === null) {
      return;
    }
    let cancelled = false;
    setVarianceError(null);
    setEditing(null);
    setNoteSaved(null);
    getEffortVariance(projectId, { report_id: reportId ?? undefined, baseline_id: baselineId ?? undefined }).then(
      (result) => {
        if (!cancelled) {
          setVariance(result);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setVariance(null);
          setVarianceError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, reportId, baselineId]);

  if (projectId === null) {
    return (
      <section data-screen-id="UI-EFF-002" className="effort-page">
        <h2>工数の集計と差異</h2>
        <p role="alert">プロジェクトが見つかりません</p>
        <p>
          <Link to="/projects">プロジェクト一覧へ</Link>
        </p>
      </section>
    );
  }

  function startEditing(row: EffortVarianceRow) {
    setEditing({
      row,
      reason: row.note?.reason ?? "",
      actionId: row.note?.corrective_action_id == null ? "" : String(row.note.corrective_action_id),
    });
    setNoteError(null);
    setNoteSaved(null);
  }

  async function handleNoteSubmit(event: FormEvent) {
    event.preventDefault();
    if (editing === null || projectId === null) {
      return;
    }
    const reason = editing.reason.trim();
    if (reason === "") {
      setNoteError({ message: "差異の理由を入力してください", details: [] });
      return;
    }
    setSavingNote(true);
    setNoteError(null);
    try {
      const note = await putVarianceNote(projectId, editing.row.element_id, {
        status_report_id: reportId,
        reason,
        corrective_action_id: editing.actionId === "" ? null : Number(editing.actionId),
      });
      setVariance((prev) =>
        prev === null
          ? prev
          : { ...prev, items: prev.items.map((r) => (r.element_id === note.element_id ? { ...r, note } : r)) },
      );
      setNoteSaved(`${editing.row.wbs_code} ${editing.row.name} の差異の理由を記録しました`);
      setEditing(null);
    } catch (e: unknown) {
      setNoteError(toErrorView(e));
    } finally {
      setSavingNote(false);
    }
  }

  const selectedReport = reports.find((r) => r.id === reportId) ?? null;

  return (
    <section data-screen-id="UI-EFF-002" className="effort-page">
      <h2>工数の集計と差異</h2>
      <nav aria-label="行き先" className="page-links">
        <Link to="/projects">プロジェクト一覧</Link>
        <Link to={`/projects/${projectId}/baselines`}>ベースライン</Link>
        <Link to={`/projects/${projectId}/corrective-actions`}>是正措置</Link>
      </nav>

      <div role="group" aria-label="集計の条件" className="effort-basis">
        <label>
          集計の軸
          <select value={axis} onChange={(e) => setAxis(e.target.value as EffortAxis)}>
            {AXES.map((a) => (
              <option key={a} value={a}>
                {AXIS_LABELS[a]}
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
            <option value="">指定しない（最新の報告日の進捗率・全ての実績）</option>
            {reports.map((r) => (
              <option key={r.id} value={String(r.id)}>
                {`${r.report_date}（${reportStatusLabel(r.status)}）`}
              </option>
            ))}
          </select>
        </label>
        <label>
          基準
          <select
            value={baselineId === null ? "" : String(baselineId)}
            onChange={(e) => setBaselineId(e.target.value === "" ? null : Number(e.target.value))}
          >
            <option value="">最新のベースライン</option>
            {baselines.map((b) => (
              <option key={b.id} value={String(b.id)}>
                {`第 ${b.version} 版（${b.name}）`}
              </option>
            ))}
          </select>
        </label>
      </div>

      <section aria-labelledby="effort-summary-heading">
        <h3 id="effort-summary-heading">多軸集計</h3>
        <ErrorAlert error={summaryError} />
        {summary !== null && (
          <>
            <p>
              {reportBasisText(summary)}・基準：{baselineText(summary.baseline)}
            </p>
            <SummaryTable summary={summary} />
            <p className="note">工数の単位は人時。どの軸でも合計の行はプロジェクト全体の値になる</p>
          </>
        )}
      </section>

      <section aria-labelledby="effort-variance-heading">
        <h3 id="effort-variance-heading">差異の分析</h3>
        <ErrorAlert error={varianceError} />
        {variance !== null && (
          <>
            <p>
              基準：{baselineText(variance.baseline)}・差異（EAC−基準）が差異率 {rateText(variance.rate_threshold, false)}{" "}
              と {hoursText(variance.hours_threshold)} 人時の両方を超えた要素と、基準に無い要素
            </p>
            {variance.items.length === 0 ? (
              <p>閾値を超えた要素はありません</p>
            ) : (
              <table aria-label="閾値を超えた要素" className="effort-variance">
                <thead>
                  <tr>
                    <th scope="col">WBS ID</th>
                    <th scope="col">名称</th>
                    <th scope="col">基準</th>
                    <th scope="col">EAC</th>
                    <th scope="col">差異</th>
                    <th scope="col">差異率</th>
                    <th scope="col">判定</th>
                    <th scope="col">差異の理由</th>
                    <th scope="col">是正措置</th>
                    {canNote && <th scope="col">操作</th>}
                  </tr>
                </thead>
                <tbody>
                  {variance.items.map((row) => (
                    <tr key={row.element_id} data-element-id={row.element_id}>
                      <td>{row.wbs_code}</td>
                      <td>{row.name}</td>
                      <td>{hoursText(row.baseline)}</td>
                      <td>{hoursText(row.eac)}</td>
                      <td>{signedHoursText(row.variance)}</td>
                      <td>{rateText(row.rate)}</td>
                      <td>{varianceStatusLabel(row.status)}</td>
                      <td>{row.note?.reason ?? "未記入"}</td>
                      <td>{actionLabel(actions, row.note?.corrective_action_id ?? null)}</td>
                      {canNote && (
                        <td>
                          <button type="button" onClick={() => startEditing(row)} disabled={savingNote}>
                            {row.note === null ? `${row.wbs_code} の理由を書く` : `${row.wbs_code} の理由を直す`}
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {noteSaved !== null && <p role="status">{noteSaved}</p>}
            {editing !== null && (
              <form
                aria-label={`${editing.row.wbs_code} ${editing.row.name} の差異の理由`}
                className="variance-note-form"
                onSubmit={(e) => void handleNoteSubmit(e)}
              >
                <p>
                  {selectedReport !== null
                    ? `報告日 ${selectedReport.report_date} の分析の理由として記録する`
                    : "報告日を指定しない分析の理由として記録する"}
                </p>
                <label>
                  差異の理由
                  <textarea
                    value={editing.reason}
                    maxLength={REASON_MAX}
                    required
                    onChange={(e) => setEditing({ ...editing, reason: e.target.value })}
                  />
                </label>
                <label>
                  紐付ける是正措置
                  <select
                    value={editing.actionId}
                    onChange={(e) => setEditing({ ...editing, actionId: e.target.value })}
                  >
                    <option value="">なし</option>
                    {actions.map((a) => (
                      <option key={a.id} value={String(a.id)}>
                        {`#${a.id} ${a.wbs_code ?? ""} ${a.action}`}
                      </option>
                    ))}
                  </select>
                </label>
                <ErrorAlert error={noteError} />
                <div className="form-actions">
                  <button type="submit" disabled={savingNote}>
                    記録する
                  </button>
                  <button type="button" onClick={() => setEditing(null)} disabled={savingNote}>
                    やめる
                  </button>
                </div>
              </form>
            )}
          </>
        )}
      </section>
    </section>
  );
}
