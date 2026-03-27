"""Tests for redirect model, middleware, and studio views."""

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import Client, TestCase

from integrations.middleware import clear_redirect_cache
from integrations.models import Redirect

User = get_user_model()


class RedirectModelTest(TestCase):
    """Test the Redirect model."""

    def test_create_redirect(self):
        r = Redirect.objects.create(
            source_path='/old',
            target_path='/new',
            redirect_type=301,
        )
        self.assertEqual(r.source_path, '/old')
        self.assertEqual(r.target_path, '/new')
        self.assertEqual(r.redirect_type, 301)
        self.assertTrue(r.is_active)

    def test_str_representation(self):
        r = Redirect.objects.create(
            source_path='/old',
            target_path='/new',
            redirect_type=301,
        )
        self.assertEqual(str(r), '/old -> /new (301)')

    def test_source_path_unique(self):
        Redirect.objects.create(source_path='/old', target_path='/new')
        with self.assertRaises(IntegrityError):
            Redirect.objects.create(source_path='/old', target_path='/other')

    def test_default_redirect_type_is_301(self):
        r = Redirect.objects.create(source_path='/a', target_path='/b')
        self.assertEqual(r.redirect_type, 301)

    def test_default_is_active_true(self):
        r = Redirect.objects.create(source_path='/a', target_path='/b')
        self.assertTrue(r.is_active)

    def test_timestamps_auto_set(self):
        r = Redirect.objects.create(source_path='/a', target_path='/b')
        self.assertIsNotNone(r.created_at)
        self.assertIsNotNone(r.updated_at)


class RedirectSeedTest(TestCase):
    """Test that seed redirects exist after migration."""

    def test_seed_redirects_exist(self):
        expected = [
            ('/ai-engineer-resources', '/interview'),
            ('/ai-engineer-interview-questions', '/interview'),
            ('/ai-engineer-interview-questions/theory-interview-questions', '/interview/theory'),
            ('/ai-engineer-learning-path', '/learning-path/ai-engineer'),
        ]
        for source, target in expected:
            r = Redirect.objects.get(source_path=source)
            self.assertEqual(r.target_path, target)
            self.assertEqual(r.redirect_type, 301)
            self.assertTrue(r.is_active)


class RedirectMiddlewareTest(TestCase):
    """Test the redirect middleware."""

    def setUp(self):
        clear_redirect_cache()

    def tearDown(self):
        clear_redirect_cache()

    def test_301_redirect(self):
        Redirect.objects.create(
            source_path='/test-old',
            target_path='/test-new',
            redirect_type=301,
            is_active=True,
        )
        clear_redirect_cache()
        response = self.client.get('/test-old')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/test-new')

    def test_302_redirect(self):
        Redirect.objects.create(
            source_path='/temp-old',
            target_path='/temp-new',
            redirect_type=302,
            is_active=True,
        )
        clear_redirect_cache()
        response = self.client.get('/temp-old')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/temp-new')

    def test_inactive_redirect_not_applied(self):
        Redirect.objects.create(
            source_path='/inactive-path',
            target_path='/target',
            redirect_type=301,
            is_active=False,
        )
        clear_redirect_cache()
        response = self.client.get('/inactive-path')
        # Should NOT be a redirect - should be 404 or whatever the normal response is
        self.assertNotEqual(response.status_code, 301)

    def test_non_matching_path_passes_through(self):
        response = self.client.get('/nonexistent-page-xyz')
        # Should not be a redirect
        self.assertNotIn(response.status_code, [301, 302])

    def test_seed_redirect_works(self):
        """Test one of the seeded redirects works via middleware."""
        clear_redirect_cache()
        response = self.client.get('/ai-engineer-resources')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/interview')


