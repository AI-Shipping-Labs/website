"""Staff-token instructor account-linking API (issue #1345).

Covers GET listing + ``?linked=false``, PUT set/clear by email/user_id,
POST reconcile counts, and the 401 guard.
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

from accounts.models import Token
from content.models import Instructor

User = get_user_model()


@tag('core')
class InstructorsApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='s')
        cls.ada_user = User.objects.create_user(
            email='ada@test.com', password='pw', email_verified=True,
        )

    def _auth(self):
        return {'HTTP_AUTHORIZATION': f'Token {self.staff_token.key}'}

    # ---- GET list -----------------------------------------------------
    def test_list_returns_link_state(self):
        Instructor.objects.create(
            instructor_id='ada', name='Ada', email='ada@test.com',
            user=self.ada_user,
        )
        Instructor.objects.create(
            instructor_id='dane', name='Dane', email='dane@test.com',
        )
        resp = self.client.get(reverse('api_instructors'), **self._auth())
        self.assertEqual(resp.status_code, 200)
        by_id = {
            i['instructor_id']: i for i in resp.json()['instructors']
        }
        self.assertTrue(by_id['ada']['linked'])
        self.assertEqual(by_id['ada']['linked_user_email'], 'ada@test.com')
        self.assertFalse(by_id['dane']['linked'])
        self.assertIsNone(by_id['dane']['linked_user_email'])

    def test_list_linked_false_returns_only_unlinked(self):
        Instructor.objects.create(
            instructor_id='ada', name='Ada', user=self.ada_user,
        )
        Instructor.objects.create(instructor_id='dane', name='Dane')
        resp = self.client.get(
            reverse('api_instructors'), {'linked': 'false'}, **self._auth(),
        )
        ids = [i['instructor_id'] for i in resp.json()['instructors']]
        self.assertEqual(ids, ['dane'])

    def test_list_requires_token(self):
        resp = self.client.get(reverse('api_instructors'))
        self.assertEqual(resp.status_code, 401)

    # ---- PUT set/clear ------------------------------------------------
    def test_put_links_by_verified_email(self):
        Instructor.objects.create(instructor_id='ada', name='Ada')
        resp = self.client.put(
            reverse('api_instructor_user', args=['ada']),
            data=json.dumps({'email': 'ada@test.com'}),
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['linked'])
        self.assertEqual(
            Instructor.objects.get(instructor_id='ada').user, self.ada_user,
        )

    def test_put_rejects_unverified_email(self):
        User.objects.create_user(
            email='cleo@test.com', password='pw', email_verified=False,
        )
        Instructor.objects.create(instructor_id='cleo', name='Cleo')
        resp = self.client.put(
            reverse('api_instructor_user', args=['cleo']),
            data=json.dumps({'email': 'cleo@test.com'}),
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 422)
        self.assertIsNone(
            Instructor.objects.get(instructor_id='cleo').user_id,
        )

    def test_put_rejects_unknown_email(self):
        Instructor.objects.create(instructor_id='ada', name='Ada')
        resp = self.client.put(
            reverse('api_instructor_user', args=['ada']),
            data=json.dumps({'email': 'nobody@test.com'}),
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 422)
        self.assertIsNone(
            Instructor.objects.get(instructor_id='ada').user_id,
        )

    def test_put_links_by_user_id(self):
        Instructor.objects.create(instructor_id='ada', name='Ada')
        resp = self.client.put(
            reverse('api_instructor_user', args=['ada']),
            data=json.dumps({'user_id': self.ada_user.pk}),
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            Instructor.objects.get(instructor_id='ada').user, self.ada_user,
        )

    def test_put_clears_with_user_null(self):
        Instructor.objects.create(
            instructor_id='ada', name='Ada', user=self.ada_user,
        )
        resp = self.client.put(
            reverse('api_instructor_user', args=['ada']),
            data=json.dumps({'user': None}),
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['linked'])
        self.assertIsNone(
            Instructor.objects.get(instructor_id='ada').user_id,
        )

    def test_put_requires_token(self):
        Instructor.objects.create(instructor_id='ada', name='Ada')
        resp = self.client.put(
            reverse('api_instructor_user', args=['ada']),
            data=json.dumps({'email': 'ada@test.com'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 401)

    # ---- POST reconcile -----------------------------------------------
    def test_reconcile_returns_counts_and_links(self):
        Instructor.objects.create(
            instructor_id='ada', name='Ada', email='ada@test.com',
        )
        Instructor.objects.create(
            instructor_id='dane', name='Dane', email='dane@test.com',
        )
        resp = self.client.post(
            reverse('api_instructors_reconcile'), **self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['scanned'], 2)
        self.assertEqual(data['newly_linked'], 1)
        self.assertEqual(data['left_unlinked'], 1)
        self.assertIsNotNone(
            Instructor.objects.get(instructor_id='ada').user_id,
        )

    def test_reconcile_requires_token(self):
        resp = self.client.post(reverse('api_instructors_reconcile'))
        self.assertEqual(resp.status_code, 401)
