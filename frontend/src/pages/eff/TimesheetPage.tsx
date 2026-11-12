// UI-EFF-001 工数入力（週のタイムシート）（設計書 7.1 のルート /timesheet）。EFF-02。
// 自分の割当の活動（API-EFF-002。effort.enter_own を持つプロジェクトごと）を行に、月曜から日曜の 7 日を列にして、
// 日別・活動別の実績工数を入力する。読むのは API-EFF-004（自分の行）、書くのは API-EFF-003（変えたマスだけをまとめて送る）。
// 1 日の合計が 24 時間を超える日は画面でも示して送らない（正の検査はサーバの RULE-EFF-005。問題があれば何も書かれない）
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { getMe, hasProjectPermission, type Me } from "../../api/cmn";
import {
  listAssignments,
  listTimeEntries,
  putTimeEntries,
  type Assignment,
  type TimeEntry,
  type TimeEntryCellInput,
} from "../../api/eff";
import { toErrorView, type ErrorView } from "../../api/errors";
import { listProjects, type Project } from "../../api/prj";
import ErrorAlert from "../../components/ErrorAlert";
import {
  addDays,
  centsText,
  DAY_HOURS_MAX,
  dayLabel,
  LIST_LIMIT_MAX,
  localIsoDate,
  parseHoursCents,
  resolveWeek,
  toCents,
  weekDays,
} from "./labels";

const ENTER_OWN = "effort.enter_own";

/** 自分の割当（プロジェクトごと。API-EFF-002 の並び＝ガントの行の順） */
export interface AssignedGroup {
  projectId: number;
  items: Assignment[];
}

/** タイムシートの 1 行（1 つの活動） */
export interface SheetRow {
  projectId: number;
  activityId: number;
  wbsCode: string;
  activityName: string;
  /** 自分の割当の活動か。割当が外れた活動の実績は読むだけ（書けばサーバが 403 を返す） */
  assigned: boolean;
}

/**
 * 行を組む：プロジェクトの順（自分の割当のあるプロジェクトの順）→割当の順に並べ、
 * 割当に無いが実績のある活動（割当が外れた活動）はそのプロジェクトの末尾に足す
 */
export function buildRows(groups: readonly AssignedGroup[], entries: readonly TimeEntry[]): SheetRow[] {
  const rows: SheetRow[] = [];
  const seen = new Set<number>();
  for (const group of groups) {
    for (const a of group.items) {
      if (!seen.has(a.activity_id)) {
        seen.add(a.activity_id);
        rows.push({
          projectId: group.projectId,
          activityId: a.activity_id,
          wbsCode: a.wbs_code,
          activityName: a.activity_name,
          assigned: true,
        });
      }
    }
  }
  for (const e of entries) {
    if (!seen.has(e.activity_id)) {
      seen.add(e.activity_id);
      rows.push({
        projectId: e.project_id,
        activityId: e.activity_id,
        wbsCode: e.wbs_code,
        activityName: e.activity_name,
        assigned: false,
      });
    }
  }
  const order = groups.map((g) => g.projectId);
  const rank = (projectId: number): [number, number] => {
    const i = order.indexOf(projectId);
    return i === -1 ? [1, projectId] : [0, i];
  };
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => {
      const [ga, ra] = rank(a.row.projectId);
      const [gb, rb] = rank(b.row.projectId);
      return ga - gb || ra - rb || a.index - b.index;
    })
    .map((x) => x.row);
}

function cellKey(activityId: number, day: string): string {
  return `${activityId}|${day}`;
}

interface Setup {
  /** effort.enter_own を持つプロジェクトが 1 つも無い */
  noProjects: boolean;
  projects: ReadonlyMap<number, Project>;
  groups: AssignedGroup[];
}

interface WeekEntries {
  monday: string;
  items: TimeEntry[];
  total: number;
}

interface Change {
  activityId: number;
  day: string;
  cents: number;
  note: string | null;
}

