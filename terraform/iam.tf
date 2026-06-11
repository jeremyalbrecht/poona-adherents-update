resource "google_iam_oauth_client" "cashier" {
  oauth_client_id =   "augny-badminton-cashier"
  display_name              = "Augny Badminton - Cashier"
  description               = "OAuth client permettant d'accéder à l'interface de gestion des dettes."
  location                  = "global"
  disabled                  = false
  allowed_grant_types       = ["AUTHORIZATION_CODE_GRANT"]
  allowed_redirect_uris     = ["http://localhost:3000/auth/google", "https://cashier.augny-badminton.fr/auth/google"]
  allowed_scopes            = ["https://www.googleapis.com/auth/cloud-platform"]
  client_type               = "CONFIDENTIAL_CLIENT"
}

resource "google_iam_oauth_client_credential" "cashier-credentials" {
  location                   = google_iam_oauth_client.cashier.location
  oauth_client_credential_id = "cahsier-webapp"
  oauthclient                = google_iam_oauth_client.cashier.oauth_client_id
}