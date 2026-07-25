"""Studio instructor account-linking list + actions (issue #1345)."""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

from content.models import (
    Course,
    Instructor,
    Module,
    Unit,
)

User = get_user_model()


@tag('core')
class StudioInstructorListTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.non_staff = User.objects.create_user(
            email='free@test.com', password='pw',
        )
        cls.ada_user = User.objects.create_user(
            email='ada@test.com', password='pw', email_verified=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )

        cls.linked = Instructor.objects.create(
            instructor_id='ada', name='Ada', email='ada@test.com',
            user=cls.ada_user,
        )
        # Unlinked with comment-bearing content -> needs_link.
        cls.dane = Instructor.objects.create(
            instructor_id='dane', name='Dane', email='dane@test.com',
        )
        course = Course.objects.create(
            title='C', slug='c', status='published',
        )
        module = Module.objects.create(
            course=course, title='M', slug='m', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=module, title='U', slug='u', sort_order=1,
            content_id=uuid.uuid4(),
        )
        course.instructors.add(cls.dane)
        # Unlinked without any comment-bearing content -> not needs_link.
        cls.eve = Instructor.objects.create(instructor_id='eve', name='Eve')

        from comments.services import create_comment
        create_comment(
            content_id=cls.unit.content_id, user=cls.member, body='q',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_non_staff_cannot_view(self):
        self.client.logout()
        self.client.login(email='free@test.com', password='pw')
        resp = self.client.get(reverse('studio_instructor_list'))
        self.assertIn(resp.status_code, (302, 403))

    def test_list_shows_all_with_link_state_and_counts(self):
        resp = self.client.get(reverse('studio_instructor_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Ada')
        self.assertContains(resp, 'ada@test.com')
        self.assertContains(resp, 'Dane')
        self.assertContains(resp, 'Not linked')

    def test_unlinked_filter_excludes_linked(self):
        resp = self.client.get(
            reverse('studio_instructor_list'), {'filter': 'unlinked'},
        )
        ids = [r['instructor'].instructor_id for r in resp.context['rows']]
        self.assertNotIn('ada', ids)
        self.assertIn('dane', ids)
        self.assertIn('eve', ids)

    def test_needs_link_filter_requires_comment_content(self):
        resp = self.client.get(
            reverse('studio_instructor_list'), {'filter': 'needs_link'},
        )
        ids = [r['instructor'].instructor_id for r in resp.context['rows']]
        self.assertIn('dane', ids)
        self.assertNotIn('eve', ids)
        self.assertNotIn('ada', ids)

    def test_empty_filter_shows_helpful_empty_state(self):
        # Link the only unlinked-with-content instructor so needs_link empties.
        self.dane.user = self.ada_user
        self.dane.save(update_fields=['user'])
        resp = self.client.get(
            reverse('studio_instructor_list'), {'filter': 'needs_link'},
        )
        self.assertEqual(len(resp.context['rows']), 0)
        self.assertContains(resp, 'highest-')


@tag('core')
class StudioInstructorLinkActionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.ada_user = User.objects.create_user(
            email='ada@test.com', password='pw', email_verified=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_link_sets_user_and_redirects_with_filter(self):
        Instructor.objects.create(instructor_id='dane', name='Dane')
        resp = self.client.post(
            reverse('studio_instructor_link', args=['dane']),
            {'user_id': self.ada_user.pk, 'filter': 'unlinked'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn('filter=unlinked', resp.url)
        self.assertEqual(
            Instructor.objects.get(instructor_id='dane').user, self.ada_user,
        )

    def test_link_without_user_id_shows_error(self):
        Instructor.objects.create(instructor_id='dane', name='Dane')
        resp = self.client.post(
            reverse('studio_instructor_link', args=['dane']),
            {'filter': 'all'},
            follow=True,
        )
        self.assertIsNone(
            Instructor.objects.get(instructor_id='dane').user_id,
        )
        self.assertContains(resp, 'Select an account')

    def test_unlink_clears_user(self):
        Instructor.objects.create(
            instructor_id='ada', name='Ada', user=self.ada_user,
        )
        resp = self.client.post(
            reverse('studio_instructor_unlink', args=['ada']),
            {'filter': 'all'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(
            Instructor.objects.get(instructor_id='ada').user_id,
        )


@tag('core')
class StudioInstructorNavTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_people_nav_links_to_instructors(self):
        resp = self.client.get(reverse('studio_instructor_list'))
        self.assertContains(resp, reverse('studio_instructor_list'))
        self.assertContains(resp, 'Instructors')
