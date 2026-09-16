"""Tests for the reusable tag-based checklist-item-visibility helper
(issue #1678).

`user_has_checklist_tag` is general audience-targeting plumbing for
future dashboard checklist items. It is not wired to any item in this
issue (the buildcamp item uses `CourseAccess`, an entitlement signal,
not a tag) — these are its own unit tests, independent of dashboard
rendering.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from content.views.home import user_has_checklist_tag

User = get_user_model()


class UserHasChecklistTagTest(TestCase):
    def test_returns_true_when_user_carries_the_tag(self):
        user = User.objects.create_user(
            email='tagged@test.com', password='testpass', tags=['early-adopter'],
        )
        self.assertTrue(user_has_checklist_tag(user, 'early-adopter'))

    def test_returns_false_when_user_does_not_carry_the_tag(self):
        user = User.objects.create_user(
            email='untagged@test.com', password='testpass', tags=['some-other-tag'],
        )
        self.assertFalse(user_has_checklist_tag(user, 'early-adopter'))

    def test_returns_false_for_user_with_no_tags(self):
        user = User.objects.create_user(email='no-tags@test.com', password='testpass')
        self.assertFalse(user_has_checklist_tag(user, 'early-adopter'))

    def test_applies_tag_normalization_to_the_queried_tag(self):
        """The literal passed in code doesn't need to match the stored
        casing/spacing exactly — normalization makes the comparison
        forgiving the same way `accounts/utils/tags.py` normalizes tags
        on write."""
        user = User.objects.create_user(
            email='normalized@test.com', password='testpass', tags=['early-adopter'],
        )
        self.assertTrue(user_has_checklist_tag(user, 'Early Adopter'))
        self.assertTrue(user_has_checklist_tag(user, '  early_adopter  '))

    def test_returns_false_for_blank_or_unnormalizable_tag(self):
        user = User.objects.create_user(
            email='blank-tag@test.com', password='testpass', tags=['early-adopter'],
        )
        self.assertFalse(user_has_checklist_tag(user, ''))
        self.assertFalse(user_has_checklist_tag(user, None))
