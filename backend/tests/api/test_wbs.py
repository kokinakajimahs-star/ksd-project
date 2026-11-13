"""WBS の API の試験（設計書 6.2 の API-WBS-001〜023、5.2・5.3・5.10、9 章）。

SQLite の一時ファイルの DB と TestClient で、正常系と 401・403・404・409・422・400 のうち該当するものを確かめる。
採番は要求仕様書 W§4 の例（1.0、1.1、1.2、2.0、2.1）を API で作って確かめる（5.2）。
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.domain.estimate import ActivityEstimate
from app.domain.wbs_rules import (
    AssignmentRef,
    ChangeRequestRef,
    CorrectiveActionRef,
    EstimateRecordRef,
    EstimateTarget,
    TimeEntryRef,
)
from app.domain.workflow import ApprovalStatus
from app.infra.db import Base
from app.infra.security import generate_token, hash_token
from app.models.cmn import AuthSession, RolePermission, User
from app.models.est import EstimateRecord
from app.models.prj import Project
from app.models.sch import Activity
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
from app.services.clock import get_now
from app.services.project_access import ProjectMembersTable
from app.services.wbs import (
    ActivitiesTableAbsent,
    ChangeRequestsTableAbsent,
    ChangeRequestTransitionNotWired,
    CrossGroupReaders,
    eac_not_available,
)

if TYPE_CHECKING:
    from conftest import RaceProbe

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
MISSING = 9999


@pytest.fixture(autouse=True)
def fixed_now(app: FastAPI) -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)


def make_user(session: Session, login_id: str, *, is_system_admin: bool = False) -> User:
    user = User(login_id=login_id, display_name=login_id, password_hash="unusable", is_system_admin=is_system_admin,
                is_active=True)
    session.add(user)
    session.commit()
    return user


def login(session: Session, user: User) -> dict[str, str]:
    token = generate_token()
    session.add(AuthSession(user_id=user.id, token_hash=hash_token(token), expires_at=NOW + timedelta(hours=12),
                            created_at=NOW))
    session.commit()
    return {"Authorization": f"Bearer {token}"}


class World:
    """管理者が作ったプロジェクトと、PM・PLANNER・VIEWER・CONTRACTOR・メンバーでない利用者。"""

    def __init__(self, client: TestClient, session: Session) -> None:
        self.client = client
        self.session = session
        self.admin = make_user(session, "admin", is_system_admin=True)
        self.admin_h = login(session, self.admin)
        body = {"code": "PRJ-W", "name": "WBS", "start_date": "2026-12-01", "owner_user_id": self.admin.id}
        response = client.post("/api/v1/projects", json=body, headers=self.admin_h)
        assert response.status_code == 201, response.text
        self.pid: int = response.json()["id"]
        self.headers: dict[str, dict[str, str]] = {}
        self.users: dict[str, User] = {}
        for login_id, role in (("pm", "PM"), ("planner", "PLANNER"), ("viewer", "VIEWER"), ("contractor", "CONTRACTOR")):
            user = make_user(session, login_id)
            put = client.put(f"/api/v1/projects/{self.pid}/members/{user.id}", json={"roles": [role]},
                             headers=self.admin_h)
            assert put.status_code == 200, put.text
            self.users[login_id] = user
            self.headers[login_id] = login(session, user)
        self.users["outsider"] = make_user(session, "outsider")
        self.headers["outsider"] = login(session, self.users["outsider"])

    def url(self, path: str = "") -> str:
        return f"/api/v1/projects/{self.pid}/wbs{path}"

    def add(self, name: str, parent_id: int | None = None, kind: str = "DELIVERABLE", **extra: Any) -> dict[str, Any]:
        response = self.client.post(self.url("/elements"), json={"parent_id": parent_id, "kind": kind, "name": name,
                                                                  **extra}, headers=self.headers["planner"])
        assert response.status_code == 201, response.text
        element: dict[str, Any] = response.json()
        return element

    def tree(self, who: str = "viewer") -> dict[str, Any]:
        response = self.client.get(self.url(), headers=self.headers[who])
        assert response.status_code == 200, response.text
        tree: dict[str, Any] = response.json()
        return tree

    def set_status(self, status: str) -> None:
        self.session.expire_all()
        project = self.session.get(Project, self.pid)
        assert project is not None
        project.wbs_status = status
        self.session.commit()


@pytest.fixture()
def world(client: TestClient, db_session: Session) -> World:
    return World(client, db_session)


def codes(tree: dict[str, Any]) -> list[str]:
    return [element["wbs_code"] for element in tree["elements"]]


# ---------------------------------------------------------------------------
# API-WBS-001・002：木と採番
# ---------------------------------------------------------------------------


def test_add_elements_numbers_like_w4_example_and_tree_is_preorder(world: World) -> None:
    """API-WBS-002 要素を足す（採番する）・API-WBS-001 WBSの木：既定の規則で W§4 の例 1.0・1.1・1.2・2.0・2.1 になる（5.2）。"""
    first = world.add("要件定義")
    second = world.add("設計")
    a = world.add("要件の聞き取り", first["id"], kind="WORK_PACKAGE", own_estimate_hours="16")
    world.add("要件定義書", first["id"], kind="WORK_PACKAGE", own_estimate_hours="8.5")
    world.add("基本設計", second["id"], kind="WORK_PACKAGE")
    assert (first["wbs_code"], first["sibling_seq"], first["depth"]) == ("1.0", 1, 1)
    assert (a["wbs_code"], a["depth"], a["rollup_hours"]) == ("1.1", 2, 16.0)

    tree = world.tree()
    assert codes(tree) == ["1.0", "1.1", "1.2", "2.0", "2.1"]
    assert tree["wbs_status"] == "DRAFT"
    assert tree["numbering_rule"] == {"separator": ".", "min_digits": 1, "prefix": "", "pad_first_level": True}
    by_code = {e["wbs_code"]: e for e in tree["elements"]}
    assert by_code["1.0"]["rollup_hours"] == 24.5 and by_code["1.0"]["rollup_source"] == "CHILDREN"
    assert by_code["1.2"]["rollup_source"] == "OWN_ESTIMATE"
    assert tree["project_estimate_hours"] == 24.5
    assert tree["renumber_pending"] == 0
    # WP の成果物・完了条件が空（RULE-WBS-005）の 3 件
    assert tree["validation"] == {"errors": 0, "warnings": 0, "missing_completion": 3}
    assert tree["restricted"] is False


def test_deleted_number_is_not_reused_under_parent(world: World) -> None:
    """API-WBS-002：順番は親の last_child_seq + 1。消した番号は再利用しない（5.2）。"""
    parent = world.add("成果物")
    world.add("A", parent["id"])
    b = world.add("B", parent["id"])
    assert world.client.delete(world.url(f"/elements/{b['id']}"), headers=world.headers["planner"]).status_code == 204
    c = world.add("C", parent["id"])
    assert c["wbs_code"] == "1.3"


def test_deleted_number_is_not_reused_at_first_level(world: World) -> None:
    """API-WBS-002・003：第 1 階層も消した番号を再利用しない（設計書 1.1 の 4.1・5.2：top_last_child_seq。CR-003）。

    順番が最大の第 1 階層の要素を消しても次は 3.0。第 1 階層へ移した要素も数えを進め、消しても番号を戻さない。
    """
    world.add("A")
    b = world.add("B")
    assert world.client.delete(world.url(f"/elements/{b['id']}"), headers=world.headers["planner"]).status_code == 204
    c = world.add("C")
    assert (c["wbs_code"], c["sibling_seq"]) == ("3.0", 3)

    child = world.add("子", c["id"])
    response = world.client.post(world.url(f"/elements/{child['id']}/move"), json={"parent_id": None},
                                 headers=world.headers["planner"])
    assert response.status_code == 200, response.text
    assert response.json()["sibling_seq"] == 4
    assert world.client.delete(world.url(f"/elements/{child['id']}"), headers=world.headers["planner"]).status_code == 204
    d = world.add("D")
    assert (d["wbs_code"], d["sibling_seq"]) == ("5.0", 5)

    # 改番は兄弟を 1 から振り直すが、数えは減らさない（BR-02）
    assert world.client.post(world.url("/renumber"), headers=world.headers["planner"]).status_code == 200
    assert codes(world.tree()) == ["1.0", "2.0", "3.0"]
    e = world.add("E")
    assert (e["wbs_code"], e["sibling_seq"]) == ("6.0", 6)


def test_add_element_rejections(world: World) -> None:
    """API-WBS-002：WP の下は 422、無い親・メンバーでない責任者は 400、権限なしは 403、認証なしは 401。"""
    wp = world.add("WP", kind="WORK_PACKAGE")
    body = {"parent_id": wp["id"], "kind": "WORK_PACKAGE", "name": "子"}
    response = world.client.post(world.url("/elements"), json=body, headers=world.headers["planner"])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "WORK_PACKAGE_HAS_CHILDREN"
    body = {"parent_id": MISSING, "kind": "DELIVERABLE", "name": "x"}
    assert world.client.post(world.url("/elements"), json=body, headers=world.headers["planner"]).status_code == 400
    body = {"kind": "DELIVERABLE", "name": "x", "responsible_user_id": world.users["outsider"].id}
    assert world.client.post(world.url("/elements"), json=body, headers=world.headers["planner"]).status_code == 400
    body = {"kind": "BAD", "name": "x"}
    assert world.client.post(world.url("/elements"), json=body, headers=world.headers["planner"]).status_code == 400
    body = {"kind": "DELIVERABLE", "name": "x"}
    assert world.client.post(world.url("/elements"), json=body, headers=world.headers["viewer"]).status_code == 403
    assert world.client.post(world.url("/elements"), json=body).status_code == 401
    missing = f"/api/v1/projects/{MISSING}/wbs/elements"
    assert world.client.post(missing, json=body, headers=world.headers["planner"]).status_code == 404


def test_edit_while_pending_approval_is_409_and_baselined_returns_to_draft(world: World) -> None:
    """API-WBS-002：承認待ちの間の変更は 409 INVALID_STATE、基準化の後の変更は DRAFT に戻る（RULE-WBS-006・5.10）。"""
    world.set_status("PENDING_APPROVAL")
    body = {"kind": "DELIVERABLE", "name": "x"}
    response = world.client.post(world.url("/elements"), json=body, headers=world.headers["planner"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE"
    world.set_status("BASELINED")
    world.add("基準の後")
    assert world.tree()["wbs_status"] == "DRAFT"


def test_tree_for_contractor_is_restricted_and_outsider_is_forbidden(world: World) -> None:
    """API-WBS-001：委託先は自分の割当の経路だけ（活動の表が無い間は空）。メンバーでない利用者は 403、無いプロジェクトは 404。"""
    world.add("要件定義")
    tree = world.tree("contractor")
    assert tree["restricted"] is True and tree["elements"] == []
    assert world.client.get(world.url(), headers=world.headers["outsider"]).status_code == 403
    assert world.client.get(f"/api/v1/projects/{MISSING}/wbs", headers=world.headers["viewer"]).status_code == 404


# ---------------------------------------------------------------------------
# API-WBS-003：要素を変える
# ---------------------------------------------------------------------------


def test_patch_element_changes_only_given_fields(world: World) -> None:
    """API-WBS-003 要素の名称・区分・責任者・自分の見積を変える：載せた項目だけ。WBS ID は変えない。"""
    element = world.add("旧名", own_estimate_hours="5")
    pm = world.users["pm"].id
    response = world.client.patch(world.url(f"/elements/{element['id']}"),
                                  json={"name": "新名", "responsible_user_id": pm, "responsible_team": "基盤"},
                                  headers=world.headers["planner"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["name"], body["responsible_user_id"], body["responsible_team"]) == ("新名", pm, "基盤")
    assert (body["wbs_code"], body["own_estimate_hours"], body["kind"]) == ("1.0", 5.0, "DELIVERABLE")


def test_patch_element_rejections(world: World) -> None:
    """API-WBS-003：子のある要素を WP にするのは 422、名称の null は 400、無い要素は 404、権限なしは 403。"""
    parent = world.add("親")
    world.add("子", parent["id"])
    url = world.url(f"/elements/{parent['id']}")
    response = world.client.patch(url, json={"kind": "WORK_PACKAGE"}, headers=world.headers["planner"])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "WORK_PACKAGE_HAS_CHILDREN"
    assert world.client.patch(url, json={"name": None}, headers=world.headers["planner"]).status_code == 400
    assert world.client.patch(world.url(f"/elements/{MISSING}"), json={"name": "x"},
                              headers=world.headers["planner"]).status_code == 404
    assert world.client.patch(url, json={"name": "x"}, headers=world.headers["viewer"]).status_code == 403


def test_own_estimate_on_add_and_patch_leaves_estimate_records(world: World) -> None:
    """API-WBS-002・003：自分の見積を置いた・変えたら、API-EST-001 と同じ形の見積の記録を 1 行残す（EST-08・CR-017）。

    単位は人時（入力の値＝人時）、手法・前提・参照資料は空、入力した人と日時を残す。見積を置かない追加・同じ値・見積以外の変更・
    空（null）にする変更は残さない（記録の ``hours`` は空にできない。4.1）。拒まれた書き込みも残さない。
    """
    def history(element_id: int) -> list[dict[str, Any]]:
        response = world.client.get(f"/api/v1/projects/{world.pid}/estimates/ELEMENT/{element_id}/history",
                                    headers=world.headers["viewer"])
        assert response.status_code == 200, response.text
        items: list[dict[str, Any]] = response.json()["items"]
        return items

    def patch(element_id: int, body: dict[str, Any], expect: int = 200) -> Any:
        response = world.client.patch(world.url(f"/elements/{element_id}"), json=body, headers=world.headers["planner"])
        assert response.status_code == expect, response.text
        return response.json()

    planner = world.users["planner"].id
    entered_at = NOW.isoformat().replace("+00:00", "Z")
    element = world.add("見積あり", own_estimate_hours="12.5")
    assert [(r["target_type"], r["target_id"], r["hours"], r["input_value"], r["input_unit"], r["method"],
             r["assumptions"], r["references"], r["entered_by"], r["entered_at"]) for r in history(element["id"])] == [
        ("ELEMENT", element["id"], 12.5, 12.5, "HOUR", None, None, None, planner, entered_at)]
    bare = world.add("見積なし")
    assert history(bare["id"]) == []

    patch(element["id"], {"own_estimate_hours": "12.50"})  # 同じ値
    patch(element["id"], {"name": "見積あり（改名）"})  # 見積以外
    assert len(history(element["id"])) == 1
    assert patch(element["id"], {"own_estimate_hours": "20"})["own_estimate_hours"] == 20.0
    assert [(r["hours"], r["input_unit"]) for r in history(element["id"])] == [(12.5, "HOUR"), (20.0, "HOUR")]
    assert patch(element["id"], {"own_estimate_hours": None})["own_estimate_hours"] is None  # 空にする
    assert len(history(element["id"])) == 2
    patch(element["id"], {"own_estimate_hours": "8"})  # 空から置き直す
    assert [r["hours"] for r in history(element["id"])] == [12.5, 20.0, 8.0]
    patch(bare["id"], {"own_estimate_hours": "3", "name": "見積を後で置く"})
    assert [(r["hours"], r["entered_by"]) for r in history(bare["id"])] == [(3.0, planner)]

    # 承認待ちの間は 409 で、要素も記録も書かない（RULE-WBS-006）
    world.set_status("PENDING_APPROVAL")
    patch(element["id"], {"own_estimate_hours": "30"}, expect=409)
    refused = world.client.post(world.url("/elements"), json={"kind": "DELIVERABLE", "name": "拒む", "own_estimate_hours": "1"},
                                headers=world.headers["planner"])
    assert refused.status_code == 409, refused.text
    assert [r["hours"] for r in history(element["id"])] == [12.5, 20.0, 8.0]
    world.session.expire_all()
    assert world.session.scalar(select(func.count()).select_from(EstimateRecord)) == 4


# ---------------------------------------------------------------------------
# API-WBS-004・007・008：移動・改番・新旧対応
# ---------------------------------------------------------------------------


def test_move_keeps_code_marks_renumber_then_renumber_records_history(world: World) -> None:
    """API-WBS-004 要素を移動する（IDは変えない）→ API-WBS-007 改番する → API-WBS-008 WBS IDの新旧対応の一覧（5.2）。"""
    first = world.add("要件定義")
    second = world.add("設計")
    moved = world.add("聞き取り", first["id"], kind="WORK_PACKAGE")
    world.add("基本設計", second["id"], kind="WORK_PACKAGE")
    response = world.client.post(world.url(f"/elements/{moved['id']}/move"), json={"parent_id": second["id"],
                                                                                    "position": 0},
                                 headers=world.headers["planner"])
    assert response.status_code == 200, response.text
    assert (response.json()["wbs_code"], response.json()["needs_renumber"]) == ("1.1", True)
    tree = world.tree()
    assert codes(tree) == ["1.0", "2.0", "1.1", "2.1"]
    assert tree["renumber_pending"] == 1

    response = world.client.post(world.url("/renumber"), headers=world.headers["planner"])
    assert response.status_code == 200, response.text
    assert response.json()["changes"] == [
        {"element_id": moved["id"], "old_code": "1.1", "new_code": "2.1"},
        {"element_id": world.tree()["elements"][3]["id"], "old_code": "2.1", "new_code": "2.2"},
    ]
    tree = world.tree()
    assert codes(tree) == ["1.0", "2.0", "2.1", "2.2"]
    assert tree["renumber_pending"] == 0

    history = world.client.get(world.url("/code-history"), headers=world.headers["viewer"])
    assert history.status_code == 200
    items = history.json()["items"]
    assert history.json()["total"] == 2
    assert {(i["element_id"], i["old_code"], i["new_code"], i["current_code"]) for i in items} == {
        (moved["id"], "1.1", "2.1", "2.1"),
        (tree["elements"][3]["id"], "2.1", "2.2", "2.2"),
    }
    assert all(i["renumbered_by"] == world.users["planner"].id for i in items)
    # 何も変わらない改番は書かない
    again = world.client.post(world.url("/renumber"), headers=world.headers["planner"])
    assert again.json() == {"renumbered_at": None, "changes": []}


def test_move_rejections(world: World) -> None:
    """API-WBS-004：自分の配下へは 422 PARENT_CYCLE、WP の下へは 422、無い移動先は 400、権限なしは 403。"""
    parent = world.add("親")
    child = world.add("子", parent["id"])
    wp = world.add("WP", kind="WORK_PACKAGE")
    url = world.url(f"/elements/{parent['id']}/move")
    response = world.client.post(url, json={"parent_id": child["id"]}, headers=world.headers["planner"])
    assert (response.status_code, response.json()["error"]["code"]) == (422, "PARENT_CYCLE")
    response = world.client.post(url, json={"parent_id": wp["id"]}, headers=world.headers["planner"])
    assert (response.status_code, response.json()["error"]["code"]) == (422, "WORK_PACKAGE_HAS_CHILDREN")
    assert world.client.post(url, json={"parent_id": MISSING}, headers=world.headers["planner"]).status_code == 400
    assert world.client.post(url, json={"parent_id": None}, headers=world.headers["viewer"]).status_code == 403


def test_renumber_with_new_rule_uses_new_rule(world: World) -> None:
    """API-WBS-006 採番規則を変える：今の ID は変えず、改番で新しい規則の ID になる（WBS-05）。"""
    first = world.add("A")
    world.add("A-1", first["id"])
    rule = {"separator": "-", "min_digits": 2, "prefix": "P", "pad_first_level": False}
    response = world.client.put(world.url("/numbering-rule"), json=rule, headers=world.headers["planner"])
    assert response.status_code == 200 and response.json() == rule
    assert codes(world.tree()) == ["1.0", "1.1"]
    world.client.post(world.url("/renumber"), headers=world.headers["planner"])
    assert codes(world.tree()) == ["P01", "P01-01"]
    assert world.add("B")["wbs_code"] == "P02"


def test_numbering_rule_rejections(world: World) -> None:
    """API-WBS-006：数字・「/」の区切り文字、0 桁は 400、権限なしは 403。"""
    base = {"separator": ".", "min_digits": 1, "prefix": "", "pad_first_level": True}
    for bad in ({"separator": "1"}, {"separator": "/"}, {"prefix": "a/b"}, {"min_digits": 0}):
        response = world.client.put(world.url("/numbering-rule"), json={**base, **bad}, headers=world.headers["planner"])
        assert response.status_code == 400, bad
    assert world.client.put(world.url("/numbering-rule"), json=base, headers=world.headers["viewer"]).status_code == 403


# ---------------------------------------------------------------------------
# API-WBS-005：配下ごと消す
# ---------------------------------------------------------------------------


def test_delete_removes_subtree_and_related_rows(world: World, db_session: Session) -> None:
    """API-WBS-005 要素を配下ごと消す：辞書・紐付け・新旧対応を消し、指摘は要素を空にして残す。他の ID は変えない。"""
    parent = world.add("消す")
    child = world.add("子", parent["id"], kind="WORK_PACKAGE")
    keep = world.add("残す")
    items = world.client.put(f"/api/v1/projects/{world.pid}/scope-items", json={"items": [{"kind": "IN", "title": "t"}]},
                             headers=world.headers["pm"]).json()["items"]
    world.client.put(world.url(f"/elements/{child['id']}/scope-links"), json={"scope_item_ids": [items[0]["id"]]},
                     headers=world.headers["planner"])
    world.client.put(world.url(f"/elements/{child['id']}/dictionary"), json={"deliverable": "d"},
                     headers=world.headers["planner"])
    review = WbsReview(project_id=world.pid, requested_by=world.users["planner"].id)
    db_session.add(review)
    db_session.flush()
    db_session.add(WbsReviewComment(review_id=review.id, element_id=child["id"], category="OTHER", body="b",
                                    author_id=world.users["pm"].id))
    db_session.add(WbsCodeHistory(project_id=world.pid, element_id=child["id"], old_code="9.9", new_code="1.1",
                                  renumbered_at=NOW, renumbered_by=world.users["pm"].id))
    db_session.commit()

    response = world.client.delete(world.url(f"/elements/{parent['id']}"), headers=world.headers["planner"])
    assert response.status_code == 204, response.text
    db_session.expire_all()
    assert [e.id for e in db_session.scalars(select(WbsElement))] == [keep["id"]]
    assert codes(world.tree()) == ["2.0"]
    assert db_session.scalars(select(WbsScopeLink)).all() == []
    assert db_session.scalars(select(WbsDictionaryEntry)).all() == []
    assert db_session.scalars(select(WbsCodeHistory)).all() == []
    assert [c.element_id for c in db_session.scalars(select(WbsReviewComment))] == [None]
    assert world.client.delete(world.url(f"/elements/{parent['id']}"), headers=world.headers["planner"]).status_code == 404
    assert world.client.delete(world.url(f"/elements/{keep['id']}"), headers=world.headers["viewer"]).status_code == 403


def test_delete_records_audit_logs_for_bulk_statements(world: World, db_session: Session) -> None:
    """API-WBS-005 の削除も変更履歴に残る（6.1・NFR-05）：一括の DELETE・UPDATE で消した要素・紐付け・指摘の行ごとに記録する。"""
    from app.models.chg import AuditLog

    parent = world.add("消す")
    child = world.add("子", parent["id"], kind="WORK_PACKAGE")
    review = WbsReview(project_id=world.pid, requested_by=world.users["planner"].id)
    db_session.add(review)
    db_session.flush()
    comment = WbsReviewComment(review_id=review.id, element_id=child["id"], category="OTHER", body="b",
                               author_id=world.users["pm"].id)
    db_session.add(comment)
    db_session.commit()

    response = world.client.delete(world.url(f"/elements/{parent['id']}"), headers=world.headers["planner"])
    assert response.status_code == 204, response.text
    db_session.expire_all()
    deleted = db_session.scalars(
        select(AuditLog).where(AuditLog.entity_type == "wbs_elements", AuditLog.action == "DELETE")
    ).all()
    assert sorted(r.entity_id for r in deleted) == sorted([parent["id"], child["id"]])
    by_id = {r.entity_id: r for r in deleted}
    assert by_id[child["id"]].before is not None and by_id[child["id"]].before["name"] == "子"
    assert by_id[child["id"]].after is None
    assert by_id[child["id"]].wbs_code == child["wbs_code"]
    assert all(r.user_id == world.users["planner"].id and r.project_id == world.pid for r in deleted)
    nulled = db_session.scalars(
        select(AuditLog).where(AuditLog.entity_type == "wbs_review_comments", AuditLog.entity_id == comment.id,
                               AuditLog.action == "UPDATE")
    ).one()
    assert nulled.before is not None and nulled.after is not None
    assert (nulled.before["element_id"], nulled.after["element_id"]) == (child["id"], None)


def test_delete_baselined_element_keeps_baseline_copy(world: World, db_session: Session) -> None:
    """API-WBS-005：スコープベースラインの版に入った要素も消せ、版の写しは変わらない（設計書 1.1 の 4.2・5.10。CR-002。WBS-22）。"""
    element = world.add("基準の要素")
    baseline = ScopeBaseline(project_id=world.pid, version=1, reason="INITIAL_APPROVAL", created_by=world.admin.id)
    db_session.add(baseline)
    db_session.flush()
    copy = ScopeBaselineElement(scope_baseline_id=baseline.id, element_id=element["id"], wbs_code="1.0",
                                kind="DELIVERABLE", name="基準の要素")
    db_session.add(copy)
    db_session.commit()
    copy_id = copy.id
    response = world.client.delete(world.url(f"/elements/{element['id']}"), headers=world.headers["planner"])
    assert response.status_code == 204, response.text
    db_session.expire_all()
    assert db_session.get(WbsElement, element["id"]) is None
    kept = db_session.get(ScopeBaselineElement, copy_id)
    assert kept is not None and (kept.element_id, kept.wbs_code, kept.name) == (element["id"], "1.0", "基準の要素")
    # 消した要素の内部キーは再利用しない（後から足した要素を写しが指さない）
    assert world.add("後から足した要素")["id"] > element["id"]


# ---------------------------------------------------------------------------
# API-WBS-009・010：辞書
# ---------------------------------------------------------------------------


def test_dictionary_read_and_write(world: World) -> None:
    """API-WBS-009 WBS辞書を読む・API-WBS-010 WBS辞書を書く：要素の値と 4 項目。書く前は空。"""
    wp = world.add("WP", kind="WORK_PACKAGE", own_estimate_hours="3")
    url = world.url(f"/elements/{wp['id']}/dictionary")
    empty = world.client.get(url, headers=world.headers["viewer"])
    assert empty.status_code == 200
    assert (empty.json()["wbs_code"], empty.json()["deliverable"], empty.json()["own_estimate_hours"]) == ("1.0", None, 3.0)
    body = {"deliverable": "仕様書", "work_scope": "範囲", "completion_criteria": "承認済み", "assumptions": "前提"}
    put = world.client.put(url, json=body, headers=world.headers["planner"])
    assert put.status_code == 200, put.text
    got = world.client.get(url, headers=world.headers["viewer"]).json()
    assert {k: got[k] for k in body} == body
    assert world.tree()["validation"]["missing_completion"] == 0
    assert world.client.put(url, json=body, headers=world.headers["viewer"]).status_code == 403
    assert world.client.get(world.url(f"/elements/{MISSING}/dictionary"), headers=world.headers["viewer"]).status_code == 404
    assert world.client.get(url, headers=world.headers["contractor"]).status_code == 404


# ---------------------------------------------------------------------------
# API-WBS-012・013：検査・スコープの紐付け
# ---------------------------------------------------------------------------


def test_validation_reports_hundred_percent_and_missing_completion(world: World) -> None:
    """API-WBS-012 100%ルールと完了条件の検査の結果・API-WBS-013 要素とスコープ項目の紐付けを置き換える（5.3）。"""
    parent = world.add("親", own_estimate_hours="10")
    wp = world.add("WP", parent["id"], kind="WORK_PACKAGE", own_estimate_hours="12")
    items = world.client.put(
        f"/api/v1/projects/{world.pid}/scope-items",
        json={"items": [{"kind": "IN", "title": "対象"}, {"kind": "OUT", "title": "対象外"}]},
        headers=world.headers["pm"],
    ).json()["items"]
    response = world.client.put(world.url(f"/elements/{wp['id']}/scope-links"),
                                json={"scope_item_ids": [items[1]["id"]]}, headers=world.headers["planner"])
    assert response.status_code == 200
    assert response.json() == {"element_id": wp["id"], "scope_item_ids": [items[1]["id"]]}

    result = world.client.get(world.url("/validation"), headers=world.headers["viewer"])
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["tolerance"] == 0.0
    assert [(f["level"], f["code"], f["element_id"], f["scope_item_id"]) for f in body["findings"]] == [
        ("WARNING", "SUM_MISMATCH", parent["id"], None),
        ("ERROR", "OUT_OF_SCOPE_LINKED", wp["id"], items[1]["id"]),
        ("WARNING", "IN_SCOPE_UNCOVERED", None, items[0]["id"]),
    ]
    assert body["findings"][0]["own_estimate_hours"] == 10.0 and body["findings"][0]["lower_hours"] == 12.0
    assert body["counts"] == {"errors": 1, "warnings": 2, "missing_completion": 1}
    assert body["missing_completion"] == [
        {"element_id": wp["id"], "wbs_code": "1.1", "missing": ["deliverable", "completion_criteria"]}
    ]
    assert world.client.get(world.url("/validation"), headers=world.headers["outsider"]).status_code == 403


def test_scope_links_rejections(world: World) -> None:
    """API-WBS-013：無い項目・重複は 400、無い要素は 404、権限なしは 403。"""
    element = world.add("要素")
    url = world.url(f"/elements/{element['id']}/scope-links")
    assert world.client.put(url, json={"scope_item_ids": [MISSING]}, headers=world.headers["planner"]).status_code == 400
    items = world.client.put(f"/api/v1/projects/{world.pid}/scope-items", json={"items": [{"kind": "IN", "title": "t"}]},
                             headers=world.headers["pm"]).json()["items"]
    twice = {"scope_item_ids": [items[0]["id"], items[0]["id"]]}
    assert world.client.put(url, json=twice, headers=world.headers["planner"]).status_code == 400
    assert world.client.put(world.url(f"/elements/{MISSING}/scope-links"), json={"scope_item_ids": []},
                            headers=world.headers["planner"]).status_code == 404
    assert world.client.put(url, json={"scope_item_ids": []}, headers=world.headers["viewer"]).status_code == 403


def test_get_scope_links_reads_current_links(world: World, app: FastAPI) -> None:
    """API-WBS-026 要素とスコープ項目の紐付けを読む（設計書 1.1・CR-004）：今の紐付けを項目の ID の順で返す。

    権限は project.view。無い要素は 404、委託先には割当の経路の要素だけ（他は 404）、メンバーでない人は 403。
    """
    parent = world.add("設計")
    wp = world.add("基本設計", parent["id"], kind="WORK_PACKAGE")
    other = world.add("管理", kind="WORK_PACKAGE")
    items = world.client.put(f"/api/v1/projects/{world.pid}/scope-items",
                             json={"items": [{"kind": "IN", "title": "a"}, {"kind": "IN", "title": "b"},
                                             {"kind": "OUT", "title": "c"}]},
                             headers=world.headers["pm"]).json()["items"]
    ids = [i["id"] for i in items]
    url = world.url(f"/elements/{wp['id']}/scope-links")
    put = world.client.put(url, json={"scope_item_ids": [ids[2], ids[0]]}, headers=world.headers["planner"])
    assert put.status_code == 200, put.text

    got = world.client.get(url, headers=world.headers["viewer"])
    assert got.status_code == 200, got.text
    assert got.json()["element_id"] == wp["id"]
    assert got.json()["scope_item_ids"] == sorted(put.json()["scope_item_ids"])  # 置き換えた紐付け（項目の ID の順）
    empty = world.client.get(world.url(f"/elements/{other['id']}/scope-links"), headers=world.headers["viewer"])
    assert empty.status_code == 200 and empty.json()["scope_item_ids"] == []
    assert world.client.get(world.url(f"/elements/{MISSING}/scope-links"),
                            headers=world.headers["viewer"]).status_code == 404
    assert world.client.get(url, headers=world.headers["outsider"]).status_code == 403

    # 委託先：割当の活動（101）の WP とその上だけが見える
    contractor = world.users["contractor"].id
    app.state.wbs_activities = FakeActivities({101: wp["id"]})
    app.state.project_access = ProjectMembersTable(assignments=FakeAssigned({contractor: frozenset({101})}))
    assert world.client.get(url, headers=world.headers["contractor"]).json()["scope_item_ids"] == sorted([ids[2], ids[0]])
    assert world.client.get(world.url(f"/elements/{parent['id']}/scope-links"),
                            headers=world.headers["contractor"]).status_code == 200
    hidden = world.client.get(world.url(f"/elements/{other['id']}/scope-links"), headers=world.headers["contractor"])
    assert hidden.status_code == 404


# ---------------------------------------------------------------------------
# 別の群の表を読む口の差し替え忘れ（A-17）
# ---------------------------------------------------------------------------


def test_activities_reader_is_wired_once_activities_table_exists(app: FastAPI) -> None:
    """活動の表（activities）ができたら、アプリは WBS の判定が活動を読む実装を載せていること。"""
    if "activities" in Base.metadata.tables:
        assert not isinstance(app.state.wbs_activities, ActivitiesTableAbsent), (
            "activities ができたので、app.main.create_app の app.state.wbs_activities を活動を読む実装へ差し替える"
        )
    else:
        assert isinstance(app.state.wbs_activities, ActivitiesTableAbsent)


def test_default_activities_reader_reads_activities_table(world: World, db_session: Session) -> None:
    """アプリの既定の ActivitiesTable（A-19）：activities の行を読み、WP の集計（RULE-EST-001）と削除の拒否（OQ-18）に使う。他のプロジェクトの活動は読まない。"""
    wp = world.add("WP", kind="WORK_PACKAGE", own_estimate_hours="5")
    other_wp = world.add("活動の無い WP", kind="WORK_PACKAGE", own_estimate_hours="2")
    db_session.add_all([
        Activity(project_id=world.pid, work_package_id=wp["id"], name="設計", sort_order=1, duration_days=3,
                 estimate_hours=Decimal("3")),
        Activity(project_id=world.pid, work_package_id=wp["id"], name="レビュー", sort_order=2, duration_days=1,
                 estimate_hours=Decimal("4.5")),
    ])
    other_project = Project(code="PRJ-OTHER", name="別", start_date=NOW.date(), owner_user_id=world.admin.id)
    db_session.add(other_project)
    db_session.flush()
    foreign_wp = WbsElement(project_id=other_project.id, kind="WORK_PACKAGE", name="別の WP", wbs_code="1.0",
                            sibling_seq=1, sort_order=1)
    db_session.add(foreign_wp)
    db_session.flush()
    db_session.add(Activity(project_id=other_project.id, work_package_id=foreign_wp.id, name="別の活動", sort_order=1,
                            duration_days=2, estimate_hours=Decimal("100")))
    db_session.commit()

    by_id = {e["id"]: e for e in world.tree()["elements"]}
    assert by_id[wp["id"]]["rollup_hours"] == 7.5
    assert by_id[other_wp["id"]]["rollup_hours"] == 2.0
    assert world.tree()["project_estimate_hours"] == 9.5

    response = world.client.delete(world.url(f"/elements/{wp['id']}"), headers=world.headers["planner"])
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "ELEMENT_HAS_ACTIVITIES"
    assert world.client.delete(world.url(f"/elements/{other_wp['id']}"),
                               headers=world.headers["planner"]).status_code == 204


# ---------------------------------------------------------------------------
# API-WBS-014〜017：レビューと指摘
# ---------------------------------------------------------------------------


def test_review_start_comment_and_update(world: World) -> None:
    """API-WBS-014 レビューを始める・API-WBS-015 レビューと指摘を読む・API-WBS-016 指摘を書く・API-WBS-017 指摘の状態・対応を更新する。"""
    element = world.add("要件定義")
    approver = world.users["pm"].id
    response = world.client.post(world.url("/reviews"), json={"reviewer_ids": [approver]},
                                 headers=world.headers["planner"])
    assert response.status_code == 201, response.text
    review = response.json()
    assert (review["status"], review["reviewer_ids"], review["requested_by"]) == ("OPEN", [approver],
                                                                                 world.users["planner"].id)
    assert world.tree()["wbs_status"] == "IN_REVIEW"
    # レビュー中も WBS は変えられる（IN_REVIEW のまま。5.10）
    world.add("追加")
    assert world.tree()["wbs_status"] == "IN_REVIEW"

    comments_url = world.url(f"/reviews/{review['id']}/comments")
    created = world.client.post(comments_url, json={"element_id": element["id"], "category": "OMISSION", "body": "漏れ"},
                                headers=world.headers["pm"])
    assert created.status_code == 201, created.text
    comment = created.json()
    assert (comment["status"], comment["author_id"], comment["category"]) == ("OPEN", approver, "OMISSION")
    patched = world.client.patch(f"{comments_url}/{comment['id']}", json={"status": "RESOLVED", "resolution": "足した"},
                                 headers=world.headers["planner"])
    assert patched.status_code == 200, patched.text
    assert (patched.json()["status"], patched.json()["resolution"]) == ("RESOLVED", "足した")

    got = world.client.get(world.url(f"/reviews/{review['id']}"), headers=world.headers["viewer"])
    assert got.status_code == 200
    assert [(c["id"], c["status"]) for c in got.json()["comments"]] == [(comment["id"], "RESOLVED")]
    assert world.client.get(world.url(f"/reviews/{review['id']}"),
                            headers=world.headers["contractor"]).json()["comments"] == []


def test_review_rejections(world: World, db_session: Session) -> None:
    """API-WBS-014〜017：レビュー中の開始は 409、メンバーでない人は 400、権限なしは 403、無いものは 404。

    閉じたレビューに指摘を書くのは 409。閉じたレビューの指摘の状態は変えられる（5.10。設計書 1.1・CR-004）。
    """
    element = world.add("要素")
    outsider = world.users["outsider"].id
    assert world.client.post(world.url("/reviews"), json={"reviewer_ids": [outsider]},
                             headers=world.headers["planner"]).status_code == 400
    assert world.client.post(world.url("/reviews"), json={"reviewer_ids": [world.users["pm"].id]},
                             headers=world.headers["viewer"]).status_code == 403
    review = world.client.post(world.url("/reviews"), json={"reviewer_ids": [world.users["pm"].id]},
                               headers=world.headers["planner"]).json()
    again = world.client.post(world.url("/reviews"), json={"reviewer_ids": [world.users["pm"].id]},
                              headers=world.headers["planner"])
    assert (again.status_code, again.json()["error"]["code"]) == (409, "INVALID_STATE")
    comments_url = world.url(f"/reviews/{review['id']}/comments")
    body = {"element_id": element["id"], "category": "OTHER", "body": "b"}
    assert world.client.post(comments_url, json=body, headers=world.headers["viewer"]).status_code == 403
    assert world.client.post(comments_url, json={**body, "element_id": MISSING},
                             headers=world.headers["pm"]).status_code == 400
    assert world.client.post(comments_url, json={**body, "category": "BAD"}, headers=world.headers["pm"]).status_code == 400
    assert world.client.get(world.url(f"/reviews/{MISSING}"), headers=world.headers["viewer"]).status_code == 404
    comment = world.client.post(comments_url, json=body, headers=world.headers["pm"]).json()
    assert world.client.patch(f"{comments_url}/{MISSING}", json={"status": "RESOLVED"},
                              headers=world.headers["planner"]).status_code == 404
    assert world.client.patch(f"{comments_url}/{comment['id']}", json={"status": None},
                              headers=world.headers["planner"]).status_code == 400
    assert world.client.patch(f"{comments_url}/{comment['id']}", json={"status": "RESOLVED"},
                              headers=world.headers["pm"]).status_code == 200
    db_review = db_session.get(WbsReview, review["id"])
    assert db_review is not None
    db_review.status, db_review.closed_at = "CLOSED", NOW
    db_session.commit()
    assert world.client.post(comments_url, json=body, headers=world.headers["pm"]).status_code == 409
    reopened = world.client.patch(f"{comments_url}/{comment['id']}", json={"status": "OPEN"},
                                  headers=world.headers["planner"])
    assert (reopened.status_code, reopened.json()["status"]) == (200, "OPEN")
    assert world.client.patch(f"{comments_url}/{comment['id']}", json={"status": "RESOLVED"},
                              headers=world.headers["viewer"]).status_code == 403


def test_review_list_and_close(world: World, db_session: Session) -> None:
    """API-WBS-025 レビューの一覧・API-WBS-024 レビューを閉じる（IN_REVIEW → DRAFT。設計書 1.1・CR-004）。

    一覧は新しい順（project.view）。閉じると CLOSED と閉じた時刻が入り、WBS は DRAFT に戻る（指摘は残る）。
    閉じるのは wbs.edit。閉じたレビューは 409、無いレビューは 404。
    """
    element = world.add("要素")
    pm = world.users["pm"].id
    reviews_url = world.url("/reviews")
    empty = world.client.get(reviews_url, headers=world.headers["viewer"])
    assert empty.status_code == 200, empty.text
    assert (empty.json()["items"], empty.json()["total"]) == ([], 0)

    first = world.client.post(reviews_url, json={"reviewer_ids": [pm]}, headers=world.headers["planner"]).json()
    comment = world.client.post(world.url(f"/reviews/{first['id']}/comments"),
                                json={"element_id": element["id"], "category": "OTHER", "body": "b"},
                                headers=world.headers["pm"]).json()
    close_url = world.url(f"/reviews/{first['id']}/close")
    assert world.client.post(close_url, headers=world.headers["viewer"]).status_code == 403
    assert world.client.post(world.url(f"/reviews/{MISSING}/close"), headers=world.headers["planner"]).status_code == 404
    # PM がロールの表から project.view を外し wbs.edit だけを持つロールでは、応答（レビューと指摘）を読めないので閉じる前に 403
    view = db_session.scalars(select(RolePermission).where(
        RolePermission.project_id == world.pid, RolePermission.role == "PLANNER", RolePermission.permission == "project.view",
    )).one()
    db_session.delete(view)
    db_session.commit()
    assert world.client.post(close_url, headers=world.headers["planner"]).status_code == 403
    db_session.add(RolePermission(project_id=world.pid, role="PLANNER", permission="project.view"))
    db_session.commit()
    still = world.client.get(world.url(f"/reviews/{first['id']}"), headers=world.headers["viewer"]).json()
    assert (still["status"], world.tree()["wbs_status"]) == ("OPEN", "IN_REVIEW")  # 403 の前に何も変えていない
    closed = world.client.post(close_url, headers=world.headers["planner"])
    assert closed.status_code == 200, closed.text
    body = closed.json()
    assert (body["status"], body["closed_at"]) == ("CLOSED", NOW.isoformat().replace("+00:00", "Z"))
    assert [(c["id"], c["status"]) for c in body["comments"]] == [(comment["id"], "OPEN")]  # 指摘は残る
    assert world.tree()["wbs_status"] == "DRAFT"
    again = world.client.post(close_url, headers=world.headers["planner"])
    assert (again.status_code, again.json()["error"]["code"]) == (409, "INVALID_STATE")

    second = world.client.post(reviews_url, json={"reviewer_ids": [pm]}, headers=world.headers["planner"])
    assert second.status_code == 201, second.text  # 閉じた後は新しいレビューを始められる
    listed = world.client.get(reviews_url, headers=world.headers["viewer"]).json()
    assert [(r["id"], r["status"]) for r in listed["items"]] == [(second.json()["id"], "OPEN"), (first["id"], "CLOSED")]
    assert (listed["project_id"], listed["total"], listed["limit"], listed["offset"]) == (world.pid, 2, 100, 0)
    assert listed["items"][1]["reviewer_ids"] == [pm] and listed["items"][1]["requested_by"] == world.users["planner"].id
    paged = world.client.get(reviews_url, params={"limit": 1, "offset": 1}, headers=world.headers["viewer"]).json()
    assert [r["id"] for r in paged["items"]] == [first["id"]]
    assert world.client.get(reviews_url, params={"limit": 1001}, headers=world.headers["viewer"]).status_code == 400
    assert world.client.get(reviews_url, headers=world.headers["outsider"]).status_code == 403

    # WBS が IN_REVIEW でなければ閉じられない（レビューが OPEN のまま状態だけずれた場合も 409）
    world.set_status("DRAFT")
    stale = world.client.post(world.url(f"/reviews/{second.json()['id']}/close"), headers=world.headers["planner"])
    assert (stale.status_code, stale.json()["error"]["code"]) == (409, "INVALID_STATE")

    # 閉じたレビューに残った未対応の指摘は申請の前提（5.10）に数え、閉じた後でも対応すれば前提から外れる（申請が塞がらない）
    def unmet() -> list[str]:
        response = world.client.post(world.url("/approval-requests"), headers=world.headers["planner"])
        assert response.status_code in (201, 422), response.text
        return [] if response.status_code == 201 else [d["condition"] for d in response.json()["error"]["details"]]

    assert "open_comments" in unmet()
    resolved = world.client.patch(world.url(f"/reviews/{first['id']}/comments/{comment['id']}"),
                                  json={"status": "RESOLVED", "resolution": "直した"}, headers=world.headers["planner"])
    assert resolved.status_code == 200, resolved.text
    assert (resolved.json()["status"], resolved.json()["resolution"]) == ("RESOLVED", "直した")
    assert "open_comments" not in unmet()


# ---------------------------------------------------------------------------
# API-WBS-021・022：スコープベースライン
# ---------------------------------------------------------------------------


def test_scope_baselines_list_and_one_version(world: World, db_session: Session) -> None:
    """API-WBS-021 スコープベースラインの版の一覧・API-WBS-022 スコープベースラインの1版（要素と辞書）：写した時点の値を返す。"""
    element = world.add("要件定義")
    for version, reason in ((1, "INITIAL_APPROVAL"), (2, "RE_APPROVAL")):
        baseline = ScopeBaseline(project_id=world.pid, version=version, reason=reason, created_by=world.admin.id,
                                 created_at=NOW)
        db_session.add(baseline)
        db_session.flush()
        db_session.add(ScopeBaselineElement(scope_baseline_id=baseline.id, element_id=element["id"], wbs_code="1.0",
                                            kind="DELIVERABLE", name=f"版{version}の名称",
                                            dictionary={"deliverable": "仕様書", "work_scope": None,
                                                        "completion_criteria": "承認", "assumptions": None}))
    db_session.commit()
    world.client.patch(world.url(f"/elements/{element['id']}"), json={"name": "今の名称"}, headers=world.headers["planner"])

    listed = world.client.get(f"/api/v1/projects/{world.pid}/scope-baselines", headers=world.headers["viewer"])
    assert listed.status_code == 200, listed.text
    assert [(b["version"], b["reason"], b["element_count"]) for b in listed.json()["items"]] == [
        (1, "INITIAL_APPROVAL", 1), (2, "RE_APPROVAL", 1)]
    first_id = listed.json()["items"][0]["id"]
    one = world.client.get(f"/api/v1/projects/{world.pid}/scope-baselines/{first_id}", headers=world.headers["viewer"])
    assert one.status_code == 200, one.text
    body = one.json()
    assert (body["version"], body["restricted"]) == (1, False)
    assert [(e["element_id"], e["wbs_code"], e["name"], e["dictionary"]["deliverable"]) for e in body["elements"]] == [
        (element["id"], "1.0", "版1の名称", "仕様書")]
    missing = f"/api/v1/projects/{world.pid}/scope-baselines/{MISSING}"
    assert world.client.get(missing, headers=world.headers["viewer"]).status_code == 404
    assert world.client.get(f"/api/v1/projects/{world.pid}/scope-baselines",
                            headers=world.headers["outsider"]).status_code == 403
    restricted = world.client.get(f"/api/v1/projects/{world.pid}/scope-baselines/{first_id}",
                                  headers=world.headers["contractor"]).json()
    assert (restricted["restricted"], restricted["elements"]) == (True, [])


# ---------------------------------------------------------------------------
# 別の群の表の代わり（責任分担表・横断照会・委託先の範囲の試験に使う）
# ---------------------------------------------------------------------------


class FakeActivities:
    """活動の表（DB-SCH-001）の代わり。活動 → WP の要素（``hours`` は活動の見積。省けば空）。"""

    def __init__(self, placement: dict[int, int], hours: dict[int, Decimal] | None = None) -> None:
        self.placement = placement
        self.hours = hours or {}

    def activities(self, session: Session, project_id: int) -> list[ActivityEstimate]:
        return [ActivityEstimate(id=a, work_package_id=wp, estimate_hours=self.hours.get(a))
                for a, wp in self.placement.items()]


class FakeAssigned:
    """割当（DB-EFF-001）の代わり。利用者 → 割当の活動（委託先の見える範囲を決める）。"""

    def __init__(self, by_user: dict[int, frozenset[int]]) -> None:
        self.by_user = by_user

    def activity_ids(self, session: Session, project_id: int, user_id: int) -> frozenset[int]:
        return self.by_user.get(user_id, frozenset())


def add_member(world: World, login_id: str, role: str) -> None:
    user = make_user(world.session, login_id)
    put = world.client.put(f"/api/v1/projects/{world.pid}/members/{user.id}", json={"roles": [role]},
                           headers=world.admin_h)
    assert put.status_code == 200, put.text
    world.users[login_id] = user
    world.headers[login_id] = login(world.session, user)


# ---------------------------------------------------------------------------
# API-WBS-011：責任分担表
# ---------------------------------------------------------------------------


def test_responsibility_matrix_json_csv_and_restricted(world: World, app: FastAPI) -> None:
    """API-WBS-011 責任分担表（`format=json` か `csv`）：WP × メンバー（責任者・担当・割当工数）と責任者の無い WP（RULE-WBS-007）。"""
    pm, planner, viewer = world.users["pm"].id, world.users["planner"].id, world.users["viewer"].id
    contractor = world.users["contractor"].id
    design = world.add("設計")
    a = world.add("基本設計", design["id"], kind="WORK_PACKAGE", responsible_user_id=pm)
    b = world.add("詳細設計", design["id"], kind="WORK_PACKAGE", responsible_team="基盤")
    c = world.add("管理", kind="WORK_PACKAGE", responsible_user_id=planner)
    app.state.wbs_activities = FakeActivities({101: a["id"], 102: b["id"]})
    app.state.wbs_readers = CrossGroupReaders(assignments=lambda _s, _p: [
        AssignmentRef(activity_id=101, user_id=planner, planned_hours=Decimal("8")),
        AssignmentRef(activity_id=102, user_id=pm, planned_hours=None),
        AssignmentRef(activity_id=102, user_id=viewer, planned_hours=Decimal("4.5")),
    ])
    url = f"/api/v1/projects/{world.pid}/wbs/responsibility-matrix"

    response = world.client.get(url, headers=world.headers["viewer"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["restricted"] is False
    assert [(u["user_id"], u["login_id"], u["is_member"]) for u in body["users"]] == [
        (pm, "pm", True), (planner, "planner", True), (viewer, "viewer", True), (contractor, "contractor", True)]
    assert [(r["wbs_code"], r["responsible_user_id"], r["responsible_team"], r["planned_hours"]) for r in body["rows"]] == [
        ("1.1", pm, None, 8.0), ("1.2", None, "基盤", 4.5), ("2.0", planner, None, 0.0)]
    cells = {(r["wbs_code"], cell["user_id"]): (cell["is_responsible"], cell["is_assigned"], cell["planned_hours"])
             for r in body["rows"] for cell in r["cells"]}
    assert cells[("1.1", pm)] == (True, False, 0.0)
    assert cells[("1.1", planner)] == (False, True, 8.0)
    assert cells[("1.2", pm)] == (False, True, 0.0)
    assert cells[("1.2", viewer)] == (False, True, 4.5)
    assert cells[("2.0", planner)] == (True, False, 0.0)
    assert cells[("2.0", viewer)] == (False, False, 0.0)
    assert body["without_responsible"] == [
        {"element_id": b["id"], "wbs_code": "1.2", "name": "詳細設計", "responsible_team": "基盤"}]

    csv_response = world.client.get(url, params={"format": "csv"}, headers=world.headers["viewer"])
    assert csv_response.status_code == 200, csv_response.text
    assert csv_response.headers["content-type"].startswith("text/csv")
    raw = csv_response.content
    assert raw.startswith("﻿".encode())
    lines = raw.decode("utf-8-sig").split("\r\n")
    assert lines[0] == "WBS ID,名称,責任者のログインID,担当チーム,割当工数の合計（人時）,pm,planner,viewer,contractor"
    assert lines[1] == "1.1,基本設計,pm,,8.00,責任者,担当 8.00,,"
    assert lines[2] == "1.2,詳細設計,,基盤,4.50,担当 0.00,,担当 4.50,"
    assert lines[3] == "2.0,管理,planner,,0.00,,責任者,,"
    assert lines[4:] == [""]

    assert world.client.get(url, params={"format": "xml"}, headers=world.headers["viewer"]).status_code == 400
    assert world.client.get(url, headers=world.headers["outsider"]).status_code == 403
    assert world.client.get(f"/api/v1/projects/{MISSING}/wbs/responsibility-matrix",
                            headers=world.headers["viewer"]).status_code == 404

    # 委託先は自分の割当の活動（102）の WP の行だけ
    app.state.project_access = ProjectMembersTable(assignments=FakeAssigned({contractor: frozenset({102})}))
    restricted = world.client.get(url, headers=world.headers["contractor"]).json()
    assert restricted["restricted"] is True
    assert [r["element_id"] for r in restricted["rows"]] == [b["id"]]
    assert [w["element_id"] for w in restricted["without_responsible"]] == [b["id"]]

    # メンバーから外した後も割当が残る人は、列の後ろに「メンバー外」として出る
    assert world.client.delete(f"/api/v1/projects/{world.pid}/members/{viewer}",
                               headers=world.admin_h).status_code == 204
    after = world.client.get(url, headers=world.headers["pm"]).json()
    assert [(u["user_id"], u["is_member"]) for u in after["users"]] == [
        (pm, True), (planner, True), (contractor, True), (viewer, False)]
    header = world.client.get(url, params={"format": "csv"}, headers=world.headers["pm"]).content.decode("utf-8-sig")
    assert header.split("\r\n")[0].endswith(",contractor,viewer（メンバー外）")


def test_contractor_aggregates_and_counts_use_only_visible_elements_and_activities(
    world: World, app: FastAPI, db_session: Session
) -> None:
    """9.2 の注記（CR-011）：委託先に返す集計値・件数は見える要素・活動だけから求める。

    API-WBS-001 の ``rollup_hours``・``project_estimate_hours``・検査の件数、API-WBS-012 の件数と子の集計、
    API-WBS-011 の割当工数・担当・列、API-WBS-021 の要素の数。見えない活動（103・102・104）にも見積と割当がある。
    全て読める利用者の値は変わらない。
    """
    pm, planner, viewer = world.users["pm"].id, world.users["planner"].id, world.users["viewer"].id
    contractor, outsider = world.users["contractor"].id, world.users["outsider"].id
    design = world.add("設計", own_estimate_hours="10")
    a = world.add("基本設計", design["id"], kind="WORK_PACKAGE", own_estimate_hours="8")
    b = world.add("詳細設計", design["id"], kind="WORK_PACKAGE", own_estimate_hours="20")
    c = world.add("管理", kind="WORK_PACKAGE")
    app.state.wbs_activities = FakeActivities(
        {101: a["id"], 103: a["id"], 102: b["id"], 104: c["id"]},
        {101: Decimal("8"), 103: Decimal("4"), 102: Decimal("16"), 104: Decimal("7")},
    )
    app.state.wbs_readers = CrossGroupReaders(assignments=lambda _s, _p: [
        AssignmentRef(activity_id=101, user_id=contractor, planned_hours=Decimal("8")),
        AssignmentRef(activity_id=103, user_id=planner, planned_hours=Decimal("4")),
        AssignmentRef(activity_id=102, user_id=outsider, planned_hours=Decimal("5")),
        AssignmentRef(activity_id=104, user_id=viewer, planned_hours=Decimal("3")),
    ])
    app.state.project_access = ProjectMembersTable(assignments=FakeAssigned({contractor: frozenset({101})}))

    # 木（API-WBS-001）：全て読める利用者は全ての活動から、委託先は見える活動（101）だけから
    full = world.tree("viewer")
    assert full["restricted"] is False
    assert [(e["wbs_code"], e["rollup_hours"]) for e in full["elements"]] == [
        ("1.0", 28.0), ("1.1", 12.0), ("1.2", 16.0), ("2.0", 7.0)]
    assert full["project_estimate_hours"] == 35.0
    assert full["validation"] == {"errors": 0, "warnings": 3, "missing_completion": 3}
    seen = world.tree("contractor")
    assert seen["restricted"] is True
    assert [(e["wbs_code"], e["rollup_hours"], e["rollup_source"]) for e in seen["elements"]] == [
        ("1.0", 8.0, "CHILDREN"), ("1.1", 8.0, "ACTIVITIES")]
    assert seen["project_estimate_hours"] == 8.0
    assert seen["validation"] == {"errors": 0, "warnings": 1, "missing_completion": 1}

    # 検査（API-WBS-012）：子の集計も見える活動だけ（1.1 は 8 = 8 で差異なし、1.0 の子の集計は 8）
    validation = world.client.get(world.url("/validation"), headers=world.headers["contractor"]).json()
    assert validation["counts"] == {"errors": 0, "warnings": 1, "missing_completion": 1}
    assert [(f["code"], f["element_id"], f["own_estimate_hours"], f["lower_hours"]) for f in validation["findings"]] == [
        ("SUM_MISMATCH", design["id"], 10.0, 8.0)]
    full_validation = world.client.get(world.url("/validation"), headers=world.headers["viewer"]).json()
    assert [(f["element_id"], f["lower_hours"]) for f in full_validation["findings"]] == [
        (design["id"], 28.0), (a["id"], 12.0), (b["id"], 16.0)]

    # 責任分担表（API-WBS-011）：割当工数・担当・列も見える活動の割当だけ（見えない活動だけにいるメンバー外の人は列に出ない）
    url = f"/api/v1/projects/{world.pid}/wbs/responsibility-matrix"
    matrix = world.client.get(url, headers=world.headers["viewer"]).json()
    assert [(r["wbs_code"], r["planned_hours"]) for r in matrix["rows"]] == [("1.1", 12.0), ("1.2", 5.0), ("2.0", 3.0)]
    assert [(u["user_id"], u["is_member"]) for u in matrix["users"]][-1] == (outsider, False)
    restricted = world.client.get(url, headers=world.headers["contractor"]).json()
    assert [(r["wbs_code"], r["planned_hours"]) for r in restricted["rows"]] == [("1.1", 8.0)]
    assert [u["user_id"] for u in restricted["users"]] == [pm, planner, viewer, contractor]
    cells = {cell["user_id"]: (cell["is_assigned"], cell["planned_hours"]) for cell in restricted["rows"][0]["cells"]}
    assert cells[contractor] == (True, 8.0) and cells[planner] == (False, 0.0)

    # 版の一覧（API-WBS-021）：要素の数は今見える要素の写しだけ（API-WBS-022 と同じ範囲）
    baseline = ScopeBaseline(project_id=world.pid, version=1, reason="INITIAL_APPROVAL", created_by=world.admin.id,
                             created_at=NOW)
    db_session.add(baseline)
    db_session.flush()
    for element in (design, a, b, c):
        db_session.add(ScopeBaselineElement(scope_baseline_id=baseline.id, element_id=element["id"],
                                            wbs_code=element["wbs_code"], kind=element["kind"], name=element["name"],
                                            dictionary={}))
    db_session.commit()
    baselines = f"/api/v1/projects/{world.pid}/scope-baselines"
    assert [b["element_count"] for b in world.client.get(baselines, headers=world.headers["viewer"]).json()["items"]] == [4]
    listed = world.client.get(baselines, headers=world.headers["contractor"]).json()["items"]
    assert [b["element_count"] for b in listed] == [2]
    one = world.client.get(f"{baselines}/{baseline.id}", headers=world.headers["contractor"]).json()
    assert len(one["elements"]) == listed[0]["element_count"]


# ---------------------------------------------------------------------------
# API-WBS-018〜020：承認の申請・判断・承認待ちの一覧
# ---------------------------------------------------------------------------


def make_ready_wp(world: World, name: str = "WP") -> dict[str, Any]:
    wp = world.add(name, kind="WORK_PACKAGE", own_estimate_hours="5")
    response = world.client.put(world.url(f"/elements/{wp['id']}/dictionary"),
                                json={"deliverable": "成果物", "completion_criteria": "承認済み"},
                                headers=world.headers["planner"])
    assert response.status_code == 200, response.text
    return wp


def test_submit_checks_preconditions_and_closes_review(world: World) -> None:
    """API-WBS-018 WBSの承認を申請する（前提を確かめる）：前提を満たさなければ 422 WBS_NOT_READY、通れば PENDING_APPROVAL（5.10）。"""
    url = world.url("/approval-requests")
    wp = world.add("WP", kind="WORK_PACKAGE", own_estimate_hours="5")
    items = world.client.put(f"/api/v1/projects/{world.pid}/scope-items",
                             json={"items": [{"kind": "IN", "title": "対象"}, {"kind": "OUT", "title": "対象外"}]},
                             headers=world.headers["pm"]).json()["items"]
    world.client.put(world.url(f"/elements/{wp['id']}/scope-links"), json={"scope_item_ids": [items[1]["id"]]},
                     headers=world.headers["planner"])
    review = world.client.post(world.url("/reviews"), json={"reviewer_ids": [world.users["pm"].id]},
                               headers=world.headers["planner"]).json()
    comment = world.client.post(world.url(f"/reviews/{review['id']}/comments"),
                                json={"element_id": wp["id"], "category": "AMBIGUITY", "body": "曖昧"},
                                headers=world.headers["pm"]).json()

    response = world.client.post(url, headers=world.headers["planner"])
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "WBS_NOT_READY"
    assert response.json()["error"]["details"] == [
        {"condition": "missing_completion", "count": 1},
        {"condition": "open_comments", "count": 1},
        {"condition": "hundred_percent_errors", "count": 1},
    ]
    assert world.tree()["wbs_status"] == "IN_REVIEW"

    world.client.put(world.url(f"/elements/{wp['id']}/dictionary"),
                     json={"deliverable": "成果物", "completion_criteria": "承認済み"}, headers=world.headers["planner"])
    world.client.put(world.url(f"/elements/{wp['id']}/scope-links"), json={"scope_item_ids": [items[0]["id"]]},
                     headers=world.headers["planner"])
    world.client.patch(world.url(f"/reviews/{review['id']}/comments/{comment['id']}"),
                       json={"status": "RESOLVED", "resolution": "直した"}, headers=world.headers["planner"])
    assert world.client.post(url, headers=world.headers["viewer"]).status_code == 403

    response = world.client.post(url, headers=world.headers["planner"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["target_type"], body["target_id"], body["status"], body["requested_by"]) == (
        "WBS", world.pid, "PENDING", world.users["planner"].id)
    assert (body["decided_by"], body["decided_at"], body["comment"], body["wbs_status"]) == (
        None, None, None, "PENDING_APPROVAL")
    assert world.tree()["wbs_status"] == "PENDING_APPROVAL"
    closed = world.client.get(world.url(f"/reviews/{review['id']}"), headers=world.headers["viewer"]).json()
    assert (closed["status"], closed["closed_at"]) == ("CLOSED", NOW.isoformat().replace("+00:00", "Z"))

    again = world.client.post(url, headers=world.headers["planner"])
    assert (again.status_code, again.json()["error"]["code"]) == (409, "INVALID_STATE")
    assert world.client.post(f"/api/v1/projects/{MISSING}/wbs/approval-requests",
                             headers=world.headers["planner"]).status_code == 404


def test_approve_creates_baseline_versions_and_lists_pending(world: World) -> None:
    """API-WBS-019 承認・差戻し（WBSと変更要求で共通）・API-WBS-020 自分が判断できる承認待ちの一覧：
    承認で版 1（INITIAL_APPROVAL）、基準の後の変更と再承認で版 2（RE_APPROVAL）。本人は 403、判断済みは 409（5.10・WBS-21・22）。"""
    add_member(world, "approver", "APPROVER")
    wp = make_ready_wp(world)
    submitted = world.client.post(world.url("/approval-requests"), headers=world.headers["pm"])
    assert submitted.status_code == 201, submitted.text
    approval_id = submitted.json()["id"]

    pending = world.client.get("/api/v1/approval-requests", headers=world.headers["approver"])
    assert pending.status_code == 200, pending.text
    assert pending.json()["total"] == 1
    item = pending.json()["items"][0]
    assert (item["id"], item["project_code"], item["project_name"], item["requested_by_name"]) == (
        approval_id, "PRJ-W", "WBS", "pm")
    for who in ("pm", "planner", "viewer", "outsider"):  # 本人・判断の権限が無い・メンバーでない
        assert world.client.get("/api/v1/approval-requests", headers=world.headers[who]).json()["items"] == [], who
    assert world.client.get("/api/v1/approval-requests").status_code == 401

    decision_url = f"/api/v1/approval-requests/{approval_id}/decision"
    own = world.client.post(decision_url, json={"decision": "APPROVE"}, headers=world.headers["pm"])
    assert (own.status_code, own.json()["error"]["code"]) == (403, "SELF_APPROVAL_FORBIDDEN")
    assert world.client.post(decision_url, json={"decision": "APPROVE"},
                             headers=world.headers["planner"]).status_code == 403
    assert world.client.post(decision_url, json={"decision": "MAYBE"},
                             headers=world.headers["approver"]).status_code == 400

    approved = world.client.post(decision_url, json={"decision": "APPROVE", "comment": "問題なし"},
                                 headers=world.headers["approver"])
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert (body["status"], body["decided_by"], body["comment"], body["wbs_status"]) == (
        "APPROVED", world.users["approver"].id, "問題なし", "BASELINED")
    assert body["decided_at"] == NOW.isoformat().replace("+00:00", "Z")
    baseline = body["scope_baseline"]
    assert (baseline["version"], baseline["reason"], baseline["approval_request_id"], baseline["element_count"]) == (
        1, "INITIAL_APPROVAL", approval_id, 1)
    one = world.client.get(f"/api/v1/projects/{world.pid}/scope-baselines/{baseline['id']}",
                           headers=world.headers["viewer"]).json()
    assert [(e["element_id"], e["wbs_code"], e["own_estimate_hours"], e["dictionary"]) for e in one["elements"]] == [
        (wp["id"], "1.0", 5.0, {"deliverable": "成果物", "work_scope": None, "completion_criteria": "承認済み",
                                "assumptions": None})]
    again = world.client.post(decision_url, json={"decision": "REJECT"}, headers=world.headers["approver"])
    assert (again.status_code, again.json()["error"]["code"]) == (409, "INVALID_STATE")
    assert world.client.get("/api/v1/approval-requests", headers=world.headers["approver"]).json()["total"] == 0

    # 基準の後に変えると DRAFT。改めて承認すると次の版（RE_APPROVAL）。前の版の写しは変わらない
    world.client.patch(world.url(f"/elements/{wp['id']}"), json={"name": "変えた"}, headers=world.headers["planner"])
    assert world.tree()["wbs_status"] == "DRAFT"
    second = world.client.post(world.url("/approval-requests"), headers=world.headers["planner"]).json()
    approved = world.client.post(f"/api/v1/approval-requests/{second['id']}/decision", json={"decision": "APPROVE"},
                                 headers=world.headers["pm"])
    assert approved.status_code == 200, approved.text
    assert (approved.json()["scope_baseline"]["version"], approved.json()["scope_baseline"]["reason"]) == (
        2, "RE_APPROVAL")
    versions = world.client.get(f"/api/v1/projects/{world.pid}/scope-baselines", headers=world.headers["viewer"]).json()
    assert [(b["version"], b["reason"]) for b in versions["items"]] == [(1, "INITIAL_APPROVAL"), (2, "RE_APPROVAL")]
    first = world.client.get(f"/api/v1/projects/{world.pid}/scope-baselines/{baseline['id']}",
                             headers=world.headers["viewer"]).json()
    assert first["elements"][0]["name"] == "WP"
    assert world.client.post(f"/api/v1/approval-requests/{MISSING}/decision", json={"decision": "APPROVE"},
                             headers=world.headers["approver"]).status_code == 404


def test_reject_sends_wbs_back_to_draft(world: World, db_session: Session) -> None:
    """API-WBS-019：差戻しは PENDING_APPROVAL → DRAFT。版は作らない。判断のコメントが残る（WBS-21）。"""
    make_ready_wp(world)
    approval_id = world.client.post(world.url("/approval-requests"), headers=world.headers["planner"]).json()["id"]
    rejected = world.client.post(f"/api/v1/approval-requests/{approval_id}/decision",
                                 json={"decision": "REJECT", "comment": "見積の根拠が足りない"}, headers=world.headers["pm"])
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert (body["status"], body["wbs_status"], body["scope_baseline"], body["comment"]) == (
        "REJECTED", "DRAFT", None, "見積の根拠が足りない")
    assert world.tree()["wbs_status"] == "DRAFT"
    assert db_session.scalars(select(ScopeBaseline)).all() == []
    # 差し戻された WBS は直して申請し直せる
    assert world.client.post(world.url("/approval-requests"), headers=world.headers["planner"]).status_code == 201


def test_change_request_approvals_use_change_approve_and_fail_fast_until_wired(
    world: World, app: FastAPI, db_session: Session
) -> None:
    """API-WBS-019・020：変更要求の申請は change.approve で判断・一覧する。変更要求の表を扱う実装が無い間は判断を止める。"""
    approval = ApprovalRequest(project_id=world.pid, target_type="CHANGE_REQUEST", target_id=7,
                               requested_by=world.users["planner"].id, requested_at=NOW)
    db_session.add(approval)
    db_session.commit()
    assert [i["target_type"] for i in world.client.get("/api/v1/approval-requests",
                                                        headers=world.headers["pm"]).json()["items"]] == ["CHANGE_REQUEST"]
    assert world.client.get("/api/v1/approval-requests", headers=world.headers["planner"]).json()["items"] == []
    url = f"/api/v1/approval-requests/{approval.id}/decision"
    assert world.client.post(url, json={"decision": "APPROVE"}, headers=world.headers["viewer"]).status_code == 403
    with pytest.raises(RuntimeError, match="DB-CHG-001"):
        world.client.post(url, json={"decision": "APPROVE"}, headers=world.headers["pm"])

    decided: list[tuple[int, ApprovalStatus, int]] = []

    class FakeChangeRequests:
        def decide(self, session: Session, approval: ApprovalRequest, status: ApprovalStatus, actor: User,
                   now: datetime) -> None:
            decided.append((approval.target_id, status, actor.id))

    app.state.change_request_approvals = FakeChangeRequests()
    response = world.client.post(url, json={"decision": "REJECT"}, headers=world.headers["pm"])
    assert response.status_code == 200, response.text
    assert (response.json()["status"], response.json()["wbs_status"], response.json()["scope_baseline"]) == (
        "REJECTED", None, None)
    assert decided == [(7, ApprovalStatus.REJECTED, world.users["pm"].id)]


# ---------------------------------------------------------------------------
# API-WBS-023：WBS IDによる横断照会
# ---------------------------------------------------------------------------


def test_trace_by_current_and_old_code(world: World, app: FastAPI) -> None:
    """API-WBS-023 WBS IDによる横断照会：配下の要素・活動・見積・実績・EAC・変更要求・是正措置を 1 回で返す（RULE-WBS-008・WBS-07）。"""
    design = world.add("設計")
    a = world.add("基本設計", design["id"], kind="WORK_PACKAGE")
    other = world.add("製造")
    app.state.wbs_activities = FakeActivities({101: a["id"]})
    app.state.wbs_readers = CrossGroupReaders(
        estimate_records=lambda _s, _p: [
            EstimateRecordRef(id=1, target_type=EstimateTarget.ELEMENT, target_id=a["id"]),
            EstimateRecordRef(id=2, target_type=EstimateTarget.ACTIVITY, target_id=101),
            EstimateRecordRef(id=3, target_type=EstimateTarget.ELEMENT, target_id=other["id"]),
        ],
        time_entries=lambda _s, _p: [TimeEntryRef(id=11, activity_id=101, hours=Decimal("3.5")),
                                     TimeEntryRef(id=12, activity_id=101, hours=Decimal("2"))],
        change_requests=lambda _s, _p: [ChangeRequestRef(id=21, target_codes=("1.1",)),
                                        ChangeRequestRef(id=22, target_codes=("2.0",))],
        corrective_actions=lambda _s, _p: [CorrectiveActionRef(id=31, element_id=a["id"]),
                                           CorrectiveActionRef(id=32, activity_id=101),
                                           CorrectiveActionRef(id=33, element_id=other["id"])],
        eac_hours=lambda _s, _p: {design["id"]: Decimal("40")},
    )
    url = f"/api/v1/projects/{world.pid}/wbs-codes"

    response = world.client.get(f"{url}/1.0/trace", headers=world.headers["viewer"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["query"], body["wbs_code"], body["element_id"], body["resolved_by"], body["restricted"]) == (
        "1.0", "1.0", design["id"], "CURRENT", False)
    assert [e["wbs_code"] for e in body["elements"]] == ["1.0", "1.1"]
    assert (body["activity_ids"], body["estimate_record_ids"], body["time_entry_ids"]) == ([101], [1, 2], [11, 12])
    assert (body["actual_hours"], body["eac_hours"]) == (5.5, 40.0)
    assert (body["change_request_ids"], body["corrective_action_ids"]) == ([21], [31, 32])

    # 委託先：見える要素（割当の活動の経路）へ解決した ID だけ。EAC は返さない
    contractor = world.users["contractor"].id
    app.state.project_access = ProjectMembersTable(assignments=FakeAssigned({contractor: frozenset({101})}))
    restricted = world.client.get(f"{url}/1.0/trace", headers=world.headers["contractor"]).json()
    assert (restricted["restricted"], restricted["eac_hours"], restricted["activity_ids"]) == (True, None, [101])
    assert (restricted["estimate_record_ids"], restricted["corrective_action_ids"]) == ([1, 2], [31, 32])
    assert world.client.get(f"{url}/2.0/trace", headers=world.headers["contractor"]).status_code == 404

    # 移動と改番の後も旧 ID（1.1）で照会でき、旧 ID で書いた変更要求も当たる（RULE-WBS-002）
    world.client.post(world.url(f"/elements/{a['id']}/move"), json={"parent_id": other["id"]},
                      headers=world.headers["planner"])
    world.client.post(world.url("/renumber"), headers=world.headers["planner"])
    moved = world.client.get(f"{url}/1.1/trace", headers=world.headers["viewer"]).json()
    assert (moved["wbs_code"], moved["element_id"], moved["resolved_by"]) == ("2.1", a["id"], "HISTORY")
    assert moved["change_request_ids"] == [21] and moved["eac_hours"] is None
    whole = world.client.get(f"{url}/2.0/trace", headers=world.headers["viewer"]).json()
    assert ([e["wbs_code"] for e in whole["elements"]], whole["change_request_ids"]) == (["2.0", "2.1"], [21, 22])
    assert whole["corrective_action_ids"] == [31, 32, 33]

    assert world.client.get(f"{url}/9.9/trace", headers=world.headers["viewer"]).status_code == 404
    assert world.client.get(f"{url}/1.0/trace", headers=world.headers["outsider"]).status_code == 403
    assert world.client.get(f"/api/v1/projects/{MISSING}/wbs-codes/1.0/trace",
                            headers=world.headers["viewer"]).status_code == 404


# ---------------------------------------------------------------------------
# 別の群の表を読む口・書く口の差し替え忘れ（A-18）
# ---------------------------------------------------------------------------

_READER_TABLES = {
    "assignments": "assignments",  # DB-EFF-001
    "estimate_records": "estimate_records",  # DB-EST-001
    "time_entries": "time_entries",  # DB-EFF-002
    "change_requests": "change_requests",  # DB-CHG-001
    "corrective_actions": "corrective_actions",  # DB-CHG-002
}


def test_cross_group_readers_are_wired_once_tables_exist(app: FastAPI) -> None:
    """責任分担表・横断照会が読む別の群の表ができたら、アプリはその表を読む関数を載せていること。"""
    defaults = CrossGroupReaders()
    readers = app.state.wbs_readers
    for table, name in _READER_TABLES.items():
        if table in Base.metadata.tables:
            assert getattr(readers, name) is not getattr(defaults, name), (
                f"{table} ができたので、app.main.create_app の app.state.wbs_readers の {name} を表を読む関数へ差し替える"
            )
        else:
            assert getattr(readers, name) is getattr(defaults, name), name
    # EAC（RULE-EFF-002）は工数の群のサービス（api:EFF）ができたら差し替える
    if importlib.util.find_spec("app.services.effort") is not None:
        assert readers.eac_hours is not eac_not_available, (
            "app.services.effort ができたので、app.state.wbs_readers の eac_hours を EAC の集計を読む関数へ差し替える"
        )


def test_change_request_approvals_are_wired_once_change_requests_table_exists(app: FastAPI) -> None:
    """変更要求の表（change_requests）ができたら、アプリは変更要求の申請への判断を反映する実装を載せていること。"""
    if "change_requests" in Base.metadata.tables:
        assert not isinstance(app.state.change_request_approvals, ChangeRequestsTableAbsent), (
            "change_requests ができたので、app.main.create_app の app.state.change_request_approvals を差し替える"
        )
    else:
        assert isinstance(app.state.change_request_approvals, ChangeRequestsTableAbsent)


# 変更要求を提出する API（API-CHG-005）のパス。提出が変更要求の承認申請を作る（5.10）
CHANGE_REQUEST_SUBMIT_PATH = "/api/v1/projects/{project_id}/change-requests/{change_request_id}/submit"


def test_change_request_approvals_apply_transition_once_change_api_exists(app: FastAPI) -> None:
    """変更要求を提出する API（API-CHG-005）ができたら、アプリは申請への判断を変更要求の状態へ通す実装を載せていること（A-31）。

    それまでは ChangeRequestTransitionNotWired（申請の指す変更要求を読み、状態遷移の実装が無いので止める）。
    api:CHG が差し替えを忘れるとこの試験が落ちる（test_prj.py の日程の再計算の口と同じ形）。
    """
    # FastAPI の app.routes は include_router したルートのパスを出さないことがあるので、OpenAPI のパスでも見る
    paths = {getattr(route, "path", None) for route in app.routes} | set(app.openapi()["paths"])
    assert "/api/v1/projects/{project_id}/wbs" in paths, "ルートのパスを読めていない（判定の前提が崩れている）"
    if CHANGE_REQUEST_SUBMIT_PATH in paths:
        assert not isinstance(
            app.state.change_request_approvals, (ChangeRequestsTableAbsent, ChangeRequestTransitionNotWired)
        ), "API-CHG-005 ができたので、app.main.create_app の app.state.change_request_approvals を RULE-CHG-003 を通す実装へ差し替える"
    else:
        assert isinstance(app.state.change_request_approvals, ChangeRequestTransitionNotWired)


# ---------------------------------------------------------------------------
# 同時実行と採番規則の行（A-49 の 3。OQ-42 (3)・(24)）
# SQLite は FOR UPDATE を無視するので、ロックの直前に別のセッションで状態を変えてコミットし（先に終わった同時の操作）、
# 判定がロックの後に読み直した値で行われること・一意制約の違反が 409 CONFLICT になることを見る（conftest の race）
# ---------------------------------------------------------------------------


def _row_counts(session: Session) -> dict[str, int]:
    session.expire_all()
    return {table.name: int(session.scalar(select(func.count()).select_from(table)) or 0)
            for table in Base.metadata.sorted_tables}


def _project_without_numbering_rule(world: World, db_session: Session) -> tuple[Project, WbsElement]:
    """採番規則の行が無いプロジェクト（API-PRJ-001 を通らずに作られた）。第 1 階層に順番 1 と 3 の要素がある（2 は消した）。"""
    raw = Project(code="RAW", name="行だけ", start_date=NOW.date(), owner_user_id=world.admin.id)
    db_session.add(raw)
    db_session.flush()
    first = WbsElement(project_id=raw.id, kind="DELIVERABLE", name="一", wbs_code="1.0", sibling_seq=1, sort_order=1)
    db_session.add_all([first, WbsElement(project_id=raw.id, kind="DELIVERABLE", name="三", wbs_code="3.0",
                                          sibling_seq=3, sort_order=2)])
    db_session.commit()
    assert db_session.scalar(select(WbsNumberingRule).where(WbsNumberingRule.project_id == raw.id)) is None
    return raw, first


def test_reads_do_not_create_numbering_rule_row_and_first_write_does(world: World, db_session: Session) -> None:
    """API-WBS-001・008・009・011・012・015・021・023・026（読むだけ）は採番規則の行が無くても行を作らない（DB のどの表の行も増えない）。
    4.1 の既定の値と第 1 階層の順番の最大で読み、最初の書く操作（API-WBS-002）がその値で行を作る（A-49 の 3）。"""
    raw, first = _project_without_numbering_rule(world, db_session)
    review = WbsReview(project_id=raw.id, requested_by=world.admin.id, requested_at=NOW, reviewer_ids=[])
    db_session.add(review)
    db_session.commit()
    before = _row_counts(db_session)

    base = f"/api/v1/projects/{raw.id}"
    for path in ("/wbs", "/wbs/code-history", f"/wbs/elements/{first.id}/dictionary", "/wbs/responsibility-matrix",
                 "/wbs/validation", f"/wbs/reviews/{review.id}", "/scope-baselines", "/wbs-codes/1.0/trace",
                 f"/wbs/elements/{first.id}/scope-links"):
        response = world.client.get(base + path, headers=world.admin_h)
        assert response.status_code == 200, (path, response.text)
    tree = world.client.get(base + "/wbs", headers=world.admin_h).json()
    assert tree["numbering_rule"] == {"separator": ".", "min_digits": 1, "prefix": "", "pad_first_level": True}
    assert _row_counts(db_session) == before

    added = world.client.post(base + "/wbs/elements", json={"parent_id": None, "kind": "DELIVERABLE", "name": "四"},
                              headers=world.admin_h)
    assert added.status_code == 201, added.text
    assert added.json()["wbs_code"] == "4.0"  # 消した 2 も、今ある 1・3 も使わない（top_last_child_seq は 3 から）
    rule = db_session.scalar(select(WbsNumberingRule).where(WbsNumberingRule.project_id == raw.id))
    assert rule is not None
    assert (rule.separator, rule.min_digits, rule.prefix, rule.pad_first_level, rule.top_last_child_seq) == (
        ".", 1, "", True, 4)


def test_numbering_rule_row_created_at_once_is_409_conflict(world: World, db_session: Session,
                                                            race: RaceProbe) -> None:
    """API-WBS-002：採番規則の行が無いプロジェクトで、同時の操作が先に行を作った（一意制約の違反）。500 でなく 409 CONFLICT で、
    何も書かない（A-49 の 3）。

    PostgreSQL では、この割り込みは起こらない：要素の操作（``load_wbs_context(for_update=True)``）はプロジェクトの行を ``FOR UPDATE``
    でロックしてから採番規則の行を読んで作り、同時の操作の INSERT は外部キーの確認（プロジェクトの行の ``FOR KEY SHARE``）でその
    ロックを待つ。そこで PostgreSQL では、別のセッションの INSERT がプロジェクトの行のロックを待って lock_timeout（SQLSTATE 55P03）に
    なり、何も書かれないことを確かめる。PostgreSQL でも 409 に届く経路は、次の試験（API-WBS-006）が確かめる。
    """
    raw, _ = _project_without_numbering_rule(world, db_session)
    race.before_insert(WbsNumberingRule, lambda other: other.add(WbsNumberingRule(project_id=raw.id)))
    elements_before = _row_counts(db_session)["wbs_elements"]
    race.locked.clear()

    def post() -> Any:
        return world.client.post(f"/api/v1/projects/{raw.id}/wbs/elements",
                                 json={"parent_id": None, "kind": "DELIVERABLE", "name": "四"}, headers=world.admin_h)

    if db_session.get_bind().dialect.name == "postgresql":
        with pytest.raises(OperationalError) as caught:
            post()
        orig: Any = caught.value.orig
        assert orig.sqlstate == "55P03" and '"projects"' in orig.diag.context  # プロジェクトの行のロックを待った
        assert race.locked == ["projects", "wbs_numbering_rules"]
        assert _row_counts(db_session)["wbs_elements"] == elements_before
        assert db_session.scalars(select(WbsNumberingRule).where(WbsNumberingRule.project_id == raw.id)).all() == []
        return

    response = post()
    assert response.status_code == 409, response.text
    assert response.json()["error"] == {
        "code": "CONFLICT",
        "message": "同時に行われた別の操作と重なったため保存できませんでした。読み直してからやり直してください",
        "details": [],
    }
    assert race.locked == ["projects", "wbs_numbering_rules"]
    assert _row_counts(db_session)["wbs_elements"] == elements_before
    rules = db_session.scalars(select(WbsNumberingRule).where(WbsNumberingRule.project_id == raw.id)).all()
    assert [r.top_last_child_seq for r in rules] == [0]  # 先に作った方の行だけが残る


def test_numbering_rule_put_on_row_created_at_once_is_409_conflict(world: World, db_session: Session,
                                                                 race: RaceProbe) -> None:
    """API-WBS-006：採番規則の行が無いプロジェクトで規則を変える間に、同時の操作が先に行を作った（一意制約の違反）。500 でなく
    409 CONFLICT で、規則を書かない（A-49 の 3）。この操作はプロジェクトの行をロックしないので、PostgreSQL でも同じ割り込みが起こる
    （別のセッションの INSERT は待たない）。"""
    raw, _ = _project_without_numbering_rule(world, db_session)
    race.before_insert(WbsNumberingRule, lambda other: other.add(WbsNumberingRule(project_id=raw.id)))
    race.locked.clear()

    response = world.client.put(f"/api/v1/projects/{raw.id}/wbs/numbering-rule",
                                json={"separator": "-", "min_digits": 2, "prefix": "X", "pad_first_level": False},
                                headers=world.admin_h)
    assert response.status_code == 409, response.text
    assert response.json()["error"] == {
        "code": "CONFLICT",
        "message": "同時に行われた別の操作と重なったため保存できませんでした。読み直してからやり直してください",
        "details": [],
    }
    assert race.locked == ["wbs_numbering_rules"]
    db_session.expire_all()
    rules = db_session.scalars(select(WbsNumberingRule).where(WbsNumberingRule.project_id == raw.id)).all()
    # 先に作った方の行（4.1 の既定の値）だけが残る
    assert [(r.separator, r.min_digits, r.prefix, r.pad_first_level) for r in rules] == [(".", 1, "", True)]


def _pending_by_other(world: World, requested_by: str = "pm") -> Any:
    """先に終わった申請（別のセッション）：WBS を PENDING_APPROVAL にし、承認申請を 1 つ作る。"""
    def submit(other: Session) -> None:
        project = other.get(Project, world.pid)
        assert project is not None
        project.wbs_status = "PENDING_APPROVAL"
        other.add(ApprovalRequest(project_id=world.pid, target_type="WBS", target_id=world.pid,
                                  requested_by=world.users[requested_by].id, requested_at=NOW))
    return submit


def test_submit_judges_wbs_status_after_locking_project(world: World, db_session: Session, race: RaceProbe) -> None:
    """API-WBS-018：同時の 2 つの申請。プロジェクトの行をロックして読み直した状態で判定するので、先の申請がコミットした後の申請は
    409 INVALID_STATE になり、承認申請は 1 つだけ（WBS の承認申請の二重化。OQ-42 (3)・A-49 の 3）。"""
    make_ready_wp(world)
    race.before_lock("projects", _pending_by_other(world))
    race.locked.clear()

    response = world.client.post(world.url("/approval-requests"), headers=world.headers["planner"])
    assert (response.status_code, response.json()["error"]["code"]) == (409, "INVALID_STATE")
    assert race.locked == ["projects", "wbs_numbering_rules"]
    assert response.json()["error"]["details"] == [{"wbs_status": "PENDING_APPROVAL"}]
    assert len(db_session.scalars(select(ApprovalRequest)).all()) == 1


def test_edit_and_reviews_judge_wbs_status_after_locking_project(world: World, db_session: Session,
                                                                 race: RaceProbe) -> None:
    """API-WBS-003・014・016・024：要素の変更・レビューを始める・指摘を書く・レビューを閉じるは、プロジェクトの行をロックして
    読み直した WBS・レビューの状態で判定する。先に申請・レビューを閉じる操作がコミットしていれば 409 INVALID_STATE（A-49 の 3）。"""
    wp = make_ready_wp(world)
    race.before_lock("projects", _pending_by_other(world))
    race.locked.clear()
    changed = world.client.patch(world.url(f"/elements/{wp['id']}"), json={"name": "変えた"},
                                 headers=world.headers["planner"])
    assert (changed.status_code, changed.json()["error"]["code"]) == (409, "INVALID_STATE")
    assert race.locked == ["projects", "wbs_numbering_rules"]
    db_session.expire_all()
    element = db_session.get(WbsElement, wp["id"])
    assert element is not None and element.name == "WP"

    world.set_status("DRAFT")
    race.before_lock("projects", _pending_by_other(world))
    race.locked.clear()
    started = world.client.post(world.url("/reviews"), json={"reviewer_ids": [world.users["pm"].id]},
                                headers=world.headers["planner"])
    assert (started.status_code, started.json()["error"]["code"]) == (409, "INVALID_STATE")
    assert race.locked == ["projects"]
    assert db_session.scalars(select(WbsReview)).all() == []

    world.set_status("DRAFT")
    review = world.client.post(world.url("/reviews"), json={"reviewer_ids": [world.users["pm"].id]},
                               headers=world.headers["planner"]).json()

    def close_by_other(other: Session) -> None:
        row = other.get(WbsReview, review["id"])
        project = other.get(Project, world.pid)
        assert row is not None and project is not None
        row.status, row.closed_at, project.wbs_status = "CLOSED", NOW, "DRAFT"

    race.before_lock("projects", close_by_other)
    race.locked.clear()
    comment = world.client.post(world.url(f"/reviews/{review['id']}/comments"),
                                json={"element_id": wp["id"], "category": "OTHER", "body": "b"}, headers=world.headers["pm"])
    assert (comment.status_code, comment.json()["error"]["code"]) == (409, "INVALID_STATE")
    assert race.locked == ["projects", "wbs_reviews"]
    assert db_session.scalars(select(WbsReviewComment)).all() == []

    db_session.expire_all()
    reopened = db_session.get(WbsReview, review["id"])
    assert reopened is not None
    reopened.status, reopened.closed_at = "OPEN", None
    db_session.commit()
    world.set_status("IN_REVIEW")
    race.before_lock("projects", close_by_other)
    race.locked.clear()
    closed = world.client.post(world.url(f"/reviews/{review['id']}/close"), headers=world.headers["planner"])
    assert (closed.status_code, closed.json()["error"]["code"]) == (409, "INVALID_STATE")
    assert closed.json()["error"]["details"] == [{"review_id": review["id"], "status": "CLOSED"}]
    assert race.locked == ["projects", "wbs_reviews"]


def test_decision_judges_after_locking_target_then_request(world: World, db_session: Session, race: RaceProbe) -> None:
    """API-WBS-019：同時の 2 つの判断。申請の対象（projects）を先に、申請の行を後にロックし、読み直した申請の状態で判定する。
    先の承認がコミットした後の判断は 409 INVALID_STATE で、版を作らない（同時の承認。OQ-42 (3)・A-49 の 3）。"""
    add_member(world, "approver", "APPROVER")
    make_ready_wp(world)
    approval_id = world.client.post(world.url("/approval-requests"), headers=world.headers["pm"]).json()["id"]

    def approve_by_other(other: Session) -> None:
        approval = other.get(ApprovalRequest, approval_id)
        project = other.get(Project, world.pid)
        assert approval is not None and project is not None
        approval.status, approval.decided_by, approval.decided_at = "APPROVED", world.admin.id, NOW
        project.wbs_status = "BASELINED"

    race.before_lock("projects", approve_by_other)
    race.locked.clear()
    response = world.client.post(f"/api/v1/approval-requests/{approval_id}/decision", json={"decision": "REJECT"},
                                 headers=world.headers["approver"])
    assert (response.status_code, response.json()["error"]["code"]) == (409, "INVALID_STATE")
    assert response.json()["error"]["details"] == [{"approval_id": approval_id, "status": "APPROVED"}]
    assert race.locked == ["projects", "wbs_numbering_rules", "approval_requests"]
    assert db_session.scalars(select(ScopeBaseline)).all() == []
    assert world.tree()["wbs_status"] == "BASELINED"
