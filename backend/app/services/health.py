"""死活の確認のユースケース（設計ID を持たない）。"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def check_database(session: Session) -> None:
    """DB へ問い合わせが通ることを確かめる。通らなければ例外がそのまま上がる。"""
    session.execute(text("SELECT 1"))
