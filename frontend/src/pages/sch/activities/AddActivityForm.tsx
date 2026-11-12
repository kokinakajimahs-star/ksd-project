// UI-SCH-001 の活動を足す部品（SCH-01・03・09。API-SCH-002）。活動は WBS の WP の配下に作る。
// 開始日・終了日・期間（稼働日）のうち 2 つを入れると、残りはサーバがカレンダーで求める（期間だけでもよい）。
// 期間 0 の活動はマイルストーン（SCH-09）
import { useState, type FormEvent } from "react";

import { toErrorView, type ErrorView } from "../../../api/errors";
import { createActivity, type Activity, type ActivityCreateInput } from "../../../api/sch";
import type { WbsElement } from "../../../api/wbs";
import ErrorAlert from "../../../components/ErrorAlert";
import { ACTIVITY_NAME_MAX, DURATION_MAX } from "../limits";

export interface AddActivityFormProps {
  projectId: number;
  /** 置き場に選べる WP（WBS の要素のうち区分が WORK_PACKAGE のもの） */
  workPackages: WbsElement[];
  onAdded: (activity: Activity) => void;
}

export interface FormState {
  workPackageId: string;
  name: string;
  requestedStart: string;
  requestedFinish: string;
  duration: string;
  isMilestone: boolean;
}

const EMPTY: FormState = {
  workPackageId: "",
  name: "",
  requestedStart: "",
  requestedFinish: "",
  duration: "",
  isMilestone: false,
};

/** 入力から送る形を作る。足りない・形の違う入力は理由の一覧を返す */
export function toCreateInput(form: FormState): { input: ActivityCreateInput | null; errors: string[] } {
  const errors: string[] = [];
  if (form.workPackageId === "") {
    errors.push("置き場の WP を選んでください");
  }
  const name = form.name.trim();
  if (name === "") {
    errors.push("名称を入力してください");
  }
  let duration: number | null = null;
  if (form.isMilestone) {
    duration = 0;
  } else if (form.duration.trim() !== "") {
    const text = form.duration.trim();
    if (!/^\d+$/.test(text) || Number(text) > DURATION_MAX) {
      errors.push(`期間は 0 以上 ${DURATION_MAX} 以下の整数（稼働日）で入力してください`);
    } else {
      duration = Number(text);
    }
  }
  if (errors.length > 0) {
    return { input: null, errors };
  }
  const input: ActivityCreateInput = { work_package_id: Number(form.workPackageId), name };
  if (form.requestedStart !== "") {
    input.requested_start = form.requestedStart;
  }
  if (form.requestedFinish !== "" && !form.isMilestone) {
    input.requested_finish = form.requestedFinish;
  }
  if (duration !== null) {
    input.duration_days = duration;
  }
  if (form.isMilestone) {
    input.is_milestone = true;
  }
  return { input, errors };
}

export default function AddActivityForm({ projectId, workPackages, onAdded }: AddActivityFormProps) {
  const [form, setForm] = useState<FormState>(EMPTY);
  const [error, setError] = useState<ErrorView | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const { input, errors } = toCreateInput(form);
    if (input === null) {
      setError({ message: "入力に誤りがあります", details: errors });
      return;
    }
    setSubmitting(true);
    try {
      const created = await createActivity(projectId, input);
      setForm((f) => ({ ...EMPTY, workPackageId: f.workPackageId }));
      onAdded(created);
    } catch (e) {
      setError(toErrorView(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form aria-label="活動を足す" onSubmit={(e) => void handleSubmit(e)} className="add-activity-form">
      <h3>活動を足す</h3>
      {workPackages.length === 0 && <p>WBS に WP（ワークパッケージ）がありません。先に WBS で WP を作ってください。</p>}
      <label>
        置き場の WP
        <select value={form.workPackageId} onChange={(e) => set("workPackageId", e.target.value)}>
          <option value="">（選ぶ）</option>
          {workPackages.map((wp) => (
            <option key={wp.id} value={wp.id}>
              {`${wp.wbs_code} ${wp.name}`}
            </option>
          ))}
        </select>
      </label>
      <label>
        名称
        <input value={form.name} maxLength={ACTIVITY_NAME_MAX} onChange={(e) => set("name", e.target.value)} />
      </label>
      <p className="hint">開始日・終了日・期間のうち 2 つを入れると、残りはカレンダーで求める（期間だけでもよい）。</p>
      <label>
        開始日
        <input type="date" value={form.requestedStart} onChange={(e) => set("requestedStart", e.target.value)} />
      </label>
      <label>
        終了日
        <input
          type="date"
          value={form.requestedFinish}
          disabled={form.isMilestone}
          onChange={(e) => set("requestedFinish", e.target.value)}
        />
      </label>
      <label>
        期間（稼働日）
        <input
          inputMode="numeric"
          value={form.isMilestone ? "0" : form.duration}
          disabled={form.isMilestone}
          onChange={(e) => set("duration", e.target.value)}
        />
      </label>
      <label>
        <input type="checkbox" checked={form.isMilestone} onChange={(e) => set("isMilestone", e.target.checked)} />
        マイルストーン（期間 0）
      </label>
      <ErrorAlert error={error} />
      <button type="submit" disabled={submitting}>
        足す
      </button>
    </form>
  );
}
