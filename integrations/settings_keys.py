"""Studio-configurable integration keys, declared in the package registry.

Converted from the former hand-written ``INTEGRATION_GROUPS`` list; the
donor snapshot used for the equivalence test lives at
``integrations/tests/fixtures/donor_settings_inventory_2026-09-08.json``.
The legacy registry in ``integrations.settings_registry`` derives its
``INTEGRATION_GROUPS`` view from these declarations until the A0.2
cutover deletes it. One ``declare`` per key; ``docs_url`` values are
preserved.

Type mapping: donor ``boolean`` keys declare ``value_type="bool"`` and
``integer`` keys declare ``value_type="int"``; the package serves typed
values because ``get()`` coerces resolved values. Donor defaults are
string literals (booleans ``"true"``/``"false"``, integers decimal
strings) and are declared verbatim, so the derived legacy view and the
package registry agree on every key. Keys with no donor default
declare their type's zero value.

Group display labels and donor group/key ordering have no slot in the
package registry (which sorts alphabetically), so they are recorded
here and feed the derived legacy view.
"""

from community_base.config.registry import declare

GROUP_LABELS = {
    "analytics": "Analytics",
    "auth": "Auth",
    "banner_generator": "Banner Generator",
    "calendly": "Calendly",
    "github": "GitHub App",
    "llm": "LLM Provider",
    "maven": "Maven",
    "observability": "Observability",
    "s3_content": "S3 Content Images",
    "s3_downloads": "S3 Downloads",
    "s3_recordings": "S3 Recordings",
    "ses": "Email (SES)",
    "site": "Site",
    "slack": "Slack",
    "stripe": "Stripe",
    "triggers": "Event triggers",
    "zoom": "Zoom",
}

_GROUP_ORDER = (
    "stripe",
    "zoom",
    "ses",
    "s3_recordings",
    "s3_content",
    "s3_downloads",
    "calendly",
    "github",
    "slack",
    "site",
    "analytics",
    "auth",
    "banner_generator",
    "llm",
    "observability",
    "maven",
    "triggers",
)

_KEY_ORDER = {
    "analytics": ("GOOGLE_ANALYTICS_ID", "USER_ACTIVITY_RETENTION_DAYS"),
    "auth": (
        "UNVERIFIED_USER_TTL_DAYS",
        "PURGE_UNVERIFIED_BATCH_SIZE",
        "PURGE_UNVERIFIED_MAX_BATCHES",
        "AUTH_THROTTLE_LOGIN_IP_LIMIT",
        "AUTH_THROTTLE_LOGIN_EMAIL_LIMIT",
        "AUTH_THROTTLE_LOGIN_WINDOW_SECONDS",
        "AUTH_THROTTLE_MAIL_IP_LIMIT",
        "AUTH_THROTTLE_MAIL_EMAIL_LIMIT",
        "AUTH_THROTTLE_MAIL_WINDOW_SECONDS",
    ),
    "banner_generator": (
        "BANNER_GENERATOR_FUNCTION_URL",
        "BANNER_GENERATOR_AUTH_TOKEN",
        "BANNER_GENERATOR_TIMEOUT_SECONDS",
        "BANNER_UPLOAD_MAX_MB",
        "BANNER_UPLOAD_ALLOWED_TYPES",
        "BANNER_UPLOAD_KEY_PREFIX",
    ),
    "calendly": (
        "CALENDLY_ACCESS_TOKEN",
        "CALENDLY_WEBHOOK_SIGNING_KEY",
        "CALENDLY_OAUTH_CLIENT_ID",
        "CALENDLY_OAUTH_CLIENT_SECRET",
        "CALENDLY_REFRESH_TOKEN",
        "CALENDLY_ACCESS_TOKEN_EXPIRES_AT",
        "CALENDLY_CONNECTED_USER_URI",
        "CALENDLY_ORGANIZATION_URI",
        "CALENDLY_WEBHOOK_SUBSCRIPTION_URI",
        "CALENDLY_WEBHOOK_TOLERANCE_SECONDS",
        "CALENDLY_WEBHOOK_RETENTION_DAYS",
    ),
    "github": (
        "GITHUB_APP_ID",
        "GITHUB_APP_INSTALLATION_ID",
        "GITHUB_APP_PRIVATE_KEY_SECRET_ID",
        "GITHUB_APP_PRIVATE_KEY_SECRET_REGION",
        "GITHUB_APP_PRIVATE_KEY",
    ),
    "llm": (
        "LLM_PROVIDER",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_JUDGE_MODEL",
        "LLM_MAX_RETRIES",
        "ONBOARDING_AI_ENABLED",
        "ONBOARDING_AI_STREAMING",
        "ONBOARDING_AI_DEADLINE_SECONDS",
        "ONBOARDING_AI_MAX_ATTEMPTS",
        "NEXT_SPRINT_DRAFT_USE_PROFILE",
    ),
    "maven": (
        "MAVEN_ENROLLMENT_ENABLED",
        "MAVEN_WEBHOOK_SHARED_SECRET",
        "MAVEN_OVERRIDE_TIER_SLUG",
        "MAVEN_OVERRIDE_DURATION_DAYS",
        "MAVEN_COURSE_SLACK_CHANNEL",
    ),
    "observability": ("LOGFIRE_ENABLED", "LOGFIRE_TOKEN", "LOGFIRE_ENVIRONMENT"),
    "s3_content": ("AWS_S3_CONTENT_BUCKET", "AWS_S3_CONTENT_REGION", "CONTENT_CDN_BASE", "S3_ENABLED"),
    "s3_downloads": (
        "AWS_S3_DOWNLOADS_BUCKET",
        "AWS_S3_DOWNLOADS_REGION",
        "DOWNLOAD_PRESIGNED_URL_TTL_SECONDS",
        "DOWNLOAD_DELIVERY_TOKEN_TTL_HOURS",
    ),
    "s3_recordings": (
        "AWS_S3_RECORDINGS_BUCKET",
        "AWS_S3_RECORDINGS_REGION",
        "RECORDING_PRESIGNED_URL_TTL_SECONDS",
        "RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD",
        "RECORDING_TRANSCRIPT_INGEST_ENABLED",
        "RECORDING_RECAP_AUTO_DRAFT_ENABLED",
    ),
    "ses": (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SES_REGION",
        "SES_TRANSACTIONAL_FROM_EMAIL",
        "SES_PROMOTIONAL_FROM_EMAIL",
        "SES_WELCOME_FROM_EMAIL",
        "SES_WELCOME_REPLY_TO_EMAIL",
        "SES_CONFIGURATION_SET_NAME",
        "SES_WEBHOOK_VALIDATION_ENABLED",
        "SES_WEBHOOK_SHARED_SECRET",
        "EMAIL_BATCH_SIZE",
        "CAMPAIGN_DELIVERY_MAX_ATTEMPTS",
        "CAMPAIGN_BATCH_INTERVAL_SECONDS",
        "CAMPAIGN_TEST_RECIPIENTS",
        "RECORDING_AVAILABLE_SUBJECT_TEMPLATE",
        "RECORDING_AVAILABLE_BODY_TEMPLATE",
    ),
    "site": (
        "SITE_BASE_URL",
        "SITE_BASE_URL_ALIASES",
        "EVENT_DISPLAY_TIMEZONE",
        "SYNC_QUEUED_THRESHOLD_MINUTES",
        "SYNC_RUNNING_THRESHOLD_MINUTES",
        "EXPECT_WORKER",
        "PRIVACY_REQUEST_EMAIL",
        "PAYMENT_NOTIFICATION_EMAIL",
        "STAFF_SIGNUP_NOTIFY_EMAIL",
        "ONBOARDING_REMINDER_ENABLED",
        "ONBOARDING_REMINDER_DELAY_DAYS",
        "SPRINT_BADGE_WINDOW_DAYS",
        "SPRINT_END_AUTO_DISTRIBUTE_FEEDBACK_ENABLED",
        "CRM_EXPORT_MAX_LIMIT",
        "SOCIAL_YOUTUBE_URL",
        "SOCIAL_LINKEDIN_URL",
        "SOCIAL_GITHUB_URL",
        "SOCIAL_X_URL",
    ),
    "slack": (
        "SLACK_ENABLED",
        "SLACK_ENVIRONMENT",
        "SLACK_BOT_TOKEN",
        "SLACK_COMMUNITY_CHANNEL_IDS",
        "SLACK_ANNOUNCEMENTS_CHANNEL_ID",
        "STAFF_SIGNUP_NOTIFY_CHANNEL_ID",
        "STAFF_SLACK_JOIN_NOTIFY_ENABLED",
        "SLACK_DEV_COMMUNITY_CHANNEL_IDS",
        "SLACK_DEV_ANNOUNCEMENTS_CHANNEL_ID",
        "SLACK_TEST_COMMUNITY_CHANNEL_IDS",
        "SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID",
        "SLACK_PLAN_SPRINTS_CHANNEL_ID",
        "SLACK_PLAN_SPRINTS_USER_TOKEN",
        "SLACK_DEV_PLAN_SPRINTS_CHANNEL_ID",
        "SLACK_TEST_PLAN_SPRINTS_CHANNEL_ID",
        "SLACK_TEAM_REQUESTS_CHANNEL_ID",
        "SLACK_DEV_TEAM_REQUESTS_CHANNEL_ID",
        "SLACK_TEST_TEAM_REQUESTS_CHANNEL_ID",
        "PLAN_SPRINTS_FIRST_RUN_LOOKBACK_DAYS",
        "PLAN_SPRINTS_THREAD_REFRESH_DAYS",
        "PLAN_SPRINTS_INGEST_LEASE_MINUTES",
        "PLAN_SPRINTS_RAW_TEXT_RETENTION_DAYS",
        "SLACK_INVITE_URL",
        "SLACK_TEAM_ID",
        "BOOK_CLUB_SLACK_URL",
    ),
    "stripe": (
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "STRIPE_PAYMENT_LINKS",
        "STRIPE_CUSTOMER_PORTAL_URL",
        "STRIPE_DASHBOARD_ACCOUNT_ID",
        "STRIPE_WEBHOOK_EXPECTED_URL",
        "AUTHENTICATED_CHECKOUT_BINDING_ENABLED",
        "CHECKOUT_BINDING_TTL_MINUTES",
        "LEGACY_NUMERIC_CHECKOUT_REFERENCE_ENABLED",
        "LEGACY_NUMERIC_CHECKOUT_REFERENCE_CUTOFF",
        "STRIPE_RECONCILIATION_STALE_MINUTES",
        "STRIPE_MONTHLY_PAYMENT_GRACE_MODE",
        "PAYMENT_FAILURE_TEAM_EMAIL",
    ),
    "triggers": ("TRIGGERS_ENABLED",),
    "zoom": (
        "ZOOM_CLIENT_ID",
        "ZOOM_CLIENT_SECRET",
        "ZOOM_ACCOUNT_ID",
        "ZOOM_WEBHOOK_SECRET_TOKEN",
        "ZOOM_WEBHOOK_TOLERANCE_SECONDS",
        "ZOOM_WAITING_ROOM",
        "ZOOM_JOIN_BEFORE_HOST",
        "ZOOM_AUTO_RECORDING",
    ),
}

