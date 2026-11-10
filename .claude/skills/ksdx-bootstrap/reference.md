# ksdx-bootstrap の詳細（参照）

Skill `ksdx-bootstrap` の入口は同じディレクトリの `SKILL.md` である。ここには各手順の詳細・落とし穴の実測・報告の雛形・つまずいたときの表を置く
（2026-10-19 に `SKILL.md` から移した。ksd-ls 統合設計書 §24.3 T-1：SKILL.md は入口だけにし、詳細は参照のファイルに分ける。文言は移したときのまま）。
**`SKILL.md` の「絶対に守ること」は、ここのどの手順にも効く。**変数 `$KSDX`・`$KLOOP` は `SKILL.md` の「環境」で決める。

---

## 順序

**ksd-roadmap（`/mnt/c/ksd-roadmap`）を対象に通して確かめた依存関係である**（2026-09-21）。

### 1. `kloop bootstrap --survey` —— 設計書に何があるかを見る

```bash
$KLOOP bootstrap <対象ディレクトリ> --survey     # spec-ids.draft.toml を書く
```

**候補を挙げただけである。**採否は次の手順で人が決める。

### 2. **【判断】** ID 体系と `evidence` 型を決める

**この Skill はここを代行しない。**`kloop-bootstrap` の §1〜§2 を読み、
設計書そのものを読んで決める。とくに `evidence`：

- 既定は `declaration`。**`evidence = "id"` は参照コメントだけで「実装済み」になる**
- インフラのように「書いただけでは存在しないもの」は `evidence = "state"`

決めたら `spec-ids.toml` として保存し、**件数が設計書の記載と一致するかを検算する。**

### 3. `trace.toml` を書いて `ksdx trace verify` を通す

書き方は **`docs/adoption-guide.md` §4**（正はこちら）。またぎで効くのは 1 点だけ：

```toml
spec_ids      = "/abs/path/to/spec-ids.toml"   # 手順 2 の成果物を**再利用する**
from_spec_ids = true                           # 設計層の抽出式を二重に持たない
kinds         = ["ui", "api", ...]             # spec-ids.toml の [[kinds]] のキー
```

**設計層を自前で書かない。**書くと必ず食い違う（ADR-X-002）。

```bash
$KSDX doctor      -C <対象>     # 契約・設計書・spec-ids が読めるか
$KSDX trace verify -C <対象>    # exit 0 / 2 / 6
$KSDX trace gaps   -C <対象>    # 欠落・孤立の一覧
```

そして——

> **最初の `trace verify` は FAIL する。それが正常である。**
> ORPHAN（どの要求からも参照されない設計要素）は、たいてい**要求側の書き漏れ**を示す。
> **FAIL を消すために `baseline` を下げてはならない。**

### 4. `ksdx plan ... --dry-run` で中身を見る

```bash
$KSDX plan graph  -C <対象> --dry-run
$KSDX plan tasks  -C <対象> --dry-run
```

**まだ何も書いていない。**利用者に見せて、スコープの割れ方を確認してもらう（落とし穴 (c)）。

### 5. **【判断】** 不足プロンプト 2 本を書く

```text
prompts/implement.md      ← [tasks.*] の prompt_file
prompts/review-diff.md    ← graph の review ノード
```

**生成器はこの 2 本を作らない**（落とし穴 (a)）。中身はプロジェクト固有であり、
機械が書けるものではない。

### 6. 生成して `kloop.toml` へ貼る

```bash
$KSDX plan graph  -C <対象> --out graphs/implement.toml --scope-by group
$KSDX plan tasks  -C <対象> --out tasks.toml            # kloop.toml へ**人が**貼る
$KSDX plan agents -C <対象> --graph graphs/implement.toml --agents-dir agents/
```

- **`--out`（書き出し先）と `--graph`（読み込むグラフ）は別物である**
- `--agents-dir` に既定は無い。**明示する**
- `plan graph` は `scripts/verify-baseline.sh` を**無ければ書き出す**（あれば触らない）。
  基準の結果は `verify-before` のノードディレクトリに残る（kloop の検証器のとき。CR-I-040）。
  古いラッパの見分け方と作り直しは `docs/adoption-guide.md` §7
- `[graph.command_policy]` を宣言するなら、そのラッパを `allow` へ足す
  （`docs/adoption-guide.md` §7）。**宣言しただけでは効いたことにならない**——
  囮を 1 本渡して exit 7 を確かめること

### 7. 承認ゲートは**何も宣言しない**（`plan graph` は承認ゲートを置かない）

