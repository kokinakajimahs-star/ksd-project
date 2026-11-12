// UI-INT-001 入出力（Excel・CSV）（設計書 7.1 のルート /projects/:projectId/import-export。INT-01）。
// WBS・活動（計画）・実績工数を 6.3 の列で出力し（API-INT-001。権限は data.export）、取り込む（API-INT-002。権限は data.import）。
// 取込はサーバが全行を先に検査し、誤りが 1 件でもあれば何も書かない。「検査する」は dry_run で、書かずに結果だけを見る。
// 権限の無い操作の部品は出さない（7.2）。正の判定はサーバが行う
import { useState, type ChangeEvent, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import { hasProjectPermission } from "../../api/cmn";
import { ApiError } from "../../api/client";
import { toErrorView, type ErrorView } from "../../api/errors";
import {
  exportData,
  importData,
  importIssuesFrom,
  IO_FORMATS,
  IO_TARGETS,
  type ImportIssue,
  type ImportResult,
  type IoFormat,
  type IoTarget,
} from "../../api/int";
import ErrorAlert from "../../components/ErrorAlert";
import { saveBlob } from "../../lib/download";
import { useMe } from "../chg/useMe";
import { parseProjectId } from "../prj/ProjectSettingsPage";
import {
  ACTION_LABELS,
  fallbackFilename,
  FORMAT_LABELS,
  formatFromFilename,
  TARGET_COLUMNS,
  TARGET_LABELS,
} from "./labels";

/** 取込の結果を 1 行の文にする */
export function describeImportResult(result: ImportResult): string {
  const counts = `追加 ${result.created} 件・更新 ${result.updated} 件・変更なし ${result.unchanged} 件`;
  if (!result.ok) {
    return `検査で ${result.issues.length} 件の誤りが見つかりました。何も書いていません`;
  }
  if (result.dry_run) {
    return `検査を通りました。取り込むと ${counts} になります（まだ書いていません）`;
  }
  return `取り込みました（${counts}）`;
}

function TargetSelect({ id, value, onChange }: { id: string; value: IoTarget; onChange: (v: IoTarget) => void }) {
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value as IoTarget)}>
      {IO_TARGETS.map((t) => (
        <option key={t} value={t}>
          {TARGET_LABELS[t]}
        </option>
      ))}
    </select>
  );
}

function FormatSelect({ id, value, onChange }: { id: string; value: IoFormat; onChange: (v: IoFormat) => void }) {
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value as IoFormat)}>
      {IO_FORMATS.map((f) => (
        <option key={f} value={f}>
          {FORMAT_LABELS[f]}
        </option>
      ))}
    </select>
  );
}

function ColumnDefinition({ target }: { target: IoTarget }) {
  const definition = TARGET_COLUMNS[target];
  return (
    <p className="note">
      {TARGET_LABELS[target]} の列（この順。1 行目が見出し・日付は YYYY-MM-DD）：{definition.columns.join(", ")}
      <br />
      取込の照合キー：{definition.key}
    </p>
  );
}

