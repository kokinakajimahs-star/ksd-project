// UI-SCH-003 ベースラインと差異（設計書 7.1 のルート /projects/:projectId/baselines。SCH-16・17・18）。
// 版の一覧（API-SCH-012）・保存（API-SCH-011。baseline.manage を持つ人にだけ出す。7.2）・
// 選んだ版と最新計画の差異（API-SCH-013。既定は最新の版）を 1 つの画面に並べる
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import { getMe, hasProjectPermission, type Me } from "../../api/cmn";
import { toErrorView, type ErrorView } from "../../api/errors";
import {
  createScheduleBaseline,
  getScheduleVariance,
  listScheduleBaselines,
  type ScheduleBaseline,
  type ScheduleBaselineList,
  type Variance,
  type VarianceStatus,
} from "../../api/sch";
import ErrorAlert from "../../components/ErrorAlert";
import Pager from "../../components/Pager";
import { formatDateTime } from "../../lib/format";
import { parseProjectId } from "../prj/ProjectSettingsPage";
import { BASELINE_NAME_MAX } from "./limits";

export const BASELINES_PAGE_SIZE = 100;

export const VARIANCE_STATUS_LABELS: Record<VarianceStatus, string> = {
  MATCHED: "両方にある",
  ADDED: "追加された",
  DELETED: "削除された",
};

/** 保存の理由（4.1 の schedule_baselines.reason。backend/app/models/sch.py の SCHEDULE_BASELINE_REASONS）。知らない値はそのまま */
const REASON_LABELS: Record<string, string> = {
  MANUAL: "保存",
  RE_APPROVAL: "再承認",
  CHANGE_REQUEST: "変更要求の反映",
};

/** 既定で比べる版：版の番号が最も大きいもの（SCH-17：既定は最新の版）。無ければ null */
export function latestBaseline(items: ScheduleBaseline[]): ScheduleBaseline | null {
  let latest: ScheduleBaseline | null = null;
  for (const item of items) {
    if (latest === null || item.version > latest.version) {
      latest = item;
    }
  }
  return latest;
}

