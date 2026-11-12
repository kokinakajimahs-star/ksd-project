// 変更内容（設計書 5.9 の操作の配列）を、入力欄の値から組み立てる・1 行で説明する。
// 形の検査の正はサーバ（RULE-CHG-001）。ここでは必須の欄の空と、数の形だけを見て早めに知らせる
import type { ChangeOperation } from "../../api/chg";
import type { ElementKind } from "../../api/wbs";
import { elementKindLabel } from "./labels";

export type OperationName =
  | "add_element"
  | "update_element"
  | "remove_element"
  | "add_activity"
  | "update_activity"
  | "remove_activity"
  | "add_dependency"
  | "remove_dependency";

/** 5.9 の表の順 */
export const OPERATION_NAMES: OperationName[] = [
  "add_element",
  "update_element",
  "remove_element",
  "add_activity",
  "update_activity",
  "remove_activity",
  "add_dependency",
  "remove_dependency",
];

/** 5.9 の「意味」 */
export const OPERATION_LABELS: Record<OperationName, string> = {
  add_element: "要素を足す",
  update_element: "要素を変える",
  remove_element: "要素を配下ごと消す",
  add_activity: "活動を足す",
  update_activity: "活動を変える",
  remove_activity: "活動を消す（依存も消す）",
  add_dependency: "依存を足す",
  remove_dependency: "依存を消す",
};

/** 入力欄の値（文字のまま持ち、組み立てるときに直す） */
export interface OperationDraft {
  op: OperationName;
  /** add_element：親の WBS ID（空はルート） */
  parentCode: string;
  /** add_element は必須。update_element は空なら変えない */
  kind: ElementKind | "";
  /** add_element・add_activity は必須。update_* は空なら変えない */
  name: string;
  /** 新しく作るものの仮の名前（add_element・add_activity） */
  ref: string;
  /** update_element・remove_element：対象の WBS ID */
  code: string;
  ownEstimateHours: string;
  responsibleUserId: string;
  /** add_activity の置き場：既存の WP の WBS ID か、この変更要求で足す WP の仮の名前 */
  wpBy: "code" | "ref";
  wpCode: string;
  wpRef: string;
  durationDays: string;
  estimateHours: string;
  requestedStart: string;
  /** update_activity・remove_activity：対象の活動の ID */
  activityId: string;
  /** 依存の端：活動の ID（数字だけ）か、この変更要求で足す活動の仮の名前 */
  predecessor: string;
  successor: string;
}

export function emptyDraft(op: OperationName = "add_element"): OperationDraft {
  return {
    op,
    parentCode: "",
    kind: "",
    name: "",
    ref: "",
    code: "",
    ownEstimateHours: "",
    responsibleUserId: "",
    wpBy: "code",
    wpCode: "",
    wpRef: "",
    durationDays: "",
    estimateHours: "",
    requestedStart: "",
    activityId: "",
    predecessor: "",
    successor: "",
  };
}

export type BuildResult = { ok: true; operation: ChangeOperation } | { ok: false; message: string };

class DraftError extends Error {}

/** 項目名が英数字で終わるとき（「WBS ID」など）は、助詞の前に空白を入れる */
function subject(label: string): string {
  return /[A-Za-z0-9]$/.test(label) ? `${label} ` : label;
}

function required(value: string, label: string): string {
  const text = value.trim();
  if (text === "") {
    throw new DraftError(`${subject(label)}を入力してください`);
  }
  return text;
}

/** 人時（0 以上の数）。空は null（未見積） */
function hours(value: string, label: string): number | null {
  const text = value.trim();
  if (text === "") {
    return null;
  }
  const number = Number(text);
  if (!Number.isFinite(number) || number < 0) {
    throw new DraftError(`${subject(label)}は 0 以上の数で入力してください`);
  }
  return number;
}

function integer(value: string, label: string, min: number): number {
  const text = required(value, label);
  if (!/^\d+$/.test(text) || Number(text) < min) {
    throw new DraftError(`${subject(label)}は ${min} 以上の整数で入力してください`);
  }
  return Number(text);
}

/** 依存の端：数字だけなら活動の ID、それ以外は仮の名前（5.9：活動の id か ref） */
export function activityEndpoint(value: string): number | string {
  const text = value.trim();
  return /^\d+$/.test(text) ? Number(text) : text;
}

