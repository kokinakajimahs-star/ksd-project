// UI-WBS-002 WBS辞書・横断照会（設計書 7.1 のルート /projects/:projectId/wbs/:elementId。WBS-11・13・07、EST-08、PRJ-02）。
// 辞書（API-WBS-009：WBS-11 の 8 項目。WBS ID・名称・責任者・見積は WBS 本体の値）を出し、4 項目を書く（API-WBS-010）。
// 見積の根拠と履歴（API-EST-002）と見積の記入（API-EST-001）、スコープ項目との紐付け（API-WBS-013）、横断照会（API-WBS-023）を並べる。
// 書く部品は wbs.edit（辞書・紐付け）・estimate.edit（見積）を持つ人にだけ出す（7.2）
import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import { getMe, hasProjectPermission, type Me } from "../../api/cmn";
import { toErrorView, type ErrorView } from "../../api/errors";
import { getEstimateHistory, type EstimateHistory, type EstimateWritten } from "../../api/est";
import { listScopeItems, type ScopeItem } from "../../api/prj";
import { getDictionary, replaceDictionary, replaceScopeLinks, type Dictionary } from "../../api/wbs";
import ErrorAlert from "../../components/ErrorAlert";
import Pager from "../../components/Pager";
import { optionalText } from "../../lib/form";
import { formatDateTime } from "../../lib/format";
import { LIST_LIMIT_MAX } from "../chg/labels";
import { useProjectMembers } from "../chg/useProjectMembers";
import { parseProjectId } from "../prj/ProjectSettingsPage";
import EstimateForm from "./EstimateForm";
import TracePanel from "./TracePanel";
import {
  ELEMENT_KIND_LABELS,
  ESTIMATE_METHOD_LABELS,
  INPUT_UNIT_LABELS,
  hoursText,
  labelOf,
  responsibleText,
} from "./labels";

export const ESTIMATE_HISTORY_PAGE_SIZE = 50;

const SCOPE_KIND_LABELS: Record<string, string> = { IN: "対象内", OUT: "対象外" };

function textOrDash(value: string | null): string {
  return value === null || value === "" ? "—" : value;
}

/** WP で成果物か完了条件が空なら、その項目の名前（WBS-13） */
export function missingCompletion(dictionary: Dictionary): string[] {
  if (dictionary.kind !== "WORK_PACKAGE") {
    return [];
  }
  const missing: string[] = [];
  if (dictionary.deliverable === null || dictionary.deliverable.trim() === "") {
    missing.push("成果物");
  }
  if (dictionary.completion_criteria === null || dictionary.completion_criteria.trim() === "") {
    missing.push("完了条件");
  }
  return missing;
}

/**
 * 要素を移るたび（横断照会の要素の一覧のリンクなど、同じルートの別の要素）に画面を作り直す。
 * 前の要素のスコープ紐付けの選択・保存の結果・履歴の頁を次の要素へ持ち越さない（持ち越すと別の要素の紐付けを置き換える。A-47）
 */
export default function WbsElementPage() {
  const params = useParams<{ projectId: string; elementId: string }>();
  return <WbsElementView key={`${params.projectId ?? ""}/${params.elementId ?? ""}`} />;
}

