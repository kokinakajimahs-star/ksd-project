# Change Requests（設計書の変更要求）

設計書の**記載変更が必要**になった場合に起票する。
**該当が無い場合も "None" と記載してファイルを残す。**

## 起票の条件

以下に該当する場合に**限り**、当該スコープのみ停止して起票する。

```text
1. 設計書の記載どおりに実装すると技術的に成立しない
2. 設計書の記載どおりに実装すると完了条件を満たせない
3. 設計書内に両立できない矛盾がある
4. 設計書の記載どおりに実装するとセキュリティ・法令要件に違反する
5. 実測の結果、設計書の前提値が明確に成立しない
```

**該当スコープ以外の作業は停止しない。**

「未確定事項だから確認したい」は起票理由にならない。それは `docs/open-questions.md` である。

## 手順

```text
1. 該当スコープのみ実装を停止する
2. 本ファイルへ下記の書式で追記する
3. ユーザーへ提示する
4. 承認後に設計書を改訂し、版数と改訂履歴を更新する
5. 改訂後の記載を新たな「正」としてループを再開する
```

**設計書は保護パスである。** 承認前に書き換えてはならない。

---

## 書式

```markdown
## CR-XXX : <タイトル>

### 対象箇所
設計書 §X.Y

### 現行の記載

### 問題

### 根拠
（実測値／公式ドキュメント／テスト結果／エラー出力）

### 提案する変更後の記載

### 影響範囲
（他章・ID 体系・既存実装への波及。ID を変える提案は追跡が崩れるため特に慎重に）

### 暫定対応
（承認までの間、実装をどう扱うか）
```

---

## 現在の CR

## CR-001 : 画面の依存の主版（React Router 6・Vite 5・Vitest 2）に、その主版では直らない脆弱性の勧告がある

- 状態：起票（承認待ち）。2026-11-10。構成づくり（scaffold）の独立レビューの指摘から

### 対象箇所
設計書 §1.1 の表の「画面」（React・React Router・Vite：18・6・5）と「試験（画面）」（Vitest・Testing Library：2）

### 現行の記載
`| 画面 | React・React Router・Vite | 18・6・5 | |` と `| 試験（画面） | Vitest・Testing Library | 2 | |`

### 問題
各主版の最新に固定しても（react-router-dom 6.30.6・vite 5.4.21・vitest 2.1.9）、`npm audit` の勧告が残る。直る版は次の主版以降にしか無い。

- 実行時（利用者のブラウザに届く）：react-router 6 の 2 件（moderate）
  - GHSA-wrjc-x8rr-h8h6：`<Link>`・`useNavigate` のバックスラッシュによる外部への遷移（6.0.0 以上 7.18.0 未満）
  - GHSA-337j-9hxr-rhxg：SSR の復元の deserializeErrors（6.4.0 以上 7.18.0 未満）。PMT は SSR を使わないので届かない
- 開発・試験の時だけ（配布物に入らない）：vite 5（開発サーバの fs.allow の迂回・別オリジンからのスクリプト実行など。6.4.4 以上で直る）、esbuild 0.21（開発サーバへの別オリジンからの要求）、vitest 2・tinypool・@vitest/mocker（UI サーバ・モックの登録の経路。vitest 4.1.12 以上・tinypool 2.1.2 以上で直る）

### 根拠
`npm --prefix frontend audit`（2026-11-10。lock は frontend/package-lock.json）。`--omit=dev` でも react-router の 2 件が残る

### 提案する変更後の記載
`| 画面 | React・React Router・Vite | 18・7・6 以上（勧告の無い版） | |`、`| 試験（画面） | Vitest・Testing Library | 4 以上（勧告の無い版） | |`。
あわせて「依存は版を固定し、`npm audit` で high 以上の勧告が無い版を選ぶ」を 1.1 の注に足す

### 影響範囲
- 設計ID・7.1 のルートの文字列には影響しない（React Router 7 も `path` の書き方は同じ）
- 画面の実装はまだ構成づくりの枠だけ（`frontend/src/App.tsx`・`main.tsx`・`api/client.ts`）。主版を上げる費用は今が最小
- `vite.config.ts` の `test` の書き方が Vitest 4 で変わる可能性がある

### 暫定対応
承認までは設計書どおり 6・5・2 の主版の中の最新（react-router-dom 6.30.6・vite 5.4.21・vitest 2.1.9）で進める。
開発サーバ（`npm run dev`）は `127.0.0.1` だけで待ち受け、信頼できない Web ページを同じブラウザで開かない。
画面のスコープ（ui:*）は、外部の URL や利用者の入力の文字列を `<Link to>`・`navigate()` にそのまま渡さない

---

## CR-002 : スコープベースラインの写しの外部キーと、基準の後の要素の削除が両立しない

### 対象箇所
設計書 §4.2（外部キー制約）・§4.1 の DB-WBS-010 `scope_baseline_elements`・§5.9 の `remove_element`・§5.10（基準の後も WBS は編集できる）・§6.2 の API-WBS-005・API-CHG-006

### 現行の記載
- §4.2「外部キーは `<テーブルの単数形>_id` とし、DB の外部キー制約を張る。」
- §4.1 DB-WBS-010：`scope_baseline_elements` の列 `element_id`・`parent_element_id`
- 要求 WBS-22「その時点のWBSと辞書を後から変更されない形で照会できる。版は上書きされず増える」
- §5.10「基準化の後もWBSは編集できる」・§5.9 `remove_element`「要素を配下ごと消す」・API-WBS-005「要素を配下ごと消す」

