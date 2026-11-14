# ASSUMPTIONS（環境構築時に行った判断）

設計書に明記が無かったため自ら決めた事項を記録する。
設計内容の変更ではなく、実装・検証を機械化するための補完である。

## 書式

```markdown
## A-xx: <選択の要約>

- **選択:**
- **理由:**
- **影響範囲:**
- **覆す場合:** （何を変える必要があるか）
- **限界:** （この判断で担保できないこと）
```

---

## A-01: 進捗指標は「未実装 ID 数」

- **選択:** 設計書の ID のうち実装に現れていないものの数を kloop の進捗指標にする
- **理由:** 設計書の規模が 1 セッションで終わらないため反復が前提になる。
  反復を機械的に回すには進捗を人手を介さず測る必要がある。
- **影響範囲:** `scripts/check_traceability.py`、`kloop.toml` の `progress_metrics`
- **限界:** **ID の存在は実装の正しさを意味しない。** 独立レビューで補う。

## A-02: 設計ID は 4 種別（db・rule・api・ui）× 群で数える

- **選択:** 設計書 4.1・5.1・6.2・7.1 の表の 1 行を 1 件とし（187 件）、`<種別>-<群>-<3桁>` の ID の群でスコープを割る（37 スコープ）
- **理由:** `ksdx plan` は ID 文字列の群（`group_from`）でしかスコープを割れない。1 スコープ 1〜23 件にして、1 本のループで収束できる大きさにする
- **影響範囲:** `spec-ids.toml`・`trace.toml`・`graphs/implement.toml`・`kloop.toml` の `[tasks.implement-*]`
- **覆す場合:** 設計書の ID と 11 章の対応表の列、`spec-ids.toml` の `[[kinds]]` を同時に変え、`ksdx plan` で生成し直す
- **限界:** ID の存在は実装の正しさを意味しない。設計書 8 章（非機能）・9 章（権限の表の全組合せ）には ID が無く、検証器は見ていない

## A-03: 証拠の形（evidence）

- **選択:** db は `regex`（`__tablename__ = "<テーブル名>"`。ID ではなく実体）、rule は `declaration_and_test`、api は `declaration`、ui は `declaration` ＋ `route_key`
- **理由:** 既定の `id` は参照コメントだけで実装済みになる。テーブルは名前が実体そのもの、ルールは試験が無いと式の正しさを何も確かめないため
- **影響範囲:** `spec-ids.toml`
- **限界:** API の宣言は試験を求めていない（API の試験は `backend-tests` の検査で流れるが、ID ごとの試験の有無は数えない）

## A-04: 実装の順（群の順）

- **選択:** `graphs/group-needs.toml` で テーブル → ルール → API → 画面 と、群をまたぐ依存（外部キー・計算の部品・認証）を宣言した。構成づくり（scaffold）はグラフの外で先に `kloop run scaffold` で回す
- **理由:** `ksdx plan graph` は `[[extra_tasks]]` をノードにしない（グラフの中で scaffold を先に置けない）。待つスコープが収束しないと下流は SKIPPED になるので、辺は必要なものだけにした
- **影響範囲:** `graphs/implement.toml`（生成物）
- **覆す場合:** `graphs/group-needs.toml` を直し、`ksdx plan graph … --group-needs graphs/group-needs.toml --out graphs/implement.toml --force` で生成し直す
- **限界:** 書くノードは 1 本ずつ走る（`[graph.worktrees]` を使っていない。作業ツリーを分けると `.venv`・`node_modules` が無く検査が落ちるため）

## A-05: グラフの実装ループはコミットしない

- **選択:** 実装グラフ（`graphs/implement.toml`）はコミットせず、最後の `review` の `findings.md` を読んでから作業者が `dev` へコミットする
- **理由:** `ksdx plan graph` の生成物の形（コミットの前の承認ゲートを置かない。CR-I-034）。`main` への反映は関門とエスカレーションで行う
- **限界:** 途中のスコープの成果は run が終わるまで未コミットで作業ツリーに積まれる

## A-06: 実機のループはサンドボックスの外で起動する

- **選択:** `[state] graph = "ksdls-loop"`（ksd-ls のグラフ層へ記録）。実機の `kloop run`・`kloop graph` は利用者の端末（サンドボックスの外）で起動する
- **理由:** Claude Code のサンドボックスの中からは、グラフ DB の接続先と Claude の資格が読めない（2026-11-10 実測：グラフの doctor が TargetError、`kloop doctor` が「認証・課金先が見つからない」）
- **起動の口:** Claude Code の入力欄で利用者が打つ `!` の行（シェルモード）は、前景のセッションではサンドボックスの外で動く
  （公式文書 code.claude.com/docs/en/sandboxing。v2.1.260 以降で、`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` が無いとき。2026-11-10 の版は 2.1.322）。
  `! cd ~/ksd-project && nohup kloop graph graphs/implement.toml > .kloop/kick.log 2>&1 &` で、会話を離れずに起動できる。
  **2026-11-11 から、資源の上限の中で起動する A-24 の口を使う。**
  エージェントの Bash（サンドボックスの中）では起動しない（2026-11-10 実測：入れ子の `claude -p` が「Not logged in」）
- **限界:** サンドボックスの中で確かめたのは dry-run・mock の配線と検証器だけである


## A-07: 構成づくり（scaffold）で決めたこと

- **選択:**
  - サーバの依存は 2026-11-10 時点の版で固定した（fastapi 0.162.2・SQLAlchemy 2.0.57・alembic 1.20.1・pydantic 2.14.3・pytest 8.4.2 ほか。`backend/requirements.lock`）。設計書 1.1 の下限を満たす
  - PostgreSQL（本番）のドライバは psycopg 3（`psycopg[binary]` 3.3.8）。`PMT_DATABASE_URL` は `postgresql://…` と書けば `postgresql+psycopg://…` として扱う（`app.infra.db.normalize_database_url`）
  - 画面の依存は設計書 1.1 の主版（React 18・React Router 6・Vite 5・Vitest 2・TypeScript 5）の中の最新で固定した（react-router-dom 6.30.6・vite 5.4.21・vitest 2.1.9。`frontend/package.json` に `^` を付けない。`package-lock.json` をコミットする）。主版の中では直らない勧告は CR-001 に起票した
  - フレームワークの `HTTPException` の文言は状態コードで決まる日本語にし、`detail` は `details` に入れる。文言を指定するエラーは全て `AppError` で投げる。想定外の例外も 500 `INTERNAL_ERROR` の形で返す（中身は応答に出さない）
  - SQLite の移行は外部キーの強制を外して流し、終わったら `PRAGMA foreign_key_check` で違反が無いことを確かめる（表の作り直しの DROP が子の行を連鎖で消すため。`create_migration_engine`）
  - `app.main` の `app` は最初に参照されたときに作る（import しただけで環境変数を読まない。設計書 2.2 の `uvicorn app.main:app` はそのまま使える）
  - API 層はセッションの依存を `app.services` から取る（1.3：API はサービスとスキーマにだけ依存する）
  - `build` は `tsc --noEmit -p tsconfig.json && vite build` にした（`prompts/scaffold.md` の `tsc -b` ではない。tsconfig を 1 本にして `noEmit` で型だけ見るため）
  - 業務上のエラーは `backend/app/errors.py` の `AppError`（状態コード・コード・文言・details）。サービス層と API 層の両方が使うので、どちらの層にも置かない
  - 入力の誤り（FastAPI の検証エラー）は 400 `VALIDATION_ERROR` に直す（設計書 6.1。FastAPI の既定の 422 は業務ルールの違反に空けておく）
  - 設定は `pydantic-settings` で環境変数 `PMT_*` だけから読み、`.env` は読まない。最初の管理者のログイン名・パスワードは既定値を持たない
  - 死活の口 `GET /api/v1/health`（設計ID を持たない。DB へ `SELECT 1` を流す）
- **理由:** 設計書 2.1・2.2・2.4・6.1・8 章。後続のスコープが同じ形で足せるようにする
- **限界:** starlette 1.10 が「httpx を TestClient に使うのは非推奨（httpx2 へ）」と警告を出す。今は動く。上げるときは `backend/requirements.lock` を作り直して試験を流す

## A-08: 共通（CMN）のテーブル（db:CMN）で決めたこと

- **選択:**
  - `role_permissions.project_id`・`share_links.project_id` は**外部キーの制約をまだ張らない**（整数・NOT NULL・索引だけ）。
    `projects`（DB-PRJ-001）は db:PRJ のスコープが作り、`graphs/group-needs.toml` では db:PRJ が db:CMN を待つ（`projects.owner_user_id` → `users`）。
    db:CMN の時点で `ForeignKey("projects.id")` を書くと `create_all` が参照先の無い表で落ちる。
    **db:PRJ のスコープは、`projects` を作る移行で両方の列に `projects.id` への外部キーを張る**（SQLite は `batch_alter_table` で表を作り直す）。
    張り忘れると `backend/tests/models/test_models_cmn.py::test_project_id_columns_and_reference_to_projects` が落ちる
  - 時刻の列は `app.models.types.UTCDateTime`（`DateTime(timezone=True)` の上の型）。UTC へ直して保存し、UTC の aware な値で返す。
    時差の無い（naive な）値は拒む（fail fast）。作成の時刻の既定値は `utc_now`（モデルの既定値だけ。ドメイン層は使わない）。
    移行のスクリプトは app のコードを import せず `sa.DateTime(timezone=True)` で書く
  - 文字列の長さ：`login_id` 100・`display_name` 200・`email` 254・`password_hash` 255・`organization` 200・`role` 32・`permission` 64・`scope` 16・`token_hash` 64（SHA-256 の 16 進）
  - 必須でない列：`users.email`・`users.organization`、`revoked_at`（失効まで空）。`share_links.expires_at` は必須（要求 RPT-03「有効期限付き」）、`auth_sessions.expires_at` も必須（6.1 の 12 時間）
  - `share_links.token_hash`・`auth_sessions.token_hash` に一意制約を付けた（4.1 に「一意」の記載は無いが、トークンからの引き当てに索引が要り、256 ビットの乱数のハッシュは重ならない）
  - 値の一覧の検査（CHECK）は 4.1 の列に値の一覧が書かれているものだけに付けた（`share_links.scope` の GANTT/REPORT）。
    `role_permissions.role`・`permission` のコードの検査は RULE-AUTH-001 と API-CMN-007 が行う（9.1・9.2 のコードの一覧はドメインが持つ）
  - 真偽の既定値は DB の既定値（`server_default`）にも置く。移行では `sa.false()`・`sa.true()` で書く（PostgreSQL の boolean に `DEFAULT 0` は書けない）
  - 移行の結果とモデルの差（autogenerate の差分）が無いことを `test_migration_creates_cmn_tables_matching_models` が確かめる（全てのテーブルが対象。後のスコープも移行とモデルを揃える）
- **理由:** 設計書 2.4（時刻は UTC）・4.1・4.2（外部キー・トークンのハッシュ）。群の実装の順（A-04）と外部キーの向きが逆になる箇所の扱い
- **影響範囲:** `backend/app/models/cmn.py`・`backend/app/models/types.py`・`backend/alembic/versions/20261110_0001_cmn_tables.py`・`backend/tests/models/test_models_cmn.py`
- **覆す場合:** 外部キーを先に張るなら、`projects` を db:CMN より前に作る順へ `graphs/group-needs.toml` を変える（保護パス）
- **限界:** db:PRJ が外部キーを張るまでは、存在しない `project_id` の行を DB が拒まない。移行は SQLite でだけ流している（PostgreSQL は `--sql` の出力を目で見ただけ）

## A-09: 権限の判定・共有リンクの検証（rule:AUTH）で決めたこと

- **選択:**
  - 「プロジェクトの上書き」は、プロジェクトの `role_permissions` の表（作成時に 9.2 を写し、PM が permission.manage で変えたもの）を**そのまま正**とする。
    表に行の無いロールは何も持たない（9.2 の既定へ戻らない）。既定の表は `DEFAULT_ROLE_PERMISSIONS`・`default_role_permission_rows()` が持ち、プロジェクトの作成（API-PRJ-001）が写す
  - 判定の順：無効な利用者（`is_active` が偽）は全て拒む（システム管理者でも）→ システム管理者は全て許す → システムの権限（project.create・user.admin）は個別の許可だけ → プロジェクトの権限はメンバーのロールの**和**（1 人が複数のロールを持てる。要求仕様書 3 章）
  - progress.enter_own・effort.enter_own は対象の活動（`activity_id`）を必須にし（渡さなければ `ValueError`）、自分の割当の活動だけを許す。
    progress.enter_own を問われたとき、progress.enter_all を持てば割当に関わらず許す。effort には全件の権限が無いので割当だけ
  - 委託先の閲覧の絞り込み（9.2 の注記）は、**project.view を持つロールが CONTRACTOR だけ**のときに効かせる。VIEWER など他のロールでも project.view を持てば絞らない。
    見せる範囲は割当の活動と、その活動の WP から根までの WBS 要素（`own_assignment_scope`）。兄弟の要素は見せない
  - 共有リンク：照合は SHA-256 の 16 進の値の定数時間の比較。`revoked_at` が入っていれば（時刻に関わらず）REVOKED、`now >= expires_at` で EXPIRED（期限の時刻ちょうどで見られなくなる）。
    失効と期限切れが重なれば REVOKED。リンクが無い・ハッシュが合わない・トークンが空は区別せず NOT_FOUND
  - 見せる範囲：GANTT のリンクはガントだけ、REPORT のリンクはレポートだけを見せる（一致するものだけ。REPORT がガントの画面を含むとは扱わない）。会議レポートの中のガントの欄（7.4）はレポートの一部として見せる
  - トークンのハッシュ（`hash_token`）はドメインに置いた（標準ライブラリだけで純粋）。トークンの生成（乱数）は infra（`app/infra/security.py`）が行い、ハッシュはこの関数を使う
  - 自分の申請・提出したものの判断の禁止（9.2 の注記・5.10）は RULE-WBS-006・CHG-003（`workflow.py`）の担当とし、ここでは扱わない
- **理由:** 設計書 5.1 の RULE-AUTH-001・002、9 章、4.2、要求仕様書 3 章・RPT-03・CMN-01。最小の権限に倒す
- **影響範囲:** `backend/app/domain/authz.py`・`backend/tests/domain/test_authz.py`。サービス層の全ての権限の確認（RULE-AUTH-001 を呼ぶ）と API-RPT-004
- **覆す場合:** プロジェクトの表を「既定＋差分」にするなら `role_permission_table` と `authorize` の表の扱いを変える。REPORT のリンクでガントも見せるなら `ShareLinkVerdict.can_view` を変える
- **限界:** 委託先の絞り込みは project.view にだけ効く。PM が委託先へ他の読む権限（data.export・effort.view など）を足すと、その操作は割当で絞られない。
  project.create の個別の許可の保存先が無い（OQ-09）。サービス層・API からの呼び出し（403 になるか）はまだ無く、試験していない

## A-10: 共通（CMN）の API（api:CMN）で決めたこと

- **選択:**
  - ルータは設計書 2.1 の群のファイルに分けた：`backend/app/api/auth.py`（API-CMN-001・002・006・007・008）と `backend/app/api/users.py`（API-CMN-003〜005）。
    認証の依存（Bearer の取り出し・利用者の引き当て・「今」・プロジェクトを読む口）は `backend/app/api/deps.py`
  - **プロジェクトの有無とメンバーの立場は `app.services.project_access.ProjectAccess` から読む。** `projects`（DB-PRJ-001）・`project_members`（DB-PRJ-005）・
    `assignments`（DB-EFF-001）は別の群のスコープが作り、`graphs/group-needs.toml` では api:CMN はそれらを待たない。
    表がまだ無い間は `ProjectTablesAbsent`（プロジェクトは 1 つも無い。どれを問われても 404・メンバーでない）を `app.state.project_access` に載せる。
    **db:PRJ・api:PRJ のスコープは `project_members` を読む実装を書き、`app.main.create_app` で差し替える。**
    差し替え忘れは `backend/tests/api/test_cmn.py::test_project_access_is_wired_once_project_tables_exist` が落とす。
    試験はメンバーを `FakeProjectAccess` で与える
  - 「今」は `app.services.clock.get_now`（FastAPI の依存）。試験は `app.dependency_overrides[get_now]` で固定する
  - ログイン：ログイン名が無い・パスワードが違う・無効な利用者は、どれも同じ 401 `INVALID_CREDENTIALS`（どれに当たったかを漏らさない。
    無いログイン名でも scrypt を 1 回まわして応答時間を揃える）。トークンは `secrets.token_urlsafe(32)`、DB には SHA-256 だけ。
    有効期間は `PMT_TOKEN_TTL_HOURS`（既定 12）。期限の時刻ちょうどで使えなくなる（`now >= expires_at`。RULE-AUTH-002 の共有リンクと同じ境界）
  - 認証が通らない（トークンが無い・知らない・失効・期限切れ・利用者が無効）ときは 401 と `WWW-Authenticate: Bearer`。
    ログアウト（API-CMN-002）は送ったトークンだけを失効させ、204 を返す
  - パスワードの形式：`scrypt$<n>$<r>$<p>$<塩の base64>$<鍵の base64>`（鍵 64 バイト）。形式の壊れたハッシュは「合わない」とする
  - ユーザーの一覧（API-CMN-003）の「member.manage」は、**メンバーであるどれかのプロジェクト**の表で member.manage を持つこと（一覧は project_id を取らない）。
    返す項目は user.admin と同じ（password_hash は誰にも返さない）。一覧の形は `{"items", "total", "limit", "offset"}`（後の群の一覧も同じ形にそろえるとよい）
  - ユーザーを作る（API-CMN-004）：ログイン名の重複は 409 `DUPLICATE_LOGIN_ID`。`is_active` は作るときは常に真。
    パスワードは空でなく 1024 文字以下だけを確かめる（長さ・文字種の方針は OQ-10）
  - ユーザーを変える（API-CMN-005）：載せた項目だけを変える（`exclude_unset`）。ログイン名は変えない（載せると 400）。
    display_name・password・is_system_admin・is_active の null は 400。
    **有効なシステム管理者が 1 人も残らなくなる変更（最後の 1 人を無効にする・管理者から外す）は 422 `LAST_SYSTEM_ADMIN`**（設計書に記載が無い。user.admin を持つ人がいなくなると、誰もユーザーを管理できなくなるため）。
    パスワードを変えた・無効にしたら、その利用者の認証トークンを全て失効させる
  - ロール×権限の表（API-CMN-006・007）：出力は 3 章のロールの順に**全てのロール**を載せ、権限は 9.1 の順に並べる。
    置き換えは表を丸ごと置き換える（載せなかったロールは何も持たない。A-09）。3 章に無いロール・プロジェクトの表に載らない権限（未知のコード・
    project.create・user.admin）・同じロールの重複は 400 `VALIDATION_ERROR`（スキーマ層はドメインのコードの一覧に依存できないので、サービス層で確かめる）。
    プロジェクトが無ければ 404 を 403 より先に返す
  - 自分の情報（API-CMN-008）：`system_permissions`（システム管理者なら project.create・user.admin）と、メンバーであるプロジェクトごとの
    `roles`・`permissions`（RULE-AUTH-001 の `effective_permissions`。システムの権限を除く）・`view_level`（ALL・OWN_ASSIGNMENTS）。
    システム管理者もメンバーでないプロジェクトは載せない（画面は `is_system_admin` で全てを許す）
  - 最初のシステム管理者：起動時（lifespan）に `PMT_INITIAL_ADMIN_LOGIN`・`PMT_INITIAL_ADMIN_PASSWORD` の両方があれば作る。
    そのログイン名が既にあれば何もしない（パスワード・権限を上書きしない）。片方だけなら起動を止める（fail fast）。両方とも無ければ何もしない
- **理由:** 設計書 6.1・6.2・8 章（NFR-04・10）・9 章、要求仕様書 CMN-01・NFR-04。群の実装の順（A-04）でプロジェクトの表がまだ無いこと
- **影響範囲:** `backend/app/api/{auth,users,deps}.py`・`backend/app/services/{auth,users,clock,project_access}.py`・`backend/app/schemas/{auth,users}.py`・
  `backend/app/infra/security.py`・`backend/app/main.py`・`backend/tests/api/test_cmn.py`・`backend/tests/infra/test_security.py`
- **覆す場合:** 一覧を「そのプロジェクトのメンバーを選ぶため」に絞るなら API-CMN-003 に `project_id` を足す（設計書 6.2 の変更）。
  最後の管理者の保護をやめるなら `users_service.update_user` の 422 を外す
- **限界:** 変更履歴（6.1「書き込みは全て変更履歴に残る」）は infra の `audit_listener` がまだ空なので、この群の書き込みも記録していない（CHG のスコープ。OQ-08）。
  ログインの試行回数の制限・アカウントのロックは設計書に無く、実装していない。PostgreSQL では流していない。
  `ProjectTablesAbsent` の間、API-CMN-006・007 は本番では常に 404 になる（プロジェクトが作れないため）

## A-11: プロジェクト基盤（PRJ）のテーブル（db:PRJ）で決めたこと

- **選択:**
  - `role_permissions.project_id`・`share_links.project_id` に `projects.id` への外部キーを張った（A-08 の申し送り。移行 `0002_prj`。SQLite は表の作り直し）。
    0001 の後に参照先の無い行があれば、移行は外部キーの違反で止まる（fail fast。`test_migration_stops_on_cmn_rows_without_project`）。
    既存の試験の準備（`backend/tests/models/test_models_cmn.py`・`backend/tests/api/test_cmn.py`）は `projects` の行を先に作る形に直した（確かめる値は変えていない）
  - 必須の列：`projects` の `code`・`name`・`start_date`・`owner_user_id`（PRJ-01 の必須項目は名称・開始日・責任者。`code` は 4.1 で一意の業務キーなので必須にした。OQ-11）・
    `wbs_status`・`settings`。`scope_items` の `kind`・`title`・`sort_order`。`calendar_holidays.holiday_date`。`project_members.roles`。
    任意：`purpose`・`requirements`・`key_deliverables`・`target_finish_date`・`scope_items.description`・`calendar_holidays.name`・`project_members.organization`・`team`
  - 文字列の長さ：`code` 50・`name`／`title`／休日の `name`／`organization`／`team` 200・`wbs_status` 32・`kind` 8・`working_weekdays` 7。目的・要求事項・主要成果物・説明は `Text`
  - 値の検査（CHECK）：`projects.wbs_status` は 3 章・5.10 の 4 状態、`scope_items.kind` は IN/OUT、`calendars.working_weekdays` は 0 と 1 の 7 文字、
    `calendars.hours_per_day` は 0 より大きく 24 以下（1 日の時間の物理的な上限。本番値ではない）。
    **稼働日が 1 つも無い `0000000` は DB では拒まない**（形は正しい）。拒むのは API-PRJ-008 と RULE-CAL-001 の担当
  - 既定値：`wbs_status`＝DRAFT・`working_weekdays`＝1111100・`hours_per_day`＝8 はモデルと DB の両方。`settings` は 4.2 の値をモデルの既定値（`default_project_settings()`。行ごとに新しい辞書）だけに置き、
    DB の既定値は置かない（移行に JSON を二重に書いてずれるのを避ける。ORM を通らない書き込みは NOT NULL で止まる）
  - `hours_per_day` は 2.4 の工数と同じ `Numeric(10,2)`
  - JSON の列（`settings`・`roles`）は変更の追跡（`MutableDict` 等）を付けていない。**中身をその場で書き換えても保存されない**ので、サービス層は新しい値を代入する
  - 外部キーに `ON DELETE` を付けていない（6.2 にプロジェクトの削除は無い）。カレンダーの休日の置き換え（API-PRJ-008）などはサービス層が子の行を明示的に消す
  - 一意制約は 4.1 に書かれたものだけ（`projects.code`・`calendars.project_id`・`calendar_holidays`（calendar_id, holiday_date）・`project_members`（project_id, user_id））。`scope_items` の並び順は一意にしていない
  - **プロジェクトを読む口を `ProjectMembersTable`（`app/services/project_access.py`）に差し替えた**（A-10 の申し送り）。`projects`・`project_members` から `Membership` を作る。
    `roles` が文字列の配列でない・3 章に無いロールのコードが入っていれば `ValueError` で止める（権限を推し量らない）。
    割当の活動は `AssignedActivities` から読み、`assignments`（DB-EFF-001）がまだ無いので既定は `AssignmentsTableAbsent`（割当は 1 つも無い）。
    **db:EFF のスコープは `assignments` を読む実装を書き、`app.main.create_app` の `ProjectMembersTable(...)` に渡す。**
    差し替え忘れは `backend/tests/services/test_project_access.py::test_assignments_reader_is_wired_once_assignments_table_exists` が落とす
- **理由:** 設計書 2.4・3 章・4.1・4.2・5.10、要求仕様書 PRJ-01・02・04・06。A-08・A-10 の申し送り
- **影響範囲:** `backend/app/models/prj.py`・`backend/app/models/cmn.py`・`backend/app/models/__init__.py`・`backend/alembic/versions/20261110_0002_prj_tables.py`・
  `backend/app/services/project_access.py`・`backend/app/main.py`・`backend/tests/models/test_models_prj.py`・`backend/tests/models/test_models_cmn.py`・
  `backend/tests/api/test_cmn.py`・`backend/tests/services/test_project_access.py`
- **覆す場合:** `code` を任意（または自動採番）にするなら列を NULL 可にして移行を足す。稼働日なしを DB でも拒むなら `ck_calendars_working_weekdays` に `<> '0000000'` を足す
- **限界:** 移行は SQLite でだけ流した（PostgreSQL は `--sql` の出力を目で見ただけ）。`hours_per_day` の SQLite での小数の往復は 2 桁の値でだけ確かめた。
  委託先（CONTRACTOR）は db:EFF まで割当が無いので、`progress.enter_own`・`effort.enter_own` は通らず、閲覧の絞り込み（A-09）で何も見えない。
  プロジェクトを作る操作（API-PRJ-001。カレンダー・採番規則・既定の権限も作る）は api:PRJ の担当で、まだ無い

## A-12: 稼働日の判定・日付の数え方・稼働日の軸（rule:CAL）で決めたこと

- **選択:**
  - 置き場は `backend/app/domain/calendar.py`（5.1 の実装先）。RULE-CAL-001 は `WorkCalendar`（稼働日の判定・寄せ・数え方・`resolve_dates`）、
    RULE-CAL-002 は `WorkdayAxis`（`index`・`workday_at`・`report_line`・`display_date`）。軸はカレンダーの数え方の上に作る
  - `working_weekdays` は 4.1 の形（月〜日の 7 文字、`1` が稼働）。**稼働の曜日が 1 つも無い `0000000` は `CalendarError` で拒む**（稼働日が存在せず、寄せ・数えが終わらない。A-11 で「拒むのは RULE-CAL-001 の担当」とした分）
  - 非稼働の曜日に重なる休日は数えに影響しない（二重に引かない）
  - `resolve_dates` は開始日・期間・終了日の**ちょうど 2 つ**を受ける。0・1・3 つ、負の期間、寄せた後に終了日が開始日より前になる入力（例：土〜日）は `CalendarError`（OQ-12）
  - 返す日付は寄せた後の値。開始日＋終了日から求める期間は両端を含む稼働日の数（≧1）。期間 0（マイルストーン）は開始日＝終了日で、
    開始日で与えれば次の稼働日へ、終了日で与えれば前の稼働日へ寄せる（BR-04 の寄せ方をそのまま当てた）
  - 位置は `int`・`float`・`Decimal`・`Fraction` を受ける（5.6 の P は小数）。表示の日付は 5.4 の式 `max(ceil(p) − 1, 0)` をそのまま使い、
    原点より前の位置は添字 0 の稼働日になる。NaN・無限大は `CalendarError`
  - 数え方は 7 日の周期と休日の二分探索で求め、日数に比例する繰り返しをしない（5.5 の NFR-01 で日程計算が何度も呼ぶため）。
    n 稼働日後・前は「稼働日が n 件になる境目」の二分探索で求め、探索の幅は曜日の数と休日の数からの上界にした
  - `hours_per_day` はこのルールに要らないので `WorkCalendar` に持たせていない（人日→人時の換算は RULE-EST-002 が引数で受ける）
- **理由:** 設計書 4.1・5.1・5.4・5.5・5.6、要求仕様書 7.4 BR-04・7.7 BR-07、PRJ-04・SCH-03・PRG-08・PRG-10。A-11 の申し送り
- **影響範囲:** `backend/app/domain/calendar.py`・`backend/tests/domain/test_calendar.py`
- **覆す場合:** 3 つの入力を受けて整合を確かめる形にするなら `resolve_dates` の入口の検査を変え、試験 `test_resolve_rejects_undefined_input` の 3 つの例を直す
- **限界:** 暦日上の座標（7.3 の「日付＋その日の中の割合」）はこのルールに入れていない（ガント・イナズマ線の組み立て RULE-SCH-005・RULE-PRG-005 の担当）。
  カレンダーの行（DB）からデータクラスへ直す口はまだ無い（API-PRJ-007・008、日程計算のサービスの担当）。性能は試験で測っていない

## A-13: 見積のボトムアップ集計・単位換算と記録（rule:EST）で決めたこと

- **選択:**
  - 置き場は `backend/app/domain/estimate.py`（5.1 の実装先）。RULE-EST-001 は `rollup_estimates`（入力は `WbsNode`・`ActivityEstimate`、結果は `EstimateRollup`）、
    RULE-EST-002 は `record_estimate`（換算は `to_hours`、結果は 4.1 の `estimate_records` の 1 行に当たる `EstimateRecord`。`id` は DB が付ける）
  - 集計：活動を持つ WP は活動の見積の合計（自分の見積は使わない）。**活動を 1 つも持たない WP** だけが自分の見積を使う（5.8）。
    活動を持つが見積が全て NULL の WP は 0 で、自分の見積へ戻らない。WP 以外の要素は子の合計で、子が無ければ 0（OQ-13）。プロジェクトの値は最上位の要素の合計
  - NULL の見積（`own_estimate_hours`・`activities.estimate_hours`）は 0 として足す。要素ごとに下位の合計（`lower_hours`。下位が無ければ `None`）を別に返し、
    100% ルール（RULE-WBS-004・5.3 の「子の集計」「子を持つ要素」）が使えるようにした
  - 木になっていない入力（ID の重複・存在しない親・循環）、子を持つ WP（WBS-02）、WP 以外・存在しない要素に置かれた活動（WBS-08）、負・非数の見積は `EstimateError` で拒む（推測して足さない）
  - 工数は `Decimal`（または `int`）だけを受け、`float`・`bool` は拒む（2.4 の `Numeric(10,2)`。2 進の誤差を持ち込まない）。集計は丸めない
  - 換算：人日×`hours_per_day` を小数 2 桁へ四捨五入（ROUND_HALF_UP）。入力の値は 0 以上・小数 2 桁まで（OQ-14）。`hours_per_day` は 0 より大きく 24 以下（4.1 の DB の検査制約と同じ）を、
    人時の入力でも確かめる。入力の値・換算した人時が `Numeric(10,2)` の上限 99999999.99 を超えれば拒む
  - 記録：手法（EST-08 の 5 つ）・前提・参照資料は省ける（`None`）。前提・参照資料の文字列は書き換えない。`entered_at` は時差つきの時刻を引数で受ける（ドメインは「今」を求めない）。
    対象・単位・手法は列挙のほか DB の値（文字列）でも受ける
- **理由:** 設計書 2.4・4.1・5.1・5.3・5.8、要求仕様書 EST-01・EST-02・EST-08・WBS-02・WBS-08
- **影響範囲:** `backend/app/domain/estimate.py`・`backend/tests/domain/test_estimate.py`
- **覆す場合:** 葉の上位の要素の自分の見積を集計に入れるなら `rollup_estimates` の WP 以外の枝を変え、試験 `test_rollup_sums_activity_to_wp_to_upper_to_project` の 2.2 の期待値を直す。
  丸め方を変えるなら `to_hours` の `rounding` と、試験の 0.005・2.5575 の例を直す。手法を必須にするなら `record_estimate` の `method` の既定を外す
- **限界:** 見積の履歴（API-EST-002）・対象の存在の確かめ・`wbs_elements.own_estimate_hours`／`activities.estimate_hours` への反映はサービス層（api:EST）の担当で、まだ無い。
  `estimate_records` のテーブル（DB-EST-001）は db:EST の担当で、まだ無い。前提・参照資料の長さの上限は 4.1 に無いので確かめていない

## A-14: プロジェクト基盤（PRJ）の API（api:PRJ）で決めたこと

- **選択:**
  - ルータは `backend/app/api/projects.py`（API-PRJ-001〜011）、ユースケースは `backend/app/services/projects.py`、入出力は `backend/app/schemas/projects.py`。
    権限は全て `app.services.auth.authorize_project`（RULE-AUTH-001）を通す。プロジェクトが無ければ 404 を 403 より先に返す（api:CMN と同じ）
  - **別の群の表に書く 2 つの口を `app.state` に載せた**（`graphs/group-needs.toml` では api:PRJ は db:WBS・db:SCH を待たない。A-10・A-11 の `ProjectAccess` と同じ形）：
    - `NumberingRulesStore`（API-PRJ-001 の「採番規則も作る」）。採番規則の表 `wbs_numbering_rules`（DB-WBS-002）がまだ無いので既定は `NumberingRulesTableAbsent`（書く先が無い）。
      **db:WBS のスコープは表に既定の行を書く実装を書き、`app.main.create_app` の `app.state.numbering_rules` を差し替える**
    - `ScheduleRecalculator`（API-PRJ-008 の「日程を再計算する」と、開始日を変えた API-PRJ-004。5.5 の下限が `project.start_date` を使うため）。
      活動の表 `activities`（DB-SCH-001）がまだ無いので既定は `ActivitiesTableAbsent`（再計算する活動が無い）。**db:SCH・api:SCH のスコープは差し替える**
    - どちらもコミットの前に同じトランザクションの中で呼ぶ（口が失敗すれば全て巻き戻る。試験で確かめた）。差し替え忘れは
      `backend/tests/api/test_prj.py::test_numbering_rules_store_is_wired_once_numbering_table_exists`・`::test_schedule_recalculator_is_wired_once_activities_table_exists` が落とす
  - プロジェクトを作る（API-PRJ-001）：`code`・名称・開始日・責任者が必須（OQ-11：`code` は利用者が入れる 1〜50 文字。形式は決めない）。
    カレンダーは 4.1 の既定（`1111100`・8 時間・休日なし）、設定は 4.2 の既定、ロール×権限の表は 9.2 の既定（`default_role_permission_rows()`）を写す。
    **メンバーは作らない**（設計書の「カレンダー・採番規則・既定の権限も作る」に無い。責任者・作成者を PM にするかは OQ-15）。
    `code` の重複は 409 `DUPLICATE_PROJECT_CODE`。終了予定日が開始日より前（同じ日は可）・責任者が無い／無効は 400。作るときに `settings`・`wbs_status` は受け付けない（400）
  - 一覧（API-PRJ-002）：システム管理者は全て、ほかはメンバーであり**そのプロジェクトの表で** project.view を持つもの（委託先も載る）。ID の順。形は `{"items", "total", "limit", "offset"}`（A-10）
  - 更新（API-PRJ-004）：載せた項目だけ（`exclude_unset`）。`code`・名称・開始日・責任者・`settings`（と各項目）の null は 400。`settings` は載せた項目だけを今の値へ重ねる。
    `settings` の値は 0 以上の有限の数・進捗率の方式は BR-06 の 7 つのコード（上限は決めない）。`wbs_status` は書けない（5.10 の遷移の担当。400）。`code` は変えられる（重複は 409）
  - スコープ項目の置き換え（API-PRJ-006）：`id` を書いた項目は ID を保って変え、書かない項目は足し、載せなかった項目は消す（3 章：他の表は内部キーで指すため。
    丸ごと消して作り直すと `wbs_scope_links` の紐付けが切れる）。`sort_order` は配列の順に 1 から。他のプロジェクトの・無い・重複した `id` は 400。
    応答は置き換えた後の全ての項目（`total`＝`limit`＝件数・`offset`＝0）
  - スコープ項目・メンバーの一覧も 6.1 の `limit`（既定 100・上限 1000）・`offset` を受ける
  - カレンダー（API-PRJ-007・008）：PUT は 3 項目とも必須で休日は丸ごと置き換える（休日を書き忘れて消すのを避けるため、`holidays` の省略は 400）。
    `working_weekdays` の形（0/1 の 7 文字）・`hours_per_day`（0 より大きく 24 以下・小数 2 桁まで）・同じ日付の重複は 400。
    **稼働の曜日が無いなど RULE-CAL-001（`WorkCalendar`）が数えられないカレンダーは 422 `INVALID_CALENDAR`**（A-11・A-12 の申し送り）。
    カレンダーの行が無いプロジェクト（API-PRJ-001 を通らずに作った行）は 4.1・PRJ-04 の既定として読み、PUT で行を作る。`hours_per_day` は数（float）で返す
  - メンバー（API-PRJ-009〜011）：PUT はロール・所属・チームを載せた値で置き換える（足す・変えるの両方。応答は 200）。ロールは 3 章のコードを 1 つ以上・重複なし（400）で、3 章の順に保存する。
    利用者が無ければ 404、**無効な利用者を新しく足すのは 422 `USER_INACTIVE`**（既にメンバーなら変える・外すはできる）。メンバーでない利用者を外すのは 404。
    最後の PM を外す・PM から外すのは止めない（システム管理者が member.manage を持つので戻せる。OQ-15）
