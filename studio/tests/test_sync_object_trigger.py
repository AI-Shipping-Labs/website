"""Tests for the per-object Re-sync source button (issue #281).

Covers:
- Origin panel renders the Re-sync button when ``obj.source_repo`` is set
  and hides it for manually-created content.
- New URL ``/studio/sync/object/<model_name>/<object_id>/trigger/`` resolves
  to ``studio_sync_object_trigger``, requires staff, accepts only POST.
- The view enqueues a sync_content_source async task for the right
  ContentSource (matched by repo_name + content_type-from-model) and uses
  the same _mark_source_queued plumbing as the dashboard.
- Edge cases: missing object → 404, missing source_repo → flash error,
  missing ContentSource row → flash error, non-staff → 403.
- Worker-down warning surfaces on the flash.
- Redirects to HTTP_REFERER when same-host, else to /studio/sync/.
- Unit edit page origin actions target the parent course when requested.
"""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from content.models import Article, Course, Module, Unit, Workshop
from integrations.models import ContentSource, SyncLog

User = get_user_model()


def _make_article(**kwargs):
    defaults = {
        'title': 'Test Article',
        'slug': 'test-article',
        'date': datetime.date(2026, 1, 1),
        'source_repo': 'AI-Shipping-Labs/content',
        'source_path': 'blog/test.md',
        'source_commit': 'abc1234def5678901234567890123456789abcde',
        'published': True,
    }
    defaults.update(kwargs)
    return Article.objects.create(**defaults)


def _make_course(**kwargs):
    defaults = {
        'title': 'Test Course',
        'slug': 'test-course',
        'source_repo': 'AI-Shipping-Labs/content',
        'source_path': 'courses/test/course.yaml',
        'source_commit': 'def4567abc890123456789012345678901234567',
    }
    defaults.update(kwargs)
    return Course.objects.create(**defaults)


def _make_workshop(**kwargs):
    defaults = {
        'slug': 'demo',
        'title': 'Demo Workshop',
        'date': datetime.date(2026, 4, 21),
        'description': 'Hands-on intro.',
        'tags': ['agents'],
        'status': 'published',
        'landing_required_level': 0,
        'pages_required_level': 10,
        'recording_required_level': 20,
        'cover_image_url': '',
        'code_repo_url': '',
        'source_repo': 'AI-Shipping-Labs/workshops-content',
        'source_path': '2026/demo/workshop.yaml',
        'source_commit': 'abc1234def5678901234567890123456789abcde',
    }
    defaults.update(kwargs)
    return Workshop.objects.create(**defaults)