# The donor inventory omitted ``default`` for these keys. The package
# registry requires a concrete default, so they declare their value
# type's zero value (``''``/``0``/``False``); the derived legacy view
# omits their ``default`` so Studio and API source resolution and
# truthiness stay unchanged.
_KEYS_WITHOUT_DONOR_DEFAULT = frozenset(
    [
        "AWS_ACCESS_KEY_ID",
        "AWS_S3_CONTENT_BUCKET",
        "AWS_S3_CONTENT_REGION",
        "AWS_S3_DOWNLOADS_BUCKET",
        "AWS_S3_RECORDINGS_BUCKET",
        "AWS_S3_RECORDINGS_REGION",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SES_REGION",
        "BANNER_GENERATOR_AUTH_TOKEN",
        "BANNER_GENERATOR_FUNCTION_URL",
        "BOOK_CLUB_SLACK_URL",
        "CALENDLY_ACCESS_TOKEN",
        "CALENDLY_ACCESS_TOKEN_EXPIRES_AT",
        "CALENDLY_CONNECTED_USER_URI",
        "CALENDLY_OAUTH_CLIENT_ID",
        "CALENDLY_OAUTH_CLIENT_SECRET",
        "CALENDLY_ORGANIZATION_URI",
        "CALENDLY_REFRESH_TOKEN",
        "CALENDLY_WEBHOOK_SIGNING_KEY",
        "CALENDLY_WEBHOOK_SUBSCRIPTION_URI",
        "CAMPAIGN_BATCH_INTERVAL_SECONDS",
        "CAMPAIGN_TEST_RECIPIENTS",
        "CONTENT_CDN_BASE",
        "EVENT_DISPLAY_TIMEZONE",
        "GITHUB_APP_ID",
        "GITHUB_APP_INSTALLATION_ID",
        "GITHUB_APP_PRIVATE_KEY",
        "GOOGLE_ANALYTICS_ID",
        "LLM_API_KEY",
        "LOGFIRE_TOKEN",
        "MAVEN_COURSE_SLACK_CHANNEL",
        "MAVEN_WEBHOOK_SHARED_SECRET",
        "PAYMENT_NOTIFICATION_EMAIL",
        "PLAN_SPRINTS_FIRST_RUN_LOOKBACK_DAYS",
        "PLAN_SPRINTS_INGEST_LEASE_MINUTES",
        "PLAN_SPRINTS_RAW_TEXT_RETENTION_DAYS",
        "PLAN_SPRINTS_THREAD_REFRESH_DAYS",
        "RECORDING_AVAILABLE_BODY_TEMPLATE",
        "RECORDING_AVAILABLE_SUBJECT_TEMPLATE",
        "SES_CONFIGURATION_SET_NAME",
        "SES_PROMOTIONAL_FROM_EMAIL",
        "SES_TRANSACTIONAL_FROM_EMAIL",
        "SES_WEBHOOK_SHARED_SECRET",
        "SES_WEBHOOK_VALIDATION_ENABLED",
        "SES_WELCOME_FROM_EMAIL",
        "SITE_BASE_URL",
        "SITE_BASE_URL_ALIASES",
        "SLACK_ANNOUNCEMENTS_CHANNEL_ID",
        "SLACK_BOT_TOKEN",
        "SLACK_COMMUNITY_CHANNEL_IDS",
        "SLACK_DEV_ANNOUNCEMENTS_CHANNEL_ID",
        "SLACK_DEV_COMMUNITY_CHANNEL_IDS",
        "SLACK_DEV_PLAN_SPRINTS_CHANNEL_ID",
        "SLACK_DEV_TEAM_REQUESTS_CHANNEL_ID",
        "SLACK_ENABLED",
        "SLACK_ENVIRONMENT",
        "SLACK_INVITE_URL",
        "SLACK_PLAN_SPRINTS_CHANNEL_ID",
        "SLACK_PLAN_SPRINTS_USER_TOKEN",
        "SLACK_TEAM_ID",
        "SLACK_TEAM_REQUESTS_CHANNEL_ID",
        "SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID",
        "SLACK_TEST_COMMUNITY_CHANNEL_IDS",
        "SLACK_TEST_PLAN_SPRINTS_CHANNEL_ID",
        "SLACK_TEST_TEAM_REQUESTS_CHANNEL_ID",
        "SOCIAL_GITHUB_URL",
        "SOCIAL_LINKEDIN_URL",
        "SOCIAL_X_URL",
        "SOCIAL_YOUTUBE_URL",
        "STAFF_SIGNUP_NOTIFY_CHANNEL_ID",
        "STAFF_SIGNUP_NOTIFY_EMAIL",
        "STAFF_SLACK_JOIN_NOTIFY_ENABLED",
        "STRIPE_CUSTOMER_PORTAL_URL",
        "STRIPE_DASHBOARD_ACCOUNT_ID",
        "STRIPE_PAYMENT_LINKS",
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "UNVERIFIED_USER_TTL_DAYS",
        "ZOOM_ACCOUNT_ID",
        "ZOOM_CLIENT_ID",
        "ZOOM_CLIENT_SECRET",
        "ZOOM_WEBHOOK_SECRET_TOKEN",
    ]
)

# Donor-only metadata with no package-registry slot. ``allowed_values``
# has no consumer anywhere in the site; it is kept only so the derived
# legacy view stays identical to the donor inventory.
_DONOR_KEY_EXTRA_METADATA = {
    "STRIPE_MONTHLY_PAYMENT_GRACE_MODE": {"allowed_values": ["observe", "enforce"]},
    "LOGFIRE_ENABLED": {"requires_restart": True},
    "LOGFIRE_TOKEN": {"requires_restart": True},
    "LOGFIRE_ENVIRONMENT": {"requires_restart": True},
}

# --- Stripe (stripe) ---
STRIPE_SECRET_KEY = declare(
    key="STRIPE_SECRET_KEY",
    group="stripe",
    label="Stripe Secret Key",
    description="Server-side Stripe API key. Get from Stripe Dashboard > Developers > API keys. Without this checkout fails.",
    value_type="str",
    default="",
    secret=True,
    docs_url="_docs/integrations/stripe.md#stripe_secret_key",
)
STRIPE_WEBHOOK_SECRET = declare(
    key="STRIPE_WEBHOOK_SECRET",
    group="stripe",
    label="Stripe Webhook Secret",
    description="Verifies that webhook callbacks really came from Stripe. Get from Stripe Dashboard > Webhooks > [your endpoint].",
    value_type="str",
    default="",
    secret=True,
    docs_url="_docs/integrations/stripe.md#stripe_webhook_secret",
)
STRIPE_PAYMENT_LINKS = declare(
    key="STRIPE_PAYMENT_LINKS",
    group="stripe",
    label="Stripe Payment Links",
    description="Complete JSON matrix of Stripe Payment Links for Basic, Main, and Premium monthly/annual checkout. All six non-blank links are required; invalid overrides fall back to the links bundled in Django settings.",
    value_type="str",
    default="",
    secret=False,
    multiline=True,
    optional=True,
    django_settings_fallback=True,
    docs_url="_docs/integrations/stripe.md#stripe_payment_links",
)
STRIPE_CUSTOMER_PORTAL_URL = declare(
    key="STRIPE_CUSTOMER_PORTAL_URL",
    group="stripe",
    label="Stripe Customer Portal URL",
    description="Stripe-hosted page where members manage their subscription. Get from Stripe Dashboard > Settings > Billing > Customer portal.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/stripe.md#stripe_customer_portal_url",
)
STRIPE_DASHBOARD_ACCOUNT_ID = declare(
    key="STRIPE_DASHBOARD_ACCOUNT_ID",
    group="stripe",
    label="Stripe Dashboard Account ID",
    description='Stripe account ID used to build dashboard deep-links (e.g. "acct_1T1mfGB7mZrgL7H5"). Find it in the Stripe URL when you are signed in to your account. Optional — when blank, the Stripe icon next to a user is shown but not clickable.',
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/stripe.md#stripe_dashboard_account_id",
)
STRIPE_WEBHOOK_EXPECTED_URL = declare(
    key="STRIPE_WEBHOOK_EXPECTED_URL",
    group="stripe",
    label="Stripe Webhook Expected URL",
    description="Exact webhook callback URL the endpoint verifier expects Stripe to target. Defaults to the production URL; override it on non-production environments so the verifier checks the local host.",
    value_type="str",
    default="https://aishippinglabs.com/api/webhooks/payments",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/stripe.md#stripe_webhook_expected_url",
)
AUTHENTICATED_CHECKOUT_BINDING_ENABLED = declare(
    key="AUTHENTICATED_CHECKOUT_BINDING_ENABLED",
    group="stripe",
    label="Authenticated Checkout Binding Enabled",
    description="Kill switch for issuing authenticated opaque Stripe checkout bindings; enabled by default.",
    value_type="bool",
    default="true",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/stripe.md#authenticated_checkout_binding_enabled",
)
CHECKOUT_BINDING_TTL_MINUTES = declare(
    key="CHECKOUT_BINDING_TTL_MINUTES",
    group="stripe",
    label="Checkout Binding TTL Minutes",
    description="Lifetime in minutes for authenticated checkout bindings (clamped to 5–1440 minutes).",
    value_type="int",
    default="120",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/stripe.md#checkout_binding_ttl_minutes",
)
LEGACY_NUMERIC_CHECKOUT_REFERENCE_ENABLED = declare(
    key="LEGACY_NUMERIC_CHECKOUT_REFERENCE_ENABLED",
    group="stripe",
    label="Legacy Numeric Checkout Reference Enabled",
    description="Temporary compatibility switch for verified same-account numeric checkout references; the cutoff still applies.",
    value_type="bool",
    default="true",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/stripe.md#legacy_numeric_checkout_reference_enabled",
)
LEGACY_NUMERIC_CHECKOUT_REFERENCE_CUTOFF = declare(
    key="LEGACY_NUMERIC_CHECKOUT_REFERENCE_CUTOFF",
    group="stripe",
    label="Legacy Numeric Checkout Reference Cutoff",
    description="Hard UTC cutoff after which numeric checkout references are quarantined even if the compatibility switch remains enabled.",
    value_type="str",
    default="2026-08-01T00:00:00Z",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/stripe.md#legacy_numeric_checkout_reference_cutoff",
)
STRIPE_RECONCILIATION_STALE_MINUTES = declare(
    key="STRIPE_RECONCILIATION_STALE_MINUTES",
    group="stripe",
    label="Stripe Reconciliation Stale Minutes",
    description="Minutes after which a stuck running subscription-reconciliation run is marked failed so the next daily run can start.",
    value_type="str",
    default="120",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/stripe.md#stripe_reconciliation_stale_minutes",
)
STRIPE_MONTHLY_PAYMENT_GRACE_MODE = declare(
    key="STRIPE_MONTHLY_PAYMENT_GRACE_MODE",
    group="stripe",
    label="Stripe Monthly Payment Grace Mode",
    description="Observe records monthly payment grace without expiry; enforce enables the 168-hour reminder and base-tier transition policy.",
    value_type="str",
    default="observe",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/stripe.md#stripe_monthly_payment_grace_mode",
)
PAYMENT_FAILURE_TEAM_EMAIL = declare(
    key="PAYMENT_FAILURE_TEAM_EMAIL",
    group="stripe",
    label="Payment Failure Team Email",
    description="Validated single operator recipient for initial monthly payment-failure diagnostics.",
    value_type="str",
    default="team@aishippinglabs.com",
    secret=False,
    optional=True,
    is_email=True,
    docs_url="_docs/integrations/stripe.md#payment_failure_team_email",
)

