"""The session row moved app label without changing the table (A3.2, #1692)."""

from django.conf import settings
from django.test import TestCase

from accounts_ext.models import AccountSession


class AccountSessionMoveTest(TestCase):
    def test_session_model_keeps_the_unmanaged_django_session_table(self):
        self.assertEqual(AccountSession._meta.db_table, "django_session")
        self.assertEqual(AccountSession._meta.app_label, "accounts_ext")
        self.assertFalse(AccountSession._meta.managed)

    def test_session_engine_points_at_the_moved_backend(self):
        self.assertEqual(settings.SESSION_ENGINE, "accounts_ext.session_backend")
