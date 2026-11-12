"""変更履歴の差分（app.domain.audit）の試験。

設計書 5.1（RULE-CHG-006）・4.1（audit_logs）・4.2・6.1・8 章 NFR-05、要求仕様書 CHG-09。docs/open-questions.md OQ-08。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

import pytest

from app.domain.audit import REDACTED, AuditAction, AuditError, make_audit_diff


class Kind(StrEnum):
    WORK_PACKAGE = "WORK_PACKAGE"


def test_create_records_all_after_values() -> None:
    """RULE-CHG-006：変更前が無ければ CREATE。after は全ての値、before は空。対象の種類・ID・WBS ID を付ける。"""
    diff = make_audit_diff("wbs_elements", 5, None, {"name": "設計", "kind": Kind.WORK_PACKAGE, "parent_id": None},
                           wbs_code="1.1")
    assert diff is not None
    assert (diff.entity_type, diff.entity_id, diff.wbs_code, diff.action) == ("wbs_elements", 5, "1.1",
                                                                              AuditAction.CREATE)
    assert diff.before is None
    assert dict(diff.after or {}) == {"name": "設計", "kind": "WORK_PACKAGE", "parent_id": None}


def test_delete_records_all_before_values() -> None:
    """RULE-CHG-006：変更後が無ければ DELETE。before は全ての値、after は空。"""
    diff = make_audit_diff("activities", 9, {"name": "実装", "duration_days": 5}, None)
    assert diff is not None
    assert (diff.action, dict(diff.before or {}), diff.after, diff.wbs_code) == (
        AuditAction.DELETE, {"name": "実装", "duration_days": 5}, None, None)


def test_update_records_only_changed_fields() -> None:
    """RULE-CHG-006：更新は値が変わった項目だけを前後で残す（CHG-09「変更前後の値」）。"""
    diff = make_audit_diff(
        "activities", 9,
        {"name": "実装", "duration_days": 5, "estimate_hours": Decimal("40.00"), "requested_start": None},
        {"name": "実装", "duration_days": 7, "estimate_hours": Decimal("40.00"), "requested_start": date(2026, 12, 7)},
        wbs_code="1.2",
    )
    assert diff is not None and diff.action is AuditAction.UPDATE
    assert dict(diff.before or {}) == {"duration_days": 5, "requested_start": None}
    assert dict(diff.after or {}) == {"duration_days": 7, "requested_start": "2026-12-07"}


def test_update_without_changes_is_not_recorded() -> None:
    """RULE-CHG-006：変わった項目が 1 つも無い更新は記録しない（None）。"""
    values = {"name": "実装", "estimate_hours": Decimal(40)}
    assert make_audit_diff("activities", 9, values, dict(values)) is None


def test_update_with_added_or_dropped_field_treats_missing_as_none() -> None:
    """RULE-CHG-006：片方にだけある項目は、無い側を None として差分に入れる。"""
    diff = make_audit_diff("projects", 1, {"name": "A", "purpose": "x"}, {"name": "A", "owner_user_id": 3})
    assert diff is not None
    assert dict(diff.before or {}) == {"purpose": "x", "owner_user_id": None}
    assert dict(diff.after or {}) == {"purpose": None, "owner_user_id": 3}


def test_values_are_converted_to_json() -> None:
    """RULE-CHG-006：日付・時刻は ISO 8601、Decimal は文字列、列挙は値、配列・オブジェクトは中まで直す。"""
    at = datetime(2026, 12, 1, 9, 30, tzinfo=UTC)
    diff = make_audit_diff("status_reports", 3, None, {
        "report_date": date(2026, 12, 1), "finalized_at": at, "hours": Decimal("7.50"),
        "roles": [Kind.WORK_PACKAGE], "settings": {"variance_rate": Decimal("0.10"), "ok": True}, "ratio": 0.5,
    })
    assert diff is not None
    assert dict(diff.after or {}) == {
        "report_date": "2026-12-01", "finalized_at": "2026-12-01T09:30:00+00:00", "hours": "7.50",
        "roles": ["WORK_PACKAGE"], "settings": {"variance_rate": "0.10", "ok": True}, "ratio": 0.5,
    }


def test_secret_values_are_redacted() -> None:
    """RULE-CHG-006：パスワード・トークンのハッシュは値を写さず伏せる（OQ-08）。変わったことは残り、空は空のまま。"""
    created = make_audit_diff("users", 1, None, {"login_id": "u1", "password_hash": "secret-hash"})
    assert created is not None and dict(created.after or {}) == {"login_id": "u1", "password_hash": REDACTED}
    changed = make_audit_diff("auth_sessions", 2, {"token_hash": "a", "revoked_at": None},
                              {"token_hash": "b", "revoked_at": None})
    assert changed is not None
    assert (dict(changed.before or {}), dict(changed.after or {})) == ({"token_hash": REDACTED},
                                                                       {"token_hash": REDACTED})
    unchanged_secret = make_audit_diff("users", 1, {"password_hash": "h", "display_name": "A"},
                                       {"password_hash": "h", "display_name": "B"})
    assert unchanged_secret is not None and "password_hash" not in (unchanged_secret.after or {})
    deleted = make_audit_diff("share_links", 4, {"token_hash": None, "scope": "GANTT"}, None)
    assert deleted is not None and dict(deleted.before or {}) == {"token_hash": None, "scope": "GANTT"}
    custom = make_audit_diff("users", 1, None, {"email": "x", "password_hash": "h"}, secret_fields={"email"})
    assert custom is not None and dict(custom.after or {}) == {"email": REDACTED, "password_hash": "h"}


def test_type_change_is_a_change() -> None:
    """RULE-CHG-006：JSON にした値が等しくても型が違えば変更として残す（例：1 と True）。"""
    diff = make_audit_diff("progress_items", 1, {"done": 1}, {"done": True})
    assert diff is not None and dict(diff.after or {}) == {"done": True}


@pytest.mark.parametrize(
    ("args", "kw"),
    [
        (("wbs_elements", 1, None, None), {}),
        (("", 1, None, {"a": 1}), {}),
        (("x" * 65, 1, None, {"a": 1}), {}),
        (("wbs_elements", "1", None, {"a": 1}), {}),
        (("wbs_elements", True, None, {"a": 1}), {}),
        (("wbs_elements", 1, None, {"a": 1}), {"wbs_code": ""}),
        (("wbs_elements", 1, None, {"a": 1}), {"wbs_code": "1" * 101}),
        (("wbs_elements", 1, None, {"a": object()}), {}),
        (("wbs_elements", 1, None, {"a": Decimal("NaN")}), {}),
        (("wbs_elements", 1, None, {"a": float("inf")}), {}),
    ],
)
def test_invalid_inputs_are_rejected(args: tuple[object, ...], kw: dict[str, object]) -> None:
    """RULE-CHG-006：前後の両方が無い、種類が空・65 文字、ID が整数でない、WBS ID が空・101 文字、JSON に直せない値は拒む。"""
    with pytest.raises(AuditError):
        make_audit_diff(*args, **kw)  # type: ignore[arg-type]


def test_length_boundaries_pass() -> None:
    """RULE-CHG-006：種類は 64 文字、WBS ID は 100 文字まで通す（4.1 の列の長さ。A-31）。"""
    diff = make_audit_diff("x" * 64, 1, None, {"a": 1}, wbs_code="1" * 100)
    assert diff is not None and (len(diff.entity_type), len(diff.wbs_code or "")) == (64, 100)
