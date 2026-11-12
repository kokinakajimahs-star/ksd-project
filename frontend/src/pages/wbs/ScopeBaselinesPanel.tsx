// UI-WBS-003 のスコープベースラインの部品（WBS-22）：版の一覧（API-WBS-021）と、選んだ版の要素と辞書（API-WBS-022）。
// 版は承認で作られ、上書きされず増える。中身は写した時点の値（後から変わらない）
import { useEffect, useState } from "react";

import { toErrorView, type ErrorView } from "../../api/errors";
import { getScopeBaseline, listScopeBaselines, type ScopeBaseline, type ScopeBaselineList } from "../../api/wbs";
import ErrorAlert from "../../components/ErrorAlert";
import Pager from "../../components/Pager";
import { formatDateTime } from "../../lib/format";
import { ELEMENT_KIND_LABELS, SCOPE_BASELINE_REASON_LABELS, hoursText, labelOf } from "./labels";

export const SCOPE_BASELINES_PAGE_SIZE = 50;

function dictionaryText(dictionary: Record<string, string | null> | null, key: string): string {
  const value = dictionary?.[key] ?? null;
  return value === null || value === "" ? "—" : value;
}

export interface ScopeBaselinesPanelProps {
  projectId: number;
  /** 承認した後に版の一覧を読み直す */
  reloadKey: number;
}

export default function ScopeBaselinesPanel({ projectId, reloadKey }: ScopeBaselinesPanelProps) {
  const [page, setPage] = useState<ScopeBaselineList | null>(null);
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [baseline, setBaseline] = useState<ScopeBaseline | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);

  useEffect(() => {
    let cancelled = false;
    listScopeBaselines(projectId, { limit: SCOPE_BASELINES_PAGE_SIZE, offset }).then(
      (result) => {
        if (!cancelled) {
          setPage(result);
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
  }, [projectId, offset, reloadKey]);

  useEffect(() => {
    if (selectedId === null) {
      setBaseline(null);
      return;
    }
    let cancelled = false;
    getScopeBaseline(projectId, selectedId).then(
      (result) => {
        if (!cancelled) {
          setBaseline(result);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setBaseline(null);
          setError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, selectedId]);

  return (
    <section aria-labelledby="scope-baselines-heading" className="scope-baselines-panel">
      <h3 id="scope-baselines-heading">スコープベースライン</h3>
      <ErrorAlert error={error} />
      {page !== null && page.items.length === 0 && <p>スコープベースラインの版はまだありません（WBS の承認で作られる）</p>}
      {page !== null && page.items.length > 0 && (
        <>
          <table aria-label="スコープベースラインの版">
            <thead>
              <tr>
                <th scope="col">版</th>
                <th scope="col">理由</th>
                <th scope="col">作った日時</th>
                <th scope="col">要素の数</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((b) => (
                <tr key={b.id}>
                  <td>第 {b.version} 版</td>
                  <td>{labelOf(SCOPE_BASELINE_REASON_LABELS, b.reason)}</td>
                  <td>{formatDateTime(b.created_at)}</td>
                  <td>{b.element_count}</td>
                  <td>
                    <button type="button" aria-label={`第 ${b.version} 版を見る`} onClick={() => setSelectedId(b.id)}>
                      見る
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager total={page.total} limit={page.limit} offset={page.offset} onChange={setOffset} />
        </>
      )}
      {baseline !== null && (
        <>
          <h4>第 {baseline.version} 版の要素と辞書</h4>
          {baseline.restricted && <p className="note">自分の割当の経路の要素だけを表示している</p>}
          <table aria-label={`第 ${baseline.version} 版の要素`}>
            <thead>
              <tr>
                <th scope="col">WBS ID</th>
                <th scope="col">区分</th>
                <th scope="col">名称</th>
                <th scope="col">見積</th>
                <th scope="col">成果物</th>
                <th scope="col">作業範囲</th>
                <th scope="col">完了条件</th>
                <th scope="col">前提条件</th>
              </tr>
            </thead>
            <tbody>
              {baseline.elements.map((e) => (
                <tr key={e.element_id}>
                  <td>{e.wbs_code}</td>
                  <td>{labelOf(ELEMENT_KIND_LABELS, e.kind)}</td>
                  <td>{e.name}</td>
                  <td>{hoursText(e.own_estimate_hours)}</td>
                  <td>{dictionaryText(e.dictionary, "deliverable")}</td>
                  <td>{dictionaryText(e.dictionary, "work_scope")}</td>
                  <td>{dictionaryText(e.dictionary, "completion_criteria")}</td>
                  <td>{dictionaryText(e.dictionary, "assumptions")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
