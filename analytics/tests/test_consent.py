"""Consent-gate regression tests for optional analytics."""

import json

from django.test import Client, TestCase

from analytics.consent import ANALYTICS_CONSENT_COOKIE
from analytics.middleware import ANON_ID_COOKIE, FIRST_TOUCH_COOKIE, SESSION_LAST_TOUCH
from analytics.models import CampaignVisit
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting


class AnalyticsConsentTest(TestCase):
    def setUp(self):
        IntegrationSetting.objects.create(
            key='GOOGLE_ANALYTICS_ID', value='G-CONSENTTEST', group='analytics'
        )
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_undecided_visit_has_ui_but_no_tracking(self):
        response = self.client.get('/?utm_source=test&utm_campaign=consent')
        self.assertContains(response, 'data-testid="analytics-consent-panel"')
        self.assertNotContains(response, 'googletagmanager.com')
        self.assertEqual(CampaignVisit.objects.count(), 0)
        self.assertEqual(self.client.cookies[ANON_ID_COOKIE].value, '')
        self.assertEqual(self.client.cookies[FIRST_TOUCH_COOKIE].value, '')
        self.assertNotIn(SESSION_LAST_TOUCH, self.client.session)

    def test_grant_enables_loader_and_first_party_attribution(self):
        self.client.cookies[ANALYTICS_CONSENT_COOKIE] = 'granted'
        response = self.client.get('/?utm_source=test&utm_campaign=consent')
        self.assertContains(response, 'googletagmanager.com/gtag/js?id=G-CONSENTTEST')
        self.assertTrue(self.client.cookies[ANON_ID_COOKIE].value)
        self.assertTrue(self.client.cookies[FIRST_TOUCH_COOKIE].value)

    def test_denied_state_cleans_legacy_state(self):
        self.client.cookies[ANALYTICS_CONSENT_COOKIE] = 'denied'
        self.client.cookies[ANON_ID_COOKIE] = 'legacy-id'
        self.client.cookies[FIRST_TOUCH_COOKIE] = 'legacy-touch'
        session = self.client.session
        session[SESSION_LAST_TOUCH] = {'source': 'legacy'}
        session.save()
        response = self.client.get('/')
        self.assertNotContains(response, 'googletagmanager.com')
        self.assertNotIn(SESSION_LAST_TOUCH, self.client.session)
        self.assertEqual(self.client.cookies[ANON_ID_COOKIE].value, '')
        self.assertEqual(self.client.cookies[FIRST_TOUCH_COOKIE].value, '')

    def test_revoke_clears_optional_cookies(self):
        self.client.cookies[ANALYTICS_CONSENT_COOKIE] = 'granted'
        self.client.get('/?utm_source=test')
        response = self.client.post(
            '/api/analytics/consent',
            data=json.dumps({'consent': 'denied'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.cookies[ANALYTICS_CONSENT_COOKIE].value, 'denied')
        self.assertEqual(self.client.cookies[ANON_ID_COOKIE].value, '')
        self.assertEqual(self.client.cookies[FIRST_TOUCH_COOKIE].value, '')

    def test_consent_endpoint_rejects_invalid_choice(self):
        response = self.client.post(
            '/api/analytics/consent',
            data=json.dumps({'consent': 'maybe'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_consent_endpoint_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(
            '/api/analytics/consent',
            data=json.dumps({'consent': 'granted'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)
