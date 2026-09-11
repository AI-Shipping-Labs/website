"""Registry of all known integration settings with metadata.

Each group defines the integration service and its configurable keys.
The 'multiline' flag indicates keys that need a textarea (e.g. PEM keys).
The 'is_email' flag enables Studio and browser validation for email addresses.

Each ``description`` is a short answer to: what does it do, where do I
get it, what breaks without it. Kept to one sentence so the dashboard
stays scannable. See issue #322 for the locked rewrites.

Each key MAY optionally define ``docs_url`` (issue #641) — a relative
path inside ``_docs/`` (e.g. ``_docs/integrations/stripe.md#stripe_webhook_secret``)
pointing at a per-key section in the integration docs. Studio rewrites
this to the GitHub blob URL
(``https://github.com/AI-Shipping-Labs/website/blob/main/_docs/integrations/<group>.md#<anchor>``)
and renders a (?) icon next to the key. Linking to GitHub (rather than
serving the markdown internally) avoids shipping ``_docs/`` into the
container — ``.dockerignore`` excludes it (issue #664). Entries without
``docs_url`` keep working — the icon simply isn't rendered for them.

The key inventory itself now lives in ``integrations.settings_keys`` as
package-registry ``declare(...)`` calls (plan issue A0.2 step 2); this
module derives ``INTEGRATION_GROUPS`` from those declarations so the
Studio page, the settings API and the export/import service keep their
donor behavior until the A0.2 cutover deletes this shim.

NOTE: ``_docs/configuration.md`` references the count and names of these
groups in the Studio sign-in section ("confirm 17 integration groups are
listed (...)"). When adding, removing, or renaming a group, update that
line of the doc in the same PR.
"""

from community_base.config.registry import definition as _registry_definition

from integrations import settings_keys as _settings_keys


def _legacy_key_entry(key_name):
    """Translate one package Definition back into the legacy key dict.

    Field-for-field identical to the donor inventory (see
    ``integrations/tests/fixtures/donor_settings_inventory_2026-09-08.json``):
    defaults are the verbatim donor literals, and keys whose donor entry
    had no default omit ``default`` again.
    """
    declared = _registry_definition(key_name)
    entry = {
        "key": declared.key,
        "is_secret": declared.secret,
        "description": declared.description,
        "default": declared.default,
    }
    if declared.docs_url:
        entry["docs_url"] = declared.docs_url
    if declared.multiline:
        entry["multiline"] = True
    if declared.optional:
        entry["optional"] = True
    if declared.is_email:
        entry["is_email"] = True
    if declared.value_type == "bool":
        entry["is_boolean"] = True
    if declared.django_settings_fallback:
        entry["django_settings_fallback"] = (
            True if declared.django_settings_fallback == declared.key else declared.django_settings_fallback
        )
    if key_name in _settings_keys._KEYS_WITHOUT_DONOR_DEFAULT:
        del entry["default"]
    entry.update(_settings_keys._DONOR_KEY_EXTRA_METADATA.get(key_name, {}))
    return entry


def _legacy_groups():
    """Rebuild the donor group list, preserving donor group and key order."""
    built = {}
    for group_name in _settings_keys._GROUP_ORDER:
        built[group_name] = {
            "name": group_name,
            "label": _settings_keys.GROUP_LABELS[group_name],
            "keys": [_legacy_key_entry(key_name) for key_name in _settings_keys._KEY_ORDER[group_name]],
        }
    return [built[name] for name in _settings_keys._GROUP_ORDER]


INTEGRATION_GROUPS = _legacy_groups()


