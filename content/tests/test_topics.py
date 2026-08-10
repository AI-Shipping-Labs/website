"""Unit tests for the curated topic taxonomy and tag humanizer.

Covers the Fable blog-filter redesign helpers:
- ``content.topics``: primary_topic / topics_with_matches / filter_by_topic
- ``humanize_tag`` template filter (override dict + default title-casing)
"""

from types import SimpleNamespace

from django.test import SimpleTestCase, tag

from content.templatetags.tag_filters import humanize_tag
from content.topics import (
    filter_by_topic,
    primary_topic,
    topics_with_matches,
)


def _item(*tags):
    return SimpleNamespace(tags=list(tags))


@tag('core')
class PrimaryTopicTest(SimpleTestCase):
    def test_returns_slug_and_label_of_first_matching_topic(self):
        # 'ai' -> AI Engineering (first in order), 'aws' -> Production & Infra.
        # The earliest curated topic wins even when later topics also match.
        self.assertEqual(
            primary_topic(['aws', 'ai']),
            ('ai-engineering', 'AI Engineering'),
        )

    def test_matches_a_later_topic_when_no_earlier_topic_applies(self):
        self.assertEqual(
            primary_topic(['aws']),
            ('production-infra', 'Production & Infra'),
        )

    def test_returns_none_when_no_topic_matches(self):
        self.assertIsNone(primary_topic(['totally-unmapped-tag']))

    def test_returns_none_for_empty_tags(self):
        self.assertIsNone(primary_topic([]))
        self.assertIsNone(primary_topic(None))


@tag('core')
class TopicsWithMatchesTest(SimpleTestCase):
    def test_returns_only_topics_with_at_least_one_match_in_order(self):
        items = [_item('ai'), _item('aws'), _item('unmapped')]
        result = topics_with_matches(items)
        slugs = [t['slug'] for t in result]
        # Ordered by the curated taxonomy; empty topics (career, community,
        # coding-with-ai) are excluded.
        self.assertEqual(slugs, ['ai-engineering', 'production-infra'])

    def test_counts_reflect_number_of_matching_items(self):
        items = [_item('ai'), _item('llm'), _item('aws')]
        result = {t['slug']: t['count'] for t in topics_with_matches(items)}
        self.assertEqual(result['ai-engineering'], 2)
        self.assertEqual(result['production-infra'], 1)

    def test_excludes_topic_with_no_matches(self):
        result = topics_with_matches([_item('ai')])
        slugs = [t['slug'] for t in result]
        self.assertEqual(slugs, ['ai-engineering'])
        self.assertNotIn('career', slugs)

    def test_carries_human_label(self):
        result = topics_with_matches([_item('ai')])
        self.assertEqual(result[0]['label'], 'AI Engineering')


@tag('core')
class FilterByTopicTest(SimpleTestCase):
    def test_returns_only_items_under_the_topic(self):
        ai = _item('ai')
        infra = _item('aws')
        result = filter_by_topic([ai, infra], 'ai-engineering')
        self.assertEqual(result, [ai])

    def test_unknown_slug_returns_all_items(self):
        items = [_item('ai'), _item('aws')]
        self.assertEqual(filter_by_topic(items, 'no-such-topic'), items)


@tag('core')
class HumanizeTagFilterTest(SimpleTestCase):
    def test_override_dict_wins_for_acronyms_and_proper_nouns(self):
        self.assertEqual(humanize_tag('aws'), 'AWS')
        self.assertEqual(humanize_tag('crisp-dm'), 'CRISP-DM')
        self.assertEqual(humanize_tag('chatgpt'), 'ChatGPT')
        self.assertEqual(
            humanize_tag('ai-engineering-buildcamp'),
            'AI Engineering Buildcamp',
        )

    def test_override_is_case_insensitive_on_input(self):
        self.assertEqual(humanize_tag('AWS'), 'AWS')

    def test_default_title_cases_unknown_slugs(self):
        self.assertEqual(humanize_tag('some-random-tag'), 'Some Random Tag')
        self.assertEqual(humanize_tag('design-patterns'), 'Design Patterns')

    def test_blank_and_none_render_empty(self):
        self.assertEqual(humanize_tag(None), '')
        self.assertEqual(humanize_tag(''), '')
        self.assertEqual(humanize_tag('   '), '')
