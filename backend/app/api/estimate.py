"""見積の API（設計書 6.2 の API-EST-001〜003）：見積を書く・見積の履歴・ボトムアップの集計。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_project_access
from app.domain.estimate import TargetType
from app.schemas.estimate import (
    EstimateHistoryOut,
    EstimatePut,
    EstimateRecordOut,
    EstimateRollupOut,
    EstimateWrittenOut,
    RollupActivityOut,
    RollupElementOut,
)
from app.services import estimate as estimate_service
from app.services import get_session
from app.services.auth import User
from app.services.clock import get_now
from app.services.project_access import ProjectAccess

router = APIRouter(tags=["estimate"])


def _num(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


@router.put("/projects/{project_id}/estimates/{target_type}/{target_id}", response_model=EstimateWrittenOut)
def put_estimate(
    project_id: int,
    target_type: TargetType,
    target_id: int,
    body: EstimatePut,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    now: datetime = Depends(get_now),
) -> EstimateWrittenOut:
    """API-EST-001 見積を書く（単位・手法・前提・参照資料）。

    権限は estimate.edit。``target_type`` は ELEMENT（WBS要素）か ACTIVITY（活動）。人日（DAY）は 1 日の稼働時間で人時へ換算する
    （RULE-EST-002）。記録（``estimate_records``）を 1 行足し、対象の今の見積を同じ値にする（EST-01・EST-08）。
    対象が無ければ 404、負の値・小数 3 桁以降は 400、WBS要素の見積は承認待ちの間は 409 ``INVALID_STATE``（RULE-WBS-006）。
    """
    view = estimate_service.write_estimate(
        session, access, user, project_id, target_type, target_id,
        input_value=body.input_value, input_unit=body.input_unit, method=body.method,
        assumptions=body.assumptions, references=body.references, now=now,
    )
    return EstimateWrittenOut(
        record=EstimateRecordOut(**estimate_service.record_fields(view.record)),
        current_hours=float(view.current_hours),
        wbs_status=view.wbs_status,
        project_estimate_hours=float(view.project_hours),
    )


@router.get("/projects/{project_id}/estimates/{target_type}/{target_id}/history", response_model=EstimateHistoryOut)
def get_estimate_history(
    project_id: int,
    target_type: TargetType,
    target_id: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> EstimateHistoryOut:
    """API-EST-002 見積の履歴。

    権限は project.view（委託先は見える要素・自分の割当の活動だけ。見えなければ 404）。記録を古い順（``entered_at``・``id``）に返す。
    対象を消しても記録は残るので、対象が無くても記録があれば返す（``target_exists`` が偽）。対象も記録も無ければ 404。
    """
    view = estimate_service.estimate_history(
        session, access, user, project_id, target_type, target_id, limit=limit, offset=offset
    )
    return EstimateHistoryOut(
        target_type=target_type.value,
        target_id=target_id,
        target_exists=view.target_exists,
        current_hours=_num(view.current_hours),
        items=[EstimateRecordOut(**estimate_service.record_fields(r)) for r in view.items],
        total=view.total,
        limit=limit,
        offset=offset,
    )


@router.get("/projects/{project_id}/estimates/rollup", response_model=EstimateRollupOut)
def get_estimate_rollup(
    project_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> EstimateRollupOut:
    """API-EST-003 ボトムアップの集計。

    権限は project.view（委託先は自分の割当の経路だけ。``restricted`` が真。集計値も見える要素・活動だけから求める）。
    RULE-EST-001：活動→WP→上位→プロジェクトへ合計し、活動の無い WP は自分の見積を使う（5.8）。
    要素は先行順（兄弟は表示順。API-WBS-001 と同じ）、活動は WP の中の表示順。
    """
    view = estimate_service.estimate_rollup(session, access, user, project_id)
    elements = []
    for element, depth in view.elements:
        result = view.rollup.elements[element.id]
        elements.append(RollupElementOut(
            element_id=element.id, parent_id=element.parent_id, wbs_code=element.wbs_code, name=element.name,
            kind=element.kind, depth=depth, hours=float(result.hours), source=result.source.value,
            lower_hours=_num(result.lower_hours), own_estimate_hours=_num(result.own_estimate_hours),
        ))
    activities = [
        RollupActivityOut(activity_id=a.id, work_package_id=a.work_package_id, name=a.name,
                          hours=float(view.rollup.activities[a.id]), estimate_hours=_num(a.estimate_hours))
        for a in view.activities
    ]
    return EstimateRollupOut(project_id=project_id, project_estimate_hours=float(view.project_hours),
                             restricted=view.restricted, elements=elements, activities=activities)
