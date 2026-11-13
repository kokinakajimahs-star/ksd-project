"""表形式の入出力（INT）のユースケース（設計書 6.2 の API-INT-001・002、6.3、9 章。要求仕様書 INT-01・BR-02）。

- 権限（RULE-AUTH-001）：出力は data.export、取込は data.import と対象の書く権限（``wbs`` は wbs.edit、``activities`` は
  schedule.edit。どちらも見積の列を変える行があれば estimate.edit も。``time-entries`` は data.import だけ。設計書 1.1・CR-006）。
  委託先だけで読む利用者（9.2 の注記）の出力は、
  自分の割当の活動とその WBS の経路に絞る（見えない活動の先行・実績は出さない）
- 列・照合・検査・往復の一致は RULE-INT-001（``app.domain.tabular``）。このモジュールは DB の行を 6.3 の行へ直して渡し、
  取込の計画（``ImportPlan``）を DB へ書く
- 形式：CSV は ``encode_csv``・``decode_csv``（UTF-8 の BOM 付き）。xlsx は openpyxl で 1 枚目のシートに同じ表を置く（6.3）。
  出力の xlsx のセルは全て文字（``=`` で始まる名称も式にしない。往復で一致させる）
- 取込は全行を検査し、誤りがあれば何も書かない（6.3）。``dry_run`` は検査と書く行の一覧だけを返す。
  誤りのある取込（``dry_run`` でない）は 422 ``IMPORT_INVALID``（``details`` が ``{row, column, code, message}`` の配列）
- 書くときは 1 つのトランザクションで、既存の API と同じ規則を当てる：WBS は RULE-WBS-002 の採番（``new_element_code``）・
  移動（``move_element``。ID は変えない）・RULE-WBS-006 の EDIT（承認待ちは 409）、活動は希望開始日を稼働日へ寄せて日程を計算し直す
  （RULE-SCH-002・003）、見積の値の変更は見積の記録（``estimate_records``。EST-08）を足す、実績工数の 0 時間は行を持たない
- 書き込みは ORM を通すので変更履歴（``audit_logs``）に残る（6.1。A-37。割当の置き換えの一括 DELETE は A-37 の限界のとおり記録されない）

詳細と、6.2・6.3 に定めの無かったことの判断は docs/decisions/ASSUMPTIONS.md A-39。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from io import BytesIO
from typing import Final

from openpyxl import Workbook, load_workbook
from openpyxl.utils.exceptions import IllegalCharacterError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.authz import VisibleScope
from app.domain.effort import TimeEntryValue
from app.domain.estimate import InputUnit, TargetType, record_estimate
from app.domain.tabular import (
    ACTIVITY_COLUMNS,
    COLUMNS,
    HEADER_ROW,
    TIME_ENTRY_COLUMNS,
    ActivityChange,
    ActivityRow,
    ChangeAction,
    ImportIssue,
    ImportIssueCode,
    TabularFileError,
    TabularTarget,
    TimeEntryImportChange,
    TimeEntryRow,
    UserEffortContext,
    WbsChange,
    WbsRow,
    cell_text,
    decode_csv,
    encode_csv,
    export_grid,
    plan_activity_import,
    plan_time_entry_import,
    plan_wbs_import,
)
from app.domain.wbs_code import CodeHistoryEntry, WbsCodeError, move_element, new_element_code
from app.domain.wbs_rules import ActivityRef
from app.domain.workflow import WbsAction, WorkflowError, transition_wbs
from app.errors import AppError
from app.models.cmn import User
from app.models.eff import Assignment, TimeEntry
from app.models.est import EstimateRecord
from app.models.prg import StatusReport
from app.models.prj import ProjectMember
from app.models.sch import Activity, ActivityDependency
from app.models.wbs import WbsCodeHistory, WbsDictionaryEntry, WbsElement
from app.services.auth import authorize_project
from app.services.estimate import project_hours_per_day, visible_scope
from app.services.project_access import ProjectAccess
from app.services.schedule import Plan, ScheduleLinks, calculate_and_store, load_plan, resolve_activity_dates
from app.services.wbs import (
    ActivitiesReader,
    WbsContext,
    coded_element,
    load_wbs_context,
    mark_wbs_edited,
    preorder_elements,
)

DATA_EXPORT: Final = "data.export"
DATA_IMPORT: Final = "data.import"
WBS_EDIT: Final = "wbs.edit"
SCHEDULE_EDIT: Final = "schedule.edit"
ESTIMATE_EDIT: Final = "estimate.edit"
STATUS_FINALIZED: Final = "FINALIZED"
WORK_PACKAGE: Final = "WORK_PACKAGE"


class FileFormat(StrEnum):
    """入出力のファイルの形式（6.2 の ``format``）。"""

    CSV = "csv"
    XLSX = "xlsx"


MEDIA_TYPES: Final[Mapping[FileFormat, str]] = {
    FileFormat.CSV: "text/csv; charset=utf-8",
    FileFormat.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

_DICTIONARY_COLUMNS: Final = ("成果物", "作業範囲", "完了条件", "前提条件")
_SCHEDULE_COLUMNS: Final = ("希望開始日", "期間（稼働日）", "マイルストーン（0/1）")


# ---------------------------------------------------------------------------
# ファイルの読み書き（CSV・xlsx ↔ セルの格子）
# ---------------------------------------------------------------------------


def _xlsx_bytes(grid: Sequence[Sequence[str]], sheet_title: str) -> bytes:
    """表を 1 枚目のシートに置いた xlsx のバイト列にする。セルは全て文字（式として解釈させない）。空のセルは置かない。"""
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None  # 新しいブックには必ず 1 枚目のシートがある
    sheet.title = sheet_title
    try:
        for r, values in enumerate(grid, start=1):
            for c, value in enumerate(values, start=1):
                if value == "":
                    continue
                cell = sheet.cell(row=r, column=c, value=value)
                cell.data_type = "s"  # openpyxl は "=" で始まる文字を式にする。文字のまま置く（往復で一致させる）
    except IllegalCharacterError as exc:
        raise AppError(422, "EXPORT_UNSUPPORTED_CHARACTER", "xlsx に置けない制御文字を含む値があります（CSV で出力します）",
                       details=[{"row": r, "column": c}]) from exc
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _xlsx_value(value: object) -> object:
    """xlsx から読んだセルの値を ``cell_text`` が扱える値にする（時刻・期間などは文字にして、形式の誤りとして返す）。"""
    if value is None or isinstance(value, (str, bool, int, Decimal, datetime, date)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    return str(value)  # 時刻・期間など


def _unreadable(message: str) -> TabularFileError:
    return TabularFileError([ImportIssue(HEADER_ROW, None, ImportIssueCode.FILE_UNREADABLE, message)])


def _xlsx_grid(data: bytes) -> list[list[object]]:
    """xlsx の 1 枚目のシートの格子（1 行目が見出し）。xlsx として読めなければ ``TabularFileError``。"""
    try:
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # 壊れた・xlsx でないファイル（zip・XML・形式の誤りなど openpyxl は様々な例外を投げる）
        raise _unreadable(f"xlsx として読めない（{type(exc).__name__}）") from exc
    try:
        if not workbook.worksheets:
            raise _unreadable("xlsx にシートが無い")
        sheet = workbook.worksheets[0]
        return [[_xlsx_value(v) for v in row] for row in sheet.iter_rows(values_only=True)]
    except TabularFileError:
        raise
    except Exception as exc:  # シートの XML が壊れている
        raise _unreadable(f"xlsx の 1 枚目のシートを読めない（{type(exc).__name__}）") from exc
    finally:
        workbook.close()


def read_grid(file_format: FileFormat, data: bytes) -> list[list[object]]:
    """ファイルのバイト列をセルの格子にする。読めなければ ``TabularFileError``（6.3 の誤りの形）。"""
    if file_format is FileFormat.CSV:
        return [list(row) for row in decode_csv(data)]
    return _xlsx_grid(data)


def write_file(file_format: FileFormat, grid: Sequence[Sequence[str]], target: TabularTarget) -> bytes:
    if file_format is FileFormat.CSV:
        return encode_csv(grid)
    return _xlsx_bytes(grid, target.value)


# ---------------------------------------------------------------------------
# 共通の部品
# ---------------------------------------------------------------------------


def _logins_of(session: Session, user_ids: Iterable[int]) -> dict[int, str]:
    ids = sorted(set(user_ids))
    if not ids:
        return {}
    return dict(session.execute(select(User.id, User.login_id).where(User.id.in_(ids))).tuples().all())


def _assignable(session: Session, project_id: int) -> dict[str, int]:
    """責任者・担当に置ける利用者（そのプロジェクトのメンバーである有効な利用者。WBS-10・EFF-01）のログインID → ID。"""
    rows = session.execute(
        select(User.login_id, User.id)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project_id, User.is_active.is_(True))
    ).tuples().all()
    return dict(rows)


def _history(session: Session, project_id: int) -> list[CodeHistoryEntry]:
    return [
        CodeHistoryEntry(id=r.id, element_id=r.element_id, old_code=r.old_code, new_code=r.new_code,
                         renumbered_at=r.renumbered_at)
        for r in session.scalars(select(WbsCodeHistory).where(WbsCodeHistory.project_id == project_id))
    ]


def _dictionaries(session: Session, element_ids: Iterable[int]) -> dict[int, WbsDictionaryEntry]:
    ids = sorted(set(element_ids))
    if not ids:
        return {}
    return {d.element_id: d for d in session.scalars(select(WbsDictionaryEntry).where(WbsDictionaryEntry.element_id.in_(ids)))}


def _hours(value: object) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _predecessors(dependencies: Iterable[ActivityDependency]) -> dict[int, set[int]]:
    result: dict[int, set[int]] = {}
    for d in dependencies:
        result.setdefault(d.successor_id, set()).add(d.predecessor_id)
    return result


def _assignees(links: ScheduleLinks, session: Session, project_id: int) -> dict[int, set[int]]:
    result: dict[int, set[int]] = {}
    for ref in links.assignments.assignments(session, project_id):
        result.setdefault(ref.activity_id, set()).add(ref.user_id)
    return result


def _add_estimate_record(
    session: Session, project_id: int, target_type: TargetType, target_id: int, hours: Decimal, actor: User,
    now: datetime,
) -> None:
    """見積の値を変えた記録を 1 行足す（EST-08。単位は人時。API-EST-001 と同じ形）。"""
    record = record_estimate(
        project_id=project_id, target_type=target_type, target_id=target_id, input_value=hours,
        input_unit=InputUnit.HOUR, hours_per_day=project_hours_per_day(session, project_id), entered_by=actor.id,
        entered_at=now,
    )
    session.add(EstimateRecord(
        project_id=record.project_id, target_type=record.target_type.value, target_id=record.target_id,
        hours=record.hours, input_value=record.input_value, input_unit=record.input_unit.value, method=None,
        assumptions=None, references=None, entered_by=record.entered_by, entered_at=record.entered_at,
    ))


# ---------------------------------------------------------------------------
# 6.3 の行（今の値）
# ---------------------------------------------------------------------------


def _wbs_rows(
    session: Session, elements: Sequence[WbsElement], dictionaries: Mapping[int, WbsDictionaryEntry]
) -> dict[int, WbsRow]:
    by_id = {e.id: e for e in elements}
    logins = _logins_of(session, (e.responsible_user_id for e in elements if e.responsible_user_id is not None))
    rows: dict[int, WbsRow] = {}
    for e in elements:
        entry = dictionaries.get(e.id)
        rows[e.id] = WbsRow(
            wbs_code=e.wbs_code,
            parent_code=None if e.parent_id is None else by_id[e.parent_id].wbs_code,
            kind=e.kind,  # type: ignore[arg-type]  # 4.1 の値（ElementKind の値）
            name=e.name,
            responsible_login_id=None if e.responsible_user_id is None else logins[e.responsible_user_id],
            own_estimate_hours=_hours(e.own_estimate_hours),
            deliverable=None if entry is None else entry.deliverable,
            work_scope=None if entry is None else entry.work_scope,
            completion_criteria=None if entry is None else entry.completion_criteria,
            assumptions=None if entry is None else entry.assumptions,
        )
    return rows


def _activity_rows(
    session: Session, activities: Sequence[Activity], elements: Sequence[WbsElement],
    predecessors: Mapping[int, set[int]], assignees: Mapping[int, set[int]],
) -> dict[int, ActivityRow]:
    codes = {e.id: e.wbs_code for e in elements}
    logins = _logins_of(session, (u for users in assignees.values() for u in users))
    return {
        a.id: ActivityRow(
            activity_id=a.id,
            work_package_code=codes[a.work_package_id],
            name=a.name,
            requested_start=a.requested_start,
            duration_days=a.duration_days,
            is_milestone=bool(a.is_milestone),
            estimate_hours=_hours(a.estimate_hours),
            predecessor_ids=tuple(sorted(predecessors.get(a.id, ()))),
            assignee_login_ids=tuple(sorted(logins[u] for u in assignees.get(a.id, ()))),
        )
        for a in activities
    }


def _time_entry_rows(session: Session, project_id: int) -> list[tuple[TimeEntry, TimeEntryRow]]:
    rows = session.execute(
        select(TimeEntry, User.login_id)
        .join(User, User.id == TimeEntry.user_id)
        .join(Activity, Activity.id == TimeEntry.activity_id)
        .where(Activity.project_id == project_id)
        .order_by(TimeEntry.work_date, User.login_id, TimeEntry.activity_id)
    ).tuples().all()
    return [
        (entry, TimeEntryRow(login_id=login, activity_id=entry.activity_id, work_date=entry.work_date,
                             hours=_hours(entry.hours) or Decimal(0), note=entry.note))
        for entry, login in rows
    ]


# ---------------------------------------------------------------------------
# 出力（API-INT-001）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportFile:
    content: bytes
    media_type: str
    filename: str
    rows: int


def export_data(
    session: Session,
    access: ProjectAccess,
    actor: User,
    links: ScheduleLinks,
    project_id: int,
    target: TabularTarget,
    file_format: FileFormat,
) -> ExportFile:
    """WBS・活動・実績工数を 6.3 の列で出力する（data.export。INT-01）。

    行の順：``wbs`` は WBS の並び（先行順・兄弟は表示順）、``activities`` は WBS の並び → WP の中の表示順、
    ``time-entries`` は作業日 → ログインID → 活動ID。委託先だけで読む利用者は自分の割当の範囲だけ（RULE-AUTH-001）。
    """
    table = authorize_project(session, access, actor, project_id, DATA_EXPORT)
    plan = load_plan(session, project_id)
    visible = visible_scope(session, access, actor, project_id, table, plan.elements, plan.activities)
    rows: list[WbsRow] | list[ActivityRow] | list[TimeEntryRow]
    if target is TabularTarget.WBS:
        current = _wbs_rows(session, plan.elements, _dictionaries(session, (e.id for e in plan.elements)))
        rows = [current[e.id] for e, _ in preorder_elements(plan.elements) if visible is None or e.id in visible.element_ids]
    elif target is TabularTarget.ACTIVITIES:
        rows = _export_activities(session, links, project_id, plan.elements, plan.ordered_activities(),
                                  plan.dependencies, visible)
    else:
        rows = [row for entry, row in _time_entry_rows(session, project_id)
                if visible is None or entry.activity_id in visible.activity_ids]
    grid = export_grid(target, rows)
    return ExportFile(
        content=write_file(file_format, grid, target),
        media_type=MEDIA_TYPES[file_format],
        filename=f"project-{project_id}-{target.value}.{file_format.value}",
        rows=len(rows),
    )


def _export_activities(
    session: Session, links: ScheduleLinks, project_id: int, elements: Sequence[WbsElement],
    ordered: Sequence[Activity], dependencies: Sequence[ActivityDependency], visible: VisibleScope | None,
) -> list[ActivityRow]:
    predecessors = _predecessors(dependencies)
    if visible is not None:  # 見えない活動は、行にも先行にも出さない
        ordered = [a for a in ordered if a.id in visible.activity_ids]
        predecessors = {s: {p for p in ps if p in visible.activity_ids} for s, ps in predecessors.items()}
    current = _activity_rows(session, ordered, elements, predecessors, _assignees(links, session, project_id))
    return [current[a.id] for a in ordered]


# ---------------------------------------------------------------------------
# 取込（API-INT-002）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChangeSummary:
    """取り込む（取り込んだ）1 行。``key`` は照合キー（追加を書いた後は振った WBS ID・活動ID）。"""

    row: int
    action: str
    key: str
    changed: tuple[str, ...]


@dataclass(frozen=True)
class ImportResult:
    target: TabularTarget
    file_format: FileFormat
    dry_run: bool
    written: bool
    issues: tuple[ImportIssue, ...]
    changes: tuple[ChangeSummary, ...]
    unchanged: int

    @property
    def ok(self) -> bool:
        return not self.issues


def _ordered(target: TabularTarget, issues: Iterable[ImportIssue]) -> tuple[ImportIssue, ...]:
    """行の順（同じ行は列の順。列の無い誤りが先）。RULE-INT-001 の並べ方と同じ。"""
    position = {name: i for i, name in enumerate(COLUMNS[target])}
    return tuple(sorted(issues, key=lambda i: (i.row, -1 if i.column is None else position[i.column])))


def _rejected(target: TabularTarget, file_format: FileFormat, dry_run: bool,
              issues: Iterable[ImportIssue]) -> ImportResult:
    ordered = _ordered(target, issues)
    if not dry_run:
        raise AppError(422, "IMPORT_INVALID", "取込の検査に誤りがあるため、何も書きませんでした",
                       details=[issue.to_dict() for issue in ordered])
    return ImportResult(target, file_format, dry_run, False, ordered, (), 0)


def import_data(
    session: Session,
    access: ProjectAccess,
    actor: User,
    activities: ActivitiesReader,
    links: ScheduleLinks,
    project_id: int,
    target: TabularTarget,
    file_format: FileFormat,
    data: bytes,
    *,
    dry_run: bool,
    now: datetime,
) -> ImportResult:
    """6.3 の表を取り込む（data.import と対象の書く権限。INT-01・BR-02）。全行を検査し、誤りが 1 件でもあれば何も書かない。

    ``dry_run`` は検査と書く行の一覧だけを返す（誤りがあっても 200）。``dry_run`` でなく誤りがあれば 422 ``IMPORT_INVALID``。
    WBS を変える取込は承認待ちの間は 409 ``INVALID_STATE``（RULE-WBS-006。``dry_run`` でも同じ）。
    権限（設計書 1.1 の API-INT-002・CR-006）：data.import に加えて、``wbs`` は wbs.edit、``activities`` は schedule.edit を
    初めに確かめる（無ければ 403。何も読まない）。見積の列を変える行があれば estimate.edit も確かめる（行の検査の後。``dry_run`` でも同じ）。
    """
    authorize_project(session, access, actor, project_id, DATA_IMPORT)
    write_permission = _WRITE_PERMISSIONS.get(target)
    if write_permission is not None:
        authorize_project(session, access, actor, project_id, write_permission)

    def require_estimate_edit() -> None:
        authorize_project(session, access, actor, project_id, ESTIMATE_EDIT)

    try:
        grid = read_grid(file_format, data)
    except TabularFileError as exc:
        return _rejected(target, file_format, dry_run, exc.issues)
    if target is TabularTarget.WBS:
        return _import_wbs(session, actor, activities, project_id, grid, file_format, dry_run=dry_run, now=now,
                           require_estimate_edit=require_estimate_edit)
    if target is TabularTarget.ACTIVITIES:
        return _import_activities(session, actor, links, project_id, grid, file_format, dry_run=dry_run, now=now,
                                  require_estimate_edit=require_estimate_edit)
    return _import_time_entries(session, project_id, grid, file_format, dry_run=dry_run)


# 取込の対象ごとの書く権限（設計書 1.1 の API-INT-002）。time-entries は data.import だけ（他人の行を書く権限が 9.1 に無い）
_WRITE_PERMISSIONS: Final[Mapping[TabularTarget, str]] = {
    TabularTarget.WBS: WBS_EDIT,
    TabularTarget.ACTIVITIES: SCHEDULE_EDIT,
}


def _changes_estimate(action: ChangeAction, changed: Sequence[str], value: Decimal | None, column: str) -> bool:
    """取込の行が見積の列を変えるか（追加の行は値があれば、更新の行はその列が変わっていれば。estimate.edit を要する）。"""
    if action is ChangeAction.CREATE:
        return value is not None
    return column in changed


# --- wbs -------------------------------------------------------------------


def _check_wbs_editable(status: str) -> None:
    """WBS を変える取込は RULE-WBS-006 の EDIT。承認待ちの間は 409 ``INVALID_STATE``（API-WBS-003 と同じ）。"""
    try:
        transition_wbs(status, WbsAction.EDIT)
    except WorkflowError as exc:
        raise AppError(409, exc.code.value, "承認待ちの間は WBS を変えられません（差戻しの後に取り込みます）",
                       details=[{"wbs_status": status}]) from exc


def _import_wbs(
    session: Session, actor: User, activities: ActivitiesReader, project_id: int, grid: Sequence[Sequence[object]],
    file_format: FileFormat, *, dry_run: bool, now: datetime, require_estimate_edit: Callable[[], None],
) -> ImportResult:
    target = TabularTarget.WBS
    context = load_wbs_context(session, project_id, activities)
    dictionaries = _dictionaries(session, (e.id for e in context.elements))
    current = _wbs_rows(session, context.elements, dictionaries)
    assignable = _assignable(session, project_id)
    # 今の値に現れる責任者（無効にした利用者を含む）も「いる」とみなす（往復の一致。A-34 の限界）
    known = set(assignable) | {r.responsible_login_id for r in current.values() if r.responsible_login_id is not None}
    plan = plan_wbs_import(
        grid,
        elements=[coded_element(e) for e in context.elements],
        current=current,
        login_ids=known,
        history=_history(session, project_id),
        activities=[ActivityRef(id=a.id, work_package_id=a.work_package_id) for a in context.activities],
    )
    if not plan.ok:
        return _rejected(target, file_format, dry_run, plan.issues)
    # 書く行の責任者は、置ける利用者（メンバーである有効な利用者）でなければならない（API-WBS-002・003 と同じ。WBS-10）
    column = "責任者のログインID"
    issues = [
        ImportIssue(c.line, column, ImportIssueCode.NOT_FOUND,
                    f"ログインID {c.row.responsible_login_id!r} はプロジェクトのメンバーである有効な利用者でない")
        for c in plan.changes
        if c.row.responsible_login_id is not None and c.row.responsible_login_id not in assignable
        and (c.action is ChangeAction.CREATE or column in c.changed)
    ]
    if issues:
        return _rejected(target, file_format, dry_run, issues)
    if any(_changes_estimate(c.action, c.changed, c.row.own_estimate_hours, "自分の見積（人時）") for c in plan.changes):
        require_estimate_edit()
    if plan.changes:
        _check_wbs_editable(context.project.wbs_status)
    if dry_run or not plan.changes:
        return ImportResult(target, file_format, dry_run, False, (),
                            tuple(ChangeSummary(c.line, c.action.value, c.row.wbs_code, c.changed) for c in plan.changes),
                            plan.unchanged)

    keys = _write_wbs(session, actor, context.project.id, context, dictionaries, plan.changes, assignable, now)
    mark_wbs_edited(context.project)
    session.commit()
    return ImportResult(target, file_format, dry_run, True, (),
                        tuple(ChangeSummary(c.line, c.action.value, keys[c.line], c.changed) for c in plan.changes),
                        plan.unchanged)


def _write_wbs(
    session: Session, actor: User, project_id: int, context: WbsContext, dictionaries: dict[int, WbsDictionaryEntry],
    changes: Sequence[WbsChange], assignable: Mapping[str, int], now: datetime,
) -> dict[int, str]:
    """取込の計画を書く（コミットは呼び手）。追加 → 移動（移した後の木の浅い順）→ 列の更新の順。行 → WBS ID を返す。

    検査は取り込んだ後の木に当ててある（RULE-WBS-003）。移動を浅い順に当てると、途中の木にも循環ができない。
    """
    rule = context.rule
    elements: list[WbsElement] = list(context.elements)
    by_id = {e.id: e for e in elements}
    created: dict[int, WbsElement] = {}

    def code_error(exc: WbsCodeError) -> AppError:
        return AppError(422, "WBS_CODE_INVALID", str(exc))

    def end_of(parent_id: int | None, excluding: int | None = None) -> int:
        return max((e.sort_order for e in elements if e.parent_id == parent_id and e.id != excluding), default=0) + 1

    def parent_of(change: WbsChange) -> int | None:
        if change.parent_id is not None:
            return change.parent_id
        if change.parent_line is not None:
            return created[change.parent_line].id
        return None

    # 追加（親が先に並んでいる。RULE-INT-001）
    for change in changes:
        if change.action is not ChangeAction.CREATE:
            continue
        row = change.row
        parent_id = parent_of(change)
        parent = None if parent_id is None else by_id[parent_id]
        try:
            code = new_element_code(None if parent is None else coded_element(parent), rule,
                                    top_last_child_seq=context.rule_row.top_last_child_seq if parent is None else None)
        except WbsCodeError as exc:
            raise code_error(exc) from exc
        element = WbsElement(
            project_id=project_id, parent_id=parent_id, kind=row.kind.value, name=row.name, wbs_code=code.wbs_code,
            sibling_seq=code.sibling_seq, last_child_seq=0, sort_order=end_of(parent_id),
            own_estimate_hours=row.own_estimate_hours,
            responsible_user_id=None if row.responsible_login_id is None else assignable[row.responsible_login_id],
            needs_renumber=code.needs_renumber,
        )
        if parent is None:
            context.rule_row.top_last_child_seq = code.parent_last_child_seq
        else:
            parent.last_child_seq = code.parent_last_child_seq
        session.add(element)
        session.flush()
        elements.append(element)
        by_id[element.id] = element
        created[change.line] = element
        _write_dictionary(session, element.id, row, None)
        if row.own_estimate_hours is not None:
            _add_estimate_record(session, project_id, TargetType.ELEMENT, element.id, row.own_estimate_hours, actor, now)

    updates = [c for c in changes if c.action is ChangeAction.UPDATE]
    # 移動：移した後の親で深さを求め、浅い順に当てる
    final_parent = {e.id: e.parent_id for e in elements}
    moves: dict[int, int | None] = {}
    for change in updates:
        if "親のWBS ID" in change.changed:
            assert change.element_id is not None
            moves[change.element_id] = final_parent[change.element_id] = parent_of(change)

    def depth(element_id: int) -> int:
        count, parent_id = 0, final_parent[element_id]
        while parent_id is not None:
            count, parent_id = count + 1, final_parent[parent_id]
        return count

    for element_id in sorted(moves, key=lambda i: (depth(i), i)):
        element, new_parent_id = by_id[element_id], moves[element_id]
        try:
            result = move_element(
                [coded_element(e) for e in elements], element_id, new_parent_id, rule,
                top_last_child_seq=context.rule_row.top_last_child_seq
                if new_parent_id is None and element.parent_id is not None else None,
            )
        except WbsCodeError as exc:
            raise code_error(exc) from exc
        if result.parent_last_child_seq is not None:
            if new_parent_id is None:
                context.rule_row.top_last_child_seq = result.parent_last_child_seq
            else:
                by_id[new_parent_id].last_child_seq = result.parent_last_child_seq
        element.sort_order = end_of(new_parent_id, excluding=element_id)
        element.parent_id = new_parent_id
        element.sibling_seq = result.sibling_seq
        for node_id, flag in result.needs_renumber.items():
            by_id[node_id].needs_renumber = flag

    # 列の更新
    for change in updates:
        assert change.element_id is not None
        element, row, changed = by_id[change.element_id], change.row, set(change.changed)
        if "区分" in changed:
            element.kind = row.kind.value
        if "名称" in changed:
            element.name = row.name
        if "責任者のログインID" in changed:
            element.responsible_user_id = (None if row.responsible_login_id is None
                                           else assignable[row.responsible_login_id])
        if "自分の見積（人時）" in changed:
            element.own_estimate_hours = row.own_estimate_hours
            if row.own_estimate_hours is not None:
                _add_estimate_record(session, project_id, TargetType.ELEMENT, element.id, row.own_estimate_hours,
                                     actor, now)
        if changed & set(_DICTIONARY_COLUMNS):
            _write_dictionary(session, element.id, row, dictionaries.get(element.id))

    keys = {line: e.wbs_code for line, e in created.items()}
    keys.update({c.line: c.row.wbs_code for c in updates})
    return keys


def _write_dictionary(session: Session, element_id: int, row: WbsRow, entry: WbsDictionaryEntry | None) -> None:
    """WBS 辞書の 4 項目をファイルの値で置き換える（API-WBS-010 と同じ）。全て空で行も無ければ行を作らない。"""
    values = {"deliverable": row.deliverable, "work_scope": row.work_scope,
              "completion_criteria": row.completion_criteria, "assumptions": row.assumptions}
    if entry is None:
        if all(v is None for v in values.values()):
            return
        entry = WbsDictionaryEntry(element_id=element_id)
        session.add(entry)
    for name, value in values.items():
        setattr(entry, name, value)


# --- activities ------------------------------------------------------------


def _import_activities(
    session: Session, actor: User, links: ScheduleLinks, project_id: int, grid: Sequence[Sequence[object]],
    file_format: FileFormat, *, dry_run: bool, now: datetime, require_estimate_edit: Callable[[], None],
) -> ImportResult:
    target = TabularTarget.ACTIVITIES
    plan = load_plan(session, project_id)
    predecessors = _predecessors(plan.dependencies)
    assignees = _assignees(links, session, project_id)
    current = _activity_rows(session, plan.activities, plan.elements, predecessors, assignees)
    assignable = _assignable(session, project_id)
    # 今の担当（無効にした利用者を含む）のログインID → ID（往復の一致と、残す担当の置き換えに使う）
    login_ids = {login: user_id for user_id, login in
                 _logins_of(session, (u for users in assignees.values() for u in users)).items()}
    login_ids.update(assignable)
    result = plan_activity_import(
        grid,
        elements=[coded_element(e) for e in plan.elements],
        work_package_ids=[e.id for e in plan.elements if e.kind == WORK_PACKAGE],
        current=current,
        login_ids=login_ids,
        history=_history(session, project_id),
    )
    if not result.ok:
        return _rejected(target, file_format, dry_run, result.issues)
    # 新しく置く担当は、置ける利用者（メンバーである有効な利用者）でなければならない（API-EFF-001・API-SCH-004 と同じ）
    column = ACTIVITY_COLUMNS[8]
    issues: list[ImportIssue] = []
    for change in result.changes:
        before = set() if change.activity_id is None else set(current[change.activity_id].assignee_login_ids)
        for login in sorted(set(change.row.assignee_login_ids) - before):
            if login not in assignable:
                issues.append(ImportIssue(change.line, column, ImportIssueCode.NOT_FOUND,
                                          f"ログインID {login!r} はプロジェクトのメンバーである有効な利用者でない"))
    if issues:
        return _rejected(target, file_format, dry_run, issues)
    if any(_changes_estimate(c.action, c.changed, c.row.estimate_hours, "見積（人時）") for c in result.changes):
        require_estimate_edit()

    def key(change: ActivityChange) -> str:
        return "" if change.activity_id is None else str(change.activity_id)

    if dry_run or not result.changes:
        return ImportResult(target, file_format, dry_run, False, (),
                            tuple(ChangeSummary(c.line, c.action.value, key(c), c.changed) for c in result.changes),
                            result.unchanged)

    keys = _write_activities(session, actor, links, project_id, plan, result.changes, login_ids, now)
    calculate_and_store(session, project_id)  # 日程を計算し直す（RULE-SCH-002・003。循環は検査で弾いてある）
    session.commit()
    return ImportResult(target, file_format, dry_run, True, (),
                        tuple(ChangeSummary(c.line, c.action.value, keys[c.line], c.changed) for c in result.changes),
                        result.unchanged)


def _write_activities(
    session: Session, actor: User, links: ScheduleLinks, project_id: int, plan: Plan,
    changes: Sequence[ActivityChange], login_ids: Mapping[str, int], now: datetime,
) -> dict[int, str]:
    """取込の計画を書く（コミット・日程の再計算は呼び手）。行 → 活動ID を返す。"""
    activities: list[Activity] = list(plan.activities)
    by_id = {a.id: a for a in activities}
    dependencies: dict[int, list[ActivityDependency]] = {}
    for d in plan.dependencies:
        dependencies.setdefault(d.successor_id, []).append(d)
    keys: dict[int, str] = {}

    def end_of(work_package_id: int, excluding: int | None = None) -> int:
        return max((a.sort_order for a in activities if a.work_package_id == work_package_id and a.id != excluding),
                   default=0) + 1

    for change in changes:
        row = change.row
        start, duration = resolve_activity_dates(plan.calendar, start=row.requested_start, finish=None,
                                                 duration=row.duration_days, is_milestone=row.is_milestone)
        if change.action is ChangeAction.CREATE:
            activity = Activity(
                project_id=project_id, work_package_id=change.work_package_id, name=row.name,
                sort_order=end_of(change.work_package_id), requested_start=start, duration_days=duration,
                is_milestone=duration == 0, estimate_hours=row.estimate_hours,
            )
            session.add(activity)
            session.flush()
            activities.append(activity)
            for predecessor_id in row.predecessor_ids:
                session.add(ActivityDependency(project_id=project_id, predecessor_id=predecessor_id,
                                               successor_id=activity.id))
            if row.assignee_login_ids:
                links.assignments.replace_assignees(session, activity.id,
                                                    frozenset(login_ids[x] for x in row.assignee_login_ids))
            if row.estimate_hours is not None:
                _add_estimate_record(session, project_id, TargetType.ACTIVITY, activity.id, row.estimate_hours,
                                     actor, now)
            keys[change.line] = str(activity.id)
            continue

        assert change.activity_id is not None
        activity, changed = by_id[change.activity_id], set(change.changed)
        if "WPのWBS ID" in changed:
            activity.sort_order = end_of(change.work_package_id, excluding=activity.id)
            activity.work_package_id = change.work_package_id
        if "名称" in changed:
            activity.name = row.name
        if changed & set(_SCHEDULE_COLUMNS):
            activity.requested_start = start
            activity.duration_days = duration
            activity.is_milestone = duration == 0
        if "見積（人時）" in changed:
            activity.estimate_hours = row.estimate_hours
            if row.estimate_hours is not None:
                _add_estimate_record(session, project_id, TargetType.ACTIVITY, activity.id, row.estimate_hours,
                                     actor, now)
        if ACTIVITY_COLUMNS[7] in changed:  # 先行：ファイルの値がその活動の先行の全て
            wanted = set(row.predecessor_ids)
            kept: set[int] = set()
            for dependency in dependencies.get(activity.id, []):
                if dependency.predecessor_id in wanted:
                    kept.add(dependency.predecessor_id)
                else:
                    session.delete(dependency)
            for predecessor_id in sorted(wanted - kept):
                session.add(ActivityDependency(project_id=project_id, predecessor_id=predecessor_id,
                                               successor_id=activity.id))
        if ACTIVITY_COLUMNS[8] in changed:
            links.assignments.replace_assignees(session, activity.id,
                                                frozenset(login_ids[x] for x in row.assignee_login_ids))
        keys[change.line] = str(activity.id)
    return keys


# --- time-entries ----------------------------------------------------------


def _file_logins(grid: Sequence[Sequence[object]]) -> set[str]:
    """表の 1 列目（ログインID）に現れる値。見出しの検査の前なので、読めない値は飛ばす（検査はドメインが行う）。"""
    logins: set[str] = set()
    for record in grid[1:]:
        if record:
            try:
                value = cell_text(record[0]).strip()
            except ValueError:
                continue
            if value:
                logins.add(value)
    return logins


def _import_time_entries(
    session: Session, project_id: int, grid: Sequence[Sequence[object]], file_format: FileFormat, *, dry_run: bool,
) -> ImportResult:
    target = TabularTarget.TIME_ENTRIES
    current = _time_entry_rows(session, project_id)
    activity_ids = list(session.scalars(select(Activity.id).where(Activity.project_id == project_id)))
    users = list(session.scalars(select(User).where(User.login_id.in_(sorted(_file_logins(grid))))))
    contexts: dict[str, UserEffortContext] = {}
    for user in users:
        existing = tuple(
            TimeEntryValue(user_id=e.user_id, activity_id=e.activity_id, work_date=e.work_date,
                           hours=_hours(e.hours) or Decimal(0))
            for e in session.scalars(select(TimeEntry).where(TimeEntry.user_id == user.id))
        )
        assigned = frozenset(session.scalars(
            select(Assignment.activity_id).join(Activity, Activity.id == Assignment.activity_id)
            .where(Activity.project_id == project_id, Assignment.user_id == user.id)
        ))
        contexts[user.login_id] = UserEffortContext(user_id=user.id, existing=existing, assigned_activity_ids=assigned)
    finalized = list(session.scalars(
        select(StatusReport.report_date)
        .where(StatusReport.project_id == project_id, StatusReport.status == STATUS_FINALIZED)
    ))
    plan = plan_time_entry_import(grid, current=[row for _, row in current], activity_ids=activity_ids,
                                  users=contexts, finalized_report_dates=finalized)
    if not plan.ok:
        return _rejected(target, file_format, dry_run, plan.issues)
    # 0 時間の実績は行を持たない（API-EFF-003 と同じ）ので、備考を書けない
    issues = [
        ImportIssue(c.line, TIME_ENTRY_COLUMNS[4], ImportIssueCode.INVALID_VALUE,
                    "0 時間の行には備考を書けない（0 時間の実績は行を持たない）")
        for c in plan.changes if c.row.hours == 0 and c.row.note is not None
    ]
    if issues:
        return _rejected(target, file_format, dry_run, issues)

    def key(change: TimeEntryImportChange) -> str:
        r = change.row
        return f"{r.login_id}/{r.activity_id}/{r.work_date.isoformat()}"

    summaries = tuple(ChangeSummary(c.line, c.action.value, key(c), c.changed) for c in plan.changes)
    if dry_run or not plan.changes:
        return ImportResult(target, file_format, dry_run, False, (), summaries, plan.unchanged)

    rows = {(e.user_id, e.activity_id, e.work_date): e for e, _ in current}
    for change in plan.changes:
        r = change.row
        user_id = contexts[r.login_id].user_id
        entry = rows.get((user_id, r.activity_id, r.work_date))
        if entry is None:
            if r.hours > 0:
                session.add(TimeEntry(user_id=user_id, activity_id=r.activity_id, work_date=r.work_date, hours=r.hours,
                                      note=r.note))
        elif r.hours == 0:
            session.delete(entry)
        else:
            entry.hours = r.hours
            entry.note = r.note
    session.commit()
    return ImportResult(target, file_format, dry_run, True, (), summaries, plan.unchanged)