class OriginPanelRendersResyncButtonTest(TestCase):
    """The origin panel renders a Re-sync source button for synced content."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.synced_article = _make_article(slug='synced')
        cls.manual_article = Article.objects.create(
            title='Manual Article',
            slug='manual-article',
            date=datetime.date(2026, 1, 1),
            published=True,
            # explicitly NOT synced
            source_repo='',
            source_path='',
            source_commit='',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_synced_article_edit_page_renders_resync_button(self):
        response = self.client.get(
            f'/studio/articles/{self.synced_article.pk}/edit',
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # The button itself.
        self.assertIn('data-testid="resync-source-button"', body)
        # POSTs to the per-object trigger URL with the article's pk.
        self.assertIn(
            f'/studio/sync/object/article/{self.synced_article.pk}/trigger/',
            body,
        )

    def test_manual_article_edit_page_does_not_render_resync_button(self):
        """No source_repo → no Re-sync button or source metadata panel."""
        response = self.client.get(
            f'/studio/articles/{self.manual_article.pk}/edit',
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertNotIn('data-testid="resync-source-button"', body)
        self.assertNotIn('Re-sync source', body)

    def test_resync_button_form_uses_post_with_csrf(self):
        response = self.client.get(
            f'/studio/articles/{self.synced_article.pk}/edit',
        )
        body = response.content.decode()
        # Locate the form by its data-testid.
        idx = body.find('data-testid="resync-source-form"')
        self.assertGreater(idx, -1)
        # The chunk before the matching </form> contains a CSRF token input.
        end = body.find('</form>', idx)
        self.assertGreater(end, idx)
        chunk = body[idx:end]
        self.assertIn('name="csrfmiddlewaretoken"', chunk)
        self.assertIn('method="post"', body[max(0, idx - 200):idx + 50].lower())


class OriginPanelOnUnitEditPageTargetsParentCourseTest(TestCase):
    """Course unit edit pages target the parent course's pk."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.course = _make_course(slug='course-with-units')
        cls.module = Module.objects.create(
            course=cls.course,
            title='Mod 1',
            slug='mod-1',
            sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module,
            title='Lesson 1',
            slug='lesson-1',
            sort_order=1,
            # Unit also has its own source metadata, but the banner should
            # still target the parent course because the template uses
            # ``obj=course`` on the unit edit page.
            source_repo='AI-Shipping-Labs/content',
            source_path='courses/course-with-units/mod-1/lesson-1.md',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_unit_edit_page_resync_button_targets_parent_course(self):
        response = self.client.get(f'/studio/units/{self.unit.pk}/edit')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Button URL points at the COURSE pk, not the unit pk.
        self.assertIn(
            f'/studio/sync/object/course/{self.course.pk}/trigger/',
            body,
        )
        # Make sure it's NOT pointing at the unit pk.
        self.assertNotIn(
            f'/studio/sync/object/unit/{self.unit.pk}/trigger/',
            body,
        )


class SyncObjectTriggerAccessControlTest(TestCase):
    """Anonymous users get redirected; non-staff users get 403."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='testpass', is_staff=False,
        )
        cls.article = _make_article()
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )

    def test_anonymous_user_redirected_to_login(self):
        response = self.client.post(
            f'/studio/sync/object/article/{self.article.pk}/trigger/',
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_non_staff_user_gets_403(self):
        self.client.login(email='member@test.com', password='testpass')
        response = self.client.post(
            f'/studio/sync/object/article/{self.article.pk}/trigger/',
        )
        self.assertEqual(response.status_code, 403)
        # Defensive: no SyncLog row was created.
        self.assertFalse(
            SyncLog.objects.filter(source=self.source).exists(),
        )

    def test_non_staff_user_does_not_change_source_status(self):
        self.client.login(email='member@test.com', password='testpass')
        before = self.source.last_sync_status
        self.client.post(
            f'/studio/sync/object/article/{self.article.pk}/trigger/',
        )
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_sync_status, before)


class SyncObjectTriggerHTTPMethodTest(TestCase):
    """The view is POST-only — GET returns 405."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.article = _make_article()

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_get_is_not_allowed(self):
        response = self.client.get(
            f'/studio/sync/object/article/{self.article.pk}/trigger/',
        )
        self.assertEqual(response.status_code, 405)


class SyncObjectTrigger404Test(TestCase):
    """Unknown object pks and unknown model_names return 404."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    def test_missing_object_returns_404(self):
        response = self.client.post(
            '/studio/sync/object/article/999999/trigger/',
        )
        self.assertEqual(response.status_code, 404)

    def test_unknown_model_name_returns_404(self):
        # ``foobar`` isn't in the allowlist.
        response = self.client.post('/studio/sync/object/foobar/1/trigger/')
        self.assertEqual(response.status_code, 404)


class SyncObjectTriggerSuccessTest(TestCase):
    """Happy path: POST enqueues the sync, marks the source queued, flashes."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        # Issue #532: article + source are read-only fixtures (only the
        # source's status changes, and that's exercised via DB refresh).
        # setUpTestData is wrapped in TestData so per-test attribute
        # mutations don't leak across tests.
        cls.article = _make_article()
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    @patch('django_q.tasks.async_task')
    def test_post_enqueues_async_task_for_resolved_source(self, mock_async):
        self.client.post(
            f'/studio/sync/object/article/{self.article.pk}/trigger/',
        )
        self.assertEqual(mock_async.call_count, 1)
        # Positional args: function path + the resolved source.
        self.assertEqual(
            mock_async.call_args.args[0],
            'integrations.services.github.sync_content_source',
        )
        self.assertEqual(mock_async.call_args.args[1], self.source)

    @patch('django_q.tasks.async_task')
    def test_post_creates_queued_synclog_for_source(self, mock_async):
        self.client.post(
            f'/studio/sync/object/article/{self.article.pk}/trigger/',
        )
        log = SyncLog.objects.get(source=self.source)
        self.assertEqual(log.status, 'queued')

    @patch('django_q.tasks.async_task')
    def test_post_marks_source_status_queued(self, mock_async):
        self.client.post(
            f'/studio/sync/object/article/{self.article.pk}/trigger/',
        )
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_sync_status, 'queued')

    @patch('django_q.tasks.async_task')
    def test_flash_names_repo_and_content_type(self, mock_async):
        # Force a worker-up state so the flash uses success-level wording.
        # ``_worker_warning_suffix`` reads ``get_worker_status`` via the
        # local module's namespace import — patch it there so the suffix
        # path returns "" and we test the success branch cleanly.
        with patch(
            'studio.views.sync.get_worker_status',
            return_value={'expect_worker': True, 'alive': True},
        ):
            response = self.client.post(
                f'/studio/sync/object/article/{self.article.pk}/trigger/',
                follow=True,
            )
        body = response.content.decode()
        self.assertIn('AI-Shipping-Labs/content', body)
        self.assertIn('article', body)
        self.assertIn('Sync queued', body)
        # Worker-up branch: no warning suffix.
        self.assertNotIn('worker is not running', body)

    @patch('django_q.tasks.async_task', side_effect=Exception('queue error'))
    def test_enqueue_failure_does_not_mark_source_queued(self, mock_async):
        """If the async_task call itself raises, we must not lie about
        the source being queued. Mirrors the dashboard sync_trigger guard."""
        with self.assertLogs('studio.views.sync', level='ERROR') as logs:
            self.client.post(
                f'/studio/sync/object/article/{self.article.pk}/trigger/',
            )
        self.source.refresh_from_db()
        self.assertNotEqual(self.source.last_sync_status, 'queued')
        self.assertIn(
            'Error triggering object re-sync for AI-Shipping-Labs/content',
            logs.output[0],
        )
        self.assertFalse(
            SyncLog.objects.filter(
                source=self.source, status='queued',
            ).exists(),
        )


