// ガントの編集（設計書 7.3 の「編集」。CR-024）で、操作をどの API の引数にするかを決める純粋な関数。
// 位置は API-WBS-004・API-SCH-003 と同じ数え方：移す先の兄弟（自分を除く）の中の位置（0 から）
import type { Activity } from "../../../api/sch";
import type { ElementKind, WbsElement } from "../../../api/wbs";
import { activityKey, elementKey, type OutlineRow } from "../../../components/gantt/outline";

export type Planned<T> = { ok: true; value: T } | { ok: false; reason: string };

function ok<T>(value: T): Planned<T> {
  return { ok: true, value };
}

function no<T>(reason: string): Planned<T> {
  return { ok: false, reason };
}

/** 兄弟（表示順。API-WBS-001 の先行順のまま） */
export function siblingsOf(elements: WbsElement[], parentId: number | null): WbsElement[] {
  return elements.filter((e) => e.parent_id === parentId);
}

export interface ElementMove {
  parentId: number | null;
  /** null は末尾 */
  position: number | null;
}

/** インデント：直前の兄弟の子の末尾へ（直前の兄弟が WP なら置けない。RULE-WBS-003） */
export function indentPlan(elements: WbsElement[], elementId: number): Planned<ElementMove> {
  const element = elements.find((e) => e.id === elementId);
  if (element === undefined) {
    return no("要素が見つかりません");
  }
  const siblings = siblingsOf(elements, element.parent_id);
  const index = siblings.findIndex((e) => e.id === elementId);
  const previous = index > 0 ? siblings[index - 1] : undefined;
  if (previous === undefined) {
    return no("上に同じ階層の要素が無いので、インデントできません");
  }
  if (previous.kind === "WORK_PACKAGE") {
    return no(`直前の要素（${previous.wbs_code} ${previous.name}）はワークパッケージなので、その下には置けません`);
  }
  return ok({ parentId: previous.id, position: null });
}

/** アウトデント：親の次の兄弟へ */
export function outdentPlan(elements: WbsElement[], elementId: number): Planned<ElementMove> {
  const element = elements.find((e) => e.id === elementId);
  if (element === undefined) {
    return no("要素が見つかりません");
  }
  const parent = element.parent_id === null ? undefined : elements.find((e) => e.id === element.parent_id);
  if (parent === undefined) {
    return no("最上位の要素はアウトデントできません");
  }
  const parentSiblings = siblingsOf(elements, parent.parent_id);
  return ok({ parentId: parent.parent_id, position: parentSiblings.findIndex((e) => e.id === parent.id) + 1 });
}

/** 要素を兄弟の中で 1 つ上（-1）・下（+1）へ */
export function elementStepPlan(elements: WbsElement[], elementId: number, step: -1 | 1): Planned<ElementMove> {
  const element = elements.find((e) => e.id === elementId);
  if (element === undefined) {
    return no("要素が見つかりません");
  }
  const siblings = siblingsOf(elements, element.parent_id);
  const index = siblings.findIndex((e) => e.id === elementId);
  const next = index + step;
  if (next < 0 || next >= siblings.length) {
    return no(step < 0 ? "これより上へは動かせません" : "これより下へは動かせません");
  }
  return ok({ parentId: element.parent_id, position: next });
}

/** 活動を同じ WP の中で 1 つ上（-1）・下（+1）へ（API-SCH-003 の position）。activities は API-SCH-001 の順 */
export function activityStepPlan(activities: Activity[], activityId: number, step: -1 | 1): Planned<number> {
  const activity = activities.find((a) => a.id === activityId);
  if (activity === undefined) {
    return no("活動が見つかりません");
  }
  const siblings = activities.filter((a) => a.work_package_id === activity.work_package_id);
  const next = siblings.findIndex((a) => a.id === activityId) + step;
  if (next < 0 || next >= siblings.length) {
    return no(step < 0 ? "WP の中でこれより上へは動かせません" : "WP の中でこれより下へは動かせません");
  }
  return ok(next);
}

export interface GroupPlan {
  /** 新しい要素の親と位置（API-WBS-002 で作り、API-WBS-004 で位置を決める） */
  parentId: number | null;
  position: number;
  /** 移す順（表示順） */
  ids: number[];
}

