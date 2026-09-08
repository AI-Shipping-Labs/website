"""Maven auto-onboarding delivery, payload tolerance, and PII rules (#1565).

Covers the three P0 blockers: the enrollee actually receives a working Slack
join link, the ledger stops recording steps it could not perform, and no
placeholder course text or PII reaches an enrollee or a log line.
"""

import json
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from community.services.slack import SlackAPIError
from email_app.services.email_service import EmailService
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting, MavenEnrollmentEvent
from integrations.services.maven import _welcome_context

User = get_user_model()

WEBHOOK_URL = "/api/webhooks/maven"
SECRET = "onboarding-1565-secret"
# ``_invite_to_slack`` returns (step_status, note).
SLACK_ADDED = (MavenEnrollmentEvent.STEP_SUCCEEDED, "")


def _configure():
    for key, value in (
        ("MAVEN_ENROLLMENT_ENABLED", "true"),
        ("MAVEN_WEBHOOK_SHARED_SECRET", SECRET),
    ):
        IntegrationSetting.objects.update_or_create(key=key, defaults={"value": value})
    clear_config_cache()


def _sent(mock_send_ses):
    """Return [(email_type, subject, html)] for every SES send."""
    return [
        (call.kwargs["email_type"], call.args[1], call.args[2])
        for call in mock_send_ses.call_args_list
    ]


class MavenWebhookMixin(TestCase):
    def setUp(self):
        _configure()
        self.addCleanup(clear_config_cache)

    def post(self, payload):
        return self.client.post(
            WEBHOOK_URL,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_MAVEN_SECRET=SECRET,
        )


@patch(
    "community.services.staff_notifications.notify_maven_enrollment",
    return_value=True,
)
@patch(
    "community.services.slack.SlackCommunityService.lookup_user_by_email",
    return_value=None,
)
@patch.object(EmailService, "_send_ses", return_value="ses-message-id")
class MavenColdEnrolleeDeliveryTest(MavenWebhookMixin):
    """A brand-new enrollee gets exactly one email that can reach Slack."""

    def test_single_welcome_email_carries_the_gated_slack_join_link(
        self, send_ses, _lookup, _notify,
    ):
        response = self.post({
            "event": "user_cohort.enrolled",
            "email": "cold@example.com",
            "course": "AI Engineering Buildcamp: From RAG to Agents",
            "cohort": "Cohort 1",
        })
        self.assertEqual(response.json()["status"], "onboarded")

        sent = _sent(send_ses)
        self.assertEqual([email_type for email_type, _, _ in sent], ["maven_welcome"])

        _type, _subject, html = sent[0]
        user = User.objects.get(email="cold@example.com")
        context = _welcome_context(user, "Course", "")
        self.assertIn("https://aishippinglabs.com/community/slack", html)
        self.assertIn("/api/password-reset?token=", html)
        self.assertIn(context["sign_in_url"], html)
        # Every CTA in the email is a real absolute link, including the
        # onboarding ask, which a joining enrollee never passes otherwise
        # (sign in -> Join Slack redirects them off-site).
        self.assertIn(
            'href="https://aishippinglabs.com/onboarding/"', html,
        )
        self.assertNotIn("finish onboarding", html)

    def test_slack_step_is_skipped_with_a_visible_reason_not_succeeded(
        self, send_ses, _lookup, _notify,
    ):
        self.post({
            "event": "user_cohort.enrolled",
            "email": "cold2@example.com",
            "course": "Buildcamp",
        })
        event = MavenEnrollmentEvent.objects.get(email="cold2@example.com")
        self.assertEqual(event.slack_status, MavenEnrollmentEvent.STEP_SKIPPED)
        self.assertIn("not in the Slack workspace", event.slack_error)
        self.assertIn("welcome email", event.slack_error)
        self.assertEqual(event.welcome_status, MavenEnrollmentEvent.STEP_SUCCEEDED)


