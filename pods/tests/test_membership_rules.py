"""Join requests, waiting list, capacity and leave rules (issue #1918)."""

import datetime

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings, tag

from content.models import CohortEnrollment
from notifications.models import Notification
from plans.models import Sprint
from pods.models import (
    POD_SOURCE_STUDIO,
    Pod,
    PodJoinRequest,
    PodMembership,
)
from pods.services import membership as svc
from pods.tests.fixtures import enroll, make_cohort, make_course, make_user


def _staff_pod(cohort, owner=None, members=(), max_members=4, name='Evening builders'):
    pod = Pod.objects.create(
        cohort=cohort, name=name, purpose='Ship things', max_members=max_members,
        owner=owner, source=POD_SOURCE_STUDIO,
    )
    for user in members:
        PodMembership.objects.create(pod=pod, user=user, source='staff')
    return pod


@tag('core')
class PodRulesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        cls.other_cohort = make_cohort(cls.course, key='5', name='Cohort 5')
        cls.staff = make_user('admin@test.com', staff=True)
        cls.users = []
        for name in ('anna', 'raj', 'mike', 'lea', 'tom', 'zoe'):
            user = make_user(f'{name}@test.com', first_name=name.title(), last_name='K')
            enroll(user, cls.cohort)
            cls.users.append(user)
        cls.anna, cls.raj, cls.mike, cls.lea, cls.tom, cls.zoe = cls.users

    def test_pod_without_or_with_both_activities_is_rejected_by_db(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Pod.objects.create(name='None', purpose='x')
        sprint = Sprint.objects.create(name='S', slug='s', start_date=datetime.date(2026, 1, 5))  # date-rot-ok: fixed sprint row
        with self.assertRaises(IntegrityError), transaction.atomic():
            Pod.objects.create(name='Both', purpose='x', cohort=self.cohort, sprint=sprint)

    def test_self_paced_cohort_cannot_have_pods(self):
        self_paced = make_cohort(self.course, key='sp', name='Self-paced', mode='self_paced')
        enroll(self.anna, self_paced)
        with self.assertRaisesMessage(svc.PodError, svc.MSG_SELF_PACED):
            svc.start_member_pod(self.anna, self_paced, {'name': 'X', 'purpose': 'Y'})
        with self.assertRaisesMessage(svc.PodError, svc.MSG_SELF_PACED):
            svc.create_staff_pod(cohort=self_paced, data={'name': 'X', 'purpose': 'Y'}, source='studio')
        self.assertFalse(Pod.objects.filter(cohort=self_paced).exists())

    def test_member_pod_of_one_makes_creator_owner_and_first_member(self):
        pod = svc.start_member_pod(self.anna, self.cohort, {'name': 'RAG evals', 'purpose': 'Evals'})
        self.assertEqual(pod.owner, self.anna)
        self.assertEqual(pod.status, 'open')
        self.assertEqual(
            list(pod.memberships.values_list('user__email', 'source')),
            [('anna@test.com', 'creator')],
        )

    @override_settings(PODS_DEFAULT_MAX_MEMBERS='6')
    def test_member_pod_uses_configured_default_size(self):
        pod = svc.start_member_pod(self.anna, self.cohort, {'name': 'A', 'purpose': 'B'})
        self.assertEqual(pod.max_members, 6)

    def test_third_member_started_pod_is_rejected(self):
        svc.start_member_pod(self.anna, self.cohort, {'name': 'One', 'purpose': 'x'})
        svc.start_member_pod(self.anna, self.cohort, {'name': 'Two', 'purpose': 'x'})
        with self.assertRaisesMessage(svc.PodError, 'You can start up to 2 pods in this cohort.'):
            svc.start_member_pod(self.anna, self.cohort, {'name': 'Three', 'purpose': 'x'})
        self.assertEqual(Pod.objects.filter(created_by=self.anna).count(), 2)

    def test_request_is_pending_with_free_seat_and_waitlisted_when_full(self):
        open_pod = _staff_pod(self.cohort, owner=self.raj, members=[self.raj])
        full_pod = _staff_pod(self.cohort, members=[self.raj, self.mike], max_members=2, name='Full')
        self.assertEqual(svc.request_to_join(open_pod, self.anna).status, 'pending')
        self.assertEqual(svc.request_to_join(full_pod, self.anna).status, 'waitlisted')

    def test_second_open_request_on_same_pod_is_rejected(self):
        pod = _staff_pod(self.cohort, members=[self.raj])
        svc.request_to_join(pod, self.anna)
        with self.assertRaisesMessage(svc.PodError, svc.MSG_ALREADY_REQUESTED):
            svc.request_to_join(pod, self.anna)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PodJoinRequest.objects.create(pod=pod, user=self.anna, status='waitlisted')

    def test_open_request_limit_counts_only_this_cohort(self):
        pods = [_staff_pod(self.cohort, members=[self.raj], name=f'P{i}') for i in range(4)]
        enroll(self.anna, self.other_cohort)
        other = _staff_pod(self.other_cohort, members=[], name='Other')
        svc.request_to_join(other, self.anna)
        for pod in pods[:3]:
            svc.request_to_join(pod, self.anna)
        with self.assertRaisesMessage(
            svc.PodError,
            'You already have 3 open requests in this cohort. Withdraw one to request another pod.',
        ):
            svc.request_to_join(pods[3], self.anna)
        self.assertFalse(pods[3].join_requests.exists())

    def test_new_pending_request_notifies_owner_but_waitlisted_does_not(self):
        pod = _staff_pod(self.cohort, owner=self.raj, members=[self.raj], max_members=2)
        svc.request_to_join(pod, self.anna)
        note = Notification.objects.get(user=self.raj)
        self.assertEqual(note.title, 'New request to join Evening builders')
        self.assertEqual(note.notification_type, 'pod_request')
        self.assertEqual(note.url, f'/courses/{self.course.slug}/home/pods/{pod.pk}')
        PodMembership.objects.create(pod=pod, user=self.mike, source='staff')
        svc.request_to_join(pod, self.lea)
        self.assertEqual(Notification.objects.filter(user=self.raj).count(), 1)

    def test_approving_last_seat_waitlists_remaining_pending_requests(self):
        pod = _staff_pod(self.cohort, owner=self.raj, members=[self.raj], max_members=2)
        first = svc.request_to_join(pod, self.anna)
        second = svc.request_to_join(pod, self.mike)
        svc.approve_request(first, self.raj)
        second.refresh_from_db()
        self.assertEqual(second.status, 'waitlisted')
        first.refresh_from_db()
        self.assertEqual((first.status, first.decided_by), ('approved', self.raj))
        note = Notification.objects.get(user=self.anna)
        self.assertEqual(
            (note.title, note.notification_type),
            ('You joined Evening builders', 'pod_request_decided'),
        )

    def test_approve_on_full_pod_fails_for_owner_and_staff(self):
        pod = _staff_pod(self.cohort, owner=self.raj, members=[self.raj, self.mike, self.lea, self.tom])
        waitlisted = svc.request_to_join(pod, self.anna)
        for actor in (self.raj, self.staff):
            with self.assertRaisesMessage(svc.PodError, 'This pod is full (4 of 4). Raise the size limit first.'):
                svc.approve_request(waitlisted, actor)
        self.assertFalse(pod.memberships.filter(user=self.anna).exists())

    def test_member_leaving_full_pod_moves_only_oldest_waitlisted_to_pending(self):
        pod = _staff_pod(self.cohort, owner=self.raj, members=[self.raj, self.mike], max_members=2)
        oldest = svc.request_to_join(pod, self.anna)
        newer = svc.request_to_join(pod, self.lea)
        Notification.objects.all().delete()
        svc.remove_member(pod, self.mike)
        oldest.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual((oldest.status, newer.status), ('pending', 'waitlisted'))
        note = Notification.objects.get(user=self.raj)
        self.assertEqual((note.title, note.notification_type), ('A seat opened in Evening builders', 'pod_request'))

    def test_raising_size_limit_by_two_moves_two_oldest_waitlisted(self):
        pod = _staff_pod(self.cohort, members=[self.raj, self.mike], max_members=2)
        a = svc.request_to_join(pod, self.anna)
        b = svc.request_to_join(pod, self.lea)
        c = svc.request_to_join(pod, self.tom)
        svc.update_pod(pod, {'max_members': 4}, staff=True)
        statuses = [PodJoinRequest.objects.get(pk=r.pk).status for r in (a, b, c)]
        self.assertEqual(statuses, ['pending', 'pending', 'waitlisted'])

    def test_size_limit_cannot_go_below_member_count(self):
        pod = _staff_pod(self.cohort, members=[self.raj, self.mike, self.lea])
        with self.assertRaisesMessage(svc.PodError, 'The size limit cannot be lower than the current number of members (3).'):
            svc.update_pod(pod, {'max_members': 2}, staff=True)
        pod.refresh_from_db()
        self.assertEqual(pod.max_members, 4)

    def test_owner_leaving_passes_ownership_to_earliest_joined(self):
        pod = svc.start_member_pod(self.anna, self.cohort, {'name': 'A', 'purpose': 'B'})
        PodMembership.objects.create(pod=pod, user=self.raj, source='request')
        PodMembership.objects.create(pod=pod, user=self.mike, source='request')
        svc.remove_member(pod, self.anna)
        pod.refresh_from_db()
        self.assertEqual(pod.owner, self.raj)

    def test_last_member_leaving_member_pod_archives_and_cancels_requests(self):
        pod = svc.start_member_pod(self.anna, self.cohort, {'name': 'A', 'purpose': 'B'})
        request = svc.request_to_join(pod, self.raj)
        svc.remove_member(pod, self.anna)
        pod.refresh_from_db()
        request.refresh_from_db()
        self.assertEqual((pod.status, request.status), ('archived', 'cancelled'))

    def test_staff_pod_stays_open_when_empty(self):
        pod = _staff_pod(self.cohort, members=[self.raj])
        svc.remove_member(pod, self.raj)
        pod.refresh_from_db()
        self.assertEqual(pod.status, 'open')

    def test_unenrollment_removes_from_cohort_pods_only(self):
        enroll(self.anna, self.other_cohort)
        pod = _staff_pod(self.cohort, members=[self.anna, self.raj])
        other_member_pod = _staff_pod(self.other_cohort, members=[self.anna], name='Other')
        requested = _staff_pod(self.cohort, members=[self.mike], name='Requested')
        other_requested = _staff_pod(self.other_cohort, members=[], name='Other requested')
        req = svc.request_to_join(requested, self.anna)
        other_req = svc.request_to_join(other_requested, self.anna)
        CohortEnrollment.objects.get(user=self.anna, cohort=self.cohort).delete()
        req.refresh_from_db()
        other_req.refresh_from_db()
        self.assertFalse(pod.memberships.filter(user=self.anna).exists())
        self.assertTrue(other_member_pod.memberships.filter(user=self.anna).exists())
        self.assertEqual((req.status, other_req.status), ('cancelled', 'pending'))

    def test_decline_notifies_requester_with_pods_tab_link(self):
        pod = _staff_pod(self.cohort, owner=self.raj, members=[self.raj])
        request = svc.request_to_join(pod, self.anna)
        svc.decline_request(request, self.raj)
        note = Notification.objects.get(user=self.anna)
        self.assertEqual(note.title, 'Your request to join Evening builders was not accepted')
        self.assertEqual(note.body, 'Browse other pods or start your own.')
        self.assertEqual(note.url, f'/courses/{self.course.slug}/home/pods?cohort=4')
        with self.assertRaisesMessage(svc.PodError, svc.MSG_REQUEST_NOT_OPEN):
            svc.approve_request(request, self.staff)

    def test_staff_add_by_email_buckets_and_closes_open_request(self):
        outsider = make_user('main@test.com')
        pod = _staff_pod(self.cohort, members=[self.raj], max_members=3)
        request = svc.request_to_join(pod, self.anna)
        results = svc.add_members_by_email(
            pod,
            ['anna@test.com', 'RAJ@test.com', 'main@test.com', 'ghost@test.com', 'mike@test.com', 'lea@test.com'],
            actor=self.staff, source='staff',
        )
        self.assertEqual(results.as_dict(), {
            'added': ['anna@test.com', 'mike@test.com'],
            'not_enrolled': [outsider.email],
            'unknown_user': ['ghost@test.com'],
            'already_member': ['raj@test.com'],
            'over_capacity': ['lea@test.com'],
        })
        request.refresh_from_db()
        self.assertEqual(request.status, 'approved')

    def test_withdraw_sets_withdrawn(self):
        pod = _staff_pod(self.cohort, members=[self.raj])
        request = svc.request_to_join(pod, self.anna)
        svc.withdraw_request(request, self.anna)
        request.refresh_from_db()
        self.assertEqual(request.status, 'withdrawn')
