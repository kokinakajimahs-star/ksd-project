---
name: ksdx-bootstrap
description: 設計書 1 本から、ksd-x（要求↔設計）と kloop（設計↔実装）をまたいだ開発環境を立ち上げる。「新しいプロジェクトを立ち上げて」「この設計書で ksd-x と kloop を回せるようにして」「trace.toml を起こして」「ksdx plan の生成物を kloop.toml へ貼りたい」のようなときに使う。**2 つのツールにまたがる順序と、その途中にある人間の判断点**を案内する。Set up a new project across ksd-x and kloop from a design document.
argument-hint: "[対象プロジェクトのディレクトリ]"
allowed-tools: Bash, Read, Glob, Grep, Write, Edit
---

# 設計書 1 本から、2 つのループを立ち上げる

```text
設計書
   │
   │  kloop bootstrap --survey        機械的
   ↓
   │  ID 体系と evidence 型を決める    ← **判断。人の仕事**
   ↓
spec-ids.toml（設計 ↔ 実装）
   │
   │  trace.toml を書く                ← **判断。人の仕事**（設計層は再利用する）
   ↓
trace.toml（要求 ↔ 設計）
   │
   │  ksdx plan graph|tasks|agents     機械的
   ↓
   │  プロンプト 2 本を書く            ← **判断。人の仕事**（生成器は作らない）
   ↓
kloop.toml へ貼る → kloop が回す（**ksd-x は実行しない**）
```

**難所はどちらのツールの中にもない。順序である。**
各ツールの中の手順は、それぞれの側に既に書かれている——
この Skill が足すのは**またぎ**と、**そこで人が決めること**だけである。

| 詳しい手順 | どこにあるか |
|---|---|
| kloop 側（`spec-ids.toml` の書き方・`evidence` 型の選び方） | Skill `kloop-bootstrap` |
| ksd-x 側（`trace.toml` の書き方・段階の選び方・運用上の注意） | **`docs/adoption-guide.md`（正はこちら）** |
| この Skill | **順序・判断点・またぎで踏む落とし穴** |
| 各手順の詳細・落とし穴の実測・報告の雛形・つまずいたとき | **同じディレクトリの `reference.md`**（手順に入る前に、その番号の節を読む） |

**この Skill に手順を写さない。**写した瞬間に 2 か所になり、必ず食い違う
（`docs/adoption-guide.md` §11.6 / ADR-X-006 と同じ理由）。

## 絶対に守ること

- **参照プロジェクトへ書き込まない**（`/mnt/c/ksd-loop`・`/mnt/c/ksd-x`・`/mnt/c/ksd-analyzer`・
  `/mnt/c/ksd-analyzer-runs`。上流 3 つは 2026-10-25 に退役した凍結した記録）。CR として起票する
- **`ksdx` も `kloop bootstrap` も、対象リポジトリへ書き込む**（落とし穴 (d)）。
  実行したら **`git status` を見せること**
- **ksd-x はエージェントもグラフも実行しない。**回すのは `kloop` であり、**費用が出る**
- **分母（`baseline`）を下げない。**通らないから下げる、は禁止
- `--dry-run` の結果を利用者に見せてから適用する
- **`/tmp` を対象ディレクトリにしない**

## 環境

```bash
KSDX="${KSDX_BIN:-ksdx}"     # PATH の ksdx（ksd-ls の venv の入口）。KSDX_BIN で実体を指定できる
KLOOP="${KLOOP_BIN:-kloop}"   # PATH の kloop（ksd-ls の venv の入口）。KLOOP_BIN で実体を指定できる
```

`ksdx` は Python 3.11+ と git だけで動く。**L1（`trace.toml` だけ）なら `kloop` は要らない**
——`spec-ids.toml` を*ファイルとして*読むだけである。

## 順序（詳細は `reference.md`「順序」の同じ番号）

