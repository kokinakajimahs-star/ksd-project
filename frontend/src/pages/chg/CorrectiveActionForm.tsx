// 是正措置の登録（API-CHG-007。CHG-06：WBS ID・報告日・活動に紐付け、原因・対策・担当・期限を記録する）
import { useState, type FormEvent } from "react";

import type { CorrectiveActionInput } from "../../api/chg";
import { toErrorView, type ErrorView } from "../../api/errors";
import type { StatusReport } from "../../api/prg";
import type { Member } from "../../api/prj";
import type { Activity } from "../../api/sch";
import type { WbsElement } from "../../api/wbs";
import ErrorAlert from "../../components/ErrorAlert";
import { optionalText } from "../../lib/form";

export interface CorrectiveActionFormProps {
  elements: WbsElement[];
  activities: Activity[];
  reports: StatusReport[];
  members: Member[];
  onSave: (input: CorrectiveActionInput) => Promise<void>;
}

function idOrNull(value: string): number | null {
  return value === "" ? null : Number(value);
}

export default function CorrectiveActionForm({ elements, activities, reports, members, onSave }: CorrectiveActionFormProps) {
  const [elementId, setElementId] = useState("");
  const [activityId, setActivityId] = useState("");
  const [reportId, setReportId] = useState("");
  const [cause, setCause] = useState("");
  const [action, setAction] = useState("");
  const [ownerId, setOwnerId] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ErrorView | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const missing: string[] = [];
    if (elementId === "" && activityId === "") {
      missing.push("WBS の要素か活動の少なくとも一方に紐付けてください");
    }
    if (cause.trim() === "") {
      missing.push("原因を入力してください");
    }
    if (action.trim() === "") {
      missing.push("対策を入力してください");
    }
    if (missing.length > 0) {
      setError({ message: "入力に誤りがあります", details: missing });
      return;
    }
    setSaving(true);
    try {
      await onSave({
        element_id: idOrNull(elementId),
        activity_id: idOrNull(activityId),
        status_report_id: idOrNull(reportId),
        cause: cause.trim(),
        action: action.trim(),
        owner_user_id: idOrNull(ownerId),
        due_date: optionalText(dueDate),
      });
      setElementId("");
      setActivityId("");
      setReportId("");
      setCause("");
      setAction("");
      setOwnerId("");
      setDueDate("");
    } catch (e) {
      setError(toErrorView(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form aria-label="是正措置を登録する" onSubmit={(e) => void handleSubmit(e)} className="corrective-action-form">
      <h3>是正措置を登録する</h3>
      <label>
        WBS の要素
        <select value={elementId} onChange={(e) => setElementId(e.target.value)}>
          <option value="">紐付けない</option>
          {elements.map((el) => (
            <option key={el.id} value={el.id}>
              {el.wbs_code} {el.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        活動
        <select value={activityId} onChange={(e) => setActivityId(e.target.value)}>
          <option value="">紐付けない</option>
          {activities.map((a) => (
            <option key={a.id} value={a.id}>
              {a.wbs_code} {a.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        報告日
        <select value={reportId} onChange={(e) => setReportId(e.target.value)}>
          <option value="">紐付けない</option>
          {reports.map((r) => (
            <option key={r.id} value={r.id}>
              {r.report_date}
            </option>
          ))}
        </select>
      </label>
      <label>
        原因
        <textarea value={cause} onChange={(e) => setCause(e.target.value)} />
      </label>
      <label>
        対策
        <textarea value={action} onChange={(e) => setAction(e.target.value)} />
      </label>
      <label>
        担当
        <select value={ownerId} onChange={(e) => setOwnerId(e.target.value)}>
          <option value="">未定</option>
          {members.map((m) => (
            <option key={m.user_id} value={m.user_id}>
              {m.display_name}
            </option>
          ))}
        </select>
      </label>
      <label>
        期限
        <input type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
      </label>
      <ErrorAlert error={error} />
      <button type="submit" disabled={saving}>
        登録する
      </button>
    </form>
  );
}
