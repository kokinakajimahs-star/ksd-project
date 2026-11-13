#!/usr/bin/env bash
# PMT の本番の配置（設計書 1.1・1.2・CR-020。手順書は deploy/README.md）。利用者が流す：
#
#   bash deploy/deploy.sh [deploy|plan|apply|migrate|pgverify|url]
#
#   deploy（既定） 初回の配置と更新の全ての手順
#                   1. terraform init（状態のバケット <prefix>-tfstate-<project> が無ければ版管理ありで作る）
#                   2. 今のイメージのまま apply（初回はイメージなし：DB・秘密・置き場・サービスアカウントを作る）
#                   3. Cloud Build でサーバと試験のイメージをビルドし、Artifact Registry へ置く（ダイジェストを出す）
#                   4. 新しいイメージでジョブ（移行・検証）だけを apply
#                   5. DB の移行のジョブ（alembic upgrade head）を流して待つ。失敗したらここで止まる（サービスは前のまま）
#                   6. 新しいイメージでサービスを apply（入れ替える）
#                   7. サービスの URL を出す
#   plan          今のイメージで terraform plan（変えずに見る）
#   apply         今のイメージで terraform apply（ビルドしない。設定・DB の大きさ・起動の方針を変えるとき、前のイメージへ戻すとき）
#   migrate       DB の移行のジョブを流して待つ
#   pgverify      PostgreSQL での検証のジョブ（pytest backend/tests を検証用の DB で）を流して待ち、ログの場所を出す
#   url           サービスの URL を出す
#
# 環境変数：
#   TFVARS              変数のファイル（既定 deploy/terraform/poc.tfvars）
#   DEPLOY_IMAGE        plan・apply で使うサーバのイメージ（既定は今サービスが動かしているもの。戻すときに前のダイジェストを渡す）
#   DEPLOY_TEST_IMAGE   plan・apply で使う試験のイメージ（既定は今の検証のジョブのもの）
#   ALLOW_DIRTY=1       コミットしていない変更があってもビルドする（タグに -dirty-<時刻> を付ける）
#   TERRAFORM           terraform の実行ファイル（既定 terraform）
#   TF_VAR_<変数>        Terraform の変数（例：TF_VAR_db_activation_policy=NEVER で DB を止める）
#
# 秘密の値は画面に出さない（terraform は sensitive の値を伏せる。このスクリプトは秘密を読まない。計画のファイルは一時ディレクトリに置き、終われば消す）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$ROOT/deploy/terraform"
TFVARS="${TFVARS:-$TF_DIR/poc.tfvars}"
TERRAFORM="${TERRAFORM:-terraform}"

die() {
  echo "deploy.sh: $*" >&2
  exit 1
}

step() {
  echo
  echo "== $*"
}

usage() {
  sed -n '2,/^set -euo pipefail$/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'
}

need() {
  command -v "$1" >/dev/null 2>&1 || die "$1 が見つからない（deploy/README.md の前提を見る）"
}

[ -f "$TFVARS" ] || die "変数のファイルが無い：$TFVARS"
TFVARS="$(cd "$(dirname "$TFVARS")" && pwd)/$(basename "$TFVARS")"

# 変数の値：変数のファイル → TF_VAR_<名前> → 既定（Terraform と同じ優先順。文字列の値だけを読む）
tfvar() {
  local name=$1 default=$2 value env_name="TF_VAR_$1"
  value=$(sed -n -E "s/^[[:space:]]*${name}[[:space:]]*=[[:space:]]*\"([^\"]*)\".*$/\1/p" "$TFVARS" | tail -n 1)
  if [ -z "$value" ]; then
    value="${!env_name:-$default}"
  fi
  printf '%s' "$value"
}

PROJECT_ID="$(tfvar project_id "")"
REGION="$(tfvar region asia-northeast1)"
PREFIX="$(tfvar prefix ksdp)"
[ -n "$PROJECT_ID" ] || die "project_id が決まらない（$TFVARS に書く）"
STATE_BUCKET="${PREFIX}-tfstate-${PROJECT_ID}"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

tf() {
  "$TERRAFORM" -chdir="$TF_DIR" "$@"
}

