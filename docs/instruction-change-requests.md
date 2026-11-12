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


## CR-004 : 5.10 の遷移と 7 章の画面に要る API が 6.2 に無い（取り下げ・レビューを閉じる・置き換えだけで読めない資源）

### 対象箇所
設計書 §5.10（変更要求の状態遷移 `SUBMITTED --> DRAFT: 取り下げ`、WBS の状態遷移 `IN_REVIEW --> DRAFT: レビューを閉じる`）・§6.2・§7.1

### 現行の記載
- §5.10 に取り下げとレビューを閉じる遷移がある。§6.2 にはその遷移を呼ぶ API が無い
- §6.2 の API-PRG-004（算定項目）・API-WBS-013（スコープの紐付け）は置き換えの PUT だけで、今の値を読む API が無い。
  WBS レビュー（API-WBS-014・015）と共有リンク（API-RPT-002・003）にも一覧の API が無い

### 問題
- 提出した変更要求を直すには、却下を経て起票し直すしかない（5.10 の遷移を使えない）
- 置き換えだけの資源は、画面が今の値を出せない。保存すると、並べなかった既存の項目・紐付けが消える
  （独立レビューで、算定項目 A・B のある活動に C だけ足して保存すると A・B が消えることを確かめた）

### 根拠
docs/open-questions.md OQ-18・OQ-37 (1)・OQ-38 (1)・OQ-40 (3)・OQ-41 (1)(2)、docs/decisions/ASSUMPTIONS.md A-47

### 提案する変更後の記載
§6.2 に次を足す（権限は対の書く API と同じ）。
- `POST …/change-requests/{id}/withdraw`（change.create。SUBMITTED → DRAFT。承認申請は取り下げる）
- `POST …/wbs/reviews/{id}/close`（wbs.edit。IN_REVIEW → DRAFT）
- `GET …/activities/{id}/progress-items`（project.view）
- `GET …/wbs/elements/{id}/scope-links`（project.view）
- `GET …/wbs/reviews`（project.view）
- `GET …/share-links`（share.manage）

### 影響範囲
- 設計 ID の追加（API が 6 つ）。`spec-ids.toml`・`trace.toml` の数が変わる
- 実装：`backend/app/api/`・`services/`・画面の UI-CHG-001・UI-PRG-002・UI-WBS-002・UI-WBS-003・UI-RPT-001

### 暫定対応
画面に「保存すると丸ごと置き換える」と書いて置き換えの PUT だけを呼ぶ。レビューは URL の番号で開き、申請の時に閉じる（OQ-18）

## CR-005 : 実績工数を締める境界（RULE-EFF-005）と、写し・集計の実績の範囲（5.8）が 1 日ずれる

### 対象箇所
設計書 §5.1 の RULE-EFF-005・§5.8（実績工数）・RULE-PRG-007（確定の写し）

### 現行の記載
- RULE-EFF-005「確定した報告日より前の日は変えない」
- §5.8「実績工数：活動の `time_entries` の合計（報告日を指定したときは報告日以前の分）」

### 問題
報告日の当日の工数は、確定の後も入力・変更できる。一方で、写しと報告日を指定した集計は当日の分を含む。
そのため、確定の後に当日の工数を直すと、写しの `actual_hours` と、同じ報告日を指定した集計が食い違う（両立しない記載）

### 根拠
`backend/app/domain/effort.py` の `check_time_entries`（`work_date < locked_before`）・`services/effort.py` の `load_basis`（`work_date <= report_date`）。docs/decisions/ASSUMPTIONS.md A-28・OQ-27 (7)

### 提案する変更後の記載
RULE-EFF-005 を「確定した報告日**以前**の日は変えない」にする（集計の範囲に合わせる）。報告日の当日の入力を許すなら、§5.8 の範囲を「報告日より前」にする

### 影響範囲
設計 ID の変更は無い（文言）。実装は `check_time_entries` の比較 1 か所と、その試験

### 暫定対応
字義どおり（当日は変えられる）のまま。食い違いは OQ-27 (7) に記録した

## CR-006 : 取込（API-INT-002）の権限が data.import だけで、wbs.edit・schedule.edit・estimate.edit を迂回できる

