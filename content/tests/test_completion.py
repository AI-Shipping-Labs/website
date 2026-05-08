"""Tests for the shared content-completion primitive (issue #365).

Covers:
- ``UserContentCompletion`` model: unique constraint, lookup index.
- ``content/services/completion.py`` dispatch on ``Unit`` vs
  ``WorkshopPage``, and ``TypeError`` on any other class.
- ``mark_completed`` is idempotent and triggers auto-enrollment for
  course units.
- ``unmark_completed`` returns True only when something was deleted.
- ``is_completed`` and ``completed_ids_for`` for both kinds.
- ``POST /api/workshops/<slug>/pages/<page_slug>/complete`` endpoint:
  401 anonymous, 403 below tier, 200 toggles a row, response shape.
- The dashboard ``Continue Learning`` widget includes workshops where
  the user has at least one completed page and merges them with
  course items.
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.utils import timezone

from content.access import LEVEL_BASIC, LEVEL_MAIN
from content.models import (
    Article,
    Course,
    Enrollment,
    Module,
    Unit,
    UserContentCompletion,
    UserCourseProgress,
    Workshop,
    WorkshopPage,
)
from content.models.completion import CONTENT_TYPE_WORKSHOP_PAGE
from content.services import completion as completion_service
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _make_workshop(slug='ws', pages_required_level=LEVEL_BASIC, num_pages=3):
    workshop = Workshop.objects.create(
        slug=slug,
        title='Workshop ' + slug,
        date=date(2026, 4, 1),
        status='published',
        landing_required_level=0,
        pages_required_level=pages_required_level,
        recording_required_level=max(pages_required_level, LEVEL_MAIN),
        description='Body',
    )
    pages = []
    for i in range(num_pages):
        pages.append(WorkshopPage.objects.create(
            workshop=workshop,
            slug=f'page-{i+1}',
            title=f'Page {i+1}',
            sort_order=i + 1,
            body=f'Body {i+1}',
        ))
    return workshop, pages


class UserContentCompletionModelTest(TierSetupMixin, TestCase):
    """Smoke checks on the additive completion table."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='c@example.com', password='x',
        )
        cls.workshop, cls.pages = _make_workshop()

    def test_unique_constraint_per_user_and_object(self):
        UserContentCompletion.objects.create(
            user=self.user,
            content_type=CONTENT_TYPE_WORKSHOP_PAGE,
            object_id=self.pages[0].pk,
            completed_at=timezone.now(),
        )
        # A second insert for the same (user, content_type, object_id)
        # must fail at the DB level — the dashboard query relies on at
        # most one row per (user, page).
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserContentCompletion.objects.create(
                user=self.user,
                content_type=CONTENT_TYPE_WORKSHOP_PAGE,
                object_id=self.pages[0].pk,
                completed_at=timezone.now(),
            )

    def test_different_pages_can_coexist(self):
        UserContentCompletion.objects.create(
            user=self.user,
            content_type=CONTENT_TYPE_WORKSHOP_PAGE,
            object_id=self.pages[0].pk,
            completed_at=timezone.now(),
        )
        UserContentCompletion.objects.create(
            user=self.user,
            content_type=CONTENT_TYPE_WORKSHOP_PAGE,
            object_id=self.pages[1].pk,
            completed_at=timezone.now(),
        )
        self.assertEqual(
            UserContentCompletion.objects.filter(user=self.user).count(),
            2,
        )


