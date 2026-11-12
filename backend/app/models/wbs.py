"""WBS の群のテーブル（設計書 3 章・4.1・4.2・5.2・5.10）：WBS要素・採番規則・WBS ID の新旧対応・WBS辞書・
スコープ項目との紐付け・レビューと指摘・承認申請・スコープベースラインとその要素。

- 主キーは整数の ``id``。他のテーブルは WBS 要素を内部キー（``wbs_elements.id``）で指す（3 章。改番しても紐付けは切れない）
- 外部キーに ``ON DELETE`` を付けない（docs/decisions/ASSUMPTIONS.md A-11 と同じ）。要素を配下ごと消す操作（API-WBS-005）は、
  サービス層が辞書・紐付け・新旧対応などの行を明示的に扱う。**スコープベースラインの要素が指す要素は DB が消させない**
  （WBS-22 の「後から変更されない」。基準の後の要素の削除との両立は docs/instruction-change-requests.md CR-002）
- 値の一覧が設計書に書かれている列（区分・状態・指摘の分類・申請の対象・版の理由）は DB の検査（CHECK）でも拒む
- 時刻は UTC で保存する（2.4。``UTCDateTime``）。工数は人時の小数 ``Numeric(10,2)``（2.4）

``approval_requests.target_id`` は対象の種類（WBS／変更要求）で指す表が変わるので外部キーを持たない（4.2 の ``<単数形>_id`` に当たらない）。
``scope_baselines.change_request_id`` の ``change_requests.id``（DB-CHG-001）への外部キーは、
``change_requests`` を作る移行（db:CHG の ``0007_chg``）で張った（ASSUMPTIONS.md A-15。A-08 と同じ形）。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base
from app.models.types import UTCDateTime, in_list, utc_now

# WBS 要素の区分（4.1 の wbs_elements.kind。要求 WBS-02 の成果物・管理領域・構成要素・ワークパッケージ）
ELEMENT_KINDS = ("DELIVERABLE", "MANAGEMENT", "COMPONENT", "WORK_PACKAGE")

# 採番規則の既定（4.1。5.2 の既定の規則で要求仕様書 W§4 の例 1.0・1.1・2.0… になる）
SEPARATOR_DEFAULT = "."
MIN_DIGITS_DEFAULT = 1
PREFIX_DEFAULT = ""
PAD_FIRST_LEVEL_DEFAULT = True

# レビュー・指摘（4.1 の wbs_reviews・wbs_review_comments。要求 WBS-20）
REVIEW_STATUSES = ("OPEN", "CLOSED")
REVIEW_STATUS_INITIAL = "OPEN"
COMMENT_CATEGORIES = ("OMISSION", "DUPLICATION", "AMBIGUITY", "OTHER")
COMMENT_STATUSES = ("OPEN", "RESOLVED", "REJECTED")
COMMENT_STATUS_INITIAL = "OPEN"

# 承認申請（4.1 の approval_requests。5.10 の PENDING → APPROVED / REJECTED。要求 WBS-21・CHG-03）
APPROVAL_TARGET_TYPES = ("WBS", "CHANGE_REQUEST")
APPROVAL_STATUSES = ("PENDING", "APPROVED", "REJECTED")
APPROVAL_STATUS_INITIAL = "PENDING"

# スコープベースラインの版の理由（4.1 の scope_baselines.reason。5.10 の承認・再承認、CHG-04 の再基準化）
SCOPE_BASELINE_REASONS = ("INITIAL_APPROVAL", "RE_APPROVAL", "CHANGE_REQUEST")

# 人時の列（2.4）
_HOURS = Numeric(10, 2)


# DB-WBS-001
class WbsElement(Base):
    """WBS 要素（要求 WBS-01・02・04・08・10）。親子の木を ``parent_id`` で作る（最上位の要素は親が空）。

    - ``wbs_code`` は業務キー（WBS ID）。採番（RULE-WBS-001）が決め、移動では変えず、改番（RULE-WBS-002）でだけ変わる。
      改番の途中で一時的に同じ値が並ぶので一意にしていない（4.1 も一意を定めない）
    - ``sibling_seq`` は親の下での順番（1 から）、``last_child_seq`` は子に振った最後の順番（減らさない。5.2：消した番号を再利用しない）
    - ``sort_order`` は兄弟の表示順（改番はこの順に 1 から振り直す）。``needs_renumber`` は移動で親の ID と形が合わなくなった印
    - ``own_estimate_hours`` は自分の見積（人時。空は未見積）。``responsible_user_id``・``responsible_team`` は責任者・担当チーム（WBS-10）
    """

    __tablename__ = "wbs_elements"
    __table_args__ = (
        CheckConstraint(in_list("kind", ELEMENT_KINDS), name="kind"),
        CheckConstraint("sibling_seq >= 1", name="sibling_seq"),
        CheckConstraint("last_child_seq >= 0", name="last_child_seq"),
        CheckConstraint("own_estimate_hours IS NULL OR own_estimate_hours >= 0", name="own_estimate_hours"),
        # 内部キーを再利用しない（設計書 1.1 の 4.2。写しの値が後から作った別の行を指さないため）
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("wbs_elements.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(200))
    wbs_code: Mapped[str] = mapped_column(String(100))
    sibling_seq: Mapped[int] = mapped_column(Integer)
    last_child_seq: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    sort_order: Mapped[int] = mapped_column(Integer)
    own_estimate_hours: Mapped[Decimal | None] = mapped_column(_HOURS)
    responsible_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    responsible_team: Mapped[str | None] = mapped_column(String(200))
    needs_renumber: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


# DB-WBS-002
class WbsNumberingRule(Base):
    """プロジェクトの WBS ID の採番規則（要求 WBS-05。5.2 の ``separator``・``min_digits``・``prefix``・``pad_first_level``）。

    プロジェクトに 1 つ（``project_id`` は一意）。プロジェクトを作るとき（API-PRJ-001）に既定の行を作る。
    ``min_digits`` は順番を 0 詰めする桁数（1 以上）。区切り文字・接頭辞の形の検査は規則を変える API（API-WBS-006）が行う。
    ``top_last_child_seq`` は第 1 階層で今まで使った最大の順番（設計書 1.1 の 4.1・5.2。第 1 階層の親の ``last_child_seq`` に当たる。
    減らさない：第 1 階層の要素を消しても番号を再利用しない）。
    """

    __tablename__ = "wbs_numbering_rules"
    __table_args__ = (
        CheckConstraint("min_digits >= 1", name="min_digits"),
        CheckConstraint("top_last_child_seq >= 0", name="top_last_child_seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), unique=True)
    separator: Mapped[str] = mapped_column(
        String(8), default=SEPARATOR_DEFAULT, server_default=text(f"'{SEPARATOR_DEFAULT}'")
    )
    min_digits: Mapped[int] = mapped_column(
        Integer, default=MIN_DIGITS_DEFAULT, server_default=text(str(MIN_DIGITS_DEFAULT))
    )
    prefix: Mapped[str] = mapped_column(String(20), default=PREFIX_DEFAULT, server_default=text(f"'{PREFIX_DEFAULT}'"))
    pad_first_level: Mapped[bool] = mapped_column(Boolean, default=PAD_FIRST_LEVEL_DEFAULT, server_default=true())
    top_last_child_seq: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


# DB-WBS-003
class WbsCodeHistory(Base):
    """改番（RULE-WBS-002）で変わった WBS ID の新旧の対応（要求 WBS-06・07）。旧 ID の解決は新しい順にたどる（5.2）。"""

    __tablename__ = "wbs_code_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    element_id: Mapped[int] = mapped_column(ForeignKey("wbs_elements.id"), index=True)
    old_code: Mapped[str] = mapped_column(String(100))
    new_code: Mapped[str] = mapped_column(String(100))
    renumbered_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    renumbered_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


# DB-WBS-004
class WbsDictionaryEntry(Base):
    """要素の WBS 辞書（要求 WBS-11・13）。要素に 1 つ（``element_id`` は一意）。

    WBS ID・名称・責任者・見積は要素の行の値を表示する（WBS-11）ので、ここには成果物・作業範囲・完了条件・前提条件だけを持つ。
    成果物・完了条件の空の WP を一覧にする（WBS-13・RULE-WBS-005）ため、どの項目も空を許す。
    """

    __tablename__ = "wbs_dictionary_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    element_id: Mapped[int] = mapped_column(ForeignKey("wbs_elements.id"), unique=True)
    deliverable: Mapped[str | None] = mapped_column(Text)
    work_scope: Mapped[str | None] = mapped_column(Text)
    completion_criteria: Mapped[str | None] = mapped_column(Text)
    assumptions: Mapped[str | None] = mapped_column(Text)


# DB-WBS-005
class WbsScopeLink(Base):
    """要素とスコープ項目の紐付け（要求 PRJ-02・WBS-16。100% ルールの対象外の混入・対象内の漏れの判定に使う）。組で一意。"""

    __tablename__ = "wbs_scope_links"
    __table_args__ = (UniqueConstraint("element_id", "scope_item_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    element_id: Mapped[int] = mapped_column(ForeignKey("wbs_elements.id"))
    scope_item_id: Mapped[int] = mapped_column(ForeignKey("scope_items.id"), index=True)


# DB-WBS-006
class WbsReview(Base):
    """WBS のレビュー（要求 WBS-20）。始めると OPEN、閉じると CLOSED と ``closed_at`` が入る。

    ``reviewer_ids`` はレビューを頼んだ利用者の ID の JSON 配列（4.1）。
    """

    __tablename__ = "wbs_reviews"
    __table_args__ = (CheckConstraint(in_list("status", REVIEW_STATUSES), name="status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    status: Mapped[str] = mapped_column(
        String(16), default=REVIEW_STATUS_INITIAL, server_default=text(f"'{REVIEW_STATUS_INITIAL}'")
    )
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    reviewer_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


# DB-WBS-007
class WbsReviewComment(Base):
    """レビューの指摘（要求 WBS-20）。分類は漏れ・重複・曖昧・その他、状態は未対応・対応済み・却下。

    ``element_id`` は指摘の対象の要素。指摘への対応として要素を消すことがある（重複の指摘など）ので空を許す
    （要素を消すときはサービス層が空にし、指摘の記録は残す。ASSUMPTIONS.md A-15）。``updated_at`` は最後に変えた時刻。
    """

    __tablename__ = "wbs_review_comments"
    __table_args__ = (
        CheckConstraint(in_list("category", COMMENT_CATEGORIES), name="category"),
        CheckConstraint(in_list("status", COMMENT_STATUSES), name="status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("wbs_reviews.id"), index=True)
    element_id: Mapped[int | None] = mapped_column(ForeignKey("wbs_elements.id"), index=True)
    category: Mapped[str] = mapped_column(String(16))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default=COMMENT_STATUS_INITIAL, server_default=text(f"'{COMMENT_STATUS_INITIAL}'")
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    resolution: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)


# DB-WBS-008
class ApprovalRequest(Base):
    """承認申請（要求 WBS-21・CHG-03。5.10 の PENDING → APPROVED / REJECTED）。WBS と変更要求で共通。

    ``target_id`` は ``target_type`` が CHANGE_REQUEST なら変更要求の ID、WBS ならプロジェクトの ID（ASSUMPTIONS.md A-15）。
    判断すると ``decided_by``・``decided_at``・``comment`` が入る（承認者・日時・コメントが残る。WBS-21）。
    申請者と同じ人が判断できないこと（SELF_APPROVAL_FORBIDDEN）は判断の API が確かめる。
    """

    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(in_list("target_type", APPROVAL_TARGET_TYPES), name="target_type"),
        CheckConstraint(in_list("status", APPROVAL_STATUSES), name="status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    target_type: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(16), default=APPROVAL_STATUS_INITIAL, server_default=text(f"'{APPROVAL_STATUS_INITIAL}'")
    )
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    comment: Mapped[str | None] = mapped_column(Text)


# DB-WBS-009
class ScopeBaseline(Base):
    """スコープベースラインの版（要求 WBS-22・CHG-04）。版はプロジェクトの中の連番（1 から。``project_id``＋``version`` で一意）。

    版は上書きせず増やす。``approval_request_id`` は版を作った承認（WBS の承認・再承認）、
    ``change_request_id`` は版を作った変更要求（CHG-04 の再基準化）。どちらも理由によっては空。
    """

    __tablename__ = "scope_baselines"
    __table_args__ = (
        UniqueConstraint("project_id", "version"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(in_list("reason", SCOPE_BASELINE_REASONS), name="reason"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    version: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(32))
    approval_request_id: Mapped[int | None] = mapped_column(ForeignKey("approval_requests.id"), index=True)
    # change_requests への外部キーは db:CHG の移行（0007_chg）で張った（A-15）
    change_request_id: Mapped[int | None] = mapped_column(ForeignKey("change_requests.id"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


# DB-WBS-010
class ScopeBaselineElement(Base):
    """スコープベースラインの版に固定した要素の写し（要求 WBS-22：その時点の WBS と辞書を後から変更されない形で照会できる）。

    ``element_id``・``parent_element_id`` は写した時点の要素とその親の内部キー（木は版の中でこの 2 つでたどる）。
    写しの値として持ち、外部キー制約を張らない（設計書 1.1 の 4.2：基準の後に要素を消しても版は変わらない。CR-002）。
    WBS ID・区分・名称・自分の見積は写した時点の値、``dictionary`` は WBS 辞書の写し（辞書が無ければ空）。
    """

    __tablename__ = "scope_baseline_elements"
    __table_args__ = (
        CheckConstraint(in_list("kind", ELEMENT_KINDS), name="kind"),
        CheckConstraint("own_estimate_hours IS NULL OR own_estimate_hours >= 0", name="own_estimate_hours"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope_baseline_id: Mapped[int] = mapped_column(ForeignKey("scope_baselines.id"), index=True)
    element_id: Mapped[int] = mapped_column(Integer, index=True)
    parent_element_id: Mapped[int | None] = mapped_column(Integer, index=True)
    wbs_code: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(200))
    own_estimate_hours: Mapped[Decimal | None] = mapped_column(_HOURS)
    dictionary: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
