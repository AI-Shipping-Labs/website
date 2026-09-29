"""Staff Open in Studio destinations on public course pages."""

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, tag
from django.urls import reverse
from django.utils import timezone

from content.models import (
    Cohort,
    CohortEnrollment,
    Course,
    Homework,
    Module,
    PeerReview,
    ProjectSubmission,
    Unit,
)
from content.models.course import UNIT_KIND_EVENT, UNIT_KIND_HOMEWORK
from content.services.studio_open import studio_open_url

User = get_user_model()

STUDIO_BUTTON = 'data-testid="studio-edit-button"'


def _button_href(response):
    html = response.content.decode()
    marker = STUDIO_BUTTON
    count = html.count(marker)
    if count != 1:
        raise AssertionError(f'expected one studio button, found {count}')
    index = html.index(marker)
    start = html.rfind('<a ', 0, index)
    end = html.find('>', index)
    tag = html[start:end]
    href_at = tag.find('href="')
    if href_at < 0:
        raise AssertionError(f'button has no href: {tag}')
    href_at += len('href="')
    return tag[href_at:tag.find('"', href_at)]


@tag('core')
class StudioOpenCoursePageTest(TestCase):
    """Course pages open the operator surface for the section on screen."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='open-studio-staff@test.com', password='pw', is_staff=True,
        )
        cls.course = Course.objects.create(
            title='Open Studio Course',
            slug='open-studio-course',
            status='published',
            required_level=0,
        )

    def setUp(self):
        self.client.login(email='open-studio-staff@test.com', password='pw')

    def test_course_detail_opens_homework_list(self):
        response = self.client.get(f'/courses/{self.course.slug}')
        self.assertEqual(response.content.decode().count(STUDIO_BUTTON), 1)
        self.assertEqual(
            _button_href(response),
            f'/studio/courses/{self.course.pk}/homeworks',
        )
        self.assertContains(response, 'aria-label="Open in Studio"')
        self.assertContains(response, '>Open in Studio</span>')

    def test_anonymous_course_detail_hides_studio(self):
        self.client.logout()
        response = self.client.get(f'/courses/{self.course.slug}')
        self.assertNotContains(response, STUDIO_BUTTON)
        self.assertNotContains(response, '/studio/')

    def test_course_home_opens_homework_list(self):
        response = self.client.get(f'/courses/{self.course.slug}/home')
        self.assertEqual(
            _button_href(response),
            reverse('studio_homework_list', kwargs={'course_id': self.course.pk}),
        )

    def test_course_home_homework_opens_homework_list(self):
        response = self.client.get(f'/courses/{self.course.slug}/home/homework')
        self.assertEqual(
            _button_href(response),
            reverse('studio_homework_list', kwargs={'course_id': self.course.pk}),
        )

    def test_course_home_projects_opens_peer_reviews(self):
        response = self.client.get(f'/courses/{self.course.slug}/home/projects')
        self.assertEqual(
            _button_href(response),
            reverse(
                'studio_peer_review_management',
                kwargs={'course_id': self.course.pk},
            ),
        )

    def test_course_home_sessions_opens_cohort_list(self):
        # Live sessions belong to a dated cohort; a self-paced view has none.
        today = timezone.localdate()
        Cohort.objects.create(
            course=self.course, name='Dated', external_key='dated',
            start_date=today, end_date=today + timedelta(days=30),
        )
        response = self.client.get(f'/courses/{self.course.slug}/home/sessions?cohort=dated')
        self.assertEqual(
            _button_href(response),
            reverse(
                'studio_course_cohort_list',
                kwargs={'course_id': self.course.pk},
            ),
        )

    def test_course_home_syllabus_opens_course_editor(self):
        response = self.client.get(f'/courses/{self.course.slug}/home/syllabus')
        self.assertEqual(
            _button_href(response),
            reverse('studio_course_edit', kwargs={'course_id': self.course.pk}),
        )

    def test_module_overview_opens_course_editor(self):
        module = Module.objects.create(
            course=self.course, title='Module', slug='module', sort_order=1,
        )
        response = self.client.get(f'/courses/{self.course.slug}/{module.slug}')
        self.assertEqual(response.content.decode().count(STUDIO_BUTTON), 1)
        self.assertEqual(
            _button_href(response),
            reverse('studio_course_edit', kwargs={'course_id': self.course.pk}),
        )

    def test_project_pages_open_peer_reviews(self):
        self.course.peer_review_enabled = True
        author = User.objects.create_user(
            email='open-studio-author@test.com', password='pw',
        )
        submission = ProjectSubmission.objects.create(
            user=author, course=self.course, project_url='https://example.com/project',
        )
        PeerReview.objects.create(submission=submission, reviewer=self.staff)
        peer_reviews = reverse(
            'studio_peer_review_management', kwargs={'course_id': self.course.pk},
        )
        for path in (
            f'/courses/{self.course.slug}/submit',
            f'/courses/{self.course.slug}/reviews',
            f'/courses/{self.course.slug}/reviews/{submission.pk}',
        ):
            response = self.client.get(path)
            self.assertEqual(_button_href(response), peer_reviews, path)


@tag('core')
class StudioOpenUnitPageTest(TestCase):
    """Homework units open submissions; lessons stay on the unit editor."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='open-studio-unit-staff@test.com', password='pw', is_staff=True,
        )
        cls.course = Course.objects.create(
            title='Open Studio Units',
            slug='open-studio-units',
            status='published',
            required_level=0,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Module', slug='module', sort_order=1,
        )
        cls.content_id = uuid.uuid4()
        cls.homework_unit = Unit.objects.create(
            module=cls.module,
            title='Homework',
            slug='homework',
            sort_order=1,
            kind=UNIT_KIND_HOMEWORK,
            content_id=cls.content_id,
            is_preview=True,
        )
        cls.lesson = Unit.objects.create(
            module=cls.module,
            title='Lesson',
            slug='lesson',
            sort_order=2,
            is_preview=True,
        )
        cls.event = Unit.objects.create(
            module=cls.module,
            title='Live session',
            slug='live-session',
            sort_order=3,
            kind=UNIT_KIND_EVENT,
            is_preview=True,
        )
        today = timezone.localdate()
        cls.cohort = Cohort.objects.create(
            course=cls.course,
            name='Enrolled cohort',
            start_date=today - timedelta(days=7),
            end_date=today + timedelta(days=30),
        )
        CohortEnrollment.objects.create(cohort=cls.cohort, user=cls.staff)
        cls.homework = Homework.objects.create(
            cohort=cls.cohort,
            slug='homework',
            title='Homework',
            content_id=cls.content_id,
        )

    def setUp(self):
        self.client.login(email='open-studio-unit-staff@test.com', password='pw')

    def test_homework_unit_opens_submissions(self):
        response = self.client.get(
            f'/courses/{self.course.slug}/{self.module.slug}/{self.homework_unit.slug}',
        )
        self.assertEqual(
            _button_href(response),
            f'/studio/homeworks/{self.homework.pk}/submissions',
        )
        self.assertNotIn(
            f'/studio/units/{self.homework_unit.pk}/edit',
            response.content.decode(),
        )

    def test_lesson_unit_opens_unit_editor(self):
        response = self.client.get(
            f'/courses/{self.course.slug}/{self.module.slug}/{self.lesson.slug}',
        )
        self.assertEqual(
            _button_href(response),
            f'/studio/units/{self.lesson.pk}/edit',
        )

    def test_event_unit_opens_unit_editor(self):
        response = self.client.get(
            f'/courses/{self.course.slug}/{self.module.slug}/{self.event.slug}',
        )
        self.assertEqual(
            _button_href(response),
            f'/studio/units/{self.event.pk}/edit',
        )

    def test_unenrolled_staff_opens_the_homework_list(self):
        CohortEnrollment.objects.filter(user=self.staff, cohort=self.cohort).delete()
        response = self.client.get(
            f'/courses/{self.course.slug}/{self.module.slug}/{self.homework_unit.slug}',
        )
        self.assertEqual(
            _button_href(response),
            f'/studio/courses/{self.course.pk}/homeworks',
        )

    def test_cohort_preview_does_not_open_the_other_cohort(self):
        other = Cohort.objects.create(
            course=self.course,
            name='Other cohort',
            start_date=timezone.localdate() - timedelta(days=1),
            end_date=timezone.localdate() + timedelta(days=20),
            external_key='other-cohort',
        )
        other_homework = Homework.objects.create(
            cohort=other,
            slug='other-homework',
            title='Other homework',
            content_id=self.content_id,
        )
        request = RequestFactory().get('/?cohort=other-cohort')
        request.user = self.staff
        self.assertEqual(
            studio_open_url(request, self.homework_unit),
            f'/studio/homeworks/{self.homework.pk}/submissions',
        )
        self.assertNotEqual(
            studio_open_url(request, self.homework_unit),
            f'/studio/homeworks/{other_homework.pk}/submissions',
        )
