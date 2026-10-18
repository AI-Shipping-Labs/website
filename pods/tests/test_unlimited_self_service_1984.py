"""Authoritative member, presentation, and migration regressions for #1984."""

from django.db import connection
from django.test import TestCase, override_settings, tag
from django.test.utils import CaptureQueriesContext

from content.models import CourseAccess
from notifications.models import Notification
from payments.models import Tier
from pods.models import Pod, PodJoinRequest, PodMembership
from pods.services.presentation import build_pod_page, build_pod_rows
from pods.tests.fixtures import COURSE_SLUG, enroll, make_cohort, make_course, make_user, set_windows
from tests.fixtures import set_membership

ENABLED = override_settings(PODS_COURSE_SLUGS=COURSE_SLUG)


def group(cohort, name, *, members=(), status="open", purpose="Build useful things.", **extra):
    pod = Pod.objects.create(
        cohort=cohort,
        name=name,
        purpose=purpose,
        status=status,
        source="studio",
        **extra,
    )
    for user in members:
        PodMembership.objects.create(pod=pod, user=user, source="staff")
    return pod


@tag("core")
@ENABLED
class UnlimitedStudyGroupMemberTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        cls.main = Tier.objects.get(slug="main")
        cls.anna = make_user("anna@test.com", first_name="Anna", timezone_name="Europe/Berlin")
        cls.raj = make_user("raj@test.com", first_name="Raj", timezone_name="America/New_York")
        cls.mike = make_user("mike@test.com", first_name="Mike")
        cls.outsider = make_user("outsider@test.com")
        cls.staff = make_user("staff@test.com", staff=True)
        for user in (cls.anna, cls.raj, cls.mike, cls.outsider):
            set_membership(user, tier=cls.main)
        for user in (cls.anna, cls.raj, cls.mike):
            enroll(user, cls.cohort)
        CourseAccess.objects.create(user=cls.anna, course=cls.course, access_type="granted")
        CourseAccess.objects.create(user=cls.raj, course=cls.course, access_type="granted")
        CourseAccess.objects.create(user=cls.mike, course=cls.course, access_type="granted")

    def list_url(self):
        return f"/courses/{COURSE_SLUG}/home/pods?cohort=4"

    def detail_url(self, pod):
        return f"/courses/{COURSE_SLUG}/home/pods/{pod.pk}"

    def test_member_joins_two_groups_immediately_and_leaves_one(self):
        first = group(self.cohort, "First", members=[self.raj])
        second = group(self.cohort, "Second", members=[self.mike])
        self.client.force_login(self.anna)
        for pod in (first, second):
            response = self.client.post(f"{self.detail_url(pod)}/join", follow=True)
            self.assertContains(response, f"You joined {pod.name}.")
        self.assertEqual(PodMembership.objects.filter(user=self.anna).count(), 2)
        page = self.client.get(self.list_url())
        self.assertEqual(page.content.count(b'data-testid="pod-row-leave"'), 2)

        response = self.client.post(f"{self.detail_url(first)}/leave", follow=True)
        self.assertContains(response, "You left First.")
        self.assertFalse(first.memberships.filter(user=self.anna).exists())
        self.assertTrue(second.memberships.filter(user=self.anna).exists())
        self.assertEqual(Notification.objects.count(), 0)

    def test_closed_group_is_readable_but_only_existing_member_can_leave(self):
        closed = group(self.cohort, "Closed research", members=[self.raj], status="closed")
        self.client.force_login(self.anna)
        detail = self.client.get(self.detail_url(closed))
        self.assertContains(detail, "Closed research")
        self.assertNotContains(detail, 'data-testid="pod-join"')
        self.assertContains(self.client.get(self.list_url()), 'data-testid="pod-row-closed"')
        self.assertFalse(closed.memberships.filter(user=self.anna).exists())
        self.client.force_login(self.raj)
        self.assertContains(self.client.get(self.detail_url(closed)), 'data-testid="pod-leave"')

    def test_ineligible_viewer_cannot_read_or_join(self):
        pod = group(self.cohort, "Private cohort group")
        self.client.force_login(self.outsider)
        for method, url in (
            ("get", self.list_url()),
            ("get", self.detail_url(pod)),
            ("post", f"{self.detail_url(pod)}/join"),
        ):
            response = getattr(self.client, method)(url)
            self.assertEqual(response.status_code, 404, url)
        self.assertFalse(pod.memberships.filter(user=self.outsider).exists())

    def test_scannable_rows_have_public_copy_timezone_and_one_truthful_control(self):
        set_windows(self.raj, "America/New_York", [(1, "09:00", "10:00", "preferred")])
        set_windows(self.anna, "Europe/Berlin", [(1, "09:00", "10:00", "preferred")])
        own = group(self.cohort, "Own", members=[self.anna, self.raj], purpose="Review agent evidence.")
        open_group = group(self.cohort, "Open", members=[self.mike], purpose="Build technical agents.")
        closed = group(self.cohort, "Closed", status="closed")
        self.client.force_login(self.anna)
        response = self.client.get(self.list_url())
        self.assertEqual(response.content.count(b'data-testid="pod-row"'), 3)
        self.assertContains(response, "Review agent evidence.")
        self.assertContains(response, "2 members")
        self.assertContains(response, "Time zones not set")
        self.assertEqual(response.content.count(b'data-testid="pod-row-leave"'), 1)
        self.assertEqual(response.content.count(b'data-testid="pod-row-join"'), 1)
        self.assertEqual(response.content.count(b'data-testid="pod-row-closed"'), 1)
        for removed in ("waiting list", "Request to join", "of 4", "Plan:"):
            self.assertNotContains(response, removed)
        rows, _availability = build_pod_rows(self.cohort, self.anna)
        self.assertEqual([row["pod"].pk for row in rows], [own.pk, open_group.pk, closed.pk])
        self.assertTrue(all(not hasattr(row["pod"], "internal_notes") for row in rows))

    def test_list_query_count_does_not_grow_with_groups(self):
        group(self.cohort, "One group", members=[self.raj])
        with CaptureQueriesContext(connection) as one_group:
            rows, _availability = build_pod_rows(self.cohort, self.anna)
        self.assertEqual(len(rows), 1)

        group(self.cohort, "Second group", members=[self.mike])
        group(self.cohort, "Third group", members=[self.raj, self.mike])
        with CaptureQueriesContext(connection) as three_groups:
            rows, _availability = build_pod_rows(self.cohort, self.anna)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(one_group), len(three_groups))

    def test_detail_separates_plan_copy_and_excludes_staff_notes_from_context_and_html(self):
        secret = "Introduced by a named member in a private Slack thread."
        pod = group(
            self.cohort,
            "Safe public group",
            members=[self.raj],
            purpose="Compare grounded approaches.",
            scheduling_note="Choose a weekly time after joining.",
            internal_notes=secret,
            meeting_count=1,
            meeting_minutes=60,
        )
        self.client.force_login(self.anna)
        response = self.client.get(self.detail_url(pod))
        self.assertContains(response, "Compare grounded approaches.")
        self.assertContains(response, "Choose a weekly time after joining.")
        self.assertContains(response, "Plan: 1 meeting × 60 min")
        self.assertNotContains(response, secret)
        self.assertFalse(hasattr(response.context["pod"], "internal_notes"))
        page = build_pod_page(pod, self.anna)
        self.assertFalse(hasattr(page["pod"], "internal_notes"))

    def test_owner_form_cannot_write_internal_notes_but_staff_studio_can(self):
        pod = group(self.cohort, "Editable", members=[self.anna], owner=self.anna, internal_notes="keep")
        self.client.force_login(self.anna)
        response = self.client.post(
            f"{self.detail_url(pod)}/edit",
            {
                "name": "Editable",
                "purpose": "Public changed.",
                "scheduling_note": "Neutral guidance.",
                "internal_notes": "leak",
                "meeting_count": "1",
                "meeting_minutes": "60",
                "status": "open",
            },
        )
        self.assertEqual(response.status_code, 302)
        pod.refresh_from_db()
        self.assertEqual(
            (pod.purpose, pod.scheduling_note, pod.internal_notes),
            (
                "Public changed.",
                "Neutral guidance.",
                "keep",
            ),
        )
        self.client.force_login(self.staff)
        studio = self.client.get(f"/studio/pods/{pod.pk}/")
        self.assertContains(studio, "Internal formation notes — staff only")
        self.assertContains(studio, "keep")

    def test_stale_member_and_studio_request_posts_return_gone_without_mutation(self):
        pod = group(self.cohort, "No approval queue")
        join_request = PodJoinRequest.objects.create(
            pod=pod,
            user=self.anna,
            status="pending",
            message="keep for audit",
        )
        base = self.detail_url(pod)
        member_urls = (
            f"{base}/request",
            f"{base}/withdraw",
            f"{base}/requests/{join_request.pk}/approve",
            f"{base}/requests/{join_request.pk}/decline",
        )
        self.client.force_login(self.anna)
        for url in member_urls:
            self.assertEqual(self.client.post(url).status_code, 410, url)
        self.client.force_login(self.staff)
        for action in ("approve", "decline"):
            url = f"/studio/pods/{pod.pk}/requests/{join_request.pk}/{action}"
            self.assertEqual(self.client.post(url).status_code, 410, url)
        join_request.refresh_from_db()
        self.assertEqual((join_request.status, join_request.message), ("pending", "keep for audit"))
        self.assertFalse(pod.memberships.exists())
