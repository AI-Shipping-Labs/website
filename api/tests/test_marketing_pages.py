import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.models import Token
from content.models import MarketingPage

User = get_user_model()


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class MarketingPageApiTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='marketing-api-staff@test.com',
            password='testpass',
            is_staff=True,
        )
        self.member = User.objects.create_user(
            email='marketing-api-member@test.com',
            password='testpass',
        )
        self.staff_token = Token.objects.create(user=self.staff, name='marketing-api')
        self.member_token = Token(key='marketing-member-token', user=self.member, name='member')
        Token.objects.bulk_create([self.member_token])

    def _auth(self, token=None):
        token = token or self.staff_token
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}

    def test_collection_requires_staff_token(self):
        self.assertEqual(self.client.get('/api/marketing-pages').status_code, 401)
        self.assertEqual(
            self.client.get('/api/marketing-pages', **self._auth(self.member_token)).status_code,
            401,
        )

    def test_create_returns_content_id_and_publishes_public_page(self):
        response = self.client.post(
            '/api/marketing-pages',
            data={
                'title': 'Campaign Overview',
                'public_path': '/campaign-overview',
                'content_markdown': '# Campaign Overview\n\nAPI body.',
                'status': 'published',
                'nav_section': 'none',
            },
            content_type='application/json',
            **self._auth(),
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['public_path'], '/campaign-overview')
        self.assertEqual(body['public_url'], 'https://aishippinglabs.com/campaign-overview')
        self.assertTrue(uuid.UUID(body['content_id']))
        self.assertEqual(self.client.get('/campaign-overview').status_code, 200)
        self.assertNotContains(self.client.get('/campaign-overview'), 'nav-community-link-marketing')

    def test_patch_rejects_reserved_or_duplicate_path_and_delete_is_refused(self):
        page = MarketingPage.objects.create(
            title='Manual',
            public_path='/manual-page',
            content_markdown='Manual',
        )
        MarketingPage.objects.create(
            title='Other',
            public_path='/other-page',
            content_markdown='Other',
        )

        reserved = self.client.patch(
            f'/api/marketing-pages/{page.content_id}',
            data={'public_path': '/pricing'},
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(reserved.status_code, 422)
        self.assertIn('conflicts', reserved.json()['details']['public_path'][0])

        duplicate = self.client.patch(
            f'/api/marketing-pages/{page.content_id}',
            data={'public_path': '/other-page'},
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(duplicate.status_code, 422)
        self.assertIn('already uses', duplicate.json()['details']['public_path'][0])

        deleted = self.client.delete(
            f'/api/marketing-pages/{page.content_id}',
            **self._auth(),
        )
        self.assertEqual(deleted.status_code, 405)
        self.assertEqual(deleted.json()['code'], 'marketing_page_delete_not_available')
        self.assertIn('status=draft', deleted.json()['error'])

    def test_preview_link_and_regenerate(self):
        page = MarketingPage.objects.create(
            title='Draft',
            public_path='/draft-api-page',
            content_markdown='Draft',
        )
        old_url = page.get_preview_url()

        link = self.client.get(
            f'/api/marketing-pages/{page.content_id}/preview-link',
            **self._auth(),
        )
        self.assertEqual(link.status_code, 200)
        self.assertEqual(
            link.json()['preview_url'],
            f'https://aishippinglabs.com{old_url}',
        )

        regen = self.client.post(
            f'/api/marketing-pages/{page.content_id}/preview-token/regenerate',
            **self._auth(),
        )
        page.refresh_from_db()
        self.assertEqual(regen.status_code, 200)
        self.assertNotEqual(old_url, page.get_preview_url())
