#!/usr/bin/env bash
# 実ブラウザでの評価がログインして開くための、検証専用で読むだけの利用者を本番に用意する（spec-ids.toml の
# [[ui_runtime.login]]。CR-026。手順書は deploy/README.md の 12 章）。利用者が自分の端末で流す：
#
#   bash deploy/ui-verify-user.sh [<プロジェクトの ID>]
#
#   1. 検証専用の利用者のパスワードの秘密（<prefix>-ui-verify-password）が無ければ作り、有効な版が無ければ英数字 32 文字の値を置く
#      （ROTATE=1 なら新しい値を置く。置き場は Terraform の秘密と同じリージョン）。流したアカウントで latest を読めることを確かめる
#   2. 最初のシステム管理者（秘密 <prefix>-initial-admin-password）でログインし、書き込む前に次を確かめる。どれかに外れれば何も変えずに止まる
#      - 検証専用の利用者（既定 ui-verify）が既にあれば、この手順で作ったもの（表示名が下の DISPLAY_NAME で、システム管理者でない）か
#      - 使うプロジェクト（ID を渡さなければ、1 つだけのときはそれ。2 つ以上なら一覧を出して止まる）のロール×権限の表で、
#        VIEWER（閲覧者）が読む権限（project.view・data.export。設計書 9.1・9.2）しか持たないか
#   3. 利用者を作る（あれば、パスワードを秘密の値に合わせ、有効にし、システム管理者とプロジェクト作成の許可を外す）。
#      プロジェクトのメンバーにし、ロールを VIEWER だけにする
#   4. 検証専用の利用者でログインし、システムの権限が無く、どのプロジェクトでも読む権限しか持たないことを確かめる。
#      外れれば、そのプロジェクトのメンバーから外し、利用者を無効にしてから止まる（宣言は本番を指すので、書けるセッションを残さない）
#   5. spec-ids.toml に書く値（[[ui_runtime.login]] の origin・secret・username と、[ui_runtime].route_params の
#      projectId・elementId・reportId。どれも秘密ではない）を出す
#
# 秘密の値（2 つのパスワードと認証のトークン）は画面・コマンドの引数に出さない。置くのはシェルの変数・curl の標準入力・
# 権限 0700 の一時ディレクトリの中のファイルだけで、終われば（止まっても・出力が閉じても）2 つのトークンを失効させ、ディレクトリを消す。
# bash -x で流しても xtrace はこのファイルの先頭で止める。gcloud の HTTP の記録（core/log_http）もこの中では止める。
# 流すと検証専用の利用者のパスワードを設定し直すので、その利用者の今のセッションは失効する（ui-runtime の評価と同時に流さない）。
#
# 環境変数：
#   TFVARS           変数のファイル（既定 deploy/terraform/poc.tfvars。project_id・region・prefix・initial_admin_login を読む）
#   UI_VERIFY_LOGIN  検証専用の利用者のログイン名（既定 ui-verify。spec-ids.toml の [[ui_runtime.login]] の username と同じにする）
#   ROTATE=1         検証専用の利用者のパスワードを替える（秘密に新しい版を置き、利用者のパスワードを合わせる。古い版は残る）
#   PMT_URL          サービスの URL（既定は Cloud Run の <prefix>-pmt の status.url。https のオリジンだけ。手元の試しに
#                    http://127.0.0.1:<口>・http://localhost:<口> も受ける）
#   GCLOUD           gcloud の実行ファイル（既定 gcloud）
set +x
set -euo pipefail
export CLOUDSDK_CORE_LOG_HTTP=false

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TFVARS="${TFVARS:-$ROOT/deploy/terraform/poc.tfvars}"
GCLOUD="${GCLOUD:-gcloud}"
LOGIN="${UI_VERIFY_LOGIN:-ui-verify}"
ROTATE="${ROTATE:-}"
# この手順で作った利用者の印（既にある利用者は、この表示名でなければ作り替えない）
DISPLAY_NAME="画面の検証（読むだけ）"
ROLE="VIEWER"
# 読む権限（設計書 9.1。data.export は計画・実績を書き出すだけで、何も変えない）。この外の権限を持てば止まる
READ_ONLY_PERMISSIONS="project.view data.export"

