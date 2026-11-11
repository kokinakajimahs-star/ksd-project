"""プロジェクト基盤（PRJ）の群のテーブル（設計書 3 章・4.1・4.2）：プロジェクト・スコープ項目・稼働カレンダー・休日・メンバー。

- 主キーは整数の ``id``。外部キーは ``<テーブルの単数形>_id`` で、DB の外部キー制約を張る（4.2）
- 日付は ``Date``（ISO 8601 の日付。2.4）。1 日の稼働時間は人時の小数 ``Numeric(10,2)``（2.4 の工数と同じ型）
- 値の一覧が設計書に書かれている列（``projects.wbs_status``・``scope_items.kind``・``calendars.working_weekdays`` の形）は
  DB の検査（CHECK）でも拒む。ロールのコード（``project_members.roles``）の検査はドメイン（RULE-AUTH-001 の ``ROLES``）が持つ

``role_permissions.project_id``・``share_links.project_id``（共通の群）の ``projects.id`` への外部キーは、
このテーブルを作る移行で張る（docs/decisions/ASSUMPTIONS.md A-08・A-11）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base
from app.models.types import in_list

# WBS の状態（設計書 3 章・5.10。RULE-WBS-006 が遷移を決める）。作った直後は DRAFT
WBS_STATUSES = ("DRAFT", "IN_REVIEW", "PENDING_APPROVAL", "BASELINED")
WBS_STATUS_INITIAL = "DRAFT"

# スコープ項目の区分（4.1 の scope_items.kind。要求 PRJ-02 の対象内・対象外）
SCOPE_ITEM_KINDS = ("IN", "OUT")

# 稼働カレンダーの既定（4.1・要求 PRJ-04：土日休み・1 日 8 時間）。working_weekdays は月〜日の 7 文字で、1 が稼働日
WORKING_WEEKDAYS_DEFAULT = "1111100"
HOURS_PER_DAY_DEFAULT = Decimal("8")


def default_project_settings() -> dict[str, Any]:
    """``projects.settings`` の既定値（設計書 4.2。要求仕様書 BR-03・09・11）。呼ぶたびに新しい辞書を返す。"""
    return {
        "progress_method_default": "DIRECT",
        "delay_tolerance_days": 0.5,
        "delay_extract_days": 2,
        "variance_rate": 0.10,
        "variance_hours": 8,
        "hundred_percent_tolerance": 0.0,
    }


# DB-PRJ-001
class Project(Base):
    """プロジェクトの基本情報（要求 PRJ-01）と WBS の状態・プロジェクトの設定。

    名称・開始日・責任者は必須（PRJ-01 の受入基準）。``code`` はプロジェクトの業務キーで一意。
    ``settings`` は 4.2 の既定値から始まる JSON（進捗率の既定の算定方式・遅延判定の許容幅・抽出の閾値・差異の閾値・100% ルールの許容差）。
    """

    __tablename__ = "projects"
    __table_args__ = (CheckConstraint(in_list("wbs_status", WBS_STATUSES), name="wbs_status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str | None] = mapped_column(Text)
    requirements: Mapped[str | None] = mapped_column(Text)
    key_deliverables: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date] = mapped_column(Date)
    target_finish_date: Mapped[date | None] = mapped_column(Date)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    wbs_status: Mapped[str] = mapped_column(
        String(32), default=WBS_STATUS_INITIAL, server_default=text(f"'{WBS_STATUS_INITIAL}'")
    )
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=default_project_settings)


# DB-PRJ-002
class ScopeItem(Base):
    """スコープ項目（要求 PRJ-02）。対象内（IN）・対象外（OUT）の区分を持ち、100% ルール（RULE-WBS-004）の基準になる。"""

    __tablename__ = "scope_items"
    __table_args__ = (CheckConstraint(in_list("kind", SCOPE_ITEM_KINDS), name="kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer)


# DB-PRJ-003
class Calendar(Base):
    """プロジェクトの稼働カレンダー（要求 PRJ-04）。プロジェクトに 1 つ（``project_id`` は一意）。

    ``working_weekdays`` は月曜から日曜の順の 7 文字で、``1`` が稼働日・``0`` が非稼働日（既定 ``1111100``＝土日休み）。
    ``hours_per_day`` は 1 日の稼働時間（既定 8。0 より大きく 24 以下）。
    """

    __tablename__ = "calendars"
    __table_args__ = (
        # 7 文字で、0 と 1 だけ（length と replace は SQLite・PostgreSQL のどちらにもある）
        CheckConstraint(
            "length(working_weekdays) = 7 AND replace(replace(working_weekdays, '0', ''), '1', '') = ''",
            name="working_weekdays",
        ),
        CheckConstraint("hours_per_day > 0 AND hours_per_day <= 24", name="hours_per_day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), unique=True)
    working_weekdays: Mapped[str] = mapped_column(
        String(7), default=WORKING_WEEKDAYS_DEFAULT, server_default=text(f"'{WORKING_WEEKDAYS_DEFAULT}'")
    )
    hours_per_day: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=HOURS_PER_DAY_DEFAULT, server_default=text(str(HOURS_PER_DAY_DEFAULT))
    )


# DB-PRJ-004
class CalendarHoliday(Base):
    """稼働カレンダーの休日（日付）。同じカレンダーに同じ日付は 1 つ（``calendar_id``＋``holiday_date`` で一意）。"""

    __tablename__ = "calendar_holidays"
    __table_args__ = (UniqueConstraint("calendar_id", "holiday_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_id: Mapped[int] = mapped_column(ForeignKey("calendars.id"))
    holiday_date: Mapped[date] = mapped_column(Date)
    name: Mapped[str | None] = mapped_column(String(200))


# DB-PRJ-005
class ProjectMember(Base):
    """プロジェクトのメンバー（要求 PRJ-06）。利用者ごとに 1 行（``project_id``＋``user_id`` で一意）。

    ``roles`` は要求仕様書 3 章のロールのコードの JSON 配列（1 人が複数のロールを持てる）。``organization``・``team`` は所属。
    """

    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    roles: Mapped[list[str]] = mapped_column(JSON)
    organization: Mapped[str | None] = mapped_column(String(200))
    team: Mapped[str | None] = mapped_column(String(200))
