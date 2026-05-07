"""Pagination on the Studio users list (issue #438).

Page size is hard-coded at 50. Out-of-range page numbers clamp to the
nearest valid page (no 404). Pager links preserve every existing query
param. The CSV export endpoint does NOT paginate.
"""

import csv
import io
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from studio.views.users import USER_LIST_PAGE_SIZE

User = get_user_model()
FAST_PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


def _create_users(count, *, email_prefix='user', **extra):
    """Bulk-create ``count`` users with deterministic emails."""
    users = [
        User(
            email=f'{email_prefix}{i:04d}@test.com',
            password='!',
            **extra,
        )
        for i in range(count)
    ]
    return User.objects.bulk_create(users)


class PatchedUserListPageSizeMixin:
    """Run pagination behavior against a small test-only page size."""

    test_page_size = 5

    def setUp(self):
        super().setUp()
        patcher = mock.patch(
            'studio.views.users.USER_LIST_PAGE_SIZE',
            self.test_page_size,
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class StudioUserListPageSizeContractTest(SimpleTestCase):
    """The production Studio users list page size stays locked at 50."""

    def test_page_size_constant_is_fifty(self):
        # Locked by the spec; if we change this we change the user-visible
        # contract and need to bump the issue.
        self.assertEqual(USER_LIST_PAGE_SIZE, 50)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class StudioUserListPaginationTest(PatchedUserListPageSizeMixin, TestCase):
    """``GET /studio/users/?page=N`` paginates and clamps."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='testpass',
            is_staff=True,
        )
        # 8 extra users + the staff member = 9 total. 9 / 5 = 2 pages.
        _create_users(8)

    def setUp(self):
        super().setUp()
        self.client.login(email='staff@test.com', password='testpass')

    def test_first_page_shows_page_size_rows(self):
        response = self.client.get('/studio/users/')
        self.assertEqual(len(response.context['page'].object_list), 5)
        self.assertEqual(response.context['page'].number, 1)

    def test_second_page_shows_remaining_rows(self):
        response = self.client.get('/studio/users/?page=2')
        self.assertEqual(len(response.context['page'].object_list), 4)
        self.assertEqual(response.context['page'].number, 2)

    def test_first_and_second_page_have_no_overlap(self):
        first = self.client.get('/studio/users/?page=1')
        second = self.client.get('/studio/users/?page=2')
        first_emails = {row['email'] for row in first.context['page'].object_list}
        second_emails = {row['email'] for row in second.context['page'].object_list}
        self.assertEqual(first_emails & second_emails, set())
        self.assertEqual(len(first_emails) + len(second_emails), 9)

    def test_out_of_range_page_clamps_to_last(self):
        last_page = self.client.get('/studio/users/?page=2')
        clamped = self.client.get('/studio/users/?page=999')
        self.assertEqual(clamped.context['page'].number, 2)
        # The visible rows on a clamped request match the actual last page.
        self.assertEqual(
            [row['email'] for row in clamped.context['page'].object_list],
            [row['email'] for row in last_page.context['page'].object_list],
        )

    def test_zero_page_clamps_to_first(self):
        response = self.client.get('/studio/users/?page=0')
        self.assertEqual(response.context['page'].number, 1)

    def test_negative_page_clamps_to_first(self):
        response = self.client.get('/studio/users/?page=-1')
        self.assertEqual(response.context['page'].number, 1)

    def test_non_integer_page_clamps_to_first(self):
        response = self.client.get('/studio/users/?page=garbage')
        self.assertEqual(response.context['page'].number, 1)

    def test_range_indicator_uses_one_indexed_inclusive_bounds(self):
        response = self.client.get('/studio/users/?page=2')
        self.assertEqual(response.context['page_start_index'], 6)
        self.assertEqual(response.context['page_end_index'], 9)
        self.assertEqual(response.context['filtered_total'], 9)

    def test_range_indicator_rendered_in_html(self):
        response = self.client.get('/studio/users/?page=2')
        self.assertContains(response, 'Showing 6-9 of 9')

    def test_pager_next_link_preserves_filter_and_search(self):
        # The chips include the staff user too; q=staff narrows to just one
        # match -> single page -> no pager. Use a search that hits many
        # users: the email convention is userNNNN@... so q=user matches all.
        response = self.client.get('/studio/users/?filter=all&q=user&page=1')
        # 8 user000N rows match -> 2 pages.
        self.assertTrue(response.context['show_pager'])
        next_url = response.context['pager_next_url']
        self.assertIn('filter=all', next_url)
        self.assertIn('q=user', next_url)
        self.assertIn('page=2', next_url)

    def test_pager_preserves_slack_and_tag_params(self):
        for user in User.objects.filter(email__startswith='user'):
            user.tags = ['cohort-a']
            user.save(update_fields=['tags'])

        response = self.client.get('/studio/users/?slack=no&tag=&page=1')
        next_url = response.context['pager_next_url']
        self.assertIn('slack=no', next_url)
        # ``page`` is overwritten, not appended twice.
        self.assertEqual(next_url.count('page='), 1)

        response = self.client.get('/studio/users/?tag=cohort-a&page=1')
        next_url = response.context['pager_next_url']
        self.assertIn('tag=cohort-a', next_url)
        self.assertIn('page=2', next_url)

    def test_chip_links_do_not_carry_page_param(self):
        # Switching filter chips should reset to page 1, so the chip URLs
        # in the rendered template must NOT include the current page=N.
        response = self.client.get('/studio/users/?page=2')
        self.assertNotContains(response, 'filter=paid&amp;slack=any&amp;page=2')

    def test_pager_partial_does_not_leak_template_comment(self):
        # Regression: Django's ``{# ... #}`` comment syntax is single-line
        # only. A multi-line ``{# ... #}`` block in the pager partial would
        # render as raw text on the page. Use ``{% comment %}`` instead.
        response = self.client.get('/studio/users/?page=1')
        self.assertNotContains(response, 'Pager partial for the Studio users list')
        self.assertNotContains(response, 'Inputs (from user_list view)')


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class StudioUserListPagerHiddenTest(PatchedUserListPageSizeMixin, TestCase):
    """When the result set fits on one page, the pager is hidden."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='testpass',
            is_staff=True,
        )
        _create_users(4)

    def setUp(self):
        super().setUp()
        self.client.login(email='staff@test.com', password='testpass')

    def test_show_pager_false_for_single_page(self):
        response = self.client.get('/studio/users/')
        self.assertFalse(response.context['show_pager'])

    def test_pager_partial_not_rendered_when_single_page(self):
        response = self.client.get('/studio/users/')
        self.assertNotContains(response, 'data-testid="user-list-pager"')

    def test_single_page_still_shows_all_rows(self):
        response = self.client.get('/studio/users/')
        # 5 users (4 + staff), all visible.
        self.assertEqual(len(response.context['page'].object_list), 5)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class StudioUserListPagerEmptyTest(TestCase):
    """Zero-result filters don't blow up; one-page is reported with 0 rows."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='testpass',
            is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_zero_result_search_returns_one_page_with_no_rows(self):
        response = self.client.get('/studio/users/?q=zzznosuchuser')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['page'].object_list), 0)
        self.assertEqual(response.context['filtered_total'], 0)
        # An empty result set is one logical page; pager stays hidden.
        self.assertFalse(response.context['show_pager'])


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class StudioUserListPagerFourRowsTest(PatchedUserListPageSizeMixin, TestCase):
    """4 rows is one full-but-not-full page; no pager rendered."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='testpass',
            is_staff=True,
        )
        _create_users(3)  # +staff = 4 total

    def setUp(self):
        super().setUp()
        self.client.login(email='staff@test.com', password='testpass')

    def test_four_rows_fit_in_one_page(self):
        response = self.client.get('/studio/users/')
        self.assertEqual(len(response.context['page'].object_list), 4)
        self.assertFalse(response.context['show_pager'])


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class StudioUserListPagerExactlyFiveTest(PatchedUserListPageSizeMixin, TestCase):
    """Exactly 5 rows is still one page; no pager."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='testpass',
            is_staff=True,
        )
        _create_users(4)  # +staff = 5 total

    def setUp(self):
        super().setUp()
        self.client.login(email='staff@test.com', password='testpass')

    def test_five_rows_fit_in_one_page(self):
        response = self.client.get('/studio/users/')
        self.assertEqual(len(response.context['page'].object_list), 5)
        self.assertEqual(response.context['paginator'].num_pages, 1)
        self.assertFalse(response.context['show_pager'])


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class StudioUserListPagerSixRowsTest(PatchedUserListPageSizeMixin, TestCase):
    """6 rows triggers pagination: 5 + 1."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='testpass',
            is_staff=True,
        )
        _create_users(5)  # +staff = 6 total

    def setUp(self):
        super().setUp()
        self.client.login(email='staff@test.com', password='testpass')

    def test_six_rows_split_into_two_pages(self):
        first = self.client.get('/studio/users/?page=1')
        second = self.client.get('/studio/users/?page=2')
        self.assertEqual(len(first.context['page'].object_list), 5)
        self.assertEqual(len(second.context['page'].object_list), 1)
        self.assertTrue(first.context['show_pager'])


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class StudioUserListPagerFifteenRowsTest(PatchedUserListPageSizeMixin, TestCase):
    """15 rows = exactly 3 pages (5 + 5 + 5)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='testpass',
            is_staff=True,
        )
        _create_users(14)  # +staff = 15 total

    def setUp(self):
        super().setUp()
        self.client.login(email='staff@test.com', password='testpass')

    def test_three_pages_returned(self):
        response = self.client.get('/studio/users/')
        self.assertEqual(response.context['paginator'].num_pages, 3)

    def test_middle_page_has_prev_and_next(self):
        response = self.client.get('/studio/users/?page=2')
        self.assertTrue(response.context['page'].has_previous())
        self.assertTrue(response.context['page'].has_next())
        self.assertIsNotNone(response.context['pager_first_url'])
        self.assertIsNotNone(response.context['pager_last_url'])

    def test_last_page_disables_next_and_last(self):
        response = self.client.get('/studio/users/?page=3')
        self.assertIsNone(response.context['pager_next_url'])
        self.assertIsNone(response.context['pager_last_url'])


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class StudioUserExportUnpaginatedTest(PatchedUserListPageSizeMixin, TestCase):
    """The CSV export endpoint always returns the full filtered set.

    Pagination must NOT leak into the export. This is the operator's
    escape hatch for "I need every row" so we explicitly verify the
    export ignores ``page=`` and returns 8 rows out of an 8-row filter.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='testpass',
            is_staff=True,
        )
        # All 8 paid users get the 'paid' tag so we can filter on it.
        users = _create_users(8)
        for user in users:
            user.tags = ['paid']
            user.save(update_fields=['tags'])

    def setUp(self):
        super().setUp()
        self.client.login(email='staff@test.com', password='testpass')

    def test_export_returns_all_filtered_rows(self):
        response = self.client.get('/studio/users/export?tag=paid')
        rows = list(csv.DictReader(io.StringIO(response.content.decode())))
        self.assertEqual(len(rows), 8)

    def test_export_ignores_page_param(self):
        # Even with ?page=2 explicitly set, the export returns everything.
        response = self.client.get('/studio/users/export?tag=paid&page=2')
        rows = list(csv.DictReader(io.StringIO(response.content.decode())))
        self.assertEqual(len(rows), 8)