# 出力の値（状態が無い・出力が無ければ空）
tf_output() {
  tf output -raw "$1" 2>/dev/null || true
}

ensure_state_bucket() {
  if gcloud storage buckets describe "gs://$STATE_BUCKET" --project "$PROJECT_ID" --format='value(name)' >/dev/null 2>&1; then
    return
  fi
  step "状態のバケット gs://$STATE_BUCKET を作る（$REGION・版管理あり・公開しない）"
  gcloud storage buckets create "gs://$STATE_BUCKET" --project "$PROJECT_ID" --location "$REGION" \
    --uniform-bucket-level-access --public-access-prevention
  gcloud storage buckets update "gs://$STATE_BUCKET" --project "$PROJECT_ID" --versioning
}

tf_init() {
  need gcloud
  need "$TERRAFORM"
  ensure_state_bucket
  step "terraform init（状態：gs://$STATE_BUCKET）"
  tf init -input=false -backend-config="bucket=$STATE_BUCKET"
}

# 今のイメージ（DEPLOY_IMAGE・DEPLOY_TEST_IMAGE があればそれ）
current_images() {
  IMAGE="${DEPLOY_IMAGE:-$(tf_output app_image)}"
  TEST_IMAGE="${DEPLOY_TEST_IMAGE:-$(tf_output test_image)}"
  echo "サーバのイメージ：${IMAGE:-（なし）}"
  echo "試験のイメージ：${TEST_IMAGE:-（なし）}"
}

# 計画を一時ファイルに書いてから、その計画を適用する（確認の入力を求めない。計画は画面に出る）
tf_apply() {
  local image=$1 test_image=$2
  shift 2
  local plan="$WORK_DIR/pmt.tfplan"
  tf plan -input=false -var-file="$TFVARS" -var "image=$image" -var "test_image=$test_image" "$@" -out="$plan"
  tf apply -input=false "$plan"
  rm -f "$plan"
}

digest_of() {
  gcloud artifacts docker images describe "$1" --project "$PROJECT_ID" --format='value(image_summary.digest)'
}

# イメージのタグ（git の短い sha）を TAG に決める。コミットしていない変更があれば、何かを変える前に止める
decide_tag() {
  need git
  local sha
  sha="$(git -C "$ROOT" rev-parse --short HEAD)"
  TAG="$sha"
  if [ -n "$(git -C "$ROOT" status --porcelain)" ]; then
    [ "${ALLOW_DIRTY:-}" = "1" ] || die "コミットしていない変更がある（イメージとコミットを対応させるため止める。続けるなら ALLOW_DIRTY=1）"
    TAG="$sha-dirty-$(date +%Y%m%d%H%M%S)"
  fi
}

# Cloud Build で 2 つのイメージをビルドし、NEW_IMAGE・NEW_TEST_IMAGE（ダイジェストで指す名前）を決める
build_images() {
  local repo build_sa source_bucket tag=$TAG
  repo="$(tf_output image_repo)"
  build_sa="$(tf_output build_service_account)"
  source_bucket="$(tf_output build_source_bucket)"
  [ -n "$repo" ] && [ -n "$build_sa" ] && [ -n "$source_bucket" ] || die "イメージの置き場が無い（先に apply する）"

  echo "タグ：$tag（$repo/pmt・$repo/pmt-test）"
  # 上げるのは backend/・frontend/・Dockerfile・.dockerignore だけ（deploy/gcloudignore）
  (cd "$ROOT" && gcloud builds submit . --project "$PROJECT_ID" --region "$REGION" \
    --config deploy/cloudbuild.yaml --ignore-file deploy/gcloudignore \
    --service-account "projects/$PROJECT_ID/serviceAccounts/$build_sa" \
    --gcs-source-staging-dir "gs://$source_bucket/source" \
    --substitutions "_IMAGE_REPO=$repo,_TAG=$tag")

  NEW_IMAGE="$repo/pmt@$(digest_of "$repo/pmt:$tag")"
  NEW_TEST_IMAGE="$repo/pmt-test@$(digest_of "$repo/pmt-test:$tag")"
  echo "サーバのイメージ：$NEW_IMAGE"
  echo "試験のイメージ：$NEW_TEST_IMAGE"
}

