"""Per-token staff API rate limit and concurrency cap (issue #1854)."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.models import Token
from accounts.services import staff_api_throttle
from accounts.services.staff_api_throttle import (
    reset_staff_api_throttle,
    staff_api_slot,
)

User = get_user_model()

CLOCK = "accounts.services.staff_api_throttle.time.monotonic"


class StaffApiThrottleTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="ops@test.com", password="x", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="agent")
        cls.other_token = Token.objects.create(user=cls.staff, name="laptop")

    def setUp(self):
        reset_staff_api_throttle()
        self.addCleanup(reset_staff_api_throttle)

    def _get(self, token=None):
        token = token or self.token
        return self.client.get(
            f"/api/users/{self.staff.email}",
            HTTP_AUTHORIZATION=f"Token {token.key}",
        )

    def _served(self, response):
        """True when the user-detail view ran and returned the staff user."""
        return response.json().get("email") == self.staff.email


@override_settings(
    STAFF_API_RATE_LIMIT_PER_MINUTE=6,
    STAFF_API_MAX_CONCURRENT_PER_TOKEN=0,
)
class StaffApiRateLimitTest(StaffApiThrottleTestBase):
    """6/min refills one request every 10 s with a burst of one."""

    def test_over_rate_returns_429_with_retry_after_before_the_view(self):
        with mock.patch(CLOCK, return_value=1000.0):
            self.assertTrue(self._served(self._get()))
            used_at = Token.objects.get(pk=self.token.pk).last_used_at
            response = self._get()

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "10")
        self.assertEqual(response.json()["code"], "rate_limited")
        # Rejected before authentication side effects or the view ran.
        self.assertEqual(Token.objects.get(pk=self.token.pk).last_used_at, used_at)

    def test_bucket_refills_over_time(self):
        with mock.patch(CLOCK, return_value=1000.0):
            self._get()
            self.assertEqual(self._get().status_code, 429)
        with mock.patch(CLOCK, return_value=1010.0):
            self.assertTrue(self._served(self._get()))

    def test_limit_is_per_token(self):
        with mock.patch(CLOCK, return_value=1000.0):
            self._get()
            self.assertEqual(self._get().status_code, 429)
            self.assertTrue(self._served(self._get(self.other_token)))

    @override_settings(STAFF_API_RATE_LIMIT_PER_MINUTE=0)
    def test_zero_disables_the_rate_limit(self):
        with mock.patch(CLOCK, return_value=1000.0):
            served = [self._served(self._get()) for _ in range(5)]
        self.assertEqual(served, [True] * 5)


@override_settings(
    STAFF_API_RATE_LIMIT_PER_MINUTE=0,
    STAFF_API_MAX_CONCURRENT_PER_TOKEN=1,
)
class StaffApiConcurrencyCapTest(StaffApiThrottleTestBase):
    def test_request_over_the_cap_gets_429_until_the_slot_frees(self):
        with staff_api_slot(self.token.key) as held:
            self.assertIsNone(held)
            with mock.patch("accounts.auth.Token.authenticate") as authenticate:
                response = self._get()

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "1")
        self.assertEqual(
            response.json()["code"], "too_many_concurrent_requests",
        )
        authenticate.assert_not_called()
        self.assertIsNone(Token.objects.get(pk=self.token.pk).last_used_at)
        self.assertTrue(self._served(self._get()))

    @override_settings(STAFF_API_RATE_LIMIT_PER_MINUTE=6)
    def test_bucket_bound_never_evicts_an_active_credential(self):
        with mock.patch.object(
            staff_api_throttle,
            "MAX_TRACKED_CREDENTIALS",
            1,
        ), staff_api_slot(self.token.key), staff_api_slot("different-key"):
            with staff_api_slot(self.token.key) as rejection:
                self.assertEqual(rejection.status_code, 429)

        self.assertEqual(staff_api_throttle._in_flight, {})

    def test_slot_is_released_when_the_view_raises(self):
        with self.assertRaises(RuntimeError):
            with staff_api_slot(self.token.key):
                raise RuntimeError("view failed")

        self.assertTrue(self._served(self._get()))


@override_settings(TESTING=False)
class StaffApiThrottleDefaultsTest(StaffApiThrottleTestBase):
    def test_defaults_allow_two_concurrent_requests_per_token(self):
        with staff_api_slot(self.token.key), staff_api_slot(self.token.key):
            self.assertEqual(self._get().status_code, 429)
            self.assertTrue(self._served(self._get(self.other_token)))

    def test_default_rate_allows_a_burst_of_twenty(self):
        with mock.patch(CLOCK, return_value=1000.0):
            responses = [self._get() for _ in range(21)]

        served = [self._served(response) for response in responses[:20]]
        self.assertEqual(served, [True] * 20)
        self.assertEqual(responses[20].status_code, 429)

    def test_invalid_concurrency_values_warn_and_keep_the_safe_default(self):
        for invalid in ("", "many", -1):
            with self.subTest(value=invalid), override_settings(
                STAFF_API_RATE_LIMIT_PER_MINUTE=0,
                STAFF_API_MAX_CONCURRENT_PER_TOKEN=invalid,
            ):
                with self.assertLogs(
                    "accounts.services.staff_api_throttle",
                    level="WARNING",
                ) as captured:
                    with staff_api_slot(self.token.key), staff_api_slot(
                        self.token.key,
                    ):
                        response = self._get()
                self.assertEqual(response.status_code, 429)
                self.assertIn(
                    "STAFF_API_MAX_CONCURRENT_PER_TOKEN",
                    captured.output[0],
                )

    def test_invalid_rate_values_warn_and_keep_the_safe_default(self):
        for invalid in ("", "many", -1):
            with self.subTest(value=invalid), override_settings(
                STAFF_API_RATE_LIMIT_PER_MINUTE=invalid,
                STAFF_API_MAX_CONCURRENT_PER_TOKEN=0,
            ), mock.patch(CLOCK, return_value=1000.0):
                reset_staff_api_throttle()
                with self.assertLogs(
                    "accounts.services.staff_api_throttle",
                    level="WARNING",
                ) as captured:
                    rejections = []
                    for _ in range(21):
                        with staff_api_slot(self.token.key) as rejection:
                            rejections.append(rejection)
                self.assertTrue(all(item is None for item in rejections[:20]))
                self.assertEqual(rejections[20].status_code, 429)
                self.assertIn(
                    "STAFF_API_RATE_LIMIT_PER_MINUTE",
                    captured.output[0],
                )

    @override_settings(
        STAFF_API_RATE_LIMIT_PER_MINUTE=6,
        STAFF_API_MAX_CONCURRENT_PER_TOKEN=0,
    )
    def test_unique_credentials_use_bounded_expiring_digest_state(self):
        with mock.patch.object(
            staff_api_throttle,
            "MAX_TRACKED_CREDENTIALS",
            2,
        ), mock.patch(CLOCK, return_value=1000.0):
            for credential in ("invalid-one", "invalid-two", "invalid-three"):
                with staff_api_slot(credential):
                    pass

        self.assertEqual(len(staff_api_throttle._buckets), 2)
        self.assertTrue(
            all(isinstance(key, bytes) for key in staff_api_throttle._buckets)
        )
        with mock.patch(CLOCK, return_value=1011.0):
            with staff_api_slot("invalid-four"):
                pass
        self.assertEqual(len(staff_api_throttle._buckets), 1)
