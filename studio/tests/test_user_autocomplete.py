"""Autocomplete suppression for Studio user-selection flows (issue #476)."""

import datetime
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.views.auth import _generate_password_reset_token
from content.models import Course, Module, Unit
from plans.models import Sprint

User = get_user_model()


class _ControlParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.controls = []

    def handle_starttag(self, tag, attrs):
        if tag in {'form', 'input', 'select', 'textarea'}:
            self.controls.append((tag, dict(attrs)))


def _controls(response):
    parser = _ControlParser()
    parser.feed(response.content.decode())
    return parser.controls


def _find_control(response, tag, **expected_attrs):
    for control_tag, attrs in _controls(response):
        if control_tag != tag:
            continue
        if all(attrs.get(name) == value for name, value in expected_attrs.items()):
            return attrs
    raise AssertionError(f'Could not find <{tag}> with {expected_attrs!r}')


def _make_course(slug):
    course = Course.objects.create(
        title=f'Course {slug}',
        slug=slug,
        status='published',
    )
    module = Module.objects.create(
        course=course,
        title='Module',
        slug=f'{slug}-m',
    )
    Unit.objects.create(
        module=module,
        title='Unit',
        slug=f'{slug}-u',
        sort_order=0,
    )
    return course


class StudioUserAutocompleteSuppressionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='pw',
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com',
            password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='May 2026',
            slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.course = _make_course('autocomplete')

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_user_search_input_suppresses_credential_autocomplete(self):
        response = self.client.get('/studio/users/')

        search = _find_control(response, 'input', name='q')
        self.assertEqual(search.get('autocomplete'), 'off')
        self.assertTrue(
            any(
                tag == 'form' and attrs.get('autocomplete') == 'off'
                for tag, attrs in _controls(response)
            )
        )

    def test_add_member_select_suppresses_credential_autocomplete(self):
        response = self.client.get(
            f'/studio/sprints/{self.sprint.pk}/add-member',
        )

        member = _find_control(response, 'select', name='member')
        self.assertEqual(member.get('autocomplete'), 'off')
        self.assertEqual(member.get('data-testid'), 'add-member-select')
        self.assertEqual(_find_control(response, 'form').get('autocomplete'), 'off')

    def test_new_plan_selectors_suppress_credential_autocomplete(self):
        response = self.client.get('/studio/plans/new')

        self.assertEqual(
            _find_control(response, 'select', name='member').get('autocomplete'),
            'off',
        )
        self.assertEqual(
            _find_control(response, 'select', name='sprint').get('autocomplete'),
            'off',
        )
        self.assertEqual(
            _find_control(response, 'select', name='status').get('autocomplete'),
            'off',
        )
        self.assertEqual(_find_control(response, 'form').get('autocomplete'), 'off')

    def test_bulk_enrollment_textarea_suppresses_credential_autocomplete(self):
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/enroll')

        emails = _find_control(response, 'textarea', name='emails')
        self.assertEqual(emails.get('autocomplete'), 'off')
        self.assertEqual(emails.get('data-testid'), 'bulk-enroll-emails')
        self.assertEqual(_find_control(response, 'form').get('autocomplete'), 'off')

    def test_existing_user_tag_input_keeps_autocomplete_suppressed(self):
        response = self.client.get(f'/studio/users/{self.member.pk}/')

        tag = _find_control(response, 'input', name='tag')
        self.assertEqual(tag.get('autocomplete'), 'off')
        self.assertEqual(tag.get('data-testid'), 'user-tag-input')

    def test_plan_list_user_filters_suppress_credential_autocomplete(self):
        response = self.client.get('/studio/plans/')

        self.assertEqual(
            _find_control(response, 'input', name='member').get('autocomplete'),
            'off',
        )
        self.assertEqual(
            _find_control(response, 'input', name='q').get('autocomplete'),
            'off',
        )
        self.assertEqual(
            _find_control(response, 'select', name='sprint').get('autocomplete'),
            'off',
        )
        self.assertEqual(
            _find_control(response, 'select', name='status').get('autocomplete'),
            'off',
        )

    def test_user_grant_and_enrollment_email_fields_suppress_autocomplete(self):
        access = self.client.get(f'/studio/courses/{self.course.pk}/access/')
        enrollments = self.client.get(
            f'/studio/courses/{self.course.pk}/enrollments/',
        )
        tier_override = self.client.get('/studio/tier_overrides/')

        for response in (access, enrollments):
            email = _find_control(response, 'input', name='email')
            self.assertEqual(email.get('autocomplete'), 'off')

        search = _find_control(
            tier_override,
            'input',
            id='tier-override-user-search',
        )
        self.assertEqual(search.get('autocomplete'), 'off')

class PublicAuthAutocompleteGuardTest(TestCase):
    """Public auth forms must not inherit Studio autocomplete suppression."""

    def _assert_allowed_autocomplete(self, response, control_id, allowed):
        attrs = _find_control(response, 'input', id=control_id)
        self.assertIn(attrs.get('autocomplete'), allowed)
        self.assertNotEqual(attrs.get('autocomplete'), 'off')

    def test_login_form_keeps_credential_appropriate_autocomplete(self):
        response = self.client.get('/accounts/login/')

        self._assert_allowed_autocomplete(response, 'login-email', {None, 'email'})
        self._assert_allowed_autocomplete(
            response,
            'login-password',
            {None, 'current-password'},
        )

    def test_register_form_keeps_credential_appropriate_autocomplete(self):
        response = self.client.get('/accounts/register/')

        self._assert_allowed_autocomplete(response, 'register-email', {None, 'email'})
        self._assert_allowed_autocomplete(
            response,
            'register-password',
            {None, 'new-password'},
        )
        self._assert_allowed_autocomplete(
            response,
            'register-password-confirm',
            {None, 'new-password'},
        )

    def test_password_reset_form_keeps_credential_appropriate_autocomplete(self):
        user = User.objects.create_user(email='reset@test.com', password='pw')
        token = _generate_password_reset_token(user.pk)

        response = self.client.get(f'/api/password-reset?token={token}')

        self._assert_allowed_autocomplete(
            response,
            'new-password',
            {None, 'new-password'},
        )
        self._assert_allowed_autocomplete(
            response,
            'confirm-password',
            {None, 'new-password'},
        )
