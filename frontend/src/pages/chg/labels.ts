// 変更管理の画面（UI-CHG-001〜003）の呼び名・上限・小さな計算
import type { AuditAction, ChangeRequestStatus, CorrectiveActionStatus } from "../../api/chg";
import type { ElementKind } from "../../api/wbs";

/** 一覧の 1 頁の件数（6.1：limit の既定 100） */
export const CHG_PAGE_SIZE = 100;
/** 選択肢に読む一覧の件数（6.1：limit の上限 1000） */
export const LIST_LIMIT_MAX = 1000;
/** 変更要求の件名（4.1 の change_requests.title。backend/app/schemas/change.py の max_length） */
export const CHANGE_TITLE_MAX = 200;
/** WBS ID（backend/app/schemas/change.py の wbs_code の max_length） */
export const WBS_CODE_MAX = 100;
/** 対象の種類（4.1 の audit_logs.entity_type。backend/app/schemas/change.py の max_length） */
export const ENTITY_TYPE_MAX = 64;

/** 変更要求の状態（5.10） */
export const CHANGE_STATUS_LABELS: Record<ChangeRequestStatus, string> = {
  DRAFT: "下書き",
  SUBMITTED: "提出済み（承認待ち）",
  APPROVED: "承認済み",
  REJECTED: "却下",
  APPLIED: "反映済み",
};

export const CHANGE_STATUSES: ChangeRequestStatus[] = ["DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "APPLIED"];

/** 是正措置の状態（CHG-06：未着手・対応中・完了） */
export const ACTION_STATUS_LABELS: Record<CorrectiveActionStatus, string> = {
  NOT_STARTED: "未着手",
  IN_PROGRESS: "対応中",
  DONE: "完了",
};

export const ACTION_STATUSES: CorrectiveActionStatus[] = ["NOT_STARTED", "IN_PROGRESS", "DONE"];

/**
 * 今の状態から選べる状態（RULE-CHG-005：NOT_STARTED → IN_PROGRESS → DONE、DONE → IN_PROGRESS に戻せる）。
 * 今の状態のまま（効果だけを直す）も選べる。正の判定はサーバが行う
 */
export function nextActionStatuses(current: CorrectiveActionStatus): CorrectiveActionStatus[] {
  switch (current) {
    case "NOT_STARTED":
      return ["NOT_STARTED", "IN_PROGRESS"];
    case "IN_PROGRESS":
      return ["IN_PROGRESS", "DONE"];
    case "DONE":
      return ["DONE", "IN_PROGRESS"];
  }
}

/** 変更履歴の操作（4.1 の audit_logs.action） */
export const AUDIT_ACTION_LABELS: Record<AuditAction, string> = {
  CREATE: "追加",
  UPDATE: "更新",
  DELETE: "削除",
};

/** WBS の要素の区分（要求仕様書 WBS-02 の 4 値） */
export const ELEMENT_KIND_LABELS: Record<ElementKind, string> = {
  DELIVERABLE: "成果物",
  MANAGEMENT: "管理領域",
  COMPONENT: "構成要素",
  WORK_PACKAGE: "ワークパッケージ",
};

export const ELEMENT_KINDS: ElementKind[] = ["DELIVERABLE", "MANAGEMENT", "COMPONENT", "WORK_PACKAGE"];

/** 区分の呼び名。知らない値はそのまま */
export function elementKindLabel(kind: string): string {
  return ELEMENT_KIND_LABELS[kind as ElementKind] ?? kind;
}

/** 利用者の ID を名前にする（メンバーに居ない人は #ID）。空は — */
export function userLabel(names: ReadonlyMap<number, string>, userId: number | null): string {
  if (userId === null) {
    return "—";
  }
  return names.get(userId) ?? `#${userId}`;
}

/** 対象の WBS ID の入力（カンマ・読点・空白・改行で区切る）を、空と重複を除いた並びにする */
export function parseTargetCodes(text: string): string[] {
  const codes: string[] = [];
  for (const part of text.split(/[\s,、]+/)) {
    if (part !== "" && !codes.includes(part)) {
      codes.push(part);
    }
  }
  return codes;
}

/** 稼働日のずれの表示。正は + を付ける。空は — */
export function shiftLabel(days: number | null): string {
  if (days === null) {
    return "—";
  }
  return `${days > 0 ? "+" : ""}${days} 稼働日`;
}

/** 値を 1 行の文字にする（変更履歴・影響分析の前後の値）。空は — */
export function valueText(value: unknown): string {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}
