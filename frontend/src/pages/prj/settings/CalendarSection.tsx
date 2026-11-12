// UI-PRJ-002 の稼働カレンダー（PRJ-04。API-PRJ-007・008）。稼働の曜日・1 日の稼働時間・休日を変え、まとめて保存する。
// 保存するとサーバが日程を計算し直す（API-PRJ-008）。変えられるのは project.manage を持つ人だけ（9.1）
import { useEffect, useState } from "react";

import { toErrorView, type ErrorView } from "../../../api/errors";
import { getCalendar, replaceCalendar, type CalendarHoliday, type WorkCalendar } from "../../../api/prj";
import ErrorAlert from "../../../components/ErrorAlert";
import { optionalText } from "../../../lib/form";
import { WEEKDAY_LABELS } from "../labels";
import { HOLIDAY_NAME_MAX, HOURS_PER_DAY_MAX } from "../limits";

/** 7 文字の working_weekdays（月曜から日曜。1 が稼働日。設計書 4.1）を曜日ごとの真偽にする */
export function parseWeekdays(text: string): boolean[] {
  return WEEKDAY_LABELS.map((_, index) => text.charAt(index) === "1");
}

export function formatWeekdays(flags: readonly boolean[]): string {
  return WEEKDAY_LABELS.map((_, index) => (flags[index] ? "1" : "0")).join("");
}

/** 休日を日付の順に足す。同じ日付が既にあれば null（サーバも 400 にする） */
export function addHoliday(holidays: readonly CalendarHoliday[], holiday: CalendarHoliday): CalendarHoliday[] | null {
  if (holidays.some((h) => h.holiday_date === holiday.holiday_date)) {
    return null;
  }
  return [...holidays, holiday].sort((a, b) => a.holiday_date.localeCompare(b.holiday_date));
}

export default function CalendarSection({ projectId, canEdit }: { projectId: number; canEdit: boolean }) {
  const [calendar, setCalendar] = useState<WorkCalendar | null>(null);
  const [weekdays, setWeekdays] = useState<boolean[]>([]);
  const [hoursPerDay, setHoursPerDay] = useState("");
  const [holidays, setHolidays] = useState<CalendarHoliday[]>([]);
  const [newDate, setNewDate] = useState("");
  const [newName, setNewName] = useState("");
  const [loadError, setLoadError] = useState<ErrorView | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function show(value: WorkCalendar) {
    setCalendar(value);
    setWeekdays(parseWeekdays(value.working_weekdays));
    setHoursPerDay(String(value.hours_per_day));
    setHolidays(value.holidays);
  }

  useEffect(() => {
    let cancelled = false;
    getCalendar(projectId).then(
      (value) => {
        if (!cancelled) {
          show(value);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setLoadError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  function handleAddHoliday() {
    setNotice(null);
    if (newDate === "") {
      setError({ message: "休日の日付を入れます", details: [] });
      return;
    }
    const next = addHoliday(holidays, { holiday_date: newDate, name: optionalText(newName) });
    if (next === null) {
      setError({ message: `${newDate} は既に休日です`, details: [] });
      return;
    }
    setError(null);
    setHolidays(next);
    setNewDate("");
    setNewName("");
  }

  async function handleSave() {
    setNotice(null);
    const hours = Number(hoursPerDay.trim());
    if (hoursPerDay.trim() === "" || !Number.isFinite(hours) || hours <= 0 || hours > HOURS_PER_DAY_MAX) {
      setError({ message: `1 日の稼働時間は 0 より大きく ${HOURS_PER_DAY_MAX} 以下の数を入れます`, details: [] });
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const saved = await replaceCalendar(projectId, {
        working_weekdays: formatWeekdays(weekdays),
        hours_per_day: hours,
        holidays,
      });
      show(saved);
      setNotice("稼働カレンダーを保存しました（日程を計算し直しました）");
    } catch (e: unknown) {
      setError(toErrorView(e));
    } finally {
      setSubmitting(false);
    }
  }

  const disabled = !canEdit || submitting;

  return (
    <section aria-labelledby="project-calendar-heading">
      <h3 id="project-calendar-heading">稼働カレンダー</h3>
      <ErrorAlert error={loadError} />
      {calendar !== null && (
        <>
          <fieldset disabled={disabled}>
            <legend>稼働の曜日</legend>
            {WEEKDAY_LABELS.map((label, index) => (
              <label key={label}>
                <input
                  type="checkbox"
                  checked={weekdays[index] ?? false}
                  onChange={(e) =>
                    setWeekdays((current) => current.map((flag, i) => (i === index ? e.target.checked : flag)))
                  }
                />
                {label}
              </label>
            ))}
          </fieldset>
          <div>
            <label htmlFor="calendar-hours">1 日の稼働時間</label>
            <input
              id="calendar-hours"
              type="number"
              min={0.01}
              max={HOURS_PER_DAY_MAX}
              step={0.01}
              disabled={disabled}
              value={hoursPerDay}
              onChange={(e) => setHoursPerDay(e.target.value)}
            />
          </div>
          <table aria-label="休日">
            <thead>
              <tr>
                <th scope="col">日付</th>
                <th scope="col">名前</th>
                {canEdit && <th scope="col">操作</th>}
              </tr>
            </thead>
            <tbody>
              {holidays.map((holiday) => (
                <tr key={holiday.holiday_date}>
                  <td>{holiday.holiday_date}</td>
                  <td>{holiday.name ?? ""}</td>
                  {canEdit && (
                    <td>
                      <button
                        type="button"
                        aria-label={`${holiday.holiday_date} を休日から外す`}
                        disabled={submitting}
                        onClick={() => setHolidays((current) => current.filter((h) => h !== holiday))}
                      >
                        外す
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          {holidays.length === 0 && <p>休日はありません</p>}
          {canEdit && (
            <div>
              <label htmlFor="calendar-new-date">休日の日付</label>
              <input
                id="calendar-new-date"
                type="date"
                disabled={submitting}
                value={newDate}
                onChange={(e) => setNewDate(e.target.value)}
              />
              <label htmlFor="calendar-new-name">休日の名前</label>
              <input
                id="calendar-new-name"
                maxLength={HOLIDAY_NAME_MAX}
                disabled={submitting}
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
              />
              <button type="button" disabled={submitting} onClick={handleAddHoliday}>
                休日を足す
              </button>
            </div>
          )}
          {notice !== null && <p role="status">{notice}</p>}
          <ErrorAlert error={error} />
          {canEdit && (
            <button type="button" disabled={submitting} onClick={() => void handleSave()}>
              稼働カレンダーを保存する
            </button>
          )}
        </>
      )}
    </section>
  );
}