- **理由:** 設計書 1.3・3 章・4.1・4.2・5.4・5.5・5.10・6.1・6.2・9 章、要求仕様書 PRJ-01・02・04・06・BR-03・06・09・11。A-10・A-11・A-12 の申し送り
- **影響範囲:** `backend/app/api/projects.py`・`backend/app/api/deps.py`・`backend/app/services/projects.py`・`backend/app/schemas/projects.py`・`backend/app/main.py`・`backend/tests/api/test_prj.py`
- **覆す場合:** 作成時に責任者（または作成者）を PM のメンバーにするなら `projects_service.create_project` に `ProjectMember` の追加を足し、
  試験 `test_admin_creates_project_with_calendar_permissions_and_defaults` の「メンバーがいない」を直す。スコープ項目を丸ごと作り直す形にするなら `replace_scope_items` と
  `test_pm_replaces_scope_items_keeping_ids` を直す
- **限界:** 変更履歴（6.1「書き込みは全て変更履歴に残る」）は infra の `audit_listener` がまだ空なので、この群の書き込みも記録していない（CHG のスコープ。OQ-08）。
  スコープ項目を消すとき、WBS 要素との紐付け（`wbs_scope_links`。DB-WBS-005）がまだ無いので紐付いた項目の扱い（拒む・紐付けも消す）は決めていない（db:WBS・api:WBS の担当）。
  基準化（BASELINED）の後にスコープ・カレンダーを変えたときの WBS の状態・日程基準への影響は扱っていない（5.10 は WBS の編集だけを定める）。PostgreSQL では流していない

## A-15: WBS のテーブル（db:WBS）で決めたこと

- **選択:**
  - 置き場は `backend/app/models/wbs.py`（DB-WBS-001〜010）、移行は `0003_wbs`。値の一覧の検査の式は `app.models.types.in_list` に移した（`prj.py` も使う。式は同じ）
  - 外部キー：4.1 の `*_id`・`requested_by`・`renumbered_by`・`author_id`・`decided_by`・`created_by`・`parent_element_id` に張った。`ON DELETE` は付けない（A-11 と同じ。配下ごとの削除はサービス層が子の行から扱う）。
    **`approval_requests.target_id` は外部キーを持たない**（対象の種類で指す表が変わる。4.2 の `<単数形>_id` に当たらない）。
    **`scope_baselines.change_request_id` は外部キーをまだ張らない**（整数・空を許す・索引だけ）。`change_requests`（DB-CHG-001）は db:CHG が作る（`graphs/group-needs.toml` では db:CHG が db:WBS を待つ）。
    **db:CHG のスコープは、`change_requests` を作る移行でこの列に `change_requests.id` への外部キーを張る**（SQLite は `batch_alter_table`）。
    張り忘れると `backend/tests/models/test_models_wbs.py::test_scope_baselines_change_request_id_references_change_requests_once_it_exists` が落ちる
  - **版の写し（`scope_baseline_elements`）の `element_id`・`parent_element_id` にも外部キーを張った**（4.2 どおり）。基準に入った要素は DB が消させない。
    基準の後の要素の削除（5.9 `remove_element`・API-WBS-005）と両立しないので CR-002 を起票した（暫定対応はそこに書いた）
  - 必須の列：要素の `project_id`・`kind`・`name`・`wbs_code`・`sibling_seq`・`last_child_seq`・`sort_order`・`needs_renumber`、採番規則の 4 項目、新旧対応の全ての列、紐付けの 2 列、
    レビューの `status`・`requested_by`・`requested_at`・`reviewer_ids`、指摘の `review_id`・`category`・`body`・`status`・`author_id`・`updated_at`、
    申請の `target_type`・`target_id`・`status`・`requested_by`・`requested_at`、版の `version`・`reason`・`created_by`・`created_at`、写しの `scope_baseline_id`・`element_id`・`wbs_code`・`kind`・`name`。
    任意：要素の `parent_id`（最上位）・`own_estimate_hours`（未見積）・責任者・担当チーム、辞書の 4 項目（WBS-13 で空の WP を一覧にするため）、`closed_at`、
    **指摘の `element_id`**（重複の指摘への対応で要素を消せるように。消すときはサービス層が空にして指摘は残す。WBS 全体への指摘にも使える）、`resolution`、`decided_*`・`comment`、
    版の `approval_request_id`・`change_request_id`、写しの `parent_element_id`・`own_estimate_hours`・`dictionary`
  - 一意制約は 4.1 に書かれたものだけ（`wbs_numbering_rules.project_id`・`wbs_dictionary_entries.element_id`・`wbs_scope_links`（element_id, scope_item_id））と、
    版の「プロジェクト内の連番」から `scope_baselines`（project_id, version）。**`wbs_code` は一意にしていない**（4.1 に無い。改番で行を 1 つずつ書き換える途中で同じ値が並ぶ）
  - 値の検査（CHECK）：区分・状態・指摘の分類・申請の対象・版の理由は 4.1 の値の一覧。`sibling_seq >= 1`・`last_child_seq >= 0`（5.2 の順番は 1 から）、
    `own_estimate_hours` は空か 0 以上（RULE-EST-001 が負を拒むのと同じ）、`min_digits >= 1`、`version >= 1`。区切り文字・接頭辞の形は API-WBS-006 の担当で DB は見ない
  - 既定値：`last_child_seq`＝0・`needs_renumber`＝偽、採番規則は 4.1 の既定（`.`・1・空・真）、レビュー・指摘は OPEN、申請は PENDING を、モデルと DB の両方に置いた。
    `reviewer_ids` はモデルの既定値（空の配列）だけ。時刻（`renumbered_at`・`requested_at`・`updated_at`・`created_at`）はモデルの既定値 `utc_now`（`updated_at` は更新でも入れ直す）
  - 文字列の長さ：`wbs_code`・`old_code`・`new_code` 100、名称・担当チーム 200、`kind`・`status`・`category`・`target_type` 16、`reason` 32、`separator` 8、`prefix` 20。本文・対応・コメント・辞書の項目は `Text`
  - 写しの `dictionary` は `JSON(none_as_null=True)`（辞書の無い要素は SQL の NULL）
  - **WBS の承認申請の `target_id` はプロジェクトの ID とした**（WBS はプロジェクトに 1 つ。OQ-16）
  - **採番規則を作る口を `NumberingRulesTable`（`app/services/projects.py`）に差し替えた**（A-14 の申し送り）。API-PRJ-001 は同じトランザクションで 4.1 の既定の行を 1 つ作る。
    `app.main.create_app` の `app.state.numbering_rules` を差し替え、`backend/tests/api/test_prj.py` に既定の行ができることの試験を足した
- **理由:** 設計書 2.4・3 章・4.1・4.2・5.2・5.9・5.10・6.2、要求仕様書 WBS-01・02・05・06・10・11・13・16・20・21・22・CHG-03・04。A-08・A-11・A-14 の申し送り
- **影響範囲:** `backend/app/models/wbs.py`・`backend/app/models/types.py`・`backend/app/models/prj.py`・`backend/app/models/__init__.py`・`backend/alembic/versions/20261110_0003_wbs_tables.py`・
  `backend/app/services/projects.py`・`backend/app/main.py`・`backend/tests/models/test_models_wbs.py`・`backend/tests/api/test_prj.py`
- **覆す場合:** CR-002 で写しの外部キーを外すなら移行を 1 本足し、試験 `test_scope_baseline_elements_block_deleting_baselined_element` を「消せて写しは残る」に直す。
  指摘の `element_id` を必須にするなら NOT NULL の移行を足し、試験 `test_review_comments_defaults_and_states` の WBS 全体への指摘を直す
- **限界:** 移行は SQLite でだけ流した（upgrade・downgrade・upgrade）。PostgreSQL では流していない。第 1 階層の順番を数える場所（OQ-16）は決めていない。
  変更履歴（`audit_logs`）への記録は CHG のスコープ（OQ-08）で、この群の書き込みも記録していない

## A-16: WBS の採番・維持・判定・承認の状態遷移（rule:WBS）で決めたこと

- **選択:**
  - 置き場は 5.1 の実装先どおり。`backend/app/domain/wbs_code.py`（RULE-WBS-001 `make_code`・RULE-WBS-002 `new_element_code` ほか）、
    `backend/app/domain/wbs_rules.py`（RULE-WBS-003 `check_hierarchy`・004 `check_hundred_percent`・005 `find_missing_completion`・007 `build_responsibility_matrix`・008 `trace_wbs_code`）、
    `backend/app/domain/workflow.py`（RULE-WBS-006 `transition_wbs`。承認申請の判断 `decide_approval` も置いた）
  - 採番：5.2 の式をそのまま使う。子の ID は**親が今持っている ID**（`code_stem`）から作る（移動して改番待ちの親の子も、親の表示と同じ頭になる）。
    第 1 階層の補完は 5.2 の式どおり `separator + "0"`（最小桁数が 2 でも `"00"` にしない）。区切り文字は空と数字を含むものを拒む（順番の桁と見分けられない）。
    ID の長さは列の長さ（A-15 の 100 文字）を超えれば拒む
  - 新規：順番は「親の `last_child_seq` ＋ 1」。改番待ちの親の下の新しい要素も改番待ち（5.2 の「配下を含む」）。
    最上位の要素の数え（OQ-16 (2)）は引数 `top_last_child_seq` で受け、置き場は決めていない（api:WBS が決める）
  - 移動：親が変わるときだけ「移動先の `last_child_seq` ＋ 1」を順番にする。**同じ親の中の移動（並べ替え）では順番も `last_child_seq` も変えない**。
    「形が合う」は、最上位なら `prefix + pad(n)`（補完ありなら末尾に `separator + "0"`）、子なら「親の `code_stem` ＋ 区切り ＋ `pad(n)`」（0 詰めの形まで見る）。
    移動した要素と配下の改番待ちを全て計算し直して返す（元の親へ戻して形が合えば解ける）。移動先が自分か配下なら拒む
  - 改番：兄弟の並びは `sort_order`、同じなら今の順番、次に要素の ID。**`last_child_seq` は減らさない**（BR-02「今まで使った最大の順番＋1」。改番の後も、消した番号の後から振る）。
    子の数が `last_child_seq` を超える入力では子の数まで上げる
  - 旧 ID の解決：今の ID が優先（BR-02）。新旧対応は `renumbered_at` の新しい順、同じなら `id` の大きい順。**最初に一致した対応の要素が消えていれば `None`**（古い対応へ遡らない）。
    今の ID が同じ要素が 2 つ以上あれば決めずに拒む
  - 階層と区分：操作の前後の判定は持たず、サービス層が**操作した後の木**を渡して不整合を全て列挙させる（`ensure_hierarchy` は最初の不整合のコードで `WbsRuleError`）
  - 100% ルール：許容幅は割合（0.10 が 10%）の `Decimal`。「割合 > 許容幅」は割り算をせず `abs(子の集計−自分の見積) > 許容幅 × 自分の見積` で比べる（同値）。
    `OUT_OF_SCOPE_LINKED` は紐付け 1 件につき 1 件。並びは差異 → 混入 → 漏れ
  - 完了条件：空は「辞書の行が無い・NULL・空白だけ（全角の空白を含む）」
  - 責任分担表：「責任者の無い WP」は `responsible_user_id` が無い WP（担当チームだけでも入れる）。担当は WP の活動のどれかに割当がある人。
    メンバーでない人が責任者・担当に残っていれば列の後ろに足して `non_member_user_ids` で示す（表を出せなくしない）
  - 横断照会：EAC は計算せず、呼び手が RULE-EFF-002 の集計の値（`eac_hours_by_element`）を渡したときだけ解決した要素の値を返す（EFF の群の担当を取らない）。
    変更要求は `target_codes` の各 ID を旧 ID の解決にかけて配下に当たるかで選ぶ（解決できない ID は数えない）
  - 状態遷移：5.10 の表に無い遷移は `INVALID_STATE`。承認待ちの間の変更（`EDIT`）は拒む（OQ-17）。状態を先に見て、次に前提（`WBS_NOT_READY`）・本人（`SELF_APPROVAL_FORBIDDEN`）。
    承認の版は、最新の版が無ければ 1・`INITIAL_APPROVAL`、あれば次の版・`RE_APPROVAL`。差戻しも本人を拒む（5.10 の「判断できない」）
- **理由:** 設計書 3 章・4.1・5.1・5.2・5.3・5.10・6.1、要求仕様書 WBS-01・02・05・06・07・08・10・11・13・16・20・21・22、PRJ-02、BR-01・02・03、W§4 の例
- **影響範囲:** `backend/app/domain/wbs_code.py`・`backend/app/domain/wbs_rules.py`・`backend/app/domain/workflow.py`・
  `backend/tests/domain/test_wbs_code.py`・`backend/tests/domain/test_wbs_rules.py`・`backend/tests/domain/test_workflow.py`
- **覆す場合:** 改番で `last_child_seq` を子の数へ戻すなら `renumber` の `last_child_seqs` を変え、試験 `test_renumber_follows_sort_order_and_returns_changes` の 5・2 を直す。
  同じ親への移動でも新しい順番を振るなら `move_element` の分岐を外し、試験 `test_move_within_same_parent_keeps_seq` を直す。
  消した要素の旧 ID で古い対応へ遡るなら `resolve_code` の `return None` を `continue` にし、試験 `test_resolve_takes_newest_history_first` を直す
- **限界:** ドメインの関数だけで、サービス・API（api:WBS）からはまだ呼んでいない。最上位の数えの置き場（OQ-16 (2)）・変更履歴への記録・
  `projects.wbs_status` の保存はサービス層の担当で、まだ無い。変更要求（RULE-CHG-003）の状態遷移は CHG の群の担当で、`decide_approval` だけを共通に置いた

## A-17: WBS の API（api:WBS）の API-WBS-001〜010・012〜017・021・022 で決めたこと

- **選択:**
  - ルータは `backend/app/api/wbs.py`、ユースケースは `backend/app/services/wbs.py`、入出力は `backend/app/schemas/wbs.py`。読むのは project.view、書くのは wbs.edit（`authorize_project`。無いプロジェクトは 404 を先に返す）。
    委託先だけで読む利用者は `own_assignment_scope`（RULE-AUTH-001）の要素に絞る（木の `restricted` が真。見えない要素の辞書は 404）
  - **活動を読む口 `ActivitiesReader` を `app.state.wbs_activities` に載せた**（活動の表 DB-SCH-001 がまだ無いので既定は `ActivitiesTableAbsent`＝活動なし。A-14 と同じ形）。
    **db:SCH・api:SCH のスコープは読む実装へ差し替える。** 差し替え忘れは `backend/tests/api/test_wbs.py::test_activities_reader_is_wired_once_activities_table_exists` が落とす
  - WBS を変える操作（要素の追加・変更・移動・削除、改番、辞書、紐付け）は RULE-WBS-006 の `EDIT` で `projects.wbs_status` を動かす（BASELINED → DRAFT。承認待ちは 409 `INVALID_STATE`）。採番規則の変更は要素を変えないので状態を動かさない
  - 木（API-WBS-001）は先行順（兄弟は `sort_order`・順番・ID）の平らな配列に `depth`（最上位 1）を付ける。集計値は RULE-EST-001、検査の件数は API-WBS-012 と同じ値
  - 追加（API-WBS-002）：表示順は兄弟の末尾。第 1 階層の順番は**今ある第 1 階層の `sibling_seq` の最大＋1**（4.1 に置き場が無い。CR-003・OQ-16 (2)）。責任者はメンバーである有効な利用者（400。OQ-18）
  - 移動（API-WBS-004）：`position`（0 から。省けば末尾）に入れ、移動先の兄弟の `sort_order` を 1 から振り直す。自分か配下の下は 422 `PARENT_CYCLE`、WP の下は 422 `WORK_PACKAGE_HAS_CHILDREN`
  - 削除（API-WBS-005）：子から消す。辞書・紐付け・新旧対応は消し、指摘は `element_id` を空にして残す。版に入った要素は 422 `ELEMENT_IN_BASELINE`（CR-002）、活動のある WP は 422 `ELEMENT_HAS_ACTIVITIES`（OQ-18）
  - 採番規則（API-WBS-006）：4 項目を全て受ける。RULE-WBS-001 の拒む規則と、`/` を含む区切り・接頭辞（WBS ID が API-WBS-023 のパスに入る）は 400。最小桁数の上限は 9。今の ID は変えない
  - 改番（API-WBS-007）：ID・順番・改番待ちが何も変わらなければ何も書かない（`renumbered_at` は null）。新旧対応の一覧（API-WBS-008）は新しい順で、要素の今の ID を添える
  - 辞書（API-WBS-010）は 4 項目を載せた値で置き換える（省いた項目は空）。紐付け（API-WBS-013）は丸ごと置き換え、無い・他のプロジェクトの項目・重複は 400
  - 工数は小数 2 桁までの数で受け、数（float）で返す
  - レビュー（API-WBS-014〜017）：始めると WBS は DRAFT → IN_REVIEW（他の状態は 409）。頼む人は 1 人以上のメンバー（重複・メンバーでない人は 400）。
    指摘は閉じた（CLOSED）レビューには書けず・変えられない（409）。指摘の状態は 3 値のどれへも変えられる。委託先には見える要素への指摘だけを返す。
    **レビューを閉じる操作（5.10 の IN_REVIEW → DRAFT）は 6.2 に無いので作っていない**（OQ-18 (2)）
  - スコープベースライン（API-WBS-021・022）：版の順。版の中身は写しのまま返す（今の要素の値で上書きしない）。委託先には今見える要素の写しだけ
- **理由:** 設計書 1.3・3 章・4.1・5.2・5.3・5.8・5.10・6.1・6.2・9 章、要求仕様書 WBS-01・02・04・05・06・10・11・13・16、BR-01・02・03。A-14・A-15・A-16 の申し送り
- **影響範囲:** `backend/app/api/wbs.py`・`backend/app/services/wbs.py`・`backend/app/schemas/wbs.py`・`backend/app/main.py`・`backend/tests/api/test_wbs.py`
- **覆す場合:** CR-003 が承認されたら `_top_last_child_seq` を列の読み書きへ替え、試験に「最大の第 1 階層の要素を消した後も番号を再利用しない」を足す
- **限界:** API-WBS-011・018・019・020・023（責任分担表・承認の申請と判断・承認待ちの一覧・横断照会）はこの項目の時点では未実装だった（A-18 で実装した）。変更履歴（6.1）は infra の `audit_listener` が空なので記録していない（OQ-08）。
  PostgreSQL では流していない。同時の書き込み（同じ親への 2 つの追加）の順番の衝突は扱っていない

## A-18: WBS の API（api:WBS）の API-WBS-011・018・019・020・023 で決めたこと

- **選択:**
  - **別の群の表を読む口 `CrossGroupReaders` を `app.state.wbs_readers` に載せた**（`app/services/wbs.py`）。割当（DB-EFF-001）・見積の記録（DB-EST-001）・実績工数（DB-EFF-002）・
    変更要求（DB-CHG-001）・是正措置（DB-CHG-002）を読む関数と EAC（RULE-EFF-002）を読む関数の 6 つ。どの表もまだ無いので既定は 0 件・EAC は `None`（A-17 の `ActivitiesReader` と同じ形）。
    **表を作るスコープ（db:EFF・db:EST・db:CHG）はその表を読む関数へ、api:EFF は EAC を読む関数へ差し替える。** 差し替え忘れは
    `backend/tests/api/test_wbs.py::test_cross_group_readers_are_wired_once_tables_exist` が落とす（EAC は `app.services.effort` ができたら）
  - **変更要求の申請への判断は `ChangeRequestApprovals`（`app.state.change_request_approvals`）へ渡す**。既定の `ChangeRequestsTableAbsent` は止める（RuntimeError。
    変更要求の申請は提出 API-CHG-005 でしか作られないので、表が無い間は来ないはず。fail fast）。**api:CHG が RULE-CHG-003 で変更要求の状態を動かす実装へ差し替える。**
    差し替え忘れは `test_change_request_approvals_are_wired_once_change_requests_table_exists` が落とす
  - 責任分担表（API-WBS-011）：行は WP を木の先行順（兄弟は表示順）、列はメンバーを加えた順（`project_members.id`）。メンバーでない人が責任者・担当に残れば列の後ろ（RULE-WBS-007）。
    委託先には見える WP の行だけ（列は減らさない）。**CSV は 6.3 の約束（UTF-8・BOM 付き・カンマ区切り・1 行目が見出し）に合わせ、列は
    「WBS ID, 名称, 責任者のログインID, 担当チーム, 割当工数の合計（人時）, 利用者ごとの列（見出しはログイン ID。メンバー外は「（メンバー外）」）」、
    マスは「責任者」「担当 <人時>」「責任者・担当 <人時>」か空、人時は小数 2 桁**（6.3 に責任分担表の行が無い。OQ-19）。`format` が json・csv 以外は 400
  - 承認の申請（API-WBS-018）：前提の件数は RULE-WBS-005 の欠け・**プロジェクトの全てのレビューの OPEN の指摘**・RULE-WBS-004 の ERROR。満たさなければ 422 `WBS_NOT_READY`、
    `details` は満たしていない前提ごとの `{condition, count}`。申請の本文は無い。**申請のときに開いている（OPEN）レビューを閉じる（CLOSED・`closed_at`）**
    （OQ-18 (2)。閉じる API が 6.2 に無いので、5.10 の IN_REVIEW → PENDING_APPROVAL で閉じる。差し戻された後は新しいレビューを始める）
  - 承認・差戻し（API-WBS-019）：権限は申請の対象で選ぶ（WBS は wbs.approve、変更要求は change.approve）。**本人の判断は 403 `SELF_APPROVAL_FORBIDDEN`**
    （9.2 の注記「権限を持っていても判断できない」に合わせて権限の拒否として扱う。OQ-19）、判断済みの申請・承認待ちでない WBS は 409 `INVALID_STATE`。
    WBS の承認はその時点の全ての要素と辞書（4 項目。辞書が無ければ空）を版の写しにする。版の `created_by` は承認した人。差戻しは版を作らない。本文は `decision`（APPROVE・REJECT）と任意の `comment`
  - 承認待ちの一覧（API-WBS-020）：PENDING の申請のうち、そのプロジェクトで判断の権限を持ち（RULE-AUTH-001。システム管理者は全て）、自分の申請でないもの。申請の古い順、`limit`・`offset`。
    プロジェクトのコード・名称と申請者の表示名を添える
  - 横断照会（API-WBS-023）：解決できない ID は 404、今の ID を 2 つ以上の要素が持つなど決められなければ 422 `WBS_CODE_INVALID`。
    委託先は見える要素へ解決した ID だけ（見えなければ 404）、活動は自分の割当、見積・是正措置・変更要求は見える要素か割当の活動に当たるものだけ、EAC は返さない（配下の見えない分を含むため）
- **理由:** 設計書 1.3・4.1・5.1（RULE-WBS-006・007・008）・5.10・6.1・6.2・6.3・9 章、要求仕様書 WBS-07・10・13・20・21・22、CHG-03。A-15・A-16・A-17 の申し送り
- **影響範囲:** `backend/app/api/wbs.py`・`backend/app/services/wbs.py`・`backend/app/schemas/wbs.py`・`backend/app/main.py`・`backend/tests/api/test_wbs.py`
- **覆す場合:** 本人の判断を 422 にするなら `app/services/wbs.py` の `_WORKFLOW_STATUS` を変え、試験 `test_approve_creates_baseline_versions_and_lists_pending` の 403 を直す。
  レビューを申請で閉じないなら `submit_wbs` の `update(WbsReview)` を外し、試験 `test_submit_checks_preconditions_and_closes_review` の CLOSED を直す
- **限界:** 割当・見積の記録・実績・変更要求・是正措置・EAC は表が無いので、試験は差し替えた読み口（試験の中の代わり）でだけ確かめた。実際の表からの読み方は確かめていない。
  変更履歴（6.1）は記録していない（OQ-08）。同時に 2 人が同じ申請を判断したときの衝突（版の番号の一意制約で後の方が 500 になる）は扱っていない。PostgreSQL では流していない

## A-19: スケジュール（SCH）のテーブル（db:SCH）で決めたこと

- **選択:**
  - 置き場は `backend/app/models/sch.py`（DB-SCH-001〜004）、移行は `0004_sch`
  - 外部キー：4.1 の `*_id`・`created_by` に張った。`activities.work_package_id` は `wbs_elements.id`、依存の `predecessor_id`・`successor_id` とベースラインの項目の `activity_id` は `activities.id`。`ON DELETE` は付けない（A-11・A-15 と同じ）。
    活動のある WP・依存のある活動・項目のある版は DB が消させない（活動を消すサービスが依存の行から扱う）。
    **`schedule_baselines.change_request_id` は外部キーをまだ張らない**（`scope_baselines` と同じ。A-15）。**db:CHG のスコープは `change_requests` を作る移行で、`scope_baselines` とあわせてこの列にも外部キーを張る**。
    張り忘れると `backend/tests/models/test_models_sch.py::test_schedule_baselines_change_request_id_references_change_requests_once_it_exists` が落ちる
  - **ベースラインの項目の `activity_id`・`element_id` は空を許す**（OQ-20 (1)）。写した活動・WP を後から消すサービス（API-SCH-005 ほか）は、先に写しの紐付けを空にしてから消す（写しの値は残る）。
    紐付けの空いた ACTIVITY の項目が SCH-18 の「削除された活動」。WP の項目は活動を指さない（DB の検査）。`element_id` は項目の WP（WP の項目は自分、活動の項目は活動の置き場の WP）
  - 必須の列：活動の `project_id`・`work_package_id`・`name`・`sort_order`・`duration_days`・`is_milestone`・`is_critical`、依存の全ての列、版の `version`・`name`・`reason`・`created_by`・`created_at`、
    項目の `schedule_baseline_id`・`item_type`・`wbs_code`・`name`・`is_milestone`。任意：希望開始日（空ならプロジェクトの開始日。5.5）・見積（未見積）・算定方式（空はプロジェクトの既定。4.1）・
    日程計算の結果の 7 列（計算の前は空）・版の `change_request_id`・項目の日付・期間・見積（活動の無い WP の項目には日付が無い）
  - 一意制約は 4.1 に書かれた依存の（predecessor_id, successor_id）と、版の「プロジェクト内の連番」から `schedule_baselines`（project_id, version）。活動には一意の定めが無い
  - 値の検査（CHECK）：算定方式は BR-06 の 7 つ（`app/schemas/projects.py` の `ProgressMethod` と同じ。試験で突き合わせる）、版の理由・項目の種類は 4.1 の値の一覧。
    **マイルストーンは期間 0、それ以外の活動は期間 1 以上**（4.2 と RULE-CAL-001 の「期間 0 がマイルストーン」。期間 0 の活動は 5.5 の終了日が開始日の前になる）。
    **依存は FS・ラグ 0 だけ**（4.1 の「R1 は」・OQ-04。API の 422 に加えて DB も拒む）と、自分への依存（自明な循環）。見積・項目の期間は 0 以上、`version >= 1`
  - 最早・最遅（`early_start`〜`late_finish`）は日付（OQ-20 (2)）。5.5 の「結果の日付」と同じく、開始は添字 ES・LS の稼働日、終了は添字 EF − 1・LF − 1 の稼働日を入れる想定（RULE-SCH-002・003 と api:SCH が書く）。`total_float` は稼働日の整数（負も許す）
  - 版の `name` は必須・200 文字（OQ-20 (3)）。文字列の長さは他の群に合わせた（名称 200、WBS ID 100、種類・区分 16、理由・算定方式 32、依存の種類 8）
  - 既定値：`is_milestone`・`is_critical` は偽、依存は FS・0 を、モデルと DB の両方に置いた。版の `created_at` はモデルの既定値 `utc_now`
  - **別の群の口を差し替えた**（A-14・A-17 の申し送り）：
    - WBS の判定・集計が読む活動 `app.state.wbs_activities` を、表を読む `ActivitiesTable`（`app/services/wbs.py`）へ。活動の ID の順に、ID・WP・見積を返す
    - 日程の再計算 `app.state.schedule_recalculator` を `SchedulingNotWired`（`app/services/projects.py`）へ。**日程計算（RULE-SCH-002・003）は rule:SCH、保存まで通すのは api:SCH の担当で、このスコープには無い**。
      活動の無いプロジェクトは何もせず、活動があればカレンダー・開始日の変更を止める（RuntimeError。fail fast。計算しないまま古い日程を残さない）。
      今は活動を作る API が無いので起きない。**api:SCH は API-SCH-008 と同時に日程計算の実装へ差し替える**。差し替え忘れは
      `backend/tests/api/test_prj.py::test_schedule_recalculator_is_replaced_once_schedule_api_exists` が落とす
- **理由:** 設計書 2.4・3 章・4.1・4.2・5.5・6.2・15 章、要求仕様書 SCH-01・03・04・06・09・10・16・17・18、EFF-08、BR-05・06・12、OQ-04。A-11・A-14・A-15・A-17 の申し送り
- **影響範囲:** `backend/app/models/sch.py`・`backend/app/models/__init__.py`・`backend/alembic/versions/20261111_0004_sch_tables.py`・`backend/app/services/wbs.py`・`backend/app/services/projects.py`・
  `backend/app/main.py`・`backend/tests/models/test_models_sch.py`・`backend/tests/api/test_wbs.py`・`backend/tests/api/test_prj.py`
- **覆す場合:** 写した活動を消せなくするなら項目の `activity_id` を必須にする移行を足し、試験 `test_schedule_baseline_items_survive_deleted_activity` を「消せない」に直す。
  最早・最遅を軸の位置で持つなら 4 列を整数にする移行を足し、試験 `test_activities_store_schedule_results` を直す。依存の種類を広げる（R2 の SCH-05）なら検査を外す移行を足し、試験 `test_dependencies_rejections` の SS・FF・SF・ラグを直す
- **限界:** 移行は SQLite でだけ流した（upgrade・downgrade・upgrade）。PostgreSQL では流していない（真偽の列の検査の式は両方で通る形にした）。
  活動の `project_id` と置き場の WP の `project_id` が同じこと、WP だけに置けること（RULE-WBS-003）、依存の両端が同じプロジェクトであることは DB では見ていない（サービス層の担当）。
  変更履歴（`audit_logs`）への記録は CHG のスコープ（OQ-08）で、この群の書き込みも記録していない。日程の再計算は実装していない（rule:SCH・api:SCH）

## A-20: スケジュール（SCH）の計算・判定ルール（rule:SCH）で決めたこと

- **選択:**
  - 置き場は 5.1 どおり `backend/app/domain/scheduling.py`（RULE-SCH-001・002・003・006）・`baseline.py`（RULE-SCH-004）・`gantt.py`（RULE-SCH-005）。
    試験は `backend/tests/domain/test_scheduling.py`・`test_baseline.py`・`test_gantt.py`
  - 循環検出（RULE-SCH-001）：強連結成分（Tarjan、再帰なし）で**循環に含まれる活動だけ**を返す（循環の下流・2 つの循環の間の活動は入れない）。
    依存を足す前の判定 `DependencyGraph.cycle_if_added(p, s)` は、後続から先行への最短の経路（同じ長さなら ID の小さい活動を先）を返す。
    R1 の依存は FS・ラグ 0 だけを受け、それ以外は `SchedulingError`（OQ-04）
  - 前進計算（RULE-SCH-002）：5.5 の式どおり。軸の原点はプロジェクトの開始日。**希望開始日がプロジェクトの開始日より前でも下限にしない**（OQ-21 (1)）。
    トポロジカル順の同順位は id の昇順（優先度つきの列。この分だけ O(活動 log 活動)）。日程の結果は前進・後退の後で日付へ直す（`calculate_schedule`）
  - 後退計算（RULE-SCH-003）：`E` はマイルストーンの `M` も含む `EF` の最大。マイルストーンの最遅の日付は添字 `LM − 1`（最早の `M − 1` と同じ変換。A-19 の申し送り）
  - 一括更新の検査（RULE-SCH-006）：変える項目は名称・希望開始日（`None` で空にする）・期間・先行の全体（置き換え）・担当の全体（置き換え）。
    「変えない」は `UNSET` で表す。マイルストーンは期間で決まる（期間 0。BR-04）。担当にできる利用者はサービス層が決めて渡す（`assignable_user_ids`）。
    変更ごとの不正は全て集めて返し、**変更ごとの不正が無いときだけ**変更後の依存の全体で循環を見る（循環も全て返す）
  - 差異（RULE-SCH-004）：差は「最新 − 基準」（正は遅れ・増加）。日付の差は `[基準, 最新)` の稼働日の数（前なら負）。
    追加・削除の行の差は `None`（OQ-21 (4)）。行は計画の並び（渡された順）の後に削除を基準の項目の順に並べる。比べる版の既定は版の番号の最大（OQ-21 (3)）
  - ガント（RULE-SCH-005）：行は活動だけ（OQ-21 (2)）。位置は稼働日の軸、座標は暦日（日付＋その日の中の割合）。**区間の始まりは添字 `floor(p)` の日の割合、
    終わりは添字 `ceil(p) − 1` の日の割合**（金曜に終わるバーは月曜の始業でなく金曜の終業）。マイルストーンはその日の終業の 1 点。
    実績のバーは未完了なら報告日の終業（非稼働日なら前の稼働日の終業＝5.4 の報告日線）まで。未完了の実績があるのに報告日が無ければ `GanttError`（fail fast）。
    非稼働日の帯は全てのバーの最も早い日〜最も遅い日の範囲
  - WBS の並び（先行順・兄弟は表示順）を他の層から使えるよう、`app/domain/wbs_code.py` に `display_preorder` を足した（既存の木の検査 `_index_tree` をそのまま使う）
- **理由:** 設計書 5.1・5.4・5.5・7.3、要求仕様書 7.4 BR-04・7.5 BR-05、SCH-02・04・06・09・10・11・12・13・17・18、NFR-01。A-19 の申し送り
- **影響範囲:** `backend/app/domain/scheduling.py`・`baseline.py`・`gantt.py`・`wbs_code.py`、`backend/tests/domain/test_scheduling.py`・`test_baseline.py`・`test_gantt.py`
- **覆す場合:** 希望開始日をプロジェクトの開始日で下限にするなら `forward_pass` の下限に `max(…, 0)` を足し、試験 `test_requested_start_before_project_start_follows_formula` を直す。
  ガントに WBS の集約の行を足すなら `GanttRow` に行の種類を足す。追加・削除の見積の差を出すなら `compare_with_baseline` と試験 `test_added_and_deleted_activities_are_distinguished` を直す
- **限界:** サービス・API（api:SCH）からはまだ呼んでいない（`app.state.schedule_recalculator` は `SchedulingNotWired` のまま。A-19）。
  NFR-01 の性能は domain の計算だけを測った（活動 2,000・依存 5,000 で 0.3 秒程度。DB の読み書きは含まない）。
  一括更新は、変更ごとの不正があると循環の検査まで進まない（循環はその不正を直した後の 2 回目で出る）

## A-21: スケジュール（SCH）の API（api:SCH）で決めたこと

