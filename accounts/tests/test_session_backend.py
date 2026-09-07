from datetime import timedelta

from django.conf import settings
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import AccountSession, User


@tag("core")
class SessionBackendTest(TestCase):
    def test_session_engine_is_database_backed(self):
        self.assertEqual(settings.SESSION_ENGINE, "accounts.session_backend")
        self.assertNotIn("cache", settings.SESSION_ENGINE)
        self.assertNotIn("redis", settings.SESSION_ENGINE.lower())
        self.assertNotIn("user_sessions", settings.INSTALLED_APPS)

    def test_login_writes_queryable_account_id(self):
        user = User.objects.create_user(
            email="session-login@test.com",
            password="TestPass123!",
        )

        self.assertTrue(
            self.client.login(email=user.email, password="TestPass123!"),
        )
        row = AccountSession.objects.get(session_key=self.client.session.session_key)
        self.assertEqual(row.account_id, user.pk)
        self.assertEqual(
            AccountSession.objects.filter(account_id=user.pk).count(),
            1,
        )

    def test_anonymous_session_keeps_null_account_id(self):
        session = self.client.session
        session["anon"] = True
        session.save()

        row = AccountSession.objects.get(session_key=session.session_key)
        self.assertIsNone(row.account_id)

    def test_logout_clears_account_mapping(self):
        user = User.objects.create_user(
            email="session-logout@test.com",
            password="TestPass123!",
        )
        self.client.login(email=user.email, password="TestPass123!")
        self.client.logout()

        self.assertFalse(AccountSession.objects.filter(account_id=user.pk).exists())

    def test_cycle_key_leaves_only_current_session_mapped(self):
        user = User.objects.create_user(
            email="session-cycle@test.com",
            password="TestPass123!",
        )
        self.client.login(email=user.email, password="TestPass123!")
        session = self.client.session
        old_key = session.session_key
        session.cycle_key()
        session.save()

        self.assertNotEqual(session.session_key, old_key)
        self.assertFalse(AccountSession.objects.filter(session_key=old_key).exists())
        current = AccountSession.objects.get(session_key=session.session_key)
        self.assertEqual(current.account_id, user.pk)
        self.assertEqual(
            AccountSession.objects.filter(account_id=user.pk).count(),
            1,
        )

    def test_force_login_sets_account_id(self):
        user = User.objects.create_user(email="session-force@test.com")
        self.client.force_login(user)
        row = AccountSession.objects.get(session_key=self.client.session.session_key)
        self.assertEqual(row.account_id, user.pk)

    def test_unexpired_authenticated_row_is_filterable_without_decode(self):
        user = User.objects.create_user(email="session-filter@test.com")
        AccountSession.objects.create(
            session_key="mapped-session-key-00000000000000001",
            session_data=".",
            expire_date=timezone.now() + timedelta(days=1),
            account_id=user.pk,
        )

        found = AccountSession.objects.filter(account_id=user.pk)
        self.assertEqual(found.count(), 1)
        self.assertEqual(found.get().session_key, "mapped-session-key-00000000000000001")
