# PMT の配置と運用の手順書（設計書 1.1・1.2・8 NFR-06・NFR-10・CR-020）

本番は Google Cloud に置く。サーバが画面のビルドも同じオリジンで配る 1 つのコンテナを Cloud Run で動かし、DB は Cloud SQL for PostgreSQL 16 を使う。
資源は Terraform（`deploy/terraform/`）で作り、名前には接頭辞 `ksdp` を付ける。

| 資源 | 名前（既定） | 役割 |
|---|---|---|
| Cloud Run のサービス | `ksdp-pmt` | 画面と API（`https://ksdp-pmt-….run.app`） |
| Cloud Run のジョブ | `ksdp-migrate` | DB の移行（`alembic upgrade head`）。サービスを入れ替える前に流す |
| Cloud Run のジョブ | `ksdp-pgverify` | PostgreSQL での検証（`pytest backend/tests`。検証用の DB `pmt_verify` だけを使う） |
| Cloud SQL | `ksdp-pg` | PostgreSQL 16。DB `pmt`（本番）・`pmt_verify`（検証）。ユーザー `pmt_app` |
| Secret Manager | `ksdp-database-url`・`ksdp-verify-database-url`・`ksdp-initial-admin-password` | 秘密の値。読めるのは Cloud Run の身元 `ksdp-run` だけ |
| Artifact Registry | `ksdp-images` | イメージ `pmt`（サーバ）・`pmt-test`（試験） |
| サービスアカウント | `ksdp-run`・`ksdp-build` | Cloud Run の身元・Cloud Build の身元 |
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

## 7. 費用の目安と止め方

費用のほとんどは Cloud SQL が常に動いている分である。金額は目安で、確かめていない（最新は Google Cloud の料金表と請求の画面で確かめる）。

| 資源 | 費用を決める値 | 目安 |
|---|---|---|
| Cloud SQL | `db_tier`（既定 `db-custom-1-3840`：1 vCPU・3.75 GB）・`db_edition`（`ENTERPRISE`）・`db_availability_type`（`ZONAL`。`REGIONAL` でおよそ 2 倍）・ディスク（SSD 10 GB から自動で増える）・バックアップ 7 世代とログ 7 日分 | 東京でおよそ月 60〜80 米ドル |
| Cloud Run のサービス | `max_instances`（既定 2）・`run_memory`（既定 1Gi）・CPU 1。最小 0 インスタンスで、要求を処理している間だけ CPU を割り当てる | 使っていない間は掛からない。少ない利用なら無料枠の範囲の見込み |
| Cloud Run のジョブ | 移行（CPU 1・`run_memory`）・検証（CPU 2・2Gi。最長 1 時間） | 流した時間だけ |
| Artifact Registry | ビルドのたびにイメージが増える（古いものを消す方針は置いていない） | 1 GB あたり月 0.1 米ドルほど |
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

ここまでの手順は書いたうえでクラウドに流していない。初回の配置で確かめる：

- `terraform validate`・`plan`・`apply`（作った環境ではプロバイダを起動できず、属性はプロバイダ 8.11.0 の文書と突き合わせただけ）
- `docker build`（Dockerfile は手元でビルドしていない。同じ並びのファイルで、移行・起動・ログイン・一部の試験を確かめた）
- Cloud Build を専用のサービスアカウントで流すこと、Docker Hub からベースのイメージを取るときの回数の制限
- Cloud Run から Cloud SQL への Unix ソケットの接続、起動の確認（`/api/v1/health`）が時間内に通ること
- 組織のポリシー（`invoker_iam_disabled`・公開の IP を持つ Cloud SQL・リージョン）で止められないこと
- IAM の付与が広まるまでの待ち（初回にもう一度流す必要があるか）
- 6 章の復元と 7.2 の片付けの手順（流していない）
- 費用の金額
- PostgreSQL での検証は、試験の側（`backend/tests` の conftest）が `PMT_TEST_DATABASE_URL` を読むようになってから意味を持つ

運用の上で知っておくこと：

- Terraform の状態（`ksdp-tfstate-<project>`）には秘密の値（DB のパスワード・最初の管理者のパスワード）が平文で入る。状態のバケットを読めるのはオーナーだけにする
- サービスの URL はインターネットから届く。守りはアプリのログインと共有リンクだけ（CR-020）
- 検証用の DB は本番と同じインスタンスにある
- 独自のドメイン・監視と通知・イメージの掃除は置いていない