- **選択:**
  - ルータは `backend/app/api/schedule.py`（2.1 の群のファイル名）、ユースケースは `backend/app/services/schedule.py`、入出力は `backend/app/schemas/schedule.py`、試験は `backend/tests/api/test_sch.py`。
    読むのは project.view、活動・依存・日程計算は schedule.edit、ベースラインの保存は baseline.manage（`authorize_project`。無いプロジェクトは 404 を先に返す）。
    委託先だけで読む利用者は自分の割当の活動に絞る（一覧・クリティカルパス・ガント・差異。`restricted` が真。クリティカルパスのプロジェクトの終了日は返さない）
  - **活動・依存を変える操作は、同じトランザクションの最後にプロジェクトの全ての活動の日程を計算し直して保存する**（`calculate_and_store`。RULE-SCH-001〜003。SCH-06）。
    **`app.state.schedule_recalculator` を `SchedulingNotWired` から `ScheduleCalculator` へ差し替えた**（A-19 の申し送り）。カレンダー・開始日の変更（API-PRJ-004・008）も同じ計算で保存する
  - 開始日・期間・終了日（SCH-03）：保存するのは希望開始日と期間（4.2）。終了日（`requested_finish`）は保存せず、開始日か期間と組にして RULE-CAL-001 で残りを求める（3 つ全て・終了日だけは 400）。
    PATCH で終了日だけを載せたら期間を保って開始日を動かす。期間 0 がマイルストーン（`is_milestone` は期間と食い違えば 400）。日付の組み合わせの誤りは 400、WP 以外の置き場は 422 `ACTIVITY_NOT_ON_WORK_PACKAGE`
  - 一覧（API-SCH-001）・ガントの行の順は RULE-SCH-005 と同じ（WBS の先行順 → WP の中の `sort_order` → id）。一覧は依存（ID 付き。API-SCH-007 で使う）を添える。追加した活動は WP の中の末尾
  - 一括更新（API-SCH-004）：RULE-SCH-006 の結果をそのまま返す。不正が循環だけなら 422 `SCHEDULE_CYCLE`、それ以外は 422 `BULK_UPDATE_INVALID`（`details` に全ての不正）。名称・期間の null は 400
  - 依存（API-SCH-006）：両端が無い 400 → FS・ラグ 0 以外 422 `UNSUPPORTED_DEPENDENCY` → 同じ組 409 `DUPLICATE_DEPENDENCY` → 循環 422 `SCHEDULE_CYCLE`（`details` は `{activity_ids}`。自分への依存も循環）の順に見る
  - クリティカルパス（API-SCH-009）は今の入力から計算する（保存しない）。日程計算（API-SCH-008）は保存して同じ形で返す
  - ガント（API-SCH-010）：`baseline_id` を省けば最新の版（版が無ければ基準のバーは無い。OQ-22 (4)）。位置と暦日の割合は数で返す
  - ベースライン（API-SCH-011）：版は最大＋1、理由は MANUAL、作った時刻は「今」（`get_now`）。項目は全ての活動と全ての WP（OQ-22 (5)）
  - **別の群の表の口 `ScheduleLinks` を `app.state.schedule_links` に載せた**：割当（`ActivityAssignments`。既定 `AssignmentsTableAbsent`＝割当なし・担当を置く変更は止める）と
    最新の進捗（`latest_progress`。既定 `no_progress`）。**db:EFF は割当を、db:PRG・api:PRG は最新の進捗を読む実装へ差し替える**。差し替え忘れは
    `backend/tests/api/test_sch.py::test_assignments_port_is_wired_once_assignments_table_exists`・`test_progress_reader_is_wired_once_progress_table_exists` が落とす
  - `backend/tests/api/test_prj.py::test_schedule_recalculator_is_replaced_once_schedule_api_exists` は、FastAPI 0.162 の `app.routes` が `include_router` したパスを出さない
    （`_IncludedRouter`）ため API-SCH-008 を見つけられず、常に「まだ無い」側を確かめていた。OpenAPI のパスでも探し、パスを読めていることも確かめるように直した（判定を厳しくする向き）。
    `test_scheduling_not_wired_stops_only_projects_with_activities` は既定の口が変わったので、`SchedulingNotWired` を明示して載せてから同じ主張を確かめる
- **理由:** 設計書 1.3・2.1・4.1・4.2・5.4・5.5・6.1・6.2・7.3・9 章、要求仕様書 SCH-01〜04・06・09〜13・16〜18、BR-04・05・12。A-19・A-20 の申し送り
- **影響範囲:** `backend/app/api/schedule.py`・`backend/app/services/schedule.py`・`backend/app/schemas/schedule.py`・`backend/app/main.py`・`backend/tests/api/test_sch.py`・`backend/tests/api/test_prj.py`
- **覆す場合:** 見積を SCH の API でも書くなら `ActivityCreate`・`ActivityUpdate` と `UPDATABLE_FIELDS` に `estimate_hours` を足す（見積の記録 DB-EST-001 も同時に書く）。
  ガントの既定を「基準なし」にするなら `gantt` の `select_baseline` を `baseline_id` があるときだけにし、試験 `test_save_and_list_baselines` の省いたときの版を直す
- **限界:** 割当・最新の進捗は表が無いので、試験は差し替えた口（試験の中の代わり）でだけ確かめた。担当を置く一括更新は既定の口では 500 になる（OQ-22 (2)）。
  変更履歴（6.1）は記録していない（OQ-08）。同時に 2 人がベースラインを保存したときの版の番号の衝突（一意制約で後の方が 500）・同時の活動の変更の衝突は扱っていない。
  日程の再計算はプロジェクトの全活動を毎回読み書きする（NFR-01 の 1 秒は domain の計算だけを測った。DB の読み書きを含めた時間は測っていない）。PostgreSQL では流していない

## A-22: 進捗（PRG）のテーブル（db:PRG）で決めたこと

- **選択:**
  - 置き場は `backend/app/models/prg.py`（DB-PRG-001〜005）、移行は `0005_prg`
  - 外部キー：4.1 の `*_id`・`created_by`・`finalized_by`・`entered_by` に張った。`ON DELETE` は付けない（A-11・A-15・A-19 と同じ）。
    進捗の入力・算定項目のある活動、入力・スナップショットのある報告日、活動の写しのあるスナップショットは DB が消させない。
    **スナップショットの活動の `activity_id` は空を許す**（A-19 のベースラインの項目と同じ）。写した活動を後から消すサービスは、先に写しの紐付けを空にしてから消す（写しの値は残る。BR-13）。
    **活動を消す API-SCH-005（`app/services/schedule.py` の削除）は、まだ進捗の入力・算定項目・スナップショットの写しを扱っていない**。今は進捗を書く API が無いので起きないが、
    api:PRG がそれらを書くようになると、進捗のある活動の削除は DB の外部キーで止まる（500）。扱い方（拒む 409 か、入力・項目を消し写しの紐付けを空にするか）は OQ-23 (3)
  - 必須の列：報告日の `project_id`・`report_date`・`status`・`created_by`・`created_at`、入力の `status_report_id`・`activity_id`・`entered_by`・`entered_at`、
    項目の `activity_id`・`item_type`・`title`・`weight`・`done`・`approved`、スナップショットの全ての列、写しの `snapshot_id`・`row_order`・`wbs_code`・`name`・`is_milestone`・`is_critical`・`point`・`deviation`・`judgement`。
    任意：確定の利用者・時刻（OPEN の間は空）、進捗率（NULL＝未算定。4.1）・残工数（NULL＝自動。4.1）・実績日付・遅延理由・完了見込み、項目の完了の時刻、写しの計画日付・期間・進捗率・実績・工数（活動の列が空を許すのに合わせた）
  - 一意制約：4.1 に書かれた報告日の（project_id, report_date）、入力の（status_report_id, activity_id）、スナップショットの `status_report_id`。
    4.1 に無いものを 1 つ足した：**写しの（snapshot_id, row_order）**（折れ線の点の順。RULE-PRG-005。重複すると線の順が決まらない）
  - 値の検査（CHECK）：報告日の状態は OPEN/FINALIZED、**FINALIZED なら確定の利用者・時刻が両方あり、OPEN なら両方空**（5.10 の確定は戻さない）。
    進捗率は 0〜100（PRG-02）、**実績開始日より前の実績終了日を拒む**（PRG-02。両方あるとき）、残工数・実績工数・ETC は 0 以上、
    項目の種類は 4.1 の 4 つ、**重みは 0 より大きい**（重みの総和を 0 にしない。BR-06 の重み付きマイルストーン）、完了していない項目は完了の時刻を持たない、
    計画の基準は LATEST だけ（OQ-03）、許容幅・抽出の閾値は 0 以上、写しの期間は 0 以上。
    **実績終了日があれば進捗率 100（BR-06）は DB では見ない**（方式によって入力値と算定値の扱いが分かれるため RULE-PRG-001 とサービス層の担当）
  - 型：進捗率は `Numeric(5,2)`（項目を数える方式の割合を 0.01% まで持つ）。報告日線 `report_line` は整数（5.4 の `line(r)` は稼働日の数）。
    ポイント・ずれは `Numeric(12,4)`（期間（整数）× 進捗率（0.01% 刻み）の積が小数 4 桁に収まる。5.6 の 5.25・12.6・−4.75 を丸めずに持つ）。
    許容幅・抽出の閾値は `Numeric(6,2)`（`projects.settings` の 0.5・2 の写し）。重みは `Numeric(10,2)`。遅延理由は長さの定めが無いので `Text`。完了の時刻 `done_at` は列名の `_at` に合わせて UTC の時刻
  - **判定の値の名前**（設計書に無い。OQ-23 (1)）：`COMPLETED`（完了）・`NOT_STARTED`（未着手（予定内））・`BEHIND`（遅れ）・`AHEAD`（前倒し）・`ON_SCHEDULE`（予定どおり）・`NOT_COMPUTED`（未算定）。
    `app.models.prg.JUDGEMENTS` に置いた。**rule:PRG（`lightning.py`）は同じ 6 つの名前を使い、試験で `JUDGEMENTS` と突き合わせる**（ドメインはモデルを import できないので、突き合わせは試験で行う）
  - 既定値：報告日の `status`（OPEN）、項目の `weight`（1）・`done`・`approved`（偽）、計画の基準（LATEST）、写しの `is_milestone`・`is_critical`（偽）をモデルと DB の両方に置いた。時刻はモデルの既定値 `utc_now`
  - **別の群の口を差し替えた**（A-21 の申し送り）：ガントが読む最新の進捗 `app.state.schedule_links.latest_progress` を、`no_progress` から表を読む
    `progress_entries_latest`（`app/services/schedule.py`）へ。4.2 どおり、OPEN の最新の報告日、無ければ FINALIZED の最新の報告日を 1 つ選び、**その報告日の行だけ**を返す
    （その報告日に行の無い活動は進捗なし。前の報告日の値を引き継ぐかは OQ-23 (2)）
- **理由:** 設計書 1.3・2.4・3 章・4.1・4.2・5.4・5.6・5.10・6.2、要求仕様書 PRG-01〜03・05・17・18、EFF-06、BR-06・08・09・10・13、OQ-03。A-19・A-21 の申し送り
- **影響範囲:** `backend/app/models/prg.py`・`backend/app/models/__init__.py`・`backend/alembic/versions/20261111_0005_prg_tables.py`・`backend/app/services/schedule.py`・`backend/app/main.py`・
  `backend/tests/models/test_models_prg.py`・`backend/tests/services/test_schedule_links.py`
- **覆す場合:** 判定の名前を変えるなら `JUDGEMENTS` と検査を直す移行を足し、試験 `test_progress_snapshot_items_accept_six_judgements` ほかの値を直す。
  写した活動を消せなくするなら写しの `activity_id` を必須にする移行を足し、試験 `test_progress_snapshot_items_survive_deleted_activity` を「消せない」に直す。
  重み 0 を許すなら検査を `weight >= 0` にする移行を足し、試験 `test_progress_items_rejections` の zero-weight を直す（RULE-PRG-001 は総和 0 を未算定にする）。
  最新の進捗を活動ごとに引き継ぐなら `progress_entries_latest` と試験 `backend/tests/services/test_schedule_links.py` を直す
- **限界:** 移行は SQLite でだけ流した（upgrade・downgrade・upgrade）。PostgreSQL では流していない（真偽の列の検査の式 `done OR done_at IS NULL` は両方で通る形にした）。
  入力・写しの活動が報告日と同じプロジェクトであること、確定した報告日への入力を拒むこと（BR-13・RULE-PRG-007）、算定項目の種類が活動の算定方式に合うことは DB では見ていない（ドメイン・サービス層の担当）。
  変更履歴（`audit_logs`）への記録は CHG のスコープ（OQ-08）で、この群の書き込みも記録していない

## A-23: 進捗（PRG）の計算・判定ルール（rule:PRG）で決めたこと

- **選択:**
  - 置き場は 5.1 どおり `backend/app/domain/progress.py`（RULE-PRG-001）・`lightning.py`（RULE-PRG-002〜005）・`delay.py`（RULE-PRG-006）・`snapshot.py`（RULE-PRG-007）。
    試験は `backend/tests/domain/test_progress.py`・`test_lightning.py`・`test_delay.py`・`test_snapshot.py`。方式・項目の種類・判定・報告日の状態・計画の基準の名前は
    `app.models.sch.PROGRESS_METHODS`・`app.models.prg` の値の一覧と試験で突き合わせる（A-22 の申し送り）
  - 進捗率（RULE-PRG-001）：`Decimal` の 0〜100。項目を数える方式の割合は 4.1 の `Numeric(5,2)` の桁へ**四捨五入**する（写した進捗率から同じポイントを再現するため）。
    重みの総和 0 は割れないので未算定（A-22 と同じ）、負の重みは拒む。直接入力の値は `DIRECT` だけが使う。
    **実績開始日の無い実績終了日は拒まない**（マイルストーンは実績終了日だけを持つ。RULE-SCH-005 の実績のバーと同じ扱い）。実績開始日より前の実績終了日は拒む（PRG-02）
  - 判定（RULE-PRG-004）：**未算定（進捗率 `None`）を最初に見る**（マイルストーン・報告日より後に開始予定の活動でも、未算定なら `NOT_COMPUTED`。OQ-24 (2)）。
    次に BR-08 の 2 つ（RULE-PRG-003）、残りをマイルストーンは `M(m)`、活動は `S＋d×進捗率` で置く。報告日線上に置いた活動（完了・未着手（予定内）・未算定）の
    ずれは 0（`P＝L`。4.1 の `deviation` は必須）。許容幅の境界（`δ＝±許容幅`）は予定どおり（BR-09 の不等号どおり）
  - イナズマ線（RULE-PRG-005）：頂点は渡された行の順（サービス層が RULE-SCH-005 の順で渡す）。暦日上の座標は区間の終わりの変換（`end_coordinate`。整数の位置は前の稼働日の終業）で、
    5.4 の表示の日付と同じ日になる。過去の線は写しの `row_order`・`point`・`deviation`・`judgement` だけから作る（`restore_lightning_line`）。
    重ねるとき（PRG-18）は報告日の古い順に線の種類 `style_index` を 0 から振り、頂点を今のガントの行へ置き直す。今のガントに無い活動の頂点は外し、その ID を返す
  - 遅延（RULE-PRG-006）：抽出は**ずれで判定した活動（遅れ・予定どおり・前倒し）だけ**（報告日線上に置いた活動は、閾値 0 でも抽出しない）。同じずれは活動の id の昇順。
    完了見込み日が非稼働日なら**終了日の寄せ（BR-04。前の稼働日）をしてから** `index＋1`（稼働日なら 5.7 の式と同じ。OQ-24 (4)）。
    見込みは **EF の下限**として当てる（`EF' = max(ES' + d, 見込み)`）。個別の影響では 5.7 の 3 の `max(EF, 見込み)` と一致し、全体の影響では、抽出した後続が
    抽出した先行の遅れも受ける（OQ-24 (3)）。変わった後続・マイルストーン・推移的な後続はトポロジカル順。クリティカルは計画の後退計算（RULE-SCH-003）の値
  - 確定（RULE-PRG-007）：`OPEN` 以外の確定と確定後の入力（`ensure_report_open`）は `INVALID_STATE`（409。5.10 の「戻さない」）、写しの入力の誤り（未定義の状態・
    計画の基準が LATEST 以外・負の閾値・重複した活動）は `VALIDATION_ERROR`。ポイント・ずれ・判定と報告日線は RULE-PRG-005 と同じ計算で求めて写す。
    実績工数・ETC はサービス層が求めて渡す（RULE-EFF-001・db:EFF の担当。無ければ空）
- **理由:** 設計書 1.3・4.1・4.2・5.1・5.4〜5.7・5.10・7.3、要求仕様書 7.6 BR-06・7.7 BR-07・7.8 BR-08・7.9 BR-09・7.13 BR-13、PRG-01〜03・05・08〜13・17〜19。A-22 の申し送り
- **影響範囲:** `backend/app/domain/progress.py`・`lightning.py`・`delay.py`・`snapshot.py`、`backend/tests/domain/test_progress.py`・`test_lightning.py`・`test_delay.py`・`test_snapshot.py`
- **覆す場合:** 進捗率を丸めずに持つなら `progress._quantize` を外し、試験 `test_check_item_ratio`・`test_weighted_milestone_uses_weights` の値を直す。
  未算定より BR-08 の 2 を先に見るなら `judge_progress` の順を入れ替え、試験 `test_not_started_rule_needs_all_three_conditions` の未算定の行を直す。
  見込みを文字どおり「固定」にするなら `delay._propagate` で見込みの活動の EF を `max(計画の EF, 見込み)` に置き、試験 `test_combined_successor_also_waits_for_delayed_predecessor` を直す。
  完了見込み日を寄せないなら `forecast_end` の `roll_backward` を外し、試験 `test_forecast_on_non_working_day_rolls_back` を直す
- **限界:** サービス・API（api:PRG）からはまだ呼んでいない。過去の線の暦日上の座標は、写しの位置を**今の**カレンダーとプロジェクトの開始日で直す（OQ-24 (1)）。
  プロジェクト全体の進捗率（7.4 の KPI）・ETC（RULE-EFF-001）はこの群に入れていない

## A-24: RUN を資源の上限の中で起動する（2026-11-11 に VM が落ちた後の対策）

- **事実（2026-11-11）:** 13:10:33 JST に WSL の VM が外から止まった（前回の起動のジャーナルが、停止の手順なしで途切れている。
  Linux 側に OOM・ハングの記録は無い）。そのとき重なっていたもの：
  - RUN（run_20261110T094851_c358e7 の impl-rule-prg、反復 1）。エージェントが `verify.sh rule:PRG` を裏で流しながら、別の pytest も流していた
  - ksd-ls のセッションの試験の計画の実行（pytest `-n 4`）。33 分以上走っていた

  VM は 8 コア・16GB で、`.wslconfig` にはメモリ・CPU の上限も、メモリを Windows へ返す設定も無い。sysstat は無効なので、落ちる前の負荷の推移は残っていない
- **実測（2026-11-11。ほかに何も走らせずに測った）:** `pytest backend/tests` の 1100 件は、1 コア・最大 RSS 216MB・2 分 8 秒で、約 1.1GB を書き込む（試験ごとの SQLite）。
  RUN の試験 1 本はメモリの主因ではない
- **推定（確かめていない）:** 原因は RUN 単体ではない。同時に走った試験（ksd-ls の 4 並列と RUN の 2 本）と claude 群の CPU・メモリの合計、
  それに 1 日走らせた間に溜まったページキャッシュを、WSL が Windows へ返さないことが重なった
- **選択:**
  - RUN は `scripts/run_capped.py` で起動する。systemd のユーザの scope（cgroup v2）で包み、MemoryHigh を MemTotal の 40%・MemoryMax を 50%・
    MemorySwapMax を 1G・CPUQuota をコア数の半分・TasksMax を 2048 にする。そのうえで nice 10・ionice best-effort 7 にする。
    上限を掛けられないときは起動しない（上限なしで走らせるのは `--allow-uncapped` を明示したときだけ）
  - 負荷を `.kloop/load-monitor/<name>-<時刻>.tsv` へ 15 秒ごとに書く（行ごとに fsync）。VM 全体の空きメモリ・PSI・scope のメモリと CPU・常駐の大きいプロセスを記録する
  - 歯止めを置く。VM 全体の空きメモリが 10% を 1 分割り続けたら kloop へ SIGINT を送る（kloop は走っているノードの終わりを待ち、CANCELLED で止まる）。
    5% を 1 分割り続けたら scope の全プロセスへ SIGTERM を送り、30 秒後に残っていれば SIGKILL を送る
  - `nohup … &` で起動すると、SIGINT が無視されたまま始まって kloop を穏当に止められない。これを、起動の直前に SIGINT を既定へ戻すことで解く
  - エージェントには `prompts/implement.md` で次を指示する：試験は一度に 1 本・前景・最も狭いものを流す。並列化と常駐するものを足さない
  - vitest のワーカーを 2 つまでにする（`frontend/vite.config.ts`。既定は CPU 数 − 1 の 7）。UI の 9 スコープがこれから毎回 vitest を流すため
  - kloop.toml とグラフ定義は変えない。変えると run_20261110T094851_c358e7 を再開できない
    （config_hash とグラフの hash を 2026-11-11 に今の kloop 11.11.0 で計算し、run の値と一致することを確かめた）
- **起動の口（A-06 の口を置き換える）:**
  - 再開：`! cd ~/ksd-project && nohup python3 scripts/run_capped.py --name implement -- kloop resume --run-id run_20261110T094851_c358e7 --graph-file graphs/implement.toml > .kloop/kick.log 2>&1 &`
  - 新しい run：`--` の後を `kloop graph graphs/implement.toml` にする
  - 止める：`! cd ~/ksd-project && python3 scripts/run_capped.py --stop implement`（もう一度打つと SIGTERM）
- **覆す場合:** 上限は引数で変える（`--mem-max 60%`・`--cpus 6` など）。歯止めは `--no-brake` で外す
- **限界:**
  - サンドボックスの中からは systemd のユーザのバスに繋がらない。上限を掛ける経路（systemd-run）は利用者の端末で確かめた
    （2026-11-11 の `--name probe`：`app.slice/ksd-probe-….scope` の中で走り、memory.max 8269070336・cpu.max `400000 100000`・pids.max 2048・nice 10）。
    非常停止と `--stop` の段（SIGINT → SIGTERM → SIGKILL）は、サンドボックスの中で上限なし（`--allow-uncapped`）でだけ確かめた
  - ほかのセッション（ksd-ls の試験）には上限が掛からない。同じスクリプトで包むか、RUN と重ねないこと
  - Windows 側（`.wslconfig`）はこのリポジトリの外にある

## A-25: 共通の画面（ui:CMN）の UI-CMN-001・002・003 で決めたこと

- **選択:**
  - **ログインのトークンは sessionStorage に置く**（`frontend/src/auth/session.tsx`。タブを閉じると忘れる）。読み出すときに `expires_at`（API-CMN-001 の応答）を過ぎたものは捨てる。
    トークンを付けた要求が 401 になったら（失効・期限切れ）、この端末のログインを忘れてログインの画面へ戻す（`setUnauthorizedHandler`。トークンを付けない 401＝ログインの失敗では戻さない）。
    6.1 は「Bearer トークン・有効期間 12 時間」だけを定め、画面での保持の仕方は定めていない。localStorage（ブラウザを閉じても残る）にしなかったのは、共有の端末で残さないため
  - ログインが要る画面（UI-CMN-002・003）は、ログインしていなければ `/login` へ送り、ログインの後に元の画面へ戻す。元の画面が無ければ 7.2 の遷移どおり `/projects`（UI-PRJ-001）へ進む。
    **UI-PRJ-001 は ui:PRJ のスコープなので、それまでは `/projects` が「ページが見つかりません」になる**
  - ログアウト（API-CMN-002）はサーバでの失効に失敗しても（接続できない・既に失効している）、この端末のログインは忘れる
  - 日時の表示は端末の時刻帯で `YYYY-MM-DD HH:mm`（NFR-09 の日付の形に時刻を足した）。サーバは UTC で記録する（`app/services/clock.py`）ので、時差の指定が無い値は UTC とみなす（`frontend/src/lib/format.ts`）
  - ユーザー管理（UI-CMN-002）：一覧は user.admin か member.manage で見られる（API-CMN-003 の権限）が、作成・変更・無効化の部品は user.admin（システム管理者）にだけ出す（7.2）。
    変更は変えた項目だけを送る（API-CMN-005）。メール・所属を空欄にしたら null、パスワードは入れたときだけ送る。ヘッダのユーザー管理への行き先はシステム管理者にだけ出す（主な利用者がシステム管理者のため）
  - 承認待ち一覧（UI-CMN-003）：判断の部品は、申請の対象ごとの権限（WBS は wbs.approve、変更要求は change.approve。システム管理者は全て）を `GET /api/v1/me` で見て出す。
    差戻しのボタンの呼び名は WBS を「差戻し」（WBS-21）、変更要求を「却下」（CHG-03）にした。送る値はどちらも `REJECT`（API-WBS-019）。
    判断の後と判断の失敗（403 自分の申請・409 判断済み）の後は一覧を読み直す。対象の画面への行き先は UI-WBS-003（`/projects/:projectId/wbs-review`）と
    UI-CHG-001（`/projects/:projectId/change-requests`）。**どちらも別のスコープの画面なので、それまでは「ページが見つかりません」になる**
- **覆す場合:** 保持の場所は `SessionProvider` の `storage` で差し替えられる。ログインの後の行き先は `frontend/src/pages/cmn/LoginPage.tsx` の `AFTER_LOGIN_PATH`
- **限界:** 試験は fetch を差し替えた画面の試験だけで、実際のサーバ（uvicorn）と繋いでは確かめていない。応答の形は `backend/app/schemas` を読んで合わせた

## A-26: 進捗（PRG）の API（api:PRG）で決めたこと

- **選択:**
  - ルータは `backend/app/api/progress.py`（2.1 の群のファイル名）、ユースケースは `backend/app/services/progress.py`、入出力は `backend/app/schemas/progress.py`、試験は `backend/tests/api/test_prg.py`。
    報告日を作る・確定するは status_report.manage、算定方式は schedule.edit、読むのは project.view（`authorize_project`）。
    **進捗・算定項目を書くのは progress.enter_own**：先に活動を問わない粗い確認（enter_own か enter_all を持つか。無ければ 403。活動の有無を見せない）、
    次に報告日・活動の 404、最後に RULE-AUTH-001 の `authorize(..., activity_id=…)` で自分の割当か（enter_all を持てば割当に関わらず許す）。委託先だけで読む利用者は自分の割当の活動に絞る（`restricted`）
  - **進捗率は読むたびに RULE-PRG-001 で求める**（効いている方式・算定項目・その報告日の入力）。`progress_entries.percent_complete` には書いた時点の算定値を保存する
    （直接入力は入力値。実績終了日があれば 100）。算定項目（API-PRG-004）・方式（API-PRG-005）を変えたら **OPEN の報告日の行だけ**求め直す（ガントが読む 4.2 の「最新の値」と食い違わせない。確定済みは変えない）。
    直接入力へ戻したときは、それまでの算定値を入力値として引き継ぐ。**直接入力以外の方式の活動へ進捗率を入れたら 400**（OQ-25 (3)）
  - 報告日を作る（API-PRG-001）：同じ日付は 409 `DUPLICATE_STATUS_REPORT`。**この日付より前の最も新しい報告日から、進捗率・実績開始日・実績終了日だけを写す**（PRG-01 の入力の枠。OQ-25 (1)）。
    残工数・遅延理由・完了見込みはその報告日の時点の評価なので写さない（残工数は自動に戻る）
  - 進捗の入力（API-PRG-003）は PUT＝全ての項目の置き換え。確かめる順はプロジェクト 404 → 権限 403 → 報告日・活動 404 → 自分の割当 403 → 確定済み 409 `INVALID_STATE`（BR-13）→ 入力 400
  - 算定項目（API-PRG-004）：`id` を載せた項目は変え、載せない項目は足し、載せなかった既存の項目は消す。完了の時刻は完了になったときに「今」、完了のままなら保つ。
    作業項目の「承認済み」を誰が付けるかは定めが無いので、進捗を書く権限で付けられる
  - イナズマ線（API-PRG-006）：**OPEN は今の計画（`activities` の日程の列。OQ-03）とその報告日の入力だけから計算（CALCULATED）、FINALIZED は写しだけから（SNAPSHOT。BR-13）**。
    行にはその報告日の入力（残工数・遅延理由・完了見込み）と効いている方式を載せた（UI-PRG-001 が今の入力を読む API が 6.2 に無いため。OQ-25 (2)）。SNAPSHOT の方式は写しに無いので null。
    日程の計算結果の無い活動があれば 409（日程を計算し直す）
  - 確定（API-PRG-007）：RULE-PRG-007 で全ての活動をガントの行の順に写す。許容幅・抽出の閾値は今の `projects.settings`（欠けていれば 4.2 の既定）。
    **実績工数・ETC は口 `app.state.progress_links.effort`（`SnapshotEffort`）から読む**。既定 `EffortTablesAbsent` は実績工数を空、ETC を入力した残工数だけにする
    （自動の ETC は RULE-EFF-001 で、まだ無い。値を作らない）。**db:EFF・rule:EFF・api:EFF は表と RULE-EFF-001 を使う実装へ差し替える**。
    差し替え忘れは `backend/tests/api/test_prg.py::test_effort_port_is_wired_once_time_entries_table_exists` が落とす
  - 重ねる線（API-PRG-008）：`report_ids` は繰り返しのクエリ。空・重複は 400、確定していない報告日は 409 `INVALID_STATE`（PRG-18 は確定済みを選ぶ）
  - 遅延（API-PRG-009）：ずれ・判定・進捗率は API-PRG-006 と同じ元（FINALIZED は写し。今の計画に無い活動は除く）、影響は今の計画で求める（OQ-25 (4)）。
    抽出の閾値は OPEN なら今の設定、FINALIZED なら写した値。委託先には自分の割当の遅延活動だけ、影響の一覧も自分の割当の活動に絞り、プロジェクトの終了のずれは返さない
  - **活動を消す（API-SCH-005。`app/services/schedule.py` の `remove_activity`）は、算定項目と全ての報告日の入力を消し、写しは紐付けだけ空にする**（A-22 の申し送り。OQ-23 (3)・OQ-25 (5)）
- **理由:** 設計書 1.3・1.4・2.1・4.1・4.2・5.4・5.6・5.7・5.10・6.1・6.2・9 章、要求仕様書 PRG-01〜03・05・08〜13・17〜19、EFF-06、BR-06・09・13。A-21・A-22・A-23 の申し送り
- **影響範囲:** `backend/app/api/progress.py`・`backend/app/services/progress.py`・`backend/app/schemas/progress.py`・`backend/app/main.py`・`backend/app/services/schedule.py`・`backend/tests/api/test_prg.py`
- **覆す場合:** 報告日を作るときに写さないなら `create_status_report` の写す部分を消し、試験 `test_new_report_carries_forward_actuals_but_not_assessments` を直す。
  直接入力以外の進捗率を黙って無視するなら `put_progress` の 400 を外し、試験 `test_put_progress_validates_and_derives_percent` を直す。
  確定済みの入力を残して活動を消せなくするなら `remove_activity` で確定済みの報告日の入力があれば 409 にする
- **限界:** 割当の表（DB-EFF-001）が無いので、自分の割当の判定は試験の中の代わり（`FakeAssigned`）でだけ確かめた。実績工数・自動の ETC は写していない（口の既定）。
  変更履歴（6.1）は記録していない（OQ-08）。同時に 2 人が同じ日付の報告日を作ったとき（一意制約で後の方が 500）・確定と入力が重なったときは扱っていない。PostgreSQL では流していない

## A-27: 工数（EFF）のテーブル（db:EFF）で決めたこと

- **選択:**
  - 置き場は `backend/app/models/eff.py`（DB-EFF-001〜003）、移行は `0006_eff`
  - 外部キー：4.1 の `*_id`・`created_by` に張った。`ON DELETE` は付けない（A-11・A-15・A-19・A-22 と同じ）。割当・実績工数のある活動、差異の理由のある WBS要素・報告日は DB が消させない。
    **`variance_notes.corrective_action_id` は外部キーをまだ張らない**（`scope_baselines.change_request_id` と同じ形。A-15・A-19）。**db:CHG のスコープは `corrective_actions` を作る移行で、この列にも外部キーを張る**。
    張り忘れると `backend/tests/models/test_models_eff.py::test_variance_notes_corrective_action_id_references_corrective_actions_once_it_exists` が落ちる
  - 必須の列：割当の `activity_id`・`user_id`、実績工数の `user_id`・`activity_id`・`work_date`・`hours`、差異の理由の `project_id`・`element_id`・`reason`・`created_by`・`created_at`。
    任意：割当工数（NULL は「決めていない」。API-SCH-004 の担当の置き換えは割当工数を書かない）、備考、差異の理由の報告日（報告日を指定しない分析。5.8。OQ-26 (1)）・是正措置
  - 一意制約は 4.1 に書かれた割当の（activity_id, user_id）と実績工数の（user_id, activity_id, work_date）だけ。**差異の理由には一意を置かない**（4.1 に定めが無い。上書きか追記かは API-EFF-007。OQ-26 (1)）
  - 値の検査（CHECK）：割当工数は 0 以上、**実績工数は 0 以上 24 以下**（RULE-EFF-005 の「負の値の禁止」と、EFF-02 の「1 日の合計 24 時間以下」の 1 行での必要条件。日の合計は行をまたぐのでドメインが見る）。
    工数は人時の `Numeric(10,2)`（2.4）、備考・理由は長さの定めが無いので `Text`、作成の時刻はモデルの既定値 `utc_now`
  - **別の群の口を差し替えた**（A-11・A-18・A-21・A-26 の申し送り）：
    - 委託先の絞り込み・自分の割当の判定 `app.state.project_access.assignments` を `AssignmentsTable`（`app/services/project_access.py`）へ
    - スケジュールの担当の読み書き `app.state.schedule_links.assignments` を `AssignmentsStore`（`app/services/schedule.py`）へ。置き換えは外れた担当の行を消し、新しい担当を割当工数なしで足す（残る担当の割当工数は変えない）。活動を消すときは割当の行を消す
    - 責任分担表・横断照会の `app.state.wbs_readers` の `assignments` を同じ `AssignmentsStore.assignments`、`time_entries` を `time_entries_table`（`app/services/wbs.py`）へ
    - 確定の実績工数・ETC `app.state.progress_links.effort` を `TimeEntriesEffort`（`app/services/progress.py`）へ。**実績工数は報告日以前の `time_entries` の合計（5.8）、行が無ければ 0**。
      **ETC はまだ入力した残工数だけ**（自動の ETC は RULE-EFF-001 で、rule:EFF のスコープ。値を作らない）。**RULE-EFF-001（`app/domain/effort.py`）を作るスコープは ETC を計算する実装へ差し替える**。
      差し替え忘れは新しく足した `backend/tests/api/test_prg.py::test_effort_port_computes_etc_once_effort_rules_exist` が落とす
  - 既定の口が変わったので、既存の試験を 2 つ直した（判定を弱めない向き）：`test_sch.py::test_bulk_update_assignees` は表の無い口 `AssignmentsTableAbsent` を明示して載せてから同じ主張（担当を置けば止まる）を確かめ、
    表を読み書きする既定の口は新しい `test_default_assignments_port_reads_and_writes_assignments_table` で確かめる。`test_prg.py::test_finalize_freezes_snapshot_and_rejects_later_input` は
    「実績工数は空」を「報告日以前の実績工数の合計（翌日の行は入れない）、実績の無い活動は 0」に置き換えた
- **理由:** 設計書 2.4・3 章・4.1・4.2・5.8・6.2・9.2、要求仕様書 EFF-01・02・06・10、WBS-10、BR-11・13。A-11・A-15・A-18・A-19・A-21・A-22・A-26 の申し送り
- **影響範囲:** `backend/app/models/eff.py`・`backend/app/models/__init__.py`・`backend/alembic/versions/20261111_0006_eff_tables.py`・`backend/app/services/project_access.py`・`backend/app/services/schedule.py`・
  `backend/app/services/wbs.py`・`backend/app/services/progress.py`・`backend/app/main.py`・`backend/tests/models/test_models_eff.py`・`backend/tests/services/test_effort_tables.py`・`backend/tests/api/test_sch.py`・`backend/tests/api/test_prg.py`
- **覆す場合:** 差異の理由を要素・報告日ごとに 1 行にするなら一意制約を足す移行を足し、試験 `test_variance_notes_hold_several_notes_per_element` を直す。
  1 行 24 時間の検査を外す（日の合計だけを見る）なら検査を外す移行を足し、試験 `test_time_entries_rejections` の over-24 を直す。
  実績の無い活動の実績工数を空にするなら `TimeEntriesEffort` と試験 `test_time_entries_effort_sums_up_to_report_date` を直す
- **限界:** 移行は SQLite でだけ流した（upgrade・downgrade・upgrade）。PostgreSQL では流していない。
  割当の利用者がそのプロジェクトのメンバーであること、実績工数の 1 日の合計・自分の割当の活動だけ・確定した報告日より前の日（RULE-EFF-005）は DB では見ていない（rule:EFF・api:EFF の担当）。
  **実績工数のある活動を消す（API-SCH-005）と、外部キーで止まる（500）**。今は実績工数を書く API が無いので起きない。扱い方は OQ-26 (2)（api:EFF か api:SCH が決める）。
  変更履歴（`audit_logs`）への記録は CHG のスコープ（OQ-08）で、この群の書き込みも記録していない

## A-28: 工数（EFF）の計算・判定ルール（rule:EFF）で決めたこと

