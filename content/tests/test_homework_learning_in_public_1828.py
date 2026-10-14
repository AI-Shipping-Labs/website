"""Learning in Public homework step contract (issue #1828).

The step itself shipped earlier; these tests pin the presentation and
submission rules the issue lists: collapsed authored examples, a readable
Review URL list, targeted link errors that keep the draft, and a clean
omission when the configured cap is zero.
"""

from html.parser import HTMLParser

from community_base.homework_steps.models import HomeworkDraft
from django.test import TestCase

from content.models.homework import Submission
from content.services.homework_step_reader import LEARNING_IN_PUBLIC_KEY
from content.tests.test_homework_submission_view import HomeworkUnitSetupMixin

AUTHORED_HOMEWORK = (
    'Download the books first.\n\n'
    '## Question 1. Lines\nCount them.\n'
    '## Question 2. Reflect\nWhat was difficult?\n'
    '## Question 3. Primary key\nName it.\n'
    '## Learning in Public\n'
    'Share one result from this week.\n\n'
    'Useful links:\n\n'
    '- [Why learning in public helps](https://example.com/why)\n\n'
    '<details>\n<summary>X example (under 280 weighted characters)</summary>\n\n'
    '<pre><code>Shipped my agent. Project: &lt;LINK&gt;</code></pre>\n\n'
    '</details>\n'
)


