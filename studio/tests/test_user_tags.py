"""Tests for the Studio contact-tag UI (issue #354).

Covers:
- the ``?tag=`` query filter on /studio/users/ (combines with filter + q,
  normalizes server-side)
- the active-tag chip + clear link
- the new /studio/users/<id>/ detail page
- POST /studio/users/<id>/tags/add (normalizes, idempotent, rejects empty)
- POST /studio/users/<id>/tags/remove (idempotent)
- staff-only access on every endpoint
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from payments.models import Tier

User = get_user_model()


class StudioUserListTagFilterTest(TestCase):
    """``?tag=`` filters the listing and combines with ``filter`` + ``q``."""

    @classmethod
    def setUpTestData(cls):
        cls.main_tier = Tier.objects.get(slug='main')

        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.alice = User.objects.create_user(
            email='alice@test.com', password='testpass',
            tags=['early-adopter', 'beta'],
        )
        cls.bob = User.objects.create_user(
            email='bob@test.com', password='testpass',
            tags=['beta'],
        )
        cls.carol = User.objects.create_user(
            email='carol@test.com', password='testpass',
        )
        # A paid user with the early-adopter tag (used by combined-filter test).
        cls.dan = User.objects.create_user(
            email='dan@test.com', password='testpass',
            tier=cls.main_tier, tags=['early-adopter'],
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_tag_filter_returns_only_tagged_users(self):
        response = self.client.get('/studio/users/?tag=early-adopter')
        emails = [row['email'] for row in response.context['user_rows']]
        self.assertEqual(set(emails), {'alice@test.com', 'dan@test.com'})

    def test_tag_filter_normalizes_query_param(self):
        response = self.client.get('/studio/users/?tag=Early%20Adopter')
        emails = [row['email'] for row in response.context['user_rows']]
        self.assertEqual(set(emails), {'alice@test.com', 'dan@test.com'})

    def test_tag_filter_combines_with_paid_filter(self):
        response = self.client.get('/studio/users/?tag=early-adopter&filter=paid')
        emails = [row['email'] for row in response.context['user_rows']]
        # Only Dan is both paid (Main) AND tagged early-adopter; Alice is
        # tagged but on free.
        self.assertEqual(emails, ['dan@test.com'])

    def test_tag_filter_combines_with_search(self):
        response = self.client.get('/studio/users/?tag=early-adopter&q=alice')
        emails = [row['email'] for row in response.context['user_rows']]
        self.assertEqual(emails, ['alice@test.com'])

    def test_active_tag_in_context_is_normalized(self):
        response = self.client.get('/studio/users/?tag=Early%20Adopter')
        self.assertEqual(response.context['active_tag'], 'early-adopter')

    def test_no_tag_param_means_no_tag_filtering(self):
        response = self.client.get('/studio/users/')
        emails = {row['email'] for row in response.context['user_rows']}
        # All five users are listed.
        self.assertIn('carol@test.com', emails)
        self.assertEqual(response.context['active_tag'], '')

    def test_tags_column_shows_chips(self):
        response = self.client.get('/studio/users/')
        # Alice's chips appear in the Tags column.
        self.assertContains(response, 'early-adopter')
        self.assertContains(response, 'beta')

    def test_list_rows_render_single_line_email_with_full_value_access(self):
        long_email = 'very-long-address-for-studio-scanability@example.test'
        User.objects.create_user(email=long_email, password='testpass')

        response = self.client.get('/studio/users/?q=very-long-address')

        self.assertContains(response, 'data-testid="user-email"')
        self.assertContains(response, 'class="text-sm font-medium text-foreground truncate"')
        self.assertContains(response, f'title="{long_email}"')
        self.assertContains(response, f'aria-label="Email {long_email}"')

    def test_membership_state_is_grouped_into_compact_badges(self):
        self.alice.slack_member = True
        self.alice.slack_checked_at = self.alice.date_joined
        self.alice.save(update_fields=['slack_member', 'slack_checked_at'])

        response = self.client.get('/studio/users/?q=alice')

        self.assertContains(response, 'data-testid="membership-badges"')
        self.assertContains(response, self.alice.tier.name)
        self.assertContains(response, 'Newsletter')
        self.assertContains(response, 'Slack')
        self.assertContains(response, 'Active')

    def test_tag_display_is_bounded_with_visible_filter_links_and_overflow(self):
        self.alice.tags = [
            'early-adopter',
            'beta',
            'paid-2026',
            'vip',
            'cohort-a',
        ]
        self.alice.slack_member = True
        self.alice.slack_checked_at = self.alice.date_joined
        self.alice.tier = self.main_tier
        self.alice.save(update_fields=['tags', 'slack_member', 'slack_checked_at', 'tier'])

        response = self.client.get('/studio/users/?filter=paid&slack=yes&q=alice')
        row = response.context['user_rows'][0]

        self.assertEqual(row['visible_tags'], ['early-adopter', 'beta', 'paid-2026'])
        self.assertEqual(row['tag_overflow_count'], 2)
        self.assertEqual(row['hidden_tags_label'], 'vip, cohort-a')
        self.assertContains(
            response,
            '?filter=paid&amp;slack=yes&amp;q=alice&amp;tag=early-adopter',
        )
        self.assertContains(response, 'data-testid="user-tags-overflow">+2</span>')
        self.assertContains(response, 'aria-label="2 more tags: vip, cohort-a"')
        self.assertNotContains(response, '>vip</a>')

    def test_active_tag_chip_renders_with_clear_link(self):
        response = self.client.get('/studio/users/?tag=early-adopter&filter=paid&q=dan')
        self.assertContains(response, 'data-testid="active-tag-chip"')
        self.assertContains(response, 'Tag: early-adopter')
        # Clear link drops ``tag`` but preserves ``filter``, ``slack``,
        # and ``q``. (Issue #358 added the slack filter to chip URLs.)
        self.assertContains(response, '?filter=paid&amp;slack=any&amp;q=dan')

    def test_filter_chip_links_carry_tag_value(self):
        response = self.client.get('/studio/users/?tag=early-adopter')
        self.assertContains(response, 'tag=early-adopter')

    def test_export_link_preserves_active_filters_search_slack_and_tag(self):
        response = self.client.get(
            '/studio/users/?filter=paid&slack=yes&q=alice&tag=early-adopter',
        )

        self.assertContains(
            response,
            '/studio/users/export?filter=paid&amp;slack=yes&amp;q=alice&amp;tag=early-adopter',
        )

    def test_view_link_present_for_each_user(self):
        response = self.client.get('/studio/users/')
        self.assertContains(response, f'/studio/users/{self.alice.pk}/')
        self.assertContains(response, f'/studio/users/{self.dan.pk}/')

    def test_non_staff_cannot_access_tag_filter(self):
        self.client.logout()
        User.objects.create_user(
            email='regular@test.com', password='testpass', is_staff=False,
        )
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.get('/studio/users/?tag=early-adopter')
        self.assertEqual(response.status_code, 403)


class StudioUserDetailTest(TestCase):
    """``GET /studio/users/<id>/`` renders the staff-only detail page."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='testpass',
            tags=['early-adopter'],
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_detail_returns_200(self):
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        self.assertEqual(response.status_code, 200)

    def test_detail_uses_correct_template(self):
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        self.assertTemplateUsed(response, 'studio/users/detail.html')

    def test_detail_shows_email_and_existing_tag(self):
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        self.assertContains(response, 'member@test.com')
        self.assertContains(response, 'early-adopter')

    def test_detail_known_tags_in_context(self):
        # Datalist is fed from User.objects.values_list('tags', flat=True).
        # Add a second user with an extra tag to confirm dedup + sort.
        User.objects.create_user(
            email='other@test.com', password='x', tags=['beta', 'early-adopter'],
        )
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        self.assertEqual(response.context['known_tags'], ['beta', 'early-adopter'])

    def test_detail_renders_datalist_options(self):
        User.objects.create_user(
            email='other@test.com', password='x', tags=['beta'],
        )
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        self.assertContains(response, '<datalist id="known-contact-tags">')
        self.assertContains(response, '<option value="beta">')

    def test_detail_404_for_missing_user(self):
        response = self.client.get('/studio/users/999999/')
        self.assertEqual(response.status_code, 404)

    def test_detail_non_staff_forbidden(self):
        self.client.logout()
        User.objects.create_user(
            email='regular@test.com', password='testpass', is_staff=False,
        )
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.get(f'/studio/users/{self.member.pk}/')
        self.assertEqual(response.status_code, 403)


