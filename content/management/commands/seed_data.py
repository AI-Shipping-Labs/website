"""
Seed development data (fake users, events, cohorts, polls, notifications, subscribers).

Content (articles, courses, recordings, projects, curated links, downloads) now comes
from GitHub sync (AI-Shipping-Labs/content). Tiers are seeded via migration 0003_seed_tiers.

This command creates only dev-only fixtures for testing access control, event pages,
course flows, voting, notification UI, and email.

Also seeds OAuth social apps (Google, GitHub, Slack) if the corresponding
client ID / secret environment variables are set in .env.

Idempotent: running twice does not create duplicates.
"""

import os
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from django.utils import timezone

from allauth.socialaccount.models import SocialApp
from content.models import Cohort, CohortEnrollment, Course
from email_app.models import NewsletterSubscriber
from events.models import Event, EventRegistration
from notifications.models import Notification
from payments.models import Tier
from voting.models import Poll, PollOption, PollVote


User = get_user_model()

now = timezone.now()
today = now.date()


# ---------------------------------------------------------------------------
# User definitions
# ---------------------------------------------------------------------------
USERS = [
    {
        'email': 'admin@aishippinglabs.com',
        'password': 'admin123',
        'first_name': 'Admin',
        'last_name': 'User',
        'is_superuser': True,
        'is_staff': True,
        'tier_slug': 'premium',
    },
    {
        'email': 'free@test.com',
        'password': 'testpass123',
        'first_name': 'Freya',
        'last_name': 'Freeman',
        'tier_slug': 'free',
    },
    {
        'email': 'basic@test.com',
        'password': 'testpass123',
        'first_name': 'Bob',
        'last_name': 'Baker',
        'tier_slug': 'basic',
    },
    {
        'email': 'main@test.com',
        'password': 'testpass123',
        'first_name': 'Maria',
        'last_name': 'Martinez',
        'tier_slug': 'main',
    },
    {
        'email': 'premium@test.com',
        'password': 'testpass123',
        'first_name': 'Pete',
        'last_name': 'Preston',
        'tier_slug': 'premium',
    },
    {
        'email': 'alice@test.com',
        'password': 'testpass123',
        'first_name': 'Alice',
        'last_name': 'Anderson',
        'tier_slug': 'main',
    },
    {
        'email': 'charlie@test.com',
        'password': 'testpass123',
        'first_name': 'Charlie',
        'last_name': 'Chen',
        'tier_slug': 'basic',
    },
    {
        'email': 'diana@test.com',
        'password': 'testpass123',
        'first_name': 'Diana',
        'last_name': 'Davis',
        'tier_slug': 'free',
    },
]

# ---------------------------------------------------------------------------
# Event definitions
# ---------------------------------------------------------------------------
EVENTS = [
    {
        'slug': 'llm-agents-workshop-march',
        'title': 'LLM Agents Workshop: Building Your First Agent',
        'description': (
            'Hands-on workshop where we build an LLM agent from scratch. '
            'Bring your laptop and an API key.'
        ),
        'event_type': 'live',
        'status': 'upcoming',
        'start_offset_days': 14,
        'duration_hours': 2,
        'location': 'Zoom',
        'tags': ['agents', 'workshop', 'hands-on'],
        'required_level': 0,
        'max_participants': 50,
    },
    {
        'slug': 'rag-deep-dive-live',
        'title': 'RAG Deep Dive: Chunking and Retrieval',
        'description': (
            'Live session exploring advanced chunking strategies, hybrid retrieval, '
            'and practical tips for production RAG.'
        ),
        'event_type': 'live',
        'status': 'upcoming',
        'start_offset_days': 28,
        'duration_hours': 1.5,
        'location': 'Zoom',
        'tags': ['rag', 'retrieval', 'deep-dive'],
        'required_level': 20,
    },
    {
        'slug': 'community-demo-day-feb',
        'title': 'Community Demo Day: February Projects',
        'description': (
            'Community members showcase their AI projects. '
            'Five-minute demos followed by Q&A.'
        ),
        'event_type': 'live',
        'status': 'live',
        'start_offset_days': 0,
        'duration_hours': 1,
        'location': 'Zoom',
        'tags': ['community', 'demo', 'showcase'],
        'required_level': 10,
    },
    {
        'slug': 'fine-tuning-masterclass-jan',
        'title': 'Fine-Tuning Masterclass: LoRA and QLoRA',
        'description': (
            'Hands-on masterclass covering LoRA, QLoRA, and data preparation '
            'for fine-tuning open-source models.'
        ),
        'event_type': 'live',
        'status': 'completed',
        'start_offset_days': -14,
        'duration_hours': 2,
        'location': 'Zoom',
        'tags': ['fine-tuning', 'lora', 'masterclass'],
        'required_level': 20,
    },
    {
        'slug': 'prompt-engineering-async',
        'title': 'Async Challenge: Prompt Engineering Tournament',
        'description': (
            'Week-long async challenge: solve 10 prompt engineering puzzles. '
            'Leaderboard and prizes for top scorers.'
        ),
        'event_type': 'async',
        'status': 'completed',
        'start_offset_days': -30,
        'duration_hours': 168,
        'location': 'GitHub',
        'tags': ['prompt-engineering', 'challenge', 'async'],
        'required_level': 0,
    },
]

