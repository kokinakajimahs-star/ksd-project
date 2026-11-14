// UI-SCH-001 の担当と割当工数（EFF-01。API-EFF-001）。1 つの活動に複数の担当を置き、割当工数の合計を見積と比べて出す。
// 担当はプロジェクトの有効なメンバーから選ぶ（違えばサーバが 400）
import { useState, type FormEvent } from "react";

import { toErrorView, type ErrorView } from "../../../api/errors";
import { replaceAssignments, type ActivityAssignments, type AssignmentInput } from "../../../api/eff";
import type { Member } from "../../../api/prj";
import type { Activity } from "../../../api/sch";
import ErrorAlert from "../../../components/ErrorAlert";
import { assignmentTotals } from "./bulkEdit";

export interface AssignmentEditorProps {
  projectId: number;
  activity: Activity;
  members: Member[];
  onSaved: (result: ActivityAssignments) => void;
  onClose: () => void;
  /** ガントの詳細と進捗のダイアログに入れる（見出しと「閉じる」はダイアログが持つ。CR-027） */
  embedded?: boolean;
}

interface Row {
  userId: string;
  plannedHours: string;
}

/** 入力の行から送る形を作る。工数の空は未定（null）。形の違う入力・重なる担当は理由を返す */
export function toAssignmentInputs(rows: Row[]): { inputs: AssignmentInput[] | null; errors: string[] } {
  const errors: string[] = [];
  const inputs: AssignmentInput[] = [];
  const seen = new Set<string>();
  rows.forEach((row, i) => {
    const n = i + 1;
    if (row.userId === "") {
      errors.push(`${n} 行目：担当者を選んでください`);
      return;
    }
    if (seen.has(row.userId)) {
      errors.push(`${n} 行目：同じ担当者が重なっています`);
      return;
    }
    seen.add(row.userId);
    const text = row.plannedHours.trim();
    if (text !== "" && !(/^\d+(\.\d+)?$/.test(text) && Number.isFinite(Number(text)))) {
      errors.push(`${n} 行目：割当工数は 0 以上の数（人時）で入力してください`);
      return;
    }
    inputs.push({ user_id: Number(row.userId), planned_hours: text === "" ? null : Number(text) });
  });
  return { inputs: errors.length > 0 ? null : inputs, errors };
}

function hoursText(value: number | null): string {
  return value === null ? "—" : `${value} 人時`;
}

export default function AssignmentEditor({
  projectId,
  activity,
  members,
  onSaved,
  onClose,
  embedded = false,
}: AssignmentEditorProps) {
  const [rows, setRows] = useState<Row[]>(() =>
    activity.assignees.map((a) => ({
      userId: String(a.user_id),
      plannedHours: a.planned_hours === null ? "" : String(a.planned_hours),
    })),
  );
  const [result, setResult] = useState<ActivityAssignments | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const activeMembers = members.filter((m) => m.is_active);

  const preview = assignmentTotals(
    rows.map((r) => (r.plannedHours.trim() === "" || Number.isNaN(Number(r.plannedHours)) ? null : Number(r.plannedHours))),
    activity.estimate_hours,
  );

  function update(index: number, patch: Partial<Row>) {
    setRows((current) => current.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const { inputs, errors } = toAssignmentInputs(rows);
    if (inputs === null) {
      setError({ message: "入力に誤りがあります", details: errors });
      return;
    }
    setSubmitting(true);
    try {
      const saved = await replaceAssignments(projectId, activity.id, inputs);
      setResult(saved);
      onSaved(saved);
    } catch (e) {
      setError(toErrorView(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form aria-label={`${activity.name} の担当と割当工数`} onSubmit={(e) => void handleSubmit(e)}>
      {!embedded && (
        <h3>
          担当と割当工数：{activity.id} {activity.name}
        </h3>
      )}
      <table aria-label="担当">
        <thead>
          <tr>
            <th scope="col">担当者</th>
            <th scope="col">割当工数（人時）</th>
            <th scope="col">操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              <td>
                <select
                  aria-label={`${i + 1} 行目の担当者`}
                  value={row.userId}
                  onChange={(e) => update(i, { userId: e.target.value })}
                >
                  <option value="">（選ぶ）</option>
                  {activeMembers.map((m) => (
                    <option key={m.user_id} value={m.user_id}>
                      {m.display_name}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                <input
                  aria-label={`${i + 1} 行目の割当工数`}
                  inputMode="decimal"
                  value={row.plannedHours}
                  onChange={(e) => update(i, { plannedHours: e.target.value })}
                />
              </td>
              <td>
                <button type="button" onClick={() => setRows((current) => current.filter((_, j) => j !== i))}>
                  外す
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button type="button" onClick={() => setRows((current) => [...current, { userId: "", plannedHours: "" }])}>
        担当を足す
      </button>
      <p aria-label="割当工数と見積">
        割当工数の合計 {hoursText(result?.planned_hours_total ?? preview.total)}／見積{" "}
        {hoursText(result?.estimate_hours ?? activity.estimate_hours)}／差{" "}
        {hoursText(result !== null ? result.difference_hours : preview.difference)}
      </p>
      <ErrorAlert error={error} />
      <button type="submit" disabled={submitting}>
        担当を保存する
      </button>
      {!embedded && (
        <button type="button" onClick={onClose}>
          閉じる
        </button>
      )}
    </form>
  );
}
