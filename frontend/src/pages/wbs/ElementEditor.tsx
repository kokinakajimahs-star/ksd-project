// UI-WBS-001 で選んだ要素を変える部品：名称・区分・責任者・担当チーム（API-WBS-003）、移動（API-WBS-004。ID は変えない）、
// 配下ごとの削除（API-WBS-005。子を持つ要素は配下の件数を示して確かめる：WBS-01）、見積（API-EST-001。EST-01）。
// 変える部品は wbs.edit、見積は estimate.edit を持つ人にだけ出す（7.2）。正の判定はサーバが行う
import { useEffect, useMemo, useState, type FormEvent } from "react";

import { toErrorView, type ErrorView } from "../../api/errors";
import type { EstimateWritten } from "../../api/est";
import type { Member } from "../../api/prj";
import {
  deleteElement,
  moveElement,
  updateElement,
  type ElementKind,
  type ElementUpdateInput,
  type WbsElement,
} from "../../api/wbs";
import ErrorAlert from "../../components/ErrorAlert";
import EstimateForm from "./EstimateForm";
import { ELEMENT_KIND_LABELS, ELEMENT_KINDS, descendantIds, hoursText } from "./labels";

/** 移動先の親の選択肢の値。最上位は空文字 */
const TOP = "";

export interface ElementEditorProps {
  projectId: number;
  element: WbsElement;
  /** 木の全ての要素（先行順） */
  elements: WbsElement[];
  members: Member[];
  canEdit: boolean;
  canEstimate: boolean;
  /** 変えた後（notice は利用者に出す 1 行） */
  onChanged: (notice: string) => void;
  onDeleted: (notice: string) => void;
}

/** 名称・区分・責任者・担当チームのうち、今の値から変えたものだけ（API-WBS-003 は載せた項目だけを変える） */
export function changedFields(
  element: Pick<WbsElement, "name" | "kind" | "responsible_user_id" | "responsible_team">,
  form: { name: string; kind: ElementKind; responsibleUserId: number | null; team: string },
): ElementUpdateInput {
  const input: ElementUpdateInput = {};
  if (form.name.trim() !== element.name) {
    input.name = form.name.trim();
  }
  if (form.kind !== element.kind) {
    input.kind = form.kind;
  }
  if (form.responsibleUserId !== element.responsible_user_id) {
    input.responsible_user_id = form.responsibleUserId;
  }
  const team = form.team.trim() === "" ? null : form.team.trim();
  if (team !== element.responsible_team) {
    input.responsible_team = team;
  }
  return input;
}

