"""Pods for community sprints: the sprint as a pod activity (issue #1921)."""

import datetime
import re

from django.test import TestCase, override_settings, tag
from django.utils import timezone

from notifications.models import Notification
from payments.models import Tier
from plans.models import Sprint, SprintEnrollment
from pods.models import (
    MEETING_STATUS_PROPOSED,
    POD_SOURCE_MEMBER,
    POD_STATUS_ARCHIVED,
    Pod,
    PodArtifact,
    PodJoinRequest,
    PodMembership,
)
from pods.services import membership as svc
from pods.services.activity import activity_for_pod, cohort_activity, sprint_activity
from pods.services.first_meeting_nudge import dashboard_pod_nudges
from pods.services.meeting_presentation import dashboard_pod_meetings
from pods.services.meetings import propose_meeting
from pods.tests.fixtures import (
    COURSE_SLUG,
    enroll,
    make_cohort,
    make_course,
    make_meeting,
    make_user,
    set_windows,
)
from tests.fixtures import set_membership

EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+')
SPRINT_SLUG = 'spring-sprint'


def make_sprint(slug=SPRINT_SLUG, name='Spring sprint', *, status='active', pods_enabled=True, weeks=6, offset_days=-7):
    return Sprint.objects.create(
        name=name, slug=slug, status=status, pods_enabled=pods_enabled,
        start_date=timezone.localdate() + datetime.timedelta(days=offset_days), duration_weeks=weeks,
    )


def sprint_pod(sprint, name, members=(), *, owner=None, max_members=4, source='studio', **extra):
    pod = Pod.objects.create(
        sprint=sprint, name=name, purpose=f'{name} purpose', owner=owner,
        max_members=max_members, source=source, **extra,
    )
    for user in members:
        PodMembership.objects.create(pod=pod, user=user, source='staff')
    return pod


class SprintPodFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = make_sprint()
        cls.other_sprint = make_sprint('autumn-sprint', 'Autumn sprint')
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        main = Tier.objects.get(slug='main')
        cls.anna = make_user('main@test.com', first_name='Anna', last_name='Klein', timezone_name='Europe/Berlin')
        cls.mike = make_user('main2@test.com', first_name='Mike', last_name='Kay', timezone_name='Europe/Berlin')
        cls.free = make_user('free@test.com', first_name='Fran', last_name='Free')
        cls.outsider = make_user('basic@test.com', first_name='Bea', last_name='Sic')
        cls.staff = make_user('admin@test.com', staff=True)
        for user in (cls.anna, cls.mike):
            set_membership(user, tier=main)
        set_membership(cls.outsider, tier=Tier.objects.get(slug='basic'))
        for user in (cls.anna, cls.mike, cls.free):
            SprintEnrollment.objects.create(sprint=cls.sprint, user=user)

    def url(self, suffix='', sprint=None):
        return f'/sprints/{(sprint or self.sprint).slug}/pods{suffix}'

    def pod_url(self, pod, suffix=''):
        return f'/sprints/{pod.sprint.slug}/pods/{pod.pk}{suffix}'


