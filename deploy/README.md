# PMT の配置と運用の手順書（設計書 1.1・1.2・8 NFR-06・NFR-10・CR-020）

本番は Google Cloud に置く。サーバが画面のビルドも同じオリジンで配る 1 つのコンテナを Cloud Run で動かし、DB は Cloud SQL for PostgreSQL 16 を使う。
資源は Terraform（`deploy/terraform/`）で作り、名前には接頭辞 `ksdp` を付ける。

| 資源 | 名前（既定） | 役割 |
|---|---|---|
| Cloud Run のサービス | `ksdp-pmt` | 画面と API（`https://ksdp-pmt-….run.app`） |
| Cloud Run のジョブ | `ksdp-migrate` | DB の移行（`alembic upgrade head`）。サービスを入れ替える前に流す |
| Cloud Run のジョブ | `ksdp-pgverify` | PostgreSQL での検証（`pytest backend/tests`。検証用の DB `pmt_verify` だけを使う） |
| Cloud SQL | `ksdp-pg` | PostgreSQL 16。DB `pmt`（本番）・`pmt_verify`（検証）。ユーザー `pmt_app`（本番の表の持ち主）・`pmt_verifier`（検証。試験ごとの DB を作って消す） |
| Secret Manager | `ksdp-database-url`・`ksdp-verify-database-url`・`ksdp-initial-admin-password` | 秘密の値。本番の 2 つを読めるのは `ksdp-run`、検証の DB の URL を読めるのは `ksdp-verify` だけ。ほかに Terraform の外の `ksdp-ui-verify-password`（12 章） |
| Artifact Registry | `ksdp-images` | イメージ `pmt`（サーバ）・`pmt-test`（試験） |
| サービスアカウント | `ksdp-run`・`ksdp-verify`・`ksdp-build` | Cloud Run のサービスと移行のジョブの身元・検証のジョブの身元・Cloud Build の身元 |
| Cloud Storage | `ksdp-build-<project>`・`ksdp-tfstate-<project>` | Cloud Build へ上げるソース（7 日で消す）・Terraform の状態（版管理あり。`deploy.sh` が作る） |

以下の例は、プロジェクト `kc-dbsb-poc-260109`・リージョン `asia-northeast1` とする（`deploy/terraform/poc.tfvars` と変数の既定値）。

```bash
PROJECT=kc-dbsb-poc-260109
REGION=asia-northeast1
```

## 1. 前提

- 道具：`gcloud`（Google Cloud CLI）・`terraform` 1.9 以上・`git`・`bash`。プロバイダの版は `deploy/terraform/.terraform.lock.hcl` で固定している
- 認証（gcloud と Terraform で別々に要る）：

  ```bash
  gcloud auth login
  gcloud auth application-default login
  gcloud auth application-default set-quota-project "$PROJECT"
  ```

- 権限：プロジェクトのオーナー（または同等。API の有効化・IAM・Cloud SQL・Secret Manager・Cloud Run・Cloud Build・Cloud Storage を作れること）
- `serviceusage.googleapis.com`・`cloudresourcemanager.googleapis.com` が無効なら、先に有効にする（他の API は Terraform が有効にする）：

  ```bash
  gcloud services enable serviceusage.googleapis.com cloudresourcemanager.googleapis.com --project "$PROJECT"
  ```

- 組織のポリシーで「Cloud Run の IAM の呼出しの確認を切る」（`invoker_iam_disabled`）が禁じられていると、サービスを作れない。
  利用者の認証はアプリのログインと共有リンクで行う設計なので（CR-020）、その場合はポリシーの例外を管理者に頼む

## 2. 初回の配置

コミットした状態で、リポジトリの根から流す：

```bash
bash deploy/deploy.sh
```

`deploy.sh` は次の順に進む（どこかで失敗すれば、そこで止まる）：

1. Terraform の状態のバケット `ksdp-tfstate-<project>` が無ければ作り（版管理あり・公開しない）、`terraform init`
2. イメージなしで apply：API の有効化・Cloud SQL・秘密・イメージの置き場・サービスアカウントを作る（Cloud SQL の作成に 10〜15 分ほど掛かる）
3. Cloud Build でサーバと試験のイメージをビルドし Artifact Registry へ置く（タグは git の短い sha。ダイジェストを出す）
4. 新しいイメージで移行・検証のジョブだけを apply
5. 移行のジョブ（`alembic upgrade head`）を流して待つ。失敗したらここで止まり、サービスは前のまま
6. 新しいイメージでサービスを apply（入れ替える）
7. サービスの URL を出す

