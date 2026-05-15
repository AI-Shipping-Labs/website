"""
NotificationService: creates on-platform notifications for eligible users
and posts to Slack #announcements channel.

Usage:
    from notifications.services import NotificationService

    # When an article is published:
    NotificationService.notify('article', article.pk)
"""

import logging

from django.contrib.auth import get_user_model

from notifications.models import Notification

logger = logging.getLogger(__name__)

User = get_user_model()


# Maps content_type to (model_import_path, title_template, body_field, url_method)
CONTENT_TYPE_CONFIG = {
    'article': {
        'app_label': 'content',
        'model_name': 'Article',
        'title_template': 'New article: {title}',
        'level_field': 'required_level',
        'published_filter': {'published': True},
    },
    'course': {
        'app_label': 'content',
        'model_name': 'Course',
        'title_template': 'New course: {title}',
        'level_field': 'required_level',
        'published_filter': {'status': 'published'},
    },
    'event': {
        'app_label': 'events',
        'model_name': 'Event',
        'title_template': 'Upcoming event: {title}',
        'level_field': 'required_level',
        'published_filter': {'status': 'upcoming'},
    },
    'recording': {
        'app_label': 'events',
        'model_name': 'Event',
        'title_template': 'New recording: {title}',
        'level_field': 'required_level',
        'published_filter': {'published': True},
    },
    'download': {
        'app_label': 'content',
        'model_name': 'Download',
        'title_template': 'New download: {title}',
        'level_field': 'required_level',
        'published_filter': {'published': True},
    },
    'poll': {
        'app_label': 'voting',
        'model_name': 'Poll',
        'title_template': 'New poll: {title}',
        'level_field': 'required_level',
        'published_filter': {'status': 'open'},
    },
    'workshop': {
        'app_label': 'content',
        'model_name': 'Workshop',
        'title_template': 'New workshop: {title}',
        # The notification deep-links to Workshop.get_absolute_url() (the
        # landing page), so the audience must clear the landing gate.
        'level_field': 'landing_required_level',
        'published_filter': {'status': 'published'},
        # Issue #655: workshop announcements fan out as a third channel
        # via EmailService. Other content types stay bell+Slack-only
        # until their own opt-out + audience story is designed.
        'email_template': 'workshop_announcement',
    },
}


def _get_content_object(content_type, content_id):
    """Load a content object by type and ID."""
    from django.apps import apps

    config = CONTENT_TYPE_CONFIG.get(content_type)
    if not config:
        raise ValueError(f'Unknown content_type: {content_type}')

    model = apps.get_model(config['app_label'], config['model_name'])
    return model.objects.get(pk=content_id)


def _get_eligible_users(required_level):
    """Get users whose tier level is >= the required_level.

    For level 0 (open), all users are eligible.
    """
    if required_level == 0:
        return User.objects.filter(is_active=True)

    return User.objects.filter(
        is_active=True,
        tier__isnull=False,
        tier__level__gte=required_level,
    )


def _get_body(content):
    """Extract a short description from a content object."""
    description = getattr(content, 'description', '')
    if description:
        return description[:200]
    content_md = getattr(content, 'content_markdown', '')
    if content_md:
        return content_md[:200]
    return ''


def get_email_eligible_users(content_type, content):
    """Return the email-eligible audience for a content notification.

    Starts from the tier-eligible notification audience (same base as the
    bell channel) and applies the email-specific filters required for
    promotional sends (issue #655):

    - ``unsubscribed=False`` -- ``EmailService`` would skip these anyway
      for promotional kinds, but pre-filtering keeps the operator counter
      accurate.
    - ``email_verified=True`` -- unverified addresses don't receive
      promotional mail (prevents bouncing on un-confirmed addresses).
    - ``email_preferences.get('workshop_emails', True) is not False`` --
      per-content-type opt-out. Default is opted-in when the key is
      missing or the JSONField is empty.
    """
    config = CONTENT_TYPE_CONFIG.get(content_type)
    if not config:
        return User.objects.none()

    required_level = getattr(content, config['level_field'], 0)
    base = _get_eligible_users(required_level).filter(
        unsubscribed=False,
        email_verified=True,
    )

    # The per-channel opt-out lives inside the JSONField. The intent is:
    # exclude rows where ``email_preferences['workshop_emails']`` is
    # explicitly ``False``; KEEP rows where the key is missing entirely
    # (default opted-in, the new-account case).
    #
    # SQLite + Django's JSONField ``__key=False`` filter also matches
    # rows where the key is absent, which would drop opted-in users. So
    # we collect the explicit-False user ids in Python and exclude by pk
    # -- portable across SQLite and Postgres without an extra round-trip
    # for empty preferences.
    opted_out_ids = [
        pk for pk, prefs in
        User.objects.filter(
            is_active=True,
            email_preferences__has_key='workshop_emails',
        ).values_list('pk', 'email_preferences')
        if prefs.get('workshop_emails') is False
    ]
    if opted_out_ids:
        return base.exclude(pk__in=opted_out_ids)
    return base


