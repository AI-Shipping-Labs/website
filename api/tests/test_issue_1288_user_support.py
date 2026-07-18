"""API regressions for issue #1288 user-support additions."""

import datetime
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import TierOverride, Token
from payments.models import Tier

User = get_user_model()


class Issue1288ApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='api-staff-1288@test.com', is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name='issue-1288')
        cls.main = Tier.objects.filter(slug='main').first() or Tier.objects.create(
            slug='main', name='Main', level=20,
        )

    def auth(self):
        return {'HTTP_AUTHORIZATION': f'Token {self.token.key}'}

    def test_users_sort_validation_and_subscription_summary(self):
        member = User.objects.create_user(
            email='sub-1288@test.com', tier=self.main,
            subscription_id='sub_1288',
            billing_period_end=timezone.now() + datetime.timedelta(days=30),
        )
        bad = self.client.get('/api/users?sort=nope', **self.auth())
        self.assertEqual(bad.status_code, 422)
        response = self.client.get(f'/api/users/{member.email}', **self.auth())
        self.assertEqual(response.json()['subscription']['status'], 'active')
        free = Tier.objects.get(slug='free')
        member.pending_tier = free
        member.save(update_fields=['pending_tier'])
        response = self.client.get(f'/api/users/{member.email}', **self.auth())
        self.assertEqual(
            response.json()['subscription']['status'], 'cancellation_scheduled',
        )

    def test_patch_slack_id_and_check_unknown_does_not_mutate(self):
        member = User.objects.create_user(email='slack-1288@test.com')
        response = self.client.patch(
            f'/api/users/{member.email}',
            data=json.dumps({'slack_user_id': ' u01abc123 '}),
            content_type='application/json',
            **self.auth(),
        )
        self.assertEqual(response.status_code, 200)
        member.refresh_from_db()
        self.assertEqual(member.slack_user_id, 'U01ABC123')
        checked_at = member.slack_checked_at
        with patch(
            'api.views.users.check_user_slack_membership', return_value='unknown',
        ):
            response = self.client.post(
                f'/api/users/{member.email}/slack-membership/check', **self.auth(),
            )
        self.assertEqual(response.status_code, 503)
        member.refresh_from_db()
        self.assertEqual(member.slack_checked_at, checked_at)

    def test_custom_batch_expiry_and_exact_idempotency(self):
        member = User.objects.create_user(email='grant-1288@test.com')
        expiry = timezone.now().date() + datetime.timedelta(days=10)
        payload = {'emails': [member.email], 'tier': 'main', 'expires_at': expiry.isoformat()}
        first = self.client.post(
            '/api/tier-overrides', data=json.dumps(payload),
            content_type='application/json', **self.auth(),
        )
        second = self.client.post(
            '/api/tier-overrides', data=json.dumps(payload),
            content_type='application/json', **self.auth(),
        )
        self.assertEqual(first.json()['granted'], 1)
        self.assertEqual(second.json()['skipped'], 1)
        override = TierOverride.objects.get(user=member, is_active=True)
        self.assertEqual(override.expires_at.date(), expiry)
        self.assertEqual(override.expires_at.time(), datetime.time(23, 59, 59))

    def test_crm_export_tag_is_exact_and_invalid_normalization_is_422(self):
        User.objects.create_user(
            email='tag-match-1288@test.com', tags=['support-priority'],
        )
        User.objects.create_user(
            email='tag-substring-1288@test.com', tags=['support-priority-vip'],
        )
        response = self.client.get(
            '/api/crm/export?scope=all&tag=Support_Priority', **self.auth(),
        )
        self.assertEqual(response.status_code, 200)
        emails = [member['email'] for member in response.json()['members']]
        self.assertEqual(emails, ['tag-match-1288@test.com'])
        invalid = self.client.get(
            '/api/crm/export?scope=all&tag=---', **self.auth(),
        )
        self.assertEqual(invalid.status_code, 422)

    def test_api_write_permissions_methods_and_invalid_expiry_are_atomic(self):
        member = User.objects.create_user(email='permission-api-1288@test.com')
        url = f'/api/users/{member.email}/slack-membership/check'
        self.assertEqual(self.client.post(url).status_code, 401)
        self.assertEqual(self.client.get(url, **self.auth()).status_code, 405)
        invalid = self.client.post(
            '/api/tier-overrides',
            data=json.dumps({
                'emails': [member.email], 'tier': 'main',
                'expires_at': 'not-a-date',
            }),
            content_type='application/json',
            **self.auth(),
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertFalse(TierOverride.objects.filter(user=member).exists())
        bad_patch = self.client.patch(
            f'/api/users/{member.email}',
            data=json.dumps({'slack_user_id': None}),
            content_type='application/json',
            **self.auth(),
        )
        self.assertEqual(bad_patch.status_code, 422)
