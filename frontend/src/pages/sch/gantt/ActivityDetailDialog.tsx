// UI-SCH-002 の活動の詳細と進捗のダイアログ（設計書 7.3。CR-027）。活動の行の見出し・バーのダブルクリック、右クリックの
// メニューの「詳細と進捗…」、判定の一覧の活動の名前から開く。project.view を持つ全員が開け、書く欄はその権限を持つ人にだけ出す。
// 詳細：活動の値は API-SCH-001 から読む。名前・希望開始日・期間（API-SCH-003）と担当と割当工数（API-EFF-001）は schedule.edit。
// 進捗：表示の設定で選んだ報告日のその活動の行（API-PRG-006）。未確定の報告日で、progress.enter_all か、progress.enter_own で
// その活動の担当の人が書く（UI-PRG-001 と同じ部品 EntryEditor・同じ権限）。保存するとガントの画面が読み直し、ダイアログは開いたまま
import { useEffect, useMemo, useState, type FormEvent } from "react";

import { hasProjectPermission, type Me } from "../../../api/cmn";
import { toErrorView, type ErrorView } from "../../../api/errors";
import { fetchAllPages } from "../../../api/pages";
import type { LightningLine, LightningRow, StatusReport } from "../../../api/prg";
import { listMembers, type Member } from "../../../api/prj";
import { listActivities, updateActivity, type Activity, type ActivityUpdateInput } from "../../../api/sch";
import Dialog from "../../../components/Dialog";
import ErrorAlert from "../../../components/ErrorAlert";
import { JUDGEMENT_LABELS, judgementColor } from "../../../components/gantt/scale";
import EntryEditor from "../../prg/EntryEditor";
import { progressMethodLabel } from "../../prg/labels";
import AssignmentEditor from "../activities/AssignmentEditor";
import { ACTIVITY_NAME_MAX, DURATION_MAX } from "../limits";

export interface ActivityDetailDialogProps {
  projectId: number;
  activityId: number;
  me: Me;
  /** 編集の部品が読んだ活動の一覧（API-SCH-001）。null ならダイアログが読む（編集できない人） */
  activities: Activity[] | null;
  /** 表示の設定で選んだ報告日（null は選んでいない）と、そのイナズマ線（読むまで null） */
  report: StatusReport | null;
  lightning: LightningLine | null;
  /** イナズマ線を読めなかった（進捗の節に「読み込み中」を出し続けない） */
  lightningFailed: boolean;
  /** 画面の読み直しの番号（変わると、ダイアログが読んだ活動の一覧も読み直す） */
  reloadKey: number;
  /** 保存した（画面がガント・イナズマ線・WBS・活動を読み直す） */
  onSaved: () => void;
  onClose: () => void;
}

/** 詳細の入力欄の値（文字のまま持つ） */
export interface DetailForm {
  name: string;
  /** 空は指定なし */
  requestedStart: string;
  duration: string;
}

/**
 * 詳細の入力欄を API-SCH-003 の本体にする（変えた項目だけ）。送れない形は理由を返す。
 * マイルストーンは期間を送らない（期間の欄を出さない。7.3）
 */
export function detailChanges(
  activity: Activity,
  form: DetailForm,
): { input: ActivityUpdateInput; errors: [] } | { input: null; errors: string[] } {
  const errors: string[] = [];
  const input: ActivityUpdateInput = {};
  const name = form.name.trim();
  if (name === "") {
    errors.push("名前を入力してください");
  } else if (name.length > ACTIVITY_NAME_MAX) {
    errors.push(`名前は ${ACTIVITY_NAME_MAX} 文字までです`);
  } else if (name !== activity.name) {
    input.name = name;
  }
  const requested = form.requestedStart === "" ? null : form.requestedStart;
  if (requested !== activity.requested_start) {
    input.requested_start = requested;
  }
  if (!activity.is_milestone) {
    const text = form.duration.trim();
    if (!/^\d+$/.test(text) || Number(text) < 1 || Number(text) > DURATION_MAX) {
      errors.push(`期間は 1 以上 ${DURATION_MAX} 以下の整数（稼働日）で入力してください`);
    } else if (Number(text) !== activity.duration_days) {
      input.duration_days = Number(text);
    }
  }
  return errors.length > 0 ? { input: null, errors } : { input, errors: [] };
}