@patch(
    "community.services.staff_notifications.notify_maven_enrollment",
    return_value=True,
)
@patch(
    "community.services.slack.SlackCommunityService.lookup_user_by_email",
    return_value="U-IN-WORKSPACE",
)
@patch.object(EmailService, "_send_ses", return_value="ses-message-id")
@override_settings(
    SLACK_ENABLED=True,
    SLACK_BOT_TOKEN="xoxb-test",
    SLACK_ENVIRONMENT="development",
    SLACK_DEV_COMMUNITY_CHANNEL_IDS=["C001", "C002"],
)
class MavenSlackChannelFailureTest(MavenWebhookMixin):
    """The ledger must not claim a Slack join that never happened (#1565)."""

    @patch("community.services.slack.SlackCommunityService._api_call")
    def test_step_is_failed_not_succeeded_when_every_channel_add_errors(
        self, mock_api, _send_ses, _lookup, _notify,
    ):
        # The enrollee resolves to a Slack account, but the bot is not in
        # any community channel, so they join nothing.
        mock_api.side_effect = SlackAPIError(
            "Slack API error: not_in_channel",
            method="conversations.invite",
            error_code="not_in_channel",
        )

        self.post({
            "event": "user_cohort.enrolled",
            "email": "no-channel@example.com",
            "course": "Buildcamp",
        })

        event = MavenEnrollmentEvent.objects.get(email="no-channel@example.com")
        self.assertNotEqual(event.slack_status, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertEqual(event.slack_status, MavenEnrollmentEvent.STEP_FAILED)
        self.assertIn("joined no community channel", event.slack_error)
        self.assertIn("not_in_channel", event.slack_error)
        self.assertLessEqual(len(event.slack_error), 255)

    @patch("community.services.slack.SlackCommunityService._api_call")
    def test_step_succeeds_when_the_member_actually_joins_a_channel(
        self, mock_api, _send_ses, _lookup, _notify,
    ):
        mock_api.return_value = {"ok": True}

        self.post({
            "event": "user_cohort.enrolled",
            "email": "joined@example.com",
            "course": "Buildcamp",
        })

        event = MavenEnrollmentEvent.objects.get(email="joined@example.com")
        self.assertEqual(event.slack_status, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertEqual(event.slack_error, "")


@patch(
    "community.services.staff_notifications.notify_maven_enrollment",
    return_value=True,
)
@patch("integrations.services.maven._invite_to_slack", lambda user, actions: SLACK_ADDED)
@patch.object(EmailService, "_send_ses", return_value="ses-message-id")
class MavenPayloadToleranceTest(MavenWebhookMixin):
    def test_data_envelope_is_processed_identically_to_the_flat_payload(
        self, _send_ses, _notify,
    ):
        flat = self.post({
            "event": "user_cohort.enrolled",
            "email": "flat@example.com",
            "course": "Buildcamp",
            "cohort": "Cohort 1",
        })
        self.assertEqual(flat.json()["status"], "onboarded")
        flat_event = MavenEnrollmentEvent.objects.get(email="flat@example.com")

        wrapped = self.post({
            "event": "user_cohort.enrolled",
            "data": {
                "email": "wrapped@example.com",
                "course": "Buildcamp",
                "cohort": "Cohort 1",
            },
        })
        self.assertEqual(wrapped.json()["status"], "onboarded")
        wrapped_event = MavenEnrollmentEvent.objects.get(email="wrapped@example.com")

        self.assertTrue(User.objects.filter(email="wrapped@example.com").exists())
        self.assertEqual(wrapped_event.course, flat_event.course)
        self.assertEqual(wrapped_event.cohort, flat_event.cohort)
        self.assertEqual(
            wrapped_event.welcome_status, MavenEnrollmentEvent.STEP_SUCCEEDED,
        )

    def test_wrapped_and_flat_deliveries_for_one_person_share_an_identity(
        self, _send_ses, _notify,
    ):
        self.post({
            "event": "user_cohort.enrolled",
            "payload": {
                "email": "same@example.com",
                "course": "Buildcamp",
                "cohort": "Cohort 1",
            },
        })
        wrapped_hash = MavenEnrollmentEvent.objects.get(
            email="same@example.com"
        ).identity_hash

        redelivered = self.post({
            "event": "user_cohort.enrolled",
            "email": "same@example.com",
            "course": "Buildcamp",
            "cohort": "Cohort 1",
        })

        self.assertEqual(redelivered.json()["status"], "already_processed")
        events = MavenEnrollmentEvent.objects.filter(email="same@example.com")
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().identity_hash, wrapped_hash)

    def test_student_and_member_nesting_are_accepted_for_the_email(
        self, _send_ses, _notify,
    ):
        self.post({
            "event": "user_cohort.enrolled",
            "student": {"email": "student@example.com"},
            "course": "Buildcamp",
        })
        self.post({
            "event": "user_cohort.enrolled",
            "member": {"email": "member@example.com"},
            "course": "Buildcamp",
        })
        self.assertTrue(User.objects.filter(email="student@example.com").exists())
        self.assertTrue(User.objects.filter(email="member@example.com").exists())


@patch(
    "community.services.staff_notifications.notify_maven_enrollment",
    return_value=True,
)
@patch("integrations.services.maven._invite_to_slack", lambda user, actions: SLACK_ADDED)
@patch.object(EmailService, "_send_ses", return_value="ses-message-id")
class MavenNameCaptureTest(MavenWebhookMixin):
    def test_nested_names_are_stored_and_greet_the_enrollee_by_name(
        self, send_ses, _notify,
    ):
        self.post({
            "event": "user_cohort.enrolled",
            "user": {
                "email": "john@example.com",
                "first_name": "John",
                "last_name": "Smith",
            },
            "course": "Buildcamp",
        })
        user = User.objects.get(email="john@example.com")
        self.assertEqual(user.first_name, "John")
        self.assertEqual(user.last_name, "Smith")

        _type, _subject, html = _sent(send_ses)[0]
        self.assertIn("Hi John Smith,", html)

    def test_name_never_reaches_the_ledger_payload(self, _send_ses, _notify):
        self.post({
            "event": "user_cohort.enrolled",
            "email": "ledger@example.com",
            "first_name": "John",
            "last_name": "Smith",
            "course": "Buildcamp",
        })
        event = MavenEnrollmentEvent.objects.get(email="ledger@example.com")
        serialized = json.dumps(event.payload)
        self.assertNotIn("John", serialized)
        self.assertNotIn("Smith", serialized)

    def test_top_level_and_single_full_name_forms_are_accepted(
        self, _send_ses, _notify,
    ):
        self.post({
            "event": "user_cohort.enrolled",
            "email": "top@example.com",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "course": "Buildcamp",
        })
        top = User.objects.get(email="top@example.com")
        self.assertEqual((top.first_name, top.last_name), ("Ada", "Lovelace"))

        self.post({
            "event": "user_cohort.enrolled",
            "email": "whole@example.com",
            "full_name": "Grace Brewster Hopper",
            "course": "Buildcamp",
        })
        whole = User.objects.get(email="whole@example.com")
        self.assertEqual(whole.first_name, "Grace")
        self.assertEqual(whole.last_name, "Brewster Hopper")

    def test_an_over_long_name_is_truncated_to_the_column_width(
        self, _send_ses, _notify,
    ):
        """An unvalidated payload name must not raise a DataError (#1565).

        An over-long value would be a 500 and a Maven redelivery loop in
        production, where the column is Postgres rather than SQLite.
        """
        self.post({
            "event": "user_cohort.enrolled",
            "email": "long-name@example.com",
            "first_name": "A" * 400,
            "last_name": "B" * 400,
            "course": "Buildcamp",
        })
        user = User.objects.get(email="long-name@example.com")
        self.assertEqual(len(user.first_name), 150)
        self.assertEqual(len(user.last_name), 150)

    def test_a_name_the_member_set_themselves_is_never_overwritten(
        self, _send_ses, _notify,
    ):
        existing = User.objects.create_user(
            email="jo@example.com", password="x", first_name="Jo",
        )
        self.post({
            "event": "user_cohort.enrolled",
            "email": "jo@example.com",
            "first_name": "Johnathan",
            "last_name": "Doe",
            "course": "Buildcamp",
        })
        existing.refresh_from_db()
        self.assertEqual(existing.first_name, "Jo")
        # A blank field is still filled.
        self.assertEqual(existing.last_name, "Doe")


@patch(
    "community.services.staff_notifications.notify_maven_enrollment",
    return_value=True,
)
@patch("integrations.services.maven._invite_to_slack", lambda user, actions: SLACK_ADDED)
@patch.object(EmailService, "_send_ses", return_value="ses-message-id")
class MavenWelcomeStaffCopyTest(MavenWebhookMixin):
    """Staff gets a hidden copy of the enrollee welcome (issue #1570).

    Same mechanism and same setting as the Stripe paid-signup welcome, so an
    unset ``STAFF_SIGNUP_NOTIFY_EMAIL`` stays a clean no-op.
    """

    def _welcome_call(self, send_ses):
        calls = [
            call for call in send_ses.call_args_list
            if call.kwargs["email_type"] == "maven_welcome"
        ]
        self.assertEqual(len(calls), 1)
        return calls[0]

    def test_staff_is_bcced_when_the_signup_notify_email_is_set(
        self, send_ses, _notify,
    ):
        IntegrationSetting.objects.update_or_create(
            key="STAFF_SIGNUP_NOTIFY_EMAIL",
            defaults={"value": "staff@example.com"},
        )
        clear_config_cache()

        self.post({
            "event": "user_cohort.enrolled",
            "email": "bcc-on@example.com",
            "course": "Buildcamp",
        })

        call = self._welcome_call(send_ses)
        self.assertEqual(call.kwargs["bcc"], "staff@example.com")
        # The enrollee remains the primary recipient.
        self.assertEqual(call.args[0], "bcc-on@example.com")

    def test_no_bcc_when_the_setting_is_unset(self, send_ses, _notify):
        IntegrationSetting.objects.filter(key="STAFF_SIGNUP_NOTIFY_EMAIL").delete()
        clear_config_cache()

        self.post({
            "event": "user_cohort.enrolled",
            "email": "bcc-off@example.com",
            "course": "Buildcamp",
        })

        self.assertIsNone(self._welcome_call(send_ses).kwargs["bcc"])

    def test_a_malformed_staff_address_never_costs_the_enrollee_the_welcome(
        self, send_ses, _notify,
    ):
        """One bad BCC would make SES reject the primary To as well."""
        IntegrationSetting.objects.update_or_create(
            key="STAFF_SIGNUP_NOTIFY_EMAIL",
            defaults={"value": "not-an-email"},
        )
        clear_config_cache()

        with self.assertLogs("integrations.config", level="ERROR"):
            self.post({
                "event": "user_cohort.enrolled",
                "email": "bcc-bad@example.com",
                "course": "Buildcamp",
            })

        call = self._welcome_call(send_ses)
        self.assertIsNone(call.kwargs["bcc"])
        self.assertEqual(call.args[0], "bcc-bad@example.com")
        event = MavenEnrollmentEvent.objects.get(email="bcc-bad@example.com")
        self.assertEqual(event.welcome_status, MavenEnrollmentEvent.STEP_SUCCEEDED)


class MavenRejectionLoggingTest(MavenWebhookMixin):
    def test_missing_email_logs_key_names_only(self):
        with self.assertLogs("integrations.views.maven_webhook", level="WARNING") as logs:
            response = self.post({
                "attendee": {"contact": "sam@example.com"},
                "programme": "X",
            })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "missing_email")
        output = "\n".join(logs.output)
        self.assertIn("missing_email", output)
        self.assertIn("attendee, programme", output)
        self.assertNotIn("@", output)
        self.assertNotIn("sam", output)
        self.assertNotIn("example.com", output)

    def test_invalid_json_logs_only_shape(self):
        with self.assertLogs("integrations.views.maven_webhook", level="WARNING") as logs:
            response = self.client.post(
                WEBHOOK_URL,
                data='{"email": "leak@example.com"',
                content_type="application/json",
                HTTP_X_MAVEN_SECRET=SECRET,
            )

        self.assertEqual(response.status_code, 400)
        output = "\n".join(logs.output)
        self.assertIn("invalid_json", output)
        self.assertIn("28 bytes", output)
        self.assertIn("application/json", output)
        self.assertNotIn("leak", output)

    def test_unrecognised_event_type_logs_at_info(self):
        with self.assertLogs("integrations.views.maven_webhook", level="INFO") as logs:
            response = self.post({
                "event": "user_cohort.waitlisted",
                "email": "wait@example.com",
            })

        self.assertEqual(response.json()["status"], "ignored")
        output = "\n".join(logs.output)
        self.assertIn("user_cohort.waitlisted", output)
        self.assertNotIn("wait@example.com", output)


