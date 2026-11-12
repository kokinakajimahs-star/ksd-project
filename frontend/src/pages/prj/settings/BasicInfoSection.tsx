// UI-PRJ-002 の基本情報と設定（PRJ-01。API-PRJ-004）。変えた項目だけを送る（settings も変えた項目だけ）。
// 変えられるのは project.manage を持つ人だけ（9.1）。持たなければ値を見せるだけで、保存の部品を出さない（7.2）
import { useEffect, useState, type FormEvent } from "react";

import type { User } from "../../../api/cmn";
import { toErrorView, type ErrorView } from "../../../api/errors";
import {
  updateProject,
  type ProgressMethod,
  type Project,
  type ProjectSettings,
  type ProjectUpdateInput,
} from "../../../api/prj";
import ErrorAlert from "../../../components/ErrorAlert";
import { optionalText } from "../../../lib/form";
import { loadActiveUsers } from "../activeUsers";
import { PROGRESS_METHODS, wbsStatusLabel } from "../labels";
import { PROJECT_CODE_MAX, PROJECT_NAME_MAX } from "../limits";
import OwnerSelect from "../OwnerSelect";

/** 数で持つ設定の項目と、その呼び名（設計書 4.2・要求仕様書 BR-03・09・11） */
const NUMERIC_SETTINGS: readonly { key: Exclude<keyof ProjectSettings, "progress_method_default">; label: string }[] = [
  { key: "delay_tolerance_days", label: "遅れ・前倒しの判定の許容幅（稼働日）" },
  { key: "delay_extract_days", label: "大きく遅れた活動の抽出の閾値（稼働日）" },
  { key: "variance_rate", label: "工数の差異の閾値（割合。0.1 は 10%）" },
  { key: "variance_hours", label: "工数の差異の閾値（人時）" },
  { key: "hundred_percent_tolerance", label: "100%ルールの許容幅（割合）" },
];

type NumericSettingKey = (typeof NUMERIC_SETTINGS)[number]["key"];

export interface ProjectForm {
  code: string;
  name: string;
  purpose: string;
  requirements: string;
  keyDeliverables: string;
  startDate: string;
  targetFinishDate: string;
  ownerUserId: number;
  progressMethodDefault: ProgressMethod;
  /** 入力欄の文字のまま（数に直すのは collectProjectChanges） */
  numbers: Record<NumericSettingKey, string>;
}

export function formOf(project: Project): ProjectForm {
  const numbers = {} as Record<NumericSettingKey, string>;
  for (const { key } of NUMERIC_SETTINGS) {
    numbers[key] = String(project.settings[key]);
  }
  return {
    code: project.code,
    name: project.name,
    purpose: project.purpose ?? "",
    requirements: project.requirements ?? "",
    keyDeliverables: project.key_deliverables ?? "",
    startDate: project.start_date,
    targetFinishDate: project.target_finish_date ?? "",
    ownerUserId: project.owner_user_id,
    progressMethodDefault: project.settings.progress_method_default,
    numbers,
  };
}

export type CollectResult = { ok: true; changes: ProjectUpdateInput } | { ok: false; message: string };

/**
 * 変えた項目だけを集める（API-PRJ-004 は載せた項目だけを変える）。空欄にした任意の項目は null で送る。
 * 設定の数は 0 以上の有限の数でなければ送らずに理由を返す（サーバも 400 にする）
 */
export function collectProjectChanges(project: Project, form: ProjectForm): CollectResult {
  const changes: ProjectUpdateInput = {};
  if (form.code !== project.code) {
    changes.code = form.code;
  }
  if (form.name !== project.name) {
    changes.name = form.name;
  }
  const purpose = optionalText(form.purpose);
  if (purpose !== project.purpose) {
    changes.purpose = purpose;
  }
  const requirements = optionalText(form.requirements);
  if (requirements !== project.requirements) {
    changes.requirements = requirements;
  }
  const keyDeliverables = optionalText(form.keyDeliverables);
  if (keyDeliverables !== project.key_deliverables) {
    changes.key_deliverables = keyDeliverables;
  }
  if (form.startDate !== project.start_date) {
    changes.start_date = form.startDate;
  }
  const targetFinishDate = form.targetFinishDate === "" ? null : form.targetFinishDate;
  if (targetFinishDate !== project.target_finish_date) {
    changes.target_finish_date = targetFinishDate;
  }
  if (form.ownerUserId !== project.owner_user_id) {
    changes.owner_user_id = form.ownerUserId;
  }

  const settings: Partial<ProjectSettings> = {};
  if (form.progressMethodDefault !== project.settings.progress_method_default) {
    settings.progress_method_default = form.progressMethodDefault;
  }
  for (const { key, label } of NUMERIC_SETTINGS) {
    const text = form.numbers[key].trim();
    const value = Number(text);
    if (text === "" || !Number.isFinite(value) || value < 0) {
      return { ok: false, message: `${label}は 0 以上の数を入れます` };
    }
    if (value !== project.settings[key]) {
      settings[key] = value;
    }
  }
  if (Object.keys(settings).length > 0) {
    changes.settings = settings;
  }
  return { ok: true, changes };
}

