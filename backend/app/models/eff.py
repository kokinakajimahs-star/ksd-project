"""工数（EFF）の群のテーブル（設計書 3 章・4.1・4.2・5.8、要求仕様書 EFF-01・02・10、BR-11）：
担当・割当、実績工数、差異の理由。

- 主キーは整数の ``id``。活動・WBS要素は内部キー（``activities.id``・``wbs_elements.id``）で指す（3 章。改番しても紐付けは切れない）
- 外部キーに ``ON DELETE`` を付けない（docs/decisions/ASSUMPTIONS.md A-11・A-15・A-19・A-22 と同じ）。
  活動を消す操作（API-SCH-005）は、割当をサービス層が消す（``app.services.schedule`` の ``AssignmentsStore.remove_activity``）。
  実績工数のある活動・差異の理由のある WBS要素は DB が消させない（実績を消すかどうかは OQ-26 (2)）
- 工数は人時の小数 ``Numeric(10,2)``（2.4）。負の工数は DB も拒む（RULE-EFF-005 の「負の値の禁止」）。
  実績工数は 1 行でも 24 時間を超えない（1 日の合計 24 時間以下（EFF-02・RULE-EFF-005）の必要条件。日の合計はドメインが見る）
- 時刻は UTC で保存する（2.4。``UTCDateTime``）

``variance_notes.corrective_action_id`` の ``corrective_actions.id``（DB-CHG-002）への外部キーは、
``corrective_actions`` を作る移行（db:CHG の ``0007_chg``）で張った（``scope_baselines.change_request_id`` と同じ形。ASSUMPTIONS.md A-15・A-19・A-27）。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Integer, Numeric, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base
from app.models.types import UTCDateTime, utc_now

# 1 行の実績工数の上限（人時。EFF-02 の「1日の合計が24時間を超えるとエラー」の必要条件）
TIME_ENTRY_MAX_HOURS = 24

# 人時の列（2.4）
_HOURS = Numeric(10, 2)


# DB-EFF-001
class Assignment(Base):
    """活動の担当・割当（要求 EFF-01・WBS-10）。活動と利用者の組で一意（1 つの活動に複数の担当を置ける。EFF-01）。

    - ``planned_hours`` は割当工数（人時）。NULL は割当工数を決めていない（担当だけを置いた。API-SCH-004 の担当の置き換え）。0 以上
    - 委託先が読める活動（9.2 の注記）・自分の割当の活動への入力（RULE-EFF-005・progress.enter_own）の正
    """

    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("activity_id", "user_id"),
        CheckConstraint("planned_hours IS NULL OR planned_hours >= 0", name="planned_hours"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    planned_hours: Mapped[Decimal | None] = mapped_column(_HOURS)


# DB-EFF-002
class TimeEntry(Base):
    """日別・活動別の実績工数（要求 EFF-02）。利用者・活動・作業日の組で一意（1 日 1 活動に 1 行）。

    - ``hours`` は人時（0 以上 24 以下）。1 日の合計 24 時間以下・自分の割当の活動だけ・確定した報告日より前の日は変えない
      （RULE-EFF-005）はドメインとサービス層の担当（行をまたぐ・別の表を読むため DB では見ない）
    - 活動の実績工数は、その活動の行の合計（報告日を指定したときは報告日以前の分。5.8）
    """

    __tablename__ = "time_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "activity_id", "work_date"),
        CheckConstraint(f"hours >= 0 AND hours <= {TIME_ENTRY_MAX_HOURS}", name="hours"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id"), index=True)
    work_date: Mapped[date] = mapped_column(Date)
    hours: Mapped[Decimal] = mapped_column(_HOURS)
    note: Mapped[str | None] = mapped_column(Text)


# DB-EFF-003
class VarianceNote(Base):
    """予定対実績の差異の理由（要求 EFF-10・BR-11）。WBS要素ごとに理由を記録し、是正措置（CHG-06）へ紐付ける。

    - ``element_id`` は差異を判定した WBS要素（RULE-EFF-004 の抽出の単位）
    - ``status_report_id`` は差異を見た報告日。空は報告日を指定しない分析（5.8 の「報告日を指定しなければ 4.2 の最新」）
    - ``reason`` は差異の理由（必須）。``corrective_action_id`` は紐付けた是正措置（DB-CHG-002。任意）
    - 1 要素・1 報告日に何行を持つか（上書きか追記か）は API-EFF-007 の担当（4.1 に一意の定めが無い。OQ-26 (1)）
    """

    __tablename__ = "variance_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    element_id: Mapped[int] = mapped_column(ForeignKey("wbs_elements.id"), index=True)
    status_report_id: Mapped[int | None] = mapped_column(ForeignKey("status_reports.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    corrective_action_id: Mapped[int | None] = mapped_column(ForeignKey("corrective_actions.id"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
