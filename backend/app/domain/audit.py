"""変更履歴の差分（設計書 5.1 の RULE-CHG-006、4.1 の ``audit_logs``、4.2、8 章 NFR-05。要求仕様書 CHG-09）。

書き込みの操作 1 件の変更前後の値から、変更履歴の 1 行に入れる差分を作り、対象の種類・ID・WBS ID を付ける。

- 追加（変更前が無い）：``CREATE``。``before`` は空、``after`` は全ての値
- 削除（変更後が無い）：``DELETE``。``before`` は全ての値、``after`` は空
- 更新（両方ある）：``UPDATE``。``before``・``after`` は**値が変わった項目だけ**。変わった項目が無ければ記録しない（``None`` を返す）
- 値は JSON に入る形へ直す（日付・時刻は ISO 8601、``Decimal`` は文字列、列挙は値）
- 秘密の値（パスワード・トークンのハッシュ。docs/open-questions.md OQ-08）は値を写さず、伏せた印に置き換える（変わったことは残る）

記録（``audit_logs`` へ足すこと）と、変更者・プロジェクト・時刻を付けることは infra の SQLAlchemy のイベントが行う（6.1・1.3）。
純粋に保つ（1.3）。DB・HTTP・時刻・乱数に触れない。
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum, StrEnum
from types import MappingProxyType
from typing import Any, Final

# 4.1 の audit_logs の列の長さ（A-31：entity_type は 64 文字、wbs_code は 100 文字）
MAX_ENTITY_TYPE_LENGTH: Final = 64
MAX_WBS_CODE_LENGTH: Final = 100
# 値を写さない列（OQ-08 の提案：ハッシュの値を履歴に残さない）と、その代わりに入れる印
SECRET_FIELDS_DEFAULT: Final = frozenset({"password_hash", "token_hash"})
REDACTED: Final = "***"


class AuditError(ValueError):
    """差分の入力が不正（未定義の入力は推測せずに拒む）。"""


class AuditAction(StrEnum):
    """変更履歴の操作（4.1 の ``audit_logs.action``）。"""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


@dataclass(frozen=True)
class AuditDiff:
    """変更履歴の 1 行に入れる差分（4.1 の ``audit_logs`` のうち、変更者・プロジェクト・時刻を除く列）。"""

    entity_type: str
    entity_id: int
    wbs_code: str | None
    action: AuditAction
    before: Mapping[str, Any] | None
    after: Mapping[str, Any] | None


def _json_value(value: object, field: str) -> object:
    """値を JSON に入る形へ直す。直せない型は推測せずに拒む。"""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise AuditError(f"項目 {field} の値が数ではない: {value!r}")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise AuditError(f"項目 {field} の値が数ではない: {value!r}")
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return _json_value(value.value, field)
    if isinstance(value, list | tuple):
        return [_json_value(item, field) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item, field) for key, item in value.items()}
    raise AuditError(f"項目 {field} の値は JSON に入る形へ直せない: {type(value).__name__}")


def _snapshot(values: Mapping[str, object], secret: Collection[str]) -> dict[str, Any]:
    return {
        str(field): (REDACTED if field in secret and value is not None else _json_value(value, str(field)))
        for field, value in values.items()
    }


# RULE-CHG-006 変更履歴の差分
def make_audit_diff(
    entity_type: str,
    entity_id: int,
    before: Mapping[str, object] | None,
    after: Mapping[str, object] | None,
    *,
    wbs_code: str | None = None,
    secret_fields: Collection[str] = SECRET_FIELDS_DEFAULT,
) -> AuditDiff | None:
    """変更前後の値から変更履歴の差分を作る（CHG-09「変更前後の値が残る」・NFR-05）。

    - ``before`` が ``None`` なら ``CREATE``（``after`` は全ての値）、``after`` が ``None`` なら ``DELETE``（``before`` は全ての値）
    - 両方あれば ``UPDATE``：値が変わった項目だけを、``before`` に前の値・``after`` に後の値で持つ。片方にだけある項目は、無い側を ``None`` とみなす。
      比べるのは JSON に直した後の値（``Decimal("1.0")`` と ``Decimal("1.00")`` のような表記の違いも変更として残す）。
      変わった項目が 1 つも無ければ ``None``（記録しない）
    - ``secret_fields`` の項目は値を写さず ``REDACTED`` にする（値が ``None`` のときは ``None`` のまま）。
      更新では、値が変わったときだけ前後とも ``REDACTED`` で残る
    - 対象の種類（表の名前。64 文字まで）・対象の ID（整数）・WBS ID（あれば。100 文字まで）を付ける

    両方とも ``None``、種類が空・長すぎる、ID が整数でない、値が JSON に直せないときは ``AuditError``。
    """
    if not isinstance(entity_type, str) or entity_type.strip() == "" or len(entity_type) > MAX_ENTITY_TYPE_LENGTH:
        raise AuditError(f"対象の種類は空でない {MAX_ENTITY_TYPE_LENGTH} 文字以内の文字列: {entity_type!r}")
    if isinstance(entity_id, bool) or not isinstance(entity_id, int):
        raise AuditError(f"対象の ID は整数: {entity_id!r}")
    if wbs_code is not None and (not isinstance(wbs_code, str) or wbs_code == "" or len(wbs_code) > MAX_WBS_CODE_LENGTH):
        raise AuditError(f"WBS ID は空でない {MAX_WBS_CODE_LENGTH} 文字以内の文字列か None: {wbs_code!r}")
    if before is None and after is None:
        raise AuditError("変更前と変更後の両方が無い")

    if before is None:
        assert after is not None
        return AuditDiff(entity_type, entity_id, wbs_code, AuditAction.CREATE, None,
                         MappingProxyType(_snapshot(after, secret_fields)))
    if after is None:
        return AuditDiff(entity_type, entity_id, wbs_code, AuditAction.DELETE,
                         MappingProxyType(_snapshot(before, secret_fields)), None)

    old = {str(k): _json_value(v, str(k)) for k, v in before.items()}
    new = {str(k): _json_value(v, str(k)) for k, v in after.items()}
    changed_before: dict[str, Any] = {}
    changed_after: dict[str, Any] = {}
    for field in [*old, *(k for k in new if k not in old)]:
        was, now = old.get(field), new.get(field)
        if was == now and type(was) is type(now):
            continue
        hidden = field in secret_fields
        changed_before[field] = REDACTED if hidden and was is not None else was
        changed_after[field] = REDACTED if hidden and now is not None else now
    if not changed_after:
        return None
    return AuditDiff(entity_type, entity_id, wbs_code, AuditAction.UPDATE,
                     MappingProxyType(changed_before), MappingProxyType(changed_after))
