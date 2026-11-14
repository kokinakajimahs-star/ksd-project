#!/usr/bin/env bash
# バックアップから戻せることを確かめる訓練（設計書 8 NFR-06。手順書は deploy/README.md の 6.4。A-51）。利用者が流す：
#
#   bash deploy/restore-drill.sh [clone|restore-backup|check|cleanup|status|wait]
#
#   clone           本番のインスタンスを、ある時刻の状態で訓練用のインスタンスへ複製する（ポイントインタイムリカバリ。README 6.3）
#                   時刻の既定は 10 分前（DRILL_POINT_IN_TIME で UTC の時刻を渡せる）
#   restore-backup  本番の最新の自動バックアップを、訓練用のインスタンスへ戻す（README 6.2 と同じ操作を、本番でなく訓練用へ）
#   check [名前]     インスタンスの DB pmt の件数と最後の変更履歴の時刻を読む（既定は訓練用。本番と比べるなら本番の名前を渡す）。
#                   CSV の書き出し（gcloud sql export csv）で読むので、DB へつながない・パスワードを使わない
#   cleanup         訓練用のインスタンスと書き出しの置き場を消す
#   status          訓練用のインスタンスの状態と、続いている操作を出す
#   wait <操作の ID>  Cloud SQL の操作の終わりを上限なしで待ち、掛かった時間を出す（clone・restore-backup は自分で待つ。
#                   gcloud の待ちが途中で打ち切られたときに使う）
#
# 本番のインスタンスへは、複製の元・バックアップの元・読む（check）としてしか触れない。書き込む先の名前は -drill を含むものに限る。
# 本番のデータは変えないが、複製と CSV の書き出しは本番のインスタンスの操作として走る（同時に 1 つ。その間の deploy.sh apply は待たされるか失敗する）。
# 訓練用のインスタンスは Terraform の外の資源で、残している間は本番と同じほどの費用が掛かる。終えたら cleanup を流す。
#
# 環境変数：
#   TFVARS               変数のファイル（既定 deploy/terraform/poc.tfvars。project_id・region・prefix を読む）
#   DRILL_INSTANCE       訓練用のインスタンスの名前（既定 <prefix>-pg-drill。-drill で終わるか -drill- を含む名前だけ）。
#                        消したインスタンスの名前はしばらく（最長 1 週間ほど）使えないので、続けて訓練するなら別の名前（例 ksdp-pg-drill-2）を渡す
#   DRILL_POINT_IN_TIME  clone の時刻（UTC。例 2026-11-14T01:23:45.000Z）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TFVARS="${TFVARS:-$ROOT/deploy/terraform/poc.tfvars}"

die() {
  echo "restore-drill.sh: $*" >&2
  exit 1
}

step() {
  echo
  echo "== $* （$(date -u +%Y-%m-%dT%H:%M:%SZ)）"
}

usage() {
  sed -n '2,/^set -euo pipefail$/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'
}

[ -f "$TFVARS" ] || die "変数のファイルが無い：$TFVARS"

# 変数の値：変数のファイル → TF_VAR_<名前> → 既定（deploy.sh と同じ読み方）
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
SOURCE_INSTANCE="${PREFIX}-pg"
DRILL_INSTANCE="${DRILL_INSTANCE:-${PREFIX}-pg-drill}"
# 訓練だけの置き場。README 6.3 の本当の復旧の置き場（<prefix>-restore-<project>）とは別にし、cleanup で消しても復旧の取り込み元を消さない
BUCKET="gs://${PREFIX}-restore-drill-${PROJECT_ID}"

# 書き込む先（複製の先・バックアップを戻す先・消す先）は、名前に -drill を含む訓練用のインスタンスだけにする。
# 本番（<prefix>-pg）や本当の復旧のクローン（README 6.3 の <prefix>-pg-pitr）を渡しても止まる
case "$DRILL_INSTANCE" in
  *-drill | *-drill-*) ;;
  *) die "訓練用のインスタンスの名前は -drill で終わるか -drill- を含める（渡された名前：$DRILL_INSTANCE。本番は $SOURCE_INSTANCE）" ;;
esac

# 件数を読む問い合わせ（表の持ち主・列は backend/app/models。秘密の値は読まない）
CHECK_COLUMNS="schema_version,users,projects,wbs_elements,activities,time_entries,audit_logs,last_audit_utc"
CHECK_QUERY="SELECT (SELECT version_num FROM alembic_version), (SELECT count(*) FROM users), (SELECT count(*) FROM projects), \
(SELECT count(*) FROM wbs_elements), (SELECT count(*) FROM activities), (SELECT count(*) FROM time_entries), \
(SELECT count(*) FROM audit_logs), (SELECT max(occurred_at) FROM audit_logs)"

instance_exists() {
  gcloud sql instances describe "$1" --project "$PROJECT_ID" --format='value(name)' >/dev/null 2>&1
}

cmd_status() {
  step "訓練用のインスタンス $DRILL_INSTANCE"
  if instance_exists "$DRILL_INSTANCE"; then
    gcloud sql instances describe "$DRILL_INSTANCE" --project "$PROJECT_ID" \
      --format='table(name,state,databaseVersion,settings.tier,createTime)'
    gcloud sql operations list --instance "$DRILL_INSTANCE" --project "$PROJECT_ID" --limit 5 \
      --format='table(operationType,status,startTime,endTime,error.errors[0].code)'
  else
    echo "無い"
  fi
}

