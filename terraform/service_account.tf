resource "google_service_account" "poona_update" {
  account_id   = "poona-update"
  display_name = "Poona Update Function"
  description  = "Service account for the update-poona Knative function"
}
