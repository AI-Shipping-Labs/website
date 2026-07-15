"""Member privacy export and local account deletion services."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from django.apps import apps
from django.contrib.sessions.models import Session
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import salted_hmac

from accounts.models import PrivacyRequestLog
from integrations.config import get_config, is_enabled, site_base_url

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "2026-07-11.1"
REDACTED = "[privacy-redacted]"
SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
SENSITIVE_METADATA_KEY_PARTS = (
    "access_token",
    "refresh_token",
    "id_token",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "authorization",
    "bearer",
    "api_key",
    "apikey",
    "client_secret",
    "key_hash",
    "hash",
    "signature",
    "signed_request",
    "session_key",
    "csrf",
)
TOKEN_VALUE_PREFIXES = (
    "bearer ",
    "ya29.",
    "gho_",
    "ghp_",
    "github_pat_",
    "glpat-",
    "xoxb-",
    "xoxp-",
)
JWT_RE = re.compile(r"^[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}$")


@dataclass(frozen=True)
class PrivacyDeletionResult:
    success: bool
    status: str
    audit_log_id: int | None = None
    blocker_reason: str = ""
    row_count_summary: dict[str, Any] | None = None


def request_context_from_request(request):
    """Return the minimal request metadata used by privacy audit logs."""
    return {
        "ip": request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", ""),
        "user_agent": request.META.get("HTTP_USER_AGENT", ""),
    }


def build_user_data_export(user):
    """Build a machine-readable export for ``user`` without secrets."""
    generated_at = timezone.now()
    return {
        "manifest": {
            "generated_at": generated_at.isoformat(),
            "site": site_base_url(),
            "user_id": user.pk,
            "primary_email": user.email,
            "schema_version": SCHEMA_VERSION,
        },
        "account_profile": _account_profile(user),
        "membership_payment": _membership_payment(user),
        "auth_security": _auth_security(user),
        "learning_content": _learning_content(user),
        "events_community": _events_community(user),
        "sprints_plans": _sprints_plans(user),
        "crm_onboarding": _crm_onboarding(user),
        "communications_activity": _communications_activity(user),
    }


def write_privacy_export_log(user, request_context=None):
    return _create_privacy_log(
        user=user,
        request_type=PrivacyRequestLog.REQUEST_EXPORT,
        status=PrivacyRequestLog.STATUS_COMPLETED,
        row_count_summary={"exported_sections": 8},
        request_context=request_context,
    )


def log_blocked_privacy_delete(user, reason, request_context=None):
    return _create_privacy_log(
        user=user,
        request_type=PrivacyRequestLog.REQUEST_DELETE,
        status=PrivacyRequestLog.STATUS_BLOCKED,
        blocker_reason=reason,
        row_count_summary={},
        request_context=request_context,
    )


def delete_account_for_privacy(user, request_context=None):
    """Delete a member account locally, retaining only scrubbed audit data."""
    if user.is_staff or user.is_superuser:
        log = log_blocked_privacy_delete(
            user,
            PrivacyRequestLog.BLOCKER_STAFF_ACCOUNT,
            request_context,
        )
        return PrivacyDeletionResult(
            success=False,
            status=PrivacyRequestLog.STATUS_BLOCKED,
            audit_log_id=log.pk,
            blocker_reason=PrivacyRequestLog.BLOCKER_STAFF_ACCOUNT,
        )

    if user.subscription_id:
        log = log_blocked_privacy_delete(
            user,
            PrivacyRequestLog.BLOCKER_ACTIVE_SUBSCRIPTION,
            request_context,
        )
        notify_privacy_staff(
            event="blocked_active_subscription",
            email=user.email,
            old_user_id=user.pk,
            row_count_summary={},
        )
        return PrivacyDeletionResult(
            success=False,
            status=PrivacyRequestLog.STATUS_BLOCKED,
            audit_log_id=log.pk,
            blocker_reason=PrivacyRequestLog.BLOCKER_ACTIVE_SUBSCRIPTION,
        )

    old_user_id = user.pk
    email = user.email
    identifiers = _known_member_identifiers(user)

    with transaction.atomic():
        summary = _empty_summary()
        _delete_user_sessions(user, summary)
        _erase_local_slack_threads(user, summary)
        _anonymize_member_projects(user, summary)
        _detach_payment_diagnostics(user, summary, email)
        _scrub_matching_webhook_payloads(identifiers, summary)

        _, deleted_counts = user.delete()
        summary["erased"].update({key: value for key, value in sorted(deleted_counts.items()) if value})

        log = _create_privacy_log(
            user=None,
            request_type=PrivacyRequestLog.REQUEST_DELETE,
            status=PrivacyRequestLog.STATUS_COMPLETED,
            old_user_id=old_user_id,
            email=email,
            row_count_summary=summary,
            request_context=request_context,
        )

    notify_privacy_staff(
        event="completed_delete",
        email=email,
        old_user_id=old_user_id,
        row_count_summary=summary,
    )
    return PrivacyDeletionResult(
        success=True,
        status=PrivacyRequestLog.STATUS_COMPLETED,
        audit_log_id=log.pk,
        row_count_summary=summary,
    )


def notify_privacy_staff(*, event, email, old_user_id, row_count_summary):
    """Best-effort staff heads-up. Never raise back to the privacy flow."""
    staff_email = (get_config("STAFF_SIGNUP_NOTIFY_EMAIL", "") or "").strip()
    slack_channel_id = (get_config("STAFF_SIGNUP_NOTIFY_CHANNEL_ID", "") or "").strip()
    subject = f"Privacy request {event}: user {old_user_id}"
    body = (
        f"Privacy request event: {event}\n"
        f"User ID: {old_user_id}\n"
        f"Email: {email}\n"
        f"Summary: {json.dumps(row_count_summary, sort_keys=True)}"
    )

    if staff_email:
        try:
            send_mail(
                subject,
                body,
                None,
                [staff_email],
                fail_silently=False,
            )
        except Exception:
            logger.exception(
                "Privacy staff email failed for event=%s old_user_id=%s",
                event,
                old_user_id,
            )

    if slack_channel_id:
        try:
            _post_privacy_slack(slack_channel_id, event, email, old_user_id)
        except Exception:
            logger.exception(
                "Privacy staff Slack notification failed for event=%s old_user_id=%s",
                event,
                old_user_id,
            )


def _post_privacy_slack(channel_id, event, email, old_user_id):
    if not is_enabled("SLACK_ENABLED"):
        return False
    bot_token = get_config("SLACK_BOT_TOKEN")
    if not bot_token:
        return False
    response = requests.post(
        SLACK_POST_MESSAGE_URL,
        json={
            "channel": channel_id,
            "text": (f"*Privacy request {event}*\nUser ID: `{old_user_id}`\nEmail: `{email}`"),
        },
        headers={
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        timeout=10,
    )
    data = response.json()
    if not isinstance(data, dict) or not data.get("ok"):
        logger.warning(
            "Privacy Slack notification rejected for channel=%s: %s",
            channel_id,
            (data or {}).get("error", "unknown") if isinstance(data, dict) else data,
        )
        return False
    return True


def _account_profile(user):
    return {
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "display_name": user.get_full_name() or user.email,
        "email_verified": user.email_verified,
        "date_joined": _plain(user.date_joined),
        "last_login": _plain(user.last_login),
        "signup_source": user.signup_source,
        "account_activated": user.account_activated,
        "import_source": user.import_source,
        "imported_at": _plain(user.imported_at),
        "import_metadata": user.import_metadata,
        "preferred_timezone": user.preferred_timezone,
        "theme_preference": user.theme_preference,
        "unsubscribed": user.unsubscribed,
        "email_preferences": user.email_preferences,
        "dashboard_dismissals": user.dashboard_dismissals,
        "slack_member": user.slack_member,
        "slack_user_id": user.slack_user_id,
        "slack_checked_at": _plain(user.slack_checked_at),
    }


def _membership_payment(user):
    conversion = _model("payments", "ConversionAttribution")
    mismatch = _model("payments", "PaymentAccountMismatch")
    binding = _model("payments", "CheckoutAccountBinding")
    fulfillment = _model("payments", "CheckoutFulfillment")
    return {
        "current_tier": _tier_snapshot(user.tier),
        "base_tier": _tier_snapshot(user.tier),
        "effective_tier": _tier_snapshot(_effective_tier(user)),
        "pending_tier": _tier_snapshot(user.pending_tier),
        "billing_period_end": _plain(user.billing_period_end),
        "stripe_customer_id": user.stripe_customer_id,
        "subscription_id": user.subscription_id,
        "tier_overrides": _values(
            _model("accounts", "TierOverride"),
            Q(user=user),
            [
                "id",
                "original_tier_id",
                "override_tier_id",
                "expires_at",
                "created_at",
                "is_active",
            ],
        ),
        "conversion_attributions": _values(
            conversion,
            Q(user=user),
            [
                "id",
                "stripe_session_id",
                "stripe_subscription_id",
                "tier_id",
                "billing_period",
                "amount_eur",
                "mrr_eur",
                "first_touch_utm_source",
                "first_touch_utm_campaign",
                "last_touch_utm_source",
                "last_touch_utm_campaign",
                "created_at",
            ],
        ),
        "payment_mismatches": _values(
            mismatch,
            Q(paid_user=user) | Q(candidate_user=user) | Q(resolved_by=user),
            [
                "id",
                "stripe_session_id",
                "stripe_customer_id",
                "stripe_subscription_id",
                "stripe_email",
                "paid_user_id",
                "candidate_user_id",
                "reason",
                "status",
                "details",
                "created_at",
                "updated_at",
                "resolved_at",
            ],
        ),
        "checkout_bindings": _values(
            binding,
            Q(user=user),
            ["id", "tier_id", "billing_period", "source", "created_at", "expires_at", "revoked_at"],
        ),
        "checkout_fulfillments": _values(
            fulfillment,
            Q(user=user),
            ["id", "stripe_session_id", "tier_id", "status", "reason", "created_at"],
        ),
        "card_data": "not_stored",
    }


def _auth_security(user):
    social = _model("socialaccount", "SocialAccount")
    allauth_email = _model("account", "EmailAddress")
    return {
        "email_aliases": _values(
            _model("accounts", "EmailAlias"),
            Q(user=user),
            ["email", "source", "note", "created_at"],
        ),
        "allauth_email_addresses": _values(
            allauth_email,
            Q(user=user),
            ["email", "verified", "primary"],
        ),
        "oauth_social_accounts": _social_account_values(social, user),
        "member_api_keys": _values(
            _model("accounts", "MemberAPIKey"),
            Q(user=user),
            [
                "id",
                "name",
                "lookup_prefix",
                "scopes",
                "created_at",
                "revoked_at",
                "last_used_at",
            ],
        ),
        "staff_api_tokens": _values(
            _model("accounts", "Token"),
            Q(user=user),
            ["id", "name", "lookup_prefix", "created_at", "last_used_at"],
        ),
    }


def _social_account_values(model, user):
    if model is None:
        return []
    rows = []
    for account in model.objects.filter(user=user).order_by("provider", "uid"):
        rows.append(
            {
                "provider": account.provider,
                "uid": account.uid,
                "extra_data": _sanitize_provider_metadata(account.extra_data),
                "date_joined": _plain(account.date_joined),
                "last_login": _plain(account.last_login),
            }
        )
    return rows


def _sanitize_provider_metadata(value):
    if isinstance(value, dict):
        return {
            key: (REDACTED if _metadata_key_is_sensitive(key) else _sanitize_provider_metadata(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_provider_metadata(item) for item in value]
    if isinstance(value, str):
        redacted_url = _redact_secret_url_query_values(value)
        if redacted_url != value:
            return redacted_url
        if _metadata_value_is_token_like(value):
            return REDACTED
    return value


def _metadata_key_is_sensitive(key):
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
    return any(part in normalized for part in SENSITIVE_METADATA_KEY_PARTS)


def _metadata_value_is_token_like(value):
    stripped = value.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if lowered.startswith(TOKEN_VALUE_PREFIXES):
        return True
    if JWT_RE.match(stripped):
        return True
    return False


def _redact_secret_url_query_values(value):
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc or not parsed.query:
        return value
    changed = False
    query = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        if _metadata_key_is_sensitive(key) or _metadata_value_is_token_like(item):
            item = REDACTED
            changed = True
        query.append((key, item))
    if not changed:
        return value
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query),
            parsed.fragment,
        )
    )


def _learning_content(user):
    return {
        "course_enrollments": _values(
            _model("content", "Enrollment"),
            Q(user=user),
            ["id", "course_id", "enrolled_at", "unenrolled_at", "source"],
        ),
        "cohort_enrollments": _values(
            _model("content", "CohortEnrollment"),
            Q(user=user),
            ["id", "cohort_id", "enrolled_at"],
        ),
        "course_progress": _values(
            _model("content", "UserCourseProgress"),
            Q(user=user),
            ["id", "unit_id", "completed_at"],
        ),
        "content_completions": _values(
            _model("content", "UserContentCompletion"),
            Q(user=user),
            ["id", "content_type", "object_id", "completed_at"],
        ),
        "course_access": _values(
            _model("content", "CourseAccess"),
            Q(user=user),
            ["id", "course_id", "access_type", "stripe_session_id", "created_at"],
        ),
        "course_certificates": _values(
            _model("content", "CourseCertificate"),
            Q(user=user),
            ["id", "course_id", "issued_at", "submission_id", "revoked_at"],
        ),
        "project_submissions": _values(
            _model("content", "ProjectSubmission"),
            Q(user=user),
            [
                "id",
                "course_id",
                "cohort_id",
                "project_url",
                "description",
                "status",
                "submitted_at",
                "certificate_issued_at",
            ],
        ),
        "submitted_projects": _values(
            _model("content", "Project"),
            Q(submitter=user),
            ["id", "title", "slug", "status", "published", "published_at"],
        ),
        "peer_reviews_given": _values(
            _model("content", "PeerReview"),
            Q(reviewer=user),
            [
                "id",
                "submission_id",
                "score",
                "feedback",
                "is_complete",
                "assigned_at",
                "completed_at",
            ],
        ),
    }


def _events_community(user):
    return {
        "event_registrations": _values(
            _model("events", "EventRegistration"),
            Q(user=user),
            ["id", "event_id", "registered_at", "joined_at"],
        ),
        "series_registrations": _values(
            _model("events", "SeriesRegistration"),
            Q(user=user),
            ["id", "series_id", "registered_at"],
        ),
        "event_feedback": _values(
            _model("events", "EventFeedback"),
            Q(user=user),
            ["id", "event_id", "rating", "comment", "would_change", "created_at"],
        ),
        "event_join_clicks": _values(
            _model("events", "EventJoinClick"),
            Q(user=user),
            ["id", "event_id", "clicked_at"],
        ),
        "event_reminder_logs": _values(
            _model("notifications", "EventReminderLog"),
            Q(user=user),
            ["id", "event_id", "interval", "created_at"],
        ),
        "booked_calls": _values(
            _model("community", "BookedCall"),
            Q(member=user),
            [
                "id",
                "host_id",
                "invitee_email",
                "invitee_name",
                "scheduled_at",
                "status",
                "calendly_event_uri",
                "created_at",
                "updated_at",
            ],
        ),
        "calendly_webhook_deliveries": _calendly_webhook_export(user),
        "slack_threads": _slack_threads_export(user),
        "slack_authored_messages": _slack_authored_messages_export(user),
    }


def _calendly_webhook_export(user):
    model = _model('integrations', 'WebhookLog')
    if model is None:
        return []
    identifiers = {user.email, *user.email_aliases.values_list('email', flat=True)}
    rows = []
    for row in model.objects.filter(service='calendly').order_by('-received_at'):
        text = json.dumps(row.payload, default=str).lower()
        if not any(value and value.lower() in text for value in identifiers):
            continue
        rows.append({
            'id': row.pk, 'event_type': row.event_type,
            'payload': _plain(row.payload), 'received_at': _plain(row.received_at),
            'processed': row.processed,
        })
    return rows


def _sprints_plans(user):
    plans = _model("plans", "Plan")
    return {
        "sprint_enrollments": _values(
            _model("plans", "SprintEnrollment"),
            Q(user=user),
            ["id", "sprint_id", "enrolled_at", "enrolled_by_id"],
        ),
        "accountability_partner_links": _values(
            _model("plans", "SprintAccountabilityPartner"),
            Q(member=user) | Q(partner=user),
            ["id", "sprint_id", "member_id", "partner_id", "source", "created_at"],
        ),
        "plans": _values(
            plans,
            Q(member=user),
            [
                "id",
                "sprint_id",
                "visibility",
                "title",
                "goal",
                "summary_current_situation",
                "summary_goal",
                "summary_main_gap",
                "summary_weekly_hours",
                "summary_why_this_plan",
                "focus_main",
                "focus_supporting",
                "accountability",
                "shared_at",
                "created_at",
                "updated_at",
            ],
        ),
        "weeks": _child_values(plans, user, "weeks"),
        "week_notes": _week_notes_export(user),
        "checkpoints": _plan_item_values(user, "Checkpoint"),
        "deliverables": _plan_item_values(user, "Deliverable"),
        "resources": _plan_item_values(user, "Resource"),
        "next_steps": _plan_item_values(user, "NextStep"),
        "plan_requests": _values(
            _model("plans", "PlanRequest"),
            Q(member=user),
            ["id", "sprint_id", "created_at", "updated_at"],
        ),
        "interview_notes": _values(
            _model("plans", "InterviewNote"),
            Q(member=user),
            [
                "id",
                "plan_id",
                "visibility",
                "kind",
                "body",
                "tags",
                "source_type",
                "source_metadata",
                "created_at",
                "updated_at",
            ],
        ),
        "plan_ready_email_logs": _values(
            _model("plans", "PlanReadyEmailLog"),
            Q(member=user),
            ["id", "plan_id", "sprint_id", "status", "sent_at", "last_error"],
        ),
        "sprint_partner_intro_email_logs": _values(
            _model("plans", "SprintPartnerIntroEmailLog"),
            Q(member=user),
            [
                "id",
                "sprint_id",
                "status",
                "sent_at",
                "last_error",
                "partner_snapshot",
            ],
        ),
    }


def _crm_onboarding(user):
    return {
        "crm_record": _single_values(
            _model("crm", "CRMRecord"),
            Q(user=user),
            [
                "id",
                "status",
                "persona",
                "persona_ref_id",
                "summary",
                "next_steps",
                "created_at",
                "updated_at",
            ],
        ),
        "questionnaire_responses": _questionnaire_responses_export(user),
    }


def _communications_activity(user):
    return {
        "email_logs": _values(
            _model("email_app", "EmailLog"),
            Q(user=user),
            [
                "id",
                "campaign_id",
                "event_id",
                "recipient_email",
                "email_type",
                "sent_at",
                "ses_message_id",
                "opened_at",
                "opens",
                "clicked_at",
                "clicks",
                "bounced_at",
                "bounce_type",
                "complained_at",
            ],
        ),
        "ses_events": _values(
            _model("email_app", "SesEvent"),
            Q(user=user) | Q(recipient_email__iexact=user.email),
            [
                "id",
                "event_type",
                "recipient_email",
                "email_log_id",
                "bounce_type",
                "bounce_subtype",
                "diagnostic_code",
                "received_at",
            ],
        ),
        "notifications": _values(
            _model("notifications", "Notification"),
            Q(user=user),
            ["id", "title", "body", "url", "notification_type", "read", "created_at"],
        ),
        "comments": _values(
            _model("comments", "Comment"),
            Q(user=user),
            ["id", "content_id", "parent_id", "body", "created_at", "updated_at"],
        ),
        "comment_votes": _values(
            _model("comments", "CommentVote"),
            Q(user=user),
            ["id", "comment_id", "created_at"],
        ),
        "poll_votes": _values(
            _model("voting", "PollVote"),
            Q(user=user),
            ["id", "poll_id", "option_id", "created_at"],
        ),
        "poll_proposals": _values(
            _model("voting", "PollOption"),
            Q(proposed_by=user),
            ["id", "poll_id", "title", "description", "created_at"],
        ),
        "analytics_attribution": _single_values(
            _model("analytics", "UserAttribution"),
            Q(user=user),
            [
                "first_touch_utm_source",
                "first_touch_utm_medium",
                "first_touch_utm_campaign",
                "first_touch_utm_content",
                "first_touch_utm_term",
                "first_touch_ts",
                "last_touch_utm_source",
                "last_touch_utm_medium",
                "last_touch_utm_campaign",
                "last_touch_utm_content",
                "last_touch_utm_term",
                "last_touch_ts",
                "signup_path",
                "anonymous_id",
            ],
        ),
        "analytics_activity": _values(
            _model("analytics", "UserActivity"),
            Q(user=user),
            [
                "id",
                "event_type",
                "occurred_at",
                "object_type",
                "object_id",
                "label",
                "target_url",
            ],
        ),
        "campaign_visits": _values(
            _model("analytics", "CampaignVisit"),
            Q(user=user),
            [
                "id",
                "campaign_id",
                "utm_source",
                "utm_medium",
                "utm_campaign",
                "utm_content",
                "utm_term",
                "path",
                "referrer",
                "anonymous_id",
                "ts",
            ],
        ),
        "trigger_event_emissions": _values(
            _model("triggers", "EventEmission"),
            Q(user=user),
            ["id", "event_name", "properties", "envelope_id", "created_at"],
        ),
    }


def _model(app_label, model_name):
    try:
        return apps.get_model(app_label, model_name)
    except LookupError:
        return None


def _values(model, filters, fields):
    if model is None:
        return []
    rows = []
    for row in model.objects.filter(filters).values(*fields):
        rows.append({key: _plain(value) for key, value in row.items()})
    return rows


def _single_values(model, filters, fields):
    rows = _values(model, filters, fields)
    return rows[0] if rows else {}


def _plain(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _tier_snapshot(tier):
    if tier is None:
        return {"slug": "free", "name": "Free", "level": 0}
    return {"id": tier.pk, "slug": tier.slug, "name": tier.name, "level": tier.level}


def _effective_tier(user):
    override_model = _model("accounts", "TierOverride")
    if override_model is None:
        return user.tier
    active = (
        override_model.objects.filter(
            user=user,
            is_active=True,
            expires_at__gt=timezone.now(),
        )
        .select_related("override_tier")
        .order_by("-override_tier__level")
        .first()
    )
    if active and (user.tier is None or active.override_tier.level > user.tier.level):
        return active.override_tier
    return user.tier


def _child_values(plan_model, user, related_name):
    if plan_model is None:
        return []
    plan_ids = plan_model.objects.filter(member=user).values("id")
    model_by_related = {
        "weeks": (
            "plans",
            "Week",
            Q(plan_id__in=plan_ids),
            [
                "id",
                "plan_id",
                "week_number",
                "theme",
                "position",
                "created_at",
                "updated_at",
            ],
        ),
    }
    app_label, model_name, filters, fields = model_by_related[related_name]
    return _values(_model(app_label, model_name), filters, fields)


def _plan_item_values(user, model_name):
    plan_model = _model("plans", "Plan")
    if plan_model is None:
        return []
    plan_ids = plan_model.objects.filter(member=user).values("id")
    fields_by_model = {
        "Checkpoint": [
            "id",
            "week_id",
            "description",
            "position",
            "done_at",
            "created_at",
            "updated_at",
        ],
        "Deliverable": [
            "id",
            "plan_id",
            "description",
            "position",
            "done_at",
            "created_at",
            "updated_at",
        ],
        "Resource": [
            "id",
            "plan_id",
            "title",
            "url",
            "note",
            "position",
            "created_at",
            "updated_at",
        ],
        "NextStep": [
            "id",
            "plan_id",
            "kind",
            "description",
            "position",
            "done_at",
            "created_at",
            "updated_at",
        ],
    }
    model = _model("plans", model_name)
    if model_name == "Checkpoint":
        week_model = _model("plans", "Week")
        if week_model is None:
            return []
        week_ids = week_model.objects.filter(plan_id__in=plan_ids).values("id")
        return _values(model, Q(week_id__in=week_ids), fields_by_model[model_name])
    return _values(model, Q(plan_id__in=plan_ids), fields_by_model[model_name])


def _week_notes_export(user):
    plan_model = _model("plans", "Plan")
    week_model = _model("plans", "Week")
    if plan_model is None or week_model is None:
        return []
    plan_ids = plan_model.objects.filter(member=user).values("id")
    week_ids = week_model.objects.filter(plan_id__in=plan_ids).values("id")
    return _values(
        _model("plans", "WeekNote"),
        Q(week_id__in=week_ids) | Q(author=user),
        ["id", "week_id", "body", "author_id", "created_at", "updated_at"],
    )


def _questionnaire_responses_export(user):
    response_model = _model("questionnaires", "Response")
    if response_model is None:
        return []
    responses = []
    for response in response_model.objects.filter(respondent=user).order_by("-created_at"):
        response_data = {
            "id": response.pk,
            "questionnaire_id": response.questionnaire_id,
            "status": response.status,
            "submitted_at": _plain(response.submitted_at),
            "created_at": _plain(response.created_at),
            "updated_at": _plain(response.updated_at),
            "answers": [],
            "ai_conversation": {},
        }
        for answer in response.answers.select_related("question").all():
            response_data["answers"].append(
                {
                    "id": answer.pk,
                    "question_id": answer.question_id,
                    "prompt": answer.question.prompt,
                    "text_value": answer.text_value,
                    "number_value": answer.number_value,
                    "selected_option_ids": list(answer.selected_options.values_list("id", flat=True)),
                }
            )
        conversation = getattr(response, "ai_conversation", None)
        if conversation is not None:
            response_data["ai_conversation"] = {
                "id": conversation.pk,
                "transcript": conversation.transcript,
                "persona_signal": conversation.persona_signal,
                "created_at": _plain(conversation.created_at),
                "updated_at": _plain(conversation.updated_at),
            }
        responses.append(response_data)
    return responses


def _slack_threads_export(user):
    thread_model = _model("crm", "SlackThread")
    if thread_model is None:
        return []
    result = []
    for thread in thread_model.objects.filter(member=user).prefetch_related("messages"):
        result.append(
            {
                "id": thread.pk,
                "channel_id": thread.channel_id,
                "thread_ts": thread.thread_ts,
                "slack_user_id": thread.slack_user_id,
                "plan_id": thread.plan_id,
                "posted_at": _plain(thread.posted_at),
                "permalink": thread.permalink,
                "reply_count": thread.reply_count,
                "messages": [
                    {
                        "id": message.pk,
                        "ts": message.ts,
                        "slack_user_id": message.slack_user_id,
                        "author_display": message.author_display,
                        "text": message.text,
                        "posted_at": _plain(message.posted_at),
                        "is_root": message.is_root,
                    }
                    for message in thread.messages.all()
                ],
            }
        )
    return result


def _slack_authored_messages_export(user):
    """Export this Slack identity's messages, including replies elsewhere."""
    message_model = _model("crm", "SlackMessage")
    if message_model is None or not user.slack_user_id:
        return []
    return _values(
        message_model,
        Q(slack_user_id=user.slack_user_id),
        [
            "id",
            "thread_id",
            "ts",
            "slack_user_id",
            "author_display",
            "text",
            "posted_at",
            "is_root",
        ],
    )


