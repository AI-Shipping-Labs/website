"""The Maven welcome names the course by its title, never by a slug (#1682).

The producer (``_send_welcome``) resolves the enrollee's Course from the
occurrence's ``course_key`` and attaches it to the durable delivery as its
natural relation; the stored context persists no Maven course or cohort
identifier. ``email_app.hooks._resolve_maven_welcome_context`` reads the
title from that relation at delivery time, so the SES-bound subject and
body carry the human title — or the template's generic course-free copy
when no course resolves. These tests drain real ``EmailDelivery`` rows
through the worker and assert on the exact SES payload (StubSESClient),
the same authoritative surface as the #960/#1647 welcome suites.
"""

from unittest.mock import patch

from community_base.mail.jobs import deliver as deliver_job
from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.test import TestCase

from content.models import Cohort, Course
from email_app.package_mail import send_package_mail
from email_app.testing import StubSESClient
from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import run_occurrence_steps
from tests.fixtures import create_user_with_membership

User = get_user_model()

# Maven sends this slug-like string as the course label; the linked Course
# row carries the human title the enrollee should see instead.
SLUG_LABEL = "from-rag-to-agents"
COURSE_TITLE = "AI Engineering Buildcamp: From RAG to Agents"
COHORT_LABEL = "Cohort 1"


def _drain(delivery):
    """Deliver one delivery through the real worker; return (subject, html)."""

    stub = StubSESClient()
    with patch(
        "community_base.mail.backends.ses_local.configured_client",
        return_value=stub,
    ):
        deliver_job(None, {"delivery_id": str(delivery.id)})
    assert len(stub.calls) == 1, "maven_welcome was not delivered"
    simple = stub.calls[0]["Content"]["Simple"]
    return simple["Subject"]["Data"], simple["Body"]["Html"]["Data"]


class _WelcomePipelineMixin:
    def _course(self, **fields):
        defaults = {
            "title": COURSE_TITLE,
            "slug": "buildcamp-1682",
            "maven_course_key": SLUG_LABEL,
        }
        defaults.update(fields)
        return Course.objects.create(**defaults)

    def _cohort(self, course):
        return Cohort.objects.create(
            course=course,
            external_key="cohort-1",
            name=COHORT_LABEL,
            start_date="2026-09-21",
            end_date="2026-11-22",
        )

    def _user(self, email):
        return User.objects.create_user(email=email, password="pw")

    def _occurrence(self, user, key, **fields):
        defaults = {
            "course": SLUG_LABEL,
            "cohort": COHORT_LABEL,
            "course_key": SLUG_LABEL,
            "cohort_key": "cohort-1",
            "override_status": MavenEnrollmentEvent.STEP_SUCCEEDED,
            "enrollment_status": MavenEnrollmentEvent.STEP_SKIPPED,
            "notification_status": MavenEnrollmentEvent.STEP_SKIPPED,
            "slack_status": MavenEnrollmentEvent.STEP_SKIPPED,
            "welcome_status": MavenEnrollmentEvent.STEP_PENDING,
            "lifecycle": MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
            "event_type": "user_cohort.enrolled",
        }
        defaults.update(fields)
        return MavenEnrollmentEvent.objects.create(
            dedupe_key=key, identity_hash=key, user=user, **defaults,
        )

    def _welcome_delivery(self):
        return EmailDelivery.objects.get(purpose="maven_welcome")


class MavenWelcomeTitleRenderTest(_WelcomePipelineMixin, TestCase):
    def test_resolved_course_renders_its_title_never_the_slug(self):
        course = self._course()
        self._cohort(course)
        user = self._user("enrollee-1682@example.com")
        occurrence = self._occurrence(user, "title-1")

        run_occurrence_steps(occurrence)
        occurrence.refresh_from_db()

        self.assertEqual(
            occurrence.welcome_status, MavenEnrollmentEvent.STEP_SUCCEEDED,
        )
        delivery = self._welcome_delivery()
        self.assertEqual(delivery.related_object_type, "content.course")
        self.assertEqual(str(delivery.related_object_id), str(course.pk))

        subject, html = _drain(delivery)

        self.assertIn(COURSE_TITLE, subject)
        self.assertIn(COURSE_TITLE, html)
        # The slug-like Maven label and the cohort label are integration
        # identifiers; neither may reach member-facing copy.
        self.assertNotIn(SLUG_LABEL, subject)
        self.assertNotIn(SLUG_LABEL, html)
        self.assertNotIn(COHORT_LABEL, subject)
        self.assertNotIn(COHORT_LABEL, html)

    def test_worker_reads_the_title_at_delivery_time(self):
        course = self._course()
        user = self._user("fresh-title-1682@example.com")
        # #1659 matching is case-insensitive, so the key case never matters.
        occurrence = self._occurrence(
            user, "title-2", course_key="FROM-RAG-TO-AGENTS",
        )
        run_occurrence_steps(occurrence)
        delivery = self._welcome_delivery()

        # The title is re-read at delivery, not frozen into the row: an edit
        # between queue and send is what the enrollee actually sees.
        course.title = "Renamed Buildcamp"
        course.save()

        subject, _html = _drain(delivery)

        self.assertIn("Renamed Buildcamp", subject)
        self.assertNotIn(COURSE_TITLE, subject)