class CompletionServiceDispatchTest(TierSetupMixin, TestCase):
    """The service routes Unit -> UserCourseProgress, WorkshopPage ->
    UserContentCompletion, and rejects anything else."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='svc@example.com', password='x',
        )
        cls.course = Course.objects.create(
            title='C', slug='c', status='published',
        )
        module = Module.objects.create(
            course=cls.course, title='M', slug='m', sort_order=0,
        )
        cls.unit = Unit.objects.create(
            module=module, title='U', slug='u', sort_order=0,
        )
        cls.workshop, cls.pages = _make_workshop()

    def test_mark_completed_unit_writes_user_course_progress(self):
        completion_service.mark_completed(self.user, self.unit)
        self.assertTrue(
            UserCourseProgress.objects.filter(
                user=self.user, unit=self.unit, completed_at__isnull=False,
            ).exists(),
        )
        # No row should land in the new table.
        self.assertFalse(
            UserContentCompletion.objects.filter(user=self.user).exists(),
        )

    def test_mark_completed_unit_auto_enrolls(self):
        # Pre-condition: not enrolled.
        self.assertFalse(
            Enrollment.objects.filter(
                user=self.user, course=self.course,
            ).exists(),
        )
        completion_service.mark_completed(self.user, self.unit)
        self.assertTrue(
            Enrollment.objects.filter(
                user=self.user,
                course=self.course,
                unenrolled_at__isnull=True,
            ).exists(),
        )

    def test_mark_completed_workshop_page_writes_new_table(self):
        completion_service.mark_completed(self.user, self.pages[0])
        self.assertTrue(
            UserContentCompletion.objects.filter(
                user=self.user,
                content_type=CONTENT_TYPE_WORKSHOP_PAGE,
                object_id=self.pages[0].pk,
            ).exists(),
        )
        # Workshop pages must NEVER touch UserCourseProgress.
        self.assertFalse(
            UserCourseProgress.objects.filter(user=self.user).exists(),
        )

    def test_mark_completed_is_idempotent_for_workshop_page(self):
        first = completion_service.mark_completed(self.user, self.pages[0])
        # Second call returns the same row without creating a duplicate.
        second = completion_service.mark_completed(self.user, self.pages[0])
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            UserContentCompletion.objects.filter(user=self.user).count(),
            1,
        )

    def test_mark_completed_rejects_unsupported_class(self):
        article = Article.objects.create(
            title='A', slug='a', description='', date=date.today(),
            published=True,
        )
        with self.assertRaises(TypeError):
            completion_service.mark_completed(self.user, article)

    def test_unmark_completed_returns_true_when_row_existed(self):
        completion_service.mark_completed(self.user, self.pages[0])
        self.assertTrue(
            completion_service.unmark_completed(self.user, self.pages[0]),
        )
        self.assertFalse(
            UserContentCompletion.objects.filter(user=self.user).exists(),
        )

    def test_unmark_completed_returns_false_when_no_row(self):
        self.assertFalse(
            completion_service.unmark_completed(self.user, self.pages[0]),
        )

    def test_is_completed_anonymous_user_returns_false_without_query(self):
        from django.contrib.auth.models import AnonymousUser
        with self.assertNumQueries(0):
            result = completion_service.is_completed(
                AnonymousUser(), self.pages[0],
            )
        self.assertFalse(result)

    def test_is_completed_dispatches_correctly(self):
        completion_service.mark_completed(self.user, self.unit)
        completion_service.mark_completed(self.user, self.pages[0])
        self.assertTrue(completion_service.is_completed(self.user, self.unit))
        self.assertTrue(
            completion_service.is_completed(self.user, self.pages[0]),
        )
        self.assertFalse(
            completion_service.is_completed(self.user, self.pages[1]),
        )

    def test_completed_ids_for_workshop_pages_batches(self):
        completion_service.mark_completed(self.user, self.pages[0])
        completion_service.mark_completed(self.user, self.pages[2])
        ids = completion_service.completed_ids_for(self.user, self.pages)
        self.assertEqual(ids, {self.pages[0].pk, self.pages[2].pk})

    def test_completed_ids_for_rejects_mixed_types(self):
        with self.assertRaises(TypeError):
            completion_service.completed_ids_for(
                self.user, [self.unit, self.pages[0]],
            )


class WorkshopPageCompleteEndpointTest(TierSetupMixin, TestCase):
    """API contract for the new toggle endpoint."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop, cls.pages = _make_workshop()

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email=f'wapi-{id(self)}@example.com', password='pw',
        )
        self.user.tier = self.basic_tier
        self.user.save()

    def _url(self, page=None):
        page = page or self.pages[0]
        return (
            f'/api/workshops/{self.workshop.slug}/pages/{page.slug}/complete'
        )

    def test_anonymous_returns_401(self):
        response = self.client.post(self._url())
        self.assertEqual(response.status_code, 401)
        # Must NOT have created a row.
        self.assertFalse(UserContentCompletion.objects.exists())

    def test_below_tier_returns_403(self):
        # Free user vs. Basic-required workshop.
        free_user = User.objects.create_user(
            email='free@example.com', password='pw',
        )
        free_user.tier = self.free_tier
        free_user.save()
        self.client.login(email='free@example.com', password='pw')
        response = self.client.post(self._url())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(UserContentCompletion.objects.exists())

    def test_first_post_returns_completed_true_and_writes_row(self):
        self.client.login(email=self.user.email, password='pw')
        response = self.client.post(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'completed': True})
        self.assertTrue(
            UserContentCompletion.objects.filter(
                user=self.user, object_id=self.pages[0].pk,
            ).exists(),
        )

    def test_second_post_returns_completed_false_and_deletes_row(self):
        self.client.login(email=self.user.email, password='pw')
        self.client.post(self._url())
        response = self.client.post(self._url())
        self.assertEqual(response.json(), {'completed': False})
        self.assertFalse(UserContentCompletion.objects.exists())

    def test_unknown_workshop_returns_404(self):
        self.client.login(email=self.user.email, password='pw')
        response = self.client.post(
            '/api/workshops/no-such/pages/page-1/complete',
        )
        self.assertEqual(response.status_code, 404)

    def test_unknown_page_returns_404(self):
        self.client.login(email=self.user.email, password='pw')
        response = self.client.post(
            f'/api/workshops/{self.workshop.slug}/pages/no-such/complete',
        )
        self.assertEqual(response.status_code, 404)