def _empty_summary():
    return {"erased": {}, "anonymized": {}, "retained": {}, "skipped": {}}


def _increment(summary, section, key, amount):
    if amount:
        summary[section][key] = summary[section].get(key, 0) + amount


def _delete_user_sessions(user, summary):
    deleted = 0
    for session in Session.objects.all():
        try:
            data = session.get_decoded()
        except Exception:
            continue
        if data.get("_auth_user_id") == str(user.pk):
            session.delete()
            deleted += 1
    _increment(summary, "erased", "sessions", deleted)


def _erase_local_slack_threads(user, summary):
    thread_model = _model("crm", "SlackThread")
    message_model = _model("crm", "SlackMessage")
    if thread_model is None or message_model is None:
        return
    owned_filter = Q(member=user)
    if user.slack_user_id:
        owned_filter |= Q(slack_user_id=user.slack_user_id)
    owned_threads = thread_model.objects.filter(owned_filter)
    owned_ids = list(owned_threads.values_list("id", flat=True))
    count = len(owned_ids)
    # Retain only content-free unique-key tombstones. Otherwise the next
    # upstream history pull would recreate data that this deletion erased.
    message_model.objects.filter(thread_id__in=owned_ids).update(
        slack_user_id="",
        author_display="",
        text="",
    )
    from crm.tasks.apply_plan_sprint_progress import reverse_event

    for thread in owned_threads.select_related("interview_note"):
        if thread.interview_note_id:
            thread.interview_note.delete()
        for event in thread.progress_events.all():
            reverse_event(event)
    owned_threads.update(
        slack_user_id="",
        member=None,
        plan=None,
        permalink="",
        reply_count=0,
        interview_note=None,
        privacy_erased=True,
    )
    _increment(summary, "erased", "local_slack_threads", count)

    if not user.slack_user_id:
        return
    authored_replies = message_model.objects.filter(
        slack_user_id=user.slack_user_id,
    ).exclude(thread_id__in=owned_ids)
    affected_thread_ids = list(
        authored_replies.values_list("thread_id", flat=True).distinct()
    )
    reply_count = authored_replies.count()
    # Preserve the per-thread message timestamp as an erased tombstone so a
    # later `conversations.replies` response cannot restore the content.
    authored_replies.update(slack_user_id="", author_display="", text="")
    _increment(summary, "erased", "local_slack_authored_messages", reply_count)

    # Remove the erased reply from copied canonical note bodies as well.
    from crm.services.slack_note_sync import sync_thread_to_interview_note

    for thread in (
        thread_model.objects
        .filter(pk__in=affected_thread_ids)
        .select_related("member", "plan__sprint", "interview_note")
        .prefetch_related("messages")
    ):
        if thread.member_id is not None:
            sync_thread_to_interview_note(thread)


