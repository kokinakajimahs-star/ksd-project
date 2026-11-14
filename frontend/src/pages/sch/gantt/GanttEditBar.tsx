// ガントの編集の帯（設計書 7.3 の「編集」。CR-024）：WBS の状態・操作のボタン・行を足す欄・まとめる欄・消すことの確かめ、
// と、選んだ活動の欄（希望開始日・期間・移す先の WP・後続を足す・先行を外す）。操作のボタンはその権限を持つ人にだけ出す
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import type { Activity } from "../../../api/sch";
import type { ElementKind, WbsTree } from "../../../api/wbs";
import ErrorAlert from "../../../components/ErrorAlert";
import StatusBadge, { WBS_STATUS_TONES } from "../../../components/StatusBadge";
import { ELEMENT_KIND_LABELS, ELEMENT_KINDS, WBS_STATUS_LABELS } from "../../wbs/labels";
import type { GanttEditor } from "./useGanttEditor";

/** まとめる先の上位の要素の区分（WP は子を持てない。RULE-WBS-003） */
const PARENT_KINDS = ELEMENT_KINDS.filter((k) => k !== "WORK_PACKAGE");

export function GanttEditBar({ editor, tree }: { editor: GanttEditor; tree: WbsTree }) {
  const [addName, setAddName] = useState("");
  const [addKind, setAddKind] = useState<ElementKind>("WORK_PACKAGE");
  const [groupName, setGroupName] = useState("");
  const [groupKind, setGroupKind] = useState<ElementKind>("COMPONENT");
  const { selection, addTarget: target, busy } = editor;
  const canAdd = target.kind === "activity" ? editor.canEditSchedule : editor.canEditWbs;
  const groupsActivities =
    selection.kind === "activity" || (selection.kind === "many" && selection.elementIds.length === 0 && selection.activityIds.length > 0);
  const groupsElements = selection.kind === "many" && selection.activityIds.length === 0 && selection.elementIds.length > 0;
  const canGroup = groupsActivities ? editor.canEditWbs && editor.canEditSchedule : groupsElements && editor.canEditWbs;

  async function handleAdd(event: FormEvent) {
    event.preventDefault();
    if (await editor.addRow(addName, addKind)) {
      setAddName("");
    }
    // 続けて足せるように入力に戻る
    editor.addInputRef.current?.focus();
  }

  async function handleGroup(event: FormEvent) {
    event.preventDefault();
    if (await editor.groupSelected(groupName, groupKind)) {
      setGroupName("");
    }
  }

  const targetText =
    target.kind === "activity"
      ? `${target.workPackage.wbs_code} ${target.workPackage.name} に活動を足す（期間 1 稼働日）`
      : target.parent === null
        ? "最上位に要素を足す"
        : `${target.parent.wbs_code} ${target.parent.name} の下に要素を足す`;

  return (
    <section aria-label="ガントの編集" className="gantt-edit">
      <div role="toolbar" aria-label="編集の操作" className="gantt-edit-toolbar">
        <span>
          WBS の状態 <StatusBadge tone={WBS_STATUS_TONES[tree.wbs_status]}>{WBS_STATUS_LABELS[tree.wbs_status] ?? tree.wbs_status}</StatusBadge>
        </span>
        <button type="button" onClick={editor.rename} disabled={busy} title="F2・ダブルクリック">
          名前を変える
        </button>
        {editor.canEditWbs && (
          <>
            <button type="button" onClick={editor.indent} disabled={busy} title="Alt+Shift+→">
              インデント
            </button>
            <button type="button" onClick={editor.outdent} disabled={busy} title="Alt+Shift+←">
              アウトデント
            </button>
          </>
        )}
        <button type="button" onClick={editor.stepUp} disabled={busy} title="Alt+Shift+↑">
          上へ
        </button>
        <button type="button" onClick={editor.stepDown} disabled={busy} title="Alt+Shift+↓">
          下へ
        </button>
        <button type="button" onClick={editor.askDelete} disabled={busy || selection.kind === "none"} title="Delete">
          消す
        </button>
      </div>
      {tree.wbs_status !== "DRAFT" && editor.canEditWbs && (
        <p className="note">{`WBS は${WBS_STATUS_LABELS[tree.wbs_status] ?? tree.wbs_status}。要素を変えると作成中に戻る（5.10）`}</p>
      )}

      {editor.confirmingDelete && (
        <div role="group" aria-label="行を消すことの確かめ" className="actions">
          <span>{`${editor.deleteSummary}を消す。消した行は戻せない`}</span>
          <button type="button" onClick={() => void editor.confirmDelete()} disabled={busy}>
            消す
          </button>
          <button type="button" onClick={editor.cancelDelete}>
            やめる
          </button>
        </div>
      )}

      {canAdd && (
        <form aria-label="行を足す" className="gantt-edit-add" onSubmit={(e) => void handleAdd(e)}>
          <span>{targetText}</span>
          <input
            ref={editor.addInputRef}
            aria-label="足す行の名前"
            value={addName}
            onChange={(e) => setAddName(e.target.value)}
            placeholder="名前（Enter で足す）"
          />
          {target.kind === "element" && (
            <select aria-label="足す要素の区分" value={addKind} onChange={(e) => setAddKind(e.target.value as ElementKind)}>
              {ELEMENT_KINDS.map((k) => (
                <option key={k} value={k}>
                  {ELEMENT_KIND_LABELS[k]}
                </option>
              ))}
            </select>
          )}
          <button type="submit" disabled={busy}>
            足す
          </button>
        </form>
      )}

      {canGroup && (
        <form aria-label="まとめる" className="gantt-edit-add" onSubmit={(e) => void handleGroup(e)}>
          <span>
            {groupsActivities
              ? `選んだ活動 ${selection.kind === "many" ? selection.activityIds.length : 1} 件を新しい WP にまとめる`
              : `選んだ要素 ${selection.kind === "many" ? selection.elementIds.length : 0} 件を新しい上位の要素にまとめる`}
          </span>
          <input aria-label="まとめる先の名前" value={groupName} onChange={(e) => setGroupName(e.target.value)} />
          {groupsElements && (
            <select aria-label="まとめる先の区分" value={groupKind} onChange={(e) => setGroupKind(e.target.value as ElementKind)}>
              {PARENT_KINDS.map((k) => (
                <option key={k} value={k}>
                  {ELEMENT_KIND_LABELS[k]}
                </option>
              ))}
            </select>
          )}
          <button type="submit" disabled={busy}>
            まとめる
          </button>
        </form>
      )}

      <ErrorAlert error={editor.error} />
      {/* 結果の行は常に置く（文言が出入りしても図が上下に動かない） */}
      <p role="status" className="gantt-edit-status">
        {editor.message ?? (editor.busy ? "保存しています…" : "")}
      </p>
      <p className="note">
        行をクリックで選ぶ（Ctrl＋クリックで足す、Shift＋クリックで範囲）。上下の矢印で選び、F2 で名前、Insert で行を足す欄へ、
        Delete で消す。Alt+Shift+矢印でインデント・アウトデント・上下へ。バーの本体をドラッグすると開始、右端をドラッグすると終わりが動く（Esc で取り消す）。
        選んだ活動のバーの右の丸を別の活動のバーへドラッグすると依存をつなぐ。矢印を選んで Delete で外す
      </p>
    </section>
  );
}

