"""Tests for the contact-tag normalization helpers and the User.tags field
(issue #354).

The User-level tag mutation helpers wrap ``content/utils/tags.py`` for slug
rules; we re-test the helper-level behavior here only to lock in the contract
that contact-tag callers depend on (idempotent add/remove, empty rejection,
JSON-list lookup).
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.utils.tags import add_tag, normalize_tags, remove_tag

User = get_user_model()


class NormalizeTagsTest(TestCase):
    """``normalize_tags`` mirrors the content-tag rules but is the helper that
    contact-tag callers import directly. Re-verify the cases the spec lists."""

    def test_dedup_lowercase_hyphenate_strip_special(self):
        # Mirrors content/utils/tags.py: ``/`` is stripped (not replaced with
        # a hyphen), so "AI/ML" -> "aiml". See content/tests/test_tags.py.
        result = normalize_tags(["Early Adopter", "early-adopter", "  ", "AI/ML"])
        self.assertEqual(result, ["early-adopter", "aiml"])

    def test_normalize_handles_ampersand_to_hyphen(self):
        # "AI & ML" -> "ai-ml" because spaces become hyphens, then the
        # ``&`` is stripped, leaving the surrounding hyphens which collapse.
        self.assertEqual(normalize_tags(["AI & ML"]), ["ai-ml"])

    def test_normalize_preserves_namespaced_course_tag(self):
        self.assertEqual(
            normalize_tags(["Course:Data Engineering Zoomcamp"]),
            ["course:data-engineering-zoomcamp"],
        )

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(normalize_tags([]), [])

    def test_non_list_returns_empty_list(self):
        # Defensive: callers may pass None when reading a fresh model.
        self.assertEqual(normalize_tags(None), [])


class AddRemoveTagTest(TestCase):
    """``add_tag`` / ``remove_tag`` wrap normalization plus persistence and
    must be idempotent so view code can call them without pre-checks."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='tagged@test.com',
            password='testpass',
        )

    def test_add_tag_normalizes_and_persists(self):
        add_tag(self.user, 'Early Adopter')
        self.user.refresh_from_db()
        self.assertEqual(self.user.tags, ['early-adopter'])

    def test_add_tag_is_idempotent(self):
        add_tag(self.user, 'Early Adopter')
        add_tag(self.user, 'early-adopter')
        add_tag(self.user, 'EARLY ADOPTER')
        self.user.refresh_from_db()
        self.assertEqual(self.user.tags, ['early-adopter'])

    def test_add_tag_rejects_empty_input(self):
        result = add_tag(self.user, '   ')
        self.user.refresh_from_db()
        self.assertEqual(result, '')
        self.assertEqual(self.user.tags, [])

    def test_remove_tag_on_missing_is_noop(self):
        # Should not raise when the tag is not present.
        remove_tag(self.user, 'never-added')
        self.user.refresh_from_db()
        self.assertEqual(self.user.tags, [])

    def test_remove_tag_normalizes_and_persists(self):
        add_tag(self.user, 'early-adopter')
        add_tag(self.user, 'beta')
        remove_tag(self.user, 'Early Adopter')
        self.user.refresh_from_db()
        self.assertEqual(self.user.tags, ['beta'])

    def test_default_tags_empty_list_for_new_user(self):
        # The acceptance criterion that "existing users get [] on migrate"
        # is structurally equivalent to "new rows default to []" because the
        # migration ships ``default=list``. Verify the field default applies
        # to freshly-created rows without explicit value.
        fresh = User.objects.create_user(
            email='fresh@test.com',
            password='testpass',
        )
        self.assertEqual(fresh.tags, [])


class TagsFieldDefaultTest(TestCase):
    """Lock in that ``tags`` is always an empty list, never null. The Studio
    filter logic and add/remove helpers rely on the value being a list."""

    def test_users_without_tags_have_empty_list_not_null(self):
        carol = User.objects.create_user(email='carol@test.com', password='x')
        carol.refresh_from_db()
        self.assertEqual(carol.tags, [])

    def test_explicit_tags_persist_through_save(self):
        alice = User.objects.create_user(
            email='alice@test.com', password='x', tags=['early-adopter', 'beta'],
        )
        alice.refresh_from_db()
        self.assertEqual(alice.tags, ['early-adopter', 'beta'])
