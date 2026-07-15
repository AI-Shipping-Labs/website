"""Registry of all known integration settings with metadata.

Each group defines the integration service and its configurable keys.
The 'multiline' flag indicates keys that need a textarea (e.g. PEM keys).

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

NOTE: ``_docs/configuration.md`` references the count and names of these
groups in the Studio sign-in section ("confirm 16 integration groups are
listed (...)"). When adding, removing, or renaming a group here, update
that line of the doc in the same PR.
"""

INTEGRATION_GROUPS = [
    {
        'name': 'stripe',
        'label': 'Stripe',
        'keys': [
            {
                'key': 'STRIPE_SECRET_KEY',
                'is_secret': True,
                'description': 'Server-side Stripe API key. Get from Stripe Dashboard > Developers > API keys. Without this checkout fails.',
                'docs_url': '_docs/integrations/stripe.md#stripe_secret_key',
            },
            {
                'key': 'STRIPE_WEBHOOK_SECRET',
                'is_secret': True,
                'description': 'Verifies that webhook callbacks really came from Stripe. Get from Stripe Dashboard > Webhooks > [your endpoint].',
                'docs_url': '_docs/integrations/stripe.md#stripe_webhook_secret',
            },
            {
                'key': 'STRIPE_CUSTOMER_PORTAL_URL',
                'is_secret': False,
                'description': 'Stripe-hosted page where members manage their subscription. Get from Stripe Dashboard > Settings > Billing > Customer portal.',
                'docs_url': '_docs/integrations/stripe.md#stripe_customer_portal_url',
            },
            {
                'key': 'STRIPE_DASHBOARD_ACCOUNT_ID',
                'is_secret': False,
                'description': (
                    'Stripe account ID used to build dashboard deep-links '
                    '(e.g. "acct_1T1mfGB7mZrgL7H5"). Find it in the Stripe URL when '
                    'you are signed in to your account. Optional — when blank, the '
                    'Stripe icon next to a user is shown but not clickable.'
                ),
                'docs_url': '_docs/integrations/stripe.md#stripe_dashboard_account_id',
            },
            {
                'key': 'AUTHENTICATED_CHECKOUT_BINDING_ENABLED',
                'is_secret': False,
                'is_boolean': True,
                'optional': True,
                'default': 'true',
                'description': 'Kill switch for issuing authenticated opaque Stripe checkout bindings; enabled by default.',
                'docs_url': '_docs/integrations/stripe.md#authenticated_checkout_binding_enabled',
            },
            {
                'key': 'CHECKOUT_BINDING_TTL_MINUTES',
                'is_secret': False,
                'optional': True,
                'default': '120',
                'description': 'Lifetime in minutes for authenticated checkout bindings (clamped to 5–1440 minutes).',
                'docs_url': '_docs/integrations/stripe.md#checkout_binding_ttl_minutes',
            },
            {
                'key': 'LEGACY_NUMERIC_CHECKOUT_REFERENCE_ENABLED',
                'is_secret': False,
                'is_boolean': True,
                'optional': True,
                'default': 'true',
                'description': 'Temporary compatibility switch for verified same-account numeric checkout references; the cutoff still applies.',
                'docs_url': '_docs/integrations/stripe.md#legacy_numeric_checkout_reference_enabled',
            },
            {
                'key': 'LEGACY_NUMERIC_CHECKOUT_REFERENCE_CUTOFF',
                'is_secret': False,
                'optional': True,
                'default': '2026-08-01T00:00:00Z',
                'description': 'Hard UTC cutoff after which numeric checkout references are quarantined even if the compatibility switch remains enabled.',
                'docs_url': '_docs/integrations/stripe.md#legacy_numeric_checkout_reference_cutoff',
            },
        ],
    },
    {
        'name': 'zoom',
        'label': 'Zoom',
        'keys': [
            {'key': 'ZOOM_CLIENT_ID', 'is_secret': True, 'description': 'Zoom Server-to-Server OAuth app client ID. Without this we cannot create or fetch meetings.', 'docs_url': '_docs/integrations/zoom.md#zoom_client_id'},
            {'key': 'ZOOM_CLIENT_SECRET', 'is_secret': True, 'description': 'Zoom OAuth client secret. Get from your Zoom app under Marketplace > Build App > S2S OAuth.', 'docs_url': '_docs/integrations/zoom.md#zoom_client_secret'},
            {'key': 'ZOOM_ACCOUNT_ID', 'is_secret': True, 'description': 'Zoom account UUID the OAuth app belongs to. Found in the Zoom Marketplace app settings.', 'docs_url': '_docs/integrations/zoom.md#zoom_account_id'},
            {'key': 'ZOOM_WEBHOOK_SECRET_TOKEN', 'is_secret': True, 'description': 'Verifies Zoom webhook callbacks (event start, recording ready). Set in the Zoom app event subscription.', 'docs_url': '_docs/integrations/zoom.md#zoom_webhook_secret_token'},
            {
                'key': 'ZOOM_WAITING_ROOM',
                'is_secret': False,
                'is_boolean': True,
                'optional': True,
                'default': 'false',
                'description': (
                    'Set true to place attendees in a Zoom waiting room until '
                    'the host admits them (requires the host to admit each '
                    'attendee). Off by default — keeping join-before-host off '
                    'is enough to make cloud recording start only when the '
                    'host starts the meeting, with no manual admitting.'
                ),
                'docs_url': '_docs/integrations/zoom.md#zoom_waiting_room',
            },
            {
                'key': 'ZOOM_JOIN_BEFORE_HOST',
                'is_secret': False,
                'is_boolean': True,
                'optional': True,
                'default': 'false',
                'description': (
                    'Set true to let attendees join a Zoom meeting before the '
                    'host arrives (recommended OFF). Off by default — early '
                    'joiners then see Zoom\'s "waiting for the host to start" '
                    'hold and cloud recording does not begin until the host '
                    'joins, so it never captures pre-host waiting time.'
                ),
                'docs_url': '_docs/integrations/zoom.md#zoom_join_before_host',
            },
            {
                'key': 'ZOOM_AUTO_RECORDING',
                'is_secret': False,
                'optional': True,
                'default': 'cloud',
                'description': (
                    'How event-created Zoom meetings auto-record: cloud '
                    '(default, records to Zoom cloud so the recording-ready '
                    'webhook can fetch it), local, or none. Requires cloud '
                    'recording to be enabled and not locked at the Zoom '
                    'account level for the host account, otherwise the '
                    'per-meeting request is ignored.'
                ),
                'docs_url': '_docs/integrations/zoom.md#zoom_auto_recording',
            },
        ],
    },
    {
        'name': 'ses',
        'label': 'Email (SES)',
        'keys': [
            {'key': 'AWS_ACCESS_KEY_ID', 'is_secret': True, 'description': 'AWS access key for an IAM user with SES send + suppression-list permissions.', 'docs_url': '_docs/integrations/ses.md#aws_access_key_id'},
            {'key': 'AWS_SECRET_ACCESS_KEY', 'is_secret': True, 'description': 'AWS secret key paired with the access key above. Without these no email is sent.', 'docs_url': '_docs/integrations/ses.md#aws_secret_access_key'},
            {'key': 'AWS_SES_REGION', 'is_secret': False, 'description': 'AWS region for SES (e.g. eu-west-1). Must match the verified domain region.', 'docs_url': '_docs/integrations/ses.md#aws_ses_region'},
            {'key': 'SES_TRANSACTIONAL_FROM_EMAIL', 'is_secret': False, 'description': 'Sender address for required account and service email. Must be verified in SES.', 'docs_url': '_docs/integrations/ses.md#ses_transactional_from_email'},
            {'key': 'SES_PROMOTIONAL_FROM_EMAIL', 'is_secret': False, 'description': 'Sender address for campaigns, newsletters, and marketing email. Must be verified in SES.', 'docs_url': '_docs/integrations/ses.md#ses_promotional_from_email'},
            {'key': 'SES_WELCOME_FROM_EMAIL', 'is_secret': False, 'description': 'Sender address for welcome emails (welcome, paid-signup, imported-user welcomes). Must be verified in SES.', 'docs_url': '_docs/integrations/ses.md#ses_welcome_from_email'},
            {
                'key': 'SES_WELCOME_REPLY_TO_EMAIL',
                'is_secret': False,
                'optional': True,
                'default': 'welcome@aishippinglabs.com',
                'description': (
                    'Reply-To address set on welcome emails so a member who '
                    'replies reaches a monitored, team-forwarded inbox '
                    'instead of the send-only welcome/noreply mailbox. '
                    'Defaults to welcome@aishippinglabs.com (forwarded to the '
                    'founders by the inbound email-forwarder Lambda). Leave '
                    'blank to send welcome emails with no Reply-To header.'
                ),
                'docs_url': '_docs/integrations/ses.md#ses_welcome_reply_to_email',
            },
            {
                'key': 'SES_CONFIGURATION_SET_NAME',
                'is_secret': False,
                'description': (
                    'SES configuration set name that publishes delivery, '
                    'open, bounce, and click events to SNS. Required in '
                    'production: set to "aishippinglabs" (matches the '
                    'configuration set in ai-shipping-labs-infra/email.tf). '
                    'When blank, SES publishes no events to SNS regardless '
                    'of the HTTPS subscription wiring, so the bounce / '
                    'complaint webhook never fires. Safe to leave blank '
                    'only in local dev.'
                ),
                'docs_url': '_docs/integrations/ses.md#ses_configuration_set_name',
            },
            {'key': 'SES_WEBHOOK_VALIDATION_ENABLED', 'is_secret': False, 'is_boolean': True, 'description': 'Set true to verify SNS bounce/complaint signatures (recommended in production).', 'docs_url': '_docs/integrations/ses.md#ses_webhook_validation_enabled'},
            {
                'key': 'SES_WEBHOOK_SHARED_SECRET',
                'is_secret': True,
                'optional': True,
                'description': (
                    'Optional shared secret required in the '
                    'X-SES-Webhook-Secret header on the SES webhook. Set in '
                    'prod and inject from the infra-side Lambda forwarder. '
                    'Leave blank locally to allow runserver replay.'
                ),
                'docs_url': '_docs/integrations/ses.md#ses_webhook_shared_secret',
            },
            {
                'key': 'CAMPAIGN_BATCH_INTERVAL_SECONDS',
                'is_secret': False,
                'optional': True,
                'description': (
                    'Seconds to stagger campaign send batches apart so the '
                    'fan-out does not burst past the SES send-rate limit '
                    '(issue #922). Batch i is scheduled at now + i * this '
                    'interval; the first batch fires immediately. Default 60. '
                    'Set to 0 to send all batches at once (no stagger).'
                ),
                'docs_url': '_docs/integrations/ses.md#campaign_batch_interval_seconds',
            },
            {
                'key': 'CAMPAIGN_TEST_RECIPIENTS',
                'is_secret': False,
                'optional': True,
                'description': (
                    'Comma/space/semicolon/newline-separated list of common '
                    'test-send addresses surfaced as click-to-fill chips '
                    'beneath the Test Recipients field on the campaign detail '
                    'page (issue #921). Lets operators one-click-fill the '
                    'mailboxes they repeatedly test to (a teammate, a QA seed '
                    'inbox) instead of retyping. Invalid entries are silently '
                    'dropped. Leave blank to show only the operator\'s own '
                    'email and recently-sent addresses.'
                ),
                'docs_url': '_docs/integrations/ses.md#campaign_test_recipients',
            },
            {
                'key': 'RECORDING_AVAILABLE_SUBJECT_TEMPLATE',
                'is_secret': False,
                'optional': True,
                'description': (
                    'Default subject pre-filled into the "recording available" '
                    'campaign draft an operator reaches from the host '
                    'recording-ready email or the Studio event page (issue '
                    '#1076). ``{event_title}`` is substituted with the event '
                    'title. Pre-fill only — the operator reviews and edits the '
                    'draft before sending, so a blank/odd setting can never '
                    'auto-broadcast.'
                ),
                'docs_url': '_docs/integrations/ses.md#recording_available_subject_template',
            },
            {
                'key': 'RECORDING_AVAILABLE_BODY_TEMPLATE',
                'is_secret': False,
                'optional': True,
                'description': (
                    'Default markdown body pre-filled into the "recording '
                    'available" campaign draft (issue #1076). Placeholders: '
                    '``{event_title}``, ``{recording_url}``, and '
                    '``{workshop_writeup}`` (the linked workshop write-up, or a '
                    'short generic line when the event has no linked workshop). '
                    'Pre-fill only — never auto-sent.'
                ),
                'docs_url': '_docs/integrations/ses.md#recording_available_body_template',
            },
        ],
    },
    {
        'name': 's3_recordings',
        'label': 'S3 Recordings',
        'keys': [
            {'key': 'AWS_S3_RECORDINGS_BUCKET', 'is_secret': False, 'description': 'S3 bucket where event recordings are uploaded after processing.', 'docs_url': '_docs/integrations/s3_recordings.md#aws_s3_recordings_bucket'},
            {'key': 'AWS_S3_RECORDINGS_REGION', 'is_secret': False, 'description': 'AWS region of the recordings bucket (e.g. eu-west-1).', 'docs_url': '_docs/integrations/s3_recordings.md#aws_s3_recordings_region'},
            {
                'key': 'RECORDING_PRESIGNED_URL_TTL_SECONDS',
                'is_secret': False,
                'optional': True,
                'default': '900',
                'description': (
                    'Lifetime (in seconds) of the short-lived presigned S3 '
                    'GetObject URL the access-controlled recording serving '
                    'endpoint redirects to (issue #1134). Default 900 (15 '
                    'minutes). The presigned URL is never rendered into HTML — '
                    'the in-page video player points at the authenticated '
                    'serving endpoint, which re-checks access and mints a '
                    'fresh presigned URL on every request. Keep this long '
                    'enough that a member can watch/seek without the URL '
                    'expiring mid-playback, but short enough that a leaked URL '
                    'is quickly useless.'
                ),
                'docs_url': '_docs/integrations/s3_recordings.md#recording_presigned_url_ttl_seconds',
            },
            {
                'key': 'RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD',
                'is_secret': False,
                'is_boolean': True,
                'default': 'true',
                'description': (
                    'When on, a successful Zoom -> S3 recording upload '
                    'auto-publishes the event so entitled members can watch '
                    'the recording right away, and the host notification says '
                    'the recording is available to watch with a link to the '
                    'workshop video page (issue #1134, Phase B). On by default '
                    'per the product decision that the recording should be '
                    'watchable immediately. Turn it off to keep the '
                    'review-first flow: the event stays unpublished after '
                    'upload and the host email keeps the "ready for '
                    'review/publishing" framing with a Studio link.'
                ),
                'docs_url': '_docs/integrations/s3_recordings.md#recording_auto_publish_on_s3_upload',
            },
        ],
    },
    {
        'name': 's3_content',
        'label': 'S3 Content Images',
        'keys': [
            {'key': 'AWS_S3_CONTENT_BUCKET', 'is_secret': False, 'description': 'S3 bucket for content images extracted from synced markdown. Public-read.', 'docs_url': '_docs/integrations/s3_content.md#aws_s3_content_bucket'},
            {'key': 'AWS_S3_CONTENT_REGION', 'is_secret': False, 'description': 'AWS region of the content-images bucket.', 'docs_url': '_docs/integrations/s3_content.md#aws_s3_content_region'},
            {'key': 'CONTENT_CDN_BASE', 'is_secret': False, 'description': 'Public CDN base URL fronting the content bucket (e.g. https://cdn.aishippinglabs.com). Without this images break on the live site.', 'docs_url': '_docs/integrations/s3_content.md#content_cdn_base'},
            {
                'key': 'S3_ENABLED',
                'is_secret': False,
                'is_boolean': True,
                'default': 'true',
                'description': (
                    'Master switch for content-image uploads to S3 during '
                    'content sync. On by default; set explicitly to false to '
                    'disable content-image uploads. When off, image URLs are '
                    'still rewritten to CDN paths but no objects are uploaded, '
                    'so images 403 in production. Leave on in production.'
                ),
                'docs_url': '_docs/integrations/s3_content.md#s3_enabled',
            },
        ],
    },
    {
        'name': 'calendly',
        'label': 'Calendly',
        'keys': [
            {
                'key': 'CALENDLY_ACCESS_TOKEN',
                'is_secret': True,
                'optional': True,
                'description': (
                    'Calendly host access token (personal access token or '
                    'an OAuth access token) used to read scheduled events '
                    'and create the webhook subscription. Get a personal '
                    'token from Calendly > Integrations > API & Webhooks. '
                    'Without it the platform cannot register the booked-call '
                    'webhook or fetch event details.'
                ),
                'docs_url': '_docs/integrations/calendly.md#calendly_access_token',
            },
            {
                'key': 'CALENDLY_WEBHOOK_SIGNING_KEY',
                'is_secret': True,
                'optional': True,
                'description': (
                    'Signing key Calendly returns when the webhook '
                    'subscription is created. Verifies that '
                    'invitee.created / invitee.canceled callbacks really '
                    'came from Calendly. When blank, webhook calls are '
                    'rejected in production but allowed locally for replay.'
                ),
                'docs_url': '_docs/integrations/calendly.md#calendly_webhook_signing_key',
            },
            {
                'key': 'CALENDLY_OAUTH_CLIENT_ID',
                'is_secret': True,
                'optional': True,
                'description': (
                    'Calendly OAuth app client ID. Used for the optional '
                    'authorize-Calendly flow that mints a host access '
                    'token without pasting a personal token. Get it from '
                    'Calendly > Integrations > OAuth applications.'
                ),
                'docs_url': '_docs/integrations/calendly.md#calendly_oauth_client_id',
            },
            {
                'key': 'CALENDLY_OAUTH_CLIENT_SECRET',
                'is_secret': True,
                'optional': True,
                'description': (
                    'Calendly OAuth app client secret paired with the '
                    'client ID above. Required only for the authorize flow.'
                ),
                'docs_url': '_docs/integrations/calendly.md#calendly_oauth_client_secret',
            },
            {
                'key': 'CALENDLY_WEBHOOK_VALIDATION_ENABLED',
                'is_secret': False,
                'is_boolean': True,
                'optional': True,
                'description': (
                    'Set true to require a valid Calendly-Webhook-Signature '
                    'header on the booked-call webhook (recommended in '
                    'production). When false, signatures are not enforced so '
                    'local replay works without the signing key.'
                ),
                'docs_url': '_docs/integrations/calendly.md#calendly_webhook_validation_enabled',
            },
        ],
    },
    {
        'name': 'github',
        'label': 'GitHub App',
        'keys': [
            {'key': 'GITHUB_APP_ID', 'is_secret': False, 'description': 'Numeric ID of the GitHub App used to read content repos. Found at github.com/settings/apps/<your-app>.', 'docs_url': '_docs/integrations/github.md#github_app_id'},
            {'key': 'GITHUB_APP_INSTALLATION_ID', 'is_secret': False, 'description': 'Installation ID of the GitHub App on the content org. Found at github.com/organizations/<org>/settings/installations.', 'docs_url': '_docs/integrations/github.md#github_app_installation_id'},
            {'key': 'GITHUB_APP_PRIVATE_KEY_SECRET_ID', 'is_secret': False, 'description': 'AWS Secrets Manager secret name, path, or ARN containing the GitHub App PEM private key. Leave the PEM field empty when this is set.', 'default': 'ai-shipping-labs/github-app-private-key', 'docs_url': '_docs/integrations/github.md#github_app_private_key_secret_id'},
            {'key': 'GITHUB_APP_PRIVATE_KEY_SECRET_REGION', 'is_secret': False, 'description': 'AWS region for the GitHub App private-key secret. Defaults to eu-west-1 when empty.', 'default': 'eu-west-1', 'optional': True, 'docs_url': '_docs/integrations/github.md#github_app_private_key_secret_region'},
            {'key': 'GITHUB_APP_PRIVATE_KEY', 'is_secret': True, 'description': 'Optional direct PEM private key issued by GitHub. Prefer the AWS Secrets Manager secret path above for production.', 'multiline': True, 'optional': True, 'docs_url': '_docs/integrations/github.md#github_app_private_key'},
        ],
    },
    {
        'name': 'slack',
        'label': 'Slack',
        'keys': [
            {'key': 'SLACK_ENABLED', 'is_secret': False, 'is_boolean': True, 'description': 'Set true to enable Slack bot posting and event listening. Off by default to keep dev/test silent.', 'docs_url': '_docs/integrations/slack.md#slack_enabled'},
            {'key': 'SLACK_ENVIRONMENT', 'is_secret': False, 'description': 'Slack routing mode: production, development, or test. Non-production modes ignore production channel IDs.', 'docs_url': '_docs/integrations/slack.md#slack_environment'},
            {'key': 'SLACK_BOT_TOKEN', 'is_secret': True, 'description': 'Slack bot user OAuth token (xoxb-...). Used to post announcements and read community channel events.', 'docs_url': '_docs/integrations/slack.md#slack_bot_token'},
            {'key': 'SLACK_COMMUNITY_CHANNEL_IDS', 'is_secret': False, 'description': 'Comma-separated channel IDs the bot watches for community signals (mentions, reactions).', 'docs_url': '_docs/integrations/slack.md#slack_community_channel_ids'},
            {'key': 'SLACK_ANNOUNCEMENTS_CHANNEL_ID', 'is_secret': False, 'description': 'Channel ID where the bot posts new content and event announcements.', 'docs_url': '_docs/integrations/slack.md#slack_announcements_channel_id'},
            {
                'key': 'STAFF_SIGNUP_NOTIFY_CHANNEL_ID',
                'is_secret': False,
                'description': (
                    'Slack channel ID where the bot posts an internal heads-up '
                    'every time a paid signup completes (Basic and above). '
                    'Leave blank to skip the Slack post; the staff email side '
                    'still runs.'
                ),
                'optional': True,
                'docs_url': '_docs/integrations/slack.md#staff_signup_notify_channel_id',
            },
            {
                'key': 'STAFF_SLACK_JOIN_NOTIFY_ENABLED',
                'is_secret': False,
                'is_boolean': True,
                'optional': True,
                'description': (
                    'Enables the staff heads-up (email + optional Slack post) '
                    'sent when the periodic membership refresh observes a known '
                    'user genuinely join the Slack workspace. Recommended ON. '
                    'Acts as a no-redeploy kill switch — turn off to suppress '
                    'all join notifications. Reuses STAFF_SIGNUP_NOTIFY_EMAIL '
                    'and STAFF_SIGNUP_NOTIFY_CHANNEL_ID for delivery.'
                ),
                'docs_url': '_docs/integrations/slack.md#staff_slack_join_notify_enabled',
            },
            {'key': 'SLACK_DEV_COMMUNITY_CHANNEL_IDS', 'is_secret': False, 'description': 'Development-only community channel IDs. Used only when SLACK_ENVIRONMENT=development.', 'docs_url': '_docs/integrations/slack.md#slack_dev_community_channel_ids'},
            {'key': 'SLACK_DEV_ANNOUNCEMENTS_CHANNEL_ID', 'is_secret': False, 'description': 'Development-only announcement channel ID. Used only when SLACK_ENVIRONMENT=development.', 'docs_url': '_docs/integrations/slack.md#slack_dev_announcements_channel_id'},
            {'key': 'SLACK_TEST_COMMUNITY_CHANNEL_IDS', 'is_secret': False, 'description': 'Test-only community channel IDs. Used only when SLACK_ENVIRONMENT=test.', 'docs_url': '_docs/integrations/slack.md#slack_test_community_channel_ids'},
            {'key': 'SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID', 'is_secret': False, 'description': 'Test-only announcement channel ID, e.g. #integration-tests. Used only when SLACK_ENVIRONMENT=test.', 'docs_url': '_docs/integrations/slack.md#slack_test_announcements_channel_id'},
            {'key': 'SLACK_PLAN_SPRINTS_CHANNEL_ID', 'is_secret': False, 'optional': True, 'description': 'Channel ID of #plan-sprints. The daily ingest job (issue #889) reads member sprint updates from here. Requires channels:history/groups:history and the bot to be a member. Leave blank to disable ingestion.', 'docs_url': '_docs/integrations/slack.md#slack_plan_sprints_channel_id'},
            {'key': 'SLACK_PLAN_SPRINTS_USER_TOKEN', 'is_secret': True, 'optional': True, 'description': 'User OAuth token (xoxp-...) used only for conversations.replies on public/private #plan-sprints threads. Required for full-thread ingestion; the bot token remains in use for history and all other Slack calls.', 'docs_url': '_docs/integrations/slack.md#slack_plan_sprints_user_token'},
            {'key': 'SLACK_DEV_PLAN_SPRINTS_CHANNEL_ID', 'is_secret': False, 'optional': True, 'description': 'Development-only #plan-sprints channel ID. Used only when SLACK_ENVIRONMENT=development.', 'docs_url': '_docs/integrations/slack.md#slack_dev_plan_sprints_channel_id'},
            {'key': 'SLACK_TEST_PLAN_SPRINTS_CHANNEL_ID', 'is_secret': False, 'optional': True, 'description': 'Test-only #plan-sprints channel ID. Used only when SLACK_ENVIRONMENT=test.', 'docs_url': '_docs/integrations/slack.md#slack_test_plan_sprints_channel_id'},
            {'key': 'SLACK_TEAM_REQUESTS_CHANNEL_ID', 'is_secret': False, 'optional': True, 'description': 'Production channel ID of the team-requests channel where staff notifications post: plan-request pings ("Ask the team to plan with me", issue #585) and onboarding-submitted heads-ups (issue #882). Leave blank to skip the Slack post; email + in-app notifications still run. Used only when SLACK_ENVIRONMENT=production.', 'docs_url': '_docs/integrations/slack.md#slack_team_requests_channel_id'},
            {'key': 'SLACK_DEV_TEAM_REQUESTS_CHANNEL_ID', 'is_secret': False, 'optional': True, 'description': 'Development-only team-requests channel ID. Used only when SLACK_ENVIRONMENT=development.', 'docs_url': '_docs/integrations/slack.md#slack_dev_team_requests_channel_id'},
            {'key': 'SLACK_TEST_TEAM_REQUESTS_CHANNEL_ID', 'is_secret': False, 'optional': True, 'description': 'Test-only team-requests channel ID. Used only when SLACK_ENVIRONMENT=test.', 'docs_url': '_docs/integrations/slack.md#slack_test_team_requests_channel_id'},
            {'key': 'PLAN_SPRINTS_FIRST_RUN_LOOKBACK_DAYS', 'is_secret': False, 'optional': True, 'description': 'How many days the #plan-sprints ingest reads back on its very first run, before the forward watermark takes over. Used only when no prior successful run exists and no explicit since/oldest_ts is given (e.g. the retroactive backfill command/API of issue #904). Defaults to 7.', 'docs_url': '_docs/integrations/slack.md#plan_sprints_first_run_lookback_days'},
            {'key': 'PLAN_SPRINTS_THREAD_REFRESH_DAYS', 'is_secret': False, 'optional': True, 'description': 'How many recent days of unmatched/completed #plan-sprints threads the daily job re-checks for late replies; active-sprint threads are always checked. Defaults to 45.', 'docs_url': '_docs/integrations/slack.md#plan_sprints_thread_refresh_days'},
            {'key': 'PLAN_SPRINTS_INGEST_LEASE_MINUTES', 'is_secret': False, 'optional': True, 'description': 'Maximum age of a running #plan-sprints ingest lease before it is terminalized as abandoned. Defaults to 60 minutes.', 'docs_url': '_docs/integrations/slack.md#plan_sprints_ingest_lease_minutes'},
            {'key': 'PLAN_SPRINTS_RAW_TEXT_RETENTION_DAYS', 'is_secret': False, 'optional': True, 'description': 'Days to retain raw #plan-sprints Slack message text locally before the daily retention task redacts it. Defaults to 365.', 'docs_url': '_docs/integrations/slack.md#plan_sprints_raw_text_retention_days'},
            {'key': 'SLACK_INVITE_URL', 'is_secret': False, 'description': 'Public Slack workspace invite URL shown to Main+ members on the dashboard.', 'docs_url': '_docs/integrations/slack.md#slack_invite_url'},
            {
                'key': 'SLACK_TEAM_ID',
                'is_secret': False,
                'description': (
                    'Workspace team ID (e.g. "T01ABC123"). Used to build deep '
                    'links from Studio to a member\'s Slack profile. Find it in '
                    'Slack: workspace menu > Settings & administration > '
                    'Workspace settings, or in any Slack URL after "/team/". '
                    'Optional — when blank, the Slack icon next to a user is '
                    'shown but not clickable.'
                ),
                'docs_url': '_docs/integrations/slack.md#slack_team_id',
            },
        ],
    },
    {
        'name': 'site',
        'label': 'Site',
        'keys': [
            {'key': 'SITE_BASE_URL', 'is_secret': False, 'description': 'Canonical absolute URL — used for generated links, OAuth callbacks, etc.', 'docs_url': '_docs/integrations/site.md#site_base_url'},
            {'key': 'SITE_BASE_URL_ALIASES', 'is_secret': False, 'multiline': True, 'description': 'Additional hosts that should not trigger the host-mismatch banner. Comma- or whitespace-separated (newlines work too).', 'docs_url': '_docs/integrations/site.md#site_base_url_aliases'},
            {'key': 'EVENT_DISPLAY_TIMEZONE', 'is_secret': False, 'description': 'Default IANA timezone for public event times when the browser cannot provide one.', 'docs_url': '_docs/integrations/site.md#event_display_timezone'},
            {
                'key': 'PAYMENT_NOTIFICATION_EMAIL',
                'is_secret': False,
                'description': (
                    'Operator email address that receives an internal notification '
                    'whenever a Stripe checkout completes (new paid signup, tier '
                    'upgrade, or course purchase). Leave blank to disable — there '
                    'is no hard-coded default, so a blank setting means nobody is '
                    'notified.'
                ),
                'optional': True,
                'docs_url': '_docs/integrations/site.md#payment_notification_email',
            },
            {
                'key': 'STAFF_SIGNUP_NOTIFY_EMAIL',
                'is_secret': False,
                'description': (
                    'Single staff mailbox used for paid signups: hidden BCC '
                    'on the member-facing paid welcome, the structured '
                    'internal heads-up email, and (issue #1133) the hidden '
                    'BCC on the one-week onboarding reminder. Leave blank to '
                    'skip these staff copies; the member welcome and reminder '
                    'still send and Slack still runs when configured. Replies '
                    'still route only via SES_WELCOME_REPLY_TO_EMAIL.'
                ),
                'optional': True,
                'docs_url': '_docs/integrations/site.md#staff_signup_notify_email',
            },
            {
                'key': 'ONBOARDING_REMINDER_ENABLED',
                'is_secret': False,
                'is_boolean': True,
                'default': 'true',
                'description': (
                    'Master switch for the one-week onboarding reminder sweep '
                    '(issue #1133). When on, a daily job emails paid members '
                    'who received their onboarding-link welcome but have not '
                    'completed onboarding after ONBOARDING_REMINDER_DELAY_DAYS. '
                    'When off, the sweep is a no-op (no emails, no logs). '
                    'Defaults on; switchable without a redeploy.'
                ),
                'docs_url': '_docs/integrations/site.md#onboarding_reminder_enabled',
            },
            {
                'key': 'ONBOARDING_REMINDER_DELAY_DAYS',
                'is_secret': False,
                'optional': True,
                'default': '7',
                'description': (
                    'Days after the onboarding-link welcome email before the '
                    'reminder is due (issue #1133). A member whose earliest '
                    'welcome is older than this and who has not onboarded is '
                    'reminded once. Default 7. A blank, non-numeric, or '
                    'non-positive override falls back to 7.'
                ),
                'docs_url': '_docs/integrations/site.md#onboarding_reminder_delay_days',
            },
            {
                'key': 'SPRINT_BADGE_WINDOW_DAYS',
                'is_secret': False,
                'optional': True,
                'default': '7',
                'description': (
                    'Window in days around a sprint start / end that flips '
                    'the date-derived sprint badge to "Starting soon" (within '
                    'this many days before start) and "Ending soon" (within '
                    'this many days of end). A larger window surfaces the '
                    'soon-states earlier. Default 7. A blank, non-numeric, or '
                    'non-positive override falls back to 7.'
                ),
                'docs_url': '_docs/integrations/site.md#sprint_badge_window_days',
            },
            {
                'key': 'SPRINT_END_AUTO_DISTRIBUTE_FEEDBACK_ENABLED',
                'is_secret': False,
                'is_boolean': True,
                'optional': True,
                'default': 'false',
                'description': (
                    'When on, the daily sprint-end recap job distributes '
                    'attached sprint feedback requests before sending member '
                    'recaps, so the recap can link to each member feedback '
                    'form. Defaults off for staff-controlled distribution.'
                ),
                'docs_url': '_docs/integrations/site.md#sprint_end_auto_distribute_feedback_enabled',
            },
            {
                'key': 'CRM_EXPORT_MAX_LIMIT',
                'is_secret': False,
                'optional': True,
                'default': '200',
                'description': (
                    'Hard ceiling on the page size for the CRM export '
                    'endpoint (GET /api/crm/export, issue #1079). The '
                    'requested ``limit`` is clamped to this ceiling so a '
                    'single call cannot pull an unbounded aggregate. Default '
                    '200. A blank, non-numeric, or non-positive override '
                    'falls back to 200.'
                ),
                'docs_url': '_docs/integrations/site.md#crm_export_max_limit',
            },
        ],
    },
    {
        'name': 'analytics',
        'label': 'Analytics',
        'keys': [
            {
                'key': 'GOOGLE_ANALYTICS_ID',
                'is_secret': False,
                'optional': True,
                'description': (
                    'Google Analytics 4 measurement ID (e.g. G-XXXXXXXXXX). '
                    'When blank, no GA loader is emitted. Find it in GA: '
                    'Admin > Data Streams > [your stream] > Measurement ID.'
                ),
                'docs_url': '_docs/integrations/analytics.md#google_analytics_id',
            },
            {
                'key': 'USER_ACTIVITY_RETENTION_DAYS',
                'is_secret': False,
                'optional': True,
                'default': '365',
                'description': (
                    'How many days of per-user CRM activity timeline rows '
                    '(analytics.UserActivity) to keep before the daily '
                    'purge_old_user_activity job deletes them. Longer than '
                    'the 90-day SES audit-log window because activity is a '
                    'CRM signal staff use, but still bounded for storage / '
                    'PII. Default 365. A non-integer or non-positive '
                    'override falls back to 365. Issue #853.'
                ),
                'docs_url': '_docs/integrations/analytics.md#user_activity_retention_days',
            },
        ],
    },
    {
        'name': 'auth',
        'label': 'Auth',
        'keys': [
            {
                'key': 'UNVERIFIED_USER_TTL_DAYS',
                'is_secret': False,
                'description': (
                    'Number of days an email-signup account stays alive without verifying '
                    'before the daily purge job hard-deletes it. Default 7. Lower this '
                    '(e.g. 3) during spam waves; raise it for relaxed launches. Issue #452.'
                ),
                'docs_url': '_docs/integrations/auth.md#unverified_user_ttl_days',
            },
        ],
    },
    {
        'name': 'banner_generator',
        'label': 'Banner Generator',
        'keys': [
            {
                'key': 'BANNER_GENERATOR_FUNCTION_URL',
                'is_secret': False,
                'description': (
                    'HTTPS Function URL of the banner-generator Lambda. '
                    'Used to render OG banners for synced content. Without '
                    'this auto-banner generation is silently skipped.'
                ),
                'docs_url': '_docs/integrations/banner_generator.md#banner_generator_function_url',
            },
            {
                'key': 'BANNER_GENERATOR_AUTH_TOKEN',
                'is_secret': True,
                'description': (
                    'Bearer token used in the Authorization header when '
                    'calling the banner-generator Lambda. Issued '
                    'out-of-band by the operator.'
                ),
                'docs_url': '_docs/integrations/banner_generator.md#banner_generator_auth_token',
            },
            {
                'key': 'BANNER_GENERATOR_TIMEOUT_SECONDS',
                'is_secret': False,
                'optional': True,
                'default': '90',
                'description': (
                    'HTTP timeout in seconds for the render call to the '
                    'banner-generator Lambda. Should comfortably cover a '
                    'container-Lambda cold start; warm renders finish in '
                    '~1.4s, so a high ceiling costs nothing on the happy '
                    'path. Default 90. A non-integer or non-positive '
                    'override falls back to 90.'
                ),
                'docs_url': '_docs/integrations/banner_generator.md#banner_generator_timeout_seconds',
            },
            {
                'key': 'BANNER_UPLOAD_MAX_MB',
                'is_secret': False,
                'optional': True,
                'default': '5',
                'description': (
                    'Maximum size (in MB) for an operator-uploaded custom '
                    'banner/social image in Studio. Default 5. A non-integer '
                    'or non-positive override falls back to 5.'
                ),
                'docs_url': '_docs/integrations/banner_generator.md#banner_upload_max_mb',
            },
            {
                'key': 'BANNER_UPLOAD_ALLOWED_TYPES',
                'is_secret': False,
                'optional': True,
                'default': 'image/jpeg,image/png,image/webp',
                'description': (
                    'Comma-separated list of MIME types accepted for custom '
                    'banner uploads. Only JPEG, PNG, and WebP are supported '
                    'by the storage key builder; unknown types are ignored.'
                ),
                'docs_url': '_docs/integrations/banner_generator.md#banner_upload_allowed_types',
            },
            {
                'key': 'BANNER_UPLOAD_KEY_PREFIX',
                'is_secret': False,
                'optional': True,
                'default': 'custom-banners',
                'description': (
                    'CDN/S3 key prefix under which operator-uploaded custom '
                    'banners are stored (e.g. custom-banners/article/...). '
                    'The safe-delete cleanup is scoped to this prefix.'
                ),
                'docs_url': '_docs/integrations/banner_generator.md#banner_upload_key_prefix',
            },
        ],
    },
    {
        'name': 'llm',
        'label': 'LLM Provider',
        'keys': [
            {
                'key': 'LLM_PROVIDER',
                'is_secret': False,
                'default': 'anthropic',
                'description': (
                    'Which backend the LLM service uses. Only "anthropic" is '
                    'implemented today (also covers Anthropic-compatible '
                    'gateways such as Z.ai via LLM_BASE_URL). "openai" and '
                    '"bedrock" are reserved for future backends.'
                ),
                'docs_url': '_docs/integrations/llm.md#llm_provider',
            },
            {
                'key': 'LLM_API_KEY',
                'is_secret': True,
                'description': (
                    'API key/credential for the selected provider. For '
                    '"anthropic" this is an Anthropic (or compatible-gateway) '
                    'key. Without it, LLM features are disabled.'
                ),
                'docs_url': '_docs/integrations/llm.md#llm_api_key',
            },
            {
                'key': 'LLM_BASE_URL',
                'is_secret': False,
                'optional': True,
                'default': 'https://api.anthropic.com',
                'description': (
                    'Base URL of the provider API. Leave as default for '
                    'Anthropic; override to point at an Anthropic-compatible '
                    'gateway/proxy (e.g. a Z.ai-style endpoint).'
                ),
                'docs_url': '_docs/integrations/llm.md#llm_base_url',
            },
            {
                'key': 'LLM_MODEL',
                'is_secret': False,
                'default': 'claude-sonnet-4-5',
                'description': (
                    'Default model name used when a caller does not pass an '
                    'explicit model. Override to pin a different Claude model '
                    'or a gateway model name.'
                ),
                'docs_url': '_docs/integrations/llm.md#llm_model',
            },
            {
                'key': 'LLM_JUDGE_MODEL',
                'is_secret': False,
                'optional': True,
                'default': '',
                'description': (
                    'Model used by the live LLM-judge test set '
                    '(tests/live_judge/, make test-judge). Leave empty to '
                    'fall back to LLM_MODEL (judge == assistant model). '
                    'Override to swap in a stronger/cheaper judge without '
                    'changing the assistant model under test.'
                ),
                'docs_url': '_docs/integrations/llm.md#llm_judge_model',
            },
            {
                'key': 'ONBOARDING_AI_ENABLED',
                'is_secret': False,
                'is_boolean': True,
                'default': 'true',
                'description': (
                    'Set true to offer the conversational AI onboarding flow '
                    'when the LLM is enabled. When off (or the LLM is '
                    'disabled), /onboarding/ shows the form-first flow only. '
                    'Defaults on; switchable without a redeploy.'
                ),
                'docs_url': '_docs/integrations/llm.md#onboarding_ai_enabled',
            },
            {
                'key': 'ONBOARDING_AI_STREAMING',
                'is_secret': False,
                'is_boolean': True,
                'default': 'true',
                'description': (
                    'Set true to stream the AI onboarding assistant reply '
                    'token-by-token over Server-Sent Events when the AI path '
                    'is enabled. When off (or the AI path is disabled), the '
                    'chat uses the non-streaming request/response transport '
                    'and opens no SSE connection. Defaults on; switchable '
                    'without a redeploy. The browser falls back to the '
                    'non-streaming path automatically if a proxy buffers the '
                    'stream.'
                ),
                'docs_url': '_docs/integrations/llm.md#onboarding_ai_streaming',
            },
            {
                'key': 'NEXT_SPRINT_DRAFT_USE_PROFILE',
                'is_secret': False,
                'is_boolean': True,
                'default': 'true',
                'description': (
                    'Set true to feed the member onboarding profile (stated '
                    'background/goals, persona, CRM summary and next-steps) '
                    'into the LLM next-sprint plan draft, so the generated '
                    'draft is informed by the profile and not just plan state '
                    'and recent #plan-sprints updates. When off, the draft is '
                    'assembled without the profile block (pre-#913 behaviour). '
                    'Affects both the Studio "Draft next sprint plan" button '
                    'and POST /api/plans/<id>/draft-next-sprint. Defaults on; '
                    'switchable without a redeploy.'
                ),
                'docs_url': (
                    '_docs/integrations/llm.md#next_sprint_draft_use_profile'
                ),
            },
        ],
    },
    {
        'name': 'observability',
        'label': 'Observability',
        'keys': [
            {
                'key': 'LOGFIRE_ENABLED',
                'is_secret': False,
                'is_boolean': True,
                'default': 'false',
                'description': (
                    'Explicit on switch for Pydantic Logfire. Default off '
                    'everywhere; must be true (plus a token, plus not running '
                    'tests) before Logfire initializes. Keeps local/dev/eval '
                    'runs silent unless an operator opts in.'
                ),
                'docs_url': '_docs/integrations/observability.md#logfire_enabled',
            },
            {
                'key': 'LOGFIRE_TOKEN',
                'is_secret': True,
                'description': (
                    'Logfire write token. Get it from the Logfire project '
                    'settings. When blank, Logfire is fully off. Masked in '
                    'Studio.'
                ),
                'docs_url': '_docs/integrations/observability.md#logfire_token',
            },
            {
                'key': 'LOGFIRE_ENVIRONMENT',
                'is_secret': False,
                'optional': True,
                'default': 'production',
                'description': (
                    'Logfire environment tag passed to '
                    'logfire.configure(environment=...), so prod traces are '
                    'separable from any opt-in dev run. Defaults to '
                    '"production".'
                ),
                'docs_url': '_docs/integrations/observability.md#logfire_environment',
            },
        ],
    },
    {
        'name': 'maven',
        'label': 'Maven',
        'keys': [
            {
                'key': 'MAVEN_ENROLLMENT_ENABLED',
                'is_secret': False,
                'is_boolean': True,
                'default': 'false',
                'description': (
                    'Master switch for the Maven cohort auto-onboarding flow '
                    '(issue #960). When off, the /api/webhooks/maven endpoint '
                    'returns {"status":"disabled"} and creates no accounts, '
                    'overrides, Slack invites, or emails. Default off.'
                ),
                'docs_url': '_docs/integrations/maven.md#maven_enrollment_enabled',
            },
            {
                'key': 'MAVEN_WEBHOOK_SHARED_SECRET',
                'is_secret': True,
                'description': (
                    'Shared secret that authenticates inbound Maven (or Zapier) '
                    'webhook calls. Generate a long random token, paste it here, '
                    'and put it in the webhook URL (?secret=...) or an '
                    'X-Maven-Secret header. When blank the endpoint rejects all '
                    'requests with 403, even when the feature is enabled.'
                ),
                'docs_url': '_docs/integrations/maven.md#maven_webhook_shared_secret',
            },
            {
                'key': 'MAVEN_OVERRIDE_TIER_SLUG',
                'is_secret': False,
                'optional': True,
                'default': 'main',
                'description': (
                    'Tier slug granted as a long-lived override to Maven '
                    'enrollees. Validated against Tier; free / level-0 slugs are '
                    'rejected and fall back to "main" (logged). Defaults to "main".'
                ),
                'docs_url': '_docs/integrations/maven.md#maven_override_tier_slug',
            },
            {
                'key': 'MAVEN_OVERRIDE_DURATION_DAYS',
                'is_secret': False,
                'optional': True,
                'default': '3650',
                'description': (
                    'Lifetime in days of the override granted to Maven enrollees '
                    '(default 3650, ~10 years, matching the manual contact-import '
                    'practice). An existing longer override is never shortened.'
                ),
                'docs_url': '_docs/integrations/maven.md#maven_override_duration_days',
            },
        ],
    },
    {
        'name': 'triggers',
        'label': 'Event triggers',
        'keys': [
            {
                'key': 'TRIGGERS_ENABLED',
                'is_secret': False,
                'is_boolean': True,
                'default': 'false',
                'description': (
                    'Master switch for the outbound event-hooks subsystem '
                    '(issue #1070). When off, emit_event records nothing and '
                    'dispatches no webhooks, and claim widgets show a paused '
                    'state. Turn on once at least one TriggerSubscription points '
                    'at a live handler. Default off.'
                ),
                'docs_url': '_docs/integrations/triggers.md#triggers_enabled',
            },
        ],
    },
]


def get_group_by_name(name):
    """Look up an integration group by its name.

    Args:
        name: Group name (e.g. 'stripe', 'zoom').

    Returns:
        dict or None: The group definition, or None if not found.
    """
    for group in INTEGRATION_GROUPS:
        if group['name'] == name:
            return group
    return None