def _anonymize_member_projects(user, summary):
    project_model = _model("content", "Project")
    if project_model is None:
        return
    unpublished = project_model.objects.filter(submitter=user, published=False)
    unpublished_count = unpublished.count()
    unpublished.delete()
    _increment(summary, "erased", "unpublished_submitted_projects", unpublished_count)

    published = project_model.objects.filter(submitter=user)
    published_count = published.count()
    published.update(submitter=None, author="Deleted member")
    _increment(summary, "anonymized", "published_submitted_projects", published_count)


def _detach_payment_diagnostics(user, summary, email):
    conversion_model = _model("payments", "ConversionAttribution")
    if conversion_model is not None:
        conversion_count = conversion_model.objects.filter(user=user).update(user=None)
        _increment(summary, "retained", "conversion_attributions", conversion_count)

    binding_model = _model("payments", "CheckoutAccountBinding")
    if binding_model is not None:
        bindings = binding_model.objects.filter(user=user)
        binding_count = bindings.update(
            user=None,
            email_snapshot=f"deleted-user-{user.pk}@privacy.invalid",
        )
        _increment(summary, "retained", "checkout_account_bindings", binding_count)

    fulfillment_model = _model("payments", "CheckoutFulfillment")
    if fulfillment_model is not None:
        fulfillment_count = fulfillment_model.objects.filter(user=user).update(user=None)
        _increment(summary, "retained", "checkout_fulfillments", fulfillment_count)

    mismatch_model = _model("payments", "PaymentAccountMismatch")
    if mismatch_model is None:
        return
    mismatches = mismatch_model.objects.filter(Q(paid_user=user) | Q(candidate_user=user) | Q(resolved_by=user))
    mismatch_count = mismatches.count()
    anonymized_email = f"deleted-user-{user.pk}@privacy.invalid"
    for mismatch in mismatches:
        identifiers = {email, mismatch.stripe_email}
        changed = []
        if mismatch.paid_user_id == user.pk:
            mismatch.paid_user = None
            changed.append("paid_user")
        if mismatch.candidate_user_id == user.pk:
            mismatch.candidate_user = None
            changed.append("candidate_user")
        if mismatch.resolved_by_id == user.pk:
            mismatch.resolved_by = None
            changed.append("resolved_by")
        # The Stripe billing address may deliberately differ from the member's
        # canonical login. It is still personal data attached to this data
        # subject, so redact it unconditionally for every linked diagnostic.
        mismatch.stripe_email = anonymized_email
        changed.append("stripe_email")
        mismatch.details = _scrub_payload(mismatch.details, identifiers)
        changed.append("details")
        mismatch.save(update_fields=changed)
    _increment(summary, "retained", "payment_account_mismatches", mismatch_count)