/** 選んだ活動を新しい WP にまとめる：最初の活動の WP と同じ親の下、その WP の次に作る */
export function groupActivitiesPlan(elements: WbsElement[], activities: Activity[], selected: number[]): Planned<GroupPlan> {
  const chosen = activities.filter((a) => selected.includes(a.id));
  const first = chosen[0];
  if (first === undefined) {
    return no("まとめる活動を選びます");
  }
  const wp = elements.find((e) => e.id === first.work_package_id);
  if (wp === undefined) {
    return no("活動の WP が WBS に見つかりません");
  }
  const position = siblingsOf(elements, wp.parent_id).findIndex((e) => e.id === wp.id) + 1;
  return ok({ parentId: wp.parent_id, position, ids: chosen.map((a) => a.id) });
}

function isDescendant(elements: WbsElement[], id: number, ancestorIds: Set<number>): boolean {
  let current = elements.find((e) => e.id === id);
  while (current !== undefined && current.parent_id !== null) {
    if (ancestorIds.has(current.parent_id)) {
      return true;
    }
    const parentId: number = current.parent_id;
    current = elements.find((e) => e.id === parentId);
  }
  return false;
}

/** 選んだ要素を新しい上位の要素にまとめる：最初の要素と同じ親の下、最初の要素の位置に作る。選んだ要素の配下は一緒に動くので外す */
export function groupElementsPlan(elements: WbsElement[], selected: number[]): Planned<GroupPlan> {
  const chosen = elements.filter((e) => selected.includes(e.id));
  const first = chosen[0];
  if (first === undefined) {
    return no("まとめる要素を選びます");
  }
  const ids = new Set(chosen.map((e) => e.id));
  const roots = chosen.filter((e) => !isDescendant(elements, e.id, ids));
  const position = siblingsOf(elements, first.parent_id).findIndex((e) => e.id === first.id);
  return ok({ parentId: first.parent_id, position, ids: roots.map((e) => e.id) });
}

/** 表示の行の index の行と、その配下（続く深い行）の最後の位置 */
export function subtreeEnd(outline: OutlineRow[], index: number): number {
  const depth = outline[index]?.depth ?? 0;
  let end = index;
  while (end + 1 < outline.length && outline[end + 1]!.depth > depth) {
    end += 1;
  }
  return end;
}

/**
 * 行を足す先（設計書 7.3。CR-025）と、名前の入力の行を差し込む表示の位置（index）・字下げの深さ（depth）。
 * position は足した後に置く位置（API-SCH-003・API-WBS-004 の数え方。null は末尾のまま）
 */
export type AddPlan = (
  | { kind: "activity"; workPackage: WbsElement; position: number | null }
  | { kind: "element"; parent: WbsElement | null; elementKind: ElementKind; position: number | null }
) & { index: number; depth: number };

/**
 * 選んだ行（key）に足す先：活動の行ならその活動の次（同じ WP）に活動を、WP の行ならその WP の末尾に活動を、
 * WP 以外の要素の行なら子の要素（WP）を、何も選んでいなければ最上位の要素（WP）を。
 * sibling なら要素の行の下に同じ階層の要素（区分は選んだ要素と同じ）を足す
 */
export function addPlan(
  elements: WbsElement[],
  activities: Activity[],
  outline: OutlineRow[],
  key: string | null,
  sibling = false,
): Planned<AddPlan> {
  const index = key === null ? -1 : outline.findIndex((r) => r.key === key);
  const row = index < 0 ? undefined : outline[index];
  if (row === undefined) {
    return ok({ kind: "element", parent: null, elementKind: "WORK_PACKAGE", position: null, index: outline.length, depth: 1 });
  }
  if (row.kind === "activity") {
    const activity = activities.find((a) => a.id === row.row.activity_id);
    const wp = activity === undefined ? undefined : elements.find((e) => e.id === activity.work_package_id);
    if (activity === undefined || wp === undefined) {
      return no("活動の WP が WBS に見つかりません");
    }
    const siblings = activities.filter((a) => a.work_package_id === wp.id);
    const next = siblings.findIndex((a) => a.id === activity.id) + 1;
    return ok({ kind: "activity", workPackage: wp, position: next < siblings.length ? next : null, index: index + 1, depth: row.depth });
  }
  const element = row.element;
  const end = subtreeEnd(outline, index) + 1;
  if (sibling) {
    const siblings = siblingsOf(elements, element.parent_id);
    const next = siblings.findIndex((e) => e.id === element.id) + 1;
    const parent = element.parent_id === null ? null : (elements.find((e) => e.id === element.parent_id) ?? null);
    return ok({
      kind: "element",
      parent,
      elementKind: element.kind,
      position: next < siblings.length ? next : null,
      index: end,
      depth: row.depth,
    });
  }
  if (element.kind === "WORK_PACKAGE") {
    return ok({ kind: "activity", workPackage: element, position: null, index: end, depth: row.depth + 1 });
  }
  return ok({ kind: "element", parent: element, elementKind: "WORK_PACKAGE", position: null, index: end, depth: row.depth + 1 });
}

