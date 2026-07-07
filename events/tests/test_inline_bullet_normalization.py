"""Issue #1126: inline dash-run descriptions (``lead: - a - b - c``) render as
real ``<ul>``/``<li>`` lists on both the event page (``render_description_html``)
and email (``render_email_markdown``) surfaces, without leaking literal ` - `
separators, while hyphenated words, em/en dashes, and lone ` - ` separators are
left untouched. The stored source text is never mutated.
"""

from django.test import TestCase
from django.utils import timezone

from content.utils.markdown import (
    normalize_inline_bullets,
    render_description_html,
    render_email_markdown,
)
from events.models import Event, EventSeries

FOCUS_DESCRIPTION = (
    'We will focus on: - Turning one base CV into domain-specific versions '
    '- Making the CV easier to adjust - Improving ATS readability'
)
BRING_DESCRIPTION = (
    'Bring: - your current CV - one or two job descriptions or companies '
    'you want to target'
)


class InlineBulletNormalizerTest(TestCase):
    """The pre-parse normalizer converts inline dash-runs to markdown lists."""

    def test_three_item_run_becomes_ul_with_three_li(self):
        html = render_description_html(FOCUS_DESCRIPTION)
        self.assertIn('<ul>', html)
        self.assertEqual(html.count('<li>'), 3)
        self.assertIn('Turning one base CV into domain-specific versions', html)
        self.assertIn('Making the CV easier to adjust', html)
        self.assertIn('Improving ATS readability', html)
        # No literal " - " separators survive as prose.
        self.assertNotIn(' - ', html)

    def test_two_item_bring_run_becomes_ul_with_two_li(self):
        html = render_email_markdown(BRING_DESCRIPTION)
        self.assertIn('<ul>', html)
        self.assertEqual(html.count('<li>'), 2)
        self.assertIn('your current CV', html)
        self.assertIn(
            'one or two job descriptions or companies you want to target', html
        )
        self.assertNotIn(' - ', html)

    def test_hyphenated_word_stays_intact_in_item(self):
        html = render_description_html(FOCUS_DESCRIPTION)
        # domain-specific is one word inside its bullet, not split into a bullet.
        self.assertIn('<li>Turning one base CV into domain-specific versions</li>', html)

    def test_hyphenated_words_never_become_list_items(self):
        for word in ('state-of-the-art', 'end-to-end', 'AI-powered'):
            description = f'Our {word} tooling is ready.'
            page_html = render_description_html(description)
            email_html = render_email_markdown(description)
            self.assertNotIn('<ul>', page_html, word)
            self.assertNotIn('<ul>', email_html, word)
            self.assertIn(word, page_html)
            self.assertIn(word, email_html)

    def test_em_dash_prose_is_left_unchanged(self):
        description = 'It was a great session — join us next time'
        page_html = render_description_html(description)
        email_html = render_email_markdown(description)
        self.assertNotIn('<ul>', page_html)
        self.assertNotIn('<ul>', email_html)
        self.assertIn('—', page_html)

    def test_en_dash_prose_is_left_unchanged(self):
        description = 'Doors open 6–7pm, arrive early'
        html = render_description_html(description)
        self.assertNotIn('<ul>', html)
        self.assertIn('–', html)

    def test_lone_dash_separator_is_not_converted(self):
        description = 'Score was 3 - 1 at the break'
        page_html = render_description_html(description)
        email_html = render_email_markdown(description)
        self.assertNotIn('<ul>', page_html)
        self.assertNotIn('<ul>', email_html)
        self.assertIn('3 - 1', page_html)

    def test_colon_not_followed_by_dash_run_is_not_converted(self):
        # A colon with a single following item must not become a one-item list.
        description = 'Bring: - your current CV only'
        html = render_description_html(description)
        self.assertNotIn('<ul>', html)

    def test_page_and_email_produce_same_list_structure(self):
        page_html = render_description_html(BRING_DESCRIPTION)
        email_html = render_email_markdown(BRING_DESCRIPTION)
        self.assertIn('<ul>', page_html)
        self.assertIn('<ul>', email_html)
        self.assertEqual(page_html.count('<li>'), email_html.count('<li>'))
        self.assertEqual(page_html.count('<li>'), 2)

    def test_already_multiline_list_is_idempotent(self):
        already_normalized = 'Bring:\n\n- your current CV\n- a job description'
        self.assertEqual(
            normalize_inline_bullets(already_normalized), already_normalized
        )

    def test_normalizer_is_idempotent_on_its_own_output(self):
        once = normalize_inline_bullets(FOCUS_DESCRIPTION)
        twice = normalize_inline_bullets(once)
        self.assertEqual(once, twice)

    def test_normalizer_does_not_mutate_stored_description(self):
        event = Event.objects.create(
            title='Focus', slug='focus-cv',
            description=FOCUS_DESCRIPTION,
            start_datetime=timezone.now(),
        )
        event.refresh_from_db()
        self.assertEqual(event.description, FOCUS_DESCRIPTION)

    def test_empty_and_dashless_text_pass_through_unchanged(self):
        self.assertEqual(normalize_inline_bullets(''), '')
        self.assertEqual(normalize_inline_bullets('Just plain prose.'), 'Just plain prose.')


class EventSaveInlineBulletTest(TestCase):
    """Event.save() / EventSeries.save() store the normalized description_html."""

    def test_event_save_stores_ul_for_inline_dash_description(self):
        event = Event.objects.create(
            title='Focus', slug='focus-event',
            description=FOCUS_DESCRIPTION,
            start_datetime=timezone.now(),
        )
        self.assertIn('<ul>', event.description_html)
        self.assertEqual(event.description_html.count('<li>'), 3)
        self.assertNotIn(' - ', event.description_html)

    def test_series_save_stores_ul_for_inline_dash_description(self):
        series = EventSeries.objects.create(
            name='Weekly Focus', slug='weekly-focus',
            start_time=timezone.now().time(), timezone='Europe/Berlin',
            description=BRING_DESCRIPTION,
        )
        self.assertIn('<ul>', series.description_html)
        self.assertEqual(series.description_html.count('<li>'), 2)
        self.assertNotIn(' - ', series.description_html)


class InlineBulletBackfillTest(TestCase):
    """A pre-fix leaked description_html is corrected by re-rendering (backfill)."""

    def test_resave_repairs_leaked_description_html(self):
        event = Event.objects.create(
            title='Legacy', slug='legacy-focus',
            description=FOCUS_DESCRIPTION,
            start_datetime=timezone.now(),
        )
        # Simulate a legacy row whose stored HTML still has the leaked dashes.
        leaked = '<p>We will focus on: - Turning one base CV - Making it easier</p>'
        Event.objects.filter(pk=event.pk).update(description_html=leaked)
        event.refresh_from_db()
        self.assertNotIn('<ul>', event.description_html)

        # The backfill path re-renders from source through the fixed pipeline.
        event.description_html = render_description_html(event.description)
        event.save(update_fields=['description_html'])
        event.refresh_from_db()
        self.assertIn('<ul>', event.description_html)
        self.assertEqual(event.description_html.count('<li>'), 3)
        self.assertNotIn(' - ', event.description_html)
