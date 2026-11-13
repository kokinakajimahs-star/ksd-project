# Cloud SQL for PostgreSQL 16（設計書 1.1・1.2・8 NFR-06）と、接続先の秘密（NFR-10。Secret Manager）。

resource "google_sql_database_instance" "main" {
  name             = "${local.name}-pg"
  database_version = "POSTGRES_16"
  region           = var.region
  # Terraform からの削除を止める（NFR-06）。GCP の側の削除保護は settings の deletion_protection_enabled
  deletion_protection = var.db_deletion_protection

  settings {
    tier              = var.db_tier
    edition           = var.db_edition
    availability_type = var.db_availability_type
    activation_policy = var.db_activation_policy
    disk_type         = "PD_SSD"
    disk_autoresize   = true

    deletion_protection_enabled = var.db_deletion_protection

    # 自動バックアップを日次で取り 7 世代を残す。ポイントインタイムリカバリ（7 日分のログ）を有効にする（NFR-06）
    backup_configuration {
      enabled                        = true
      start_time                     = var.db_backup_start_time
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7

      backup_retention_settings {
        retained_backups = 7
        retention_unit   = "COUNT"
      }
    }

    # 公開の IP は持つが、許可するネットワークを置かない（直接はつなげない）。Cloud Run は Cloud SQL の接続（Unix ソケット）でつなぐ。
    # 暗号化していない接続を拒む
    ip_configuration {
      ipv4_enabled = true
      ssl_mode     = "ENCRYPTED_ONLY"
    }

    # 保守は日本時間の月曜 3 時（UTC の日曜 18 時）
    maintenance_window {
      day  = 7
      hour = 18
    }
  }

  depends_on = [google_project_service.enabled]
}

# 本番の DB。Terraform の変更で消さない（消すなら deletion_policy を外してから）
resource "google_sql_database" "pmt" {
  name            = "pmt"
  instance        = google_sql_database_instance.main.name
  deletion_policy = "PREVENT"
}

# PostgreSQL での検証（pytest。検証のジョブ）が使う DB。本番の DB とは別
resource "google_sql_database" "pmt_verify" {
  name     = "pmt_verify"
  instance = google_sql_database_instance.main.name
}

# アプリの DB ユーザー（表の持ち主になる）。PostgreSQL は持ち物のあるユーザーを API で消せないので、片付けでは管理から外すだけにする
resource "random_password" "db_app" {
  length  = 32
  special = false # URL・alembic の設定の % の解釈に掛からないよう英数字だけにする
}

resource "google_sql_user" "app" {
  name            = "pmt_app"
  instance        = google_sql_database_instance.main.name
  password        = random_password.db_app.result
  deletion_policy = "ABANDON"
}

# 最初のシステム管理者のパスワード（PMT_INITIAL_ADMIN_PASSWORD。NFR-04 の方針：12 文字以上・ログイン名と違う）
resource "random_password" "initial_admin" {
  length  = 24
  special = false
}

locals {
  # psycopg 3 で Cloud Run の Cloud SQL の接続（/cloudsql/<接続名> の Unix ソケット）へつなぐ URL
  db_socket_host = "/cloudsql/${google_sql_database_instance.main.connection_name}"
  db_url_prefix  = "postgresql+psycopg://${google_sql_user.app.name}:${random_password.db_app.result}@"

  # 秘密の名前（for_each のキー。値が秘密なので、キーは値と別の固定の集合にする）と値
  secret_names = toset(["database-url", "verify-database-url", "initial-admin-password"])
  secrets = {
    database-url           = "${local.db_url_prefix}/${google_sql_database.pmt.name}?host=${local.db_socket_host}"
    verify-database-url    = "${local.db_url_prefix}/${google_sql_database.pmt_verify.name}?host=${local.db_socket_host}"
    initial-admin-password = random_password.initial_admin.result
  }
}

resource "google_secret_manager_secret" "app" {
  for_each  = local.secret_names
  secret_id = "${local.name}-${each.key}"

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_version" "app" {
  for_each    = google_secret_manager_secret.app
  secret      = each.value.id
  secret_data = local.secrets[each.key]
}

# 秘密を読めるのは Cloud Run の身元だけ（秘密ごとに与える）
resource "google_secret_manager_secret_iam_member" "run_accessor" {
  for_each  = google_secret_manager_secret.app
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.run.member
}
