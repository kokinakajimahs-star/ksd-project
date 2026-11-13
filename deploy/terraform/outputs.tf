# 出力（deploy/deploy.sh と運用が読む）。秘密の値は出さない（秘密の名前だけ）

output "service_url" {
  description = "サービスの URL（画面と API。イメージが無ければ空）"
  value       = local.deploy_app ? google_cloud_run_v2_service.app[0].uri : ""
}

output "service_name" {
  value = "${local.name}-pmt"
}

output "migrate_job" {
  value = "${local.name}-migrate"
}

output "pgverify_job" {
  value = "${local.name}-pgverify"
}

output "app_image" {
  description = "今のサーバのイメージ（deploy.sh がビルドせずに apply するときに渡し直す）"
  value       = var.image
}

output "test_image" {
  description = "今の試験のイメージ"
  value       = var.test_image
}

output "image_repo" {
  description = "イメージの置き場（<image_repo>/pmt・<image_repo>/pmt-test）"
  value       = local.image_repo
}

output "build_service_account" {
  value = google_service_account.build.email
}

output "build_source_bucket" {
  value = google_storage_bucket.build_source.name
}

output "run_service_account" {
  value = google_service_account.run.email
}

output "sql_instance" {
  value = google_sql_database_instance.main.name
}

output "sql_connection_name" {
  value = google_sql_database_instance.main.connection_name
}

output "initial_admin_login" {
  value = var.initial_admin_login
}

output "initial_admin_password_secret" {
  description = "最初のシステム管理者のパスワードを置いた秘密の名前（値は gcloud secrets versions access で利用者が読む）"
  value       = google_secret_manager_secret.app["initial-admin-password"].secret_id
}