**`plan graph` は承認ゲートを置かない**（2026-10-20。CR-I-034）。2026-10-20 までは最後に `gate-commit`
（`kind = "approval"`。コミットの前の承認）を置いていた。人の承認のゲートはクラウドの資源の構成変更にだけ置く
（ksd-ls の Q-LS-34・LS-D31。kloop 7.0.0 / CR-073）。生成物の最後は `review`（実装ループを `needs_optional` で待つ。CR-I-039）で、`findings.md` は
コミットする作業者が読む。変異検査を配線したときは、結果を読むことを生成のときの注記で促す
（`docs/adoption-guide.md` §7）。承認ゲートを自分で足す導入先も、**kloop 側に宣言するものは無い。**

**`kloop.toml` に `[graph.controller]` を書くと読み込みで止まる**（`ConfigError` / exit 7。
kloop 4.0.0 / CR-039 で撤去。2026-09-22 に 5.0.0 で実測）。かつてあった 3 段の分岐
（宣言しない／自己完結／外部提出）は、**「宣言しない」の 1 段だけが残った。**

非 TTY では `PAUSED_FOR_APPROVAL` で止まり、**承認トークンが端末にだけ出る**
（ファイルには sha256 だけが残る）。承認は `kloop approve` → `kloop resume` の 2 段ではなく、
**トークンと共に 1 コマンド**で渡す（kloop 5.0.0 / CR-040）。

```bash
kloop resume  --run-id <id> --approve <ゲート> --token <token>   # 承認して再開
kloop resume  --run-id <id> --reject  <ゲート> --token <token>   # 却下して停止
kloop approve --run-id <id> --node <ゲート> --reject             # 却下だけは事前に記録できる
```

- `kloop approve --run-id <id> --node <ゲート>`（承認）は **exit 7 で拒否される**（当時の `gate-commit` で実測）
- 既に承認したゲートも、resume のたびに `--approve` で渡し直す（停止時にその形のコマンドが表示される）

### 8. push を宣言する（**kloop 3.0.0 以上**。2.2.0 は同日 3.0.0 へ置き換えられた）

**エージェントはいまも push しない**——`[loop].disallowed_tools` の既定
（`Bash(git push:*)` / `Bash(gh pr create:*)`）は変わっていない。
push するのは `kind = "push"` ノードだけであり、**決定論的な 11 規則がすべて `pass`** のときにだけ走る
（kloop 指示書 §6.8 / CR-038）。**上流の承認ゲートは kloop 7.0.0（CR-073）から要らない**
（3.0.0〜6.x は、承認ゲートへ届かない push ノードを読み込みで止めた）。

```toml
# kloop.toml（**保護パス側**で宣言する。グラフから任意のブランチへは push できない）
[push]
remote           = "origin"
allowed_branches = ["main"]      # **既定は空＝全閉。宣言しないかぎり push しない**
```

```toml
# グラフ側。kloop 7.0.0 からは承認ゲートを挟まない（6.x 以前は needs で承認ゲートへ届かないと読み込み時に止まった）
[[nodes]]
id = "publish"
kind = "push"
needs = ["review"]
branch = "main"
```

**宣言しただけでは効いたことにならない。**囮を渡して測ること（2026-09-21 実測）。

| 囮 | 結果 |
|---|---|
| `[push]` を宣言せずに `kind = "push"` を置く | **exit 7**（`allowed_branches` が空＝全閉） |
| 承認ゲートへ届かない push ノード | **exit 7**（「自律実行させない」）。**kloop 7.0.0（CR-073）からは読み込める**（2026-10-20 に 7.2.0 で exit 0） |
| `kind = "push"` 以外に `branch` を書く | **exit 7**（効かない宣言を許さない） |

**測ったのは読み込みだけである。**11 規則の評価は走らせていない
（走らせれば実際に push される）。判定は三値であり、`fail` は `POLICY_DENIED`(6)、
`undetermined` は `NEEDS_HUMAN`(5) になる——**「確認できなかった」を「問題なし」にしない。**

---

## またぎで踏む落とし穴（**実測した 4 つ**）

### (a) 生成器はプロンプトを作らない

`ksdx plan` の生成物は `prompts/implement.md` / `prompts/review-diff.md` を**参照する**が、
ファイルは生成されない。**`--dry-run` は警告を出す**——

```text
注記: **prompts/implement.md が無い。**生成物はこれを参照する。貼る前に書くこと
```

気付かずに貼ると、kloop がプロンプトを読めずに落ちる。
**警告は標準出力の 1 行目に出る。生成物を読み飛ばして最後だけ見ると見落とす。**

### (b) 「外部の判断者を使わない」は、宣言をしないこと

上の手順 7。**`[graph.controller]` は kloop 4.0.0 で撤去され、書くと読み込みで止まる**
（`ConfigError`）。宣言しなければ人間待ちである点は変わらないが、
**宣言して別の挙動にする道はもう無い。**

### (c) スコープが 1 つにまとまる／名前が空になる

`ksdx plan` はスコープを**設計層の `by_kind`** から割る。したがって:

