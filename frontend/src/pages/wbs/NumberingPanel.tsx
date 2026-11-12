// UI-WBS-001 の採番と改番の部品：採番規則（API-WBS-006。WBS-05）・改番（API-WBS-007。新旧の対応を出す）・
// WBS ID の新旧対応の一覧（API-WBS-008。WBS-06）。規則を変えても今の ID は変わらず、改番の操作でだけ変わる
import { useEffect, useState, type FormEvent } from "react";

import { toErrorView, type ErrorView } from "../../api/errors";
import {
  listCodeHistory,
  renumberWbs,
  replaceNumberingRule,
  type CodeHistoryList,
  type NumberingRule,
  type RenumberResult,
} from "../../api/wbs";
import ErrorAlert from "../../components/ErrorAlert";
import Pager from "../../components/Pager";
import { formatDateTime } from "../../lib/format";

export const CODE_HISTORY_PAGE_SIZE = 50;

export interface NumberingPanelProps {
  projectId: number;
  rule: NumberingRule;
  renumberPending: number;
  canEdit: boolean;
  /** 規則を変えた・改番した後（木を読み直す） */
  onChanged: (notice: string) => void;
  /** 木を読み直した回数（改番の後に新旧対応の一覧も読み直す） */
  reloadKey: number;
}

export default function NumberingPanel({ projectId, rule, renumberPending, canEdit, onChanged, reloadKey }: NumberingPanelProps) {
  const [separator, setSeparator] = useState(rule.separator);
  const [minDigits, setMinDigits] = useState(String(rule.min_digits));
  const [prefix, setPrefix] = useState(rule.prefix);
  const [padFirst, setPadFirst] = useState(rule.pad_first_level);
  const [result, setResult] = useState<RenumberResult | null>(null);
  const [history, setHistory] = useState<CodeHistoryList | null>(null);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorView | null>(null);

  useEffect(() => {
    setSeparator(rule.separator);
    setMinDigits(String(rule.min_digits));
    setPrefix(rule.prefix);
    setPadFirst(rule.pad_first_level);
  }, [rule]);

  useEffect(() => {
    let cancelled = false;
    listCodeHistory(projectId, { limit: CODE_HISTORY_PAGE_SIZE, offset }).then(
      (page) => {
        if (!cancelled) {
          setHistory(page);
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

  async function handleRule(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (!/^[1-9]$/.test(minDigits.trim())) {
      setError({ message: "最小の桁数は 1〜9 の整数で入力してください", details: [] });
      return;
    }
    setBusy(true);
    try {
      await replaceNumberingRule(projectId, {
        separator,
        min_digits: Number(minDigits.trim()),
        prefix,
        pad_first_level: padFirst,
      });
      onChanged("採番規則を変えた。今の ID は改番するまで変わらない");
    } catch (e) {
      setError(toErrorView(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleRenumber() {
    setError(null);
    setBusy(true);
    try {
      const renumbered = await renumberWbs(projectId);
      setResult(renumbered);
      setOffset(0);
      onChanged(
        renumbered.renumbered_at === null ? "改番で変わる ID はなかった" : `改番した（ID が変わった要素 ${renumbered.changes.length} 件）`,
      );
    } catch (e) {
      setError(toErrorView(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="numbering-heading" className="numbering-panel">
      <h3 id="numbering-heading">採番と改番</h3>
      <ErrorAlert error={error} />
      <p>
        今の規則：区切り「{rule.separator}」・最小の桁数 {rule.min_digits}・接頭辞「{rule.prefix}」・第 1 階層の補完
        {rule.pad_first_level ? "あり" : "なし"}。改番待ちの要素 {renumberPending} 件
      </p>
      {canEdit && (
        <>
          <form aria-label="採番規則を変える" onSubmit={(e) => void handleRule(e)}>
            <label>
              区切り文字
              <input value={separator} maxLength={8} onChange={(e) => setSeparator(e.target.value)} />
            </label>
            <label>
              最小の桁数
              <input inputMode="numeric" value={minDigits} onChange={(e) => setMinDigits(e.target.value)} />
            </label>
            <label>
              接頭辞
              <input value={prefix} maxLength={20} onChange={(e) => setPrefix(e.target.value)} />
            </label>
            <label>
              <input type="checkbox" checked={padFirst} onChange={(e) => setPadFirst(e.target.checked)} />
              第 1 階層を補完する（1 → 1.0）
            </label>
            <button type="submit" disabled={busy}>
              規則を保存する
            </button>
          </form>
          <button type="button" onClick={() => void handleRenumber()} disabled={busy}>
            改番する
          </button>
        </>
      )}

      {result !== null && result.changes.length > 0 && (
        <table aria-label="改番の結果">
          <thead>
            <tr>
              <th scope="col">旧 ID</th>
              <th scope="col">新 ID</th>
            </tr>
          </thead>
          <tbody>
            {result.changes.map((c) => (
              <tr key={c.element_id}>
                <td>{c.old_code}</td>
                <td>{c.new_code}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h4>WBS ID の新旧対応</h4>
      {history !== null && history.items.length === 0 && <p>改番の記録はまだありません</p>}
      {history !== null && history.items.length > 0 && (
        <>
          <table aria-label="WBS ID の新旧対応">
            <thead>
              <tr>
                <th scope="col">改番した日時</th>
                <th scope="col">旧 ID</th>
                <th scope="col">新 ID</th>
                <th scope="col">今の ID</th>
              </tr>
            </thead>
            <tbody>
              {history.items.map((h) => (
                <tr key={h.id}>
                  <td>{formatDateTime(h.renumbered_at)}</td>
                  <td>{h.old_code}</td>
                  <td>{h.new_code}</td>
                  <td>{h.current_code}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager total={history.total} limit={history.limit} offset={history.offset} onChange={setOffset} />
        </>
      )}
    </section>
  );
}