# ---------------------------------------------------------------------------
# Poll definitions
# ---------------------------------------------------------------------------
POLLS = [
    {
        'title': 'What topic should our next deep-dive cover?',
        'description': 'Vote for the topic you want to see in our next deep-dive session.',
        'poll_type': 'topic',
        'status': 'open',
        'allow_proposals': True,
        'max_votes_per_user': 2,
        'options': [
            {'title': 'Advanced RAG: GraphRAG and Knowledge Graphs', 'description': 'Explore graph-based retrieval approaches.'},
            {'title': 'LLM Security: Prompt Injection and Defenses', 'description': 'Security patterns for LLM applications.'},
            {'title': 'Building Multi-Modal Agents', 'description': 'Agents that process text, images, and audio.'},
            {'title': 'AI-Assisted Code Review at Scale', 'description': 'How to set up AI code review in your CI/CD pipeline.'},
        ],
    },
    {
        'title': 'Which mini-course should we create next?',
        'description': 'Premium members: vote on our next mini-course.',
        'poll_type': 'course',
        'status': 'open',
        'allow_proposals': False,
        'max_votes_per_user': 1,
        'options': [
            {'title': 'Fine-Tuning with Unsloth', 'description': 'Efficient fine-tuning with the Unsloth library.'},
            {'title': 'Building MCP Servers in Python', 'description': 'Hands-on course on the Model Context Protocol.'},
            {'title': 'Evaluation-Driven AI Development', 'description': 'Build better AI apps by writing evals first.'},
        ],
    },
]

# ---------------------------------------------------------------------------
# Newsletter subscriber definitions
# ---------------------------------------------------------------------------
NEWSLETTER_SUBSCRIBERS = [
    'newsletter1@test.com',
    'newsletter2@test.com',
    'newsletter3@test.com',
    'newsletter4@test.com',
    'newsletter5@test.com',
]