# --- Zoom (zoom) ---
ZOOM_CLIENT_ID = declare(
    key="ZOOM_CLIENT_ID",
    group="zoom",
    label="Zoom Client ID",
    description="Zoom Server-to-Server OAuth app client ID. Without this we cannot create or fetch meetings.",
    value_type="str",
    default="",
    secret=True,
    docs_url="_docs/integrations/zoom.md#zoom_client_id",
)
ZOOM_CLIENT_SECRET = declare(
    key="ZOOM_CLIENT_SECRET",
    group="zoom",
    label="Zoom Client Secret",
    description="Zoom OAuth client secret. Get from your Zoom app under Marketplace > Build App > S2S OAuth.",
    value_type="str",
    default="",
    secret=True,
    docs_url="_docs/integrations/zoom.md#zoom_client_secret",
)
ZOOM_ACCOUNT_ID = declare(
    key="ZOOM_ACCOUNT_ID",
    group="zoom",
    label="Zoom Account ID",
    description="Zoom account UUID the OAuth app belongs to. Found in the Zoom Marketplace app settings.",
    value_type="str",
    default="",
    secret=True,
    docs_url="_docs/integrations/zoom.md#zoom_account_id",
)
ZOOM_WEBHOOK_SECRET_TOKEN = declare(
    key="ZOOM_WEBHOOK_SECRET_TOKEN",
    group="zoom",
    label="Zoom Webhook Secret Token",
    description="Verifies Zoom webhook callbacks (event start, recording ready). Set in the Zoom app event subscription.",
    value_type="str",
    default="",
    secret=True,
    docs_url="_docs/integrations/zoom.md#zoom_webhook_secret_token",
)
ZOOM_WEBHOOK_TOLERANCE_SECONDS = declare(
    key="ZOOM_WEBHOOK_TOLERANCE_SECONDS",
    group="zoom",
    label="Zoom Webhook Tolerance Seconds",
    description="Maximum accepted Zoom webhook age or future clock skew in seconds. Defaults to 300 seconds; invalid or non-positive overrides fall back to that safe default.",
    value_type="int",
    default="300",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/zoom.md#zoom_webhook_tolerance_seconds",
)
ZOOM_WAITING_ROOM = declare(
    key="ZOOM_WAITING_ROOM",
    group="zoom",
    label="Zoom Waiting Room",
    description="Set true to place attendees in a Zoom waiting room until the host admits them (requires the host to admit each attendee). Off by default — keeping join-before-host off is enough to make cloud recording start only when the host starts the meeting, with no manual admitting.",
    value_type="bool",
    default="false",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/zoom.md#zoom_waiting_room",
)
ZOOM_JOIN_BEFORE_HOST = declare(
    key="ZOOM_JOIN_BEFORE_HOST",
    group="zoom",
    label="Zoom Join Before Host",
    description='Set true to let attendees join a Zoom meeting before the host arrives (recommended OFF). Off by default — early joiners then see Zoom\'s "waiting for the host to start" hold and cloud recording does not begin until the host joins, so it never captures pre-host waiting time.',
    value_type="bool",
    default="false",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/zoom.md#zoom_join_before_host",
)
ZOOM_AUTO_RECORDING = declare(
    key="ZOOM_AUTO_RECORDING",
    group="zoom",
    label="Zoom Auto Recording",
    description="How event-created Zoom meetings auto-record: cloud (default, records to Zoom cloud so the recording-ready webhook can fetch it), local, or none. Requires cloud recording to be enabled and not locked at the Zoom account level for the host account, otherwise the per-meeting request is ignored.",
    value_type="str",
    default="cloud",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/zoom.md#zoom_auto_recording",
)

# --- Email (SES) (ses) ---
AWS_ACCESS_KEY_ID = declare(
    key="AWS_ACCESS_KEY_ID",
    group="ses",
    label="Aws Access Key ID",
    description="AWS access key for an IAM user with SES send + suppression-list permissions.",
    value_type="str",
    default="",
    secret=True,
    docs_url="_docs/integrations/ses.md#aws_access_key_id",
)
AWS_SECRET_ACCESS_KEY = declare(
    key="AWS_SECRET_ACCESS_KEY",
    group="ses",
    label="Aws Secret Access Key",
    description="AWS secret key paired with the access key above. Without these no email is sent.",
    value_type="str",
    default="",
    secret=True,
    docs_url="_docs/integrations/ses.md#aws_secret_access_key",
)
AWS_SES_REGION = declare(
    key="AWS_SES_REGION",
    group="ses",
    label="Aws SES Region",
    description="AWS region for SES (e.g. eu-west-1). Must match the verified domain region.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/ses.md#aws_ses_region",
)
SES_TRANSACTIONAL_FROM_EMAIL = declare(
    key="SES_TRANSACTIONAL_FROM_EMAIL",
    group="ses",
    label="SES Transactional From Email",
    description="Sender address for required account and service email. Must be verified in SES.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/ses.md#ses_transactional_from_email",
)
SES_PROMOTIONAL_FROM_EMAIL = declare(
    key="SES_PROMOTIONAL_FROM_EMAIL",
    group="ses",
    label="SES Promotional From Email",
    description="Sender address for campaigns, newsletters, and marketing email. Must be verified in SES.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/ses.md#ses_promotional_from_email",
)
SES_WELCOME_FROM_EMAIL = declare(
    key="SES_WELCOME_FROM_EMAIL",
    group="ses",
    label="SES Welcome From Email",
    description="Sender address for welcome emails (welcome, paid-signup, imported-user welcomes). Must be verified in SES.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/ses.md#ses_welcome_from_email",
)
SES_WELCOME_REPLY_TO_EMAIL = declare(
    key="SES_WELCOME_REPLY_TO_EMAIL",
    group="ses",
    label="SES Welcome Reply To Email",
    description="Reply-To address set on welcome emails so a member who replies reaches a monitored, team-forwarded inbox instead of the send-only welcome/noreply mailbox. Defaults to welcome@aishippinglabs.com (forwarded to the founders by the inbound email-forwarder Lambda). Leave blank to send welcome emails with no Reply-To header.",
    value_type="str",
    default="welcome@aishippinglabs.com",
    secret=False,
    optional=True,
    is_email=True,
    docs_url="_docs/integrations/ses.md#ses_welcome_reply_to_email",
)
SES_CONFIGURATION_SET_NAME = declare(
    key="SES_CONFIGURATION_SET_NAME",
    group="ses",
    label="SES Configuration Set Name",
    description='SES configuration set name that publishes delivery, open, bounce, and click events to SNS. Required in production: set to "aishippinglabs" (matches the configuration set in DataTalksClub/aws-infra at main/aisl/email.tf). When blank, SES publishes no events to SNS regardless of the HTTPS subscription wiring, so the bounce / complaint webhook never fires. Safe to leave blank only in local dev.',
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/ses.md#ses_configuration_set_name",
)
SES_WEBHOOK_VALIDATION_ENABLED = declare(
    key="SES_WEBHOOK_VALIDATION_ENABLED",
    group="ses",
    label="SES Webhook Validation Enabled",
    description="Set true to verify SNS bounce/complaint signatures (recommended in production).",
    value_type="bool",
    default=False,
    secret=False,
    docs_url="_docs/integrations/ses.md#ses_webhook_validation_enabled",
)
SES_WEBHOOK_SHARED_SECRET = declare(
    key="SES_WEBHOOK_SHARED_SECRET",
    group="ses",
    label="SES Webhook Shared Secret",
    description="Optional shared secret required in the X-SES-Webhook-Secret header on the SES webhook. Set in prod and inject from the infra-side Lambda forwarder. Leave blank locally to allow runserver replay.",
    value_type="str",
    default="",
    secret=True,
    optional=True,
    docs_url="_docs/integrations/ses.md#ses_webhook_shared_secret",
)
EMAIL_BATCH_SIZE = declare(
    key="EMAIL_BATCH_SIZE",
    group="ses",
    label="Email Batch Size",
    description="Positive number of recipients placed in each campaign send task. Default 200; invalid, zero, or negative overrides safely fall back to 200.",
    value_type="int",
    default="200",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/ses.md#email_batch_size",
)
CAMPAIGN_DELIVERY_MAX_ATTEMPTS = declare(
    key="CAMPAIGN_DELIVERY_MAX_ATTEMPTS",
    group="ses",
    label="Campaign Delivery Max Attempts",
    description="Maximum SES attempts for a failed campaign delivery before it stays failed and the campaign needs attention. Default 3; invalid, zero, or negative overrides fall back to 3. Ambiguous outcomes are never auto-retried.",
    value_type="int",
    default="3",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/ses.md#campaign_delivery_max_attempts",
)
CAMPAIGN_BATCH_INTERVAL_SECONDS = declare(
    key="CAMPAIGN_BATCH_INTERVAL_SECONDS",
    group="ses",
    label="Campaign Batch Interval Seconds",
    description="Seconds to stagger campaign send batches apart so the fan-out does not burst past the SES send-rate limit (issue #922). Batch i is scheduled at now + i * this interval; the first batch fires immediately. Default 60. Set to 0 to send all batches at once (no stagger).",
    value_type="int",
    default=0,
    secret=False,
    optional=True,
    docs_url="_docs/integrations/ses.md#campaign_batch_interval_seconds",
)
CAMPAIGN_TEST_RECIPIENTS = declare(
    key="CAMPAIGN_TEST_RECIPIENTS",
    group="ses",
    label="Campaign Test Recipients",
    description="Comma/space/semicolon/newline-separated list of common test-send addresses surfaced as click-to-fill chips beneath the Test Recipients field on the campaign detail page (issue #921). Lets operators one-click-fill the mailboxes they repeatedly test to (a teammate, a QA seed inbox) instead of retyping. Invalid entries are silently dropped. Leave blank to show only the operator's own email and recently-sent addresses.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/ses.md#campaign_test_recipients",
)
RECORDING_AVAILABLE_SUBJECT_TEMPLATE = declare(
    key="RECORDING_AVAILABLE_SUBJECT_TEMPLATE",
    group="ses",
    label="Recording Available Subject Template",
    description='Default subject pre-filled into the "recording available" campaign draft an operator reaches from the host recording-ready email or the Studio event page (issue #1076). ``{event_title}`` is substituted with the event title. Pre-fill only — the operator reviews and edits the draft before sending, so a blank/odd setting can never auto-broadcast.',
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/ses.md#recording_available_subject_template",
)
RECORDING_AVAILABLE_BODY_TEMPLATE = declare(
    key="RECORDING_AVAILABLE_BODY_TEMPLATE",
    group="ses",
    label="Recording Available Body Template",
    description='Default markdown body pre-filled into the "recording available" campaign draft (issue #1076). Placeholders: ``{event_title}``, ``{recording_url}``, and ``{workshop_writeup}`` (the linked workshop write-up, or a short generic line when the event has no linked workshop). Pre-fill only — never auto-sent.',
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/ses.md#recording_available_body_template",
)