class ReviewRowParser(HTMLParser):
    """Collect each Review row's label and the links in its URL list."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self._in_label = False
        self._in_list = False
        self._in_rows = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'ol' and attrs.get('class') == 'homework-review-rows':
            self._in_rows = True
        elif tag == 'li' and attrs.get('class') == 'homework-review-row':
            self.rows.append({'label': '', 'links': [], 'text': ''})
        elif tag == 'strong' and self.rows:
            self._in_label = True
        elif tag == 'ul' and attrs.get('data-testid') == 'homework-review-public-links':
            self._in_list = True
        elif tag == 'a' and self._in_list:
            self.rows[-1]['links'].append(attrs.get('href'))

    def handle_endtag(self, tag):
        if tag == 'strong':
            self._in_label = False
        elif tag == 'ul':
            self._in_list = False
        elif tag == 'ol':
            self._in_rows = False

    def handle_data(self, data):
        if self._in_label:
            self.rows[-1]['label'] += data.strip()
        elif self._in_rows and self.rows and data.strip():
            self.rows[-1]['text'] += ' ' + data.strip()


class DetailsParser(HTMLParser):
    """Record ``<details>`` elements inside the question prompt."""

    def __init__(self):
        super().__init__()
        self.details = []
        self.summaries = []
        self._depth = 0
        self._in_summary = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'div' and attrs.get('data-testid') == 'homework-question-prompt':
            self._depth = 1
        elif self._depth and tag == 'div':
            self._depth += 1
        elif self._depth and tag == 'details':
            self.details.append(attrs)
        elif self._depth and tag == 'summary':
            self._in_summary = True
            self.summaries.append('')

    def handle_endtag(self, tag):
        if self._depth and tag == 'div':
            self._depth -= 1
        elif tag == 'summary':
            self._in_summary = False

    def handle_data(self, data):
        if self._in_summary:
            self.summaries[-1] += data


class LearningInPublicStepTest(HomeworkUnitSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.unit.homework = AUTHORED_HOMEWORK
        self.unit.save(update_fields=['homework'])
        self.homework.stepper_enabled = True
        self.homework.learning_in_public_cap = 3
        self.homework.homework_url_field = True
        self.homework.save(update_fields=[
            'stepper_enabled', 'learning_in_public_cap', 'homework_url_field',
        ])
        self.client.force_login(self.student)
        self.client.get(self.unit_url, follow=True)

    def draft(self):
        return HomeworkDraft.objects.get(user=self.student)

    def save_links(self, links, *, key=LEARNING_IN_PUBLIC_KEY):
        draft = self.draft()
        return self.client.post(
            f'/api/homework-reader/drafts/{self.homework.pk}/questions/{key}',
            {
                'draft_token': str(draft.token), 'revision': str(draft.revision),
                'answer': links,
            },
        )

    def submit(self):
        draft = self.draft()
        return self.client.post(self.unit_url, {
            'assignment_key': f'aisl:homework:{self.homework.pk}',
            'draft_token': str(draft.token), 'homework_step': 'review',
            'revision': str(draft.revision), 'intent': 'submit',
            'final_homework_link': 'https://github.com/student/project',
        })

    def test_step_renders_authored_examples_collapsed(self):
        response = self.client.get(f'{self.unit_url}/{LEARNING_IN_PUBLIC_KEY}')

        parser = DetailsParser()
        parser.feed(response.content.decode())
        self.assertEqual(len(parser.details), 1)
        self.assertNotIn('open', parser.details[0])
        self.assertEqual(
            [summary.strip() for summary in parser.summaries],
            ['X example (under 280 weighted characters)'],
        )
        self.assertContains(response, 'href="https://example.com/why"')
        self.assertContains(response, 'Optional. Add up to 3 links to posts about your progress.')

    def test_review_lists_saved_links_under_learning_in_public(self):
        self.save_links('https://example.com/progress\nhttps://x.com/me/status/1')

        response = self.client.get(f'{self.unit_url}/review')

        parser = ReviewRowParser()
        parser.feed(response.content.decode())
        public_rows = [row for row in parser.rows if row['links']]
        self.assertEqual(len(public_rows), 1)
        self.assertEqual(public_rows[0]['label'], 'Learning in Public')
        self.assertEqual(
            public_rows[0]['links'],
            ['https://example.com/progress', 'https://x.com/me/status/1'],
        )
        labels = [row['label'] for row in parser.rows]
        self.assertNotIn('Question 4', labels)
        self.assertNotIn(LEARNING_IN_PUBLIC_KEY, labels)

    def test_review_keeps_an_invalid_draft_link_as_plain_text(self):
        self.save_links('javascript:alert(1)')

        response = self.client.get(f'{self.unit_url}/review')

        self.assertContains(response, '<li>javascript:alert(1)</li>', html=True)
        self.assertNotContains(response, 'href="javascript:alert(1)"')

    def public_review_row(self):
        response = self.client.get(f'{self.unit_url}/review')
        parser = ReviewRowParser()
        parser.feed(response.content.decode())
        rows = [row for row in parser.rows if row['label'] == 'Learning in Public']
        self.assertEqual(len(rows), 1)
        return response, rows[0]

    def test_review_escapes_html_in_an_invalid_draft_line(self):
        self.save_links('<b>x</b>')

        response, row = self.public_review_row()

        self.assertContains(response, '<li>&lt;b&gt;x&lt;/b&gt;</li>')
        self.assertNotContains(response, '<b>x</b>')
        self.assertEqual(row['text'].strip(), '<b>x</b> Edit Learning in Public')

    def test_review_without_links_reads_no_links_added(self):
        _, row = self.public_review_row()

        self.assertEqual(row['links'], [])
        self.assertTrue(row['text'].strip().startswith('No links added'))

    def test_review_with_only_blank_draft_lines_reads_no_links_added(self):
        self.save_links('   \n\n  \n')

        response, row = self.public_review_row()

        self.assertTrue(row['text'].strip().startswith('No links added'))
        self.assertNotContains(response, 'data-testid="homework-review-public-links"')

    def test_invalid_link_submit_names_the_link_and_keeps_the_draft(self):
        self.save_links('https://example.com/ok\nnot a url')

        response = self.submit()

        self.assertContains(
            response,
            'Learning in Public: public link 2 is not a valid http or https link.',
            status_code=400,
        )
        self.assertEqual(
            self.draft().answers[LEARNING_IN_PUBLIC_KEY],
            'https://example.com/ok\nnot a url',
        )
        self.assertFalse(Submission.objects.filter(homework=self.homework).exists())

    def test_draft_saves_never_create_a_submission_and_submit_stores_links_once(self):
        self.save_links('https://example.com/one\nhttps://example.com/two')
        self.save_links('https://example.com/one\nhttps://example.com/edited')
        self.assertFalse(Submission.objects.filter(homework=self.homework).exists())

        response = self.submit()

        self.assertEqual(response.status_code, 302)
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertEqual(
            submission.learning_in_public_links,
            ['https://example.com/one', 'https://example.com/edited'],
        )


class ZeroCapLearningInPublicTest(HomeworkUnitSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.unit.homework = AUTHORED_HOMEWORK
        self.unit.save(update_fields=['homework'])
        self.homework.stepper_enabled = True
        self.homework.learning_in_public_cap = 0
        self.homework.save(update_fields=['stepper_enabled', 'learning_in_public_cap'])
        self.client.force_login(self.student)
        self.client.get(self.unit_url, follow=True)

    def test_zero_cap_omits_navigation_review_row_and_draft_key(self):
        review = self.client.get(f'{self.unit_url}/review')

        nav_titles = [title for title, *_ in review.context['stepper']['nav_steps']]
        self.assertNotIn('Learning in Public', nav_titles)
        self.assertNotContains(review, f'href="{self.unit_url}/{LEARNING_IN_PUBLIC_KEY}"')
        self.assertNotContains(review, 'data-testid="homework-review-public-links"')

        draft = HomeworkDraft.objects.get(user=self.student)
        forged = self.client.post(
            f'/api/homework-reader/drafts/{self.homework.pk}/questions/{LEARNING_IN_PUBLIC_KEY}',
            {
                'draft_token': str(draft.token), 'revision': str(draft.revision),
                'answer': 'https://example.com/forged',
            },
        )
        self.assertGreaterEqual(forged.status_code, 400)
        draft.refresh_from_db()
        self.assertNotIn(LEARNING_IN_PUBLIC_KEY, draft.answers)

        submitted = self.client.post(self.unit_url, {
            'assignment_key': f'aisl:homework:{self.homework.pk}',
            'draft_token': str(draft.token), 'homework_step': 'review',
            'revision': str(draft.revision), 'intent': 'submit',
            'final_homework_link': 'https://github.com/student/project',
        })
        self.assertEqual(submitted.status_code, 302)
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertEqual(submission.learning_in_public_links, [])
