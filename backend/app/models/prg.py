"""進捗（PRG）の群のテーブル（設計書 3 章・4.1・4.2・5.4・5.6・5.10、要求仕様書 BR-06・08・09・13）：
報告日・進捗の入力・進捗の算定項目・スナップショットとその活動。

- 主キーは整数の ``id``。活動は内部キー（``activities.id``）で指す（3 章。改番しても紐付けは切れない）
- 外部キーに ``ON DELETE`` を付けない（docs/decisions/ASSUMPTIONS.md A-11・A-15・A-19 と同じ）。
  スナップショットの活動の ``activity_id`` は、写した活動を後から消すときにサービス層が空にする（写しは残す。BR-13。A-22）
- 値の一覧が設計書・要求仕様書に書かれている列（報告日の状態・算定項目の種類・計画の基準）は DB の検査（CHECK）でも拒む。
  スナップショットの判定は設計書に値の名前が無いので、5.6・BR-09 の 6 つに名前を付けた（A-22・OQ-23）
- 進捗率は 0〜100 の小数 ``Numeric(5,2)``（PRG-02。項目を数える方式の割合を丸めずに持つ）。NULL は未算定（4.1・BR-06）
- 稼働日の軸の位置（5.4）：報告日線 ``report_line`` は原点から報告日までの稼働日の数（整数）。
  進捗ポイント ``point``・ずれ ``deviation`` は ``開始位置 + 期間 × 進捗率`` の小数（5.6）を ``Numeric(12,4)`` で持つ
- 時刻は UTC で保存する（2.4。``UTCDateTime``）。工数は人時の小数 ``Numeric(10,2)``（2.4）
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
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base
from app.models.types import UTCDateTime, in_list, utc_now

# 報告日の状態（4.1 の status_reports.status。5.10：OPEN → FINALIZED。戻さない）
STATUS_REPORT_STATUSES = ("OPEN", "FINALIZED")
STATUS_REPORT_STATUS_DEFAULT = "OPEN"

# 進捗の算定項目の種類（4.1 の progress_items.item_type。BR-06 の項目を数える 4 つの方式に対応する）
#   DELIVERABLE：成果物完了割合、WORK：承認済み作業割合、CHECK：検証項目完了割合、MILESTONE：重み付きマイルストーン
PROGRESS_ITEM_TYPES = ("DELIVERABLE", "WORK", "CHECK", "MILESTONE")
PROGRESS_ITEM_WEIGHT_DEFAULT = 1

# スナップショットの計画の基準（4.1 の progress_snapshots.plan_basis。R1 は最新計画だけ。OQ-03）
PLAN_BASES = ("LATEST",)
PLAN_BASIS_DEFAULT = "LATEST"

# スナップショットの判定（5.6・BR-08・BR-09 の 6 つ。値の名前は設計書に無いので A-22 で付けた。OQ-23）
#   COMPLETED：完了（BR-08 の 1）、NOT_STARTED：未着手（予定内。BR-08 の 2）、BEHIND：遅れ、AHEAD：前倒し、
#   ON_SCHEDULE：予定どおり、NOT_COMPUTED：未算定（進捗率が算定できない。線の上では報告日線に置く）
JUDGEMENTS = ("COMPLETED", "NOT_STARTED", "BEHIND", "AHEAD", "ON_SCHEDULE", "NOT_COMPUTED")

# 人時の列（2.4）・進捗率の列（0〜100）・軸の位置の列（5.4・5.6）・稼働日の閾値の列（BR-09。projects.settings の値の写し）・重みの列
_HOURS = Numeric(10, 2)
_PERCENT = Numeric(5, 2)
_POSITION = Numeric(12, 4)
_DAYS = Numeric(6, 2)
_WEIGHT = Numeric(10, 2)


def _percent_range(column: str) -> str:
    return f"{column} IS NULL OR ({column} >= 0 AND {column} <= 100)"


def _not_before(finish: str, start: str) -> str:
    return f"{finish} IS NULL OR {start} IS NULL OR {finish} >= {start}"


# DB-PRG-001
class StatusReport(Base):
    """報告日（ステータス日。要求 PRG-01・17）。プロジェクトの中で日付ごとに 1 つ（``project_id``＋``report_date`` で一意）。

    - ``status`` は OPEN（入力を受け付ける）か FINALIZED（確定済み。入力を拒む。BR-13）。OPEN → FINALIZED だけで戻さない（5.10）
    - ``finalized_by``・``finalized_at`` は確定した利用者と時刻。**OPEN の間は空、FINALIZED なら両方ある**（DB の検査）
    - 確定の前提・スナップショットの作成・確定後の入力の拒否は RULE-PRG-007 とサービス層が行う
    """

    __tablename__ = "status_reports"
    __table_args__ = (
        UniqueConstraint("project_id", "report_date"),
        CheckConstraint(in_list("status", STATUS_REPORT_STATUSES), name="status"),
        CheckConstraint(
            "(status = 'OPEN' AND finalized_by IS NULL AND finalized_at IS NULL)"
            " OR (status = 'FINALIZED' AND finalized_by IS NOT NULL AND finalized_at IS NOT NULL)",
            name="finalized_pair",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    report_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        String(16), default=STATUS_REPORT_STATUS_DEFAULT, server_default=text(f"'{STATUS_REPORT_STATUS_DEFAULT}'")
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    finalized_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    finalized_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


# DB-PRG-002
class ProgressEntry(Base):
    """報告日ごとの活動の進捗の入力（要求 PRG-02・03、EFF-06）。報告日と活動の組で一意。

    - ``percent_complete`` は進捗率（0〜100。PRG-02）。NULL は未算定（4.1。項目 0 件の方式など。BR-06）
    - ``actual_start``・``actual_finish`` は実績の開始日・終了日。**実績開始日より前の実績終了日は DB も拒む**（PRG-02）。
      実績終了日があれば進捗率を 100 とするのは RULE-PRG-001 の担当（方式によって入力値と算定値が分かれるため DB では見ない）
    - ``remaining_hours`` は担当者が入力した残工数（ETC。人時）。NULL は自動（BR-10 の式。RULE-EFF-001）
    - ``delay_reason``・``forecast_finish`` は遅延理由と完了見込み日（PRG-03。RULE-PRG-006 の見込みの終了に使う）
    - 最新の値は「確定していない最新の報告日、無ければ最新の確定済みの報告日」の行である（4.2。サービス層が選ぶ）
    """

    __tablename__ = "progress_entries"
    __table_args__ = (
        UniqueConstraint("status_report_id", "activity_id"),
        CheckConstraint(_percent_range("percent_complete"), name="percent_complete"),
        CheckConstraint(_not_before("actual_finish", "actual_start"), name="actual_dates"),
        CheckConstraint("remaining_hours IS NULL OR remaining_hours >= 0", name="remaining_hours"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status_report_id: Mapped[int] = mapped_column(ForeignKey("status_reports.id"))
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id"), index=True)
    percent_complete: Mapped[Decimal | None] = mapped_column(_PERCENT)
    actual_start: Mapped[date | None] = mapped_column(Date)
    actual_finish: Mapped[date | None] = mapped_column(Date)
    remaining_hours: Mapped[Decimal | None] = mapped_column(_HOURS)
    delay_reason: Mapped[str | None] = mapped_column(Text)
    forecast_finish: Mapped[date | None] = mapped_column(Date)
    entered_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    entered_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


# DB-PRG-003
class ProgressItem(Base):
    """活動の進捗の算定項目（要求 PRG-05・BR-06）。項目を数える 4 つの方式の分子・分母になる。

    - ``item_type``：DELIVERABLE（成果物）・WORK（作業）・CHECK（検証項目）・MILESTONE（マイルストーン）
    - ``weight`` は重み（既定 1。重み付きマイルストーンの分子・分母）。**0 より大きい**（重みの総和を 0 にしない）
    - ``done`` は完了・達成、``approved`` は承認済み（承認済み作業割合の分子）。``done_at`` は完了の時刻（完了していなければ空）
    """

    __tablename__ = "progress_items"
    __table_args__ = (
        CheckConstraint(in_list("item_type", PROGRESS_ITEM_TYPES), name="item_type"),
        CheckConstraint("weight > 0", name="weight"),
        CheckConstraint("done OR done_at IS NULL", name="done_at_when_done"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id"), index=True)
    item_type: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    weight: Mapped[Decimal] = mapped_column(
        _WEIGHT, default=PROGRESS_ITEM_WEIGHT_DEFAULT, server_default=text(str(PROGRESS_ITEM_WEIGHT_DEFAULT))
    )
    done: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    approved: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


# DB-PRG-004
class ProgressSnapshot(Base):
    """報告日を確定したときのスナップショット（要求 PRG-17・BR-13）。報告日ごとに 1 つ（``status_report_id`` で一意）。

    - ``report_date`` は報告日の写し、``report_line`` は報告日線の位置（5.4 の ``line(r)``。原点から報告日までの稼働日の数）
    - ``tolerance_days``・``extract_days`` は確定の時点のプロジェクトの許容幅・遅延抽出の閾値（稼働日。BR-09。``projects.settings`` の写し）
    - ``plan_basis`` は計画の基準（R1 は LATEST＝最新計画だけ。OQ-03）
    - 過去のイナズマ線はスナップショットだけから描く（BR-13。現在の計画・進捗から計算し直さない）
    """

    __tablename__ = "progress_snapshots"
    __table_args__ = (
        CheckConstraint("tolerance_days >= 0", name="tolerance_days"),
        CheckConstraint("extract_days >= 0", name="extract_days"),
        CheckConstraint(in_list("plan_basis", PLAN_BASES), name="plan_basis"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status_report_id: Mapped[int] = mapped_column(ForeignKey("status_reports.id"), unique=True)
    report_date: Mapped[date] = mapped_column(Date)
    report_line: Mapped[int] = mapped_column(Integer)
    tolerance_days: Mapped[Decimal] = mapped_column(_DAYS)
    extract_days: Mapped[Decimal] = mapped_column(_DAYS)
    plan_basis: Mapped[str] = mapped_column(
        String(16), default=PLAN_BASIS_DEFAULT, server_default=text(f"'{PLAN_BASIS_DEFAULT}'")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


# DB-PRG-005
class ProgressSnapshotItem(Base):
    """スナップショットに固定した活動の写し（要求 PRG-17・18、BR-13）：確定の時点の計画日付・期間・進捗率・実績日付・ETC・
    進捗ポイント・判定。

    - ``row_order`` はガントの行の順（RULE-PRG-005 の折れ線の点の順）。スナップショットの中で一意
    - ``activity_id`` は写した活動。写しの値として持ち、外部キー制約を張らない（設計書 1.1 の 4.2。後から活動を消しても変えない。CR-002）
    - ``wbs_code`` 以降は写した時点の値。後から活動・進捗を変えても変わらない（BR-13）
    - ``point`` は進捗ポイント、``deviation`` はずれ（``point − report_line``。5.6）。``judgement`` は 5.6 の判定（``JUDGEMENTS``）
    """

    __tablename__ = "progress_snapshot_items"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "row_order"),
        CheckConstraint("duration_days IS NULL OR duration_days >= 0", name="duration_days"),
        CheckConstraint(_percent_range("percent_complete"), name="percent_complete"),
        CheckConstraint(_not_before("actual_finish", "actual_start"), name="actual_dates"),
        CheckConstraint("actual_hours IS NULL OR actual_hours >= 0", name="actual_hours"),
        CheckConstraint("etc_hours IS NULL OR etc_hours >= 0", name="etc_hours"),
        CheckConstraint(in_list("judgement", JUDGEMENTS), name="judgement"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("progress_snapshots.id"), index=True)
    activity_id: Mapped[int | None] = mapped_column(Integer, index=True)
    row_order: Mapped[int] = mapped_column(Integer)
    wbs_code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    start_date: Mapped[date | None] = mapped_column(Date)
    finish_date: Mapped[date | None] = mapped_column(Date)
    duration_days: Mapped[int | None] = mapped_column(Integer)
    is_milestone: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    is_critical: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    percent_complete: Mapped[Decimal | None] = mapped_column(_PERCENT)
    actual_start: Mapped[date | None] = mapped_column(Date)
    actual_finish: Mapped[date | None] = mapped_column(Date)
    actual_hours: Mapped[Decimal | None] = mapped_column(_HOURS)
    etc_hours: Mapped[Decimal | None] = mapped_column(_HOURS)
    point: Mapped[Decimal] = mapped_column(_POSITION)
    deviation: Mapped[Decimal] = mapped_column(_POSITION)
    judgement: Mapped[str] = mapped_column(String(16))