SETTING_VALUE_TYPES = {
    'EXPECT_WORKER': 'boolean',
    'SYNC_QUEUED_THRESHOLD_MINUTES': 'integer',
    'SYNC_RUNNING_THRESHOLD_MINUTES': 'integer',
    'PURGE_UNVERIFIED_BATCH_SIZE': 'integer',
    'PURGE_UNVERIFIED_MAX_BATCHES': 'integer',
    # Booleans
    "AUTHENTICATED_CHECKOUT_BINDING_ENABLED": "boolean",
    "LEGACY_NUMERIC_CHECKOUT_REFERENCE_ENABLED": "boolean",
    "ZOOM_WAITING_ROOM": "boolean",
    "ZOOM_JOIN_BEFORE_HOST": "boolean",
    "SES_WEBHOOK_VALIDATION_ENABLED": "boolean",
    "RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD": "boolean",
    "RECORDING_TRANSCRIPT_INGEST_ENABLED": "boolean",
    "RECORDING_RECAP_AUTO_DRAFT_ENABLED": "boolean",
    "S3_ENABLED": "boolean",
    "SLACK_ENABLED": "boolean",
    "STAFF_SLACK_JOIN_NOTIFY_ENABLED": "boolean",
    "ONBOARDING_REMINDER_ENABLED": "boolean",
    "SPRINT_END_AUTO_DISTRIBUTE_FEEDBACK_ENABLED": "boolean",
    "ONBOARDING_AI_ENABLED": "boolean",
    "ONBOARDING_AI_STREAMING": "boolean",
    "NEXT_SPRINT_DRAFT_USE_PROFILE": "boolean",
    "LOGFIRE_ENABLED": "boolean",
    "MAVEN_ENROLLMENT_ENABLED": "boolean",
    "TRIGGERS_ENABLED": "boolean",
    # Base-10 integers
    "CHECKOUT_BINDING_TTL_MINUTES": "integer",
    "EMAIL_BATCH_SIZE": "integer",
    "CAMPAIGN_DELIVERY_MAX_ATTEMPTS": "integer",
    "CAMPAIGN_BATCH_INTERVAL_SECONDS": "integer",
    "RECORDING_PRESIGNED_URL_TTL_SECONDS": "integer",
    "DOWNLOAD_PRESIGNED_URL_TTL_SECONDS": "integer",
    "DOWNLOAD_DELIVERY_TOKEN_TTL_HOURS": "integer",
    "ZOOM_WEBHOOK_TOLERANCE_SECONDS": "integer",
    "CALENDLY_WEBHOOK_TOLERANCE_SECONDS": "integer",
    "CALENDLY_WEBHOOK_RETENTION_DAYS": "integer",
    "PLAN_SPRINTS_FIRST_RUN_LOOKBACK_DAYS": "integer",
    "PLAN_SPRINTS_THREAD_REFRESH_DAYS": "integer",
    "PLAN_SPRINTS_INGEST_LEASE_MINUTES": "integer",
    "PLAN_SPRINTS_RAW_TEXT_RETENTION_DAYS": "integer",
    "ONBOARDING_REMINDER_DELAY_DAYS": "integer",
    "SPRINT_BADGE_WINDOW_DAYS": "integer",
    "CRM_EXPORT_MAX_LIMIT": "integer",
    "USER_ACTIVITY_RETENTION_DAYS": "integer",
    "UNVERIFIED_USER_TTL_DAYS": "integer",
    "AUTH_THROTTLE_LOGIN_IP_LIMIT": "integer",
    "AUTH_THROTTLE_LOGIN_EMAIL_LIMIT": "integer",
    "AUTH_THROTTLE_LOGIN_WINDOW_SECONDS": "integer",
    "AUTH_THROTTLE_MAIL_IP_LIMIT": "integer",
    "AUTH_THROTTLE_MAIL_EMAIL_LIMIT": "integer",
    "AUTH_THROTTLE_MAIL_WINDOW_SECONDS": "integer",
    "BANNER_GENERATOR_TIMEOUT_SECONDS": "integer",
    "BANNER_UPLOAD_MAX_MB": "integer",
    "LLM_MAX_RETRIES": "integer",
    "ONBOARDING_AI_DEADLINE_SECONDS": "integer",
    "ONBOARDING_AI_MAX_ATTEMPTS": "integer",
    "MAVEN_OVERRIDE_DURATION_DAYS": "integer",
    # One absolute HTTP(S) URL
    "STRIPE_CUSTOMER_PORTAL_URL": "url",
    "CALENDLY_CONNECTED_USER_URI": "url",
    "CALENDLY_ORGANIZATION_URI": "url",
    "CALENDLY_WEBHOOK_SUBSCRIPTION_URI": "url",
    "SLACK_INVITE_URL": "url",
    "BOOK_CLUB_SLACK_URL": "url",
    "SITE_BASE_URL": "url",
    "BANNER_GENERATOR_FUNCTION_URL": "url",
    "LLM_BASE_URL": "url",
}

for _group in INTEGRATION_GROUPS:
    for _key_definition in _group["keys"]:
        _value_type = SETTING_VALUE_TYPES.get(_key_definition["key"])
        if _value_type:
            _key_definition["value_type"] = _value_type


def get_group_by_name(name):
    """Look up an integration group by its name.

    Args:
        name: Group name (e.g. 'stripe').

    Returns:
        dict or None: The group definition, or None if not found.
    """
    for group in INTEGRATION_GROUPS:
        if group["name"] == name:
            return group
    return None
