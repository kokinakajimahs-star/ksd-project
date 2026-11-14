// ガントの編集（設計書 7.3 の「編集」。CR-024・CR-025）の状態と操作。どの操作も既存の API を呼んでその場で保存し、
// 保存の後に onChanged で呼び手（GanttPage）にガント・WBS・活動を読み直させる。元に戻す操作は無い。
// 読み直しが終わるまで次の操作は受けない（古い木・一覧で位置を数えない）。
// 操作は図の上で受ける（キー・右クリックのメニュー・行の中の入力・ドラッグ）。図の外に入力欄と操作のボタンを置かない（CR-025）。
// 操作はその権限を持つときだけ行う（活動・日付・依存は schedule.edit、要素は wbs.edit。サーバでも必ず判定する）
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { toErrorView, type ErrorView } from "../../../api/errors";
import { fetchAllPages } from "../../../api/pages";
import {
  createActivity,
  createDependency,
  deleteActivity,
  deleteDependency,
  listActivities,
  updateActivity,
  type Activity,
  type Dependency,
  type Gantt,
} from "../../../api/sch";
import { createElement, deleteElement, moveElement, updateElement, type ElementKind, type WbsElement } from "../../../api/wbs";
import type { BarDrag, GanttChartEdit, InlineField, MenuTarget, SelectHow } from "../../../components/gantt/GanttChart";
import { activityKey, elementKey, type OutlineRow } from "../../../components/gantt/outline";
import { addDays } from "../../../components/gantt/scale";
import { ELEMENT_KIND_LABELS, ELEMENT_KINDS } from "../../wbs/labels";
import {
  activityStepPlan,
  addPlan,
  deletionPlan,
  elementStepPlan,
  groupActivitiesPlan,
  groupElementsPlan,
  indentPlan,
  moveTargetPlan,
  outdentPlan,
  rangeKeys,
  stepSelection,
  type AddPlan,
  type DeletionPlan,
  type ElementMove,
  type Planned,
} from "./editOps";

export interface GanttEditorInput {
  projectId: number;
  /** 編集に切り替えている */
  enabled: boolean;
  gantt: Gantt | null;
  /** WBS の要素（API-WBS-001。先行順）。読めなければ null */
  elements: WbsElement[] | null;
  outline: OutlineRow[];
  canEditSchedule: boolean;
  canEditWbs: boolean;
  /** 読み直すたびに変わる値（活動の一覧も読み直す） */
  reloadKey: number;
  /** ガントと WBS を今の reloadKey で読み終えた（読めなかったときも含む。読めなかったことは呼び手が示す） */
  pageSettled: boolean;
  /** 保存した後に呼ぶ（ガント・WBS・活動を読み直す） */
  onChanged: () => void;
}

/** 選んだ行の中身 */
export type Selection =
  | { kind: "none" }
  | { kind: "element"; element: WbsElement }
  | { kind: "activity"; activity: Activity }
  | { kind: "many"; elementIds: number[]; activityIds: number[] };

/** 右クリックのメニューの項目（CR-025）。その権限を持つ操作だけを並べる */
export type MenuEntry =
  | { kind: "item"; id: string; label: string; shortcut?: string; run: () => void }
  | { kind: "radio"; id: string; label: string; checked: boolean; run: () => void }
  | { kind: "link"; id: string; label: string; to: string }
  | { kind: "separator"; id: string };

export interface OpenMenu {
  /** 開いた所（画面の座標） */
  x: number;
  y: number;
  /** メニューの名前（読み上げ） */
  label: string;
  entries: MenuEntry[];
}

export interface GanttEditor {
  /** 活動の一覧（API-SCH-001。依存の ID を持つ）を読めた */
  ready: boolean;
  chartEdit: GanttChartEdit | undefined;
  canEditSchedule: boolean;
  canEditWbs: boolean;
  /** 保存しているか、保存の後に読み直している */
  busy: boolean;
  /** 保存している */
  saving: boolean;
  /** 保存の後に読み直している（終わるまで次の操作を受けない） */
  reloading: boolean;
  error: ErrorView | null;
  /** 活動の一覧を読めなかった（読み直せるまで操作を受けない） */
  loadError: ErrorView | null;
  message: string | null;
  selection: Selection;
  /** 開いている右クリックのメニュー */
  menu: OpenMenu | null;
  /** メニューを閉じる（restoreFocus なら入力の的を図へ戻す） */
  closeMenu: (restoreFocus: boolean) => void;
  /** メニューの項目を選ぶ（閉じてから行う） */
  chooseMenuEntry: (entry: MenuEntry) => void;
  confirmingDelete: boolean;
  /** 消すことを確かめるときの文言 */
  deleteSummary: string;
  cancelDelete: () => void;
  confirmDelete: () => Promise<void>;
}

/** 1 件ずつの操作が途中で失敗した（そこまでの移動は残る。移せなかった行を文言に入れる） */
class PartialFailure extends Error {
  constructor(readonly view: ErrorView) {
    super(view.message);
  }
}

/** 誤りの文言に、そこまでにしたことを添える */
function partialFailure(error: unknown, note: string): PartialFailure {
  const view = toErrorView(error);
  return new PartialFailure({ message: `${view.message}（${note}）`, details: view.details });
}

/** 行の名前を並べる（多ければ先頭の 3 件と残りの件数） */
function listText(names: string[]): string {
  return names.length <= 3 ? names.join("、") : `${names.slice(0, 3).join("、")} ほか ${names.length - 3} 件`;
}

/** 確かめた時点の消す計画と文言（確かめた後に選び直しても、確かめていない行を消さない） */
interface PendingDeletion {
  plan: DeletionPlan;
  summary: string;
}

/** 行の中の入力（CR-025）。足す先・まとめる行は入力を開いた時点のもの */
type Field =
  | { kind: "name"; key: string }
  | { kind: "start"; activityId: number }
  | { kind: "duration"; activityId: number }
  | { kind: "add"; plan: AddPlan }
  | { kind: "group"; activityIds: number[]; elementIds: number[]; index: number; depth: number };

