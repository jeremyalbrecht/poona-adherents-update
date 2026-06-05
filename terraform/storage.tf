resource "google_storage_bucket" "augny_badminton_tfstate" {
  name     = "augny-badminton-terraform"
  location = "europe-west9"
  force_destroy = true

  uniform_bucket_level_access = true

  public_access_prevention = "enforced"

  versioning {
    enabled = false
  }
}

# Terraform state bucket access — bucket name must match the backend block in providers.tf.
resource "google_storage_bucket_iam_member" "terraform_state" {
  bucket = google_storage_bucket.augny_badminton_tfstate.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.terraform_deployer.email}"
}