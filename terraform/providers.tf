terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
  backend "gcs" {
    bucket = "augny-badminton-terraform"
    prefix = "augny-badminton"
  }
}

provider "google" {
  project               = var.project_id
  region                = "europe-west9"
  billing_project       = var.project_id
  user_project_override = true
}
