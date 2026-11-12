# DB を通る経路を検証対象にする

インメモリの偽物へ差し替えたテストは、**アプリが 2xx を返しながら何も永続化していなくても通る**。
`Session.close()` は未確定のトランザクションを破棄するため、`commit()` の欠落は
テストにもログにも現れない。実際にこの欠陥が、検証器が PASS を返し続ける間ずっと残った例がある。

`backend_test_failures = 0` はこの層に対して**無情報**である。
この文書は「DB を通る経路を実行していない」ことを `skipped_checks` に出すための手順を書く。

---

## 1. マーカーを登録する（`backend/pyproject.toml`）

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
markers = [
    "requires_db: 実 DB を必要とする（TEST_DATABASE_URL が無ければ skip）",
]
```

マーカーは**新しく書くテストにだけ**付ける。既存テストの実行条件は変えない。

## 2. 実 DB のフィクスチャを足す（`backend/tests/conftest.py`）

**既存の方針は変えない。** DB が無ければ従来どおり全テストが通ること。
`TEST_DATABASE_URL` が与えられたときだけ実 DB へ接続する。

```python
@pytest.fixture
def pg_session():
    """実 DB のセッション。未設定なら skip する。"""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL が無いため DB 依存の検証を実行していない")
    ...  # マイグレーションで最新へ upgrade → セッションを yield → 後始末
```

`conftest.py` 冒頭の docstring も書き換えること。

| 書かない | 書く |
|---|---|
| 「DB が無い環境でも実行できる」 | 「DB が無い環境では DB 依存の検証を skip し、**その事実を報告する**」 |

## 3. 検証器へ経路を足す（`spec-ids.toml`）

`[[checks]]` の pytest に次を足す。`scripts/verify.sh` は保護パスであり、
**編集するのは宣言側の `spec-ids.toml` だけでよい**。

```toml
requires_env = ["TEST_DATABASE_URL"]
command_without_env = ["python3", "-m", "pytest", "backend/tests", "-q", "-m", "not requires_db"]
skip_note = "DB を通る検証（requires_db）を実行していない。トランザクションの確定・制約・マイグレーション整合はこの run では見ていない"
```

- 環境変数があれば `command`（全件）を実行する
- 無ければ `command_without_env` を実行し、**`skipped_checks` を 1 増やして `skip_note` を notes に残す**

`skip_note` を空にしないこと。ここが空だと `status=PASS` が実態より広い範囲を
保証しているように読める。

## 4. ローカル DB を用意する

**クラウドへの操作は含めない。ローカルだけで完結させること。**

```yaml
# compose.yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
      POSTGRES_DB: app_test
    ports: ["5432:5432"]
```

```bash
docker compose up -d db
export TEST_DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/app_test
```

## 5. 何を `@pytest.mark.requires_db` で見るか

偽物では通ってしまうものを選ぶ。少なくとも次の 4 つ。

- 登録した資源が**別のセッションから読める**（＝確定している）
- 例外を返した要求の後に、書きかけの行が残っていない
- 監査ログが実際に残る
- マイグレーションとモデル定義の整合（`upgrade head` の後に差分が無い）

## 6. 完了の確認

```bash
# DB 無し（従来どおり全件通り、skipped_checks が 1 以上になる）
bash scripts/verify.sh all
python3 -c "import json; d=json.load(open('.kloop/verify-result.json')); \
print(d['metrics']['skipped_checks'], d['notes'])"

# DB 有り（requires_db を含めて実行され、skipped_checks はその分増えない）
export TEST_DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/app_test
bash scripts/verify.sh all
```

## 7. これでも見ていないもの

- **DB 製品固有の挙動**。SQLite で代用した場合、型・制約・分離レベルは対象外である
- `commit()` 自体が失敗した場合。FastAPI 0.106 以降は応答送出後に後処理が走るため、
  **クライアントは既に 2xx を受け取っている**。確定をエンドポイント側（UoW）へ移すかは
  設計の改訂を伴う。決めていないなら `docs/open-questions.md` に残すこと
