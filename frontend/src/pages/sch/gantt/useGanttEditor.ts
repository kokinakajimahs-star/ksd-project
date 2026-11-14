// ガントの編集（設計書 7.3 の「編集」。CR-024）の状態と操作。どの操作も既存の API を呼んでその場で保存し、
// 保存の後に onChanged で呼び手（GanttPage）にガント・WBS・活動を読み直させる。元に戻す操作は無い。
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
  busy: boolean;
  error: ErrorView | null;
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

function partialFailure(error: unknown, moved: number, left: string[], what: string): PartialFailure {
  const view = toErrorView(error);
  return new PartialFailure({
    message: `${view.message}（${moved} 件は移した。移せなかった${what}：${left.join("、")}）`,
    details: view.details,
  });
}

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
  const { projectId, enabled, gantt, outline, canEditSchedule, canEditWbs, reloadKey, onChanged } = input;
  const elements = useMemo(() => input.elements ?? [], [input.elements]);
  const [activities, setActivities] = useState<Activity[] | null>(null);
  const [dependencies, setDependencies] = useState<Dependency[]>([]);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [cursor, setCursor] = useState<string | null>(null);
  const [anchor, setAnchor] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [selectedLink, setSelectedLink] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorView | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const addInputRef = useRef<HTMLInputElement>(null);
  // 操作を重ねて流さない（state は次の描画まで変わらないので ref でも見る）
  const busyRef = useRef(false);

  // 活動の一覧（並びの位置・希望開始日・依存の ID に要る）
  useEffect(() => {
    if (!enabled) {
      return;
    }
    let cancelled = false;
    loadActivities(projectId).then(
      (result) => {
        if (!cancelled) {
          setActivities(result.items);
          setDependencies(result.dependencies);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setActivities(null);
          setError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, enabled, reloadKey]);

  // 編集を終えたら選択と確かめを消す
  useEffect(() => {
    if (!enabled) {
      setSelected(new Set());
      setCursor(null);
      setAnchor(null);
      setRenaming(null);
      setSelectedLink(null);
      setConfirmingDelete(false);
      setMessage(null);
      setError(null);
    }
  }, [enabled]);

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

  const target = useMemo(() => addTarget(elements, list, cursor), [elements, list, cursor]);
  const plannedDeletion = useMemo(() => deletionPlan(elements, list, selected), [elements, list, selected]);
  const deleteSummary = [
    plannedDeletion.elementIds.length > 0 ? `要素 ${plannedDeletion.elementIds.length} 件（配下の要素と活動ごと）` : "",
    plannedDeletion.activityIds.length > 0 ? `活動 ${plannedDeletion.activityIds.length} 件` : "",
  ]
    .filter((t) => t !== "")
    .join("と");

  /** 1 つの操作を流す：誤りは示し、成否に関わらず読み直す */
  async function run(action: () => Promise<string | null>): Promise<boolean> {
    if (busyRef.current) {
      return false;
    }
    busyRef.current = true;
    setBusy(true);
    setError(null);
    setMessage(null);
    let ok = false;
    try {
      setMessage(await action());
      ok = true;
    } catch (e) {
      setError(e instanceof PartialFailure ? e.view : toErrorView(e));
    } finally {
      busyRef.current = false;
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

  function selectOnly(key: string) {
    setSelected(new Set([key]));
    setCursor(key);
    setAnchor(key);
    setSelectedLink(null);
  }

  function onSelectRow(key: string, how: SelectHow) {
    setSelectedLink(null);
    setConfirmingDelete(false);
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
      return;
    }
    setSelected(new Set(rangeKeys(outline, anchor ?? key, key)));
    setCursor(key);
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
    if (selected.size === 0 || (plannedDeletion.elementIds.length === 0 && plannedDeletion.activityIds.length === 0)) {
      refuse("消す行を選びます");
      return;
    }
    if ((plannedDeletion.elementIds.length > 0 && !canEditWbs) || (plannedDeletion.activityIds.length > 0 && !canEditSchedule)) {
      refuse("選んだ行を消す権限がありません");
      return;
    }
    setConfirmingDelete(true);
  }

  async function confirmDelete() {
    setConfirmingDelete(false);
    const plan = plannedDeletion;
    const ok = await run(async () => {
      for (const id of plan.elementIds) {
        await deleteElement(projectId, id);
      }
      for (const id of plan.activityIds) {
        await deleteActivity(projectId, id);
      }
      return `${deleteSummary}を消した`;
    });
    if (ok) {
      setSelected(new Set());
      setCursor(null);
      setAnchor(null);
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
    if (selection.kind === "none" || selection.kind === "element" || (selection.kind === "many" && selection.elementIds.length > 0 && selection.activityIds.length > 0)) {
      refuse("活動だけ、または要素だけを選んでまとめます");
      return false;
    }
    const activityIds = selection.kind === "activity" ? [selection.activity.id] : selection.activityIds;
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
        await moveElement(projectId, wp.id, plan.value.parentId, plan.value.position);
        let moved = 0;
        try {
          for (const id of plan.value.ids) {
            await updateActivity(projectId, id, { work_package_id: wp.id });
            moved += 1;
          }
        } catch (e) {
          const left = plan.value.ids.slice(moved).map((id) => list.find((a) => a.id === id)?.name ?? String(id));
          throw partialFailure(e, moved, left, "活動");
        }
        return `活動 ${moved} 件を新しい WP「${trimmed}」にまとめた`;
      });
    }
    if (!canEditWbs) {
      refuse("要素をまとめる権限（wbs.edit）がありません");
      return false;
    }
    const plan = groupElementsPlan(elements, selection.kind === "many" ? selection.elementIds : []);
    if (!plan.ok) {
      refuse(plan.reason);
      return false;
    }
    return run(async () => {
      const parent = await createElement(projectId, { parent_id: plan.value.parentId, kind, name: trimmed });
      await moveElement(projectId, parent.id, plan.value.parentId, plan.value.position);
      let moved = 0;
      try {
        for (const id of plan.value.ids) {
          await moveElement(projectId, id, parent.id, null);
          moved += 1;
        }
      } catch (e) {
        const left = plan.value.ids.slice(moved).map((id) => elements.find((el) => el.id === id)?.wbs_code ?? String(id));
        throw partialFailure(e, moved, left, "要素");
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
        setSelected(new Set(rangeKeys(outline, anchor ?? next, next)));
        setCursor(next);
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
      addInputRef.current?.focus();
      return;
    }
    if (key === "Delete") {
      event.preventDefault();
      if (selectedLink !== null) {
        const [p, s] = selectedLink.split("-").map(Number);
        const dependency = dependencies.find((d) => d.predecessor_id === p && d.successor_id === s);
        if (dependency !== undefined && canEditSchedule) {
          void removeDependency(dependency.id);
        }
        return;
      }
      askDelete();
      return;
    }
    if (key === "Escape") {
      setSelectedLink(null);
      setConfirmingDelete(false);
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
          setConfirmingDelete(false);
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
    busy,
    error,
    message,
    selection,
    activities: list,
    dependencies,
    workPackages,
    addTarget: target,
    addInputRef,
    confirmingDelete,
    deleteSummary,
    rename: () => (cursor === null ? refuse("名前を変える行を選びます") : startRename(cursor)),
    indent: () => moveElementBy(indentPlan, "インデントした"),
    outdent: () => moveElementBy(outdentPlan, "アウトデントした"),
    stepUp: () => step(-1),
    stepDown: () => step(1),
    askDelete,
    cancelDelete: () => setConfirmingDelete(false),
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
