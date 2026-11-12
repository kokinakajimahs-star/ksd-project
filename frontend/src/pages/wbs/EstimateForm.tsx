// WBS要素・活動の見積を書く部品（API-EST-001。EST-01・EST-08）。estimate.edit を持つ人にだけ出す（7.2）。
// WBS（UI-WBS-001・002）は ELEMENT、タスクリスト（UI-SCH-001）は ACTIVITY で使う。
// 人時・人日のどちらでも入れられ、人日はサーバが 1 日の稼働時間で人時へ換算する（RULE-EST-002）。
// 手法・前提・参照資料を見積値とあわせて送り、履歴（API-EST-002）に残る
import { useState, type FormEvent } from "react";

import {
  putEstimate,
  type EstimateMethod,
  type EstimateTargetType,
  type EstimateWritten,
  type InputUnit,
} from "../../api/est";
import { toErrorView, type ErrorView } from "../../api/errors";
import ErrorAlert from "../../components/ErrorAlert";
import { optionalText } from "../../lib/form";
import { ESTIMATE_METHOD_LABELS, INPUT_UNIT_LABELS } from "./labels";

/** 0 以上・小数 2 桁まで（サーバの検査と同じ形。負の値は入れられない：EST-01） */
const ESTIMATE_PATTERN = /^\d+(\.\d{1,2})?$/;

export function parseEstimateValue(text: string): number | null {
  const trimmed = text.trim();
  return ESTIMATE_PATTERN.test(trimmed) ? Number(trimmed) : null;
}

export interface EstimateFormProps {
  projectId: number;
  /** 見積の対象の種類：WBS要素（ELEMENT）か活動（ACTIVITY） */
  targetType: EstimateTargetType;
  targetId: number;
  /** 見出しに出す対象（要素は WBS ID と名称） */
  label: string;
  onSaved: (result: EstimateWritten) => void;
}

export default function EstimateForm({ projectId, targetType, targetId, label, onSaved }: EstimateFormProps) {
  const [value, setValue] = useState("");
  const [unit, setUnit] = useState<InputUnit>("HOUR");
  const [method, setMethod] = useState<EstimateMethod | "">("");
  const [assumptions, setAssumptions] = useState("");
  const [references, setReferences] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ErrorView | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const parsed = parseEstimateValue(value);
    if (parsed === null) {
      setError({ message: "見積は 0 以上の数（小数 2 桁まで）で入力してください", details: [] });
      return;
    }
    setSaving(true);
    try {
      const result = await putEstimate(projectId, targetType, targetId, {
        input_value: parsed,
        input_unit: unit,
        method: method === "" ? null : method,
        assumptions: optionalText(assumptions),
        references: optionalText(references),
      });
      setValue("");
      setAssumptions("");
      setReferences("");
      setMethod("");
      onSaved(result);
    } catch (e) {
      setError(toErrorView(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form aria-label={`見積を書く（${label}）`} onSubmit={(e) => void handleSubmit(e)} className="estimate-form">
      <label>
        見積
        <input inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} />
      </label>
      <label>
        単位
        <select value={unit} onChange={(e) => setUnit(e.target.value as InputUnit)}>
          {(Object.keys(INPUT_UNIT_LABELS) as InputUnit[]).map((u) => (
            <option key={u} value={u}>
              {INPUT_UNIT_LABELS[u]}
            </option>
          ))}
        </select>
      </label>
      <label>
        見積手法
        <select value={method} onChange={(e) => setMethod(e.target.value as EstimateMethod | "")}>
          <option value="">（指定しない）</option>
          {(Object.keys(ESTIMATE_METHOD_LABELS) as EstimateMethod[]).map((m) => (
            <option key={m} value={m}>
              {ESTIMATE_METHOD_LABELS[m]}
            </option>
          ))}
        </select>
      </label>
      <label>
        前提
        <textarea value={assumptions} onChange={(e) => setAssumptions(e.target.value)} />
      </label>
      <label>
        参照資料
        <textarea value={references} onChange={(e) => setReferences(e.target.value)} />
      </label>
      <ErrorAlert error={error} />
      <button type="submit" disabled={saving}>
        見積を保存する
      </button>
    </form>
  );
}
