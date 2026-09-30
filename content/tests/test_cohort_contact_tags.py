"""Every dated ``CohortEnrollment`` creation path applies the cohort tags.

A member of a dated cohort carries the course tag (the course slug) and the
cohort tag (``<course-slug>-<cohort external_key>``), so an email campaign
targeting e.g. ``ai-buildcamp-4`` reaches the whole cohort. Self-paced
cohorts are never tagged. ``maven`` stays exclusive to Maven-sourced rows
(the webhook's own tagging step), so none of these paths add it.
"""

import datetime
import json
import uuid

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from accounts.models import EmailAlias, Token
from content.admin.cohort import CohortEnrollmentAdmin
from content.models import Cohort, CohortEnrollment, Course
from content.models.enrollment import SOURCE_ADMIN, Enrollment
from content.models.homework import Homework
from content.services.course_cohorts import (
    apply_cohort_enrollment_tags,
    cohort_contact_tags,
)
from content.services.homework_submissions import save_submission
from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import retry_occurrence_step

User = get_user_model()


class CohortTagFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='AI Buildcamp', slug='ai-buildcamp', status='published',
            required_level=0, maven_course_key='from-rag-to-agents',
        )
        today = timezone.localdate()
        cls.cohort3 = Cohort.objects.create(
            course=cls.course, name='Cohort 3', external_key='3',
            start_date=today - datetime.timedelta(days=200),
            end_date=today - datetime.timedelta(days=100),
        )
        cls.cohort4 = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='4',
            start_date=today - datetime.timedelta(days=7),
            end_date=today + datetime.timedelta(days=60),
        )
        cls.self_paced = Cohort.objects.create(
            course=cls.course, name='Self-paced', mode='self_paced',
        )
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
            is_superuser=True,
        )

    def _learner(self, email='learner@test.com', tags=None):
        return User.objects.create_user(
            email=email, password='pw', tags=list(tags or []),
        )

    def _tags(self, user):
        user.refresh_from_db()
        return list(user.tags)

    def _relation_slugs(self, user):
        return set(
            User.objects.filter(pk=user.pk).values_list(
                'member_extra__contact_tags__slug', flat=True,
            )
        )


class CohortContactTagRuleTest(CohortTagFixtureMixin, TestCase):
    def test_dated_cohort_gets_course_and_cohort_tag(self):
        self.assertEqual(
            cohort_contact_tags(self.cohort4), ['ai-buildcamp', 'ai-buildcamp-4'],
        )

    def test_self_paced_cohort_gets_no_tags(self):
        self.assertEqual(cohort_contact_tags(self.self_paced), [])

    def test_cohort_without_external_key_gets_course_tag_only(self):
        today = timezone.localdate()
        cohort = Cohort.objects.create(
            course=self.course, name='Unkeyed', external_key='',
            start_date=today, end_date=today + datetime.timedelta(days=10),
        )
        self.assertEqual(cohort_contact_tags(cohort), ['ai-buildcamp'])

    def test_apply_is_additive_and_idempotent(self):
        user = self._learner(tags=['vip'])

        added = apply_cohort_enrollment_tags(user, self.cohort4)
        again = apply_cohort_enrollment_tags(user, self.cohort4)

        self.assertEqual(added, ['ai-buildcamp', 'ai-buildcamp-4'])
        self.assertEqual(again, [])
        self.assertEqual(self._tags(user), ['vip', 'ai-buildcamp', 'ai-buildcamp-4'])
        # Campaign audiences read the relation, not the JSON column.
        self.assertEqual(
            self._relation_slugs(user), {'vip', 'ai-buildcamp', 'ai-buildcamp-4'},
        )