### 対象箇所
設計書 §6.2 の API-INT-002・§9.1（ロール×権限の表）

### 現行の記載
API-INT-002 の権限は data.import だけ。既定の表で data.import を持つのは PM・PLANNER

### 問題
取込は WBS・日程・見積・担当を書く。プロジェクトごとに表を変えられる（API-CMN-007）ので、data.import だけを付けたロールの利用者は、
wbs.edit・schedule.edit・estimate.edit を持たなくても、取込でそれらを書ける（権限の迂回。起票の条件 4）

### 根拠
`backend/app/services/io.py`（data.import だけを確かめる）。独立レビューで、data.import だけのロールで WBS の CSV を取り込むと要素が書かれることを読んで確かめた

### 提案する変更後の記載
API-INT-002 の権限を「data.import に加えて、対象の書く権限（`wbs` は wbs.edit（見積の列を変えるなら estimate.edit も）、`activities` は schedule.edit（見積の列を変えるなら estimate.edit も））」にする。`time-entries` は他人の行を書く権限が 9.1 に無いので、data.import で許すか、本人の行だけ（effort.enter_own）にするかを決める

### 影響範囲
設計 ID の変更は無い（§6.2 の権限の欄）。実装は `services/io.py` の権限の検査と試験

### 暫定対応
data.import だけを確かめる（記載どおり）。既定の表では data.import を持つロールは書く権限も持つので、表を変えない限り迂回は起きない

## CR-007 : 5.8 の基準の値の数え方が、基準の後に消した・足した活動と、非 WP の葉を定めていない（BR-11 の「追加」を誤る）

### 対象箇所
設計書 §5.8（基準の値）・BR-11（差異の抽出と「追加された要素」）・§4.1 の DB-SCH-004 `schedule_baseline_items`

### 現行の記載
「基準の値＝選んだスケジュールベースライン（既定は最新の版）の見積」。上位は下位の合計

### 問題
基準を活動の値の和で数えると（どの軸でも合計が一致する。EFF-09）、次のことが起きる。
- 基準の後に活動を消すと、その基準の値が WP から消えて差異が隠れる
- 基準の時点で活動の無かった WP に活動を足すと、基準にあった WP が「追加された要素」になる
- 子の無い非 WP の要素は、スケジュールベースラインに項目が無いので、いつも「追加」になる

WP の基準の項目（WORK_PACKAGE の行）を使うと、担当・期間の軸の合計が WBS の軸と合わなくなる（EFF-09）。設計書の記載だけでは両方を満たせない

### 根拠
`backend/app/domain/effort.py` の `rollup_effort`・docs/decisions/ASSUMPTIONS.md A-28・OQ-27 (5)。独立レビューで純粋関数を直接呼んで再現した

### 提案する変更後の記載
§5.8 に「WBS の軸の基準の値は、WP は版の WORK_PACKAGE の項目、上位は版にある下位の合計。担当・期間の軸は活動の項目だけを配り、
消した活動の基準は『基準にあって今は無い』の桶に置く」を足す。BR-11 の「追加」は「版に WORK_PACKAGE の項目も活動の項目も無い要素」とする

### 影響範囲
設計 ID の変更は無い（文言）。実装は `domain/effort.py` の `rollup_effort`・`aggregate_effort` と試験（`test_effort.py`）、`services/effort.py` の `load_basis`

### 暫定対応
A-28 のまま（基準は集計の葉＝活動と活動の無い WP で持つ）

## CR-008 : 画面の入口と表示範囲の欠け（活動の見積・最初の報告日・ガントの非稼働日）

### 対象箇所
設計書 §7.1・§7.2・§7.3・§6.2（API-SCH-010 の `non_working_days`）・11 章（EST-01 の画面）

### 現行の記載
- 11 章で EST-01（WP・活動の見積）に当たる画面は UI-WBS-001 だけ。7.1 の UI-SCH-001 は EST-01 を挙げない
- 7.2 の進捗の遷移はガント → 進捗入力だけ。UI-PRG-001 のルートは `:reportId` を要する
- 7.3「非稼働日を灰色にする」。API-SCH-010 の `non_working_days` の範囲は定めが無い。会議レポート（7.4）の表示範囲は報告日の前後 4 週

