"""Tests for campaign test-send recipient suggestion chips (issue #921).

Covers the backend that builds the You / Recent / Common suggestion
list, the session-persisted "recently sent" memory, and the validation
+ de-dup + cap rules. The click-to-fill JS behaviour itself is verified
by Playwright (playwright_tests/test_studio_campaign_test_suggestions.py)
per the testing guidelines — these TestCases never assert on the inline
script.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from email_app.models import EmailCampaign
from studio.views.campaigns import RECENT_TEST_RECIPIENTS_SESSION_KEY

User = get_user_model()


class CampaignTestRecipientSuggestionsTest(TestCase):
    """The campaign detail page surfaces click-to-fill suggestion chips."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email="admin@test.com",
            password="testpass",
            is_staff=True,
        )
        self.client.login(email="admin@test.com", password="testpass")
        self.campaign = EmailCampaign.objects.create(
            subject="Suggestion Campaign",
            body="Body",
        )

    def _detail(self):
        return self.client.get(f"/studio/campaigns/{self.campaign.pk}/")

    def _suggestion_emails(self, response):
        return [s["email"] for s in response.context["test_recipient_suggestions"]]

    # --- Source 1: operator's own email -----------------------------------

    def test_own_email_is_first_suggestion(self):
        response = self._detail()
        emails = self._suggestion_emails(response)
        self.assertTrue(emails, "expected at least one suggestion")
        self.assertEqual(emails[0], "admin@test.com")
        self.assertEqual(
            response.context["test_recipient_suggestions"][0]["label"], "You"
        )

    def test_chips_render_in_template(self):
        response = self._detail()
        self.assertContains(response, 'data-testid="test-recipient-suggestions"')
        self.assertContains(response, 'data-email="admin@test.com"')

    # --- Source 3: configured common recipients ---------------------------

    @override_settings(CAMPAIGN_TEST_RECIPIENTS="seed@example.com, team@example.com")
    def test_configured_common_addresses_appear(self):
        response = self._detail()
        emails = self._suggestion_emails(response)
        self.assertIn("seed@example.com", emails)
        self.assertIn("team@example.com", emails)
        labels = {
            s["email"]: s["label"]
            for s in response.context["test_recipient_suggestions"]
        }
        self.assertEqual(labels["seed@example.com"], "Common")

    @override_settings(CAMPAIGN_TEST_RECIPIENTS="alpha@x.com;beta@x.com gamma@x.com\ndelta@x.com")
    def test_config_split_on_all_separators(self):
        response = self._detail()
        emails = self._suggestion_emails(response)
        for addr in ("alpha@x.com", "beta@x.com", "gamma@x.com", "delta@x.com"):
            self.assertIn(addr, emails)

    @override_settings(CAMPAIGN_TEST_RECIPIENTS="not-an-email, ok@example.com")
    def test_invalid_config_value_is_dropped_not_errored(self):
        response = self._detail()
        self.assertEqual(response.status_code, 200)
        emails = self._suggestion_emails(response)
        self.assertIn("ok@example.com", emails)
        self.assertNotIn("not-an-email", emails)

    # --- De-dup + cap -----------------------------------------------------

    @override_settings(CAMPAIGN_TEST_RECIPIENTS="ADMIN@test.com, dup@x.com, Dup@x.com")
    def test_dedup_is_case_insensitive_first_occurrence_wins(self):
        response = self._detail()
        emails = self._suggestion_emails(response)
        # Own email already present as admin@test.com — the cased config
        # duplicate must not add a second entry.
        self.assertEqual(
            [e for e in emails if e.casefold() == "admin@test.com"],
            ["admin@test.com"],
        )
        self.assertEqual([e for e in emails if e.casefold() == "dup@x.com"], ["dup@x.com"])

    @override_settings(
        CAMPAIGN_TEST_RECIPIENTS=(
            "c1@x.com c2@x.com c3@x.com c4@x.com c5@x.com "
            "c6@x.com c7@x.com c8@x.com c9@x.com c10@x.com"
        )
    )
    def test_suggestions_capped_at_eight(self):
        response = self._detail()
        emails = self._suggestion_emails(response)
        self.assertEqual(len(emails), 8)
        # Own email is first, so the last configured entries are trimmed.
        self.assertEqual(emails[0], "admin@test.com")

    # --- No suggestions: render nothing -----------------------------------

    def test_no_suggestions_renders_no_chip_container(self):
        # Operator with no email, empty config, fresh session -> nothing.
        self.staff.email = ""
        self.staff.save(update_fields=["email"])
        response = self._detail()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["test_recipient_suggestions"], [])
        self.assertNotContains(response, 'data-testid="test-recipient-suggestions"')