1. `kloop bootstrap --survey` —— 設計書に何があるかを見る。**候補を挙げただけである**

   ```bash
   $KLOOP bootstrap <対象ディレクトリ> --survey     # spec-ids.draft.toml を書く
   ```

2. **【判断】** ID 体系と `evidence` 型を決める。**この Skill はここを代行しない**（`kloop-bootstrap` の §1〜§2）。
   **`evidence = "id"` は参照コメントだけで「実装済み」になる。**件数が設計書の記載と一致するかを検算する
3. `trace.toml` を書いて（**`docs/adoption-guide.md` §4**）`ksdx trace verify` を通す。
   `from_spec_ids = true` で手順 2 の成果物を再利用し、**設計層を自前で書かない**（ADR-X-002）

   ```bash
   $KSDX doctor      -C <対象>     # 契約・設計書・spec-ids が読めるか
   $KSDX trace verify -C <対象>    # exit 0 / 2 / 6
   $KSDX trace gaps   -C <対象>    # 欠落・孤立の一覧
   ```

   > **最初の `trace verify` は FAIL する。それが正常である。FAIL を消すために `baseline` を下げてはならない。**

4. `--dry-run` で中身を見る。利用者に見せて、スコープの割れ方を確認してもらう（落とし穴 (c)）

   ```bash
   $KSDX plan graph  -C <対象> --dry-run
   $KSDX plan tasks  -C <対象> --dry-run
   ```

5. **【判断】** 不足プロンプト 2 本（`prompts/implement.md`・`prompts/review-diff.md`）を書く。**生成器はこの 2 本を作らない**（落とし穴 (a)）
6. 生成して `kloop.toml` へ**人が**貼る（`--out` と `--graph` は別物。`--agents-dir` は明示する）

   ```bash
   $KSDX plan graph  -C <対象> --out graphs/implement.toml --scope-by group
   $KSDX plan tasks  -C <対象> --out tasks.toml
   $KSDX plan agents -C <対象> --graph graphs/implement.toml --agents-dir agents/
   ```

7. 承認ゲートは**何も宣言しない**。`plan graph` は承認ゲートを置かない（CR-I-034）。`[graph.controller]` を書くと読み込みで止まる。足したゲートの承認はトークンと共に 1 コマンドで渡す
8. push は `kind = "push"` ノードと `[push]` の宣言でだけ（kloop 3.0.0 以上）。**宣言しただけでは効いたことにならない。**囮を渡して測ること

## またぎで踏む落とし穴（**実測した 4 つ**。詳細は `reference.md`）

- **(a) 生成器はプロンプトを作らない。**`--dry-run` の警告は標準出力の 1 行目に出る
- **(b) 「外部の判断者を使わない」は、宣言をしないこと。**`[graph.controller]` は kloop 4.0.0 で撤去された
- **(c) スコープが 1 つにまとまる／名前が空になる。**`row` だけだと既定の 1 種別 `_` にまとまる（`trace.py` の `DEFAULT_KIND`）。
  `group_from` が当たるのは ID 文字列だけである（`plan.py` の `scopes()`）
- **(d) `ksdx` は対象リポジトリへ書き込む**（`.ksdx/verify-result.json`）。`.gitignore` に `.ksdx/` を入れる

## この Skill が案内しないもの

- 既存資産からの要求層の自動抽出（**人が書く**）
- **実行**。`kloop graph` / `specarch analyze` の実機実行は**費用が出る**（Claude の契約の利用の枠）。**費用の承認は取らない**（CR-I-036）。`--dry-run` と予算の上限は任意。使った量を報告する（`docs/adoption-guide.md` §11.4）
- 要求が正しいか・設計が良いかの判定。測っているのは**宣言の一貫性**だけである

## 報告する

雛形は `reference.md`「報告する」。**「セットアップ完了」で終わらせない。**何を測れて何を測れないかを必ず添える。
つまずいたら `reference.md`「つまずいたとき」の表を引く。
