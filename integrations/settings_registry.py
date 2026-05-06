"""Registry of all known integration settings with metadata.

Each group defines the integration service and its configurable keys.
The 'multiline' flag indicates keys that need a textarea (e.g. PEM keys).

Each ``description`` is a short answer to: what does it do, where do I
get it, what breaks without it. Kept to one sentence so the dashboard
stays scannable. See issue #322 for the locked rewrites.

NOTE: ``_docs/configuration.md`` references the count and names of these
groups in the Studio sign-in section ("confirm 10 integration groups are
listed (...)"). When adding, removing, or renaming a group here, update
that line of the doc in the same PR.
"""

INTEGRATION_GROUPS = [
    {
        'name': 'stripe',
        'label': 'Stripe',
        'keys': [
            {'key': 'STRIPE_SECRET_KEY', 'is_secret': True, 'description': 'Server-side Stripe API key. Get from Stripe Dashboard > Developers > API keys. Without this checkout fails.'},
            {'key': 'STRIPE_WEBHOOK_SECRET', 'is_secret': True, 'description': 'Verifies that webhook callbacks really came from Stripe. Get from Stripe Dashboard > Webhooks > [your endpoint].'},
            {'key': 'STRIPE_PUBLISHABLE_KEY', 'is_secret': False, 'description': 'Public Stripe key embedded in the frontend. Get from Stripe Dashboard > Developers > API keys.'},
            {'key': 'STRIPE_CHECKOUT_ENABLED', 'is_secret': False, 'is_boolean': True, 'description': 'Set true to use hosted Stripe Checkout sessions; false to use payment links.'},
            {'key': 'STRIPE_CUSTOMER_PORTAL_URL', 'is_secret': False, 'description': 'Stripe-hosted page where members manage their subscription. Get from Stripe Dashboard > Settings > Billing > Customer portal.'},
            {
                'key': 'STRIPE_DASHBOARD_ACCOUNT_ID',
                'is_secret': False,
                'description': (
                    'Stripe account ID used to build dashboard deep-links '
                    '(e.g. "acct_1T1mfGB7mZrgL7H5"). Find it in the Stripe URL when '
                    'you are signed in to your account. Optional — when blank, the '
                    'Stripe icon next to a user is shown but not clickable.'
                ),
            },
        ],
    },
    {
        'name': 'zoom',
        'label': 'Zoom',
        'keys': [
            {'key': 'ZOOM_CLIENT_ID', 'is_secret': True, 'description': 'Zoom Server-to-Server OAuth app client ID. Without this we cannot create or fetch meetings.'},
            {'key': 'ZOOM_CLIENT_SECRET', 'is_secret': True, 'description': 'Zoom OAuth client secret. Get from your Zoom app under Marketplace > Build App > S2S OAuth.'},
            {'key': 'ZOOM_ACCOUNT_ID', 'is_secret': True, 'description': 'Zoom account UUID the OAuth app belongs to. Found in the Zoom Marketplace app settings.'},
            {'key': 'ZOOM_WEBHOOK_SECRET_TOKEN', 'is_secret': True, 'description': 'Verifies Zoom webhook callbacks (event start, recording ready). Set in the Zoom app event subscription.'},
        ],
    },
    {
        'name': 'ses',
        'label': 'Email (SES)',
        'keys': [
            {'key': 'AWS_ACCESS_KEY_ID', 'is_secret': True, 'description': 'AWS access key for an IAM user with SES send + suppression-list permissions.'},
            {'key': 'AWS_SECRET_ACCESS_KEY', 'is_secret': True, 'description': 'AWS secret key paired with the access key above. Without these no email is sent.'},
            {'key': 'AWS_SES_REGION', 'is_secret': False, 'description': 'AWS region for SES (e.g. eu-west-1). Must match the verified domain region.'},
            {'key': 'SES_FROM_EMAIL', 'is_secret': False, 'description': 'Sender address for transactional and campaign email. Must be verified in SES.'},
            {'key': 'SES_CONFIGURATION_SET_NAME', 'is_secret': False, 'description': 'Optional SES configuration set name that publishes delivery, open, and click events to SNS.'},
            {'key': 'SES_WEBHOOK_VALIDATION_ENABLED', 'is_secret': False, 'is_boolean': True, 'description': 'Set true to verify SNS bounce/complaint signatures (recommended in production).'},
        ],
    },
    {
        'name': 's3_recordings',
        'label': 'S3 Recordings',
        'keys': [
            {'key': 'AWS_S3_RECORDINGS_BUCKET', 'is_secret': False, 'description': 'S3 bucket where event recordings are uploaded after processing.'},
            {'key': 'AWS_S3_RECORDINGS_REGION', 'is_secret': False, 'description': 'AWS region of the recordings bucket (e.g. eu-west-1).'},
        ],
    },
    {
        'name': 's3_content',
        'label': 'S3 Content Images',
        'keys': [
            {'key': 'AWS_S3_CONTENT_BUCKET', 'is_secret': False, 'description': 'S3 bucket for content images extracted from synced markdown. Public-read.'},
            {'key': 'AWS_S3_CONTENT_REGION', 'is_secret': False, 'description': 'AWS region of the content-images bucket.'},
            {'key': 'CONTENT_CDN_BASE', 'is_secret': False, 'description': 'Public CDN base URL fronting the content bucket (e.g. https://cdn.aishippinglabs.com). Without this images break on the live site.'},
        ],
    },
    {
        'name': 'youtube',
        'label': 'YouTube',
        'keys': [
            {'key': 'YOUTUBE_CLIENT_ID', 'is_secret': True, 'description': 'YouTube Data API OAuth client ID. Used to upload event recordings to the channel.'},
            {'key': 'YOUTUBE_CLIENT_SECRET', 'is_secret': True, 'description': 'YouTube OAuth client secret. Get from Google Cloud Console > APIs & Services > Credentials.'},
            {'key': 'YOUTUBE_REFRESH_TOKEN', 'is_secret': True, 'description': 'Long-lived OAuth refresh token authorising uploads. Generated once via the YouTube auth flow.'},
        ],
    },
    {
        'name': 'github',
        'label': 'GitHub App',
        'keys': [
            {'key': 'GITHUB_APP_ID', 'is_secret': False, 'description': 'Numeric ID of the GitHub App used to read content repos. Found at github.com/settings/apps/<your-app>.'},
            {'key': 'GITHUB_APP_INSTALLATION_ID', 'is_secret': False, 'description': 'Installation ID of the GitHub App on the content org. Found at github.com/organizations/<org>/settings/installations.'},
            {'key': 'GITHUB_APP_PRIVATE_KEY', 'is_secret': True, 'description': 'PEM private key issued by GitHub when the app was created. Used to sign API requests; without this content sync fails.', 'multiline': True},
        ],
    },
    {
        'name': 'slack',
        'label': 'Slack',
        'keys': [
            {'key': 'SLACK_ENABLED', 'is_secret': False, 'is_boolean': True, 'description': 'Set true to enable Slack bot posting and event listening. Off by default to keep dev/test silent.'},
            {'key': 'SLACK_ENVIRONMENT', 'is_secret': False, 'description': 'Slack routing mode: production, development, or test. Non-production modes ignore production channel IDs.'},
            {'key': 'SLACK_BOT_TOKEN', 'is_secret': True, 'description': 'Slack bot user OAuth token (xoxb-...). Used to post announcements and read community channel events.'},
            {'key': 'SLACK_COMMUNITY_CHANNEL_IDS', 'is_secret': False, 'description': 'Comma-separated channel IDs the bot watches for community signals (mentions, reactions).'},
            {'key': 'SLACK_ANNOUNCEMENTS_CHANNEL_ID', 'is_secret': False, 'description': 'Channel ID where the bot posts new content and event announcements.'},
            {'key': 'SLACK_DEV_COMMUNITY_CHANNEL_IDS', 'is_secret': False, 'description': 'Development-only community channel IDs. Used only when SLACK_ENVIRONMENT=development.'},
            {'key': 'SLACK_DEV_ANNOUNCEMENTS_CHANNEL_ID', 'is_secret': False, 'description': 'Development-only announcement channel ID. Used only when SLACK_ENVIRONMENT=development.'},
            {'key': 'SLACK_TEST_COMMUNITY_CHANNEL_IDS', 'is_secret': False, 'description': 'Test-only community channel IDs. Used only when SLACK_ENVIRONMENT=test.'},
            {'key': 'SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID', 'is_secret': False, 'description': 'Test-only announcement channel ID, e.g. #integration-tests. Used only when SLACK_ENVIRONMENT=test.'},
            {'key': 'SLACK_ANNOUNCEMENTS_CHANNEL_NAME', 'is_secret': False, 'description': 'Display name of the announcements channel (e.g. #announcements). Used in UI copy.'},
            {'key': 'SLACK_INVITE_URL', 'is_secret': False, 'description': 'Public Slack workspace invite URL shown to Main+ members on the dashboard.'},
        ],
    },
    {
        'name': 'site',
        'label': 'Site',
        'keys': [
            {'key': 'SITE_BASE_URL', 'is_secret': False, 'description': 'Canonical absolute URL — used for generated links, OAuth callbacks, etc.'},
            {'key': 'SITE_BASE_URL_ALIASES', 'is_secret': False, 'description': 'Additional hosts that should not trigger the host-mismatch banner. Comma- or whitespace-separated.'},
            {'key': 'EVENT_DISPLAY_TIMEZONE', 'is_secret': False, 'description': 'Default IANA timezone for public event times when the browser cannot provide one.'},
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
