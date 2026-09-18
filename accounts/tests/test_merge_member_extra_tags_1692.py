"""A merge must clear the retired account's contact tags (issue #1692).

A3.2 moved the authoritative contact-tag relation from ``User.contact_tags`` to
``accounts_ext.MemberExtra.contact_tags`` and made every read in
``accounts/utils/tags.py`` go through it. ``_repoint_relations`` walks
``User._meta.get_fields()``, so it clears the legacy relation and cannot reach
the authoritative one; ``_strategy_member_extra`` keeps the ``MemberExtra`` row
and therefore owns its contents.

Before the fix, a merged-away secondary kept its tags in the relation everything
now reads: ``tags_with_user_counts()`` reported 2 carriers where the legacy
relation -- and origin/main -- reported 1.

The expand window is the second half of the contract: ``User.contact_tags``
still exists and is dual-written, so an old image and a new image must agree
about who carries which tag, on BOTH accounts, after the merge.
"""

from django.contrib.auth import get_user_model
from django.db.models import Count
from django.test import TestCase

from accounts.services.account_merge import merge_accounts
from accounts.utils.tags import (
    count_users_with_tag,
    set_tags,
    tags_with_user_counts,
    user_ids_with_exact_tag,
)
from accounts_ext.models import ContactTag, MemberExtra

User = get_user_model()


def _authoritative_slugs(user):
    """Slugs on ``accounts_ext.MemberExtra.contact_tags``."""
    return sorted(
        MemberExtra.for_user(user).contact_tags.values_list("slug", flat=True)
    )


def _legacy_slugs(user):
    """Slugs on the deprecated ``User.contact_tags``, still dual-written."""
    return sorted(user.contact_tags.values_list("slug", flat=True))


def _legacy_counts():
    """``tags_with_user_counts()`` read through the legacy relation instead.

    The expand-window equivalence check: the pre-A3.2 image computes this, the
    post-A3.2 image computes ``tags_with_user_counts()``, and after a merge the
    two must return the same rows.
    """
    rows = (
        ContactTag.objects.annotate(user_count=Count("users", distinct=True))
        .filter(user_count__gt=0)
        .order_by("slug")
        .values("slug", "user_count")
    )
    return [{"name": row["slug"], "user_count": row["user_count"]} for row in rows]


class MergeClearsRetiredContactTagsTest(TestCase):
    """The exact scenario the defect report reproduced side by side."""

    def setUp(self):
        self.canonical = User.objects.create_user(
            email="keep-1692@test.com", password="pw",
        )
        self.secondary = User.objects.create_user(
            email="dupe-1692@test.com", password="pw",
        )
        set_tags(self.canonical, ["alpha", "beta"])
        set_tags(self.secondary, ["alpha", "gamma"])

    def _merge(self):
        plan = merge_accounts(
            self.canonical, self.secondary, actor_label="test-1692",
        )
        self.canonical.refresh_from_db()
        self.secondary.refresh_from_db()
        return plan

    def test_secondary_keeps_no_contact_tags_on_either_relation(self):
        self.assertEqual(_authoritative_slugs(self.secondary), ["alpha", "gamma"])

        self._merge()

        self.assertEqual(_authoritative_slugs(self.secondary), [])
        self.assertEqual(_legacy_slugs(self.secondary), [])

    def test_canonical_carries_the_union_on_both_relations(self):
        self._merge()

        self.assertEqual(
            _authoritative_slugs(self.canonical), ["alpha", "beta", "gamma"],
        )
        self.assertEqual(
            _legacy_slugs(self.canonical), ["alpha", "beta", "gamma"],
        )
        self.assertEqual(self.canonical.tags, ["alpha", "beta", "gamma"])

    def test_tag_counts_report_one_carrier_not_two(self):
        self._merge()

        self.assertEqual(
            tags_with_user_counts(),
            [
                {"name": "alpha", "user_count": 1},
                {"name": "beta", "user_count": 1},
                {"name": "gamma", "user_count": 1},
            ],
        )
        self.assertEqual(count_users_with_tag("alpha"), 1)

    def test_old_and_new_images_agree_about_every_tag(self):
        """Expand-window equivalence: both relations answer identically."""
        self._merge()

        self.assertEqual(tags_with_user_counts(), _legacy_counts())

    def test_the_retired_row_is_not_a_tag_carrier(self):
        self._merge()

        carriers = set(user_ids_with_exact_tag("gamma"))
        self.assertEqual(carriers, {self.canonical.pk})
        self.assertNotIn(self.secondary.pk, carriers)

    def test_the_move_is_reported_in_the_merge_plan(self):
        plan = self._merge()

        entries = [
            entry for entry in plan.moved
            if entry["field"] == "memberextra.contact_tags"
        ]
        self.assertEqual(
            entries,
            [{
                "model": "accounts_ext.ContactTag",
                "field": "memberextra.contact_tags",
                "added": 1,
            }],
        )

    def test_dry_run_moves_no_tags(self):
        merge_accounts(
            self.canonical,
            self.secondary,
            actor_label="test-1692",
            dry_run=True,
        )

        self.assertEqual(_authoritative_slugs(self.secondary), ["alpha", "gamma"])
        self.assertEqual(_legacy_slugs(self.secondary), ["alpha", "gamma"])


class MergeWithoutTagsTest(TestCase):
    """A tagless secondary must not create work or a spurious plan entry."""

    def test_no_move_is_recorded_when_the_secondary_has_no_tags(self):
        canonical = User.objects.create_user(
            email="keep-notags@test.com", password="pw",
        )
        secondary = User.objects.create_user(
            email="dupe-notags@test.com", password="pw",
        )
        set_tags(canonical, ["alpha"])

        plan = merge_accounts(canonical, secondary, actor_label="test-1692")

        self.assertEqual(
            [e for e in plan.moved if e["field"] == "memberextra.contact_tags"],
            [],
        )
        canonical.refresh_from_db()
        self.assertEqual(_authoritative_slugs(canonical), ["alpha"])
        self.assertEqual(_legacy_slugs(canonical), ["alpha"])


class KeptRowManyToManyGuardTest(TestCase):
    """Structural ratchet for the class of defect this file pins.

    ``_repoint_relations`` only reaches many-to-many relations declared on
    ``User``. A strategy that KEEPS the secondary's related row therefore owns
    every many-to-many declared on that row's model -- nothing else will clear
    it. Today ``accounts_ext.MemberExtra.contact_tags`` is the only one. When a
    second appears, this test fails and the new relation gets an explicit
    decision instead of silently following the retired identity around.
    """

    def test_member_extra_contact_tags_is_the_only_m2m_on_a_kept_row(self):
        from django.apps import apps

        from accounts.services.account_merge import _SPECIAL_STRATEGIES

        found = set()
        for (model_label, _field_name) in _SPECIAL_STRATEGIES:
            model = apps.get_model(model_label)
            for field in model._meta.get_fields():
                if field.many_to_many and not field.auto_created:
                    found.add((model_label, field.name))

        self.assertEqual(found, {("accounts_ext.MemberExtra", "contact_tags")})
