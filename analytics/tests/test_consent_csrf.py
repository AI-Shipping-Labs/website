"""Decided visitors can change consent without embedding tokens in cached pages."""

import json

from django.test import Client, TestCase

from analytics.consent import ANALYTICS_CONSENT_COOKIE


class ConsentPreferenceCsrfTest(TestCase):
    def test_decided_visitors_can_fetch_token_and_change_preference(self):
        for previous, choice in [('denied', 'granted'), ('granted', 'denied')]:
            with self.subTest(previous=previous):
                client = Client(enforce_csrf_checks=True)
                client.cookies[ANALYTICS_CONSENT_COOKIE] = previous
                token_response = client.get('/api/analytics/consent/csrf')
                self.assertEqual(token_response.status_code, 200)
                self.assertIn('no-store', token_response['Cache-Control'])
                self.assertIn('csrftoken', token_response.cookies)
                self.assertNotIn(ANALYTICS_CONSENT_COOKIE, token_response.cookies)
                response = client.post(
                    '/api/analytics/consent',
                    data=json.dumps({'consent': choice}),
                    content_type='application/json',
                    HTTP_X_CSRFTOKEN=token_response.json()['csrf_token'],
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.cookies[ANALYTICS_CONSENT_COOKIE].value, choice)
