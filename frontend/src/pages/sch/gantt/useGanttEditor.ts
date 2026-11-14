// ガントの編集（設計書 7.3 の「編集」。CR-024）の状態と操作。どの操作も既存の API を呼んでその場で保存し、
// 保存の後に onChanged で呼び手（GanttPage）にガント・WBS・活動を読み直させる。元に戻す操作は無い。
// 読み直しが終わるまで次の操作は受けない（古い木・一覧で位置を数えない）。
// 操作はその権限を持つときだけ行う（活動・日付・依存は schedule.edit、要素は wbs.edit。サーバでも必ず判定する）
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type RefObject } from "react";

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
import type { BarDrag, GanttChartEdit, SelectHow } from "../../../components/gantt/GanttChart";
import type { OutlineRow } from "../../../components/gantt/outline";
import { addDays } from "../../../components/gantt/scale";
import {
  activityStepPlan,
  addTarget,
  deletionPlan,
  elementStepPlan,
  groupActivitiesPlan,
  groupElementsPlan,
  indentPlan,
  outdentPlan,
  rangeKeys,
  stepSelection,
  type AddTarget,
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

export interface GanttEditor {
  /** 活動の一覧（API-SCH-001。依存の ID を持つ）を読めた */
  ready: boolean;
  chartEdit: GanttChartEdit | undefined;
  canEditSchedule: boolean;
  canEditWbs: boolean;
  /** 保存しているか、保存の後に読み直している（操作のボタンを押せなくする） */
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
  activities: Activity[];
  dependencies: Dependency[];
  workPackages: WbsElement[];
  addTarget: AddTarget;
  addInputRef: RefObject<HTMLInputElement>;
  confirmingDelete: boolean;
  /** 消すことを確かめるときの文言 */
  deleteSummary: string;
  rename: () => void;
  indent: () => void;
  outdent: () => void;
  stepUp: () => void;
  stepDown: () => void;
  askDelete: () => void;
  cancelDelete: () => void;
  confirmDelete: () => Promise<void>;
  addRow: (name: string, kind: ElementKind) => Promise<boolean>;
  groupSelected: (name: string, kind: ElementKind) => Promise<boolean>;
  moveActivityTo: (activityId: number, workPackageId: number) => Promise<void>;
  setRequestedStart: (activityId: number, date: string | null) => Promise<void>;
  setDuration: (activityId: number, days: number) => Promise<void>;
  addSuccessor: (predecessorId: number, successorId: number) => Promise<void>;
  removeDependency: (dependencyId: number) => Promise<void>;
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
  const [renaming, setRenaming] = useState<string | null>(null);
  const [selectedLink, setSelectedLink] = useState<string | null>(null);
  const [pendingDeletion, setPendingDeletion] = useState<PendingDeletion | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorView | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const addInputRef = useRef<HTMLInputElement>(null);
  // 操作を重ねて流さない・保存の後の読み直しが終わるまで次の操作を受けない（state は次の描画まで変わらないので ref でも見る）
  const busyRef = useRef(false);
  const awaitingReloadRef = useRef(false);
  // 編集を終えるたびに進める（終えた後に届いた操作の結果の文言を出さない）
  const generation = useRef(0);

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

  // 編集を終えたら、選択・確かめ・文言と活動の一覧を消す（入り直したら読み直す）
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
      setRenaming(null);
      setSelectedLink(null);
      setPendingDeletion(null);
      setMessage(null);
      setError(null);
    }
  }, [enabled]);

  // 今の reloadKey でガント・WBS・活動を読み終えた
  const settled = pageSettled && activitiesKey === reloadKey;
  useEffect(() => {
    if (settled) {
      awaitingReloadRef.current = false;
    }
  }, [settled, reloadKey]);
  const reloading = enabled && !settled;

  const list = useMemo(() => activities ?? [], [activities]);
  const workPackages = useMemo(() => elements.filter((e) => e.kind === "WORK_PACKAGE"), [elements]);
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
  const target = useMemo(() => addTarget(elements, list, addKey), [elements, list, addKey]);

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

  function startRename(key: string) {
    if (!canEditKey(key)) {
      refuse(isActivityKey(key) ? "活動の名前を変える権限（schedule.edit）がありません" : "要素の名前を変える権限（wbs.edit）がありません");
      return;
    }
    selectOnly(key);
    setRenaming(key);
  }

  function onRename(key: string, name: string) {
    setRenaming(null);
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

  function cursorElement(): WbsElement | undefined {
    return cursor === null || isActivityKey(cursor) ? undefined : elements.find((e) => e.id === idOf(cursor));
  }

  function moveElementBy(plan: (elements: WbsElement[], id: number) => Planned<ElementMove>, done: string) {
    const element = cursorElement();
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

  function step(direction: -1 | 1) {
    if (cursor === null) {
      refuse("動かす行を選びます");
      return;
    }
    if (!isActivityKey(cursor)) {
      moveElementBy((all, id) => elementStepPlan(all, id, direction), direction < 0 ? "上へ動かした" : "下へ動かした");
      return;
    }
    if (!canEditSchedule) {
      refuse("活動を動かす権限（schedule.edit）がありません");
      return;
    }
    const planned = activityStepPlan(list, idOf(cursor), direction);
    if (!planned.ok) {
      refuse(planned.reason);
      return;
    }
    const id = idOf(cursor);
    void run(async () => {
      await updateActivity(projectId, id, { position: planned.value });
      return null;
    });
  }

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

  async function addRow(name: string, kind: ElementKind): Promise<boolean> {
    const trimmed = name.trim();
    if (trimmed === "") {
      refuse("足す行の名前を入れます");
      return false;
    }
    if (target.kind === "activity") {
      if (!canEditSchedule) {
        refuse("活動を足す権限（schedule.edit）がありません");
        return false;
      }
      const wp = target.workPackage;
      return run(async () => {
        await createActivity(projectId, { work_package_id: wp.id, name: trimmed, duration_days: 1 });
        return `${wp.wbs_code} ${wp.name} に活動「${trimmed}」を足した（期間 1 稼働日）`;
      });
    }
    if (!canEditWbs) {
      refuse("要素を足す権限（wbs.edit）がありません");
      return false;
    }
    const parent = target.parent;
    return run(async () => {
      await createElement(projectId, { parent_id: parent?.id ?? null, kind, name: trimmed });
      return parent === null ? `最上位に要素「${trimmed}」を足した` : `${parent.wbs_code} ${parent.name} の下に要素「${trimmed}」を足した`;
    });
  }

  async function groupSelected(name: string, kind: ElementKind): Promise<boolean> {
    const trimmed = name.trim();
    if (trimmed === "") {
      refuse("まとめる先の名前を入れます");
      return false;
    }
    if (selection.kind === "none" || (selection.kind === "many" && selection.elementIds.length > 0 && selection.activityIds.length > 0)) {
      refuse("活動だけ、または要素だけを選んでまとめます");
      return false;
    }
    const activityIds = selection.kind === "activity" ? [selection.activity.id] : selection.kind === "many" ? selection.activityIds : [];
    const elementIds = selection.kind === "element" ? [selection.element.id] : selection.kind === "many" ? selection.elementIds : [];
    if (activityIds.length > 0) {
      if (!canEditWbs || !canEditSchedule) {
        refuse("活動を新しい WP にまとめるには、wbs.edit と schedule.edit の両方が要ります");
        return false;
      }
      const plan = groupActivitiesPlan(elements, list, activityIds);
      if (!plan.ok) {
        refuse(plan.reason);
        return false;
      }
      return run(async () => {
        const wp = await createElement(projectId, { parent_id: plan.value.parentId, kind: "WORK_PACKAGE", name: trimmed });
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
    }
    if (!canEditWbs) {
      refuse("要素をまとめる権限（wbs.edit）がありません");
      return false;
    }
    const plan = groupElementsPlan(elements, elementIds);
    if (!plan.ok) {
      refuse(plan.reason);
      return false;
    }
    return run(async () => {
      const parent = await createElement(projectId, { parent_id: plan.value.parentId, kind, name: trimmed });
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
      return `要素 ${moved} 件を新しい要素「${trimmed}」にまとめた`;
    });
  }

  async function moveActivityTo(activityId: number, workPackageId: number) {
    const wp = workPackages.find((e) => e.id === workPackageId);
    await run(async () => {
      await updateActivity(projectId, activityId, { work_package_id: workPackageId });
      return wp === undefined ? "活動を移した" : `活動を ${wp.wbs_code} ${wp.name} の末尾へ移した`;
    });
  }

  async function setRequestedStart(activityId: number, date: string | null) {
    await run(async () => {
      const saved = await updateActivity(projectId, activityId, { requested_start: date });
      if (date === null) {
        return `希望開始日を外した（計算した開始は ${saved.start_date ?? "—"}）`;
      }
      return startNotice(date, saved);
    });
  }

  async function setDuration(activityId: number, days: number) {
    await run(async () => {
      const saved = await updateActivity(projectId, activityId, { duration_days: days });
      return `期間を ${saved.duration_days} 稼働日にした（${saved.start_date ?? "—"}〜${saved.finish_date ?? "—"}）`;
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

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget) {
      return;
    }
    const { key } = event;
    if (event.altKey && event.shiftKey) {
      const action =
        key === "ArrowRight"
          ? () => moveElementBy(indentPlan, "インデントした")
          : key === "ArrowLeft"
            ? () => moveElementBy(outdentPlan, "アウトデントした")
            : key === "ArrowUp"
              ? () => step(-1)
              : key === "ArrowDown"
                ? () => step(1)
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
      if (event.shiftKey) {
        setSelection(rangeKeys(outline, anchor ?? next, next), next);
      } else {
        selectOnly(next);
      }
      return;
    }
    if (key === "F2" || key === "Enter") {
      if (cursor !== null) {
        event.preventDefault();
        startRename(cursor);
      }
      return;
    }
    if (key === "Insert") {
      event.preventDefault();
      if (addInputRef.current === null) {
        refuse(target.kind === "activity" ? "活動を足す権限（schedule.edit）がありません" : "要素を足す権限（wbs.edit）がありません");
      } else {
        addInputRef.current.focus();
      }
      return;
    }
    if (key === "Delete") {
      event.preventDefault();
      if (selectedLink !== null) {
        if (!canEditSchedule) {
          refuse("依存を外す権限（schedule.edit）がありません");
          return;
        }
        const [p, s] = selectedLink.split("-").map(Number);
        const dependency = dependencies.find((d) => d.predecessor_id === p && d.successor_id === s);
        if (dependency === undefined) {
          refuse(WAIT_MESSAGE);
          return;
        }
        void removeDependency(dependency.id);
        return;
      }
      askDelete();
      return;
    }
    // Esc：矢印の選択・消すことの確かめを取り消す。何も無ければ行の選択を外す（最上位に足せるように）
    if (key === "Escape") {
      if (selectedLink !== null || pendingDeletion !== null) {
        setSelectedLink(null);
        setPendingDeletion(null);
      } else {
        setSelection([], null, null);
      }
    }
  }

  const ready = enabled && activities !== null;
  const chartEdit: GanttChartEdit | undefined = ready
    ? {
        selected,
        cursor,
        renaming,
        selectedLink,
        requestedStart,
        canEditSchedule,
        onSelectRow,
        onStartRename: startRename,
        onRename,
        onCancelRename: () => setRenaming(null),
        onSelectLink: (link) => {
          setSelectedLink(link);
          setPendingDeletion(null);
        },
        onKeyDown,
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
    activities: list,
    dependencies,
    workPackages,
    addTarget: target,
    addInputRef,
    confirmingDelete: pendingDeletion !== null,
    deleteSummary: pendingDeletion?.summary ?? "",
    rename: () => (cursor === null ? refuse("名前を変える行を選びます") : startRename(cursor)),
    indent: () => moveElementBy(indentPlan, "インデントした"),
    outdent: () => moveElementBy(outdentPlan, "アウトデントした"),
    stepUp: () => step(-1),
    stepDown: () => step(1),
    askDelete,
    cancelDelete: () => setPendingDeletion(null),
    confirmDelete,
    addRow,
    groupSelected,
    moveActivityTo,
    setRequestedStart,
    setDuration,
    addSuccessor,
    removeDependency,
  };
}

/** 落とした日（希望開始日）と計算した開始が違えば、その旨を示す（7.3） */
function startNotice(date: string, saved: Activity): string {
  if (saved.start_date !== null && saved.start_date !== date) {
    return `希望開始日を ${date} にした。計算した開始は ${saved.start_date}（先行の終わり・非稼働日による）`;
  }
  return `開始を ${date} にした`;
}