die() {
  echo "ui-verify-user.sh: $*" >&2
  exit 1
}

step() {
  echo
  echo "== $*"
}

need() {
  command -v "$1" >/dev/null 2>&1 || die "$1 が見つからない"
}

# 変数の値：変数のファイル → TF_VAR_<名前> → 既定（deploy.sh と同じ読み方）
tfvar() {
  local name=$1 default=$2 value env_name="TF_VAR_$1"
  value=$(sed -n -E "s/^[[:space:]]*${name}[[:space:]]*=[[:space:]]*\"([^\"]*)\".*$/\1/p" "$TFVARS" | tail -n 1)
  if [ -z "$value" ]; then
    value="${!env_name:-$default}"
  fi
  printf '%s' "$value"
}

need "$GCLOUD"
need curl
need python3
[ -f "$TFVARS" ] || die "変数のファイルが無い：$TFVARS"

PROJECT_ID="$(tfvar project_id "")"
REGION="$(tfvar region asia-northeast1)"
PREFIX="$(tfvar prefix ksdp)"
ADMIN_LOGIN="$(tfvar initial_admin_login pmt-admin)"
[ -n "$PROJECT_ID" ] || die "project_id が決まらない（$TFVARS に書く）"
ADMIN_SECRET="${PREFIX}-initial-admin-password"
VERIFY_SECRET="${PREFIX}-ui-verify-password"

case "$LOGIN" in
  "" | *[!A-Za-z0-9._-]*) die "UI_VERIFY_LOGIN は英数字と . _ - だけにする：$LOGIN" ;;
esac
[ "$LOGIN" != "$ADMIN_LOGIN" ] || die "UI_VERIFY_LOGIN に最初のシステム管理者（$ADMIN_LOGIN）は使えない"
case "$ROTATE" in
  "" | 1) ;;
  *) die "ROTATE は 1 か空にする：$ROTATE" ;;
esac
PMT_PROJECT="${1:-}"
if [ -n "$PMT_PROJECT" ] && ! [[ "$PMT_PROJECT" =~ ^[1-9][0-9]*$ ]]; then
  die "プロジェクトの ID は正の整数で渡す：$PMT_PROJECT"
fi

if [ -z "${PMT_URL:-}" ]; then
  # GA の describe は v1 の Service を返す（URL は status.url）
  PMT_URL="$("$GCLOUD" run services describe "${PREFIX}-pmt" --region "$REGION" --project "$PROJECT_ID" --format='value(status.url)')" \
    || die "サービス ${PREFIX}-pmt の URL を読めない"
