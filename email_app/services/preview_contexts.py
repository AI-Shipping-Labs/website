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
    },
    'free_welcome': {
        'user_name': 'Ada',
        'site_url': 'https://aishippinglabs.com',
    },
    'email_verification_signup': {
        'user_name': 'Ada',
        'verify_url': 'https://aishippinglabs.com/verify?token=demo',
        'site_url': 'https://aishippinglabs.com',
        'ttl_days': 7,
    },
    'email_verification_subscribe': {
        'user_name': 'Ada',
        'verify_url': 'https://aishippinglabs.com/verify?token=demo',
        'site_url': 'https://aishippinglabs.com',
        'ttl_days': 7,
    },
    'email_verification_signup_reminder': {
        'user_name': 'Ada',
        'verify_url': 'https://aishippinglabs.com/verify?token=demo',
    },
    'email_verification_subscribe_reminder': {
        'user_name': 'Ada',
        'verify_url': 'https://aishippinglabs.com/verify?token=demo',
    },
    'password_reset': {
        'user_name': 'Ada',
        'reset_url': 'https://aishippinglabs.com/reset?token=demo',
    },
    'account_email_change_confirm': {
        'user_name': 'Ada',
        'old_email': 'old-member@test.com',
        'new_email': 'new-member@test.com',
        'confirm_url': (
            'https://aishippinglabs.com/account/change-email/confirm'
            '?token=demo'
        ),
        'expiry_hours': 24,
    },
    'account_email_changed_notice': {
        'user_name': 'Ada',
        'old_email': 'old-member@test.com',
        'new_email': 'new-member@test.com',
        'account_url': 'https://aishippinglabs.com/account/',
    },
    'cancellation': {
        'user_name': 'Ada',
        'tier_name': 'Main',
        'access_until': 'March 15, 2026',
        'site_url': 'https://aishippinglabs.com',
    },
    'community_invite': {
        'user_name': 'Ada',
        # Issue #953: the invite now links to the gated /community/slack
        # redirect built from ``site_url`` (auto-injected on real sends),
        # never the raw SLACK_INVITE_URL.
        'site_url': 'https://aishippinglabs.com',
    },
    'event_registration': {
        'user_name': 'Ada',
        'event_title': 'AI Shipping Workshop',
        # Issue #666: real sends produce "<formatted time> <tz label>" via
        # accounts.services.timezones.format_user_datetime. Mirror that
        # shape here so the Studio preview matches what users will see.
        'event_datetime': 'March 21, 2026, 18:00 Europe/Berlin',
        # Match the real send shape: {site}/events/{id}/{slug}/join (#1082).
        'join_url': 'https://aishippinglabs.com/events/42/community-lunch/join',
        # Issue #588: real send mints a JWT-signed cancel URL. The
        # preview URL must be a real URL shape so operators can hover
        # the link in the Studio preview and confirm the wiring.
        'cancel_url': (
            'https://aishippinglabs.com/events/community-lunch/'
            'cancel-registration?token=preview-token'
        ),
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
        # Issue #666: see note on event_registration above.
        'event_datetime': 'March 21, 2026, 18:00 Europe/Berlin',
        # Issue #706: ``event_url`` is the platform-side join redirect
        # (#704 time-gates it). Single CTA, matches the real send shape:
        # {site}/events/{id}/{slug}/join (#1082).
        'event_url': 'https://aishippinglabs.com/events/42/ai-shipping-workshop/join',
    },
    'post_event_followup': {
        'user_name': 'Ada',
        'event_title': 'AI Shipping Workshop',
        'event_summary': (
            'Thanks for joining the AI Shipping Workshop. We covered '
            'three deployment patterns and ended with live Q&A.'
        ),
        'recording_url': (
            'https://cdn.aishippinglabs.com/recordings/'
            'ai-shipping-workshop.mp4'
        ),
        'event_url': 'https://aishippinglabs.com/events/42/ai-shipping-workshop',
        'notes_placeholder': True,
        'feedback_url': (
            'https://aishippinglabs.com/events/ai-shipping-workshop/feedback'
        ),
    },
    'event_recording_ready': {
        'user_name': 'Ada',
        'event_title': 'AI Shipping Workshop',
        'event_datetime': 'March 21, 2026, 18:00-19:00 Europe/Berlin',
        # Issue #1134 (Phase B): preview the "available to watch" variant so
        # the watch-link path renders in the Studio email preview. The watch
        # link points at the workshop video page, never a raw S3 URL.
        'publish_state': 'Published and available to watch',
        'publish_copy': (
            'The recording is now available to watch. Members with access can '
            'watch it right away on the workshop video page.'
        ),
        'is_available_to_watch': True,
        'watch_url': 'https://aishippinglabs.com/workshops/ai-shipping-workshop/video',
        'studio_event_url': 'https://aishippinglabs.com/studio/events/42/edit',
        'zoom_recording_url': 'https://zoom.us/rec/play/preview',
    },
    'event_rescheduled': {
        'user_name': 'Ada',
        'event_title': 'AI Shipping Workshop',
        # Issue #670: both times pre-formatted via format_user_datetime in
        # the recipient's preferred timezone. Issue #1071: calendar-invite
        # emails (incl. event_rescheduled) carry the weekday via
        # CALENDAR_INVITE_DATETIME_FORMAT — mirror that shape here. Both
        # dates fall on a Saturday.
        'old_event_datetime': 'Saturday, March 21, 2026, 18:00 Europe/Berlin',
        'new_event_datetime': 'Saturday, March 28, 2026, 18:00 Europe/Berlin',
        'join_url': (
            'https://aishippinglabs.com/events/42/community-lunch/join'
        ),
        'cancel_url': (
            'https://aishippinglabs.com/events/community-lunch/'
            'cancel-registration?token=preview-token'
        ),
    },
    'event_cancelled': {
        'user_name': 'Ada',
        'event_title': 'AI Shipping Workshop',
        'event_datetime': 'Saturday, March 28, 2026, 18:00 Europe/Berlin',
    },
    'plan_shared': {
        # Issue #732: in-product share notification for sprint plans.
        # ``plan_url`` deep-links to the member-owned workspace
        # (``my_plan_detail`` at ``/sprints/<slug>/plan/<id>``), not
        # the cohort-board sibling.
        'user_name': 'Ada',
        'sprint_name': 'May 2026',
        'plan_url': 'https://aishippinglabs.com/sprints/may-2026/plan/42',
    },
    'sprint_week_start': {
        'user_name': 'Ada',
        'sprint_name': 'May 2026',
        'week_number': 3,
        'week_theme': 'Ship the working prototype',
        'unfinished_count': 2,
        'unfinished_label': 'unfinished checkpoints',
        'needs_previous_week_note': True,
        'previous_week_number': 2,
        'plan_url': (
            'https://aishippinglabs.com/sprints/may-2026/plan/42#week-3'
        ),
    },
    'sprint_week_note_prompt': {
        'user_name': 'Ada',
        'sprint_name': 'May 2026',
        'week_number': 2,
        'week_theme': 'Validate the workflow',
        'plan_url': (
            'https://aishippinglabs.com/sprints/may-2026/plan/42#week-2'
        ),
    },
    'sprint_end_recap': {
        'user_name': 'Ada',
        'sprint_name': 'May 2026',
        'completed_count': 9,
        'total_count': 12,
        'progress_sentence': 'You completed 9 of 12 checkpoints.',
        'plan_url': 'https://aishippinglabs.com/sprints/may-2026/plan/42',
        'has_feedback': True,
        'feedback_url': (
            'https://aishippinglabs.com/sprints/may-2026/feedback/7'
        ),
        'feedback_cta_label': 'Share sprint feedback',
        'feedback_copy': (
            'A short feedback form is ready when you have a minute.'
        ),
        'has_next_action': True,
        'next_action_url': 'https://aishippinglabs.com/sprints/june-2026',
        'next_action_label': 'Join the next sprint',
        'next_action_copy': (
            'June 2026 is open to your tier. Join when you are ready for '
            'the next cohort window.'
        ),
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
    # Issue #847: tier-specific paid-signup welcomes. The real send
    # passes ``user_first_name`` and EmailService auto-injects
    # ``site_url``; mirror both so the Studio preview resolves the
    # greeting and the ``/onboarding/`` link instead of leaving them
    # blank.
    'cofounder_welcome': {
        'user_first_name': 'Ada',
        'site_url': 'https://aishippinglabs.com',
        # Issue #950: the welcome copy is evergreen; the injected sprint
        # paragraph is always empty now, so the preview mirrors that.
        'current_sprint_status_paragraph': '',
    },
    'basic_welcome': {
        'user_first_name': 'Ada',
        'site_url': 'https://aishippinglabs.com',
    },
    'premium_welcome': {
        'user_first_name': 'Ada',
        'site_url': 'https://aishippinglabs.com',
    },
    # Issue #1133: one-week onboarding reminder. The real send passes no
    # extra context; EmailService auto-injects ``user_name`` and
    # ``site_url``. Mirror both so the Studio preview renders the greeting
    # and the ``/onboarding/`` link instead of leaving them blank.
    'onboarding_reminder': {
        'user_name': 'Ada',
        'site_url': 'https://aishippinglabs.com',
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
    # Issue #869 / #1071: the series calendar-invite emails. Real sends
    # assemble ``occurrences_list`` as a preformatted markdown bullet list
    # via _render_series_email, with calendar-invite times carrying the
    # weekday (CALENDAR_INVITE_DATETIME_FORMAT). Mirror that string shape so
    # the Studio preview renders the template with no missing variables.
    'series_registration': {
        'user_name': 'Ada',
        'series_name': 'LLM Zoomcamp 2026 office hours',
        'series_url': (
            'https://aishippinglabs.com/events/series/llm-zoomcamp-office-hours'
        ),
        'registered_count': 2,
        'registered_count_plural': 's',
        'occurrences_list': (
            '- Office hours — Thursday, June 25, 2026, 18:00 Europe/Berlin\n'
            '- Office hours — Thursday, July 02, 2026, 18:00 Europe/Berlin'
        ),
        'partial_note': '',
        'timezone_help': (
            'Times above are shown in your timezone. Wrong zone? '
            '[Change your timezone](https://aishippinglabs.com/account/'
            '#display-preferences-section).'
        ),
    },
    # Issue #1071: changed-occurrence framing. ``changed_occurrence`` is
    # truthy and the changed line in ``occurrences_list`` already carries a
    # ``(was ...)`` before/after annotation, matching how the real send
    # renders a single-occurrence reschedule.
    'series_update': {
        'user_name': 'Ada',
        'series_name': 'LLM Zoomcamp 2026 office hours',
        'series_url': (
            'https://aishippinglabs.com/events/series/llm-zoomcamp-office-hours'
        ),
        'registered_count': 2,
        'registered_count_plural': 's',
        'changed_occurrence': True,
        'event_title': 'Office hours',
        'occurrences_list': (
            '- Office hours — Thursday, June 25, 2026, 18:00 Europe/Berlin '
            '(was Wednesday, June 24, 2026, 18:00 Europe/Berlin)\n'
            '- Office hours — Thursday, July 02, 2026, 18:00 Europe/Berlin'
        ),
        'partial_note': '',
        'timezone_help': (
            'Times above are shown in your timezone. Wrong zone? '
            '[Change your timezone](https://aishippinglabs.com/account/'
            '#display-preferences-section).'
        ),
    },
    'series_cancellation': {
        'user_name': 'Ada',
        'series_name': 'LLM Zoomcamp 2026 office hours',
        'series_url': (
            'https://aishippinglabs.com/events/series/llm-zoomcamp-office-hours'
        ),
        'registered_count': 1,
        'registered_count_plural': '',
        'occurrences_list': (
            '- Office hours — Thursday, June 25, 2026, 18:00 Europe/Berlin'
        ),
        'partial_note': '',
        'timezone_help': '',
    },
}


def get_preview_context(template_name):
    """Return the placeholder context dict for a template, or {} if unknown."""
    return dict(PREVIEW_CONTEXTS.get(template_name, {}))
