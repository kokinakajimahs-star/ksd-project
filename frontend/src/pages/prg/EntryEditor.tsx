// 進捗入力（UI-PRG-001）の 1 つの活動の編集の部品：
// 進捗率・実績日付・残工数・遅延理由・完了見込み日（API-PRG-003。PRG-02・03、EFF-06）、
// 算定方式（API-PRG-005。schedule.edit を持つ人だけ）、算定項目（今の項目を API-PRG-010 で読み、API-PRG-004 で置き換える。
// 項目を数える方式の活動だけ）。PRG-05
import { useEffect, useState, type FormEvent } from "react";

import { toErrorView, type ErrorView } from "../../api/errors";
import {
  getProgressItems,
  putProgress,
  replaceProgressItems,
  setProgressMethod,
  type LightningRow,
  type ProgressEntryInput,
  type ProgressItem,
  type ProgressItemInput,
  type ProgressItemType,
} from "../../api/prg";
import type { ProgressMethod } from "../../api/prj";
import ErrorAlert from "../../components/ErrorAlert";
import { optionalText } from "../../lib/form";
import { PROGRESS_METHODS } from "../prj/labels";
import { countedItemType, ITEM_TYPE_LABELS, ITEM_TYPES, progressMethodLabel } from "./labels";
import { DELAY_REASON_MAX, HOURS_MAX, ITEM_TITLE_MAX, ITEM_WEIGHT_MAX, ITEMS_MAX } from "./limits";

/** 進捗の入力欄の値（文字のまま持ち、保存のときに数・空へ直す） */
export interface EntryForm {
  percent: string;
  actualStart: string;
  actualFinish: string;
  remaining: string;
  delayReason: string;
  forecastFinish: string;
}

export function entryFormFrom(row: LightningRow): EntryForm {
  return {
    percent: row.percent_complete === null ? "" : String(row.percent_complete),
    actualStart: row.actual_start ?? "",
    actualFinish: row.actual_finish ?? "",
    remaining: row.remaining_hours === null ? "" : String(row.remaining_hours),
    delayReason: row.delay_reason ?? "",
    forecastFinish: row.forecast_finish ?? "",
  };
}

function parseNumber(text: string): number | null | "invalid" {
  if (text.trim() === "") {
    return null;
  }
  const value = Number(text);
  return Number.isFinite(value) ? value : "invalid";
}

/**
 * 入力欄の値を API-PRG-003 の本体にする。送れない形（範囲の外・実績開始日より前の実績終了日）は理由を返す。
 * 進捗率は直接入力（DIRECT）の活動だけが送る（他の方式は RULE-PRG-001 がサーバで求める）。残工数の空は自動（BR-10）
 */
export function buildEntryInput(
  form: EntryForm,
  direct: boolean,
): { input: ProgressEntryInput; errors: [] } | { input: null; errors: string[] } {
  const errors: string[] = [];
  let percent: number | null = null;
  if (direct) {
    const value = parseNumber(form.percent);
    if (value === "invalid" || (value !== null && (value < 0 || value > 100))) {
      errors.push("進捗率は 0〜100 の数で入力してください");
    } else {
      percent = value;
    }
  }
  if (form.actualStart !== "" && form.actualFinish !== "" && form.actualFinish < form.actualStart) {
    errors.push("実績開始日より前の実績終了日は入力できません");
  }
  const remaining = parseNumber(form.remaining);
  if (remaining === "invalid" || (remaining !== null && (remaining < 0 || remaining > HOURS_MAX))) {
    errors.push(`残工数は 0〜${HOURS_MAX} 時間の数で入力してください（空欄は自動）`);
  }
  if (errors.length > 0 || remaining === "invalid") {
    return { input: null, errors };
  }
  return {
    input: {
      percent_complete: percent,
      actual_start: form.actualStart === "" ? null : form.actualStart,
      actual_finish: form.actualFinish === "" ? null : form.actualFinish,
      remaining_hours: remaining,
      delay_reason: optionalText(form.delayReason),
      forecast_finish: form.forecastFinish === "" ? null : form.forecastFinish,
    },
    errors: [],
  };
}

/** 算定項目の入力欄 1 行。id は保存済みの項目（読んだ今の項目・保存の応答で得る） */
export interface ItemDraft {
  key: number;
  id: number | null;
  itemType: ProgressItemType;
  title: string;
  weight: string;
  done: boolean;
  approved: boolean;
}

/** 保存済みの算定項目を入力欄の行にする（key は keyFrom から順に振る。id を保つ） */
export function itemDraftsFrom(items: ProgressItem[], keyFrom: number): ItemDraft[] {
  return items.map((i, index) => ({
    key: keyFrom + index,
    id: i.id,
    itemType: i.item_type,
    title: i.title,
    weight: String(i.weight),
    done: i.done,
    approved: i.approved,
  }));
}

