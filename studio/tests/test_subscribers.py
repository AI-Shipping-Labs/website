"""Tests for the Studio users list / CSV export and subscriber redirect shims."""

import csv
import io
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import TierOverride
from payments.models import Tier

User = get_user_model()


def _parse_csv(response):
    """Decode the response body and parse it as CSV via DictReader."""
    return list(csv.DictReader(io.StringIO(response.content.decode())))


class StudioUserListTest(TestCase):
    """Render and filter the /studio/users/ page."""

    @classmethod
    def setUpTestData(cls):
        cls.free_tier = Tier.objects.get(slug='free')
        cls.main_tier = Tier.objects.get(slug='main')
        cls.premium_tier = Tier.objects.get(slug='premium')

        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='testpass',
            is_staff=True,
            unsubscribed=True,
        )
        cls.alice = User.objects.create_user(
            email='alice@test.com',
            password='testpass',
        )
        cls.bob = User.objects.create_user(
            email='bob@test.com',
            password='testpass',
            unsubscribed=True,
        )
        cls.main_user = User.objects.create_user(
            email='main@test.com',
            password='testpass',
            tier=cls.main_tier,
        )
        cls.premium_user = User.objects.create_user(
            email='premium@test.com',
            password='testpass',
            tier=cls.premium_tier,
        )
        cls.override_user = User.objects.create_user(
            email='override@test.com',
            password='testpass',
            tier=cls.free_tier,
        )
        TierOverride.objects.create(
            user=cls.override_user,
            original_tier=cls.free_tier,
            override_tier=cls.premium_tier,
            expires_at=timezone.now() + timedelta(days=14),
            is_active=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_list_returns_200(self):
        response = self.client.get('/studio/users/')
        self.assertEqual(response.status_code, 200)

    def test_list_uses_correct_template(self):
        response = self.client.get('/studio/users/')
        self.assertTemplateUsed(response, 'studio/users/list.html')

    def test_default_filter_is_all(self):
        response = self.client.get('/studio/users/')
        self.assertEqual(response.context['active_filter'], 'all')

    def test_default_view_lists_all_users(self):
        response = self.client.get('/studio/users/')
        emails = [row['email'] for row in response.context['user_rows']]
        self.assertEqual(
            emails,
            [
                'override@test.com',
                'premium@test.com',
                'main@test.com',
                'bob@test.com',
                'alice@test.com',
                'staff@test.com',
            ],
        )

    def test_filter_paid_uses_effective_tier(self):
        response = self.client.get('/studio/users/?filter=paid')
        emails = [row['email'] for row in response.context['user_rows']]
        self.assertEqual(
            emails,
            ['override@test.com', 'premium@test.com', 'main@test.com'],
        )

    def test_filter_main_plus_uses_effective_tier(self):
        response = self.client.get('/studio/users/?filter=main_plus')
        emails = [row['email'] for row in response.context['user_rows']]
        self.assertEqual(
            emails,
            ['override@test.com', 'premium@test.com', 'main@test.com'],
        )

    def test_filter_premium_uses_effective_tier(self):
        response = self.client.get('/studio/users/?filter=premium')
        emails = [row['email'] for row in response.context['user_rows']]
        self.assertEqual(
            emails,
            ['override@test.com', 'premium@test.com'],
        )

    def test_filter_subscribers_uses_user_newsletter_preference(self):
        response = self.client.get('/studio/users/?filter=subscribers')
        emails = [row['email'] for row in response.context['user_rows']]
        self.assertEqual(
            emails,
            ['override@test.com', 'premium@test.com', 'main@test.com', 'alice@test.com'],
        )

    def test_unknown_filter_falls_back_to_all(self):
        response = self.client.get('/studio/users/?filter=garbage')
        self.assertEqual(response.context['active_filter'], 'all')

    def test_search_filters_within_chip(self):
        response = self.client.get('/studio/users/?filter=paid&q=override')
        emails = [row['email'] for row in response.context['user_rows']]
        self.assertEqual(emails, ['override@test.com'])

    def test_search_value_preserved_in_form(self):
        response = self.client.get('/studio/users/?filter=paid&q=override')
        self.assertContains(response, 'value="override"')

    def test_chip_links_carry_search_value(self):
        # Issue #358: chips also carry the slack filter so combining
        # chip clicks doesn't reset the slack filter back to "any".
        response = self.client.get('/studio/users/?filter=all&q=alice')
        self.assertContains(response, '?filter=all&amp;slack=any&amp;q=alice')
        self.assertContains(response, '?filter=paid&amp;slack=any&amp;q=alice')
        self.assertContains(response, '?filter=main_plus&amp;slack=any&amp;q=alice')
        self.assertContains(response, '?filter=premium&amp;slack=any&amp;q=alice')
        self.assertContains(response, '?filter=subscribers&amp;slack=any&amp;q=alice')

    def test_subscribed_column_uses_user_unsubscribed_flag(self):
        response = self.client.get('/studio/users/?filter=all')
        rows = {row['email']: row for row in response.context['user_rows']}
        self.assertTrue(rows['alice@test.com']['is_subscribed'])
        self.assertFalse(rows['bob@test.com']['is_subscribed'])

    def test_tier_column_shows_effective_override_tier(self):
        response = self.client.get('/studio/users/?filter=all')
        rows = {row['email']: row for row in response.context['user_rows']}
        self.assertEqual(rows['override@test.com']['tier_name'], 'Premium (override)')

    def test_tier_column_shows_paid_tier_name(self):
        response = self.client.get('/studio/users/?filter=all')
        rows = {row['email']: row for row in response.context['user_rows']}
        self.assertEqual(rows['main@test.com']['tier_name'], 'Main')
        self.assertEqual(rows['premium@test.com']['tier_name'], 'Premium')

    def test_status_column_marks_staff(self):
        response = self.client.get('/studio/users/?filter=all')
        rows = {row['email']: row for row in response.context['user_rows']}
        self.assertEqual(rows['staff@test.com']['status'], 'Staff')

    def test_status_column_marks_inactive(self):
        User.objects.create_user(
            email='deactivated@test.com',
            password='x',
            is_active=False,
        )
        response = self.client.get('/studio/users/?filter=all')
        rows = {row['email']: row for row in response.context['user_rows']}
        self.assertEqual(rows['deactivated@test.com']['status'], 'Inactive')

    def test_status_column_marks_regular_active(self):
        response = self.client.get('/studio/users/?filter=all')
        rows = {row['email']: row for row in response.context['user_rows']}
        self.assertEqual(rows['alice@test.com']['status'], 'Active')

    def test_login_as_button_present_for_every_user(self):
        response = self.client.get('/studio/users/?filter=all')
        self.assertContains(response, f'/studio/impersonate/{self.alice.pk}/')
        self.assertContains(response, f'/studio/impersonate/{self.override_user.pk}/')

    def test_counts_in_context(self):
        response = self.client.get('/studio/users/')
        self.assertEqual(response.context['total_users'], 6)
        self.assertEqual(response.context['paid_count'], 3)
        self.assertEqual(response.context['main_plus_count'], 3)
        self.assertEqual(response.context['premium_count'], 2)
        self.assertEqual(response.context['subscriber_count'], 4)


class StudioUserListSlackFilterTest(TestCase):
    """Issue #358: Slack-membership column + filter on /studio/users/."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='testpass',
            slack_member=True, slack_checked_at=timezone.now(),
        )
        cls.outsider = User.objects.create_user(
            email='outsider@test.com', password='testpass',
            slack_member=False, slack_checked_at=timezone.now(),
        )
        cls.unchecked = User.objects.create_user(
            email='unchecked@test.com', password='testpass',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_filter_yes_returns_only_members(self):
        response = self.client.get('/studio/users/?slack=yes')
        emails = [row['email'] for row in response.context['user_rows']]
        self.assertEqual(emails, ['member@test.com'])

    def test_filter_no_returns_non_members_including_unchecked(self):
        response = self.client.get('/studio/users/?slack=no')
        emails = sorted(row['email'] for row in response.context['user_rows'])
        self.assertEqual(
            emails,
            ['outsider@test.com', 'staff@test.com', 'unchecked@test.com'],
        )

    def test_filter_any_default(self):
        response = self.client.get('/studio/users/?slack=garbage')
        self.assertEqual(response.context['slack_filter'], 'any')

    def test_slack_status_column_renders_three_states(self):
        response = self.client.get('/studio/users/')
        rows = {row['email']: row for row in response.context['user_rows']}
        self.assertEqual(rows['member@test.com']['slack_status'], 'Member')
        self.assertEqual(rows['outsider@test.com']['slack_status'], 'Not in Slack')
        self.assertEqual(rows['unchecked@test.com']['slack_status'], 'Never checked')

    def test_slack_member_count_in_context(self):
        response = self.client.get('/studio/users/')
        self.assertEqual(response.context['slack_member_count'], 1)

    def test_csv_export_includes_slack_column(self):
        response = self.client.get('/studio/users/export')
        rows = _parse_csv(response)
        by_email = {row['email']: row for row in rows}
        self.assertEqual(by_email['member@test.com']['slack'], 'Member')
        self.assertEqual(by_email['outsider@test.com']['slack'], 'Not in Slack')
        self.assertEqual(by_email['unchecked@test.com']['slack'], 'Never checked')

    def test_filter_combines_with_tier_filter(self):
        # Make member also paid.
        main_tier = Tier.objects.get(slug='main')
        StudioUserListSlackFilterTest.member.tier = main_tier
        StudioUserListSlackFilterTest.member.save()

        response = self.client.get('/studio/users/?filter=paid&slack=yes')
        emails = [row['email'] for row in response.context['user_rows']]
        self.assertEqual(emails, ['member@test.com'])

        # Reset.
        StudioUserListSlackFilterTest.member.tier = None
        StudioUserListSlackFilterTest.member.save()


class StudioUserExportTest(TestCase):
    """CSV export at /studio/users/export.

    Columns and filename were re-locked in issue #355: header is
    ``email,tier,tags,email_verified,unsubscribed,date_joined,last_login``
    and the filename includes a UTC timestamp for provenance.
    """

    @classmethod
    def setUpTestData(cls):
        cls.free_tier = Tier.objects.get(slug='free')
        cls.main_tier = Tier.objects.get(slug='main')
        cls.premium_tier = Tier.objects.get(slug='premium')

        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='testpass',
            is_staff=True,
            unsubscribed=True,
        )
        cls.alice = User.objects.create_user(
            email='alice@test.com',
            password='testpass',
        )
        cls.alice.tags = ['early-adopter', 'paid-2026']
        cls.alice.save(update_fields=['tags'])
        cls.bob = User.objects.create_user(
            email='bob@test.com',
            password='testpass',
            unsubscribed=True,
        )
        cls.main_user = User.objects.create_user(
            email='main@test.com',
            password='testpass',
            tier=cls.main_tier,
        )
        cls.override_user = User.objects.create_user(
            email='override@test.com',
            password='testpass',
            tier=cls.free_tier,
        )
        cls.override_user.tags = ['vip']
        cls.override_user.save(update_fields=['tags'])
        TierOverride.objects.create(
            user=cls.override_user,
            original_tier=cls.free_tier,
            override_tier=cls.premium_tier,
            expires_at=timezone.now() + timedelta(days=14),
            is_active=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def _row_by_email(self, response, email):
        rows = _parse_csv(response)
        for row in rows:
            if row['email'] == email:
                return row
        raise AssertionError(f'No row for {email}; rows: {rows}')

    def test_export_returns_csv(self):
        response = self.client.get('/studio/users/export')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn('attachment', response['Content-Disposition'])

    def test_export_filename_uses_timestamped_pattern(self):
        response = self.client.get('/studio/users/export')
        # The filename embeds a UTC timestamp; assert on the locked pattern,
        # not a specific instant, so the test is not flaky around midnight.
        match = re.search(
            r'filename="(aishippinglabs-contacts-\d{8}-\d{6}\.csv)"',
            response['Content-Disposition'],
        )
        self.assertIsNotNone(
            match,
            f"Content-Disposition did not match pattern: {response['Content-Disposition']!r}",
        )

    def test_export_header_lists_locked_columns(self):
        response = self.client.get('/studio/users/export')
        first_line = response.content.decode().splitlines()[0]
        self.assertEqual(
            first_line,
            'email,tier,tags,email_verified,unsubscribed,date_joined,last_login,slack',
        )

    def test_export_dictreader_fieldnames_match_locked_set(self):
        response = self.client.get('/studio/users/export')
        reader = csv.DictReader(io.StringIO(response.content.decode()))
        self.assertEqual(
            reader.fieldnames,
            [
                'email',
                'tier',
                'tags',
                'email_verified',
                'unsubscribed',
                'date_joined',
                'last_login',
                'slack',
            ],
        )

    def test_export_default_filter_is_all(self):
        response = self.client.get('/studio/users/export')
        emails = {row['email'] for row in _parse_csv(response)}
        self.assertEqual(
            emails,
            {
                'staff@test.com',
                'alice@test.com',
                'bob@test.com',
                'main@test.com',
                'override@test.com',
            },
        )

    def test_export_filter_paid(self):
        response = self.client.get('/studio/users/export?filter=paid')
        emails = {row['email'] for row in _parse_csv(response)}
        self.assertEqual(emails, {'main@test.com', 'override@test.com'})

    def test_export_filter_subscribers_uses_user_preference(self):
        response = self.client.get('/studio/users/export?filter=subscribers')
        emails = {row['email'] for row in _parse_csv(response)}
        self.assertEqual(
            emails,
            {'alice@test.com', 'main@test.com', 'override@test.com'},
        )

    def test_export_honours_search(self):
        response = self.client.get('/studio/users/export?filter=all&q=override')
        emails = {row['email'] for row in _parse_csv(response)}
        self.assertEqual(emails, {'override@test.com'})

    def test_export_honours_tag_filter(self):
        response = self.client.get('/studio/users/export?tag=vip')
        emails = {row['email'] for row in _parse_csv(response)}
        self.assertEqual(emails, {'override@test.com'})

    def test_export_tag_filter_normalizes_input(self):
        # Operator passes a non-normalized URL value; the export should
        # still resolve to the normalized tag stored on the user.
        response = self.client.get('/studio/users/export?tag=Early%20Adopter')
        emails = {row['email'] for row in _parse_csv(response)}
        self.assertEqual(emails, {'alice@test.com'})

    def test_export_tier_column_shows_override_tier(self):
        response = self.client.get('/studio/users/export?filter=all')
        row = self._row_by_email(response, 'override@test.com')
        self.assertEqual(row['tier'], 'Premium (override)')

    def test_export_tier_column_shows_paid_tier_name_without_override(self):
        response = self.client.get('/studio/users/export?filter=all')
        row = self._row_by_email(response, 'main@test.com')
        self.assertEqual(row['tier'], 'Main')

    def test_export_tier_column_shows_free_for_default_user(self):
        response = self.client.get('/studio/users/export?filter=all')
        row = self._row_by_email(response, 'alice@test.com')
        self.assertEqual(row['tier'], 'Free')

    def test_export_tags_cell_joins_with_commas(self):
        response = self.client.get('/studio/users/export?filter=all')
        row = self._row_by_email(response, 'alice@test.com')
        # csv.DictReader unquotes the cell, so we get the raw joined string.
        self.assertEqual(row['tags'], 'early-adopter,paid-2026')

    def test_export_tags_cell_empty_when_user_has_no_tags(self):
        response = self.client.get('/studio/users/export?filter=all')
        row = self._row_by_email(response, 'bob@test.com')
        self.assertEqual(row['tags'], '')

    def test_export_tags_cell_quoted_in_raw_csv_when_multiple(self):
        # The raw CSV bytes must quote a multi-tag cell so the embedded
        # commas don't bleed into the next column.
        response = self.client.get('/studio/users/export?filter=all')
        raw = response.content.decode()
        self.assertIn('"early-adopter,paid-2026"', raw)

    def test_export_email_verified_column_yes_no(self):
        response = self.client.get('/studio/users/export?filter=all')
        # alice was created via create_user; the default is email_verified=False.
        # We don't care which one alice is in; we only assert the cell
        # renders as the literal Yes/No (not True/False/empty).
        row = self._row_by_email(response, 'alice@test.com')
        self.assertIn(row['email_verified'], {'Yes', 'No'})

        # Force a known value and re-check.
        self.alice.email_verified = True
        self.alice.save(update_fields=['email_verified'])
        response = self.client.get('/studio/users/export?filter=all')
        row = self._row_by_email(response, 'alice@test.com')
        self.assertEqual(row['email_verified'], 'Yes')

    def test_export_unsubscribed_column_yes_no(self):
        response = self.client.get('/studio/users/export?filter=all')
        alice_row = self._row_by_email(response, 'alice@test.com')
        bob_row = self._row_by_email(response, 'bob@test.com')
        self.assertEqual(alice_row['unsubscribed'], 'No')
        self.assertEqual(bob_row['unsubscribed'], 'Yes')

    def test_export_date_joined_isoformat(self):
        response = self.client.get('/studio/users/export?filter=all')
        row = self._row_by_email(response, 'alice@test.com')
        # ISO 8601 from timezone-aware datetime -> contains T and timezone offset.
        self.assertEqual(row['date_joined'], self.alice.date_joined.isoformat())

    def test_export_last_login_empty_when_never_logged_in(self):
        # alice has never logged in; bob has never logged in either. The cell
        # must be the empty string, not the literal "None".
        response = self.client.get('/studio/users/export?filter=all')
        row = self._row_by_email(response, 'alice@test.com')
        self.assertEqual(row['last_login'], '')

    def test_export_last_login_isoformat_when_set(self):
        login_time = timezone.now()
        self.alice.last_login = login_time
        self.alice.save(update_fields=['last_login'])
        response = self.client.get('/studio/users/export?filter=all')
        row = self._row_by_email(response, 'alice@test.com')
        self.assertEqual(row['last_login'], login_time.isoformat())

    def test_export_drops_status_column(self):
        # The Status column was removed in issue #355.
        response = self.client.get('/studio/users/export?filter=all')
        reader = csv.DictReader(io.StringIO(response.content.decode()))
        self.assertNotIn('Status', reader.fieldnames)
        self.assertNotIn('status', reader.fieldnames)

    def test_export_non_staff_forbidden(self):
        User.objects.create_user(
            email='regular@test.com',
            password='testpass',
            is_staff=False,
        )
        self.client.logout()
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.get('/studio/users/export')
        self.assertEqual(response.status_code, 403)


class SubscriberRedirectShimTest(TestCase):
    """The old /studio/subscribers/ URLs 301-redirect to the new ones."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='testpass',
            is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_subscriber_list_redirects_permanently_to_subscribers_chip(self):
        response = self.client.get('/studio/subscribers/')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/studio/users/?filter=subscribers')

    def test_subscriber_export_redirects_permanently_to_subscribers_chip(self):
        response = self.client.get('/studio/subscribers/export')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            '/studio/users/export?filter=subscribers',
        )

    def test_redirect_followed_lands_on_user_list(self):
        response = self.client.get('/studio/subscribers/', follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studio/users/list.html')
        self.assertEqual(response.context['active_filter'], 'subscribers')
