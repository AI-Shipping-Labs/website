"""Responsive CRM recent-activity regression coverage for issue #1305."""

from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from analytics.models import UserActivity
from crm.models import CRMRecord

User = get_user_model()


class _ActivityMarkupParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        testid = attributes.get('data-testid', '')
        if testid.startswith('crm-activity'):
            self.tags.append((tag, attributes))

    def matching(self, testid):
        return [attrs for _, attrs in self.tags if attrs.get('data-testid') == testid]


class CRMActivityResponsiveTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-1305@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member-1305@test.com', password='pw',
        )
        cls.non_staff = User.objects.create_user(
            email='nonstaff-1305@test.com', password='pw',
        )
        cls.record = CRMRecord.objects.create(user=cls.member)
        UserActivity.objects.filter(user=cls.member).delete()

    def setUp(self):
        self.client.force_login(self.staff)
        UserActivity.objects.filter(user=self.member).delete()

    def _url(self):
        return reverse('studio_crm_detail', args=[self.record.pk])

    def _add(self, event_type, label, *, target_url=''):
        return UserActivity.objects.create(
            user=self.member,
            event_type=event_type,
            label=label,
            target_url=target_url,
            occurred_at=timezone.now(),
        )

    def _parse(self, response):
        parser = _ActivityMarkupParser()
        parser.feed(response.content.decode())
        return parser

    def test_filter_controls_are_accessible_non_wrapping_and_query_preserving(self):
        self._add(UserActivity.EVENT_COURSE_ENROLL, 'Enrolled in course')
        response = self.client.get(
            self._url(),
            {'activity_category': 'learning', 'source': 'backlog'},
        )
        parser = self._parse(response)

        filters = parser.matching('crm-activity-filters')
        self.assertEqual(len(filters), 1)
        self.assertEqual(filters[0]['role'], 'group')
        self.assertEqual(
            filters[0]['aria-label'], 'Filter activity by category',
        )
        self.assertIn('flex-nowrap', filters[0]['class'])
        self.assertIn('overflow-x-auto', filters[0]['class'])
        self.assertIn('sm:flex-wrap', filters[0]['class'])

        chips = [
            attrs for _, attrs in parser.tags
            if attrs.get('data-testid', '').startswith('crm-activity-filter-')
        ]
        self.assertEqual(len(chips), 6)
        self.assertTrue(all('min-h-[44px]' in chip['class'] for chip in chips))
        self.assertTrue(all('sm:min-h-0' in chip['class'] for chip in chips))
        self.assertTrue(all('focus-visible:ring-2' in chip['class'] for chip in chips))
        current = [chip for chip in chips if chip.get('aria-current') == 'true']
        self.assertEqual(
            [chip['data-testid'] for chip in current],
            ['crm-activity-filter-learning'],
        )
        for chip in chips:
            self.assertIn('source=backlog', chip['href'])

    def test_activity_rows_are_label_first_on_mobile_and_compact_on_desktop(self):
        linked_label = 'Viewed article: ' + ('a' * 239)
        self._add(
            UserActivity.EVENT_RESOURCE_VIEW,
            linked_label[:255],
            target_url='/blog/responsive-activity',
        )
        self._add(
            UserActivity.EVENT_EMAIL_CLICK,
            'person-with-one-unbroken-token-' + ('x' * 210),
            target_url='https://dashboard.stripe.com/customer/unsafe',
        )

        response = self.client.get(self._url())
        parser = self._parse(response)

        section = parser.matching('crm-activity-section')[0]
        self.assertIn('p-4', section['class'])
        self.assertIn('sm:p-6', section['class'])
        rows = parser.matching('crm-activity-row')
        self.assertEqual(len(rows), 2)
        self.assertTrue(all('sm:flex-row' in row['class'] for row in rows))

        label_wrappers = parser.matching('crm-activity-label-wrap')
        self.assertTrue(all('order-1' in item['class'] for item in label_wrappers))
        self.assertTrue(all('sm:order-2' in item['class'] for item in label_wrappers))
        self.assertTrue(
            all('[overflow-wrap:anywhere]' in item['class'] for item in label_wrappers),
        )
        metadata = parser.matching('crm-activity-metadata')
        self.assertTrue(all('flex-wrap' in item['class'] for item in metadata))
        self.assertTrue(all('sm:contents' in item['class'] for item in metadata))
        self.assertTrue(
            all('whitespace-nowrap' in item['class'] for item in parser.matching('crm-activity-time')),
        )
        self.assertContains(response, linked_label[:255])
        self.assertContains(response, 'href="/blog/responsive-activity"')
        self.assertNotContains(
            response, 'href="https://dashboard.stripe.com/customer/unsafe"',
        )

    def test_filtered_empty_state_links_to_all_and_keeps_other_sections(self):
        self._add(UserActivity.EVENT_COURSE_ENROLL, 'Learning activity')
        response = self.client.get(
            self._url(),
            {'activity_category': 'events', 'source': 'backlog'},
        )

        self.assertContains(
            response, 'No Events activity recorded for this member.',
        )
        self.assertContains(response, 'data-testid="crm-activity-view-all"')
        self.assertContains(
            response,
            'href="?activity_category=all&amp;source=backlog"',
        )
        self.assertNotContains(response, 'No recorded activity yet.')
        for testid in (
            'crm-snapshot-card',
            'crm-plans-section',
            'crm-booked-calls-section',
            'crm-onboarding-section',
            'crm-notes-section',
        ):
            self.assertContains(response, f'data-testid="{testid}"')

    def test_global_empty_state_is_only_used_when_no_activity_exists(self):
        response = self.client.get(
            self._url(), {'activity_category': 'events'},
        )

        self.assertContains(
            response,
            'No recorded activity yet. Activity will appear here as the member uses the site.',
        )
        self.assertNotContains(response, 'crm-activity-filter-empty')
        self.assertNotContains(response, 'View all activity')

    def test_crm_activity_remains_staff_only(self):
        self._add(UserActivity.EVENT_SIGNUP, 'Private activity label')
        self.client.logout()

        anonymous = self.client.get(self._url())
        self.assertEqual(anonymous.status_code, 302)
        self.assertNotIn('Private activity label', anonymous.content.decode())

        self.client.force_login(self.non_staff)
        non_staff = self.client.get(self._url())
        self.assertEqual(non_staff.status_code, 403)
        self.assertNotIn('Private activity label', non_staff.content.decode())