@tag('core')
class ActivityHelperTest(SprintPodFixture):
    def test_sprint_activity_answers_every_question(self):
        activity = sprint_activity(self.sprint)
        self.assertEqual(activity.noun, 'sprint')
        self.assertEqual(activity.label, 'Spring sprint')
        self.assertTrue(activity.is_participant(self.anna))
        self.assertTrue(activity.is_participant(self.free))
        self.assertFalse(activity.is_participant(self.outsider))
        self.assertTrue(activity.can_view(self.staff))
        self.assertFalse(activity.can_view(self.outsider))
        self.assertTrue(activity.member_pages_enabled())
        self.assertEqual(activity.list_url(), '/sprints/spring-sprint/pods')
        self.assertEqual(activity.new_url(), '/sprints/spring-sprint/pods/new')
        self.assertEqual(
            activity.availability_url('/x'), '/sprints/spring-sprint/pods/availability?next=%2Fx',
        )
        pod = sprint_pod(self.sprint, 'Agents')
        self.assertEqual(activity_for_pod(pod).pod_url(pod), f'/sprints/spring-sprint/pods/{pod.pk}')
        self.assertEqual(
            activity.action_url(pod, 'pod_meeting_cancel', meeting_id=9),
            f'/sprints/spring-sprint/pods/{pod.pk}/meetings/9/cancel',
        )

    def test_cohort_activity_keeps_course_urls_and_label(self):
        activity = cohort_activity(self.cohort)
        self.assertEqual(activity.noun, 'cohort')
        self.assertEqual(activity.label, 'Pods course - Cohort 4')
        self.assertEqual(activity.list_url(), f'/courses/{COURSE_SLUG}/home/pods?cohort=4')
        pod = Pod.objects.create(cohort=self.cohort, name='C', purpose='p', source='studio')
        self.assertEqual(svc.pod_url(pod), f'/courses/{COURSE_SLUG}/home/pods/{pod.pk}')

    def test_draft_and_cancelled_sprints_hide_pages_from_enrolled_non_staff(self):
        for status in ('draft', 'cancelled'):
            self.sprint.status = status
            activity = sprint_activity(self.sprint)
            self.assertFalse(activity.can_view(self.anna), status)
            self.assertTrue(activity.can_view(self.staff), status)


@tag('core')
class SprintPodAccessTest(SprintPodFixture):
    def test_anonymous_is_redirected_to_login_with_next(self):
        response = self.client.get(self.url())
        self.assertRedirects(response, f'/accounts/login/?next={self.url()}', fetch_redirect_response=False)

    def test_pods_off_404s_every_url_for_member_and_staff(self):
        pod = sprint_pod(self.other_sprint, 'Off pod', members=[self.anna])
        Sprint.objects.filter(pk=self.other_sprint.pk).update(pods_enabled=False)
        SprintEnrollment.objects.create(sprint=self.other_sprint, user=self.anna)
        urls = [
            self.url(sprint=self.other_sprint), self.url('/new', self.other_sprint),
            self.url('/availability', self.other_sprint), self.pod_url(pod),
        ]
        for user in (self.anna, self.staff):
            self.client.force_login(user)
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 404, (user.email, url))
        self.client.force_login(self.anna)
        self.assertEqual(self.client.post(self.pod_url(pod, '/messages'), {'body': 'hi'}).status_code, 404)

    def test_non_enrolled_user_gets_404_on_list_and_pod_page(self):
        pod = sprint_pod(self.sprint, 'Agents', members=[self.anna])
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertEqual(self.client.get(self.pod_url(pod)).status_code, 404)
        self.assertEqual(self.client.post(self.pod_url(pod, '/join')).status_code, 404)

    def test_draft_and_cancelled_sprints_404_for_members_but_not_staff(self):
        pod = sprint_pod(self.sprint, 'Agents', members=[self.anna])
        for status in ('draft', 'cancelled'):
            Sprint.objects.filter(pk=self.sprint.pk).update(status=status)
            self.client.force_login(self.anna)
            for url in (self.url(), self.url('/new'), self.url('/availability'), self.pod_url(pod)):
                self.assertEqual(self.client.get(url).status_code, 404, (status, url))
            self.client.force_login(self.staff)
            self.assertContains(self.client.get(self.url()), 'data-testid="sprint-pods-header"', msg_prefix=status)

    def test_pod_from_another_sprint_or_a_cohort_404s(self):
        SprintEnrollment.objects.create(sprint=self.other_sprint, user=self.anna)
        other = sprint_pod(self.other_sprint, 'Other', members=[self.anna])
        cohort_pod = Pod.objects.create(cohort=self.cohort, name='Cohort', purpose='p', source='studio')
        self.client.force_login(self.anna)
        self.assertEqual(self.client.get(f'{self.url()}/{other.pk}').status_code, 404)
        self.assertEqual(self.client.get(f'{self.url()}/{cohort_pod.pk}').status_code, 404)

    def test_staff_not_enrolled_get_an_operator_view(self):
        pod = sprint_pod(self.sprint, 'Agents', members=[self.anna], owner=self.anna)
        self.client.force_login(self.staff)
        listing = self.client.get(self.url())
        self.assertContains(listing, f'data-pod-id="{pod.pk}"')
        self.assertNotContains(listing, 'data-testid="pods-start"')
        self.assertNotContains(listing, 'data-testid="pods-empty"')
        page = self.client.get(self.pod_url(pod))
        self.assertContains(page, 'data-testid="pod-name"')
        self.assertNotContains(page, 'data-testid="pod-request-form"')
        self.assertEqual(self.client.get(self.url('/new')).status_code, 404)

    def test_ended_sprint_keeps_pods_usable(self):
        ended = make_sprint('ended-sprint', 'Ended sprint', weeks=1, offset_days=-30)
        SprintEnrollment.objects.create(sprint=ended, user=self.anna)
        self.client.force_login(self.anna)
        self.assertContains(self.client.get(self.url(sprint=ended)), 'href="/sprints/ended-sprint/pods/new"')


