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
from django.db.models import Q

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
        'app_label': 'content',
        'model_name': 'Recording',
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


class NotificationService:
    """Service for creating notifications and dispatching to channels."""

    @staticmethod
    def notify(content_type, content_id):
        """Create on-platform notifications for eligible users and post to Slack.

        Args:
            content_type: One of 'article', 'course', 'event', 'recording',
                         'download', 'poll'.
            content_id: Primary key of the content object.
        """
        config = CONTENT_TYPE_CONFIG.get(content_type)
        if not config:
            logger.warning('Unknown content_type for notify: %s', content_type)
            return

        try:
            content = _get_content_object(content_type, content_id)
        except Exception:
            logger.exception(
                'Failed to load content for notify: %s/%s',
                content_type, content_id,
            )
            return

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

        # Post to Slack #announcements
        try:
            from notifications.services.slack_announcements import post_slack_announcement
            post_slack_announcement(content_type, content)
        except Exception:
            logger.exception(
                'Failed to post Slack announcement for %s/%s',
                content_type, content_id,
            )

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