class SyncObjectTriggerWorkshopTest(TestCase):
    """Workshop button resolves to the workshop ContentSource."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        # Issue #532: workshop + source are read-only fixtures.
        cls.workshop = _make_workshop()
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/workshops-content',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    @patch('django_q.tasks.async_task')
    def test_workshop_resync_uses_workshop_repo(self, mock_async):
        self.client.post(
            f'/studio/sync/object/workshop/{self.workshop.pk}/trigger/',
        )
        self.assertEqual(mock_async.call_count, 1)
        # Issue #310: the resolved ContentSource is the workshops repo.
        self.assertEqual(
            mock_async.call_args.args[1].repo_name,
            'AI-Shipping-Labs/workshops-content',
        )
        # And a queued SyncLog row exists for it.
        log = SyncLog.objects.get(source=self.source)
        self.assertEqual(log.status, 'queued')


class SyncObjectTriggerCourseUnitInheritsCourseTest(TestCase):
    """Re-syncing from a unit page (POST to /object/course/<course_id>/) goes
    through the COURSE ContentSource, not a unit-specific one."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        # Issue #532: course/module/unit/source are read-only fixtures.
        cls.course = _make_course(slug='cwu')
        cls.module = Module.objects.create(
            course=cls.course, title='Mod 1', slug='mod-1', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module,
            title='Lesson 1', slug='lesson-1', sort_order=1,
            source_repo='AI-Shipping-Labs/content',
            source_path='courses/cwu/mod-1/lesson-1.md',
        )
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    @patch('django_q.tasks.async_task')
    def test_course_target_uses_course_repo(self, mock_async):
        # POSTing with the course's pk (the banner template uses
        # ``obj=course`` on the unit edit page) hits the course source.
        self.client.post(
            f'/studio/sync/object/course/{self.course.pk}/trigger/',
        )
        self.assertEqual(mock_async.call_count, 1)
        # Issue #310: the resolved ContentSource is the content repo
        # (one source per repo, no per-type lookup).
        self.assertEqual(
            mock_async.call_args.args[1].repo_name,
            'AI-Shipping-Labs/content',
        )


