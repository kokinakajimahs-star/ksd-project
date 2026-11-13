# 本番の配置（設計書 1.1・1.2・8 NFR-06・NFR-10。CR-020）：Google Cloud の Cloud Run（1 コンテナ）・Cloud SQL for PostgreSQL・
# Secret Manager・Artifact Registry・Cloud Build。資源の名前は全て "${var.prefix}-…"。
# 手順は deploy/README.md、流し方は deploy/deploy.sh。

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  name = var.prefix
  # イメージが無ければ Cloud Run のサービス・ジョブを作らない（最初の apply はイメージのビルドの前に流す）
  deploy_app  = var.image != ""
  deploy_test = var.test_image != ""
  image_repo  = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

# --- 要る API --------------------------------------------------------------------------------------
resource "google_project_service" "enabled" {
  for_each = toset([
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
  ])
  service = each.value
  # 片付けで API を止めない（同じプロジェクトの既存の資源が使っているかもしれない）
  disable_on_destroy = false
}

# --- イメージの置き場 --------------------------------------------------------------------------------
resource "google_artifact_registry_repository" "images" {
  repository_id = "${local.name}-images"
  location      = var.region
  format        = "DOCKER"
  description   = "PMT のイメージ（サーバ pmt・試験 pmt-test）"

  depends_on = [google_project_service.enabled]
}

# --- サービスアカウント（最小の権限） ----------------------------------------------------------------
# Cloud Run（サービス・移行のジョブ・検証のジョブ）が動く身元：Cloud SQL へつなぐ・自分の秘密を読む
resource "google_service_account" "run" {
  account_id   = "${local.name}-run"
  display_name = "PMT の Cloud Run"
  depends_on   = [google_project_service.enabled]
}

# Cloud Build が動く身元：ソースを読む・イメージを置く・ログを書く
resource "google_service_account" "build" {
  account_id   = "${local.name}-build"
  display_name = "PMT のビルド（Cloud Build）"
  depends_on   = [google_project_service.enabled]
}

resource "google_project_iam_member" "run_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = google_service_account.run.member
}

resource "google_project_iam_member" "build_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = google_service_account.build.member
}

resource "google_artifact_registry_repository_iam_member" "build_writer" {
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.build.member
}

# gcloud builds submit が上げるソースの置き場（ビルドの身元にだけ読ませる。古いものは消す）
resource "google_storage_bucket" "build_source" {
  name                        = "${local.name}-build-${var.project_id}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true

  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket_iam_member" "build_source_reader" {
  bucket = google_storage_bucket.build_source.name
  role   = "roles/storage.objectViewer"
  member = google_service_account.build.member
}