class CampaignRecentTestRecipientsTest(TestCase):
    """A successful test send remembers the sent addresses in the session."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email="admin@test.com",
            password="testpass",
            is_staff=True,
        )
        self.client.login(email="admin@test.com", password="testpass")
        self.campaign = EmailCampaign.objects.create(
            subject="Recent Campaign",
            body="Body",
        )

    @patch("studio.views.campaigns.EmailService")
    def test_successful_send_records_recent_recipient(self, MockService):
        mock_service = MockService.return_value
        mock_service.render_markdown_email.return_value = "<html>x</html>"
        mock_service._send_ses.return_value = "ses-id"

        self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/test-send",
            {"test_recipients": "qa@example.com"},
        )

        session = self.client.session
        self.assertEqual(
            session.get(RECENT_TEST_RECIPIENTS_SESSION_KEY), ["qa@example.com"]
        )

    @patch("studio.views.campaigns.EmailService")
    def test_recent_recipient_appears_as_chip_on_next_visit(self, MockService):
        mock_service = MockService.return_value
        mock_service.render_markdown_email.return_value = "<html>x</html>"
        mock_service._send_ses.return_value = "ses-id"

        self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/test-send",
            {"test_recipients": "qa@example.com"},
        )

        response = self.client.get(f"/studio/campaigns/{self.campaign.pk}/")
        chips = {
            s["email"]: s["label"]
            for s in response.context["test_recipient_suggestions"]
        }
        self.assertIn("qa@example.com", chips)
        self.assertEqual(chips["qa@example.com"], "Recent")

    @patch("studio.views.campaigns.EmailService")
    def test_recent_list_is_capped_at_five_most_recent_first(self, MockService):
        mock_service = MockService.return_value
        mock_service.render_markdown_email.return_value = "<html>x</html>"
        mock_service._send_ses.return_value = "ses-id"

        for i in range(1, 7):
            self.client.post(
                f"/studio/campaigns/{self.campaign.pk}/test-send",
                {"test_recipients": f"r{i}@example.com"},
            )

        recent = self.client.session.get(RECENT_TEST_RECIPIENTS_SESSION_KEY)
        self.assertEqual(
            recent,
            ["r6@example.com", "r5@example.com", "r4@example.com",
             "r3@example.com", "r2@example.com"],
        )

    @patch("studio.views.campaigns.EmailService")
    def test_resending_existing_recent_dedups_case_insensitively(self, MockService):
        mock_service = MockService.return_value
        mock_service.render_markdown_email.return_value = "<html>x</html>"
        mock_service._send_ses.return_value = "ses-id"

        self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/test-send",
            {"test_recipients": "qa@example.com"},
        )
        self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/test-send",
            {"test_recipients": "QA@example.com"},
        )

        recent = self.client.session.get(RECENT_TEST_RECIPIENTS_SESSION_KEY)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].casefold(), "qa@example.com")

    @patch("studio.views.campaigns.EmailService")
    def test_failed_send_does_not_record_recent(self, MockService):
        from email_app.services.email_service import EmailServiceError

        mock_service = MockService.return_value
        mock_service.render_markdown_email.return_value = "<html>x</html>"
        mock_service._send_ses.side_effect = EmailServiceError("boom")

        self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/test-send",
            {"test_recipients": "fail@example.com"},
        )

        self.assertEqual(
            self.client.session.get(RECENT_TEST_RECIPIENTS_SESSION_KEY), None
        )
