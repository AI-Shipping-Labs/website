"""Broadened ``?q=`` search on the Studio users list (issue #438).

The pre-existing email-substring match is preserved. Search now also
OR-matches against ``first_name``, ``last_name``, ``stripe_customer_id``,
and any tag (after ``normalize_tag``) on the user.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class StudioUserListSearchTest(TestCase):
    """Each new field is matched independently; original email match still works."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='testpass',
            is_staff=True,
        )
        cls.ada = User.objects.create_user(
            email='ada@example.com',
            password='testpass',
            first_name='Ada',
            last_name='Lovelace',
        )
        cls.ada.tags = ['ai-buildcamp', 'maven']
        cls.ada.save(update_fields=['tags'])

        cls.grace = User.objects.create_user(
            email='grace@example.com',
            password='testpass',
            first_name='Grace',
            last_name='Hopper',
            stripe_customer_id='cus_USSV1H5ew94CBG',
        )
        cls.grace.tags = ['stripe']
        cls.grace.save(update_fields=['tags'])

        cls.alan = User.objects.create_user(
            email='alan@example.com',
            password='testpass',
            first_name='Alan',
            last_name='Turing',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def _emails_for(self, query):
        response = self.client.get('/studio/users/', {'q': query})
        return {row['email'] for row in response.context['page'].object_list}

    # --- email regression ----------------------------------------------------

    def test_search_email_substring_still_works(self):
        # Pre-existing behavior; locked in so the OR rewrite cannot regress it.
        self.assertEqual(self._emails_for('ada@'), {'ada@example.com'})

    def test_empty_q_returns_all_users(self):
        # The staff user is included too because they're a user row.
        emails = self._emails_for('')
        self.assertEqual(
            emails,
            {
                'staff@test.com',
                'ada@example.com',
                'grace@example.com',
                'alan@example.com',
            },
        )

    # --- first/last name -----------------------------------------------------

    def test_search_matches_first_name(self):
        self.assertEqual(self._emails_for('Ada'), {'ada@example.com'})

    def test_search_first_name_case_insensitive(self):
        self.assertEqual(self._emails_for('ada'), {'ada@example.com'})

    def test_search_matches_last_name_case_insensitive(self):
        self.assertEqual(self._emails_for('lovelace'), {'ada@example.com'})

    def test_search_substring_of_last_name(self):
        # 'turing' substring 'tur' should still hit Alan.
        self.assertIn('alan@example.com', self._emails_for('tur'))

    # --- stripe customer id --------------------------------------------------

    def test_search_matches_stripe_customer_id_prefix(self):
        self.assertEqual(self._emails_for('cus_USS'), {'grace@example.com'})

    def test_search_matches_stripe_customer_id_lowercase(self):
        # Search is case-insensitive.
        self.assertEqual(self._emails_for('cus_uss'), {'grace@example.com'})

    def test_search_matches_stripe_customer_id_substring(self):
        # Mid-string substring still hits.
        self.assertEqual(self._emails_for('USSV1H5'), {'grace@example.com'})

    def test_blank_stripe_customer_id_does_not_match_empty_query_substring(self):
        # If a user has stripe_customer_id='', the 'cus_' search MUST NOT
        # match them (substring of '' is empty, but we guard on truthy).
        self.assertNotIn('alan@example.com', self._emails_for('cus_'))
        self.assertNotIn('ada@example.com', self._emails_for('cus_'))

    # --- tag matching --------------------------------------------------------

    def test_search_matches_tag_substring(self):
        self.assertEqual(self._emails_for('buildcamp'), {'ada@example.com'})

    def test_search_matches_full_tag(self):
        self.assertEqual(self._emails_for('ai-buildcamp'), {'ada@example.com'})

    def test_search_normalizes_input_for_tag_match(self):
        # ``normalize_tag('AI Buildcamp')`` -> 'ai-buildcamp'. The OR-arm
        # for tags runs the input through normalize_tag before matching.
        self.assertEqual(self._emails_for('AI Buildcamp'), {'ada@example.com'})

    def test_search_combined_or_match(self):
        # 'a' is a substring of every email here, so all three example.com
        # users + staff match. This is intentional: empty/short q is permissive.
        self.assertIn('ada@example.com', self._emails_for('a'))
        self.assertIn('grace@example.com', self._emails_for('a'))
        self.assertIn('alan@example.com', self._emails_for('a'))

    def test_search_or_across_email_and_name_returns_one_user(self):
        # 'lovelace' only matches Ada (last name), not anyone's email or
        # tags. Verifies the name-only branch is reachable in isolation.
        self.assertEqual(self._emails_for('lovelace'), {'ada@example.com'})

    def test_search_or_across_stripe_id_only(self):
        # 'USSV' only appears in Grace's stripe_customer_id, nowhere else.
        self.assertEqual(self._emails_for('USSV'), {'grace@example.com'})

    def test_search_no_match_returns_empty_page(self):
        emails = self._emails_for('zznosuchstring')
        self.assertEqual(emails, set())

    def test_search_placeholder_text_updated(self):
        response = self.client.get('/studio/users/')
        self.assertContains(response, 'Email, name, Stripe ID, or tag')


class StudioUserListSearchTagFilterUnchangedTest(TestCase):
    """The ``?tag=`` chip filter stays exact-equality after normalization.

    Issue #438 only changes ``?q=``. The chip filter is a separate
    code path with different semantics (exact tag equality), and the spec
    is explicit about leaving it alone.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='testpass',
            is_staff=True,
        )
        cls.ada = User.objects.create_user(
            email='ada@example.com',
            password='testpass',
        )
        cls.ada.tags = ['ai-buildcamp']
        cls.ada.save(update_fields=['tags'])

        cls.grace = User.objects.create_user(
            email='grace@example.com',
            password='testpass',
        )
        cls.grace.tags = ['ai-buildcamp-2026']  # contains 'ai-buildcamp'
        cls.grace.save(update_fields=['tags'])

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_tag_chip_uses_exact_equality_not_substring(self):
        # The chip is exact-equality, so it returns only ada (whose tag is
        # exactly 'ai-buildcamp'), NOT grace (whose tag is 'ai-buildcamp-2026').
        response = self.client.get('/studio/users/?tag=ai-buildcamp')
        emails = {row['email'] for row in response.context['page'].object_list}
        self.assertEqual(emails, {'ada@example.com'})

    def test_q_search_uses_substring_unlike_tag_chip(self):
        # Same string, different field: q= is substring, tag= is exact.
        # Both ada and grace have tags containing 'ai-buildcamp' as substring.
        response = self.client.get('/studio/users/?q=ai-buildcamp')
        emails = {row['email'] for row in response.context['page'].object_list}
        self.assertEqual(emails, {'ada@example.com', 'grace@example.com'})