class SyncObjectTriggerMissingSourceRepoTest(TestCase):
    """Object exists but has no source_repo → flash error, no enqueue."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        # Issue #532: manually-created article fixture is read-only.
        cls.article = Article.objects.create(
            title='Manual', slug='manual',
            date=datetime.date(2026, 1, 1),
            published=True,
            source_repo='',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    @patch('django_q.tasks.async_task')
    def test_no_async_task_enqueued(self, mock_async):
        self.client.post(
            f'/studio/sync/object/article/{self.article.pk}/trigger/',
        )
        mock_async.assert_not_called()

    @patch('django_q.tasks.async_task')
    def test_flash_explains_no_source_repo(self, mock_async):
        response = self.client.post(
            f'/studio/sync/object/article/{self.article.pk}/trigger/',
            follow=True,
        )
        body = response.content.decode()
        self.assertIn('no source_repo', body)


class SyncObjectTriggerMissingContentSourceTest(TestCase):
    """Object has source_repo but no matching ContentSource row exists.

    The matching source might have been deleted from /studio/sync/ while
    the object retained its orphan source_repo metadata. The view must
    flash an error rather than 500.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        # Issue #532: orphan-source article is a read-only fixture.
        # Note: NO ContentSource for ('Old-Org/old-repo', 'article').
        cls.article = _make_article(
            source_repo='Old-Org/old-repo',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    @patch('django_q.tasks.async_task')
    def test_no_async_task_enqueued(self, mock_async):
        self.client.post(
            f'/studio/sync/object/article/{self.article.pk}/trigger/',
        )
        mock_async.assert_not_called()

    @patch('django_q.tasks.async_task')
    def test_no_synclog_created(self, mock_async):
        self.client.post(
            f'/studio/sync/object/article/{self.article.pk}/trigger/',
        )
        self.assertEqual(SyncLog.objects.count(), 0)

    @patch('django_q.tasks.async_task')
    def test_flash_mentions_missing_content_source(self, mock_async):
        response = self.client.post(
            f'/studio/sync/object/article/{self.article.pk}/trigger/',
            follow=True,
        )
        body = response.content.decode()
        # Operator-friendly message naming both the repo and the
        # missing content type.
        self.assertIn('No content source is configured', body)
        self.assertIn('Old-Org/old-repo', body)
        self.assertIn('article', body)


class SyncObjectTriggerWorkerWarningTest(TestCase):
    """When the worker is down the flash is warning-level and includes
    the standard "worker is not running" wording."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        # Issue #532: article + source are read-only fixtures.
        cls.article = _make_article()
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    @patch('django_q.tasks.async_task')
    def test_worker_down_flash_includes_warning_suffix(self, mock_async):
        # ``_worker_warning_suffix`` resolves ``get_worker_status`` through
        # the importing module's namespace — patch the symbol where it's
        # used (``studio.views.sync``), not at the source module.
        with patch(
            'studio.views.sync.get_worker_status',
            return_value={'expect_worker': True, 'alive': False},
        ):
            response = self.client.post(
                f'/studio/sync/object/article/{self.article.pk}/trigger/',
                follow=True,
            )
        body = response.content.decode()
        # Must mention the worker-down condition and the qcluster command.
        self.assertIn('worker is not running', body)
        self.assertIn('manage.py qcluster', body)


class SyncObjectTriggerRedirectTest(TestCase):
    """Successful POST redirects to HTTP_REFERER when same-host, else
    /studio/sync/."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        # Issue #532: article + source are read-only fixtures.
        cls.article = _make_article()
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')

    @patch('django_q.tasks.async_task')
    def test_redirects_to_same_host_referer(self, mock_async):
        edit_url = f'/studio/articles/{self.article.pk}/edit'
        response = self.client.post(
            f'/studio/sync/object/article/{self.article.pk}/trigger/',
            HTTP_REFERER=f'http://testserver{edit_url}',
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], f'http://testserver{edit_url}')

    @patch('django_q.tasks.async_task')
    def test_redirects_to_dashboard_when_no_referer(self, mock_async):
        response = self.client.post(
            f'/studio/sync/object/article/{self.article.pk}/trigger/',
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/sync/')

    @patch('django_q.tasks.async_task')
    def test_redirects_to_dashboard_when_referer_is_external(self, mock_async):
        """Defends against open-redirect: a hostile referer header pointing
        to evil.example.com must NOT be honoured."""
        response = self.client.post(
            f'/studio/sync/object/article/{self.article.pk}/trigger/',
            HTTP_REFERER='https://evil.example.com/phish',
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/sync/')
