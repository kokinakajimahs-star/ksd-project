// UI-PRJ-002 のスコープ（PRJ-02。API-PRJ-005・006）。対象内・対象外の項目を並べ、まとめて置き換える。
// 置き換え（API-PRJ-006）は載せなかった項目を消すので、全ての項目を読めたときだけ保存できる。
// 変えられるのは project.manage を持つ人だけ（9.1）
import { useEffect, useRef, useState } from "react";

import { toErrorView, type ErrorView } from "../../../api/errors";
import {
  listScopeItems,
  replaceScopeItems,
  type ScopeItem,
  type ScopeItemInput,
  type ScopeItemKind,
} from "../../../api/prj";
import ErrorAlert from "../../../components/ErrorAlert";
import { optionalText } from "../../../lib/form";
import { SCOPE_KIND_LABELS } from "../labels";
import { SCOPE_TITLE_MAX } from "../limits";

/** 一度に読む上限（6.1：limit の上限 1000） */
export const SCOPE_ITEMS_LIMIT = 1000;

export interface ScopeRow {
  /** 画面の中だけの鍵（足した行は id を持たない） */
  key: string;
  id?: number;
  kind: ScopeItemKind;
  title: string;
  description: string;
}

function rowOf(item: ScopeItem): ScopeRow {
  return { key: `item-${item.id}`, id: item.id, kind: item.kind, title: item.title, description: item.description ?? "" };
}

/** 画面の行を置き換えの入力にする。並び順は行の順（サーバが 1 から振る） */
export function toScopeInputs(rows: readonly ScopeRow[]): ScopeItemInput[] {
  return rows.map((row) => {
    const input: ScopeItemInput = { kind: row.kind, title: row.title, description: optionalText(row.description) };
    if (row.id !== undefined) {
      input.id = row.id;
    }
    return input;
  });
}

export default function ScopeSection({ projectId, canEdit }: { projectId: number; canEdit: boolean }) {
  const [rows, setRows] = useState<ScopeRow[] | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [loadError, setLoadError] = useState<ErrorView | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const nextKey = useRef(0);

  useEffect(() => {
    let cancelled = false;
    listScopeItems(projectId, { limit: SCOPE_ITEMS_LIMIT, offset: 0 }).then(
      (list) => {
        if (!cancelled) {
          setRows(list.items.map(rowOf));
          setTruncated(list.total > list.items.length);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setLoadError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const editable = canEdit && !truncated;

  function changeRow(key: string, patch: Partial<ScopeRow>) {
    setRows((current) => (current ?? []).map((row) => (row.key === key ? { ...row, ...patch } : row)));
  }

  function addRow(kind: ScopeItemKind) {
    nextKey.current += 1;
    const row: ScopeRow = { key: `new-${nextKey.current}`, kind, title: "", description: "" };
    setRows((current) => [...(current ?? []), row]);
  }

  function removeRow(key: string) {
    setRows((current) => (current ?? []).filter((row) => row.key !== key));
  }

  function moveRow(index: number, delta: -1 | 1) {
    setRows((current) => {
      const next = [...(current ?? [])];
      const target = index + delta;
      if (target < 0 || target >= next.length) {
        return next;
      }
      const [moved] = next.splice(index, 1);
      if (moved !== undefined) {
        next.splice(target, 0, moved);
      }
      return next;
    });
  }

  async function handleSave() {
    if (rows === null) {
      return;
    }
    setNotice(null);
    if (rows.some((row) => row.title.trim() === "")) {
      setError({ message: "名称の無い項目があります", details: [] });
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const saved = await replaceScopeItems(projectId, toScopeInputs(rows));
      setRows(saved.items.map(rowOf));
      setNotice("スコープを保存しました");
    } catch (e: unknown) {
      setError(toErrorView(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section aria-labelledby="project-scope-heading">
      <h3 id="project-scope-heading">スコープ</h3>
      <ErrorAlert error={loadError} />
      {truncated && (
        <p role="note">項目が {SCOPE_ITEMS_LIMIT} 件を超えるため、この画面では保存できません（読めた分だけを表示しています）</p>
      )}
      {rows !== null && (
        <>
          <table aria-label="スコープ項目">
            <thead>
              <tr>
                <th scope="col">区分</th>
                <th scope="col">名称</th>
                <th scope="col">説明</th>
                {editable && <th scope="col">操作</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={row.key}>
                  <td>
                    <select
                      aria-label={`${index + 1} 行目の区分`}
                      value={row.kind}
                      disabled={!editable || submitting}
                      onChange={(e) => changeRow(row.key, { kind: e.target.value as ScopeItemKind })}
                    >
                      <option value="IN">{SCOPE_KIND_LABELS.IN}</option>
                      <option value="OUT">{SCOPE_KIND_LABELS.OUT}</option>
                    </select>
                  </td>
                  <td>
                    <input
                      aria-label={`${index + 1} 行目の名称`}
                      maxLength={SCOPE_TITLE_MAX}
                      value={row.title}
                      disabled={!editable || submitting}
                      onChange={(e) => changeRow(row.key, { title: e.target.value })}
                    />
                  </td>
                  <td>
                    <textarea
                      aria-label={`${index + 1} 行目の説明`}
                      value={row.description}
                      disabled={!editable || submitting}
                      onChange={(e) => changeRow(row.key, { description: e.target.value })}
                    />
                  </td>
                  {editable && (
                    <td>
                      <button
                        type="button"
                        aria-label={`${index + 1} 行目を上へ`}
                        disabled={index === 0 || submitting}
                        onClick={() => moveRow(index, -1)}
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        aria-label={`${index + 1} 行目を下へ`}
                        disabled={index === rows.length - 1 || submitting}
                        onClick={() => moveRow(index, 1)}
                      >
                        ↓
                      </button>
                      <button
                        type="button"
                        aria-label={`${index + 1} 行目を消す`}
                        disabled={submitting}
                        onClick={() => removeRow(row.key)}
                      >
                        消す
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && <p>スコープ項目がありません</p>}
          {notice !== null && <p role="status">{notice}</p>}
          <ErrorAlert error={error} />
          {editable && (
            <div>
              <button type="button" disabled={submitting} onClick={() => addRow("IN")}>
                対象内の項目を足す
              </button>
              <button type="button" disabled={submitting} onClick={() => addRow("OUT")}>
                対象外の項目を足す
              </button>
              <button type="button" disabled={submitting} onClick={() => void handleSave()}>
                スコープを保存する
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
