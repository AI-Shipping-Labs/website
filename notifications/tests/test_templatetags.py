"""Tests for notification template tags."""

from django.contrib.auth import get_user_model
from django.template import Template, Context
from django.test import TestCase, RequestFactory

from notifications.models import Notification

User = get_user_model()


class UnreadNotificationCountTagTest(TestCase):
    """Tests for the {% unread_notification_count %} template tag."""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            email='testuser@example.com', password='testpass123',
        )

    def _render(self, request):
        template = Template(
            '{% load notification_tags %}'
            '{% unread_notification_count as count %}'
            '{{ count }}'
        )
        context = Context({'request': request})
        return template.render(context)

    def test_returns_zero_for_anonymous_user(self):
        from django.contrib.auth.models import AnonymousUser
        request = self.factory.get('/')
        request.user = AnonymousUser()
        result = self._render(request)
        self.assertEqual(result.strip(), '0')

    def test_returns_unread_count_for_authenticated_user(self):
        Notification.objects.create(user=self.user, title='N1')
        Notification.objects.create(user=self.user, title='N2')
        Notification.objects.create(user=self.user, title='N3', read=True)
        request = self.factory.get('/')
        request.user = self.user
        result = self._render(request)
        self.assertEqual(result.strip(), '2')

    def test_returns_zero_when_no_notifications(self):
        request = self.factory.get('/')
        request.user = self.user
        result = self._render(request)
        self.assertEqual(result.strip(), '0')