class MavenCourseFallbackTest(MavenWebhookMixin):
    def _render(self, course, cohort):
        user = User.objects.create_user(
            email=f"fallback-{course or 'x'}-{cohort or 'y'}@example.com",
            password="x",
            first_name="Sam",
        )
        return EmailService()._render_template(
            "maven_welcome", user, _welcome_context(user, course, cohort),
        )

    def test_cohort_is_used_when_the_payload_has_no_course(self):
        subject, body = self._render("", "Cohort 1")
        self.assertIn("Cohort 1", subject)
        self.assertNotIn("your course", subject)
        self.assertNotIn("your course", body)

    def test_neither_course_nor_cohort_falls_back_to_the_generic_subject(self):
        subject, body = self._render("", "")
        self.assertEqual(subject, "Welcome to the AI Shipping Labs community")
        self.assertNotIn("your course", body)
        self.assertIn("the course you just enrolled in", body)

    def test_course_framed_subject_is_unchanged_when_a_course_is_present(self):
        subject, body = self._render("Buildcamp", "Cohort 1")
        self.assertIn("You're enrolled in Buildcamp", subject)
        self.assertNotIn("your course", body)

    def test_missing_course_label_warns_with_key_names_only(self):
        with self.assertLogs("integrations.services.maven", level="WARNING") as logs:
            self.post({
                "event": "user_cohort.enrolled",
                "email": "nolabel@example.com",
                "programme": "X",
            })

        output = "\n".join(
            line for line in logs.output if "no course or cohort" in line
        )
        self.assertIn("email, event, programme", output)
        self.assertNotIn("nolabel", output)
        self.assertNotIn("@example.com", output)


