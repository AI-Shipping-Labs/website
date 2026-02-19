"""Template tags for notifications."""

from django import template

from notifications.models import Notification

register = template.Library()


@register.simple_tag(takes_context=True)
def unread_notification_count(context):
    """Return the unread notification count for the current user.

    Usage in templates:
        {% load notification_tags %}
        {% unread_notification_count as unread_count %}
        {{ unread_count }}
    """
    request = context.get('request')
    if not request or not hasattr(request, 'user') or not request.user.is_authenticated:
        return 0
    return Notification.objects.filter(user=request.user, read=False).count()
