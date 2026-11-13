"""表形式の入出力の API（設計書 6.2 の API-INT-001・002、6.3）：Excel（xlsx）・CSV の出力と取込。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Body, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_project_access
from app.api.schedule import get_schedule_links
from app.api.wbs import get_wbs_activities
from app.domain.tabular import ChangeAction, TabularTarget
from app.schemas.io import ImportChangeOut, ImportIssueOut, ImportResultOut
from app.services import get_session
from app.services import io as io_service
from app.services.auth import User
from app.services.clock import get_now
from app.services.io import FileFormat
from app.services.project_access import ProjectAccess
from app.services.schedule import ScheduleLinks
from app.services.wbs import ActivitiesReader

router = APIRouter(tags=["io"])


@router.get(
    "/projects/{project_id}/export",
    response_class=Response,
    responses={200: {"content": {media: {} for media in io_service.MEDIA_TYPES.values()}}},
)
def export_file(
    project_id: int,
    target: TabularTarget = Query(...),
    file_format: FileFormat = Query(FileFormat.CSV, alias="format"),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    links: ScheduleLinks = Depends(get_schedule_links),
) -> Response:
    """API-INT-001 出力する（`target`＝wbs/activities/time-entries、`format`＝csv/xlsx）。

    権限は data.export（``time-entries`` は effort.view も。無ければ 403。CR-015）。6.3 の列（この順）で、1 行目が見出し。CSV は UTF-8（BOM 付き）・カンマ区切り、xlsx は 1 枚目のシートに
    同じ表を置く。委託先だけで読む利用者は自分の割当の範囲だけ（RULE-AUTH-001）。``target``・``format`` の誤りは 400。
    """
    exported = io_service.export_data(session, access, user, links, project_id, target, file_format)
    return Response(
        content=exported.content,
        media_type=exported.media_type,
        headers={"Content-Disposition": f'attachment; filename="{exported.filename}"'},
    )


@router.post("/projects/{project_id}/import", response_model=ImportResultOut)
def import_file(
    project_id: int,
    target: TabularTarget = Query(...),
    file_format: FileFormat = Query(FileFormat.CSV, alias="format"),
    dry_run: bool = Query(False),
    content: bytes = Body(b"", media_type="application/octet-stream"),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
    links: ScheduleLinks = Depends(get_schedule_links),
    now: datetime = Depends(get_now),
) -> ImportResultOut:
    """API-INT-002 取り込む（`target`・`format`・`dry_run`。検査が全て通ったときだけ書く）。

    権限は data.import。要求の本体がファイルそのもの（``Content-Type`` は ``text/csv``・xlsx の型・``application/octet-stream``）。
    全行を先に検査し（RULE-INT-001）、誤りは ``{row, column, code, message}`` の配列。``dry_run=true`` は検査と書く行の一覧だけを返す
    （誤りがあっても 200 で ``ok`` が偽）。``dry_run`` でなく誤りがあれば何も書かずに 422 ``IMPORT_INVALID``（``details`` が誤りの配列）。
    WBS を変える取込は承認待ちの間は 409 ``INVALID_STATE``（RULE-WBS-006）。
    """
    result = io_service.import_data(
        session, access, user, activities, links, project_id, target, file_format, content, dry_run=dry_run, now=now
    )
    return ImportResultOut(
        target=result.target.value,
        format=result.file_format.value,
        dry_run=result.dry_run,
        ok=result.ok,
        written=result.written,
        created=sum(1 for c in result.changes if c.action == ChangeAction.CREATE.value),
        updated=sum(1 for c in result.changes if c.action == ChangeAction.UPDATE.value),
        unchanged=result.unchanged,
        changes=[ImportChangeOut(row=c.row, action=c.action, key=c.key, changed=list(c.changed)) for c in result.changes],
        issues=[ImportIssueOut(**issue.to_dict()) for issue in result.issues],  # type: ignore[arg-type]
    )