class MavenCourseChannelTest(MavenWebhookMixin):
    """The optional MAVEN_COURSE_SLACK_CHANNEL setting.

    It ships unset and is filled in from Studio afterwards, so the unset
    case is the one that must read cleanly.
    """

    def _render(self, email, channel):
        user = User.objects.create_user(email=email, password="x", first_name="Sam")
        if channel:
            IntegrationSetting.objects.update_or_create(
                key="MAVEN_COURSE_SLACK_CHANNEL", defaults={"value": channel},
            )
        else:
            IntegrationSetting.objects.filter(
                key="MAVEN_COURSE_SLACK_CHANNEL"
            ).delete()
        clear_config_cache()
        _subject, body = EmailService()._render_template(
            "maven_welcome", user, _welcome_context(user, "Buildcamp", "Cohort 1"),
        )
        return body

    def test_channel_is_named_when_the_setting_is_configured(self):
        body = self._render("chan-on@example.com", "#ai-engineering-buildcamp")

        self.assertIn("compare notes in #ai-engineering-buildcamp.", body)

    def test_the_sentence_reads_cleanly_when_the_setting_is_unset(self):
        body = self._render("chan-off@example.com", "")

        self.assertIn("compare notes.", body)
        self.assertNotIn("compare notes in .", body)
        self.assertNotIn("compare notes  ", body)
        # No dangling preposition and no empty parentheses anywhere.
        self.assertNotIn(" in .", body)
        self.assertNotIn("()", body)

    def test_the_main_benefits_match_the_authoritative_tier_records(self):
        """The listed benefits are the Main tier benefits (content tiers.yaml).

        Issue #1593 rewrote the list from bullets into a single sentence, so
        the assertion is case-insensitive: what matters is that every Main
        benefit is still named, not how the sentence capitalizes it.
        """
        body = self._render("benefits@example.com", "").lower()

        for benefit in (
            "community sprints",
            "live events",
            "personalized onboarding plan",
            "topic voting",
        ):
            self.assertIn(benefit, body)


