"""Unit tests for the display-time task-name humanizer (issue #920).

``humanize_task_name(name, func)`` is the display-layer safety net for the
Studio worker dashboard: descriptive stored names pass through unchanged, but
empty names and Django-Q random codenames fall back to the dotted func path so
an operator can still identify the task.
"""

from django.test import SimpleTestCase
from django_q.humanhash import DEFAULT_WORDLIST

from jobs.tasks.names import (
    AUTO_NAMED_HINT,
    humanize_task_name,
    is_django_q_codename,
)

# A real four-word codename built from Django-Q's own word list so the test
# can never drift from the library's actual output shape.
CODENAME = "-".join(DEFAULT_WORDLIST[:4])


class IsDjangoQCodenameTest(SimpleTestCase):
    def test_four_word_wordlist_name_is_codename(self):
        # texas-texas-oscar-earth style: four words all from the word list.
        self.assertTrue(is_django_q_codename("texas-texas-oscar-earth"))
        self.assertTrue(is_django_q_codename(CODENAME))

    def test_hyphenated_schedule_name_is_not_codename(self):
        # The misclassification guard: legitimate two/three-word hyphenated
        # schedule names must NOT be treated as codenames.
        self.assertFalse(is_django_q_codename("event-reminders"))
        self.assertFalse(is_django_q_codename("slack-membership-refresh"))
        self.assertFalse(is_django_q_codename("complete-finished-events"))

    def test_four_hyphenated_words_not_in_wordlist_is_not_codename(self):
        # Four hyphen-joined words that are NOT all in the codename word list
        # must not be misclassified.
        self.assertFalse(is_django_q_codename("send-weekly-digest-now"))

    def test_descriptive_name_with_colon_is_not_codename(self):
        self.assertFalse(
            is_django_q_codename("Send campaign: Weekly digest from campaign admin")
        )

    def test_empty_name_is_not_codename(self):
        self.assertFalse(is_django_q_codename(""))
        self.assertFalse(is_django_q_codename(None))


class HumanizeTaskNameTest(SimpleTestCase):
    def test_descriptive_name_passes_through_unchanged(self):
        name = "Send campaign: Weekly digest from campaign admin"
        self.assertEqual(
            humanize_task_name(name, "email_app.tasks.send_campaign.run"),
            name,
        )

    def test_codename_falls_back_to_func_path(self):
        result = humanize_task_name(
            "texas-texas-oscar-earth",
            "community.tasks.email_matcher.match_community_emails",
        )
        self.assertIn(
            "community.tasks.email_matcher.match_community_emails", result
        )
        self.assertNotIn("texas-texas-oscar-earth", result)
        self.assertIn(AUTO_NAMED_HINT, result)

    def test_empty_name_falls_back_to_func_path(self):
        result = humanize_task_name(
            "", "community.tasks.email_matcher.match_community_emails"
        )
        self.assertIn(
            "community.tasks.email_matcher.match_community_emails", result
        )
        self.assertIn(AUTO_NAMED_HINT, result)

    def test_hyphenated_schedule_name_passes_through_unchanged(self):
        # event-reminders is a descriptive schedule name, not a codename.
        self.assertEqual(
            humanize_task_name("event-reminders", "events.tasks.send_reminders.run"),
            "event-reminders",
        )

    def test_empty_name_and_no_func_returns_empty(self):
        self.assertEqual(humanize_task_name("", None), "")
