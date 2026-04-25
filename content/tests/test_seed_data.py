"""
Tests for the seed_data management command.

This command seeds dev-only fixtures (users, events, cohorts, polls, notifications,
subscribers). Content (articles, courses, recordings, projects, links, downloads)
comes from GitHub sync, and tiers come from migration 0003_seed_tiers.
"""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from email_app.models import NewsletterSubscriber
from events.models import Event
from notifications.models import Notification
from voting.models import Poll, PollOption, PollVote

User = get_user_model()


def run_seed(**kwargs):
    """Run the seed_data command and return stdout output."""
    out = StringIO()
    call_command('seed_data', stdout=out, **kwargs)
    return out.getvalue()


class SeedDataCommandTest(TestCase):
    """Tests for the seed_data management command."""

    def test_command_runs_without_errors(self):
        """seed_data runs to completion and prints success message."""
        output = run_seed()
        self.assertIn('Seed data created successfully', output)

    def test_creates_admin_superuser(self):
        """Command creates admin superuser with correct email."""
        run_seed()
        admin = User.objects.get(email='admin@aishippinglabs.com')
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.check_password('admin123'))

    def test_creates_tier_users(self):
        """Command creates users across all four tiers with predictable emails."""
        run_seed()
        tier_emails = {
            'free': 'free@test.com',
            'basic': 'basic@test.com',
            'main': 'main@test.com',
            'premium': 'premium@test.com',
        }
        for tier_slug, email in tier_emails.items():
            user = User.objects.get(email=email)
            self.assertEqual(user.tier.slug, tier_slug)
            self.assertTrue(user.email_verified)

    def test_creates_expected_user_count(self):
        """Command creates 8 users total (admin + 4 tier users + 3 extra)."""
        run_seed()
        seeded_emails = [
            'admin@aishippinglabs.com', 'free@test.com', 'basic@test.com',
            'main@test.com', 'premium@test.com', 'alice@test.com',
            'charlie@test.com', 'diana@test.com',
        ]
        count = User.objects.filter(email__in=seeded_emails).count()
        self.assertEqual(count, 8)

    def test_creates_polls_with_options_and_votes(self):
        """Command creates polls with options and sample votes."""
        run_seed()
        self.assertGreaterEqual(Poll.objects.count(), 1)
        self.assertGreater(PollOption.objects.count(), 0)
        self.assertGreater(PollVote.objects.count(), 0)

    def test_creates_notifications(self):
        """Command creates notifications for test users."""
        run_seed()
        self.assertGreater(Notification.objects.count(), 0)

    def test_creates_newsletter_subscribers(self):
        """Command creates confirmed newsletter subscribers."""
        run_seed()
        expected_emails = {
            'newsletter1@test.com', 'newsletter2@test.com',
            'newsletter3@test.com', 'newsletter4@test.com',
            'newsletter5@test.com',
        }
        subs = NewsletterSubscriber.objects.filter(email__in=expected_emails)
        self.assertEqual(
            set(subs.values_list('email', flat=True)), expected_emails,
        )
        for sub in subs:
            self.assertTrue(sub.is_active)

    def test_prints_summary(self):
        """Command prints a summary of created objects."""
        output = run_seed()
        self.assertIn('Summary:', output)
        self.assertIn('Users:', output)
        self.assertIn('Polls:', output)

    def test_events_have_descriptions(self):
        """All events have non-empty descriptions."""
        run_seed()
        for event in Event.objects.all():
            self.assertTrue(event.description)

    def test_polls_include_topic_and_course_types(self):
        """Seeded polls include at least one topic and one course poll."""
        run_seed()
        open_polls = Poll.objects.filter(status='open')
        topic_polls = open_polls.filter(poll_type='topic')
        course_polls = open_polls.filter(poll_type='course')
        self.assertGreaterEqual(topic_polls.count(), 1)
        self.assertGreaterEqual(course_polls.count(), 1)

    def test_each_poll_has_at_least_3_options(self):
        """Each open poll has at least 3 options."""
        run_seed()
        for poll in Poll.objects.filter(status='open'):
            options_count = PollOption.objects.filter(poll=poll).count()
            self.assertGreaterEqual(
                options_count, 3,
                f"Poll '{poll.title}' has only {options_count} options",
            )


class SeedDataIdempotencyTest(TestCase):
    """Tests verifying the command is idempotent."""

    def test_running_twice_does_not_create_duplicates(self):
        """Running seed_data twice produces the same object counts."""
        run_seed()
        counts_first = {
            'users': User.objects.count(),
            'events': Event.objects.count(),
            'polls': Poll.objects.count(),
            'poll_options': PollOption.objects.count(),
            'poll_votes': PollVote.objects.count(),
            'notifications': Notification.objects.count(),
            'newsletter_subs': NewsletterSubscriber.objects.count(),
        }

        run_seed()
        counts_second = {
            'users': User.objects.count(),
            'events': Event.objects.count(),
            'polls': Poll.objects.count(),
            'poll_options': PollOption.objects.count(),
            'poll_votes': PollVote.objects.count(),
            'notifications': Notification.objects.count(),
            'newsletter_subs': NewsletterSubscriber.objects.count(),
        }

        for key in counts_first:
            self.assertEqual(
                counts_first[key], counts_second[key],
                f'{key} count changed: {counts_first[key]} -> {counts_second[key]}',
            )

    def test_second_run_reports_zero_created(self):
        """Second run reports 0 for all categories."""
        run_seed()
        output = run_seed()
        # All per-category lines (containing "N created") should show 0
        lines = [
            l.strip() for l in output.strip().split('\n')
            if 'created' in l and ':' in l
        ]
        self.assertGreater(len(lines), 0, 'Expected per-category lines with "created"')
        for line in lines:
            self.assertIn('0 created', line, f'Expected "0 created" in: {line}')


class SeedDataFlushTest(TestCase):
    """Tests for the --flush flag."""

    def test_flush_clears_and_reseeds(self):
        """--flush clears dev data then reseeds everything."""
        run_seed()
        first_poll_count = Poll.objects.count()
        self.assertGreater(first_poll_count, 0)

        # Flush and reseed
        output = run_seed(flush=True)
        self.assertIn('Flushing existing dev data', output)
        self.assertIn('Seed data created successfully', output)

        # Counts should be the same as first run
        self.assertEqual(Poll.objects.count(), first_poll_count)

    def test_flush_recreates_users(self):
        """--flush removes and recreates seeded users."""
        run_seed()
        admin = User.objects.get(email='admin@aishippinglabs.com')
        original_pk = admin.pk

        run_seed(flush=True)
        admin_new = User.objects.get(email='admin@aishippinglabs.com')
        # The user should be recreated (new PK)
        self.assertNotEqual(admin_new.pk, original_pk)
        self.assertTrue(admin_new.is_superuser)