# ジョブを流して終わるまで待ち、実行の名前とログの場所を出す。ジョブの終了の状態を返す
run_job() {
  local job=$1 rc=0 execution log_uri
  gcloud run jobs execute "$job" --project "$PROJECT_ID" --region "$REGION" --wait || rc=$?
  execution="$(gcloud run jobs describe "$job" --project "$PROJECT_ID" --region "$REGION" \
    --format='value(status.latestCreatedExecution.name)' 2>/dev/null || true)"
  if [ -n "$execution" ]; then
    log_uri="$(gcloud run jobs executions describe "$execution" --project "$PROJECT_ID" --region "$REGION" \
      --format='value(status.logUri)' 2>/dev/null || true)"
    echo "実行：$execution"
    echo "ログ（コンソール）：${log_uri:-（取れなかった）}"
    echo "ログ（端末で読む）：gcloud logging read 'resource.type=\"cloud_run_job\" AND labels.\"run.googleapis.com/execution_name\"=\"$execution\"' --project $PROJECT_ID --order=asc --limit=1000 --format='value(textPayload)'"
  fi
  if [ "$rc" -ne 0 ]; then
    echo "ジョブ $job は失敗した（終了の状態 $rc）" >&2
  fi
  return "$rc"
}

cmd_url() {
  local url
  url="$(tf_output service_url)"
  [ -n "$url" ] || die "サービスがまだ無い（bash deploy/deploy.sh で配置する）"
  echo "$url"
}

cmd_deploy() {
  decide_tag
  tf_init

  step "2. 今のイメージのまま apply（初回はイメージなしで、DB・秘密・置き場・サービスアカウントを作る）"
  current_images
  tf_apply "$IMAGE" "$TEST_IMAGE"

  step "3. Cloud Build でビルドし、Artifact Registry へ置く"
  build_images

  step "4. 新しいイメージでジョブ（移行・検証）を apply"
  tf_apply "$NEW_IMAGE" "$NEW_TEST_IMAGE" \
    -target=google_cloud_run_v2_job.migrate -target=google_cloud_run_v2_job.pgverify

  step "5. DB の移行（alembic upgrade head）を流して待つ"
  run_job "$(tf_output migrate_job)"

  step "6. 新しいイメージでサービスを入れ替える"
  tf_apply "$NEW_IMAGE" "$NEW_TEST_IMAGE"

  step "7. サービスの URL"
  cmd_url
}

cmd_plan() {
  tf_init
  step "terraform plan（今のイメージ）"
  current_images
  tf plan -input=false -var-file="$TFVARS" -var "image=$IMAGE" -var "test_image=$TEST_IMAGE"
}

cmd_apply() {
  tf_init
  step "terraform apply（今のイメージ。ビルドしない）"
  current_images
  tf_apply "$IMAGE" "$TEST_IMAGE"
}

# ジョブがあることを確かめる（無ければ配置を促して止める）
require_job() {
  gcloud run jobs describe "$1" --project "$PROJECT_ID" --region "$REGION" --format='value(metadata.name)' >/dev/null 2>&1 ||
    die "ジョブ $1 がまだ無い（bash deploy/deploy.sh で配置する）"
}

cmd_migrate() {
  tf_init
  local job
  job="$(tf_output migrate_job)"
  require_job "$job"
  step "DB の移行のジョブ $job を流して待つ"
  run_job "$job"
}

cmd_pgverify() {
  tf_init
  local job
  job="$(tf_output pgverify_job)"
  require_job "$job"
  step "PostgreSQL での検証のジョブ $job を流して待つ（検証用の DB pmt_verify。本番の DB は使わない）"
  run_job "$job"
}

main() {
  local cmd=${1:-deploy}
  case "$cmd" in
    deploy) cmd_deploy ;;
    plan) cmd_plan ;;
    apply) cmd_apply ;;
    migrate) cmd_migrate ;;
    pgverify) cmd_pgverify ;;
    url)
      tf_init >/dev/null
      cmd_url
      ;;
    -h | --help | help) usage ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
