"""Member Pods pages: enablement, access, list, pod page, actions (issue #1918)."""

import datetime
import re
from html.parser import HTMLParser

from django.test import TestCase, override_settings, tag
from django.utils import timezone
from freezegun import freeze_time

from content.models import CourseAccess
from payments.models import Tier
from pods.models import POD_SOURCE_STUDIO, Pod, PodMembership
from pods.tests.fixtures import (
    COURSE_SLUG,
    enroll,
    make_cohort,
    make_course,
    make_user,
    set_windows,
)
from tests.fixtures import set_membership

ENABLED = override_settings(PODS_COURSE_SLUGS=COURSE_SLUG, SLACK_TEAM_ID="T0TEAM")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


class _TestIdText(HTMLParser):
    """Collect the text inside every element carrying a given data-testid."""

    def __init__(self, testid):
        super().__init__()
        self.testid = testid
        self.depth = 0
        self.chunks = []
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        if self.depth:
            self.depth += 1
        elif dict(attrs).get("data-testid") == self.testid:
            self.depth = 1
            self.chunks = []

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1
            if not self.depth:
                self.blocks.append(" ".join(" ".join(self.chunks).split()))

    def handle_data(self, data):
        if self.depth:
            self.chunks.append(data)


def texts(response, testid):
    parser = _TestIdText(testid)
    parser.feed(response.content.decode())
    return parser.blocks


def _pod(cohort, name, members=(), owner=None, max_members=4, status="open", created_offset=0):
    pod = Pod.objects.create(
        cohort=cohort,
        name=name,
        purpose=f"{name} purpose",
        max_members=max_members,
        owner=owner,
        source=POD_SOURCE_STUDIO,
        status=status,
    )
    if created_offset:
        Pod.objects.filter(pk=pod.pk).update(created_at=timezone.now() - datetime.timedelta(minutes=created_offset))
    for user in members:
        PodMembership.objects.create(pod=pod, user=user, source="staff")
    return pod


class PodViewFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        cls.main_tier = Tier.objects.get(slug="main")
        cls.anna = make_user("anna@test.com", first_name="Anna", last_name="Klein", timezone_name="Europe/Berlin")
        cls.raj = make_user("raj@test.com", first_name="Raj", last_name="Shah", timezone_name="Asia/Kolkata")
        cls.mike = make_user("mike@test.com", first_name="Mike", last_name="Kay", timezone_name="America/New_York")
        cls.basic = make_user("basic@test.com", first_name="Bea", last_name="Sic")
        cls.outsider = make_user("main@test.com", first_name="Out", last_name="Sider")
        cls.staff = make_user("admin@test.com", staff=True)
        for user in (cls.anna, cls.raj, cls.mike, cls.outsider):
            set_membership(user, tier=cls.main_tier)
        set_membership(cls.basic, tier=Tier.objects.get(slug="basic"))
        for user in (cls.anna, cls.raj, cls.mike, cls.basic):
            enroll(user, cls.cohort)
        CourseAccess.objects.create(user=cls.basic, course=cls.course, access_type="granted")

    def pods_url(self, suffix=""):
        return f"/courses/{COURSE_SLUG}/home/pods{suffix}"

    def pod_url(self, pod, suffix=""):
        return f"/courses/{COURSE_SLUG}/home/pods/{pod.pk}{suffix}"


