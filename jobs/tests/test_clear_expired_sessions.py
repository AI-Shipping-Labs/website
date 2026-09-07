from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import AccountSession, User
from accounts.session_backend import SessionStore
from jobs.tasks.cleanup import (
    EXPIRED_SESSION_BATCH_SIZE,
    EXPIRED_SESSION_TIME_BUDGET_SECONDS,
    clear_expired_sessions,
)


def _session(*, key, expire_date, account_id=None, data=None):
    encoded = SessionStore().encode(data or {})
    return AccountSession.objects.create(
        session_key=key,
        session_data=encoded,
        expire_date=expire_date,
        account_id=account_id,
    )


@tag("core")
class ClearExpiredSessionsTaskTest(TestCase):
    def test_batch_and_budget_constants(self):
        self.assertEqual(EXPIRED_SESSION_BATCH_SIZE, 1000)
        self.assertEqual(EXPIRED_SESSION_TIME_BUDGET_SECONDS, 240)

    def test_deletes_only_expired_rows_without_decoding(self):
        now = timezone.now()
        expired = _session(
            key="expired-session-key-0000000000000001",
            expire_date=now - timedelta(hours=1),
            account_id=7,
        )
        live = _session(
            key="live-session-key-0000000000000000001",
            expire_date=now + timedelta(days=1),
            account_id=8,
        )

        with patch.object(AccountSession, "get_decoded") as decoded:
            result = clear_expired_sessions()

        decoded.assert_not_called()
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["remaining_expired"], 0)
        self.assertFalse(AccountSession.objects.filter(pk=expired.pk).exists())
        self.assertTrue(AccountSession.objects.filter(pk=live.pk).exists())

    def test_stops_at_wall_clock_budget_and_later_run_continues(self):
        now = timezone.now()
        past = now - timedelta(hours=1)
        AccountSession.objects.bulk_create(
            [
                AccountSession(
                    session_key=f"exp{i:037d}",
                    session_data=".",
                    expire_date=past,
                )
                for i in range(1001)
            ]
        )
        times = iter([0.0, 0.0, 241.0, 241.0])

        with patch(
            "jobs.tasks.cleanup.monotonic",
            side_effect=lambda: next(times, 241.0),
        ):
            first = clear_expired_sessions()

        self.assertEqual(first["deleted"], 1000)
        self.assertEqual(first["remaining_expired"], 1)
        self.assertEqual(AccountSession.objects.count(), 1)

        second = clear_expired_sessions()
        self.assertEqual(second["deleted"], 1)
        self.assertEqual(second["remaining_expired"], 0)
        self.assertEqual(AccountSession.objects.count(), 0)

    def test_backfills_unexpired_authenticated_null_account_id(self):
        user = User.objects.create_user(email="session-backfill@test.com")
        now = timezone.now()
        _session(
            key="auth-null-account-id-00000000000000001",
            expire_date=now + timedelta(days=1),
            data={"_auth_user_id": str(user.pk)},
        )
        _session(
            key="anon-null-account-id-00000000000000001",
            expire_date=now + timedelta(days=1),
            data={"anon": True},
        )

        result = clear_expired_sessions()

        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["backfilled"], 1)
        self.assertEqual(
            AccountSession.objects.get(
                session_key="auth-null-account-id-00000000000000001",
            ).account_id,
            user.pk,
        )
        self.assertIsNone(
            AccountSession.objects.get(
                session_key="anon-null-account-id-00000000000000001",
            ).account_id,
        )