export default function BasicInfoSection({
  project,
  canEdit,
  onSaved,
}: {
  project: Project;
  canEdit: boolean;
  onSaved: (project: Project) => void;
}) {
  const [form, setForm] = useState<ProjectForm>(() => formOf(project));
  const [ownerCandidates, setOwnerCandidates] = useState<User[] | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!canEdit) {
      return;
    }
    let cancelled = false;
    void loadActiveUsers().then((users) => {
      if (!cancelled) {
        setOwnerCandidates(users);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [canEdit]);

  function update<K extends keyof ProjectForm>(key: K, value: ProjectForm[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function updateNumber(key: NumericSettingKey, value: string) {
    setForm((current) => ({ ...current, numbers: { ...current.numbers, [key]: value } }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNotice(null);
    const result = collectProjectChanges(project, form);
    if (!result.ok) {
      setError({ message: result.message, details: [] });
      return;
    }
    if (Object.keys(result.changes).length === 0) {
      setError({ message: "変更した項目がありません", details: [] });
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const updated = await updateProject(project.id, result.changes);
      setForm(formOf(updated));
      setNotice("基本情報を保存しました");
      onSaved(updated);
    } catch (e: unknown) {
      setError(toErrorView(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section aria-labelledby="project-basic-heading">
      <h3 id="project-basic-heading">基本情報</h3>
      <form onSubmit={handleSubmit} aria-label="基本情報">
        <fieldset disabled={!canEdit || submitting}>
          <div>
            <label htmlFor="project-code">コード</label>
            <input
              id="project-code"
              required
              maxLength={PROJECT_CODE_MAX}
              value={form.code}
              onChange={(e) => update("code", e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="project-name">名称</label>
            <input
              id="project-name"
              required
              maxLength={PROJECT_NAME_MAX}
              value={form.name}
              onChange={(e) => update("name", e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="project-purpose">目的</label>
            <textarea id="project-purpose" value={form.purpose} onChange={(e) => update("purpose", e.target.value)} />
          </div>
          <div>
            <label htmlFor="project-requirements">要求事項</label>
            <textarea
              id="project-requirements"
              value={form.requirements}
              onChange={(e) => update("requirements", e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="project-deliverables">主要成果物</label>
            <textarea
              id="project-deliverables"
              value={form.keyDeliverables}
              onChange={(e) => update("keyDeliverables", e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="project-start">開始日</label>
            <input
              id="project-start"
              type="date"
              required
              value={form.startDate}
              onChange={(e) => update("startDate", e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="project-finish">終了予定日</label>
            <input
              id="project-finish"
              type="date"
              min={form.startDate === "" ? undefined : form.startDate}
              value={form.targetFinishDate}
              onChange={(e) => update("targetFinishDate", e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="project-owner">責任者</label>
            <OwnerSelect
              id="project-owner"
              value={form.ownerUserId}
              onChange={(userId) => update("ownerUserId", userId)}
              always={[project.owner]}
              candidates={ownerCandidates}
            />
          </div>
          <p>WBSの状態：{wbsStatusLabel(project.wbs_status)}</p>
          <h4>設定</h4>
          <div>
            <label htmlFor="project-progress-method">進捗率の算定方式（既定）</label>
            <select
              id="project-progress-method"
              value={form.progressMethodDefault}
              onChange={(e) => update("progressMethodDefault", e.target.value as ProgressMethod)}
            >
              {PROGRESS_METHODS.map((method) => (
                <option key={method.code} value={method.code}>
                  {method.label}
                </option>
              ))}
            </select>
          </div>
          {NUMERIC_SETTINGS.map(({ key, label }) => (
            <div key={key}>
              <label htmlFor={`project-setting-${key}`}>{label}</label>
              <input
                id={`project-setting-${key}`}
                type="number"
                min={0}
                step="any"
                required
                value={form.numbers[key]}
                onChange={(e) => updateNumber(key, e.target.value)}
              />
            </div>
          ))}
        </fieldset>
        {notice !== null && <p role="status">{notice}</p>}
        <ErrorAlert error={error} />
        {canEdit && (
          <button type="submit" disabled={submitting}>
            基本情報を保存する
          </button>
        )}
      </form>
    </section>
  );
}
