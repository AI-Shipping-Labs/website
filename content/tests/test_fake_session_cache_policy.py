from importlib import import_module

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.middleware import AuthenticationMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings

from content.caching import cache_anonymous_page
from website.middleware import PublicCachePolicyMiddleware


@override_settings(PUBLIC_PAGE_CACHE_ENABLED=True)
class FakeSessionPageCacheTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email='cache-session-user@example.test',
            password='test-password',
        )

    def setUp(self):
        self.factory = RequestFactory()
        cache.clear()
        self.addCleanup(cache.clear)

    def _response(self, view, session_cookie=None):
        request = self.factory.get('/projects')
        if session_cookie is not None:
            request.COOKIES[settings.SESSION_COOKIE_NAME] = session_cookie
        application = PublicCachePolicyMiddleware(
            SessionMiddleware(
                AuthenticationMiddleware(cache_anonymous_page()(view)),
            ),
        )
        return application(request)

    def _assert_no_session_cookie(self, response):
        self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)

    def test_unknown_session_cookie_reuses_cache_without_set_cookie(self):
        rendered = []

        def view(request):
            rendered.append(True)
            return HttpResponse('public listing', content_type='text/html')

        responses = [
            self._response(view, 'not-a-real-session-key'),
            self._response(view, 'not-a-real-session-key'),
        ]

        self.assertEqual(len(rendered), 1)
        for response in responses:
            self.assertEqual(response.content, b'public listing')
            self.assertEqual(response['Cache-Control'], 'public, max-age=0, s-maxage=300')
            self.assertIn('cookie', response['Vary'].lower())
            self._assert_no_session_cookie(response)

    def test_empty_and_malformed_session_cookies_need_no_session_lookup(self):
        for value in ('', 'short'):
            cache.clear()
            with self.subTest(value=value):
                with self.assertNumQueries(0):
                    response = self._response(
                        lambda request: HttpResponse(
                            'public listing', content_type='text/html',
                        ),
                        value,
                    )
                self.assertEqual(response.content, b'public listing')
                self.assertEqual(response['Cache-Control'], 'public, max-age=0, s-maxage=300')
                self._assert_no_session_cookie(response)

    def test_authenticated_session_bypasses_anonymous_cache(self):
        anonymous_response = self._response(
            lambda request: HttpResponse('anonymous', content_type='text/html'),
        )
        client = Client()
        client.force_login(self.user)
        session_cookie = client.cookies[settings.SESSION_COOKIE_NAME].value
        rendered = []

        def view(request):
            rendered.append(request.user.is_authenticated)
            return HttpResponse(
                'member' if request.user.is_authenticated else 'anonymous',
                content_type='text/html',
            )

        first_member_response = self._response(view, session_cookie)
        second_member_response = self._response(view, session_cookie)

        self.assertEqual(anonymous_response.content, b'anonymous')
        self.assertEqual(first_member_response.content, b'member')
        self.assertEqual(second_member_response.content, b'member')
        self.assertEqual(rendered, [True, True])
        self.assertEqual(first_member_response['Cache-Control'], 'private, no-store')

    def test_nonempty_anonymous_session_bypasses_page_cache(self):
        session_store = import_module(settings.SESSION_ENGINE).SessionStore()
        session_store['visitor_state'] = 'private'
        session_store.save()
        session_cookie = session_store.session_key
        rendered = []

        def view(request):
            rendered.append(True)
            return HttpResponse('session-specific', content_type='text/html')

        first = self._response(view, session_cookie)
        second = self._response(view, session_cookie)

        self.assertEqual(len(rendered), 2)
        self.assertEqual(first.content, b'session-specific')
        self.assertEqual(second.content, b'session-specific')
        self.assertEqual(first['Cache-Control'], 'private, no-store')