/** 差の表示（最新 − 基準）。正は + を付ける。空は — */
export function signed(value: number | null, unit: string): string {
  if (value === null) {
    return "—";
  }
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded}${unit}`;
}

function text(value: string | number | null): string {
  return value === null ? "—" : String(value);
}

export default function BaselinesPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = parseProjectId(params.projectId);
  const [me, setMe] = useState<Me | null>(null);
  const [page, setPage] = useState<ScheduleBaselineList | null>(null);
  const [offset, setOffset] = useState(0);
  const [reloadKey, setReloadKey] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [variance, setVariance] = useState<Variance | null>(null);
  const [statusFilter, setStatusFilter] = useState<VarianceStatus | "">("");
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);
  const [saveError, setSaveError] = useState<ErrorView | null>(null);

  useEffect(() => {
    if (projectId === null) {
      return;
    }
    let cancelled = false;
    getMe().then(
      (result) => {
        if (!cancelled) {
          setMe(result);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  useEffect(() => {
    if (projectId === null) {
      return;
    }
    let cancelled = false;
    listScheduleBaselines(projectId, { limit: BASELINES_PAGE_SIZE, offset }).then(
      (result) => {
        if (!cancelled) {
          setPage(result);
          // 選んでいなければ、最新の版を比べる（SCH-17）
          setSelectedId((current) => current ?? latestBaseline(result.items)?.id ?? null);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, offset, reloadKey]);

  useEffect(() => {
    if (projectId === null || selectedId === null) {
      setVariance(null);
      return;
    }
    let cancelled = false;
    getScheduleVariance(projectId, selectedId).then(
      (result) => {
        if (!cancelled) {
          setVariance(result);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setVariance(null);
          setError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, selectedId]);

  const rows = useMemo(() => {
    if (variance === null) {
      return [];
    }
    return statusFilter === "" ? variance.rows : variance.rows.filter((r) => r.status === statusFilter);
  }, [variance, statusFilter]);

  if (projectId === null) {
    return (
      <section data-screen-id="UI-SCH-003" className="baselines-page">
        <h2>ベースラインと差異</h2>
        <p role="alert">プロジェクトが見つかりません</p>
        <p>
          <Link to="/projects">プロジェクト一覧へ</Link>
        </p>
      </section>
    );
  }

  const canManage = me !== null && hasProjectPermission(me, projectId, "baseline.manage");

  async function handleSave(event: FormEvent) {
    event.preventDefault();
    if (projectId === null) {
      return;
    }
    setSaveError(null);
    setNotice(null);
    if (name.trim() === "") {
      setSaveError({ message: "版の名前を入力してください", details: [] });
      return;
    }
    setSaving(true);
    try {
      const saved = await createScheduleBaseline(projectId, name.trim());
      setName("");
      setNotice(`第 ${saved.version} 版「${saved.name}」を保存した（活動 ${saved.item_count} 件）`);
      setSelectedId(saved.id);
      setOffset(0);
      setReloadKey((k) => k + 1);
    } catch (e) {
      setSaveError(toErrorView(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section data-screen-id="UI-SCH-003" className="baselines-page">
      <h2>ベースラインと差異</h2>
      <nav aria-label="行き先" className="page-links">
        <Link to="/projects">プロジェクト一覧へ</Link>
        <Link to={`/projects/${projectId}/gantt`}>ガント</Link>
      </nav>
      <ErrorAlert error={error} />
      {notice !== null && <p role="status">{notice}</p>}

      {canManage && (
        <form aria-label="ベースラインを保存する" onSubmit={(e) => void handleSave(e)} className="baseline-form">
          <p>今の全ての活動の開始日・終了日・期間・見積を、新しい版として固定する（版は上書きされず増える）。</p>
          <label>
            版の名前
            <input value={name} maxLength={BASELINE_NAME_MAX} onChange={(e) => setName(e.target.value)} />
          </label>
          <ErrorAlert error={saveError} />
          <button type="submit" disabled={saving}>
            保存する
          </button>
        </form>
      )}

      <section aria-labelledby="baseline-list-heading">
        <h3 id="baseline-list-heading">版の一覧</h3>
        {page !== null && page.items.length === 0 && <p>保存した版はまだありません</p>}
        {page !== null && page.items.length > 0 && (
          <>
            <table aria-label="ベースラインの版">
              <thead>
                <tr>
                  <th scope="col">比べる</th>
                  <th scope="col">版</th>
                  <th scope="col">名前</th>
                  <th scope="col">理由</th>
                  <th scope="col">保存した日時</th>
                  <th scope="col">活動の数</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((b) => (
                  <tr key={b.id}>
                    <td>
                      <input
                        type="radio"
                        name="compare-baseline"
                        aria-label={`第 ${b.version} 版と比べる`}
                        checked={selectedId === b.id}
                        onChange={() => setSelectedId(b.id)}
                      />
                    </td>
                    <td>第 {b.version} 版</td>
                    <td>{b.name}</td>
                    <td>{REASON_LABELS[b.reason] ?? b.reason}</td>
                    <td>{formatDateTime(b.created_at)}</td>
                    <td>{b.item_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pager total={page.total} limit={page.limit} offset={page.offset} onChange={setOffset} />
          </>
        )}
      </section>

      {variance !== null && (
        <section aria-labelledby="variance-heading">
          <h3 id="variance-heading">
            第 {variance.baseline.version} 版「{variance.baseline.name}」と最新計画の差異
          </h3>
          {variance.restricted && <p className="note">自分の割当の活動だけを表示している</p>}
          <p>差は「最新 − 基準」。日付の差は稼働日、見積の差は人時。</p>
          <label>
            区別で絞り込む
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as VarianceStatus | "")}>
              <option value="">すべて</option>
              {(Object.keys(VARIANCE_STATUS_LABELS) as VarianceStatus[]).map((s) => (
                <option key={s} value={s}>
                  {VARIANCE_STATUS_LABELS[s]}
                </option>
              ))}
            </select>
          </label>
          <table aria-label="基準と最新計画の差異">
            <thead>
              <tr>
                <th scope="col">区別</th>
                <th scope="col">WBS ID</th>
                <th scope="col">活動</th>
                <th scope="col">基準の開始</th>
                <th scope="col">基準の終了</th>
                <th scope="col">最新の開始</th>
                <th scope="col">最新の終了</th>
                <th scope="col">開始の差</th>
                <th scope="col">終了の差</th>
                <th scope="col">基準の見積</th>
                <th scope="col">最新の見積</th>
                <th scope="col">見積の差</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.status}-${r.activity_id ?? `x${i}`}`} data-status={r.status}>
                  <td>{VARIANCE_STATUS_LABELS[r.status]}</td>
                  <td>{r.wbs_code}</td>
                  <td>{r.name}</td>
                  <td>{text(r.baseline_start)}</td>
                  <td>{text(r.baseline_finish)}</td>
                  <td>{text(r.current_start)}</td>
                  <td>{text(r.current_finish)}</td>
                  <td>{signed(r.start_variance_days, " 日")}</td>
                  <td>{signed(r.finish_variance_days, " 日")}</td>
                  <td>{text(r.baseline_estimate_hours)}</td>
                  <td>{text(r.current_estimate_hours)}</td>
                  <td>{signed(r.estimate_variance_hours, " 人時")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && <p>該当する活動はありません</p>}
        </section>
      )}
    </section>
  );
}