class StudioRedirectListTest(TestCase):
    """Test studio redirect list view."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_list_returns_200(self):
        response = self.client.get('/studio/redirects/')
        self.assertEqual(response.status_code, 200)

    def test_list_uses_correct_template(self):
        response = self.client.get('/studio/redirects/')
        self.assertTemplateUsed(response, 'studio/redirects/list.html')

    def test_list_shows_redirects(self):
        Redirect.objects.create(
            source_path='/show-me', target_path='/target',
        )
        response = self.client.get('/studio/redirects/')
        self.assertContains(response, '/show-me')
        self.assertContains(response, '/target')

    def test_list_requires_staff(self):
        client = Client()
        response = client.get('/studio/redirects/')
        self.assertEqual(response.status_code, 302)  # redirect to login

    def test_non_staff_gets_403(self):
        client = Client()
        User.objects.create_user(
            email='user@test.com', password='testpass', is_staff=False,
        )
        client.login(email='user@test.com', password='testpass')
        response = client.get('/studio/redirects/')
        self.assertEqual(response.status_code, 403)


class StudioRedirectCreateTest(TestCase):
    """Test studio redirect creation."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        clear_redirect_cache()

    def tearDown(self):
        clear_redirect_cache()

    def test_create_form_returns_200(self):
        response = self.client.get('/studio/redirects/new')
        self.assertEqual(response.status_code, 200)

    def test_create_redirect_post(self):
        response = self.client.post('/studio/redirects/new', {
            'source_path': '/old-page',
            'target_path': '/new-page',
            'redirect_type': '301',
            'is_active': 'on',
        })
        self.assertEqual(response.status_code, 302)
        r = Redirect.objects.get(source_path='/old-page')
        self.assertEqual(r.target_path, '/new-page')
        self.assertEqual(r.redirect_type, 301)
        self.assertTrue(r.is_active)

    def test_create_auto_prepends_slash(self):
        self.client.post('/studio/redirects/new', {
            'source_path': 'no-slash',
            'target_path': 'also-no-slash',
            'redirect_type': '301',
            'is_active': 'on',
        })
        r = Redirect.objects.get(source_path='/no-slash')
        self.assertEqual(r.target_path, '/also-no-slash')

    def test_create_duplicate_source_shows_error(self):
        Redirect.objects.create(source_path='/exists', target_path='/target')
        response = self.client.post('/studio/redirects/new', {
            'source_path': '/exists',
            'target_path': '/other',
            'redirect_type': '301',
            'is_active': 'on',
        })
        self.assertEqual(response.status_code, 200)  # re-renders form
        self.assertEqual(Redirect.objects.filter(source_path='/exists').count(), 1)

    def test_create_302_redirect(self):
        self.client.post('/studio/redirects/new', {
            'source_path': '/temp',
            'target_path': '/dest',
            'redirect_type': '302',
            'is_active': 'on',
        })
        r = Redirect.objects.get(source_path='/temp')
        self.assertEqual(r.redirect_type, 302)


class StudioRedirectEditTest(TestCase):
    """Test studio redirect editing."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.redirect_obj = Redirect.objects.create(
            source_path='/edit-me', target_path='/old-target',
        )
        clear_redirect_cache()

    def tearDown(self):
        clear_redirect_cache()

    def test_edit_form_returns_200(self):
        response = self.client.get(f'/studio/redirects/{self.redirect_obj.pk}/edit')
        self.assertEqual(response.status_code, 200)

    def test_edit_redirect_post(self):
        self.client.post(f'/studio/redirects/{self.redirect_obj.pk}/edit', {
            'source_path': '/edit-me',
            'target_path': '/new-target',
            'redirect_type': '302',
            'is_active': 'on',
        })
        self.redirect_obj.refresh_from_db()
        self.assertEqual(self.redirect_obj.target_path, '/new-target')
        self.assertEqual(self.redirect_obj.redirect_type, 302)

    def test_edit_nonexistent_returns_404(self):
        response = self.client.get('/studio/redirects/99999/edit')
        self.assertEqual(response.status_code, 404)


class StudioRedirectDeleteTest(TestCase):
    """Test studio redirect deletion."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.redirect_obj = Redirect.objects.create(
            source_path='/delete-me', target_path='/target',
        )
        clear_redirect_cache()

    def tearDown(self):
        clear_redirect_cache()

    def test_delete_redirect(self):
        response = self.client.post(f'/studio/redirects/{self.redirect_obj.pk}/delete')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Redirect.objects.filter(pk=self.redirect_obj.pk).exists())

    def test_delete_nonexistent_returns_404(self):
        response = self.client.post('/studio/redirects/99999/delete')
        self.assertEqual(response.status_code, 404)

    def test_delete_requires_post(self):
        response = self.client.get(f'/studio/redirects/{self.redirect_obj.pk}/delete')
        self.assertEqual(response.status_code, 405)


class StudioRedirectToggleTest(TestCase):
    """Test studio redirect toggle active/inactive."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.redirect_obj = Redirect.objects.create(
            source_path='/toggle-me', target_path='/target', is_active=True,
        )
        clear_redirect_cache()

    def tearDown(self):
        clear_redirect_cache()

    def test_toggle_deactivates(self):
        self.client.post(f'/studio/redirects/{self.redirect_obj.pk}/toggle')
        self.redirect_obj.refresh_from_db()
        self.assertFalse(self.redirect_obj.is_active)

    def test_toggle_activates(self):
        self.redirect_obj.is_active = False
        self.redirect_obj.save()
        self.client.post(f'/studio/redirects/{self.redirect_obj.pk}/toggle')
        self.redirect_obj.refresh_from_db()
        self.assertTrue(self.redirect_obj.is_active)

    def test_toggle_requires_post(self):
        response = self.client.get(f'/studio/redirects/{self.redirect_obj.pk}/toggle')
        self.assertEqual(response.status_code, 405)
