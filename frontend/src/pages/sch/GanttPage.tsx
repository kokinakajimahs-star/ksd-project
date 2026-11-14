// UI-SCH-002 ガントチャート・イナズマ線（設計書 7.1 のルート /projects/:projectId/gantt。7.3 の描画）。
// SCH-06・10・11・12・13、PRG-01・08・12・13・18。ガント（API-SCH-010）に、選んだ報告日のイナズマ線（API-PRG-006）と
// 確定済みの過去の線（API-PRG-008）を重ねる。比べる基準は版の一覧（API-SCH-012）から選ぶ（既定は最新の版）。
// 非稼働日はサーバから受け取る：描く範囲（バーの範囲を報告日線・頂点・過去の線で広げたもの）が受け取った範囲に収まらなければ、
// 描く範囲を range_start・range_end に渡して読み直す（7.3）。
// 表示の単位・基準・報告日・過去の線・報告日を作る・日程の再計算は「表示の設定」のダイアログに置き、図の上には今の設定を
// 1 行で示す（7.3。CR-027）。WBS の木（API-WBS-001）から要素の行を差し込み（7.3。読めなければ活動の行だけ）、
// schedule.edit か wbs.edit を持つ人は開いたときから行・日付・依存をこの画面の図の上で変えられる（7.3 の「編集」。
// CR-024・CR-025・CR-027）。活動の行・バーのダブルクリック（編集できる人はメニューでも）と判定の一覧の活動の名前から、
// 活動の詳細と進捗のダイアログを開く（CR-027）
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
import { getGantt, listScheduleBaselines, type Gantt, type ScheduleBaseline } from "../../api/sch";
import { getWbsTree, type WbsTree } from "../../api/wbs";
import ErrorAlert from "../../components/ErrorAlert";
import GanttChart from "../../components/gantt/GanttChart";
import { buildOutline } from "../../components/gantt/outline";
import {
  chartExtraDates,
  editExtraDates,
  editRangeBase,
  judgementColor,
  JUDGEMENT_LABELS,
  rangeToRefetch,
  UNIT_LABELS,
  unplacedVertexCounts,
  type DateRange,
  type GanttUnit,
} from "../../components/gantt/scale";
import { parseProjectId } from "../prj/ProjectSettingsPage";
import ActivityDetailDialog from "./gantt/ActivityDetailDialog";
import { GanttEditBar } from "./gantt/GanttEditBar";
import GanttSettingsDialog from "./gantt/GanttSettingsDialog";
import { useGanttEditor } from "./gantt/useGanttEditor";
import { LIST_LIMIT_MAX } from "./limits";

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
  const [lightningFailed, setLightningFailed] = useState(false);
  const [pastReportIds, setPastReportIds] = useState<number[]>([]);
  const [pastLines, setPastLines] = useState<OverlaidLine[]>([]);
  const [unit, setUnit] = useState<GanttUnit>("day");
  const [judgementFilter, setJudgementFilter] = useState<Judgement | "">("");
  const [reloadKey, setReloadKey] = useState(0);
  const [error, setError] = useState<ErrorView | null>(null);
  /** ガントの描画データ（API-SCH-010）を読めなかった（読み込み中の表示を消す。他の API の誤りでは消さない） */
  const [ganttFailed, setGanttFailed] = useState(false);
  /** WBS の木（要素の行に使う）。読めなければ null（要素の行を出さない） */
  const [tree, setTree] = useState<WbsTree | null>(null);
  const [treeFailed, setTreeFailed] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  /** 開いている詳細と進捗のダイアログの活動。fromMenu は右クリックのメニューから開いた（閉じたら入力の的を図へ戻す） */
  const [detail, setDetail] = useState<{ activityId: number; fromMenu: boolean } | null>(null);
  /** ガント・WBS の木を読み終えた reloadKey（読めなかったときも。保存の後の読み直しが終わるまで編集の操作を受けない） */
  const [ganttKey, setGanttKey] = useState<number | null>(null);
  const [treeKey, setTreeKey] = useState<number | null>(null);

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
    const key = reloadKey;
    setGanttFailed(false);
    getGantt(projectId, baselineId ?? undefined, requestedRange ?? undefined).then(
      (result) => {
        if (!cancelled) {
          setGantt(result);
          setGanttKey(key);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setGanttFailed(true);
          setGanttKey(key);
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
    setLightningFailed(false);
    getLightningLine(projectId, reportId).then(
      (result) => {
        if (!cancelled) {
          setLightning(result);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setLightning(null);
          setLightningFailed(true);
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

  // WBS の木（要素の行。保存の後も読み直す）。読めなければ要素の行を出さずに今までどおり描く（7.3）
  useEffect(() => {
    if (projectId === null) {
      return;
    }
    let cancelled = false;
    const key = reloadKey;
    getWbsTree(projectId).then(
      (result) => {
        if (!cancelled) {
          setTree(result);
          setTreeFailed(false);
          setTreeKey(key);
        }
      },
      () => {
        if (!cancelled) {
          setTree(null);
          setTreeFailed(true);
          setTreeKey(key);
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, reloadKey]);

  // 編集できる人は開いたときから図の上で編集する（「編集」への切り替えは無い。7.3。CR-027）
  const canEditSchedule = me !== null && projectId !== null && hasProjectPermission(me, projectId, "schedule.edit");
  const canEditWbs = me !== null && projectId !== null && hasProjectPermission(me, projectId, "wbs.edit");
  const editing = canEditSchedule || canEditWbs;

  // 描く範囲を広げる日付（GanttChart と同じ関数。7.3）。編集するときはバーの終わりの後ろへ広げる
  const extraDates = useMemo(() => {
    if (gantt === null) {
      return [];
    }
    const lines = chartExtraDates(gantt.rows, { lightning, pastLines });
    return editing ? [...lines, ...editExtraDates(gantt)] : lines;
  }, [gantt, lightning, pastLines, editing]);

  // 今のガントの行に当てられず描かない頂点の数（GanttChart と同じ当て方。黙って落とさず注記する。A-49 の 13）
  const unplaced = useMemo(
    () => (gantt === null ? null : unplacedVertexCounts(gantt.rows, { lightning, pastLines })),
    [gantt, lightning, pastLines],
  );

  // 描く範囲が受け取った非稼働日の範囲に収まらなければ、描く範囲で読み直す。収まれば何もしない（読み直した後は
  // 要求した範囲＝描く範囲なので止まる）。基準の版・報告日・過去の線を選び直したときも同じ判定をする
  useEffect(() => {
    if (gantt === null) {
      return;
    }
    const next = rangeToRefetch(editing ? editRangeBase(gantt) : gantt, extraDates, requestedRange);
    if (next !== null) {
      setRequestedRange(next);
    }
  }, [gantt, extraDates, requestedRange, editing]);

  const outline = useMemo(
    () => (gantt === null || tree === null ? undefined : buildOutline(tree.elements, gantt.rows, editing)),
    [gantt, tree, editing],
  );
  const editor = useGanttEditor({
    projectId: projectId ?? 0,
    enabled: editing && projectId !== null && tree !== null && gantt !== null,
    gantt,
    elements: tree?.elements ?? null,
    outline: outline ?? [],
    canEditSchedule,
    canEditWbs,
    reloadKey,
    pageSettled: ganttKey === reloadKey && treeKey === reloadKey,
    onChanged: () => setReloadKey((k) => k + 1),
    onOpenDetail: (activityId) => setDetail({ activityId, fromMenu: true }),
  });

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

  const canManageReports = me !== null && hasProjectPermission(me, projectId, "status_report.manage");
  const selectedReport = reports.find((r) => r.id === reportId) ?? null;

  // 作った報告日を一覧に足して選ぶ（行き先に進捗入力が出る。7.2）
  function handleReportCreated(created: StatusReport) {
    setReports((current) => withReport(current, created));
    setReportId(created.id);
  }

  function togglePast(id: number, checked: boolean) {
    setPastReportIds((ids) => (checked ? [...ids, id].sort((a, b) => a - b) : ids.filter((x) => x !== id)));
  }

  function openDetail(activityId: number) {
    setDetail({ activityId, fromMenu: false });
  }

  function closeDetail() {
    const fromMenu = detail?.fromMenu ?? false;
    setDetail(null);
    if (fromMenu) {
      editor.focusChart();
    }
  }

  // 図の上に示す今の設定（1 行。7.3。CR-027）
  const settingsSummary = [
    `単位：${UNIT_LABELS[unit]}`,
    gantt === null
      ? null
      : gantt.baseline !== null
        ? `基準：第 ${gantt.baseline.version} 版 ${gantt.baseline.name}`
        : "基準の版はまだ無い",
    lightning !== null
      ? `報告日：${lightning.report.report_date}${lightning.report.status === "FINALIZED" ? "（確定）" : ""}（許容幅 ${lightning.tolerance_days} 日）`
      : reportId === null
        ? "報告日：選んでいない"
        : null,
    pastReportIds.length > 0 ? `過去の線 ${pastReportIds.length} 本` : null,
  ]
    .filter((part) => part !== null)
    .join("／");

  return (
    <section data-screen-id="UI-SCH-002" className="gantt-page">
      <h2>ガントチャート・イナズマ線</h2>
      <nav aria-label="行き先" className="page-links">
        <Link to="/projects">プロジェクト一覧へ</Link>
        <Link to={`/projects/${projectId}/activities`}>タスクリスト</Link>
        <Link to={`/projects/${projectId}/baselines`}>ベースラインと差異</Link>
        {reportId !== null && <Link to={`/projects/${projectId}/status-reports/${reportId}`}>進捗入力</Link>}
        <button type="button" aria-haspopup="dialog" onClick={() => setSettingsOpen(true)}>
          表示の設定
        </button>
      </nav>
      <ErrorAlert error={error} />

      {/* 描画データが届くまで図の場所が空にならないよう、読んでいることを示す */}
      {gantt === null && !ganttFailed && <p className="loading">読み込み中…</p>}
      {gantt !== null && (
        <>
          {gantt.restricted && <p className="note">自分の割当の活動だけを表示している</p>}
          <p className="gantt-legend">{settingsSummary}</p>
          {editing && tree === null && (
            <p role={treeFailed ? "alert" : undefined} className={treeFailed ? undefined : "loading"}>
              {treeFailed ? "WBS を読めないので編集できません。画面を開き直してください" : "WBS を読み込み中…"}
            </p>
          )}
          {editing && tree !== null && !editor.ready && (
            <p role={editor.loadError !== null ? "alert" : undefined} className={editor.loadError !== null ? undefined : "loading"}>
              {editor.loadError !== null
                ? `活動の一覧を読めないので編集できません（${editor.loadError.message}）。画面を開き直してください`
                : "活動を読み込み中…"}
            </p>
          )}
          {editor.ready && tree !== null && <GanttEditBar editor={editor} tree={tree} />}
          {editor.ready && outline !== undefined && outline.length === 0 && (
            <p className="note">WBS の要素がまだ無い。図の上で Enter を押す（または右クリックする）と最上位の要素を足す</p>
          )}
          <GanttChart
            gantt={gantt}
            unit={unit}
            lightning={lightning}
            pastLines={pastLines}
            outline={outline}
            edit={editor.chartEdit}
            onOpenActivity={openDetail}
          />
          {unplaced !== null && unplaced.lightning > 0 && (
            <p className="note">{`今の計画に無い活動の頂点 ${unplaced.lightning} 件は描いていない`}</p>
          )}
          {unplaced?.pastLines
            .filter((line) => line.count > 0)
            .map((line) => (
              <p key={line.reportId} className="note">
                {`報告日 ${line.reportDate} の線：今の計画に無い活動の頂点 ${line.count} 件は描いていない`}
              </p>
            ))}
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
                  <td>
                    {row.activity_id === null ? (
                      row.name
                    ) : (
                      <button
                        type="button"
                        className="link-button"
                        aria-haspopup="dialog"
                        onClick={() => openDetail(row.activity_id!)}
                      >
                        {row.name}
                      </button>
                    )}
                  </td>
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

      {settingsOpen && (
        <GanttSettingsDialog
          projectId={projectId}
          unit={unit}
          onUnit={setUnit}
          baselines={baselines}
          baselineId={baselineId}
          onBaseline={setBaselineId}
          reports={reports}
          reportId={reportId}
          onReport={setReportId}
          pastReportIds={pastReportIds}
          onTogglePast={togglePast}
          canManageReports={canManageReports}
          onReportCreated={handleReportCreated}
          canEditSchedule={canEditSchedule}
          onCalculated={() => setReloadKey((k) => k + 1)}
          onClose={() => setSettingsOpen(false)}
        />
      )}
      {detail !== null && me !== null && (
        <ActivityDetailDialog
          key={detail.activityId}
          projectId={projectId}
          activityId={detail.activityId}
          me={me}
          activities={editor.activities}
          report={selectedReport}
          lightning={lightning}
          lightningFailed={lightningFailed}
          reloadKey={reloadKey}
          onSaved={() => setReloadKey((k) => k + 1)}
          onClose={closeDetail}
        />
      )}
    </section>
  );
}
