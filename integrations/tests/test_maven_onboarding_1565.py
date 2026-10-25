"""Maven auto-onboarding delivery, payload tolerance, and PII rules (#1565).

Covers the three P0 blockers: the enrollee actually receives a working Slack
join link, the ledger stops recording steps it could not perform, and no
placeholder course text or PII reaches an enrollee or a log line.
"""

import json
from io import StringIO
from unittest.mock import patch

from community_base.mail.jobs import deliver as deliver_job
from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from content.models import Cohort, Course
from email_app.package_mail import send_package_mail
from email_app.testing import StubRelayClient, deliver_pending_mail
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting, MavenEnrollmentEvent
from integrations.services.maven import MavenResult, _welcome_context

User = get_user_model()

WEBHOOK_URL = "/api/webhooks/maven"
SECRET = "onboarding-1565-secret"

def _configure():
    for key, value in (
        ("MAVEN_ENROLLMENT_ENABLED", "true"),
        ("MAVEN_WEBHOOK_SHARED_SECRET", SECRET),
    ):
        IntegrationSetting.objects.update_or_create(key=key, defaults={"value": value})
    clear_config_cache()


def _deliver_all():
    """Drain pending deliveries through a fresh stub; return the SES calls.

    Since A1.2 slice 3 the welcome is a durable ``EmailDelivery``; tests
    that assert on the provider-visible email drain the worker first.
    """
    stub = StubRelayClient()
    with patch(
        "community_base.mail.backends.relay.configured_client",
        return_value=stub,
    ):
        deliver_pending_mail()
    return stub.calls