# Cloud SQL の操作の終わりを待ち、状態と掛かった時間（操作の記録の開始・終了の時刻）を出す。
# gcloud の既定の待ちは 10 分ほどで打ち切られ（操作は続く）、複製はそれより長く掛かることがあるので、上限なしで待つ
cmd_wait() {
  local op=$1
  [ -n "$op" ] || die "待つ操作の ID を渡す（例：bash deploy/restore-drill.sh wait <操作の ID>）"
  echo "操作 $op の終わりを待つ（status で途中の状態を見られる）"
  gcloud sql operations wait "$op" --project "$PROJECT_ID" --timeout=unlimited >/dev/null
  gcloud sql operations describe "$op" --project "$PROJECT_ID" \
    --format='table(operationType,status,startTime,endTime,error.errors[0].code)'
}

cmd_clone() {
  local point op
  point="${DRILL_POINT_IN_TIME:-$(date -u -d '-10 min' +%Y-%m-%dT%H:%M:%S.000Z)}"
  instance_exists "$DRILL_INSTANCE" && die "$DRILL_INSTANCE が既にある（cleanup で消してから流す）"
  step "$SOURCE_INSTANCE を $point（UTC）の状態で $DRILL_INSTANCE へ複製する（10〜20 分ほど掛かる）"
  op=$(gcloud sql instances clone "$SOURCE_INSTANCE" "$DRILL_INSTANCE" --point-in-time="$point" \
    --project "$PROJECT_ID" --async --format='value(name)')
  cmd_wait "$op"
  echo "複製した時刻：$point（掛かった時間は上の startTime〜endTime）"
  cmd_status
}

cmd_restore_backup() {
  instance_exists "$DRILL_INSTANCE" || die "$DRILL_INSTANCE が無い（先に clone を流す。バックアップは今あるインスタンスへ戻す）"
  local backup_id backup_end
  backup_id=$(gcloud sql backups list --instance "$SOURCE_INSTANCE" --project "$PROJECT_ID" \
    --filter='status=SUCCESSFUL AND type=AUTOMATED' --sort-by='~endTime' --limit 1 --format='value(id)')
  [ -n "$backup_id" ] || die "$SOURCE_INSTANCE に成功した自動バックアップが無い"
  backup_end=$(gcloud sql backups describe "$backup_id" --instance "$SOURCE_INSTANCE" --project "$PROJECT_ID" \
    --format='value(endTime)')
  step "$SOURCE_INSTANCE のバックアップ $backup_id（$backup_end に取得）を $DRILL_INSTANCE へ戻す（訓練用の中身は置き換わる）"
  local op
  op=$(gcloud sql backups restore "$backup_id" --backup-instance="$SOURCE_INSTANCE" \
    --restore-instance="$DRILL_INSTANCE" --project "$PROJECT_ID" --quiet --async --format='value(name)')
  cmd_wait "$op"
  echo "戻したバックアップ：$backup_id（$backup_end に取得。掛かった時間は上の startTime〜endTime）"
  cmd_status
}

# 書き出しの置き場（訓練だけのもの）を用意し、そのインスタンスの身元に読み書きを許す
prepare_bucket() {
  local instance=$1 sql_sa
  if ! gcloud storage buckets describe "$BUCKET" --project "$PROJECT_ID" --format='value(name)' >/dev/null 2>&1; then
    gcloud storage buckets create "$BUCKET" --project "$PROJECT_ID" --location "$REGION" \
      --uniform-bucket-level-access --public-access-prevention
  fi
  sql_sa=$(gcloud sql instances describe "$instance" --project "$PROJECT_ID" --format='value(serviceAccountEmailAddress)')
  gcloud storage buckets add-iam-policy-binding "$BUCKET" --member="serviceAccount:$sql_sa" \
    --role=roles/storage.objectAdmin --project "$PROJECT_ID" >/dev/null
}

cmd_check() {
  local instance=${1:-$DRILL_INSTANCE} object
  instance_exists "$instance" || die "$instance が無い"
  object="$BUCKET/check-${instance}-$(date -u +%Y%m%dT%H%M%SZ).csv"
  step "$instance の DB pmt の件数を読む"
  prepare_bucket "$instance"
  gcloud sql export csv "$instance" "$object" --database=pmt --query="$CHECK_QUERY" --project "$PROJECT_ID" >/dev/null
  echo "$CHECK_COLUMNS"
  gcloud storage cat "$object"
}

cmd_cleanup() {
  step "$DRILL_INSTANCE を消す"
  if instance_exists "$DRILL_INSTANCE"; then
    gcloud sql instances patch "$DRILL_INSTANCE" --no-deletion-protection --project "$PROJECT_ID" --quiet
    gcloud sql instances delete "$DRILL_INSTANCE" --project "$PROJECT_ID" --quiet
  else
    echo "無い"
  fi
  step "書き出しの置き場 $BUCKET を消す"
  if gcloud storage buckets describe "$BUCKET" --project "$PROJECT_ID" --format='value(name)' >/dev/null 2>&1; then
    gcloud storage rm --recursive "$BUCKET"
  else
    echo "無い"
  fi
}

main() {
  command -v gcloud >/dev/null 2>&1 || die "gcloud が見つからない（deploy/README.md の前提を見る）"
  local cmd=${1:-}
  case "$cmd" in
    clone) cmd_clone ;;
    restore-backup) cmd_restore_backup ;;
    check)
      shift
      cmd_check "${1:-}"
      ;;
    cleanup) cmd_cleanup ;;
    status) cmd_status ;;
    wait)
      shift
      cmd_wait "${1:-}"
      ;;
    -h | --help | help | "") usage ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
