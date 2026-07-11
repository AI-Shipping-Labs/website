"""Tests for ``GET /api/signup-analytics``."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from analytics.models import CampaignVisit, UserAttribution
from api.openapi.builder import build_spec
from integrations.models import UtmCampaign

User = get_user_model()

URL = '/api/signup-analytics'


def _make_attribution(*, email, signup_path='email_password',
                      first_source='', first_medium='', first_campaign='',
                      first_campaign_obj=None, created_at=None,
                      anonymous_id='', user_kwargs=None):
    user = User.objects.create_user(
        email=email,
        password='pw',
        **(user_kwargs or {}),
    )
    attr, _created = UserAttribution.objects.get_or_create(user=user)
    attr.signup_path = signup_path
    attr.first_touch_utm_source = first_source
    attr.first_touch_utm_medium = first_medium
    attr.first_touch_utm_campaign = first_campaign
    attr.first_touch_campaign = first_campaign_obj
    attr.anonymous_id = anonymous_id
    attr.save()
    if created_at is not None:
        UserAttribution.objects.filter(pk=attr.pk).update(created_at=created_at)
        attr.refresh_from_db()
    return user, attr


def _make_visit(*, anonymous_id, path, ts, campaign='launch'):
    visit = CampaignVisit.objects.create(
        anonymous_id=anonymous_id,
        path=path,
        utm_source='newsletter',
        utm_medium='email',
        utm_campaign=campaign,
        referrer='https://example.com/full?secret=query',
        user_agent='Raw Browser UA',
        ip_hash='raw-ish-hash',
    )
    CampaignVisit.objects.filter(pk=visit.pk).update(ts=ts)
    visit.refresh_from_db()
    return visit


class SignupAnalyticsApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-api@test.com',
            password='pw',
            is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='signup')
        cls.non_staff = User.objects.create_user(
            email='member-api@test.com',
            password='pw',
            is_staff=False,
        )
        cls.non_staff_token = Token(
            key='non-staff-signup-analytics-token',
            user=cls.non_staff,
            name='non-staff',
        )
        Token.objects.bulk_create([cls.non_staff_token])
        UserAttribution.objects.filter(user__in=[cls.staff, cls.non_staff]).delete()

    def _auth(self, token=None):
        token = token or self.staff_token
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}

    def test_requires_valid_staff_token(self):
        cases = (
            ({}, 'Authentication token required'),
            ({'HTTP_AUTHORIZATION': 'Token invalid'}, 'Invalid token'),
            (self._auth(self.non_staff_token), 'Invalid token'),
        )
        for headers, message in cases:
            with self.subTest(message=message):
                response = self.client.get(URL, **headers)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()['error'], message)

    def test_returns_report_without_private_tracking_fields(self):
        now = timezone.now()
        campaign = UtmCampaign.objects.create(
            name='Spring Launch',
            slug='spring_launch',
            default_utm_source='newsletter',
            default_utm_medium='email',
        )
        _make_attribution(
            email='api-user@test.com',
            first_campaign='spring_launch',
            first_campaign_obj=campaign,
            anonymous_id='anon-api-private',
            created_at=now - timedelta(hours=1),
        )
        _make_visit(
            anonymous_id='anon-api-private',
            path='/pricing?secret=query',
            ts=now - timedelta(hours=2),
            campaign='spring_launch',
        )

        response = self.client.get(URL + '?range=7d', **self._auth())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['filters']['range'], '7d')
        self.assertEqual(body['window_total'], 1)
        self.assertEqual(
            body['actionable_source_rows'][0]['label'],
            'Spring Launch (spring_launch)',
        )
        self.assertEqual(
            body['pre_signup_activity_rows'][0]['path'],
            '/pricing',
        )
        self.assertEqual(
            body['recent_signups'][0]['first_tracked_landing_path'],
            '/pricing',
        )
        rendered = response.content.decode()
        for forbidden in (
            'anon-api-private',
            'Raw Browser UA',
            'secret=query',
            'example.com/full',
            'raw-ish-hash',
        ):
            self.assertNotIn(forbidden, rendered)

    def test_signup_path_filter_and_limit_apply(self):
        now = timezone.now()
        _make_attribution(
            email='google@test.com',
            signup_path='google_oauth',
            first_source='google',
            first_medium='oauth',
            anonymous_id='anon-google',
            created_at=now - timedelta(hours=1),
        )
        _make_visit(
            anonymous_id='anon-google',
            path='/courses/agents',
            ts=now - timedelta(hours=2),
        )
        _make_attribution(
            email='email@test.com',
            signup_path='email_password',
            first_source='newsletter',
            first_medium='email',
            anonymous_id='anon-email',
            created_at=now - timedelta(hours=1),
        )
        _make_visit(
            anonymous_id='anon-email',
            path='/pricing',
            ts=now - timedelta(hours=2),
        )

        response = self.client.get(
            URL + '?signup_path=google_oauth&limit=1',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['filters']['signup_path'], 'google_oauth')
        self.assertEqual(body['filters']['limit'], 1)
        self.assertEqual(body['window_total'], 1)
        self.assertEqual(len(body['recent_signups']), 1)
        self.assertEqual(body['recent_signups'][0]['email'], 'google@test.com')
        self.assertEqual(
            body['pre_signup_activity_rows'][0]['path'],
            '/courses/agents',
        )

    def test_account_lifecycle_filter_and_breakdown_apply(self):
        now = timezone.now()
        _make_attribution(
            email='newsletter-only@test.com',
            signup_path='newsletter',
            created_at=now - timedelta(hours=1),
            user_kwargs={
                'signup_source': 'newsletter',
                'account_activated': False,
                'unsubscribed': True,
            },
        )
        _make_attribution(
            email='full@test.com',
            signup_path='email_password',
            created_at=now - timedelta(hours=2),
            user_kwargs={
                'signup_source': 'signup',
                'account_activated': True,
            },
        )
        _make_attribution(
            email='imported@test.com',
            signup_path='unknown',
            created_at=now - timedelta(hours=3),
            user_kwargs={
                'signup_source': 'imported',
                'account_activated': False,
            },
        )

        response = self.client.get(
            URL + '?account_lifecycle=newsletter_only',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['filters']['account_lifecycle'], 'newsletter_only')
        self.assertEqual(body['window_total'], 1)
        self.assertEqual(
            body['recent_signups'][0]['email'],
            'newsletter-only@test.com',
        )
        self.assertEqual(
            body['recent_signups'][0]['account_lifecycle'],
            'newsletter_only',
        )
        breakdown = {
            row['account_lifecycle']: row['signup_count']
            for row in body['account_lifecycle_rows']
        }
        self.assertEqual(breakdown['newsletter_only'], 1)
        self.assertEqual(breakdown['full_account'], 0)
        self.assertEqual(breakdown['imported_or_unknown'], 0)

    def test_limit_is_capped_at_safe_maximum(self):
        response = self.client.get(URL + '?limit=999', **self._auth())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['filters']['limit'], 100)

    def test_invalid_filters_return_structured_422(self):
        cases = (
            ('?range=bogus', 'range'),
            ('?range=custom&start=2026-02-03', 'end'),
            ('?range=custom&start=bad&end=2026-02-03', 'date'),
            ('?range=custom&start=2026-02-04&end=2026-02-03', 'date'),
            ('?signup_path=bogus', 'signup_path'),
            ('?account_lifecycle=bogus', 'account_lifecycle'),
            ('?limit=zero', 'limit'),
        )
        for query, field in cases:
            with self.subTest(query=query):
                response = self.client.get(URL + query, **self._auth())
                self.assertEqual(response.status_code, 422)
                body = response.json()
                self.assertEqual(body['code'], 'validation_error')
                self.assertIn(field, body['details'])

    def test_openapi_documents_signup_analytics(self):
        from api.urls import urlpatterns
        document = build_spec(urlpatterns)
        self.assertIn('/api/signup-analytics', document['paths'])
        operation = document['paths']['/api/signup-analytics']['get']
        self.assertEqual(operation['tags'], ['Analytics'])
        params = {param['name'] for param in operation['parameters']}
        self.assertIn('signup_path', params)
        self.assertIn('account_lifecycle', params)
        self.assertIn('limit', params)
