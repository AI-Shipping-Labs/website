"""Tests for issue #492 access management improvements.

Covers:
- Grant access by autocomplete-selected user_id (preserves email path)
- Both desktop table and mobile cards render access records
- Mobile revoke action is present and uses POST + CSRF
- Course edit page shows access + active enrollment counts in workflow panel
- Mobile revoke button has min tappable height
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag

from content.models import Course, CourseAccess, Enrollment

User = get_user_model()


@tag('core')
class StudioCourseAccessGrantByUserIdTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.course = Course.objects.create(
            title='Grant Course', slug='grant-course-uid', status='published',
        )
        cls.target = User.objects.create_user(
            email='target@test.com', password='pw',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_grant_by_user_id_creates_record(self):
        self.client.post(
            f'/studio/courses/{self.course.pk}/access/grant/',
            {'user_id': str(self.target.pk), 'email': ''},
        )
        self.assertTrue(
            CourseAccess.objects.filter(
                user=self.target, course=self.course, access_type='granted',
            ).exists()
        )

    def test_grant_by_user_id_wins_over_email(self):
        """If both are supplied, user_id is authoritative."""
        other = User.objects.create_user(email='other@test.com', password='pw')
        self.client.post(
            f'/studio/courses/{self.course.pk}/access/grant/',
            {'user_id': str(self.target.pk), 'email': 'other@test.com'},
        )
        self.assertTrue(
            CourseAccess.objects.filter(user=self.target, course=self.course).exists()
        )
        self.assertFalse(
            CourseAccess.objects.filter(user=other, course=self.course).exists()
        )

    def test_grant_by_user_id_unknown_id_shows_error(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/access/grant/',
            {'user_id': '99999999', 'email': ''},
            follow=True,
        )
        self.assertContains(response, 'Selected user no longer exists')

    def test_grant_by_email_still_works(self):
        """Keyboard-fast path: typing a known email and submitting still grants."""
        self.client.post(
            f'/studio/courses/{self.course.pk}/access/grant/',
            {'email': 'target@test.com'},
        )
        self.assertTrue(
            CourseAccess.objects.filter(user=self.target, course=self.course).exists()
        )

    def test_grant_by_email_is_case_insensitive(self):
        self.client.post(
            f'/studio/courses/{self.course.pk}/access/grant/',
            {'email': 'TARGET@test.com'},
        )
        self.assertTrue(
            CourseAccess.objects.filter(user=self.target, course=self.course).exists()
        )


@tag('core')
class StudioCourseEnrollmentCreateByUserIdTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.course = Course.objects.create(
            title='Enroll Course', slug='enroll-course-uid', status='published',
        )
        cls.target = User.objects.create_user(
            email='target@enroll.com', password='pw',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_enroll_by_user_id_creates_enrollment(self):
        self.client.post(
            f'/studio/courses/{self.course.pk}/enrollments/create',
            {'user_id': str(self.target.pk), 'email': ''},
        )
        self.assertTrue(
            Enrollment.objects.filter(
                user=self.target, course=self.course, unenrolled_at__isnull=True,
            ).exists()
        )

    def test_enroll_by_email_still_works(self):
        self.client.post(
            f'/studio/courses/{self.course.pk}/enrollments/create',
            {'email': 'target@enroll.com'},
        )
        self.assertTrue(
            Enrollment.objects.filter(user=self.target, course=self.course).exists()
        )


@tag('core')
class StudioCourseAccessListMobileRenderTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.course = Course.objects.create(
            title='Mobile Course', slug='mobile-course', status='published',
        )
        cls.user_a = User.objects.create_user(email='a@test.com', password='pw')
        cls.user_b = User.objects.create_user(email='b@test.com', password='pw')
        CourseAccess.objects.create(
            user=cls.user_a, course=cls.course,
            access_type='granted', granted_by=cls.staff,
        )
        CourseAccess.objects.create(
            user=cls.user_b, course=cls.course, access_type='purchased',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_mobile_cards_block_present(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        # Mobile-only block uses md:hidden class
        self.assertContains(response, 'data-testid="access-cards"')

    def test_desktop_table_block_present(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        # Desktop block hidden on small screens via hidden md:block
        content = response.content.decode()
        self.assertIn('hidden md:block', content)

    def test_mobile_card_lists_each_record(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        # Two access records should produce two access-card list items
        self.assertEqual(response.content.decode().count('data-testid="access-card"'), 2)

    def test_user_id_shown_in_listing(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        self.assertContains(response, f'ID: {self.user_a.pk}')
        self.assertContains(response, f'ID: {self.user_b.pk}')

    def test_mobile_revoke_button_present_for_granted(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        self.assertContains(response, 'data-testid="revoke-btn-mobile"')
        # Mobile revoke is wrapped in a POST form with CSRF token (CSRF preserved)
        content = response.content.decode()
        self.assertIn('csrfmiddlewaretoken', content)

    def test_mobile_revoke_uses_min_tap_target(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        # 44px tap target via min-h-[44px] utility on mobile revoke
        content = response.content.decode()
        self.assertIn('min-h-[44px]', content)

    def test_purchased_access_explains_non_revocable(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        self.assertContains(response, 'Purchased access cannot be revoked')

    def test_lookup_form_has_search_url_and_hidden_user_id(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        expected_url = f'/studio/courses/{self.course.pk}/access/users/search/'
        self.assertContains(response, expected_url)
        self.assertContains(response, 'name="user_id"')


@tag('core')
class StudioEnrollmentsListMobileRenderTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.course = Course.objects.create(
            title='Mobile Enroll Course', slug='mobile-enroll', status='published',
        )
        cls.user_a = User.objects.create_user(email='ea@test.com', password='pw')
        Enrollment.objects.create(
            user=cls.user_a, course=cls.course, source='admin',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_mobile_cards_block_present(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/enrollments/')
        self.assertContains(response, 'data-testid="enrollment-cards"')

    def test_mobile_unenroll_button_present(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/enrollments/')
        self.assertContains(response, 'data-testid="unenroll-row-btn-mobile"')

    def test_mobile_unenroll_uses_min_tap_target(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/enrollments/')
        content = response.content.decode()
        self.assertIn('min-h-[44px]', content)

    def test_user_id_shown_in_enrollments(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/enrollments/')
        self.assertContains(response, f'ID: {self.user_a.pk}')

    def test_enrollments_form_has_search_url(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/enrollments/')
        expected_url = f'/studio/courses/{self.course.pk}/access/users/search/'
        self.assertContains(response, expected_url)
        self.assertContains(response, 'name="user_id"')


@tag('core')
class StudioCourseEditCountsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.course = Course.objects.create(
            title='Counts Course', slug='counts-course', status='draft',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    def test_edit_shows_zero_counts_when_no_records(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')
        self.assertEqual(response.context['access_count'], 0)
        self.assertEqual(response.context['active_enrollment_count'], 0)
        self.assertContains(response, 'data-testid="panel-access-count"')
        self.assertContains(response, 'data-testid="panel-enrollment-count"')

    def test_edit_shows_access_and_enrollment_counts(self):
        u1 = User.objects.create_user(email='c1@test.com', password='pw')
        u2 = User.objects.create_user(email='c2@test.com', password='pw')
        u3 = User.objects.create_user(email='c3@test.com', password='pw')
        CourseAccess.objects.create(
            user=u1, course=self.course, access_type='granted', granted_by=self.staff,
        )
        CourseAccess.objects.create(
            user=u2, course=self.course, access_type='purchased',
        )
        Enrollment.objects.create(user=u3, course=self.course, source='admin')

        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')
        self.assertEqual(response.context['access_count'], 2)
        self.assertEqual(response.context['active_enrollment_count'], 1)

    def test_unenrolled_does_not_count_as_active(self):
        u1 = User.objects.create_user(email='u1@test.com', password='pw')
        from django.utils import timezone
        Enrollment.objects.create(
            user=u1, course=self.course, source='admin',
            unenrolled_at=timezone.now(),
        )
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')
        self.assertEqual(response.context['active_enrollment_count'], 0)