途中で計画（`terraform plan` の出力）が画面に出て、確認を求めずに適用する。中身を先に見たいときは `bash deploy/deploy.sh plan`。

初回は IAM の付与が広まる前にジョブ・サービスを作ろうとして、権限の誤りで止まることがある。数分おいて `bash deploy/deploy.sh` をもう一度流す（作り済みの資源はそのまま）。

URL はあとからも出せる：

```bash
bash deploy/deploy.sh url
```

## 3. 最初のシステム管理者でログインする

最初の起動で、ログイン名 `pmt-admin`（変数 `initial_admin_login`）のシステム管理者を作る。パスワードは Terraform が作り Secret Manager に置いた。
**利用者が自分の端末で**次を流して読む（値を会話・チケット・文書に貼らない）：

```bash
gcloud secrets versions access latest --secret=ksdp-initial-admin-password --project "$PROJECT"
```

- ログインした後に、画面のユーザーの管理でパスワードを変えてよい。秘密の値は最初の作成にだけ使い、既にあるユーザーを上書きしない
- 連続して 10 回誤ると 15 分ロックする（NFR-04）

## 4. 更新

変更をコミットしてから流す（イメージとコミットを対応させるため、コミットしていない変更があれば止まる。試しに上げるときだけ `ALLOW_DIRTY=1` を付け、タグに `-dirty-<時刻>` が付く）：

```bash
bash deploy/deploy.sh
```

- DB の移行はサービスを入れ替える前に流れる。移行が失敗したら、サービスは前のイメージのまま動き続ける。ログの場所が出るので読み、直してからもう一度流す。
  PostgreSQL では DDL もトランザクションに入り、`backend/alembic/env.py` は移行ごとのトランザクションを指定していないので、失敗した `upgrade head` は全体が戻る
- 移行だけを流し直す：`bash deploy/deploy.sh migrate`
- イメージを変えずに設定だけを変える（最大インスタンス数・DB の大きさ・API の説明など）：`TF_VAR_<変数>=<値> bash deploy/deploy.sh apply`。
  ずっと変えておくなら `deploy/terraform/poc.tfvars` に書いてコミットする（変数のファイルは `TF_VAR_` より優先される。`TF_VAR_` は流したその時だけ効き、次の配置で既定へ戻る）

変数は `deploy/terraform/variables.tf` にある。主なもの：`db_tier`・`db_availability_type`・`db_activation_policy`・`max_instances`・`run_memory`・`api_docs`。

## 5. 前のイメージへ戻す

サービスは Terraform で管理しているので、戻すときも Terraform を通す（`gcloud run services update-traffic` で戻すと、次の apply で元へ戻る）。

1. 前のイメージのダイジェストを探す：

   ```bash
   # サービスのリビジョンごとのイメージ（ダイジェストで指している）
   gcloud run revisions list --service ksdp-pmt --region "$REGION" --project "$PROJECT" \
     --format='table(metadata.name,metadata.creationTimestamp,spec.containers[0].image)'
   # 置き場のイメージ（タグ＝git の短い sha）
   gcloud artifacts docker images list "$REGION-docker.pkg.dev/$PROJECT/ksdp-images/pmt" \
     --include-tags --sort-by=~UPDATE_TIME --project "$PROJECT"
   ```

2. そのイメージで apply する（ビルドしない。移行のジョブも同じイメージになる）：

   ```bash
   DEPLOY_IMAGE="$REGION-docker.pkg.dev/$PROJECT/ksdp-images/pmt@sha256:<ダイジェスト>" bash deploy/deploy.sh apply
   ```

- **DB は戻らない。** 戻す先のイメージより後に DB の移行を足していたら、前のイメージが新しいスキーマで動くかを先に確かめる。
  動かないなら、DB を更新の前の時点へ戻す（6 章）必要がある
- 試験のイメージも戻すなら `DEPLOY_TEST_IMAGE` を同じように渡す

## 6. バックアップと復元（NFR-06）

Cloud SQL の自動バックアップを日次で取り（日本時間の 3 時ごろ。変数 `db_backup_start_time`）、7 世代を残す。
ポイントインタイムリカバリ（7 日分のログ）を有効にしている。インスタンスには削除保護を掛けている（Terraform と Google Cloud の両方）。

- **インスタンスを消すと、自動バックアップも消える。** 消す前に必要なら書き出す（7 章の「全てを片付ける」）
- 停止（`db_activation_policy = NEVER`）の間はバックアップを取らない

