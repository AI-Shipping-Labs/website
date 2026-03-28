"""Registry of all known integration settings with metadata.

Each group defines the integration service and its configurable keys.
The 'multiline' flag indicates keys that need a textarea (e.g. PEM keys).
"""

INTEGRATION_GROUPS = [
    {
        'name': 'stripe',
        'label': 'Stripe',
        'keys': [
            {'key': 'STRIPE_SECRET_KEY', 'is_secret': True, 'description': 'Stripe secret API key'},
            {'key': 'STRIPE_WEBHOOK_SECRET', 'is_secret': True, 'description': 'Stripe webhook signing secret'},
            {'key': 'STRIPE_PUBLISHABLE_KEY', 'is_secret': False, 'description': 'Stripe publishable key'},
            {'key': 'STRIPE_CHECKOUT_ENABLED', 'is_secret': False, 'description': 'Enable Stripe Checkout Sessions (true/false)'},
            {'key': 'STRIPE_CUSTOMER_PORTAL_URL', 'is_secret': False, 'description': 'Stripe customer portal URL'},
        ],
    },
    {
        'name': 'zoom',
        'label': 'Zoom',
        'keys': [
            {'key': 'ZOOM_CLIENT_ID', 'is_secret': True, 'description': 'Zoom OAuth client ID'},
            {'key': 'ZOOM_CLIENT_SECRET', 'is_secret': True, 'description': 'Zoom OAuth client secret'},
            {'key': 'ZOOM_ACCOUNT_ID', 'is_secret': True, 'description': 'Zoom account ID'},
            {'key': 'ZOOM_WEBHOOK_SECRET_TOKEN', 'is_secret': True, 'description': 'Zoom webhook secret token'},
        ],
    },
    {
        'name': 'ses',
        'label': 'Email (SES)',
        'keys': [
            {'key': 'AWS_ACCESS_KEY_ID', 'is_secret': True, 'description': 'AWS access key ID'},
            {'key': 'AWS_SECRET_ACCESS_KEY', 'is_secret': True, 'description': 'AWS secret access key'},
            {'key': 'AWS_SES_REGION', 'is_secret': False, 'description': 'AWS SES region (e.g. us-east-1)'},
            {'key': 'SES_FROM_EMAIL', 'is_secret': False, 'description': 'From email address for SES'},
            {'key': 'SES_WEBHOOK_VALIDATION_ENABLED', 'is_secret': False, 'description': 'Enable SES webhook validation (true/false)'},
        ],
    },
    {
        'name': 's3_recordings',
        'label': 'S3 Recordings',
        'keys': [
            {'key': 'AWS_S3_RECORDINGS_BUCKET', 'is_secret': False, 'description': 'S3 bucket for event recordings'},
            {'key': 'AWS_S3_RECORDINGS_REGION', 'is_secret': False, 'description': 'S3 region for recordings'},
        ],
    },
    {
        'name': 's3_content',
        'label': 'S3 Content Images',
        'keys': [
            {'key': 'AWS_S3_CONTENT_BUCKET', 'is_secret': False, 'description': 'S3 bucket for content images'},
            {'key': 'AWS_S3_CONTENT_REGION', 'is_secret': False, 'description': 'S3 region for content images'},
            {'key': 'CONTENT_CDN_BASE', 'is_secret': False, 'description': 'CDN base URL for content images'},
        ],
    },
    {
        'name': 'youtube',
        'label': 'YouTube',
        'keys': [
            {'key': 'YOUTUBE_CLIENT_ID', 'is_secret': True, 'description': 'YouTube OAuth client ID'},
            {'key': 'YOUTUBE_CLIENT_SECRET', 'is_secret': True, 'description': 'YouTube OAuth client secret'},
            {'key': 'YOUTUBE_REFRESH_TOKEN', 'is_secret': True, 'description': 'YouTube OAuth refresh token'},
        ],
    },
    {
        'name': 'github',
        'label': 'GitHub App',
        'keys': [
            {'key': 'GITHUB_APP_ID', 'is_secret': False, 'description': 'GitHub App ID'},
            {'key': 'GITHUB_APP_INSTALLATION_ID', 'is_secret': False, 'description': 'GitHub App installation ID'},
            {'key': 'GITHUB_APP_PRIVATE_KEY', 'is_secret': True, 'description': 'GitHub App private key (PEM)', 'multiline': True},
        ],
    },
    {
        'name': 'slack',
        'label': 'Slack',
        'keys': [
            {'key': 'SLACK_ENABLED', 'is_secret': False, 'description': 'Enable Slack integration (true/false) — off by default'},
            {'key': 'SLACK_BOT_TOKEN', 'is_secret': True, 'description': 'Slack bot OAuth token'},
            {'key': 'SLACK_COMMUNITY_CHANNEL_IDS', 'is_secret': False, 'description': 'Comma-separated community channel IDs'},
            {'key': 'SLACK_ANNOUNCEMENTS_CHANNEL_ID', 'is_secret': False, 'description': 'Announcements channel ID'},
            {'key': 'SLACK_ANNOUNCEMENTS_CHANNEL_NAME', 'is_secret': False, 'description': 'Announcements channel name (e.g. #announcements)'},
            {'key': 'SLACK_INVITE_URL', 'is_secret': False, 'description': 'Slack workspace invite URL'},
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