/** 選んだ行の欄（活動は希望開始日・期間・移す先の WP・依存。要素は WBS の画面へ） */
export function GanttSelectionPanel({ editor, projectId }: { editor: GanttEditor; projectId: number }) {
  const { selection } = editor;
  if (selection.kind === "none") {
    return null;
  }
  if (selection.kind === "many") {
    return (
      <section aria-label="選んだ行" className="gantt-selection">
        <p>{`要素 ${selection.elementIds.length} 件・活動 ${selection.activityIds.length} 件を選んでいる`}</p>
      </section>
    );
  }
  if (selection.kind === "element") {
    const e = selection.element;
    return (
      <section aria-label="選んだ行" className="gantt-selection">
        <h3>{`${e.wbs_code} ${e.name}`}</h3>
        <p>
          {ELEMENT_KIND_LABELS[e.kind]}・<Link to={`/projects/${projectId}/wbs/${e.id}`}>WBS 辞書・担当・見積を開く</Link>
        </p>
      </section>
    );
  }
  return <ActivityPanel key={selection.activity.id} editor={editor} activity={selection.activity} />;
}

function ActivityPanel({ editor, activity }: { editor: GanttEditor; activity: Activity }) {
  const [requested, setRequested] = useState(activity.requested_start ?? "");
  const [duration, setDuration] = useState(String(activity.duration_days));
  const [target, setTarget] = useState("");
  const [successor, setSuccessor] = useState("");
  const { busy } = editor;
  const canEdit = editor.canEditSchedule;
  const nameOf = (id: number) => {
    const a = editor.activities.find((x) => x.id === id);
    return a === undefined ? String(id) : `${a.wbs_code} ${a.name}`;
  };
  const predecessors = editor.dependencies.filter((d) => d.successor_id === activity.id);
  const others = editor.activities.filter((a) => a.id !== activity.id && !activity.successor_ids.includes(a.id));
  const moveTargets = editor.workPackages.filter((wp) => wp.id !== activity.work_package_id);

  return (
    <section aria-label="選んだ行" className="gantt-selection">
      <h3>{`${activity.wbs_code} ${activity.name}`}</h3>
      <p>
        {`予定 ${activity.start_date ?? "—"}〜${activity.finish_date ?? "—"}・期間 ${activity.duration_days} 稼働日`}
        {activity.is_milestone && "（マイルストーン）"}
        {activity.total_float !== null && `・余裕 ${activity.total_float} 日`}
      </p>
      {canEdit && (
        <div className="gantt-selection-fields">
          <form
            aria-label="希望開始日"
            className="actions"
            onSubmit={(e) => {
              e.preventDefault();
              if (requested !== "") {
                void editor.setRequestedStart(activity.id, requested);
              }
            }}
          >
            <label>
              希望開始日
              <input type="date" value={requested} onChange={(e) => setRequested(e.target.value)} />
            </label>
            <button type="submit" disabled={busy || requested === "" || requested === activity.requested_start}>
              保存
            </button>
            {activity.requested_start !== null && (
              <button type="button" disabled={busy} onClick={() => void editor.setRequestedStart(activity.id, null)}>
                希望開始日を外す
              </button>
            )}
          </form>
          {!activity.is_milestone && (
            <form
              aria-label="期間"
              className="actions"
              onSubmit={(e) => {
                e.preventDefault();
                void editor.setDuration(activity.id, Number(duration));
              }}
            >
              <label>
                期間（稼働日）
                <input type="number" min={1} step={1} value={duration} onChange={(e) => setDuration(e.target.value)} />
              </label>
              <button type="submit" disabled={busy || !(Number(duration) >= 1) || Number(duration) === activity.duration_days}>
                保存
              </button>
            </form>
          )}
          <form
            aria-label="移す先の WP"
            className="actions"
            onSubmit={(e) => {
              e.preventDefault();
              if (target !== "") {
                void editor.moveActivityTo(activity.id, Number(target));
              }
            }}
          >
            <label>
              移す先の WP
              <select value={target} onChange={(e) => setTarget(e.target.value)}>
                <option value="">（選ぶ）</option>
                {moveTargets.map((wp) => (
                  <option key={wp.id} value={wp.id}>
                    {`${wp.wbs_code} ${wp.name}`}
                  </option>
                ))}
              </select>
            </label>
            <button type="submit" disabled={busy || target === ""}>
              移す
            </button>
          </form>
          <form
            aria-label="後続を足す"
            className="actions"
            onSubmit={(e) => {
              e.preventDefault();
              if (successor !== "") {
                void editor.addSuccessor(activity.id, Number(successor));
                setSuccessor("");
              }
            }}
          >
            <label>
              後続を足す
              <select value={successor} onChange={(e) => setSuccessor(e.target.value)}>
                <option value="">（選ぶ）</option>
                {others.map((a) => (
                  <option key={a.id} value={a.id}>
                    {`${a.wbs_code} ${a.name}`}
                  </option>
                ))}
              </select>
            </label>
            <button type="submit" disabled={busy || successor === ""}>
              足す
            </button>
          </form>
        </div>
      )}
      <h4>先行</h4>
      {predecessors.length === 0 ? (
        <p>先行は無い</p>
      ) : (
        <ul aria-label="先行">
          {predecessors.map((d) => (
            <li key={d.id}>
              {nameOf(d.predecessor_id)}
              {canEdit && (
                <button
                  type="button"
                  disabled={busy}
                  aria-label={`先行 ${nameOf(d.predecessor_id)} を外す`}
                  onClick={() => void editor.removeDependency(d.id)}
                >
                  外す
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