@tag('core')
class SprintPodPagesTest(SprintPodFixture):
    def test_list_renders_sprint_header_and_no_course_tabs(self):
        sprint_pod(self.sprint, 'Agents', members=[self.mike])
        self.client.force_login(self.anna)
        response = self.client.get(self.url())
        self.assertContains(response, '<title>Study groups · Spring sprint | AI Shipping Labs</title>', html=False)
        self.assertContains(response, f'href="/sprints/{SPRINT_SLUG}" class="hover:text-accent')
        self.assertContains(response, 'Small groups in this sprint that meet to work on one thing together.')
        self.assertNotContains(response, 'data-course-tabs')
        self.assertNotContains(response, 'data-testid="course-tab-pods"')
        self.assertContains(response, f'href="/sprints/{SPRINT_SLUG}/pods/new"')

    def test_member_starts_a_pod_and_lands_on_the_sprint_pod_page(self):
        self.client.force_login(self.anna)
        response = self.client.post(self.url('/new'), {
            'name': 'Agents study group', 'purpose': 'Build agents', 'scheduling_note': '',
            'meeting_count': '1', 'meeting_minutes': '60',
        })
        pod = Pod.objects.get(name='Agents study group')
        self.assertEqual(pod.sprint_id, self.sprint.pk)
        self.assertIsNone(pod.cohort_id)
        self.assertRedirects(response, self.pod_url(pod), fetch_redirect_response=False)
        page = self.client.get(self.pod_url(pod))
        self.assertContains(page, 'Anna K.')

    def test_every_pod_page_form_posts_under_the_sprint(self):
        pod = sprint_pod(self.sprint, 'Agents', members=[self.anna, self.mike], owner=self.anna, meeting_count=4)
        set_windows(self.anna, 'Europe/Berlin', [(d, '08:00', '22:00') for d in range(7)])
        set_windows(self.mike, 'Europe/Berlin', [(d, '08:00', '22:00') for d in range(7)])
        start = timezone.now() + datetime.timedelta(days=2)
        make_meeting(pod, start.replace(minute=0, second=0, microsecond=0))
        self.client.force_login(self.anna)
        page = self.client.get(self.pod_url(pod)).content.decode()
        actions = re.findall(r'action="([^"]+)"', page)
        self.assertTrue(actions)
        for action in actions:
            if action.startswith('/accounts/') or action == '/search':
                continue
            self.assertTrue(action.startswith(f'/sprints/{SPRINT_SLUG}/pods/{pod.pk}/'), action)
        for path in ('/slack', '/messages', '/call-link', '/leave'):
            self.assertIn(f'/sprints/{SPRINT_SLUG}/pods/{pod.pk}{path}', page)
        self.assertNotIn('/courses/', ' '.join(actions))
        self.assertIn(f'/sprints/{SPRINT_SLUG}/pods/{pod.pk}/meetings/new?start=', page)

    def test_member_below_tier_uses_pods_but_not_slack(self):
        pod = sprint_pod(self.sprint, 'Agents', members=[self.free, self.anna], owner=self.anna)
        self.client.force_login(self.free)
        page = self.client.get(self.pod_url(pod))
        self.assertContains(page, 'data-testid="pod-slack-gate"')
        self.assertContains(page, 'data-testid="pod-chat-form"')
        self.assertEqual(self.client.post(self.pod_url(pod, '/slack'), {'slack_channel_url': ''}).status_code, 403)
        response = self.client.post(self.pod_url(pod, '/messages'), {'body': 'Hello pod'})
        self.assertRedirects(response, f'{self.pod_url(pod)}#chat', fetch_redirect_response=False)

    def test_pod_pages_never_show_member_emails(self):
        pod = sprint_pod(self.sprint, 'Agents', members=[self.anna, self.mike], owner=self.anna)
        self.client.force_login(self.anna)
        for url in (self.url(), self.pod_url(pod)):
            body = self.client.get(url).content.decode()
            main = body[body.index('id="main-content"'):body.index('</main>')]
            self.assertEqual(EMAIL_RE.findall(main), [], url)


