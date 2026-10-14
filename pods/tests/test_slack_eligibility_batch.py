"""Batched member Slack eligibility (perf lane 2026-10-14).

The pod page resolved every member's tier separately (a membership row
plus the strongest active override per member, once for the Slack links
and again for the gate message), so a full pod cost several queries per
member just for the Slack section. These tests pin the batched map's
values and the pod page's flat query cost as membership grows.
"""

import datetime

from django.db import connection
from django.utils import timezone

from payments.models import Tier, TierOverride
from django.test import TestCase, tag
from django.test.utils import CaptureQueriesContext

from pods.services.people import is_slack_eligible, slack_eligibility_map
from pods.services.presentation import build_pod_page
from pods.tests.fixtures import (
    enroll,
    make_cohort,
    make_course,
    make_pod,
    make_user,
)
from tests.fixtures import set_membership


@tag('core')
class SlackEligibilityMapTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        cls.main = make_user('main-map@example.com', first_name='Main', last_name='One')
        set_membership(cls.main, tier=Tier.objects.get(slug='main'))
        cls.basic = make_user('basic-map@example.com', first_name='Basic', last_name='One')
        set_membership(cls.basic, tier=Tier.objects.get(slug='basic'))
        cls.staff = make_user('staff-map@example.com', staff=True)
        cls.no_membership = make_user('nomembership-map@example.com', first_name='No', last_name='Mem')

    def test_map_matches_is_slack_eligible(self):
        users = [self.main, self.basic, self.staff, self.no_membership]
        mapping = slack_eligibility_map(users)
        for user in users:
            with self.subTest(user=user.email):
                self.assertEqual(mapping[user.pk], is_slack_eligible(user))
        self.assertTrue(mapping[self.main.pk])
        self.assertTrue(mapping[self.staff.pk])
        self.assertFalse(mapping[self.basic.pk])
        self.assertFalse(mapping[self.no_membership.pk])

    def test_override_grants_main_without_extra_queries_per_user(self):
        override = TierOverride.objects.create(
            user=self.basic,
            override_tier=Tier.objects.get(slug='main'),
            is_active=True,
            expires_at=timezone.now() + datetime.timedelta(days=30),
        )
        try:
            with CaptureQueriesContext(connection) as captured:
                mapping = slack_eligibility_map([self.main, self.basic])
            self.assertTrue(mapping[self.basic.pk])
            # One membership query plus one override query, whatever the
            # member count.
            self.assertEqual(len(captured.captured_queries), 2)
        finally:
            override.delete()


@tag('core')
class PodPageEligibilityCostTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        cls.main_tier = Tier.objects.get(slug='main')
        cls.anna = make_user('anna-cost@example.com', first_name='Anna', last_name='Klein')
        set_membership(cls.anna, tier=cls.main_tier)
        enroll(cls.anna, cls.cohort)

    def _pod_with_members(self, count):
        members = [self.anna]
        for index in range(count - 1):
            member = make_user(
                f'cost-{count}-{index}@example.com',
                first_name='M', last_name=str(index),
            )
            set_membership(member, tier=Tier.objects.get(slug='basic'))
            enroll(member, self.cohort)
            members.append(member)
        return make_pod(self.cohort, f'Cost pod {count}', members, owner=self.anna)

    def test_pod_page_eligibility_queries_do_not_grow_per_member(self):
        def build(member_count):
            pod = self._pod_with_members(member_count)
            with CaptureQueriesContext(connection) as captured:
                context = build_pod_page(pod, self.anna)
            self.assertTrue(context['slack']['others_below_main'])
            return len(captured.captured_queries)

        small = build(2)
        large = build(12)
        self.assertLessEqual(
            large, small + 2,
            'pod page still resolves Slack eligibility per member',
        )