/** 後続・移す先を行で選んでいる（CR-025） */
interface Picking {
  action: "link" | "move";
  activityId: number;
}

/** メニューを開いた先（開いた時点で決め、項目は描くたびに今の状態から作る） */
type MenuSubject = { kind: "row"; key: string } | { kind: "many" } | { kind: "link"; key: string } | { kind: "empty" };

const WAIT_MESSAGE = "前の操作の保存・読み直しが終わっていません。終わってからもう一度操作します";

function isActivityKey(key: string): boolean {
  return key.startsWith("a");
}

function idOf(key: string): number {
  return Number(key.slice(1));
}

/** 活動の一覧の全件と、読める活動どうしの依存（どの頁にも全件が載る） */
async function loadActivities(projectId: number): Promise<{ items: Activity[]; dependencies: Dependency[] }> {
  let dependencies: Dependency[] = [];
  const items = await fetchAllPages(async (page) => {
    const result = await listActivities(projectId, page);
    dependencies = result.dependencies;
    return result;
  });
  return { items, dependencies };
}

/** 項目の並びを整える（先頭・末尾・続いた区切りを落とす） */
function tidy(entries: (MenuEntry | false)[]): MenuEntry[] {
  const out: MenuEntry[] = [];
  for (const entry of entries) {
    if (entry === false) {
      continue;
    }
    if (entry.kind === "separator" && (out.length === 0 || out[out.length - 1]!.kind === "separator")) {
      continue;
    }
    out.push(entry);
  }
  while (out.length > 0 && out[out.length - 1]!.kind === "separator") {
    out.pop();
  }
  return out;
}

