"""「今」の時刻（設計書 1.3・NFR-08）。

サービス層が「今」を決めてドメイン層へ引数で渡す。API 層は ``get_now`` を ``Depends`` で受け、
試験は ``app.dependency_overrides[get_now]`` で時刻を固定する（試験を時刻に依存させない。12 章）。
"""

from __future__ import annotations

from datetime import UTC, datetime


def get_now() -> datetime:
    """UTC の現在時刻（aware）。"""
    return datetime.now(UTC)