@tag('core')
class SprintLimitsTest(SprintPodFixture):
    @override_settings(PODS_MAX_CREATED_PER_MEMBER='1')
    def test_start_limit_counts_per_sprint_and_says_sprint(self):
        SprintEnrollment.objects.create(sprint=self.other_sprint, user=self.anna)
        svc.start_member_pod(self.anna, self.other_sprint, {'name': 'Elsewhere', 'purpose': 'x'})
        svc.start_member_pod(self.anna, self.sprint, {'name': 'First', 'purpose': 'x'})
        with self.assertRaises(svc.PodError) as ctx:
            svc.start_member_pod(self.anna, self.sprint, {'name': 'Second', 'purpose': 'x'})
        self.assertEqual(ctx.exception.message, 'You can start up to 1 pods in this sprint.')

    def test_non_enrolled_user_cannot_join_with_sprint_copy(self):
        pod = sprint_pod(self.sprint, 'Agents', members=[self.anna])
        with self.assertRaises(svc.PodError) as ctx:
            svc.join_pod(pod, self.outsider)
        self.assertEqual(ctx.exception.message, 'Only members of this sprint can join its pods.')


@tag('core')
class SprintUnenrollTest(SprintPodFixture):
    def setUp(self):
        self.pod = sprint_pod(self.sprint, 'Agents', members=[self.anna, self.mike], owner=self.anna)
        self.other_pod = sprint_pod(self.sprint, 'Other', members=[self.anna], owner=self.anna)
        PodJoinRequest.objects.create(pod=self.other_pod, user=self.mike)
        SprintEnrollment.objects.create(sprint=self.other_sprint, user=self.mike)
        self.elsewhere = sprint_pod(self.other_sprint, 'Elsewhere', members=[self.mike])
        enroll(self.mike, self.cohort)
        self.cohort_pod = Pod.objects.create(cohort=self.cohort, name='Cohort pod', purpose='p', source='studio')
        PodMembership.objects.create(pod=self.cohort_pod, user=self.mike, source='staff')

    def assert_removed_only_from_this_sprint(self):
        self.assertFalse(PodMembership.objects.filter(pod=self.pod, user=self.mike).exists())
        self.assertEqual(PodJoinRequest.objects.get(pod=self.other_pod, user=self.mike).status, 'cancelled')
        self.assertTrue(PodMembership.objects.filter(pod=self.elsewhere, user=self.mike).exists())
        self.assertTrue(PodMembership.objects.filter(pod=self.cohort_pod, user=self.mike).exists())

    def test_sprint_leave_removes_the_member_from_this_sprints_pods(self):
        self.client.force_login(self.mike)
        self.client.post(f'/sprints/{SPRINT_SLUG}/leave')
        self.assert_removed_only_from_this_sprint()

    def test_studio_unenroll_removes_the_member(self):
        enrollment = SprintEnrollment.objects.get(sprint=self.sprint, user=self.mike)
        self.client.force_login(self.staff)
        self.client.post(f'/studio/sprints/{self.sprint.pk}/enrollments/{enrollment.pk}/unenroll')
        self.assertFalse(SprintEnrollment.objects.filter(pk=enrollment.pk).exists())
        self.assert_removed_only_from_this_sprint()


