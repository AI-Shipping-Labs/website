"""
Tests for the seed_data management command.
"""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from content.models import (
    Article, Course, Module, Unit, Cohort, CohortEnrollment,
    Recording, Project, CuratedLink, Download,
)
from email_app.models import NewsletterSubscriber
from events.models import Event, EventRegistration
from notifications.models import Notification
from payments.models import Tier
from voting.models import Poll, PollOption, PollVote


User = get_user_model()


def run_seed(**kwargs):
    """Run the seed_data command and return stdout output."""
    out = StringIO()
    call_command('seed_data', stdout=out, **kwargs)
    return out.getvalue()


class SeedDataCommandTest(TestCase):
    """Tests for the seed_data management command."""

    def setUp(self):
        """Ensure tiers exist before seeding (matches real database setup)."""
        # Tiers may already exist from other fixtures; seed creates them if needed.
        pass

    def test_command_runs_without_errors(self):
        """seed_data runs to completion and prints success message."""
        output = run_seed()
        self.assertIn('Seed data created successfully', output)

    def test_creates_tiers(self):
        """Command creates the four membership tiers."""
        run_seed()
        self.assertEqual(Tier.objects.count(), 4)
        for slug in ['free', 'basic', 'main', 'premium']:
            self.assertTrue(Tier.objects.filter(slug=slug).exists())

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
        # Filter to only seeded users
        seeded_emails = [
            'admin@aishippinglabs.com', 'free@test.com', 'basic@test.com',
            'main@test.com', 'premium@test.com', 'alice@test.com',
            'charlie@test.com', 'diana@test.com',
        ]
        count = User.objects.filter(email__in=seeded_emails).count()
        self.assertEqual(count, 8)

    def test_creates_articles(self):
        """Command creates articles with realistic content (not lorem ipsum)."""
        run_seed()
        articles = Article.objects.all()
        self.assertGreaterEqual(articles.count(), 5)
        for article in articles:
            self.assertTrue(article.title)
            self.assertTrue(article.slug)
            self.assertTrue(article.content_markdown)
            self.assertTrue(article.content_html)  # Auto-rendered on save
            self.assertTrue(article.tags)
            self.assertNotIn('lorem', article.content_markdown.lower())

    def test_creates_courses_with_modules_and_units(self):
        """Command creates courses with modules and units."""
        run_seed()
        self.assertGreaterEqual(Course.objects.count(), 2)
        self.assertGreater(Module.objects.count(), 0)
        self.assertGreater(Unit.objects.count(), 0)

        # Check a specific course
        agents_course = Course.objects.get(slug='llm-agents-fundamentals')
        self.assertEqual(agents_course.modules.count(), 2)
        self.assertGreater(agents_course.total_units(), 0)

    def test_course_units_have_content(self):
        """Units have video URLs, homework, and timestamps where defined."""
        run_seed()
        # At least one unit should have a video URL
        self.assertTrue(Unit.objects.filter(video_url__gt='').exists())
        # At least one unit should have homework
        self.assertTrue(Unit.objects.filter(homework__gt='').exists())

    def test_creates_cohorts_with_enrollments(self):
        """Command creates cohorts linked to courses with user enrollments."""
        run_seed()
        self.assertGreaterEqual(Cohort.objects.count(), 1)
        self.assertGreater(CohortEnrollment.objects.count(), 0)

        # Check cohort is linked to a course
        for cohort in Cohort.objects.all():
            self.assertIsNotNone(cohort.course)

    def test_creates_events(self):
        """Command creates events with a mix of statuses."""
        run_seed()
        events = Event.objects.all()
        self.assertGreaterEqual(events.count(), 3)

        statuses = set(events.values_list('status', flat=True))
        self.assertIn('upcoming', statuses)
        self.assertIn('completed', statuses)

    def test_creates_event_registrations(self):
        """Command creates registrations for upcoming/live events."""
        run_seed()
        self.assertGreater(EventRegistration.objects.count(), 0)

    def test_creates_recordings(self):
        """Command creates recordings linked to past events."""
        run_seed()
        recordings = Recording.objects.all()
        self.assertGreaterEqual(recordings.count(), 4)
        # At least one recording should be linked to an event
        linked = recordings.filter(event__isnull=False)
        self.assertGreater(linked.count(), 0)

    def test_creates_projects(self):
        """Command creates projects with difficulty levels and tags."""
        run_seed()
        projects = Project.objects.all()
        self.assertGreaterEqual(projects.count(), 3)

        difficulties = set(projects.values_list('difficulty', flat=True))
        self.assertIn('beginner', difficulties)
        self.assertIn('intermediate', difficulties)
        self.assertIn('advanced', difficulties)

    def test_projects_have_community_submissions(self):
        """Some projects have a submitter (community-submitted)."""
        run_seed()
        submitted = Project.objects.filter(submitter__isnull=False)
        self.assertGreater(submitted.count(), 0)

    def test_creates_curated_links(self):
        """Command creates curated links across categories."""
        run_seed()
        links = CuratedLink.objects.filter(item_id__startswith='seed-')
        self.assertGreaterEqual(links.count(), 8)

        categories = set(links.values_list('category', flat=True))
        self.assertTrue(len(categories) >= 2)

    def test_creates_downloads(self):
        """Command creates downloadable resources with varying types and access levels."""
        run_seed()
        downloads = Download.objects.all()
        self.assertGreaterEqual(downloads.count(), 3)

        # Check mix of free and gated
        free_downloads = downloads.filter(required_level=0)
        gated_downloads = downloads.filter(required_level__gt=0)
        self.assertGreater(free_downloads.count(), 0)
        self.assertGreater(gated_downloads.count(), 0)

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
        subs = NewsletterSubscriber.objects.filter(
            email__in=[
                'newsletter1@test.com', 'newsletter2@test.com',
                'newsletter3@test.com', 'newsletter4@test.com',
                'newsletter5@test.com',
            ],
        )
        self.assertEqual(subs.count(), 5)
        for sub in subs:
            self.assertTrue(sub.is_active)

    def test_prints_summary(self):
        """Command prints a summary of created objects."""
        output = run_seed()
        self.assertIn('Summary:', output)
        self.assertIn('Users:', output)
        self.assertIn('Articles:', output)
        self.assertIn('Courses:', output)
        self.assertIn('Events:', output)