class MavenWelcomeGenericFallbackTest(_WelcomePipelineMixin, TestCase):
    def test_unresolvable_key_sends_generic_copy_and_step_still_succeeds(self):
        user = self._user("unmatched-1682@example.com")
        occurrence = self._occurrence(
            user,
            "generic-1",
            course_key="no-such-key",
            cohort_key="no-such-cohort",
        )

        run_occurrence_steps(occurrence)
        occurrence.refresh_from_db()

        self.assertEqual(
            occurrence.welcome_status, MavenEnrollmentEvent.STEP_SUCCEEDED,
        )
        delivery = self._welcome_delivery()
        self.assertEqual(delivery.related_object_type, "")
        self.assertEqual(delivery.context_data, {"course_name": ""})

        subject, html = _drain(delivery)

        self.assertEqual(subject, "Welcome to the AI Shipping Labs community")
        self.assertIn("Thanks for enrolling.", html)
        self.assertNotIn(SLUG_LABEL, subject)
        self.assertNotIn(SLUG_LABEL, html)
        self.assertNotIn(COHORT_LABEL, subject)
        self.assertNotIn(COHORT_LABEL, html)

    def test_deleted_course_relation_still_delivers_generic_copy(self):
        course = self._course()
        user = self._user("deleted-1682@example.com")
        occurrence = self._occurrence(user, "generic-2")
        run_occurrence_steps(occurrence)
        delivery = self._welcome_delivery()
        self.assertEqual(delivery.related_object_type, "content.course")

        # The course disappears between queue and delivery (content sync,
        # admin cleanup): the send must degrade to the generic copy, not
        # fail closed into a PermanentJobError retry loop.
        course.delete()

        subject, html = _drain(delivery)

        self.assertEqual(subject, "Welcome to the AI Shipping Labs community")
        self.assertIn("Thanks for enrolling.", html)
        self.assertNotIn(SLUG_LABEL, html)
        self.assertNotIn(COHORT_LABEL, html)


class MavenWelcomeStoredContextHygieneTest(_WelcomePipelineMixin, TestCase):
    def test_pipeline_deliveries_store_no_course_or_cohort_identifier(self):
        course = self._course()
        self._cohort(course)
        run_occurrence_steps(self._occurrence(self._user("hygiene-1@example.com"), "hygiene-1"))
        run_occurrence_steps(
            self._occurrence(
                self._user("hygiene-2@example.com"),
                "hygiene-2",
                course_key="no-such-key",
                cohort_key="no-such-cohort",
            ),
        )

        contexts = list(
            EmailDelivery.objects.filter(purpose="maven_welcome")
            .order_by("id")
            .values_list("context_data", flat=True)
        )

        self.assertEqual(contexts, [{"course_name": ""}, {"course_name": ""}])


class MavenWelcomeLegacyDeliveryTest(_WelcomePipelineMixin, TestCase):
    def test_relationless_delivery_keeps_rendering_the_stored_scalar(self):
        # Rows queued before #1682 carry a stored scalar and no relation;
        # they must keep rendering whatever was stored at queue time.
        user = self._user("legacy-1682@example.com")
        delivery = send_package_mail(
            user,
            "maven_welcome",
            {"course_name": "Legacy Buildcamp label"},
        )
        self.assertEqual(delivery.related_object_type, "")

        subject, html = _drain(delivery)

        self.assertIn("Legacy Buildcamp label", subject)
        self.assertIn("Legacy Buildcamp label", html)


class MavenStaffSurfacesKeepRawLabelTest(_WelcomePipelineMixin, TestCase):
    """Staff surfaces keep the raw identifier; only member mail got the title.

    The occurrence's raw course label differs from its ``course_key`` here,
    so a member-facing leak of either identifier would fail these asserts.
    """

    def test_enrollment_and_removal_headsup_receive_the_raw_label(self):
        course = self._course(maven_course_key="key-1682")
        self._cohort(course)
        # The notification step reads the member's membership entitlement,
        # so this user needs the membership fixture, not a bare user.
        user = create_user_with_membership(email="staff-1682@example.com")
        enrolled = self._occurrence(
            user,
            "staff-1",
            course_key="key-1682",
            # The notification step's entitlement read needs the Maven grant
            # a real override step produces, so let the whole chain run.
            override_status=MavenEnrollmentEvent.STEP_PENDING,
            notification_status=MavenEnrollmentEvent.STEP_PENDING,
        )
        removed = self._occurrence(
            user,
            "staff-2",
            course_key="key-1682",
            lifecycle=MavenEnrollmentEvent.LIFECYCLE_REMOVED,
            override_status=MavenEnrollmentEvent.STEP_SKIPPED,
            # The model defaults removal_status to SKIPPED (terminal); a
            # fresh removal occurrence that still owes its heads-up is
            # PENDING until the step runs.
            removal_status=MavenEnrollmentEvent.STEP_PENDING,
            event_type="user_cohort.removed",
        )

        with patch(
            "community.services.staff_notifications.notify_maven_enrollment",
        ) as notify_enrolled, patch(
            "community.services.staff_notifications.notify_maven_cohort_removal",
        ) as notify_removed:
            run_occurrence_steps(enrolled)
            run_occurrence_steps(removed)

        enrolled.refresh_from_db()
        self.assertEqual(
            enrolled.notification_status, MavenEnrollmentEvent.STEP_SUCCEEDED,
        )
        self.assertEqual(notify_enrolled.call_count, 1)
        self.assertEqual(
            notify_enrolled.call_args.kwargs["course"], SLUG_LABEL,
        )
        self.assertEqual(
            notify_enrolled.call_args.kwargs["cohort"], COHORT_LABEL,
        )
        self.assertEqual(notify_removed.call_count, 1)
        # notify_maven_cohort_removal(user, cohort, course, email=...).
        self.assertEqual(notify_removed.call_args.args[1], COHORT_LABEL)
        self.assertEqual(notify_removed.call_args.args[2], SLUG_LABEL)