# --- S3 Recordings (s3_recordings) ---
AWS_S3_RECORDINGS_BUCKET = declare(
    key="AWS_S3_RECORDINGS_BUCKET",
    group="s3_recordings",
    label="Aws S3 Recordings Bucket",
    description="S3 bucket where event recordings are uploaded after processing.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/s3_recordings.md#aws_s3_recordings_bucket",
)
AWS_S3_RECORDINGS_REGION = declare(
    key="AWS_S3_RECORDINGS_REGION",
    group="s3_recordings",
    label="Aws S3 Recordings Region",
    description="AWS region of the recordings bucket (e.g. eu-west-1).",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/s3_recordings.md#aws_s3_recordings_region",
)
RECORDING_PRESIGNED_URL_TTL_SECONDS = declare(
    key="RECORDING_PRESIGNED_URL_TTL_SECONDS",
    group="s3_recordings",
    label="Recording Presigned URL TTL Seconds",
    description="Lifetime (in seconds) of the short-lived presigned S3 GetObject URL the access-controlled recording serving endpoint redirects to (issue #1134). Default 900 (15 minutes). The presigned URL is never rendered into HTML — the in-page video player points at the authenticated serving endpoint, which re-checks access and mints a fresh presigned URL on every request. Keep this long enough that a member can watch/seek without the URL expiring mid-playback, but short enough that a leaked URL is quickly useless.",
    value_type="int",
    default="900",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/s3_recordings.md#recording_presigned_url_ttl_seconds",
)
RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD = declare(
    key="RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD",
    group="s3_recordings",
    label="Recording Auto Publish On S3 Upload",
    description='When on, a successful Zoom -> S3 recording upload auto-publishes the event so entitled members can watch the recording right away, and the host notification says the recording is available to watch with a link to the workshop video page (issue #1134, Phase B). On by default per the product decision that the recording should be watchable immediately. Turn it off to keep the review-first flow: the event stays unpublished after upload and the host email keeps the "ready for review/publishing" framing with a Studio link.',
    value_type="bool",
    default="true",
    secret=False,
    docs_url="_docs/integrations/s3_recordings.md#recording_auto_publish_on_s3_upload",
)
RECORDING_TRANSCRIPT_INGEST_ENABLED = declare(
    key="RECORDING_TRANSCRIPT_INGEST_ENABLED",
    group="s3_recordings",
    label="Recording Transcript Ingest Enabled",
    description="When on, Zoom webhooks and the post-S3-upload chain automatically download, parse, and store recording transcripts. The explicit sync-transcript recovery action still works when this is off.",
    value_type="bool",
    default="true",
    secret=False,
    docs_url="_docs/integrations/zoom.md#recording_transcript_ingest_enabled",
)
RECORDING_RECAP_AUTO_DRAFT_ENABLED = declare(
    key="RECORDING_RECAP_AUTO_DRAFT_ENABLED",
    group="s3_recordings",
    label="Recording Recap Auto Draft Enabled",
    description="When on, a stored transcript automatically drafts recap_notes through the configured LLM when operator notes are still empty. Registrant notifications remain explicit.",
    value_type="bool",
    default="true",
    secret=False,
    docs_url="_docs/integrations/zoom.md#recording_recap_auto_draft_enabled",
)

# --- S3 Content Images (s3_content) ---
AWS_S3_CONTENT_BUCKET = declare(
    key="AWS_S3_CONTENT_BUCKET",
    group="s3_content",
    label="Aws S3 Content Bucket",
    description="S3 bucket for content images extracted from synced markdown. Public-read.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/s3_content.md#aws_s3_content_bucket",
)
AWS_S3_CONTENT_REGION = declare(
    key="AWS_S3_CONTENT_REGION",
    group="s3_content",
    label="Aws S3 Content Region",
    description="AWS region of the content-images bucket.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/s3_content.md#aws_s3_content_region",
)
CONTENT_CDN_BASE = declare(
    key="CONTENT_CDN_BASE",
    group="s3_content",
    label="Content Cdn Base",
    description="Public CDN base URL fronting the content bucket (e.g. https://cdn.aishippinglabs.com). Without this images break on the live site.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/s3_content.md#content_cdn_base",
)
S3_ENABLED = declare(
    key="S3_ENABLED",
    group="s3_content",
    label="S3 Enabled",
    description="Master switch for content-image uploads to S3 during content sync. On by default; set explicitly to false to disable content-image uploads. When off, image URLs are still rewritten to CDN paths but no objects are uploaded, so images 403 in production. Leave on in production.",
    value_type="bool",
    default="true",
    secret=False,
    docs_url="_docs/integrations/s3_content.md#s3_enabled",
)

# --- S3 Downloads (s3_downloads) ---
AWS_S3_DOWNLOADS_BUCKET = declare(
    key="AWS_S3_DOWNLOADS_BUCKET",
    group="s3_downloads",
    label="Aws S3 Downloads Bucket",
    description="Private S3 bucket containing gated downloadable resources.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/s3_downloads.md#aws_s3_downloads_bucket",
)
AWS_S3_DOWNLOADS_REGION = declare(
    key="AWS_S3_DOWNLOADS_REGION",
    group="s3_downloads",
    label="Aws S3 Downloads Region",
    description="AWS region of the private downloads bucket.",
    value_type="str",
    default="eu-central-1",
    secret=False,
    docs_url="_docs/integrations/s3_downloads.md#aws_s3_downloads_region",
)
DOWNLOAD_PRESIGNED_URL_TTL_SECONDS = declare(
    key="DOWNLOAD_PRESIGNED_URL_TTL_SECONDS",
    group="s3_downloads",
    label="Download Presigned URL TTL Seconds",
    description="Lifetime of an authorized S3 download redirect in seconds.",
    value_type="int",
    default="300",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/s3_downloads.md#download_presigned_url_ttl_seconds",
)
DOWNLOAD_DELIVERY_TOKEN_TTL_HOURS = declare(
    key="DOWNLOAD_DELIVERY_TOKEN_TTL_HOURS",
    group="s3_downloads",
    label="Download Delivery Token TTL Hours",
    description="Lifetime of a requested, slug-scoped email delivery link.",
    value_type="int",
    default="24",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/s3_downloads.md#download_delivery_token_ttl_hours",
)