export interface ActivityMove {
  workPackage: WbsElement;
  /** 移す先の WP の中の位置（自分を除いた活動の中。API-SCH-003 の position） */
  position: number;
}

/** 活動を移す先（設計書 7.3。CR-025）：WP の行ならその WP の末尾へ、活動の行ならその活動の次へ */
export function moveTargetPlan(elements: WbsElement[], activities: Activity[], activityId: number, key: string): Planned<ActivityMove> {
  if (!activities.some((a) => a.id === activityId)) {
    return no("活動が見つかりません");
  }
  let wp: WbsElement | undefined;
  let after: number | null = null;
  if (key.startsWith("a")) {
    const target = activities.find((a) => activityKey(a.id) === key);
    if (target === undefined || target.id === activityId) {
      return no("移す先に、別の活動の行か WP の行を選びます");
    }
    wp = elements.find((e) => e.id === target.work_package_id);
    after = target.id;
  } else {
    wp = elements.find((e) => elementKey(e.id) === key);
    if (wp !== undefined && wp.kind !== "WORK_PACKAGE") {
      return no(`${wp.wbs_code} ${wp.name} はワークパッケージではないので、活動を置けません（RULE-WBS-003）`);
    }
  }
  if (wp === undefined) {
    return no("移す先の WP が WBS に見つかりません");
  }
  const workPackageId = wp.id;
  const others = activities.filter((a) => a.work_package_id === workPackageId && a.id !== activityId);
  const position = after === null ? others.length : others.findIndex((a) => a.id === after) + 1;
  return ok({ workPackage: wp, position });
}

/** 上下の矢印で選ぶ次の行 */
export function stepSelection(outline: OutlineRow[], key: string | null, step: -1 | 1): string | null {
  if (outline.length === 0) {
    return null;
  }
  const index = key === null ? -1 : outline.findIndex((r) => r.key === key);
  if (index < 0) {
    return outline[step > 0 ? 0 : outline.length - 1]!.key;
  }
  return outline[Math.min(outline.length - 1, Math.max(0, index + step))]!.key;
}

/** 選んだ行の範囲（Shift＋クリック）：表示順で from から to まで */
export function rangeKeys(outline: OutlineRow[], from: string, to: string): string[] {
  const a = outline.findIndex((r) => r.key === from);
  const b = outline.findIndex((r) => r.key === to);
  if (a < 0 || b < 0) {
    return [to];
  }
  return outline.slice(Math.min(a, b), Math.max(a, b) + 1).map((r) => r.key);
}

export interface DeletionPlan {
  /** 消す活動（選んだ活動の全て。要素より先に 1 件ずつ消す。API-SCH-005） */
  activityIds: number[];
  /** 消す要素（配下ごと消えるので、選んだ要素の配下の要素は入れない。API-WBS-005） */
  elementIds: number[];
  /** 選んでいない活動が残る WP（API-WBS-005 は活動のある WP を配下に含む要素を 422 で拒む。OQ-18 (1)） */
  blockedWorkPackages: WbsElement[];
}

/** 選んだ行を消す順：活動を先に 1 件ずつ、次に要素を配下ごと。選んでいない活動を配下に残す要素は消さない */
export function deletionPlan(elements: WbsElement[], activities: Activity[], keys: Iterable<string>): DeletionPlan {
  const chosen = new Set(keys);
  const selectedElements = elements.filter((e) => chosen.has(elementKey(e.id)));
  const ids = new Set(selectedElements.map((e) => e.id));
  const elementIds = selectedElements.filter((e) => !isDescendant(elements, e.id, ids)).map((e) => e.id);
  const activityIds = activities.filter((a) => chosen.has(activityKey(a.id))).map((a) => a.id);
  const blocked = new Set(
    activities
      .filter((a) => !chosen.has(activityKey(a.id)))
      .filter((a) => ids.has(a.work_package_id) || isDescendant(elements, a.work_package_id, ids))
      .map((a) => a.work_package_id),
  );
  return { activityIds, elementIds, blockedWorkPackages: elements.filter((e) => blocked.has(e.id)) };
}
