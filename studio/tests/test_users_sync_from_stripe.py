from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class StudioUserSyncFromStripeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com",
            password="testpass",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member@test.com",
            password="testpass",
            stripe_customer_id="cus_member",
        )

    def test_anonymous_redirected(self):
        response = self.client.post(
            f"/studio/users/{self.member.pk}/sync-from-stripe/"
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_non_staff_403(self):
        regular = User.objects.create_user(
            email="regular@test.com",
            password="testpass",
            is_staff=False,
        )
        self.client.login(email=regular.email, password="testpass")

        response = self.client.post(
            f"/studio/users/{self.member.pk}/sync-from-stripe/"
        )

        self.assertEqual(response.status_code, 403)

    def test_post_calls_backfill_for_user(self):
        self.client.login(email=self.staff.email, password="testpass")

        with patch(
            "studio.views.users.backfill_user_from_stripe"
        ) as backfill:
            backfill.return_value.status = "changed"
            backfill.return_value.message = "changed: free -> main"
            response = self.client.post(
                f"/studio/users/{self.member.pk}/sync-from-stripe/"
            )

        self.assertRedirects(response, f"/studio/users/{self.member.pk}/")
        backfill.assert_called_once()
        called_user = backfill.call_args.args[0]
        self.assertEqual(called_user.pk, self.member.pk)

    def test_post_get_405(self):
        self.client.login(email=self.staff.email, password="testpass")

        response = self.client.get(
            f"/studio/users/{self.member.pk}/sync-from-stripe/"
        )

        self.assertEqual(response.status_code, 405)