# --- Calendly (calendly) ---
CALENDLY_ACCESS_TOKEN = declare(
    key="CALENDLY_ACCESS_TOKEN",
    group="calendly",
    label="Calendly Access Token",
    description="Calendly host access token (personal access token or an OAuth access token) used to read scheduled events and create the webhook subscription. Get a personal token from Calendly > Integrations > API & Webhooks. Without it the platform cannot register the booked-call webhook or fetch event details.",
    value_type="str",
    default="",
    secret=True,
    optional=True,
    docs_url="_docs/integrations/calendly.md#calendly_access_token",
)
CALENDLY_WEBHOOK_SIGNING_KEY = declare(
    key="CALENDLY_WEBHOOK_SIGNING_KEY",
    group="calendly",
    label="Calendly Webhook Signing Key",
    description="Signing key Calendly returns when the webhook subscription is created. Verifies that invitee.created / invitee.canceled callbacks really came from Calendly. When blank, webhook calls are rejected in every environment.",
    value_type="str",
    default="",
    secret=True,
    optional=True,
    docs_url="_docs/integrations/calendly.md#calendly_webhook_signing_key",
)
CALENDLY_OAUTH_CLIENT_ID = declare(
    key="CALENDLY_OAUTH_CLIENT_ID",
    group="calendly",
    label="Calendly Oauth Client ID",
    description="Calendly OAuth app client ID. Used for the optional authorize-Calendly flow that mints a host access token without pasting a personal token. Get it from Calendly > Integrations > OAuth applications.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/calendly.md#calendly_oauth_client_id",
)
CALENDLY_OAUTH_CLIENT_SECRET = declare(
    key="CALENDLY_OAUTH_CLIENT_SECRET",
    group="calendly",
    label="Calendly Oauth Client Secret",
    description="Calendly OAuth app client secret paired with the client ID above. Required only for the authorize flow.",
    value_type="str",
    default="",
    secret=True,
    optional=True,
    docs_url="_docs/integrations/calendly.md#calendly_oauth_client_secret",
)
CALENDLY_REFRESH_TOKEN = declare(
    key="CALENDLY_REFRESH_TOKEN",
    group="calendly",
    label="Calendly Refresh Token",
    description="Managed rotating OAuth refresh token. Do not edit manually.",
    value_type="str",
    default="",
    secret=True,
    optional=True,
    docs_url="_docs/integrations/calendly.md#calendly_refresh_token",
)
CALENDLY_ACCESS_TOKEN_EXPIRES_AT = declare(
    key="CALENDLY_ACCESS_TOKEN_EXPIRES_AT",
    group="calendly",
    label="Calendly Access Token Expires At",
    description="Managed ISO timestamp for OAuth access-token refresh.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/calendly.md#managed_oauth_state",
)
CALENDLY_CONNECTED_USER_URI = declare(
    key="CALENDLY_CONNECTED_USER_URI",
    group="calendly",
    label="Calendly Connected User Uri",
    description="Validated Calendly user URI for operator diagnostics.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/calendly.md#managed_oauth_state",
)
CALENDLY_ORGANIZATION_URI = declare(
    key="CALENDLY_ORGANIZATION_URI",
    group="calendly",
    label="Calendly Organization Uri",
    description="Validated Calendly organization used for webhook provisioning.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/calendly.md#managed_oauth_state",
)
CALENDLY_WEBHOOK_SUBSCRIPTION_URI = declare(
    key="CALENDLY_WEBHOOK_SUBSCRIPTION_URI",
    group="calendly",
    label="Calendly Webhook Subscription Uri",
    description="Managed active webhook subscription URI.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/calendly.md#managed_oauth_state",
)
CALENDLY_WEBHOOK_TOLERANCE_SECONDS = declare(
    key="CALENDLY_WEBHOOK_TOLERANCE_SECONDS",
    group="calendly",
    label="Calendly Webhook Tolerance Seconds",
    description="Maximum accepted webhook signature age/future skew in seconds. Signatures are always required.",
    value_type="int",
    default="300",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/calendly.md#calendly_webhook_tolerance_seconds",
)
CALENDLY_WEBHOOK_RETENTION_DAYS = declare(
    key="CALENDLY_WEBHOOK_RETENTION_DAYS",
    group="calendly",
    label="Calendly Webhook Retention Days",
    description="Days to retain processed Calendly webhook payloads.",
    value_type="int",
    default="30",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/calendly.md#calendly_webhook_retention_days",
)

# --- GitHub App (github) ---
GITHUB_APP_ID = declare(
    key="GITHUB_APP_ID",
    group="github",
    label="Github App ID",
    description="Numeric ID of the GitHub App used to read content repos. Found at github.com/settings/apps/<your-app>.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/github.md#github_app_id",
)
GITHUB_APP_INSTALLATION_ID = declare(
    key="GITHUB_APP_INSTALLATION_ID",
    group="github",
    label="Github App Installation ID",
    description="Installation ID of the GitHub App on the content org. Found at github.com/organizations/<org>/settings/installations.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/github.md#github_app_installation_id",
)
GITHUB_APP_PRIVATE_KEY_SECRET_ID = declare(
    key="GITHUB_APP_PRIVATE_KEY_SECRET_ID",
    group="github",
    label="Github App Private Key Secret ID",
    description="AWS Secrets Manager secret name, path, or ARN containing the GitHub App PEM private key. Leave the PEM field empty when this is set.",
    value_type="str",
    default="ai-shipping-labs/github-app-private-key",
    secret=False,
    docs_url="_docs/integrations/github.md#github_app_private_key_secret_id",
)
GITHUB_APP_PRIVATE_KEY_SECRET_REGION = declare(
    key="GITHUB_APP_PRIVATE_KEY_SECRET_REGION",
    group="github",
    label="Github App Private Key Secret Region",
    description="AWS region for the GitHub App private-key secret. Defaults to eu-west-1 when empty.",
    value_type="str",
    default="eu-west-1",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/github.md#github_app_private_key_secret_region",
)
GITHUB_APP_PRIVATE_KEY = declare(
    key="GITHUB_APP_PRIVATE_KEY",
    group="github",
    label="Github App Private Key",
    description="Optional direct PEM private key issued by GitHub. Prefer the AWS Secrets Manager secret path above for production.",
    value_type="str",
    default="",
    secret=True,
    multiline=True,
    optional=True,
    docs_url="_docs/integrations/github.md#github_app_private_key",
)

