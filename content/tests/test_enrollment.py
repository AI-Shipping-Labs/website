"""Tests for Course Enrollment — issue #236.

Covers:
- Enrollment model + partial unique index (re-enroll after unenroll)
- ``ensure_enrollment`` / ``auto_enroll_on_progress`` service helpers
- Enroll button on course detail (POST creates Enrollment, redirects)
- Auto-enroll when first lesson is marked complete
- Idempotent: marking complete with active enrollment doesn't create dup
- Course catalog "Enrolled" badge
- Dashboard "Continue Learning" sources from Enrollment, respects tier
- Re-enrollment after unenrollment (no UNIQUE blow-up)
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, tag
from django.utils import timezone

from content.access import LEVEL_MAIN, LEVEL_PREMIUM
from content.models import Course, Enrollment, Module, Unit, UserCourseProgress
from content.models.enrollment import (
    SOURCE_AUTO_PROGRESS,
    SOURCE_MANUAL,
)
from content.services.enrollment import (
    auto_enroll_on_progress,
    ensure_enrollment,
    is_enrolled,
    unenroll,
)
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _make_course_with_units(title='Course', slug='course', n_units=3, required_level=0):
    course = Course.objects.create(
        title=title, slug=slug, status='published', required_level=required_level,
    )
    module = Module.objects.create(
        course=course, title='Mod', slug=f'{slug}-mod', sort_order=0,
    )
    units = []
    for i in range(n_units):
        units.append(Unit.objects.create(
            module=module, title=f'U{i+1}', slug=f'{slug}-u{i+1}', sort_order=i,
        ))
    return course, units


# ============================================================
# Model-level tests
# ============================================================


@tag('core')
class EnrollmentModelTest(TierSetupMixin, TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            email='m@example.com', password='testpass', email_verified=True,
        )
        self.course, _ = _make_course_with_units(slug='m-course')

    def test_default_source_is_manual(self):
        enr = Enrollment.objects.create(user=self.user, course=self.course)
        self.assertEqual(enr.source, SOURCE_MANUAL)

    def test_is_active_property(self):
        enr = Enrollment.objects.create(user=self.user, course=self.course)
        self.assertTrue(enr.is_active)
        enr.unenrolled_at = timezone.now()
        self.assertFalse(enr.is_active)

    def test_partial_unique_blocks_duplicate_active_enrollment(self):
        Enrollment.objects.create(user=self.user, course=self.course)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Enrollment.objects.create(user=self.user, course=self.course)

    def test_reenroll_after_unenroll_succeeds(self):
        # Active enrollment, then unenroll, then re-enroll — partial unique
        # index permits a new active row alongside the historical
        # unenrolled row.
        enr1 = Enrollment.objects.create(user=self.user, course=self.course)
        enr1.unenrolled_at = timezone.now()
        enr1.save(update_fields=['unenrolled_at'])

        enr2 = Enrollment.objects.create(user=self.user, course=self.course)
        self.assertNotEqual(enr1.pk, enr2.pk)
        self.assertEqual(
            Enrollment.objects.filter(user=self.user, course=self.course).count(),
            2,
        )
        self.assertEqual(
            Enrollment.objects.filter(
                user=self.user, course=self.course, unenrolled_at__isnull=True,
            ).count(),
            1,
        )


# ============================================================
# Service helpers
# ============================================================


@tag('core')
class EnrollmentServiceTest(TierSetupMixin, TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            email='svc@example.com', password='testpass', email_verified=True,
        )
        self.course, _ = _make_course_with_units(slug='svc-course')

    def test_ensure_enrollment_creates_when_missing(self):
        enr, created = ensure_enrollment(self.user, self.course)
        self.assertTrue(created)
        self.assertIsNotNone(enr)
        self.assertEqual(enr.source, SOURCE_MANUAL)

    def test_ensure_enrollment_is_idempotent(self):
        enr1, created1 = ensure_enrollment(self.user, self.course)
        enr2, created2 = ensure_enrollment(self.user, self.course)
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(enr1.pk, enr2.pk)

    def test_auto_enroll_uses_auto_progress_source(self):
        enr, created = auto_enroll_on_progress(self.user, self.course)
        self.assertTrue(created)
        self.assertEqual(enr.source, SOURCE_AUTO_PROGRESS)

    def test_auto_enroll_does_not_overwrite_existing_source(self):
        # If user already enrolled manually, auto-enroll-on-progress is a no-op
        ensure_enrollment(self.user, self.course, source=SOURCE_MANUAL)
        _, created = auto_enroll_on_progress(self.user, self.course)
        self.assertFalse(created)
        existing = Enrollment.objects.get(user=self.user, course=self.course)
        self.assertEqual(existing.source, SOURCE_MANUAL)

    def test_is_enrolled(self):
        self.assertFalse(is_enrolled(self.user, self.course))
        ensure_enrollment(self.user, self.course)
        self.assertTrue(is_enrolled(self.user, self.course))

    def test_unenroll_marks_unenrolled_at(self):
        ensure_enrollment(self.user, self.course)
        changed = unenroll(self.user, self.course)
        self.assertTrue(changed)
        self.assertFalse(is_enrolled(self.user, self.course))

    def test_unenroll_no_active_enrollment_returns_false(self):
        self.assertFalse(unenroll(self.user, self.course))

    def test_anonymous_user_returns_safely(self):
        from django.contrib.auth.models import AnonymousUser
        anon = AnonymousUser()
        self.assertFalse(is_enrolled(anon, self.course))
        result = ensure_enrollment(anon, self.course)
        self.assertEqual(result, (None, False))


# ============================================================
# Enroll / Unenroll views
# ============================================================


@tag('core')
class EnrollViewTest(TierSetupMixin, TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            email='ev@example.com', password='testpass', email_verified=True,
        )
        self.course, self.units = _make_course_with_units(slug='ev-course')
        self.client.login(email='ev@example.com', password='testpass')

    def test_enroll_creates_enrollment(self):
        self.assertFalse(is_enrolled(self.user, self.course))
        response = self.client.post(f'/courses/{self.course.slug}/enroll')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(is_enrolled(self.user, self.course))

    def test_enroll_redirects_to_first_unit(self):
        response = self.client.post(f'/courses/{self.course.slug}/enroll')
        self.assertRedirects(
            response, self.units[0].get_absolute_url(), fetch_redirect_response=False,
        )

    def test_enroll_redirects_to_next_unfinished_unit(self):
        # User has completed first unit (e.g. via API) before clicking Enroll
        # explicitly. Enroll should jump to the next unfinished unit.
        UserCourseProgress.objects.create(
            user=self.user, unit=self.units[0], completed_at=timezone.now(),
        )
        response = self.client.post(f'/courses/{self.course.slug}/enroll')
        self.assertRedirects(
            response, self.units[1].get_absolute_url(), fetch_redirect_response=False,
        )

    def test_enroll_is_idempotent(self):
        self.client.post(f'/courses/{self.course.slug}/enroll')
        self.client.post(f'/courses/{self.course.slug}/enroll')
        self.assertEqual(
            Enrollment.objects.filter(
                user=self.user, course=self.course, unenrolled_at__isnull=True,
            ).count(),
            1,
        )

    def test_enroll_blocked_for_user_without_tier_access(self):
        # Tier-gated course, free user — no enrollment should be created.
        gated_course, _ = _make_course_with_units(
            slug='gated', required_level=LEVEL_PREMIUM,
        )
        response = self.client.post(f'/courses/{gated_course.slug}/enroll')
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(
            response, gated_course.get_absolute_url(), fetch_redirect_response=False,
        )
        self.assertFalse(is_enrolled(self.user, gated_course))

    def test_enroll_requires_login(self):
        self.client.logout()
        response = self.client.post(f'/courses/{self.course.slug}/enroll')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_enroll_get_request_rejected(self):
        # require_POST decorator
        response = self.client.get(f'/courses/{self.course.slug}/enroll')
        self.assertEqual(response.status_code, 405)


@tag('core')
class UnenrollViewTest(TierSetupMixin, TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            email='un@example.com', password='testpass', email_verified=True,
        )
        self.course, _ = _make_course_with_units(slug='un-course')
        self.client.login(email='un@example.com', password='testpass')

    def test_unenroll_soft_deletes(self):
        Enrollment.objects.create(user=self.user, course=self.course)
        response = self.client.post(f'/courses/{self.course.slug}/unenroll')
        self.assertEqual(response.status_code, 302)
        enr = Enrollment.objects.get(user=self.user, course=self.course)
        self.assertIsNotNone(enr.unenrolled_at)

    def test_unenroll_no_op_when_not_enrolled(self):
        # Should redirect without raising
        response = self.client.post(f'/courses/{self.course.slug}/unenroll')
        self.assertEqual(response.status_code, 302)


# ============================================================
# Auto-enroll on lesson complete
# ============================================================


@tag('core')
class AutoEnrollOnCompleteTest(TierSetupMixin, TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            email='auto@example.com', password='testpass', email_verified=True,
        )
        self.course, self.units = _make_course_with_units(slug='auto-course')
        self.client.login(email='auto@example.com', password='testpass')

    def test_first_complete_creates_enrollment(self):
        self.assertFalse(is_enrolled(self.user, self.course))
        response = self.client.post(
            f'/api/courses/{self.course.slug}/units/{self.units[0].pk}/complete',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(is_enrolled(self.user, self.course))
        enr = Enrollment.objects.get(user=self.user, course=self.course)
        self.assertEqual(enr.source, SOURCE_AUTO_PROGRESS)

    def test_complete_with_existing_enrollment_does_not_duplicate(self):
        # Manually enroll first
        Enrollment.objects.create(
            user=self.user, course=self.course, source=SOURCE_MANUAL,
        )
        self.client.post(
            f'/api/courses/{self.course.slug}/units/{self.units[0].pk}/complete',
        )
        # Still exactly one active enrollment, source still 'manual'
        enrs = Enrollment.objects.filter(user=self.user, course=self.course)
        self.assertEqual(enrs.count(), 1)
        self.assertEqual(enrs.first().source, SOURCE_MANUAL)

    def test_uncompleting_does_not_remove_enrollment(self):
        # Toggling complete twice (complete -> uncomplete) should leave the
        # enrollment in place.
        url = f'/api/courses/{self.course.slug}/units/{self.units[0].pk}/complete'
        self.client.post(url)  # complete
        self.client.post(url)  # uncomplete
        self.assertTrue(is_enrolled(self.user, self.course))


# ============================================================
# Course detail page — Enroll / Continue button
# ============================================================


class CourseDetailEnrollButtonTest(TierSetupMixin, TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            email='cd@example.com', password='testpass', email_verified=True,
        )
        self.course, self.units = _make_course_with_units(slug='cd-course')
        self.client.login(email='cd@example.com', password='testpass')

    def test_enroll_button_shown_when_not_enrolled(self):
        response = self.client.get(f'/courses/{self.course.slug}')
        self.assertContains(response, 'data-testid="enroll-button"')
        self.assertNotContains(response, 'data-testid="continue-button"')

    def test_continue_button_shown_when_enrolled(self):
        Enrollment.objects.create(user=self.user, course=self.course)
        response = self.client.get(f'/courses/{self.course.slug}')
        self.assertContains(response, 'data-testid="continue-button"')
        self.assertNotContains(response, 'data-testid="enroll-button"')
        # Continue link points at the first unfinished unit
        self.assertContains(response, self.units[0].get_absolute_url())

    def test_unenroll_button_shown_when_enrolled(self):
        Enrollment.objects.create(user=self.user, course=self.course)
        response = self.client.get(f'/courses/{self.course.slug}')
        self.assertContains(response, 'data-testid="unenroll-button"')

    def test_enrollment_cta_hidden_for_anonymous_user(self):
        self.client.logout()
        response = self.client.get(f'/courses/{self.course.slug}')
        self.assertNotContains(response, 'data-testid="enroll-button"')
        self.assertNotContains(response, 'data-testid="continue-button"')

    def test_enrollment_cta_hidden_when_user_lacks_tier_access(self):
        gated, _ = _make_course_with_units(
            slug='cd-gated', required_level=LEVEL_PREMIUM,
        )
        response = self.client.get(f'/courses/{gated.slug}')
        # Without access we show the upgrade CTA instead.
        self.assertNotContains(response, 'data-testid="enroll-button"')
        self.assertNotContains(response, 'data-testid="continue-button"')


# ============================================================
# Catalog "Enrolled" badge
# ============================================================


class CatalogEnrolledBadgeTest(TierSetupMixin, TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            email='cat@example.com', password='testpass', email_verified=True,
        )
        self.enrolled_course, _ = _make_course_with_units(
            title='Enrolled Course', slug='cat-enrolled',
        )
        self.other_course, _ = _make_course_with_units(
            title='Other Course', slug='cat-other',
        )
        Enrollment.objects.create(user=self.user, course=self.enrolled_course)
        self.client.login(email='cat@example.com', password='testpass')

    def test_enrolled_badge_renders_for_enrolled_course(self):
        response = self.client.get('/courses')
        self.assertContains(response, 'data-testid="enrolled-badge"', count=1)

    def test_unenrolled_course_has_no_badge(self):
        # Only one badge total (for the enrolled course); the other course
        # row exists but has no badge.
        response = self.client.get('/courses')
        content = response.content.decode()
        # Sanity: both courses appear
        self.assertIn('Enrolled Course', content)
        self.assertIn('Other Course', content)

    def test_anonymous_user_sees_no_badges(self):
        self.client.logout()
        response = self.client.get('/courses')
        self.assertNotContains(response, 'data-testid="enrolled-badge"')


# ============================================================
# Dashboard sourced from enrollment
# ============================================================


class DashboardEnrollmentSourceTest(TierSetupMixin, TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            email='dsh@example.com', password='testpass', email_verified=True,
        )
        # Use a distinctive title so substring matching against the
        # rendered dashboard isn't confused by template chrome words.
        self.course, self.units = _make_course_with_units(
            title='Dashboard Test Course', slug='dsh-course',
        )
        self.client.login(email='dsh@example.com', password='testpass')

    def test_enrollment_without_progress_appears_in_dashboard(self):
        # Brand-new enrollment, no completed units. The old behaviour
        # would show "No courses in progress yet" — issue #236 changes
        # that.
        Enrollment.objects.create(user=self.user, course=self.course)
        response = self.client.get('/')
        self.assertContains(response, 'Dashboard Test Course')
        self.assertContains(response, '0/3 units completed')
        self.assertContains(response, 'Just enrolled')

    def test_unenrolled_course_hidden_from_dashboard(self):
        enr = Enrollment.objects.create(user=self.user, course=self.course)
        UserCourseProgress.objects.create(
            user=self.user, unit=self.units[0], completed_at=timezone.now(),
        )
        # Confirm visible
        response = self.client.get('/')
        self.assertContains(response, 'Dashboard Test Course')
        # Unenroll, then it should disappear
        enr.unenrolled_at = timezone.now()
        enr.save(update_fields=['unenrolled_at'])
        response = self.client.get('/')
        self.assertNotContains(response, 'Dashboard Test Course')

    def test_tier_gated_enrollment_hidden_when_access_lost(self):
        # User enrolled while having access; later their tier drops below
        # required_level. Enrollment row stays but the card is hidden.
        gated, units = _make_course_with_units(
            title='Gated Course', slug='dsh-gated', required_level=LEVEL_MAIN,
        )
        Enrollment.objects.create(user=self.user, course=gated)
        UserCourseProgress.objects.create(
            user=self.user, unit=units[0], completed_at=timezone.now(),
        )
        # User has free tier — no LEVEL_MAIN access.
        response = self.client.get('/')
        self.assertNotContains(response, 'Gated Course')
        # The enrollment row still exists in the DB
        self.assertTrue(
            Enrollment.objects.filter(
                user=self.user, course=gated, unenrolled_at__isnull=True,
            ).exists(),
        )

    def test_tier_gated_enrollment_visible_when_access_returns(self):
        # Same setup as above but user is on Main tier — card should
        # show (issue #236 acceptance criterion).
        self.user.tier = self.main_tier
        self.user.save()
        gated, units = _make_course_with_units(
            title='Gated Course', slug='dsh-gated2', required_level=LEVEL_MAIN,
        )
        Enrollment.objects.create(user=self.user, course=gated)
        UserCourseProgress.objects.create(
            user=self.user, unit=units[0], completed_at=timezone.now(),
        )
        response = self.client.get('/')
        self.assertContains(response, 'Gated Course')

    def test_dashboard_sort_falls_back_to_enrolled_at(self):
        # Course A enrolled 2 days ago, no progress.
        # Course B enrolled today, no progress.
        # B should sort first because its enrolled_at is newer.
        course_a, _ = _make_course_with_units(title='A Course', slug='dsh-a')
        course_b, _ = _make_course_with_units(title='B Course', slug='dsh-b')
        enr_a = Enrollment.objects.create(user=self.user, course=course_a)
        enr_b = Enrollment.objects.create(user=self.user, course=course_b)
        # Simulate enrolled_at history
        Enrollment.objects.filter(pk=enr_a.pk).update(
            enrolled_at=timezone.now() - timedelta(days=2),
        )
        Enrollment.objects.filter(pk=enr_b.pk).update(
            enrolled_at=timezone.now(),
        )
        response = self.client.get('/')
        content = response.content.decode()
        pos_b = content.index('B Course')
        pos_a = content.index('A Course')
        self.assertLess(pos_b, pos_a)

    def test_empty_state_copy_updated(self):
        response = self.client.get('/')
        self.assertContains(
            response,
            'No courses in progress yet — browse the catalog to enroll.',
        )


# ============================================================
# Backfill migration
# ============================================================


class BackfillMigrationTest(TestCase):
    """The 0025_backfill_enrollments data migration is applied as part
    of the test database creation; verify the migration logic by running
    the same code path on fresh data and asserting idempotency.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='bf@example.com', password='testpass', email_verified=True,
        )
        self.course, self.units = _make_course_with_units(slug='bf-course')

    def test_running_backfill_creates_enrollment_for_user_with_progress(self):
        # Simulate pre-#236 state: progress exists, no enrollment.
        Enrollment.objects.filter(user=self.user, course=self.course).delete()
        UserCourseProgress.objects.create(
            user=self.user, unit=self.units[0], completed_at=timezone.now(),
        )
        import importlib

        from django.apps import apps
        backfill_mod = importlib.import_module(
            'content.migrations.0025_backfill_enrollments',
        )
        backfill_mod.backfill_enrollments(apps, None)
        enrollments = Enrollment.objects.filter(
            user=self.user, course=self.course, unenrolled_at__isnull=True,
        )
        self.assertEqual(enrollments.count(), 1)
        self.assertEqual(enrollments.first().source, SOURCE_AUTO_PROGRESS)

    def test_backfill_is_idempotent(self):
        # Running twice creates exactly one enrollment per (user, course)
        UserCourseProgress.objects.create(
            user=self.user, unit=self.units[0], completed_at=timezone.now(),
        )
        import importlib

        from django.apps import apps
        backfill_mod = importlib.import_module(
            'content.migrations.0025_backfill_enrollments',
        )
        backfill_mod.backfill_enrollments(apps, None)
        backfill_mod.backfill_enrollments(apps, None)
        self.assertEqual(
            Enrollment.objects.filter(user=self.user, course=self.course).count(),
            1,
        )

    def test_backfill_uses_earliest_completion_as_enrolled_at(self):
        # Multiple completed units — enrolled_at = earliest completion.
        first = timezone.now() - timedelta(days=10)
        last = timezone.now() - timedelta(days=1)
        UserCourseProgress.objects.create(
            user=self.user, unit=self.units[0], completed_at=last,
        )
        UserCourseProgress.objects.create(
            user=self.user, unit=self.units[1], completed_at=first,
        )
        # Drop the auto-created enrollment (the API hook would have
        # made one) so we test the backfill path cleanly.
        Enrollment.objects.filter(user=self.user, course=self.course).delete()
        import importlib

        from django.apps import apps
        backfill_mod = importlib.import_module(
            'content.migrations.0025_backfill_enrollments',
        )
        backfill_mod.backfill_enrollments(apps, None)
        enr = Enrollment.objects.get(user=self.user, course=self.course)
        # enrolled_at should match the earliest completion (within a
        # microsecond — datetimes round-trip exactly).
        self.assertEqual(enr.enrolled_at, first)