- **選択:**
  - 置き場は 5.1 どおり `backend/app/domain/effort.py`（RULE-EFF-001〜005）、試験は `backend/tests/domain/test_effort.py`。工数は人時の `Decimal`（`float`・`bool` は拒む。A-13 の見積と同じ）
  - ETC（RULE-EFF-001。`compute_etc`）：**完了（進捗率 100）を最初に見て 0**、次に入力した残工数、次に 見積×（1−進捗率）、進捗率が未算定なら max(見積−実績, 0)。
    完了は進捗率 100 で見る（RULE-PRG-001 は実績終了日があれば 100 を返す）。未見積は 0（RULE-EST-001 と同じ）。
    式の ETC は小数 2 桁へ四捨五入（ROUND_HALF_UP）。出どころ（入力・進捗率・実績・完了・活動の無い WP）を `EtcSource` で返す（EFF-06 の「自動か入力か」）。OQ-27 (1)
  - EAC と集計（RULE-EFF-002。`rollup_effort`）：木の検査と計画の値は RULE-EST-001（`rollup_estimates`）をそのまま使う。
    **基準の値は集計の葉（活動と、活動の無い WP）で持ち**、上位は基準にある下位だけを足す（1 つも無ければ `None`＝基準に無い）。
    どの軸でも合計を一致させる（EFF-09）ためで、基準の後に足した・消した活動の扱いは OQ-27 (5)。基準にあって見積が空の項目は呼び手が 0 にして渡す
  - 多軸集計（RULE-EFF-003。`aggregate_effort`）：5.8 の表どおり。比で分けた値・稼働日に配った値は**小数 2 桁で最大剰余法**で配り、桶の合計を崩さない（同じ剰余は先の桶）。
    割当工数が全て空か 0 なら均等。組織は `project_members.organization` を呼び手が渡し、所属が無い・メンバーでない入力者は「所属なし」、割当の無い値は「未割当」の桶。
    週は月曜から。日付の無い値は「日付なし」、ETC は「残り」の桶。実績工数の行の合計が RULE-EFF-002 の実績と違えば `EffortError`（軸ごとの合計の食い違いを防ぐ）。OQ-27 (2)〜(4)
  - 差異（RULE-EFF-004。`judge_variance`・`extract_variances`）：「超えた」は不等号（閾値ちょうどは抽出しない）。増えた向き・減った向きの両方。
    比べ方は |差異| ＞ 差異率の閾値×基準（割り算の丸めを持ち込まない）。基準 0 は差異率 `None`、差異が 0 でなければ差異率の閾値を超えたとみなす。抽出は入力の順。OQ-27 (6)
  - 実績工数の入力検査（RULE-EFF-005。`check_time_entries`）：**全てのマスを検査して問題を全て返す**（拒むのは呼び手。RULE-SCH-006 と同じ「1 件でも不正なら全体を拒む」の形を API が取れるように）。
    1 日の合計は呼び手が渡したその利用者の今の行（プロジェクトをまたぐ）に書いた後の値を重ねて数え、ちょうど 24 は通す。
    「確定した報告日より前の日」は字義どおり報告日ちょうどを含まない。**今の値と同じ値のマス（行の無いマスは 0）は変えていないとみなし、割当・確定の検査を当てない**。値は 0 以上・小数 2 桁まで。OQ-27 (7)(8)
  - **確定の ETC の口を差し替えた**（A-26・A-27 の申し送り）：`app.state.progress_links.effort` を `ComputedEtcEffort`（`app/services/progress.py`）へ。
    実績工数は `TimeEntriesEffort` と同じく表から、ETC は RULE-EFF-001 で求める。進捗率は確定が RULE-PRG-001 で求めた値を渡す（口に省ける引数 `percents` を足した。
    既存の 2 つの口も受けて無視する）。これに合わせて `test_prg.py::test_finalize_freezes_snapshot_and_rejects_later_input` のマイルストーンの ETC を `None` から計算値 0 に直し、
    `test_effort_port_computes_etc_once_effort_rules_exist` に `ComputedEtcEffort` であることの主張を足した（どちらも判定を弱めない向き）
- **理由:** 設計書 1.3・2.4・4.1・4.2・5.1・5.8、要求仕様書 7.10 BR-10・7.11 BR-11、EFF-01・02・06〜10。A-23・A-26・A-27 の申し送り
- **影響範囲:** `backend/app/domain/effort.py`・`backend/tests/domain/test_effort.py`・`backend/app/services/progress.py`・`backend/app/main.py`・
  `backend/tests/services/test_effort_tables.py`・`backend/tests/api/test_prg.py`
- **覆す場合:** 入力値を完了より優先するなら `compute_etc` の完了と入力の順を入れ替え、試験 `test_completed_activity_etc_is_zero` を直す。
  報告日ちょうどの実績も固めるなら `check_time_entries` の `<` を `<=` にし、試験 `test_days_before_finalized_report_are_locked` を直す。
  割当工数の無い活動を「未割当」にするなら `aggregate_effort` の均等の分岐を外し、試験 `test_every_axis_sums_to_project_total`・`test_assignee_axis_splits_by_assigned_hours` を直す
- **限界:** API（api:EFF。API-EFF-003〜006）からはまだ呼んでいない（確定の ETC だけが呼ぶ）。権限（effort.enter_own の自分の割当）は RULE-AUTH-001 の担当で、ここは割当の活動の集合を受けるだけ。
  基準の版の選び方（既定は最新）・`projects.settings` の閾値の読み出し・組織の読み出しは呼び手（api:EFF）が行う。期間の軸の基準は今のカレンダーで配る（基準を取った時点の休日ではない）

## A-29: プロジェクト基盤の画面（ui:PRJ）の UI-PRJ-001・002 で決めたこと

- **選択:**
  - 置き場は `frontend/src/pages/prj/`。UI-PRJ-002 は 1 つの画面に 5 つの節（基本情報・スコープ・稼働カレンダー・メンバー・権限）を並べ、節ごとに `settings/` の部品に分けた。
    API の型と呼び出しは `frontend/src/api/prj.ts`（形は `backend/app/schemas/projects.py`・`auth.py` を読んで合わせた）
  - 出し分け（7.2）：見るのは project.view。変える部品は節ごとの権限を `GET /api/v1/me` で見て出す（基本情報・スコープ・カレンダーは project.manage、メンバーは member.manage、
    権限の表は permission.manage。システム管理者は全て）。権限が無ければ入力欄を無効にして値だけを見せ、保存のボタンを出さない。
    判定の部品 `hasProjectPermission`・`canCreateProject` を `api/cmn.ts` に置き、`canDecide`（UI-CMN-003）も同じ部品を通すようにした（振る舞いは同じ）
  - 一覧（UI-PRJ-001）の行き先は 7.2 の UI-PRJ-001 からの遷移（設定・WBS・タスクリスト・ガント・ベースライン・工数・変更要求・変更履歴・入出力）。
    工数は effort.view、変更履歴は audit.view、入出力は data.export か data.import を持つときだけ出す（行き先の画面の API の権限）。
    **設定以外の行き先は別のスコープの画面なので、それまでは「ページが見つかりません」になる**（A-25 と同じ）。ヘッダに一覧への行き先（「プロジェクト」）を足した
  - 作成（API-PRJ-001）の部品は project.create を持つ人にだけ出す。**責任者の候補はユーザーの一覧（API-CMN-003）の有効な人の先頭 1000 件**。
    API-CMN-003 は user.admin か member.manage が要る（9.1）ので、project.create だけを持つ人は自分しか選べない（403 は画面のエラーにしない）。
    作った人はメンバーにならない（OQ-15）ので、システム管理者でない人が作ったプロジェクトは一覧に出ないことがある
  - 基本情報の節は API-PRJ-004 の「設定」（`projects.settings`：算定方式の既定・遅れの許容幅・抽出の閾値・差異の閾値・100%ルールの許容幅）も変える。
    変えた項目だけを送り（settings も変えた項目だけ）、空欄にした任意の項目は null。割合の設定（`variance_rate`・`hundred_percent_tolerance`）は保存の値（0.1＝10%）のまま見せる
  - スコープの置き換え（API-PRJ-006）は載せなかった項目を消すので、**一覧を上限の 1000 件で読み、総数がそれを超えたら保存させない**（読めなかった項目を消さないため）。
    並びは行の順（上へ・下へで変える）。名称の無い行は送らずに止める
  - カレンダー（API-PRJ-008）は曜日・稼働時間・休日をまとめて送る。休日は日付の順に並べ、同じ日付は画面で止める（サーバも 400）。稼働日の無いカレンダーはサーバの 422 をそのまま出す
  - メンバー：足す候補は今の頁のメンバーを除いた有効なユーザー。外すときは画面の中で確かめてから送る（`window.confirm` を使わない）。
    メンバー・権限の表を変えた後は `/me` を読み直す（自分のロール・権限が変わると出し分けが変わる）
  - 権限の表（API-CMN-007）は載せなかったロールが何も持たなくなるので、**3 章の 7 つのロールを全て送る**。権限は 9.1 の順に並べ、画面の一覧に無い権限のコードがサーバの表にあれば保つ
  - 呼び名：ロールは要求仕様書 3 章、算定方式は 7.6 BR-06、権限は 9.1 の「内容」の欄。**WBS の状態（5.10）の呼び名は設計書に無いので決めた**
    （DRAFT＝作成中・IN_REVIEW＝レビュー中・PENDING_APPROVAL＝承認待ち・BASELINED＝基準化済み。`pages/prj/labels.ts`）
  - 400 の項目ごとの理由（details の message）も出す（`toErrorView`・`components/ErrorAlert.tsx`）。「入力に誤りがあります」だけではどこを直せばよいか分からないため。
    入力の検査（pydantic）の理由は英語のまま出る（画面の入力欄の制約で、ふつうは起きない）
- **理由:** 設計書 6.2（API-PRJ-001〜011・API-CMN-003・006〜008）・7.1・7.2・9.1・9.2、要求仕様書 PRJ-01・02・04・06・CMN-01、A-14・A-25・OQ-15
- **影響範囲:** `frontend/src/pages/prj/`・`frontend/src/api/prj.ts`・`frontend/src/api/cmn.ts`・`frontend/src/api/errors.ts`・`frontend/src/components/ErrorAlert.tsx`・
  `frontend/src/lib/form.ts`（`optionalText` を UI-CMN-002 と共有）・`frontend/src/App.tsx`・`frontend/src/test/fixtures.ts`
- **覆す場合:** 行き先の出し分けは `ProjectListPage.tsx` の `DESTINATIONS`、WBS の状態の呼び名は `labels.ts` の `WBS_STATUS_LABELS`、スコープの読み込みの上限は `ScopeSection.tsx` の `SCOPE_ITEMS_LIMIT`
- **限界:** 試験は fetch を差し替えた画面の試験だけで、実際のサーバ（uvicorn）と繋いでは確かめていない。見た目（CSS）は付けていない。
  メンバーの頁が 100 件を超えると、足す候補から他の頁のメンバーを除けない（選ぶと API-PRJ-010 がそのロール・所属を置き換える）

## A-30: 工数（EFF）の API（api:EFF）の API-EFF-001〜007 で決めたこと

- **選択:**
  - 置き場は `backend/app/api/effort.py`（ルート）・`backend/app/services/effort.py`（ユースケース）・`backend/app/schemas/effort.py`（入出力）、試験は `backend/tests/api/test_eff.py`。
    計算・判定は rule:EFF の `app/domain/effort.py`（RULE-EFF-001〜005）をそのまま呼ぶ（サービス層は DB の行をデータクラスへ直して渡すだけ）
  - 割当（API-EFF-001）：載せた担当を作る・割当工数を変え、載せなかった担当を外す（全件の置き換え）。担当はプロジェクトの有効なメンバーだけ（API-SCH-004 と同じ判定）。
    重複・メンバーでない・負・小数 3 桁以降は 400（1 件でもあれば何も変えない）。応答に割当工数の合計と「合計−見積」を付ける（EFF-01 の「見積と比べて表示する」。見積が空なら差は空）
  - 割当の一覧（API-EFF-002）：並びはガントの行の順→利用者の ID。委託先だけで読む利用者は自分の割当の活動の分だけ（9.2 の注記）
  - 実績工数を書く（API-EFF-003）：日×活動のマスの置き換え（0 は行を消す。メモだけの変更も「変える」とみなす。0 時間のマスのメモは 400）。プロジェクトをまたいでよい。
    活動のプロジェクトで effort.enter_own を持たなければ 403。RULE-EFF-005 の「自分の割当」「確定した報告日より前の日」はマスのプロジェクトごとに、
    「1 日 24 時間以下」はプロジェクトをまたいだ自分の全ての行で見る。問題は全て details に載せ、状態は 400＞403＞409（`INVALID_STATE`）＞422（`DAY_TOTAL_EXCEEDED`）の順で 1 つ選ぶ。OQ-28 (2)(5)
  - 実績工数を読む（API-EFF-004）：プロジェクトごとに effort.view があれば全ての利用者の行、effort.enter_own だけなら自分の行。他人を指定して effort.view が 1 つも無い・どちらの権限も無いは 403。
    `from`・`to` は作業日の範囲（両端を含む）。OQ-28 (1)
  - 集計（API-EFF-005）：5.8 どおり。報告日を指定しなければ 4.2 の「最新」の報告日の入力（進捗率は RULE-PRG-001 でイナズマ線と同じ値を求める。`app.services.progress.report_percents`）、
    実績は全件。指定すれば実績は報告日以前の分。基準は指定の版、無ければ最新の版、版が無ければ基準は全て空。基準の項目の見積の空は 0（A-28 の申し送り）。
    行は軸ごと：wbs は WBS の並びの全ての要素、member は利用者の ID の順と最後に「未割当」、organization は桶の種類→所属名、week・month は桶の順。OQ-28 (4)
  - 差異（API-EFF-006）：WBS の全ての要素を WBS の並びで RULE-EFF-004 に掛け、超えた要素と追加の要素だけを返す。閾値は `projects.settings`（4.2 の既定を重ねる）。
    スケジュールベースラインが 1 つも無ければ 409 `INVALID_STATE`。各行に同じ報告日（指定しなければ「報告日を指定しない分析」）の差異の理由を付ける。OQ-28 (3)
  - 差異の理由（API-EFF-007）：WBS要素・報告日（空も 1 つの鍵）ごとに 1 行を上書きする（PUT の冪等。`created_by`・`created_at` は最後に書いた人・時刻）。OQ-26 (1)
  - **是正措置を読む口 `EffortLinks.corrective_actions` を `app.state.effort_links` に載せた**。是正措置の表（DB-CHG-002）がまだ無いので既定は `CorrectiveActionsTableAbsent`（是正措置は無い＝紐付けは 404）。
    **db:CHG のスコープが表を読む実装へ差し替える。** 差し替え忘れは `test_eff.py::test_corrective_actions_port_is_wired_once_table_exists` が落とす
  - **横断照会（API-WBS-023）の EAC の口を差し替えた**（A-18 の申し送り）：`app.state.wbs_readers.eac_hours` を `app.services.effort.eac_by_element`（最新の報告日の入力・全ての実績での RULE-EFF-002）へ
  - **実績工数のある活動を消す（API-SCH-005）は 409 `INVALID_STATE`（details に `HAS_TIME_ENTRIES`）**。実績を書く API ができて、外部キーの 500 が起き得るようになったため（A-27 の限界・OQ-26 (2)）。
    実績を黙って消さない向き（人が決めるまで消さない）を取った。実績を 0 にすれば消せる
- **理由:** 設計書 1.3・2.4・4.1・4.2・5.8・6.1・6.2・9 章、要求仕様書 EFF-01・02・06〜10、WBS-10、BR-10・11・13。A-18・A-26・A-27・A-28 の申し送り
- **影響範囲:** `backend/app/api/effort.py`・`backend/app/services/effort.py`・`backend/app/schemas/effort.py`・`backend/app/main.py`・`backend/app/services/progress.py`（`report_percents`・`entries_of_report` を足した）・
  `backend/app/services/schedule.py`（`remove_activity` の 409）・`backend/tests/api/test_eff.py`
- **覆す場合:** 差異の理由を追記にするなら `put_variance_note` の上書きを外し、試験 `test_variance_extracts_elements_over_both_thresholds` の `again["id"] == note["id"]` を直す。
  実績工数のある活動を実績ごと消すなら `remove_activity` の 409 を `delete(TimeEntry)` に替え、試験 `test_activity_with_time_entries_is_not_deleted` を直す。
  基準が無いときに空の差異を返すなら `variance` の 409 を外し、試験の `no_baseline` を直す
- **限界:** 変更履歴（`audit_logs`）への記録は CHG のスコープ（OQ-08）で、この群の書き込みも記録していない。PostgreSQL では流していない。
  同時の書き込み（同じ利用者が同じ日に 2 つの要求で書く）で 1 日 24 時間の検査をすり抜ける競合は扱っていない（DB に日の合計の制約は無い）。
  集計・差異は要求のたびに全件を読んで計算する（キャッシュしない。性能は実データで見ていない）。画面（ui:EFF）はまだ無い

## A-31: 変更管理（CHG）のテーブル（db:CHG）で決めたこと

- **選択:**
  - 置き場は `backend/app/models/chg.py`（DB-CHG-001〜003）、移行は `0007_chg`
  - 外部キー：4.1 の `*_id`・`created_by`・`applied_by` に張った。`ON DELETE` は付けない（A-11・A-15・A-19・A-22・A-27 と同じ）。版を作った変更要求・差異の理由が指す是正措置は DB が消させない。
    **先に作った表の列へ外部キーを張った**（A-15・A-19・A-27 の申し送り）：`scope_baselines.change_request_id`・`schedule_baselines.change_request_id` → `change_requests.id`、
    `variance_notes.corrective_action_id` → `corrective_actions.id`（SQLite では表の作り直し）。`audit_logs.entity_id` は対象の種類で指す表が変わるので外部キーを持たない（対象が消えても履歴は残る）
  - 必須の列：変更要求の `project_id`・`number`・`title`（200 文字）・`reason`・`target_codes`・`operations`（既定は空の配列）・`status`・`created_by`、
    是正措置の `project_id`・`cause`・`action`・`status`、変更履歴の `occurred_at`・`entity_type`（64 文字）・`entity_id`・`action`。
    任意：影響分析（提出の前）・提出／反映の時刻と反映した人、是正措置の紐付け（要素・活動・報告日）・担当・期限・効果、変更履歴の変更者・プロジェクト・WBS ID・変更前後の値。OQ-29 (1)
  - 一意制約は 4.1 の「プロジェクト内の連番」から変更要求の（project_id, number）。値の検査（CHECK）：番号は 1 以上、変更要求の状態は 5.10 の 5 つ（起票は DRAFT）、
    是正措置の状態は 4.1 の 3 つ（登録は NOT_STARTED）、変更履歴の操作は 4.1 の 3 つ。遷移（RULE-CHG-003・005）は DB では見ない
  - 変更要求の `operations` は JSON の配列をそのまま持つ（5.9 の形の検査は RULE-CHG-001）。`target_codes` は起票した時点の WBS ID（改番の後は RULE-WBS-008 が新旧対応で解決する）
  - **変更履歴は追記だけ**（4.2・NFR-05）：ORM からの更新・削除を `before_update`・`before_delete` のイベントで止める（`AuditLogImmutableError`）。
    検索（API-CHG-010）のために、時刻とプロジェクト・変更者・対象（種類と ID）・WBS ID に索引を置いた。**記録する仕組み（infra の `before_flush`・RULE-CHG-006）はこのスコープでは作っていない**
  - **別の群の口を差し替えた**（A-18・A-30 の申し送り）：
    - 横断照会の `app.state.wbs_readers` の `change_requests` を `change_requests_table`、`corrective_actions` を `corrective_actions_table`（`app/services/wbs.py`）へ。プロジェクトの全件を ID の順に読む（状態で絞らない）
    - 差異の理由の紐付け `app.state.effort_links.corrective_actions` を `CorrectiveActionsTable`（`app/services/effort.py`）へ。別のプロジェクトの是正措置は「無い」（404）
    - 変更要求の申請への判断 `app.state.change_request_approvals` を `ChangeRequestTransitionNotWired`（`app/services/wbs.py`）へ。
      **状態遷移（RULE-CHG-003）は rule:CHG、変更要求へ通すのは api:CHG の担当で、このスコープには無い**（A-19 の `SchedulingNotWired` と同じ形）。
      申請の指す変更要求が無ければ食い違いとして、あっても状態を動かす実装が無いので止める（RuntimeError。変更要求の状態は変えない）。変更要求の申請は提出（API-CHG-005）でしか作られないので今は起きない。
      **api:CHG は API-CHG-005 と同時に RULE-CHG-003 を通す実装へ差し替える**。差し替え忘れは新しく足した
      `backend/tests/api/test_wbs.py::test_change_request_approvals_apply_transition_once_change_api_exists` が落とす
  - 試験：`backend/tests/models/test_models_chg.py`（列・一意・検査・外部キー・追記だけ・移行）、`backend/tests/services/test_change_tables.py`（読む口と止める口）、
    `backend/tests/api/test_eff.py::test_variance_note_links_corrective_action_of_same_project`（差異の理由が同じプロジェクトの是正措置にだけ紐付く）を足した。
    既存の `test_eff.py` の「表がまだ無いので 404」のコメントを「1 つも登録していないので 404」に直した（主張は変えていない）。
    既存の `test_models_sch.py::test_schedule_baselines_versions_increase_per_project` は実在しない変更要求 7 を指していたので、外部キーを張った後は実在する変更要求を作って指すように直した（主張は変えていない）
- **理由:** 設計書 2.4・3 章・4.1・4.2・5.9・5.10・6.2・8 章 NFR-05、要求仕様書 CHG-01〜04・06・09、EFF-10、BR-12。A-15・A-18・A-19・A-27・A-30 の申し送り
- **影響範囲:** `backend/app/models/chg.py`・`backend/app/models/__init__.py`・`backend/app/models/wbs.py`・`backend/app/models/sch.py`・`backend/app/models/eff.py`・
  `backend/alembic/versions/20261111_0007_chg_tables.py`・`backend/app/services/wbs.py`・`backend/app/services/effort.py`・`backend/app/main.py`・
  `backend/tests/models/test_models_chg.py`・`backend/tests/services/test_change_tables.py`・`backend/tests/api/test_wbs.py`・`backend/tests/api/test_eff.py`
- **覆す場合:** 是正措置の担当・期限を必須にするなら NOT NULL の移行を足し、試験 `test_corrective_actions_links_and_owner_are_optional` を直す。
  変更履歴の更新・削除を ORM で許す（保守の手段が要る）なら `_refuse_audit_log_change` のイベントを外し、試験 `test_audit_logs_are_append_only` を直す
- **限界:** 移行は SQLite でだけ流した（upgrade・downgrade・upgrade）。PostgreSQL では流していない。追記だけの歯止めは ORM の単位の操作だけで、
  `update(AuditLog)`・`delete(AuditLog)` の一括の文や SQL の直接の実行は止めない（DB の権限・トリガは R1 で扱わない）。
  ~~是正措置の紐付いた要素・活動を消す（API-WBS-005・API-SCH-005）と外部キーで止まる（500）~~ → A-47 で 422 `ELEMENT_REFERENCED`・`ACTIVITY_REFERENCED` にした（変更要求の反映と同じ）。
  変更履歴は**まだ 1 行も記録されない**（記録の仕組みは OQ-08・RULE-CHG-006 のスコープ）。変更要求・是正措置がプロジェクトの WBS・活動・報告日と同じプロジェクトを指すことは DB では見ていない（サービス層の担当）

## A-32: 見積（EST）のテーブル（db:EST）で決めたこと

- **選択:**
  - 置き場は `backend/app/models/est.py`（DB-EST-001）、移行は `0008_est`（`0007_chg` の次）
  - **見積を書くたびに 1 行を足す履歴の表**として作った（EST-08 の「見積値を変えた履歴が残る」）。一意制約は置かない（4.1 に定めが無い）。
    今の見積の値は `wbs_elements.own_estimate_hours`・`activities.estimate_hours` のまま（両方へ書くのは API-EST-001。api:EST の担当）
  - **`target_id` は外部キーを持たない**：対象の種類（ELEMENT＝`wbs_elements.id`、ACTIVITY＝`activities.id`）で指す表が変わり、4.2 の `<単数形>_id` に当たらない
    （`approval_requests.target_id`・`audit_logs.entity_id` と同じ）。対象の WBS要素・活動を消しても記録は残る（履歴を黙って消さない）。
    対象がそのプロジェクトの要素・活動であることは書く API（API-EST-001）が確かめる。`project_id`・`entered_by` には外部キーを張り、`ON DELETE` は付けない（A-11・A-15・A-19・A-22・A-27・A-31）
  - 必須の列：`project_id`・`target_type`・`target_id`・`hours`・`input_value`・`input_unit`・`entered_by`・`entered_at`（既定は `utc_now`）。
    **任意：`method`・`assumptions`・`references`**（EST-08 の受入基準は「保存でき」。RULE-EST-002 の `record_estimate` も省ける）
  - 値の検査（CHECK）：対象の種類は 4.1 の 2 つ、入力の単位は 2 つ、手法は空か EST-08 の 5 つ、`hours`・`input_value` は 0 以上（EST-01 の「負の値は入力できない」）。
    値の一覧は `app.domain.estimate` の `TargetType`・`InputUnit`・`EstimateMethod` と同じ（試験 `test_value_lists_match_design_4_1_and_estimate_rules` が食い違いを落とす）。
    工数は人時の `Numeric(10,2)`（2.4）、前提・参照資料は長さの定めが無いので `Text`。人日の換算（`hours` と `input_value` の関係）は RULE-EST-002 の担当で DB では見ない
  - 索引：対象ごとの履歴（API-EST-002）のために（project_id, target_type, target_id）、利用者の外部キーに `entered_by`
  - **別の群の口を差し替えた**（A-18・A-31 の申し送り）：横断照会の `app.state.wbs_readers.estimate_records` を `estimate_records_table`（`app/services/wbs.py`）へ。
    プロジェクトの全ての記録（同じ対象の古い記録も）を ID の順に読む
- **理由:** 設計書 2.4・3 章・4.1・4.2・5.1（RULE-EST-002）・5.8・6.2（API-EST-001〜003）、要求仕様書 EST-01・08。A-18・A-31 の申し送り
- **影響範囲:** `backend/app/models/est.py`・`backend/app/models/__init__.py`・`backend/alembic/versions/20261111_0008_est_tables.py`・`backend/app/services/wbs.py`・`backend/app/main.py`・
  `backend/tests/models/test_models_est.py`・`backend/tests/services/test_estimate_tables.py`
- **覆す場合:** 手法を必須にするなら NOT NULL の移行を足し、試験 `test_estimate_records_keep_history_per_target` を直す。
  対象を消したら記録も消すなら、消す API（API-WBS-005・API-SCH-005）で記録を消し、試験 `test_estimate_records_target_has_no_foreign_key_and_survives_its_target` を直す
- **限界:** 移行は SQLite でだけ流した（upgrade・downgrade・upgrade）。PostgreSQL では流していない（`references` は SQL の予約語で、SQLAlchemy が引用符で囲む）。
  見積を書く API（api:EST）はまだ無いので、行を作るのは試験だけ。横断照会（API-WBS-023）を実際の表と繋いだ API の試験は足していない（読む口の単体の試験だけ）。
  変更履歴（`audit_logs`）への記録は OQ-08・RULE-CHG-006 のスコープで、この表の書き込みも記録していない

## A-33: 変更管理（CHG）の計算・判定ルール（rule:CHG）で決めたこと

- **選択:**
  - 置き場は 5.1 どおり：RULE-CHG-001・002 は `backend/app/domain/change.py`、RULE-CHG-003・005 は `workflow.py`、RULE-CHG-004 は `baseline.py`、
    RULE-CHG-006 は `audit.py`。試験は `backend/tests/domain/` の `test_change.py`・`test_workflow.py`・`test_baseline.py`・`test_audit.py`
  - 変更内容の適用（RULE-CHG-001。`apply_operations`）：計画のモデル（`PlanModel`。不変のデータ）の写しへ 5.9 の操作を**順に**当てる。
    1 件でも不正ならその操作の位置（`index`）を付けた `ChangeError` で止め、何も返さない（途中まで当てた計画は返さない）。
    新しく作る要素・活動には負の仮の ID（-1, -2, … の作った順）を振る（DB の ID と重ならない。入力の計画の ID は 1 以上でなければ拒む）。
    新しい要素の WBS ID は RULE-WBS-002 の `new_element_code`（削除で空いた番号は再利用しない）、表示順は兄弟の最大＋1（API-WBS の要素の追加と同じ）。
    WBS ID は今の ID、無ければ新旧対応の旧 ID で解決する。仮の参照名（`ref`）は要素と活動で 1 つの名前空間（重なれば `DUPLICATE_REF`）。
    業務ルールは操作ごとに RULE-WBS-003（WP は子を持たない・活動は WP にだけ・区分の変更も含む）と RULE-SCH-001（`cycle_if_added`。`SCHEDULE_CYCLE` の
    `details` は循環の活動）を当て、最後に当てた後の木を `check_hierarchy` で検査し直す。期間 0 の活動をマイルストーンにする（4.2）。OQ-30 (1)〜(5)(9)
  - 影響分析（RULE-CHG-002。`analyze_impact`）：前後の計画それぞれに `calculate_schedule`（RULE-SCH-002・003）と `rollup_estimates`（RULE-EST-001）を流す。
    ずれは稼働日（`working_day_difference`。前へずれたら負）。`milestones` は前後の両方にあって日付の変わったもの、`critical_path` は加わった（後の順）・外れた（前の順。消した活動を含む）、
    `elements` は追加（作った順）・`update_element` の 4 項目が変わった要素・消した要素（配下を含む）。新しく作ったものは ID を出さず `ref` を出す。
    `ImpactAnalysis.to_json()` が `change_requests.impact` に保存する形（日付は ISO 8601、工数は小数 2 桁の文字列）を作る。OQ-30 (6)
  - 変更要求の状態遷移（RULE-CHG-003。`transition_change_request`）：5.10 の表どおり（提出・承認・却下・取り下げ・反映）。状態を先に見て、
    承認・却下は提出者と同じ人なら `SELF_APPROVAL_FORBIDDEN`（RULE-WBS-006 と同じ `ensure_not_self_approval`）。反映は APPROVED だけ。
    直せるのは DRAFT だけ（`ensure_change_request_editable`。6.2 の API-CHG-003）
  - 再基準化（RULE-CHG-004。`plan_rebaseline`）：反映した後の WBS と日程から、スコープとスケジュールの**足す版だけ**を作る（今ある版の最大＋1、理由 CHANGE_REQUEST、元の変更要求）。
    写し方は手動の版（API-SCH-011 の `save_baseline`）と承認の版（`_snapshot_scope`）と同じ。スケジュールの版の名前は呼び手が渡す（OQ-20 (3)。空・201 文字は拒む）
  - 是正措置（RULE-CHG-005）：`transition_corrective_action` は 5.10 の 3 つの遷移と、同じ状態のまま（効果だけの更新）を通す。
    `is_corrective_action_overdue` は「期限 ＜ 今日 かつ DONE でない」。期限ちょうどは超過でない、期限の無い措置は超過にならない。OQ-30 (8)
  - 変更履歴の差分（RULE-CHG-006。`make_audit_diff`）：CREATE は後の全ての値、DELETE は前の全ての値、UPDATE は変わった項目だけ（変わらなければ記録しない `None`）。
    値は JSON の形へ直す（日付・時刻は ISO、Decimal は文字列、列挙は値）。**ハッシュの列（`password_hash`・`token_hash`）は既定で `***` に伏せる**（OQ-08 の提案。変わったことは残る）
- **理由:** 設計書 1.3・4.1・4.2・5.1・5.2・5.5・5.8・5.9・5.10・6.1・6.2、要求仕様書 CHG-01〜04・06・09、BR-02・BR-12、NFR-05。OQ-08・OQ-16・OQ-20
- **影響範囲:** `backend/app/domain/change.py`・`backend/app/domain/workflow.py`・`backend/app/domain/baseline.py`・`backend/app/domain/audit.py`・
  `backend/tests/domain/test_change.py`・`backend/tests/domain/test_workflow.py`・`backend/tests/domain/test_baseline.py`・`backend/tests/domain/test_audit.py`
- **覆す場合:** `add_element` に `parent_ref` を足すなら `_OPERATION_KEYS` の任意の項目に足して `_add_element` で引き、試験を足す。
  足した・消したマイルストーンも出すなら `analyze_impact` の `milestones` の条件を変え、試験 `test_impact_reports_updated_and_removed_elements` を直す。
  ハッシュの値も履歴に写すなら `SECRET_FIELDS_DEFAULT` を空にし、試験 `test_secret_values_are_redacted` を直す
- **限界:** ドメインの関数だけで、API（api:CHG）・サービス・infra の記録の口（`audit_listener`）からはまだ呼んでいない。
  `app.state.change_request_approvals` は `ChangeRequestTransitionNotWired` のまま（A-31。API-CHG-005 と同時に api:CHG が差し替える）。
  影響分析は今のカレンダーとプロジェクトの開始日で前後を計算する（変更内容でカレンダーは変えられない）。権限（change.create・change.approve・change.apply・action.manage・audit.view）は RULE-AUTH-001 の担当

## A-34: 表形式の入出力（rule:INT）の RULE-INT-001 で決めたこと

