"""WBS の群のテーブルの試験（設計書 3 章・4.1・4.2・5.2・5.10）：DB-WBS-001〜010。

列が 4.1 の行どおりであること、既定値（4.1・5.2・5.10）、一意制約・外部キー・値の制約が DB で効くこと、
スコープベースラインの写しが指す要素を DB が消させないこと（WBS-22）、移行（alembic upgrade head）の結果がモデルと食い違わないことを確かめる。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR
from app.infra.db import Base, create_db_engine
from app.models.cmn import User
from app.models.prj import Project, ScopeItem
from app.models.wbs import (
    ApprovalRequest,
    ScopeBaseline,
    ScopeBaselineElement,
    WbsCodeHistory,
    WbsDictionaryEntry,
    WbsElement,
    WbsNumberingRule,
    WbsReview,
    WbsReviewComment,
    WbsScopeLink,
)

# 設計書 4.1 の「主な列」
COLUMNS_4_1 = {
    "wbs_elements": {
        "id", "project_id", "parent_id", "kind", "name", "wbs_code", "sibling_seq", "last_child_seq", "sort_order",
        "own_estimate_hours", "responsible_user_id", "responsible_team", "needs_renumber",
    },
    "wbs_numbering_rules": {
        "id", "project_id", "separator", "min_digits", "prefix", "pad_first_level", "top_last_child_seq",
    },
    "wbs_code_history": {"id", "project_id", "element_id", "old_code", "new_code", "renumbered_at", "renumbered_by"},
    "wbs_dictionary_entries": {"id", "element_id", "deliverable", "work_scope", "completion_criteria", "assumptions"},
    "wbs_scope_links": {"id", "element_id", "scope_item_id"},
    "wbs_reviews": {"id", "project_id", "status", "requested_by", "requested_at", "reviewer_ids", "closed_at"},
    "wbs_review_comments": {
        "id", "review_id", "element_id", "category", "body", "status", "author_id", "resolution", "updated_at",
    },
    "approval_requests": {
        "id", "project_id", "target_type", "target_id", "status", "requested_by", "requested_at", "decided_by",
        "decided_at", "comment",
    },
    "scope_baselines": {
        "id", "project_id", "version", "reason", "approval_request_id", "change_request_id", "created_by", "created_at",
    },
    "scope_baseline_elements": {
        "id", "scope_baseline_id", "element_id", "parent_element_id", "wbs_code", "kind", "name", "own_estimate_hours",
        "dictionary",
    },
}

AT = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


def _user(session: Session, login_id: str = "pm") -> User:
    user = User(login_id=login_id, display_name="利用者", password_hash="scrypt$dummy")
    session.add(user)
    session.flush()
    return user


def _project(session: Session, code: str = "P-001") -> Project:
    owner = _user(session, f"owner-{code}")
    project = Project(code=code, name="プロジェクト", start_date=date(2026, 12, 1), owner_user_id=owner.id)
    session.add(project)
    session.flush()
    return project


def _element(
    session: Session,
    project: Project,
    *,
    parent: WbsElement | None = None,
    code: str = "1.0",
    seq: int = 1,
    kind: str = "DELIVERABLE",
    **extra: Any,
) -> WbsElement:
    element = WbsElement(
        project_id=project.id,
        parent_id=parent.id if parent else None,
        kind=kind,
        name=f"要素 {code}",
        wbs_code=code,
        sibling_seq=seq,
        sort_order=seq,
        **extra,
    )
    session.add(element)
    session.flush()
    return element


def _flush_fails(session: Session) -> None:
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


@pytest.mark.parametrize("table", sorted(COLUMNS_4_1))
def test_columns_match_design_4_1(table: str) -> None:
    """DB-WBS-001〜010 の列が設計書 4.1 の行どおりである。"""
    assert set(Base.metadata.tables[table].c.keys()) == COLUMNS_4_1[table]


# ---------------------------------------------------------------------------
# DB-WBS-001 wbs_elements
# ---------------------------------------------------------------------------


def test_wbs_elements_tree_and_defaults(db_session: Session) -> None:
    """DB-WBS-001 wbs_elements：親子の木を parent_id で作る（最上位は親が空）。last_child_seq は 0・needs_renumber は偽から始まる。"""
    project = _project(db_session)
    responsible = _user(db_session, "lead")
    root = _element(db_session, project, code="1.0")
    wp = _element(
        db_session,
        project,
        parent=root,
        code="1.1",
        kind="WORK_PACKAGE",
        own_estimate_hours=Decimal("12.50"),
        responsible_user_id=responsible.id,
        responsible_team="A",
    )
    root.last_child_seq = 1
    db_session.commit()
    db_session.expunge_all()

    loaded_root = db_session.get(WbsElement, root.id)
    loaded_wp = db_session.get(WbsElement, wp.id)
    assert loaded_root is not None and loaded_wp is not None
    assert (loaded_root.parent_id, loaded_root.last_child_seq, loaded_root.needs_renumber) == (None, 1, False)
    assert loaded_root.own_estimate_hours is None
    assert (loaded_wp.parent_id, loaded_wp.wbs_code, loaded_wp.sibling_seq, loaded_wp.last_child_seq) == (root.id, "1.1", 1, 0)
    assert loaded_wp.own_estimate_hours == Decimal("12.50")
    assert (loaded_wp.responsible_user_id, loaded_wp.responsible_team) == (responsible.id, "A")


@pytest.mark.parametrize("kind", ["DELIVERABLE", "MANAGEMENT", "COMPONENT", "WORK_PACKAGE"])
def test_wbs_elements_kind_accepts_four_values(db_session: Session, kind: str) -> None:
    """DB-WBS-001 wbs_elements：区分は 4 値（要求 WBS-02）のどれか。"""
    _element(db_session, _project(db_session), kind=kind)
    db_session.commit()


def test_wbs_elements_kind_rejects_other_values(db_session: Session) -> None:
    """DB-WBS-001 wbs_elements：4 値以外の区分は DB が拒む。"""
    project = _project(db_session)
    db_session.add(
        WbsElement(project_id=project.id, kind="TASK", name="x", wbs_code="1.0", sibling_seq=1, sort_order=1)
    )
    _flush_fails(db_session)


@pytest.mark.parametrize(
    ("column", "value"),
    [("sibling_seq", 0), ("last_child_seq", -1), ("own_estimate_hours", Decimal("-0.01"))],
)
def test_wbs_elements_rejects_out_of_range(db_session: Session, column: str, value: object) -> None:
    """DB-WBS-001 wbs_elements：順番は 1 から（5.2）、last_child_seq は 0 以上、自分の見積は 0 以上。境界の外は DB が拒む。"""
    project = _project(db_session)
    values: dict[str, object] = {
        "project_id": project.id, "kind": "DELIVERABLE", "name": "x", "wbs_code": "1.0", "sibling_seq": 1,
        "sort_order": 1, column: value,
    }
    db_session.add(WbsElement(**values))
    _flush_fails(db_session)


def test_wbs_elements_accepts_lower_bounds(db_session: Session) -> None:
    """DB-WBS-001 wbs_elements：順番 1・last_child_seq 0・自分の見積 0 は受け付ける。"""
    _element(db_session, _project(db_session), seq=1, last_child_seq=0, own_estimate_hours=Decimal("0"))
    db_session.commit()


@pytest.mark.parametrize("missing", ["project_id", "kind", "name", "wbs_code", "sibling_seq", "sort_order"])
def test_wbs_elements_required_columns(db_session: Session, missing: str) -> None:
    """DB-WBS-001 wbs_elements：プロジェクト・区分・名称・WBS ID・順番・表示順は必須。"""
    project = _project(db_session)
    values: dict[str, object] = {
        "project_id": project.id, "kind": "DELIVERABLE", "name": "x", "wbs_code": "1.0", "sibling_seq": 1,
        "sort_order": 1,
    }
    del values[missing]
    db_session.add(WbsElement(**values))
    _flush_fails(db_session)


def test_wbs_elements_same_code_allowed(db_session: Session) -> None:
    """DB-WBS-001 wbs_elements：wbs_code は一意にしていない（4.1 に一意の定めが無い。改番の途中で同じ値が並ぶ）。"""
    project = _project(db_session)
    _element(db_session, project, code="1.0", seq=1)
    _element(db_session, project, code="1.0", seq=2)
    db_session.commit()


@pytest.mark.parametrize("column", ["project_id", "parent_id", "responsible_user_id"])
def test_wbs_elements_references(db_session: Session, column: str) -> None:
    """DB-WBS-001 wbs_elements：project_id は projects、parent_id は wbs_elements、responsible_user_id は users を指す。"""
    project = _project(db_session)
    values: dict[str, object] = {
        "project_id": project.id, "kind": "DELIVERABLE", "name": "x", "wbs_code": "1.0", "sibling_seq": 1,
        "sort_order": 1, column: 9999,
    }
    db_session.add(WbsElement(**values))
    _flush_fails(db_session)


def test_wbs_elements_parent_cannot_be_deleted_while_children_exist(db_session: Session) -> None:
    """DB-WBS-001 wbs_elements：子のある要素だけを消すことはできない（配下ごと消すのはサービス層が子から消す。ON DELETE なし）。"""
    project = _project(db_session)
    root = _element(db_session, project)
    _element(db_session, project, parent=root, code="1.1")
    db_session.commit()
    db_session.delete(root)
    _flush_fails(db_session)


def test_wbs_elements_server_defaults_apply_to_raw_insert(db_session: Session) -> None:
    """DB-WBS-001 wbs_elements：last_child_seq（0）・needs_renumber（偽）は DB の既定値としても入る。"""
    project = _project(db_session)
    db_session.execute(
        sa.text(
            "INSERT INTO wbs_elements (project_id, kind, name, wbs_code, sibling_seq, sort_order) "
            "VALUES (:p, 'DELIVERABLE', '生', '1.0', 1, 1)"
        ),
        {"p": project.id},
    )
    row = db_session.execute(sa.text("SELECT last_child_seq, needs_renumber FROM wbs_elements")).one()
    assert (row[0], bool(row[1])) == (0, False)


# ---------------------------------------------------------------------------
# DB-WBS-002 wbs_numbering_rules
# ---------------------------------------------------------------------------


def test_numbering_rules_defaults(db_session: Session) -> None:
    """DB-WBS-002 wbs_numbering_rules：既定は区切り「.」・桁数 1・接頭辞なし・第 1 階層の補完あり（4.1。W§4 の例 1.0・1.1）。"""
    project = _project(db_session)
    rule = WbsNumberingRule(project_id=project.id)
    db_session.add(rule)
    db_session.commit()
    db_session.expunge_all()
    loaded = db_session.get(WbsNumberingRule, rule.id)
    assert loaded is not None
    assert (loaded.separator, loaded.min_digits, loaded.prefix, loaded.pad_first_level) == (".", 1, "", True)
    # 第 1 階層で今まで使った最大の順番（設計書 1.1 の 4.1）は 0 から
    assert loaded.top_last_child_seq == 0


def test_numbering_rules_server_defaults_apply_to_raw_insert(db_session: Session) -> None:
    """DB-WBS-002 wbs_numbering_rules：4 項目の既定値は DB の既定値としても入る（ORM を通らない書き込み）。"""
    project = _project(db_session)
    db_session.execute(sa.text("INSERT INTO wbs_numbering_rules (project_id) VALUES (:p)"), {"p": project.id})
    row = db_session.execute(
        sa.text("SELECT separator, min_digits, prefix, pad_first_level, top_last_child_seq FROM wbs_numbering_rules")
    ).one()
    assert (row[0], row[1], row[2], bool(row[3]), row[4]) == (".", 1, "", True, 0)


def test_numbering_rules_one_per_project(db_session: Session) -> None:
    """DB-WBS-002 wbs_numbering_rules：project_id は一意（プロジェクトに採番規則は 1 つ）。project_id は projects を指す。"""
    project = _project(db_session)
    db_session.add(WbsNumberingRule(project_id=project.id, separator="-", min_digits=2, prefix="P", pad_first_level=False))
    db_session.commit()
    db_session.add(WbsNumberingRule(project_id=project.id))
    _flush_fails(db_session)

    db_session.add(WbsNumberingRule(project_id=9999))
    _flush_fails(db_session)


def test_numbering_rules_min_digits_at_least_one(db_session: Session) -> None:
    """DB-WBS-002 wbs_numbering_rules：桁数は 1 以上。0 は DB が拒む。"""
    project = _project(db_session)
    db_session.add(WbsNumberingRule(project_id=project.id, min_digits=0))
    _flush_fails(db_session)


def test_numbering_rules_top_last_child_seq_not_negative(db_session: Session) -> None:
    """DB-WBS-002 wbs_numbering_rules：第 1 階層の数え（top_last_child_seq）は 0 以上。負の値は DB が拒む。"""
    project = _project(db_session)
    db_session.add(WbsNumberingRule(project_id=project.id, top_last_child_seq=-1))
    _flush_fails(db_session)


# ---------------------------------------------------------------------------
# DB-WBS-003 wbs_code_history
# ---------------------------------------------------------------------------


def test_code_history_keeps_old_and_new_codes(db_session: Session) -> None:
    """DB-WBS-003 wbs_code_history：要素ごとの旧 ID・新 ID・改番の時刻（UTC）・改番した人を残す。1 つの要素に何行でも持てる。"""
    project = _project(db_session)
    user = _user(db_session, "renumberer")
    element = _element(db_session, project, code="2.0")
    first = WbsCodeHistory(
        project_id=project.id, element_id=element.id, old_code="3.0", new_code="2.0", renumbered_at=AT,
        renumbered_by=user.id,
    )
    second = WbsCodeHistory(project_id=project.id, element_id=element.id, old_code="2.0", new_code="1.0", renumbered_by=user.id)
    db_session.add_all([first, second])
    db_session.commit()
    db_session.expunge_all()
    loaded = db_session.get(WbsCodeHistory, first.id)
    assert loaded is not None
    assert (loaded.old_code, loaded.new_code, loaded.renumbered_at) == ("3.0", "2.0", AT)
    assert db_session.get(WbsCodeHistory, second.id).renumbered_at.tzinfo is not None  # type: ignore[union-attr]


@pytest.mark.parametrize("column", ["project_id", "element_id", "renumbered_by"])
def test_code_history_references(db_session: Session, column: str) -> None:
    """DB-WBS-003 wbs_code_history：project_id・element_id・renumbered_by はそれぞれ projects・wbs_elements・users を指す。"""
    project = _project(db_session)
    user = _user(db_session, "renumberer")
    element = _element(db_session, project)
    values: dict[str, object] = {
        "project_id": project.id, "element_id": element.id, "old_code": "1.0", "new_code": "2.0", "renumbered_by": user.id,
        column: 9999,
    }
    db_session.add(WbsCodeHistory(**values))
    _flush_fails(db_session)


# ---------------------------------------------------------------------------
# DB-WBS-004 wbs_dictionary_entries
# ---------------------------------------------------------------------------


def test_dictionary_one_per_element_and_fields_optional(db_session: Session) -> None:
    """DB-WBS-004 wbs_dictionary_entries：要素に 1 つ（element_id 一意）。項目は空を許す（WBS-13 で空の WP を一覧にする）。"""
    project = _project(db_session)
    element = _element(db_session, project, kind="WORK_PACKAGE")
    other = _element(db_session, project, code="2.0", seq=2)
    full = WbsDictionaryEntry(
        element_id=element.id, deliverable="設計書", work_scope="画面の設計", completion_criteria="レビュー済み",
        assumptions="要件が確定している",
    )
    db_session.add_all([full, WbsDictionaryEntry(element_id=other.id)])
    db_session.commit()
    db_session.expunge_all()
    loaded = db_session.get(WbsDictionaryEntry, full.id)
    assert loaded is not None
    assert (loaded.deliverable, loaded.work_scope, loaded.completion_criteria, loaded.assumptions) == (
        "設計書", "画面の設計", "レビュー済み", "要件が確定している",
    )

    db_session.add(WbsDictionaryEntry(element_id=element.id, deliverable="重複"))
    _flush_fails(db_session)

    db_session.add(WbsDictionaryEntry(element_id=9999))
    _flush_fails(db_session)


# ---------------------------------------------------------------------------
# DB-WBS-005 wbs_scope_links
# ---------------------------------------------------------------------------


def test_scope_links_unique_pair_and_references(db_session: Session) -> None:
    """DB-WBS-005 wbs_scope_links：element_id＋scope_item_id で一意。1 つの要素は複数のスコープ項目に紐付けられる。"""
    project = _project(db_session)
    element = _element(db_session, project)
    items = [ScopeItem(project_id=project.id, kind=kind, title=kind, sort_order=i) for i, kind in enumerate(("IN", "OUT"))]
    db_session.add_all(items)
    db_session.flush()
    db_session.add_all([WbsScopeLink(element_id=element.id, scope_item_id=item.id) for item in items])
    db_session.commit()

    db_session.add(WbsScopeLink(element_id=element.id, scope_item_id=items[0].id))
    _flush_fails(db_session)

    for values in ({"element_id": 9999, "scope_item_id": items[0].id}, {"element_id": element.id, "scope_item_id": 9999}):
        db_session.add(WbsScopeLink(**values))
        _flush_fails(db_session)


# ---------------------------------------------------------------------------
# DB-WBS-006 wbs_reviews・DB-WBS-007 wbs_review_comments
# ---------------------------------------------------------------------------


def _review(session: Session, project: Project, requester: User, **extra: Any) -> WbsReview:
    review = WbsReview(project_id=project.id, requested_by=requester.id, **extra)
    session.add(review)
    session.flush()
    return review


def test_reviews_defaults_and_close(db_session: Session) -> None:
    """DB-WBS-006 wbs_reviews：始めると OPEN・依頼の時刻が入り、レビュアーの ID は JSON 配列で往復する。閉じると CLOSED と閉じた時刻。"""
    project = _project(db_session)
    requester = _user(db_session, "requester")
    reviewer = _user(db_session, "reviewer")
    review = _review(db_session, project, requester, reviewer_ids=[reviewer.id, requester.id])
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(WbsReview, review.id)
    assert loaded is not None
    assert (loaded.status, loaded.reviewer_ids, loaded.closed_at) == ("OPEN", [reviewer.id, requester.id], None)
    assert loaded.requested_at.tzinfo is not None

    loaded.status = "CLOSED"
    loaded.closed_at = AT
    db_session.commit()
    db_session.expunge_all()
    closed = db_session.get(WbsReview, review.id)
    assert closed is not None
    assert (closed.status, closed.closed_at) == ("CLOSED", AT)


def test_reviews_reject_other_status_and_missing_references(db_session: Session) -> None:
    """DB-WBS-006 wbs_reviews：状態は OPEN・CLOSED だけ。project_id・requested_by は projects・users を指す。"""
    project = _project(db_session)
    requester = _user(db_session, "requester")
    for values in (
        {"project_id": project.id, "requested_by": requester.id, "status": "DONE"},
        {"project_id": 9999, "requested_by": requester.id},
        {"project_id": project.id, "requested_by": 9999},
    ):
        db_session.add(WbsReview(**values))
        _flush_fails(db_session)


def test_review_comments_defaults_and_states(db_session: Session) -> None:
    """DB-WBS-007 wbs_review_comments：指摘は要素に紐付けて OPEN で始まり、RESOLVED・REJECTED へ変えられる。WBS 全体への指摘は要素が空。"""
    project = _project(db_session)
    author = _user(db_session, "reviewer")
    review = _review(db_session, project, author)
    element = _element(db_session, project)
    comment = WbsReviewComment(review_id=review.id, element_id=element.id, category="DUPLICATION", body="2.0 と重複", author_id=author.id)
    whole = WbsReviewComment(review_id=review.id, category="OMISSION", body="テストの工程が無い", author_id=author.id)
    db_session.add_all([comment, whole])
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(WbsReviewComment, comment.id)
    assert loaded is not None
    assert (loaded.status, loaded.element_id, loaded.resolution) == ("OPEN", element.id, None)
    assert loaded.updated_at.tzinfo is not None
    assert db_session.get(WbsReviewComment, whole.id).element_id is None  # type: ignore[union-attr]

    for status in ("RESOLVED", "REJECTED"):
        loaded.status = status
        loaded.resolution = "対応した"
        db_session.commit()


@pytest.mark.parametrize("category", ["OMISSION", "DUPLICATION", "AMBIGUITY", "OTHER"])
def test_review_comments_accept_four_categories(db_session: Session, category: str) -> None:
    """DB-WBS-007 wbs_review_comments：分類は漏れ・重複・曖昧・その他の 4 つ（要求 WBS-20）。"""
    project = _project(db_session)
    author = _user(db_session, "reviewer")
    review = _review(db_session, project, author)
    db_session.add(WbsReviewComment(review_id=review.id, category=category, body="指摘", author_id=author.id))
    db_session.commit()


@pytest.mark.parametrize(
    "override",
    [
        {"category": "TYPO"},
        {"status": "CLOSED"},
        {"review_id": 9999},
        {"element_id": 9999},
        {"author_id": 9999},
        {"body": None},
    ],
    ids=["category", "status", "no-review", "no-element", "no-author", "no-body"],
)
def test_review_comments_rejections(db_session: Session, override: dict[str, object]) -> None:
    """DB-WBS-007 wbs_review_comments：4.1 に無い分類・状態、無いレビュー・要素・利用者、本文の無い指摘は DB が拒む。"""
    project = _project(db_session)
    author = _user(db_session, "reviewer")
    review = _review(db_session, project, author)
    values: dict[str, object] = {"review_id": review.id, "category": "OTHER", "body": "指摘", "author_id": author.id}
    values.update(override)
    db_session.add(WbsReviewComment(**values))
    _flush_fails(db_session)


# ---------------------------------------------------------------------------
# DB-WBS-008 approval_requests
# ---------------------------------------------------------------------------


def test_approval_requests_pending_then_decided(db_session: Session) -> None:
    """DB-WBS-008 approval_requests：申請は PENDING で始まり、判断すると判断者・時刻・コメントが残る（要求 WBS-21）。"""
    project = _project(db_session)
    requester = _user(db_session, "requester")
    approver = _user(db_session, "approver")
    request = ApprovalRequest(project_id=project.id, target_type="WBS", target_id=project.id, requested_by=requester.id)
    db_session.add(request)
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(ApprovalRequest, request.id)
    assert loaded is not None
    assert (loaded.status, loaded.decided_by, loaded.decided_at, loaded.comment) == ("PENDING", None, None, None)
    assert loaded.requested_at.tzinfo is not None

    loaded.status = "APPROVED"
    loaded.decided_by = approver.id
    loaded.decided_at = AT
    loaded.comment = "承認します"
    db_session.commit()
    db_session.expunge_all()
    decided = db_session.get(ApprovalRequest, request.id)
    assert decided is not None
    assert (decided.status, decided.decided_by, decided.decided_at, decided.comment) == ("APPROVED", approver.id, AT, "承認します")


@pytest.mark.parametrize(("target_type", "status"), [("WBS", "REJECTED"), ("CHANGE_REQUEST", "PENDING")])
def test_approval_requests_accept_design_values(db_session: Session, target_type: str, status: str) -> None:
    """DB-WBS-008 approval_requests：対象は WBS・CHANGE_REQUEST、状態は PENDING・APPROVED・REJECTED。target_id は外部キーを持たない。"""
    project = _project(db_session)
    requester = _user(db_session, "requester")
    db_session.add(
        ApprovalRequest(project_id=project.id, target_type=target_type, target_id=12345, status=status, requested_by=requester.id)
    )
    db_session.commit()


@pytest.mark.parametrize(
    "override",
    [
        {"target_type": "ACTIVITY"},
        {"status": "WITHDRAWN"},
        {"project_id": 9999},
        {"requested_by": 9999},
        {"decided_by": 9999},
        {"target_id": None},
    ],
    ids=["target_type", "status", "no-project", "no-requester", "no-decider", "no-target"],
)
def test_approval_requests_rejections(db_session: Session, override: dict[str, object]) -> None:
    """DB-WBS-008 approval_requests：4.1・5.10 に無い対象・状態、無いプロジェクト・利用者、対象の ID の無い申請は DB が拒む。"""
    project = _project(db_session)
    requester = _user(db_session, "requester")
    values: dict[str, object] = {
        "project_id": project.id, "target_type": "WBS", "target_id": project.id, "requested_by": requester.id,
    }
    values.update(override)
    db_session.add(ApprovalRequest(**values))
    _flush_fails(db_session)


def test_approval_requests_target_id_has_no_foreign_key() -> None:
    """DB-WBS-008 approval_requests：target_id は対象の種類で指す表が変わるので外部キーを持たない（4.2 の <単数形>_id に当たらない）。"""
    assert Base.metadata.tables["approval_requests"].c.target_id.foreign_keys == set()


# ---------------------------------------------------------------------------
# DB-WBS-009 scope_baselines・DB-WBS-010 scope_baseline_elements
# ---------------------------------------------------------------------------


def _baseline(
    session: Session, project: Project, creator: User, version: int = 1, reason: str = "INITIAL_APPROVAL", **extra: Any
) -> ScopeBaseline:
    baseline = ScopeBaseline(project_id=project.id, version=version, reason=reason, created_by=creator.id, **extra)
    session.add(baseline)
    session.flush()
    return baseline


def test_scope_baselines_versions_increase_per_project(db_session: Session) -> None:
    """DB-WBS-009 scope_baselines：版はプロジェクトの中の連番で、project_id＋version で一意（上書きせず増やす）。別のプロジェクトは 1 から。"""
    project = _project(db_session, "P-001")
    other = _project(db_session, "P-002")
    creator = _user(db_session, "approver")
    request = ApprovalRequest(project_id=project.id, target_type="WBS", target_id=project.id, requested_by=creator.id)
    db_session.add(request)
    db_session.flush()
    first = _baseline(db_session, project, creator, 1, approval_request_id=request.id)
    _baseline(db_session, project, creator, 2, reason="RE_APPROVAL")
    _baseline(db_session, other, creator, 1)
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(ScopeBaseline, first.id)
    assert loaded is not None
    assert (loaded.version, loaded.reason, loaded.approval_request_id, loaded.change_request_id) == (
        1, "INITIAL_APPROVAL", request.id, None,
    )
    assert loaded.created_at.tzinfo is not None

    db_session.add(ScopeBaseline(project_id=project.id, version=2, reason="CHANGE_REQUEST", created_by=creator.id))
    _flush_fails(db_session)


@pytest.mark.parametrize(
    "override",
    [
        {"reason": "MANUAL"},
        {"version": 0},
        {"project_id": 9999},
        {"created_by": 9999},
        {"approval_request_id": 9999},
    ],
    ids=["reason", "version-0", "no-project", "no-creator", "no-approval"],
)
def test_scope_baselines_rejections(db_session: Session, override: dict[str, object]) -> None:
    """DB-WBS-009 scope_baselines：4.1 に無い理由、0 の版、無いプロジェクト・利用者・承認申請は DB が拒む。"""
    project = _project(db_session)
    creator = _user(db_session, "approver")
    values: dict[str, object] = {"project_id": project.id, "version": 1, "reason": "INITIAL_APPROVAL", "created_by": creator.id}
    values.update(override)
    db_session.add(ScopeBaseline(**values))
    _flush_fails(db_session)


def test_scope_baselines_change_request_id_references_change_requests_once_it_exists() -> None:
    """DB-WBS-009 scope_baselines：change_request_id は整数・空を許す。change_requests（DB-CHG-001）がモデルにあれば外部キーを持つ。

    change_requests は db:CHG のスコープが作る。そのとき外部キーを張り忘れるとこの試験が落ちる（ASSUMPTIONS.md A-15）。
    """
    column = Base.metadata.tables["scope_baselines"].c.change_request_id
    assert isinstance(column.type, sa.Integer)
    assert column.nullable is True
    if "change_requests" in Base.metadata.tables:
        assert {fk.target_fullname for fk in column.foreign_keys} == {"change_requests.id"}
    else:
        assert column.foreign_keys == set()


def test_scope_baseline_elements_snapshot_round_trip(db_session: Session) -> None:
    """DB-WBS-010 scope_baseline_elements：版に要素の木（element_id・parent_element_id）・WBS ID・区分・名称・見積・辞書の写しを持つ。"""
    project = _project(db_session)
    creator = _user(db_session, "approver")
    root = _element(db_session, project)
    wp = _element(db_session, project, parent=root, code="1.1", kind="WORK_PACKAGE", own_estimate_hours=Decimal("8"))
    baseline = _baseline(db_session, project, creator)
    dictionary = {"deliverable": "設計書", "work_scope": None, "completion_criteria": "承認済み", "assumptions": None}
    rows = [
        ScopeBaselineElement(
            scope_baseline_id=baseline.id, element_id=root.id, wbs_code="1.0", kind="DELIVERABLE", name="成果物"
        ),
        ScopeBaselineElement(
            scope_baseline_id=baseline.id, element_id=wp.id, parent_element_id=root.id, wbs_code="1.1",
            kind="WORK_PACKAGE", name="設計", own_estimate_hours=Decimal("8.00"), dictionary=dictionary,
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.scalars(
        sa.select(ScopeBaselineElement).where(ScopeBaselineElement.scope_baseline_id == baseline.id).order_by(ScopeBaselineElement.id)
    ).all()
    assert [(r.element_id, r.parent_element_id, r.wbs_code, r.kind, r.name) for r in loaded] == [
        (root.id, None, "1.0", "DELIVERABLE", "成果物"),
        (wp.id, root.id, "1.1", "WORK_PACKAGE", "設計"),
    ]
    assert (loaded[0].own_estimate_hours, loaded[0].dictionary) == (None, None)
    assert (loaded[1].own_estimate_hours, loaded[1].dictionary) == (Decimal("8.00"), dictionary)
    # 辞書の無い要素は SQL の NULL（JSON の null ではない）
    assert db_session.execute(
        sa.text("SELECT count(*) FROM scope_baseline_elements WHERE dictionary IS NULL")
    ).scalar_one() == 1


def test_scope_baseline_elements_keep_snapshot_when_element_changes(db_session: Session) -> None:
    """DB-WBS-010 scope_baseline_elements：基準の後に要素を変え・改番しても、写しの値は変わらない（WBS-22）。"""
    project = _project(db_session)
    creator = _user(db_session, "approver")
    element = _element(db_session, project, own_estimate_hours=Decimal("5"))
    baseline = _baseline(db_session, project, creator)
    snapshot = ScopeBaselineElement(
        scope_baseline_id=baseline.id, element_id=element.id, wbs_code="1.0", kind="DELIVERABLE", name="要素 1.0",
        own_estimate_hours=Decimal("5"),
    )
    db_session.add(snapshot)
    db_session.commit()

    element.name = "変えた名称"
    element.wbs_code = "2.0"
    element.own_estimate_hours = Decimal("9")
    db_session.commit()
    db_session.expunge_all()
    loaded = db_session.get(ScopeBaselineElement, snapshot.id)
    assert loaded is not None
    assert (loaded.wbs_code, loaded.name, loaded.own_estimate_hours) == ("1.0", "要素 1.0", Decimal("5.00"))


def test_scope_baseline_elements_keep_values_after_element_is_deleted(db_session: Session) -> None:
    """DB-WBS-010 scope_baseline_elements：写しの要素の列は外部キーを張らない写しの値（設計書 1.1 の 4.2。CR-002）。

    版に入った要素を消しても写しは変わらない（WBS-22）。消した要素の内部キーは再利用しない（AUTOINCREMENT）。
    """
    project = _project(db_session)
    creator = _user(db_session, "approver")
    parent = _element(db_session, project)
    element = _element(db_session, project, parent=parent, code="1.1")
    baseline = _baseline(db_session, project, creator)
    db_session.add(
        ScopeBaselineElement(scope_baseline_id=baseline.id, element_id=element.id, parent_element_id=parent.id,
                             wbs_code="1.1", kind="DELIVERABLE", name="x")
    )
    db_session.commit()
    removed_ids = (parent.id, element.id)
    db_session.delete(element)
    db_session.flush()
    db_session.delete(parent)
    db_session.commit()

    copy = db_session.scalar(sa.select(ScopeBaselineElement))
    assert copy is not None and (copy.element_id, copy.parent_element_id) == (removed_ids[1], removed_ids[0])
    again = _element(db_session, project, code="2.0", seq=2)
    assert again.id > max(removed_ids)


@pytest.mark.parametrize(
    "override",
    [
        {"kind": "PROJECT"},
        {"own_estimate_hours": Decimal("-1")},
        {"scope_baseline_id": 9999},
        {"element_id": None},
        {"wbs_code": None},
        {"name": None},
    ],
    ids=["kind", "negative-hours", "no-baseline", "no-element", "no-code", "no-name"],
)
def test_scope_baseline_elements_rejections(db_session: Session, override: dict[str, object]) -> None:
    """DB-WBS-010 scope_baseline_elements：4 値以外の区分、負の見積、無い版、要素の ID・WBS ID・名称の無い写しは DB が拒む。

    写した要素・親の ID は今は無い要素を指してよい（外部キーを張らない。設計書 1.1 の 4.2）。
    """
    project = _project(db_session)
    creator = _user(db_session, "approver")
    element = _element(db_session, project)
    baseline = _baseline(db_session, project, creator)
    values: dict[str, object] = {
        "scope_baseline_id": baseline.id, "element_id": element.id, "wbs_code": "1.0", "kind": "DELIVERABLE", "name": "x",
    }
    values.update(override)
    db_session.add(ScopeBaselineElement(**values))
    _flush_fails(db_session)


# ---------------------------------------------------------------------------
# 移行
# ---------------------------------------------------------------------------


def _upgrade(url: str, revision: str = "head") -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, revision)


def _downgrade(url: str, revision: str) -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.downgrade(config, revision)


def test_migration_creates_wbs_tables_matching_models(tmp_path: Path) -> None:
    """alembic upgrade head の結果に DB-WBS-001〜010 のテーブルがあり、モデルとの差が無い。0002 へ戻すと消え、また作れる。"""
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    _upgrade(url)

    engine = create_db_engine(url)
    try:
        assert set(COLUMNS_4_1) <= set(sa.inspect(engine).get_table_names())
        with engine.connect() as connection:
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
        assert diff == []
    finally:
        engine.dispose()

    _downgrade(url, "0002_prj")
    engine = create_db_engine(url)
    try:
        assert not set(COLUMNS_4_1) & set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()
    _upgrade(url)


def test_migration_fills_top_last_child_seq_from_existing_first_level(tmp_path: Path) -> None:
    """0009（CR-003）：既にある採番規則の top_last_child_seq を、第 1 階層の要素の順番の最大で埋める。戻すと列が消える。"""
    url = f"sqlite:///{tmp_path / 'top_seq.db'}"
    _upgrade(url, "0008_est")
    engine = create_db_engine(url)
    try:
        with Session(engine) as session:
            filled = _project(session, "P-001")
            empty = _project(session, "P-002")
            for project in (filled, empty):
                session.execute(sa.text("INSERT INTO wbs_numbering_rules (project_id) VALUES (:p)"), {"p": project.id})
            insert = sa.text(
                "INSERT INTO wbs_elements (project_id, parent_id, kind, name, wbs_code, sibling_seq, sort_order) "
                "VALUES (:p, :parent, 'DELIVERABLE', 'x', :code, :seq, :seq)"
            )
            session.execute(insert, {"p": filled.id, "parent": None, "code": "1.0", "seq": 1})
            session.execute(insert, {"p": filled.id, "parent": None, "code": "3.0", "seq": 3})
            parent_id = session.scalar(sa.text("SELECT id FROM wbs_elements WHERE wbs_code = '1.0'"))
            # 第 2 階層の順番は数えない
            session.execute(insert, {"p": filled.id, "parent": parent_id, "code": "1.5", "seq": 5})
            session.commit()
            ids = (filled.id, empty.id)
    finally:
        engine.dispose()

    _upgrade(url)
    engine = create_db_engine(url)
    try:
        with engine.connect() as connection:
            rows = dict(connection.execute(sa.text("SELECT project_id, top_last_child_seq FROM wbs_numbering_rules")).all())
        assert rows == {ids[0]: 3, ids[1]: 0}
    finally:
        engine.dispose()

    _downgrade(url, "0008_est")
    engine = create_db_engine(url)
    try:
        columns = {c["name"] for c in sa.inspect(engine).get_columns("wbs_numbering_rules")}
        assert "top_last_child_seq" not in columns
    finally:
        engine.dispose()
