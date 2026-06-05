resource "google_secret_manager_secret" "session_cookies" {
  secret_id = "poona-session-cookies"

  replication {
    auto {}
  }

  depends_on = [google_project_service.secretmanager]
}

resource "google_secret_manager_secret_iam_member" "accessor" {
  secret_id = google_secret_manager_secret.session_cookies.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.poona_update.email}"
}

resource "google_secret_manager_secret_iam_member" "version_manager" {
  secret_id = google_secret_manager_secret.session_cookies.id
  role      = "roles/secretmanager.secretVersionManager"
  member    = "serviceAccount:${google_service_account.poona_update.email}"
}
