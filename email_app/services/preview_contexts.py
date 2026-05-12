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
        'site_url': 'https://aishippinglabs.com',
        'ttl_days': 7,
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
        # Match the real send shape: {site}/events/{slug}/join.
        'join_url': 'https://aishippinglabs.com/events/community-lunch/join',
        # Representative one-click "Add to Calendar" URLs. Shape mirrors
        # what events.services.calendar_links.build_calendar_links emits so
        # operators can hover the preview and confirm the wiring.
        'google_calendar_url': (
            'https://calendar.google.com/calendar/render'
            '?action=TEMPLATE'
            '&text=AI%20Shipping%20Workshop'
            '&dates=20260321T180000Z%2F20260321T190000Z'
            '&details=Join%3A%20https%3A%2F%2Faishippinglabs.com'
            '%2Fevents%2Fcommunity-lunch%2Fjoin'
            '&location=https%3A%2F%2Faishippinglabs.com'
            '%2Fevents%2Fcommunity-lunch%2Fjoin'
        ),
        'outlook_calendar_url': (
            'https://outlook.live.com/calendar/0/deeplink/compose'
            '?path=%2Fcalendar%2Faction%2Fcompose'
            '&rru=addevent'
            '&subject=AI%20Shipping%20Workshop'
            '&startdt=2026-03-21T18%3A00%3A00Z'
            '&enddt=2026-03-21T19%3A00%3A00Z'
            '&body=Join%3A%20https%3A%2F%2Faishippinglabs.com'
            '%2Fevents%2Fcommunity-lunch%2Fjoin'
            '&location=https%3A%2F%2Faishippinglabs.com'
            '%2Fevents%2Fcommunity-lunch%2Fjoin'
        ),
        'office365_calendar_url': (
            'https://outlook.office.com/calendar/0/deeplink/compose'
            '?path=%2Fcalendar%2Faction%2Fcompose'
            '&rru=addevent'
            '&subject=AI%20Shipping%20Workshop'
            '&startdt=2026-03-21T18%3A00%3A00Z'
            '&enddt=2026-03-21T19%3A00%3A00Z'
            '&body=Join%3A%20https%3A%2F%2Faishippinglabs.com'
            '%2Fevents%2Fcommunity-lunch%2Fjoin'
            '&location=https%3A%2F%2Faishippinglabs.com'
            '%2Fevents%2Fcommunity-lunch%2Fjoin'
        ),
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