@tag('core')
class SprintNotificationsAndDashboardTest(SprintPodFixture):
    def test_meeting_notifications_link_to_the_sprint_pod(self):
        pod = sprint_pod(self.sprint, 'Agents', members=[self.anna, self.mike], owner=self.anna)
        start = (timezone.now() + datetime.timedelta(days=3)).replace(minute=0, second=0, microsecond=0)
        propose_meeting(pod, self.anna, start, zone_name='UTC')
        note = Notification.objects.get(user=self.mike)
        self.assertTrue(note.url.startswith(self.pod_url(pod)), note.url)

    def test_dashboard_rows_and_nudge_follow_pods_enabled(self):
        now = timezone.now()
        pod = sprint_pod(self.sprint, 'Agents', members=[self.anna, self.mike], owner=self.anna)
        PodMembership.objects.filter(pod=pod).update(joined_at=now - datetime.timedelta(days=10))
        make_meeting(pod, now + datetime.timedelta(days=2))
        rows = dashboard_pod_meetings(self.anna, now)
        self.assertEqual([row['url'] for row in rows], [self.pod_url(pod)])
        pod.meetings.all().delete()
        nudges = dashboard_pod_nudges(self.anna, now)
        self.assertEqual([row['url'] for row in nudges], [f'{self.pod_url(pod)}#suggested-times'])
        Sprint.objects.filter(pk=self.sprint.pk).update(pods_enabled=False)
        self.assertEqual(dashboard_pod_nudges(self.anna, now), [])
        make_meeting(pod, now + datetime.timedelta(days=2))
        self.assertEqual(dashboard_pod_meetings(self.anna, now), [])

    def test_dashboard_proposal_row_links_to_the_sprint_pod_meetings(self):
        now = timezone.now()
        pod = sprint_pod(self.sprint, 'Agents', members=[self.anna, self.mike], owner=self.anna)
        make_meeting(pod, now + datetime.timedelta(days=2), status=MEETING_STATUS_PROPOSED, proposed_by=self.anna)
        rows = dashboard_pod_meetings(self.mike, now)
        self.assertEqual([row['url'] for row in rows], [f'{self.pod_url(pod)}#meetings'])

    def test_dashboard_skips_pods_of_draft_and_cancelled_sprints(self):
        now = timezone.now()
        pod = sprint_pod(self.sprint, 'Agents', members=[self.anna, self.mike], owner=self.anna)
        PodMembership.objects.filter(pod=pod).update(joined_at=now - datetime.timedelta(days=10))
        self.assertEqual(len(dashboard_pod_nudges(self.anna, now)), 1)
        make_meeting(pod, now + datetime.timedelta(days=2))
        self.assertEqual(len(dashboard_pod_meetings(self.anna, now)), 1)
        for status in ('draft', 'cancelled'):
            Sprint.objects.filter(pk=self.sprint.pk).update(status=status)
            self.assertEqual(dashboard_pod_meetings(self.anna, now), [], status)
        pod.meetings.all().delete()
        self.assertEqual(dashboard_pod_nudges(self.anna, now), [])

