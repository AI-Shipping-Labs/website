"""Studio support for sprint pods (issue #1921)."""

import datetime

from django.test import TestCase, tag
from django.utils import timezone

from plans.models import Sprint, SprintEnrollment
from pods.models import Pod, PodMembership
from pods.tests.fixtures import make_cohort, make_course, make_user


def make_sprint(slug, name, *, pods_enabled=True):
    return Sprint.objects.create(
        name=name, slug=slug, status='active', pods_enabled=pods_enabled,
        start_date=timezone.localdate() - datetime.timedelta(days=7), duration_weeks=6,
    )


@tag('core')
class StudioSprintPodsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = make_sprint('spring-sprint', 'Spring sprint')
        cls.other_sprint = make_sprint('autumn-sprint', 'Autumn sprint', pods_enabled=False)
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        cls.staff = make_user('admin@test.com', staff=True)
        cls.anna = make_user('anna@test.com', first_name='Anna')
        cls.mike = make_user('mike@test.com', first_name='Mike')
        cls.outsider = make_user('out@test.com')
        for user in (cls.anna, cls.mike):
            SprintEnrollment.objects.create(sprint=cls.sprint, user=user)

    def setUp(self):
        self.client.force_login(self.staff)

    def test_list_shows_sprint_pods_with_activity_and_filters_by_sprint(self):
        sprint_pod = Pod.objects.create(sprint=self.sprint, name='Sprint pod', purpose='p', source='studio')
        other = Pod.objects.create(sprint=self.other_sprint, name='Other sprint pod', purpose='p', source='studio')
        cohort_pod = Pod.objects.create(cohort=self.cohort, name='Cohort pod', purpose='p', source='studio')
        everything = self.client.get('/studio/pods/')
        for pod in (sprint_pod, other, cohort_pod):
            self.assertContains(everything, f'data-pod-id="{pod.pk}"')
        self.assertContains(everything, '<option value="%s"' % self.sprint.pk)
        filtered = self.client.get(f'/studio/pods/?sprint={self.sprint.pk}')
        self.assertContains(filtered, f'data-pod-id="{sprint_pod.pk}"')
        self.assertNotContains(filtered, f'data-pod-id="{other.pk}"')
        self.assertNotContains(filtered, f'data-pod-id="{cohort_pod.pk}"')
        body = filtered.content.decode()
        cell = body[body.index('data-testid="studio-pod-activity"'):]
        cell = cell[:cell.index('</td>')]
        self.assertIn('Spring sprint', cell)
        self.assertIn('Sprint', cell)
        self.assertIn(f'href="/studio/pods/new?sprint={self.sprint.pk}"', body)

    def test_create_page_preselects_the_sprint(self):
        response = self.client.get(f'/studio/pods/new?sprint={self.sprint.pk}')
        self.assertContains(response, f'<option value="sprint:{self.sprint.pk}" selected>Spring sprint</option>', html=True)
        self.assertContains(response, '<optgroup label="Sprints">')
        self.assertContains(response, '<optgroup label="Cohorts">')

    def test_create_sprint_pod_reports_non_enrolled_emails_and_is_idempotent(self):
        response = self.client.post('/studio/pods/new', {
            'activity': f'sprint:{self.sprint.pk}', 'name': 'Agents', 'purpose': 'Ship',
            'max_members': '4', 'meeting_count': '1', 'meeting_minutes': '60',
            'owner_email': 'anna@test.com', 'member_emails': 'mike@test.com\nout@test.com',
        }, follow=True)
        pod = Pod.objects.get(name='Agents')
        self.assertEqual(pod.sprint_id, self.sprint.pk)
        self.assertEqual(set(pod.memberships.values_list('user__email', flat=True)), {'anna@test.com', 'mike@test.com'})
        self.assertContains(response, 'data-testid="studio-pod-result-not_enrolled"')
        self.assertContains(response, 'out@test.com')
        again = self.client.post('/studio/pods/new', {
            'activity': f'sprint:{self.sprint.pk}', 'name': 'agents', 'purpose': 'Ship',
            'max_members': '4', 'meeting_count': '1', 'meeting_minutes': '60',
        }, follow=True)
        self.assertEqual(Pod.objects.filter(sprint=self.sprint).count(), 1)
        self.assertContains(again, 'already exists in this sprint')

    def test_create_rejects_a_non_enrolled_owner(self):
        response = self.client.post('/studio/pods/new', {
            'activity': f'sprint:{self.sprint.pk}', 'name': 'Agents', 'purpose': 'Ship',
            'max_members': '4', 'meeting_count': '1', 'meeting_minutes': '60', 'owner_email': 'out@test.com',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'out@test.com is not enrolled in this sprint.', status_code=400)
        self.assertFalse(Pod.objects.exists())

    def test_detail_shows_sprint_activity_and_member_page_link(self):
        pod = Pod.objects.create(sprint=self.sprint, name='Agents', purpose='p', source='studio')
        PodMembership.objects.create(pod=pod, user=self.anna, source='staff')
        response = self.client.get(f'/studio/pods/{pod.pk}/')
        self.assertContains(response, 'Spring sprint')
        self.assertContains(response, f'href="/sprints/spring-sprint/pods/{pod.pk}"')
        self.assertNotContains(response, 'data-testid="studio-pod-not-enabled"')
        off = Pod.objects.create(sprint=self.other_sprint, name='Off', purpose='p', source='studio')
        response = self.client.get(f'/studio/pods/{off.pk}/')
        self.assertContains(response, 'Members cannot see pods for this sprint yet.')

    def test_sprint_form_saves_the_pods_checkbox(self):
        data = {
            'name': 'Autumn sprint', 'slug': 'autumn-sprint', 'start_date': '2026-10-01',
            'duration_weeks': '6', 'status': 'active', 'min_tier_level': '20', 'pods_enabled': '1',
        }
        form = self.client.get(f'/studio/sprints/{self.other_sprint.pk}/edit')
        self.assertContains(form, 'Show pods to enrolled members')
        self.client.post(f'/studio/sprints/{self.other_sprint.pk}/edit', data)
        self.other_sprint.refresh_from_db()
        self.assertTrue(self.other_sprint.pods_enabled)
        data.pop('pods_enabled')
        self.client.post(f'/studio/sprints/{self.other_sprint.pk}/edit', data)
        self.other_sprint.refresh_from_db()
        self.assertFalse(self.other_sprint.pods_enabled)

    def test_sprint_detail_pods_card(self):
        Pod.objects.create(sprint=self.sprint, name='One', purpose='p', source='studio')
        Pod.objects.create(sprint=self.sprint, name='Two', purpose='p', source='studio')
        on = self.client.get(f'/studio/sprints/{self.sprint.pk}/')
        self.assertContains(on, f'href="/studio/pods/?sprint={self.sprint.pk}"')
        self.assertContains(on, '2 pods')
        self.assertContains(on, f'href="/studio/pods/new?sprint={self.sprint.pk}"')
        self.assertNotContains(on, 'data-testid="sprint-pods-off"')
        off = self.client.get(f'/studio/sprints/{self.other_sprint.pk}/')
        self.assertContains(off, '0 pods')
        self.assertContains(off, 'Pods are off for this sprint. Turn them on in')
