variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "billing_account_id" {
  description = "GCP billing account ID (format: XXXXXX-XXXXXX-XXXXXX)"
  type        = string
}

variable "alert_email" {
  description = "Email address for billing alerts"
  type        = string
}

variable "monthly_budget_eur" {
  description = "Monthly budget ceiling in EUR — alerts fire at 50%, 90%, and 100% of this"
  type        = number
  default     = 5
}