export default function ElementEditor({
  projectId,
  element,
  elements,
  members,
  canEdit,
  canEstimate,
  onChanged,
  onDeleted,
}: ElementEditorProps) {
  const [name, setName] = useState(element.name);
  const [kind, setKind] = useState<ElementKind>(element.kind);
  const [responsible, setResponsible] = useState(element.responsible_user_id === null ? "" : String(element.responsible_user_id));
  const [team, setTeam] = useState(element.responsible_team ?? "");
  const [parent, setParent] = useState(element.parent_id === null ? TOP : String(element.parent_id));
  const [position, setPosition] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorView | null>(null);

  // 別の要素を選んだら、入力を今の値へ戻す
  useEffect(() => {
    setName(element.name);
    setKind(element.kind);
    setResponsible(element.responsible_user_id === null ? "" : String(element.responsible_user_id));
    setTeam(element.responsible_team ?? "");
    setParent(element.parent_id === null ? TOP : String(element.parent_id));
    setPosition("");
    setError(null);
  }, [element]);

  const descendants = useMemo(() => descendantIds(elements, element.id), [elements, element.id]);
  // 移動先に選べる親：自分と配下・WP（子を持てない：WBS-02）を除く。サーバも 422 で拒む
  const parentChoices = useMemo(
    () => elements.filter((e) => e.id !== element.id && !descendants.has(e.id) && e.kind !== "WORK_PACKAGE"),
    [elements, element.id, descendants],
  );
  const label = `${element.wbs_code} ${element.name}`;

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(toErrorView(e));
    } finally {
      setBusy(false);
    }
  }

  function handleUpdate(event: FormEvent) {
    event.preventDefault();
    if (name.trim() === "") {
      setError({ message: "名称を入力してください", details: [] });
      return;
    }
    const input = changedFields(element, {
      name,
      kind,
      responsibleUserId: responsible === "" ? null : Number(responsible),
      team,
    });
    if (Object.keys(input).length === 0) {
      setError({ message: "変えた項目がありません", details: [] });
      return;
    }
    void run(async () => {
      const updated = await updateElement(projectId, element.id, input);
      onChanged(`「${updated.wbs_code} ${updated.name}」を変えた`);
    });
  }

  function handleMove(event: FormEvent) {
    event.preventDefault();
    const trimmed = position.trim();
    if (trimmed !== "" && !/^[1-9]\d*$/.test(trimmed)) {
      setError({ message: "位置は 1 以上の整数で入力してください（空なら末尾）", details: [] });
      return;
    }
    // 画面は 1 から数え、API は 0 から数える
    const index = trimmed === "" ? null : Number(trimmed) - 1;
    void run(async () => {
      const moved = await moveElement(projectId, element.id, parent === TOP ? null : Number(parent), index);
      const pending = moved.needs_renumber ? "（親の ID と形が合わないので改番待ち。改番するまで ID は変わらない）" : "";
      onChanged(`「${moved.wbs_code} ${moved.name}」を移動した${pending}`);
    });
  }

  function handleDelete() {
    const message =
      descendants.size > 0
        ? `「${label}」と配下の要素 ${descendants.size} 件を消します。よろしいですか`
        : `「${label}」を消します。よろしいですか`;
    if (!window.confirm(message)) {
      return;
    }
    void run(async () => {
      await deleteElement(projectId, element.id);
      onDeleted(`「${label}」を消した`);
    });
  }

  function handleEstimateSaved(result: EstimateWritten) {
    onChanged(`「${label}」の見積を ${hoursText(result.current_hours)}にした（プロジェクトの総見積 ${hoursText(result.project_estimate_hours)}）`);
  }

  return (
    <section aria-label={`選んだ要素（${label}）`} className="element-editor">
      <h3>
        選んだ要素：{element.wbs_code} {element.name}
      </h3>
      <ErrorAlert error={error} />
      {canEdit && (
        <>
          <form aria-label="要素を変える" onSubmit={handleUpdate}>
            <label>
              名称
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label>
              区分
              <select value={kind} onChange={(e) => setKind(e.target.value as ElementKind)}>
                {ELEMENT_KINDS.map((k) => (
                  <option key={k} value={k}>
                    {ELEMENT_KIND_LABELS[k]}
                  </option>
                ))}
              </select>
            </label>
            <label>
              責任者
              <select value={responsible} onChange={(e) => setResponsible(e.target.value)}>
                <option value="">（未設定）</option>
                {members.map((m) => (
                  <option key={m.user_id} value={String(m.user_id)}>
                    {m.display_name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              担当チーム
              <input value={team} onChange={(e) => setTeam(e.target.value)} />
            </label>
            <button type="submit" disabled={busy}>
              変更を保存する
            </button>
          </form>

          <form aria-label="要素を移動する" onSubmit={handleMove}>
            <p>移動しても WBS ID は変わらない。新しい ID にするには改番する（WBS-06）。</p>
            <label>
              移動先の親
              <select value={parent} onChange={(e) => setParent(e.target.value)}>
                <option value={TOP}>（最上位）</option>
                {parentChoices.map((e) => (
                  <option key={e.id} value={String(e.id)}>
                    {e.wbs_code} {e.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              兄弟の中の位置（1 から。空なら末尾）
              <input inputMode="numeric" value={position} onChange={(e) => setPosition(e.target.value)} />
            </label>
            <button type="submit" disabled={busy}>
              移動する
            </button>
          </form>

          <button type="button" onClick={handleDelete} disabled={busy}>
            配下ごと消す
          </button>
        </>
      )}
      {canEstimate && (
        <>
          <p>自分の見積：{hoursText(element.own_estimate_hours)}</p>
          <EstimateForm projectId={projectId} elementId={element.id} label={label} onSaved={handleEstimateSaved} />
        </>
      )}
    </section>
  );
}