class StudioUserTagAddTest(TestCase):
    """POST ``/studio/users/<id>/tags/add``."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='testpass',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_add_tag_normalizes_and_redirects(self):
        response = self.client.post(
            f'/studio/users/{self.member.pk}/tags/add',
            {'tag': 'Early Adopter'},
        )
        self.assertRedirects(response, f'/studio/users/{self.member.pk}/')
        self.member.refresh_from_db()
        self.assertEqual(self.member.tags, ['early-adopter'])

    def test_add_tag_is_idempotent(self):
        self.client.post(
            f'/studio/users/{self.member.pk}/tags/add',
            {'tag': 'early-adopter'},
        )
        response = self.client.post(
            f'/studio/users/{self.member.pk}/tags/add',
            {'tag': 'EARLY ADOPTER'},
        )
        self.assertEqual(response.status_code, 302)
        self.member.refresh_from_db()
        self.assertEqual(self.member.tags, ['early-adopter'])

    def test_empty_tag_input_is_rejected_with_flash(self):
        response = self.client.post(
            f'/studio/users/{self.member.pk}/tags/add',
            {'tag': '   '},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.member.refresh_from_db()
        self.assertEqual(self.member.tags, [])
        # The flash message is in the response.
        self.assertContains(response, 'Enter a tag')

    def test_add_tag_get_not_allowed(self):
        response = self.client.get(f'/studio/users/{self.member.pk}/tags/add')
        self.assertEqual(response.status_code, 405)

    def test_add_tag_non_staff_forbidden(self):
        self.client.logout()
        User.objects.create_user(
            email='regular@test.com', password='testpass', is_staff=False,
        )
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.post(
            f'/studio/users/{self.member.pk}/tags/add',
            {'tag': 'early-adopter'},
        )
        self.assertEqual(response.status_code, 403)
        self.member.refresh_from_db()
        self.assertEqual(self.member.tags, [])


class StudioUserTagRemoveTest(TestCase):
    """POST ``/studio/users/<id>/tags/remove``."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')
        # Use setUp (not setUpTestData) for the row whose tags we mutate so
        # each test gets a fresh ``tags`` list.
        self.member = User.objects.create_user(
            email=f'member-{self._testMethodName}@test.com',
            password='testpass',
            tags=['early-adopter', 'beta'],
        )

    def test_remove_tag_persists_and_redirects(self):
        response = self.client.post(
            f'/studio/users/{self.member.pk}/tags/remove',
            {'tag': 'early-adopter'},
        )
        self.assertRedirects(response, f'/studio/users/{self.member.pk}/')
        self.member.refresh_from_db()
        self.assertEqual(self.member.tags, ['beta'])

    def test_remove_missing_tag_is_idempotent(self):
        response = self.client.post(
            f'/studio/users/{self.member.pk}/tags/remove',
            {'tag': 'never-added'},
        )
        self.assertEqual(response.status_code, 302)
        self.member.refresh_from_db()
        self.assertEqual(self.member.tags, ['early-adopter', 'beta'])

    def test_remove_tag_get_not_allowed(self):
        response = self.client.get(f'/studio/users/{self.member.pk}/tags/remove')
        self.assertEqual(response.status_code, 405)

    def test_remove_tag_non_staff_forbidden(self):
        self.client.logout()
        User.objects.create_user(
            email='regular@test.com', password='testpass', is_staff=False,
        )
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.post(
            f'/studio/users/{self.member.pk}/tags/remove',
            {'tag': 'early-adopter'},
        )
        self.assertEqual(response.status_code, 403)
        self.member.refresh_from_db()
        self.assertEqual(self.member.tags, ['early-adopter', 'beta'])
