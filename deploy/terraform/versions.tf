# Terraform と provider の版（設計書 1.1：Terraform 1.9 以上。provider は版を固定し、.terraform.lock.hcl をコミットする）
terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "8.11.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.9.1"
    }
  }

  # 状態は GCS に置く。bucket は -backend-config で渡す（deploy/deploy.sh が ksdp-tfstate-<project> を作って渡す）
  backend "gcs" {
    prefix = "pmt"
  }
}