# --- Slack (slack) ---
SLACK_ENABLED = declare(
    key="SLACK_ENABLED",
    group="slack",
    label="Slack Enabled",
    description="Set true to enable Slack bot posting and event listening. Off by default to keep dev/test silent.",
    value_type="bool",
    default=False,
    secret=False,
    docs_url="_docs/integrations/slack.md#slack_enabled",
)
SLACK_ENVIRONMENT = declare(
    key="SLACK_ENVIRONMENT",
    group="slack",
    label="Slack Environment",
    description="Slack routing mode: production, development, or test. Non-production modes ignore production channel IDs.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/slack.md#slack_environment",
)
SLACK_BOT_TOKEN = declare(
    key="SLACK_BOT_TOKEN",
    group="slack",
    label="Slack Bot Token",
    description="Slack bot user OAuth token (xoxb-...). Used to post announcements and read community channel events.",
    value_type="str",
    default="",
    secret=True,
    docs_url="_docs/integrations/slack.md#slack_bot_token",
)
SLACK_COMMUNITY_CHANNEL_IDS = declare(
    key="SLACK_COMMUNITY_CHANNEL_IDS",
    group="slack",
    label="Slack Community Channel Ids",
    description="Comma-separated channel IDs the bot watches for community signals (mentions, reactions).",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/slack.md#slack_community_channel_ids",
)
SLACK_ANNOUNCEMENTS_CHANNEL_ID = declare(
    key="SLACK_ANNOUNCEMENTS_CHANNEL_ID",
    group="slack",
    label="Slack Announcements Channel ID",
    description="Channel ID where the bot posts new content and event announcements.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/slack.md#slack_announcements_channel_id",
)
STAFF_SIGNUP_NOTIFY_CHANNEL_ID = declare(
    key="STAFF_SIGNUP_NOTIFY_CHANNEL_ID",
    group="slack",
    label="Staff Signup Notify Channel ID",
    description="Slack channel ID where the bot posts an internal heads-up every time a paid signup completes (Basic and above). Leave blank to skip the Slack post; the staff email side still runs.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/slack.md#staff_signup_notify_channel_id",
)
STAFF_SLACK_JOIN_NOTIFY_ENABLED = declare(
    key="STAFF_SLACK_JOIN_NOTIFY_ENABLED",
    group="slack",
    label="Staff Slack Join Notify Enabled",
    description="Enables the staff heads-up (email + optional Slack post) sent when the periodic membership refresh observes a known user genuinely join the Slack workspace. Recommended ON. Acts as a no-redeploy kill switch — turn off to suppress all join notifications. Reuses STAFF_SIGNUP_NOTIFY_EMAIL and STAFF_SIGNUP_NOTIFY_CHANNEL_ID for delivery.",
    value_type="bool",
    default=False,
    secret=False,
    optional=True,
    docs_url="_docs/integrations/slack.md#staff_slack_join_notify_enabled",
)
SLACK_DEV_COMMUNITY_CHANNEL_IDS = declare(
    key="SLACK_DEV_COMMUNITY_CHANNEL_IDS",
    group="slack",
    label="Slack Dev Community Channel Ids",
    description="Development-only community channel IDs. Used only when SLACK_ENVIRONMENT=development.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/slack.md#slack_dev_community_channel_ids",
)
SLACK_DEV_ANNOUNCEMENTS_CHANNEL_ID = declare(
    key="SLACK_DEV_ANNOUNCEMENTS_CHANNEL_ID",
    group="slack",
    label="Slack Dev Announcements Channel ID",
    description="Development-only announcement channel ID. Used only when SLACK_ENVIRONMENT=development.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/slack.md#slack_dev_announcements_channel_id",
)
SLACK_TEST_COMMUNITY_CHANNEL_IDS = declare(
    key="SLACK_TEST_COMMUNITY_CHANNEL_IDS",
    group="slack",
    label="Slack Test Community Channel Ids",
    description="Test-only community channel IDs. Used only when SLACK_ENVIRONMENT=test.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/slack.md#slack_test_community_channel_ids",
)
SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID = declare(
    key="SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID",
    group="slack",
    label="Slack Test Announcements Channel ID",
    description="Test-only announcement channel ID, e.g. #integration-tests. Used only when SLACK_ENVIRONMENT=test.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/slack.md#slack_test_announcements_channel_id",
)
SLACK_PLAN_SPRINTS_CHANNEL_ID = declare(
    key="SLACK_PLAN_SPRINTS_CHANNEL_ID",
    group="slack",
    label="Slack Plan Sprints Channel ID",
    description="Channel ID of #plan-sprints. The daily ingest job (issue #889) reads member sprint updates from here. Requires channels:history/groups:history and the bot to be a member. Leave blank to disable ingestion.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/slack.md#slack_plan_sprints_channel_id",
)
SLACK_PLAN_SPRINTS_USER_TOKEN = declare(
    key="SLACK_PLAN_SPRINTS_USER_TOKEN",
    group="slack",
    label="Slack Plan Sprints User Token",
    description="User OAuth token (xoxp-...) used only for conversations.replies on public/private #plan-sprints threads. Required for full-thread ingestion; the bot token remains in use for history and all other Slack calls.",
    value_type="str",
    default="",
    secret=True,
    optional=True,
    docs_url="_docs/integrations/slack.md#slack_plan_sprints_user_token",
)
SLACK_DEV_PLAN_SPRINTS_CHANNEL_ID = declare(
    key="SLACK_DEV_PLAN_SPRINTS_CHANNEL_ID",
    group="slack",
    label="Slack Dev Plan Sprints Channel ID",
    description="Development-only #plan-sprints channel ID. Used only when SLACK_ENVIRONMENT=development.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/slack.md#slack_dev_plan_sprints_channel_id",
)
SLACK_TEST_PLAN_SPRINTS_CHANNEL_ID = declare(
    key="SLACK_TEST_PLAN_SPRINTS_CHANNEL_ID",
    group="slack",
    label="Slack Test Plan Sprints Channel ID",
    description="Test-only #plan-sprints channel ID. Used only when SLACK_ENVIRONMENT=test.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/slack.md#slack_test_plan_sprints_channel_id",
)
SLACK_TEAM_REQUESTS_CHANNEL_ID = declare(
    key="SLACK_TEAM_REQUESTS_CHANNEL_ID",
    group="slack",
    label="Slack Team Requests Channel ID",
    description='Production channel ID of the team-requests channel where staff notifications post: plan-request pings ("Ask the team to plan with me", issue #585) and onboarding-submitted heads-ups (issue #882). Leave blank to skip the Slack post; email + in-app notifications still run. Used only when SLACK_ENVIRONMENT=production.',
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/slack.md#slack_team_requests_channel_id",
)
SLACK_DEV_TEAM_REQUESTS_CHANNEL_ID = declare(
    key="SLACK_DEV_TEAM_REQUESTS_CHANNEL_ID",
    group="slack",
    label="Slack Dev Team Requests Channel ID",
    description="Development-only team-requests channel ID. Used only when SLACK_ENVIRONMENT=development.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/slack.md#slack_dev_team_requests_channel_id",
)
SLACK_TEST_TEAM_REQUESTS_CHANNEL_ID = declare(
    key="SLACK_TEST_TEAM_REQUESTS_CHANNEL_ID",
    group="slack",
    label="Slack Test Team Requests Channel ID",
    description="Test-only team-requests channel ID. Used only when SLACK_ENVIRONMENT=test.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/slack.md#slack_test_team_requests_channel_id",
)
PLAN_SPRINTS_FIRST_RUN_LOOKBACK_DAYS = declare(
    key="PLAN_SPRINTS_FIRST_RUN_LOOKBACK_DAYS",
    group="slack",
    label="Plan Sprints First Run Lookback Days",
    description="How many days the #plan-sprints ingest reads back on its very first run, before the forward watermark takes over. Used only when no prior successful run exists and no explicit since/oldest_ts is given (e.g. the retroactive backfill command/API of issue #904). Defaults to 7.",
    value_type="int",
    default=0,
    secret=False,
    optional=True,
    docs_url="_docs/integrations/slack.md#plan_sprints_first_run_lookback_days",
)
PLAN_SPRINTS_THREAD_REFRESH_DAYS = declare(
    key="PLAN_SPRINTS_THREAD_REFRESH_DAYS",
    group="slack",
    label="Plan Sprints Thread Refresh Days",
    description="How many recent days of unmatched/completed #plan-sprints threads the daily job re-checks for late replies; active-sprint threads are always checked. Defaults to 45.",
    value_type="int",
    default=0,
    secret=False,
    optional=True,
    docs_url="_docs/integrations/slack.md#plan_sprints_thread_refresh_days",
)
PLAN_SPRINTS_INGEST_LEASE_MINUTES = declare(
    key="PLAN_SPRINTS_INGEST_LEASE_MINUTES",
    group="slack",
    label="Plan Sprints Ingest Lease Minutes",
    description="Maximum age of a running #plan-sprints ingest lease before it is terminalized as abandoned. Defaults to 60 minutes.",
    value_type="int",
    default=0,
    secret=False,
    optional=True,
    docs_url="_docs/integrations/slack.md#plan_sprints_ingest_lease_minutes",
)
PLAN_SPRINTS_RAW_TEXT_RETENTION_DAYS = declare(
    key="PLAN_SPRINTS_RAW_TEXT_RETENTION_DAYS",
    group="slack",
    label="Plan Sprints Raw Text Retention Days",
    description="Days to retain raw #plan-sprints Slack message text locally before the daily retention task redacts it. Defaults to 365.",
    value_type="int",
    default=0,
    secret=False,
    optional=True,
    docs_url="_docs/integrations/slack.md#plan_sprints_raw_text_retention_days",
)
SLACK_INVITE_URL = declare(
    key="SLACK_INVITE_URL",
    group="slack",
    label="Slack Invite URL",
    description="Public Slack workspace invite URL shown to Main+ members on the dashboard.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/slack.md#slack_invite_url",
)
SLACK_TEAM_ID = declare(
    key="SLACK_TEAM_ID",
    group="slack",
    label="Slack Team ID",
    description='Workspace team ID (e.g. "T01ABC123"). Used to build deep links from Studio to a member\'s Slack profile. Find it in Slack: workspace menu > Settings & administration > Workspace settings, or in any Slack URL after "/team/". Optional — when blank, the Slack icon next to a user is shown but not clickable.',
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/slack.md#slack_team_id",
)
BOOK_CLUB_SLACK_URL = declare(
    key="BOOK_CLUB_SLACK_URL",
    group="slack",
    label="Book Club Slack URL",
    description="Link to the #book-club channel shown to members on Book Club surfaces. A workspace deep link (e.g. https://<workspace>.slack.com/archives/<channel_id>) or any join URL. Leave blank to fall back to the account page, where Slack joining lives.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/slack.md#book_club_slack_url",
)

# --- Site (site) ---
SITE_BASE_URL = declare(
    key="SITE_BASE_URL",
    group="site",
    label="Site Base URL",
    description="Canonical absolute URL — used for generated links, OAuth callbacks, etc.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/site.md#site_base_url",
)
SITE_BASE_URL_ALIASES = declare(
    key="SITE_BASE_URL_ALIASES",
    group="site",
    label="Site Base URL Aliases",
    description="Additional hosts that should not trigger the host-mismatch banner. Comma- or whitespace-separated (newlines work too).",
    value_type="str",
    default="",
    secret=False,
    multiline=True,
    docs_url="_docs/integrations/site.md#site_base_url_aliases",
)
EVENT_DISPLAY_TIMEZONE = declare(
    key="EVENT_DISPLAY_TIMEZONE",
    group="site",
    label="Event Display Timezone",
    description="Default IANA timezone for public event times when the browser cannot provide one.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/site.md#event_display_timezone",
)

SYNC_QUEUED_THRESHOLD_MINUTES = declare(
    key="SYNC_QUEUED_THRESHOLD_MINUTES",
    group="site",
    label="Sync Queued Threshold Minutes",
    description='Minutes a content sync may remain queued before the watchdog marks it failed; invalid or non-positive input fall back to 10.',
    value_type="int",
    default="10",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/site.md#sync_queued_threshold_minutes",
)

SYNC_RUNNING_THRESHOLD_MINUTES = declare(
    key="SYNC_RUNNING_THRESHOLD_MINUTES",
    group="site",
    label="Sync Running Threshold Minutes",
    description='Minutes a content sync may remain running before the watchdog marks it failed; invalid or non-positive input fall back to 30.',
    value_type="int",
    default="30",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/site.md#sync_running_threshold_minutes",
)

EXPECT_WORKER = declare(
    key="EXPECT_WORKER",
    group="site",
    label="Expect Worker",
    description='Whether this environment expects a django-q worker; disable only for one-off environments without a worker.',
    value_type="bool",
    default="true",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/site.md#expect_worker",
)
PRIVACY_REQUEST_EMAIL = declare(
    key="PRIVACY_REQUEST_EMAIL",
    group="site",
    label="Privacy Request Email",
    description="Validated team mailbox that receives signed-in account deletion requests, with the requester visibly copied.",
    value_type="str",
    default="team@aishippinglabs.com",
    secret=False,
    is_email=True,
    docs_url="_docs/integrations/site.md#privacy_request_email",
)
PAYMENT_NOTIFICATION_EMAIL = declare(
    key="PAYMENT_NOTIFICATION_EMAIL",
    group="site",
    label="Payment Notification Email",
    description="Operator email address that receives an internal notification whenever a Stripe checkout completes (new paid signup, tier upgrade, or course purchase). Leave blank to disable — there is no hard-coded default, so a blank setting means nobody is notified.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/site.md#payment_notification_email",
)
STAFF_SIGNUP_NOTIFY_EMAIL = declare(
    key="STAFF_SIGNUP_NOTIFY_EMAIL",
    group="site",
    label="Staff Signup Notify Email",
    description="Single staff mailbox used for paid signups: hidden BCC on the member-facing paid welcome, the structured internal heads-up email, and (issue #1133) the hidden BCC on the one-week onboarding reminder. Leave blank to skip these staff copies; the member welcome and reminder still send and Slack still runs when configured. Replies still route only via SES_WELCOME_REPLY_TO_EMAIL.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    is_email=True,
    docs_url="_docs/integrations/site.md#staff_signup_notify_email",
)
ONBOARDING_REMINDER_ENABLED = declare(
    key="ONBOARDING_REMINDER_ENABLED",
    group="site",
    label="Onboarding Reminder Enabled",
    description="Master switch for the one-week onboarding reminder sweep (issue #1133). When on, a daily job emails paid members who received their onboarding-link welcome but have not completed onboarding after ONBOARDING_REMINDER_DELAY_DAYS. When off, the sweep is a no-op (no emails, no logs). Defaults on; switchable without a redeploy.",
    value_type="bool",
    default="true",
    secret=False,
    docs_url="_docs/integrations/site.md#onboarding_reminder_enabled",
)
ONBOARDING_REMINDER_DELAY_DAYS = declare(
    key="ONBOARDING_REMINDER_DELAY_DAYS",
    group="site",
    label="Onboarding Reminder Delay Days",
    description="Days after the onboarding-link welcome email before the reminder is due (issue #1133). A member whose earliest welcome is older than this and who has not onboarded is reminded once. Default 7. A blank, non-numeric, or non-positive override falls back to 7.",
    value_type="int",
    default="7",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/site.md#onboarding_reminder_delay_days",
)
SPRINT_BADGE_WINDOW_DAYS = declare(
    key="SPRINT_BADGE_WINDOW_DAYS",
    group="site",
    label="Sprint Badge Window Days",
    description='Window in days around a sprint start / end that flips the date-derived sprint badge to "Starting soon" (within this many days before start) and "Ending soon" (within this many days of end). A larger window surfaces the soon-states earlier. Default 7. A blank, non-numeric, or non-positive override falls back to 7.',
    value_type="int",
    default="7",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/site.md#sprint_badge_window_days",
)
SPRINT_END_AUTO_DISTRIBUTE_FEEDBACK_ENABLED = declare(
    key="SPRINT_END_AUTO_DISTRIBUTE_FEEDBACK_ENABLED",
    group="site",
    label="Sprint End Auto Distribute Feedback Enabled",
    description="When on, the daily sprint-end recap job distributes attached sprint feedback requests before sending member recaps, so the recap can link to each member feedback form. Defaults off for staff-controlled distribution.",
    value_type="bool",
    default="false",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/site.md#sprint_end_auto_distribute_feedback_enabled",
)
CRM_EXPORT_MAX_LIMIT = declare(
    key="CRM_EXPORT_MAX_LIMIT",
    group="site",
    label="CRM Export Max Limit",
    description="Hard ceiling on the page size for the CRM export endpoint (GET /api/crm/export, issue #1079). The requested ``limit`` is clamped to this ceiling so a single call cannot pull an unbounded aggregate. Default 200. A blank, non-numeric, or non-positive override falls back to 200.",
    value_type="int",
    default="200",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/site.md#crm_export_max_limit",
)
SOCIAL_YOUTUBE_URL = declare(
    key="SOCIAL_YOUTUBE_URL",
    group="site",
    label="Social Youtube URL",
    description="Public YouTube channel URL for the footer social row (issue #1356). Leave blank to hide the YouTube icon; there is no default handle.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/site.md#social_youtube_url",
)
SOCIAL_LINKEDIN_URL = declare(
    key="SOCIAL_LINKEDIN_URL",
    group="site",
    label="Social Linkedin URL",
    description="Public LinkedIn page URL for the footer social row (issue #1356). Leave blank to hide the LinkedIn icon; there is no default handle.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/site.md#social_linkedin_url",
)
SOCIAL_GITHUB_URL = declare(
    key="SOCIAL_GITHUB_URL",
    group="site",
    label="Social Github URL",
    description="Public GitHub organisation URL for the footer social row (issue #1356). Leave blank to hide the GitHub icon; there is no default handle.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/site.md#social_github_url",
)
SOCIAL_X_URL = declare(
    key="SOCIAL_X_URL",
    group="site",
    label="Social X URL",
    description="Public X (formerly Twitter) profile URL for the footer social row (issue #1356). Leave blank to hide the X icon; there is no default handle.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/site.md#social_x_url",
)