### 6.1 バックアップの一覧

```bash
gcloud sql backups list --instance ksdp-pg --project "$PROJECT"
```

### 6.2 バックアップの時点へインスタンスごと戻す

インスタンスの全ての DB（`pmt`・`pmt_verify`）がバックアップの時点に置き換わり、その後の変更は失われる。戻している間は使えない。

```bash
gcloud sql backups restore <BACKUP_ID> --restore-instance=ksdp-pg --project "$PROJECT"
```

### 6.3 ある時刻の状態を取り出す（ポイントインタイムリカバリ）

ポイントインタイムリカバリは別のインスタンスへの複製（クローン）として行う。時刻は UTC で書く（日本時間から 9 時間を引く）。
クローンは Terraform の外の資源で、残している間は費用が掛かる。

```bash
gcloud sql instances clone ksdp-pg ksdp-pg-pitr --point-in-time='2026-11-13T01:23:45.000Z' --project "$PROJECT"
```

クローンの `pmt` を本番へ戻すときは、書き出して入れ替える：

```bash
# 1. 書き出しの置き場を作り、Cloud SQL の身元（プロジェクトで 1 つ）に読み書きを許す
gcloud storage buckets create "gs://ksdp-restore-$PROJECT" --project "$PROJECT" --location "$REGION" \
  --uniform-bucket-level-access --public-access-prevention
SQL_SA="$(gcloud sql instances describe ksdp-pg --project "$PROJECT" --format='value(serviceAccountEmailAddress)')"
gcloud storage buckets add-iam-policy-binding "gs://ksdp-restore-$PROJECT" \
  --member="serviceAccount:$SQL_SA" --role=roles/storage.objectAdmin

# 2. クローンから pmt を書き出す
gcloud sql export sql ksdp-pg-pitr "gs://ksdp-restore-$PROJECT/pmt.sql.gz" --database=pmt --project "$PROJECT"

# 3. 入れ替えの間は外から使えないようにする（次の apply で元へ戻る）
gcloud run services update ksdp-pmt --ingress=internal --region "$REGION" --project "$PROJECT"

# 4. 本番の pmt を作り直して取り込む（取り込みのユーザーを表の持ち主 pmt_app にする）
gcloud sql databases delete pmt --instance ksdp-pg --project "$PROJECT"
gcloud sql databases create pmt --instance ksdp-pg --project "$PROJECT"
gcloud sql import sql ksdp-pg "gs://ksdp-restore-$PROJECT/pmt.sql.gz" --database=pmt --user=pmt_app --project "$PROJECT"

# 5. 外から使えるように戻す（今のイメージで apply）
bash deploy/deploy.sh apply

# 6. 片付ける（クローンに削除保護が付いていれば、先に外す）
gcloud sql instances patch ksdp-pg-pitr --no-deletion-protection --project "$PROJECT"
gcloud sql instances delete ksdp-pg-pitr --project "$PROJECT"
gcloud storage rm --recursive "gs://ksdp-restore-$PROJECT"
```

### 6.4 戻せることを確かめる訓練（本番を変えない）

`deploy/restore-drill.sh` は、6.2・6.3 と同じ操作を訓練用のインスタンス `ksdp-pg-drill`（Terraform の外）に向けて流す。

- 本番の `ksdp-pg` に触れるのは、複製の元・バックアップの元・件数を読む先としてだけである
- 書き込む先（複製の先・戻す先・消す先）は、名前に `-drill` を含むインスタンスだけにしている。本番や 6.3 の `ksdp-pg-pitr` を渡しても止まる
- 書き出しの置き場は訓練だけの `ksdp-restore-drill-<project>` で、6.3 の置き場とは別である
- 本番のデータは変えない。ただし複製と CSV の書き出しは、本番のインスタンスの操作として走る。インスタンスごとに操作は同時に 1 つしか走らないので、
  その間は `deploy.sh apply` を流さない
- 訓練用のインスタンスは、残している間は本番と同じほどの費用が掛かる
- 消したインスタンスの名前は、しばらく（最長 1 週間ほど）使えない。続けて訓練するなら `DRILL_INSTANCE=ksdp-pg-drill-2` のように別の名前を渡す