function WbsElementView() {
  const params = useParams<{ projectId: string; elementId: string }>();
  const projectId = parseProjectId(params.projectId);
  const elementId = parseProjectId(params.elementId);
  const [me, setMe] = useState<Me | null>(null);
  const [dictionary, setDictionary] = useState<Dictionary | null>(null);
  const [history, setHistory] = useState<EstimateHistory | null>(null);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [reloadKey, setReloadKey] = useState(0);
  const [scopeItems, setScopeItems] = useState<ScopeItem[]>([]);
  const [linked, setLinked] = useState<Set<number>>(new Set());
  const [savedLinks, setSavedLinks] = useState<number[] | null>(null);
  const [form, setForm] = useState({ deliverable: "", work_scope: "", completion_criteria: "", assumptions: "" });
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);
  const [dictError, setDictError] = useState<ErrorView | null>(null);
  const [linkError, setLinkError] = useState<ErrorView | null>(null);
  const { names } = useProjectMembers(projectId);

  const valid = projectId !== null && elementId !== null;
  const canEdit = valid && me !== null && hasProjectPermission(me, projectId, "wbs.edit");
  const canEstimate = valid && me !== null && hasProjectPermission(me, projectId, "estimate.edit");

  useEffect(() => {
    if (!valid) {
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
  }, [valid]);

  useEffect(() => {
    if (projectId === null || elementId === null) {
      return;
    }
    let cancelled = false;
    getDictionary(projectId, elementId).then(
      (result) => {
        if (!cancelled) {
          setDictionary(result);
          setForm({
            deliverable: result.deliverable ?? "",
            work_scope: result.work_scope ?? "",
            completion_criteria: result.completion_criteria ?? "",
            assumptions: result.assumptions ?? "",
          });
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setDictionary(null);
          setError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, elementId, reloadKey]);

  useEffect(() => {
    if (projectId === null || elementId === null) {
      return;
    }
    let cancelled = false;
    getEstimateHistory(projectId, "ELEMENT", elementId, { limit: ESTIMATE_HISTORY_PAGE_SIZE, offset: historyOffset }).then(
      (result) => {
        if (!cancelled) {
          setHistory(result);
        }
      },
      () => {
        // 記録の無い要素（対象も記録も無いときは 404）は、履歴なしとして出す
        if (!cancelled) {
          setHistory(null);
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, elementId, historyOffset, reloadKey]);

  useEffect(() => {
    if (projectId === null || !canEdit) {
      return;
    }
    let cancelled = false;
    listScopeItems(projectId, { limit: LIST_LIMIT_MAX, offset: 0 }).then(
      (page) => {
        if (!cancelled) {
          setScopeItems(page.items);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setLinkError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, canEdit]);

  if (projectId === null || elementId === null) {
    return (
      <section data-screen-id="UI-WBS-002" className="wbs-element-page">
        <h2>WBS辞書・横断照会</h2>
        <p role="alert">要素が見つかりません</p>
        <p>
          <Link to="/projects">プロジェクト一覧へ</Link>
        </p>
      </section>
    );
  }

  async function handleDictionary(event: FormEvent) {
    event.preventDefault();
    if (projectId === null || elementId === null) {
      return;
    }
    setDictError(null);
    setNotice(null);
    setBusy(true);
    try {
      const saved = await replaceDictionary(projectId, elementId, {
        deliverable: optionalText(form.deliverable),
        work_scope: optionalText(form.work_scope),
        completion_criteria: optionalText(form.completion_criteria),
        assumptions: optionalText(form.assumptions),
      });
      setDictionary(saved);
      setNotice("辞書を保存した");
    } catch (e) {
      setDictError(toErrorView(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleLinks(event: FormEvent) {
    event.preventDefault();
    if (projectId === null || elementId === null) {
      return;
    }
    setLinkError(null);
    setNotice(null);
    setBusy(true);
    try {
      const ids = scopeItems.filter((s) => linked.has(s.id)).map((s) => s.id);
      const saved = await replaceScopeLinks(projectId, elementId, ids);
      setSavedLinks(saved.scope_item_ids);
      setNotice(`スコープ項目の紐付けを ${saved.scope_item_ids.length} 件に置き換えた`);
    } catch (e) {
      setLinkError(toErrorView(e));
    } finally {
      setBusy(false);
    }
  }

  function toggleLink(id: number, checked: boolean) {
    setLinked((current) => {
      const next = new Set(current);
      if (checked) {
        next.add(id);
      } else {
        next.delete(id);
      }
      return next;
    });
  }

  function handleEstimateSaved(result: EstimateWritten) {
    setNotice(`見積を ${hoursText(result.current_hours)}にした（プロジェクトの総見積 ${hoursText(result.project_estimate_hours)}）`);
    setHistoryOffset(0);
    setReloadKey((k) => k + 1);
  }

  const missing = dictionary === null ? [] : missingCompletion(dictionary);
  const scopeTitles = new Map(scopeItems.map((s) => [s.id, s.title]));

  return (
    <section data-screen-id="UI-WBS-002" className="wbs-element-page">
      <h2>WBS辞書・横断照会</h2>
      <nav aria-label="行き先" className="page-links">
        <Link to={`/projects/${projectId}/wbs`}>WBS へ戻る</Link>
        <Link to={`/projects/${projectId}/wbs-review`}>検証・レビュー・承認</Link>
      </nav>
      <ErrorAlert error={error} />
      {notice !== null && <p role="status">{notice}</p>}

      {dictionary !== null && (
        <section aria-labelledby="dictionary-heading">
          <h3 id="dictionary-heading">
            WBS辞書：{dictionary.wbs_code} {dictionary.name}
          </h3>
          {missing.length > 0 && (
            <p role="note" className="warning">
              {missing.join("・")}が未定義のワークパッケージ（このままでは WBS の承認を申請できない：WBS-13）
            </p>
          )}
          <dl aria-label="WBS辞書">
            <dt>WBS ID</dt>
            <dd>{dictionary.wbs_code}</dd>
            <dt>名称</dt>
            <dd>{dictionary.name}</dd>
            <dt>区分</dt>
            <dd>{labelOf(ELEMENT_KIND_LABELS, dictionary.kind)}</dd>
            <dt>成果物</dt>
            <dd>{textOrDash(dictionary.deliverable)}</dd>
            <dt>作業範囲</dt>
            <dd>{textOrDash(dictionary.work_scope)}</dd>
            <dt>責任者</dt>
            <dd>{responsibleText(dictionary.responsible_user_id, dictionary.responsible_team, names)}</dd>
            <dt>完了条件</dt>
            <dd>{textOrDash(dictionary.completion_criteria)}</dd>
            <dt>見積工数</dt>
            <dd>{hoursText(dictionary.own_estimate_hours)}</dd>
            <dt>前提条件</dt>
            <dd>{textOrDash(dictionary.assumptions)}</dd>
          </dl>

          {canEdit && (
            <form aria-label="辞書を書く" onSubmit={(e) => void handleDictionary(e)}>
              <p>WBS ID・名称・責任者・見積は WBS の画面で変える。</p>
              {(
                [
                  ["deliverable", "成果物"],
                  ["work_scope", "作業範囲"],
                  ["completion_criteria", "完了条件"],
                  ["assumptions", "前提条件"],
                ] as const
              ).map(([key, label]) => (
                <label key={key}>
                  {label}
                  <textarea value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
                </label>
              ))}
              <ErrorAlert error={dictError} />
              <button type="submit" disabled={busy}>
                辞書を保存する
              </button>
            </form>
          )}
        </section>
      )}

      <section aria-labelledby="estimate-heading">
        <h3 id="estimate-heading">見積の根拠と履歴</h3>
        {(history === null || history.items.length === 0) && <p>見積の記録はまだありません</p>}
        {history !== null && history.items.length > 0 && (
          <>
            <table aria-label="見積の履歴">
              <thead>
                <tr>
                  <th scope="col">記録した日時</th>
                  <th scope="col">見積（人時）</th>
                  <th scope="col">入力</th>
                  <th scope="col">手法</th>
                  <th scope="col">前提</th>
                  <th scope="col">参照資料</th>
                  <th scope="col">記録した人</th>
                </tr>
              </thead>
              <tbody>
                {history.items.map((r) => (
                  <tr key={r.id}>
                    <td>{formatDateTime(r.entered_at)}</td>
                    <td>{hoursText(r.hours)}</td>
                    <td>
                      {r.input_value} {INPUT_UNIT_LABELS[r.input_unit]}
                    </td>
                    <td>{r.method === null ? "—" : ESTIMATE_METHOD_LABELS[r.method]}</td>
                    <td>{textOrDash(r.assumptions)}</td>
                    <td>{textOrDash(r.references)}</td>
                    <td>{r.entered_by_name ?? `#${r.entered_by}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pager total={history.total} limit={history.limit} offset={history.offset} onChange={setHistoryOffset} />
          </>
        )}
        {canEstimate && dictionary !== null && (
          <EstimateForm
            projectId={projectId}
            elementId={elementId}
            label={`${dictionary.wbs_code} ${dictionary.name}`}
            onSaved={handleEstimateSaved}
          />
        )}
      </section>

      {canEdit && (
        <section aria-labelledby="scope-links-heading">
          <h3 id="scope-links-heading">スコープ項目との紐付け</h3>
          <p>
            100% ルールの検査（対象外の混入・対象内の漏れ）に使う。保存すると、選んだ項目で紐付けを丸ごと置き換える
            （今の紐付けを読む口が無いため、選んだ項目だけが残る）。
          </p>
          {scopeItems.length === 0 && <p>スコープ項目はまだありません（プロジェクト設定で登録する）</p>}
          {scopeItems.length > 0 && (
            <form aria-label="スコープ項目を紐付ける" onSubmit={(e) => void handleLinks(e)}>
              {scopeItems.map((s) => (
                <label key={s.id}>
                  <input type="checkbox" checked={linked.has(s.id)} onChange={(e) => toggleLink(s.id, e.target.checked)} />
                  {labelOf(SCOPE_KIND_LABELS, s.kind)}：{s.title}
                </label>
              ))}
              <ErrorAlert error={linkError} />
              <button type="submit" disabled={busy}>
                紐付けを保存する
              </button>
            </form>
          )}
          {savedLinks !== null && (
            <p>
              紐付けた項目：
              {savedLinks.length === 0 ? "なし" : savedLinks.map((id) => scopeTitles.get(id) ?? `#${id}`).join("、")}
            </p>
          )}
        </section>
      )}

      {dictionary !== null && <TracePanel projectId={projectId} initialCode={dictionary.wbs_code} />}
    </section>
  );
}
