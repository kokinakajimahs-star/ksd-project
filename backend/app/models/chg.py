"""変更管理（CHG）の群のテーブル（設計書 3 章・4.1・4.2・5.9・5.10、要求仕様書 CHG-01〜04・06・09、NFR-05）：
変更要求、是正措置、変更履歴。

- 主キーは整数の ``id``。WBS要素・活動は内部キー（``wbs_elements.id``・``activities.id``）で指す（3 章。改番しても紐付けは切れない）。
  ただし変更要求の対象（``target_codes``）は**起票した時点の WBS ID の並び**（CHG-01。改番の後は新旧対応で解決する。RULE-WBS-008）
- 外部キーに ``ON DELETE`` を付けない（docs/decisions/ASSUMPTIONS.md A-11・A-15・A-19・A-22・A-27 と同じ）
- 値の一覧が設計書に書かれている列（変更要求の状態は 5.10、是正措置の状態は 4.1・5.10、変更履歴の操作は 4.1）は DB の検査（CHECK）でも拒む
- 時刻は UTC で保存する（2.4。``UTCDateTime``）
- 全ての表の内部キーは再利用しない（設計書 1.2 の 4.2。SQLite は ``AUTOINCREMENT``。外部キーの無い ``entity_id`` が
  後から作った別の行を指さないため。CR-012）
- JSON の列の ``None`` は SQL の NULL で持つ（``none_as_null``。JSON の ``null`` を保存しない。ASSUMPTIONS.md A-49 の 5）。
  NOT NULL の列の ``None`` は、ORM の挿入なら既定値（``default``）になり、既定値の無い列・更新・SQL の挿入では DB が拒む

この群の表ができたので、先に作った表の列へ外部キーを張った（ASSUMPTIONS.md A-15・A-19・A-27 の申し送り）：
``scope_baselines.change_request_id``・``schedule_baselines.change_request_id`` → ``change_requests.id``、
``variance_notes.corrective_action_id`` → ``corrective_actions.id``。

``audit_logs`` は追記だけを行う（4.2・NFR-05）。``entity_id`` は対象の種類（``entity_type``）で指す表が変わるので外部キーを持たない
（``approval_requests.target_id`` と同じ。対象の行が消えた後も履歴は残る）。ORM からの更新・削除はこのモジュールのイベントで止める。
記録する仕組み（infra の ``before_flush`` のイベントと RULE-CHG-006）は、このモジュールの担当ではない。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.infra.db import Base
from app.models.types import UTCDateTime, in_list, utc_now

# 変更要求の状態（5.10。RULE-CHG-003）。起票は DRAFT
CHANGE_REQUEST_STATUSES = ("DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "APPLIED")
CHANGE_REQUEST_STATUS_INITIAL = "DRAFT"

# 是正措置の状態（4.1・5.10。RULE-CHG-005）。登録は NOT_STARTED
CORRECTIVE_ACTION_STATUSES = ("NOT_STARTED", "IN_PROGRESS", "DONE")
CORRECTIVE_ACTION_STATUS_INITIAL = "NOT_STARTED"

# 変更履歴の操作（4.1）
AUDIT_ACTIONS = ("CREATE", "UPDATE", "DELETE")


# DB-CHG-001
class ChangeRequest(Base):
    """変更要求（要求 CHG-01〜04。5.9・5.10）。番号はプロジェクトの中の連番（1 から。``project_id``＋``number`` で一意）。

    - ``target_codes`` は対象の WBS ID の JSON 配列（複数可。CHG-01）。起票した時点の ID で持つ
    - ``operations`` は変更内容（5.9 の操作の JSON 配列。順に当てる）。形の検査は RULE-CHG-001 の担当（DB は配列であることも見ない）
    - ``status`` は 5.10 の DRAFT → SUBMITTED → APPROVED／REJECTED → APPLIED（遷移は RULE-CHG-003 の担当）
    - ``impact`` は提出のときに保存する影響分析の結果（5.9 の形。RULE-CHG-002）。提出の前は空
    - ``submitted_at`` は提出した時刻、``applied_by``・``applied_at`` は反映（再基準化。CHG-04）した人と時刻。それまでは空
    - 状態と時刻・人の組は DB の検査でも拒む（5.10 の遷移から。``status_reports`` の ``finalized_pair`` と同じ形。A-49 の 6）：
      **DRAFT は提出の時刻が空（取り下げで空に戻す）、SUBMITTED 以降は提出の時刻がある**（``submitted_at``）。
      **APPLIED だけが反映した人・時刻の両方を持ち、ほかの状態では両方とも空**（``applied_pair``）。
      承認・却下した人と時刻は承認申請（``approval_requests``）が持つ
    """

    __tablename__ = "change_requests"
    __table_args__ = (
        UniqueConstraint("project_id", "number"),
        CheckConstraint("number >= 1", name="number"),
        CheckConstraint(in_list("status", CHANGE_REQUEST_STATUSES), name="status"),
        CheckConstraint(
            "(status = 'DRAFT' AND submitted_at IS NULL)"
            " OR (status IN ('SUBMITTED', 'APPROVED', 'REJECTED', 'APPLIED') AND submitted_at IS NOT NULL)",
            name="submitted_at",
        ),
        CheckConstraint(
            "(status = 'APPLIED' AND applied_by IS NOT NULL AND applied_at IS NOT NULL)"
            " OR (status IN ('DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED') AND applied_by IS NULL AND applied_at IS NULL)",
            name="applied_pair",
        ),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text)
    target_codes: Mapped[list[str]] = mapped_column(JSON(none_as_null=True), default=list)
    operations: Mapped[list[dict[str, Any]]] = mapped_column(JSON(none_as_null=True), default=list)
    status: Mapped[str] = mapped_column(
        String(16), default=CHANGE_REQUEST_STATUS_INITIAL, server_default=text(f"'{CHANGE_REQUEST_STATUS_INITIAL}'")
    )
    impact: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    applied_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


# DB-CHG-002
class CorrectiveAction(Base):
    """遅延原因・是正措置（要求 CHG-06）。WBS要素・活動・報告日に紐付け、状態と効果を完了まで追う。

    - ``element_id``・``activity_id``・``status_report_id`` は紐付け（どれも任意。どれを必須にするかは API-CHG-007 の担当）
    - ``cause`` は遅延原因、``action`` は対策（どちらも必須）。``owner_user_id`` は担当、``due_date`` は期限（どちらも後から決めてよい）
    - ``status`` は NOT_STARTED → IN_PROGRESS → DONE（DONE から IN_PROGRESS へ戻せる。遷移と期限超過の判定は RULE-CHG-005 の担当）
    - ``effect`` は対策の効果（更新で入る。CHG-06）
    """

    __tablename__ = "corrective_actions"
    __table_args__ = (
        CheckConstraint(in_list("status", CORRECTIVE_ACTION_STATUSES), name="status"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    element_id: Mapped[int | None] = mapped_column(ForeignKey("wbs_elements.id"), index=True)
    activity_id: Mapped[int | None] = mapped_column(ForeignKey("activities.id"), index=True)
    status_report_id: Mapped[int | None] = mapped_column(ForeignKey("status_reports.id"), index=True)
    cause: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        String(16),
        default=CORRECTIVE_ACTION_STATUS_INITIAL,
        server_default=text(f"'{CORRECTIVE_ACTION_STATUS_INITIAL}'"),
    )
    effect: Mapped[str | None] = mapped_column(Text)


# DB-CHG-003
class AuditLog(Base):
    """変更履歴（要求 CHG-09・NFR-05）。書き込みの操作ごとに、変更者・日時・対象・変更前後の値を 1 行ずつ追記する。

    - ``user_id`` は変更者（利用者の操作でない書き込みは空）、``project_id`` は対象が属するプロジェクト（利用者など、属さないものは空）
    - ``entity_type`` は対象の種類（表の名前）、``entity_id`` は対象の行の ``id``、``wbs_code`` はその時点の WBS ID（あれば）
    - ``action`` は CREATE・UPDATE・DELETE。``before`` は変更前の値（CREATE は空）、``after`` は変更後の値（DELETE は空）
    - 検索（API-CHG-010：期間・変更者・対象・WBS ID）のために、時刻・変更者・対象・WBS ID に索引を置く
    - 追記だけ（4.2）：ORM からの更新・削除は ``_refuse_audit_log_change`` が止める
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint(in_list("action", AUDIT_ACTIONS), name="action"),
        Index("ix_audit_logs_entity_type_entity_id", "entity_type", "entity_id"),
        Index("ix_audit_logs_project_id_occurred_at", "project_id", "occurred_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[int] = mapped_column(Integer)
    wbs_code: Mapped[str | None] = mapped_column(String(100), index=True)
    action: Mapped[str] = mapped_column(String(8))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))


class AuditLogImmutableError(RuntimeError):
    """変更履歴の行を更新・削除しようとした（4.2：``audit_logs`` は追記だけ。NFR-05）。"""


def _refuse_audit_log_change(_mapper: Mapper[Any], _connection: Any, target: AuditLog) -> None:
    raise AuditLogImmutableError(f"変更履歴（audit_logs）は追記だけで、行 {target.id} を更新・削除できない（設計書 4.2・NFR-05）")


event.listen(AuditLog, "before_update", _refuse_audit_log_change)
event.listen(AuditLog, "before_delete", _refuse_audit_log_change)