```bash
bash deploy/restore-drill.sh clone            # 10 分前の状態を ksdp-pg-drill へ複製する（6.3。DRILL_POINT_IN_TIME で時刻を選べる）
bash deploy/restore-drill.sh check            # 複製の pmt の件数と最後の変更履歴の時刻（CSV の書き出しで読む。DB へつながない）
bash deploy/restore-drill.sh check ksdp-pg    # 本番の同じ値（複製した時刻より後に書き込みが無ければ一致する）
bash deploy/restore-drill.sh restore-backup   # 本番の最新の自動バックアップを ksdp-pg-drill へ戻す（6.2 の操作）
bash deploy/restore-drill.sh check            # 戻したバックアップの時点の件数
bash deploy/restore-drill.sh cleanup          # 訓練用のインスタンスと書き出しの置き場を消す
```

確かめること：

- 複製と戻すのに掛かった時間（出力に出る）
- `schema_version` が本番と同じこと
- 件数が本番と合うこと（複製）、またはバックアップの時点の値であること
- `last_audit_utc` が、複製した時刻・バックアップを取った時刻より前であること

結果は `docs/decisions/ASSUMPTIONS.md` に残す。

## 7. 費用の目安と止め方

費用のほとんどは Cloud SQL が常に動いている分である。金額は目安で、確かめていない（最新は Google Cloud の料金表と請求の画面で確かめる）。

| 資源 | 費用を決める値 | 目安 |
|---|---|---|
| Cloud SQL | `db_tier`（既定 `db-custom-1-3840`：1 vCPU・3.75 GB）・`db_edition`（`ENTERPRISE`）・`db_availability_type`（`ZONAL`。`REGIONAL` でおよそ 2 倍）・ディスク（SSD 10 GB から自動で増える。上限は `db_disk_autoresize_limit_gb`、既定 50 GB。増えたディスクは減らせない）・バックアップ 7 世代とログ 7 日分 | 東京でおよそ月 60〜80 米ドル |
| Cloud Run のサービス | `max_instances`（既定 2）・`run_memory`（既定 1Gi）・CPU 1。最小 0 インスタンスで、要求を処理している間だけ CPU を割り当てる | 使っていない間は掛からない。少ない利用なら無料枠の範囲の見込み |
| Cloud Run のジョブ | 移行（CPU 1・`run_memory`）・検証（CPU 2・2Gi。最長 1 時間） | 流した時間だけ |
| Artifact Registry | ビルドのたびにイメージが増える。イメージ（`pmt`・`pmt-test`）ごとに新しい `registry_keep_recent`（既定 10）個を残す。それより古く `registry_delete_older_than_days`（既定 30）日を過ぎたものは、掃除の方針で消す（消えるまで 1 日ほど掛かる） | 1 GB あたり月 0.1 米ドルほど |
| Cloud Build | ビルドの分数 | 無料枠の範囲の見込み |
| Secret Manager・Cloud Storage・Cloud Logging | 秘密 3 つ・状態とソースの置き場（ソースは 7 日で消す）・ログの量 | わずか |

DB の接続：サービスの 1 インスタンスが最大 15 本（SQLAlchemy の既定）を使う。`max_instances` を上げるときは、`max_instances × 15` とジョブの分が Cloud SQL の接続の上限に収まるかを見る。

### 7.1 DB を止める・動かす

止めている間はサービスが DB につながらず、画面と API は使えない。ディスク・バックアップの分の費用は残る。

```bash
TF_VAR_db_activation_policy=NEVER bash deploy/deploy.sh apply    # 止める
bash deploy/deploy.sh apply                                      # 動かす（既定 ALWAYS）
```

`TF_VAR_` はその時だけ効くので、止めたままにするなら `poc.tfvars` に `db_activation_policy = "NEVER"` を書く（書かないと、次の配置で動き出す）。
Cloud Run のサービスは要求が無ければ 0 インスタンスになるので、止める操作は要らない。

### 7.2 全てを片付ける

**本番のデータとバックアップが消え、戻せない。** 残すなら、先に 6.3 の手順 1・2 で本番の `ksdp-pg` から `pmt` を書き出し、置き場を別に残す。

```bash
# 1. 削除保護を外す（Terraform と Google Cloud の両方）
TF_VAR_db_deletion_protection=false bash deploy/deploy.sh apply
# 2. 本番の DB は Terraform からの削除を止めている（deletion_policy = PREVENT）。管理から外す（インスタンスと一緒に消える）
terraform -chdir=deploy/terraform state rm google_sql_database.pmt
# 3. 消す（イメージの変数は空のままでよい）
TF_VAR_db_deletion_protection=false terraform -chdir=deploy/terraform destroy -var-file=poc.tfvars
# 4. Terraform の外のもの：状態のバケット
gcloud storage rm --recursive "gs://ksdp-tfstate-$PROJECT"
```