fi
PMT_URL="${PMT_URL%/}"
# オリジンだけを受ける（パスワードを平文や別のホストへ送らない。http は手元の試しだけ）
if ! [[ "$PMT_URL" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?$ || "$PMT_URL" =~ ^http://(127\.0\.0\.1|localhost):[0-9]+$ ]]; then
  die "PMT_URL は https のオリジン（https://<ホスト>）にする：${PMT_URL:-（空）}"
fi

umask 077
WORK="$(mktemp -d)"
ADMIN_HEADERS="$WORK/admin.headers"
VERIFY_HEADERS="$WORK/verify.headers"
USER_ID=""
MEMBER_ADDED=""

# api <メソッド> <パス> <ヘッダのファイル | -> [body]：応答の本文を $WORK/resp に置き、HTTP の状態を出す。
# body を付けたときは本文（JSON）を標準入力から送る。リダイレクトに従わない（curl の既定）
api() {
  local method=$1 path=$2 headers=$3 body=${4:-}
  local args=(-sS --max-time 60 -o "$WORK/resp" -w '%{http_code}' -X "$method")
  if [ "$headers" != "-" ]; then
    args+=(-H "@$headers")
  fi
  if [ -n "$body" ]; then
    args+=(-H 'Content-Type: application/json' --data-binary @-)
  elif [ "$method" = "POST" ] || [ "$method" = "PUT" ] || [ "$method" = "PATCH" ]; then
    # Cloud Run の前段は Content-Length の無い POST を HTTP 411 で断る（curl は本文が無いと付けない）
    args+=(-H 'Content-Length: 0')
  fi
  # つなげなければ curl は状態 000 を出す（expect が止める）
  curl "${args[@]}" "$PMT_URL/api/v1$path" || true
}

# py <プログラム> [引数…]：応答の本文（JSON）を標準入力として読む
py() {
  python3 -I -c "$1" "${@:2}" <"$WORK/resp"
}

# 期待した状態でなければ止まる。応答の本文は出さない（トークンが入りうる）。誤りのコード（設計書 2.4 の error.code）だけを出す
expect() {
  local got=$1 want=$2 what=$3 code
  [ "$got" = "$want" ] && return 0
  [ "$got" != "000" ] || die "$what：$PMT_URL へつなげなかった（上の curl の誤りを見る）"
  code="$(py 'import json, sys
try:
    error = json.load(sys.stdin).get("error") or {}
    print(error.get("code", "") if isinstance(error, dict) else "")
except Exception:
    print("")' 2>/dev/null || true)"
  die "$what が HTTP $got を返した（期待は $want${code:+。誤りのコード $code}）"
}

# ログインの本文。パスワードは標準入力から受ける（引数に出さない）
login_body() {
  python3 -I -c 'import json, sys; sys.stdout.write(json.dumps({"login_id": sys.argv[1], "password": sys.stdin.read()}))' "$1"
}

# ログインして、トークンを Authorization のヘッダのファイルに置く
login_to() {
  local login=$1 password=$2 headers=$3 status
  status="$(printf '%s' "$password" | login_body "$login" | api POST /auth/login - body)"
  expect "$status" 200 "$login でのログイン（API-CMN-001）"
  py 'import json, sys; sys.stdout.write("Authorization: Bearer " + json.load(sys.stdin)["access_token"] + "\n")' >"$headers"
  : >"$WORK/resp"
}

# トークンを失効させ、ヘッダのファイルを消す。HTTP の状態を出す
logout_from() {
  local headers=$1 status
  status="$(api POST /auth/logout "$headers")"
  rm -f "$headers"
  printf '%s' "${status:-000}"
}

# 終わり（止まったとき・出力が閉じたときも）：先に 2 つのトークンを失効させてディレクトリを消し、表示は最後に標準エラーへ出す
cleanup() {
  set +e
  trap '' PIPE
  local admin_status="" verify_status=""
  [ ! -s "$VERIFY_HEADERS" ] || verify_status="$(logout_from "$VERIFY_HEADERS")"
  [ ! -s "$ADMIN_HEADERS" ] || admin_status="$(logout_from "$ADMIN_HEADERS")"
  rm -rf "$WORK"
  if [ -n "$verify_status" ] && [ "$verify_status" != "204" ]; then
    echo "注意：$LOGIN のトークンを失効させられなかった（HTTP $verify_status。期限まで残る）" >&2 || true
  fi
  if [ "$admin_status" = "204" ]; then
    echo "最初のシステム管理者のトークンを失効させた" >&2 || true
  elif [ -n "$admin_status" ]; then
    echo "注意：最初のシステム管理者のトークンを失効させられなかった（HTTP $admin_status。期限まで残る）" >&2 || true
  fi
}
trap cleanup EXIT

# 読む権限を超えていたら、足したメンバーの行を外し、利用者を無効にしてから止まる
revoke_and_die() {
  local why=$1 removed="外していない" disabled
  # 無効にした後はトークンを失効させる API が 401 になるので、先に失効させる
  [ ! -s "$VERIFY_HEADERS" ] || logout_from "$VERIFY_HEADERS" >/dev/null
  if [ -n "$MEMBER_ADDED" ]; then
    removed="HTTP $(api DELETE "/projects/$PMT_PROJECT/members/$USER_ID" "$ADMIN_HEADERS")"
  fi
  disabled="$(printf '{"is_active": false}' | api PATCH "/users/$USER_ID" "$ADMIN_HEADERS" body)"
  die "$why。$LOGIN をプロジェクト $PMT_PROJECT のメンバーから外し（$removed）、無効にした（HTTP $disabled）。直してから流し直す"
}

echo "サービス：$PMT_URL"
echo "検証専用の利用者：$LOGIN（ロール $ROLE）"

step "1. 検証専用の利用者のパスワードの秘密（$VERIFY_SECRET）"
if "$GCLOUD" secrets describe "$VERIFY_SECRET" --project "$PROJECT_ID" >/dev/null 2>&1; then
  echo "秘密はある"
else
  "$GCLOUD" secrets create "$VERIFY_SECRET" --project "$PROJECT_ID" \
    --replication-policy=user-managed --locations="$REGION" --labels=purpose=ui-verify >/dev/null
  echo "秘密を作った"
fi
ENABLED="$("$GCLOUD" secrets versions list "$VERIFY_SECRET" --project "$PROJECT_ID" --filter='state=ENABLED' --limit=1 --format='value(name)')" \
  || die "秘密 $VERIFY_SECRET の版の一覧を読めない"
if [ -z "$ENABLED" ] || [ "$ROTATE" = "1" ]; then
  python3 -I -c 'import secrets, string, sys
alphabet = string.ascii_letters + string.digits
sys.stdout.write("".join(secrets.choice(alphabet) for _ in range(32)))' \
    | "$GCLOUD" secrets versions add "$VERIFY_SECRET" --project "$PROJECT_ID" --data-file=- >/dev/null
  echo "英数字 32 文字の値を新しい版として置いた（値は出さない）"
fi
VERIFY_PASSWORD="$("$GCLOUD" secrets versions access latest --secret "$VERIFY_SECRET" --project "$PROJECT_ID")" \
  || die "秘密 $VERIFY_SECRET の latest を読めない（流したアカウントに secretmanager.versions.access が要る。最も新しい版が無効なら ROTATE=1 で流す）"
case "$VERIFY_PASSWORD" in
  "" | *[!A-Za-z0-9]*) die "秘密 $VERIFY_SECRET の値が英数字だけでない（この手順は自分で作った英数字の値だけを扱う）" ;;
esac
[ "${#VERIFY_PASSWORD}" -ge 12 ] || die "秘密 $VERIFY_SECRET の値が 12 文字より短い（NFR-04）"
echo "流したアカウントで latest を読めた（値は出さない）"

step "2. 最初のシステム管理者（$ADMIN_LOGIN）でログインし、変える前に確かめる"
ADMIN_PASSWORD="$("$GCLOUD" secrets versions access latest --secret "$ADMIN_SECRET" --project "$PROJECT_ID")" \
  || die "秘密 $ADMIN_SECRET の latest を読めない"
login_to "$ADMIN_LOGIN" "$ADMIN_PASSWORD" "$ADMIN_HEADERS"
unset ADMIN_PASSWORD

status="$(api GET '/users?limit=1000' "$ADMIN_HEADERS")"
expect "$status" 200 "利用者の一覧（API-CMN-003）"
# 既にある同じログイン名の利用者は、この手順で作ったもの（表示名が印のもので、システム管理者でない）だけを合わせる
USER_ID="$(py 'import json, sys
data = json.load(sys.stdin)
login, mark = sys.argv[1], sys.argv[2]
found = [u for u in data["items"] if u["login_id"] == login]
if not found and data["total"] > len(data["items"]):
    sys.exit("利用者が 1000 人を超えていて、一覧の先頭に " + login + " が無い")
if found:
    user = found[0]
    if user["display_name"] != mark or user["is_system_admin"]:
        sys.exit("ログイン名 " + login + " の利用者が既にあり、この手順で作った利用者ではない（表示名が「" + mark
                 + "」でないか、システム管理者）。作り替えないので、別の UI_VERIFY_LOGIN を使う")
    print(user["id"])' "$LOGIN" "$DISPLAY_NAME")" || exit 1
if [ -n "$USER_ID" ]; then
  echo "利用者 $LOGIN（ID $USER_ID）はこの手順で作ったもの"
else
  echo "利用者 $LOGIN はまだ無い（3 で作る）"
fi

status="$(api GET '/projects?limit=1000' "$ADMIN_HEADERS")"
expect "$status" 200 "プロジェクトの一覧（API-PRJ-002）"
# ID・コード・名前（表示できない文字は ? に置き換える）
PROJECTS="$(py 'import json, sys
data = json.load(sys.stdin)
if data["total"] > len(data["items"]):
    sys.exit("プロジェクトが 1000 件を超える（この手順は先頭の 1000 件しか見ない）")
clean = lambda s: "".join(c if c.isprintable() else "?" for c in str(s))
for p in data["items"]:
    print("%s\t%s\t%s" % (p["id"], clean(p["code"]), clean(p["name"])))')" || exit 1
COUNT=0
[ -z "$PROJECTS" ] || COUNT="$(awk 'END { print NR }' <<<"$PROJECTS")"
if [ -z "$PMT_PROJECT" ]; then
  if [ "$COUNT" = "1" ]; then
    PMT_PROJECT="$(cut -f1 <<<"$PROJECTS")"
  elif [ "$COUNT" = "0" ]; then
    die "プロジェクトが 1 つも無い。画面の検証に使うプロジェクトを作ってから流し直す"
  else
    echo "プロジェクトが $COUNT 個ある（ID・コード・名前）："
    sed 's/^/  /' <<<"$PROJECTS"
    die "検証に使うプロジェクトの ID を渡して流し直す：bash deploy/ui-verify-user.sh <ID>"
  fi
fi
CHOSEN="$(awk -F '\t' -v id="$PMT_PROJECT" '$1 == id { print $1 "（" $2 " " $3 "）" }' <<<"$PROJECTS")"
[ -n "$CHOSEN" ] || die "ID $PMT_PROJECT のプロジェクトが無い"
echo "プロジェクト：$CHOSEN"

status="$(api GET "/projects/$PMT_PROJECT/role-permissions" "$ADMIN_HEADERS")"
expect "$status" 200 "ロール×権限の表（API-CMN-006）"
py 'import json, sys
allowed, role = set(sys.argv[1].split()), sys.argv[2]
grants = [set(r["permissions"]) for r in json.load(sys.stdin)["roles"] if r["role"] == role]
extra = sorted(set().union(*grants) - allowed) if grants else []
if extra:
    sys.exit("このプロジェクトの " + role + " が読む以外の権限を持つ：" + ", ".join(extra) + "（ロール×権限の表を確かめる。何も変えていない）")
print("このプロジェクトの " + role + " の権限：" + (", ".join(sorted(set().union(*grants))) if grants else "なし"))' \
  "$READ_ONLY_PERMISSIONS" "$ROLE" || exit 1

step "3. $LOGIN を作るか合わせ、プロジェクト $PMT_PROJECT のメンバー（ロール $ROLE だけ）にする"
if [ -z "$USER_ID" ]; then
  status="$(printf '%s' "$VERIFY_PASSWORD" | python3 -I -c 'import json, sys
sys.stdout.write(json.dumps({
    "login_id": sys.argv[1], "display_name": sys.argv[2], "password": sys.stdin.read(),
    "is_system_admin": False, "can_create_project": False,
}))' "$LOGIN" "$DISPLAY_NAME" | api POST /users "$ADMIN_HEADERS" body)"
  expect "$status" 201 "利用者の作成（API-CMN-004）"
  USER_ID="$(py 'import json, sys; print(json.load(sys.stdin)["id"])')"
  echo "利用者 $LOGIN（ID $USER_ID）を作った"
else
  status="$(printf '%s' "$VERIFY_PASSWORD" | python3 -I -c 'import json, sys
sys.stdout.write(json.dumps({
    "password": sys.stdin.read(), "is_system_admin": False, "can_create_project": False, "is_active": True,
}))' | api PATCH "/users/$USER_ID" "$ADMIN_HEADERS" body)"
  expect "$status" 200 "利用者の変更（API-CMN-005）"
  echo "利用者 $LOGIN のパスワードを秘密の値に合わせ、有効にし、システム管理者とプロジェクト作成の許可を外した（今のセッションは失効する）"
fi

status="$(printf '{"roles": ["%s"]}' "$ROLE" | api PUT "/projects/$PMT_PROJECT/members/$USER_ID" "$ADMIN_HEADERS" body)"
expect "$status" 200 "メンバーの設定（API-PRJ-010）"
MEMBER_ADDED=1
echo "$LOGIN をメンバーにし、ロールを $ROLE だけにした"

step "4. $LOGIN でログインし、読む権限しか持たないことを確かめる"
login_to "$LOGIN" "$VERIFY_PASSWORD" "$VERIFY_HEADERS"
unset VERIFY_PASSWORD
status="$(api GET /me "$VERIFY_HEADERS")"
expect "$status" 200 "自分の情報（API-CMN-008）"
if ! py 'import json, sys
me = json.load(sys.stdin)
allowed = set(sys.argv[1].split())
project = int(sys.argv[2])
problems = []
if me["system_permissions"]:
    problems.append("システムの権限がある：" + ", ".join(sorted(me["system_permissions"])))
seen = False
for p in me["projects"]:
    extra = sorted(set(p["permissions"]) - allowed)
    if extra:
        problems.append("プロジェクト %s で読む以外の権限がある：%s" % (p["project_id"], ", ".join(extra)))
    if p["project_id"] == project:
        seen = True
        print("プロジェクト %s：ロール %s・権限 %s" % (project, ", ".join(p["roles"]), ", ".join(sorted(p["permissions"]))))
if not seen:
    problems.append("プロジェクト %s を見られない" % project)
print("システムの権限：%s／メンバーのプロジェクト %d 個" % (", ".join(me["system_permissions"]) or "なし", len(me["projects"])))
if problems:
    print("。".join(problems), file=sys.stderr)
    sys.exit(1)' "$READ_ONLY_PERMISSIONS" "$PMT_PROJECT"; then
  revoke_and_die "読む権限を超えている"
fi
status="$(logout_from "$VERIFY_HEADERS")"
[ "$status" = "204" ] || die "$LOGIN のログアウト（API-CMN-002）が HTTP $status を返した"
echo "$LOGIN をログアウトした"

step "5. spec-ids.toml に書く値（秘密ではない）"
status="$(api GET "/projects/$PMT_PROJECT/wbs" "$ADMIN_HEADERS")"
expect "$status" 200 "WBS（API-WBS-001）"
ELEMENT="$(py 'import json, sys
elements = json.load(sys.stdin)["elements"]
clean = lambda s: "".join(c if c.isprintable() else "?" for c in str(s))
print("%s\t%s" % (elements[0]["id"], clean(elements[0]["name"])) if elements else "")')"
# 報告日は古い順。1000 件を超えるときは最後のページを読む
status="$(api GET "/projects/$PMT_PROJECT/status-reports?limit=1" "$ADMIN_HEADERS")"
expect "$status" 200 "報告日の一覧（API-PRG-002）"
OFFSET="$(py 'import json, sys; print(max(json.load(sys.stdin)["total"] - 1000, 0))')"
status="$(api GET "/projects/$PMT_PROJECT/status-reports?limit=1000&offset=$OFFSET" "$ADMIN_HEADERS")"
expect "$status" 200 "報告日の一覧（API-PRG-002）"
REPORT="$(py 'import json, sys
items = json.load(sys.stdin)["items"]
latest = max(items, key=lambda r: (r["report_date"], r["id"])) if items else None
print("%s\t%s" % (latest["id"], latest["report_date"]) if latest else "")')"

ORIGIN="$PMT_URL"
echo "[[ui_runtime.login]]"
echo "  origin   = \"$ORIGIN\""
echo "  secret   = \"projects/$PROJECT_ID/secrets/$VERIFY_SECRET/versions/latest\""
echo "  username = \"$LOGIN\""
echo "[ui_runtime].route_params"
echo "  projectId = \"$PMT_PROJECT\""
if [ -n "$ELEMENT" ]; then
  echo "  elementId = \"$(cut -f1 <<<"$ELEMENT")\"   # WBS の先頭の要素（$(cut -f2 <<<"$ELEMENT")）"
else
  echo "  elementId = （無い：WBS に要素が無い。WBS の要素の詳細の画面は見ないことになる）"
fi
if [ -n "$REPORT" ]; then
  echo "  reportId  = \"$(cut -f1 <<<"$REPORT")\"   # 最も新しい報告日（$(cut -f2 <<<"$REPORT")）"
else
  echo "  reportId  = （無い：報告日が無い。報告日・遅れ・会議の 3 画面は見ないことになる）"
fi