function build(d: OperationDraft): ChangeOperation {
  switch (d.op) {
    case "add_element": {
      if (d.kind === "") {
        throw new DraftError("区分を選んでください");
      }
      return {
        op: d.op,
        parent_code: d.parentCode.trim(),
        kind: d.kind,
        name: required(d.name, "名称"),
        ref: required(d.ref, "仮の名前"),
      };
    }
    case "update_element": {
      const fields: Record<string, unknown> = {};
      if (d.name.trim() !== "") {
        fields.name = d.name.trim();
      }
      if (d.kind !== "") {
        fields.kind = d.kind;
      }
      const own = hours(d.ownEstimateHours, "自分の見積");
      if (own !== null) {
        fields.own_estimate_hours = own;
      }
      if (d.responsibleUserId !== "") {
        fields.responsible_user_id = integer(d.responsibleUserId, "責任者", 1);
      }
      if (Object.keys(fields).length === 0) {
        throw new DraftError("変える項目（名称・区分・自分の見積・責任者）を 1 つ以上入力してください");
      }
      return { op: d.op, code: required(d.code, "WBS ID"), fields };
    }
    case "remove_element":
      return { op: d.op, code: required(d.code, "WBS ID") };
    case "add_activity": {
      const operation: ChangeOperation = { op: d.op };
      if (d.wpBy === "code") {
        operation.wp_code = required(d.wpCode, "置き場の WP の WBS ID");
      } else {
        operation.wp_ref = required(d.wpRef, "置き場の WP の仮の名前");
      }
      operation.name = required(d.name, "名称");
      operation.duration_days = integer(d.durationDays, "期間（稼働日）", 0);
      operation.estimate_hours = hours(d.estimateHours, "見積");
      operation.ref = required(d.ref, "仮の名前");
      if (d.requestedStart !== "") {
        operation.requested_start = d.requestedStart;
      }
      return operation;
    }
    case "update_activity": {
      const fields: Record<string, unknown> = {};
      if (d.name.trim() !== "") {
        fields.name = d.name.trim();
      }
      if (d.durationDays.trim() !== "") {
        fields.duration_days = integer(d.durationDays, "期間（稼働日）", 0);
      }
      const estimate = hours(d.estimateHours, "見積");
      if (estimate !== null) {
        fields.estimate_hours = estimate;
      }
      if (d.requestedStart !== "") {
        fields.requested_start = d.requestedStart;
      }
      if (Object.keys(fields).length === 0) {
        throw new DraftError("変える項目（名称・期間・見積・希望開始日）を 1 つ以上入力してください");
      }
      return { op: d.op, activity_id: integer(d.activityId, "活動の ID", 1), fields };
    }
    case "remove_activity":
      return { op: d.op, activity_id: integer(d.activityId, "活動の ID", 1) };
    case "add_dependency":
    case "remove_dependency":
      return {
        op: d.op,
        predecessor: activityEndpoint(required(d.predecessor, "先行")),
        successor: activityEndpoint(required(d.successor, "後続")),
      };
  }
}

/** 入力欄の値から 5.9 の操作 1 件を作る。欠けがあれば理由を返す */
export function buildOperation(draft: OperationDraft): BuildResult {
  try {
    return { ok: true, operation: build(draft) };
  } catch (e) {
    if (e instanceof DraftError) {
      return { ok: false, message: e.message };
    }
    throw e;
  }
}

function text(value: unknown): string {
  return typeof value === "string" || typeof value === "number" ? String(value) : JSON.stringify(value);
}

function endpoint(value: unknown): string {
  return typeof value === "number" ? `活動 #${value}` : `「${text(value)}」`;
}

const FIELD_LABELS: Record<string, string> = {
  name: "名称",
  kind: "区分",
  own_estimate_hours: "自分の見積",
  responsible_user_id: "責任者",
  duration_days: "期間",
  estimate_hours: "見積",
  requested_start: "希望開始日",
};

function fieldsText(fields: unknown): string {
  if (typeof fields !== "object" || fields === null) {
    return text(fields);
  }
  return Object.entries(fields as Record<string, unknown>)
    .map(([key, value]) => `${FIELD_LABELS[key] ?? key}＝${key === "kind" ? elementKindLabel(text(value)) : text(value)}`)
    .join("、");
}

/** 操作 1 件を 1 行で説明する。知らない形は JSON のまま */
export function describeOperation(operation: ChangeOperation): string {
  const o = operation;
  switch (o.op) {
    case "add_element": {
      const parent = typeof o.parent_code === "string" && o.parent_code !== "" ? o.parent_code : "ルート";
      return `要素を足す：${parent} の下に「${text(o.name)}」（${elementKindLabel(text(o.kind))}、仮の名前 ${text(o.ref)}）`;
    }
    case "update_element":
      return `要素を変える：${text(o.code)}（${fieldsText(o.fields)}）`;
    case "remove_element":
      return `要素を配下ごと消す：${text(o.code)}`;
    case "add_activity": {
      const place = "wp_code" in o ? text(o.wp_code) : `仮の名前 ${text(o.wp_ref)} の WP`;
      const estimate = o.estimate_hours === null || o.estimate_hours === undefined ? "未見積" : `${text(o.estimate_hours)} 人時`;
      const start = typeof o.requested_start === "string" ? `、希望開始 ${o.requested_start}` : "";
      return `活動を足す：${place} に「${text(o.name)}」（${text(o.duration_days)} 稼働日、${estimate}${start}、仮の名前 ${text(o.ref)}）`;
    }
    case "update_activity":
      return `活動を変える：活動 #${text(o.activity_id)}（${fieldsText(o.fields)}）`;
    case "remove_activity":
      return `活動を消す：活動 #${text(o.activity_id)}（依存も消す）`;
    case "add_dependency":
      return `依存を足す：${endpoint(o.predecessor)} → ${endpoint(o.successor)}`;
    case "remove_dependency":
      return `依存を消す：${endpoint(o.predecessor)} → ${endpoint(o.successor)}`;
    default:
      return JSON.stringify(o);
  }
}