| 設計層の宣言 | 割れ方 |
|---|---|
| `from_spec_ids = true` + `kinds = [...]` | `[[kinds]]` ごとに 1 スコープ |
| `row` だけ（`spec-ids.toml` を使わない） | **既定の 1 種別 `_` にまとまる**（`trace.py` の `DEFAULT_KIND`） |

さらに `group_from` で細かく割るとき、**それが当たるのは ID 文字列だけである**
（`plan.py` の `scopes()` は `rx.match(spec_id)`）。
設計書の表に種別の列を足しても group は取れない。
ID が `<KIND>-<GROUP>-<NNN>` の形でないなら、**kind ごとに `row` で ID 範囲を切る**ことになる。

**実測**（ksd-roadmap の複製。`RKM-D-\d{3}` 21 件・`spec-ids.toml` 無し）:

```toml
[tasks.implement-]                                  # ← 名前が空
description = "_ の未実装 21 件を実装する"          # ← 種別が `_`
```

スコープが 1 つなら、**実装ループも 1 本にまとまる。**それでよいかを利用者に確かめること。

### (d) `ksdx` は対象リポジトリへ書き込む

`ksdx trace verify` は `.ksdx/verify-result.json` を書く（`--result` で変えられる）。
**読み取り専用のコマンドではない。**

**実測**: ksd-roadmap の `.gitignore` に `.ksdx/` が無く、
`.ksdx/verify-result.json` が **git の追跡下に入っていた**（`git ls-files .ksdx`）。

```bash
echo '.ksdx/' >> <対象>/.gitignore      # 立ち上げのときに必ず入れる
```

実行したあとは **`git status` を見せること。**

---

## この Skill が案内しないもの

- **既存資産からの要求層の自動抽出**。`ksd-double-loop-evaluation.md` §4.1 が実測した
  「要求層の文書 0 本」は依然として壁である。この Skill が案内するのは
  **(A) 新規に kloop が食える形式で書き下ろす**側であり、**(B) の自動抽出は人が書く**
- **実行**。`kloop graph` / `specarch analyze` の実機実行は**費用が出る**（Claude の契約の利用の枠。USD は CLI の見積もり）。
  **費用の承認は取らない**（2026-10-20。ksd-ls の Q-LS-34・LS-D32、ksd-x の CR-I-036）。`--dry-run` で計画を見るのは勧める（任意）。
  予算の上限（`max_budget_usd` など）は任意の宣言。使った量を記録して報告する（`docs/adoption-guide.md` §11.4）
- **要求が正しいか・設計が良いかの判定。**測っているのは**宣言の一貫性**だけである

## 報告する

```markdown
## 立ち上げたもの
- 設計書: <名前>
- ID 体系: <種別ごとの件数> = N 件（`spec-ids.toml` / kloop が見る）
- 層間写像: 要求 A 件 / 設計 B 件 / GAP C / ORPHAN D（`trace.toml` / ksdx が見る）
- 生成物: graphs/ tasks/ agents/（**貼ったのは人である**）

## 判断したこと
（ID 体系の選択・evidence 型・スコープの割り方・対象外にした章）

## この環境が保証していないもの
- **ID が実装に現れていることと、その実装が正しいことは別である**
- **ORPHAN / GAP は宣言の欠落であって、設計の誤りではない**
- 実行していないもの（グラフ・要求ループ・運用ループ）
```

**「セットアップ完了」で終わらせない。**何を測れて何を測れないかを必ず添える。

---

## つまずいたとき

| 症状 | 原因 | 対処 |
|---|---|---|
| `trace verify` が ERROR（`from_spec_ids` と `row` の併用） | 設計層を二重に定義した | `row` を消す（設計層の正は `spec-ids.toml`） |
| `trace verify` が ORPHAN を大量に出す | 要求 → 設計の写像表が無い／薄い | **正常。**要求側の書き漏れを示す。消さずに埋める |
| `trace verify` が exit 6 | `baseline` を下回った（分母の減少） | **下げない。**設計書から ID が消えた理由を調べる |
| `plan` が「設計層が読めない」 | `trace.toml` の `[[layers]] design` が無い／読めない | 手順 3 へ戻る |
| `[tasks.implement-]` の名前が空 | スコープが既定種別 `_` 1 つ | 落とし穴 (c) |
| kloop がプロンプトを読めずに落ちる | 手順 5 を飛ばした | 落とし穴 (a) |
| kloop が読み込みで「`[graph]` に未知のキー controller」と止まる | `[graph.controller]` を書いた（4.0.0 で撤去） | 落とし穴 (b) |
| `kloop approve` が exit 7 | 承認を `approve` で記録しようとした（5.0.0 から却下専用） | 手順 7 |
| 対象リポジトリの差分に `.ksdx/` が出る | `.gitignore` に入れていない | 落とし穴 (d) |
