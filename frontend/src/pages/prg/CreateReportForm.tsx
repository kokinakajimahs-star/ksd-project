// 報告日を作る部品（API-PRG-001。PRG-01）。進捗入力（UI-PRG-001）とガント（UI-SCH-002。7.2）が使う。
// status_report.manage を持つ人にだけ出す（7.2。サーバでも必ず判定する）。同じ日付の報告日は 409、形の違う日付は 400 で、
// サーバの文言をこの部品の中に出す
import { useState, type FormEvent } from "react";

import { toErrorView, type ErrorView } from "../../api/errors";
import { createStatusReport, type StatusReport } from "../../api/prg";
import ErrorAlert from "../../components/ErrorAlert";

export interface CreateReportFormProps {
  projectId: number;
  onCreated: (report: StatusReport) => void;
}

export default function CreateReportForm({ projectId, onCreated }: CreateReportFormProps) {
  const [newDate, setNewDate] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<ErrorView | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (newDate === "") {
      return;
    }
    setCreating(true);
    setError(null);
    try {
      const created = await createStatusReport(projectId, newDate);
      setNewDate("");
      onCreated(created);
    } catch (e) {
      setError(toErrorView(e));
    } finally {
      setCreating(false);
    }
  }

  return (
    <form aria-label="報告日を作る" onSubmit={(e) => void handleSubmit(e)} className="create-report-form">
      <label>
        新しい報告日
        <input type="date" required value={newDate} onChange={(e) => setNewDate(e.target.value)} />
      </label>
      <button type="submit" disabled={creating || newDate === ""}>
        報告日を作る
      </button>
      <ErrorAlert error={error} />
    </form>
  );
}