### 問題
- 活動の見積（人時・人日、手法・前提・参照資料）を書ける画面が無い。活動を足した WP の集計見積は活動の見積の和（A-13）なので、画面だけでは見積を戻せない（EST-01・EST-08 を満たせない）
- 報告日が 1 件も無いプロジェクトでは、画面から最初の報告日を作れない（PRG の一連の流れの入口が無い）
- 表示範囲がバーの範囲より広いとき（会議レポートの前後 4 週、報告日・過去の線で広げたガント）、広がった部分の非稼働日が灰色にならない。
  画面は祝日などの例外を知らないので、自分では足せない

### 根拠
docs/open-questions.md OQ-22 (1)・OQ-37 (2)・OQ-42 (21)。`frontend/src/pages/wbs/EstimateForm.tsx`（ELEMENT だけ）・`backend/app/domain/gantt.py`（`non_working_days` はバーの範囲）

### 提案する変更後の記載
- 7.1 の UI-SCH-001 に EST-01（活動の見積。API-EST-001 の ACTIVITY）を足す
- 7.2 に「ガント（UI-SCH-002）から報告日を作る（API-PRG-001。status_report.manage）」を足す
- 6.2 の API-SCH-010 に `range_start`・`range_end`（省けばバーの範囲）を足し、その範囲の `non_working_days` を返す。7.4 の会議レポートは自分の表示範囲を渡す

### 影響範囲
設計 ID の追加は無い（既存の UI・API の項目）。実装は `ActivityListPage.tsx`・`GanttPage.tsx`・`GanttChart.tsx`・`backend/app/domain/gantt.py`・`report.py`

### 暫定対応
活動の見積は CSV の取込か変更要求で書く。最初の報告日は存在しない番号の URL で開いた画面の作成の部品から作れる。非稼働日はバーの範囲だけ灰色にする

## CR-009 : 設計書の中の食い違い（5.7 の見込みの終了と BR-04、5.6 と 7.3 の表示、進捗率の精度と BR-07）

### 対象箇所
設計書 §5.7 の 2・BR-04・§5.6・§7.3・§4.1（`progress_entries.percent_complete` などの型）・BR-07

### 現行の記載
- §5.7 の 2「完了見込み日があれば `index(完了見込み日) + 1`」。BR-04 は非稼働日を前の稼働日へ寄せる
- §5.6 は完了・未着手（予定内）を「表示で区別する」。§7.3 は頂点の色を 4 色だけ定める
- 4.1 は進捗率の精度を定めない。BR-07 は位置の境目を前の稼働日にする

### 問題
- 完了見込み日が非稼働日のとき、式どおりなら次の稼働日の終業、BR-04 の寄せなら前の稼働日の終業になる（例：土日休み・原点 12/01・見込み 12/05（土）で 5 と 4）
- 5.6 の区別を 7.3 の 4 色では表せない（完了と未着手（予定内）がどちらも予定どおりの色）
- 進捗率を小数 2 桁で持つと、3 項目中 2 完了（66.67%）の P が 2.0001 になり、BR-07 の境目の日付と判定がずれる（独立レビューで再現した）

### 根拠
`backend/app/domain/delay.py`（A-23 の寄せ）・`frontend/src/components/gantt/scale.ts`・`backend/app/models/prg.py`（`Numeric(5,2)`）・`domain/progress.py`（0.01 への丸め）

### 提案する変更後の記載
- §5.7 の 2 に「完了見込み日は BR-04 で稼働日へ寄せてから `index + 1`」を足す
- §7.3 に「完了・未着手（予定内）は予定どおりの色に印（形）を重ねる」を足す
- §4.1 に進捗率の精度（小数 2 桁）を書き、§5.4 に「P は丸める前の進捗率から求める」を足す

### 影響範囲
設計 ID の変更は無い（文言）。実装は `domain/delay.py`・`domain/progress.py`・`domain/lightning.py`・`scale.ts` と試験

### 暫定対応
見込みは BR-04 の寄せ（A-23）、頂点は 4 色とツールチップの文言、進捗率は小数 2 桁で丸めた値から P を求める（OQ-42 (7)）