class Command(BaseCommand):
    help = 'Seed development data (fake users, events, polls). Content comes from GitHub sync.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--flush',
            action='store_true',
            help='Clear existing dev data before seeding.',
        )

    def handle(self, *args, **options):
        if options['flush']:
            self._flush()

        summary = {}
        summary['users'] = self._seed_users()
        summary['cohorts'] = self._seed_cohorts()
        summary['events'] = self._seed_events()
        summary['polls'] = self._seed_polls()
        summary['notifications'] = self._seed_notifications()
        summary['newsletter_subscribers'] = self._seed_newsletter_subscribers()
        summary['social_apps'] = self._seed_social_apps()

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Seed data created successfully.'))
        self.stdout.write('')
        self.stdout.write('Summary:')
        for key, count in summary.items():
            label = key.replace('_', ' ').title()
            self.stdout.write(f'  {label}: {count}')

    # ------------------------------------------------------------------
    # Flush
    # ------------------------------------------------------------------
    def _flush(self):
        self.stdout.write('Flushing existing dev data...')
        PollVote.objects.all().delete()
        PollOption.objects.all().delete()
        Poll.objects.all().delete()
        Notification.objects.all().delete()
        EventRegistration.objects.all().delete()
        CohortEnrollment.objects.all().delete()
        Cohort.objects.all().delete()
        Event.objects.all().delete()
        NewsletterSubscriber.objects.all().delete()
        User.objects.filter(email__in=[u['email'] for u in USERS]).delete()
        self.stdout.write('  Flushed.')

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------
    def _seed_users(self):
        count = 0
        for user_data in USERS:
            email = user_data['email']
            if User.objects.filter(email=email).exists():
                continue
            tier = Tier.objects.get(slug=user_data['tier_slug'])
            is_super = user_data.get('is_superuser', False)
            if is_super:
                user = User.objects.create_superuser(
                    email=email,
                    password=user_data['password'],
                    first_name=user_data.get('first_name', ''),
                    last_name=user_data.get('last_name', ''),
                )
            else:
                user = User.objects.create_user(
                    email=email,
                    password=user_data['password'],
                    first_name=user_data.get('first_name', ''),
                    last_name=user_data.get('last_name', ''),
                )
            user.tier = tier
            user.email_verified = True
            user.save()
            count += 1
        self.stdout.write(f'  Users: {count} created')
        return count

    # ------------------------------------------------------------------
    # Cohorts
    # ------------------------------------------------------------------
    def _seed_cohorts(self):
        count = 0
        # Cohort for the RAG course (if it exists from GitHub sync)
        rag_course = Course.objects.filter(slug='rag-in-production').first()
        if rag_course:
            cohort, created = Cohort.objects.get_or_create(
                course=rag_course,
                name='March 2026 Cohort',
                defaults={
                    'start_date': today + timedelta(days=10),
                    'end_date': today + timedelta(days=40),
                    'is_active': True,
                    'max_participants': 30,
                },
            )
            if created:
                count += 1
                # Enroll some users
                for email in ['main@test.com', 'premium@test.com', 'alice@test.com']:
                    user = User.objects.filter(email=email).first()
                    if user:
                        CohortEnrollment.objects.get_or_create(
                            cohort=cohort, user=user,
                        )

        # Cohort for MLOps course (if it exists from GitHub sync)
        mlops_course = Course.objects.filter(slug='mlops-with-docker-and-kubernetes').first()
        if mlops_course:
            cohort2, created = Cohort.objects.get_or_create(
                course=mlops_course,
                name='April 2026 Cohort',
                defaults={
                    'start_date': today + timedelta(days=45),
                    'end_date': today + timedelta(days=75),
                    'is_active': True,
                    'max_participants': 25,
                },
            )
            if created:
                count += 1
                for email in ['basic@test.com', 'main@test.com']:
                    user = User.objects.filter(email=email).first()
                    if user:
                        CohortEnrollment.objects.get_or_create(
                            cohort=cohort2, user=user,
                        )

        self.stdout.write(f'  Cohorts: {count} created')
        return count

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def _seed_events(self):
        count = 0
        for event_data in EVENTS:
            start = now + timedelta(days=event_data['start_offset_days'])
            end = start + timedelta(hours=event_data.get('duration_hours', 1))
            _, created = Event.objects.get_or_create(
                slug=event_data['slug'],
                defaults={
                    'title': event_data['title'],
                    'description': event_data['description'],
                    'event_type': event_data['event_type'],
                    'status': event_data['status'],
                    'start_datetime': start,
                    'end_datetime': end,
                    'location': event_data.get('location', ''),
                    'tags': event_data.get('tags', []),
                    'required_level': event_data.get('required_level', 0),
                    'max_participants': event_data.get('max_participants'),
                },
            )
            if created:
                count += 1
                # Register some users for upcoming/live events
                if event_data['status'] in ('upcoming', 'live'):
                    event = Event.objects.get(slug=event_data['slug'])
                    for email in ['main@test.com', 'premium@test.com', 'alice@test.com']:
                        user = User.objects.filter(email=email).first()
                        if user:
                            EventRegistration.objects.get_or_create(
                                event=event, user=user,
                            )
        self.stdout.write(f'  Events: {count} created')
        return count

    # ------------------------------------------------------------------
    # Polls
    # ------------------------------------------------------------------
    def _seed_polls(self):
        count = 0
        for poll_data in POLLS:
            poll, created = Poll.objects.get_or_create(
                title=poll_data['title'],
                defaults={
                    'description': poll_data['description'],
                    'poll_type': poll_data['poll_type'],
                    'status': poll_data['status'],
                    'allow_proposals': poll_data.get('allow_proposals', False),
                    'max_votes_per_user': poll_data.get('max_votes_per_user', 3),
                },
            )
            if created:
                count += 1
                options = []
                for opt_data in poll_data.get('options', []):
                    option, _ = PollOption.objects.get_or_create(
                        poll=poll,
                        title=opt_data['title'],
                        defaults={
                            'description': opt_data.get('description', ''),
                        },
                    )
                    options.append(option)

                # Add some votes from users
                voters = User.objects.filter(
                    email__in=['main@test.com', 'premium@test.com', 'alice@test.com'],
                )
                for voter in voters:
                    # Each voter votes on 1-2 options
                    for option in options[:poll_data.get('max_votes_per_user', 1)]:
                        PollVote.objects.get_or_create(
                            poll=poll, option=option, user=voter,
                        )
        self.stdout.write(f'  Polls: {count} created')
        return count

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------
    def _seed_notifications(self):
        count = 0
        notification_data = [
            {
                'email': 'main@test.com',
                'title': 'New article: Prompt Engineering Patterns',
                'body': 'A new article on prompt engineering patterns has been published.',
                'url': '/blog/prompt-engineering-patterns',
                'notification_type': 'new_content',
            },
            {
                'email': 'premium@test.com',
                'title': 'LLM Agents Workshop in 24 hours',
                'body': 'Reminder: the LLM Agents Workshop starts tomorrow.',
                'url': '/events/llm-agents-workshop-march',
                'notification_type': 'event_reminder',
            },
            {
                'email': 'main@test.com',
                'title': 'New course available: RAG in Production',
                'body': 'A new course on production RAG pipelines is now available.',
                'url': '/courses/rag-in-production',
                'notification_type': 'new_content',
            },
            {
                'email': 'alice@test.com',
                'title': 'Community Demo Day is live!',
                'body': 'The February Community Demo Day is starting now. Join the Zoom call.',
                'url': '/events/community-demo-day-feb',
                'notification_type': 'event_reminder',
            },
            {
                'email': 'free@test.com',
                'title': 'Welcome to AI Shipping Labs!',
                'body': 'Thanks for joining. Check out our free courses and articles.',
                'url': '/',
                'notification_type': 'announcement',
            },
        ]
        for notif_data in notification_data:
            user = User.objects.filter(email=notif_data['email']).first()
            if user:
                _, created = Notification.objects.get_or_create(
                    user=user,
                    title=notif_data['title'],
                    defaults={
                        'body': notif_data['body'],
                        'url': notif_data['url'],
                        'notification_type': notif_data['notification_type'],
                    },
                )
                if created:
                    count += 1
        self.stdout.write(f'  Notifications: {count} created')
        return count

    # ------------------------------------------------------------------
    # Newsletter subscribers
    # ------------------------------------------------------------------
    def _seed_newsletter_subscribers(self):
        count = 0
        for email in NEWSLETTER_SUBSCRIBERS:
            _, created = NewsletterSubscriber.objects.get_or_create(
                email=email,
                defaults={'is_active': True},
            )
            if created:
                count += 1
        self.stdout.write(f'  Newsletter subscribers: {count} created')
        return count

    # ------------------------------------------------------------------
    # Social apps (OAuth providers from .env)
    # ------------------------------------------------------------------
    SOCIAL_APPS = [
        {
            'provider': 'google',
            'name': 'Google',
            'client_id_env': 'GOOGLE_OAUTH_CLIENT_ID',
            'secret_env': 'GOOGLE_OAUTH_CLIENT_SECRET',
        },
        {
            'provider': 'github',
            'name': 'GitHub',
            'client_id_env': 'GITHUB_OAUTH_CLIENT_ID',
            'secret_env': 'GITHUB_OAUTH_CLIENT_SECRET',
        },
        {
            'provider': 'slack',
            'name': 'Slack',
            'client_id_env': 'SLACK_OAUTH_CLIENT_ID',
            'secret_env': 'SLACK_OAUTH_CLIENT_SECRET',
        },
    ]

    def _seed_social_apps(self):
        count = 0
        site = Site.objects.get_current()
        for app_def in self.SOCIAL_APPS:
            client_id = os.environ.get(app_def['client_id_env'], '')
            secret = os.environ.get(app_def['secret_env'], '')
            if not client_id or not secret:
                continue
            app, created = SocialApp.objects.update_or_create(
                provider=app_def['provider'],
                defaults={
                    'name': app_def['name'],
                    'client_id': client_id,
                    'secret': secret,
                },
            )
            app.sites.add(site)
            if created:
                count += 1
                self.stdout.write(f'    Created {app_def["name"]} social app')
            else:
                self.stdout.write(f'    Updated {app_def["name"]} social app')
        self.stdout.write(f'  Social apps: {count} created')
        return count