export default function TimesheetPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const today = localIsoDate(new Date());
  const monday = resolveWeek(searchParams.get("week"), today);
  const days = useMemo(() => weekDays(monday), [monday]);
  const sunday = days[6] ?? monday;

  const [me, setMe] = useState<Me | null>(null);
  const [setup, setSetup] = useState<Setup | null>(null);
  const [week, setWeek] = useState<WeekEntries | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<ErrorView | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  // 自分（利用者の ID とプロジェクトごとの権限）
  useEffect(() => {
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
  }, []);

  // 入力できるプロジェクトと、そこでの自分の割当
  useEffect(() => {
    if (me === null) {
      return;
    }
    const targets = me.projects.filter((p) => hasProjectPermission(me, p.project_id, ENTER_OWN)).map((p) => p.project_id);
    if (targets.length === 0) {
      setSetup({ noProjects: true, projects: new Map(), groups: [] });
      return;
    }
    let cancelled = false;
    Promise.all([
      listProjects({ limit: LIST_LIMIT_MAX, offset: 0 }),
      Promise.all(
        targets.map((projectId) =>
          listAssignments(projectId, { user_id: me.user.id, limit: LIST_LIMIT_MAX, offset: 0 }).then(
            (page): AssignedGroup => ({ projectId, items: page.items }),
          ),
        ),
      ),
    ]).then(
      ([projects, groups]) => {
        if (!cancelled) {
          setSetup({ noProjects: false, projects: new Map(projects.items.map((p) => [p.id, p])), groups });
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
  }, [me]);

  // 表示する週の自分の実績
  useEffect(() => {
    if (me === null || setup === null || setup.noProjects) {
      return;
    }
    let cancelled = false;
    setWeek(null);
    setDrafts({});
    setSaveError(null);
    setSaved(null);
    listTimeEntries({ user_id: me.user.id, from: monday, to: sunday, limit: LIST_LIMIT_MAX, offset: 0 }).then(
      (page) => {
        if (!cancelled) {
          setWeek({ monday, items: page.items, total: page.total });
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
  }, [me, setup, monday, sunday]);

  const entries = useMemo(() => (week !== null && week.monday === monday ? week.items : null), [week, monday]);
  const original = useMemo(() => {
    const map = new Map<string, TimeEntry>();
    for (const e of entries ?? []) {
      map.set(cellKey(e.activity_id, e.work_date), e);
    }
    return map;
  }, [entries]);
  const rows = useMemo(
    () => (setup === null || entries === null ? [] : buildRows(setup.groups, entries)),
    [setup, entries],
  );

  function originalCents(key: string): number {
    const e = original.get(key);
    return e === undefined ? 0 : toCents(e.hours);
  }

  function shownText(key: string): string {
    const draft = drafts[key];
    if (draft !== undefined) {
      return draft;
    }
    const cents = originalCents(key);
    return cents === 0 ? "" : centsText(cents);
  }

  // 入力の解釈：読めないマス、変わったマス、日ごとの合計（読めないマスは元の値で数える）
  const invalid: string[] = [];
  const changes: Change[] = [];
  const dayTotals = new Map<string, number>(days.map((d) => [d, 0]));
  const rowTotals = new Map<number, number>();
  for (const row of rows) {
    for (const day of days) {
      const key = cellKey(row.activityId, day);
      const draft = drafts[key];
      let cents = originalCents(key);
      if (draft !== undefined) {
        const parsed = parseHoursCents(draft);
        if (parsed === null) {
          invalid.push(`${row.wbsCode} ${row.activityName}・${day}：「${draft}」`);
        } else {
          if (parsed !== cents) {
            changes.push({
              activityId: row.activityId,
              day,
              cents: parsed,
              note: parsed === 0 ? null : (original.get(key)?.note ?? null),
            });
          }
          cents = parsed;
        }
      }
      dayTotals.set(day, (dayTotals.get(day) ?? 0) + cents);
      rowTotals.set(row.activityId, (rowTotals.get(row.activityId) ?? 0) + cents);
    }
  }
  const overDays = days.filter((d) => (dayTotals.get(d) ?? 0) > DAY_HOURS_MAX * 100);
  const weekTotal = [...dayTotals.values()].reduce((a, b) => a + b, 0);
  const dirty = invalid.length > 0 || changes.length > 0;

  function moveTo(nextMonday: string) {
    setSearchParams({ week: nextMonday });
  }

  function handleChange(key: string, value: string) {
    setDrafts((prev) => ({ ...prev, [key]: value }));
    setSaved(null);
  }

  async function handleSave() {
    setSaved(null);
    if (invalid.length > 0) {
      setSaveError({ message: "工数は 0 以上の数（小数 2 桁まで）で入力してください", details: invalid });
      return;
    }
    if (overDays.length > 0) {
      setSaveError({
        message: `1 日の合計が ${DAY_HOURS_MAX} 時間を超えています`,
        details: overDays.map((d) => `${d}：${centsText(dayTotals.get(d) ?? 0)} 時間`),
      });
      return;
    }
    if (changes.length === 0) {
      return;
    }
    const cells: TimeEntryCellInput[] = changes.map((c) => ({
      activity_id: c.activityId,
      work_date: c.day,
      hours: c.cents / 100,
      note: c.note,
    }));
    setSaving(true);
    setSaveError(null);
    try {
      const written = await putTimeEntries(cells);
      const writtenDays = new Set(cells.map((c) => c.work_date));
      setWeek((prev) => {
        if (prev === null || prev.monday !== monday) {
          return prev;
        }
        const kept = prev.items.filter((e) => !writtenDays.has(e.work_date));
        const fresh = written.entries.filter((e) => writtenDays.has(e.work_date));
        return { ...prev, items: [...kept, ...fresh] };
      });
      setDrafts({});
      setSaved(`保存しました（${written.changed} 件）`);
    } catch (e: unknown) {
      setSaveError(toErrorView(e));
    } finally {
      setSaving(false);
    }
  }

  function handleDiscard() {
    setDrafts({});
    setSaveError(null);
    setSaved(null);
  }

  function projectLabel(projectId: number): string {
    const p = setup?.projects.get(projectId);
    return p === undefined ? `#${projectId}` : `${p.code} ${p.name}`;
  }

  return (
    <section data-screen-id="UI-EFF-001" className="timesheet-page">
      <h2>工数入力（週のタイムシート）</h2>
      <nav aria-label="週の切り替え" className="week-nav">
        <button type="button" onClick={() => moveTo(addDays(monday, -7))} disabled={dirty || saving}>
          前の週
        </button>
        <span>
          {monday} 〜 {sunday}
        </span>
        <button type="button" onClick={() => moveTo(resolveWeek(null, today))} disabled={dirty || saving}>
          今週
        </button>
        <button type="button" onClick={() => moveTo(addDays(monday, 7))} disabled={dirty || saving}>
          次の週
        </button>
      </nav>
      <ErrorAlert error={error} />

      {setup?.noProjects === true && <p>工数を入力できるプロジェクトがありません</p>}
      {setup !== null && !setup.noProjects && entries === null && error === null && <p>読み込み中…</p>}

      {entries !== null && (
        <>
          {week !== null && week.total > week.items.length && (
            <p className="note">{`この週の実績が ${LIST_LIMIT_MAX} 件を超えるため、先頭の ${LIST_LIMIT_MAX} 件だけを表示している`}</p>
          )}
          {rows.length === 0 ? (
            <p>入力できる活動がありません（自分の割当の活動がありません）</p>
          ) : (
            <table aria-label="週のタイムシート" className="timesheet">
              <thead>
                <tr>
                  <th scope="col">プロジェクト</th>
                  <th scope="col">WBS ID</th>
                  <th scope="col">活動</th>
                  {days.map((d) => (
                    <th key={d} scope="col">
                      {dayLabel(d)}
                    </th>
                  ))}
                  <th scope="col">週の計</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.activityId} data-activity-id={row.activityId}>
                    <td>{projectLabel(row.projectId)}</td>
                    <td>{row.wbsCode}</td>
                    <td>
                      {row.activityName}
                      {!row.assigned && <span className="note">（割当が外れた活動。読むだけ）</span>}
                    </td>
                    {days.map((d) => {
                      const key = cellKey(row.activityId, d);
                      const draft = drafts[key];
                      const bad = draft !== undefined && parseHoursCents(draft) === null;
                      return (
                        <td key={d}>
                          <input
                            type="text"
                            inputMode="decimal"
                            size={5}
                            aria-label={`${row.wbsCode} ${row.activityName} ${d}`}
                            aria-invalid={bad || undefined}
                            value={shownText(key)}
                            disabled={!row.assigned || saving}
                            onChange={(e) => handleChange(key, e.target.value)}
                          />
                        </td>
                      );
                    })}
                    <td>{centsText(rowTotals.get(row.activityId) ?? 0)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr aria-label="1 日の合計">
                  <th scope="row" colSpan={3}>
                    1 日の合計
                  </th>
                  {days.map((d) => {
                    const total = dayTotals.get(d) ?? 0;
                    const over = total > DAY_HOURS_MAX * 100;
                    return (
                      <td key={d} data-day={d} className={over ? "over-limit" : undefined}>
                        {centsText(total)}
                        {over && `（${DAY_HOURS_MAX} 時間超）`}
                      </td>
                    );
                  })}
                  <td>{centsText(weekTotal)}</td>
                </tr>
              </tfoot>
            </table>
          )}

          {dirty && <p className="note">保存していない入力があります（週を移るには、保存するか入力を取り消す）</p>}
          <ErrorAlert error={saveError} />
          {saved !== null && <p role="status">{saved}</p>}
          {rows.some((r) => r.assigned) && (
            <div className="form-actions">
              <button type="button" onClick={() => void handleSave()} disabled={!dirty || saving}>
                保存する
              </button>
              <button type="button" onClick={handleDiscard} disabled={!dirty || saving}>
                入力を取り消す
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
