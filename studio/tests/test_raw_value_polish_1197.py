"""Regression coverage for Studio raw-value polish (#1197)."""

import datetime
import re
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from django_q.models import OrmQ
from django_q.signing import SignedPackage

from accounts.templatetags.date_formatting import operator_datetime
from analytics.models import UserAttribution
from email_app.models import EmailTemplateOverride
from notifications.models import Notification
from plans.models import Plan, Sprint
from triggers.models import TriggerSubscription

User = get_user_model()


def _make_ormq(*, lock, name='task'):
    payload = {
        'id': uuid.uuid4().hex,
        'name': name,
        'func': 'integrations.services.github.sync_content_source',
        'args': (),
        'kwargs': {},
    }
    return OrmQ.objects.create(
        key='default',
        payload=SignedPackage.dumps(payload),
        lock=lock,
    )


class StudioRawValuePolishTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_worker_pending_locks_use_compact_units_in_page_and_fragment(self):
        now = timezone.now()
        _make_ormq(lock=now + timedelta(minutes=3), name='future-lock')
        _make_ormq(lock=now - timedelta(days=15), name='expired-lock')
        _make_ormq(lock=None, name='unlocked-task')

        with (
            patch('studio.views.worker.timezone.now', return_value=now),
            patch('studio.worker_health.Stat.get_all', return_value=[]),
        ):
            page = self.client.get('/studio/worker/')
            fragment = self.client.get('/studio/worker/?fragment=pending')

        for response in (page, fragment):
            body = response.content.decode()
            self.assertIn('future-lock', body)
            self.assertIn('in 3m', body)
            self.assertIn('expired-lock', body)
            self.assertIn('expired 15d ago', body)
            self.assertIn('unlocked-task', body)
            self.assertFalse(
                re.search(r'\b\d{4,}s\b', body),
                'Raw multi-digit seconds leaked into lock display',
            )

    def test_plan_list_strips_markdown_without_changing_detail_title(self):
        sprint = Sprint.objects.create(
            name='Sprint',
            slug='sprint-1197',
            start_date=datetime.date(2026, 7, 1),
        )
        plan = Plan.objects.create(
            member=self.member,
            sprint=sprint,
            title='**Ship** `RAG` with [docs](https://example.com)',
        )

        list_response = self.client.get('/studio/plans/')
        self.assertContains(list_response, 'Ship RAG with docs')
        self.assertNotContains(list_response, '**Ship**')
        self.assertNotContains(list_response, '`RAG`')
        self.assertNotContains(list_response, '[docs](https://example.com)')

        detail_response = self.client.get(f'/studio/plans/{plan.pk}/')
        self.assertContains(detail_response, '**Ship** `RAG` with [docs]')

    def test_email_template_list_strips_control_flow_but_edit_preserves_source(self):
        subject = (
            '{% if user_name %}Hi {{ user_name }}'
            '{% else %}Welcome builder{% endif %}'
        )
        EmailTemplateOverride.objects.create(
            template_name='welcome',
            subject=subject,
            body_markdown='Body',
        )

        list_response = self.client.get('/studio/email-templates/')
        self.assertContains(list_response, 'Hi user_name Welcome builder')
        self.assertNotContains(list_response, '{% if')
        self.assertNotContains(list_response, '{% else')
        self.assertNotContains(list_response, '{% endif')

        edit_response = self.client.get('/studio/email-templates/welcome/edit/')
        self.assertContains(edit_response, subject)

    def test_trigger_subscription_filters_are_readable_not_python_reprs(self):
        TriggerSubscription.objects.create(
            event_type='custom',
            property_filter={},
            target_url='https://handler.example.com/all',
            secret='secret',
        )
        TriggerSubscription.objects.create(
            event_type='custom',
            property_filter={'name': 'experiment_demo'},
            target_url='https://handler.example.com/filtered',
            secret='secret',
        )

        response = self.client.get('/studio/triggers/subscriptions/')
        self.assertContains(response, 'All events')
        self.assertContains(response, 'name = experiment_demo')
        self.assertNotContains(response, "{'name': 'experiment_demo'}")

    def test_import_schedule_cards_show_human_cron_glosses(self):
        response = self.client.get('/studio/imports/')
        self.assertContains(response, 'daily 03:00 UTC')
        self.assertContains(response, 'daily 03:30 UTC')
        self.assertNotContains(response, '>03:00 UTC · 0 3 * * *<')

    def test_recent_signup_uses_operator_datetime_not_relative_timesince(self):
        UserAttribution.objects.all().delete()
        user = User.objects.create_user(email='signup@test.com', password='pw')
        attr, _ = UserAttribution.objects.get_or_create(user=user)
        created_at = timezone.now() - timedelta(days=6)
        UserAttribution.objects.filter(pk=attr.pk).update(created_at=created_at)
        attr.refresh_from_db()

        response = self.client.get('/studio/signup-analytics/')
        self.assertContains(response, operator_datetime(attr.created_at))
        self.assertNotContains(response, '6 days ago')

    def test_notification_log_normalizes_site_local_urls_only_for_display(self):
        target_user = User.objects.create_user(email='target@test.com', password='pw')
        urls = [
            '/events/example?x=1#join',
            'http://localhost:8000/blog/example?x=1',
            'https://aishippinglabs.com/blog/example?x=1#frag',
            'https://external.example.com/blog/example?x=1',
        ]
        for index, url in enumerate(urls):
            Notification.objects.create(
                user=target_user,
                title=f'Batch {index}',
                url=url,
                notification_type='new_content',
            )

        response = self.client.get('/studio/notifications/')
        self.assertContains(response, 'href="/events/example?x=1#join"')
        self.assertContains(response, 'href="/blog/example?x=1"')
        self.assertContains(response, 'href="/blog/example?x=1#frag"')
        self.assertContains(
            response,
            'href="https://external.example.com/blog/example?x=1"',
        )
        self.assertNotContains(
            response,
            'href="http://localhost:8000/blog/example?x=1"',
        )
        self.assertNotContains(
            response,
            'href="https://aishippinglabs.com/blog/example?x=1#frag"',
        )
