"""モデル層（設計書 1.3・4 章）：SQLAlchemy のモデル（群ごとに 1 ファイル）。

**ここで全てのモデルのモジュールを import する。** Alembic（``backend/alembic/env.py``）と試験は
``import app.models`` だけで ``Base.metadata`` に全てのテーブルが載ることを前提にしている。
群のモジュールを足したら、下に ``from app.models import <群> as <群>`` の形で足すこと。
"""

from app.infra.db import Base as Base
from app.models import cmn as cmn
from app.models import prg as prg
from app.models import prj as prj
from app.models import sch as sch
from app.models import wbs as wbs
