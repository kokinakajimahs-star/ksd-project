"""死活の確認（設計ID を持たない。運用と試験のための口）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.services import get_session
from app.services.health import check_database

router = APIRouter(tags=["health"])


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict[str, str]:
    """サーバと DB への接続が生きていることを返す。"""
    check_database(session)
    return {"status": "ok"}