export function useGanttEditor(input: GanttEditorInput): GanttEditor {
  const { projectId, enabled, gantt, outline, canEditSchedule, canEditWbs, reloadKey, pageSettled, onChanged } = input;
  const elements = useMemo(() => input.elements ?? [], [input.elements]);
  const [activities, setActivities] = useState<Activity[] | null>(null);
  const [dependencies, setDependencies] = useState<Dependency[]>([]);
  /** 活動の一覧を読めた reloadKey */
  const [activitiesKey, setActivitiesKey] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<ErrorView | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [cursor, setCursor] = useState<string | null>(null);
  const [anchor, setAnchor] = useState<string | null>(null);
  const [field, setField] = useState<Field | null>(null);
  const [picking, setPicking] = useState<Picking | null>(null);
  const [menuAt, setMenuAt] = useState<{ subject: MenuSubject; x: number; y: number } | null>(null);
  const [focusRequest, setFocusRequest] = useState(0);
  const [selectedLink, setSelectedLink] = useState<string | null>(null);
  const [pendingDeletion, setPendingDeletion] = useState<PendingDeletion | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorView | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  // 操作を重ねて流さない・保存の後の読み直しが終わるまで次の操作を受けない（state は次の描画まで変わらないので ref でも見る）
  const busyRef = useRef(false);
  const awaitingReloadRef = useRef(false);
  // 編集を終えるたびに進める（終えた後に届いた操作の結果の文言を出さない）
  const generation = useRef(0);
  // メニュー・確かめを閉じた後、行の中の入力も確かめも開いていなければ、入力の的を図へ戻す
  const focusAfterRef = useRef(false);

  // 活動の一覧（並びの位置・希望開始日・依存の ID に要る）。読み直せなければ前の一覧を残し、操作は受けない
  useEffect(() => {
    if (!enabled) {
      return;
    }
    let cancelled = false;
    const key = reloadKey;
    loadActivities(projectId).then(
      (result) => {
        if (!cancelled) {
          setActivities(result.items);
          setDependencies(result.dependencies);
          setActivitiesKey(key);
          setLoadError(null);
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
  }, [projectId, enabled, reloadKey]);

  // 編集を終えたら、選択・入力・メニュー・確かめ・文言と活動の一覧を消す（入り直したら読み直す）
  useEffect(() => {
    if (!enabled) {
      generation.current += 1;
      setActivities(null);
      setDependencies([]);
      setActivitiesKey(null);
      setLoadError(null);
      setSelected(new Set());
      setCursor(null);
      setAnchor(null);
      setField(null);
      setPicking(null);
      setMenuAt(null);
      setSelectedLink(null);
      setPendingDeletion(null);
      setMessage(null);
      setError(null);
    }
  }, [enabled]);

  useEffect(() => {
    if (focusAfterRef.current) {
      focusAfterRef.current = false;
      if (field === null && pendingDeletion === null) {
        setFocusRequest((n) => n + 1);
      }
    }
  });

  // 今の reloadKey でガント・WBS・活動を読み終えた
  const settled = pageSettled && activitiesKey === reloadKey;
  useEffect(() => {
    if (settled) {
      awaitingReloadRef.current = false;
    }
  }, [settled, reloadKey]);
  const reloading = enabled && !settled;

  const list = useMemo(() => activities ?? [], [activities]);
  const requestedStart = useMemo(() => {
    const map = new Map<number, string>();
    for (const a of list) {
      if (a.requested_start !== null) {
        map.set(a.id, a.requested_start);
      }
    }
    return map;
  }, [list]);

  const selection = useMemo((): Selection => {
    const keys = [...selected];
    if (keys.length === 0) {
      return { kind: "none" };
    }
    if (keys.length === 1) {
      const key = keys[0]!;
      if (isActivityKey(key)) {
        const activity = list.find((a) => a.id === idOf(key));
        return activity === undefined ? { kind: "none" } : { kind: "activity", activity };
      }
      const element = elements.find((e) => e.id === idOf(key));
      return element === undefined ? { kind: "none" } : { kind: "element", element };
    }
    // 表示順に並べる
    const ordered = outline.filter((r) => selected.has(r.key));
    return {
      kind: "many",
      elementIds: ordered.flatMap((r) => (r.kind === "element" ? [r.element.id] : [])),
      activityIds: ordered.flatMap((r) => (r.kind === "activity" ? [r.row.activity_id] : [])),
    };
  }, [selected, list, elements, outline]);

  // 足す先は選んでいる行で決める（何も選んでいなければ最上位の要素。7.3）
  const addKey = cursor !== null && selected.has(cursor) ? cursor : null;

  function activityName(id: number): string {
    const a = list.find((x) => x.id === id);
    return a === undefined ? String(id) : `${a.wbs_code} ${a.name}`;
  }

  function elementName(id: number): string {
    const e = elements.find((x) => x.id === id);
    return e === undefined ? String(id) : `${e.wbs_code} ${e.name}`;
  }

  function deletionSummary(plan: DeletionPlan): string {
    const parts: string[] = [];
    if (plan.elementIds.length > 0) {
      parts.push(`要素 ${plan.elementIds.length} 件（${listText(plan.elementIds.map(elementName))}。配下の要素ごと）`);
    }
    if (plan.activityIds.length > 0) {
      parts.push(`活動 ${plan.activityIds.length} 件（${listText(plan.activityIds.map(activityName))}）`);
    }
    return parts.join("と");
  }

  /** 保存も読み直しもしていない */
  function idle(): boolean {
    return !busyRef.current && !awaitingReloadRef.current && settled;
  }

  /** 1 つの操作を流す：誤りは示し、成否に関わらず読み直す。読み直しが終わるまで次の操作は受けない */
  async function run(action: () => Promise<string | null>): Promise<boolean> {
    if (!idle()) {
      refuse(WAIT_MESSAGE);
      return false;
    }
    busyRef.current = true;
    const current = generation.current;
    setBusy(true);
    setError(null);
    setMessage(null);
    let ok = false;
    try {
      const done = await action();
      if (current === generation.current) {
        setMessage(done);
      }
      ok = true;
    } catch (e) {
      if (current === generation.current) {
        setError(e instanceof PartialFailure ? e.view : toErrorView(e));
      }
    } finally {
      busyRef.current = false;
      awaitingReloadRef.current = true;
      setBusy(false);
      onChanged();
    }
    return ok;
  }

  function refuse(reason: string) {
    setError(null);
    setMessage(reason);
  }

  function canEditKey(key: string): boolean {
    return isActivityKey(key) ? canEditSchedule : canEditWbs;
  }

  /** 選択を変える（変えたら消すことの確かめは取り消す。確かめていない行を消さない） */
  function setSelection(keys: Iterable<string>, nextCursor: string | null, nextAnchor?: string | null) {
    setSelected(new Set(keys));
    setCursor(nextCursor);
    if (nextAnchor !== undefined) {
      setAnchor(nextAnchor);
    }
    setPendingDeletion(null);
  }

  function selectOnly(key: string) {
    setSelection([key], key, key);
    setSelectedLink(null);
  }

  function onSelectRow(key: string, how: SelectHow) {
    if (picking !== null) {
      completePick(key);
      return;
    }
    setSelectedLink(null);
    if (how === "only") {
      selectOnly(key);
      return;
    }
    if (how === "toggle") {
      setSelected((current) => {
        const next = new Set(current);
        if (next.has(key)) {
          next.delete(key);
        } else {
          next.add(key);
        }
        return next;
      });
      setCursor(key);
      setAnchor(key);
      setPendingDeletion(null);
      return;
    }
    setSelection(rangeKeys(outline, anchor ?? key, key), key);
  }

  // ---- 行の中の入力（名前・希望開始日・期間・足す行・まとめる先の名前） ----

  function startRename(key: string) {
    if (!canEditKey(key)) {
      refuse(isActivityKey(key) ? "活動の名前を変える権限（schedule.edit）がありません" : "要素の名前を変える権限（wbs.edit）がありません");
      return;
    }
    if (!idle()) {
      refuse(WAIT_MESSAGE);
      return;
    }
    endPick();
    selectOnly(key);
    setField({ kind: "name", key });
  }

  function rename(key: string, name: string) {
    const trimmed = name.trim();
    const current = isActivityKey(key) ? list.find((a) => a.id === idOf(key))?.name : elements.find((e) => e.id === idOf(key))?.name;
    if (trimmed === "" || trimmed === current) {
      return;
    }
    if (!idle()) {
      refuse(`名前は変えていない（${WAIT_MESSAGE}）`);
      return;
    }
    void run(async () => {
      if (isActivityKey(key)) {
        await updateActivity(projectId, idOf(key), { name: trimmed });
      } else {
        await updateElement(projectId, idOf(key), { name: trimmed });
      }
      return `名前を「${trimmed}」にした`;
    });
  }

  /** Enter・Insert・メニューの「足す」：足す先を決め、名前の入力の行を差し込む */
  function startAdd(key: string | null, sibling = false) {
    if (!idle()) {
      refuse(WAIT_MESSAGE);
      return;
    }
    const planned = addPlan(elements, list, outline, key, sibling);
    if (!planned.ok) {
      refuse(planned.reason);
      return;
    }
    const plan = planned.value;
    if (plan.kind === "activity" ? !canEditSchedule : !canEditWbs) {
      refuse(plan.kind === "activity" ? "活動を足す権限（schedule.edit）がありません" : "要素を足す権限（wbs.edit）がありません");
      return;
    }
    endPick();
    setPendingDeletion(null);
    setField({ kind: "add", plan });
  }

  async function addRow(plan: AddPlan, name: string) {
    const trimmed = name.trim();
    if (trimmed === "") {
      return;
    }
    if (!idle()) {
      refuse(`行は足していない（${WAIT_MESSAGE}）`);
      return;
    }
    const made: { key: string | null } = { key: null };
    const current = generation.current;
    if (plan.kind === "activity") {
      const wp = plan.workPackage;
      const position = plan.position;
      await run(async () => {
        const activity = await createActivity(projectId, { work_package_id: wp.id, name: trimmed, duration_days: 1 });
        made.key = activityKey(activity.id);
        if (position !== null) {
          try {
            await updateActivity(projectId, activity.id, { position });
          } catch (e) {
            throw partialFailure(e, `活動「${trimmed}」は ${wp.wbs_code} ${wp.name} の末尾に足した。選んだ活動の次へは置けなかった`);
          }
        }
        return `${wp.wbs_code} ${wp.name} に活動「${trimmed}」を足した（期間 1 稼働日）`;
      });
    } else {
      const parent = plan.parent;
      const parentId = parent?.id ?? null;
      const position = plan.position;
      const where = parent === null ? "最上位" : `${parent.wbs_code} ${parent.name} の下`;
      const kindLabel = ELEMENT_KIND_LABELS[plan.elementKind];
      await run(async () => {
        const element = await createElement(projectId, { parent_id: parentId, kind: plan.elementKind, name: trimmed });
        made.key = elementKey(element.id);
        if (position !== null) {
          try {
            await moveElement(projectId, element.id, parentId, position);
          } catch (e) {
            throw partialFailure(e, `${kindLabel}「${trimmed}」は${where}の末尾に足した。選んだ要素の次へは置けなかった`);
          }
        }
        return `${where}に${kindLabel}「${trimmed}」を足した`;
      });
    }
    // 足した行を選ぶ（続けて Enter で次の行を足せる）
    if (made.key !== null && current === generation.current) {
      selectOnly(made.key);
    }
  }

  function startStart(activityId: number) {
    if (!canEditSchedule) {
      refuse("希望開始日を変える権限（schedule.edit）がありません");
      return;
    }
    if (!idle()) {
      refuse(WAIT_MESSAGE);
      return;
    }
    selectOnly(activityKey(activityId));
    setField({ kind: "start", activityId });
  }

  function commitStart(activityId: number, value: string) {
    const activity = list.find((a) => a.id === activityId);
    if (activity === undefined || value === "" || value === (activity.requested_start ?? activity.start_date)) {
      return;
    }
    if (!idle()) {
      refuse(`希望開始日は変えていない（${WAIT_MESSAGE}）`);
      return;
    }
    void setRequestedStart(activityId, value);
  }

  function startDuration(activityId: number) {
    if (!canEditSchedule) {
      refuse("期間を変える権限（schedule.edit）がありません");
      return;
    }
    if (!idle()) {
      refuse(WAIT_MESSAGE);
      return;
    }
    selectOnly(activityKey(activityId));
    setField({ kind: "duration", activityId });
  }

  function commitDuration(activityId: number, value: string) {
    const activity = list.find((a) => a.id === activityId);
    const days = Number(value);
    if (activity === undefined || value.trim() === "" || days === activity.duration_days) {
      return;
    }
    if (!Number.isInteger(days) || days < 1) {
      refuse("期間は 1 以上の整数（稼働日）で入れます");
      return;
    }
    if (!idle()) {
      refuse(`期間は変えていない（${WAIT_MESSAGE}）`);
      return;
    }
    void run(async () => {
      const saved = await updateActivity(projectId, activityId, { duration_days: days });
      return `期間を ${saved.duration_days} 稼働日にした（${saved.start_date ?? "—"}〜${saved.finish_date ?? "—"}）`;
    });
  }

  function onFieldCommit(value: string) {
    const current = field;
    setField(null);
    if (current === null) {
      return;
    }
    switch (current.kind) {
      case "name":
        rename(current.key, value);
        return;
      case "start":
        commitStart(current.activityId, value);
        return;
      case "duration":
        commitDuration(current.activityId, value);
        return;
      case "add":
        void addRow(current.plan, value);
        return;
      case "group":
        void groupRows(current, value);
        return;
    }
  }

  const chartField = useMemo((): InlineField | null => {
    if (field === null) {
      return null;
    }
    switch (field.kind) {
      case "name": {
        const name = isActivityKey(field.key)
          ? list.find((a) => a.id === idOf(field.key))?.name
          : elements.find((e) => e.id === idOf(field.key))?.name;
        return { at: { kind: "row", key: field.key }, input: "text", label: "名前", initial: name ?? "" };
      }
      case "start": {
        const a = list.find((x) => x.id === field.activityId);
        return {
          at: { kind: "row", key: activityKey(field.activityId) },
          input: "date",
          label: "希望開始日",
          initial: a?.requested_start ?? a?.start_date ?? "",
        };
      }
      case "duration": {
        const a = list.find((x) => x.id === field.activityId);
        return {
          at: { kind: "row", key: activityKey(field.activityId) },
          input: "number",
          label: "期間（稼働日）",
          initial: String(a?.duration_days ?? 1),
        };
      }
      case "add": {
        const plan = field.plan;
        const what = plan.kind === "activity" ? "活動" : `要素（${ELEMENT_KIND_LABELS[plan.elementKind]}）`;
        return {
          at: { kind: "draft", index: plan.index, depth: plan.depth },
          input: "text",
          label: "足す行の名前",
          initial: "",
          placeholder: `${what}の名前（Enter で足す・Esc でやめる）`,
        };
      }
      case "group":
        return {
          at: { kind: "draft", index: field.index, depth: field.depth },
          input: "text",
          label: "まとめる先の名前",
          initial: "",
          placeholder: `${field.activityIds.length > 0 ? "新しい WP" : "新しい上位の要素"}の名前（Enter でまとめる・Esc でやめる）`,
        };
    }
  }, [field, list, elements]);

  // ---- 動かす・まとめる・区分 ----

  function moveElementBy(elementId: number, plan: (elements: WbsElement[], id: number) => Planned<ElementMove>, done: string) {
    const element = elements.find((e) => e.id === elementId);
    if (element === undefined) {
      refuse("要素の行を選びます");
      return;
    }
    if (!canEditWbs) {
      refuse("要素を動かす権限（wbs.edit）がありません");
      return;
    }
    const planned = plan(elements, element.id);
    if (!planned.ok) {
      refuse(planned.reason);
      return;
    }
    void run(async () => {
      await moveElement(projectId, element.id, planned.value.parentId, planned.value.position);
      return `${element.wbs_code} ${element.name} を${done}（WBS ID は改番するまで変わらない）`;
    });
  }

  function cursorElementId(): number | null {
    return cursor === null || isActivityKey(cursor) ? null : idOf(cursor);
  }

  function indentKey() {
    const id = cursorElementId();
    if (id === null) {
      refuse("要素の行を選びます");
      return;
    }
    moveElementBy(id, indentPlan, "インデントした");
  }

  function outdentKey() {
    const id = cursorElementId();
    if (id === null) {
      refuse("要素の行を選びます");
      return;
    }
    moveElementBy(id, outdentPlan, "アウトデントした");
  }

  function step(key: string | null, direction: -1 | 1) {
    if (key === null) {
      refuse("動かす行を選びます");
      return;
    }
    if (!isActivityKey(key)) {
      moveElementBy(idOf(key), (all, id) => elementStepPlan(all, id, direction), direction < 0 ? "上へ動かした" : "下へ動かした");
      return;
    }
    if (!canEditSchedule) {
      refuse("活動を動かす権限（schedule.edit）がありません");
      return;
    }
    const id = idOf(key);
    const planned = activityStepPlan(list, id, direction);
    if (!planned.ok) {
      refuse(planned.reason);
      return;
    }
    void run(async () => {
      await updateActivity(projectId, id, { position: planned.value });
      return null;
    });
  }

  function changeKind(element: WbsElement, kind: ElementKind) {
    if (kind === element.kind) {
      return;
    }
    void run(async () => {
      await updateElement(projectId, element.id, { kind });
      return `${element.wbs_code} ${element.name} の区分を${ELEMENT_KIND_LABELS[kind]}にした`;
    });
  }

  /** メニューの「まとめる」：選んだ最初の行の上に名前の入力の行を差し込む */
  function startGroup() {
    if (!idle()) {
      refuse(WAIT_MESSAGE);
      return;
    }
    const activityIds = selection.kind === "activity" ? [selection.activity.id] : selection.kind === "many" ? selection.activityIds : [];
    const elementIds = selection.kind === "element" ? [selection.element.id] : selection.kind === "many" ? selection.elementIds : [];
    if ((activityIds.length > 0) === (elementIds.length > 0)) {
      refuse("活動だけ、または要素だけを選んでまとめます");
      return;
    }
    if (activityIds.length > 0 && (!canEditWbs || !canEditSchedule)) {
      refuse("活動を新しい WP にまとめるには、wbs.edit と schedule.edit の両方が要ります");
      return;
    }
    if (elementIds.length > 0 && !canEditWbs) {
      refuse("要素をまとめる権限（wbs.edit）がありません");
      return;
    }
    const planned = activityIds.length > 0 ? groupActivitiesPlan(elements, list, activityIds) : groupElementsPlan(elements, elementIds);
    if (!planned.ok) {
      refuse(planned.reason);
      return;
    }
    const first = outline.findIndex((r) => selected.has(r.key));
    endPick();
    setPendingDeletion(null);
    setField({ kind: "group", activityIds, elementIds, index: Math.max(0, first), depth: outline[first]?.depth ?? 1 });
  }

  async function groupRows(group: { activityIds: number[]; elementIds: number[] }, name: string) {
    const trimmed = name.trim();
    if (trimmed === "") {
      return;
    }
    if (!idle()) {
      refuse(`まとめていない（${WAIT_MESSAGE}）`);
      return;
    }
    const made: { key: string | null } = { key: null };
    const current = generation.current;
    if (group.activityIds.length > 0) {
      const plan = groupActivitiesPlan(elements, list, group.activityIds);
      if (!plan.ok) {
        refuse(plan.reason);
        return;
      }
      await run(async () => {
        const wp = await createElement(projectId, { parent_id: plan.value.parentId, kind: "WORK_PACKAGE", name: trimmed });
        made.key = elementKey(wp.id);
        let moved = 0;
        try {
          await moveElement(projectId, wp.id, plan.value.parentId, plan.value.position);
          for (const id of plan.value.ids) {
            await updateActivity(projectId, id, { work_package_id: wp.id });
            moved += 1;
          }
        } catch (e) {
          const left = plan.value.ids.slice(moved).map((id) => list.find((a) => a.id === id)?.name ?? String(id));
          throw partialFailure(e, `新しい WP「${trimmed}」は作った。${moved} 件は移した。移せなかった活動：${left.join("、")}`);
        }
        return `活動 ${moved} 件を新しい WP「${trimmed}」にまとめた`;
      });
    } else {
      const plan = groupElementsPlan(elements, group.elementIds);
      if (!plan.ok) {
        refuse(plan.reason);
        return;
      }
      await run(async () => {
        const parent = await createElement(projectId, { parent_id: plan.value.parentId, kind: "COMPONENT", name: trimmed });
        made.key = elementKey(parent.id);
        let moved = 0;
        try {
          await moveElement(projectId, parent.id, plan.value.parentId, plan.value.position);
          for (const id of plan.value.ids) {
            await moveElement(projectId, id, parent.id, null);
            moved += 1;
          }
        } catch (e) {
          const left = plan.value.ids.slice(moved).map((id) => elements.find((el) => el.id === id)?.wbs_code ?? String(id));
          throw partialFailure(e, `新しい要素「${trimmed}」は作った。${moved} 件は移した。移せなかった要素：${left.join("、")}`);
        }
        return `要素 ${moved} 件を新しい要素「${trimmed}」（構成要素）にまとめた`;
      });
    }
    if (made.key !== null && current === generation.current) {
      selectOnly(made.key);
    }
  }

  // ---- 後続・移す先を行で選ぶ ----

  function startPick(action: Picking["action"], activityId: number) {
    if (!canEditSchedule) {
      refuse(action === "link" ? "依存を足す権限（schedule.edit）がありません" : "活動を動かす権限（schedule.edit）がありません");
      return;
    }
    if (!idle()) {
      refuse(WAIT_MESSAGE);
      return;
    }
    selectOnly(activityKey(activityId));
    setField(null);
    setPicking({ action, activityId });
    setError(null);
    setMessage(
      action === "link"
        ? `${activityName(activityId)} の後続にする活動の行を選ぶ（クリック、または矢印で選んで Enter。Esc でやめる）`
        : `${activityName(activityId)} を移す先の WP の行か活動の行を選ぶ（クリック、または矢印で選んで Enter。Esc でやめる）`,
    );
  }

  /** 選ぶのをやめる（案内の文言も消す）。メニュー・F2・Insert・まとめるで別の操作を始めたとき */
  function endPick() {
    if (picking !== null) {
      setPicking(null);
      setMessage(null);
    }
  }

  function cancelPick() {
    if (picking === null) {
      return;
    }
    selectOnly(activityKey(picking.activityId));
    endPick();
  }

  function completePick(key: string) {
    const current = picking;
    if (current === null) {
      return;
    }
    // 読み直しの間は選ぶ状態を残す（読み直した後にもう一度選べる）
    if (!idle()) {
      refuse(`${WAIT_MESSAGE}（Esc でやめる）`);
      return;
    }
    const source = current.activityId;
    if (!list.some((a) => a.id === source)) {
      setPicking(null);
      refuse("選び始めた活動が見つかりません（消されたか、読み直した計画に無い）");
      return;
    }
    if (current.action === "link") {
      if (!isActivityKey(key) || idOf(key) === source) {
        refuse("後続には別の活動の行を選びます（Esc でやめる）");
        return;
      }
      setPicking(null);
      selectOnly(activityKey(source));
      void addSuccessor(source, idOf(key));
      return;
    }
    const planned = moveTargetPlan(elements, list, source, key);
    if (!planned.ok) {
      refuse(`${planned.reason}（Esc でやめる）`);
      return;
    }
    setPicking(null);
    selectOnly(activityKey(source));
    const { workPackage, position } = planned.value;
    void run(async () => {
      await updateActivity(projectId, source, { work_package_id: workPackage.id, position });
      return `${activityName(source)} を ${workPackage.wbs_code} ${workPackage.name} へ移した`;
    });
  }

  // ---- 日付・依存 ----

  async function setRequestedStart(activityId: number, date: string | null) {
    await run(async () => {
      const saved = await updateActivity(projectId, activityId, { requested_start: date });
      if (date === null) {
        return `希望開始日を外した（計算した開始は ${saved.start_date ?? "—"}）`;
      }
      return startNotice(date, saved);
    });
  }

  async function addSuccessor(predecessorId: number, successorId: number) {
    await run(async () => {
      await createDependency(projectId, { predecessor_id: predecessorId, successor_id: successorId });
      const name = (id: number) => list.find((a) => a.id === id)?.name ?? String(id);
      return `依存「${name(predecessorId)}」→「${name(successorId)}」を足した`;
    });
  }

  async function removeDependency(dependencyId: number) {
    await run(async () => {
      await deleteDependency(projectId, dependencyId);
      return "依存を外した";
    });
    setSelectedLink(null);
  }

  function removeLink(link: string) {
    if (!canEditSchedule) {
      refuse("依存を外す権限（schedule.edit）がありません");
      return;
    }
    const [p, s] = link.split("-").map(Number);
    const dependency = dependencies.find((d) => d.predecessor_id === p && d.successor_id === s);
    if (dependency === undefined) {
      refuse(WAIT_MESSAGE);
      return;
    }
    void removeDependency(dependency.id);
  }

  function onBarDrop(activityId: number, how: BarDrag, days: number) {
    const planned = gantt?.rows.find((r) => r.activity_id === activityId)?.planned ?? null;
    if (planned === null || !canEditSchedule) {
      return;
    }
    if (how === "move") {
      const date = addDays(planned.start_date, days);
      void run(async () => startNotice(date, await updateActivity(projectId, activityId, { requested_start: date })));
      return;
    }
    const proposed = addDays(planned.end_date, days);
    const date = proposed < planned.start_date ? planned.start_date : proposed;
    void run(async () => {
      const saved = await updateActivity(projectId, activityId, { planned_finish: date });
      return `終わりを ${saved.finish_date ?? date} にした（期間 ${saved.duration_days} 稼働日）`;
    });
  }

  function onLinkDrop(predecessorId: number, successorId: number) {
    if (canEditSchedule) {
      void addSuccessor(predecessorId, successorId);
    }
  }

  // ---- 消す ----

  function askDelete() {
    if (!idle()) {
      refuse(WAIT_MESSAGE);
      return;
    }
    const plan = deletionPlan(elements, list, selected);
    if (plan.elementIds.length === 0 && plan.activityIds.length === 0) {
      refuse("消す行を選びます");
      return;
    }
    if ((plan.elementIds.length > 0 && !canEditWbs) || (plan.activityIds.length > 0 && !canEditSchedule)) {
      refuse("選んだ行を消す権限がありません");
      return;
    }
    // API-WBS-005 は活動のある WP を配下に含む要素を 422 で拒む（OQ-18 (1)）。送る前に同じ理由を示す
    if (plan.blockedWorkPackages.length > 0) {
      refuse(
        `活動のある WP は消せません：${listText(plan.blockedWorkPackages.map((e) => `${e.wbs_code} ${e.name}`))}` +
          "（先に活動を消すか、別の WP へ移す。活動も一緒に選べば、活動を先に消す）",
      );
      return;
    }
    setPendingDeletion({ plan, summary: deletionSummary(plan) });
  }

  /** 確かめた計画を消す：活動を先に 1 件ずつ（API-SCH-005）、次に要素を配下ごと（API-WBS-005） */
  async function confirmDelete() {
    const pending = pendingDeletion;
    setPendingDeletion(null);
    focusAfterRef.current = true;
    if (pending === null) {
      return;
    }
    const steps = [
      ...pending.plan.activityIds.map((id) => ({ name: activityName(id), remove: () => deleteActivity(projectId, id) })),
      ...pending.plan.elementIds.map((id) => ({ name: elementName(id), remove: () => deleteElement(projectId, id) })),
    ];
    const ok = await run(async () => {
      let done = 0;
      try {
        for (const step of steps) {
          await step.remove();
          done += 1;
        }
      } catch (e) {
        if (done === 0) {
          throw e;
        }
        throw partialFailure(e, `${done} 件は消した。消せなかった行：${listText(steps.slice(done).map((s) => s.name))}`);
      }
      return `${pending.summary}を消した`;
    });
    if (ok) {
      setSelection([], null, null);
    }
  }

  function cancelDelete() {
    setPendingDeletion(null);
    focusAfterRef.current = true;
  }

  // ---- 右クリックのメニュー ----

  function onContextMenu(target: MenuTarget, point: { x: number; y: number }) {
    endPick();
    setField(null);
    let subject: MenuSubject;
    if (target.kind === "link") {
      setSelectedLink(target.key);
      setPendingDeletion(null);
      subject = target;
    } else if (target.kind === "row") {
      // 選んだ行の上なら選んだ行の全て、そうでなければその行だけを選ぶ
      if (selected.has(target.key) && selected.size > 1) {
        setCursor(target.key);
        setSelectedLink(null);
        setPendingDeletion(null);
        subject = { kind: "many" };
      } else {
        selectOnly(target.key);
        subject = target;
      }
    } else {
      setSelection([], null, null);
      setSelectedLink(null);
      subject = target;
    }
    setMenuAt({ subject, x: point.x, y: point.y });
  }

  function closeMenu(restoreFocus: boolean) {
    setMenuAt(null);
    if (restoreFocus) {
      focusAfterRef.current = true;
    }
  }

  function chooseMenuEntry(entry: MenuEntry) {
    closeMenu(true);
    if (entry.kind === "item" || entry.kind === "radio") {
      entry.run();
    }
  }

  const separator = (id: string): MenuEntry => ({ kind: "separator", id });
  const item = (id: string, label: string, action: () => void, shortcut?: string): MenuEntry => ({
    kind: "item",
    id,
    label,
    run: action,
    shortcut,
  });

  function activityEntries(activity: Activity): (MenuEntry | false)[] {
    const key = activityKey(activity.id);
    const S = canEditSchedule;
    const predecessors = dependencies.filter((d) => d.successor_id === activity.id);
    const successors = dependencies.filter((d) => d.predecessor_id === activity.id);
    return [
      S && item("add", "下に活動を足す", () => startAdd(key), "Enter"),
      S && item("rename", "名前を変える", () => startRename(key), "F2"),
      separator("s1"),
      S && item("link", "後続をつなぐ…", () => startPick("link", activity.id)),
      S && item("move", "別の WP へ移す…", () => startPick("move", activity.id)),
      S && item("start", "希望開始日を指定…", () => startStart(activity.id)),
      S && activity.requested_start !== null && item("clear-start", "希望開始日を外す", () => void setRequestedStart(activity.id, null)),
      S && !activity.is_milestone && item("duration", "期間を指定…", () => startDuration(activity.id)),
      separator("s2"),
      S && item("up", "上へ", () => step(key, -1), "Alt+Shift+↑"),
      S && item("down", "下へ", () => step(key, 1), "Alt+Shift+↓"),
      S && canEditWbs && item("group", "新しい WP にまとめる…", startGroup),
      separator("s3"),
      ...predecessors.map(
        (d) => S && item(`unlink-${d.id}`, `先行「${activityName(d.predecessor_id)}」との依存を外す`, () => void removeDependency(d.id)),
      ),
      ...successors.map(
        (d) => S && item(`unlink-${d.id}`, `後続「${activityName(d.successor_id)}」との依存を外す`, () => void removeDependency(d.id)),
      ),
      separator("s4"),
      S && item("delete", "消す", askDelete, "Delete"),
    ];
  }

  function elementEntries(element: WbsElement): (MenuEntry | false)[] {
    const key = elementKey(element.id);
    const W = canEditWbs;
    const isWp = element.kind === "WORK_PACKAGE";
    return [
      isWp ? canEditSchedule && item("add", "活動を足す", () => startAdd(key), "Enter") : W && item("add", "子の要素を足す", () => startAdd(key), "Enter"),
      W && item("add-sibling", "下に同じ階層の要素を足す", () => startAdd(key, true)),
      W && item("rename", "名前を変える", () => startRename(key), "F2"),
      separator("s1"),
      ...ELEMENT_KINDS.map(
        (kind): MenuEntry | false =>
          W && {
            kind: "radio",
            id: `kind-${kind}`,
            label: `区分：${ELEMENT_KIND_LABELS[kind]}`,
            checked: element.kind === kind,
            run: () => changeKind(element, kind),
          },
      ),
      separator("s2"),
      W && item("indent", "インデント", () => moveElementBy(element.id, indentPlan, "インデントした"), "Alt+Shift+→"),
      W && item("outdent", "アウトデント", () => moveElementBy(element.id, outdentPlan, "アウトデントした"), "Alt+Shift+←"),
      W && item("up", "上へ", () => step(key, -1), "Alt+Shift+↑"),
      W && item("down", "下へ", () => step(key, 1), "Alt+Shift+↓"),
      W && item("group", "新しい上位の要素にまとめる…", startGroup),
      separator("s3"),
      { kind: "link", id: "open", label: "WBS 辞書・担当・見積を開く", to: `/projects/${projectId}/wbs/${element.id}` },
      separator("s4"),
      W && item("delete", "消す", askDelete, "Delete"),
    ];
  }

  function manyEntries(): (MenuEntry | false)[] {
    if (selection.kind !== "many") {
      return [];
    }
    const { activityIds, elementIds } = selection;
    const count = activityIds.length + elementIds.length;
    const canDelete = (elementIds.length === 0 || canEditWbs) && (activityIds.length === 0 || canEditSchedule);
    return [
      elementIds.length === 0 &&
        canEditSchedule &&
        canEditWbs &&
        item("group", `選んだ活動 ${activityIds.length} 件を新しい WP にまとめる…`, startGroup),
      activityIds.length === 0 && canEditWbs && item("group", `選んだ要素 ${elementIds.length} 件を新しい上位の要素にまとめる…`, startGroup),
      separator("s1"),
      canDelete && item("delete", `選んだ ${count} 行を消す`, askDelete, "Delete"),
    ];
  }

  function buildMenu(subject: MenuSubject): { label: string; entries: MenuEntry[] } {
    switch (subject.kind) {
      case "empty":
        return { label: "図の操作", entries: tidy([canEditWbs && item("add", "最上位に要素を足す", () => startAdd(null), "Enter")]) };
      case "link":
        return { label: "依存の操作", entries: tidy([canEditSchedule && item("unlink", "依存を外す", () => removeLink(subject.key), "Delete")]) };
      case "many":
        return { label: "選んだ行の操作", entries: tidy(manyEntries()) };
      case "row": {
        if (isActivityKey(subject.key)) {
          const activity = list.find((a) => a.id === idOf(subject.key));
          return activity === undefined
            ? { label: "行の操作", entries: [] }
            : { label: `${activity.wbs_code} ${activity.name} の操作`, entries: tidy(activityEntries(activity)) };
        }
        const element = elements.find((e) => e.id === idOf(subject.key));
        return element === undefined
          ? { label: "行の操作", entries: [] }
          : { label: `${element.wbs_code} ${element.name} の操作`, entries: tidy(elementEntries(element)) };
      }
    }
  }

  const ready = enabled && activities !== null;
  const builtMenu = ready && menuAt !== null ? buildMenu(menuAt.subject) : null;
  const menu: OpenMenu | null = builtMenu === null || menuAt === null ? null : { x: menuAt.x, y: menuAt.y, ...builtMenu };

  // ---- キー ----

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget) {
      return;
    }
    const { key } = event;
    // 後続・移す先を選んでいる間は、矢印・Enter・Esc だけを受ける（選び始めた行を消す・動かすことをさせない）
    const pickKey = (key === "ArrowDown" || key === "ArrowUp") && !event.altKey;
    if (picking !== null && !pickKey && key !== "Enter" && key !== "Escape") {
      if (key === "Delete" || key === "F2" || key === "Insert" || (event.altKey && event.shiftKey && key.startsWith("Arrow"))) {
        event.preventDefault();
        refuse("行を選んでいる間はほかの操作をしません（Enter で決める・Esc でやめる）");
      }
      return;
    }
    if (event.altKey && event.shiftKey) {
      const action =
        key === "ArrowRight"
          ? indentKey
          : key === "ArrowLeft"
            ? outdentKey
            : key === "ArrowUp"
              ? () => step(cursor, -1)
              : key === "ArrowDown"
                ? () => step(cursor, 1)
                : null;
      if (action !== null) {
        event.preventDefault();
        action();
      }
      return;
    }
    if (key === "ArrowDown" || key === "ArrowUp") {
      event.preventDefault();
      const next = stepSelection(outline, cursor, key === "ArrowDown" ? 1 : -1);
      if (next === null) {
        return;
      }
      if (event.shiftKey && picking === null) {
        setSelection(rangeKeys(outline, anchor ?? next, next), next);
      } else {
        // 後続・移す先を選んでいる間は矢印の行を選ぶだけ（Enter で決める）
        setSelection([next], next, next);
        setSelectedLink(null);
      }
      return;
    }
    if (key === "Enter" && !event.nativeEvent.isComposing) {
      event.preventDefault();
      if (picking !== null) {
        if (cursor === null) {
          refuse("行を選びます（Esc でやめる）");
        } else {
          completePick(cursor);
        }
        return;
      }
      startAdd(addKey);
      return;
    }
    if (key === "Insert") {
      event.preventDefault();
      startAdd(addKey);
      return;
    }
    if (key === "F2") {
      if (cursor !== null) {
        event.preventDefault();
        startRename(cursor);
      }
      return;
    }
    if (key === "Delete") {
      event.preventDefault();
      if (selectedLink !== null) {
        removeLink(selectedLink);
        return;
      }
      askDelete();
      return;
    }
    // Esc：選ぶのをやめる。矢印の選択・消すことの確かめを取り消す。何も無ければ行の選択を外す（最上位に足せるように）
    if (key === "Escape") {
      if (picking !== null) {
        cancelPick();
      } else if (selectedLink !== null || pendingDeletion !== null) {
        setSelectedLink(null);
        setPendingDeletion(null);
      } else {
        setSelection([], null, null);
      }
    }
  }

  const chartEdit: GanttChartEdit | undefined = ready
    ? {
        selected,
        cursor,
        field: chartField,
        selectedLink,
        requestedStart,
        canEditSchedule,
        pickingFrom: picking?.activityId ?? null,
        focusRequest,
        onSelectRow,
        onStartRename: startRename,
        onFieldCommit,
        onFieldCancel: () => setField(null),
        onSelectLink: (link) => {
          setSelectedLink(link);
          setPendingDeletion(null);
        },
        onKeyDown,
        onContextMenu,
        onBarDrop,
        onLinkDrop,
      }
    : undefined;

  return {
    ready,
    chartEdit,
    canEditSchedule,
    canEditWbs,
    busy: busy || reloading,
    saving: busy,
    reloading,
    error,
    loadError,
    message,
    selection,
    menu,
    closeMenu,
    chooseMenuEntry,
    confirmingDelete: pendingDeletion !== null,
    deleteSummary: pendingDeletion?.summary ?? "",
    cancelDelete,
    confirmDelete,
  };
}

/** 落とした日（希望開始日）と計算した開始が違えば、その旨を示す（7.3） */
function startNotice(date: string, saved: Activity): string {
  if (saved.start_date !== null && saved.start_date !== date) {
    return `希望開始日を ${date} にした。計算した開始は ${saved.start_date}（先行の終わり・非稼働日による）`;
  }
  return `開始を ${date} にした`;
}