- **選択:**
  - 置き場は 5.1 どおり `backend/app/domain/tabular.py`、試験は `backend/tests/domain/test_tabular.py`
  - **xlsx のファイルの読み書きはドメインに置かない**（1.3：ドメインは標準ライブラリのみ。openpyxl は使えない）。ドメインは
    「セルの格子（1 行目が見出し）↔ 6.3 の行」と CSV（標準の `csv`。UTF-8 の BOM 付き・カンマ区切り・CRLF）を受け持つ。
    xlsx は api:INT が openpyxl で 1 枚目のシートの格子を読み書きし、読んだセルの値は `cell_text` で同じ文字列に直す（数・日付・真偽・空）
  - 見出しは 6.3 の列の名前から Markdown の ` を外した文字（`先行の活動ID（; 区切り）` など）を、この順で完全一致で照らす。違えば
    `HEADER_MISMATCH`（1 行目）だけを返し、行は読まない。見出しの右の空のセルは許す。全てが空の行は読み飛ばす。BOM の無い UTF-8 も読む。OQ-31 (1)(7)
  - 誤りは `ImportIssue`（`row`＝ファイルの行。見出しが 1、xlsx の行番号と同じ／`column`＝列の見出し、行の誤りは `None`／`code`／`message`）。
    コードは `ImportIssueCode`（形式・範囲・必須・文字数・重複・参照先、と全件の検査の RULE-WBS-003・RULE-SCH-001・RULE-EFF-005 の名前）。
    並びは行の順、同じ行は列の順
  - 検査の段：行ごとの検査（参照先の有無・照合キーの重複を含む）を全行に当てて全件を返し、それが通ったときだけ取り込んだ後の全体へ
    RULE-WBS-003（WP の子・親子の循環・活動のある要素の区分の変更）・RULE-SCH-001（依存の循環）・RULE-EFF-005（自分の割当・1 日 24 時間・確定した報告日）を当てる。
    全体の検査の誤りはファイルの行に関わるものだけ（今のデータのままの不整合は取込の誤りにしない）
  - 誤りが 1 件でもあれば `ImportPlan.changes` は空（何も書かない）。書くのはサービス層（1 つのトランザクション）。`dry_run` は `ImportPlan` をそのまま返せばよい
  - 照合：`wbs` は WBS ID を RULE-WBS-002 の `resolve_code`（今の ID → 旧 ID）で解決し、解決できれば更新、できなければ追加。
    追加の行の WBS ID は仮の ID で、親の参照に使う（実際の ID はサービス層が `new_element_code` で振る）。追加は親を先に並べる。
    親の WBS ID の変更は移動（`move_element` で ID は変えない）。`activities` は活動ID（空なら追加、無い ID は `NOT_FOUND`）。
    `time-entries` はログインID＋活動ID＋作業日（行の無いキーは 0 時間・備考なしとみなし、同じなら変更なし）。OQ-31 (3)
  - ファイルに無い要素・活動・実績は消さない。先行には今の活動の ID だけを書ける。OQ-31 (4)(5)
  - 区分はコード（`DELIVERABLE` など）。マイルストーン（0/1）は期間 0 と組でだけ置ける（4.2・DB の検査制約と同じ）。
    工数は 0 以上・小数 2 桁まで（見積は 99999999.99 以下、実績の 1 行は 24 以下）。名称は 200 文字・ログインID は 100 文字・WBS ID は 100 文字まで。OQ-31 (2)
  - 自由な文字（名称・辞書・備考）は前後の空白も含めてそのまま持つ（往復で一致させるため）。空・空白だけは「無い」とみなす。
    識別子（WBS ID・ログインID）・日付・数は前後の空白を外す
  - 実績工数の備考だけの変更は RULE-EFF-005 の「変えない」マスだが、確定した報告日より前の日では `LOCKED_DATE` で拒む。OQ-31 (6)
  - 往復の一致：出力（`export_grid`。先行・担当者は昇順、工数は小数 2 桁）した直後の表を取り込むと `changes` が空・全ての行が `unchanged`。
    比べ方は列ごと（先行・担当者は集合、工数は `Decimal` の値、文字の空は `None`）
- **理由:** 設計書 1.3・2.4・4.1・4.2・5.1・5.2・6.2・6.3、要求仕様書 INT-01・BR-02・BR-13・EFF-02。A-28・A-30・A-33
- **影響範囲:** `backend/app/domain/tabular.py`・`backend/tests/domain/test_tabular.py`
- **覆す場合:** 見出しを変えるなら `WBS_COLUMNS`・`ACTIVITY_COLUMNS`・`TIME_ENTRY_COLUMNS` と試験 `test_columns_follow_design_6_3` を直す。
  区分を日本語にするなら `_Cells.kind` と `format_row` に対応表を足す。ファイルに無いものを消すなら `ImportPlan` に削除の行を足し、
  往復の試験はそのまま通る（全てがファイルにある）ことを確かめる
- **限界:** ドメインの関数だけで、API（API-INT-001・002）・サービス・xlsx の読み書きはまだ無い（api:INT の範囲）。
  サービス層が渡す `login_ids`・`users` は、今の値に現れるログインID（無効にした利用者を含む）を含めないと往復で `NOT_FOUND` になる。
  出力のセルの式の解釈（CSV インジェクション）は未対策（OQ-31 (8)）。権限（data.export・data.import）は RULE-AUTH-001 の担当

## A-35: 定例会議レポート（rule:RPT）の RULE-RPT-001 で決めたこと

- **選択:**
  - 置き場は 5.1 どおり `backend/app/domain/report.py`（`build_meeting_report`）、試験は `backend/tests/domain/test_report.py`
  - 値の源：確定済み（`FINALIZED`）は渡されたスナップショットの写しだけ（今の活動の値を渡すと拒む。報告日が違えば拒む）。
    確定前（`OPEN`）は写しを受けず、RULE-PRG-007 の `finalize_status_report` を**状態を変えずに**呼んで計算値を求める（保存はサービス層がしない）。
    確定と同じ計算なので、確定の前後でレポートの値が変わらない
  - 上段：ガントは RULE-SCH-005 の結果（今の計画）をそのまま載せる。イナズマ線は写しの値だけから `restore_lightning_line` で作り、
    `overlay_lightning_lines` で今のガントの行へ置き直す（今のガントに無い活動は `missing_activity_ids`）。表示の範囲は報告日の前後 4 週（暦日 28 日。両端を含む）で、行は絞らない。OQ-32 (4)
  - 下段左：写しの判定とずれ・進捗率と、報告日の完了見込み日を RULE-PRG-006 の `analyze_delays` へ渡し、その抽出（δ ≦ −写しの抽出の閾値）・
    ずれの順・見込みの終了をそのまま使う。行には WBS ID・名称・予定の終了日（写し）・遅延理由・見込みが入力か計算か・クリティカル（今の網）を付ける。
    写しの後に消した活動は今の網に無いので影響の計算に渡さない
  - 下段中：写しのマイルストーン（行の順）。予定日＝写しの日付、見込み日＝遅延の見込みを全て当てた前進計算（5.7 の 4）でずれた日（ずれなければ予定日）、
    ずれ＝見込み日と予定日の終業の位置の差（稼働日）。OQ-32 (5)
  - 下段右（KPI）：全体の進捗率＝`Σ(見積×進捗率)/Σ見積`（未算定は分子・分母から外す、見積の空は 0、重みの合計 0 は None、小数 2 桁で四捨五入）。
    遅れの件数＝写しの判定が `BEHIND` の活動の数（抽出の閾値に関わらない）。EAC と基準の差＝RULE-EFF-002 のプロジェクトの `eac − baseline`（基準が無ければ None）。
    クリティカルパスの最小フロート＝ガントのクリティカルな行のトータルフロートの最小（無ければ None）。
    是正措置の期限超過＝RULE-CHG-005 の `is_corrective_action_overdue` を報告日を今日として当てた数。OQ-32 (1)(2)
  - 入力の誤り（未定義の状態・同じ活動の進捗の入力が 2 つ・同じ是正措置が 2 つ・負の見積・写しと状態の食い違い）は `ReportError`。
    日程の循環は RULE-SCH-001 の `ScheduleCycleError` のまま返す
- **理由:** 設計書 1.3・4.1・5.1・5.6・5.7・5.8・7.3・7.4、要求仕様書 RPT-01・BR-09・BR-13。既にあるルール（RULE-SCH-005・RULE-PRG-005〜007・RULE-EFF-002・RULE-CHG-005）を組み合わせ、同じ式を二重に持たない
- **影響範囲:** `backend/app/domain/report.py`・`backend/tests/domain/test_report.py`
- **覆す場合:** 未算定を 0 として数えるなら `_overall_progress` の `continue` を外し、試験 `test_overall_progress_excludes_not_computed_and_none_when_no_weight` を直す。
  期限超過を実際の今日で数えるなら `build_meeting_report` に `today` を足して `_overdue_count` へ渡し、試験 `test_kpi_eac_variance_critical_float_and_overdue_actions` を直す。
  上段の行を範囲で絞るなら `MeetingReport` に絞った行を足す（線の頂点は全行に要る）
- **限界:** ドメインの関数だけで、API（API-RPT-001）・サービス・画面（UI-RPT-001）からはまだ呼んでいない（api:RPT・ui:RPT の範囲）。
  確定済みのレポートでも、ガント・依存の網・フロート・見積・EAC・是正措置の状態は今の値（写しに無い。OQ-32 (3)）。権限（project.view）は RULE-AUTH-001 の担当

## A-36: スケジュールの画面（ui:SCH）の UI-SCH-001・002・003 で決めたこと

- **選択:**
  - 置き場は `frontend/src/pages/sch/`（UI-SCH-001 の部品は `activities/`）、ガントの描画は 7.3 どおり `frontend/src/components/gantt/`。
    API の型と呼び出しは `frontend/src/api/sch.ts`（API-SCH-001〜013）と、画面が読む分だけの `prg.ts`（API-PRG-002・006・008）・`eff.ts`（API-EFF-001）・
    `wbs.ts`（API-WBS-001）。形は `backend/app/schemas/schedule.py`・`progress.py`・`effort.py`・`wbs.py` を読んで合わせた
  - `apiFetch` の問い合わせに配列を許し、同じ名前を繰り返す（`report_ids=40&report_ids=41`。API-PRG-008 の `list[int]` の Query の形）
  - **ガントの座標は画面で稼働日を数え直さない**。x はサーバの暦日上の座標（日付＋その日の中の割合。7.3）から求める。報告日線もサーバの `report_line` だけで描き、
    報告日を選んでいない（イナズマ線を読んでいない）ときは報告日線を描かない
  - 1 日あたりの横幅（7.3 の表示の単位）：日 24px・週 6px・月 2px。行の高さ 28px。見えている行と前後 5 行だけを描く（NFR-02）。依存の矢印は見えている範囲にかかるものだけ、
    イナズマ線の折れ線は全ての行の頂点で 1 本（頂点の円は見えている行だけ）。折れ線は報告日線の上端から始め、行の順に頂点を結び、下端で終える
  - 頂点の色は判定の上で数える値（5.6：完了・未着手（予定内）は予定どおり＝緑）。過去の線（⑧）は報告日の古い順の `style_index` で破線の種類を変え、種類が尽きたら繰り返す
  - 判定の一覧（PRG-13 の一覧化・絞り込み）は UI-SCH-002 の下に置き、6 つの判定のどれかで絞る。未算定の行には「進捗の入力が要る」と出す（5.6 の「一覧で入力を促す」）
  - 既定で開く報告日は報告日の最も新しいもの。過去の線に選べるのは確定済み（FINALIZED）の報告日だけ（PRG-18）。比べる基準の既定はサーバの既定（`baseline_id` を省く＝最新の版）
  - スナップショットのイナズマ線（API-PRG-006 の SNAPSHOT）の行は、活動 ID で今のガントの行へ置く（消した活動の行は描かない）。過去の線（API-PRG-008）はサーバの `row_index` のまま
  - タスクリストの一括編集（SCH-02）は**変えた行の変えた項目だけ**を API-SCH-004 へ 1 回で送る（全件の検査はサーバ。422 の details を全て出し、入力は残す）。
    画面で止めるのは送れない形（期間が整数でない・先行が活動 ID でない・名称が空）だけ。先行は活動 ID を `;` で区切って入れる（6.3 の CSV の活動 ID と同じ表し方）。
    1 頁は 200 件（API-SCH-004 の上限 1000 件以内）
  - 担当は一括編集（API-SCH-004 の `assignee_user_ids`）でも、担当と割当工数の部品（API-EFF-001。EFF-01 の割当工数・見積との差）でも変えられる。
    部品の合計・差は保存前は画面で求めた目安、保存後はサーバの値を出す
  - 活動を足す（API-SCH-002）：開始日・終了日・期間のうち入れたものだけを送り、残りはサーバが求める（SCH-03）。マイルストーンの印を付けると期間 0・`is_milestone` を送る（SCH-09）
  - 変える部品は schedule.edit（タスクリスト・再計算）、baseline.manage（版の保存）を `GET /api/v1/me` で見て出す（7.2）。WP の木とメンバーの一覧は変える人だけが読む
  - 版の保存の理由の呼び名（MANUAL＝保存・RE_APPROVAL＝再承認・CHANGE_REQUEST＝変更要求の反映）は設計書に無いので決めた。差異の区別は MATCHED＝両方にある・ADDED＝追加された・DELETED＝削除された
- **理由:** 設計書 6.2（API-SCH-001〜013・API-PRG-002・006・008・API-EFF-001・API-WBS-001）・7.1・7.2・7.3・5.4・5.6、要求仕様書 SCH-01〜04・06・09〜13・16〜18、PRG-08・12・13・18、EFF-01・NFR-02
- **影響範囲:** `frontend/src/pages/sch/`・`frontend/src/components/gantt/`・`frontend/src/api/{sch,prg,eff,wbs}.ts`・`frontend/src/api/client.ts`（配列の問い合わせ）・
  `frontend/src/App.tsx`・`frontend/src/test/schFixtures.ts`
- **覆す場合:** 横幅は `components/gantt/scale.ts` の `DAY_WIDTH`、余分に描く行は `visibleRows` の `overscan`、色は `judgementColor`、破線は `PAST_LINE_DASHES`、
  1 頁の件数は `pages/sch/limits.ts` の `ACTIVITIES_PAGE_SIZE`
- **限界:** 試験は fetch を差し替えた画面の試験だけで、実際のサーバと繋いでは確かめていない。NFR-02 の「2,000 件で 3 秒以内」は計っていない（試験は描く行が限られることだけを見る）。
  見た目（CSS）は付けていない。行の見出しは横に送ると隠れる（縦に送っても目盛りは上に残す）。依存の選択肢は今の頁の活動だけ。
  担当と割当工数を保存すると一覧を読み直すので、保存していない一括編集の入力は消える。UI-PRG-001（進捗入力）への行き先は ui:PRG の画面ができるまで「ページが見つかりません」になる

## A-37: 変更管理（CHG）の API（api:CHG）の API-CHG-001〜010 で決めたこと

- **選択:**
  - 置き場は 2.1 どおり：ルートは `backend/app/api/change.py`、ユースケースは `backend/app/services/change.py`、入出力は `backend/app/schemas/change.py`。
    試験は `backend/tests/api/test_chg.py`
  - 起票・修正（API-CHG-001・003）：対象の WBS ID（複数可）は今の ID か旧 ID で解決できること（無ければ 422 `REFERENCE_NOT_FOUND`）。
    変更内容は**起票・修正の時点で**今の計画へ当てて検査する（RULE-CHG-001。形の誤りは 400 `VALIDATION_ERROR`、参照・業務ルールの違反は 422 で
    `ChangeErrorCode` のコードをそのまま返す。`details` に操作の位置 `index`）。本計画には書かない。番号はプロジェクトの中の最大＋1
  - 影響分析（API-CHG-004）：保存した変更内容を今の計画の写しへ当てる（RULE-CHG-002）。本計画にも変更要求の `impact` にも書かない。
    反映済み・却下の変更要求は 409 `INVALID_STATE`（OQ-33 (3)）
  - 提出（API-CHG-005）：RULE-CHG-003 の SUBMIT。影響分析を `impact` に保存し、`submitted_at` を入れ、承認申請（`target_type = CHANGE_REQUEST`、
    申請者＝提出した人）を作る。応答の `approval_request_id` は最新の申請
  - 承認・却下は API-WBS-019 の判断を `ChangeRequestStateMachine`（`app.state.change_request_approvals`。A-31 の `ChangeRequestTransitionNotWired`
    から差し替えた）が RULE-CHG-003 で変更要求へ通す。提出者の判断は 403 `SELF_APPROVAL_FORBIDDEN`（9.2 の注記。WBS と同じ対応）。
    申請の指す変更要求が無いのは申請と変更要求の食い違いなので `RuntimeError` で止める（fail fast。A-31 と同じ）
  - 反映（API-CHG-006）：RULE-CHG-003 の APPLY → RULE-CHG-001 で今の計画へ当て直す（承認の後に計画が変わっていれば 400・422）→ 本計画へ書く
    （仮の ID の要素・活動を作り、変わった値を写し、依存の組の差を消す・足す。活動・要素の削除の後始末は API-SCH-005・API-WBS-005 と同じ）→
    日程を計算し直す（`calculate_and_store`）→ RULE-CHG-004 でスコープとスケジュールの新しい版（理由 CHANGE_REQUEST、名前は「変更要求 #番号 件名」）を作る。
    WBS の状態は新しいスコープの版ができたので BASELINED にする。WBS の承認待ち（PENDING_APPROVAL）の間は 409（OQ-33 (5)）。
    基準の写しが指す要素の削除は 422 `ELEMENT_IN_BASELINE`（CR-002 の暫定）、是正措置・差異の理由が指す要素は 422 `ELEMENT_REFERENCED`、
    是正措置が指す活動は 422 `ACTIVITY_REFERENCED`、実績工数のある活動は 409（A-30 と同じ）。どこかで止まれば何も書かない（OQ-33 (6)）
  - 是正措置（API-CHG-007〜009）：要素か活動の少なくとも一方に紐付ける（WBS ID で絞るため）。紐付け・担当はこのプロジェクトのもの（違えば 400）。
    一覧の WBS ID はその要素と配下、その WP の活動に紐付いた措置。期限超過の「今日」は `now`（UTC）の日付（OQ-33 (1)）。
    更新できるのは状態（RULE-CHG-005）・効果・担当・期限（OQ-33 (4)）
  - 委託先だけで読む利用者（9.2 の注記）：変更要求は対象の WBS ID が自分の割当の活動の WBS の経路に入るものだけ、是正措置は自分の割当の活動か
    その経路の要素に紐付いたものだけを見せる
  - 変更履歴（API-CHG-010・6.1・NFR-05）：`backend/app/infra/audit_listener.py` に記録を実装した。アプリのセッションの生成器の `after_flush` で、
    ORM で追加・更新・削除した行（`audit_logs` 自身を除く）を RULE-CHG-006 の差分にして Core の INSERT で同じトランザクションに追記する。
    変更者と時刻は認証（`get_current_user`）が `bind_actor` でセッションに載せる。プロジェクトと WBS ID は行の列、無ければ親の行
    （要素・活動・報告日・カレンダー・レビュー・版）からたどる。検索の期間は `date_from`・`date_to`（UTC の日付で両端を含む。OQ-33 (2)）、新しい順
- **覆す場合:** 起票の時点の検査は `create_change_request`・`update_change_request` の `_apply`、影響分析の状態の制限は `_IMPACT_CLOSED`、
  反映の後の WBS の状態は `apply_change_request`、是正措置の必須の紐付けは `create_corrective_action`、「今日」は `backend/app/api/change.py` の `get_today`
- **限界:** ~~変更履歴は ORM の単位作業を通る書き込みだけを記録する。一括の文は記録しない~~ → **A-47 で一括の文（`session.execute(update(...))`・`delete(...)`）も記録するよう改めた**
  （OQ-33 (7)）。記録しないのは `Session` を通らない書き込みだけ。ログインで作る認証セッションは変更者が空。
  反映は要素の移動（5.9 に操作が無い）を扱わない。試験は SQLite の TestClient だけで、PostgreSQL では流していない

## A-38: 見積（EST）の API（api:EST）の API-EST-001〜003 で決めたこと

- **選択:**
  - 置き場は `backend/app/api/estimate.py`（ルート）・`backend/app/services/estimate.py`（ユースケース）・`backend/app/schemas/estimate.py`（入出力）、
    試験は `backend/tests/api/test_est.py`。ルータは `app.main.register_routers` に足した
  - 見積を書く（API-EST-001。estimate.edit）：`target_type` はパスの `ELEMENT`／`ACTIVITY`（4.1 の値。誤りは 400）。対象がそのプロジェクトに無ければ 404。
    入力は `input_value`（0 以上・小数 2 桁まで）・`input_unit`（HOUR／DAY）・任意の `method`・`assumptions`・`references`。
    RULE-EST-002（`record_estimate`）で人時へ換算する。人日の 1 日の稼働時間は `calendars.hours_per_day`（行が無ければ 4.1 の既定 8）。
    換算後に `Numeric(10,2)` へ入らない値は 400。記録（`estimate_records`）を 1 行足し、対象の今の見積
    （`wbs_elements.own_estimate_hours`／`activities.estimate_hours`）を換算した値にする。応答は記録・今の見積・WBS の状態・プロジェクトの総見積（RULE-EST-001）
  - **WBS要素の対象は区分を問わない**（OQ-34 (1)）：API-WBS-003 が全ての要素の自分の見積を変えられ、5.3 の 100% ルールが上位の要素の自分の見積を使うため
  - **WBS要素の見積を書くのは WBS の編集**（RULE-WBS-006 の EDIT。`app.services.wbs._mark_edited` を共有）：承認待ちの間は 409 `INVALID_STATE`、
    基準化の後は DRAFT へ戻す（API-WBS-003 で自分の見積を変えたときと同じ）。**活動の見積は WBS の状態を変えない**（活動の更新 API-SCH-003 と同じ）（OQ-34 (2)）
  - 見積の履歴（API-EST-002。project.view）：対象の記録を古い順（`entered_at`・`id`）に、`limit`・`offset`（6.1）と `total` で返す。
    記録は対象を消しても残る（A-32）ので、**対象が無くても記録があれば返す**（`target_exists` が偽）。対象も記録も無ければ 404（OQ-34 (3)）。
    書いた利用者の表示名（`entered_by_name`）を付けた（UI-WBS-002 の根拠の表示のため）
  - ボトムアップの集計（API-EST-003。project.view）：RULE-EST-001 の要素ごとの値・集計の元・下位の合計・自分の見積と、活動ごとの値を返す。
    要素は先行順（兄弟は表示順。API-WBS-001 と同じ `_preorder`）、活動は WP の先行順→WP の中の表示順。集計なので `limit`・`offset` は受けない
  - 委託先だけで読む利用者（9.2 の注記・RULE-AUTH-001）：履歴は見える要素（自分の割当の活動の WBS の経路）と自分の割当の活動だけ（他は 404）。
    集計は見える要素・活動だけを返し（`restricted` が真）、プロジェクトの値は見える最上位の要素の値の合計（API-WBS-001 の `project_estimate_hours` と同じ。OQ-34 (5)）
  - 書き込みは ORM を通すので、記録の追加と今の見積の更新が変更履歴（`audit_logs`）に残る（6.1。A-37 の infra のイベント）
- **理由:** 設計書 4.1・5.3・5.8・5.10・6.1・6.2・9 章、要求仕様書 EST-01・EST-02・EST-08。A-13・A-32 の申し送り（反映と対象の確かめはサービス層の担当）
- **影響範囲:** `backend/app/api/estimate.py`・`backend/app/services/estimate.py`・`backend/app/schemas/estimate.py`・`backend/app/main.py`・`backend/tests/api/test_est.py`
- **覆す場合:** 要素の対象を WP に限るなら `write_estimate` で区分を見て 422 にし、試験 `test_put_element_estimate_updates_own_estimate_and_wbs_status` 等を直す。
  要素の見積を WBS の編集とみなさないなら `write_estimate` の `_mark_edited` を外し、試験 `test_put_element_estimate_while_pending_approval_is_409` を直す
- **限界:** API-WBS-002・003 は `own_estimate_hours` を記録を残さずに書ける（api:WBS の範囲。OQ-34 (4)）。EST-08 の「履歴が残る」は API-EST-001 で書いた分だけ。
  前提・参照資料の長さの上限は置いていない（4.1 に定めが無い。A-32）。試験は SQLite の TestClient だけで、PostgreSQL では流していない

## A-39: 表形式の入出力（INT）の API（api:INT）の API-INT-001・002 で決めたこと

- **選択:**
  - 置き場は 2.1 の名前どおり `backend/app/api/io.py`（ルート）・`backend/app/services/io.py`（ユースケース）・`backend/app/schemas/io.py`（応答）、
    試験は `backend/tests/api/test_int.py`。ルータは `app.main.register_routers` に足した。列・照合・検査・往復の一致は RULE-INT-001（A-34）をそのまま使う
  - **取込のファイルは要求の本体そのもの**（`Content-Type` は `text/csv`・xlsx の型・`application/octet-stream` のどれでもよい。JSON の型は不可）。
    `target`・`format`・`dry_run` はクエリ。multipart は python-multipart が版の固定（`backend/requirements.lock`）に無く、依存を足さずに済むため（OQ-35 (1)）。
    空の本体は「見出しの行が無い」（`HEADER_MISMATCH`）として返す
  - `format` は省けば `csv`、`target` は必須。どちらも 6.2 の値でなければ 400
  - **`dry_run=true` は誤りがあっても 200**（`ok` が偽・`issues` に 6.3 の `{row, column, code, message}` の全件・`changes` は空）。
    **`dry_run` でなく誤りがあれば何も書かずに 422 `IMPORT_INVALID`**（`details` が同じ誤りの配列。6.1 の「業務ルールの違反は 422」）。
    通れば 200 で、書いた行（`changes`：行・CREATE／UPDATE・照合キー・変わる列）と `created`・`updated`・`unchanged` を返す（OQ-35 (2)）
  - WBS を変える取込は RULE-WBS-006 の EDIT（承認待ちの間は `dry_run` でも 409 `INVALID_STATE`、基準化の後は DRAFT へ戻す）。変えない取込は状態を動かさない
  - ドメインの検査に、サービス層で 3 つ足した（誤りの形は同じ）：書く行の責任者・新しく置く担当は「メンバーである有効な利用者」（API-WBS-003・API-EFF-001 と同じ。
    今の値に現れる無効な利用者は往復のために「いる」とみなすが、新しくは置けない。`NOT_FOUND`）、0 時間の実績の行に備考は書けない（API-EFF-003 と同じ。`INVALID_VALUE`）
  - 書き方（1 つのトランザクション）：WBS は追加（親が先。RULE-WBS-002 の `new_element_code` で採番し、ファイルの ID は仮）→ 移動（移した後の木の浅い順に
    `move_element`。ID は変えず改番待ちを付け直す。表示順は移動先の末尾）→ 列の更新（辞書は 4 項目を置き換え）。活動は追加（WP の末尾）・更新、先行はファイルの値が
    その活動の先行の全て（足りない依存を足し、外れた依存を消す）、担当は置き換え（`ScheduleLinks.assignments`）、希望開始日は稼働日へ寄せ（`resolve_activity_dates`）、
    最後に日程を計算し直す（RULE-SCH-002・003）。実績工数は照合キーの行を置き換え、0 時間は行を消す
  - **見積の値（WBS の自分の見積・活動の見積）を変えた・追加した行は、見積の記録（`estimate_records`。単位 HOUR・手法なし）を 1 行足す**（EST-08 の「見積値を変えた履歴が残る」。
    API-EST-001 と同じ形。空にした変更は記録しない）（OQ-35 (6)）
  - 出力の行の順：`wbs` は WBS の並び（先行順・兄弟は表示順）、`activities` は WBS の並び → WP の中の表示順、`time-entries` は作業日 → ログインID → 活動ID。
    ファイル名は `project-<ID>-<target>.<format>`（`Content-Disposition: attachment`）。xlsx のシート名は `target`
  - **xlsx のセルは全て文字で置く**（`=` で始まる名称・辞書も式にしない。往復で一致させる）。xlsx に置けない制御文字があれば 422 `EXPORT_UNSUPPORTED_CHARACTER`。
    読むときは 1 枚目のシートの値（式は計算済みの値）を `cell_text` で直す。xlsx として読めなければ `FILE_UNREADABLE`
  - 委託先だけで読む利用者（9.2 の注記）が data.export を持つときの出力は、自分の割当の活動とその WBS の経路だけ（見えない活動は先行にも実績にも出さない。RULE-AUTH-001）
- **理由:** 設計書 1.1（openpyxl 3.1）・2.1・4.1・5.2・5.5・5.10・6.1・6.2・6.3・9 章、要求仕様書 INT-01・BR-02・EST-08・EFF-01・EFF-02。A-34・A-37・A-38
- **影響範囲:** `backend/app/api/io.py`・`backend/app/services/io.py`・`backend/app/schemas/io.py`・`backend/app/main.py`・`backend/tests/api/test_int.py`
- **覆す場合:** multipart にするなら python-multipart を版を固定して足し、`import_file` の `content` を `UploadFile` にして試験の `World.load` を直す。
  誤りのある取込も 200 で返すなら `services.io._rejected` の 422 を外し、試験 `test_import_with_errors_writes_nothing` 等を直す。
  取込で見積の記録を足さないなら `_add_estimate_record` の呼び出しを外し、試験 `test_import_wbs_creates_updates_and_moves` の記録の確かめを直す
- **限界:** 実績工数の出力は data.export だけで他人の行も出す（6.2 の権限どおり。VIEWER は API-EFF-004 では他人の行を読めないが、出力では読める。OQ-35 (3)）。
  委託先だけで読む利用者に data.import を許したとき、取込は見える範囲に絞らない（OQ-35 (4)）。本体の大きさの上限は置いていない（OQ-35 (5)）。
  担当の置き換えの一括 DELETE も変更履歴に残る（A-47 で A-37 の限界を外した）。CSV の式の解釈（CSV インジェクション）は未対策のまま（OQ-31 (8)）。
  試験は SQLite の TestClient だけで、PostgreSQL では流していない。画面（UI-INT-001）は ui:INT の範囲

## A-40: レポート・共有（RPT）の API（api:RPT）の API-RPT-001〜004 で決めたこと

- **選択:**
  - 置き場は既存の群のファイルの名づけ（`change.py`・`estimate.py`）に合わせて `backend/app/api/report.py`（ルート）・`backend/app/services/report.py`
    （ユースケース）・`backend/app/schemas/report.py`（入出力）、試験は `backend/tests/api/test_rpt.py`。ルータは `app.main.register_routers` に足した
  - **会議レポート（API-RPT-001）は RULE-RPT-001（`build_meeting_report`。A-35）をそのまま呼ぶ**。値の源は API-PRG-006・009 と同じ：確定済みは
    `progress_snapshots`・`progress_snapshot_items` の写し（`SNAPSHOT`。写しの後に消した活動の行は activity_id が空のまま渡し、今のガントに無い頂点として外れる）、
    確定前は今の計画とその報告日の入力から確定と同じ計算（`CALCULATED`。保存しない。日程の無い活動があれば 409 `INVALID_STATE`）
  - 上段のガントは今の計画を、**その報告日の進捗**（確定済みは写しの進捗率・実績日付、確定前は報告日の入力から RULE-PRG-001 で求めた値）で描き、基準は最新の版。
    ガントの組み立ては API-SCH-010 と同じにするため、`services.schedule.gantt` の中身を `plan_gantt`（権限を見ない）へ切り出し、API-SCH-010・会議レポート・共有ビューの
    3 つが使う。応答の形も `api.schedule.gantt_out` へ切り出して共有した（API-SCH-010 の応答は変えていない。`test_sch.py` で確かめた）
  - KPI の工数は RULE-EFF-002 のプロジェクトの合計（`services.effort.load_basis` を報告日を指定して呼ぶ＝その報告日の進捗率・残工数と報告日以前の実績、最新の基準）。
    是正措置はプロジェクトの全ての措置の今の状態（A-35・OQ-32 (2)(3)）。見積は `activities.estimate_hours`
  - ドメインの `ReportError` は 422 `REPORT_INVALID`、日程の循環は 422 `SCHEDULE_CYCLE`
  - 委託先だけで読む利用者（9.2 の注記）には、自分の割当の活動だけでガント・線・遅延・マイルストーンを組み立て、KPI は null（`restricted` が真）。OQ-36 (5)
  - **共有リンク（API-RPT-002）**：本体は `scope`（GANTT／REPORT）と `expires_at`（時差つき。無い時差・今以前・他の項目は 400）。トークンは `infra.security.generate_token`
    （256 ビット）で作り、DB には RULE-AUTH-002 の `hash_token`（SHA-256）だけを書く。トークンは 201 の応答でだけ返す。時刻は UTC で保存し、応答も UTC。OQ-36 (1)(7)
  - **失効（API-RPT-003）**は行を消さずに `revoked_at` へ今を入れて 204。無い・他のプロジェクトのリンクは 404、既に失効させたリンクは 409 `INVALID_STATE`。OQ-36 (4)
  - **閲覧（API-RPT-004）**は認証の依存を付けない唯一のルート。トークンのハッシュで行を引き、RULE-AUTH-002 の `verify_share_link` で判定する。
    通らなければ 404 `NOT_FOUND`（`details` の `reason`）。`GANTT` は API-SCH-010 と同じガント（最新の進捗・最新の基準・全ての活動）、`REPORT` は 4.2 の最新の報告日の
    会議レポート（API-RPT-001 と同じ形。報告日が無ければ null）。範囲は `ShareLinkVerdict.can_view` で選ぶ。OQ-36 (2)(3)(6)
- **理由:** 設計書 2.1・2.4・4.1・4.2・5.1・6.1・6.2・7.4・9 章、要求仕様書 RPT-01・RPT-03・NFR-04・BR-13。既にあるルール（RULE-RPT-001・RULE-AUTH-001・002・RULE-EFF-002・
  RULE-SCH-005）と API-PRG・API-SCH の値の源を使い、同じ計算を二重に持たない
- **影響範囲:** `backend/app/api/report.py`・`backend/app/services/report.py`・`backend/app/schemas/report.py`・`backend/app/main.py`・
  `backend/app/services/schedule.py`（`plan_gantt` の切り出し）・`backend/app/api/schedule.py`（`gantt_out` の切り出し）・`backend/tests/api/test_rpt.py`
- **覆す場合:** 期限切れ・失効を 404 以外にするなら `services.report.shared_view` の `AppError` を変え、試験 `test_revoked_expired_and_unknown_links_cannot_be_viewed` を直す。
  REPORT の共有で確定済みの報告日だけを見せるなら `_LATEST_REPORT_STATUS_ORDER` を FINALIZED だけにし、試験 `test_shared_view_shows_scope_without_login` を直す。
  委託先に会議レポートを見せないなら `meeting_report` で 403 にし、試験 `test_meeting_report_permissions_and_errors` を直す
- **限界:** 共有ビューの応答は API-SCH-010 と同じく担当者の表示名を含む（OQ-36 (6)）。共有リンクの一覧の API は無い（6.2 に無い）。有効期限の上限は置いていない。
  共有の閲覧に回数の制限（総当たりの対策）は無い（トークンは 256 ビット）。確定済みのレポートでもガント・フロート・見積・EAC・是正措置は今の値（A-35 の限界）。
  試験は SQLite の TestClient だけで、PostgreSQL では流していない。画面（UI-RPT-001・002）は ui:RPT の範囲

## A-41: 進捗の画面（ui:PRG）の UI-PRG-001・002 で決めたこと

- **選択:**
  - 置き場は `frontend/src/pages/prg/`（UI-PRG-001 は `StatusReportPage.tsx` と 1 活動の編集の部品 `EntryEditor.tsx`、UI-PRG-002 は `DelaysPage.tsx`）。
    API の型と呼び出しは `frontend/src/api/prg.ts` に API-PRG-001・003・004・005・007・009 を足した（形は `backend/app/schemas/progress.py` を読んで合わせた）
  - **UI-PRG-001 の行は API-PRG-006（イナズマ線）の行をそのまま使う**。その報告日の入力（進捗率・実績日付・残工数・遅延理由・完了見込み日）と判定・ずれが 1 回で揃うため。
    確定済みの報告日はスナップショットの写し（SNAPSHOT）を出し、入力の部品を出さない（BR-13）
  - 部品は `GET /api/v1/me` の権限で出し分ける（7.2）：報告日の作成・確定は status_report.manage、進捗の入力と算定項目は progress.enter_own か enter_all、
    算定方式は schedule.edit。enter_own だけの人には「書けるのは自分の割当の活動の進捗だけ」と出し、割当の判定はサーバに任せる（403 の文言を出す）
  - 進捗の入力（API-PRG-003）は PUT なので、入力欄は今の値で埋めて全項目を送る。進捗率は直接入力（DIRECT）の活動だけが送り、他の方式は空で送る（欄を無効にする）。
    画面で止めるのは送れない形（進捗率が 0〜100 の外・残工数が負・実績開始日より前の実績終了日（PRG-02 の受入基準））だけ。空欄の残工数は自動（BR-10）で、
    一覧は「自動」と「N 時間（入力）」で区別する（EFF-06）。進捗率が空の行は「未算定（入力が要る）」と出す（5.6）
  - 算定方式（API-PRG-005）の選択肢は BR-06 の 7 つと「プロジェクトの既定に従う」（null）。今の値は行の効いている方式で、変えていなければ送れない
  - 算定項目（API-PRG-004）の入力欄は、効いている方式が項目を数える方式（成果物・承認済み作業・検証項目・重み付きマイルストーン）の活動にだけ出す。
    読む API が無い（OQ-37 (1)）ので空から始め、置き換えで既存の項目が消える旨を注記する。保存の応答の項目は id 付きで入力欄に残す
  - 確定（API-PRG-007）は戻せないので、画面の中で確認の段（「確定する」「やめる」）を 1 つ挟む（`window.confirm` は使わない）
  - 報告日の一覧は新しい順。作った報告日へはそのまま移る。報告日の ID の形は `parseProjectId` と同じ（正の整数）で、正しくなければ API を呼ばない
  - UI-PRG-002 はサーバの順（ずれの小さい順＝遅れの大きい順）をそのまま出す。表は 1 行 1 活動（ずれ・進捗率・遅延理由・完了見込み日・見込みの終了・クリティカルか・
    影響するマイルストーン）、その下に活動ごとの影響（後続・マイルストーン・プロジェクトの終了）と全体の影響（5.7 の 4）。終わりのずれは「+N 稼働日」、0 は「ずれなし」。
    委託先に返らない値（プロジェクトの終了のずれ）は出さない。是正措置（UI-CHG-002）へのリンクを置く（7.2）
- **理由:** 設計書 6.2（API-PRG-001〜009）・7.1・7.2・5.6・5.7・9 章、要求仕様書 PRG-01・02・03・05・17・19、EFF-06、BR-06・BR-10・BR-13
- **影響範囲:** `frontend/src/pages/prg/`・`frontend/src/api/prg.ts`・`frontend/src/App.tsx`・`frontend/src/App.test.tsx`・`frontend/src/test/prgFixtures.ts`
- **覆す場合:** 算定項目を読む API ができたら `EntryEditor.tsx` の `ProgressItemsEditor` の初期値をその応答にし、注記を外す。確定の確認の段は `StatusReportPage.tsx` の `confirming`
- **限界:** 試験は fetch を差し替えた画面の試験だけで、実際のサーバと繋いでは確かめていない。見た目（CSS）は付けていない。最初の報告日を作る入口は無い（OQ-37 (2)）。
  一覧は報告日の全ての行を 1 つの表に出す（活動 2,000 件の表示の速さは計っていない）。是正措置（UI-CHG-002）の画面は ui:CHG の範囲で、できるまでリンク先は「ページが見つかりません」

## A-42: 変更管理の画面（ui:CHG）の UI-CHG-001〜003 で決めたこと

- **選択:**
  - 置き場は `frontend/src/pages/chg/`（UI-CHG-001 は `ChangeRequestsPage.tsx` と起票・手直しの `ChangeRequestForm.tsx`・変更内容の `OperationEditor.tsx`・
    影響分析の `ImpactView.tsx`、UI-CHG-002 は `CorrectiveActionsPage.tsx` と登録の `CorrectiveActionForm.tsx`、UI-CHG-003 は `AuditLogsPage.tsx`）。
    API の型と呼び出しは `frontend/src/api/chg.ts`（API-CHG-001〜010。形は `backend/app/schemas/change.py` と `backend/app/domain/change.py` の `to_json` に合わせた）
  - 部品は `GET /api/v1/me` の権限と 5.10 の状態で出し分ける（7.2）：起票・手直し・提出は change.create かつ DRAFT、反映は change.apply かつ APPROVED、
    影響分析は反映済み・却下以外（サーバが 409 を返す状態。A-37）。承認・却下は UI-CMN-003 で行い、提出済みの変更要求からは承認待ち一覧へ案内する。
    取り下げ（5.10 の SUBMITTED → DRAFT）は呼ぶ API が無いので部品を置かない（OQ-38 (1)）
  - 変更内容（5.9）は JSON の手書きではなく、操作を 1 件ずつ組み立てて足す（`operations.ts` の `buildOperation`）。画面が止めるのは必須の欄の空と数の形
    （期間・活動の ID は整数、見積は 0 以上の数）だけで、参照の解決・業務ルールはサーバ（RULE-CHG-001）の 400・422 の文言を出す。
    `update_*` の `fields` は入力した項目だけを入れる（空欄は「変えない」。値を空に戻す操作は画面からは作れない）
  - 影響分析は「試算（本計画は変えない）」と「提出のときの影響分析」（`change_requests.impact`）を見出しで区別する。総工数の差は前後の値（小数 2 桁の文字列）から出す
  - 是正措置：登録の紐付けの選択肢は WBS の木（API-WBS-001）・活動（API-SCH-001）・報告日（API-PRG-002）・メンバー（API-PRJ-009）を、action.manage を持つ人にだけ読む。
    状態の選択肢は RULE-CHG-005 の遷移（今の状態のままと、次に進める・戻せる状態）だけを出す。更新は状態・効果・担当・期限を全て送る（PATCH。A-37 の更新できる項目）
  - 変更履歴：検索の条件は期間（両端を含む）・変更者（メンバーから選ぶ）・対象の種類（表の名前）・対象の ID・WBS ID。期間が逆・対象の ID が整数でないときは送らない。
    更新は変わった項目だけ、追加は後の値、削除は前の値を出す（RULE-CHG-006 の形）。変更・削除の部品は置かない（4.2・NFR-05）。
    audit.view が無い人にはサーバの 403 の文言を出す（部品を隠すのではなく、読む画面そのものなので）
  - 作成者・担当・変更者の名前はメンバーの一覧（API-PRJ-009）から引き、居ない人は `#ID` で出す
