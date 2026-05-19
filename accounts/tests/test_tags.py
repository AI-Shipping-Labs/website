"""Tests for the contact-tag normalization helpers and the User.tags field."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.utils.tags import (
    add_tag,
    count_users_with_tag,
    delete_tag,
    list_all_tags,
    normalize_tags,
    remove_tag,
    rename_tag,
)

User = get_user_model()


class NormalizeTagsTest(TestCase):
    """Lock in the contact-tag slug rules used by import and Studio callers."""

    def test_dedup_lowercase_hyphenate_strip_special(self):
        # Mirrors content/utils/tags.py: ``/`` is stripped (not replaced with
        # a hyphen), so "AI/ML" -> "aiml". See content/tests/test_tags.py.
        result = normalize_tags(["Early Adopter", "early-adopter", "  ", "AI/ML"])
        self.assertEqual(result, ["early-adopter", "aiml"])

    def test_normalize_handles_ampersand_to_hyphen(self):
        # "AI & ML" -> "ai-ml" because spaces become hyphens, then the
        # ``&`` is stripped, leaving the surrounding hyphens which collapse.
        self.assertEqual(normalize_tags(["AI & ML"]), ["ai-ml"])

    def test_normalize_preserves_source_namespace_colon(self):
        self.assertEqual(normalize_tags(["Stripe:Active"]), ["stripe:active"])

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


class RenameTagTest(TestCase):
    """``rename_tag`` rewrites a tag across every user that carries it."""

    def setUp(self):
        # Tags are stored already-normalized (callers use add_tag); the
        # rename helper relies on that convention to keep the JSONField
        # comparison portable across sqlite / postgres.
        self.alice = User.objects.create_user(
            email='alice@test.com', password='x',
            tags=['paid-user', 'beta'],
        )
        self.bob = User.objects.create_user(
            email='bob@test.com', password='x',
            tags=['paid-user'],
        )
        self.carol = User.objects.create_user(
            email='carol@test.com', password='x',
            tags=['lapsed'],
        )

    def test_rename_propagates_to_every_user(self):
        result = rename_tag('paid_user', 'paid')
        # The OLD argument is normalized to 'paid-user' which matches the
        # stored slug on Alice and Bob.
        self.assertEqual(result['affected'], 2)
        self.assertEqual(result['old'], 'paid-user')
        self.assertEqual(result['new'], 'paid')
        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.carol.refresh_from_db()
        self.assertIn('paid', self.alice.tags)
        self.assertNotIn('paid-user', self.alice.tags)
        self.assertEqual(self.bob.tags, ['paid'])
        # Other users are untouched.
        self.assertEqual(self.carol.tags, ['lapsed'])

    def test_rename_dedupes_when_target_already_present(self):
        # Alice already has 'paid' and 'paid-user'; renaming should
        # collapse her chip list, not produce a duplicate. Both Alice
        # and Bob carry 'paid-user' in setUp, so the affected count is
        # 2; the deduplication shows up in Alice's resulting tag list.
        self.alice.tags = ['paid', 'paid-user']
        self.alice.save(update_fields=['tags'])
        result = rename_tag('paid-user', 'paid')
        self.assertEqual(result['affected'], 2)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.tags, ['paid'])
        self.bob.refresh_from_db()
        self.assertEqual(self.bob.tags, ['paid'])

    def test_rename_to_empty_raises_value_error(self):
        with self.assertRaises(ValueError):
            rename_tag('paid-user', '   ')

    def test_rename_to_only_special_chars_raises(self):
        with self.assertRaises(ValueError):
            rename_tag('paid-user', '!!!')

    def test_rename_same_name_is_noop(self):
        # ``Paid User`` normalizes to ``paid-user``; same slug -> no-op.
        result = rename_tag('paid-user', 'Paid User')
        self.assertEqual(result['affected'], 0)
        self.assertEqual(result['old'], 'paid-user')
        self.assertEqual(result['new'], 'paid-user')
        self.alice.refresh_from_db()
        # Alice's existing 'paid-user' stays.
        self.assertIn('paid-user', self.alice.tags)

    def test_rename_normalizes_both_arguments(self):
        result = rename_tag('Paid User', 'Subscriber Active')
        self.assertEqual(result['old'], 'paid-user')
        self.assertEqual(result['new'], 'subscriber-active')
        self.alice.refresh_from_db()
        self.assertIn('subscriber-active', self.alice.tags)
        self.assertNotIn('paid-user', self.alice.tags)

    def test_rename_unknown_tag_is_noop(self):
        result = rename_tag('does-not-exist', 'something')
        self.assertEqual(result['affected'], 0)


class DeleteTagTest(TestCase):
    """``delete_tag`` removes a tag from every user that carries it."""

    def setUp(self):
        self.alice = User.objects.create_user(
            email='alice@test.com', password='x',
            tags=['early-adopter', 'beta'],
        )
        self.bob = User.objects.create_user(
            email='bob@test.com', password='x',
            tags=['early-adopter'],
        )
        self.carol = User.objects.create_user(
            email='carol@test.com', password='x',
            tags=['lapsed'],
        )

    def test_delete_removes_from_every_user(self):
        result = delete_tag('early-adopter')
        self.assertEqual(result['affected'], 2)
        self.assertEqual(result['name'], 'early-adopter')
        self.alice.refresh_from_db()
        self.bob.refresh_from_db()
        self.carol.refresh_from_db()
        self.assertEqual(self.alice.tags, ['beta'])
        self.assertEqual(self.bob.tags, [])
        self.assertEqual(self.carol.tags, ['lapsed'])

    def test_delete_normalizes_input(self):
        result = delete_tag('Early Adopter')
        self.assertEqual(result['affected'], 2)
        self.alice.refresh_from_db()
        self.assertNotIn('early-adopter', self.alice.tags)

    def test_delete_empty_is_noop(self):
        result = delete_tag('   ')
        self.assertEqual(result, {'affected': 0, 'name': ''})

    def test_delete_unknown_tag_is_noop(self):
        result = delete_tag('does-not-exist')
        self.assertEqual(result['affected'], 0)


class ListAllTagsTest(TestCase):
    """``list_all_tags`` returns the sorted union across all users."""

    def test_lists_sorted_union(self):
        User.objects.create_user(
            email='a@test.com', password='x', tags=['zeta', 'alpha'],
        )
        User.objects.create_user(
            email='b@test.com', password='x', tags=['alpha', 'beta'],
        )
        self.assertEqual(list_all_tags(), ['alpha', 'beta', 'zeta'])

    def test_normalizes_legacy_rows(self):
        # If a row pre-dates the normalize_tag helper, the list helper
        # should still return normalized slugs.
        User.objects.create_user(
            email='legacy@test.com', password='x', tags=['Early Adopter'],
        )
        self.assertEqual(list_all_tags(), ['early-adopter'])

    def test_skips_users_without_tags(self):
        User.objects.create_user(email='empty@test.com', password='x')
        self.assertEqual(list_all_tags(), [])


class CountUsersWithTagTest(TestCase):
    """``count_users_with_tag`` reports the live user count."""

    def test_counts_users_carrying_normalized_tag(self):
        User.objects.create_user(
            email='a@test.com', password='x', tags=['paid'],
        )
        User.objects.create_user(
            email='b@test.com', password='x', tags=['paid', 'beta'],
        )
        User.objects.create_user(email='c@test.com', password='x')
        self.assertEqual(count_users_with_tag('paid'), 2)
        self.assertEqual(count_users_with_tag('beta'), 1)
        self.assertEqual(count_users_with_tag('Paid'), 2)

    def test_empty_input_returns_zero(self):
        User.objects.create_user(
            email='a@test.com', password='x', tags=['paid'],
        )
        self.assertEqual(count_users_with_tag(''), 0)
