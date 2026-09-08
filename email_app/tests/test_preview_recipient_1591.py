"""Issue #1591: the Studio preview can show the nameless-recipient copy.

``PREVIEW_CONTEXTS`` hardcodes ``user_name: 'Ada'``, so the degraded greeting
that roughly 7 in 10 accounts actually receive was invisible to whoever
reviews the copy.
"""

from django.test import SimpleTestCase

from email_app.services.preview_contexts import (
    PREVIEW_CONTEXTS,
    get_preview_context,
)


class PreviewRecipientTest(SimpleTestCase):
    def test_default_call_is_unchanged(self):
        self.assertEqual(
            get_preview_context('welcome'),
            dict(PREVIEW_CONTEXTS['welcome']),
        )
        self.assertEqual(get_preview_context('welcome')['user_name'], 'Ada')

    def test_explicit_named_recipient_matches_the_default(self):
        self.assertEqual(
            get_preview_context('welcome', recipient='named'),
            get_preview_context('welcome'),
        )

    def test_no_name_recipient_degrades_the_greeting_variables(self):
        context = get_preview_context('welcome', recipient='no_name')

        self.assertEqual(context['user_name'], 'there')
        self.assertEqual(context['member_name'], 'there')
        self.assertEqual(context['user_first_name'], '')
        self.assertEqual(context['user_email'], 'no-name@example.com')

    def test_no_name_recipient_never_uses_a_real_address(self):
        context = get_preview_context('welcome', recipient='no_name')
        self.assertTrue(context['user_email'].endswith('@example.com'))

    def test_no_name_recipient_keeps_the_other_placeholders(self):
        named = get_preview_context('event_registration')
        degraded = get_preview_context('event_registration', recipient='no_name')

        self.assertEqual(named['join_url'], degraded['join_url'])
        self.assertEqual(
            named['google_calendar_url'], degraded['google_calendar_url'],
        )

    def test_no_name_recipient_works_for_unknown_templates(self):
        context = get_preview_context('not_a_template', recipient='no_name')
        self.assertEqual(context['user_name'], 'there')

    def test_returned_context_is_a_copy(self):
        context = get_preview_context('welcome', recipient='no_name')
        context['user_name'] = 'mutated'
        self.assertEqual(PREVIEW_CONTEXTS['welcome']['user_name'], 'Ada')


class SprintPartnerIntroPreviewContextTest(SimpleTestCase):
    """The template greets on ``member_name`` and lists partner labels; both
    must be exercised in the preview pane."""

    def test_named_preview_has_greeting_and_partner_rows(self):
        context = get_preview_context('sprint_partner_intro')

        self.assertEqual(context['member_name'], 'Ada')
        self.assertEqual(context['partner_count'], len(context['partners']))
        self.assertTrue(all(p['name'] for p in context['partners']))
        self.assertTrue(all(p['email'] for p in context['partners']))

    def test_no_name_preview_keeps_partner_rows(self):
        context = get_preview_context(
            'sprint_partner_intro', recipient='no_name',
        )

        self.assertEqual(context['member_name'], 'there')
        self.assertTrue(all(p['name'] for p in context['partners']))
