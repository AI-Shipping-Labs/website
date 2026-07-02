"""Central email kind classification and sender resolution."""

import os

from django.conf import settings

from integrations.config import get_config

EMAIL_KIND_TRANSACTIONAL = "transactional"
EMAIL_KIND_PROMOTIONAL = "promotional"

TRANSACTIONAL_FROM_KEY = "SES_TRANSACTIONAL_FROM_EMAIL"
PROMOTIONAL_FROM_KEY = "SES_PROMOTIONAL_FROM_EMAIL"
WELCOME_FROM_KEY = "SES_WELCOME_FROM_EMAIL"
LEGACY_FROM_KEY = "SES_FROM_EMAIL"

DEFAULT_TRANSACTIONAL_FROM_EMAIL = "noreply@aishippinglabs.com"
DEFAULT_PROMOTIONAL_FROM_EMAIL = "content@aishippinglabs.com"
DEFAULT_WELCOME_FROM_EMAIL = "welcome@aishippinglabs.com"

# community_invite is transactional: it grants access to the paid/member
# community. lead_magnet_delivery is transactional: it delivers a resource the
# recipient explicitly requested, not an unsolicited marketing campaign.
TRANSACTIONAL_EMAIL_TYPES = {
    "welcome",
    "payment_failed",
    "cancellation",
    "community_invite",
    "lead_magnet_delivery",
    "event_reminder",
    # Issue #767: separate templates per signup vs newsletter-subscribe
    # flow. Both verify slugs and both reminders are transactional.
    "email_verification_signup",
    "email_verification_subscribe",
    "email_verification_signup_reminder",
    "email_verification_subscribe_reminder",
    "password_reset",
    "event_registration",
    "event_rescheduled",
    "event_cancelled",
    "welcome_imported",
    # Issue #703: paid-signup automation.
    "cofounder_welcome",
    "staff_signup_notification",
    # Issue #959: staff heads-up when a known user joins Slack. Internal
    # transactional notification to the staff mailbox.
    "slack_join_notification",
    # Issue #847: tier-specific paid-signup welcomes. Transactional for
    # the same reason as cofounder_welcome — the recipient just paid, so
    # an unsubscribed paid user still receives their welcome.
    "basic_welcome",
    "premium_welcome",
    # Issue #976: "welcome back" email for a churned member re-subscribing.
    # Transactional like the other welcomes — a re-subscribing unsubscribed
    # paid member still receives it, with no unsubscribe footer.
    "welcome_back",
    # Issue #680: post-event follow-up (recap + recording + notes).
    # Transactional because the recipient registered for this event;
    # an unsubscribed user still receives it, same policy as
    # event_reminder / event_rescheduled.
    "post_event_followup",
    # Issue #1075: internal host/operator heads-up that a Zoom recording
    # has been uploaded and is ready for Studio review.
    "event_recording_ready",
    # Issue #1118: event-specific workshop-ready broadcast. Transactional
    # because the recipient explicitly registered for this event.
    "event_workshop_ready",
    # Issue #732: staff explicitly shared a sprint plan with the
    # member. Transactional because the recipient is a named paid
    # sprint participant and the email is about an artefact created
    # for them; unsubscribed users still receive it (same policy as
    # event_registration / event_reminder).
    "plan_shared",
    # Issue #960: Maven cohort auto-onboarding. The course welcome is
    # transactional — a Maven enrollee receives it as part of enrolling,
    # so an unsubscribed user still gets it. The removal heads-up is an
    # internal staff notification, transactional like
    # staff_signup_notification.
    "maven_welcome",
    "maven_cohort_removal_notification",
}

PROMOTIONAL_EMAIL_TYPES = {
    "campaign",
    "workshop_announcement",
}

# Issue #937: welcome emails go out from a dedicated `welcome@` sender, but
# they MUST keep their transactional delivery semantics — an unsubscribed
# paid user still receives their welcome, and welcome mail gets no
# unsubscribe footer. So these stay a SUBSET of TRANSACTIONAL_EMAIL_TYPES;
# only the From address is overridden, never the classification.
WELCOME_EMAIL_TYPES = {
    "welcome",
    "cofounder_welcome",
    "basic_welcome",
    "premium_welcome",
    "welcome_imported",
    # Issue #976: the returning-member "welcome back" email sends from
    # welcome@ like the other welcomes, keeping transactional semantics.
    "welcome_back",
    # Issue #960: the Maven course welcome sends from welcome@ like the
    # other welcomes, keeping its transactional delivery semantics.
    "maven_welcome",
}

