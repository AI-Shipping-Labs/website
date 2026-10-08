"""Studio pods pages (issue #1918)."""

import datetime

from django.test import TestCase, override_settings, tag
from django.utils import timezone

from notifications.models import Notification
from pods.models import POD_SOURCE_STUDIO, Pod, PodJoinRequest, PodMembership
from pods.services import membership as svc
from pods.tests.fixtures import enroll, make_cohort, make_course, make_user


@tag('core')
class StudioPodsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        cls.other_cohort = make_cohort(cls.course, key='5', name='Cohort 5')
        cls.self_paced = make_cohort(cls.course, key='sp', name='Self-paced', mode='self_paced')
        cls.staff = make_user('admin@test.com', staff=True)
        cls.member = make_user('member@test.com')
        cls.anna = make_user('anna@test.com', first_name='Anna')
        cls.raj = make_user('raj@test.com', first_name='Raj')
        cls.mike = make_user('mike@test.com', first_name='Mike')
        cls.outsider = make_user('main@test.com')
        for user in (cls.anna, cls.raj, cls.mike):
            enroll(user, cls.cohort)

    def setUp(self):
        self.client.force_login(self.staff)

    def _pod(self, name='Evening builders', cohort=None, members=(), owner=None, max_members=4):
        pod = Pod.objects.create(
            cohort=cohort or self.cohort, name=name, purpose='Ship', owner=owner,
            max_members=max_members, source=POD_SOURCE_STUDIO,
        )
        for user in members:
            PodMembership.objects.create(pod=pod, user=user, source='staff')
        return pod

    def test_non_staff_is_denied_without_side_effects(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get('/studio/pods/').status_code, 403)
        response = self.client.post('/studio/pods/new', {
            'cohort': self.cohort.pk, 'name': 'X', 'purpose': 'Y',
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Pod.objects.exists())

    def test_list_columns_filters_and_stale_pill(self):
        stale = self._pod('Evening builders', members=[self.raj], owner=self.raj)
        request = svc.request_to_join(stale, self.anna)
        PodJoinRequest.objects.filter(pk=request.pk).update(created_at=timezone.now() - datetime.timedelta(days=6))
        fresh = self._pod('Morning crew', members=[self.mike])
        svc.request_to_join(fresh, self.anna)
        other = self._pod('Other cohort pod', cohort=self.other_cohort)
        Pod.objects.filter(pk=other.pk).update(status='archived')
        response = self.client.get('/studio/pods/')
        self.assertTemplateUsed(response, 'studio/pods/list.html')
        rows = {row['pod'].name: row for row in response.context['rows']}
        self.assertTrue(rows['Evening builders']['stale'])
        self.assertEqual(rows['Evening builders']['oldest_age'], '6 days ago')
        self.assertFalse(rows['Morning crew']['stale'])
        self.assertEqual(
            (rows['Evening builders']['pod'].member_total, rows['Evening builders']['pod'].pending_total),
            (1, 1),
        )
        filtered = self.client.get(f'/studio/pods/?cohort={self.other_cohort.pk}&status=archived')
        self.assertEqual([r['pod'].name for r in filtered.context['rows']], ['Other cohort pod'])
        searched = self.client.get('/studio/pods/?q=morning')
        self.assertEqual([r['pod'].name for r in searched.context['rows']], ['Morning crew'])

    def test_create_with_owner_and_members_reports_results(self):
        response = self.client.post('/studio/pods/new', {
            'cohort': self.cohort.pk, 'name': 'Agents pod', 'purpose': 'Ship an agent demo',
            'max_members': '4', 'meeting_count': '1', 'meeting_minutes': '60',
            'owner_email': 'anna@test.com',
            'member_emails': 'anna@test.com\nraj@test.com\nmain@test.com\nghost@test.com',
        }, follow=True)
        pod = Pod.objects.get(name='Agents pod')
        self.assertEqual((pod.owner, pod.source), (self.anna, 'studio'))
        self.assertEqual(
            set(pod.memberships.values_list('user__email', flat=True)),
            {'anna@test.com', 'raj@test.com'},
        )
        memberships = list(pod.memberships.all())
        self.assertEqual({m.source for m in memberships}, {'staff'})
        for membership in memberships:
            membership.full_clean()
        self.assertContains(response, 'data-testid="studio-pod-member-source">Staff</td>', count=2)
        again = self.client.post('/studio/pods/new', {
            'cohort': self.cohort.pk, 'name': 'agents pod', 'purpose': 'Ship an agent demo',
            'member_emails': 'raj@test.com\nmike@test.com',
        }, follow=True)
        self.assertEqual(again.redirect_chain[-1][0], f'/studio/pods/{pod.pk}/')
        self.assertEqual(Pod.objects.filter(cohort=self.cohort).count(), 1)
        self.assertEqual(
            {b['key']: b['emails'] for b in again.context['add_results']},
            {'added': ['mike@test.com'], 'already_member': ['raj@test.com']},
        )
        results = {b['key']: b['emails'] for b in response.context['add_results']}
        self.assertEqual(results, {
            'added': ['anna@test.com', 'raj@test.com'],
            'not_enrolled': ['main@test.com'],
            'unknown_user': ['ghost@test.com'],
        })

    def test_create_on_self_paced_cohort_is_rejected(self):
        response = self.client.post('/studio/pods/new', {
            'cohort': self.self_paced.pk, 'name': 'X', 'purpose': 'Y',
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context['error'], svc.MSG_SELF_PACED)
        self.assertFalse(Pod.objects.exists())

    def test_detail_edit_archive_owner_change_and_size_rule(self):
        pod = self._pod(members=[self.anna, self.raj], owner=self.anna)
        response = self.client.post(f'/studio/pods/{pod.pk}/', {
            'name': 'Renamed', 'purpose': 'New', 'max_members': '5', 'meeting_count': '2',
            'meeting_minutes': '45', 'status': 'archived', 'owner': str(self.raj.pk),
        })
        self.assertRedirects(response, f'/studio/pods/{pod.pk}/')
        pod.refresh_from_db()
        self.assertEqual((pod.name, pod.max_members, pod.status, pod.owner), ('Renamed', 5, 'archived', self.raj))
        response = self.client.post(f'/studio/pods/{pod.pk}/', {
            'name': 'Renamed', 'purpose': 'New', 'max_members': '1', 'meeting_count': '2',
            'meeting_minutes': '45', 'status': 'open', 'owner': '',
        })
        self.assertEqual(response.context['error'], 'The size limit cannot be lower than the current number of members (2).')
        pod.refresh_from_db()
        self.assertEqual((pod.max_members, pod.status), (5, 'archived'))

    def test_add_remove_members_and_override_requests(self):
        pod = self._pod(members=[self.raj], owner=self.raj, max_members=2)
        pending = svc.request_to_join(pod, self.anna)
        self.client.post(f'/studio/pods/{pod.pk}/requests/{pending.pk}/approve')
        self.assertTrue(pod.memberships.filter(user=self.anna).exists())
        self.assertTrue(Notification.objects.filter(user=self.anna, title='You joined Evening builders').exists())
        waiting = svc.request_to_join(pod, self.mike)
        response = self.client.post(f'/studio/pods/{pod.pk}/requests/{waiting.pk}/approve', follow=True)
        self.assertIn('This pod is full (2 of 2). Raise the size limit first.', [str(m) for m in response.context['messages']])
        self.client.post(f'/studio/pods/{pod.pk}/members/{self.anna.pk}/remove')
        self.assertFalse(pod.memberships.filter(user=self.anna).exists())
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, 'pending')
        self.client.post(f'/studio/pods/{pod.pk}/requests/{waiting.pk}/decline')
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, 'declined')
        response = self.client.post(f'/studio/pods/{pod.pk}/members/add', {'emails': 'mike@test.com\nmain@test.com'}, follow=True)
        results = {b['key']: b['emails'] for b in response.context['add_results']}
        self.assertEqual(results, {'added': ['mike@test.com'], 'not_enrolled': ['main@test.com']})

    def test_cohort_list_links_dated_cohorts_to_filtered_pods(self):
        self._pod(members=[self.raj])
        response = self.client.get(f'/studio/courses/{self.course.pk}/cohorts/')
        self.assertContains(response, f'href="/studio/pods/?cohort={self.cohort.pk}"')
        self.assertContains(response, 'Pods (1)')
        self.assertNotContains(response, f'href="/studio/pods/?cohort={self.self_paced.pk}"')

    @override_settings(PODS_COURSE_SLUGS='')
    def test_detail_links_to_member_view_and_warns_when_disabled(self):
        pod = self._pod(members=[self.raj])
        response = self.client.get(f'/studio/pods/{pod.pk}/')
        self.assertContains(response, f'href="/courses/{self.course.slug}/home/pods/{pod.pk}"')
        self.assertContains(response, 'data-testid="studio-pod-not-enabled"')