def _drain_welcome_context(user, course):
    """Queue one welcome through the package and return Relay context."""

    delivery = send_package_mail(
        user, "maven_welcome", _welcome_context(course),
    )
    stub = StubRelayClient()
    with patch(
        "community_base.mail.backends.relay.configured_client",
        return_value=stub,
    ):
        deliver_job(None, {"delivery_id": str(delivery.id)})
    return stub.calls[0]["context"]


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
class MavenColdEnrolleeDeliveryTest(MavenWebhookMixin):
    """A brand-new enrollee gets exactly one email that can reach Slack."""

    def test_single_welcome_email_carries_the_gated_slack_join_link(
        self, _notify,
    ):
        response = self.post({
            "event": "user_cohort.enrolled",
            "email": "cold@example.com",
            "course": "AI Engineering Buildcamp: From RAG to Agents",
            "cohort": "Cohort 1",
        })
        self.assertEqual(response.json()["status"], "onboarded")

        # Exactly one durable mail purpose was queued, and the worker
        # delivers it with the gated Slack link (slice 3).
        self.assertEqual(
            list(EmailDelivery.objects.values_list("purpose", flat=True)),
            ["maven_welcome"],
        )
        context = _deliver_all()[0]["context"]
        self.assertEqual(
            context["slack_join_url"],
            "https://aishippinglabs.com/community/slack",
        )
        self.assertIn("/api/password-reset?token=", context["password_reset_url"])
        self.assertEqual(
            context["sign_in_url"], "https://aishippinglabs.com/accounts/login/",
        )
        self.assertEqual(
            context["onboarding_url"], "https://aishippinglabs.com/onboarding/",
        )

    def test_slack_step_mirrors_the_succeeded_welcome_not_a_slack_lookup(
        self, _notify,
    ):
        # Issue #1665: the slack step no longer calls the Slack API — it
        # mirrors welcome_status. A cold enrollee's welcome succeeds, so
        # slack succeeds too, carrying the join-link-delivered note.
        self.post({
            "event": "user_cohort.enrolled",
            "email": "cold2@example.com",
            "course": "Buildcamp",
        })
        event = MavenEnrollmentEvent.objects.get(email="cold2@example.com")
        self.assertEqual(event.welcome_status, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertEqual(event.slack_status, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertIn("Join link delivered via the maven_welcome email", event.slack_error)


@patch(
    "community.services.staff_notifications.notify_maven_enrollment",
    return_value=True,
)
class MavenPayloadToleranceTest(MavenWebhookMixin):
    def test_data_envelope_is_processed_identically_to_the_flat_payload(
        self, _notify,
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
        self, _notify,
    ):
        # Issue #1659: already_processed now also requires ``enrollment`` to
        # be terminal, so this needs a resolvable maven_course_key/external_key.
        course = Course.objects.create(
            title="Buildcamp", slug="buildcamp-1565-identity",
            maven_course_key="buildcamp",
        )
        Cohort.objects.create(
            course=course, external_key="cohort 1", name="Cohort 1",
            start_date="2026-01-01", end_date="2026-03-01",
        )
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
        self, _notify,
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
class MavenNameCaptureTest(MavenWebhookMixin):
    def test_nested_names_are_stored_and_greet_the_enrollee_by_name(
        self, _notify,
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

        context = _deliver_all()[0]["context"]
        self.assertEqual(context["user_name"], "John Smith")

    def test_name_never_reaches_the_ledger_payload(self, _notify):
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
        self, _notify,
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
        self, _notify,
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
        self, _notify,
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
class MavenWelcomeStaffCopyTest(MavenWebhookMixin):
    """Staff gets a hidden copy of the enrollee welcome (issue #1570).

    Same mechanism and same setting as the Stripe paid-signup welcome, so an
    unset ``STAFF_SIGNUP_NOTIFY_EMAIL`` stays a clean no-op.
    """

    def _welcome_call(self):
        calls = _deliver_all()
        self.assertEqual(len(calls), 1)
        return calls[0]

    def test_staff_is_bcced_when_the_signup_notify_email_is_set(
        self, _notify,
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

        call = self._welcome_call()
        delivery = call["delivery"]
        self.assertEqual(delivery.transport_options["bcc"], ["staff@example.com"])
        # The enrollee remains the primary recipient.
        self.assertEqual(delivery.recipient_email, "bcc-on@example.com")

    def test_no_bcc_when_the_setting_is_unset(self, _notify):
        IntegrationSetting.objects.filter(key="STAFF_SIGNUP_NOTIFY_EMAIL").delete()
        clear_config_cache()

        self.post({
            "event": "user_cohort.enrolled",
            "email": "bcc-off@example.com",
            "course": "Buildcamp",
        })

        self.assertNotIn("bcc", self._welcome_call()["delivery"].transport_options)

    def test_a_malformed_staff_address_never_costs_the_enrollee_the_welcome(
        self, _notify,
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

        call = self._welcome_call()
        self.assertNotIn("bcc", call["delivery"].transport_options)
        self.assertEqual(call["delivery"].recipient_email, "bcc-bad@example.com")
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
    def _render(self, course):
        user = User.objects.create_user(
            email=f"fallback-{course or 'x'}@example.com",
            password="x",
            first_name="Sam",
        )
        delivery = send_package_mail(
            user, "maven_welcome", _welcome_context(course),
        )
        stub = StubRelayClient()
        with patch(
            "community_base.mail.backends.relay.configured_client",
            return_value=stub,
        ):
            deliver_job(None, {"delivery_id": str(delivery.id)})
        return stub.calls[0]["context"]

    def test_no_course_name_falls_back_to_the_generic_subject(self):
        # Issue #1682 removed the cohort fallback that used to frame this
        # subject with a Maven cohort label, so an empty course name is
        # now the generic-copy case rather than "You're enrolled in
        # Cohort 1". The cohort label has no route into this context.
        context = self._render("")
        self.assertEqual(context["course_name"], "")
        self.assertNotIn("Cohort 1", str(context))

    def test_course_framed_subject_is_unchanged_when_a_course_is_present(self):
        context = self._render("Buildcamp")
        self.assertEqual(context["course_name"], "Buildcamp")

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
        return _drain_welcome_context(user, "Buildcamp")

    def test_channel_is_named_when_the_setting_is_configured(self):
        context = self._render("chan-on@example.com", "#ai-engineering-buildcamp")

        self.assertEqual(context["course_channel"], "#ai-engineering-buildcamp")

    def test_the_sentence_reads_cleanly_when_the_setting_is_unset(self):
        context = self._render("chan-off@example.com", "")

        self.assertEqual(context["course_channel"], "")

class ReplayCommandGuardTest(TestCase):
    def setUp(self):
        IntegrationSetting.objects.update_or_create(
            key="MAVEN_ENROLLMENT_ENABLED", defaults={"value": "true"},
        )
        clear_config_cache()
        self.addCleanup(clear_config_cache)

    def test_missing_course_and_cohort_raise_command_error_and_send_nothing(self):
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
        self.assertFalse(EmailDelivery.objects.exists())
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

    def test_confirmation_is_complete_before_dry_or_real_handler_runs(self):
        body = {
            "data": {
                "event": "user_cohort.enrolled",
                "email": "before-handler@example.com",
                "course": "Before Handler Course",
                "cohort": "Before Handler Cohort",
            },
        }
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                out = StringIO()

                def assert_banner_before_handler(payload, *, dry_run):
                    self.assertEqual(payload["email"], "before-handler@example.com")
                    self.assertEqual(payload["course"], "Before Handler Course")
                    self.assertEqual(payload["cohort"], "Before Handler Cohort")
                    self.assertEqual(dry_run, dry_run_expected)
                    self.assertIn("Recipient: before-handler@example.com\n", out.getvalue())
                    self.assertIn("Course: Before Handler Course\n", out.getvalue())
                    self.assertIn("Cohort: Before Handler Cohort\n", out.getvalue())
                    return MavenResult(status="dry_run" if dry_run else "onboarded")

                dry_run_expected = dry_run
                args = ["--payload", json.dumps(body)]
                if dry_run:
                    args.append("--dry-run")
                with patch(
                    "integrations.management.commands.replay_maven_event.handle_maven_event",
                    side_effect=assert_banner_before_handler,
                ):
                    call_command("replay_maven_event", *args, stdout=out)

    @patch(
        "community.services.staff_notifications.notify_maven_enrollment",
        return_value=True,
    )
    def test_real_run_prints_resolved_recipient_course_and_cohort(
        self, _notify,
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

    @patch(
        "community.services.staff_notifications.notify_maven_enrollment",
        return_value=True,
    )
    def test_enveloped_runs_print_the_values_the_handler_processes(
        self, _notify,
    ):
        for envelope in ("data", "payload"):
            with self.subTest(envelope=envelope):
                email = f"replay-{envelope}@example.com"
                course = f"{envelope.title()} Buildcamp"
                cohort = f"{envelope.title()} Cohort"
                body = {
                    "event": "user_cohort.enrolled",
                    envelope: {
                        "email": email,
                        "course": course,
                        "cohort": cohort,
                    },
                }
                out = StringIO()

                call_command(
                    "replay_maven_event",
                    "--payload", json.dumps(body),
                    stdout=out,
                )

                output = out.getvalue()
                self.assertIn(f"Recipient: {email}\n", output)
                self.assertIn(f"Course: {course}\n", output)
                self.assertIn(f"Cohort: {cohort}\n", output)
                self.assertNotIn("Recipient: (none)", output)
                self.assertLess(output.index("Recipient:"), output.index("Status:"))

                occurrence = MavenEnrollmentEvent.objects.get(user__email=email)
                self.assertEqual(occurrence.course, course)
                self.assertEqual(occurrence.cohort, cohort)
