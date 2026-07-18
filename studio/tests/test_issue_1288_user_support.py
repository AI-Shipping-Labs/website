"""Regression coverage for the consolidated user/CRM support surface."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import TierOverride
from crm.models import CRMRecord
from payments.models import Tier
from plans.models import InterviewNote

User = get_user_model()


class Issue1288StudioTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-1288@test.com', password='pw', is_staff=True,
        )
        cls.main = Tier.objects.filter(slug='main').first() or Tier.objects.create(
            slug='main', name='Main', level=20,
        )

    def setUp(self):
        self.client.login(email=self.staff.email, password='pw')

    def test_user_sort_nulls_last_and_csv_uses_same_order(self):
        never = User.objects.create_user(email='never-1288@test.com')
        recent = User.objects.create_user(email='recent-1288@test.com')
        recent.last_login = timezone.now()
        recent.save(update_fields=['last_login'])

        response = self.client.get('/studio/users/?sort=-last_login')
        emails = [row['email'] for row in response.context['page'].object_list]
        self.assertLess(emails.index(recent.email), emails.index(never.email))
        self.assertContains(response, 'aria-sort="descending"')
        export = self.client.get('/studio/users/export?sort=-last_login')
        text = export.content.decode()
        self.assertLess(text.index(recent.email), text.index(never.email))

    def test_alias_card_is_exactly_once_immediately_after_profile(self):
        member = User.objects.create_user(email='alias-order-1288@test.com')
        body = self.client.get(f'/studio/users/{member.pk}/').content.decode()
        self.assertEqual(body.count('data-testid="user-aliases-section"'), 1)
        profile = body.index('data-testid="user-detail-profile-section"')
        aliases = body.index('data-testid="user-aliases-section"')
        membership = body.index('data-testid="user-detail-membership-section"')
        self.assertLess(profile, aliases)
        self.assertLess(aliases, membership)
        self.assertContains(response := self.client.get(f'/studio/users/{member.pk}/'), 'for="user-alias-email"')
        self.assertContains(response, 'for="user-alias-note"')

    def test_subscription_group_has_exact_cached_fields_and_operator_date(self):
        member = User.objects.create_user(email='subscription-ui-1288@test.com')
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertContains(response, 'No active subscription')
        self.assertContains(response, 'No renewal date cached. Use Sync from Stripe.')
        self.assertEqual(response.content.decode().count('&mdash;'), 2)

        member.tier = Tier.objects.get(slug='main')
        member.subscription_id = 'sub_ui_1288'
        member.billing_period_end = datetime.datetime(
            2027, 2, 3, 12, 0, tzinfo=datetime.UTC,
        )
        member.save(update_fields=['tier', 'subscription_id', 'billing_period_end'])
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertContains(response, 'data-testid="user-detail-subscription-plan">Main')
        self.assertContains(response, 'data-testid="user-detail-subscription-status">\n              Active')
        self.assertContains(response, '>Renews</dt>')
        self.assertContains(response, 'data-testid="user-detail-subscription-date">2027-02-03')

    def test_crm_exact_tag_filter_composes_with_status(self):
        matching = User.objects.create_user(
            email='matching-1288@test.com', tags=['early-adopter'],
        )
        substring = User.objects.create_user(
            email='substring-1288@test.com', tags=['early-adopter-vip'],
        )
        CRMRecord.objects.create(user=matching, created_by=self.staff)
        CRMRecord.objects.create(user=substring, created_by=self.staff)
        response = self.client.get('/studio/crm/?filter=active&tag=Early_Adopter')
        self.assertContains(response, matching.email)
        self.assertNotContains(response, substring.email)
        self.assertEqual(response.context['active_tag'], 'early-adopter')

    def test_note_next_accepts_studio_path_and_rejects_encoded_external(self):
        member = User.objects.create_user(email='notes-1288@test.com')
        good = '/studio/crm/123/#member-notes'
        response = self.client.post(
            f'/studio/users/{member.pk}/notes/new',
            {'body': 'A note', 'kind': 'general', 'visibility': 'internal', 'next': good},
        )
        self.assertRedirects(response, good, fetch_redirect_response=False)
        note = InterviewNote.objects.get(member=member)
        response = self.client.post(
            f'/studio/users/{member.pk}/notes/{note.pk}/edit',
            {
                'body': 'Updated', 'kind': 'general', 'visibility': 'internal',
                'next': '%2F%2Fevil.example%2Fstudio%2Fcrm%2F1',
            },
        )
        self.assertRedirects(
            response, f'/studio/users/{member.pk}/#member-notes',
            fetch_redirect_response=False,
        )
        response = self.client.post(
            f'/studio/users/{member.pk}/notes/{note.pk}/edit',
            {
                'body': 'Traversal rejected', 'kind': 'general',
                'visibility': 'internal',
                'next': '/studio/../../outside',
            },
        )
        self.assertRedirects(
            response, f'/studio/users/{member.pk}/#member-notes',
            fetch_redirect_response=False,
        )

    def test_custom_override_expiry_is_end_of_utc_date_and_past_is_atomic(self):
        member = User.objects.create_user(email='expiry-1288@test.com')
        future = timezone.now().date() + datetime.timedelta(days=5)
        response = self.client.post(
            f'/studio/users/{member.pk}/tier_override/create',
            {'tier_id': self.main.pk, 'custom_expiry': '1', 'expires_at': future.isoformat()},
        )
        self.assertEqual(response.status_code, 302)
        override = TierOverride.objects.get(user=member, is_active=True)
        self.assertEqual(override.expires_at.date(), future)
        self.assertEqual(override.expires_at.time(), datetime.time(23, 59, 59))

        past = timezone.now().date() - datetime.timedelta(days=1)
        self.client.post(
            f'/studio/users/{member.pk}/tier_override/create',
            {'tier_id': self.main.pk, 'custom_expiry': '1', 'expires_at': past.isoformat()},
        )
        override.refresh_from_db()
        self.assertTrue(override.is_active)

    def test_slack_check_write_permissions_and_method(self):
        member = User.objects.create_user(email='permission-1288@test.com')
        url = f'/studio/users/{member.pk}/slack-membership/check'
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)
        regular = User.objects.create_user(
            email='regular-1288@test.com', password='pw', is_staff=False,
        )
        self.client.login(email=regular.email, password='pw')
        self.assertEqual(self.client.post(url).status_code, 403)
