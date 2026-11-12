// 変更要求の起票・手直し（API-CHG-001・003。CHG-01：対象の WBS ID・理由・変更内容）
import { useState, type FormEvent } from "react";

import type { ChangeOperation, ChangeRequest, ChangeRequestInput } from "../../api/chg";
import { toErrorView, type ErrorView } from "../../api/errors";
import ErrorAlert from "../../components/ErrorAlert";
import { CHANGE_TITLE_MAX, parseTargetCodes } from "./labels";
import OperationEditor from "./OperationEditor";

export interface ChangeRequestFormProps {
  /** 直す変更要求（DRAFT）。null は新しく作る */
  initial: ChangeRequest | null;
  onSave: (input: ChangeRequestInput) => Promise<void>;
  onCancel: () => void;
}

export default function ChangeRequestForm({ initial, onSave, onCancel }: ChangeRequestFormProps) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [reason, setReason] = useState(initial?.reason ?? "");
  const [targets, setTargets] = useState(initial?.target_codes.join(", ") ?? "");
  const [operations, setOperations] = useState<ChangeOperation[]>(initial?.operations ?? []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ErrorView | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const codes = parseTargetCodes(targets);
    const missing: string[] = [];
    if (title.trim() === "") {
      missing.push("件名を入力してください");
    }
    if (reason.trim() === "") {
      missing.push("理由を入力してください");
    }
    if (codes.length === 0) {
      missing.push("対象の WBS ID を 1 つ以上入力してください");
    }
    if (missing.length > 0) {
      setError({ message: "入力に誤りがあります", details: missing });
      return;
    }
    setSaving(true);
    try {
      await onSave({ title: title.trim(), reason: reason.trim(), target_codes: codes, operations });
    } catch (e) {
      setError(toErrorView(e));
    } finally {
      setSaving(false);
    }
  }

  const label = initial === null ? "変更要求を作る" : `変更要求 #${initial.number} を直す`;
  return (
    <form aria-label={label} onSubmit={(e) => void handleSubmit(e)} className="change-request-form">
      <h3>{label}</h3>
      <label>
        件名
        <input value={title} maxLength={CHANGE_TITLE_MAX} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label>
        理由
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      <label>
        対象の WBS ID（複数はカンマか空白で区切る。旧 ID も可）
        <input value={targets} onChange={(e) => setTargets(e.target.value)} />
      </label>
      <OperationEditor operations={operations} onChange={setOperations} disabled={saving} />
      <ErrorAlert error={error} />
      <button type="submit" disabled={saving}>
        {initial === null ? "下書きとして保存する" : "保存する"}
      </button>
      <button type="button" onClick={onCancel} disabled={saving}>
        やめる
      </button>
    </form>
  );
}
