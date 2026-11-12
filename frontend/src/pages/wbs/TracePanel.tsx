// UI-WBS-002 の横断照会の部品（API-WBS-023。WBS-07・RULE-WBS-008）。
// WBS ID を 1 つ指定すると、その要素（配下を含む）の活動・見積・実績工数・EAC・変更要求・是正措置を 1 回の照会で出す。
// 旧 ID を入れても新旧対応から今の要素に解決する（RULE-WBS-002）。既定は開いている要素の今の ID
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { toErrorView, type ErrorView } from "../../api/errors";
import { traceWbsCode, type WbsTrace } from "../../api/wbs";
import ErrorAlert from "../../components/ErrorAlert";
import { RESOLVED_BY_LABELS, hoursText, labelOf } from "./labels";

function ids(values: number[]): string {
  return values.length === 0 ? "なし" : `${values.length} 件（${values.map((v) => `#${v}`).join("、")}）`;
}

export interface TracePanelProps {
  projectId: number;
  /** 既定で照会する WBS ID（開いている要素の今の ID） */
  initialCode: string;
}

export default function TracePanel({ projectId, initialCode }: TracePanelProps) {
  const [code, setCode] = useState(initialCode);
  const [query, setQuery] = useState(initialCode);
  const [trace, setTrace] = useState<WbsTrace | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);

  useEffect(() => {
    setCode(initialCode);
    setQuery(initialCode);
  }, [initialCode]);

  useEffect(() => {
    if (query === "") {
      return;
    }
    let cancelled = false;
    setError(null);
    traceWbsCode(projectId, query).then(
      (result) => {
        if (!cancelled) {
          setTrace(result);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setTrace(null);
          setError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, query]);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmed = code.trim();
    if (trimmed === "") {
      setError({ message: "WBS ID を入力してください", details: [] });
      return;
    }
    setQuery(trimmed);
  }

  return (
    <section aria-labelledby="trace-heading" className="trace-panel">
      <h3 id="trace-heading">WBS ID による横断照会</h3>
      <form aria-label="WBS ID で照会する" onSubmit={handleSubmit}>
        <label>
          WBS ID（旧 ID も可）
          <input value={code} onChange={(e) => setCode(e.target.value)} />
        </label>
        <button type="submit">照会する</button>
      </form>
      <ErrorAlert error={error} />
      {trace !== null && (
        <>
          <p>
            「{trace.query}」を{labelOf(RESOLVED_BY_LABELS, trace.resolved_by)}として、今の ID「{trace.wbs_code}」の要素に解決した
          </p>
          {trace.restricted && <p className="note">自分の割当に関わる分だけを表示している</p>}
          <dl aria-label="横断照会の結果">
            <dt>要素（配下を含む）</dt>
            <dd>
              <ul>
                {trace.elements.map((e) => (
                  <li key={e.id}>
                    <Link to={`/projects/${projectId}/wbs/${e.id}`}>
                      {e.wbs_code} {e.name}
                    </Link>
                  </li>
                ))}
              </ul>
            </dd>
            <dt>活動</dt>
            <dd>{ids(trace.activity_ids)}</dd>
            <dt>見積の記録</dt>
            <dd>{ids(trace.estimate_record_ids)}</dd>
            <dt>実績工数</dt>
            <dd>
              {hoursText(trace.actual_hours)}（記録 {trace.time_entry_ids.length} 件）
            </dd>
            <dt>EAC</dt>
            <dd>{trace.eac_hours === null ? "—（読めない）" : hoursText(trace.eac_hours)}</dd>
            <dt>変更要求</dt>
            <dd>
              {ids(trace.change_request_ids)}
              {trace.change_request_ids.length > 0 && (
                <>
                  {" "}
                  <Link to={`/projects/${projectId}/change-requests`}>変更要求へ</Link>
                </>
              )}
            </dd>
            <dt>是正措置</dt>
            <dd>
              {ids(trace.corrective_action_ids)}
              {trace.corrective_action_ids.length > 0 && (
                <>
                  {" "}
                  <Link to={`/projects/${projectId}/corrective-actions`}>是正措置へ</Link>
                </>
              )}
            </dd>
          </dl>
        </>
      )}
    </section>
  );
}
