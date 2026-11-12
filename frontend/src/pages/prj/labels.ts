// プロジェクト基盤の画面に出す呼び名。コードは要求仕様書 3 章（ロール）・7.6 BR-06（算定方式）・
// 設計書 5.10（WBS の状態）・9.1（権限）のとおり。並びも各表の順にする
import type { ProgressMethod, ScopeItemKind } from "../../api/prj";

/** 3 章のロール（SYSTEM_ADMIN は users.is_system_admin で表すので、プロジェクトのロールに含めない） */
export const ROLES: readonly { code: string; label: string }[] = [
  { code: "PM", label: "PM" },
  { code: "PLANNER", label: "計画担当者" },
  { code: "EXECUTOR", label: "実行担当者" },
  { code: "PROGRESS_MANAGER", label: "進捗管理担当者" },
  { code: "APPROVER", label: "承認者（上長）" },
  { code: "CONTRACTOR", label: "委託先" },
  { code: "VIEWER", label: "閲覧者" },
];

export function roleLabel(code: string): string {
  return ROLES.find((role) => role.code === code)?.label ?? code;
}

/** 9.1 の権限のうち、プロジェクトのロール×権限の表に載るもの（システムの権限 project.create・user.admin を除く） */
export const PROJECT_PERMISSIONS: readonly { code: string; label: string }[] = [
  { code: "project.view", label: "プロジェクトの計画・進捗を読む" },
  { code: "project.manage", label: "基本情報・スコープ・カレンダーを変える" },
  { code: "member.manage", label: "メンバーとロールを変える" },
  { code: "permission.manage", label: "ロール×権限の表を変える" },
  { code: "wbs.edit", label: "WBS・辞書・採番・レビューの開始・承認の申請" },
  { code: "wbs.review", label: "レビューの指摘を書く" },
  { code: "wbs.approve", label: "WBSを承認・差戻しする" },
  { code: "estimate.edit", label: "見積を書く" },
  { code: "schedule.edit", label: "活動・依存・割当・算定方式を変える" },
  { code: "baseline.manage", label: "スケジュールベースラインを保存する" },
  { code: "status_report.manage", label: "報告日を作る・確定する" },
  { code: "progress.enter_own", label: "自分の割当の活動の進捗を書く" },
  { code: "progress.enter_all", label: "全ての活動の進捗を書く" },
  { code: "effort.enter_own", label: "自分の実績工数を書く" },
  { code: "effort.view", label: "プロジェクトの工数の集計・差異を読む" },
  { code: "variance.note", label: "差異の理由を書く" },
  { code: "change.create", label: "変更要求を作る・提出する" },
  { code: "change.approve", label: "変更要求を承認・却下する" },
  { code: "change.apply", label: "承認済みの変更を反映する" },
  { code: "action.manage", label: "是正措置を書く" },
  { code: "audit.view", label: "変更履歴を読む" },
  { code: "share.manage", label: "共有リンクを作る・失効させる" },
  { code: "data.import", label: "取り込む" },
  { code: "data.export", label: "出力する" },
];

/** BR-06 の算定方式 */
export const PROGRESS_METHODS: readonly { code: ProgressMethod; label: string }[] = [
  { code: "DIRECT", label: "直接入力" },
  { code: "DELIVERABLE_RATIO", label: "成果物完了割合" },
  { code: "APPROVED_WORK_RATIO", label: "承認済み作業割合" },
  { code: "CHECK_ITEM_RATIO", label: "検証項目完了割合" },
  { code: "WEIGHTED_MILESTONE", label: "重み付きマイルストーン" },
  { code: "ZERO_HUNDRED", label: "0/100法" },
  { code: "FIFTY_FIFTY", label: "50/50法" },
];

/** 5.10 の WBS の状態 */
const WBS_STATUS_LABELS: Record<string, string> = {
  DRAFT: "作成中",
  IN_REVIEW: "レビュー中",
  PENDING_APPROVAL: "承認待ち",
  BASELINED: "基準化済み",
};

export function wbsStatusLabel(code: string): string {
  return WBS_STATUS_LABELS[code] ?? code;
}

export const SCOPE_KIND_LABELS: Record<ScopeItemKind, string> = {
  IN: "対象内",
  OUT: "対象外",
};

/** working_weekdays の 7 文字の並び（月曜から日曜。設計書 4.1） */
export const WEEKDAY_LABELS: readonly string[] = ["月", "火", "水", "木", "金", "土", "日"];
