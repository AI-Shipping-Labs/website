from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from accounts.utils.user_checks import is_authenticated_user, is_staff_user

User = get_user_model()


class UserChecksTest(TestCase):
    def test_none_is_not_authenticated_or_staff(self):
        self.assertFalse(is_authenticated_user(None))
        self.assertFalse(is_staff_user(None))

    def test_anonymous_user_is_not_authenticated_or_staff(self):
        user = AnonymousUser()

        self.assertFalse(is_authenticated_user(user))
        self.assertFalse(is_staff_user(user))

    def test_authenticated_regular_user_is_not_staff(self):
        user = User.objects.create_user(email='member@test.com')

        self.assertTrue(is_authenticated_user(user))
        self.assertFalse(is_staff_user(user))

    def test_authenticated_staff_user_is_staff(self):
        user = User.objects.create_user(email='staff@test.com', is_staff=True)

        self.assertTrue(is_authenticated_user(user))
        self.assertTrue(is_staff_user(user))
