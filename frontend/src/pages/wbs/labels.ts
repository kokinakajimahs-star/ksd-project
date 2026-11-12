// WBS の画面（UI-WBS-001〜004）の呼び名。値はサーバ（backend/app/models/wbs.py・app/domain/wbs_rules.py・estimate.py）の値。
// 知らない値はそのまま出す（labelOf）
import type { EstimateMethod, InputUnit } from "../../api/est";
import type { CommentCategory, CommentStatus, CompletionField, ElementKind, FindingCode, FindingLevel } from "../../api/wbs";

export const ELEMENT_KIND_LABELS: Record<ElementKind, string> = {
  DELIVERABLE: "成果物",
  MANAGEMENT: "管理領域",
  COMPONENT: "構成要素",
  WORK_PACKAGE: "ワークパッケージ",
};

export const ELEMENT_KINDS = Object.keys(ELEMENT_KIND_LABELS) as ElementKind[];

/** WBS の状態（5.10） */
export const WBS_STATUS_LABELS: Record<string, string> = {
  DRAFT: "作成中",
  IN_REVIEW: "レビュー中",
  PENDING_APPROVAL: "承認待ち",
  BASELINED: "基準化済み",
};

export const ROLLUP_SOURCE_LABELS: Record<string, string> = {
  ACTIVITIES: "活動の見積の合計",
  OWN_ESTIMATE: "自分の見積",
  CHILDREN: "下位の合計",
};

export const FINDING_LEVEL_LABELS: Record<FindingLevel, string> = {
  ERROR: "エラー",
  WARNING: "警告",
};

/** 100% ルールの 3 つの判定（BR-03・RULE-WBS-004） */
export const FINDING_CODE_LABELS: Record<FindingCode, string> = {
  SUM_MISMATCH: "合計の差異",
  OUT_OF_SCOPE_LINKED: "対象外の混入",
  IN_SCOPE_UNCOVERED: "対象内の漏れ",
};

export const COMPLETION_FIELD_LABELS: Record<CompletionField, string> = {
  deliverable: "成果物",
  completion_criteria: "完了条件",
};

/** 指摘の分類（WBS-20） */
export const COMMENT_CATEGORY_LABELS: Record<CommentCategory, string> = {
  OMISSION: "漏れ",
  DUPLICATION: "重複",
  AMBIGUITY: "曖昧",
  OTHER: "その他",
};

/** 指摘の状態（WBS-20：未対応・対応済み・却下） */
export const COMMENT_STATUS_LABELS: Record<CommentStatus, string> = {
  OPEN: "未対応",
  RESOLVED: "対応済み",
  REJECTED: "却下",
};

export const REVIEW_STATUS_LABELS: Record<string, string> = {
  OPEN: "実施中",
  CLOSED: "終了",
};

/** スコープベースラインの版を作った理由（backend/app/models/wbs.py の SCOPE_BASELINE_REASONS） */
export const SCOPE_BASELINE_REASON_LABELS: Record<string, string> = {
  INITIAL_APPROVAL: "初回の承認",
  RE_APPROVAL: "再承認",
  CHANGE_REQUEST: "変更要求の反映",
};

/** 承認の申請の前提（422 WBS_NOT_READY の details の condition。app/domain/workflow.py の Readiness） */
export const READINESS_LABELS: Record<string, string> = {
  missing_completion: "成果物・完了条件が未定義の WP",
  open_comments: "未対応の指摘",
  hundred_percent_errors: "100% ルールのエラー",
};

export const INPUT_UNIT_LABELS: Record<InputUnit, string> = {
  HOUR: "人時",
  DAY: "人日",
};

/** 見積手法（EST-08） */
export const ESTIMATE_METHOD_LABELS: Record<EstimateMethod, string> = {
  BOTTOM_UP: "ボトムアップ",
  ANALOGOUS: "類推",
  THREE_POINT: "三点",
  EXPERT: "専門家判断",
  OTHER: "その他",
};

/** 横断照会の解決のしかた（RULE-WBS-002） */
export const RESOLVED_BY_LABELS: Record<string, string> = {
  CURRENT: "今の ID",
  HISTORY: "旧 ID（新旧対応から解決）",
};

export function labelOf(labels: Record<string, string>, value: string): string {
  return labels[value] ?? value;
}

/** 工数の表示（人時）。空は — */
export function hoursText(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100) / 100} 人時`;
}

/** 責任者の表示：利用者の名前（分からなければ #ID）と担当チーム。どちらも無ければ「未設定」 */
export function responsibleText(
  userId: number | null,
  team: string | null,
  names: ReadonlyMap<number, string>,
): string {
  const parts: string[] = [];
  if (userId !== null) {
    parts.push(names.get(userId) ?? `#${userId}`);
  }
  if (team !== null && team !== "") {
    parts.push(`（${team}）`);
  }
  return parts.length === 0 ? "未設定" : parts.join(" ");
}

/** 要素の配下（自分を除く）の ID。elements は先行順でなくてもよい */
export function descendantIds(elements: readonly { id: number; parent_id: number | null }[], id: number): Set<number> {
  const children = new Map<number, number[]>();
  for (const e of elements) {
    if (e.parent_id !== null) {
      const list = children.get(e.parent_id) ?? [];
      list.push(e.id);
      children.set(e.parent_id, list);
    }
  }
  const result = new Set<number>();
  const stack = [...(children.get(id) ?? [])];
  while (stack.length > 0) {
    const next = stack.pop()!;
    if (!result.has(next)) {
      result.add(next);
      stack.push(...(children.get(next) ?? []));
    }
  }
  return result;
}