- **理由:** 設計書 5.9・5.10・6.2（API-CHG-001〜010）・7.1・7.2・9 章、要求仕様書 CHG-01〜04・06・09、NFR-05
- **影響範囲:** `frontend/src/pages/chg/`・`frontend/src/api/chg.ts`・`frontend/src/App.tsx`・`frontend/src/App.test.tsx`・`frontend/src/test/chgFixtures.ts`
- **覆す場合:** 取り下げの API ができたら `ChangeRequestsPage.tsx` の SUBMITTED の部品に足す。依存の端の解釈は `operations.ts` の `activityEndpoint`
- **限界:** 試験は fetch を差し替えた画面の試験だけで、実際のサーバと繋いでは確かめていない。見た目（CSS）は付けていない。
  紐付けの選択肢の活動・報告日は先頭の 1000 件まで（OQ-38 (3)）。変更内容の操作は足した順に並び、並べ替えの部品は無い（外して足し直す）

## A-43: 工数の画面（ui:EFF）の UI-EFF-001・002 で決めたこと

- **選択:**
  - 置き場は `frontend/src/pages/eff/`（UI-EFF-001 は `TimesheetPage.tsx`、UI-EFF-002 は `EffortPage.tsx`、週・入力の解釈・呼び名の小さな計算は `labels.ts`）。
    API の型と呼び出しは `frontend/src/api/eff.ts` に API-EFF-002〜007 を足した（形は `backend/app/schemas/effort.py` に合わせた）。
    ヘッダに「タイムシート」への行き先を足した（7.2 のログイン → タイムシート）。工数集計への行き先はプロジェクト一覧に既にある
  - タイムシート：表示する週は問い合わせの `?week=YYYY-MM-DD`（その日を含む週）。無い・読めないときは端末の今日の週。週は月曜始まり（OQ-39 (2)）。
    行は /me で effort.enter_own を持つプロジェクトごとの自分の割当（API-EFF-002 の `user_id`＝自分。OQ-39 (1)）を、プロジェクトの順→割当の順に並べ、
    割当に無いが実績のある活動は読むだけの行として末尾に足す（OQ-39 (4)）。実績は API-EFF-004 で自分の行をその週の範囲で読む
  - 書き込みは変えたマスだけを API-EFF-003 にまとめて送る（空は 0＝行を消す。同じ値に戻したマスは送らない）。今のメモは時間を変えても送り返して残し、
    0 にしたマスはメモを送らない（0 のマスにメモは書けない。OQ-39 (3)）。応答の行で、送った日の行を置き換える
  - 画面の検査は入力の補助で、送る前に止めるのは「0 以上・小数 2 桁までの数でない」マスと「1 日の合計が 24 時間を超える」日（ちょうど 24 は通す。
    EFF-02・RULE-EFF-005）。正の検査はサーバで、拒まれたら（確定した報告日より前 409・割当でない 403・24 時間超 422 など）文言を出して入力を残す。
    合計は百分の 1 時間の整数で足す（小数の誤差を出さない）。保存していない入力がある間は週を移る部品を無効にする（取り消すか保存する）
  - 工数の集計と差異：軸（WBS・担当者・組織・週・月）・報告日（API-PRG-002。新しい順）・基準（API-SCH-012。版の新しい順）を選び、多軸集計（API-EFF-005）は
    計画・基準・実績・ETC・EAC と表示用の EAC−基準を別の列に、合計の行を末尾に出す。WBS の軸は親をたどった深さで字下げする。
    差異の分析（API-EFF-006）は軸に依らないので、報告日・基準を変えたときだけ読み直す。基準が無い 409 は差異の欄にだけ出し、集計は出す
  - 差異の理由（API-EFF-007）は variance.note を持つ人にだけ部品を出し、そのときだけ是正措置の一覧（API-CHG-008）を紐付けの選択肢として読む。
    理由は選んでいる報告日（選んでいなければ「報告日を指定しない分析」）の理由として書く（サーバが要素・報告日ごとに 1 行を上書きする）
- **理由:** 設計書 6.2（API-EFF-002〜007）・7.1・7.2・9 章、要求仕様書 EFF-02・EFF-07〜10、BR-11
- **影響範囲:** `frontend/src/pages/eff/`・`frontend/src/api/eff.ts`・`frontend/src/App.tsx`・`frontend/src/App.test.tsx`・`frontend/src/test/effFixtures.ts`
- **覆す場合:** プロジェクトをまたぐ割当の API ができたら `TimesheetPage.tsx` の割当の読み込みを置き換える。週の始まりは `labels.ts` の `mondayOf`
- **限界:** 試験は fetch を差し替えた画面の試験だけで、実際のサーバと繋いでは確かめていない。見た目（CSS）は付けていない。
  一覧は先頭の 1000 件まで（OQ-39 (5)）。タイムシートは 1 週ずつで、月の一覧や前週の写しの部品は無い

## A-44: 入出力の画面（ui:INT）の UI-INT-001 で決めたこと

- **選択:**
  - 置き場は `frontend/src/pages/int/`（`ImportExportPage.tsx`。呼び名・6.3 の列の見せ方・拡張子からの形式の推定は `labels.ts`）。
    API の型と呼び出しは `frontend/src/api/int.ts`（API-INT-001・002。形は `backend/app/schemas/io.py`・`backend/app/api/io.py` に合わせた）。
    ファイルの受け取り・送りは JSON の `apiFetch` と同じエラーの扱いを `frontend/src/api/client.ts` の `send` にまとめ、`apiDownload`・`apiUpload` を足した
    （`apiFetch` の振る舞いは変えていない）。保存は `frontend/src/lib/download.ts` の `saveBlob`（`<a download>`）。プロジェクト一覧からの行き先は既にある
  - 出力・取込の部品は、/me のそのプロジェクトの権限 data.export・data.import を持つときだけ出す（7.2）。両方無ければその旨を出す
  - 取込は「検査する」（`dry_run=true`。誤りがあっても 200 で `ok` が偽）と「取り込む」（`dry_run=false`）の 2 つのボタン。
    取り込む前に検査を必須にはしない（サーバが全行を先に検査し、誤りが 1 件でもあれば何も書かないため。6.3）
  - 誤りは `{行, 列, コード, メッセージ}` の表に出す。422 `IMPORT_INVALID` のときは `details` を同じ表に出し、alert には文言だけを出す（箇条書きと重ねない）
  - 形式は利用者が選び、ファイルを選んだときに拡張子（.csv・.xlsx。大文字も可）で選び直す。送る本体の型は csv が `text/csv`、xlsx が xlsx の型
    （サーバの試験 `backend/tests/api/test_int.py` と同じ）
  - 対象・形式・ファイルを変えたら、前の検査・取込の結果を消す（別のファイルの結果を見せない）。ファイル名はサーバの Content-Disposition、
    無ければサーバと同じ形 `project-<ID>-<target>.<format>`
  - 6.3 の列の並びと照合キーを画面に出す（見せるだけ。正の定義はサーバの `backend/app/domain/tabular.py`）
- **理由:** 設計書 6.2（API-INT-001・002）・6.3・7.1・7.2・9 章、要求仕様書 INT-01
- **影響範囲:** `frontend/src/pages/int/`・`frontend/src/api/int.ts`・`frontend/src/api/client.ts`・`frontend/src/lib/download.ts`・`frontend/src/App.tsx`・
  `frontend/src/test/mockApi.ts`（送った本体そのものを `rawBody` に残す）・`frontend/src/test/download.ts`
- **覆す場合:** 取込の前に検査を必須にするなら `ImportExportPage.tsx` の `ImportSection` で、検査を通った同じファイル・対象・形式のときだけ「取り込む」を有効にする
- **限界:** 試験は fetch・`URL.createObjectURL`・`<a>` の click を差し替えた画面の試験だけで、実際のサーバ・ブラウザの保存とは繋いでいない。
  列の定義の表示はサーバの定義と手で揃えたもので、機械で突き合わせていない。見た目（CSS）は付けていない

## A-45: レポート・共有の画面（ui:RPT）の UI-RPT-001・002 で決めたこと

- **選択:**
  - 置き場は `frontend/src/pages/rpt/`（UI-RPT-001 は `MeetingReportPage.tsx`、UI-RPT-002 は `SharedViewPage.tsx`、7.4 の本体は両方が使う `MeetingReportView.tsx`、
    共有リンクの部品は `ShareLinkPanel.tsx`、呼び名・書式・印刷の CSS は `labels.ts`）。API の型と呼び出しは `frontend/src/api/rpt.ts`（API-RPT-001〜004。
    形は `backend/app/schemas/report.py` に合わせた）。ルートは `frontend/src/App.tsx`：UI-RPT-001 はログインが要り、UI-RPT-002 は `RequireAuth` で囲まない（6.1）
  - **7.4 の欄の値と並びはサーバ（RULE-RPT-001）のまま出す**。画面で数え直さない：遅延一覧はサーバの順（遅れの大きい順）、完了見込みは「入力」「計算」を付ける、
    マイルストーンのずれは UI-PRG-002 と同じ「+N 稼働日」／「ずれなし」、KPI は求められない値（null）をその理由とともに出す。値の源（写し／計算値）を見出しの下に出す
  - 上段のガントは UI-SCH-002 の描画（`frontend/src/components/gantt/GanttChart.tsx`）を使い、2 つの任意の引数を足した：`fixedRange`（横軸を報告日の前後 4 週に固定し、
    範囲の外へ出るバー・線を切り取る）と `reportLightning`（API-RPT-001 の頂点は行の番号で今のガントの行へ当てる）。どちらも渡さなければ今までの描画のまま（UI-SCH-002 の試験で確かめた）
  - 会議レポートは紙の 1 枚に載せるため、ガントの縦の仮想化をせずに全ての行を描く（行は絞らない。A-35）。表示の単位（日・週・月）は選べる（既定は日）
  - 印刷用の CSS は `@page { size: A3 landscape; }` を含む `<style>` を本体に置く（CSS の仕組みが画面に無いため。本体を出している間だけ効く）。
    印刷ではヘッダ・行き先・設定・共有リンクを出さず、ガントを紙の幅に縮め、下段を 3 列に並べる。「印刷する」はブラウザの印刷（`window.print`）を呼ぶ
  - **共有リンクの作成・失効は UI-RPT-001 の下に置き、share.manage を持つ人にだけ出す**（7.2。OQ-40 (1)）。有効期限は端末の時刻の日時を UTC の時差つきの時刻にして送り、
    既定値は置かない（OQ-40 (2)）。トークンは作った応答でだけ返るので、URL（`<origin>/shared/<token>`）は作ったときにだけ見せる。失効は戻せないので確認の段を 1 つ挟む。
    一覧の API が無いので、失効させられるのはこの画面で作ったリンクだけ（OQ-36 (7)・OQ-40 (3)）
  - 共有ビューはリンクの範囲（GANTT／REPORT）だけを読むだけで見せ、書き込みの部品を置かない。閲覧できないリンクはサーバの 404 の文言に、`details` の `reason`
    （NOT_FOUND・REVOKED・EXPIRED）の説明を添えて出す
  - 7.2 の遷移（進捗入力 → 会議レポート）のため、UI-PRG-001 の行き先に UI-RPT-001 へのリンクを足した
- **理由:** 設計書 6.1・6.2（API-RPT-001〜004）・7.1・7.2・7.3・7.4・9 章、要求仕様書 RPT-01・RPT-03。既にある画面の部品（ガントの描画・遅延分析の書式・/me の権限）を使い、同じ描画を二重に持たない
- **影響範囲:** `frontend/src/pages/rpt/`・`frontend/src/api/rpt.ts`・`frontend/src/components/gantt/GanttChart.tsx`・`frontend/src/App.tsx`・`frontend/src/App.test.tsx`・
  `frontend/src/pages/prg/StatusReportPage.tsx`（行き先のリンク）・`frontend/src/test/rptFixtures.ts`
- **覆す場合:** 共有リンクの部品を別の画面へ移すなら `ShareLinkPanel` をその画面に置き、`MeetingReportPage.tsx` から外す。上段の行を範囲で絞るなら、サーバ（A-35）に絞った行を足してから
  `MeetingReportView.tsx` の `GanttChart` に渡す行を変える。有効期限に既定値を置くなら `ShareLinkPanel.tsx` の `expiresAt` の初期値（本番値は要求元が決める）
- **限界:** 試験は fetch・`window.print` を差し替えた画面の試験だけで、実際のサーバ・ブラウザの印刷とは繋いでいない（A3 横の 1 枚に収まるかは見ていない。行が多いと収まらない。OQ-40 (5)）。
  見た目（印刷以外の CSS）は付けていない。前に作った共有リンクを画面から失効させる口は無い（OQ-40 (3)）


## A-46: WBS の画面（ui:WBS）の UI-WBS-001〜004 で決めたこと

- **選択:**
  - 置き場は `frontend/src/pages/wbs/`（UI-WBS-001 は `WbsPage.tsx`＋要素の編集 `ElementEditor.tsx`・採番と改番 `NumberingPanel.tsx`、
    UI-WBS-002 は `WbsElementPage.tsx`＋横断照会 `TracePanel.tsx`、UI-WBS-003 は `WbsReviewPage.tsx`＋`ReviewPanel.tsx`・`ScopeBaselinesPanel.tsx`、
    UI-WBS-004 は `ResponsibilityPage.tsx`、見積の記入は UI-WBS-001・002 が共有する `EstimateForm.tsx`、呼び名は `labels.ts`）。
    API の型と呼び出しは `frontend/src/api/wbs.ts`（API-WBS-001〜023 のうち画面が呼ぶもの）と `frontend/src/api/est.ts`（API-EST-001・002）。
    形は `backend/app/schemas/wbs.py`・`estimate.py` を読んで合わせた。木の型（`WbsTree`）に `numbering_rule`・`validation` を足した（サーバは前から返している）
  - **ツリー図とアウトラインは同じ木（API-WBS-001）を 2 つの表示で切り替える**（WBS-04）。アウトラインは先行順の表（深さで字下げ）、ツリー図は入れ子の一覧（`role="tree"`）。
    どちらで選んでも同じ編集の部品が開く（「どちらからでも編集できる」）。集計値はサーバの値（RULE-EST-001）をそのまま出し、画面で数え直さない
  - 要素の変更（API-WBS-003）は**変えた項目だけ**を送る。移動（API-WBS-004）の移動先に自分・配下・WP を出さない（サーバも 422 で拒む）。位置は画面で 1 から数え、API へは 0 から送る。
    削除（API-WBS-005）は `window.confirm` で確かめ、子を持つ要素は配下の件数を示す（WBS-01）。追加（API-WBS-002）の親の選択肢に WP を出さない（WBS-02）
  - **見積は API-EST-001 で書く**（人時・人日、手法・前提・参照資料。EST-08 の履歴が残る）。API-WBS-003 の `own_estimate_hours` は画面から送らない（OQ-41 (4)）。
    入力は 0 以上・小数 2 桁までを画面でも止める（EST-01）
  - 採番規則（API-WBS-006）を変えても ID は変わらず、改番（API-WBS-007）で変わることを画面に書き、改番の結果（旧 ID → 新 ID）と新旧対応の一覧（API-WBS-008）を出す
  - UI-WBS-002：辞書は WBS-11 の 8 項目（＋区分）を並べ、WBS ID・名称・責任者・見積は要素の値（API-WBS-009）。WP で成果物か完了条件が空なら「承認を申請できない」と示す（WBS-13）。
    見積の履歴（API-EST-002）は古い順。記録の無い要素（404）は「記録はまだありません」にする。横断照会（API-WBS-023）は開いた要素の今の ID で自動で照会し、旧 ID でも照会できる。
    変更要求・是正措置は件数と ID を出して一覧の画面へ送る（個別の画面のルートが 7.1 に無い）
  - **スコープ項目の紐付け（API-WBS-013）は、今の紐付けを読む API が無いので、選んだ項目での丸ごとの置き換えであることを画面に書く**（OQ-41 (2)）
  - UI-WBS-003：**開いているレビューを URL の `?review=<ID>` で持つ**（レビューの一覧の API が無い。OQ-41 (1)）。始める（wbs.edit）・指摘を書く（wbs.review）・
    指摘の状態と対応を変える（wbs.edit。閉じたレビューでは出さない）を権限で出し分ける（9.2・7.2）。
    承認の申請（API-WBS-018）が 422 `WBS_NOT_READY` なら `details` の `{condition, count}` を前提ごとの件数で出す。
    判断（API-WBS-019）は承認待ち一覧（API-WBS-020）からこのプロジェクトの WBS の申請だけを出し、wbs.approve を持つ人にだけ判断の部品を出す。差戻しの呼び名は A-25 と同じ「差戻し」。
    判断の後と失敗（403 自分の申請・409 判断済み）の後は読み直す。100% ルールの許容幅は割合なので百分率で出す
  - UI-WBS-004：表の列はサーバの順（メンバーを加えた順、メンバー外は後ろに「（メンバー外）」）、マスは CSV（A-18）と同じ言い方（「責任者」「担当 N 人時」「責任者・担当 N 人時」か空）。
    CSV は API-WBS-011 の `format=csv` をそのまま保存する（画面で作らない）。責任者の割当は UI-WBS-001 で行い、この画面から行き先を出す
- **理由:** 設計書 6.2（API-WBS-001〜023・API-EST-001・002・API-PRJ-005・009）・7.1・7.2・5.2・5.3・5.10・9 章、要求仕様書 WBS-01・02・04〜08・10・11・13・16・20〜22、EST-01・02・08、PRJ-02
- **影響範囲:** `frontend/src/pages/wbs/`・`frontend/src/api/wbs.ts`・`frontend/src/api/est.ts`・`frontend/src/App.tsx`・`frontend/src/App.test.tsx`・`frontend/src/test/wbsFixtures.ts`
- **覆す場合:** レビュー・紐付けを読む API が 6.2 に足されたら、`ReviewPanel.tsx` の番号で開く部品を一覧に、`WbsElementPage.tsx` の紐付けの初期値を読んだ値に替える。
  ツリー図を箱と線の図にするなら `WbsPage.tsx` の `TreeBranch` を描き替える（`buildTree` はそのまま使える）。見積を API-WBS-003 で書くなら `ElementEditor.tsx` の `EstimateForm` を外す
- **限界:** 試験は fetch・`window.confirm` を差し替えた画面の試験だけで、実際のサーバとは繋いでいない。見た目（CSS）は付けていない（ツリー図は入れ子の一覧のまま）。
  前に始めたレビューを開くには番号が要り、今の紐付けは画面に出ない（OQ-41）。木の要素が多いときの描画の速さは計っていない（仮想化していない）


## A-47: 独立レビュー（2026-11-12）で見つけた欠陥のうち直したもの

- **経緯:** run_20261110T094851_c358e7 の review ノードは max_turns（40）で止まった（途中までの指摘は `.kloop/runs/…/nodes/review/findings.md`）。
  残りを 4 つの観点（画面・ドメイン・API の権限と遷移・テーブルと移行）で読み直した。設計書から外れて誤ったデータを書く・漏らす・500 になるもので、
  設計書の記載だけで直し方が決まるものを直した。直し方が設計書で決まらないものは Change Request（CR-004〜009）と OQ-42 に残した
- **選択:**
  1. **一括の文も変更履歴に残す**（NFR-05・6.1「全てのモデルの追加・更新・削除」）：`backend/app/infra/audit_listener.py` に `do_orm_execute` を足し、
     `session.execute(update(...))`・`delete(...)` は文の条件に当たる行を流す前に読み、削除はその値、更新は流した後に読み直して変わった列を記録する（`_record_bulk`）。
     要素・活動の削除の後始末、担当の置き換え、レビューを閉じる処理が履歴に残る。A-37 の限界と OQ-33 (7) を改めた
  2. **変更要求の反映（API-CHG-006）は WBS が BASELINED のときだけ**（5.10 の「BASELINED へは承認（PENDING_APPROVAL → BASELINED）でだけ」）。
     DRAFT・IN_REVIEW・PENDING_APPROVAL では 409 `INVALID_STATE`（`details` に `wbs_status`）で何も書かない。反映した後は計画と新しい版が一致するので BASELINED のまま。OQ-33 (5) を改めた
  3. **影響分析（API-CHG-004）も委託先の見える範囲に絞る**（9.2 の注記）：一覧（API-CHG-002）に出ない変更要求は、委託先には見つからないもの（404 `NOT_FOUND`）として扱う
  4. **確定済みの報告日を指定した工数の集計は、確定時の進捗率を使う**（5.8「対象の報告日の progress_entries を使う」・BR-13）：`app/services/progress.py` の `percents_at_report`。
     OPEN は今までどおり入力から求め直し、FINALIZED は写し（`progress_snapshot_items`）の値。確定の後に足した活動は未算定。API-EFF-005・006 と会議レポートの KPI がこの値を読む
  5. **プロジェクトの既定の算定方式を変えたら、既定に従う活動の OPEN の報告日の進捗率を求め直す**（活動ごとに方式を変えたときと同じ。ガントの保存値とイナズマ線を揃える）：
     `refresh_open_entries_of_project`（`update_project` が `progress_method_default` を変えたときに呼ぶ）
  6. **xlsx の WBS ID の列（WBS ID・親のWBS ID・WPのWBS ID）が数のセルなら、その行を `INVALID_VALUE` にする**（RULE-INT-001・BR-02）。
     数のセルは表記を失う（`1.10` → `1.1`、`1.0` → `1`）ので、照合すると別の要素を黙って更新する。出力の xlsx は WBS ID を文字のセルで置くので、往復は今までどおり 0 件の変更
  7. **UI-WBS-002 は要素ごとに画面を作り直す**（ルートの `elementId` を `key` にした）。横断照会から別の要素へ移ったとき、前の要素のスコープ紐付けの選択・履歴の頁を持ち越して
     別の要素の紐付けを置き換えていた
  8. **活動・要素の直接の削除（API-SCH-005・API-WBS-005）も、是正措置・差異の理由が指す行は 422**（`ACTIVITY_REFERENCED`・`ELEMENT_REFERENCED`。変更要求の反映と同じ）。
     外部キー違反の 500 になっていた。スケジュールベースラインの写しだけが指す要素は、写しの要素の紐付けを空にして消す（活動を消すときと同じ。DB-SCH-004 の注記・A-19）
- **理由:** 設計書 5.8・5.10・6.1・6.3・9.2・NFR-05・BR-02・BR-13。どれも設計書の記載どおりにするだけで直し方が決まる
- **影響範囲:** `backend/app/infra/audit_listener.py`・`backend/app/services/{change,progress,effort,projects,wbs,schedule}.py`・`backend/app/domain/tabular.py`・
  `frontend/src/pages/wbs/WbsElementPage.tsx`、試験は `backend/tests/api/test_{chg,wbs,eff,prg}.py`・`backend/tests/domain/test_tabular.py`・`frontend/src/pages/wbs/WbsElementPage.test.tsx`
- **覆す場合:** 一括の文を記録しないなら `register_audit_listener` の `do_orm_execute` を外す。反映を DRAFT でも許すなら `apply_change_request` の状態の検査を外し、
  試験 `test_apply_requires_baselined_wbs` を直す。確定済みの報告日でも今の値で求め直すなら `load_basis` を `report_percents` に戻す。
  数のセルの WBS ID を受けるなら `CODE_COLUMNS` の検査を外す（表記の復元はできない）
- **限界:** 試験は SQLite の TestClient と画面の試験（fetch を差し替え）だけ。一括の文の記録は `Session` を通る文だけで、`engine`・`connection` へ直接流す SQL は記録しない。
  変更要求の反映は、スケジュールベースラインの写しが指す要素を今も 422 で拒む（直接の削除は写しの紐付けを空にする。どちらにそろえるかは CR-002・OQ-42 (9)）。
  直さなかった指摘は OQ-42 と CR-004〜009

## A-48: 設計書 1.1（CR-001〜009 を推奨案で承認。2026-11-12）の実装で決めたこと

- **経緯:** 利用者が CR-001〜009 を推奨案で承認し、設計書を 1.1 に改訂した（0.4 の改訂履歴）。作業木 `wt/cr-approved` で CR ごとに実装する。
  この項は CR ごとの解釈と限界を記す
- **選択:**
  1. **CR-005（RULE-EFF-005・API-EFF-003）**：実績工数は確定した報告日**以前**（当日を含む）の日を変えない（`LOCKED_DATE`）。取込の備考だけの変更も同じ境界。
     A-39 の「確定した報告日より前の日」を改めた
  2. **CR-006（API-INT-002）**：取込は data.import に加えて、wbs は wbs.edit、activities は schedule.edit、見積の列を変える行があれば estimate.edit も確かめる
     （dry_run でも同じ）。time-entries は data.import だけ（実績の入力の権限は RULE-EFF-005 の割当の検査が担う）。A-39 の権限の記述を改めた
  3. **CR-009（5.6・5.7・7.3）**：進捗率は `Fraction` で丸めずに求め（`exact_percent_complete`）、保存する値だけ小数 2 桁へ四捨五入する（`round_percent`）。
     P・ずれ・判定・表示日・完了見込み日の残りの日数は丸める前の値で求める。ガントの頂点は完了＝白いチェック、未着手（予定内）＝白い穴を緑に重ねる。
     A-23 の「割合を Numeric(5,2) の桁へ四捨五入してから P を求める」を改めた
  4. **CR-003（4.1 DB-WBS-002・5.2）**：第 1 階層の数えは `wbs_numbering_rules.top_last_child_seq`（既定 0）。移行 0009 は既にある行を第 1 階層の順番の最大で埋める。
     行の無いプロジェクトで行を作るときも最大から始める。追加・移動・改番・取込・変更の反映が読み書きする。A-17 の `_top_last_child_seq`（最大から数える）を改めた
  5. **CR-002（4.2）**：写しの列（`scope_baseline_elements.element_id`・`parent_element_id`、`schedule_baseline_items.activity_id`・`element_id`、
     `progress_snapshot_items.activity_id`）に外部キーを張らない（移行 0010）。SQLite では `wbs_elements`・`activities` を AUTOINCREMENT で作り直し、内部キーを再利用しない
     （PostgreSQL の identity・sequence は元から再利用しない）。版に入った要素・活動も消せる（`ELEMENT_IN_BASELINE` を外した）。消しても写しは変えない。
     A-17・A-37 の `ELEMENT_IN_BASELINE`、A-19・A-22・A-47 (8) の「写しの紐付けを空にしてから消す」を改めた
  6. **CR-007（5.8・RULE-EFF-002〜004）**：基準の値は版の項目から数える（`app/domain/effort.py` の `BaselineItem`・`_baseline_basis`）。
     - WBS の軸：WP は版のその WP の WORK_PACKAGE の項目（無く活動の項目があれば、その和）。上位は下位の合計、子の無い WP 以外の要素は 0
     - 担当者・組織・期間の軸：今もある活動の項目を、今の割当の比・項目の日付で配る。版に活動の項目を持たない今もある WP の項目は「未割当」・項目の日付
     - 桶「基準にあって今は無い」（API の `bucket_kind`＝`BASELINE_REMOVED`。どの軸でも最後の行、WBS の軸ではプロジェクトの直下）：
       WBS の軸は今は WP でない ID の版の WP の値。担当者・組織・期間の軸は、今は無い活動の項目と、版に活動の項目を持たない今は無い WP の項目。
       **今もある WP から消した活動の基準は、WBS の軸ではその WP の項目の中に残る**（設計書の「WP は版の WORK_PACKAGE の項目」のとおり）。
       活動を別の WP へ移してから元の WP を消すと、WBS の軸では元の WP の値が桶に、担当者の軸では活動の項目がその活動に置かれる。どちらの数え方でも合計は版の合計
     - 追加された要素（BR-11）：版に項目の無い活動・WP と、配下の WP が全て追加された上位の要素だけが `None`（差異は ADDED）
     - プロジェクトの基準は版の合計（会議レポートの KPI の基準もこの値）。A-28 の「基準の値は集計の葉で持ち、上位は基準にある下位だけを足す」を改めた
  7. **CR-004（5.10・6.2。サーバ）**：
     - API-WBS-024 レビューを閉じる：OPEN のレビューだけ（閉じたレビューは 409 `INVALID_STATE`）。WBS が IN_REVIEW でなければ
       RULE-WBS-006 の `CLOSE_REVIEW` が拒むので 409。WBS の承認申請（API-WBS-018）が OPEN のレビューを自動で閉じる今の動きは残す（5.10 の前提）。
       閉じる前に、応答（API-WBS-015 と同じレビューと指摘）を読めること（project.view）を確かめる。PM がロールの表（9.2）から project.view を
       外して wbs.edit だけのロールを作っても、閉じてから 403 を返さない（状態を変えずに 403）
     - **閉じたレビューの指摘も API-WBS-017 で更新できる**（新しい指摘を書く API-WBS-016 は開いているレビューだけ）。5.10 は「閉じても指摘は
       そのまま残る」「申請の前提は未対応の指摘 0 件」で、前提は閉じたレビューの指摘も数える。閉じたレビューの指摘を変えられないと、
       未対応の指摘を残して閉じた WBS は二度と申請できない（独立レビューの指摘）。6.2 の API-WBS-017 に「開いているときだけ」の定めは無い。
       A-17 の「閉じたレビューの指摘は変えられない（409）」と A-46 の「閉じたレビューでは出さない」を改めた（UI-WBS-003 も閉じたレビューの指摘に更新を出す）
     - API-WBS-025 レビューの一覧・API-RPT-005 共有リンクの一覧：新しい順（ID の降順）、6.1 の `limit`（既定 100・上限 1000）・`offset`。
       共有リンクの一覧は失効・期限切れのリンクも含める（状態は `revoked_at`・`expires_at` で分かる）
     - API-WBS-026・API-PRG-010：応答は置き換え（API-WBS-013・API-PRG-004）の応答と同じ形。委託先に見えない要素・活動は 404（9.2 の注記）
     - API-CHG-011 取り下げ：保留中（PENDING）の承認申請の行を ORM の削除で消し、変更履歴に DELETE を残す。`submitted_at` は空に戻し、
       `impact` は残す（提出し直すと流し直す）。応答の `approval_request_id` は残っている最新の承認申請（無ければ null）
     - 画面：UI-WBS-003 はレビューの一覧（50 件ずつ。`?review=` の URL で開く）と、wbs.edit を持つ人に「レビューを閉じる」。
       UI-WBS-002（スコープ項目の紐付け）・UI-PRG-001（算定項目。CR-004 の本文は UI-PRG-002 と書くが、編集の部品は UI-PRG-001 にある）は、
       今の値を読めるまで編集を出さない（置き換えなので、空から保存して今の値を消さないため）。紐付けを読むのは編集する人（wbs.edit）だけ。
       UI-CHG-001 は SUBMITTED に「提出を取り下げる」（change.create）。取り下げ・閉じるに確認の段は付けない（同じ画面の提出・反映に合わせた）。
       共有リンクの一覧は UI-RPT-001 の共有リンクの部品（作成・失効と同じ場所。OQ-40 (1)）に置く。11 章は RPT-03 の画面に UI-RPT-002 を挙げるが、
       UI-RPT-002 はログインしない閲覧の画面で share.manage を持たないので置かない。状態（有効・失効・期限切れ）は一覧を読んだ時刻で判定する
  8. **CR-008（6.2 API-SCH-010・7.3・7.4。サーバ）**：`range_start`・`range_end` は任意で、省いた端はバーの範囲の端。応答の `range_start`・`range_end` は
     **バーの範囲のまま**にし（意味を変えない）、`non_working_days` だけが表示範囲の分になる。バーが無くても両端を渡せばその範囲の非稼働日を返す。
     どちらかの端を渡したときだけ、始まりが終わりより後・長さが 3660 日（`MAX_GANTT_RANGE_DAYS`。業務の値ではなく応答の大きさの歯止め）を超える範囲を
     400 `VALIDATION_ERROR` にする（`details` の `loc` は渡した端。両方省いたバーの範囲には上限を掛けない＝1.0 と同じく常に 200）。
     片方だけ渡し、省いた端にバーが無ければ非稼働日は空。画面がバーの範囲より広く読み直して 3660 日を超えると 400 で、画面は前のガントのまま誤りを出す
     会議レポート（API-RPT-001・共有ビューの REPORT）は報告日の前後 4 週を渡す。共有ビューの GANTT はバーの範囲のまま
     - 画面：UI-SCH-001 は estimate.edit を持つ人に行ごとの「見積」を出し、API-EST-001 の ACTIVITY で書く（`EstimateForm` を対象の種類で一般化した）。
       活動の `estimate_hours` を書く経路は API-EST-001・変更要求・取込だけ（API-SCH-002〜004 は受けない。OQ-22 (1)）。
       UI-SCH-002 は status_report.manage を持つ人に報告日の作成の部品（UI-PRG-001 と共用の `CreateReportForm`）を出す（報告日が 0 件でも）。
       非稼働日は、描く範囲（バーの範囲を報告日線・頂点・過去の線で広げたもの。`chartExtraDates`）が前回受け取った範囲に収まらないときだけ、
       描く範囲を渡して API-SCH-010 を読み直す（バーより広いときに 1 回増える。狭まっても読み直さない）。ガントのデータは API-SCH-010 だけで取る（NFR-02）
  9. **CR-001（1.1 の表と注）**：react-router-dom 7.18.5・vite 6.4.6・vitest 4.1.12・@vitejs/plugin-react 4.7.0 に上げた（`--save-exact` で版を固定）。
     どれも主版の中の最新ではなく、設計書の下限（7・6・4）を満たし `npm audit` の勧告が無い（high 以上だけでなく全件 0。`--omit=dev` でも 0。2026-11-12）版。
     Vite は 7 にしない（設計書の下限は 6 で、@vitejs/plugin-react 4 の対応範囲の中で足りる）。React 18・Testing Library・jsdom は変えない。
     React Router 7 は v6 の `future`（`v7_startTransition`・`v7_relativeSplatPath`）が既定になり指定を受けないので、`main.tsx` と試験の
     `MemoryRouter` から外した（振る舞いは 6 で両方を有効にしていた今までと同じ）。Vitest 4 は `minWorkers` を持たないので外し、ワーカーの上限
     `maxWorkers: 2`（A-24）は残した。7.1 のルートの書き方は変えていない