def _known_member_identifiers(user):
    identifiers = {
        value
        for value in [
            user.email,
            user.stripe_customer_id,
            user.subscription_id,
        ]
        if value
    }
    identifiers.update(user.email_aliases.values_list('email', flat=True))
    identifiers.update(
        value for value in user.booked_calls.values_list('invitee_name', flat=True)
        if value
    )
    return identifiers


def _scrub_matching_webhook_payloads(identifiers, summary):
    webhook_model = _model("payments", "WebhookEvent")
    if not identifiers:
        return
    scrubbed = 0
    if webhook_model is not None:
        for row in webhook_model.objects.exclude(payload={}):
            payload_text = json.dumps(row.payload, default=str)
            if not any(identifier in payload_text for identifier in identifiers):
                continue
            row.payload = _scrub_payload(row.payload, identifiers)
            row.error_message = _scrub_text(row.error_message, identifiers)
            row.save(update_fields=["payload", "error_message"])
            scrubbed += 1
    _increment(summary, "retained", "scrubbed_webhook_events", scrubbed)

    inbound_model = _model('integrations', 'WebhookLog')
    inbound_scrubbed = 0
    if inbound_model is not None:
        for row in inbound_model.objects.filter(service='calendly').exclude(payload={}):
            payload_text = json.dumps(row.payload, default=str)
            if not any(identifier in payload_text for identifier in identifiers):
                continue
            row.payload = _scrub_payload(row.payload, identifiers)
            row.error_message = _scrub_text(row.error_message, identifiers)
            row.save(update_fields=['payload', 'error_message'])
            inbound_scrubbed += 1
    _increment(summary, 'retained', 'scrubbed_calendly_webhook_logs', inbound_scrubbed)