@tag("core")
class EnablementAndAccessTest(PodViewFixture):
    def test_disabled_course_404s_every_pod_url_and_hides_tab(self):
        pod = _pod(self.cohort, "Evening builders", members=[self.anna])
        self.client.force_login(self.anna)
        for url in (self.pods_url(), self.pods_url("/new"), self.pods_url("/availability"), self.pod_url(pod)):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        home = self.client.get(f"/courses/{COURSE_SLUG}/home")
        self.assertNotContains(home, 'data-testid="course-tab-pods"')

    @ENABLED
    def test_non_enrolled_user_gets_404_and_anonymous_is_redirected(self):
        pod = _pod(self.cohort, "Evening builders", members=[self.anna])
        self.client.force_login(self.outsider)
        list_response = self.client.get(self.pods_url("?cohort=4"))
        pod_response = self.client.get(self.pod_url(pod))
        self.assertEqual((list_response.status_code, pod_response.status_code), (404, 404))
        self.assertNotContains(pod_response, "Anna K.", status_code=404)
        self.client.logout()
        response = self.client.get(self.pod_url(pod))
        self.assertRedirects(response, f"/accounts/login/?next={self.pod_url(pod)}", fetch_redirect_response=False)

    @ENABLED
    def test_pod_under_another_course_slug_is_404(self):
        other = make_course(slug="other-course", title="Other")
        pod = _pod(self.cohort, "Evening builders", members=[self.anna])
        self.client.force_login(self.anna)
        with override_settings(PODS_COURSE_SLUGS=f"{COURSE_SLUG},{other.slug}"):
            response = self.client.get(f"/courses/{other.slug}/home/pods/{pod.pk}")
        self.assertEqual(response.status_code, 404)


@tag("core")
@ENABLED
class PodsListTest(PodViewFixture):
    def test_edit_availability_link_returns_to_the_editor_after_save(self):
        _pod(self.cohort, "Evening builders", members=[self.raj])
        self.client.force_login(self.anna)
        response = self.client.get(self.pods_url())
        self.assertContains(response, f'href="/courses/{COURSE_SLUG}/home/pods/availability?cohort=4"')

    def test_empty_state_offers_start_a_pod(self):
        self.client.force_login(self.anna)
        response = self.client.get(self.pods_url())
        self.assertTrue(texts(response, "pods-empty")[0].startswith("Empty state No study groups yet"))
        self.assertContains(response, f'href="/courses/{COURSE_SLUG}/home/pods/new?cohort=4"')

    def test_list_never_shows_emails(self):
        _pod(self.cohort, "Evening builders", members=[self.raj, self.mike], owner=self.raj)
        self.client.force_login(self.anna)
        response = self.client.get(self.pods_url())
        rows = " ".join(texts(response, "pods-rows"))
        self.assertNotRegex(rows, EMAIL_RE)


@tag("core")
@ENABLED
class StartPodTest(PodViewFixture):
    def test_start_pod_creates_owned_pod_and_asks_for_availability(self):
        self.client.force_login(self.anna)
        response = self.client.post(
            self.pods_url("/new?cohort=4"),
            {
                "name": "RAG evals study group",
                "purpose": "Weekly review of our eval notebooks",
                "scheduling_note": "",
                "meeting_count": "1",
                "meeting_minutes": "60",
            },
            follow=True,
        )
        pod = Pod.objects.get(name="RAG evals study group")
        self.assertEqual((pod.owner, pod.status, pod.source), (self.anna, "open", "member"))
        self.assertEqual(response.redirect_chain[-1][0], self.pod_url(pod))
        self.assertIn(
            "Study group started. Add your availability to coordinate a first check-in.",
            texts(response, "messages-region")[0],
        )
        self.assertEqual(texts(response, "pod-status-badge"), ["Member"])
        self.assertIn("1 member · Plan: 1 meeting × 60 min", texts(response, "pod-meta")[0])
        self.assertEqual(
            texts(response, "pod-times-needs-more"),
            ["Suggested times appear when at least two members have added availability."],
        )