- DB ユーザー `pmt_app` は Terraform の管理から外すだけにしている（`deletion_policy = ABANDON`。インスタンスと一緒に消える）
- API の有効化は戻さない（`disable_on_destroy = false`）

## 8. API の説明（/docs・/redoc・/api/v1/openapi.json）

既定では出さない（`PMT_API_DOCS` の既定は偽。設計書 6.1・NFR-10）。出すと、ログインしていない人も API の一覧を読める。

```bash
TF_VAR_api_docs=true bash deploy/deploy.sh apply   # 出す（次の配置で既定の「出さない」へ戻る）
bash deploy/deploy.sh apply                        # 出さない
```

## 9. PostgreSQL での検証

試験のイメージ（Dockerfile の `test` の段）で `pytest backend/tests` を流す。DB は検証用の `pmt_verify` を `PMT_TEST_DATABASE_URL` で渡し、本番の DB の URL は渡さない。
ジョブの身元 `ksdp-verify` は本番の秘密を読めず、DB ユーザー `pmt_verifier` は本番の表（`pmt_app` の持ち物）への権限を持たない。
ただし Cloud SQL が API で作るユーザーはどちらも `cloudsqlsuperuser` の一員なので、DB の作成・削除の権限は分けていない（同じインスタンスに置く限りの限界）。

```bash
bash deploy/deploy.sh pgverify
```

終わると実行の名前・コンソールのログの URL・端末でログを読む `gcloud logging read` の行が出る。
本番と同じインスタンスで流れるので、利用の多い時間を避ける。

## 10. ログ

```bash
gcloud run services logs read ksdp-pmt --region "$REGION" --project "$PROJECT" --limit 200
```

## 11. この手順書と配置の部品で確かめていないこと

配置は 2026-11-13 から利用者が流している（記録は `docs/decisions/ASSUMPTIONS.md` の A-50・A-51）。流して確かめたもの：

- `terraform plan`・`apply`（プロバイダ 8.11.0。A-51 で足した掃除の方針とディスクの上限の属性も受け付けた）
- Cloud Build を専用のサービスアカウントで流すビルド（`docker build`）、移行のジョブ、Cloud Run から Cloud SQL への Unix ソケットの接続と起動の確認（`/api/v1/health`）
- 組織のポリシー（`invoker_iam_disabled`・公開の IP を持つ Cloud SQL・リージョン）で配置が止められなかったこと
- PostgreSQL での検証（7 章の pgverify。試験の側の conftest が `PMT_TEST_DATABASE_URL` を読む）
- 6.4 の訓練（2026-11-14）：10 分前の状態の複製（操作は約 11 分）と、最新の自動バックアップの復元（約 6 分）を訓練用のインスタンスに流し、
  件数と最後の変更の時刻が、複製した時刻・バックアップを取った時刻と食い違わないことを確かめた。終えた後に訓練用の資源を消した

まだ確かめていない：

- 6.2・6.3 を本番に向けて流すこと（訓練は同じ操作を訓練用のインスタンスへ流しただけ）
- 業務のデータ（WBS・活動・工数）が入った状態での復元と、その所要時間（訓練のときの本番は、利用者 1・プロジェクト 1 で業務の行が 0 件だった）
- 7.2 の片付けの手順（流していない）
- IAM の付与が広まるまでの待ちが要るか（記録していない）
- Docker Hub からベースのイメージを取るときの回数の制限（今までのビルドでは当たっていない）
- 費用の金額（請求で確かめていない）

運用の上で知っておくこと：

- Terraform の状態（`ksdp-tfstate-<project>`）には秘密の値（DB のパスワード・最初の管理者のパスワード）が平文で入る。状態のバケットを読めるのはオーナーだけにする
- サービスの URL はインターネットから届く。守りはアプリのログインと共有リンクだけ（CR-020）
- 検証用の DB は本番と同じインスタンスにある
- 独自のドメイン・監視と通知は置いていない
- 前のイメージへ戻す（5 章）ときに選べるのは、Artifact Registry に残っているイメージだけ（新しい `registry_keep_recent` 個と、30 日を過ぎていないもの）
- 12 章の秘密 `ksdp-ui-verify-password` と検証専用の利用者は Terraform の外にある（7.2 の片付けでは消えない。12 章の「やめる」で消す）

## 12. 画面の実ブラウザでの評価に使う検証専用の利用者（CR-026）

