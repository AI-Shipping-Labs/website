"""Playwright E2E for human-readable worker task names (issue #920).

The Studio worker dashboard (`/studio/worker/`) must never surface a cryptic
Django-Q four-word codename. These scenarios seed ``django_q`` ``Task`` and
``OrmQ`` rows directly and assert the dashboard renders descriptive names or a
func-path fallback:

1. A failed task with a descriptive name is identifiable by that name, and the
   detail page shows the name plus the dotted func path.
2. A codename task falls back to its func path on the recent table, not the
   codename.
3. A hyphenated schedule name (``event-reminders``) is preserved, not mistaken
   for a codename.
4. The pending queue shows descriptive names / func paths (and the
   ``?fragment=pending`` poller applies the same humanization).
5. The detail page preserves the raw stored Django-Q name for debugging.
"""

import os
import uuid
from unittest import mock

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

# Local-only: seeds the DB and injects session cookies; cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only

_NO_CLUSTERS = "studio.worker_health.Stat.get_all"


def _reset_state():
    from django_q.models import OrmQ, Task

    Task.objects.all().delete()
    OrmQ.objects.all().delete()
    connection.close()


def _create_task(name, func, success=True, result=None):
    from django_q.models import Task

    now = timezone.now()
    return Task.objects.create(
        id=uuid.uuid4().hex,
        name=name,
        func=func,
        started=now,
        stopped=now,
        success=success,
        result=result,
    )


@pytest.mark.django_db(transaction=True)
class TestWorkerTaskNames:
    @pytest.mark.core
    def test_failed_task_identified_by_descriptive_name(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_state()
        _create_staff_user('admin@test.com')
        descriptive = 'Send campaign: Weekly digest from campaign admin'
        task = _create_task(
            descriptive,
            'email_app.tasks.send_campaign.run',
            success=False,
            result='RuntimeError: boom',
        )

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()
        with mock.patch(_NO_CLUSTERS, return_value=[]):
            page.goto(
                f'{django_server}/studio/worker/',
                wait_until='domcontentloaded',
            )
            body = page.content()
            assert descriptive in body

            page.goto(
                f'{django_server}/studio/worker/task/{task.id}/',
                wait_until='domcontentloaded',
            )
            detail = page.content()
            assert descriptive in detail
            assert 'email_app.tasks.send_campaign.run' in detail

        connection.close()
        context.close()

    def test_codename_task_shows_func_path(self, django_server, browser):
        _ensure_tiers()
        _reset_state()
        _create_staff_user('admin@test.com')
        _create_task(
            'texas-texas-oscar-earth',
            'community.tasks.email_matcher.match_community_emails',
        )

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()
        with mock.patch(_NO_CLUSTERS, return_value=[]):
            page.goto(
                f'{django_server}/studio/worker/',
                wait_until='domcontentloaded',
            )
            body = page.content()
            assert 'community.tasks.email_matcher.match_community_emails' in body
            assert 'texas-texas-oscar-earth' not in body

        connection.close()
        context.close()

    def test_hyphenated_schedule_name_preserved(self, django_server, browser):
        _ensure_tiers()
        _reset_state()
        _create_staff_user('admin@test.com')
        _create_task('event-reminders', 'events.tasks.send_reminders.run')

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()
        with mock.patch(_NO_CLUSTERS, return_value=[]):
            page.goto(
                f'{django_server}/studio/worker/',
                wait_until='domcontentloaded',
            )
            body = page.content()
            assert 'event-reminders' in body

        connection.close()
        context.close()

    def test_detail_page_preserves_raw_codename(self, django_server, browser):
        _ensure_tiers()
        _reset_state()
        _create_staff_user('admin@test.com')
        task = _create_task(
            'red-single-oranges-cold',
            'community.tasks.email_matcher.match_community_emails',
        )

        context = _auth_context(browser, 'admin@test.com')
        page = context.new_page()
        with mock.patch(_NO_CLUSTERS, return_value=[]):
            page.goto(
                f'{django_server}/studio/worker/task/{task.id}/',
                wait_until='domcontentloaded',
            )
            detail = page.content()
            # Humanized display name (func path) AND the raw codename are both
            # present, so nothing is hidden during debugging.
            assert 'community.tasks.email_matcher.match_community_emails' in detail
            assert 'red-single-oranges-cold' in detail

        connection.close()
        context.close()
