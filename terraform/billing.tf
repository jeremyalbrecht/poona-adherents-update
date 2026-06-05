resource "google_monitoring_notification_channel" "billing_email" {
  display_name = "Billing Alerts — Augny Badminton"
  type         = "email"

  labels = {
    email_address = var.alert_email
  }

  depends_on = [google_project_service.monitoring]
}

resource "google_billing_budget" "poona_update" {
  billing_account = var.billing_account_id
  display_name    = "Augny Badminton — Monthly Budget"

  budget_filter {
    projects = ["projects/${var.project_id}"]
  }

  amount {
    specified_amount {
      currency_code = "EUR"
      units         = tostring(var.monthly_budget_eur)
    }
  }

  # 50% — early warning
  threshold_rules {
    threshold_percent = 0.5
  }

  # 90% — approaching limit
  threshold_rules {
    threshold_percent = 0.9
  }

  # 100% forecasted — catch runaway spend before it lands
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  # 100% actual — budget exceeded
  threshold_rules {
    threshold_percent = 1.0
  }

  all_updates_rule {
    monitoring_notification_channels = [
      google_monitoring_notification_channel.billing_email.id,
    ]
    disable_default_iam_recipients = false
  }

  depends_on = [google_project_service.billingbudgets]
}
