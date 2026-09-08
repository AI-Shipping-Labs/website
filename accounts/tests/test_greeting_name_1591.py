"""Issue #1591: ``greeting_name`` is a greeting-safe sibling of ``display_name``.

``display_name`` falls back to the email local-part, which is correct on a
cohort-board card and wrong in an email greeting (``Hi x.arrieta,``). These
tests pin both behaviours so the two helpers cannot drift into each other.
"""

from types import SimpleNamespace

from django.test import SimpleTestCase

from accounts.utils.display import GREETING_FALLBACK, display_name, greeting_name


def _user(first='', last='', email='member@example.com'):
    return SimpleNamespace(first_name=first, last_name=last, email=email)


class GreetingNameTest(SimpleTestCase):
    def test_returns_full_name_when_both_names_present(self):
        self.assertEqual(
            greeting_name(_user('Ada', 'Lovelace')),
            'Ada Lovelace',
        )

    def test_returns_first_name_only(self):
        self.assertEqual(greeting_name(_user('Ada', '')), 'Ada')

    def test_returns_last_name_only(self):
        self.assertEqual(greeting_name(_user('', 'Lovelace')), 'Lovelace')

    def test_returns_empty_string_when_both_names_blank(self):
        self.assertEqual(greeting_name(_user()), '')

    def test_whitespace_only_names_count_as_blank(self):
        self.assertEqual(greeting_name(_user('  ', '\t')), '')

    def test_none_returns_empty_string(self):
        self.assertEqual(greeting_name(None), '')

    def test_never_returns_the_email_local_part(self):
        """The exact case that opened this issue."""
        nameless = _user(email='x.arrieta@ibernova.com')
        self.assertEqual(display_name(nameless), 'x.arrieta')
        self.assertEqual(greeting_name(nameless), '')

    def test_ignores_email_entirely_even_without_an_at_sign(self):
        self.assertEqual(greeting_name(_user(email='not-an-email')), '')

    def test_greeting_fallback_constant(self):
        self.assertEqual(GREETING_FALLBACK, 'there')
        self.assertEqual(greeting_name(_user()) or GREETING_FALLBACK, 'there')


class DisplayNameUnchangedTest(SimpleTestCase):
    """``display_name`` must keep its email-handle fallback: cohort boards,
    Studio, CRM and the users API all rely on it."""

    def test_still_falls_back_to_email_handle(self):
        self.assertEqual(
            display_name(_user(email='x.arrieta@ibernova.com')),
            'x.arrieta',
        )

    def test_still_prefers_the_real_name(self):
        self.assertEqual(display_name(_user('Ada', 'Lovelace')), 'Ada Lovelace')
