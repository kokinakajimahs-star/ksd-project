# 画面を検証対象にする

型検査とビルドが通っても、**画面の挙動は何も確認されていない**。
`npm run build` が見ているのは「型が通ること」「バンドルできること」だけで、
ID 突合が見ているのは「文字列があること」だけである。

この文書は、その隙間で実際に取り逃した欠陥と、埋めるための手順を書く。

---

## 1. 型検査を素通りする欠陥（実例）

いずれも `npm run build` が通り、ID 突合が PASS を返した状態で存在していた。

| 欠陥 | なぜ型検査を素通りしたか |
|---|---|
| 検索条件がサーバへ届かない（画面は `repairDateFrom`、API は `repairFrom`） | 状態オブジェクトを変数経由で渡すと excess property check が働かない。引数型は全項目 optional だが重複プロパティがあるため weak type 検出にも掛からない |
| 再読み込みでロール別メニューが消える（利用者を復元しない） | 型としては `CurrentUser \| null` で正しい |
| 認証ガードが無く、未ログインで描画されて全 API が 401 | 型と無関係 |
| 画面仕様の検索条件が欠落している | 型と無関係 |

1 件目は **「検索したつもりで全件が出る」** という、利用者が気づけない誤りである。

**型を厳しくしても防げない。** 画面と API の対応表が正しいことは型では保証できない
（`repairDateFrom` という名前のフィールドを持つ別の型を作れば型検査は通る）。
実際に送られるクエリ文字列を見る必要がある。

## 2. テストランナーを入れる

```bash
npm --prefix frontend install -D vitest @testing-library/react @testing-library/jest-dom jsdom
```

`frontend/package.json` に足す。

```json
{ "scripts": { "test": "vitest run" } }
```

**入れるまでの間も、検証器は「実行していない」ことを出す。**
`spec-ids.toml` の `[[checks]] frontend-test` が
`frontend/node_modules/.bin/vitest` を `requires` にしているため、
未導入なら `skipped_checks` が増え、`skip_note` が notes に出る。
`skip_note` を空にしないこと。

## 3. 何を書くか

**偽物では通ってしまうもの**を選ぶ。少なくとも次の 3 つ。

- **API へ送るクエリ名の突合。** フォームから検索条件への詰め替えを対象にし、
  実際に送られるクエリ文字列を検査する（§1 の 1 件目を機械的に防ぐ）
- **画面仕様の入力規則のうち機械的に確認できるもの。**
  「初期表示では検索しない」「開始日 ≦ 終了日でなければ送信しない」「理由が空なら送信不可」
- **認証状態による分岐。** 未ログインならログイン画面へ送る、復元中はナビを出さない

## 4. ID 突合が何を見ているか

`spec-ids.toml` の `[[kinds]]` の `evidence` で決まる。

| evidence | 実装済みと数える条件 | 落とし穴 |
|---|---|---|
| `id` | ID 文字列が `target_dirs` の**どこかに**ある | **別ファイルの参照コメントでも数える。** 画面が 1 枚も無いのに未実装数が減る |
| `declaration` | ID が**宣言の形**で現れる（行頭コメント／属性の値） | 宣言があることは見るが、中身が空でないことは見ない |
| `declaration_and_test` | 上記に加えてテストにも ID がある | 同上 |

`id` を選んだ場合、検証器はその旨を notes に出す。**画面には `declaration` を使うこと。**

宣言として認める形（既定）:

```tsx
// UI-AA-001
export function List() {
  return <main data-screen-id="UI-AA-001" />;
}
```

`declaration_patterns` で `[[kinds]]` ごとに上書きできる。

## 5. ルートが無い画面は失敗として扱う

`route_key` を指定すると、**ID の宣言はあるがルートが無い**画面を failures に数える。
画面 ID がどこかにあっても、ルートが無ければ利用者は到達できない。

```toml
route_key = "route"   # row の名前付きグループ名
```

パラメータ名は照合しない（設計書の `:caseId` と実装の `:id` は同じルートとみなす）。
パラメータが**途中**にあるルート（`/cases/:caseId/files`）も正しく照合する。

## 6. 実ブラウザで評価する

ここまでは**ソースに文字列があること**しか見ていない。画面が実際に開くかは見ていない。
`scripts/check_ui_runtime.py` がその差を埋める。`spec-ids.toml` に次を書く。

```toml
[ui_runtime]
base_url = "http://localhost:3000"
command = ["agent-browser"]
a11y_tags = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]
route_params = { caseId = "TEST-0001" }   # 書かないとその画面は対象から外れる
```

```bash
npm i -g agent-browser && agent-browser install   # 外部 CLI。無ければ skip される
npm --prefix frontend run dev                     # **検証器はアプリを起動しない**
bash scripts/verify.sh ui
```

判定するのは**決定論的なものだけ**である。

| 判定 | failures に入るか |
|---|---|
| ルートが開けない | 入る |
| 未捕捉例外（`errors`） | 入る |
| WCAG 違反（axe-core の `violations`） | 入る |
| コンソールのエラー | **入らない**（件数と例を notes に出す。`console_errors_are_failures = true` で変更可） |
| axe が判定を保留したもの（`incomplete`） | **入らない**（件数と規則名を notes に出す。人が確認する） |
| 見た目・レイアウト・文言 | **判定しない**（非決定的な判定は進捗判定と振動検出を壊す。ADR-007） |

グラフ `graphs/ui-review.toml` は「実装 → **人がアプリを起動** → 実ブラウザ評価 →
レビュー」の順に配線してある。レビューの後の承認ゲート（コミットの前の承認）は置かない
（CR-073。コミットは作業者がする）。

### 実測した前提（agent-browser 0.34.0 / Chrome for Testing 152 / axe-core 4.12）

- 出力は `{"success":.., "data":{..}, "error":null}` のラッパを持つ
- `a11y --json` → `data.violations[]`（`id` / `impact` / `help` / `nodes[].target`）と
  `data.incomplete[]`、`data.counts`
- `errors --json` → `data.errors[]`（`text` / `line` / `column`）
- `console --json` → `data.messages[]`（`text` / `type`）
- **`console --clear` は内容を返さない**（`{"cleared": true}` のみ）。
  `errors --clear` は返す。読んでからクリアすること
- axe は同梱されており**ネットワークを要さない**

版が変われば形は変わる。検証器は形を決め打ちせず、**解釈できなければ
`needs_human` を立てて「見ていない」と報告する**（黙って「違反なし」と読み替えない）。

## 7. これでも見ていないもの

- **見た目。** 設計資料との視覚的な一致は対象外である
- **宣言の中身。** ID コメントが付いた空のページは「実装済み」と数える
- **画面遷移の全体。** 個々のルートが定義されていることしか見ていない
- **操作した後の状態。** 実ブラウザ評価はページを開くだけで、クリックも入力もしない。
  操作の結果として起きる不具合は見ていない
- **パラメータ付きルート。** `route_params` に値が無ければ対象から外れる（notes に出る）