# Guard against anyone editing one set without the other. If a welcome type
# is ever dropped from TRANSACTIONAL_EMAIL_TYPES it would silently lose its
# unsubscribe-bypass delivery semantics for paid users.
assert WELCOME_EMAIL_TYPES <= TRANSACTIONAL_EMAIL_TYPES, (
    "WELCOME_EMAIL_TYPES must be a subset of TRANSACTIONAL_EMAIL_TYPES so "
    "welcome emails keep transactional delivery semantics."
)


class EmailClassificationError(ValueError):
    """Raised when an email type has no explicit kind."""


def classify_email_type(email_type):
    """Return ``transactional`` or ``promotional`` for a known email type."""
    if email_type in TRANSACTIONAL_EMAIL_TYPES:
        return EMAIL_KIND_TRANSACTIONAL
    if email_type in PROMOTIONAL_EMAIL_TYPES:
        return EMAIL_KIND_PROMOTIONAL
    raise EmailClassificationError(
        f"Email type {email_type!r} is not classified as transactional or promotional."
    )


def _integration_setting_has_value(key):
    try:
        from integrations.models import IntegrationSetting

        return IntegrationSetting.objects.filter(key=key).exclude(value="").exists()
    except Exception:
        return False


def _has_runtime_value(key, default=""):
    if os.environ.get(key):
        return True
    settings_value = getattr(settings, key, "")
    if settings_value and settings_value != default:
        return True
    return _integration_setting_has_value(key)


def get_sender_for_kind(email_kind):
    """Resolve the configured sender for an email kind.

    New explicit keys win. The legacy ``SES_FROM_EMAIL`` key remains a
    migration fallback only when the new key is not configured.
    """
    if email_kind == EMAIL_KIND_TRANSACTIONAL:
        key = TRANSACTIONAL_FROM_KEY
        default = DEFAULT_TRANSACTIONAL_FROM_EMAIL
    elif email_kind == EMAIL_KIND_PROMOTIONAL:
        key = PROMOTIONAL_FROM_KEY
        default = DEFAULT_PROMOTIONAL_FROM_EMAIL
    else:
        raise EmailClassificationError(f"Unknown email kind: {email_kind!r}")

    if _has_runtime_value(key, default):
        return get_config(key, default)

    if _has_runtime_value(LEGACY_FROM_KEY):
        return get_config(LEGACY_FROM_KEY, default)

    return default


def get_sender_for_email_type(email_type):
    """Resolve the configured From address for a specific email type.

    Welcome types (issue #937) resolve to the dedicated welcome sender via a
    per-type override on top of the transactional kind — their classification
    and delivery semantics are unchanged. Every other classified type
    delegates to ``get_sender_for_kind(classify_email_type(...))`` so
    behaviour is identical to before.

    ``None`` (or any unclassified type) falls back to the transactional
    sender, matching the historical ``email_kind='transactional'`` default
    of ``_send_ses`` — this covers low-level callers that render their own
    HTML and pass no template name (e.g. campaign preview sends pass
    ``'campaign'`` explicitly; SES-plumbing tests pass nothing).
    """
    if email_type in WELCOME_EMAIL_TYPES:
        if _has_runtime_value(WELCOME_FROM_KEY, DEFAULT_WELCOME_FROM_EMAIL):
            return get_config(WELCOME_FROM_KEY, DEFAULT_WELCOME_FROM_EMAIL)
        if _has_runtime_value(LEGACY_FROM_KEY):
            return get_config(LEGACY_FROM_KEY, DEFAULT_WELCOME_FROM_EMAIL)
        return DEFAULT_WELCOME_FROM_EMAIL

    if email_type in PROMOTIONAL_EMAIL_TYPES:
        return get_sender_for_kind(EMAIL_KIND_PROMOTIONAL)

    return get_sender_for_kind(EMAIL_KIND_TRANSACTIONAL)
