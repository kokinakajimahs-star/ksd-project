// UI-CHG-003 変更履歴（設計書 7.1 のルート /projects/:projectId/audit-logs。CHG-09）。
// 期間・変更者・対象・WBS ID で検索し（API-CHG-010。権限は audit.view。新しい順）、変更前後の値を出す。
// 履歴は追記だけで、画面から変更・削除する部品は置かない（4.2・NFR-05）
import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import { searchAuditLogs, type AuditLog, type AuditLogList, type AuditLogQuery } from "../../api/chg";
import { toErrorView, type ErrorView } from "../../api/errors";
import ErrorAlert from "../../components/ErrorAlert";
import Pager from "../../components/Pager";
import { formatDateTime } from "../../lib/format";
import { parseProjectId } from "../prj/ProjectSettingsPage";
import { AUDIT_ACTION_LABELS, CHG_PAGE_SIZE, ENTITY_TYPE_MAX, userLabel, valueText, WBS_CODE_MAX } from "./labels";
import { useProjectMembers } from "./useProjectMembers";

export interface AuditChange {
  field: string;
  before: unknown;
  after: unknown;
}

/**
 * 1 件の履歴の項目ごとの前後。追加は後だけ、削除は前だけ、更新は変わった項目だけ（RULE-CHG-006 が作った形）。
 * 項目は前に現れた順、前に無い項目は後の順で並べる
 */
export function auditChanges(log: Pick<AuditLog, "before" | "after">): AuditChange[] {
  const before = log.before ?? {};
  const after = log.after ?? {};
  const fields = [...Object.keys(before), ...Object.keys(after).filter((k) => !(k in before))];
  return fields.map((field) => ({ field, before: before[field], after: after[field] }));
}

interface SearchInput {
  dateFrom: string;
  dateTo: string;
  userId: string;
  entityType: string;
  entityId: string;
  wbsCode: string;
}

const EMPTY_SEARCH: SearchInput = { dateFrom: "", dateTo: "", userId: "", entityType: "", entityId: "", wbsCode: "" };

/** 入力欄の値を検索の条件にする（空は条件にしない）。対象の ID が整数でなければ理由を返す */
export function toAuditQuery(input: SearchInput): { query: AuditLogQuery } | { message: string } {
  const query: AuditLogQuery = {};
  if (input.dateFrom !== "") {
    query.date_from = input.dateFrom;
  }
  if (input.dateTo !== "") {
    query.date_to = input.dateTo;
  }
  if (input.dateFrom !== "" && input.dateTo !== "" && input.dateFrom > input.dateTo) {
    return { message: "期間の始まりは終わり以前の日付にしてください" };
  }
  if (input.userId !== "") {
    query.user_id = Number(input.userId);
  }
  if (input.entityType.trim() !== "") {
    query.entity_type = input.entityType.trim();
  }
  if (input.entityId.trim() !== "") {
    if (!/^\d+$/.test(input.entityId.trim())) {
      return { message: "対象の ID は整数で入力してください" };
    }
    query.entity_id = Number(input.entityId.trim());
  }
  if (input.wbsCode.trim() !== "") {
    query.wbs_code = input.wbsCode.trim();
  }
  return { query };
}

function ChangeList({ log }: { log: AuditLog }) {
  const changes = auditChanges(log);
  if (changes.length === 0) {
    return <>—</>;
  }
  return (
    <ul className="audit-changes">
      {changes.map((c) => (
        <li key={c.field}>
          {log.action === "CREATE" && `${c.field}: ${valueText(c.after)}`}
          {log.action === "DELETE" && `${c.field}: ${valueText(c.before)}`}
          {log.action === "UPDATE" && `${c.field}: ${valueText(c.before)} → ${valueText(c.after)}`}
        </li>
      ))}
    </ul>
  );
}