def _send_email_channel(email_template, content_type, content):
    """Fan out the workshop-style email channel and return the success count.

    Builds the context dict once and iterates over
    :func:`get_email_eligible_users`, calling ``EmailService().send`` per
    user inside a try/except. A single failure logs a WARNING with the
    user email and content slug, then continues to the next recipient so
    one bad address does not block the rest of the announcement.
    """
    from email_app.services.email_service import EmailService

    slug = getattr(content, 'slug', '')
    workshop_url = (
        content.get_absolute_url()
        if hasattr(content, 'get_absolute_url') else ''
    )
    context = {
        'workshop_title': content.title,
        'workshop_slug': slug,
        'workshop_description': _get_body(content),
        'workshop_url': workshop_url,
    }

    service = EmailService()
    sent = 0
    for user in get_email_eligible_users(content_type, content):
        try:
            log = service.send(user, email_template, context)
        except Exception:
            logger.warning(
                'Failed to send "%s" email to %s for %s/%s',
                email_template, user.email, content_type, slug,
                exc_info=True,
            )
            continue
        # EmailService.send returns None for skipped recipients (e.g.
        # globally unsubscribed users for promotional mail). Don't count
        # those as successful sends.
        if log is not None:
            sent += 1
    return sent


class NotificationService:
    """Service for creating notifications and dispatching to channels."""

    @staticmethod
    def notify(content_type, content_id):
        """Create on-platform notifications for eligible users and post to Slack.

        For content types with an ``email_template`` configured (workshops
        only, issue #655), also fans out a direct email to every
        email-eligible subscriber.

        Args:
            content_type: One of 'article', 'course', 'event', 'recording',
                         'download', 'poll', 'workshop'.
            content_id: Primary key of the content object.

        Returns:
            ``{"notified": int, "emailed": int}`` -- ``emailed`` is always
            ``0`` for content types without an ``email_template`` so the
            shape stays uniform across types. Returns the same dict shape
            (with both zero) on unknown content types or load failures.
        """
        result = {"notified": 0, "emailed": 0}

        config = CONTENT_TYPE_CONFIG.get(content_type)
        if not config:
            logger.warning('Unknown content_type for notify: %s', content_type)
            return result

        try:
            content = _get_content_object(content_type, content_id)
        except Exception:
            logger.exception(
                'Failed to load content for notify: %s/%s',
                content_type, content_id,
            )
            return result

        title = config['title_template'].format(title=content.title)
        body = _get_body(content)
        url = content.get_absolute_url() if hasattr(content, 'get_absolute_url') else ''
        required_level = getattr(content, config['level_field'], 0)

        # Create on-platform notifications for eligible users
        eligible_users = _get_eligible_users(required_level)
        notifications = [
            Notification(
                user=user,
                title=title,
                body=body,
                url=url,
                notification_type='new_content',
            )
            for user in eligible_users
        ]
        if notifications:
            Notification.objects.bulk_create(notifications)
            logger.info(
                'Created %d notifications for %s/%s',
                len(notifications), content_type, content_id,
            )
        result["notified"] = len(notifications)

        # Post to Slack #announcements
        try:
            from notifications.services.slack_announcements import post_slack_announcement
            post_slack_announcement(content_type, content)
        except Exception:
            logger.exception(
                'Failed to post Slack announcement for %s/%s',
                content_type, content_id,
            )

        # Issue #655: direct-email channel for content types that opt in
        # via the ``email_template`` config key. Failures on a single
        # recipient must not block the rest of the fan-out.
        email_template = config.get('email_template')
        if email_template:
            result["emailed"] = _send_email_channel(
                email_template,
                content_type,
                content,
            )

        return result

    @staticmethod
    def create_event_reminder(event, user, interval, title, body):
        """Create an event reminder notification if not already sent.

        Args:
            event: Event model instance.
            user: User model instance.
            interval: '24h' or '1h'.
            title: Notification title.
            body: Notification body.

        Returns:
            Notification if created, None if already sent.
        """
        from notifications.models import EventReminderLog

        # Check for existing reminder
        _, created = EventReminderLog.objects.get_or_create(
            event=event,
            user=user,
            interval=interval,
        )
        if not created:
            return None  # Already sent

        notification = Notification.objects.create(
            user=user,
            title=title,
            body=body,
            url=event.get_absolute_url(),
            notification_type='event_reminder',
        )
        return notification
