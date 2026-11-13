# Cloud Run（設計書 1.2 の注・CR-020）：サーバが画面も配る 1 コンテナのサービスと、DB の移行・PostgreSQL での検証のジョブ。
# イメージ（var.image・var.test_image）が空なら作らない。

locals {
  cloudsql_volume = "cloudsql"
  # 秘密の値は Secret Manager の版を指して環境変数で渡す（NFR-10）。版が変われば新しいリビジョンになる
  secret_version = { for key, version in google_secret_manager_secret_version.app : key => version.version }
}

resource "google_cloud_run_v2_service" "app" {
  count    = local.deploy_app ? 1 : 0
  name     = "${local.name}-pmt"
  location = var.region
  # 利用者の認証はアプリのログイン（API-CMN-001）と共有リンク（API-RPT-004）で行う。IAM の呼出しの確認は切る
  ingress              = "INGRESS_TRAFFIC_ALL"
  invoker_iam_disabled = true
  # 状態を持たないので、消して作り直してよい（イメージを空にした apply は消す。deploy.sh は今のイメージを渡す）
  deletion_protection = false

  template {
    service_account                  = google_service_account.run.email
    max_instance_request_concurrency = 80
    timeout                          = "300s"

    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }

    volumes {
      name = local.cloudsql_volume
      cloud_sql_instance {
        instances = [google_sql_database_instance.main.connection_name]
      }
    }

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = var.run_memory
        }
        cpu_idle          = true # 要求を処理している間だけ CPU を割り当てる（費用）
        startup_cpu_boost = true
      }

      env {
        name = "PMT_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app["database-url"].secret_id
            version = local.secret_version["database-url"]
          }
        }
      }
      env {
        name  = "PMT_INITIAL_ADMIN_LOGIN"
        value = var.initial_admin_login
      }
      env {
        name = "PMT_INITIAL_ADMIN_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.app["initial-admin-password"].secret_id
            version = local.secret_version["initial-admin-password"]
          }
        }
      }
      env {
        name  = "PMT_API_DOCS"
        value = tostring(var.api_docs)
      }

      volume_mounts {
        name       = local.cloudsql_volume
        mount_path = "/cloudsql"
      }

      # 起動の確認：サーバと DB への接続が生きている（/api/v1/health）
      startup_probe {
        initial_delay_seconds = 0
        period_seconds        = 5
        timeout_seconds       = 3
        failure_threshold     = 12
        http_get {
          path = "/api/v1/health"
        }
      }
    }
  }

  depends_on = [
    google_project_iam_member.run_cloudsql_client,
    google_secret_manager_secret_iam_member.run_accessor,
  ]
}

# DB の移行（alembic upgrade head）。サービスを入れ替える前に流す（deploy.sh migrate）
resource "google_cloud_run_v2_job" "migrate" {
  count               = local.deploy_app ? 1 : 0
  name                = "${local.name}-migrate"
  location            = var.region
  deletion_protection = false

  template {
    task_count = 1

    template {
      service_account = google_service_account.run.email
      max_retries     = 0
      timeout         = "900s"

      volumes {
        name = local.cloudsql_volume
        cloud_sql_instance {
          instances = [google_sql_database_instance.main.connection_name]
        }
      }

      containers {
        image   = var.image
        command = ["python", "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"]

        resources {
          limits = {
            cpu    = "1"
            memory = var.run_memory
          }
        }

        env {
          name = "PMT_DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.app["database-url"].secret_id
              version = local.secret_version["database-url"]
            }
          }
        }

        volume_mounts {
          name       = local.cloudsql_volume
          mount_path = "/cloudsql"
        }
      }
    }
  }

  depends_on = [
    google_project_iam_member.run_cloudsql_client,
    google_secret_manager_secret_iam_member.run_accessor,
  ]
}

# PostgreSQL での検証（Dockerfile の test の段で pytest backend/tests）。検証用の DB（pmt_verify）だけを渡す。
# 身元は ksdp-verify（本番の秘密を読めない）、DB ユーザーは pmt_verifier（本番の表への権限が無い）。deploy.sh pgverify
resource "google_cloud_run_v2_job" "pgverify" {
  count               = local.deploy_test ? 1 : 0
  name                = "${local.name}-pgverify"
  location            = var.region
  deletion_protection = false

  template {
    task_count = 1

    template {
      service_account = google_service_account.verify.email
      max_retries     = 0
      timeout         = "3600s"

      volumes {
        name = local.cloudsql_volume
        cloud_sql_instance {
          instances = [google_sql_database_instance.main.connection_name]
        }
      }

      containers {
        image = var.test_image
        # 指令はイメージの CMD（python -m pytest -q -p no:cacheprovider backend/tests）

        resources {
          limits = {
            cpu    = "2"
            memory = "2Gi"
          }
        }

        env {
          name = "PMT_TEST_DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.app["verify-database-url"].secret_id
              version = local.secret_version["verify-database-url"]
            }
          }
        }

        volume_mounts {
          name       = local.cloudsql_volume
          mount_path = "/cloudsql"
        }
      }
    }
  }

  depends_on = [
    google_project_iam_member.verify_cloudsql_client,
    google_secret_manager_secret_iam_member.verify_accessor,
  ]
}
