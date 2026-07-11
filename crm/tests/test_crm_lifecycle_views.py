"""Studio CRM account-lifecycle reporting tests."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from crm.models import CRMRecord

User = get_user_model()


class CRMLifecycleViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com",
            password="pw",
            is_staff=True,
        )
        cls.newsletter = User.objects.create_user(
            email="newsletter@test.com",
            password="pw",
            signup_source="newsletter",
            account_activated=False,
        )
        cls.newsletter_record = CRMRecord.objects.create(user=cls.newsletter)
        cls.full = User.objects.create_user(
            email="full@test.com",
            password="pw",
            signup_source="signup",
            account_activated=True,
        )
        cls.full_record = CRMRecord.objects.create(user=cls.full)
        cls.untracked_newsletter = User.objects.create_user(
            email="not-tracked@test.com",
            password="pw",
            signup_source="newsletter",
            account_activated=False,
        )

    def setUp(self):
        self.client.login(email="staff@test.com", password="pw")

    def test_crm_list_filters_existing_records_by_lifecycle(self):
        response = self.client.get(
            "/studio/crm/?account_lifecycle=newsletter_only",
        )
        self.assertEqual(response.status_code, 200)
        emails = {row["email"] for row in response.context["rows"]}
        self.assertEqual(emails, {"newsletter@test.com"})
        self.assertEqual(CRMRecord.objects.count(), 2)
        self.assertContains(response, 'data-testid="crm-list-lifecycle-pill"')
        self.assertContains(response, 'data-lifecycle="newsletter_only"')

    def test_crm_detail_shows_lifecycle_context(self):
        response = self.client.get(f"/studio/crm/{self.newsletter_record.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="crm-detail-lifecycle-pill"')
        self.assertContains(response, 'data-lifecycle="newsletter_only"')
        self.assertContains(response, "Newsletter-only")