/**
 * progress.enter_own だけの人が外せず種類を変えられない項目（保存済みの値で承認済みか作業項目。API-PRG-004・CR-014）。
 * 入力欄で種類を変えても、保存するまでは保存済みの値で決める（サーバも保存済みの値で判定する）
 */
export function guardedItemIds(items: ProgressItem[]): Set<number> {
  return new Set(items.filter((i) => i.approved || i.item_type === "WORK").map((i) => i.id));
}

/** 算定項目の入力欄を API-PRG-004 の本体にする。名前の空・重みが正の数でないものは理由を返す */
export function buildItemsInput(drafts: ItemDraft[]): { items: ProgressItemInput[]; errors: string[] } {
  const errors: string[] = [];
  const items: ProgressItemInput[] = [];
  if (drafts.length > ITEMS_MAX) {
    errors.push(`算定項目は ${ITEMS_MAX} 件までです`);
  }
  drafts.forEach((d, i) => {
    const title = d.title.trim();
    const weight = parseNumber(d.weight);
    if (title === "") {
      errors.push(`${i + 1} 行目：名前を入力してください`);
    }
    if (weight === null || weight === "invalid" || weight <= 0 || weight > ITEM_WEIGHT_MAX) {
      errors.push(`${i + 1} 行目：重みは 0 より大きい数で入力してください`);
    }
    if (title !== "" && typeof weight === "number" && weight > 0 && weight <= ITEM_WEIGHT_MAX) {
      const item: ProgressItemInput = {
        item_type: d.itemType,
        title,
        weight,
        done: d.done,
        approved: d.approved,
      };
      if (d.id !== null) {
        item.id = d.id;
      }
      items.push(item);
    }
  });
  return { items, errors };
}

interface EntryEditorProps {
  projectId: number;
  reportId: number;
  row: LightningRow & { activity_id: number };
  canEnter: boolean;
  canEditMethod: boolean;
  /** 算定項目の承認済みを変えられるか（progress.enter_all だけ。API-PRG-004）。偽なら承認済みの項目と作業項目の削除・種類の変更も出さない（CR-014） */
  canApprove: boolean;
  /** 保存した後に呼ぶ（画面はイナズマ線を読み直す） */
  onSaved: (message: string) => void;
  onClose: () => void;
}

export default function EntryEditor({
  projectId,
  reportId,
  row,
  canEnter,
  canEditMethod,
  canApprove,
  onSaved,
  onClose,
}: EntryEditorProps) {
  const activityId = row.activity_id;
  const direct = row.progress_method === "DIRECT";
  const itemType = countedItemType(row.progress_method);
  // 読み直した行の値が変われば（実績終了日で進捗率が 100 になった など）入力欄を作り直す。算定項目の入力欄は保つ
  const entryKey = [
    row.percent_complete,
    row.actual_start,
    row.actual_finish,
    row.remaining_hours,
    row.delay_reason,
    row.forecast_finish,
  ].join("|");

  return (
    <section aria-label={`進捗を入力：${row.name}`} className="entry-editor">
      <h3>
        {row.wbs_code} {row.name}
      </h3>
      <p>算定方式：{progressMethodLabel(row.progress_method)}</p>
      {canEnter && (
        <ProgressEntryForm
          key={`entry-${activityId}-${entryKey}`}
          projectId={projectId}
          reportId={reportId}
          row={row}
          direct={direct}
          onSaved={onSaved}
        />
      )}
      {canEditMethod && (
        <ProgressMethodForm
          key={`method-${activityId}-${row.progress_method ?? ""}`}
          projectId={projectId}
          activityId={activityId}
          current={row.progress_method}
          onSaved={onSaved}
        />
      )}
      {canEnter && itemType !== null && (
        <ProgressItemsEditor
          key={`items-${activityId}`}
          projectId={projectId}
          activityId={activityId}
          itemType={itemType}
          canApprove={canApprove}
          onSaved={onSaved}
        />
      )}
      <button type="button" onClick={onClose}>
        閉じる
      </button>
    </section>
  );
}

