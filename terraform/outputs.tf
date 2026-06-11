output "service_account_email" {
  description = "Share your Google Sheet with this address to grant the function access"
  value       = google_service_account.poona_update.email
}

output "poona_secret_name" {
  description = "Set this as POONA_SECRET_NAME in func.yaml / your environment"
  value       = google_secret_manager_secret.session_cookies.name
}

output "wif_provider" {
  description = "Add as WIF_PROVIDER secret in GitHub repository settings"
  value       = google_iam_workload_identity_pool_provider.poona-adherents-update.name
}

output "terraform_deployer_email" {
  description = "Add as TF_SERVICE_ACCOUNT secret in GitHub repository settings"
  value       = google_service_account.terraform_deployer.email
}