@tag("core")
@ENABLED
class PodPageTest(PodViewFixture):
    def test_non_member_sees_names_timezones_and_fit_but_no_private_sections(self):
        set_windows(self.anna, "Europe/Berlin", [(1, "18:00", "21:00")])
        set_windows(self.raj, "Europe/Berlin", [(1, "18:00", "21:00")])
        set_windows(self.mike, "Europe/Berlin", [(1, "18:00", "21:00")])
        pod = _pod(self.cohort, "Evening builders", members=[self.raj, self.mike], owner=self.raj)
        Pod.objects.filter(pk=pod.pk).update(slack_channel_url="https://x.slack.com/archives/C1")
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, "pod-member-name"), ["Raj S.", "Mike K."])
        self.assertIn("Europe/Berlin", texts(response, "pod-member-meta")[0])
        self.assertEqual(texts(response, "pod-fit-line"), ["Overlaps with you 3 h a week"])
        for hidden in ("pod-suggested-times", "pod-slack", "pod-my-availability", "pod-requests"):
            self.assertNotContains(response, f'data-testid="{hidden}"')
        self.assertNotContains(response, "x.slack.com")
        members_block = " ".join(texts(response, "pod-members"))
        self.assertNotRegex(members_block, EMAIL_RE)

    def test_non_owner_member_gets_403_on_edit_and_owner_can_close(self):
        pod = _pod(self.cohort, "Evening builders", members=[self.raj, self.mike], owner=self.raj)
        self.client.force_login(self.mike)
        self.assertEqual(self.client.get(self.pod_url(pod, "/edit")).status_code, 403)
        self.client.force_login(self.raj)
        self.client.post(
            self.pod_url(pod, "/edit"),
            {
                "name": "Evening builders",
                "purpose": "New purpose",
                "scheduling_note": "",
                "meeting_count": "3",
                "meeting_minutes": "45",
                "status": "closed",
            },
        )
        pod.refresh_from_db()
        self.assertEqual((pod.status, pod.meeting_minutes, pod.purpose), ("closed", 45, "New purpose"))

    @freeze_time("2026-07-05 00:00:00")  # date-rot-ok: frozen Sunday so suggestions are deterministic
    def test_suggested_times_render_in_viewer_zone_with_strip_and_based_on_line(self):
        set_windows(self.anna, "Europe/Berlin", [(1, "18:00", "21:00"), (3, "18:00", "21:00")])
        set_windows(self.raj, "Asia/Kolkata", [(1, "21:00", "23:30"), (3, "21:00", "23:30")])
        pod = _pod(self.cohort, "Evening builders", members=[self.anna, self.raj, self.mike], owner=self.raj)
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        times = texts(response, "pod-slot-time")
        self.assertTrue(times)
        self.assertTrue(all(t.endswith("Europe/Berlin") for t in times), times)
        self.assertEqual(times[0], "Tue, Jul 07, 18:00 Europe/Berlin")
        strip = texts(response, "pod-slot-strip")[0]
        self.assertIn("Anna K. 18:00 Berlin", strip)
        self.assertIn("Raj S. 21:30 Kolkata", strip)
        self.assertIn("Mike K. - no availability", strip)
        self.assertEqual(
            texts(response, "pod-times-based-on"),
            ["Based on 2 of 3 members. Mike K. hasn't added availability yet."],
        )
        # Issue #1927: Mike has no availability, so the badge does not claim "Everyone".
        self.assertEqual(texts(response, "pod-slot-fit")[0], "All 2 with availability - works well")
        self.assertEqual(texts(response, "availability-summary-day"), ["Tuesday 18:00-21:00", "Thursday 18:00-21:00"])
        self.client.force_login(self.raj)
        raj_times = texts(self.client.get(self.pod_url(pod)), "pod-slot-time")
        self.assertEqual(raj_times[0], "Tue, Jul 07, 21:30 Asia/Kolkata")

    @freeze_time("2026-07-05 00:00:00")  # date-rot-ok: frozen Sunday
    def test_no_fit_shows_message_with_edit_availability(self):
        set_windows(self.anna, "Europe/Berlin", [(1, "08:00", "09:00")])
        set_windows(self.raj, "Europe/Berlin", [(2, "08:00", "09:00")])
        pod = _pod(self.cohort, "Evening builders", members=[self.anna, self.raj])
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(
            texts(response, "pod-times-no-fit"),
            [
                "No time fits at least 2 of you in the next 2 weeks. Ask members to add more windows, or add some yourself."
            ],
        )
        self.assertEqual(texts(response, "pod-times-edit-availability"), ["Edit my availability"])