### 問題
版の写し（`scope_baseline_elements`）の `element_id`・`parent_element_id` に `wbs_elements.id` への外部キー制約を張ると、次の 3 つを同時に満たせない。

1. 外部キー制約を張る（§4.2）
2. 版の写しを後から変えない（WBS-22）
3. 基準に入った要素を消せる（§5.9 の `remove_element` を承認済みの変更要求として反映する API-CHG-006、§5.10 の基準の後の編集としての API-WBS-005）

- 制約が `ON DELETE` 無し（RESTRICT）なら 3 が成り立たない（基準に入った要素を消すと外部キーの違反になる）
- `ON DELETE SET NULL` なら 2 が成り立たない（写しの `element_id`・`parent_element_id` が空になり、版の中の木がたどれなくなる）
- `ON DELETE CASCADE` なら 2 が成り立たない（版の行が消える）
- 制約を張らないなら 1 が成り立たない

同じ形は、まだ実装していない `schedule_baseline_items`（DB-SCH-004 の `activity_id`・`element_id`）・`progress_snapshot_items`（DB-PRG-005 の `activity_id`）にも出る（`remove_activity` の後のスナップショット）。

### 根拠
`backend/tests/models/test_models_wbs.py::test_scope_baseline_elements_block_deleting_baselined_element`（SQLite・外部キー有効）：版の写しが指す要素の `DELETE` は `IntegrityError`（FOREIGN KEY constraint failed）

### 提案する変更後の記載
§4.2 に次の 1 文を足す：「ベースライン・スナップショットの写しの表（`scope_baseline_elements`・`schedule_baseline_items`・`progress_snapshot_items`）の、写した時点の要素・活動を指す列は、写しの値として持ち、外部キー制約を張らない。内部キーは再利用しない（SQLite は `AUTOINCREMENT`）。」
あるいは、要素・活動の削除を論理削除（`wbs_elements`・`activities` に `deleted_at` を足す）にする。どちらにするかは人の判断である

### 影響範囲
- 設計 ID の追加・変更は無い（列の制約と §4.2 の文言だけ）
- 実装済み：`backend/app/models/wbs.py`・`backend/alembic/versions/20261110_0003_wbs_tables.py`（制約を外すなら移行を 1 本足す）
- これから実装するスコープ：api:WBS（API-WBS-005）・api:CHG（API-CHG-006 の `remove_element`）・db:SCH（DB-SCH-004）・db:PRG（DB-PRG-005）

### 暫定対応
承認までは §4.2 の記載どおり、`element_id`・`parent_element_id` に外部キー制約を張り、`ON DELETE` を付けない（A-11 と同じ）。
**基準に入った要素は DB が消させない**（版の写しは変わらない。fail fast）。API-WBS-005・API-CHG-006 を実装するスコープは、
基準に入った要素の削除を IntegrityError のまま 500 にせず、業務ルールの違反として返し（コードは そのスコープが決め、docs/open-questions.md OQ-16 に記録する）、この CR の判断を待つ

## CR-003 : 第 1 階層の要素の順番（5.2 の「親の last_child_seq + 1」）を数える列が 4.1 に無い

### 対象箇所
設計書 §5.2（新規の要素の順番）・§4.1 の DB-WBS-001 `wbs_elements`・DB-WBS-002 `wbs_numbering_rules`・DB-PRJ-001 `projects`

### 現行の記載
- §5.2「新規の要素：`順番 = 親の last_child_seq + 1`。`last_child_seq` は減らさない（削除した番号を再利用しない）。」
- §4.1：`last_child_seq` は `wbs_elements` にだけある。第 1 階層の要素には親が無く、`projects`・`wbs_numbering_rules` に相当する列が無い

### 問題
第 1 階層の要素の順番を、削除した番号を再利用せずに数える置き場が無い。今ある第 1 階層の要素の `sibling_seq` の最大＋1 で数えると、
順番が最大の第 1 階層の要素を消した後に足した要素がその番号（例：消した 3.0 の後の新しい要素が再び 3.0）を再利用し、5.2 と BR-02 を満たさない。
`backend/tests/models/test_models_wbs.py::test_columns_match_design_4_1` は 4.1 の列だけを許すので、実装の側で列を足せない。

### 根拠
`backend/app/services/wbs.py` の `_top_last_child_seq`（暫定の数え方）・docs/open-questions.md OQ-16 (2)

### 提案する変更後の記載
§4.1 の DB-WBS-002 `wbs_numbering_rules` の主な列に `top_last_child_seq`（既定 0。第 1 階層で今まで使った最大の順番）を足す。
§5.2 に「第 1 階層の要素の親の `last_child_seq` は `wbs_numbering_rules.top_last_child_seq` とする」を足す

### 影響範囲
- 設計 ID の追加・変更は無い（DB-WBS-002 の列と §5.2 の文言）
- 実装：`backend/app/models/wbs.py`・移行を 1 本・`backend/tests/models/test_models_wbs.py` の 4.1 の列の表・`backend/app/services/wbs.py` の `_top_last_child_seq`

### 暫定対応
承認までは、第 1 階層の順番を今ある第 1 階層の要素の `sibling_seq` の最大＋1 にする（`_top_last_child_seq` に閉じ込めた）。
第 2 階層以下は 5.2 どおり親の `last_child_seq`（再利用しない）。

