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
from email_app.services.campaign_test_send import send_campaign_test_recipient
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

    @patch("studio.views.campaigns._send_studio_test_campaign", return_value="relay-id")
    def test_successful_send_records_recent_recipient(self, _send):
        self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/test-send",
            {"test_recipients": "qa@example.com"},
        )

        self.assertEqual(
            self.client.session.get(RECENT_TEST_RECIPIENTS_SESSION_KEY),
            ["qa@example.com"],
        )

    @patch("studio.views.campaigns._send_studio_test_campaign", return_value="relay-id")
    def test_recent_recipient_appears_as_chip_on_next_visit(self, _send):
        self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/test-send",
            {"test_recipients": "qa@example.com"},
        )

        response = self.client.get(f"/studio/campaigns/{self.campaign.pk}/")
        chips = {
            suggestion["email"]: suggestion["label"]
            for suggestion in response.context["test_recipient_suggestions"]
        }
        self.assertEqual(chips["qa@example.com"], "Recent")

    @patch("studio.views.campaigns._send_studio_test_campaign", return_value="relay-id")
    def test_recent_list_is_capped_at_five_most_recent_first(self, _send):
        for index in range(1, 7):
            self.client.post(
                f"/studio/campaigns/{self.campaign.pk}/test-send",
                {"test_recipients": f"r{index}@example.com"},
            )

        self.assertEqual(
            self.client.session.get(RECENT_TEST_RECIPIENTS_SESSION_KEY),
            ["r6@example.com", "r5@example.com", "r4@example.com",
             "r3@example.com", "r2@example.com"],
        )

    @patch("studio.views.campaigns._send_studio_test_campaign", return_value="relay-id")
    def test_resending_existing_recent_dedups_case_insensitively(self, _send):
        for recipient in ("qa@example.com", "QA@example.com"):
            self.client.post(
                f"/studio/campaigns/{self.campaign.pk}/test-send",
                {"test_recipients": recipient},
            )

        recent = self.client.session.get(RECENT_TEST_RECIPIENTS_SESSION_KEY)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].casefold(), "qa@example.com")

    @patch(
        "studio.views.campaigns._send_studio_test_campaign",
        side_effect=RuntimeError("relay unavailable"),
    )
    def test_failed_send_does_not_record_recent(self, _send):
        self.client.post(
            f"/studio/campaigns/{self.campaign.pk}/test-send",
            {"test_recipients": "fail@example.com"},
        )

        self.assertIsNone(
            self.client.session.get(RECENT_TEST_RECIPIENTS_SESSION_KEY),
        )


class CampaignTestRecipientPolicyTest(TestCase):
    def test_unsubscribed_controlled_user_gets_clean_explicit_test(self):
        user = User.objects.create_user(
            email="controlled-unsubscribed@test.com",
            unsubscribed=True,
        )
        campaign = EmailCampaign.objects.create(subject="Probe", body="Hello")
        captured = {}

        def send(*, message, campaign_id):
            captured.update(message=message, campaign_id=campaign_id)
            return "relay-test-id"

        result = send_campaign_test_recipient(campaign, user.email, send=send)

        self.assertEqual(result, "relay-test-id")
        self.assertEqual(captured["campaign_id"], campaign.pk)
        message = captured["message"]
        self.assertEqual(message.recipient, user.email)
        self.assertEqual(message.subject, "[TEST] Probe")
        self.assertEqual(message.body_markdown, "Hello")
        self.assertEqual(message.unsubscribe_url, "")
        self.assertEqual(message.footer_note, "")
        self.assertIsNone(message.skip_reason)