export default function AuditLogsPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = parseProjectId(params.projectId);
  const { members, names } = useProjectMembers(projectId);
  const [input, setInput] = useState<SearchInput>(EMPTY_SEARCH);
  const [query, setQuery] = useState<AuditLogQuery>({});
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<AuditLogList | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);
  const [inputError, setInputError] = useState<ErrorView | null>(null);

  useEffect(() => {
    if (projectId === null) {
      return;
    }
    let cancelled = false;
    searchAuditLogs(projectId, { ...query, limit: CHG_PAGE_SIZE, offset }).then(
      (result) => {
        if (!cancelled) {
          setPage(result);
          setError(null);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setPage(null);
          setError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, query, offset]);

  if (projectId === null) {
    return (
      <section data-screen-id="UI-CHG-003" className="audit-logs-page">
        <h2>変更履歴</h2>
        <p role="alert">プロジェクトが見つかりません</p>
        <p>
          <Link to="/projects">プロジェクト一覧へ</Link>
        </p>
      </section>
    );
  }

  function set<K extends keyof SearchInput>(key: K, value: SearchInput[K]) {
    setInput((current) => ({ ...current, [key]: value }));
  }

  function handleSearch(event: FormEvent) {
    event.preventDefault();
    const result = toAuditQuery(input);
    if ("message" in result) {
      setInputError({ message: result.message, details: [] });
      return;
    }
    setInputError(null);
    setOffset(0);
    setQuery(result.query);
  }

  function handleClear() {
    setInput(EMPTY_SEARCH);
    setInputError(null);
    setOffset(0);
    setQuery({});
  }

  return (
    <section data-screen-id="UI-CHG-003" className="audit-logs-page">
      <h2>変更履歴</h2>
      <nav aria-label="行き先" className="page-links">
        <Link to="/projects">プロジェクト一覧へ</Link>
        <Link to={`/projects/${projectId}/change-requests`}>変更要求</Link>
      </nav>
      <p className="note">書き込みの操作ごとに、変更者・日時・対象・変更前後の値が残る。履歴は追記だけで、変更・削除はできない。</p>

      <form aria-label="変更履歴を検索する" onSubmit={handleSearch} className="filters">
        <label>
          期間（から）
          <input type="date" value={input.dateFrom} onChange={(e) => set("dateFrom", e.target.value)} />
        </label>
        <label>
          期間（まで）
          <input type="date" value={input.dateTo} onChange={(e) => set("dateTo", e.target.value)} />
        </label>
        <label>
          変更者
          <select value={input.userId} onChange={(e) => set("userId", e.target.value)}>
            <option value="">すべて</option>
            {members.map((m) => (
              <option key={m.user_id} value={m.user_id}>
                {m.display_name}
              </option>
            ))}
          </select>
        </label>
        <label>
          対象の種類（表の名前）
          <input
            value={input.entityType}
            maxLength={ENTITY_TYPE_MAX}
            onChange={(e) => set("entityType", e.target.value)}
          />
        </label>
        <label>
          対象の ID
          <input inputMode="numeric" value={input.entityId} onChange={(e) => set("entityId", e.target.value)} />
        </label>
        <label>
          WBS ID（記録した時点の ID）
          <input value={input.wbsCode} maxLength={WBS_CODE_MAX} onChange={(e) => set("wbsCode", e.target.value)} />
        </label>
        <ErrorAlert error={inputError} />
        <button type="submit">検索する</button>
        <button type="button" onClick={handleClear}>
          条件を消す
        </button>
      </form>

      <ErrorAlert error={error} />
      {page !== null && page.items.length === 0 && <p>該当する変更履歴はありません</p>}
      {page !== null && page.items.length > 0 && (
        <>
          <table aria-label="変更履歴">
            <thead>
              <tr>
                <th scope="col">日時</th>
                <th scope="col">変更者</th>
                <th scope="col">操作</th>
                <th scope="col">対象の種類</th>
                <th scope="col">対象の ID</th>
                <th scope="col">WBS ID</th>
                <th scope="col">変更前後の値</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((log) => (
                <tr key={log.id} data-action={log.action}>
                  <td>{formatDateTime(log.occurred_at)}</td>
                  <td>{userLabel(names, log.user_id)}</td>
                  <td>{AUDIT_ACTION_LABELS[log.action]}</td>
                  <td>{log.entity_type}</td>
                  <td>{log.entity_id}</td>
                  <td>{log.wbs_code ?? "—"}</td>
                  <td>
                    <ChangeList log={log} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager total={page.total} limit={page.limit} offset={page.offset} onChange={setOffset} />
        </>
      )}
    </section>
  );
}
