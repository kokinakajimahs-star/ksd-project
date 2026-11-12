// タスクリストの一括編集（SCH-02。API-SCH-004）。画面の入力（下書き）から、変えた活動の変えた項目だけを作る。
// 全件の検査（RULE-SCH-006：循環・日付・担当など）はサーバが行い、1 件でも不正なら何も保存しない。
// 画面は送れない形（数でない期間・活動 ID）だけを先に止める
import type { Activity, ActivityChange } from "../../../api/sch";
import { DURATION_MAX } from "../limits";

export interface ActivityDraft {
  name: string;
  /** 希望開始日（YYYY-MM-DD）。空は指定なし */
  requestedStart: string;
  /** 期間（稼働日）。0 はマイルストーン */
  duration: string;
  /** 先行の活動 ID（`;`・`,`・空白で区切る） */
  predecessors: string;
  assigneeIds: number[];
}

export function draftOf(activity: Activity): ActivityDraft {
  return {
    name: activity.name,
    requestedStart: activity.requested_start ?? "",
    duration: String(activity.duration_days),
    predecessors: activity.predecessor_ids.join(";"),
    assigneeIds: activity.assignees.map((a) => a.user_id),
  };
}

/** 活動 ID の並び。空は []。数でないものがあれば null */
export function parseIdList(text: string): number[] | null {
  const parts = text
    .split(/[;,\s]+/)
    .map((p) => p.trim())
    .filter((p) => p !== "");
  const ids: number[] = [];
  for (const part of parts) {
    if (!/^[1-9]\d*$/.test(part)) {
      return null;
    }
    const id = Number(part);
    if (!ids.includes(id)) {
      ids.push(id);
    }
  }
  return ids;
}

function sameSet(a: number[], b: number[]): boolean {
  if (a.length !== b.length) {
    return false;
  }
  const set = new Set(a);
  return b.every((x) => set.has(x));
}

export interface CollectedChanges {
  changes: ActivityChange[];
  /** 送れない入力（行ごとの理由） */
  errors: string[];
}

/** 下書きと今の活動を比べ、変えた項目だけの変更を作る。下書きの無い活動は変えない */
export function collectChanges(activities: Activity[], drafts: Record<number, ActivityDraft>): CollectedChanges {
  const changes: ActivityChange[] = [];
  const errors: string[] = [];
  for (const activity of activities) {
    const draft = drafts[activity.id];
    if (draft === undefined) {
      continue;
    }
    const label = `活動 ${activity.id}（${activity.name}）`;
    const change: ActivityChange = { activity_id: activity.id };

    const name = draft.name.trim();
    if (name === "") {
      errors.push(`${label}：名称を入力してください`);
    } else if (name !== activity.name) {
      change.name = name;
    }

    const requestedStart = draft.requestedStart === "" ? null : draft.requestedStart;
    if (requestedStart !== activity.requested_start) {
      change.requested_start = requestedStart;
    }

    const durationText = draft.duration.trim();
    if (!/^\d+$/.test(durationText) || Number(durationText) > DURATION_MAX) {
      errors.push(`${label}：期間は 0 以上 ${DURATION_MAX} 以下の整数（稼働日）で入力してください`);
    } else if (Number(durationText) !== activity.duration_days) {
      change.duration_days = Number(durationText);
    }

    const predecessors = parseIdList(draft.predecessors);
    if (predecessors === null) {
      errors.push(`${label}：先行は活動 ID を ; で区切って入力してください`);
    } else if (!sameSet(predecessors, activity.predecessor_ids)) {
      change.predecessor_ids = predecessors;
    }

    if (!sameSet(draft.assigneeIds, activity.assignees.map((a) => a.user_id))) {
      change.assignee_user_ids = [...draft.assigneeIds];
    }

    if (Object.keys(change).length > 1) {
      changes.push(change);
    }
  }
  return { changes, errors };
}

/** 割当工数の合計（人時。空は 0 として足す）と見積との差（見積が無ければ null）。EFF-01 */
export function assignmentTotals(
  plannedHours: (number | null)[],
  estimateHours: number | null,
): { total: number; difference: number | null } {
  const total = Math.round(plannedHours.reduce<number>((sum, h) => sum + (h ?? 0), 0) * 100) / 100;
  return { total, difference: estimateHours === null ? null : Math.round((total - estimateHours) * 100) / 100 };
}
