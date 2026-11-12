"""見積（EST）の群のテーブル（設計書 3 章・4.1・4.2・5.8、要求仕様書 EST-01・08）：見積の記録。

- 主キーは整数の ``id``。見積を書くたびに 1 行を足す（見積値を変えた履歴が残る。EST-08 の受入基準）。
  今の見積の値は ``wbs_elements.own_estimate_hours``・``activities.estimate_hours`` にあり、この表はその履歴と根拠である
- ``target_id`` は ``target_type`` が ELEMENT なら ``wbs_elements.id``、ACTIVITY なら ``activities.id``（内部キー。3 章。改番しても紐付けは切れない）。
  対象の種類で指す表が変わるので外部キーを持たない（4.2 の ``<単数形>_id`` に当たらない。``approval_requests.target_id``・``audit_logs.entity_id`` と同じ）。
  対象が消えても記録は残る（履歴を黙って消さない）
- 工数は人時の小数 ``Numeric(10,2)``（2.4）。``hours`` は人時へ換算した値、``input_value``・``input_unit`` は利用者が入れたままの値と単位（RULE-EST-002）。
  負の値は DB も拒む（EST-01 の「負の値は入力できない」）。人日の換算は RULE-EST-002（``app.domain.estimate``）の担当で DB では見ない
- 手法・前提・参照資料は任意（RULE-EST-002 の ``record_estimate`` が省けるのと同じ）。手法は EST-08 の 5 つのいずれか
- 外部キーに ``ON DELETE`` を付けない（docs/decisions/ASSUMPTIONS.md A-11・A-15・A-19・A-22・A-27・A-31 と同じ）
- 時刻は UTC で保存する（2.4。``UTCDateTime``）
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base
from app.models.types import UTCDateTime, in_list, utc_now

# 見積の対象（4.1 の estimate_records.target_type）
ESTIMATE_TARGET_TYPES = ("ELEMENT", "ACTIVITY")
# 見積の入力の単位（4.1 の estimate_records.input_unit。EST-01：人時・人日）
ESTIMATE_INPUT_UNITS = ("HOUR", "DAY")
# 見積の手法（4.1 の estimate_records.method。EST-08：ボトムアップ・類推・三点・専門家判断・その他）
ESTIMATE_METHODS = ("BOTTOM_UP", "ANALOGOUS", "THREE_POINT", "EXPERT", "OTHER")

# 人時の列（2.4）
_HOURS = Numeric(10, 2)


# DB-EST-001
class EstimateRecord(Base):
    """見積の記録（要求 EST-01・EST-08）。WP・活動の見積の値と、手法・前提・参照資料を 1 行に持つ。

    - 1 つの対象に何行でも持つ（一意を置かない。見積を書くたびに足す。履歴は ``entered_at``・``id`` の順。API-EST-002）
    - ``entered_by``・``entered_at`` は書いた利用者と時刻（UTC）
    - 対象がそのプロジェクトの WBS要素・活動であることは書く API（API-EST-001）が確かめる（指す表が対象の種類で変わるため DB では見ない）
    """

    __tablename__ = "estimate_records"
    __table_args__ = (
        CheckConstraint(in_list("target_type", ESTIMATE_TARGET_TYPES), name="target_type"),
        CheckConstraint(in_list("input_unit", ESTIMATE_INPUT_UNITS), name="input_unit"),
        CheckConstraint(f"method IS NULL OR {in_list('method', ESTIMATE_METHODS)}", name="method"),
        CheckConstraint("hours >= 0", name="hours"),
        CheckConstraint("input_value >= 0", name="input_value"),
        # 対象ごとの履歴（API-EST-002）と横断照会（RULE-WBS-008）の読み出し
        Index("ix_estimate_records_project_id_target", "project_id", "target_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    target_type: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[int] = mapped_column(Integer)
    hours: Mapped[Decimal] = mapped_column(_HOURS)
    input_value: Mapped[Decimal] = mapped_column(_HOURS)
    input_unit: Mapped[str] = mapped_column(String(8))
    method: Mapped[str | None] = mapped_column(String(16))
    assumptions: Mapped[str | None] = mapped_column(Text)
    references: Mapped[str | None] = mapped_column(Text)
    entered_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    entered_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