@tag('core')
class SprintStaffCreateTest(SprintPodFixture):
    def test_create_staff_pod_checks_sprint_enrollment_and_is_idempotent(self):
        with self.assertRaises(svc.PodError) as ctx:
            svc.create_staff_pod(sprint=self.sprint, data={'name': 'X', 'purpose': 'Y'},
                                 owner_email='basic@test.com', source='studio')
        self.assertEqual(ctx.exception.message, 'basic@test.com is not enrolled in this sprint.')
        pod, results, created = svc.create_staff_pod(
            sprint=self.sprint, data={'name': 'Agents', 'purpose': 'Y'}, owner_email='main@test.com',
            member_emails=['main2@test.com', 'basic@test.com'], source='studio',
        )
        self.assertTrue(created)
        self.assertEqual(pod.sprint_id, self.sprint.pk)
        self.assertEqual(results.added, ['main@test.com', 'main2@test.com'])
        self.assertEqual(results.not_enrolled, ['basic@test.com'])
        again, _results, created = svc.create_staff_pod(
            sprint=self.sprint, data={'name': 'agents', 'purpose': 'Y'}, source='studio',
        )
        self.assertFalse(created)
        self.assertEqual(again.pk, pod.pk)

    def test_member_pod_limit_ignores_archived_member_pods(self):
        Pod.objects.create(
            sprint=self.sprint, name='Old', purpose='p', created_by=self.anna,
            source=POD_SOURCE_MEMBER, status=POD_STATUS_ARCHIVED,
        )
        pod = svc.start_member_pod(self.anna, self.sprint, {'name': 'New', 'purpose': 'p'})
        self.assertEqual(pod.owner_id, self.anna.pk)


@tag('core')
class SprintPodArtifactsTest(SprintPodFixture):
    """Issue #1922 notes and transcripts on sprint pods."""

    def test_member_adds_reads_and_removes_notes_under_the_sprint(self):
        pod = sprint_pod(self.sprint, 'Agents', members=[self.anna, self.mike], owner=self.anna)
        self.client.force_login(self.anna)
        form = self.client.get(self.pod_url(pod, '/notes/new'))
        self.assertContains(form, 'data-testid="sprint-pods-header"')
        self.assertContains(form, f'action="/sprints/{SPRINT_SLUG}/pods/{pod.pk}/notes/new"')
        self.assertNotContains(form, 'data-course-tabs')
        response = self.client.post(self.pod_url(pod, '/notes/new'), {
            'meeting': '', 'kind': 'notes', 'title': 'Our notes', 'text': 'We agreed on the demo.',
        })
        self.assertRedirects(response, f'{self.pod_url(pod)}#notes', fetch_redirect_response=False)
        artifact = PodArtifact.objects.get(pod=pod)
        detail = self.client.get(self.pod_url(pod, f'/notes/{artifact.pk}'))
        self.assertContains(detail, 'We agreed on the demo.')
        self.assertContains(detail, 'data-testid="sprint-pods-header"')
        self.assertContains(self.client.get(self.pod_url(pod)), f'/sprints/{SPRINT_SLUG}/pods/{pod.pk}/notes/{artifact.pk}')
        response = self.client.post(self.pod_url(pod, f'/notes/{artifact.pk}/delete'))
        self.assertRedirects(response, f'{self.pod_url(pod)}#notes', fetch_redirect_response=False)
        self.assertFalse(PodArtifact.objects.filter(pk=artifact.pk).exists())

    def test_non_enrolled_user_and_off_sprint_404_on_notes(self):
        pod = sprint_pod(self.sprint, 'Agents', members=[self.anna])
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.pod_url(pod, '/notes/new')).status_code, 404)
        Sprint.objects.filter(pk=self.sprint.pk).update(pods_enabled=False)
        self.client.force_login(self.anna)
        self.assertEqual(self.client.get(self.pod_url(pod, '/notes/new')).status_code, 404)
