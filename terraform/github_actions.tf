resource "google_service_account" "terraform_deployer" {
  account_id   = "terraform-deployer"
  display_name = "Terraform Deployer"
  description  = "Used by GitHub Actions to run Terraform"
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions Pool"
  depends_on                = [google_project_service.iam]
}

resource "google_iam_workload_identity_pool_provider" "poona-adherents-update" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-actions"
  display_name                       = "Poona Update Provider"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.actor"         = "assertion.actor"
    "attribute.repository"    = "assertion.repository"
    "attribute.repository_id" = "assertion.repository_id"
  }

  # Only tokens from this specific repository are accepted.
  attribute_condition = "assertion.repository_id == '1259479907'"
}

resource "google_project_iam_member" "terraform_deployer" {
  for_each = toset([
    "roles/serviceusage.serviceUsageAdmin",
    "roles/iam.serviceAccountAdmin",
    "roles/iam.workloadIdentityPoolAdmin",
    "roles/resourcemanager.projectIamAdmin",
    "roles/secretmanager.admin",
    "roles/monitoring.notificationChannelEditor",
    "roles/iam.oauthClientAdmin"
  ])
  project    = var.project_id
  role       = each.value
  member     = "serviceAccount:${google_service_account.terraform_deployer.email}"
  depends_on = [google_project_service.cloudresourcemanager]
}
resource "google_billing_account_iam_member" "terraform_deployer" {
  billing_account_id = var.billing_account_id
  role               = "roles/billing.costsManager"
  member             = "serviceAccount:${google_service_account.terraform_deployer.email}"
  depends_on         = [google_project_service.cloudbilling]
}

resource "google_service_account_iam_member" "wif_impersonation" {
  service_account_id = google_service_account.terraform_deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/jeremyalbrecht/poona-adherents-update"
}

resource "google_storage_bucket_iam_member" "terraform_deployer" {
  bucket = google_storage_bucket.augny_badminton_tfstate.name
  member = "serviceAccount:${google_service_account.terraform_deployer.email}"
  role   = "roles/storage.admin"
}