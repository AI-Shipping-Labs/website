"""Tests for the staff article preview-link API."""

import uuid
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.models import Token
from content.models import Article

User = get_user_model()


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class ArticlePreviewLinkApiTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='staff-articles-api@test.com',
            password='testpass',
            is_staff=True,
        )
        self.member = User.objects.create_user(
            email='member-articles-api@test.com',
            password='testpass',
        )
        self.staff_token = Token.objects.create(
            user=self.staff,
            name='articles-api',
        )
        self.non_staff_token = Token(
            key='non-staff-article-preview-token',
            user=self.member,
            name='legacy-member-token',
        )
        Token.objects.bulk_create([self.non_staff_token])
        self.article = Article.objects.create(
            title='Synced Draft',
            slug='synced-draft',
            content_id=uuid.uuid4(),
            date=date(2026, 1, 15),
            published=False,
        )

    def _auth(self, token=None):
        token = token or self.staff_token
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}

    def test_fetch_preview_link_requires_staff_token(self):
        url = f'/api/articles/{self.article.content_id}/preview-link'

        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(
            self.client.get(url, **self._auth(self.non_staff_token)).status_code,
            401,
        )

    def test_fetch_preview_link_returns_minimal_absolute_url(self):
        response = self.client.get(
            f'/api/articles/{self.article.content_id}/preview-link',
            **self._auth(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['content_id'], str(self.article.content_id))
        self.assertEqual(
            body['preview_url'],
            f'https://aishippinglabs.com{self.article.get_preview_url()}',
        )
        self.assertEqual(set(body), {'content_id', 'preview_url'})

    def test_regenerate_preview_token_rotates_and_invalidates_old_url(self):
        old_preview_url = self.article.get_preview_url()
        response = self.client.post(
            f'/api/articles/{self.article.content_id}/preview-token/regenerate',
            **self._auth(),
        )
        self.article.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(old_preview_url, self.article.get_preview_url())
        self.assertEqual(self.client.get(old_preview_url).status_code, 404)
        self.assertEqual(self.client.get(self.article.get_preview_url()).status_code, 200)
        self.assertEqual(
            response.json()['preview_url'],
            f'https://aishippinglabs.com{self.article.get_preview_url()}',
        )

    def test_unknown_content_id_returns_404(self):
        response = self.client.get(
            f'/api/articles/{uuid.uuid4()}/preview-link',
            **self._auth(),
        )

        self.assertEqual(response.status_code, 404)
