"""Immutable Relay template versions selected by this website release."""

RELAY_TEMPLATE_KEYS = frozenset(
    {
        "account_deletion_completed", "account_deletion_request",
        "account_email_change_confirm", "account_email_changed_notice",
        "basic_welcome", "bookclub_book_summary", "bookclub_chapter_summary",
        "cancellation", "checkout_payment_failed", "cofounder_welcome",
        "community_invite", "content_comment", "course_announcement",
        "course_unenroll_notification", "coursework.pool_ready",
        "coursework.review_assigned", "coursework.review_received",
        "coursework.review_window_expired", "download_delivery",
        "email_verification_signup", "email_verification_signup_reminder",
        "email_verification_subscribe", "email_verification_subscribe_reminder",
        "event_cancelled", "event_recap_ready", "event_recording_ready",
        "event_registration", "event_reminder", "event_rescheduled",
        "event_workshop_ready", "free_welcome", "lead_magnet_delivery",
        "maven_cohort_removal_notification", "maven_enrollment_notification",
        "maven_removal", "maven_welcome", "onboarding_reminder",
        "onboarding_submitted", "password_reset", "payment_failed",
        "payment_grace_expired_member", "payment_grace_failure_member",
        "payment_grace_failure_team", "payment_grace_reminder_member",
        "plan_shared", "post_event_followup", "premium_welcome",
        "series_cancellation", "series_registration", "series_update",
        "slack_join_notification", "sprint_end_recap", "sprint_partner_intro",
        "sprint_week_note_prompt", "sprint_week_start", "staff_signup_notification",
        "submission_recorded", "welcome", "welcome_back", "welcome_imported",
        "workshop_announcement",
    }
)


def template_version_for(template_key):
    """Return the immutable version verified for this website release.

    All 61 named Markdown templates and ``campaign-broadcast`` are verified at
    version 2 with ``markdown_layout_context=true``. ``raw_transactional``,
    internal forwarding, and unknown keys stay on version 1.
    """

    if template_key in RELAY_TEMPLATE_KEYS or template_key == "campaign-broadcast":
        return 2
    return 1
