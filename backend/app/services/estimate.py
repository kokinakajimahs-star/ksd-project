"""見積（EST）のユースケース（設計書 6.2 の API-EST-001〜003、4.1・5.8、9 章。要求仕様書 EST-01・02・08）。

- 権限（RULE-AUTH-001）：見積を書くは estimate.edit、履歴・集計を読むは project.view。
  委託先だけで読む利用者は、自分の割当の活動とその WP から最上位までの経路の要素に絞る（9.2 の注記。``own_assignment_scope``）
- 見積を書く（API-EST-001）：RULE-EST-002（``record_estimate``）で人時へ換算し、記録（``estimate_records``）を 1 行足し、
  対象の今の見積（WBS要素は ``wbs_elements.own_estimate_hours``、活動は ``activities.estimate_hours``）を同じ値にする。
  人日の換算はプロジェクトの稼働カレンダーの 1 日の稼働時間（``calendars.hours_per_day``。行が無ければ 4.1 の既定）。
  WBS要素の見積を書くのは API-WBS-003 と同じ「WBS を変えた」（RULE-WBS-006 の EDIT）：承認待ちの間は 409 ``INVALID_STATE``、
  基準化の後なら DRAFT へ戻す。活動の見積は WBS の状態を変えない（活動はスケジュールの側。API-SCH-003 と同じ）
- 見積の履歴（API-EST-002）：対象の記録を古い順（``entered_at``・``id``）に返す。対象を消しても記録は残る（A-32）ので、
  対象が無くても記録があれば返す。対象も記録も無ければ 404
- ボトムアップの集計（API-EST-003）：RULE-EST-001（``rollup_estimates``）の結果を、要素は先行順（兄弟は表示順。API-WBS-001 と同じ）、
  活動は WP の中の表示順で返す

書き込みは ORM を通すので変更履歴（``audit_logs``）に残る（6.1。infra のイベント。A-37）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.authz import ViewLevel, VisibleScope, own_assignment_scope, project_view_level
from app.domain.estimate import (
    ActivityEstimate,
    EstimateError,
    EstimateRollup,
    TargetType,
    WbsNode,
    record_estimate,
    rollup_estimates,
)
from app.errors import AppError
from app.models.cmn import User
from app.models.est import EstimateRecord
from app.models.prj import HOURS_PER_DAY_DEFAULT, Calendar, Project
from app.models.sch import Activity
from app.models.wbs import WbsElement
from app.services.auth import authorize_project, principal_of
from app.services.project_access import ProjectAccess
from app.services.wbs import mark_wbs_edited, preorder_elements

ESTIMATE_EDIT = "estimate.edit"
PROJECT_VIEW = "project.view"


# ---------------------------------------------------------------------------
# 共通の部品
# ---------------------------------------------------------------------------


def _target_not_found(target_type: TargetType, target_id: int) -> AppError:
    what = "WBS要素" if target_type is TargetType.ELEMENT else "活動"
    return AppError(404, "NOT_FOUND", f"見積の対象の{what}が見つかりません",
                    details=[{"target_type": target_type.value, "target_id": target_id}])


def _target_of(
    session: Session, project_id: int, target_type: TargetType, target_id: int
) -> WbsElement | Activity | None:
    """見積の対象（そのプロジェクトの WBS要素・活動）。無ければ ``None``。"""
    row: WbsElement | Activity | None
    if target_type is TargetType.ELEMENT:
        row = session.get(WbsElement, target_id)
    else:
        row = session.get(Activity, target_id)
    if row is None or row.project_id != project_id:
        return None
    return row


def _current_hours(target: WbsElement | Activity) -> Decimal | None:
    return target.own_estimate_hours if isinstance(target, WbsElement) else target.estimate_hours


def project_hours_per_day(session: Session, project_id: int) -> Decimal:
    """人日→人時の換算に使う 1 日の稼働時間（PRJ-04）。カレンダーの行が無いプロジェクトは 4.1 の既定。"""
    value = session.scalar(select(Calendar.hours_per_day).where(Calendar.project_id == project_id))
    return Decimal(HOURS_PER_DAY_DEFAULT if value is None else value)


def _elements(session: Session, project_id: int) -> list[WbsElement]:
    return list(session.scalars(select(WbsElement).where(WbsElement.project_id == project_id).order_by(WbsElement.id)))


def _activities(session: Session, project_id: int) -> list[Activity]:
    return list(session.scalars(select(Activity).where(Activity.project_id == project_id).order_by(Activity.id)))


def _rollup(project_id: int, elements: Sequence[WbsElement], activities: Sequence[Activity]) -> EstimateRollup:
    """RULE-EST-001。保存した木が壊れている（WP の子など）なら推し量らずに止める（API-WBS-001 と同じ）。"""
    try:
        return rollup_estimates(
            [WbsNode(id=e.id, parent_id=e.parent_id, kind=e.kind, own_estimate_hours=e.own_estimate_hours)  # type: ignore[arg-type]
             for e in elements],
            [ActivityEstimate(id=a.id, work_package_id=a.work_package_id, estimate_hours=a.estimate_hours)
             for a in activities],
        )
    except EstimateError as exc:
        raise RuntimeError(f"プロジェクト {project_id} の見積を集計できない: {exc}") from exc


def visible_scope(
    session: Session,
    access: ProjectAccess,
    actor: User,
    project_id: int,
    table: Mapping[str, frozenset[str]],
    elements: Sequence[WbsElement],
    activities: Sequence[Activity],
) -> VisibleScope | None:
    """project.view で全て読めるなら ``None``。委託先だけで読むなら見せる範囲（RULE-AUTH-001・9.2 の注記）。"""
    membership = access.membership(session, project_id, actor.id)
    if project_view_level(principal_of(actor), role_permissions=table, membership=membership) is ViewLevel.ALL:
        return None
    assigned = membership.assigned_activity_ids if membership is not None else frozenset()
    activity_elements = {a.id: a.work_package_id for a in activities}
    return own_assignment_scope(
        [a for a in assigned if a in activity_elements],
        activity_elements=activity_elements,
        element_parents={e.id: e.parent_id for e in elements},
    )


def _display_names(session: Session, user_ids: Sequence[int]) -> dict[int, str]:
    ids = sorted(set(user_ids))
    if not ids:
        return {}
    return {u.id: u.display_name for u in session.scalars(select(User).where(User.id.in_(ids)))}


@dataclass(frozen=True)
class RecordView:
    """見積の記録と、書いた利用者の表示名（利用者が無ければ ``None``）。"""

    record: EstimateRecord
    entered_by_name: str | None


# ---------------------------------------------------------------------------
# 見積を書く（API-EST-001）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WrittenView:
    record: RecordView
    current_hours: Decimal
    wbs_status: str
    project_hours: Decimal


def write_estimate(
    session: Session,
    access: ProjectAccess,
    actor: User,
    project_id: int,
    target_type: TargetType,
    target_id: int,
    *,
    input_value: Decimal,
    input_unit: str,
    method: str | None,
    assumptions: str | None,
    references: str | None,
    now: datetime,
) -> WrittenView:
    """見積を書く（estimate.edit。EST-01・EST-08）。

    対象がそのプロジェクトに無ければ 404。WBS要素の見積は承認待ちの間は 409（RULE-WBS-006）。
    換算・値の検査は RULE-EST-002（負の値・小数 3 桁以降・換算後の上限超えは 400）。記録を足し、対象の今の見積を同じ値にする。
    """
    authorize_project(session, access, actor, project_id, ESTIMATE_EDIT)
    project = session.get(Project, project_id)
    if project is None:  # authorize_project が先に 404 を返すので、ここへは来ない
        raise AppError(404, "NOT_FOUND", "プロジェクトが見つかりません", details=[{"project_id": project_id}])
    target = _target_of(session, project_id, target_type, target_id)
    if target is None:
        raise _target_not_found(target_type, target_id)
    try:
        record = record_estimate(
            project_id=project_id,
            target_type=target_type,
            target_id=target_id,
            input_value=input_value,
            input_unit=input_unit,
            hours_per_day=project_hours_per_day(session, project_id),
            entered_by=actor.id,
            entered_at=now,
            method=method,
            assumptions=assumptions,
            references=references,
        )
    except EstimateError as exc:
        raise AppError(400, "VALIDATION_ERROR", "見積の値が正しくありません",
                       details=[{"loc": ["body", "input_value"], "message": str(exc)}]) from exc
    if isinstance(target, WbsElement):
        mark_wbs_edited(project)
        target.own_estimate_hours = record.hours
    else:
        target.estimate_hours = record.hours
    row = EstimateRecord(
        project_id=record.project_id,
        target_type=record.target_type.value,
        target_id=record.target_id,
        hours=record.hours,
        input_value=record.input_value,
        input_unit=record.input_unit.value,
        method=None if record.method is None else record.method.value,
        assumptions=record.assumptions,
        references=record.references,
        entered_by=record.entered_by,
        entered_at=record.entered_at,
    )
    session.add(row)
    session.commit()
    rollup = _rollup(project_id, _elements(session, project_id), _activities(session, project_id))
    return WrittenView(
        record=RecordView(row, actor.display_name),
        current_hours=record.hours,
        wbs_status=project.wbs_status,
        project_hours=rollup.project_hours,
    )


# ---------------------------------------------------------------------------
# 見積の履歴（API-EST-002）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoryView:
    target_exists: bool
    current_hours: Decimal | None
    items: list[RecordView]
    total: int


def estimate_history(
    session: Session,
    access: ProjectAccess,
    actor: User,
    project_id: int,
    target_type: TargetType,
    target_id: int,
    *,
    limit: int,
    offset: int,
) -> HistoryView:
    """対象の見積の記録を古い順に返す（project.view。EST-08 の「見積値を変えた履歴が残る」）。

    委託先だけで読む利用者は、見える要素・自分の割当の活動の分だけ（見えなければ 404）。
    対象が無くても記録があれば返す（対象を消しても記録は残る。A-32）。対象も記録も無ければ 404。
    """
    table = authorize_project(session, access, actor, project_id, PROJECT_VIEW)
    target = _target_of(session, project_id, target_type, target_id)
    elements = _elements(session, project_id)
    activities = _activities(session, project_id)
    visible = visible_scope(session, access, actor, project_id, table, elements, activities)
    if visible is not None:
        allowed = visible.element_ids if target_type is TargetType.ELEMENT else visible.activity_ids
        if target is None or target_id not in allowed:
            raise _target_not_found(target_type, target_id)
    where = (
        EstimateRecord.project_id == project_id,
        EstimateRecord.target_type == target_type.value,
        EstimateRecord.target_id == target_id,
    )
    total = session.scalar(select(func.count()).select_from(EstimateRecord).where(*where)) or 0
    if target is None and total == 0:
        raise _target_not_found(target_type, target_id)
    rows = list(session.scalars(
        select(EstimateRecord).where(*where).order_by(EstimateRecord.entered_at, EstimateRecord.id)
        .limit(limit).offset(offset)
    ))
    names = _display_names(session, [r.entered_by for r in rows])
    return HistoryView(
        target_exists=target is not None,
        current_hours=None if target is None else _current_hours(target),
        items=[RecordView(r, names.get(r.entered_by)) for r in rows],
        total=total,
    )


# ---------------------------------------------------------------------------
# ボトムアップの集計（API-EST-003）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RollupView:
    rollup: EstimateRollup
    elements: list[tuple[WbsElement, int]]  # 先行順（兄弟は表示順）と深さ（最上位が 1）
    activities: list[Activity]  # WP の先行順 → WP の中の表示順
    project_hours: Decimal
    restricted: bool


def estimate_rollup(session: Session, access: ProjectAccess, actor: User, project_id: int) -> RollupView:
    """活動→WP→上位→プロジェクトへの見積の集計（project.view。RULE-EST-001・EST-02）。

    委託先だけで読む利用者は、見える要素・自分の割当の活動だけを返し（``restricted`` が真）、集計値も見える要素・活動だけから
    求める（9.2 の注記。API-WBS-001 の ``rollup_hours``・``project_estimate_hours`` と同じ求め方）。
    """
    table = authorize_project(session, access, actor, project_id, PROJECT_VIEW)
    elements = _elements(session, project_id)
    activities = _activities(session, project_id)
    visible = visible_scope(session, access, actor, project_id, table, elements, activities)
    if visible is not None:
        elements = [e for e in elements if e.id in visible.element_ids]
        activities = [a for a in activities if a.id in visible.activity_ids]
    rollup = _rollup(project_id, elements, activities)
    rows = preorder_elements(elements)
    order = {e.id: index for index, (e, _) in enumerate(rows)}
    ordered = sorted((a for a in activities if a.work_package_id in order),
                     key=lambda a: (order[a.work_package_id], a.sort_order, a.id))
    return RollupView(rollup=rollup, elements=rows, activities=ordered, project_hours=rollup.project_hours,
                      restricted=visible is not None)


def record_fields(view: RecordView) -> dict[str, Any]:
    """記録 1 件の応答の項目（API 層が ``EstimateRecordOut`` にする）。工数は数（float）で返す。"""
    r = view.record
    return {
        "id": r.id,
        "project_id": r.project_id,
        "target_type": r.target_type,
        "target_id": r.target_id,
        "hours": float(r.hours),
        "input_value": float(r.input_value),
        "input_unit": r.input_unit,
        "method": r.method,
        "assumptions": r.assumptions,
        "references": r.references,
        "entered_by": r.entered_by,
        "entered_by_name": view.entered_by_name,
        "entered_at": r.entered_at,
    }