function ExportSection({ projectId }: { projectId: number }) {
  const [target, setTarget] = useState<IoTarget>("wbs");
  const [format, setFormat] = useState<IoFormat>("csv");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorView | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const file = await exportData(projectId, target, format);
      const filename = file.filename ?? fallbackFilename(projectId, target, format);
      saveBlob(file.blob, filename);
      setNotice(`${filename} を出力しました`);
    } catch (e: unknown) {
      setError(toErrorView(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="export-heading">
      <h3 id="export-heading">出力する</h3>
      <form aria-label="出力する" onSubmit={(e) => void handleSubmit(e)} className="filters">
        <label htmlFor="export-target">対象</label>
        <TargetSelect id="export-target" value={target} onChange={setTarget} />
        <label htmlFor="export-format">形式</label>
        <FormatSelect id="export-format" value={format} onChange={setFormat} />
        <button type="submit" disabled={busy}>
          出力する
        </button>
      </form>
      <ColumnDefinition target={target} />
      <ErrorAlert error={error} />
      {notice !== null && <p role="status">{notice}</p>}
    </section>
  );
}

function IssueTable({ issues }: { issues: ImportIssue[] }) {
  return (
    <table aria-label="取込の誤り">
      <thead>
        <tr>
          <th scope="col">行</th>
          <th scope="col">列</th>
          <th scope="col">コード</th>
          <th scope="col">メッセージ</th>
        </tr>
      </thead>
      <tbody>
        {issues.map((issue, index) => (
          <tr key={`${issue.row}-${issue.column ?? ""}-${issue.code}-${index}`}>
            <td>{issue.row}</td>
            <td>{issue.column ?? "—"}</td>
            <td>{issue.code}</td>
            <td>{issue.message}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ChangeTable({ result }: { result: ImportResult }) {
  return (
    <table aria-label={result.dry_run ? "取り込む行" : "取り込んだ行"}>
      <thead>
        <tr>
          <th scope="col">行</th>
          <th scope="col">操作</th>
          <th scope="col">照合キー</th>
          <th scope="col">変わる列</th>
        </tr>
      </thead>
      <tbody>
        {result.changes.map((change) => (
          <tr key={change.row} data-action={change.action}>
            <td>{change.row}</td>
            <td>{ACTION_LABELS[change.action]}</td>
            <td>{change.key === "" ? "（取り込むときに振る）" : change.key}</td>
            <td>{change.changed.length === 0 ? "—" : change.changed.join(", ")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ImportSection({ projectId }: { projectId: number }) {
  const [target, setTarget] = useState<IoTarget>("wbs");
  const [format, setFormat] = useState<IoFormat>("csv");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [issues, setIssues] = useState<ImportIssue[]>([]);
  const [error, setError] = useState<ErrorView | null>(null);

  /** 対象・形式・ファイルを変えたら、前の結果は当てはまらないので消す */
  function clearResult() {
    setResult(null);
    setIssues([]);
    setError(null);
  }

  function handleTarget(value: IoTarget) {
    setTarget(value);
    clearResult();
  }

  function handleFormat(value: IoFormat) {
    setFormat(value);
    clearResult();
  }

  function handleFile(event: ChangeEvent<HTMLInputElement>) {
    const chosen = event.target.files?.[0] ?? null;
    setFile(chosen);
    if (chosen !== null) {
      const guessed = formatFromFilename(chosen.name);
      if (guessed !== null) {
        setFormat(guessed);
      }
    }
    clearResult();
  }

  async function run(dryRun: boolean) {
    if (file === null) {
      clearResult();
      setError({ message: "取り込むファイルを選んでください", details: [] });
      return;
    }
    setBusy(true);
    clearResult();
    try {
      const outcome = await importData(projectId, target, format, file, dryRun);
      setResult(outcome);
      setIssues(outcome.issues);
    } catch (e: unknown) {
      if (e instanceof ApiError && e.code === "IMPORT_INVALID") {
        // 誤りは表に出す（alert の箇条書きと重ねない）
        setIssues(importIssuesFrom(e.details));
        setError({ message: e.message, details: [] });
      } else {
        setError(toErrorView(e));
      }
    } finally {
      setBusy(false);
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    void run(false);
  }

  return (
    <section aria-labelledby="import-heading">
      <h3 id="import-heading">取り込む</h3>
      <p className="note">
        全ての行を先に検査し、誤りが 1 件でもあれば何も書き込まない。「検査する」は書き込まずに結果だけを見る。
        出力したファイルをそのまま取り込むと、変更は 0 件になる。
      </p>
      <form aria-label="取り込む" onSubmit={handleSubmit} className="filters">
        <label htmlFor="import-target">対象</label>
        <TargetSelect id="import-target" value={target} onChange={handleTarget} />
        <label htmlFor="import-format">形式</label>
        <FormatSelect id="import-format" value={format} onChange={handleFormat} />
        <label htmlFor="import-file">ファイル</label>
        <input
          id="import-file"
          type="file"
          accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          onChange={handleFile}
        />
        <button type="button" onClick={() => void run(true)} disabled={busy}>
          検査する
        </button>
        <button type="submit" disabled={busy}>
          取り込む
        </button>
      </form>
      <ColumnDefinition target={target} />
      <ErrorAlert error={error} />
      {result !== null && <p role="status">{describeImportResult(result)}</p>}
      {issues.length > 0 && <IssueTable issues={issues} />}
      {result !== null && result.ok && result.changes.length > 0 && <ChangeTable result={result} />}
    </section>
  );
}

export default function ImportExportPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = parseProjectId(params.projectId);
  const { me, meError } = useMe(projectId);

  if (projectId === null) {
    return (
      <section data-screen-id="UI-INT-001" className="import-export-page">
        <h2>入出力（Excel・CSV）</h2>
        <p role="alert">プロジェクトが見つかりません</p>
        <p>
          <Link to="/projects">プロジェクト一覧へ</Link>
        </p>
      </section>
    );
  }

  const canExport = me !== null && hasProjectPermission(me, projectId, "data.export");
  const canImport = me !== null && hasProjectPermission(me, projectId, "data.import");

  return (
    <section data-screen-id="UI-INT-001" className="import-export-page">
      <h2>入出力（Excel・CSV）</h2>
      <nav aria-label="行き先" className="page-links">
        <Link to="/projects">プロジェクト一覧へ</Link>
        <Link to={`/projects/${projectId}/activities`}>タスクリスト</Link>
      </nav>
      <ErrorAlert error={meError} />
      {me === null && meError === null && <p>読み込み中…</p>}
      {me !== null && !canExport && !canImport && <p>このプロジェクトで入出力を行う権限がありません</p>}
      {canExport && <ExportSection projectId={projectId} />}
      {canImport && <ImportSection projectId={projectId} />}
    </section>
  );
}