class StaffApiCohortTagTest(CohortTagFixtureMixin, TestCase):
    def test_enroll_with_cohort_tags_the_member(self):
        token = Token.objects.create(user=self.staff, name='s')
        user = self._learner()

        response = self.client.post(
            f'/api/courses/{self.course.slug}/enrollments',
            data=json.dumps({'user_email': user.email, 'cohort': '4'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token.key}',
        )

        self.assertTrue(response.json()['cohort_enrollments'][0]['created'])
        self.assertEqual(self._tags(user), ['ai-buildcamp', 'ai-buildcamp-4'])

    def test_re_enrolling_an_existing_member_backfills_missing_tags(self):
        token = Token.objects.create(user=self.staff, name='s')
        user = self._learner()
        CohortEnrollment.objects.create(user=user, cohort=self.cohort4)

        self.client.post(
            f'/api/courses/{self.course.slug}/enrollments',
            data=json.dumps({'user_email': user.email, 'cohort': '4'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token.key}',
        )

        self.assertEqual(self._tags(user), ['ai-buildcamp', 'ai-buildcamp-4'])


class StudioCohortTagTest(CohortTagFixtureMixin, TestCase):
    def setUp(self):
        self.client.force_login(self.staff)

    def test_studio_add_with_cohort_tags_the_member(self):
        user = self._learner()

        self.client.post(
            f'/studio/courses/{self.course.pk}/enrollments/create',
            {'email': user.email, 'cohort_id': str(self.cohort4.pk)},
        )

        self.assertEqual(self._tags(user), ['ai-buildcamp', 'ai-buildcamp-4'])

    def test_change_cohort_swaps_the_cohort_tag(self):
        user = self._learner(tags=['ai-buildcamp', 'ai-buildcamp-3'])
        enrollment = Enrollment.objects.create(
            user=user, course=self.course, source=SOURCE_ADMIN,
        )
        CohortEnrollment.objects.create(user=user, cohort=self.cohort3)

        self.client.post(
            f'/studio/courses/{self.course.pk}/enrollments/{enrollment.pk}/cohort',
            {'cohort_id': str(self.cohort4.pk)},
        )

        self.assertEqual(self._tags(user), ['ai-buildcamp', 'ai-buildcamp-4'])


class LearnerSelfPickCohortTagTest(CohortTagFixtureMixin, TestCase):
    def test_learner_picking_a_cohort_is_tagged(self):
        user = self._learner()
        self.client.force_login(user)

        response = self.client.post(
            f'/api/courses/{self.course.slug}/cohorts/{self.cohort4.pk}/enroll',
        )

        self.assertEqual(response.json(), {'enrolled': True, 'cohort_id': self.cohort4.pk})
        self.assertEqual(self._tags(user), ['ai-buildcamp', 'ai-buildcamp-4'])


class HomeworkAutoEnrollCohortTagTest(CohortTagFixtureMixin, TestCase):
    def test_first_submission_auto_enrolls_and_tags(self):
        user = self._learner()
        homework = Homework.objects.create(
            cohort=self.cohort4, content_id=uuid.uuid4(), slug='hw1',
            title='Homework 1',
            due_date=timezone.now() + datetime.timedelta(days=2),
        )

        save_submission(homework, user, homework_link='', answers_by_question_id={})

        self.assertEqual(self._tags(user), ['ai-buildcamp', 'ai-buildcamp-4'])


class AdminCohortTagTest(CohortTagFixtureMixin, TestCase):
    def test_admin_save_tags_the_member(self):
        user = self._learner()
        enrollment = CohortEnrollment(user=user, cohort=self.cohort4)
        request = RequestFactory().post('/')
        request.user = self.staff

        CohortEnrollmentAdmin(CohortEnrollment, admin.site).save_model(
            request, enrollment, form=None, change=False,
        )

        self.assertTrue(CohortEnrollment.objects.filter(pk=enrollment.pk).exists())
        self.assertEqual(self._tags(user), ['ai-buildcamp', 'ai-buildcamp-4'])


class MavenEnrollmentStepCohortTagTest(CohortTagFixtureMixin, TestCase):
    def test_enrollment_step_applies_cohort_tags(self):
        user = self._learner(tags=['maven'])
        occurrence = MavenEnrollmentEvent.objects.create(
            dedupe_key='k-tags', identity_hash='k-tags', user=user,
            course_key='from-rag-to-agents', cohort_key='4',
            lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
            event_type='user_cohort.enrolled',
            override_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            enrollment_status=MavenEnrollmentEvent.STEP_PENDING,
            notification_status=MavenEnrollmentEvent.STEP_SKIPPED,
            slack_status=MavenEnrollmentEvent.STEP_SKIPPED,
            welcome_status=MavenEnrollmentEvent.STEP_SKIPPED,
        )

        retry_occurrence_step(occurrence, 'enrollment')

        self.assertTrue(
            CohortEnrollment.objects.filter(user=user, cohort=self.cohort4).exists()
        )
        self.assertEqual(self._tags(user), ['maven', 'ai-buildcamp', 'ai-buildcamp-4'])


class SelfPacedEnrollmentIsNotTaggedTest(CohortTagFixtureMixin, TestCase):
    def test_self_paced_membership_adds_no_tags(self):
        user = self._learner()
        self.assertEqual(apply_cohort_enrollment_tags(user, self.self_paced), [])
        self.assertEqual(self._tags(user), [])


class AliasResolutionOnBulkPathsTest(CohortTagFixtureMixin, TestCase):
    """A known secondary address resolves to its canonical account.

    Maven exports carry whatever address the student bought with; when that
    address is an ``EmailAlias`` the staff enroll API and the contacts
    import must reach the canonical account instead of reporting it unknown
    or creating a duplicate.
    """

    def setUp(self):
        self.token = Token.objects.create(user=self.staff, name='s')
        self.canonical = self._learner(email='main@test.com')
        EmailAlias.objects.create(user=self.canonical, email='old@test.com')

    def _auth(self):
        return {'HTTP_AUTHORIZATION': f'Token {self.token.key}'}

    def test_enroll_by_alias_enrolls_and_tags_the_canonical_account(self):
        response = self.client.post(
            f'/api/courses/{self.course.slug}/enrollments',
            data=json.dumps({'user_emails': ['Old@test.com'], 'cohort': '4'}),
            content_type='application/json',
            **self._auth(),
        )

        self.assertEqual(response.json()['unknown_emails'], [])
        self.assertTrue(CohortEnrollment.objects.filter(
            user=self.canonical, cohort=self.cohort4,
        ).exists())
        self.assertEqual(
            self._tags(self.canonical), ['ai-buildcamp', 'ai-buildcamp-4'],
        )

    def test_contacts_import_by_alias_updates_canonical_without_duplicate(self):
        response = self.client.post(
            '/api/contacts/import',
            data=json.dumps({
                'contacts': [{'email': 'old@test.com', 'tags': ['maven']}],
            }),
            content_type='application/json',
            **self._auth(),
        )

        body = response.json()
        self.assertEqual((body['created'], body['updated']), (0, 1))
        self.assertFalse(User.objects.filter(email__iexact='old@test.com').exists())
        self.assertEqual(self._tags(self.canonical), ['maven'])
