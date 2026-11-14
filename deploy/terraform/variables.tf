# 変数（設計書 1.1・CR-020）。プロジェクト・リージョン・DB の大きさは設計書に書かず、ここに置く。
# 本番の値は poc.tfvars（project_id）と deploy/deploy.sh が渡す image・test_image。

variable "project_id" {
  description = "Google Cloud のプロジェクト ID。既定値は置かない（poc.tfvars で渡す）"
  type        = string
}

variable "region" {
  description = "Cloud Run・Cloud SQL・Artifact Registry・Secret Manager の複製を置くリージョン"
  type        = string
  default     = "asia-northeast1"
}

variable "prefix" {
  description = "資源の名前の接頭辞（既存の資源と名前を衝突させない。CR-020）"
  type        = string
  default     = "ksdp"

  validation {
    # サービスアカウントの ID（6〜30 文字）・Cloud Run の名前（63 文字まで）に収まる長さにする
    condition     = can(regex("^[a-z][a-z0-9-]{1,11}$", var.prefix))
    error_message = "prefix は英小文字で始まる英小文字・数字・ハイフンの 2〜12 文字にする。"
  }
}

variable "image" {
  description = "サーバのイメージ（Dockerfile の app の段。ダイジェストで指定する）。空なら Cloud Run のサービスと移行のジョブを作らない"
  type        = string
  default     = ""

  validation {
    condition     = var.image == "" || can(regex("^[a-z0-9.-]+(:[0-9]+)?/[^@[:space:]]+@sha256:[0-9a-f]{64}$", var.image))
    error_message = "image は空か、ダイジェストで指すイメージの名前（…@sha256:<64 桁>）にする。"
  }
}

variable "test_image" {
  description = "試験のイメージ（Dockerfile の test の段。ダイジェストで指定する）。空なら PostgreSQL の検証のジョブを作らない"
  type        = string
  default     = ""

  validation {
    condition     = var.test_image == "" || can(regex("^[a-z0-9.-]+(:[0-9]+)?/[^@[:space:]]+@sha256:[0-9a-f]{64}$", var.test_image))
    error_message = "test_image は空か、ダイジェストで指すイメージの名前（…@sha256:<64 桁>）にする。"
  }
}

variable "db_tier" {
  description = "Cloud SQL のマシンの型（ENTERPRISE のエディションは共有コア・カスタムの型が使える）"
  type        = string
  default     = "db-custom-1-3840"
}

variable "db_edition" {
  description = "Cloud SQL のエディション。PostgreSQL 16 は未指定だと ENTERPRISE_PLUS になるので明示する"
  type        = string
  default     = "ENTERPRISE"
}

variable "db_availability_type" {
  description = "ZONAL（1 つのゾーン）か REGIONAL（高可用性。費用はおよそ 2 倍）"
  type        = string
  default     = "ZONAL"
}

variable "db_activation_policy" {
  description = "ALWAYS（動かす）か NEVER（止める。ディスク・バックアップの費用は残る）"
  type        = string
  default     = "ALWAYS"

  validation {
    condition     = contains(["ALWAYS", "NEVER"], var.db_activation_policy)
    error_message = "db_activation_policy は ALWAYS か NEVER にする。"
  }
}

variable "db_backup_start_time" {
  description = "自動バックアップを始める時刻（UTC の HH:MM。既定 18:00 は日本時間の 3 時）"
  type        = string
  default     = "18:00"
}

variable "db_disk_autoresize_limit_gb" {
  description = "Cloud SQL のディスクを自動で増やす上限（GB。0 は上限なし）。ディスクは減らせない"
  type        = number
  default     = 50

  validation {
    condition     = floor(var.db_disk_autoresize_limit_gb) == var.db_disk_autoresize_limit_gb && (var.db_disk_autoresize_limit_gb == 0 || var.db_disk_autoresize_limit_gb >= 10)
    error_message = "db_disk_autoresize_limit_gb は整数で、0（上限なし）か 10 以上にする（最初のディスクが 10 GB）。"
  }
}

variable "registry_keep_recent" {
  description = "Artifact Registry に残すイメージの数（イメージ pmt・pmt-test ごとに新しい順）。前のイメージへ戻すときはこの中から選ぶ"
  type        = number
  default     = 10

  validation {
    condition     = floor(var.registry_keep_recent) == var.registry_keep_recent && var.registry_keep_recent >= 2
    error_message = "registry_keep_recent は 2 以上の整数にする（今のイメージと 1 つ前を残す）。"
  }
}

variable "registry_delete_older_than_days" {
  description = "Artifact Registry で、残す数より古いイメージを消すまでの日数"
  type        = number
  default     = 30

  validation {
    condition     = floor(var.registry_delete_older_than_days) == var.registry_delete_older_than_days && var.registry_delete_older_than_days >= 1
    error_message = "registry_delete_older_than_days は 1 以上の整数にする。"
  }
}

variable "db_deletion_protection" {
  description = "Cloud SQL のインスタンスの削除保護（設計書 8 NFR-06）。全てを片付けるときだけ偽にする"
  type        = bool
  default     = true
}

variable "initial_admin_login" {
  description = "最初のシステム管理者のログイン名（PMT_INITIAL_ADMIN_LOGIN。パスワードは Secret Manager に作る）"
  type        = string
  default     = "pmt-admin"
}

variable "api_docs" {
  description = "API の説明（/docs・/redoc・/api/v1/openapi.json）を出すか（PMT_API_DOCS。設計書 6.1）"
  type        = bool
  default     = false
}

variable "max_instances" {
  description = "Cloud Run のサービスの最大インスタンス数（1 つあたり DB の接続を最大 15 本使う）"
  type        = number
  default     = 2
}

variable "run_memory" {
  description = "Cloud Run のサービスと移行のジョブのメモリ"
  type        = string
  default     = "1Gi"
}
