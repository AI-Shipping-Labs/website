import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from community.models import CommunityAuditLog
from questionnaires.models import Questionnaire, Response, ResponseQuestion

User = get_user_model()


class QuestionnaireResponsesApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='api-staff-1289@test.com', is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name='queue-1289')
        cls.onboarding = Questionnaire.objects.create(
            title='API onboarding', purpose='onboarding',
        )
        cls.feedback = Questionnaire.objects.create(
            title='API feedback', purpose='feedback',
        )
        cls.awaiting = []
        for index in range(3):
            member = User.objects.create_user(email=f'api-person-{index}@test.com')
            response = Response.objects.create(
                questionnaire=cls.onboarding, respondent=member,
            )
            ResponseQuestion.objects.create(
                response=response, question_type='text', prompt='Snapshot?',
            )
            response.mark_submitted()
            cls.awaiting.append(response)
        cls.reviewed = Response.objects.create(
            questionnaire=cls.onboarding,
            respondent=User.objects.create_user(email='api-reviewed@test.com'),
            status='submitted', submitted_at=timezone.now(),
            reviewed_at=timezone.now(), reviewed_by=cls.staff,
        )
        cls.draft = Response.objects.create(
            questionnaire=cls.feedback,
            respondent=User.objects.create_user(email='api-draft@test.com'),
        )

    def auth(self):
        return {'HTTP_AUTHORIZATION': f'Token {self.token.key}'}

    def test_collection_defaults_filters_count_before_paging_and_compact_shape(self):
        response = self.client.get(
            '/api/questionnaire-responses?limit=1&offset=1', **self.auth(),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 3)
        self.assertEqual(len(data['responses']), 1)
        row = data['responses'][0]
        self.assertEqual(row['review_state'], 'awaiting')
        self.assertEqual(row['questionnaire']['purpose'], 'onboarding')
        self.assertIn('studio_user_url', row)
        self.assertIn('answered_count', row)

    def test_collection_composes_all_filters_and_validates(self):
        response = self.client.get(
            '/api/questionnaire-responses',
            {
                'status': 'submitted', 'review': 'reviewed',
                'purpose': 'onboarding',
                'questionnaire': self.onboarding.pk, 'q': 'reviewed',
            },
            **self.auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)
        self.assertEqual(
            response.json()['responses'][0]['id'], self.reviewed.pk,
        )
        for field, value in (
            ('status', 'bogus'), ('review', 'bogus'),
            ('purpose', 'bogus'), ('questionnaire', 'bogus'),
            ('since', 'bogus'), ('limit', 'bogus'), ('offset', '-1'),
        ):
            with self.subTest(field=field):
                invalid = self.client.get(
                    '/api/questionnaire-responses', {field: value}, **self.auth(),
                )
                self.assertEqual(invalid.status_code, 422)
                self.assertEqual(invalid.json()['code'], 'validation_error')

    def test_detail_full_payload_and_staff_token_gate(self):
        response = self.client.get(
            f'/api/questionnaire-responses/{self.awaiting[0].pk}', **self.auth(),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['id'], self.awaiting[0].pk)
        self.assertEqual(data['user_id'], self.awaiting[0].respondent_id)
        self.assertEqual(data['questions'][0]['prompt'], 'Snapshot?')
        self.assertEqual(
            self.client.get('/api/questionnaire-responses').status_code, 401,
        )

        nonstaff = User.objects.create_user(
            email='demoted-token@test.com', is_staff=True,
        )
        token = Token.objects.create(user=nonstaff, name='demoted')
        nonstaff.is_staff = False
        nonstaff.save(update_fields=['is_staff'])
        denied = self.client.get(
            '/api/questionnaire-responses',
            HTTP_AUTHORIZATION=f'Token {token.key}',
        )
        self.assertEqual(denied.status_code, 401)

    def test_onboarding_feed_is_additive_and_review_default_remains_all(self):
        response = self.client.get('/api/onboarding/responses', **self.auth())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        ids = {row['id'] for row in data['responses']}
        self.assertIn(self.awaiting[0].pk, ids)
        self.assertIn(self.reviewed.pk, ids)
        row = next(item for item in data['responses'] if item['id'] == self.awaiting[0].pk)
        self.assertEqual(row['user_id'], self.awaiting[0].respondent_id)
        self.assertEqual(row['review_state'], 'awaiting')
        self.assertIn('studio_user_url', row)
        self.assertIn('studio_response_url', row)
        self.assertIn('reviewed_at', row)
        self.assertIn('reviewed_by', row)

        awaiting = self.client.get(
            '/api/onboarding/responses?review=awaiting', **self.auth(),
        ).json()
        self.assertEqual(awaiting['count'], 3)
        reviewed = self.client.get(
            '/api/onboarding/responses?review=reviewed', **self.auth(),
        ).json()
        self.assertEqual(reviewed['count'], 1)

    def test_wrong_methods_and_unauthenticated_reads_do_not_leak(self):
        collection = self.client.post(
            '/api/questionnaire-responses', {}, **self.auth(),
        )
        self.assertEqual(collection.status_code, 405)
        detail_url = f'/api/questionnaire-responses/{self.awaiting[0].pk}'
        wrong_method = self.client.post(detail_url, {}, **self.auth())
        self.assertEqual(wrong_method.status_code, 405)
        unauthenticated = self.client.get(detail_url)
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertNotContains(
            unauthenticated,
            self.awaiting[0].respondent.email,
            status_code=401,
        )

    def patch(self, response, payload):
        return self.client.patch(
            f'/api/questionnaire-responses/{response.pk}',
            json.dumps(payload), content_type='application/json', **self.auth(),
        )

    def test_patch_review_reopen_idempotency_and_audit(self):
        target = self.awaiting[0]
        first = self.patch(target, {'reviewed': True})
        self.assertEqual(first.status_code, 200)
        reviewed_at = first.json()['reviewed_at']
        self.assertEqual(first.json()['reviewed_by'], self.staff.email)
        second = self.patch(target, {'reviewed': True})
        self.assertEqual(second.json()['reviewed_at'], reviewed_at)
        self.assertEqual(
            CommunityAuditLog.objects.filter(user=target.respondent).count(), 1,
        )
        reopened = self.patch(target, {'reviewed': False})
        self.assertEqual(reopened.status_code, 200)
        self.assertEqual(reopened.json()['review_state'], 'awaiting')
        self.patch(target, {'reviewed': False})
        self.assertEqual(
            CommunityAuditLog.objects.filter(user=target.respondent).count(), 2,
        )

    def test_patch_validation_not_found_and_draft_conflict(self):
        for payload in ({}, {'reviewed': 'true'}, {'reviewed': True, 'extra': 1}):
            with self.subTest(payload=payload):
                self.assertEqual(self.patch(self.awaiting[0], payload).status_code, 422)
        missing = self.client.patch(
            '/api/questionnaire-responses/999999',
            json.dumps({'reviewed': True}),
            content_type='application/json', **self.auth(),
        )
        self.assertEqual(missing.status_code, 404)
        conflict = self.patch(self.draft, {'reviewed': True})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()['code'], 'response_not_submitted')