class SeedDataIdempotencyTest(TestCase):
    """Tests verifying the command is idempotent."""

    def test_running_twice_does_not_create_duplicates(self):
        """Running seed_data twice produces the same object counts."""
        run_seed()
        counts_first = {
            'users': User.objects.count(),
            'articles': Article.objects.count(),
            'courses': Course.objects.count(),
            'modules': Module.objects.count(),
            'units': Unit.objects.count(),
            'events': Event.objects.count(),
            'recordings': Recording.objects.count(),
            'projects': Project.objects.count(),
            'downloads': Download.objects.count(),
            'polls': Poll.objects.count(),
            'poll_options': PollOption.objects.count(),
            'poll_votes': PollVote.objects.count(),
            'notifications': Notification.objects.count(),
            'newsletter_subs': NewsletterSubscriber.objects.count(),
        }

        run_seed()
        counts_second = {
            'users': User.objects.count(),
            'articles': Article.objects.count(),
            'courses': Course.objects.count(),
            'modules': Module.objects.count(),
            'units': Unit.objects.count(),
            'events': Event.objects.count(),
            'recordings': Recording.objects.count(),
            'projects': Project.objects.count(),
            'downloads': Download.objects.count(),
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
        """--flush clears data then reseeds everything."""
        run_seed()
        first_article_count = Article.objects.count()
        self.assertGreater(first_article_count, 0)

        # Flush and reseed
        output = run_seed(flush=True)
        self.assertIn('Flushing existing data', output)
        self.assertIn('Seed data created successfully', output)

        # Counts should be the same as first run
        self.assertEqual(Article.objects.count(), first_article_count)

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


class SeedDataContentQualityTest(TestCase):
    """Tests verifying content quality (not lorem ipsum)."""

    def test_articles_have_realistic_titles(self):
        """Article titles are meaningful AI/ML topics."""
        run_seed()
        articles = Article.objects.all()
        for article in articles:
            self.assertGreater(len(article.title), 10)
            self.assertNotIn('lorem', article.title.lower())
            self.assertNotIn('test article', article.title.lower())

    def test_articles_have_tags(self):
        """All articles have at least one tag."""
        run_seed()
        for article in Article.objects.all():
            self.assertGreater(len(article.tags), 0)

    def test_courses_have_descriptions(self):
        """All courses have non-empty descriptions."""
        run_seed()
        for course in Course.objects.all():
            self.assertTrue(course.description)
            self.assertGreater(len(course.description), 20)

    def test_events_have_descriptions(self):
        """All events have non-empty descriptions."""
        run_seed()
        for event in Event.objects.all():
            self.assertTrue(event.description)

    def test_projects_have_content(self):
        """All projects have markdown content."""
        run_seed()
        for project in Project.objects.all():
            self.assertTrue(project.content_markdown)
            self.assertGreater(len(project.content_markdown), 20)