`spec-ids.toml` の `[[ui_runtime.login]]` は、本番へ向けて流した実ブラウザでの評価（ui-runtime）が、検証専用で読むだけの利用者でログインしてから
ログインの要る画面を開くための宣言である。その利用者とパスワードの秘密を、**利用者が自分の端末で**次を流して用意する（値は画面に出ない）：

```bash
bash deploy/ui-verify-user.sh                 # プロジェクトが 1 つだけならそれを使う。2 つ以上なら一覧を出して止まる
bash deploy/ui-verify-user.sh <プロジェクトの ID>
```

- 作るもの：秘密 `ksdp-ui-verify-password`（英数字 32 文字。無ければ作る。Terraform の秘密と同じリージョン）、利用者 `ui-verify`
  （表示名「画面の検証（読むだけ）」。システムの権限なし）、渡したプロジェクトのメンバーの行（ロール VIEWER だけ）。流し直すと、あるものを合わせる
- 書き込む前に確かめ、外れれば何も変えずに止まる：同じログイン名の利用者が既にあり、この手順で作ったもの（上の表示名でシステム管理者でない）
  でないとき（作り替えない。`UI_VERIFY_LOGIN` で別の名にする）、そのプロジェクトの VIEWER が読む以外の権限を持つとき
- 書いた後に検証専用の利用者でログインし、システムの権限が無く、どのプロジェクトでも project.view と data.export しか持たないことを確かめる。
  外れれば（別のプロジェクトで書くロールを与えられていたときなど）、そのプロジェクトのメンバーから外し、利用者を無効にしてから止まる
- ログインには最初のシステム管理者の秘密（3 章）を使う。画面で管理者のパスワードを変えていれば HTTP 401 で止まる。
  流すたびにログインの失敗が 1 回数えられ、10 回続くと管理者が 15 分ロックされる（NFR-04）ので、401 なら流し直さない
- 流すと検証専用の利用者のパスワードを設定し直すので、その利用者のセッションは失効する。実ブラウザでの評価と同時に流さない
- 別のプロジェクトの ID で流し直しても、前のプロジェクトのメンバーの行は残る（読むだけ）。要らなければプロジェクトの設定のメンバーで外す
- 出した `projectId`・`elementId`・`reportId` は `spec-ids.toml` の `[ui_runtime].route_params` に書く（保護パスなので CR を通す）。
  プロジェクト・その要素・報告日を消したら、流し直して書き直す
- 秘密の値は画面・引数に出さない。`bash -x` で流しても xtrace はスクリプトの先頭で止め、gcloud の HTTP の記録（`core/log_http`）も止める
- 秘密を初めて作った回は、版がまだ無いので gcloud が `WARNING: The following filter keys were not present in any resource : state` を出す（害は無い）
- 本文の無い POST（ログアウト）には `Content-Length: 0` を付ける。Cloud Run の前段は付いていない POST を HTTP 411 で断る
- 評価は、アプリが動いている本番へ向けて利用者の端末で流す（agent-browser 0.34.0 と gcloud auth login が要る。sandbox の中では見えない）：

  ```bash
  KLOOP_UI_RUNTIME_BASE_URL=<サービスの URL> bash scripts/verify.sh ui
  ```

  base_url のオリジンが宣言の `origin` と一致するときだけログインする。手元（`http://localhost:5173`）へ向けたときは、ログインの要る画面は「見ていない」になる。
  TLS を検査する社内のプロキシを通る端末では、Linux の Chrome は `/etc/ssl/certs` を見ないので、全ての画面が `net::ERR_CERT_AUTHORITY_INVALID`
  で開けない（ログインと API は curl・Python が `/etc/ssl/certs` で通る）。プロキシの根の証明書を Chrome の NSS の DB へ入れる：
  `certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n <名> -i <根の証明書>`（certutil は libnss3-tools。DB が無ければ先に `-N --empty-password` で作る）
- パスワードを替える：`ROTATE=1 bash deploy/ui-verify-user.sh <プロジェクトの ID>`（秘密に新しい版を置き、利用者のパスワードを合わせる。
  古い版は残るので、要らなければ `gcloud secrets versions disable <版> --secret ksdp-ui-verify-password --project "$PROJECT"` で無効にする）
- やめる：画面のユーザーの管理で `ui-verify` を無効にし、`gcloud secrets delete ksdp-ui-verify-password --project "$PROJECT"` を流し、
  `spec-ids.toml` の宣言を外す CR を起票する
