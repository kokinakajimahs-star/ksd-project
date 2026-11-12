// UI-PRG-002 遅延分析（設計書 7.1 のルート /projects/:projectId/status-reports/:reportId/delays）。PRG-19。
// 遅延活動と影響（API-PRG-009。RULE-PRG-006・5.7）を出す：ずれ ≦ −抽出の閾値 の活動をサーバの順（ずれの小さい順＝遅れの大きい順）に並べ、
// 遅延理由・完了見込み日（PRG-03）、見込みの終了、クリティカルか、後続・マイルストーン・プロジェクトの終了への個別の影響と、
// 全ての見込みを同時に当てた全体の影響を示す。是正措置（UI-CHG-002）へ進める（7.2）
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { toErrorView, type ErrorView } from "../../api/errors";
import { getDelays, type Delay, type Delays, type FinishSlip, type Slip } from "../../api/prg";
import ErrorAlert from "../../components/ErrorAlert";
import { parseProjectId } from "../prj/ProjectSettingsPage";
import { reportStatusLabel, signedDays } from "./labels";

/** 終わりのずれ（稼働日）の表示。0 は「ずれなし」 */
export function slipText(days: number): string {
  return days === 0 ? "ずれなし" : `${days > 0 ? "+" : ""}${days} 稼働日`;
}

function finishText(finish: FinishSlip): string {
  return `${finish.before_date ?? "—"} → ${finish.after_date ?? "—"}（${slipText(finish.slip_days)}）`;
}

function SlipTable({ label, slips }: { label: string; slips: Slip[] }) {
  if (slips.length === 0) {
    return <p>{`${label}：影響なし`}</p>;
  }
  return (
    <table aria-label={label}>
      <thead>
        <tr>
          <th scope="col">活動</th>
          <th scope="col">今の終了</th>
          <th scope="col">見込みの終了</th>
          <th scope="col">ずれ</th>
        </tr>
      </thead>
      <tbody>
        {slips.map((s) => (
          <tr key={s.activity_id}>
            <td>
              {s.is_milestone && "◆ "}
              {s.name}
            </td>
            <td>{s.before_date}</td>
            <td>{s.after_date}</td>
            <td>{slipText(s.slip_days)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function DelayImpact({ delay }: { delay: Delay }) {
  return (
    <section aria-label={`${delay.name} の影響`} className="delay-impact">
      <h4>
        {delay.wbs_code} {delay.name} の影響
      </h4>
      <p>
        見込みの終了：{delay.fixed_finish.before_date} → {delay.expected_finish_date}（
        {slipText(delay.fixed_finish.slip_days)}）
        {delay.is_critical ? "・クリティカルパス上" : "・クリティカルパス外"}・後続の活動 {delay.transitive_successor_ids.length} 件
      </p>
      <SlipTable label={`${delay.name} で終わりがずれる後続の活動`} slips={delay.successor_slips} />
      <SlipTable label={`${delay.name} で影響するマイルストーン`} slips={delay.milestone_slips} />
      {delay.project_finish !== null && <p>プロジェクトの終了：{finishText(delay.project_finish)}</p>}
    </section>
  );
}

export default function DelaysPage() {
  const params = useParams<{ projectId: string; reportId: string }>();
  const projectId = parseProjectId(params.projectId);
  // 報告日の ID もプロジェクトの ID と同じ形（正の整数）
  const reportId = parseProjectId(params.reportId);
  const [data, setData] = useState<Delays | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);

  useEffect(() => {
    if (projectId === null || reportId === null) {
      return;
    }
    let cancelled = false;
    getDelays(projectId, reportId).then(
      (result) => {
        if (!cancelled) {
          setData(result);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setData(null);
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
      <section data-screen-id="UI-PRG-002" className="delays-page">
        <h2>遅延分析</h2>
        <p role="alert">{projectId === null ? "プロジェクトが見つかりません" : "報告日が見つかりません"}</p>
        <p>
          <Link to="/projects">プロジェクト一覧へ</Link>
        </p>
      </section>
    );
  }

  return (
    <section data-screen-id="UI-PRG-002" className="delays-page">
      <h2>遅延分析</h2>
      <nav aria-label="行き先" className="page-links">
        <Link to={`/projects/${projectId}/status-reports/${reportId}`}>進捗入力へ</Link>
        <Link to={`/projects/${projectId}/gantt`}>ガントチャート</Link>
        <Link to={`/projects/${projectId}/corrective-actions`}>是正措置</Link>
      </nav>
      <ErrorAlert error={error} />

      {data !== null && (
        <>
          <p>
            報告日 {data.report.report_date}（{reportStatusLabel(data.report.status)}）・ずれが −{data.extract_days}{" "}
            稼働日以下の活動を遅延として抽出（遅れの大きい順）
          </p>
          {data.restricted && <p className="note">自分の割当の活動だけを表示している</p>}

          {data.delays.length === 0 ? (
            <p>遅延している活動はありません</p>
          ) : (
            <>
              <table aria-label="遅延活動">
                <thead>
                  <tr>
                    <th scope="col">WBS ID</th>
                    <th scope="col">活動</th>
                    <th scope="col">ずれ（日）</th>
                    <th scope="col">進捗率</th>
                    <th scope="col">遅延理由</th>
                    <th scope="col">完了見込み日</th>
                    <th scope="col">見込みの終了</th>
                    <th scope="col">クリティカル</th>
                    <th scope="col">影響するマイルストーン</th>
                  </tr>
                </thead>
                <tbody>
                  {data.delays.map((d) => (
                    <tr key={d.activity_id} data-activity-id={d.activity_id}>
                      <td>{d.wbs_code}</td>
                      <td>{d.name}</td>
                      <td>{signedDays(d.deviation)}</td>
                      <td>{d.percent_complete === null ? "未算定" : `${d.percent_complete}%`}</td>
                      <td>{d.delay_reason ?? "未入力"}</td>
                      <td>{d.forecast_finish ?? "未入力"}</td>
                      <td>{d.expected_finish_date}</td>
                      <td>{d.is_critical ? "はい" : "いいえ"}</td>
                      <td>
                        {d.milestone_slips.length === 0
                          ? "なし"
                          : d.milestone_slips.map((m) => `${m.name}（${slipText(m.slip_days)}）`).join("、")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.delays.map((d) => (
                <DelayImpact key={d.activity_id} delay={d} />
              ))}
            </>
          )}

          <section aria-labelledby="combined-heading" className="combined-impact">
            <h3 id="combined-heading">全体の影響（全ての見込みを同時に当てた結果）</h3>
            <SlipTable label="全体で影響するマイルストーン" slips={data.combined_milestone_slips} />
            {data.combined_project_finish !== null && (
              <p>プロジェクトの終了：{finishText(data.combined_project_finish)}</p>
            )}
          </section>
        </>
      )}
    </section>
  );
}
