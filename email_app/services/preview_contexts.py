"""Placeholder context dicts used to render email previews in Studio.

The Studio template editor renders the same code path as a real send, but
substitutes these fake values for ``{{ user_name }}``, ``{{ verify_url }}``
etc. The operator never sees real user data leaked into the preview.

Keep one entry per template name listed in
``EmailService.TRANSACTIONAL_TYPES``. Missing entries fall back to an empty
dict, which is also fine -- previews simply leave the variables as the
literal placeholder strings.
"""

PREVIEW_CONTEXTS = {
    'welcome': {
        'user_name': 'Ada',
        'tier_name': 'Main',
        'site_url': 'https://aishippinglabs.com',
        'slack_invite_url': 'https://example.com/slack',
    },
    'email_verification': {
        'user_name': 'Ada',
        'verify_url': 'https://aishippinglabs.com/verify?token=demo',
    },
    'email_verification_reminder': {
        'user_name': 'Ada',
        'verify_url': 'https://aishippinglabs.com/verify?token=demo',
    },
    'password_reset': {
        'user_name': 'Ada',
        'reset_url': 'https://aishippinglabs.com/reset?token=demo',
    },
    'cancellation': {
        'user_name': 'Ada',
        'tier_name': 'Main',
        'access_until': 'March 15, 2026',
        'site_url': 'https://aishippinglabs.com',
    },
    'community_invite': {
        'user_name': 'Ada',
        'slack_invite_url': 'https://example.com/slack/invite',
    },
    'event_registration': {
        'user_name': 'Ada',
        'event_title': 'AI Shipping Workshop',
        'event_datetime': 'March 21, 2026 at 6:00 PM UTC',
        'join_url': 'https://zoom.us/j/123',
    },
    'event_reminder': {
        'user_name': 'Ada',
        'event_title': 'AI Shipping Workshop',
        'event_datetime': 'March 21, 2026 at 6:00 PM UTC',
        'event_url': 'https://zoom.us/j/123',
    },
    'lead_magnet_delivery': {
        'user_name': 'Ada',
        'resource_title': 'AI Cheat Sheet',
        'download_url': 'https://aishippinglabs.com/download/ai-cheat-sheet',
        'site_url': 'https://aishippinglabs.com',
    },
    'payment_failed': {
        'user_name': 'Ada',
        'tier_name': 'Main',
        'update_payment_url': 'https://billing.stripe.com/update',
    },
    'welcome_imported': {
        'user_name': 'Ada',
        'source_label': 'DataTalks Club',
        'import_tags': 'datatalks-alumni',
        'is_course_db_import': False,
        'is_slack_import': False,
        'course_slug_list': '',
        'password_reset_url': 'https://aishippinglabs.com/reset?token=demo',
        'sign_in_url': 'https://aishippinglabs.com/accounts/login/',
    },
}


def get_preview_context(template_name):
    """Return the placeholder context dict for a template, or {} if unknown."""
    return dict(PREVIEW_CONTEXTS.get(template_name, {}))