- **理由:** 設計書 1.1 の 0.4・4.1・4.2・5.1・5.2・5.6・5.7・5.8・6.2・7.3。承認した CR の推奨案のとおり
- **影響範囲:** `backend/app/domain/{effort,progress,lightning,delay,snapshot,change,tabular,wbs_code}.py`、
  `backend/app/services/{effort,wbs,io,change,schedule,progress,report}.py`、`backend/app/api/{effort,wbs}.py`・`backend/app/schemas/effort.py`、
  `backend/app/models/{wbs,sch,prg,eff}.py`、移行 0009・0010、`frontend/src/components/gantt/`・`frontend/src/pages/eff/`・`frontend/src/api/eff.ts`、
  `frontend/package.json`・`frontend/package-lock.json`・`frontend/vite.config.ts`・`frontend/src/main.tsx`。
  試験はそれぞれの `backend/tests/`・`frontend/src/` の試験
- **覆す場合:** 設計書を 1.0 へ戻すことになる（Change Request を起票する）。CR ごとの戻し方はそのコミット（`git log --grep CR-00`）の差分を戻す。
  移行 0010 は、今は無い要素・活動を指す写しがあれば戻せない（止まる）
- **限界:** 試験は SQLite の TestClient・ドメインの試験・画面の試験（fetch を差し替え）だけ。PostgreSQL での移行 0009・0010 は流していない。
  - CR-009：確定した報告日の遅延一覧は、保存した小数 2 桁の進捗率から求める（丸める前の値を持たない）。今の値から求める遅延一覧とは小数 2 桁未満でずれうる
  - CR-002：1.1 より前に消した要素・活動の写しは紐付けが空のまま（戻せない）。CR-007 の数え方では、空の `element_id` の WP の項目をまとめて 1 つの「今は無い WP」として数える。
    空の WP の項目と空の活動の項目が両方あると、担当者・組織・期間の軸の桶から WP の項目を外す（WBS の軸と合計がずれうる）。本番のデータはまだ無いので起きない見込み
  - CR-007：版の WP の項目と、その WP の活動の項目の和が食い違う版（`save_baseline`・再基準化以外で作った版）では、WBS の軸と他の軸の基準の合計がずれる（検査しない）
  - CR-004：SQLite は消した最大の ID を使い直すので、取り下げて提出し直した承認申請が前と同じ ID になりうる（変更履歴では同じ `entity_id` に
    CREATE・DELETE・CREATE が並ぶ。OQ-42 (10)）。PostgreSQL では起きない。同じ理由で SQLite では、取り下げた古い承認申請の ID へ送った判断が
    提出し直した申請に当たりうる（開発・試験の DB だけ）。取り下げと承認の判断が同時に走ったときの行ロックは無い（コード全体で `with_for_update` を
    使っていない今の型のまま。PostgreSQL で APPROVED を DRAFT で上書きしうる。流していない）
  - CR-001：確かめたのは型検査・画面の試験（jsdom）・`vite build` まで。ブラウザで開発サーバ・本番の配布物を動かしてはいない。
    react-router 7・vitest 4 は Node 20 以上を要る（lock の engines）。この VM は Node 24.13.0。`package.json` に engines を置かず、
    `scripts/setup-dev.sh`（保護パス）も Node の版を確かめない。
    `npm audit` の結果はその日の勧告の一覧によるので、後から勧告が出れば同じ注（1.1）に従って版を上げ直す

## A-49: 未確定事項 OQ-08・09・10・38・42 を推奨案で決めたこと（2026-11-13）

- **経緯:** 利用者が OPEN の 5 件（OQ-08・09・10・38・42）を推奨案で決めるよう指示した（2026-11-13）。設計書の変更が要るものは CR-010〜013 に起票して
  承認とし、設計書を 1.2 にした（D-10）。この項は、設計書を変えずに実装で決めるものと、CR ごとの実装の解釈を記す。作業木 `wt/oq-open` で実装する
- **選択（設計書 1.2 の CR）:** CR-010 → OQ-08・09・10・OQ-42 (4)(5)(20)。CR-011 → OQ-42 (1)(2)(6)。CR-012 → OQ-42 (10) と 2026-11-12 の追加の (23)
  （閉じたレビューの指摘。A-48 (7) で実装済み）。CR-013 → OQ-42 (15)
- **選択（設計書を変えずに実装で決めるもの）:**
  1. OQ-38 (2)：変更内容の依存の端は、画面で「今ある活動（一覧から選ぶ。ID を送る）」か「この変更で足す活動の仮の名前（文字列を送る）」かを選ばせる。
     API は JSON の型で区別する（整数＝ID、文字列＝`ref`。`domain/change.py` の `resolve_activity`）ので、数字だけの仮の名前も使える
  2. OQ-38 (3)・OQ-42 (21) の一覧の上限：選択肢・一覧に要る資源（活動・報告日・承認待ち・メンバー）は、6.1 の `limit` 1000 で `total` まで頁を読み足す
     （活動は NFR-01 で 2,000 件まで）
  3. OQ-42 (3) 同時実行：状態を変える操作は、対象の親の行を `with_for_update()` で読んでから判定する（WBS の承認申請・レビュー＝`projects`、報告日の入力・確定＝
     `status_reports`、変更要求の提出・取り下げ・判断・反映＝`change_requests`・`approval_requests`）。一意制約の違反は 500 でなく 409 `CONFLICT`。
     採番規則の行はプロジェクトを作るときに作り、GET では作らない（無い既存のプロジェクトは移行で作る）。SQLite は `FOR UPDATE` を無視するので、試験は判定の順と 409 だけを見る
  4. OQ-42 (8) 小数：API の入力で列の小数の桁を超える値は 400（`remaining_hours` など 2 桁の列。`weight` は 0.01 以上）、許容幅の設定は列の上限（`Numeric(6,2)` の 9999.99）まで
  5. OQ-42 (11)：JSON の列（`audit_logs.before`・`after`・`change_requests.impact` など）の None は SQL の NULL で持つ（`none_as_null`）。既にある JSON の null は移行で NULL にする
  6. OQ-42 (12)：状態と決めた人・時刻の組の CHECK を `approval_requests`・`change_requests` にも置く（5.10 の状態から導く）
  7. OQ-42 (13)：移行とモデルの一致の試験に CHECK と既定値（server_default）の比較を足す
  8. OQ-42 (14)：63 文字を超える制約名を短い名前にし、移行で付け直す（PostgreSQL は 63 文字で切るため）
  9. OQ-42 (16)：変更要求の `requested_start` も BR-04 で稼働日へ寄せる（API-SCH-002・003 と同じ）
  10. OQ-42 (17)：API 層はモデルを import しない（サービスに移す）。サービスが他のサービスの非公開の関数を使う所は、共用の公開の関数にする（1.3）
  11. OQ-42 (18)：`# RULE-…` の宣言は 1 か所だけにし、ほかは行頭に ID を書かない参照にする（2.3）
  12. OQ-42 (19)：共有リンクの 404 は失効・期限切れの理由を返さない（無いリンクと同じ応答）
  13. OQ-42 (21)：進捗の入力は progress.enter_own だけの人には自分の割当の活動だけに出す（API-SCH-001 の `assignees`）。ガントは今の行に無い頂点の数を注記する。
      タスクリストは保存前の編集があれば頁を移る前に確かめ、依存の相手は全ての活動から選ぶ。メンバーを足す候補から全ての頁のメンバーを除く
  14. OQ-42 (22)：活動を消すと確定済みの報告日の入力も消す今の動きを残す。確定した値はスナップショット（写し）が持ち、過去の線と確定済みのレポートは写しから描く
      （BR-13 は写しで満たす。CR-002 の「写しが過去を持ち、今の行は消せる」と同じ考え）
  15. OQ-42 (24)（2026-11-12 の追加）：取り下げと判断の同時実行は 3. で、SQLite の ID の再利用は CR-012 で扱う
  16. OQ-42 (25)（2026-11-12 の追加）：`frontend/package.json` に `engines`（Node 20 以上）を置き、`frontend/.npmrc` の `engine-strict=true` で古い Node の `npm ci` を止める
     （`scripts/setup-dev.sh` は保護パスなので変えない）
- **理由:** 設計書 1.2 と 6.1・1.3・2.3・BR-04・BR-13・NFR-01。利用者の推奨案での決定
- **影響範囲:**
  - CR-010（wt/oq-g1）：models/cmn.py（users の 3 列・CHECK failed_login_count >= 0）・移行 0011_users_auth・config.py（3 設定）・services/auth.py
    （ロックの判定・principal_of の project.create・API-CMN-007 の 422・API-CMN-008）・services/users.py（方針の検査・初期管理者）・services/project_access.py
    （project_ids）・schemas/users.py・api/deps.py・auth.py・users.py・main.py。画面 LoginPage・UserAdminPage・RolePermissionsSection・api/cmn.ts
    - 実装の判断：システム管理者の API-CMN-008 は全てのプロジェクト（メンバーでなければ roles は空、権限は全て・view_level ALL）。初期管理者のパスワードは
      利用者が既にいても起動時に確かめる（反すれば起動しない）。無効の利用者の誤りも数える。ロック中は now < locked_until。ログインの利用者の行を
      with_for_update で読む。401 の文言は「ログイン名またはパスワードが違うか、しばらくログインできません」。パスワードを設定し直すとロックを解く（本人の変更でも）。
      作成では方針の検査（400）がログイン名の重複（409）より先
  - 1・2・13・16（wt/oq-g5）：api/pages.ts の fetchAllPages（読む間に total が変われば画面で 409 LIST_CHANGED を作って読み直しを促す）・lib/leaveGuard.ts・
    chg/operations.ts ほか・ApprovalsPage（100 件の頁送りをやめて全件）・WbsReviewPage・StatusReportPage・EffortPage・GanttPage・ActivityListPage・MembersSection・
    CorrectiveActionsPage・useProjectMembers・gantt/scale.ts（unplacedVertexCounts）・package.json の engines（`^20.0.0 || ^22.0.0 || >=24.0.0`。vitest 4 の
    engines に合わせ、「Node 20 以上」のうち依存が動く版に絞った）・frontend/.npmrc・package-lock.json の root の engines
  - CR-013・9〜12（wt/oq-g4）：domain/report.py（マイルストーンを最新計画のガントの行から求める）・services/report.py（確定済みでも日程が未計算なら 409。
    写しの判定を Judgement に戻す＝既存の不具合の修正：確定済みのレポートの KPI の遅れの件数が常に 0 だった）・MeetingReportView の注記・domain/change.py
    （roll_requested_starts。起票・更新の時点で寄せる）・api 層のモデルの import をやめる（サービスで型を再公開）・サービス間の非公開の関数を公開の名前に
    （numbering_rule_row・load_wbs_context・coded_element・mark_wbs_edited・preorder_elements・project_hours_per_day・visible_scope）・RULE の宣言を 5.1 の実装先の
    40 か所に 1 つずつ・共有リンクの 404 を 1 つの応答に
  - CR-011（wt/oq-g2）：services/wbs.py（見える範囲の文脈で検査・木の集計・責任分担表・範囲のベースラインの件数）・estimate.py（見える分へ絞ってから集計）・
    schedule.py（依存の相手・ベースラインの項目の件数）・progress.py（entry_count・見えない活動の 404・approved の権限）・effort.py（API-EFF-003 の見えない活動の 404）・
    画面 EntryEditor・StatusReportPage（承認の欄は progress.enter_all だけ）
    - 直した API：WBS-001・011・012・021、EST-003、SCH-001・012、PRG-002・006・009（report.entry_count）。404 にした API：PRG-003・004・EFF-003
      （判定の順は 404（無い・見えない）→ 403（自分の割当。全て読める人だけ）→ 409 → 400）
    - approved の「変える」：id で既存の項目と対応づけ、保存済みと違う値、または足す項目に真を置くこと。同じ値は変えるに数えない。id の誤り（400）の後に判定し、
      1 件でもあれば 403 で何も書かない（details に loc ["body","items",i,"approved"]）
  - CR-012 の 1・3（補い）・4〜8（wt/oq-g3a）：移行 0012_db_integrity（SQLite の 33 表を AUTOINCREMENT で作り直す・JSON の null を NULL に・採番規則の行の補い・
    CHECK 3 つ・FK の名前の付け直し。新しい CHECK に反する行や NOT NULL の JSON 列の null があれば、直さずに止まる）・models の 8 ファイル（全ての表に
    sqlite_autoincrement、JSON の 9 列を none_as_null、CHECK、短い FK 名）・schemas/numeric.py（numeric_column）・schemas/progress.py・projects.py
    - CHECK：`ck_approval_requests_decided_pair`（PENDING は判断者・時刻が空、APPROVED・REJECTED は両方あり）、`ck_change_requests_submitted_at`（DRAFT だけ空。
      取り下げで空に戻す今の動きに合わせた）、`ck_change_requests_applied_pair`（APPLIED だけ反映者・時刻あり）
    - 小数の桁を確かめる入力：percent_complete・remaining_hours（2 桁）、weight（2 桁・0.01 以上）、delay_tolerance_days・delay_extract_days（2 桁・9999.99 以下）。
      誤りは 400 VALIDATION_ERROR（type は decimal_max_places・decimal_whole_digits。既存の Decimal の入力と同じ）
    - 付け直した名前：`fk_schedule_baseline_items_schedule_baseline_id`（元は SQLite で 66 文字の名前、PostgreSQL で SQLAlchemy が縮めた
      `fk_schedule_baseline_items_schedule_baseline_id_schedul_ee77`）
  - 3（wt/oq-g3b）：infra/db.py（lock_row・lock_rows・is_unique_violation）・main.py（IntegrityError のうち一意制約の違反だけを 409 CONFLICT。CHECK・外部キー・
    NOT NULL は今どおり 500）・services/wbs.py（numbering_rule_row は読む経路で書かず既定の値を返し、書く経路はロックして無ければ作る。読む経路の commit を除いた）・
    change.py・progress.py・io.py。ロックの順は change_requests → projects → wbs_numbering_rules → approval_requests（status_reports は単独）
    - ロックする操作：WBS の編集（API-WBS-002〜005・007・010・013、取込）・採番規則の変更・レビュー（開く・閉じる・指摘を書く・更新）・申請・判断、変更要求の修正・提出・
      取り下げ・反映、報告日の入力・確定・算定方式の変更による求め直し。依頼の範囲（状態を変える操作）より広く、採番の順を揃えるため WBS の全ての編集をロックした
    - 一意制約の見分け：PostgreSQL は sqlstate 23505、SQLite は sqlite_errorname が SQLITE_CONSTRAINT_UNIQUE・PRIMARYKEY
  - CR-014（wt/oq-open）：services/progress.py（`replace_progress_items` の CR-011 の判定と 1 つにし、id の誤りの 400 の後、progress.enter_all を持たないときだけ判定する。守る項目は保存済みの値で承認済みか WORK のもの）・services/projects.py（`create_project` の同じトランザクションで、システム管理者でなければ作った人を `roles=["PM"]` のメンバーにする。所属は空）・画面 EntryEditor（保存済みの承認済みの項目と作業項目は、enter_own だけの人には種類を文字で出し、外すボタンを出さない）
    - 403 の details：送った項目は `{"loc": ["body","items",i,"approved"|"item_type"]}`、消した項目は `{"loc": ["body","items"], "progress_item_id": id}`。未承認で作業でない項目を WORK に変えるのは許す（率を下げる向きにしか動かない）
  - 独立レビューの指摘（wt/oq-open）：services/estimate.py（API-EST-001 は権限の確認の直後にプロジェクトの行をロックしてから WBS の状態を読む）・services/report.py（共有リンクの会議レポートの 409 の details に活動の ID を載せない）
- **覆す場合:** CR の分は設計書を 1.1 に戻すことになる（Change Request を起票する）。実装の分は、その項目のコミットの差分を戻す
- **限界:**
  - PostgreSQL では流していない（FOR UPDATE・CHECK・移行）
  - CR-010：無いログイン名は DB に書かないので、応答の時間がわずかに違いうる。既にある短いパスワードの利用者はそのままログインできる（方針は設定するときに効く）。
    システム管理者の API-CMN-008 はプロジェクトごとに権限を読む（件数が多いと遅い。未計測）。`PMT_INITIAL_ADMIN_PASSWORD` が 12 文字未満の環境はこの版から起動しない
  - 13 c：BrowserRouter のため useBlocker を使えず、ブラウザの戻る・進む、コードからの移動（ログアウト）、画面の中で保存して読み直す操作では確かめない
  - 2：利用者の一覧（prj/activeUsers.ts）・ベースライン・スコープは先頭 1000 件のまま（A-49 の 2 の対象外）
  - 13 b：会議レポート（MeetingReportView）は行の番号が当たらない頂点を数えない（今の行に無い活動の数だけ）
  - 9：既に保存された非稼働日の requested_start は移行で寄せない（日程の計算では次の稼働日として扱う）
  - 10：API 層がドメインを直接 import する所は残る。サービス・モデル・infra の docstring に行頭の RULE の参照が残る（検証器の対象外）
  - 12：共有リンクの応答の時間の差は扱わない
  - CR-013：遅延一覧の予定・見込みの終了日は今の計画、ずれは写し（確定済み）
  - CR-011：委託先の WBS の検査（SUM_MISMATCH など）は見える子だけで比べるので、全体では出ない差異が委託先にだけ出ることがある（9.2 の注記の字句どおり）。
    件数は今の割当・今の WP の置き場で数える。書く権限（wbs.edit など）を委託先に与えたときの書き込みの応答は絞らない。progress.enter_own の人が承認済みの項目を
    消す・名前や重みを変えるのは「approved を変える」に数えない。変更要求（API-CHG-002・004）の保存した影響（impact）と操作は絞らない（OQ-43）
  - CR-012：0012 より前に今の最大より大きい id を消していた表は、その id が 1 度だけ使い直されうる（記録が無い）。PostgreSQL での 0012 の実行・json_typeof の判定・
    元の FK 名は、SQL の描画とコードの読みで確かめただけ。大きな SQLite の DB で作り直す時間は測っていない
  - 4：浮動小数の誤差を含む数（0.1+0.2 など）を送ると 400 になる（丸めない）。誤りの文言は Pydantic の英語（既存の Decimal の入力と同じ）
  - 3：SQLite は FOR UPDATE を無視するので、試験は PostgreSQL の方言で FOR UPDATE が付くことと、ロックの直前に別のセッションが変えた値で判定することを見る。
    実際の PostgreSQL でのロック待ち・デッドロックは確かめていない。申請の前提に効くスコープ項目・活動の見積、工数の群はロックしない
  - CR-014：画面の守りは読み込んだ時点の値で決める（読み込んだ後に他の人が承認した項目は、読み直すまで画面では外せ、サーバが 403 を返す）。システム管理者が作ったプロジェクトは今どおりメンバーを作らない
  - 独立レビュー（2026-11-13）で残したもの：報告日の入力の求め直し（`_refresh_open_entries`）と同時の入力の重なりは、報告日の行のロックの外で起こりうる（未確認）。API-PRG の作成・確定の応答の `entry_count` は委託先にも見える範囲で絞らない。共有リンクの `_build` の 422 `SCHEDULE_CYCLE`（details に活動の ID）は日程の計算の後には起きない経路として残した

## A-50: 残りの未確定事項を全て推奨案で決め、Google Cloud へ配置したこと（2026-11-13）

- **経緯:** 利用者が 2026-11-13 に次を指示した。(1) 配置先は Google Cloud のプロジェクト kc-dbsb-poc-260109（Cloud SQL for PostgreSQL・Cloud Run。サーバが画面も
  配るなら 1 コンテナでよい）、資源は Terraform で作り、名前の接頭辞は `ksdp`。PostgreSQL での検証は Cloud SQL で行う。(2) その他の未確定事項は全て推奨案で
  承認する（推奨案が無いものは作ってから続ける）。(3) ブラウザでの確認・性能・本番の値も推奨案で承認する。設計書の変更が要るものは CR-015〜020 に起票して承認とし、
  設計書を 1.3 にした（D-10）。Terraform と配置のスクリプトは利用者が自分の認証で流す（この機械の sandbox は gcloud の認証を読めない）。作業木 `wt/oq-open`
  （群 g7・g8・g9・pg）・`wt/records`・`wt/rpt-a3` で実装・記録する
- **選択（設計書 1.3 の CR）:** CR-015 → OQ-35 (3)。CR-016 → OQ-43 (1)。CR-017 → OQ-34 (4)。CR-018 → OQ-21 (1)。CR-019 の 1 → OQ-06、2 → OQ-33 (1)(2)、
  3 → OQ-31 (8)、4 → OQ-35 (5)、5 → OQ-36 (1)(6)・OQ-40 (2)、6・7 → OQ-43 (2)(3)、8 → OQ-15。CR-020 → OQ-01（本番の配置）
- **選択（未確定事項の閉じ方）:** 43 件を全て CLOSED にした（`docs/open-questions.md`）
  - 暫定の設計で実装済み（ASSUMED）のものは、CR で決めた項を除き「暫定どおり確定」とした（OQ-02〜05・11〜14・16〜20・22〜30・32・39・41 と、各 OQ の CR に
    含めなかった項）。OQ-05 の許容幅・抽出の閾値は今の既定値（0.5 稼働日・2 稼働日）で試行し、試行の後に見直す
  - 推奨案の無かったもの：OQ-15 の委託先へのメンバーの一覧は今どおり project.view で読める（作業の連絡に名前と所属が要るため。ロックの状態などは CR-019 の 7 で
    user.admin だけに返す）
  - OQ-01〜05 は要求仕様書 11 章の写しの表（列が 6 つ）に置いたまま CLOSED にし、OQ-06 以降を CLOSED の表へ移した
- **本番の値（推奨案。環境変数で変えられる）:** 業務の時間帯 Asia/Tokyo（`PMT_BUSINESS_TIMEZONE`）、取込の上限 10 MiB・50,000 行（`PMT_IMPORT_MAX_BYTES`・
  `PMT_IMPORT_MAX_ROWS`）、共有リンクの有効期限の上限 90 日（`PMT_SHARE_LINK_MAX_DAYS`。画面の既定は 14 日後）、API の説明は出さない（`PMT_API_DOCS`）、
  パスワードは 12 文字以上・連続 10 回の誤りで 15 分ロック（CR-010）、トークンの有効期間 12 時間。資源の既定（`deploy/terraform/variables.tf`）：
  asia-northeast1、Cloud SQL は PostgreSQL 16・ENTERPRISE・db-custom-1-3840・ZONAL・自動バックアップ 7 世代（18:00 UTC）・PITR 7 日・削除保護、
  Cloud Run は cpu 1・1Gi・最小 0・最大 2 インスタンス・同時 80 リクエスト・応答 300 秒、最初の管理者のログイン名 `pmt-admin`（パスワードは Secret Manager の
  `ksdp-initial-admin-password`）
- **選択（設計書を変えずに実装で決めるもの）:**
  1. CR-019 の 2（業務の日付）：「今日」は `services/clock.py` の `business_date(now, zone)`。期間は `business_period(date_from, date_to, zone)` で、業務の
     時間帯の [開始日の 0 時, 終了日の翌日の 0 時) を UTC の範囲にする（表せない端は限らない）。サーバで now から日付を求めていたのは変更（CHG）の群だけだった
  2. CR-019 の 5（共有ビュー）：API-RPT-004 の担当者は `assignees` の文字列の配列の形のまま、中身を所属にする（「組織／チーム」。片方だけならその値、両方とも
     空なら「所属なし」。行ごとに重複を除く）。ログインした人の API-SCH-010・API-RPT-001 は変えない。有効期限の上限を超えたときの 400 の loc は
     ["body","expires_at"]。画面の有効期限の既定値は 14 日後（端末の時刻の日時）
  3. CR-019 の 7：一覧の応答に `UserListItemOut` を足した（`can_create_project: bool | None`）。作成・変更（API-CMN-004・005）の `UserOut` は変えない
  4. CR-019 の 8（最後の PM）：PM はロールに PM を持つメンバーを、利用者の有効・無効を問わず数える。PM のいないプロジェクト（システム管理者が作っただけ）で
     PM でないメンバーを変えるのは止めない。判定の順は 404（メンバーでない）→ 409。プロジェクトの行をロックしてから対象のメンバーを読み直して数える。
     details は [{"project_id","user_id"}]
  5. CR-019 の 1・CR-020（画面の配信）：既存の APIRoute の仕組みで書く。`assets/` は `public, max-age=31536000, immutable`、他は `no-cache`。先頭のセグメントが
     `api`・`docs`・`redoc` のパスは画面へ回さない（JSON の 404・405 のまま。`//api/…` のような空のセグメントは飛ばして先頭を見る）。パスは resolve して dist の
     内側に限る。dist に index.html が無ければ起動しない。欠けた `/assets/*` も index.html（no-cache）
  6. CR-020（配置）：基のイメージはタグとダイジェストで固定（node 24 の bookworm-slim・python 3.12 の slim-bookworm。tzdata を入れて Asia/Tokyo を確かめる）。
     uid 10001 の `pmt` で動かす。provider は google 8.11.0・random 3.9.1（全てのプラットフォームのハッシュを .terraform.lock.hcl に）。費用寄りの既定
     （ZONAL・db-custom-1-3840・ENTERPRISE・最大 2 インスタンス・1Gi・cpu_idle・最小 0）。DB `pmt` は deletion_policy PREVENT、DB ユーザーは ABANDON。
     パスワードは英数字だけ（URL と alembic の % を避ける）。秘密はリージョン内の複製で、秘密ごとに読める身元を限る（本番の DB の URL と最初の管理者の
     パスワードは `ksdp-run`、検証の DB の URL は `ksdp-verify` だけ）。PostgreSQL での検証のジョブ（pgverify）は身元 `ksdp-verify`・DB ユーザー `pmt_verifier`
     で動かす（本番の秘密を読めず、本番の表（`pmt_app` の持ち物）への権限を持たない）。Cloud SQL は公開 IP・許可ネットワーク無し・ENCRYPTED_ONLY・Cloud Run
     の Unix ソケット（私設 IP は VPC の費用を避けた）。Cloud Build は専用 SA・CLOUD_LOGGING_ONLY・ソースのバケットは 7 日で消える・上げるファイルは
     gcloudignore の許可リスト。コミットしていない変更があれば配置しない（`ALLOW_DIRTY=1`）。移行のジョブが通るまでサービスを入れ替えない（jobs だけ
     `-target` で apply）。`deploy.sh` は terraform output が失敗したら止める（空のイメージで apply するとサービスとジョブを消す計画になるため）。出力が 1 つも
     無い初回の状態の警告の文・複数の行は空として扱い、変数 `image`・`test_image` はダイジェストの形だけを受ける。最初の管理者のログイン名は `pmt-admin`
  7. CR-015：画面 UI-INT-001 は effort.view が無い人の出力の対象から「実績工数」を外して理由を出す。取込の対象は変えない
  8. CR-016：委託先の判定は CR-011 と同じ（`project_view_level(...) is not ViewLevel.ALL`。全てを読めるロールも持つ人は絞らない）。API-CHG-004 は変更要求を
     読む前に 403（無い ID も 403。有無を明かさない）。画面は operations が null なら「変更内容と影響分析は表示しない」と出し、「直す」「影響分析を流す」を隠す
  9. CR-017：自分の見積を空（null）にする・空で足すときは記録を残さない（記録は値を要し、値を消すことは見積でないため）。値が変わらないとき・409 のときも
     残さない。`project_hours_per_day` を services/estimate.py から services/wbs.py に移した（循環の import を避ける）
  10. CR-018：保存済みの値は移行しないので、書く値が今の値と違い、かつ開始日より前のときだけ拒む（古い活動の他の列は直せる）。API-PRJ-004 は字義どおり
      （開始日を変えた結果、それより前の希望開始日の活動が残るなら 422。既存の活動も含む）。API-SCH-004 は RULE-SCH-006 の後、取込は行ごとの検査が全て通った
      後に検査する（取込の誤りのコード `BEFORE_PROJECT_START`）。変更要求の作成・編集では検査せず、反映（API-CHG-006）で検査する。判定はプロジェクトの行を
      ロックしてから行う（活動の追加・変更・一括変更・取込（dry_run でないとき）・開始日の変更。A-49 の 3 の順。レビューの軽 2）
  11. CR-019 の 3：無害化は CSV だけ（xlsx は既に文字列の型で書いている）。取込で外すのは、次の文字が対象のときの `'` 1 文字だけ
  12. CR-019 の 4（取込の上限）：本体は依存関数 `read_import_file` で受け、`Content-Length` が上限を超えれば読まずに 400、ヘッダが無い・小さく偽っても読んだ量が
      上限を超えた時点で止めて 400。認証（401）で読んだトランザクションは本体を受け取る前に閉じる（読む間に DB の接続を持たない。レビューの中 1）。OpenAPI の
      requestBody は元と同じ形（`openapi_extra`）。大きさの上限（400）は権限（403）より先（権限はサービス層で本体の後に確かめる今の作りのため）。行の上限は
      403 の後・行の検査の前、dry_run でも同じ 400。行の数は見出しを除く全ての行（空の行も数える）。上限ちょうどは通す。details は [{"loc": ["body"], …}]。
      CSV は上限を 1 行超えた所で解析を止める（`domain/tabular.py` の `iter_csv`。レビューの中 2）。xlsx は行の右端の空のセルを除き、64 列より右は最も右の値
      だけを残す（シートが宣言する範囲で行を右へ埋めない。見出しより右に値があることは残るので `TOO_MANY_COLUMNS` は今どおり。レビューの中 3）
  13. 定例会議レポートの印刷（UI-RPT-001。7.4 の A3 横の 1 枚。ブラウザでの確認で見つけた）：上段のガントには、予定のバーか実績のバー（未完了なら報告日まで）が
      表示の範囲（報告日の前後 4 週）に長さのある重なりで掛かる活動の行だけを描き（マイルストーンは菱形の点が範囲の内）、省いた行の数を注記する。基準の
      バーは見ない。イナズマ線は残した行の頂点だけを結ぶ。同じ部品なので REPORT の共有ビュー（API-RPT-004）も同じ。UI-SCH-002 と GANTT の共有ビューは
      全ての行を描く。印刷では版面の高さ（275mm）の縦の並びにし、ガントは残りの高さの枠へ縦横比を保って縮める。下段の遅延一覧・マイルストーンの状況は
      先頭の 15 件だけを印刷し、省いた件数を注記する（画面は全て）。長い文は印刷で省略記号で切る。ガントのバーのラベルは予定と実績のうち右へ延びる方の
      右端に置き、範囲を固定したとき（会議レポート）は範囲の始まりより前で終わるバー・右端より右から始まるラベルを描かない。
      利用者は 2026-11-13 に「印刷の体裁は問わない。画面の上で読めれば足りる」と判断した。印刷はこれ以上直さない（限界の項の縮みはそのまま受け入れる）
  14. PostgreSQL でだけ落ちる試験（pgverify）：飛ばさず（skip にせず）、方言ごとに同じ性質を確かめる枝を足す。SQLite の枝の確かめ方は変えない。
      AUTOINCREMENT は全ての表の主キー `id` がシーケンスを持つこと、NOT NULL の違反は SQLSTATE 23502 と表・列の名前（サーバの文言の言語に依らない）、
      8 文字の working_weekdays は列の型が拒む `DataError`、`PRAGMA foreign_keys` は `test_foreign_keys_are_enforced` にして両方の方言で外部キーの違反
      （PostgreSQL は 23503）を拒むこと（SQLite は PRAGMA も見る）。採番規則の行の同時の作成（API-WBS-002 の 409）は、PostgreSQL ではプロジェクトの行の
      `FOR UPDATE` が割り込みを起こさせないので、別のセッションの INSERT がそのロックを待って lock_timeout（55P03）になり何も書かれないことを確かめる
      （本番のロックを `FOR NO KEY UPDATE` に弱めることはしない）。PostgreSQL でも 409 に届く経路（API-WBS-006 の採番規則の変更。プロジェクトの行を
      ロックしない）の試験を足し、両方の方言で 409 と先に作った行だけが残ることを確かめる。試験のデータベースを消すときに Cloud SQL の管理用の
      スーパーユーザーの接続が残っていれば（42501）、待ってやり直し、流れの終わりにもう一度消し、それでも残れば名前を挙げて誤りにする。雛形に他の接続が
      あって複製できないとき（55006）も待ってやり直す。PostgreSQL では試験ごとの `create_all` を流さない（雛形を `create_all` で作ってある）
- **配置の結果（利用者が流した。2026-11-13）:**
  - 利用者が `deploy.sh plan`・`deploy.sh` を流した。初回の plan で、出力の無い状態の terraform の警告の文がイメージの名前として渡った（8d886bf で直し、
    変数にダイジェストの形の検査を足した）。直した後の plan は 32 件を足す
  - Cloud Build（1 分 35 秒）でイメージ `pmt@sha256:7f0ea8c3…`・`pmt-test@sha256:d4ee8d12…` を作り、移行のジョブ（`ksdp-migrate`）が通ってからサービス
    `ksdp-pmt` を入れた
  - 外からの確認（curl）：`/` は 200 の index.html（`no-cache`）、`/api/v1/health` は 200 `{"status":"ok"}`、`/docs`・`/api/v1/nope` は JSON の 404、`/assets/` は
    index.html、誤ったログインは 401。最初の管理者でのログインとログインした後の画面は確かめていない（パスワードを会話に出さないため）
  - 2 回目の配置（cbe7661。レビューの直し・pgverify の身元の分離・会議レポートの印刷）：Cloud Build `0e101c11` でイメージ `pmt@sha256:6e89d103…`・
    `pmt-test@sha256:07e551f2…` を作り、terraform は 0 件を足し 2 件を変え、移行のジョブ（`ksdp-migrate-q4z4c`）が通ってからサービスを入れ替えた。外からの
    確認は 1 回目と同じ結果で、`//api/v1/nope` も JSON の 404
  - Cloud SQL の PITR の WAL の置き場は `CLOUD_STORAGE`（利用者が `gcloud sql instances describe` で確かめた。pgverify が書く WAL でディスクは膨らまない）
  - PostgreSQL での検証（pgverify。実行 `ksdp-pgverify-b5pc9`・23 分 37 秒）：1,826 件が通り、10 件が落ち、1 件が後片付けで誤りになった。本番のコードの
    不具合は 0 件。SQLite に固有の確かめ方が 9 件（`sqlite_master`・`PRAGMA`・NOT NULL の違反の文言 5 件・生の SELECT の値の形・VARCHAR(7) を CHECK より
    先に型が拒む）、試験の組み立てが 1 件（採番規則の行の同時の作成）、試験の道具が 1 件（データベースを消せない。選択の 14）。消せなかった試験の
    データベース `pmt_test_1429253d_91bcc0c64dc3` が `ksdp-pg` に残った。Cloud SQL の API（`gcloud sql databases delete`）は持ち主でないので消せず
    （must be owner）、pgverify のジョブの引数をその実行だけ差し替えて `pmt_verifier` で DROP した（実行 `ksdp-pgverify-hx972`。利用者が流した。ジョブの定義は
    変えていない）
  - 3 回目の配置（19d4ec8。試験の直しを試験のイメージに載せる）：Cloud Build `8f9fcc60`（1 分 40 秒）でイメージ `pmt@sha256:0c155c88…`・
    `pmt-test@sha256:202317da…` を作り、移行のジョブ（`ksdp-migrate-2nlzp`）が通ってからサービスを入れ替えた。外からの確認は前と同じ結果
  - 直した後の pgverify（実行 `ksdp-pgverify-lgn8t`）：成功（ジョブは pytest の終了コードが 0 のときだけ成功になる。PostgreSQL の枝を含む全件が通った）。
    終わった後のインスタンスのデータベースは `postgres`・`pmt_verify`・`pmt` だけ（試験のデータベースの消し残しは無い）
  - 試験の直し（d3bf075 と、その独立レビューの直し）の独立レビュー：中 1・軽 8。SQLite の枝の弱体化は検出されなかった。中（PostgreSQL の枝が 409 を
    確かめない）は API-WBS-006 の試験を足して直した。軽のうち、PostgreSQL の枝がサーバの英語の文言に頼る・`close` が 1 つの誤りで残りを消さない・雛形の
    複製が 55006 で落ちうるは直した。42501 が所有者でないときにも出ることは、自分で作ったデータベースだけを消すので待っても直らない場合は起こらないと
    判断した（起これば待った後に名前を挙げて誤りにする）。残りは限界へ記した
