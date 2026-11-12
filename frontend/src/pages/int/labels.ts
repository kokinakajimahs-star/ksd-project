// 入出力の画面（UI-INT-001）の呼び名と、6.3 の列の定義（見せるだけ。正の定義はサーバの backend/app/domain/tabular.py）
import type { ImportChange, IoFormat, IoTarget } from "../../api/int";

export const TARGET_LABELS: Readonly<Record<IoTarget, string>> = {
  wbs: "WBS",
  activities: "活動（計画）",
  "time-entries": "実績工数",
};

export const FORMAT_LABELS: Readonly<Record<IoFormat, string>> = {
  csv: "CSV（UTF-8 BOM付き）",
  xlsx: "Excel（.xlsx）",
};

export const ACTION_LABELS: Readonly<Record<ImportChange["action"], string>> = {
  CREATE: "追加",
  UPDATE: "更新",
};

/** 6.3 の列（この順）と取込の照合キー */
export const TARGET_COLUMNS: Readonly<Record<IoTarget, { columns: readonly string[]; key: string }>> = {
  wbs: {
    columns: [
      "WBS ID",
      "親のWBS ID",
      "区分",
      "名称",
      "責任者のログインID",
      "自分の見積（人時）",
      "成果物",
      "作業範囲",
      "完了条件",
      "前提条件",
    ],
    key: "WBS ID（旧IDは改番の履歴で解決。無ければ追加）",
  },
  activities: {
    columns: [
      "活動ID",
      "WPのWBS ID",
      "名称",
      "希望開始日",
      "期間（稼働日）",
      "マイルストーン（0/1）",
      "見積（人時）",
      "先行の活動ID（; 区切り）",
      "担当者のログインID（; 区切り）",
    ],
    key: "活動ID（空なら追加）",
  },
  "time-entries": {
    columns: ["ログインID", "活動ID", "作業日", "工数（人時）", "備考"],
    key: "ログインID＋活動ID＋作業日",
  },
};

/** ファイルの名前の拡張子から形式を推す（.xlsx・.csv。大文字も可）。分からなければ null */
export function formatFromFilename(name: string): IoFormat | null {
  const lower = name.toLowerCase();
  if (lower.endsWith(".xlsx")) {
    return "xlsx";
  }
  if (lower.endsWith(".csv")) {
    return "csv";
  }
  return null;
}

/** 出力のファイルの名前。サーバが名前を返さなかったときに使う（サーバと同じ形） */
export function fallbackFilename(projectId: number, target: IoTarget, format: IoFormat): string {
  return `project-${projectId}-${target}.${format}`;
}
