# 移行のスクリプト

テーブル（設計書 4.1）を足したスコープが、ここに移行を足す。

```bash
PMT_DATABASE_URL=sqlite:///$TMPDIR/x.db .venv/bin/python -m alembic -c backend/alembic.ini revision --autogenerate -m "<群>のテーブル"
PMT_DATABASE_URL=sqlite:///$TMPDIR/x.db .venv/bin/python -m alembic -c backend/alembic.ini upgrade head
```

- 自動生成の結果は必ず読み、設計書 4.1 の列・一意制約・外部キーと照らす
- 移行は 1 本の直線に保つ（`down_revision` を分岐させない）