# --- Analytics (analytics) ---
GOOGLE_ANALYTICS_ID = declare(
    key="GOOGLE_ANALYTICS_ID",
    group="analytics",
    label="Google Analytics ID",
    description="Google Analytics 4 measurement ID (e.g. G-XXXXXXXXXX). When blank, no GA loader is emitted. Find it in GA: Admin > Data Streams > [your stream] > Measurement ID.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/analytics.md#google_analytics_id",
)
USER_ACTIVITY_RETENTION_DAYS = declare(
    key="USER_ACTIVITY_RETENTION_DAYS",
    group="analytics",
    label="User Activity Retention Days",
    description="How many days of per-user CRM activity timeline rows (analytics.UserActivity) to keep before the daily purge_old_user_activity job deletes them. Longer than the 90-day SES audit-log window because activity is a CRM signal staff use, but still bounded for storage / PII. Default 365. A non-integer or non-positive override falls back to 365. Issue #853.",
    value_type="int",
    default="365",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/analytics.md#user_activity_retention_days",
)

# --- Auth (auth) ---
UNVERIFIED_USER_TTL_DAYS = declare(
    key="UNVERIFIED_USER_TTL_DAYS",
    group="auth",
    label="Unverified User TTL Days",
    description="Number of days an email-signup account stays alive without verifying before the daily purge job hard-deletes it. Default 7. Lower this (e.g. 3) during spam waves; raise it for relaxed launches. Issue #452.",
    value_type="int",
    default=0,
    secret=False,
    docs_url="_docs/integrations/auth.md#unverified_user_ttl_days",
)

PURGE_UNVERIFIED_BATCH_SIZE = declare(
    key="PURGE_UNVERIFIED_BATCH_SIZE",
    group="auth",
    label="Purge Unverified Batch Size",
    description='Candidate ids inspected per primary-key window by the unverified-user purge. Default 500. A non-integer or non-positive override falls back to 500. Issue #1522.',
    value_type="int",
    default="500",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/auth.md#purge_unverified_batch_size",
)

PURGE_UNVERIFIED_MAX_BATCHES = declare(
    key="PURGE_UNVERIFIED_MAX_BATCHES",
    group="auth",
    label="Purge Unverified Max Batches",
    description='Maximum candidate batches inspected per purge pass and daily run. Default 50. A non-integer or non-positive override falls back to 50. Issue #1522.',
    value_type="int",
    default="50",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/auth.md#purge_unverified_max_batches",
)
AUTH_THROTTLE_LOGIN_IP_LIMIT = declare(
    key="AUTH_THROTTLE_LOGIN_IP_LIMIT",
    group="auth",
    label="Auth Throttle Login IP Limit",
    description="Max POST /api/login attempts from one IP per login window. Default 20. A non-integer or non-positive override falls back to 20. Issue #1516.",
    value_type="int",
    default="20",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/auth.md#auth_throttle_login_ip_limit",
)
AUTH_THROTTLE_LOGIN_EMAIL_LIMIT = declare(
    key="AUTH_THROTTLE_LOGIN_EMAIL_LIMIT",
    group="auth",
    label="Auth Throttle Login Email Limit",
    description="Max POST /api/login attempts for one email per login window. Default 10. A non-integer or non-positive override falls back to 10. Issue #1516.",
    value_type="int",
    default="10",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/auth.md#auth_throttle_login_email_limit",
)
AUTH_THROTTLE_LOGIN_WINDOW_SECONDS = declare(
    key="AUTH_THROTTLE_LOGIN_WINDOW_SECONDS",
    group="auth",
    label="Auth Throttle Login Window Seconds",
    description="Sliding window in seconds for login IP and email buckets. Default 900 (15 minutes). A non-integer or non-positive override falls back to 900. Issue #1516.",
    value_type="int",
    default="900",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/auth.md#auth_throttle_login_window_seconds",
)
AUTH_THROTTLE_MAIL_IP_LIMIT = declare(
    key="AUTH_THROTTLE_MAIL_IP_LIMIT",
    group="auth",
    label="Auth Throttle Mail IP Limit",
    description="Max register, password-reset request, and subscribe POSTs from one IP per mail window. Default 8. A non-integer or non-positive override falls back to 8. Issue #1516.",
    value_type="int",
    default="8",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/auth.md#auth_throttle_mail_ip_limit",
)
AUTH_THROTTLE_MAIL_EMAIL_LIMIT = declare(
    key="AUTH_THROTTLE_MAIL_EMAIL_LIMIT",
    group="auth",
    label="Auth Throttle Mail Email Limit",
    description="Max register, password-reset request, and subscribe POSTs for one email per mail window. Default 3. A non-integer or non-positive override falls back to 3. Issue #1516.",
    value_type="int",
    default="3",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/auth.md#auth_throttle_mail_email_limit",
)
AUTH_THROTTLE_MAIL_WINDOW_SECONDS = declare(
    key="AUTH_THROTTLE_MAIL_WINDOW_SECONDS",
    group="auth",
    label="Auth Throttle Mail Window Seconds",
    description="Sliding window in seconds for register, password-reset request, and subscribe buckets. Default 3600 (1 hour). A non-integer or non-positive override falls back to 3600. Issue #1516.",
    value_type="int",
    default="3600",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/auth.md#auth_throttle_mail_window_seconds",
)

# --- Banner Generator (banner_generator) ---
BANNER_GENERATOR_FUNCTION_URL = declare(
    key="BANNER_GENERATOR_FUNCTION_URL",
    group="banner_generator",
    label="Banner Generator Function URL",
    description="HTTPS Function URL of the banner-generator Lambda. Used to render OG banners for synced content. Without this auto-banner generation is silently skipped.",
    value_type="str",
    default="",
    secret=False,
    docs_url="_docs/integrations/banner_generator.md#banner_generator_function_url",
)
BANNER_GENERATOR_AUTH_TOKEN = declare(
    key="BANNER_GENERATOR_AUTH_TOKEN",
    group="banner_generator",
    label="Banner Generator Auth Token",
    description="Bearer token used in the Authorization header when calling the banner-generator Lambda. Issued out-of-band by the operator.",
    value_type="str",
    default="",
    secret=True,
    docs_url="_docs/integrations/banner_generator.md#banner_generator_auth_token",
)
BANNER_GENERATOR_TIMEOUT_SECONDS = declare(
    key="BANNER_GENERATOR_TIMEOUT_SECONDS",
    group="banner_generator",
    label="Banner Generator Timeout Seconds",
    description="HTTP timeout in seconds for the render call to the banner-generator Lambda. Should comfortably cover a container-Lambda cold start; warm renders finish in ~1.4s, so a high ceiling costs nothing on the happy path. Default 90. A non-integer or non-positive override falls back to 90.",
    value_type="int",
    default="90",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/banner_generator.md#banner_generator_timeout_seconds",
)
BANNER_UPLOAD_MAX_MB = declare(
    key="BANNER_UPLOAD_MAX_MB",
    group="banner_generator",
    label="Banner Upload Max MB",
    description="Maximum size (in MB) for an operator-uploaded custom banner/social image in Studio. Default 5. A non-integer or non-positive override falls back to 5.",
    value_type="int",
    default="5",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/banner_generator.md#banner_upload_max_mb",
)
BANNER_UPLOAD_ALLOWED_TYPES = declare(
    key="BANNER_UPLOAD_ALLOWED_TYPES",
    group="banner_generator",
    label="Banner Upload Allowed Types",
    description="Comma-separated list of MIME types accepted for custom banner uploads. Only JPEG, PNG, and WebP are supported by the storage key builder; unknown types are ignored.",
    value_type="str",
    default="image/jpeg,image/png,image/webp",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/banner_generator.md#banner_upload_allowed_types",
)
BANNER_UPLOAD_KEY_PREFIX = declare(
    key="BANNER_UPLOAD_KEY_PREFIX",
    group="banner_generator",
    label="Banner Upload Key Prefix",
    description="CDN/S3 key prefix under which operator-uploaded custom banners are stored (e.g. custom-banners/article/...). The safe-delete cleanup is scoped to this prefix.",
    value_type="str",
    default="custom-banners",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/banner_generator.md#banner_upload_key_prefix",
)