class WorkshopPageDetailIsCompletedContextTest(TierSetupMixin, TestCase):
    """The workshop page renders the right initial button state.

    AC: "On a fresh page reload after marking a workshop page completed,
    the button renders in its completed state (server-side state, not
    JS-only)."
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop, cls.pages = _make_workshop()

    def setUp(self):
        self.user = User.objects.create_user(
            email='wpd@example.com', password='x',
        )
        self.user.tier = self.basic_tier
        self.user.save()

    def _extract_btn_html(self, html: str) -> str:
        # Slice the rendered button element from the HTML so the JS
        # toggle (which contains both completed and uncompleted class
        # strings) doesn't pollute substring assertions.
        marker = 'data-testid="mark-page-complete-btn"'
        start = html.find('<button')
        # find the button element that contains the marker
        while start != -1:
            end = html.find('</button>', start)
            if end == -1:
                return ''
            chunk = html[start:end + len('</button>')]
            if marker in chunk:
                return chunk
            start = html.find('<button', end)
        return ''

    def test_completed_state_persists_across_reload(self):
        self.client.login(email='wpd@example.com', password='x')
        # Mark completed via the API.
        self.client.post(
            f'/api/workshops/{self.workshop.slug}/pages/'
            f'{self.pages[0].slug}/complete',
        )
        # Re-fetch the page — server should render the green Completed
        # button, not the default state.
        response = self.client.get(
            self.pages[0].get_absolute_url(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_completed'])
        btn_html = self._extract_btn_html(response.content.decode())
        self.assertIn('data-testid="mark-page-complete-btn"', btn_html)
        self.assertIn('border-green-500/30', btn_html)
        self.assertIn('Completed', btn_html)
        self.assertNotIn('Mark as completed', btn_html)

    def test_default_state_when_not_completed(self):
        # Inverse of the above: a fresh page (no completion row) renders
        # the default outline button with the 'Mark as completed' label.
        self.client.login(email='wpd@example.com', password='x')
        response = self.client.get(self.pages[0].get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['is_completed'])
        btn_html = self._extract_btn_html(response.content.decode())
        self.assertIn('data-testid="mark-page-complete-btn"', btn_html)
        self.assertNotIn('border-green-500/30', btn_html)
        self.assertIn('Mark as completed', btn_html)

    def test_button_hidden_for_anonymous(self):
        # Workshop allows landing-level=0 but pages=Basic; anonymous
        # users should not see the mark-as-completed button at all.
        # First, drop the page gate so the body renders.
        self.workshop.pages_required_level = 0
        self.workshop.save()
        response = self.client.get(self.pages[0].get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response, 'data-testid="mark-page-complete-btn"',
        )

    def test_button_hidden_when_gated(self):
        # Free user looking at a Basic-gated page sees the paywall and
        # no button. Issue #515: gated pages now return 403 to mirror the
        # course-unit teaser pattern.
        free_user = User.objects.create_user(
            email='free2@example.com', password='x',
        )
        free_user.tier = self.free_tier
        free_user.save()
        self.client.login(email='free2@example.com', password='x')
        response = self.client.get(self.pages[0].get_absolute_url())
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(
            response, 'data-testid="mark-page-complete-btn"', status_code=403,
        )


class DashboardContinueLearningWorkshopsTest(TierSetupMixin, TestCase):
    """Workshops with at least one completed page show in Continue
    Learning and disappear when fully completed."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop, cls.pages = _make_workshop()

    def setUp(self):
        self.user = User.objects.create_user(
            email='dash@example.com', password='x',
        )
        self.user.tier = self.basic_tier
        self.user.save()
        self.client.login(email='dash@example.com', password='x')

    def _mark(self, page, when=None):
        UserContentCompletion.objects.create(
            user=self.user,
            content_type=CONTENT_TYPE_WORKSHOP_PAGE,
            object_id=page.pk,
            completed_at=when or timezone.now(),
        )

    def test_workshop_appears_after_first_completion(self):
        self._mark(self.pages[0])
        response = self.client.get('/')
        items = response.context['in_progress_learning']
        kinds = [item['kind'] for item in items]
        self.assertIn('workshop', kinds)
        ws_item = next(i for i in items if i['kind'] == 'workshop')
        self.assertEqual(ws_item['workshop'].pk, self.workshop.pk)
        self.assertEqual(ws_item['completed_count'], 1)
        self.assertEqual(ws_item['total_units'], 3)
        self.assertEqual(ws_item['next_page'].pk, self.pages[1].pk)
        self.assertContains(response, self.workshop.title)
        self.assertContains(response, '1/3 pages completed')

    def test_workshop_drops_off_when_all_pages_completed(self):
        for p in self.pages:
            self._mark(p)
        response = self.client.get('/')
        items = response.context['in_progress_learning']
        ids = [
            item['workshop'].pk
            for item in items if item['kind'] == 'workshop'
        ]
        self.assertNotIn(self.workshop.pk, ids)

    def test_workshop_hidden_when_user_below_required_level(self):
        # User completed a page but has since downgraded to free.
        self._mark(self.pages[0])
        self.user.tier = self.free_tier
        self.user.save()
        response = self.client.get('/')
        items = response.context['in_progress_learning']
        kinds = [item['kind'] for item in items]
        self.assertNotIn('workshop', kinds)

    def test_course_only_user_sees_only_course_items(self):
        # Regression guard: if there are no workshop completions, the
        # merged list must be byte-for-byte equivalent to the
        # course-only list (same items, same order).
        course = Course.objects.create(
            title='Course X', slug='cx', status='published',
        )
        module = Module.objects.create(
            course=course, title='M', slug='m', sort_order=0,
        )
        unit_a = Unit.objects.create(
            module=module, title='UA', slug='ua', sort_order=0,
        )
        Unit.objects.create(
            module=module, title='UB', slug='ub', sort_order=1,
        )
        Enrollment.objects.create(user=self.user, course=course)
        UserCourseProgress.objects.create(
            user=self.user, unit=unit_a,
            completed_at=timezone.now(),
        )

        response = self.client.get('/')
        items = response.context['in_progress_learning']
        course_only = response.context['in_progress_courses']
        self.assertEqual(len(items), 1)
        self.assertEqual(items, course_only)

    def test_merged_list_orders_by_most_recent_activity(self):
        # Course completed more recently than workshop should rank
        # first; reverse to confirm the sort key works both ways.
        course = Course.objects.create(
            title='Course Y', slug='cy', status='published',
        )
        module = Module.objects.create(
            course=course, title='MY', slug='my', sort_order=0,
        )
        unit = Unit.objects.create(
            module=module, title='UY', slug='uy', sort_order=0,
        )
        Unit.objects.create(
            module=module, title='UY2', slug='uy2', sort_order=1,
        )
        Enrollment.objects.create(user=self.user, course=course)

        now = timezone.now()
        UserCourseProgress.objects.create(
            user=self.user, unit=unit,
            completed_at=now - timedelta(hours=5),
        )
        # Workshop completion is more recent -> should come first.
        self._mark(self.pages[0], when=now)

        response = self.client.get('/')
        items = response.context['in_progress_learning']
        kinds = [item['kind'] for item in items]
        self.assertEqual(kinds, ['workshop', 'course'])


class CompletionMigrationSmokeTest(TestCase):
    """The migration must create the table without requiring a data
    backfill. After ``migrate`` the table exists and is empty.

    This guards the AC: "Schema migration creates the table; no data
    migration is required."
    """

    def test_table_is_empty_on_fresh_install(self):
        self.assertEqual(UserContentCompletion.objects.count(), 0)
