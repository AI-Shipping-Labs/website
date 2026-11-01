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
GLUED_BULLET_DESCRIPTION = (
    'What we will cover:\n'
    '- What MCP is and when it is worth using\n'
    '- Build an MCP server that exposes tools to an agent\n'
    '- Connect it to our own agent and to coding assistants'
)
GLUED_ORDERED_DESCRIPTION = (
    'Setup steps:\n'
    '1. Install the SDK\n'
    '2. Run the server'
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


class MissingBlankLineListTest(TestCase):
    """A list glued directly under a paragraph line renders as a real list.

    The MCP server workshop report: ``What we will cover:`` followed
    immediately by ``-`` lines rendered as one paragraph with literal dashes,
    because Python-Markdown only forms a list after a blank line.
    ``normalize_inline_bullets`` now inserts that blank line at render time.
    """

    def test_glued_bullet_list_renders_ul(self):
        html = render_description_html(GLUED_BULLET_DESCRIPTION)
        self.assertIn('<ul>', html)
        self.assertEqual(html.count('<li>'), 3)
        self.assertIn('<li>What MCP is and when it is worth using</li>', html)
        self.assertNotIn('- What MCP is', html)

    def test_glued_ordered_list_renders_ol(self):
        html = render_description_html(GLUED_ORDERED_DESCRIPTION)
        self.assertIn('<ol>', html)
        self.assertEqual(html.count('<li>'), 2)
        self.assertIn('<li>Install the SDK</li>', html)
        self.assertNotIn('1. Install the SDK', html)

    def test_glued_list_after_plain_paragraph_without_colon(self):
        html = render_description_html('Join us\n- first\n- second')
        self.assertIn('<ul>', html)
        self.assertEqual(html.count('<li>'), 2)

    def test_single_glued_bullet_item_renders_list(self):
        html = render_description_html('Note:\n- one thing')
        self.assertIn('<ul>', html)
        self.assertEqual(html.count('<li>'), 1)

    def test_star_bullet_after_paragraph_renders_list(self):
        html = render_description_html('Bring:\n* passport\n* laptop')
        self.assertIn('<ul>', html)
        self.assertEqual(html.count('<li>'), 2)

    def test_nested_items_gain_no_extra_blank_line(self):
        source = 'Plan:\n- day one\n  - morning\n- day two'
        once = normalize_inline_bullets(source)
        self.assertEqual(normalize_inline_bullets(once), once)
        html = render_description_html(source)
        self.assertIn('<ul>', html)
        self.assertIn('morning', html)

    def test_fenced_code_list_lookalike_is_untouched(self):
        source = 'Example:\n```\n- not a list\n```'
        html = render_description_html(source)
        self.assertNotIn('<ul>', html)
        self.assertIn('- not a list', html)

    def test_separator_scores_and_versions_are_not_lists(self):
        for source in ('Score was\n3 - 1 at the break', 'Release\n1.5 is out'):
            html = render_description_html(source)
            self.assertNotIn('<ul>', html, source)
            self.assertNotIn('<ol>', html, source)

    def test_email_renderer_matches_page_for_glued_lists(self):
        page_html = render_description_html(GLUED_BULLET_DESCRIPTION)
        email_html = render_email_markdown(GLUED_BULLET_DESCRIPTION)
        self.assertIn('<ul>', email_html)
        self.assertEqual(page_html.count('<li>'), email_html.count('<li>'))
        self.assertEqual(email_html.count('<li>'), 3)

    def test_ordered_glued_list_matches_in_email(self):
        email_html = render_email_markdown(GLUED_ORDERED_DESCRIPTION)
        self.assertIn('<ol>', email_html)
        self.assertEqual(email_html.count('<li>'), 2)

    def test_already_separated_glued_shape_is_idempotent(self):
        source = 'What we will cover:\n\n- What MCP is\n- Build a server'
        self.assertEqual(normalize_inline_bullets(source), source)


class GluedListSaveTest(TestCase):
    """Event.save() stores real lists for paragraph-glued descriptions."""

    def test_event_save_stores_ul_for_glued_bullet_description(self):
        event = Event.objects.create(
            title='MCP', slug='mcp-glued',
            description=GLUED_BULLET_DESCRIPTION,
            start_datetime=timezone.now(),
        )
        self.assertIn('<ul>', event.description_html)
        self.assertEqual(event.description_html.count('<li>'), 3)

    def test_series_save_stores_ol_for_glued_ordered_description(self):
        series = EventSeries.objects.create(
            name='Setup', slug='setup-glued',
            start_time=timezone.now().time(), timezone='Europe/Berlin',
            description=GLUED_ORDERED_DESCRIPTION,
        )
        self.assertIn('<ol>', series.description_html)
        self.assertEqual(series.description_html.count('<li>'), 2)


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