function hoursText(value: number | null): string {
  return value === null ? "—" : `${value} 人時`;
}

function isActivityRow(row: LightningRow | undefined): row is LightningRow & { activity_id: number } {
  return row !== undefined && row.activity_id !== null;
}

export default function ActivityDetailDialog({
  projectId,
  activityId,
  me,
  activities,
  report,
  lightning,
  lightningFailed,
  reloadKey,
  onSaved,
  onClose,
}: ActivityDetailDialogProps) {
  const [loaded, setLoaded] = useState<Activity[] | null>(null);
  const [loadError, setLoadError] = useState<ErrorView | null>(null);
  const [members, setMembers] = useState<Member[] | null>(null);
  const [membersError, setMembersError] = useState<ErrorView | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const can = (permission: string) => hasProjectPermission(me, projectId, permission);
  const canEditSchedule = can("schedule.edit");
  const canEnterAll = can("progress.enter_all");
  const canEnterOwn = can("progress.enter_own");
  const mustLoad = activities === null;

  // 編集の部品が活動を読んでいなければ、ダイアログが読む（保存の後も読み直す）
  useEffect(() => {
    if (!mustLoad) {
      return;
    }
    let cancelled = false;
    setLoadError(null);
    fetchAllPages((page) => listActivities(projectId, page)).then(
      (items) => {
        if (!cancelled) {
          setLoaded(items);
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
  }, [projectId, reloadKey, mustLoad]);

  // 担当の選択肢（プロジェクトのメンバー。API-PRJ-009）。担当を書ける人だけ
  useEffect(() => {
    if (!canEditSchedule) {
      return;
    }
    let cancelled = false;
    fetchAllPages((page) => listMembers(projectId, page)).then(
      (items) => {
        if (!cancelled) {
          setMembers(items);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setMembersError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, canEditSchedule]);

  const list = activities ?? loaded;
  const activity = list?.find((a) => a.id === activityId) ?? null;
  const labelOf = useMemo(() => {
    const map = new Map((list ?? []).map((a) => [a.id, `${a.wbs_code} ${a.name}`]));
    return (id: number) => map.get(id) ?? `活動 ${id}`;
  }, [list]);
  const lineLoaded = lightning !== null && report !== null && lightning.report.id === report.id;
  const row = lineLoaded ? lightning.rows.find((r) => r.activity_id === activityId) : undefined;
  // 確定したかは読み直したイナズマ線の報告日で見る（画面を開いた後に別の人が確定しても、書く欄を出さない）
  const finalized = lineLoaded ? lightning.report.status === "FINALIZED" : report?.status === "FINALIZED";

  function saved(text: string) {
    setMessage(text);
    onSaved();
  }

  const title = activity === null ? "活動の詳細と進捗" : `活動の詳細と進捗：${activity.wbs_code} ${activity.name}`;
  return (
    <Dialog title={title} onClose={onClose} className="dialog-wide">
      <p role="status" className="dialog-status">
        {message ?? ""}
      </p>
      <ErrorAlert error={loadError} />
      {list === null && loadError === null && <p className="loading">読み込み中…</p>}
      {list !== null && activity === null && <p role="alert">この活動は見つかりません（消されたか、見る権限がありません）</p>}
      {activity !== null && (
        <>
          <section aria-labelledby="detail-heading" className="dialog-section">
            <h4 id="detail-heading">詳細</h4>
            <dl className="detail-list">
              <dt>活動の番号</dt>
              <dd>{activity.wbs_code}</dd>
              <dt>置き場の WP</dt>
              <dd>{activity.work_package_name}</dd>
              <dt>予定</dt>
              <dd>{`${activity.start_date ?? "—"}〜${activity.finish_date ?? "—"}`}</dd>
              <dt>余裕</dt>
              <dd>
                {activity.total_float === null ? "—" : `${activity.total_float} 日`}
                {activity.is_critical && "（クリティカル）"}
              </dd>
              <dt>先行</dt>
              <dd>{activity.predecessor_ids.length === 0 ? "なし" : activity.predecessor_ids.map(labelOf).join("、")}</dd>
              <dt>後続</dt>
              <dd>{activity.successor_ids.length === 0 ? "なし" : activity.successor_ids.map(labelOf).join("、")}</dd>
              <dt>見積</dt>
              <dd>{hoursText(activity.estimate_hours)}</dd>
              {!canEditSchedule && (
                <>
                  <dt>希望開始日</dt>
                  <dd>{activity.requested_start ?? "指定なし"}</dd>
                  <dt>期間</dt>
                  <dd>{activity.is_milestone ? "マイルストーン" : `${activity.duration_days} 稼働日`}</dd>
                  <dt>担当と割当工数</dt>
                  <dd>
                    {activity.assignees.length === 0
                      ? "なし"
                      : activity.assignees.map((a) => `${a.display_name}（${hoursText(a.planned_hours)}）`).join("、")}
                  </dd>
                </>
              )}
            </dl>
            {canEditSchedule && (
              <DetailEditor
                key={`${activity.id}|${activity.name}|${activity.requested_start ?? ""}|${activity.duration_days}`}
                projectId={projectId}
                activity={activity}
                onSaved={saved}
              />
            )}
            <p className="note">依存は図の上で、見積はタスクリストで変える</p>
          </section>

          {canEditSchedule && (
            <section aria-labelledby="assignment-heading" className="dialog-section">
              <h4 id="assignment-heading">担当と割当工数</h4>
              <ErrorAlert error={membersError} />
              {members === null && membersError === null && <p className="loading">メンバーを読み込み中…</p>}
              {members !== null && (
                <AssignmentEditor
                  key={`${activity.id}|${activity.assignees.map((a) => `${a.user_id}:${a.planned_hours ?? ""}`).join(",")}`}
                  projectId={projectId}
                  activity={activity}
                  members={members}
                  onSaved={() => saved("担当と割当工数を保存した")}
                  onClose={onClose}
                  embedded
                />
              )}
            </section>
          )}

          <section aria-labelledby="progress-heading" className="dialog-section">
            <h4 id="progress-heading">
              {report === null ? "進捗" : `進捗（報告日 ${report.report_date}${finalized ? "・確定済み" : ""}）`}
            </h4>
            <ProgressPart
              projectId={projectId}
              report={report}
              finalized={finalized}
              row={row}
              lightningLoaded={lineLoaded}
              lightningFailed={lightningFailed}
              canEnter={canEnterAll || (canEnterOwn && activity.assignees.some((a) => a.user_id === me.user.id))}
              canEditMethod={canEditSchedule}
              canApprove={canEnterAll}
              onSaved={saved}
            />
          </section>
        </>
      )}
    </Dialog>
  );
}

function DetailEditor({
  projectId,
  activity,
  onSaved,
}: {
  projectId: number;
  activity: Activity;
  onSaved: (message: string) => void;
}) {
  const [form, setForm] = useState<DetailForm>({
    name: activity.name,
    requestedStart: activity.requested_start ?? "",
    duration: String(activity.duration_days),
  });
  const [error, setError] = useState<ErrorView | null>(null);
  const [saving, setSaving] = useState(false);
  // 保存した時点の活動。画面が読み直して新しい値が来るまでは、同じ変更を二重に送らない
  const [savedFrom, setSavedFrom] = useState<Activity | null>(null);
  const checked = detailChanges(activity, form);
  const unchanged = checked.input !== null && Object.keys(checked.input).length === 0;
  const waiting = savedFrom === activity;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (saving || unchanged || waiting) {
      return;
    }
    if (checked.input === null) {
      setError({ message: "入力に誤りがあります", details: checked.errors });
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const result = await updateActivity(projectId, activity.id, checked.input);
      const notice =
        result.requested_start !== null && result.start_date !== null && result.start_date !== result.requested_start
          ? `。計算した開始は ${result.start_date}（先行の終わり・非稼働日による）`
          : "";
      setSavedFrom(activity);
      onSaved(`詳細を保存した（予定 ${result.start_date ?? "—"}〜${result.finish_date ?? "—"}）${notice}`);
    } catch (e) {
      setError(toErrorView(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form aria-label="活動の詳細" className="detail-form" onSubmit={(e) => void handleSubmit(e)}>
      <label>
        名前
        <input
          value={form.name}
          maxLength={ACTIVITY_NAME_MAX}
          onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
        />
      </label>
      <label>
        希望開始日
        <input
          type="date"
          value={form.requestedStart}
          onChange={(e) => setForm((f) => ({ ...f, requestedStart: e.target.value }))}
        />
      </label>
      {activity.is_milestone ? (
        <p className="note">マイルストーン（期間なし）</p>
      ) : (
        <label>
          期間（稼働日）
          <input
            inputMode="numeric"
            value={form.duration}
            onChange={(e) => setForm((f) => ({ ...f, duration: e.target.value }))}
          />
        </label>
      )}
      <ErrorAlert error={error} />
      <button type="submit" disabled={saving || unchanged || waiting}>
        詳細を保存する
      </button>
    </form>
  );
}

function ProgressPart({
  projectId,
  report,
  finalized,
  row,
  lightningLoaded,
  lightningFailed,
  canEnter,
  canEditMethod,
  canApprove,
  onSaved,
}: {
  projectId: number;
  report: StatusReport | null;
  finalized: boolean;
  row: LightningRow | undefined;
  lightningLoaded: boolean;
  lightningFailed: boolean;
  canEnter: boolean;
  canEditMethod: boolean;
  canApprove: boolean;
  onSaved: (message: string) => void;
}) {
  if (report === null) {
    return <p className="note">報告日を選んでいない。表示の設定で報告日を選ぶ（作る）と、ここで進捗を書ける</p>;
  }
  if (!lightningLoaded) {
    return lightningFailed ? (
      <p role="alert">この報告日のイナズマ線を読めないので、進捗を出せない。画面を開き直してください</p>
    ) : (
      <p className="loading">読み込み中…</p>
    );
  }
  if (!isActivityRow(row)) {
    return <p className="note">この報告日の線にこの活動の行が無い（報告日の後に足した活動など）</p>;
  }
  // 値を書けるのは未確定の報告日で canEnter の人、算定方式は未確定の報告日で schedule.edit の人。値を書けない人には値を出す（7.3）
  const writeValues = !finalized && canEnter;
  const writeMethod = !finalized && canEditMethod;
  return (
    <>
      <p>
        判定 <span style={{ color: judgementColor(row.judgement) }}>●</span> {JUDGEMENT_LABELS[row.judgement]}
        {row.judgement === "NOT_COMPUTED" && "（進捗の入力が要る）"}
      </p>
      {!writeValues && (
        <dl className="detail-list">
          {!writeMethod && (
            <>
              <dt>算定方式</dt>
              <dd>{progressMethodLabel(row.progress_method)}</dd>
            </>
          )}
          <dt>進捗率</dt>
          <dd>{row.percent_complete === null ? "—" : `${row.percent_complete}%`}</dd>
          <dt>実績開始日</dt>
          <dd>{row.actual_start ?? "—"}</dd>
          <dt>実績終了日</dt>
          <dd>{row.actual_finish ?? "—"}</dd>
          <dt>残工数</dt>
          <dd>{hoursText(row.remaining_hours)}</dd>
          <dt>完了見込み日</dt>
          <dd>{row.forecast_finish ?? "—"}</dd>
          <dt>遅延理由</dt>
          <dd>{row.delay_reason ?? "—"}</dd>
        </dl>
      )}
      {(writeValues || writeMethod) && (
        <EntryEditor
          key={`${report.id}-${row.activity_id}`}
          projectId={projectId}
          reportId={report.id}
          row={row}
          canEnter={canEnter}
          canEditMethod={canEditMethod}
          canApprove={canApprove}
          onSaved={onSaved}
          onClose={() => undefined}
          embedded
        />
      )}
      {!writeValues && (
        <p className="note">
          {finalized
            ? "確定済みの報告日なので値だけを出す"
            : writeMethod
              ? "この活動の進捗の値を書く権限が無い（算定方式は変えられる）"
              : "この活動の進捗を書く権限が無い"}
        </p>
      )}
    </>
  );
}