- **独立レビュー（CR-015〜020 の差分。2026-11-13）:**
  - 実装者の仮定を引き継がない別の文脈でのレビュー。重大 0・中 4・軽 9。検証器の弱体化は検出されなかった（書き換えた試験は CR の振る舞いの変化に見合う）
  - 直した（`wt/records`。直しを 1 つずつ戻すと新しい試験が落ちることを確かめた）：中 1 取込の本体を DB の接続を持ったまま読む、中 2 CSV の行の上限が全部を
    解析した後でしか効かない（「\n」だけの 10 MiB の CSV で 1Gi を超える見込み）、中 3 xlsx の宣言の範囲（`A1:XFD50001` など）で数 KB のファイルからメモリを
    膨らませられる、中 4 pgverify が本番と同じ身元・DB ユーザーで動く、軽 2 CR-018 の判定がプロジェクトの行をロックしない、軽 5 `deploy.sh` が terraform
    output の失敗を飲み込む、軽 7 `//api/…` に画面が 200 で返る
  - 直さずに限界へ記した：軽 1・3・4・6・8・9
- **ブラウザでの確認・性能（NFR-02。2026-11-13）:**
  - 開発機で、活動 2,000・依存 3,799 の試しのデータ（SQLite）を入れたサーバを headless の Chrome（chrome-headless-shell 153）で開いた。7.1 の画面 22 と 404 の
    全てを 31 の場面（ロールの違いを含む）で開き、意図した 401（誤ったログイン）・404 のほかにコンソールの誤り・失敗した要求は無かった。書く流れは進捗の入力・
    委託先の見え方（ガント・タスクリスト・WBS）・WBS の承認・変更要求の反映・ベースラインを流した
  - NFR-02（ガントの表示 3 秒以内）：5 回ずつ。画面の遷移から最後の描画（イナズマ線）まで中央値 656 ms（最大 710 ms）、URL を直に開いて中央値 731 ms
    （最大 882 ms）。API（API-SCH-010。2.4 MB）は中央値 290 ms
  - 見つけた不具合と直し：定例会議レポートの印刷が A3 横で 53 枚（中。選択の 13 で 1 枚に。REPORT の共有ビューも 1 枚）、範囲の左端で切れたバーのラベル（軽）、
    進行中の実績のバーに重なって読めないラベル（軽）。直しを戻すと新しい画面の試験が落ちることを確かめた
  - 気づいたこと（直していない）：変更要求の「反映する」が反映できないとき（409）にも出る、ガントに読み込み中の表示が無い、ビルドに CSS のファイルが無い、
    工数の画面で権限の文が 2 回出る、大きな表を全て描く
- **理由:** 設計書 1.3 と 6.1・6.3・8 章（NFR-02・06・10）・9.2。利用者の推奨案での決定
- **影響範囲:**
  - CR-019 の 2・5〜8（wt/oq-g8）：services/clock.py・api/deps.py（get_business_zone・get_today・get_share_link_max_days）・api/change.py・services/change.py
    （search_audit_logs）・api/report.py・services/report.py・services/effort.py・schemas/users.py・services/users.py・api/users.py・services/projects.py
    （upsert_member・remove_member）。画面 labels.ts・ShareLinkPanel・SharedViewPage・api/cmn.ts（UserListItem）・UserAdminPage・OwnerSelect・ProjectListPage・
    activeUsers.ts・BasicInfoSection・MembersSection
  - 試験を PostgreSQL でも流す（wt/oq-pg）：backend/tests/conftest.py（`PMT_TEST_DATABASE_URL` があるときだけ、雛形のデータベースを試験ごとに
    `CREATE DATABASE … TEMPLATE` で複製する。`integrity_error` もその PostgreSQL で違反させる。RaceProbe の別のセッションは PostgreSQL では lock_timeout 5 秒）
  - CR-019 の 1・CR-020（wt/oq-g9）：backend/app/main.py（説明の切り替え・画面の配信）・backend/tests/test_app_serving.py・Dockerfile・.dockerignore・.gitignore・
    deploy/terraform（versions・variables・main・database・run・outputs・poc.tfvars・.terraform.lock.hcl）・deploy/cloudbuild.yaml・deploy/gcloudignore・
    deploy/deploy.sh・deploy/README.md
  - CR-015〜018・CR-019 の 3・4（wt/oq-g7）：api/change.py・deps.py（get_import_max_bytes・get_import_max_rows）・io.py（read_import_file）・projects.py・
    schedule.py・wbs.py・domain/tabular.py・schemas/change.py（operations を list | None）・services/change.py・estimate.py・io.py・projects.py・schedule.py・
    wbs.py。画面 api/chg.ts・ChangeRequestsPage・ImportExportPage
  - レビューの直し（wt/records）：api/io.py・domain/tabular.py（iter_csv）・services/io.py（行の幅・CSV の上限・取込のロック）・services/schedule.py・
    services/projects.py・main.py・deploy/terraform（main・database・run・outputs）・deploy/deploy.sh・deploy/README.md。記録は docs/open-questions.md
  - 会議レポートの印刷（wt/rpt-a3）：画面 components/gantt/scale.ts（barInRange・rowInRange）・GanttChart.tsx（ラベルの位置）・pages/rpt/MeetingReportView.tsx
    （reportGanttRows・印刷の注記）・pages/rpt/labels.ts（印刷の CSS・PRINT_LIST_LIMIT）
  - pgverify の仕分け（wt/records）：backend/tests/conftest.py（DROP のやり直し・PostgreSQL で create_all を流さない）・tests/models/test_models_integrity.py・
    test_models_prj.py・tests/test_health.py・tests/api/test_wbs.py
  - 将来の要望の控え：docs/future-requests.md（R1 の外の要望。FR-01：ガントと進捗の状況を報告用の PPTX で出力したい。RPT-04 に近い。R2 の計画の候補）
- **覆す場合:** CR の分は設計書を 1.2 に戻すことになる（Change Request を起票する）。実装の分は、その項目のコミットの差分を戻す。資源は
  `deploy/README.md` の片付けの手順で消す（Cloud SQL は削除保護と DB `pmt` の PREVENT を先に外す）
- **限界:**
  - CR-019 の 2：画面がブラウザの時刻から求める日付（工数の入力の週など）は業務の時間帯に直していない（端末の時間帯のまま）
  - CR-019 の 6：API-EFF-003 だけを 404 にした。他の API（API-PRJ-003 など）はメンバーでない人に今どおり 403。システム管理者はメンバーでなくても今どおり書ける
  - CR-019 の 8：最後の PM の利用者を無効にする（API-CMN-005）ことは止めない。無効の利用者も PM に数えるので、有効な PM のいないプロジェクトが残りうる
    （レビューの軽 3。選択の 4 のとおり）
  - CR-016：委託先に audit.view を与えると、監査ログ（変更要求の行の before・after）から operations・impact が読める（レビューの軽 4。9.2 の既定では委託先は
    audit.view を持たない）
  - CR-018：取込では行ごとのドメインの誤り（担当者が無いなど）と `BEFORE_PROJECT_START` を同時には返さない（前者を直すと後者が出る）。422 の画面の表示を
    試験したのは活動の追加だけ。ロックの順は SQLite の RaceProbe で確かめただけ（PostgreSQL で同時に流していない）
  - CR-019 の 3：責任分担表の CSV（API-WBS の出力。6.3 の対象外）は無害化していない
  - CR-019 の 4：本体を上限で止めることは偽の受信で確かめただけ（uvicorn の実際の分割転送・Cloud Run の 32 MiB の上限では確かめていない）。止めるまでに上限に
    加えて最大 1 チャンクを持つ。xlsx の上限は圧縮後の大きさに効き、展開後の大きさ・セルの長さには上限が無い（sharedStrings を使った zip 爆弾は試していない）。
    本体は 404・403 の判定より先に読む（上限を超えれば権限の無い人にも 400、上限内なら読んだ後に 404・403。レビューの軽 1）
  - PostgreSQL：この機械で PostgreSQL を動かす検証は自動判定に拒まれた。conftest の PostgreSQL の経路と、選択の 14 で足した試験の PostgreSQL の枝は
    Cloud SQL の pgverify だけで流れる（結果は配置の結果の項）。409 の試験は PostgreSQL では lock_timeout の 5 秒を待つ。同時の操作を PostgreSQL の上で
    本当に並べて流す試験は無い（RaceProbe は同じスレッドで割り込ませる）。Cloud SQL の管理用の接続が「少し待てば離れる」ことは確かめていない（常に
    残るなら 1 件ごとに 22.5 秒待ち、ジョブの上限の 3600 秒を超えて後片付けが流れず、試験のデータベースが残る）。消し残しは流れの終わりに名前を挙げて
    誤りにするだけで、前の流れの消し残しを流れの初めに掃除しない（同時に流れている別の pgverify のデータベースを消さないため）。`PostgresDatabases` の
    やり直しの分岐は、偽の接続で確かめただけ（試験には入れていない）。この機械の `verify.sh all` を ksd-ls のセッションの試験と同時に流すと（負荷
    18.7・8 コア）、画面の試験のうち 1,000 件を読み足す 2 件（ActivityListPage・CorrectiveActionsPage）が 5 秒の時間切れで落ちた（単独で流すと通り、
    ksd-ls に新しい重い試験と合流を控えてもらって流し直した `verify.sh all` は PASS。そのときも負荷は 12〜16。時間切れの値は変えていない）。雛形のデータベースは alembic でなく `create_all` で作る（移行だけにある制約・既定値の違いは pgverify で捉えない。移行は本番の移行のジョブで
    PostgreSQL に流れる。レビューの軽 6）
  - CR-020：terraform の validate・plan・apply と docker build はこの機械で流していない（利用者が流した。sandbox が AF_UNIX のソケットを禁じ provider が
    起動しない。docker は無い）。Terraform の状態に DB と最初の管理者のパスワードが平文で入る（状態のバケットはオーナーだけに見せる）。検証用の DB は本番と
    同じインスタンスにあり（pgverify は本番の負荷になる）、Cloud SQL が API で作る DB ユーザーはどちらも `cloudsqlsuperuser` の一員なので、DB の作成・削除の
    権限は分けていない。Artifact Registry の古いイメージを掃除しない。README の復元・片付けのコマンドは流していない。Cloud SQL の費用は月 60〜80 米ドルほどの
    見積もり（料金表で確かめていない）。`.terraform.lock.hcl` の h1 のハッシュは 1 つだけ（別の環境で `init` すると書き換わり、次の配置がコミットしていない
    変更で止まる。レビューの軽 8）。disk_autoresize に上限が無い・Dockerfile の tzdata の版を固定していない・`no-cache` の画面のファイルは 304 を返さない
    （レビューの軽 9）
  - ブラウザでの確認：headless の Chrome の 1 種だけ（他のブラウザ・実際のプリンタ・Windows のフォントでは見ていない）。サーバは SQLite で、同時の利用・
    Cloud Run の上での表示の速さは測っていない。書く流れのうち工数の入力・取込・共有リンクの作成は画面で流していない
  - 会議レポートの印刷：範囲に掛かる行が多いと、1 枚に収める代わりにガントの文字が読めないほど縮む（試しのデータでは 335 行を 158mm に描き、1 行がおよそ
    0.47mm。読める文字には 60 行ほどまで）。印刷だけ行の数を限る・遅れ・クリティカルパスの行を先に出す・範囲を狭めるのいずれかを選ぶ判断が要る（今は
    範囲に掛かる全ての行を縮めて載せる）。予定の全てが範囲より前の未着手の遅れの活動は、ガントの行に出ない（遅延一覧には出る。頂点は前から範囲の外で
    描いていなかった）。範囲の内から始まり右端を越えるラベルは端で切れる。報告日に終えた行は頂点の丸がラベルの最初の文字に重なる。利用者は印刷の体裁を
    問わないと判断した（選択の 13）。報告用の資料の出力は FR-01（PPTX。R2 の計画の候補）で扱う
  - 要求仕様書 11 章は未確定のままの記載である（保護パス。更新するなら Change Request が要る）

## A-51: ブラウザでの確認で見つけた画面の 5 件と、運用の手当て（2026-11-14）

- **経緯:** 利用者が 2026-11-14 に「段1の2〜4を推奨どおり進めてください」と指示した。中身は次の 3 つ。dev で直接実装した。
  - 段1-2：A-50 のブラウザでの確認で「不具合ではない所見」とした画面の 5 件
  - 段1-3：バックアップから戻す訓練（NFR-06）
  - 段1-4：Artifact Registry の古いイメージの掃除と、Cloud SQL のディスクの増加の上限

  段1-1（本番へのログイン）は利用者が自分で確かめる。
- **選択（画面。設計書を変えない）:**
  1. **変更要求の「反映する（再基準化）」は、WBS が基準化済み（BASELINED）でなければ押せなくする**（UI-CHG-001）。A-47 の 2 のとおり、API-CHG-006 は
     それ以外を 409 `INVALID_STATE` で拒むためである。
     - change.apply を持つ人が承認済みを開いたときにだけ、プロジェクトの詳細（API-PRJ-003）を読む。開き直すたびと、一覧を読み直すたびに読み直す
     - 押せないときは、その理由と今の WBS の状態を出す
     - 状態を読めなかったときは押させ、サーバの判断（409 の文言）を出す（`wbsAllowsApply`）
  2. **ガントは描画データ（API-SCH-010）が届くまで「読み込み中…」を出す**（UI-SCH-002。他の画面と同じ書き方）。描画データを読めなければ消して理由を出す。
     他の API（報告日・イナズマ線など）の誤りでは消さない
  3. **画面の最小の見た目を `frontend/src/styles.css` に置き、`main.tsx` で読む。**
     - ヘッダ：題名と行き先を 1 行に並べ、リンクの間を空ける
     - 他は、行き先・操作の並び、表の罫線と余白、注記・誤りの色だけ
     - `@media screen` の中だけに書く。印刷は会議レポートの印刷用の CSS（7.4）だけで決まる
     - 選択子は `:where()` で包んで詳細度を 0 にする。部品の CSS・style 属性が常に勝つ
     - ガントの図は SVG で行の高さを固定しているので、影響しない
  4. **工数の集計と差異（UI-EFF-002）は、2 つの API が同じ誤りを返したら 1 回だけ出す**（`sharedError`）。effort.view の無い人には 403 が 2 回出ていた。
     文言か理由が違えば、今までどおりそれぞれの節に出す。条件を変えて読み直している間は前の誤りを残し、新しい応答で置き換える
     （読み直しの間にまとめた誤りがばらけて、読み上げが繰り返されないように）
  5. **大きな表（進捗入力・差異・ガントの下の判定の一覧・会議レポートのガント）は仮想化しない。**
     - 設計書が縦の仮想化を求めるのはガントの図だけである（7.3・NFR-02）
     - A-50 の測定では、活動 2,000 件の描画は最も遅い回でも 882 ms（3 秒以内）だった
     - 表を仮想化すると、ブラウザの検索・印刷・試験での行の数え方が変わる
     - R1 の範囲を超えるので、`docs/future-requests.md` の FR-02 に控えた
- **選択（運用。Terraform。利用者が `bash deploy/deploy.sh plan`・`apply` で当てる）:**
  1. **Artifact Registry の掃除の方針**（`deploy/terraform/main.tf`）
     - イメージ（`pmt`・`pmt-test`）ごとに新しい 10 個を残す（KEEP。DELETE に勝つ）
     - それより古く 30 日を過ぎたものは、タグの有無に依らず消す
     - 変数は `registry_keep_recent`（2 以上の整数）と `registry_delete_older_than_days`（1 以上の整数）
     - 前のイメージへ戻す（README 5 章）ときに選べるのは、残した範囲だけになる
  2. **Cloud SQL のディスクの自動の増加に上限を置く**（`deploy/terraform/database.tf`）。`disk_autoresize_limit` に `db_disk_autoresize_limit_gb`
     （既定 50 GB。0 は上限なし）を渡す。ディスクは減らせないので、暴走した書き込みで費用が際限なく増えないようにする
- **選択（バックアップから戻す訓練。NFR-06）:** `deploy/restore-drill.sh` と README 6.4 を置いた。
  - 訓練用のインスタンス `ksdp-pg-drill`（Terraform の外）に、次の 2 つを流す
    - 10 分前の状態の複製（PITR。README 6.3）
    - 最新の自動バックアップの復元（README 6.2 と同じ操作）
  - 件数は CSV の書き出しで読む。DB へはつながず、パスワードも使わない
  - 本番 `ksdp-pg` に触れるのは、複製の元・バックアップの元・件数を読む先としてだけ
  - 書き込む先（複製の先・戻す先・消す先）は、名前に `-drill` を含むインスタンスだけにする。本番・README 6.3 の `ksdp-pg-pitr` を渡すと、gcloud を呼ぶ前に止まる
  - 戻すのは、成功した自動バックアップ（`type=AUTOMATED`）のうち最新のもの
  - 書き出しの置き場は訓練だけの `ksdp-restore-drill-<project>`。README 6.3 の本当の復旧の置き場とは分け、訓練の `cleanup` が復旧の取り込み元を消さないようにした
  - 終えたら `cleanup` で、訓練用のインスタンスと訓練の置き場を消す
- **配置の結果（利用者が流した。2026-11-14）:**
  - `deploy.sh plan`：0 件を足し、2 件を変え、0 件を消す（Artifact Registry の掃除の方針・`disk_autoresize_limit` の 0 → 50）。provider 8.11.0 が両方の属性を受け付けた
  - `deploy.sh`：Cloud Build（1 分 22 秒）でタグ 72c875d のイメージ `pmt@sha256:ceff57cd…`・`pmt-test@sha256:fc837b28…` を作り、移行のジョブ（`ksdp-migrate-jhf2d`）が
    通ってからサービスを入れ替えた。`/api/v1/health` は ok。配った画面のファイル（`assets/index-BRzwCevk.css`）に `styles.css` の規則が入っている
- **訓練の結果（利用者が流した。2026-11-14。時刻は UTC）:**

  | 段 | 操作の時間 | 読んだ値（版・利用者・プロジェクト・WBS・活動・工数・変更履歴・最後の変更） |
  |---|---|---|
  | `clone`（2026-11-14 00:20:01 の状態） | 00:30:05〜00:41:20（11 分 15 秒） | 0012_db_integrity・1・1・0・0・0・67・2026-11-13 21:46:59 |
  | `check ksdp-pg`（本番。05:02） | — | 複製と全て同じ（21:46:59 より後に変更が無い） |
  | `restore-backup`（自動バックアップ 1794592800000。2026-11-13 20:23:12 に取得） | 05:03:15〜05:09:21（6 分 06 秒） | 0012_db_integrity・1・0・0・0・0・1・2026-11-13 12:15:20 |
  | `cleanup` | 05:12:39 から約 2 分でインスタンスを消し、置き場（CSV 3 つ）を消した | — |

  - 複製は、複製した時刻の本番と同じ値になった。復元は、バックアップを取った時刻（20:23:12）の後にできたプロジェクトと 66 件の変更を含まず、
    最後の変更（12:15:20 の最初の管理者）はその時刻より前である。どちらも、指定した時点の状態が戻っている
  - 件数は CSV の書き出しで読めた（DB へつながず、パスワードを使わない）
  - 最初の `clone` は、gcloud が自分の待ちを途中で打ち切り（「taking longer than expected」）、終了コード 1 で止まった。操作はサーバの側で続いていた。
    `--async` で受け付けだけ受け、`gcloud sql operations wait --timeout=unlimited` で待つ形に直し、`wait <操作の ID>` を足した（64f330a）
  - 訓練用のインスタンスも、元の設定を引き継いで自分の自動バックアップを取った（00:39・05:07）
- **理由:** 設計書 7.3・8 章（NFR-02・06）と A-47 の 2。利用者が推奨案で承認した（2026-11-11 からの事前承認と、2026-11-14 の指示）
- **影響範囲:**
  - 画面：`frontend/src/pages/chg/ChangeRequestsPage.tsx`・`frontend/src/pages/sch/GanttPage.tsx`・`frontend/src/pages/eff/EffortPage.tsx`・
    `frontend/src/styles.css`（新規）・`frontend/src/main.tsx`と、それぞれの試験
  - 配置：`deploy/terraform/main.tf`・`database.tf`・`variables.tf`・`deploy/restore-drill.sh`（新規）・`deploy/README.md`（6.4・7 章・11 章）
  - 記録：`docs/future-requests.md`（FR-02）
- **覆す場合:** 画面と配置の分は、それぞれのコミットの差分を戻す。Terraform の分を戻すと、掃除の方針とディスクの上限が外れる（消えたイメージは戻らない）
- **独立レビュー（2026-11-14）:** 検証器の弱体化は無かった（消したアサーションは工数の 403 の「2 件」の 1 件だけで、「1 件・節の中に無い」に強めた）。
  直したもの：
  - 中：反映のボタンが、開き直しても WBS の状態を読み直さず、押せないままになった（選択の 1。開き直すたびと一覧を読み直すたびに読む。試験を足した）
  - 中：訓練の `cleanup` が README 6.3 の復旧の置き場を消し、名前の守りが等号だけだった（訓練だけの置き場と `-drill` の名前に限った）
  - 軽：ガントの「読み込み中…」が他の API の誤りで消えた。工数のまとめた誤りが、条件を変えて読み直す間にばらけた（どちらも試験を足し、前の実装で
    落ちることを確かめた）
  - 軽：自動バックアップに絞っていなかった。消したインスタンスの名前をすぐ使えないこと・本番の操作の枠を占めることを README に書いていなかった。
    Terraform の数の変数が整数か確かめていなかった
  - 軽：集計と差異が違う誤りを返したときの結合試験が無かった（足した）
- **限界:**
  - 反映のボタン
    - WBS の状態は、承認済みを開くたびと一覧を読み直すたびに読む。開いている間に別の画面で WBS の状態が変わっても、開き直すまでは前の状態で
      押せる・押せないを決める（押せばサーバが判断する）
    - 状態を読んでいる間は押せる
  - 見た目
    - jsdom の試験は CSS を読まない（`main.tsx` は試験に入らない）
    - 確かめたのは、ビルドと本番に配ったファイルに CSS が入ることだけ。ブラウザでの見た目は利用者が確かめる（段1-1）
  - Terraform
    - `terraform fmt -check` は通した。`validate`・`plan` はこの機械で流せない（sandbox が provider の起動を止める）。属性は利用者の `plan`・`apply` で確かめた
    - 前のイメージへ戻した後に 10 回以上ビルドすると、今動いているイメージが「新しい 10 個」から外れ、30 日を過ぎると消える
  - 訓練
    - 本番に業務の行が無い（WBS・活動・工数が 0 件）。業務のデータが戻ることと、データの量に応じた所要時間は確かめていない。データが入った後にもう一度流す
    - 比べたのは件数と最後の変更の時刻だけで、行の中身は比べていない
    - 6.2・6.3 を本番へ向けて流してはいない（同じ操作を訓練用のインスタンスへ流した）
    - 訓練用のインスタンスが取った自動バックアップは、インスタンスと一緒に消えた（消した後のプロジェクトのバックアップの一覧に `ksdp-pg-drill` の行が無い。
      利用者が 2026-11-14 に確かめた。消したインスタンスを名指しした一覧は 403 を返す）
    - 消したインスタンスの名前をしばらく使えないことは、Cloud SQL の仕様の記憶による（確かめていない）

## A-52: 本番へ出すのを `kloop release` に移し、guard には release を止める部分だけを写したこと（2026-11-14）

- **経緯:** kloop 11.14.0 に `kloop release` が入った（関門 → 試験の層への記録 → escalate → push dev → push main を、sandbox の外の受け手が決まった順で流す）。
  それまでは、この 4 つを利用者が `!` で打っていた。利用者が 2026-11-14 に次の 3 つを決めた。
  1. 受け手を入れる（`kloop release install`。利用者が sandbox の外で 1 回）
  2. 雛形の guard のうち、release を止める部分（CR-144）を先に入れる
  3. CLAUDE.md を書き換える
- **選択:**
  - **guard**：雛形（`ksd-loop/templates/claude/hooks/guard.py`。kloop 11.14.1）から、`kloop_release_denial` と、それが使う関数・`_command_words` だけを写した。
    `fixed_bash_denial` の ksdls の判定の後で呼ぶ（雛形と同じ位置）。エージェントの `kloop release run`・`install`・`uninstall`・知らない口を止め、
    `request`・`status` は通す。今の guard は `kloop release` を何も止めないので、受け手を入れる前に入れる。
    `.claude/hooks` はエージェントが書けないため、作った版を利用者が置き換えた
  - **写さなかったもの**：雛形の RQ-KL-017・CR-139（`cat`・`tee`・`git commit -F -` の heredoc の中身を、ksdls・クラウドなどの規則から外す）。
    止める範囲を狭める変更なので、別に判断する。写さないので、heredoc の中に `kloop release run` などと書いた Bash も止まる（止める側）
  - **CLAUDE.md**：「ブランチ」の「`git push` はエージェントが行わない」を「関門・escalate・`git push` を自分の Bash で打たない」に改めた。
    雛形の「本番へ出す」の節（4 行）を写し、関門の間は作業ツリーを変えないこと、デプロイは今までどおり利用者が流すことを足した
- **確かめたこと:**
  - 雛形の試験（ksd-ls の `tests/test_release.py` の `TestTemplateGuard`）と同じコマンド：止める 30・通す 13 が全て見込みどおり。
    `[bash].deny` で置き換えられないこと、判定の途中の例外で止めることも確かめた
  - このリポジトリのセッションの transcript にある Bash のコマンド 6,624 種を、元の guard と新しい guard の両方に掛け、判定が変わったものは 0
  - フックとしての呼び出し（標準入力の JSON）で、`kloop release install` が deny、`kloop release request` と `git status` が通る
- **覆す場合:** guard の差分（足しただけで、消した行は無い）と CLAUDE.md の差分を戻し、`kloop release uninstall` で受け手を外す。関門・escalate・push は利用者が `!` で打つ形に戻る
- **限界:**
  - guard が見ないもの（雛形と同じ）：書いたスクリプトの中、名前を組み立てる形
  - 受け手そのもの（systemd の単位・関門・escalate・push の流れ）は ksd-ls の試験で確かめたもので、このリポジトリではまだ流していない
- **最初の release（2026-11-14。`run_20261114T053123_7914f9`）:** SUCCESS（05:31:23〜05:37:43Z）。関門 e3cd7ff（368.9 秒。覆いの中）→ 試験の層への記録 →
  escalate（main 96acd04。8 コミット）→ push dev・push main（push の規則 11 件は全て pass）。push の規則の台帳（`kloop-state/push-policy-log/`）は push の後に
  書かれるので、dev へコミットして次の release で main へ入れる（ksd-ls の指示。`kloop-state` は版管理下の置き場で、.gitignore に入れない）

## A-53: 画面の見た目を OpenProject（Primer）に寄せる計画（2026-11-14）

- **経緯:** 利用者が 2026-11-14 に本番へログインした上で「画面の見た目を OpenProject のようにしたい」と依頼し、推奨の順（1 見本を決める → 2 Primer の値 →
  3 画面全体の枠 → 4 画面ごと → 5 見た目の確かめ方）で 5 まで進め、見た目を統一すると決めた。5 は ksd-ls の試験の規約に関わるので相談した
- **前提（調べたこと）:** OpenProject は 13.0 から GitHub の Primer（MIT）を見た目の土台にしている（OpenProject のブログ 2023-09-28）。OpenProject そのものは
  GPLv3 なので、その CSS・アイコン・ロゴ・名前・ブランドの色は写さない。似せるのは Primer を通した見た目の作法までにする
- **設計書との関係:** 設計書が定めるのは、22 画面のルートと画面 ID（7.1）、画面の行き来（7.2）、ガントの色の規則（7.3）、会議レポートの A3（7.4）、日本語と
  YYYY-MM-DD（NFR-09）、権限の無い操作のボタンを出さないことである。画面全体の配置と配色は定めず、13.6 は見た目を「検証器が見ていない」とする。
  見た目だけを変えるので CR は要らない。OpenProject にあって R1 に無い機能（表から詳細を横に開く表示・表の中での直接の編集・ボード・ガントのドラッグ・
  絞り込みの組み立て）は足さない（足すなら CR か FR）
- **選択（似せる点。利用者が 2026-11-14 に推奨どおり承認）:**
  1. 上のバー：濃い色の 1 行。左にアプリ名とプロジェクトの切り替え、右にタイムシート・承認待ち・利用者・ログアウト
  2. プロジェクトの左メニュー：`/projects/:projectId/*` で、プロジェクトの中の画面を縦に並べ、今の画面を強調する。狭い画面では畳む
  3. ページの見出し：題名を左、操作のボタンを右。目立たせる操作は 1 つだけ
  4. 表：行を詰め、見出しに薄い背景、罫線は横だけ、指したときと選んだときに行の色を変える
  5. 部品：ボタン・入力・状態のバッジ（WBS の状態・判定など）を Primer の見た目に揃える
  6. ガント：7.3 の色の規則はそのまま守り、線と文字だけ Primer の色に合わせる
- **選択（作り方）:**
  - `@primer/primitives`（色・文字・余白の値。CSS 変数）だけを入れ、`styles.css` をその値で書き直す。`@primer/react` に部品ごと置き換えると 22 画面の
    書き直しになり、試験（ロールと名前で探す）にも響くので、今は入れない
  - `styles.css` は今までどおり `@media screen` の中だけに書く（印刷は会議レポートの印刷用の CSS で決まる）
  - ルート・画面 ID・`data-screen-id` は変えない
- **選択（5 の見た目の確かめ方。ksd-ls の答え。利用者が 2026-11-14 に推奨どおり承認）:**
  - A. 画面写し：Playwright（frontend の devDependencies とロックで版を固定。手元のブラウザ chromium-1243 に合う版）で主な画面を撮り、`.kloop/ui-shots/<sha 12 桁>/`
    に出す（コミットしない）。API は固定のデータで偽装する。試験に数えず、関門・`[[checks]]` に入れない。置き場は `frontend/e2e/`（vitest が拾わない所）。
    ui-review のレビューノードと人が見る材料にする。動いたら ksd-ls に知らせ、ksd-ls が kloop の雛形へ入れる CR を起こす
  - B. 決まった判定（ルートが開ける・未捕捉の例外・axe の WCAG 違反。配色のコントラストを含む）：`spec-ids.toml` に `[ui_runtime]` と `[[checks]] ui-runtime`
    （雛形どおり `required = false`）を足す CR を起こす（承認済み）。agent-browser は 0.34.0 に固定して利用者が入れる。関門ではアプリが動いていないので
    「見ていない」になり、判定するのは利用者がアプリを起動して流す ui-review のグラフの中だけである
  - C. 画素の差分は関門に入れない（フォント・描画で揺れ、振動の判定を壊す。ksd-ls の ADR-011）
- **理由:** 利用者の依頼と承認。依存と書き直しを最小にし、設計書の定めの範囲で見た目だけを変える
- **覆す場合:** `styles.css`・枠の部品・`@primer/primitives` の依存を戻す。画面の振る舞いは変えないので、試験は戻さなくてよい見込み
- **限界:** 見た目は jsdom の試験では見えない。3 まで入れた後に利用者が本番で見る（`deploy.sh`）。OpenProject の文書には色の値・行の高さが無いので、
  数値は Primer の値で決める（OpenProject と同じ見た目になる保証は無い）
- **実施の記録（2026-11-14）:**
  - 2（91c929f）：`@primer/primitives` 11.10.0（2026-07-30 公開）を固定して入れ、`styles.css` を書き直した。生の値は日本語の書体の並びと
    狭い画面の境（48rem）だけ
  - 3（2858b3d）：上のバーと左メニュー（`ProjectNav`）。「プロジェクトの切り替え」は、一覧へのリンクで行う（上のバーで一覧を読み込む
    選び箱は作らない。どの画面でも API を 1 本ずつ増やすため）。左メニューの行き先は UI-PRJ-001 の遷移と同じ 9 件で、WBS の検証・責任分担表・
    是正処置・進捗入力は今までどおり各画面のリンクから行く。狭い画面では「畳む」代わりに本文の上へ横に並べる
  - 5 の A（890a1c4）を 3 の確かめのために先に入れた（`@playwright/test` 1.63.0 は chromium-1243 を使う版）。撮った写しで、上のバーのリンクが
    要素の既定の色に負けていたのを見つけて直した（詳細度が全て 0 なので、書く順で決まる）
  - 画面写しで見た 4 の候補：フォームの名札と入力が揃っていない・定義リスト（dl）の字下げ・ガントの凡例の箇条の点・ページの見出しの操作の位置・
    状態のバッジ
  - 4（80d8143・fe0bfa8）：表（幅いっぱい・横罫だけ）、定義リスト（名札の列は幅の半分まで。会議レポートの主要 KPI は狭い列なので名札の下に値）、
    フォームの 1 項目（`div` の中の `label[for]` と入力を 2 列。対が続くときは名札ごとに行を改める）、並んだボタンの間、状態のバッジ
    （`StatusBadge`。WBS・変更要求・是正措置の状態。中の文字は変えない）、ガント（文字・線・下地だけ Primer の色。7.3 の色は変えない。
    日の単位の目盛りは年月と日の 2 段）。マークアップは変えず CSS で寄せた（フォームの形を画面ごとに書き換えると試験の書き直しが要るため）。
    「ページの見出しの操作の位置」は見出しと行き先の並びを今の順のままにした（動かすと画面ごとのマークアップの変更になる）
  - 4 で見ていないもの：ui-shots に入れていない画面（UI-CHG-002・UI-WBS-002 など）・狭い画面の幅・印刷（A3）・本物のデータでの表示
  - 5 の B（CR-023）：`spec-ids.toml` に `[ui_runtime]` と `[[checks]] ui-runtime`（`required = false`）を足した。`base_url` は手元の
    vite の開発サーバ（`http://localhost:5173`）で、**検証器だけが読む値**（アプリの設定ではない。`spec-ids.toml` はイメージに入らず、
    画面は `/api` を相対で呼ぶので本番の動きに関わらない）。本番の URL にしないのは、確かめたいのが「これからコミットする手元の変更」で、
    本番は release と deploy の後の版だから。agent-browser はログインしないので、ログインの要る 20 のルートは `skip_routes` で外し、
    見るのはログイン（UI-CMN-001）と共有ビュー（UI-RPT-002。無効なトークン）の 2 画面だけ。関門ではいつも「見ていない」になる
  - 5 の B を初めて流した結果（2026-11-14。dev 7203765。利用者の端末）：2 画面を見て failures 0・人の確認 0・コンソールのエラーと
    axe の保留の注記 0。流し方の前提として分かったこと：(1) agent-browser は Claude Code の sandbox の中では動かない（daemon の
    Unix ソケットの bind を sandbox が禁じる。Playwright の画面写しは pipe なので動く）ので、利用者の端末（`!`）で流す。
    (2) `agent-browser install` の Chrome の取得は利用者の環境で証明書の誤り（UnknownIssuer）で落ちるので、
    `AGENT_BROWSER_EXECUTABLE_PATH` を Playwright の chromium-1243（`~/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome`）へ向ける。
    (3) アプリはサーバ（uvicorn。一時の SQLite）と画面（vite の 5173）を別の端末で起動する

## A-54: 2026-11-14 に推奨案で決めたその他のこと

- **経緯:** 関門の待ちの間に人が決めることを推奨案つきで示し、利用者が「全て推奨案通りで進めてください」と指示した（A-53 の分を除く）
- **選択:**
  1. CLAUDE.md の「ブランチ」のリモートの記述を、実態（利用者のアカウントの下の非公開リポジトリ）に合わせて `origin` と書く。URL は利用者の名前を含むので書かない
  2. 要求仕様書 11 章を確定した扱いに揃える CR-021 を起票し、要求仕様書 1.1 に反映した（要求 ID の増減なし。`ksdx trace verify` は PASS：要求 64・設計 ID 193・GAP 0・ORPHAN 0）
  3. guard の (b)（雛形の RQ-KL-017・CR-139。heredoc の中身を規則から外す）は今は入れない。止めすぎは Edit・Write で避けられる。入れるときは、外す範囲に
     抜け道が無いかを独立レビューで見てからにする
  4. 戻す訓練は、試行運用で業務のデータ（WBS・活動・工数）が入ってから、もう一度流す（A-51 の限界）
  5. 順番：見た目の統一（A-53）→ 試行運用 → 試行の所見で R2 の順（FR-01 の PowerPoint 出力・FR-02 の大きな表の仮想化など）を決める
- **気づいたこと（直していない）:** 設計書の冒頭は「版：1.2」のままで、0.4 の改訂履歴の 1.3（CR-015〜020）と食い違う。設計書は保護パスで、承認の範囲は
  要求仕様書 11 章だけなので、変えずに利用者へ報告した
- **理由:** 利用者の承認（2026-11-14）
- **覆す場合:** 1・2 はそれぞれのコミットの差分を戻す（2 は要求仕様書の版も 1.0 に戻す）
