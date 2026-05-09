"""Tests for the teaser truncation utility — issue #248.

Covers ``content.utils.teaser.truncate_to_words`` and ``first_sentence``.
The truncator must:

* Stop at roughly ``n`` whitespace-separated visible words.
* Keep all opened tags balanced.
* Drop ``<script>``, ``<style>``, ``<iframe>`` content entirely.
* Skip ``<pre>``/``<code>`` blocks (they blow the word budget on a
  single token).
* Prefer to stop at a block-level boundary once the budget is hit.
* No-op on empty input.
"""

from django.test import SimpleTestCase

from content.utils.teaser import first_sentence, truncate_to_words


class TruncateToWordsTest(SimpleTestCase):

    def test_empty_string_returns_empty(self):
        self.assertEqual(truncate_to_words('', 50), '')

    def test_none_returns_empty(self):
        self.assertEqual(truncate_to_words(None, 50), '')

    def test_zero_words_returns_empty(self):
        # Defensive: callers should always pass a positive budget.
        self.assertEqual(truncate_to_words('<p>hello world</p>', 0), '')

    def test_short_input_unchanged(self):
        html = '<p>Hello world</p>'
        self.assertEqual(truncate_to_words(html, 50).strip(), '<p>Hello world</p>')

    def test_truncates_long_paragraph(self):
        words = ' '.join(f'word{i}' for i in range(200))
        html = f'<p>{words}</p>'
        result = truncate_to_words(html, 20)
        self.assertIn('word0', result)
        self.assertIn('word19', result)
        self.assertNotIn('word150', result)
        self.assertNotIn('word199', result)

    def test_balances_tags(self):
        words = ' '.join(f'w{i}' for i in range(100))
        html = f'<div><p>{words}</p></div>'
        result = truncate_to_words(html, 5)
        # Every opened tag must be closed.
        self.assertEqual(result.count('<p>'), result.count('</p>'))
        self.assertEqual(result.count('<div>'), result.count('</div>'))

    def test_drops_script_block(self):
        html = '<p>Hello</p><script>evil()</script><p>world</p>'
        result = truncate_to_words(html, 50)
        self.assertNotIn('evil', result)
        self.assertNotIn('<script>', result)
        self.assertIn('Hello', result)
        self.assertIn('world', result)

    def test_drops_style_block(self):
        html = '<p>Hello</p><style>body{color:red}</style>'
        result = truncate_to_words(html, 50)
        self.assertNotIn('color', result)
        self.assertNotIn('<style>', result)

    def test_drops_iframe_block(self):
        """Critical: teaser must not auto-load embedded video."""
        html = (
            '<p>Intro</p>'
            '<iframe src="https://youtube.com/embed/x">fallback</iframe>'
            '<p>Outro</p>'
        )
        result = truncate_to_words(html, 50)
        self.assertNotIn('<iframe', result)
        self.assertNotIn('youtube.com/embed', result)

    def test_skips_pre_block(self):
        html = (
            '<p>Hello world</p>'
            '<pre><code>def foo(): pass</code></pre>'
            '<p>Goodbye</p>'
        )
        result = truncate_to_words(html, 50)
        self.assertNotIn('def foo', result)
        self.assertNotIn('<pre>', result)
        self.assertIn('Hello', result)
        self.assertIn('Goodbye', result)

    def test_stops_at_block_boundary(self):
        """Once over budget the walker should flush the current block and
        stop, not keep emitting subsequent paragraphs."""
        html = (
            '<p>one two three four five</p>'
            '<p>six seven eight nine ten</p>'
            '<p>FUTURE_PARAGRAPH_MARKER</p>'
        )
        result = truncate_to_words(html, 5)
        self.assertIn('one', result)
        self.assertIn('five', result)
        self.assertNotIn('FUTURE_PARAGRAPH_MARKER', result)

    def test_preserves_inline_formatting(self):
        html = '<p>Hello <strong>bold</strong> world here</p>'
        result = truncate_to_words(html, 10)
        self.assertIn('<strong>bold</strong>', result)

    def test_preserves_attributes(self):
        html = '<a href="https://example.com" class="x">Click here now</a>'
        result = truncate_to_words(html, 10)
        self.assertIn('href="https://example.com"', result)
        self.assertIn('class="x"', result)

    def test_preserves_void_tag(self):
        html = '<p>Image: <img src="x.jpg" alt="foo"> end here</p>'
        result = truncate_to_words(html, 10)
        self.assertIn('<img', result)

    def test_handles_lists(self):
        html = (
            '<ul>'
            '<li>alpha beta</li>'
            '<li>gamma delta</li>'
            '<li>FUTURE_LI_MARKER</li>'
            '</ul>'
        )
        result = truncate_to_words(html, 4)
        self.assertIn('alpha', result)
        self.assertIn('delta', result)
        self.assertNotIn('FUTURE_LI_MARKER', result)
        # ul/li should close cleanly.
        self.assertEqual(result.count('<ul>'), result.count('</ul>'))

    def test_handles_headings(self):
        html = '<h2>Section Title Here</h2><p>Body text follows after.</p>'
        result = truncate_to_words(html, 50)
        self.assertIn('<h2>Section Title Here</h2>', result)

    def test_count_uses_visible_words_only(self):
        """HTML tags should not consume word budget."""
        html = '<p><em><strong><span class="x">one two three</span></strong></em></p>'
        result = truncate_to_words(html, 3)
        self.assertIn('one two three', result)


class FirstSentenceTest(SimpleTestCase):

    def test_empty_returns_empty(self):
        self.assertEqual(first_sentence(''), '')
        self.assertEqual(first_sentence(None), '')

    def test_single_sentence(self):
        self.assertEqual(first_sentence('Just one.'), 'Just one.')

    def test_takes_first_period_sentence(self):
        result = first_sentence('Build a thing. Then write a report.')
        self.assertEqual(result, 'Build a thing.')

    def test_takes_first_question(self):
        result = first_sentence('What is X? Discuss.')
        self.assertEqual(result, 'What is X?')

    def test_takes_first_exclamation(self):
        result = first_sentence('Wow! That is amazing.')
        self.assertEqual(result, 'Wow!')

    def test_no_terminator_returns_full_text(self):
        result = first_sentence('No ending punctuation here')
        self.assertEqual(result, 'No ending punctuation here')

    def test_strips_surrounding_whitespace(self):
        result = first_sentence('   Build it. More.   ')
        self.assertEqual(result, 'Build it.')

    def test_decimal_does_not_split(self):
        """A period followed by a digit (e.g. "v3.14") is not a sentence end."""
        result = first_sentence('Use Python 3.14 for this. Done.')
        self.assertEqual(result, 'Use Python 3.14 for this.')
