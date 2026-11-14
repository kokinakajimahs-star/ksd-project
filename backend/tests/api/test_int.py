"""表形式の入出力の API の試験（設計書 6.2 の API-INT-001・002、6.3、9 章。要求仕様書 INT-01・BR-02）。

SQLite の一時ファイルの DB と TestClient で、正常系（出力・往復の一致・追加・更新・移動・日程の再計算・実績工数）と、
401・403・409・422・400 と ``dry_run``、取込の大きさ・行の上限（CR-019）を確かめる。
WBS は 成果物 D（1.0）の下に WP1（1.1。活動 A・B）と WP2（1.2）、管理 M（2.0）の下に WP3（2.1。活動 C。委託先の割当）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from typing import TYPE_CHECKING, Any, cast
from zipfile import ZipFile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette.types import Message

from app.api.io import read_import_file
from app.domain.tabular import ACTIVITY_COLUMNS, TIME_ENTRY_COLUMNS, WBS_COLUMNS, decode_csv, encode_csv
from app.infra.security import generate_token, hash_token
from app.models.chg import AuditLog
from app.models.cmn import AuthSession, User
from app.models.eff import Assignment, TimeEntry
from app.models.est import EstimateRecord
from app.models.prj import Project
from app.models.sch import Activity, ActivityDependency
from app.errors import AppError
from app.models.wbs import WbsDictionaryEntry, WbsElement, WbsNumberingRule
from app.services import io as io_service
from app.services.clock import get_now

if TYPE_CHECKING:
    from conftest import RaceProbe

NOW = datetime(2026, 12, 10, 9, 0, tzinfo=UTC)
CODE = "PRJ-INT"
ROLES = (("pm", "PM"), ("planner", "PLANNER"), ("executor", "EXECUTOR"), ("viewer", "VIEWER"),
         ("contractor", "CONTRACTOR"))
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(autouse=True)
def fixed_now(app: FastAPI) -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)


def make_user(session: Session, login_id: str, *, is_system_admin: bool = False) -> User:
    user = User(login_id=login_id, display_name=f"{login_id}さん", password_hash="unusable",
                is_system_admin=is_system_admin, is_active=True)
    session.add(user)
    session.commit()
    return user


def login(session: Session, user: User) -> dict[str, str]:
    token = generate_token()
    session.add(AuthSession(user_id=user.id, token_hash=hash_token(token), expires_at=NOW + timedelta(hours=12),
                            created_at=NOW))
    session.commit()
    return {"Authorization": f"Bearer {token}"}


def lid(name: str) -> str:
    return f"{name}-{CODE}"


def csv_of(rows: list[list[str]]) -> bytes:
    return encode_csv(rows)


def xlsx_grid(content: bytes) -> list[list[Any]]:
    sheet = load_workbook(BytesIO(content)).worksheets[0]
    return [list(row) for row in sheet.iter_rows(values_only=True)]


class World:
    """管理者が作ったプロジェクト（開始日 2026-12-01）と 5 つのロールのメンバー・メンバーでない利用者。"""

    def __init__(self, client: TestClient, session: Session) -> None:
        self.client = client
        self.session = session
        self.admin = make_user(session, lid("admin"), is_system_admin=True)
        self.admin_h = login(session, self.admin)
        body = {"code": CODE, "name": "入出力", "start_date": "2026-12-01", "owner_user_id": self.admin.id}
        response = client.post("/api/v1/projects", json=body, headers=self.admin_h)
        assert response.status_code == 201, response.text
        self.pid: int = response.json()["id"]
        self.h: dict[str, dict[str, str]] = {}
        self.users: dict[str, User] = {}
        for name, role in ROLES:
            user = make_user(session, lid(name))
            put = client.put(f"/api/v1/projects/{self.pid}/members/{user.id}", json={"roles": [role]},
                             headers=self.admin_h)
            assert put.status_code == 200, put.text
            self.users[name] = user
            self.h[name] = login(session, user)
        self.users["outsider"] = make_user(session, lid("outsider"))
        self.h["outsider"] = login(session, self.users["outsider"])
        self.d = self.element("成果物", None, "DELIVERABLE")
        self.wp1 = self.element("WP1", self.d, "WORK_PACKAGE")
        self.wp2 = self.element("WP2", self.d, "WORK_PACKAGE")
        self.m = self.element("管理", None, "MANAGEMENT")
        self.wp3 = self.element("WP3", self.m, "WORK_PACKAGE")
        self.a = self.add(self.wp1, "A", 5)
        self.b = self.add(self.wp1, "B", 3)
        self.c = self.add(self.wp3, "C", 2)
        assigned = client.put(self.url(f"/activities/{self.c}/assignments"),
                              json={"assignments": [{"user_id": self.users["contractor"].id}]},
                              headers=self.h["planner"])
        assert assigned.status_code == 200, assigned.text

    def url(self, path: str) -> str:
        return f"/api/v1/projects/{self.pid}{path}"

    def element(self, name: str, parent_id: int | None, kind: str) -> int:
        response = self.client.post(self.url("/wbs/elements"), json={"parent_id": parent_id, "kind": kind, "name": name},
                                    headers=self.h["planner"])
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    def add(self, wp: int, name: str, duration_days: int) -> int:
        response = self.client.post(self.url("/activities"),
                                    json={"work_package_id": wp, "name": name, "duration_days": duration_days},
                                    headers=self.h["planner"])
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    def export(self, target: str, fmt: str = "csv", who: str = "planner", expect: int = 200) -> Any:
        response = self.client.get(self.url("/export"), params={"target": target, "format": fmt}, headers=self.h[who])
        assert response.status_code == expect, response.text
        return response

    def grid(self, target: str, who: str = "planner") -> list[list[str]]:
        return decode_csv(self.export(target, who=who).content)

    def load(self, target: str, content: bytes, *, fmt: str = "csv", dry_run: bool = False, who: str = "planner",
             expect: int = 200) -> Any:
        media = "text/csv" if fmt == "csv" else XLSX
        response = self.client.post(
            self.url("/import"), params={"target": target, "format": fmt, "dry_run": str(dry_run).lower()},
            content=content, headers={**self.h[who], "Content-Type": media},
        )
        assert response.status_code == expect, response.text
        return response.json()

    def elements(self) -> dict[str, WbsElement]:
        self.session.expire_all()
        rows = self.session.scalars(select(WbsElement).where(WbsElement.project_id == self.pid))
        return {e.name: e for e in rows}

    def activity(self, activity_id: int) -> Activity:
        self.session.expire_all()
        found = self.session.get(Activity, activity_id)
        assert found is not None
        return found

    def set_wbs_status(self, status: str) -> None:
        self.session.expire_all()
        project = self.session.get(Project, self.pid)
        assert project is not None
        project.wbs_status = status
        self.session.commit()

    def wbs_status(self) -> str:
        self.session.expire_all()
        project = self.session.get(Project, self.pid)
        assert project is not None
        return project.wbs_status

    def grant(self, role: str, permission: str) -> None:
        table = self.client.get(self.url("/role-permissions"), headers=self.admin_h)
        assert table.status_code == 200, table.text
        roles = [
            {"role": r["role"], "permissions": [*r["permissions"], permission] if r["role"] == role else r["permissions"]}
            for r in table.json()["roles"]
        ]
        put = self.client.put(self.url("/role-permissions"), json={"roles": roles}, headers=self.admin_h)
        assert put.status_code == 200, put.text


@pytest.fixture()
def world(client: TestClient, db_session: Session) -> World:
    return World(client, db_session)


# ---------------------------------------------------------------------------
# API-INT-001 出力する
# ---------------------------------------------------------------------------


def test_export_wbs_csv_follows_design_6_3(world: World) -> None:
    """API-INT-001：CSV は UTF-8（BOM 付き）・カンマ区切り・1 行目が 6.3 の見出し。行は WBS の並び（先行順）。"""
    response = world.export("wbs")
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="project-' in response.headers["content-disposition"]
    assert response.content.startswith("﻿".encode())
    grid = decode_csv(response.content)
    assert tuple(grid[0]) == WBS_COLUMNS
    assert [(r[0], r[1], r[2], r[3]) for r in grid[1:]] == [
        ("1.0", "", "DELIVERABLE", "成果物"),
        ("1.1", "1.0", "WORK_PACKAGE", "WP1"),
        ("1.2", "1.0", "WORK_PACKAGE", "WP2"),
        ("2.0", "", "MANAGEMENT", "管理"),
        ("2.1", "2.0", "WORK_PACKAGE", "WP3"),
    ]


def test_export_activities_and_time_entries_columns(world: World, db_session: Session) -> None:
    """API-INT-001：活動は活動の番号（CR-028）・WP の WBS ID・先行（; 区切り）・担当者のログインID、実績工数はログインID・活動ID・作業日・工数。"""
    db_session.add(ActivityDependency(project_id=world.pid, predecessor_id=world.a, successor_id=world.b))
    db_session.add(TimeEntry(user_id=world.users["contractor"].id, activity_id=world.c, work_date=date(2026, 12, 2),
                             hours=Decimal("2.5"), note="調査"))
    db_session.commit()
    activities = world.grid("activities")
    assert tuple(activities[0]) == ACTIVITY_COLUMNS
    assert activities[1:] == [
        [str(world.a), "1.1.1", "1.1", "A", "", "5", "0", "", "", ""],
        [str(world.b), "1.1.2", "1.1", "B", "", "3", "0", "", str(world.a), ""],
        [str(world.c), "2.1.1", "2.1", "C", "", "2", "0", "", "", lid("contractor")],
    ]
    entries = world.grid("time-entries")
    assert tuple(entries[0]) == TIME_ENTRY_COLUMNS
    assert entries[1:] == [[lid("contractor"), str(world.c), "2026-12-02", "2.50", "調査"]]


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
@pytest.mark.parametrize("target", ["wbs", "activities", "time-entries"])
def test_export_then_import_changes_nothing(world: World, db_session: Session, target: str, fmt: str) -> None:
    """API-INT-001・002：出力した直後のファイルを取り込むと、変更は 0 件（6.3 の往復の一致）。"""
    db_session.add(ActivityDependency(project_id=world.pid, predecessor_id=world.a, successor_id=world.b))
    db_session.add(WbsDictionaryEntry(element_id=world.wp1, deliverable="設計書", work_scope="=SUM(1,2) で始まる記述",
                                      completion_criteria=None, assumptions="前提"))
    db_session.add(TimeEntry(user_id=world.users["contractor"].id, activity_id=world.c, work_date=date(2026, 12, 2),
                             hours=Decimal("2.5"), note="調査"))
    db_session.commit()
    content = world.export(target, fmt).content
    expected_rows = {"wbs": 5, "activities": 3, "time-entries": 1}[target]
    for dry_run in (True, False):
        body = world.load(target, content, fmt=fmt, dry_run=dry_run)
        assert body["ok"] is True
        assert (body["created"], body["updated"], body["unchanged"]) == (0, 0, expected_rows)
        assert body["changes"] == [] and body["written"] is False
    assert world.wbs_status() == "DRAFT"


def test_export_xlsx_keeps_cells_as_text(world: World) -> None:
    """API-INT-001：xlsx は 1 枚目のシートに同じ表。"=" で始まる名称も式にせず文字のまま置く。"""
    world.element("=1+1", world.d, "WORK_PACKAGE")
    response = world.export("wbs", "xlsx")
    assert response.headers["content-type"] == XLSX
    grid = xlsx_grid(response.content)
    assert tuple(grid[0]) == WBS_COLUMNS
    assert grid[4][:4] == ["1.3", "1.0", "WORK_PACKAGE", "=1+1"]
    with ZipFile(BytesIO(response.content)) as archive:
        sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode()
    assert "<f>" not in sheet_xml and "<f " not in sheet_xml  # 式のセルが無い


def test_export_csv_escapes_formula_cells_and_import_restores_them(world: World, db_session: Session) -> None:
    """API-INT-001・002：CSV の出力は =・+・-・@・タブ・' で始まるセルの先頭へ ' を 1 つ足し（表計算ソフトが式として読まない）、
    出力したファイルを取り込むと ' を外して変更は 0 件（往復の一致。6.3・CR-019）。xlsx は文字列の型で書き、' を足さない。
    """
    names = ("=1+1", "+加算", "-減算", "@参照", "\tタブ", "'引用")
    for name in names:
        world.element(name, world.d, "WORK_PACKAGE")
    db_session.add(TimeEntry(user_id=world.users["contractor"].id, activity_id=world.c, work_date=date(2026, 12, 2),
                             hours=Decimal("1"), note="=cmd|' /C calc'!A0"))
    db_session.commit()

    wbs = world.export("wbs").content
    text = wbs.decode("utf-8-sig")
    for cell in ("'=1+1", "'+加算", "'-減算", "'@参照", "'\tタブ", "''引用"):
        assert f",WORK_PACKAGE,{cell}," in text
    added = slice(4, 4 + len(names))  # 成果物 1.0 の下の 1.3〜（見出し・1.0・1.1・1.2 の後）
    assert [row[3] for row in decode_csv(wbs)[added]] == list(names)
    body = world.load("wbs", wbs)
    assert (body["created"], body["updated"], body["unchanged"], body["changes"]) == (0, 0, 5 + len(names), [])

    entries = world.export("time-entries").content
    assert ",'=cmd|' /C calc'!A0" in entries.decode("utf-8-sig")
    body = world.load("time-entries", entries)
    assert (body["unchanged"], body["changes"]) == (1, [])

    grid = xlsx_grid(world.export("wbs", "xlsx").content)
    assert [row[3] for row in grid[added]] == list(names)


def test_export_permission_is_data_export(world: World) -> None:
    """API-INT-001：権限は data.export（9.2：PM・PLANNER・PROGRESS_MANAGER・VIEWER）。無ければ 403、未認証は 401。"""
    world.export("wbs", who="viewer")
    for who in ("executor", "contractor", "outsider"):
        world.export("wbs", who=who, expect=403)
    response = world.client.get(world.url("/export"), params={"target": "wbs"})
    assert response.status_code == 401


def test_export_time_entries_also_requires_effort_view(world: World, db_session: Session) -> None:
    """API-INT-001：実績工数（``time-entries``）の出力は data.export に加えて effort.view も要る（設計書 1.3・CR-015）。

    VIEWER は data.export を持ち effort.view を持たない（9.2）ので、WBS・活動は出せるが実績工数は 403。
    effort.view を足せば出せる。両方を持つ PLANNER は今どおり出せる。
    """
    db_session.add(TimeEntry(user_id=world.users["planner"].id, activity_id=world.a, work_date=date(2026, 12, 2),
                             hours=Decimal(3), note="他人の実績"))
    db_session.commit()
    world.export("wbs", who="viewer")
    world.export("activities", who="viewer")
    for fmt in ("csv", "xlsx"):
        refused = world.export("time-entries", fmt, who="viewer", expect=403)
        assert refused.json()["error"]["code"] == "FORBIDDEN"
        assert "他人の実績" not in refused.text
    assert world.grid("time-entries")[1:] == [[lid("planner"), str(world.a), "2026-12-02", "3.00", "他人の実績"]]
    world.grant("VIEWER", "effort.view")
    assert world.grid("time-entries", who="viewer")[1:] == [
        [lid("planner"), str(world.a), "2026-12-02", "3.00", "他人の実績"]]


def test_export_rejects_unknown_target_and_format(world: World) -> None:
    """API-INT-001：``target``・``format`` が 6.2 の値でなければ 400。"""
    response = world.client.get(world.url("/export"), params={"target": "users"}, headers=world.h["planner"])
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    response = world.client.get(world.url("/export"), params={"target": "wbs", "format": "pdf"},
                                headers=world.h["planner"])
    assert response.status_code == 400


def test_export_for_contractor_is_limited_to_own_assignments(world: World, db_session: Session) -> None:
    """API-INT-001：委託先だけで読む利用者は、自分の割当の活動とその WBS の経路だけ（RULE-AUTH-001・9.2 の注記）。

    実績工数は effort.view も要る（CR-015）ので、data.export だけでは 403。effort.view を足しても自分の割当の範囲だけ。
    """
    world.grant("CONTRACTOR", "data.export")
    db_session.add(TimeEntry(user_id=world.users["planner"].id, activity_id=world.a, work_date=date(2026, 12, 2),
                             hours=Decimal(1)))
    db_session.commit()
    assert [r[0] for r in world.grid("wbs", who="contractor")[1:]] == ["2.0", "2.1"]
    assert [r[0] for r in world.grid("activities", who="contractor")[1:]] == [str(world.c)]
    world.export("time-entries", who="contractor", expect=403)
    world.grant("CONTRACTOR", "effort.view")
    assert world.grid("time-entries", who="contractor")[1:] == []


# ---------------------------------------------------------------------------
# API-INT-002 取り込む：wbs
# ---------------------------------------------------------------------------


def test_import_wbs_creates_updates_and_moves(world: World, db_session: Session) -> None:
    """API-INT-002：追加は RULE-WBS-002 で採番（ファイルの ID は仮）、更新は列ごと、親の変更は移動（ID は変えない）。

    見積の値の変更は見積の記録を足し（EST-08）、WBS の状態は DRAFT（RULE-WBS-006 の EDIT）、変更は変更履歴に残る。
    """
    world.set_wbs_status("BASELINED")
    grid = [
        list(WBS_COLUMNS),
        ["1.0", "", "DELIVERABLE", "成果物", "", "", "", "", "", ""],
        ["1.1", "1.0", "WORK_PACKAGE", "WP1 改", lid("planner"), "12.5", "設計書", "", "レビュー済み", ""],
        ["1.2", "NEW-D", "WORK_PACKAGE", "WP2", "", "", "", "", "", ""],
        ["NEW-D", "", "DELIVERABLE", "新しい成果物", "", "", "", "", "", ""],
        ["NEW-W", "NEW-D", "WORK_PACKAGE", "新しいWP", "", "8", "", "", "", ""],
    ]
    preview = world.load("wbs", csv_of(grid), dry_run=True)
    assert preview["ok"] is True and preview["written"] is False
    assert (preview["created"], preview["updated"], preview["unchanged"]) == (2, 2, 1)
    assert "新しい成果物" not in world.elements()

    body = world.load("wbs", csv_of(grid))
    assert body["written"] is True
    assert {c["row"]: c["key"] for c in body["changes"] if c["action"] == "CREATE"} == {5: "3.0", 6: "3.1"}
    by_row = {c["row"]: c for c in body["changes"]}
    assert set(by_row[3]["changed"]) == {"名称", "責任者のログインID", "自分の見積（人時）", "成果物", "完了条件"}
    assert by_row[4]["changed"] == ["親のWBS ID"]

    elements = world.elements()
    new_d, new_w, wp1, wp2 = elements["新しい成果物"], elements["新しいWP"], elements["WP1 改"], elements["WP2"]
    assert (new_d.wbs_code, new_d.parent_id, new_w.wbs_code, new_w.parent_id) == ("3.0", None, "3.1", new_d.id)
    assert (wp2.wbs_code, wp2.parent_id, wp2.needs_renumber) == ("1.2", new_d.id, True)  # ID は変えず改番待ち
    assert wp1.responsible_user_id == world.users["planner"].id and wp1.own_estimate_hours == Decimal("12.5")
    entry = db_session.scalar(select(WbsDictionaryEntry).where(WbsDictionaryEntry.element_id == wp1.id))
    assert entry is not None and (entry.deliverable, entry.completion_criteria) == ("設計書", "レビュー済み")
    records = db_session.scalars(select(EstimateRecord).where(EstimateRecord.project_id == world.pid)).all()
    assert sorted((r.target_id, r.hours, r.input_unit) for r in records) == sorted(
        [(wp1.id, Decimal("12.5"), "HOUR"), (new_w.id, Decimal(8), "HOUR")])
    assert world.wbs_status() == "DRAFT"
    logged = db_session.scalars(select(AuditLog).where(AuditLog.entity_type == "wbs_elements")).all()
    assert any(log.entity_id == new_w.id for log in logged)
    # 書いた後に出力して取り込むと変更は 0 件（往復の一致）
    again = world.load("wbs", world.export("wbs").content, dry_run=True)
    assert (again["created"], again["updated"]) == (0, 0)


def test_import_wbs_does_not_reuse_first_level_numbers(world: World, db_session: Session) -> None:
    """API-INT-002：第 1 階層への追加・移動も数え（top_last_child_seq）を進め、消した番号を再利用しない（設計書 1.1 の 5.2。CR-003）。"""
    removed = world.element("消す成果物", None, "DELIVERABLE")
    assert world.elements()["消す成果物"].wbs_code == "3.0"
    deleted = world.client.delete(world.url(f"/wbs/elements/{removed}"), headers=world.h["planner"])
    assert deleted.status_code == 204, deleted.text

    grid = [
        list(WBS_COLUMNS),
        ["NEW", "", "DELIVERABLE", "新しい成果物", "", "", "", "", "", ""],
        ["1.2", "", "WORK_PACKAGE", "WP2", "", "", "", "", "", ""],
    ]
    body = world.load("wbs", csv_of(grid))
    assert body["written"] is True
    assert [c["key"] for c in body["changes"] if c["action"] == "CREATE"] == ["4.0"]
    elements = world.elements()
    assert (elements["新しい成果物"].sibling_seq, elements["WP2"].sibling_seq, elements["WP2"].parent_id) == (4, 5, None)
    db_session.expire_all()
    rule = db_session.scalar(select(WbsNumberingRule).where(WbsNumberingRule.project_id == world.pid))
    assert rule is not None and rule.top_last_child_seq == 5


def test_import_wbs_resolves_old_code(world: World) -> None:
    """API-INT-002：旧 ID で書かれた行は今の要素へ解決して更新する（RULE-WBS-002。追加にしない）。"""
    move = world.client.post(world.url(f"/wbs/elements/{world.wp2}/move"), json={"parent_id": world.m},
                             headers=world.h["planner"])
    assert move.status_code == 200, move.text
    renumber = world.client.post(world.url("/wbs/renumber"), json={}, headers=world.h["planner"])
    assert renumber.status_code == 200, renumber.text
    new_code = world.elements()["WP2"].wbs_code
    assert new_code != "1.2"
    grid = [list(WBS_COLUMNS), ["1.2", new_code.rsplit(".", 1)[0] + ".0", "WORK_PACKAGE", "WP2 改", "", "", "", "", "", ""]]
    body = world.load("wbs", csv_of(grid))
    assert (body["created"], body["updated"]) == (0, 1)
    assert body["changes"][0]["key"] == new_code
    assert "WP2 改" in world.elements()


def test_import_with_errors_writes_nothing(world: World) -> None:
    """API-INT-002：誤りが 1 件でもあれば何も書かずに 422 ``IMPORT_INVALID``。誤りは ``{row, column, code, message}`` の全件。"""
    grid = [
        list(WBS_COLUMNS),
        ["1.1", "1.0", "WORK_PACKAGE", "WP1 改", "", "", "", "", "", ""],
        ["NEW", "9.9", "WORK_PACKAGE", "親が無い", "", "", "", "", "", ""],
        ["NEW2", "1.0", "UNKNOWN", "", "nobody", "-1", "", "", "", ""],
    ]
    response = world.client.post(world.url("/import"), params={"target": "wbs", "format": "csv"},
                                 content=csv_of(grid), headers={**world.h["planner"], "Content-Type": "text/csv"})
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "IMPORT_INVALID"
    assert [(d["row"], d["column"], d["code"]) for d in error["details"]] == [
        (3, "親のWBS ID", "NOT_FOUND"),
        (4, "区分", "INVALID_VALUE"),
        (4, "名称", "REQUIRED"),
        (4, "責任者のログインID", "NOT_FOUND"),
        (4, "自分の見積（人時）", "OUT_OF_RANGE"),
    ]
    assert "WP1 改" not in world.elements()

    preview = world.load("wbs", csv_of(grid), dry_run=True)
    assert preview["ok"] is False and preview["written"] is False and preview["changes"] == []
    assert preview["issues"] == error["details"]


def test_import_rejects_responsible_who_is_not_a_member(world: World) -> None:
    """API-INT-002：責任者に置けるのはメンバーである有効な利用者だけ（API-WBS-003 と同じ。WBS-10）。"""
    grid = [list(WBS_COLUMNS), ["1.1", "1.0", "WORK_PACKAGE", "WP1", lid("outsider"), "", "", "", "", ""]]
    body = world.load("wbs", csv_of(grid), dry_run=True)
    assert [(i["row"], i["column"], i["code"]) for i in body["issues"]] == [(2, "責任者のログインID", "NOT_FOUND")]


def test_import_wbs_while_pending_approval_is_409(world: World) -> None:
    """API-INT-002：WBS を変える取込は承認待ちの間は 409 ``INVALID_STATE``（RULE-WBS-006）。変えない取込は通る。"""
    world.set_wbs_status("PENDING_APPROVAL")
    changed = [list(WBS_COLUMNS), ["1.1", "1.0", "WORK_PACKAGE", "WP1 改", "", "", "", "", "", ""]]
    for dry_run in (True, False):
        response = world.client.post(world.url("/import"), params={"target": "wbs", "dry_run": str(dry_run).lower()},
                                     content=csv_of(changed), headers={**world.h["planner"], "Content-Type": "text/csv"})
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "INVALID_STATE"
    body = world.load("wbs", world.export("wbs").content)
    assert body["unchanged"] == 5 and world.wbs_status() == "PENDING_APPROVAL"


def test_import_permission_is_data_import(world: World) -> None:
    """API-INT-002：権限は data.import（9.2：PM・PLANNER）。無ければ 403（何も読まない）、未認証は 401。"""
    content = world.export("wbs").content
    world.load("wbs", content, who="pm")
    for who in ("viewer", "executor", "contractor", "outsider"):
        body = world.load("wbs", content, who=who, expect=403)
        assert body["error"]["code"] == "FORBIDDEN"
    response = world.client.post(world.url("/import"), params={"target": "wbs"}, content=content,
                                 headers={"Content-Type": "text/csv"})
    assert response.status_code == 401
    missing = world.client.post("/api/v1/projects/9999/import", params={"target": "wbs"}, content=content,
                                headers={**world.h["planner"], "Content-Type": "text/csv"})
    assert missing.status_code == 404


def test_import_requires_write_permission_of_target(world: World, db_session: Session) -> None:
    """API-INT-002：data.import に加えて対象の書く権限（wbs は wbs.edit、activities は schedule.edit、見積の列を変えるなら
    estimate.edit も。time-entries は data.import だけ）。設計書 1.1・CR-006：data.import だけのロールで迂回させない。"""
    world.grant("EXECUTOR", "data.import")
    wbs = world.grid("wbs")
    wbs[2][3] = "WP1（改）"
    for dry_run in (True, False):
        body = world.load("wbs", csv_of(wbs), who="executor", dry_run=dry_run, expect=403)
        assert body["error"]["code"] == "FORBIDDEN"
    activities = world.grid("activities")
    activities[1][3] = "A（改）"
    world.load("activities", csv_of(activities), who="executor", expect=403)
    assert world.activity(world.a).name == "A" and world.elements()["WP1"].name == "WP1"

    # schedule.edit があれば見積以外の列は書ける。見積の列を変える行があれば estimate.edit も要る（dry_run でも）
    world.grant("EXECUTOR", "schedule.edit")
    assert world.load("activities", csv_of(activities), who="executor")["written"] is True
    assert world.activity(world.a).name == "A（改）"
    estimate = world.grid("activities")
    estimate[1][7] = "12"
    for dry_run in (True, False):
        world.load("activities", csv_of(estimate), who="executor", dry_run=dry_run, expect=403)
    added = world.grid("activities") + [["", "", "1.1", "D", "", "2", "0", "4", "", ""]]
    world.load("activities", csv_of(added), who="executor", expect=403)
    assert world.activity(world.a).estimate_hours is None
    world.grant("EXECUTOR", "estimate.edit")
    assert world.load("activities", csv_of(estimate), who="executor")["written"] is True
    assert world.activity(world.a).estimate_hours == Decimal("12")

    # wbs は wbs.edit があれば書ける
    world.grant("EXECUTOR", "wbs.edit")
    assert world.load("wbs", csv_of(wbs), who="executor")["written"] is True

    # time-entries は data.import だけで書ける（他人の行を書く権限が 9.1 に無い）
    db_session.add(TimeEntry(user_id=world.users["contractor"].id, activity_id=world.c, work_date=date(2026, 12, 2),
                             hours=Decimal("2.5"), note="調査"))
    db_session.commit()
    entries = world.grid("time-entries")
    entries[1][4] = "調査（追記）"
    world.grant("VIEWER", "data.import")
    assert world.load("time-entries", csv_of(entries), who="viewer")["written"] is True


def test_import_unreadable_files_report_file_unreadable(world: World) -> None:
    """API-INT-002：UTF-8 でない CSV・xlsx でないファイル・見出しの違う表は、行の誤りとして返す（何も書かない）。"""
    sjis = world.load("wbs", "WBS ID,名称\n".encode("shift_jis"), dry_run=True)
    assert [i["code"] for i in sjis["issues"]] == ["FILE_UNREADABLE"]
    broken = world.load("wbs", b"not a zip", fmt="xlsx", dry_run=True)
    assert [i["code"] for i in broken["issues"]] == ["FILE_UNREADABLE"]
    header = world.load("activities", csv_of([list(WBS_COLUMNS)]), dry_run=True)
    assert [(i["row"], i["code"]) for i in header["issues"]] == [(1, "HEADER_MISMATCH")]
    empty = world.load("wbs", b"", dry_run=True)
    assert [i["code"] for i in empty["issues"]] == ["HEADER_MISMATCH"]


def test_import_xlsx_reads_typed_cells(world: World) -> None:
    """API-INT-002：xlsx のセルの数・日付は 6.3 の文字列に直して読む（1 枚目のシート）。"""
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(list(ACTIVITY_COLUMNS))
    sheet.append([world.a, 1.1, "1.1", "A", datetime(2026, 12, 7), 4, 0, 6.5, None, lid("executor")])  # 番号は読まない
    buffer = BytesIO()
    workbook.save(buffer)
    body = world.load("activities", buffer.getvalue(), fmt="xlsx")
    assert body["written"] is True and body["updated"] == 1
    a = world.activity(world.a)
    assert (a.requested_start, a.duration_days, a.estimate_hours) == (date(2026, 12, 7), 4, Decimal("6.5"))


def xlsx_of(rows: list[list[str]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def limit_import(app: FastAPI, **limits: int) -> None:
    """取込の上限（``PMT_IMPORT_MAX_BYTES``・``PMT_IMPORT_MAX_ROWS``）をこの試験の app だけで小さくする。"""
    app.state.settings = app.state.settings.model_copy(update=limits)


def post_import(world: World, content: bytes, *, fmt: str = "csv", dry_run: bool = False) -> Any:
    media = "text/csv" if fmt == "csv" else XLSX
    return world.client.post(world.url("/import"), params={"target": "wbs", "format": fmt, "dry_run": str(dry_run).lower()},
                             content=content, headers={**world.h["planner"], "Content-Type": media})


def test_import_file_over_max_bytes_is_400_and_writes_nothing(world: World, app: FastAPI) -> None:
    """API-INT-002：ファイルが ``PMT_IMPORT_MAX_BYTES`` を超えれば 400 ``VALIDATION_ERROR`` で何も書かない（dry_run でも。CR-019）。
    上限ちょうどは読む。"""
    grid = world.grid("wbs")
    grid[2][3] = "WP1（改）"
    for fmt, content in (("csv", csv_of(grid)), ("xlsx", xlsx_of(grid))):
        limit_import(app, import_max_bytes=len(content) - 1)
        for dry_run in (True, False):
            response = post_import(world, content, fmt=fmt, dry_run=dry_run)
            assert response.status_code == 400, response.text
            error = response.json()["error"]
            assert error["code"] == "VALIDATION_ERROR"
            assert [d["loc"] for d in error["details"]] == [["body"]]
            assert str(len(content) - 1) in error["details"][0]["message"]
        assert world.elements()["WP1"].name == "WP1"

    content = csv_of(grid)
    limit_import(app, import_max_bytes=len(content))
    response = post_import(world, content)
    assert response.status_code == 200, response.text
    assert response.json()["written"] is True and "WP1（改）" in world.elements()


def test_import_rows_over_max_rows_is_400_and_writes_nothing(world: World, app: FastAPI) -> None:
    """API-INT-002：見出しを除く行が ``PMT_IMPORT_MAX_ROWS`` を超えれば 400 ``VALIDATION_ERROR`` で何も書かない
    （CSV・xlsx、dry_run でも。CR-019）。上限ちょうどは読む。"""
    grid = world.grid("wbs")
    grid[2][3] = "WP1（改）"
    rows = len(grid) - 1
    limit_import(app, import_max_rows=rows - 1)
    for fmt, content in (("csv", csv_of(grid)), ("xlsx", xlsx_of(grid))):
        for dry_run in (True, False):
            response = post_import(world, content, fmt=fmt, dry_run=dry_run)
            assert response.status_code == 400, response.text
            error = response.json()["error"]
            assert error["code"] == "VALIDATION_ERROR"
            assert [d["loc"] for d in error["details"]] == [["body"]]
            assert str(rows - 1) in error["details"][0]["message"]
    assert world.elements()["WP1"].name == "WP1"

    limit_import(app, import_max_rows=rows)
    preview = post_import(world, xlsx_of(grid), fmt="xlsx", dry_run=True)
    assert preview.status_code == 200, preview.text
    assert preview.json()["ok"] is True and preview.json()["updated"] == 1
    response = post_import(world, csv_of(grid))
    assert response.status_code == 200, response.text
    assert response.json()["written"] is True and "WP1（改）" in world.elements()


def test_import_csv_stops_reading_rows_past_max_rows(world: World, app: FastAPI) -> None:
    """API-INT-002：CSV は見出しと ``PMT_IMPORT_MAX_ROWS`` 行を 1 行超えたところで読むのを止める（CR-019）。
    その先の行（引用符の閉じていない行）を読まないので、誤りは読めない行（FILE_UNREADABLE）でなく行の上限の 400。"""
    grid = world.grid("wbs")
    rows = len(grid) - 1
    limit_import(app, import_max_rows=rows)
    content = csv_of(grid) + b"x,,,,,,,,,\r\n" + b'"closed never,\r\n'
    response = post_import(world, content, dry_run=True)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["details"][0]["loc"] == ["body"]
    # 上限を超えない行の数なら最後まで読み、閉じていない引用符は今どおり読めない行の誤り
    limit_import(app, import_max_rows=rows + 5)
    unreadable = post_import(world, content, dry_run=True)
    assert unreadable.status_code == 200, unreadable.text
    assert [i["code"] for i in unreadable.json()["issues"]] == ["FILE_UNREADABLE"]


def test_import_xlsx_keeps_rows_narrow_and_reports_cells_far_right(world: World) -> None:
    """API-INT-002：xlsx は宣言された範囲（dimension）で行を右へ埋めず、``XLSX_MAX_COLUMNS`` 列より右は右端の値だけを持つ
    （数 KB のファイルからメモリを膨らませない。CR-019）。見出しより右の値は今どおり ``TOO_MANY_COLUMNS``。"""
    grid = world.grid("wbs")
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    for row in grid:
        sheet.append(row)
    sheet.cell(row=2, column=16384, value="右端")  # XFD2
    sheet.cell(row=len(grid) + 3, column=16000)  # 値の無いセル：範囲（dimension）だけを広げる
    buffer = BytesIO()
    workbook.save(buffer)
    content = buffer.getvalue()

    read = io_service.read_grid(io_service.FileFormat.XLSX, content, max_rows=100)
    assert max(len(row) for row in read) == io_service.XLSX_MAX_COLUMNS + 1
    assert read[1][-1] == "右端"
    # 範囲（XFD まで）で右へ埋めない：XFD に値を置いた行のほかは、見出しの列の数を超えない
    assert all(len(row) <= len(WBS_COLUMNS) for index, row in enumerate(read) if index != 1)

    body = world.load("wbs", content, fmt="xlsx", dry_run=True)
    assert [(i["row"], i["code"]) for i in body["issues"]] == [(2, "TOO_MANY_COLUMNS")]


def test_import_file_reader_stops_reading_at_max_bytes() -> None:
    """API-INT-002：本体は ``PMT_IMPORT_MAX_BYTES`` を超えた時点で読むのを止める（残りを受け取らない。CR-019）。
    ``Content-Length`` が上限を超えていれば 1 度も受け取らない。上限ちょうどは全て読む。
    本体を受け取る前に、認証で読んだトランザクションを閉じる（DB の接続を持ったまま遅い送り手を待たない）。"""

    class FakeSession:
        def __init__(self) -> None:
            self.events: list[str] = []

        def commit(self) -> None:
            self.events.append("commit")

    def run(chunks: list[bytes], max_bytes: int, headers: list[tuple[bytes, bytes]]) -> tuple[bytes | AppError, int]:
        received = 0
        session = FakeSession()

        async def receive() -> Message:
            nonlocal received
            assert session.events == ["commit"], "本体を受け取る前に接続を返していない"
            received += 1
            index = received - 1
            return {"type": "http.request", "body": chunks[index], "more_body": index + 1 < len(chunks)}

        request = Request({"type": "http", "method": "POST", "path": "/", "headers": headers}, receive)
        try:
            result: bytes | AppError = asyncio.run(
                read_import_file(request, max_bytes=max_bytes, _user=cast(User, None), session=cast(Any, session)))
        except AppError as exc:
            result = exc
        assert session.events == ["commit"]
        return result, received

    chunks = [b"x" * 10] * 100
    too_large, received = run(chunks, 25, [])
    assert isinstance(too_large, AppError) and (too_large.status_code, too_large.code) == (400, "VALIDATION_ERROR")
    assert received == 3

    declared, received = run(chunks, 25, [(b"content-length", b"1000")])
    assert isinstance(declared, AppError) and declared.code == "VALIDATION_ERROR"
    assert received == 0

    understated, received = run(chunks, 25, [(b"content-length", b"20")])
    assert isinstance(understated, AppError) and understated.code == "VALIDATION_ERROR"
    assert received == 3

    exact, received = run([b"x" * 10, b"y" * 10, b"z" * 5], 25, [(b"content-length", b"25")])
    assert exact == b"x" * 10 + b"y" * 10 + b"z" * 5
    assert received == 3


# ---------------------------------------------------------------------------
# API-INT-002 取り込む：activities
# ---------------------------------------------------------------------------


def test_import_activities_writes_dependencies_assignees_and_recalculates(world: World, db_session: Session) -> None:
    """API-INT-002：活動の追加（活動ID が空）・更新、先行は活動の先行の全て、担当の置き換え。日程を計算し直す（RULE-SCH-002）。"""
    grid = [
        list(ACTIVITY_COLUMNS),
        [str(world.a), "1.1.1", "1.1", "A", "", "5", "0", "", "", ""],
        [str(world.b), "1.1.2", "1.1", "B", "", "2", "0", "10", str(world.a), f"{lid('executor')};{lid('planner')}"],
        ["", "", "1.2", "D", "", "0", "1", "", f"{world.b};{world.c}", ""],
    ]
    body = world.load("activities", csv_of(grid))
    assert body["written"] is True
    assert (body["created"], body["updated"], body["unchanged"]) == (1, 1, 1)
    new_id = int(next(c["key"] for c in body["changes"] if c["action"] == "CREATE"))
    b, d = world.activity(world.b), world.activity(new_id)
    assert (b.duration_days, b.estimate_hours) == (2, Decimal(10))
    assert (d.work_package_id, d.duration_days, d.is_milestone) == (world.wp2, 0, True)
    # A（12/1〜12/7 の 5 稼働日）→ B（12/8〜12/9）→ D（マイルストーン）
    assert (b.start_date, b.finish_date) == (date(2026, 12, 8), date(2026, 12, 9))
    assert d.start_date is not None and d.start_date >= date(2026, 12, 9)
    pairs = set(db_session.execute(select(ActivityDependency.predecessor_id, ActivityDependency.successor_id)
                                   .where(ActivityDependency.project_id == world.pid)).tuples())
    assert pairs == {(world.a, world.b), (world.b, new_id), (world.c, new_id)}
    assignees = set(db_session.scalars(select(Assignment.user_id).where(Assignment.activity_id == world.b)))
    assert assignees == {world.users["executor"].id, world.users["planner"].id}
    records = db_session.scalars(select(EstimateRecord).where(EstimateRecord.target_type == "ACTIVITY")).all()
    assert [(r.target_id, r.hours) for r in records] == [(world.b, Decimal(10))]

    # 先行を外す（ファイルの値がその活動の先行の全て）
    grid[2][8] = ""
    body = world.load("activities", csv_of(grid[:3]))
    assert body["changes"][0]["changed"] == ["先行の活動ID（; 区切り）"]
    db_session.expire_all()
    assert db_session.scalars(select(ActivityDependency).where(ActivityDependency.successor_id == world.b)).all() == []


def test_import_activities_cycle_is_rejected(world: World) -> None:
    """API-INT-002：取り込んだ後の依存が循環すれば ``SCHEDULE_CYCLE``（RULE-SCH-001）。何も書かない。"""
    grid = [
        list(ACTIVITY_COLUMNS),
        [str(world.a), "1.1.1", "1.1", "A", "", "5", "0", "", str(world.b), ""],
        [str(world.b), "1.1.2", "1.1", "B", "", "3", "0", "", str(world.a), ""],
    ]
    body = world.load("activities", csv_of(grid), expect=422)
    assert body["error"]["code"] == "IMPORT_INVALID"
    assert {d["code"] for d in body["error"]["details"]} == {"SCHEDULE_CYCLE"}
    assert world.client.get(world.url("/activities"), headers=world.h["planner"]).status_code == 200
    assert world.grid("activities")[1][8] == ""


def test_import_activities_rejects_assignee_who_is_not_a_member(world: World) -> None:
    """API-INT-002：新しく置く担当はメンバーである有効な利用者だけ（API-EFF-001 と同じ。EFF-01）。"""
    grid = [list(ACTIVITY_COLUMNS), [str(world.a), "1.1.1", "1.1", "A", "", "5", "0", "", "", lid("outsider")]]
    body = world.load("activities", csv_of(grid), dry_run=True)
    assert [(i["row"], i["code"]) for i in body["issues"]] == [(2, "NOT_FOUND")]


def test_import_activities_judges_start_after_locking_project(world: World, race: RaceProbe) -> None:
    """API-INT-002（CR-018・A-49 の 3）：書く取込は、プロジェクトの行をロックしてから開始日を読む。ロックの直前に別の操作が
    開始日を 12-03 にしてコミットしていれば、12-02 の希望開始日の行は ``BEFORE_PROJECT_START`` で何も書かない。"""
    grid = [list(ACTIVITY_COLUMNS), [str(world.a), "1.1.1", "1.1", "A", "2026-12-02", "5", "0", "", "", ""]]

    def move_project_start(other: Session) -> None:
        project = other.get(Project, world.pid)
        assert project is not None
        project.start_date = date(2026, 12, 3)

    race.before_lock("projects", move_project_start)
    race.locked.clear()
    body = world.load("activities", csv_of(grid), expect=422)
    assert [(d["row"], d["code"]) for d in body["error"]["details"]] == [(2, "BEFORE_PROJECT_START")]
    assert race.locked[:1] == ["projects"]
    assert world.grid("activities")[1][4] != "2026-12-02"


def test_import_activities_rejects_requested_start_before_project_start(world: World, db_session: Session) -> None:
    """API-INT-002：書く希望開始日がプロジェクトの開始日（2026-12-01）より前になる行は ``BEFORE_PROJECT_START``（5.5・CR-018）。
    6.3 の検査の誤り（``{行, 列, コード, メッセージ}``）として該当する行の全てを返し、何も書かない。開始日ちょうどと、今の値のまま
    （この版より前に保存された、開始日より前の希望開始日）の行は拒まない。
    """
    db_session.execute(update(Activity).where(Activity.id == world.c).values(requested_start=date(2026, 11, 27)))
    db_session.commit()
    grid = [
        list(ACTIVITY_COLUMNS),
        [str(world.a), "1.1.1", "1.1", "A", "2026-11-30", "5", "0", "", "", ""],
        [str(world.b), "1.1.2", "1.1", "B", "2026-12-01", "3", "0", "", "", ""],
        ["", "", "1.2", "新", "2026-11-27", "1", "0", "", "", ""],
        [str(world.c), "2.1.1", "2.1", "C 改", "2026-11-27", "4", "0", "", "", lid("contractor")],
    ]
    expected = [(2, "希望開始日", "BEFORE_PROJECT_START"), (4, "希望開始日", "BEFORE_PROJECT_START")]
    dry = world.load("activities", csv_of(grid), dry_run=True)
    assert [(i["row"], i["column"], i["code"]) for i in dry["issues"]] == expected
    assert dry["issues"][0]["message"] == "希望開始日 2026-11-30 がプロジェクトの開始日 2026-12-01 より前です"
    body = world.load("activities", csv_of(grid), expect=422)
    assert body["error"]["code"] == "IMPORT_INVALID"
    assert [(d["row"], d["column"], d["code"]) for d in body["error"]["details"]] == expected
    a, b, c = world.activity(world.a), world.activity(world.b), world.activity(world.c)
    assert (a.requested_start, b.requested_start, c.name, c.duration_days) == (None, None, "C", 2)
    assert db_session.scalar(select(func.count()).select_from(Activity).where(Activity.project_id == world.pid)) == 3

    # 誤りの行を直せば、今の値のまま（11-27）の C は名称・期間を変えて書ける
    written = world.load("activities", csv_of([grid[0], grid[4]]))
    assert written["written"] is True
    c = world.activity(world.c)
    assert (c.name, c.duration_days, c.requested_start) == ("C 改", 4, date(2026, 11, 27))


# ---------------------------------------------------------------------------
# API-INT-002 取り込む：time-entries
# ---------------------------------------------------------------------------


def test_import_time_entries_writes_and_checks_rule_eff_005(world: World, db_session: Session) -> None:
    """API-INT-002：ログインID＋活動ID＋作業日で照合して書く。0 時間は行を消す。割当でない活動は ``NOT_ASSIGNED``（RULE-EFF-005）。"""
    contractor = world.users["contractor"].id
    db_session.add(TimeEntry(user_id=contractor, activity_id=world.c, work_date=date(2026, 12, 3), hours=Decimal(3)))
    db_session.commit()
    grid = [
        list(TIME_ENTRY_COLUMNS),
        [lid("contractor"), str(world.c), "2026-12-02", "7.5", "調査"],
        [lid("contractor"), str(world.c), "2026-12-03", "0", ""],
    ]
    body = world.load("time-entries", csv_of(grid))
    assert body["written"] is True and (body["created"], body["updated"]) == (1, 1)
    db_session.expire_all()
    rows = db_session.scalars(select(TimeEntry).where(TimeEntry.user_id == contractor)).all()
    assert [(r.work_date, r.hours, r.note) for r in rows] == [(date(2026, 12, 2), Decimal("7.5"), "調査")]

    not_assigned = [list(TIME_ENTRY_COLUMNS), [lid("executor"), str(world.a), "2026-12-02", "1", ""]]
    preview = world.load("time-entries", csv_of(not_assigned), dry_run=True)
    assert [(i["row"], i["column"], i["code"]) for i in preview["issues"]] == [(2, "活動ID", "NOT_ASSIGNED")]
    too_many = [list(TIME_ENTRY_COLUMNS), [lid("contractor"), str(world.c), "2026-12-02", "0", "メモ"]]
    preview = world.load("time-entries", csv_of(too_many), dry_run=True)
    assert [(i["row"], i["column"], i["code"]) for i in preview["issues"]] == [(2, "備考", "INVALID_VALUE")]
