"""Sprint detail page ``Pods`` section and includes bullet (issue #1921)."""

import datetime
import re

from django.test import TestCase, tag
from django.utils import timezone

from plans.models import Sprint, SprintAccountabilityPartner, SprintEnrollment
from pods.models import Pod, PodJoinRequest, PodMembership
from pods.tests.fixtures import make_meeting, make_user

EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+')


def section(response, testid):
    """The HTML of the element carrying ``data-testid=testid`` (first match)."""
    body = response.content.decode()
    start = body.find(f'data-testid="{testid}"')
    if start == -1:
        return ''
    start = body.rfind('<', 0, start)
    end = body.find('</section>', start)
    return body[start:end]


@tag('core')
class SprintDetailPodsSectionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='Spring sprint', slug='spring-sprint', status='active', pods_enabled=True,
            start_date=timezone.localdate() - datetime.timedelta(days=7), duration_weeks=6,
        )
        cls.anna = make_user('anna@test.com', first_name='Anna', last_name='Klein', timezone_name='Europe/Berlin')
        cls.mike = make_user('mike@test.com', first_name='Mike', last_name='Kay')
        cls.outsider = make_user('out@test.com', first_name='Out')
        cls.staff = make_user('admin@test.com', staff=True)
        for user in (cls.anna, cls.mike):
            SprintEnrollment.objects.create(sprint=cls.sprint, user=user)

    def url(self):
        return f'/sprints/{self.sprint.slug}'

    def pod(self, name, members, owner=None):
        pod = Pod.objects.create(sprint=self.sprint, name=name, purpose='p', owner=owner, source='studio')
        for user in members:
            PodMembership.objects.create(pod=pod, user=user, source='staff')
        return pod

    def test_enrolled_member_sees_their_pods_after_partners(self):
        pod = self.pod('Agents study group', [self.anna, self.mike], owner=self.anna)
        make_meeting(pod, (timezone.now() + datetime.timedelta(days=2)).replace(second=0, microsecond=0))
        self.client.force_login(self.anna)
        response = self.client.get(self.url())
        body = response.content.decode()
        self.assertLess(body.index('data-testid="sprint-accountability-partners"'), body.index('data-testid="sprint-pods"'))
        html = section(response, 'sprint-pods')
        self.assertIn(f'href="/sprints/{self.sprint.slug}/pods"', html)
        self.assertIn('Browse study groups', html)
        self.assertIn(f'href="/sprints/{self.sprint.slug}/pods/{pod.pk}"', html)
        self.assertIn('2 members', html)
        self.assertIn('Next meeting:', html)
        self.assertEqual(EMAIL_RE.findall(html), [])

    def test_member_without_pods_sees_the_empty_state(self):
        self.client.force_login(self.mike)
        html = section(self.client.get(self.url()), 'sprint-pods')
        self.assertIn("You're not in a study group yet", html)
        self.assertIn('Start one or join an open study group in this sprint.', html)
        self.assertIn('data-testid="sprint-pods-browse"', html)

    def test_historical_request_does_not_restore_request_controls(self):
        pod = self.pod('Agents', [self.anna], owner=self.anna)
        PodJoinRequest.objects.create(pod=pod, user=self.mike)
        self.client.force_login(self.anna)
        html = section(self.client.get(self.url()), 'sprint-pods')
        self.assertIn(f'href="/sprints/{self.sprint.slug}/pods/{pod.pk}"', html)
        self.assertIn('1 member', html)
        self.assertNotIn('waiting for you', html)
        self.assertNotIn(f'href="/sprints/{self.sprint.slug}/pods/{pod.pk}#requests"', html)

    def test_member_who_does_not_own_the_pod_sees_no_waiting_link(self):
        pod = self.pod('Agents', [self.anna, self.mike], owner=self.anna)
        PodJoinRequest.objects.create(pod=pod, user=self.outsider)
        self.client.force_login(self.mike)
        html = section(self.client.get(self.url()), 'sprint-pods')
        self.assertIn(f'href="/sprints/{self.sprint.slug}/pods/{pod.pk}"', html)
        self.assertNotIn('waiting for you', html)
        self.assertNotIn('data-testid="sprint-pod-requests"', html)

    def test_non_enrolled_and_anonymous_see_no_section_but_the_bullet(self):
        for login in (self.outsider, None):
            self.client.logout()
            if login:
                self.client.force_login(login)
            response = self.client.get(self.url())
            self.assertNotContains(response, 'data-testid="sprint-pods"')
            self.assertContains(response, 'Pods: small groups that meet to work on one thing')

    def test_pods_off_hides_section_and_bullet(self):
        Sprint.objects.filter(pk=self.sprint.pk).update(pods_enabled=False)
        self.client.force_login(self.anna)
        self.assertNotContains(self.client.get(self.url()), 'data-testid="sprint-pods"')
        self.client.force_login(self.outsider)
        self.assertNotContains(self.client.get(self.url()), 'Pods: small groups')

    def test_staff_not_enrolled_see_the_section(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(self.url()), 'data-testid="sprint-pods"')

    def test_cancelled_sprint_shows_no_pods_section(self):
        Sprint.objects.filter(pk=self.sprint.pk).update(status='cancelled')
        self.client.force_login(self.anna)
        self.assertNotContains(self.client.get(self.url()), 'data-testid="sprint-pods"')

    def test_your_partners_renders_as_before(self):
        SprintAccountabilityPartner.objects.create(sprint=self.sprint, member=self.anna, partner=self.mike)
        self.client.force_login(self.anna)
        html = section(self.client.get(self.url()), 'sprint-accountability-partners')
        self.assertIn('Your partners', html)
        self.assertIn('Mike Kay', html)
        self.assertIn('mike@test.com', html)