def _scrub_payload(value, identifiers):
    if isinstance(value, dict):
        return {key: _scrub_payload(item, identifiers) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub_payload(item, identifiers) for item in value]
    if isinstance(value, str):
        return _scrub_text(value, identifiers)
    return value


def _scrub_text(value, identifiers):
    result = value
    for identifier in identifiers:
        result = result.replace(identifier, REDACTED)
    return result


def _create_privacy_log(
    *,
    user,
    request_type,
    status,
    row_count_summary,
    request_context=None,
    blocker_reason="",
    old_user_id=None,
    email="",
):
    email_value = email or getattr(user, "email", "")
    normalized = email_value.strip().lower()
    domain = normalized.split("@", 1)[1] if "@" in normalized else ""
    context = request_context or {}
    return PrivacyRequestLog.objects.create(
        request_type=request_type,
        status=status,
        old_user_id=old_user_id if old_user_id is not None else getattr(user, "pk", None),
        normalized_email_hash=_hash_value("privacy-email", normalized),
        email_domain=domain,
        row_count_summary=row_count_summary,
        blocker_reason=blocker_reason,
        request_ip_hash=_hash_value("privacy-ip", context.get("ip", "")),
        user_agent_hash=_hash_value(
            "privacy-user-agent",
            context.get("user_agent", ""),
        ),
    )


def _hash_value(salt, value):
    if not value:
        return ""
    return salted_hmac(salt, value).hexdigest()