function ProgressEntryForm({
  projectId,
  reportId,
  row,
  direct,
  onSaved,
}: {
  projectId: number;
  reportId: number;
  row: LightningRow & { activity_id: number };
  direct: boolean;
  onSaved: (message: string) => void;
}) {
  const [form, setForm] = useState<EntryForm>(() => entryFormFrom(row));
  const [error, setError] = useState<ErrorView | null>(null);
  const [busy, setBusy] = useState(false);

  function set<K extends keyof EntryForm>(key: K, value: EntryForm[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const built = buildEntryInput(form, direct);
    if (built.input === null) {
      setError({ message: "入力に誤りがあります", details: built.errors });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const saved = await putProgress(projectId, reportId, row.activity_id, built.input);
      const percent = saved.percent_complete === null ? "未算定" : `${saved.percent_complete}%`;
      const remaining = saved.remaining_hours_source === "AUTO" ? "自動" : `${saved.remaining_hours ?? "—"} 時間（入力）`;
      onSaved(`「${row.name}」の進捗を保存した。進捗率 ${percent}・残工数 ${remaining}`);
    } catch (err) {
      setError(toErrorView(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form aria-label="進捗の入力" onSubmit={(e) => void handleSubmit(e)}>
      <ErrorAlert error={error} />
      <label>
        進捗率（%）
        <input
          type="number"
          min={0}
          max={100}
          step="any"
          value={form.percent}
          disabled={!direct}
          onChange={(e) => set("percent", e.target.value)}
        />
      </label>
      {!direct && <p className="note">進捗率は算定方式で求める（直接入力の活動だけが入力できる）</p>}
      <label>
        実績開始日
        <input type="date" value={form.actualStart} onChange={(e) => set("actualStart", e.target.value)} />
      </label>
      <label>
        実績終了日
        <input type="date" value={form.actualFinish} onChange={(e) => set("actualFinish", e.target.value)} />
      </label>
      <p className="note">実績終了日を入れると、進捗率は 100% になる</p>
      <label>
        残工数（時間）
        <input
          type="number"
          min={0}
          max={HOURS_MAX}
          step="any"
          value={form.remaining}
          placeholder="空欄は自動"
          onChange={(e) => set("remaining", e.target.value)}
        />
      </label>
      <label>
        遅延理由
        <textarea
          maxLength={DELAY_REASON_MAX}
          value={form.delayReason}
          onChange={(e) => set("delayReason", e.target.value)}
        />
      </label>
      <label>
        完了見込み日
        <input type="date" value={form.forecastFinish} onChange={(e) => set("forecastFinish", e.target.value)} />
      </label>
      <button type="submit" disabled={busy}>
        進捗を保存する
      </button>
    </form>
  );
}

function ProgressMethodForm({
  projectId,
  activityId,
  current,
  onSaved,
}: {
  projectId: number;
  activityId: number;
  current: string | null;
  onSaved: (message: string) => void;
}) {
  const initial = current ?? "";
  const [value, setValue] = useState(initial);
  const [error, setError] = useState<ErrorView | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await setProgressMethod(projectId, activityId, value === "" ? null : (value as ProgressMethod));
      const how = result.progress_method === null ? "（プロジェクトの既定）" : "";
      onSaved(`算定方式を ${progressMethodLabel(result.effective_method)}${how} にした`);
    } catch (err) {
      setError(toErrorView(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form aria-label="算定方式" onSubmit={(e) => void handleSubmit(e)}>
      <ErrorAlert error={error} />
      <label>
        進捗率の算定方式
        <select value={value} onChange={(e) => setValue(e.target.value)}>
          <option value="">プロジェクトの既定に従う</option>
          {PROGRESS_METHODS.map((m) => (
            <option key={m.code} value={m.code}>
              {m.label}
            </option>
          ))}
        </select>
      </label>
      <button type="submit" disabled={busy || value === initial}>
        算定方式を変える
      </button>
    </form>
  );
}

function ProgressItemsEditor({
  projectId,
  activityId,
  itemType,
  canApprove,
  onSaved,
}: {
  projectId: number;
  activityId: number;
  itemType: ProgressItemType;
  canApprove: boolean;
  onSaved: (message: string) => void;
}) {
  const [drafts, setDrafts] = useState<ItemDraft[]>([]);
  /** 外せず種類を変えられない保存済みの項目の id（canApprove が偽の人だけに効く） */
  const [guarded, setGuarded] = useState<Set<number>>(() => new Set());
  const [nextKey, setNextKey] = useState(1);
  /** 今の項目（API-PRG-010）を読めたか。読めるまでは編集を出さない（空から始めて保存すると今の項目が消える） */
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<ErrorView | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getProgressItems(projectId, activityId).then(
      (result) => {
        if (!cancelled) {
          setDrafts(itemDraftsFrom(result.items, 1));
          setGuarded(guardedItemIds(result.items));
          setNextKey(result.items.length + 1);
          setLoaded(true);
        }
      },
      (err: unknown) => {
        if (!cancelled) {
          setLoadError(toErrorView(err));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, activityId]);

  function update(key: number, patch: Partial<ItemDraft>) {
    setDrafts((ds) => ds.map((d) => (d.key === key ? { ...d, ...patch } : d)));
  }

  function add() {
    setDrafts((ds) => [
      ...ds,
      { key: nextKey, id: null, itemType, title: "", weight: "1", done: false, approved: false },
    ]);
    setNextKey((k) => k + 1);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const built = buildItemsInput(drafts);
    if (built.errors.length > 0) {
      setError({ message: "入力に誤りがあります", details: built.errors });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await replaceProgressItems(projectId, activityId, built.items);
      setDrafts(itemDraftsFrom(result.items, nextKey));
      setGuarded(guardedItemIds(result.items));
      setNextKey(nextKey + result.items.length);
      const percent = result.percent_complete === null ? "未算定" : `${result.percent_complete}%`;
      onSaved(`算定項目を ${result.items.length} 件で保存した。進捗率 ${percent}`);
    } catch (err) {
      setError(toErrorView(err));
    } finally {
      setBusy(false);
    }
  }

  if (!loaded) {
    return (
      <section aria-label="算定項目" className="progress-items">
        <h4>算定項目（{ITEM_TYPE_LABELS[itemType]}を数える）</h4>
        {loadError === null ? <p>今の算定項目を読み込んでいる</p> : <ErrorAlert error={loadError} />}
      </section>
    );
  }

  return (
    <form aria-label="算定項目" onSubmit={(e) => void handleSubmit(e)}>
      <h4>算定項目（{ITEM_TYPE_LABELS[itemType]}を数える）</h4>
      <p className="note">
        今の項目から始める。保存すると、ここに並べた項目で活動の算定項目を置き換える（並べなかった既存の項目は消える）
      </p>
      {!canApprove && (
        <p className="note">
          承認済みを変えられるのは、全ての活動の進捗を入力できる人だけ（今の値のまま保存する）。承認済みの項目と作業の項目を外す・種類を変えるのも同じ
        </p>
      )}
      <ErrorAlert error={error} />
      {drafts.length === 0 ? (
        <p>項目がありません（項目が 0 件の活動の進捗率は未算定になる）</p>
      ) : (
        <table aria-label="算定項目の一覧">
          <thead>
            <tr>
              <th scope="col">種類</th>
              <th scope="col">名前</th>
              <th scope="col">重み</th>
              <th scope="col">完了</th>
              <th scope="col">承認済み</th>
              <th scope="col">操作</th>
            </tr>
          </thead>
          <tbody>
            {drafts.map((d, i) => {
              const locked = !canApprove && d.id !== null && guarded.has(d.id);
              return (
                <tr key={d.key}>
                  <td>
                    {locked ? (
                      ITEM_TYPE_LABELS[d.itemType]
                    ) : (
                      <select
                        aria-label={`${i + 1} 行目の種類`}
                        value={d.itemType}
                        onChange={(e) => update(d.key, { itemType: e.target.value as ProgressItemType })}
                      >
                        {ITEM_TYPES.map((t) => (
                          <option key={t} value={t}>
                            {ITEM_TYPE_LABELS[t]}
                          </option>
                        ))}
                      </select>
                    )}
                  </td>
                  <td>
                    <input
                      aria-label={`${i + 1} 行目の名前`}
                      maxLength={ITEM_TITLE_MAX}
                      value={d.title}
                      onChange={(e) => update(d.key, { title: e.target.value })}
                    />
                  </td>
                  <td>
                    <input
                      aria-label={`${i + 1} 行目の重み`}
                      type="number"
                      min={0}
                      step="any"
                      value={d.weight}
                      onChange={(e) => update(d.key, { weight: e.target.value })}
                    />
                  </td>
                  <td>
                    <input
                      aria-label={`${i + 1} 行目の完了`}
                      type="checkbox"
                      checked={d.done}
                      onChange={(e) => update(d.key, { done: e.target.checked })}
                    />
                  </td>
                  <td>
                    <input
                      aria-label={`${i + 1} 行目の承認済み`}
                      type="checkbox"
                      checked={d.approved}
                      disabled={!canApprove}
                      onChange={(e) => update(d.key, { approved: e.target.checked })}
                    />
                  </td>
                  <td>
                    {locked ? (
                      "—"
                    ) : (
                      <button type="button" onClick={() => setDrafts((ds) => ds.filter((x) => x.key !== d.key))}>
                        {`${i + 1} 行目を外す`}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <button type="button" onClick={add}>
        項目を足す
      </button>
      <button type="submit" disabled={busy}>
        算定項目を保存する
      </button>
    </form>
  );
}