# --- LLM Provider (llm) ---
LLM_PROVIDER = declare(
    key="LLM_PROVIDER",
    group="llm",
    label="LLM Provider",
    description='Which backend the LLM service uses. Only "anthropic" is implemented today (also covers Anthropic-compatible gateways such as Z.ai via LLM_BASE_URL). "openai" and "bedrock" are reserved for future backends.',
    value_type="str",
    default="anthropic",
    secret=False,
    docs_url="_docs/integrations/llm.md#llm_provider",
)
LLM_API_KEY = declare(
    key="LLM_API_KEY",
    group="llm",
    label="LLM API Key",
    description='API key/credential for the selected provider. For "anthropic" this is an Anthropic (or compatible-gateway) key. Without it, LLM features are disabled.',
    value_type="str",
    default="",
    secret=True,
    docs_url="_docs/integrations/llm.md#llm_api_key",
)
LLM_BASE_URL = declare(
    key="LLM_BASE_URL",
    group="llm",
    label="LLM Base URL",
    description="Base URL of the provider API. Leave as default for Anthropic; override to point at an Anthropic-compatible gateway/proxy (e.g. a Z.ai-style endpoint).",
    value_type="str",
    default="https://api.anthropic.com",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/llm.md#llm_base_url",
)
LLM_MODEL = declare(
    key="LLM_MODEL",
    group="llm",
    label="LLM Model",
    description="Default model name used when a caller does not pass an explicit model. Override to pin a different Claude model or a gateway model name.",
    value_type="str",
    default="claude-sonnet-4-5",
    secret=False,
    docs_url="_docs/integrations/llm.md#llm_model",
)
LLM_JUDGE_MODEL = declare(
    key="LLM_JUDGE_MODEL",
    group="llm",
    label="LLM Judge Model",
    description="Model used by the live LLM-judge test set (tests/live_judge/, make test-judge). Leave empty to fall back to LLM_MODEL (judge == assistant model). Override to swap in a stronger/cheaper judge without changing the assistant model under test.",
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/llm.md#llm_judge_model",
)
LLM_MAX_RETRIES = declare(
    key="LLM_MAX_RETRIES",
    group="llm",
    label="LLM Max Retries",
    description="Maximum retry attempts for the Anthropic-compatible SDK client. Default 6; invalid input falls back to 6 and negative input disables retries by resolving to 0.",
    value_type="int",
    default="6",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/llm.md#llm_max_retries",
)
ONBOARDING_AI_ENABLED = declare(
    key="ONBOARDING_AI_ENABLED",
    group="llm",
    label="Onboarding AI Enabled",
    description="Set true to offer the conversational AI onboarding flow when the LLM is enabled. When off (or the LLM is disabled), /onboarding/ shows the form-first flow only. Defaults on; switchable without a redeploy.",
    value_type="bool",
    default="true",
    secret=False,
    docs_url="_docs/integrations/llm.md#onboarding_ai_enabled",
)
ONBOARDING_AI_STREAMING = declare(
    key="ONBOARDING_AI_STREAMING",
    group="llm",
    label="Onboarding AI Streaming",
    description="Set true to stream the AI onboarding assistant reply token-by-token over Server-Sent Events when the AI path is enabled. When off (or the AI path is disabled), the chat uses the non-streaming request/response transport and opens no SSE connection. Defaults on; switchable without a redeploy. The browser falls back to the non-streaming path automatically if a proxy buffers the stream.",
    value_type="bool",
    default="true",
    secret=False,
    docs_url="_docs/integrations/llm.md#onboarding_ai_streaming",
)
ONBOARDING_AI_DEADLINE_SECONDS = declare(
    key="ONBOARDING_AI_DEADLINE_SECONDS",
    group="llm",
    label="Onboarding AI Deadline Seconds",
    description="Deadline for each onboarding provider call. Values are clamped to 5-28 seconds so a stuck call releases the default 30-second sync web worker.",
    value_type="int",
    default="25",
    secret=False,
    docs_url="_docs/integrations/llm.md#onboarding-ai-latency-runbook",
)
ONBOARDING_AI_MAX_ATTEMPTS = declare(
    key="ONBOARDING_AI_MAX_ATTEMPTS",
    group="llm",
    label="Onboarding AI Max Attempts",
    description="Total provider calls for one logical onboarding turn, including stream-to-form recovery. Clamped to 1-3.",
    value_type="int",
    default="2",
    secret=False,
    docs_url="_docs/integrations/llm.md#onboarding-ai-latency-runbook",
)
NEXT_SPRINT_DRAFT_USE_PROFILE = declare(
    key="NEXT_SPRINT_DRAFT_USE_PROFILE",
    group="llm",
    label="Next Sprint Draft Use Profile",
    description='Set true to feed the member onboarding profile (stated background/goals, persona, CRM summary and next-steps) into the LLM next-sprint plan draft, so the generated draft is informed by the profile and not just plan state and recent #plan-sprints updates. When off, the draft is assembled without the profile block (pre-#913 behaviour). Affects both the Studio "Draft next sprint plan" button and POST /api/plans/<id>/draft-next-sprint. Defaults on; switchable without a redeploy.',
    value_type="bool",
    default="true",
    secret=False,
    docs_url="_docs/integrations/llm.md#next_sprint_draft_use_profile",
)

# --- Observability (observability) ---
LOGFIRE_ENABLED = declare(
    key="LOGFIRE_ENABLED",
    group="observability",
    label="Logfire Enabled",
    description="Explicit on switch for Pydantic Logfire. Default off everywhere; must be true (plus a token, plus not running tests) before Logfire initializes. Changes take effect on the next web and worker process start.",
    value_type="bool",
    default="false",
    secret=False,
    requires_restart=True,
    docs_url="_docs/integrations/observability.md#logfire_enabled",
)
LOGFIRE_TOKEN = declare(
    key="LOGFIRE_TOKEN",
    group="observability",
    label="Logfire Token",
    description="Logfire write token. Get it from the Logfire project settings. When blank, Logfire is fully off. Masked in Studio; changes take effect on the next web and worker process start.",
    value_type="str",
    default="",
    secret=True,
    requires_restart=True,
    docs_url="_docs/integrations/observability.md#logfire_token",
)
LOGFIRE_ENVIRONMENT = declare(
    key="LOGFIRE_ENVIRONMENT",
    group="observability",
    label="Logfire Environment",
    description='Logfire environment tag passed to logfire.configure(environment=...), so prod traces are separable from any opt-in dev run. Defaults to "production"; changes take effect on the next web and worker process start.',
    value_type="str",
    default="production",
    secret=False,
    optional=True,
    requires_restart=True,
    docs_url="_docs/integrations/observability.md#logfire_environment",
)

# --- Maven (maven) ---
MAVEN_ENROLLMENT_ENABLED = declare(
    key="MAVEN_ENROLLMENT_ENABLED",
    group="maven",
    label="Maven Enrollment Enabled",
    description='Master switch for the Maven cohort auto-onboarding flow (issue #960). When off, the /api/webhooks/maven endpoint returns {"status":"disabled"} and creates no accounts, overrides, Slack invites, or emails. Default off.',
    value_type="bool",
    default="false",
    secret=False,
    docs_url="_docs/integrations/maven.md#maven_enrollment_enabled",
)
MAVEN_WEBHOOK_SHARED_SECRET = declare(
    key="MAVEN_WEBHOOK_SHARED_SECRET",
    group="maven",
    label="Maven Webhook Shared Secret",
    description="Shared secret that authenticates inbound Maven (or Zapier) webhook calls. Generate a long random token, paste it here, and put it in the webhook URL (?secret=...) or an X-Maven-Secret header. When blank the endpoint rejects all requests with 403, even when the feature is enabled.",
    value_type="str",
    default="",
    secret=True,
    docs_url="_docs/integrations/maven.md#maven_webhook_shared_secret",
)
MAVEN_OVERRIDE_TIER_SLUG = declare(
    key="MAVEN_OVERRIDE_TIER_SLUG",
    group="maven",
    label="Maven Override Tier Slug",
    description='Tier slug granted as a long-lived override to Maven enrollees. Validated against Tier; free / level-0 slugs are rejected and fall back to "main" (logged). Defaults to "main".',
    value_type="str",
    default="main",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/maven.md#maven_override_tier_slug",
)
MAVEN_OVERRIDE_DURATION_DAYS = declare(
    key="MAVEN_OVERRIDE_DURATION_DAYS",
    group="maven",
    label="Maven Override Duration Days",
    description="Lifetime in days of the override granted to Maven enrollees (default 1825, five years). An existing longer entitlement is never shortened.",
    value_type="int",
    default="1825",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/maven.md#maven_override_duration_days",
)
MAVEN_COURSE_SLACK_CHANNEL = declare(
    key="MAVEN_COURSE_SLACK_CHANNEL",
    group="maven",
    label="Maven Course Slack Channel",
    description='Slack channel name shown in the maven_welcome email, e.g. "#ai-engineering-buildcamp". Names where the cohort talks so a new enrollee knows exactly where to go. Optional: when blank the welcome copy reads cleanly without it and no channel is named.',
    value_type="str",
    default="",
    secret=False,
    optional=True,
    docs_url="_docs/integrations/maven.md#maven_course_slack_channel",
)

# --- Event triggers (triggers) ---
TRIGGERS_ENABLED = declare(
    key="TRIGGERS_ENABLED",
    group="triggers",
    label="Triggers Enabled",
    description="Master switch for the outbound event-hooks subsystem (issue #1070). When off, emit_event records nothing and dispatches no webhooks, and claim widgets show a paused state. Turn on once at least one TriggerSubscription points at a live handler. Default off.",
    value_type="bool",
    default="false",
    secret=False,
    docs_url="_docs/integrations/triggers.md#triggers_enabled",
)
