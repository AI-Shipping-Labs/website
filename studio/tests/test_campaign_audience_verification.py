"""Studio view + template tests for ``audience_verification`` (issue #692).

Covers form rendering, create/edit/duplicate persistence, warning copy
gating, and ``update_fields`` inclusion.
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from email_app.models import EmailCampaign

User = get_user_model()


WARNING_COPY = (
    "Warning: sending to unverified addresses may hurt deliverability. "
    "Proceed only if you understand the risk."
)


class CampaignFormAudienceVerificationRenderTest(TestCase):
    """The Studio campaign form renders the new selector and gates the warning."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email="staff@test.com",
            password="testpass",
            is_staff=True,
        )
        self.client.login(email="staff@test.com", password="testpass")

    def test_new_form_renders_selector_defaulting_to_verified_only(self):
        response = self.client.get("/studio/campaigns/new")
        self.assertEqual(response.status_code, 200)
        # The selector is present with the canonical testid.
        self.assertContains(
            response,
            'data-testid="campaign-audience-verification"',
        )
        # Verified-only is the selected option on a new campaign.
        self.assertContains(
            response,
            '<option value="verified_only" selected>Verified only</option>',
            html=True,
        )

    def test_new_form_does_not_render_warning_block(self):
        response = self.client.get("/studio/campaigns/new")
        self.assertNotContains(
            response,
            'data-testid="campaign-audience-verification-warning"',
        )
        self.assertNotContains(response, WARNING_COPY)

    def test_edit_form_verified_only_does_not_render_warning(self):
        campaign = EmailCampaign.objects.create(
            subject="V", body="b", status="draft",
            audience_verification=(
                EmailCampaign.AUDIENCE_VERIFICATION_VERIFIED_ONLY
            ),
        )
        response = self.client.get(
            f"/studio/campaigns/{campaign.pk}/edit",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response,
            'data-testid="campaign-audience-verification-warning"',
        )
        self.assertNotContains(response, WARNING_COPY)

    def test_edit_form_everyone_renders_warning_with_exact_copy(self):
        campaign = EmailCampaign.objects.create(
            subject="E", body="b", status="draft",
            audience_verification=(
                EmailCampaign.AUDIENCE_VERIFICATION_EVERYONE
            ),
        )
        response = self.client.get(
            f"/studio/campaigns/{campaign.pk}/edit",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'data-testid="campaign-audience-verification-warning"',
        )
        # Exact copy must match the spec, verbatim.
        self.assertContains(response, WARNING_COPY)
        # The "everyone" option is selected on the form.
        self.assertContains(
            response,
            (
                '<option value="everyone" selected>Everyone (including '
                'unverified)</option>'
            ),
            html=True,
        )


class CampaignCreateAudienceVerificationTest(TestCase):
    """POST /studio/campaigns/new persists audience_verification."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email="staff@test.com",
            password="testpass",
            is_staff=True,
        )
        self.client.login(email="staff@test.com", password="testpass")

    def test_create_defaults_audience_verification_to_verified_only(self):
        """Omitting the field on POST falls back to ``verified_only``."""
        self.client.post(
            "/studio/campaigns/new",
            {
                "subject": "Default",
                "body": "x",
                "target_min_level": "0",
            },
        )
        campaign = EmailCampaign.objects.get(subject="Default")
        self.assertEqual(
            campaign.audience_verification, "verified_only",
        )

    def test_create_with_everyone_persists(self):
        self.client.post(
            "/studio/campaigns/new",
            {
                "subject": "Broadcast",
                "body": "x",
                "target_min_level": "0",
                "audience_verification": "everyone",
            },
        )
        campaign = EmailCampaign.objects.get(subject="Broadcast")
        self.assertEqual(campaign.audience_verification, "everyone")

    def test_create_with_invalid_value_falls_back_to_verified_only(self):
        self.client.post(
            "/studio/campaigns/new",
            {
                "subject": "Bad",
                "body": "x",
                "target_min_level": "0",
                "audience_verification": "garbage",
            },
        )
        campaign = EmailCampaign.objects.get(subject="Bad")
        self.assertEqual(
            campaign.audience_verification, "verified_only",
        )


class CampaignEditAudienceVerificationTest(TestCase):
    """POST /studio/campaigns/<id>/edit updates audience_verification."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email="staff@test.com",
            password="testpass",
            is_staff=True,
        )
        self.client.login(email="staff@test.com", password="testpass")
        self.campaign = EmailCampaign.objects.create(
            subject="Edit me",
            body="x",
            status="draft",
            audience_verification="verified_only",
        )

    def test_edit_post_updates_audience_verification(self):
        self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/edit",
            {
                "subject": "Edit me",
                "body": "x",
                "target_min_level": "0",
                "audience_verification": "everyone",
            },
        )
        self.campaign.refresh_from_db()
        self.assertEqual(
            self.campaign.audience_verification, "everyone",
        )

    def test_edit_post_invalid_value_falls_back_to_verified_only(self):
        # Start from "everyone" to confirm the fallback writes the safe default.
        self.campaign.audience_verification = "everyone"
        self.campaign.save(update_fields=["audience_verification"])

        self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/edit",
            {
                "subject": "Edit me",
                "body": "x",
                "target_min_level": "0",
                "audience_verification": "garbage",
            },
        )
        self.campaign.refresh_from_db()
        self.assertEqual(
            self.campaign.audience_verification, "verified_only",
        )

    def test_edit_includes_audience_verification_in_update_fields(self):
        """The save() update_fields list must include audience_verification.

        Use a queryset-level update sandwich to confirm: change the value
        in-memory and persist via the view, then verify the DB reflects it.
        If ``audience_verification`` were missing from update_fields, the
        in-memory change would be discarded.
        """
        self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/edit",
            {
                "subject": "Edit me",
                "body": "x",
                "target_min_level": "0",
                "audience_verification": "everyone",
            },
        )
        # Round-trip via the DB to confirm the write actually landed.
        fresh = EmailCampaign.objects.get(pk=self.campaign.pk)
        self.assertEqual(fresh.audience_verification, "everyone")


class CampaignDuplicateAudienceVerificationTest(TestCase):
    """campaign_duplicate copies audience_verification forward."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email="staff@test.com",
            password="testpass",
            is_staff=True,
        )
        self.client.login(email="staff@test.com", password="testpass")

    def test_duplicate_carries_audience_verification_forward(self):
        original = EmailCampaign.objects.create(
            subject="Original",
            body="x",
            status="draft",
            audience_verification="everyone",
        )
        self.client.post(
            f"/studio/campaigns/{original.pk}/duplicate",
        )
        duplicate = EmailCampaign.objects.exclude(pk=original.pk).get()
        self.assertEqual(duplicate.audience_verification, "everyone")
