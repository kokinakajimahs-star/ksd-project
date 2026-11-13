"""スケジュール（SCH）の群のテーブル（設計書 3 章・4.1・4.2・5.5、要求仕様書 BR-05・06・12）：活動・依存関係・
スケジュールベースラインとその項目。

- 主キーは整数の ``id``。活動は置き場の WP を内部キー（``wbs_elements.id``）で指す（3 章。改番しても紐付けは切れない。SCH-01）
- 外部キーに ``ON DELETE`` を付けない（docs/decisions/ASSUMPTIONS.md A-11・A-15 と同じ）。活動を消す操作（API-SCH-005）は、
  サービス層が依存の行と、ベースラインの項目からの紐付け（``schedule_baseline_items.activity_id``）を明示的に扱う（A-19）
- 値の一覧が設計書・要求仕様書に書かれている列（算定方式・依存の種類・版の理由・項目の種類）は DB の検査（CHECK）でも拒む。
  依存は R1 では FS・ラグ 0 だけ（4.1・OQ-04）なので、DB もそれ以外を拒む
- 日程の列のうち ``requested_start``・``duration_days``・``is_milestone`` が利用者の入力、``start_date`` 以降は
  RULE-SCH-002・003 の計算結果（4.2）。計算の前は空。最早・最遅は 5.5 の「結果の日付」と同じ変換の日付、``total_float`` は稼働日の数
- 時刻は UTC で保存する（2.4。``UTCDateTime``）。工数は人時の小数 ``Numeric(10,2)``（2.4）
- 全ての表の内部キーは再利用しない（設計書 1.2 の 4.2。SQLite は ``AUTOINCREMENT``。CR-012）
- 制約名は 63 文字以下（PostgreSQL の識別子の上限。ASSUMPTIONS.md A-49 の 8）。名前の規約（``app.infra.db``）で
  63 文字を超える ``schedule_baseline_items.schedule_baseline_id`` の外部キーには短い名前を付ける（移行 0012 で付け直した）

``schedule_baselines.change_request_id`` の ``change_requests.id``（DB-CHG-001）への外部キーは、
``change_requests`` を作る移行（db:CHG の ``0007_chg``）で張った（``scope_baselines.change_request_id`` と同じ。ASSUMPTIONS.md A-15・A-19）。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base
from app.models.types import UTCDateTime, in_list, utc_now

# 進捗率の算定方式（4.1 の activities.progress_method。要求仕様書 7.6 BR-06 の 7 つ。NULL はプロジェクトの既定）
PROGRESS_METHODS = (
    "DIRECT",
    "DELIVERABLE_RATIO",
    "APPROVED_WORK_RATIO",
    "CHECK_ITEM_RATIO",
    "WEIGHTED_MILESTONE",
    "ZERO_HUNDRED",
    "FIFTY_FIFTY",
)

# 依存の種類とラグ（4.1 の activity_dependencies。R1 は FS・ラグ 0 だけ。OQ-04・設計書 15 章の SCH-05 で広げる）
DEPENDENCY_TYPES = ("FS",)
DEPENDENCY_TYPE_DEFAULT = "FS"
LAG_DAYS_DEFAULT = 0

# スケジュールベースラインの版の理由（4.1 の schedule_baselines.reason。SCH-16 の保存・5.10 の再承認・CHG-04 の再基準化）
SCHEDULE_BASELINE_REASONS = ("MANUAL", "RE_APPROVAL", "CHANGE_REQUEST")

# ベースラインの項目の種類（4.1 の schedule_baseline_items.item_type）
BASELINE_ITEM_TYPES = ("ACTIVITY", "WORK_PACKAGE")

# 人時の列（2.4）
_HOURS = Numeric(10, 2)

# ベースラインの項目から版への外部キーの名前（名前の規約では 63 文字を超える。A-49 の 8）
SCHEDULE_BASELINE_ITEMS_BASELINE_FK = "fk_schedule_baseline_items_schedule_baseline_id"


# DB-SCH-001
class Activity(Base):
    """活動（要求 SCH-01・03・06・09・10）。WP の配下に置く（``work_package_id``。WP だけに置けることは RULE-WBS-003 が確かめる）。

    - ``sort_order`` は WP の中の表示順。``requested_start`` は希望開始日（空ならプロジェクトの開始日。5.5 の下限）
    - ``duration_days`` は期間（稼働日）。**マイルストーンは 0、それ以外の活動は 1 以上**（4.2。RULE-CAL-001 も 0 をマイルストーンとして扱う）
    - ``estimate_hours`` は見積（人時。空は未見積）。``progress_method`` は進捗率の算定方式（空はプロジェクトの既定。BR-06）
    - ``start_date``〜``is_critical`` は日程計算（RULE-SCH-002・003）の結果。計算の前は空（``is_critical`` は偽）
    """

    __tablename__ = "activities"
    __table_args__ = (
        CheckConstraint(
            "(is_milestone AND duration_days = 0) OR (NOT is_milestone AND duration_days >= 1)",
            name="milestone_duration",
        ),
        CheckConstraint("estimate_hours IS NULL OR estimate_hours >= 0", name="estimate_hours"),
        CheckConstraint(
            f"progress_method IS NULL OR {in_list('progress_method', PROGRESS_METHODS)}", name="progress_method"
        ),
        # 内部キーを再利用しない（設計書 1.1 の 4.2。写しの値が後から作った別の行を指さないため）
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    work_package_id: Mapped[int] = mapped_column(ForeignKey("wbs_elements.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    sort_order: Mapped[int] = mapped_column(Integer)
    requested_start: Mapped[date | None] = mapped_column(Date)
    duration_days: Mapped[int] = mapped_column(Integer)
    is_milestone: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    estimate_hours: Mapped[Decimal | None] = mapped_column(_HOURS)
    progress_method: Mapped[str | None] = mapped_column(String(32))
    start_date: Mapped[date | None] = mapped_column(Date)
    finish_date: Mapped[date | None] = mapped_column(Date)
    early_start: Mapped[date | None] = mapped_column(Date)
    early_finish: Mapped[date | None] = mapped_column(Date)
    late_start: Mapped[date | None] = mapped_column(Date)
    late_finish: Mapped[date | None] = mapped_column(Date)
    total_float: Mapped[int | None] = mapped_column(Integer)
    is_critical: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


# DB-SCH-002
class ActivityDependency(Base):
    """活動の依存関係（要求 SCH-04）。先行 → 後続の組で一意。R1 は終了→開始（FS）・ラグ 0 だけ（4.1・OQ-04）。

    自分自身への依存は循環なので DB も拒む。2 つ以上の活動にまたがる循環は RULE-SCH-001 が検出する。
    """

    __tablename__ = "activity_dependencies"
    __table_args__ = (
        UniqueConstraint("predecessor_id", "successor_id"),
        CheckConstraint("predecessor_id <> successor_id", name="not_self"),
        CheckConstraint(in_list("dep_type", DEPENDENCY_TYPES), name="dep_type"),
        CheckConstraint(f"lag_days = {LAG_DAYS_DEFAULT}", name="lag_days"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    predecessor_id: Mapped[int] = mapped_column(ForeignKey("activities.id"))
    successor_id: Mapped[int] = mapped_column(ForeignKey("activities.id"), index=True)
    dep_type: Mapped[str] = mapped_column(
        String(8), default=DEPENDENCY_TYPE_DEFAULT, server_default=text(f"'{DEPENDENCY_TYPE_DEFAULT}'")
    )
    lag_days: Mapped[int] = mapped_column(Integer, default=LAG_DAYS_DEFAULT, server_default=text(str(LAG_DAYS_DEFAULT)))


# DB-SCH-003
class ScheduleBaseline(Base):
    """スケジュールベースラインの版（要求 SCH-16・17、BR-12）。版はプロジェクトの中の連番（1 から。``project_id``＋``version`` で一意）。

    版は上書き・削除せず増やす（BR-12）。``name`` は比較の対象を選ぶための版の名前（SCH-17）。
    ``change_request_id`` は版を作った変更要求（CHG-04 の再基準化。理由が CHANGE_REQUEST のとき）。
    """

    __tablename__ = "schedule_baselines"
    __table_args__ = (
        UniqueConstraint("project_id", "version"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(in_list("reason", SCHEDULE_BASELINE_REASONS), name="reason"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(String(32))
    # change_requests への外部キーは db:CHG の移行（0007_chg）で張った（A-15・A-19）
    change_request_id: Mapped[int | None] = mapped_column(ForeignKey("change_requests.id"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


# DB-SCH-004
class ScheduleBaselineItem(Base):
    """スケジュールベースラインの版に固定した項目の写し（要求 SCH-16：保存時点の全活動の開始日・終了日・期間・見積工数。SCH-18・EFF-08）。

    - ``item_type`` が ACTIVITY なら活動の写し、WORK_PACKAGE なら WP の写し（活動の無い WP の見積など。EFF-08 の基準の値）
    - ``activity_id`` は写した活動（WP の項目では空）。``element_id`` は項目の WP（WP の項目は自分、活動の項目は活動の置き場の WP）
    - ``wbs_code``・``name`` 以降は写した時点の値。後から活動・WP を変えても変わらない（BR-12）
    - ``activity_id``・``element_id`` は写した時点の値として持ち、外部キー制約を張らない（設計書 1.1 の 4.2。CR-002）。
      写した活動・WP を後から消しても変えない（今の計画に無い ID が SCH-18 の「削除された活動」）
    """

    __tablename__ = "schedule_baseline_items"
    __table_args__ = (
        CheckConstraint(in_list("item_type", BASELINE_ITEM_TYPES), name="item_type"),
        CheckConstraint("item_type = 'ACTIVITY' OR activity_id IS NULL", name="work_package_without_activity"),
        CheckConstraint("duration_days IS NULL OR duration_days >= 0", name="duration_days"),
        CheckConstraint("estimate_hours IS NULL OR estimate_hours >= 0", name="estimate_hours"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 名前の規約の名前（fk_schedule_baseline_items_schedule_baseline_id_schedule_baselines）は 66 文字で
    # PostgreSQL の上限 63 文字を超えるので、指す表を省いた名前にする（A-49 の 8）
    schedule_baseline_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_baselines.id", name=SCHEDULE_BASELINE_ITEMS_BASELINE_FK), index=True
    )
    item_type: Mapped[str] = mapped_column(String(16))
    activity_id: Mapped[int | None] = mapped_column(Integer, index=True)
    element_id: Mapped[int | None] = mapped_column(Integer, index=True)
    wbs_code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    start_date: Mapped[date | None] = mapped_column(Date)
    finish_date: Mapped[date | None] = mapped_column(Date)
    duration_days: Mapped[int | None] = mapped_column(Integer)
    is_milestone: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    estimate_hours: Mapped[Decimal | None] = mapped_column(_HOURS)
