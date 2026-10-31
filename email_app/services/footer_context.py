"""Per-template policy for Relay-owned email footer context."""

VERIFY_FOOTER_TOKEN_EXPIRY_HOURS = 24 * 7

EMAIL_TYPES_WITHOUT_VERIFY_FOOTER = frozenset(
    {
        "email_verification_signup", "email_verification_subscribe",
        "download_delivery", "account_email_change_confirm",
        "account_deletion_request", "account_deletion_completed",
        "password_reset", "payment_grace_failure_member",
        "payment_grace_failure_team", "payment_grace_reminder_member",
        "payment_grace_expired_member", "checkout_payment_failed",
        "maven_welcome", "maven_removal", "campaign_repermission",
        "campaign_verify_deadline",
    }
)
