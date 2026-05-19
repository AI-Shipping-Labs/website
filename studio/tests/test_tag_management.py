"""Tests for the global tag-management surface (issue #694).

Covers the rename / delete-everywhere endpoints, the user-list tag picker,
the per-chip rename + delete-everywhere affordances on the user detail
page, and the staff-only gate on every new endpoint.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class StudioUserListTagPickerTest(TestCase):
    """The standalone tag picker on the user list (issue #694)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.alice = User.objects.create_user(
            email='alice@test.com', password='testpass',
            tags=['paid', 'beta'],
        )
        cls.bob = User.objects.create_user(
            email='bob@test.com', password='testpass',
            tags=['paid'],
        )
        cls.carol = User.objects.create_user(
            email='carol@test.com', password='testpass',
            tags=['lapsed'],
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_picker_renders_with_known_tags(self):
        response = self.client.get('/studio/users/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="user-tag-picker"')
        # Every known tag is an <option>.
        for tag in ('paid', 'beta', 'lapsed'):
            self.assertContains(response, f'<option value="{tag}"')
        # First option is the "All tags" clear.
        self.assertContains(response, 'All tags')

    def test_picker_marks_active_tag_selected(self):
        response = self.client.get('/studio/users/?tag=paid')
        # Only the active option is selected.
        self.assertContains(response, '<option value="paid" selected>paid</option>')
        # And the default "All tags" option is NOT selected.
        self.assertNotContains(response, '<option value="" selected>All tags')

    def test_picker_preserves_filter_and_search_via_hidden_inputs(self):
        response = self.client.get('/studio/users/?filter=paid&q=alice&slack=any')
        # The picker form has hidden inputs that preserve filter / slack / q
        # so selecting a tag does not reset the other filters.
        self.assertContains(response, 'data-testid="user-tag-picker-form"')
        self.assertContains(response, '<input type="hidden" name="filter" value="paid">')
        self.assertContains(response, '<input type="hidden" name="slack" value="any">')
        self.assertContains(response, '<input type="hidden" name="q" value="alice">')

    def test_known_tags_in_list_context(self):
        response = self.client.get('/studio/users/')
        self.assertEqual(
            response.context['known_tags'],
            ['beta', 'lapsed', 'paid'],
        )

    def test_filter_chips_preserve_tag_value(self):
        # Already covered by the existing test_user_tags suite, but
        # re-asserted here to lock in the cross-feature interaction the
        # spec explicitly calls out: switching tier chips must keep the
        # ``?tag=`` query string intact.
        response = self.client.get('/studio/users/?tag=paid')
        for chip_filter in ('all', 'paid', 'main_plus', 'premium', 'subscribers'):
            self.assertContains(response, f'?filter={chip_filter}&amp;slack=any&amp;tag=paid')


class StudioTagRenameViewTest(TestCase):
    """POST ``/studio/tags/<name>/rename``."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')
        self.alice = User.objects.create_user(
            email=f'alice-{self._testMethodName}@test.com',
            password='testpass',
            tags=['paid-user', 'beta'],
        )
        self.bob = User.objects.create_user(
            email=f'bob-{self._testMethodName}@test.com',
            password='testpass',
            tags=['paid-user'],
        )

    def test_rename_propagates_and_redirects_to_referer(self):
        detail_url = f'/studio/users/{self.alice.pk}/'
        response = self.client.post(
            '/studio/tags/paid-user/rename',
            {'new': 'paid'},
            HTTP_REFERER=f'http://testserver{detail_url}',
        )
        self.assertRedirects(response, detail_url)
        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertIn('paid', self.alice.tags)
        self.assertNotIn('paid-user', self.alice.tags)
        self.assertEqual(self.bob.tags, ['paid'])

    def test_rename_flashes_affected_count(self):
        response = self.client.post(
            '/studio/tags/paid-user/rename',
            {'new': 'paid'},
            HTTP_REFERER=f'http://testserver/studio/users/{self.alice.pk}/',
            follow=True,
        )
        self.assertContains(
            response,
            'Renamed &quot;paid-user&quot; to &quot;paid&quot; on 2 user(s).',
        )

    def test_rename_to_empty_value_flashes_error(self):
        response = self.client.post(
            '/studio/tags/paid-user/rename',
            {'new': '   '},
            HTTP_REFERER=f'http://testserver/studio/users/{self.alice.pk}/',
            follow=True,
        )
        self.assertContains(response, 'New tag name cannot be empty.')
        self.alice.refresh_from_db()
        # Original chip unchanged.
        self.assertIn('paid-user', self.alice.tags)

    def test_rename_get_not_allowed(self):
        response = self.client.get('/studio/tags/paid-user/rename')
        self.assertEqual(response.status_code, 405)

    def test_rename_fallbacks_to_user_list_without_referer(self):
        response = self.client.post(
            '/studio/tags/paid-user/rename',
            {'new': 'paid'},
        )
        self.assertRedirects(response, '/studio/users/')

    def test_rename_rejects_offsite_referer(self):
        # A referer on a different host falls back to the user list to
        # avoid open-redirects, even though staff_required already gates
        # the surface.
        response = self.client.post(
            '/studio/tags/paid-user/rename',
            {'new': 'paid'},
            HTTP_REFERER='https://evil.example.com/',
        )
        self.assertRedirects(response, '/studio/users/')

    def test_rename_anonymous_user_redirects_to_login(self):
        self.client.logout()
        response = self.client.post(
            '/studio/tags/paid-user/rename',
            {'new': 'paid'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_rename_non_staff_returns_403(self):
        self.client.logout()
        User.objects.create_user(
            email='regular@test.com', password='testpass', is_staff=False,
        )
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.post(
            '/studio/tags/paid-user/rename',
            {'new': 'paid'},
        )
        self.assertEqual(response.status_code, 403)
        self.alice.refresh_from_db()
        # Tag unchanged.
        self.assertIn('paid-user', self.alice.tags)


class StudioTagDeleteViewTest(TestCase):
    """POST ``/studio/tags/<name>/delete``."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')
        self.alice = User.objects.create_user(
            email=f'alice-{self._testMethodName}@test.com',
            password='testpass',
            tags=['early-adopter', 'beta'],
        )
        self.bob = User.objects.create_user(
            email=f'bob-{self._testMethodName}@test.com',
            password='testpass',
            tags=['early-adopter'],
        )

    def test_delete_propagates_and_redirects_to_referer(self):
        detail_url = f'/studio/users/{self.alice.pk}/'
        response = self.client.post(
            '/studio/tags/early-adopter/delete',
            HTTP_REFERER=f'http://testserver{detail_url}',
        )
        self.assertRedirects(response, detail_url)
        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.assertEqual(self.alice.tags, ['beta'])
        self.assertEqual(self.bob.tags, [])

    def test_delete_flashes_affected_count(self):
        response = self.client.post(
            '/studio/tags/early-adopter/delete',
            HTTP_REFERER=f'http://testserver/studio/users/{self.alice.pk}/',
            follow=True,
        )
        self.assertContains(
            response,
            'Deleted tag &quot;early-adopter&quot; from 2 user(s).',
        )

    def test_delete_get_not_allowed(self):
        response = self.client.get('/studio/tags/early-adopter/delete')
        self.assertEqual(response.status_code, 405)

    def test_delete_anonymous_user_redirects_to_login(self):
        self.client.logout()
        response = self.client.post('/studio/tags/early-adopter/delete')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_delete_non_staff_returns_403(self):
        self.client.logout()
        User.objects.create_user(
            email='regular@test.com', password='testpass', is_staff=False,
        )
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.post('/studio/tags/early-adopter/delete')
        self.assertEqual(response.status_code, 403)
        self.alice.refresh_from_db()
        self.assertIn('early-adopter', self.alice.tags)


class StudioUserDetailTagAffordancesTest(TestCase):
    """User detail page renders rename + delete-everywhere per chip."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.alice = User.objects.create_user(
            email='alice@test.com', password='testpass',
            tags=['paid', 'beta'],
        )
        cls.bob = User.objects.create_user(
            email='bob@test.com', password='testpass',
            tags=['paid'],
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_detail_renders_rename_pencil_per_chip(self):
        response = self.client.get(f'/studio/users/{self.alice.pk}/')
        self.assertContains(
            response,
            'data-tag-rename-toggle="paid"',
        )
        self.assertContains(
            response,
            'data-tag-rename-toggle="beta"',
        )

    def test_detail_renders_delete_everywhere_button_per_chip(self):
        response = self.client.get(f'/studio/users/{self.alice.pk}/')
        self.assertContains(
            response,
            'data-tag-delete-open="paid"',
        )
        self.assertContains(
            response,
            'data-tag-delete-open="beta"',
        )

    def test_detail_renders_per_chip_confirm_dialog_copy(self):
        response = self.client.get(f'/studio/users/{self.alice.pk}/')
        # The 'paid' chip is on 2 users (alice + bob); the 'beta' chip is
        # only on alice. The confirm copy reflects each live count.
        self.assertContains(
            response,
            'Delete tag paid? This removes it from 2 users. Cannot be undone.',
        )
        self.assertContains(
            response,
            'Delete tag beta? This removes it from 1 user. Cannot be undone.',
        )

    def test_detail_rename_form_targets_global_endpoint(self):
        response = self.client.get(f'/studio/users/{self.alice.pk}/')
        self.assertContains(
            response,
            'action="/studio/tags/paid/rename"',
        )

    def test_detail_delete_form_targets_global_endpoint(self):
        response = self.client.get(f'/studio/users/{self.alice.pk}/')
        self.assertContains(
            response,
            'action="/studio/tags/paid/delete"',
        )

    def test_detail_keeps_per_user_remove_button(self):
        # The existing 'x' affordance must remain so the per-user remove
        # is still distinct from the delete-everywhere action.
        response = self.client.get(f'/studio/users/{self.alice.pk}/')
        self.assertContains(response, 'data-testid="user-tag-remove"')
        self.assertContains(
            response,
            f'action="/studio/users/{self.alice.pk}/tags/remove"',
        )

    def test_detail_tag_chips_context_includes_counts(self):
        response = self.client.get(f'/studio/users/{self.alice.pk}/')
        chips = response.context['tag_chips']
        chips_by_name = {chip['name']: chip['user_count'] for chip in chips}
        self.assertEqual(chips_by_name, {'paid': 2, 'beta': 1})


class StudioTagRenameEndToEndTest(TestCase):
    """End-to-end: rename via the view, verify the filter reflects it."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')
        self.alice = User.objects.create_user(
            email=f'alice-{self._testMethodName}@test.com',
            password='testpass',
            tags=['paid-user'],
        )
        self.bob = User.objects.create_user(
            email=f'bob-{self._testMethodName}@test.com',
            password='testpass',
            tags=['paid-user'],
        )
        self.carol = User.objects.create_user(
            email=f'carol-{self._testMethodName}@test.com',
            password='testpass',
            tags=['paid-user'],
        )

    def test_after_rename_old_tag_returns_zero_users(self):
        self.client.post(
            '/studio/tags/paid-user/rename',
            {'new': 'paid'},
        )
        response = self.client.get('/studio/users/?tag=paid-user')
        emails = [row['email'] for row in response.context['user_rows']]
        self.assertNotIn(self.alice.email, emails)
        self.assertNotIn(self.bob.email, emails)
        self.assertNotIn(self.carol.email, emails)

    def test_after_rename_new_tag_returns_renamed_users(self):
        self.client.post(
            '/studio/tags/paid-user/rename',
            {'new': 'paid'},
        )
        response = self.client.get('/studio/users/?tag=paid')
        emails = {row['email'] for row in response.context['user_rows']}
        self.assertEqual(
            emails,
            {self.alice.email, self.bob.email, self.carol.email},
        )


class StudioTagDeleteEndToEndTest(TestCase):
    """End-to-end: delete via the view, picker no longer offers the tag."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')
        self.alice = User.objects.create_user(
            email=f'alice-{self._testMethodName}@test.com',
            password='testpass',
            tags=['early-adopter'],
        )
        self.bob = User.objects.create_user(
            email=f'bob-{self._testMethodName}@test.com',
            password='testpass',
            tags=['early-adopter', 'beta'],
        )

    def test_after_delete_tag_filter_returns_zero_users(self):
        self.client.post('/studio/tags/early-adopter/delete')
        response = self.client.get('/studio/users/?tag=early-adopter')
        emails = [row['email'] for row in response.context['user_rows']]
        self.assertEqual(emails, [])

    def test_after_delete_tag_picker_no_longer_offers_it(self):
        self.client.post('/studio/tags/early-adopter/delete')
        response = self.client.get('/studio/users/')
        self.assertNotIn('early-adopter', response.context['known_tags'])
        # Other tags remain available.
        self.assertIn('beta', response.context['known_tags'])
