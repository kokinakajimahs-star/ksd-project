"""WBS の API の試験（設計書 6.2 の API-WBS-001〜023、5.2・5.3・5.10、9 章）。

SQLite の一時ファイルの DB と TestClient で、正常系と 401・403・404・409・422・400 のうち該当するものを確かめる。
採番は要求仕様書 W§4 の例（1.0、1.1、1.2、2.0、2.1）を API で作って確かめる（5.2）。
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
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
from app.models.cmn import AuthSession, User
from app.models.prj import Project
from app.models.sch import Activity
from app.models.wbs import (
    ApprovalRequest,
    ScopeBaseline,
    ScopeBaselineElement,
    WbsCodeHistory,
    WbsDictionaryEntry,
    WbsElement,
    WbsReview,
    WbsReviewComment,
    WbsScopeLink,
)
from app.services.clock import get_now
from app.services.project_access import ProjectMembersTable
from app.services.wbs import (
    ActivitiesTableAbsent,
    ChangeRequestsTableAbsent,
    CrossGroupReaders,
    eac_not_available,
)

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


def test_delete_baselined_element_is_422(world: World, db_session: Session) -> None:
    """API-WBS-005：スコープベースラインの版に入った要素は 422 ELEMENT_IN_BASELINE（CR-002 の暫定。版の写しを変えない）。"""
    element = world.add("基準の要素")
    baseline = ScopeBaseline(project_id=world.pid, version=1, reason="INITIAL_APPROVAL", created_by=world.admin.id)
    db_session.add(baseline)
    db_session.flush()
    db_session.add(ScopeBaselineElement(scope_baseline_id=baseline.id, element_id=element["id"], wbs_code="1.0",
                                        kind="DELIVERABLE", name="基準の要素"))
    db_session.commit()
    response = world.client.delete(world.url(f"/elements/{element['id']}"), headers=world.headers["planner"])
    assert (response.status_code, response.json()["error"]["code"]) == (422, "ELEMENT_IN_BASELINE")


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
    """API-WBS-014〜017：レビュー中の開始は 409、メンバーでない人は 400、権限なしは 403、無いものは 404、閉じたレビューは 409。"""
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
    assert world.client.patch(f"{comments_url}/{comment['id']}", json={"status": "OPEN"},
                              headers=world.headers["planner"]).status_code == 409


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
    """活動の表（DB-SCH-001）の代わり。活動 → WP の要素。"""

    def __init__(self, placement: dict[int, int]) -> None:
        self.placement = placement

    def activities(self, session: Session, project_id: int) -> list[ActivityEstimate]:
        return [ActivityEstimate(id=a, work_package_id=wp) for a, wp in self.placement.items()]


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
