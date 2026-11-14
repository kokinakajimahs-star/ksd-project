// ガントの編集の帯（設計書 7.3 の「編集」。CR-024・CR-025）：WBS の状態・選んだ行の要約・結果と誤りの文言・消すことの確かめ・
// 操作の手引きだけを図の上に出す。入力欄と操作のボタンは置かない（操作は図の上のキー・右クリックのメニュー・行の中の入力・ドラッグ）
import type { KeyboardEvent } from "react";

import type { WbsTree } from "../../../api/wbs";
import ErrorAlert from "../../../components/ErrorAlert";
import StatusBadge, { WBS_STATUS_TONES } from "../../../components/StatusBadge";
import { ELEMENT_KIND_LABELS, WBS_STATUS_LABELS } from "../../wbs/labels";
import { GanttMenu } from "./GanttMenu";
import type { GanttEditor, Selection } from "./useGanttEditor";

/** 選んだ行の要約（1 行） */
export function selectionSummary(selection: Selection): string {
  switch (selection.kind) {
    case "none":
      return "選んだ行：なし（Enter で最上位に要素を足す）";
    case "many":
      return `選んだ行：要素 ${selection.elementIds.length} 件・活動 ${selection.activityIds.length} 件`;
    case "element":
      return `選んだ行：${selection.element.wbs_code} ${selection.element.name}（${ELEMENT_KIND_LABELS[selection.element.kind]}）`;
    case "activity": {
      const a = selection.activity;
      const parts = [
        `予定 ${a.start_date ?? "—"}〜${a.finish_date ?? "—"}`,
        a.is_milestone ? "マイルストーン" : `期間 ${a.duration_days} 稼働日`,
        a.total_float === null ? null : `余裕 ${a.total_float} 日`,
        a.requested_start === null ? null : `希望開始日 ${a.requested_start}`,
        `先行 ${a.predecessor_ids.length} 件・後続 ${a.successor_ids.length} 件`,
      ].filter((p) => p !== null);
      return `選んだ行：${a.wbs_code} ${a.name}（${parts.join("・")}）`;
    }
  }
}

export function GanttEditBar({ editor, tree }: { editor: GanttEditor; tree: WbsTree }) {
  function onConfirmKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      editor.cancelDelete();
    }
  }

  return (
    <section aria-label="ガントの編集" aria-busy={editor.busy} className="gantt-edit">
      <p>
        WBS の状態 <StatusBadge tone={WBS_STATUS_TONES[tree.wbs_status]}>{WBS_STATUS_LABELS[tree.wbs_status] ?? tree.wbs_status}</StatusBadge>
        {" "}
        <span className="gantt-edit-selection">{selectionSummary(editor.selection)}</span>
      </p>
      {tree.wbs_status !== "DRAFT" && editor.canEditWbs && (
        <p className="note">{`WBS は${WBS_STATUS_LABELS[tree.wbs_status] ?? tree.wbs_status}。要素を変えると作成中に戻る（5.10）`}</p>
      )}

      {editor.confirmingDelete && (
        <div role="alertdialog" aria-label="行を消すことの確かめ" className="gantt-edit-confirm" onKeyDown={onConfirmKeyDown}>
          <span>{`${editor.deleteSummary}を消す。消した行は戻せない（Enter で消す・Esc でやめる）`}</span>
          <button type="button" autoFocus onClick={() => void editor.confirmDelete()}>
            消す
          </button>
          <button type="button" onClick={editor.cancelDelete}>
            やめる
          </button>
        </div>
      )}

      <ErrorAlert error={editor.error} />
      {editor.loadError !== null && (
        <p role="alert">{`活動の一覧を読み直せません（${editor.loadError.message}）。編集を終えてから入り直してください`}</p>
      )}
      {/* 結果の行は常に置く（文言が出入りしても図が上下に動かない） */}
      <p role="status" className="gantt-edit-status">
        {editor.message ?? (editor.saving ? "保存しています…" : editor.reloading ? "読み直しています…" : "")}
      </p>
      <p className="note">
        右クリック（Shift+F10）で行の操作のメニュー。行をクリック・上下の矢印で選び（Ctrl＋クリックで足す、Shift で範囲、Esc で外す）、
        Enter で下に行を足し、F2・ダブルクリックで名前、Delete で消す。Alt+Shift+矢印でインデント・アウトデント・上下へ。
        バーを横へドラッグすると開始（右端は終わり）が動き、別の活動の行へドラッグすると依存をつなぐ（Esc で取り消す）。矢印を選んで Delete で依存を外す
      </p>
      {editor.menu !== null && <GanttMenu menu={editor.menu} onChoose={editor.chooseMenuEntry} onClose={editor.closeMenu} />}
    </section>
  );
}