class ReplayCommandGuardTest(TestCase):
    def setUp(self):
        IntegrationSetting.objects.update_or_create(
            key="MAVEN_ENROLLMENT_ENABLED", defaults={"value": "true"},
        )
        clear_config_cache()
        self.addCleanup(clear_config_cache)

    def test_missing_course_and_cohort_raise_command_error_and_send_nothing(self):
        with patch.object(EmailService, "_send_ses") as send_ses:
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    "replay_maven_event",
                    "--event", "user_cohort.enrolled",
                    "--email", "real@person.com",
                    stdout=StringIO(),
                )
        message = str(ctx.exception)
        self.assertIn("--course", message)
        self.assertIn("--cohort", message)
        send_ses.assert_not_called()
        self.assertFalse(User.objects.filter(email="real@person.com").exists())

    def test_missing_cohort_alone_names_only_that_flag(self):
        with self.assertRaises(CommandError) as ctx:
            call_command(
                "replay_maven_event",
                "--event", "user_cohort.enrolled",
                "--email", "real@person.com",
                "--course", "Buildcamp",
                stdout=StringIO(),
            )
        self.assertIn("--cohort", str(ctx.exception))
        self.assertNotIn("--course", str(ctx.exception))

    @patch(
        "community.services.staff_notifications.notify_maven_enrollment",
        return_value=True,
    )
    @patch("integrations.services.maven._invite_to_slack", lambda user, actions: SLACK_ADDED)
    @patch.object(EmailService, "_send_ses", return_value="ses-message-id")
    def test_real_run_prints_resolved_recipient_course_and_cohort(
        self, _send_ses, _notify,
    ):
        out = StringIO()
        call_command(
            "replay_maven_event",
            "--event", "user_cohort.enrolled",
            "--email", "replay@example.com",
            "--course", "Buildcamp",
            "--cohort", "Cohort 1",
            stdout=out,
        )
        output = out.getvalue()
        self.assertIn("REAL RUN", output)
        self.assertIn("Recipient: replay@example.com", output)
        self.assertIn("Course: Buildcamp", output)
        self.assertIn("Cohort: Cohort 1", output)
        self.assertLess(output.index("Recipient:"), output.index("Status:"))
