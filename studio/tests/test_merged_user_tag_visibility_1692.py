"""Merged-away accounts must disappear from Studio tag surfaces (issue #1692).

``_annotated_user_queryset`` has no ``is_active`` filter, so a scrubbed
``merged+<pk>@merged.invalid`` row is only absent from a tag-filtered listing
because the merge removed it from the contact-tag relation. A3.2 moved that
relation to ``accounts_ext.MemberExtra`` and the merge stopped clearing it,
which put the retired row back on the list and inflated the Studio tag counts.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.services.account_merge import merge_accounts
from accounts.utils.tags import set_tags

User = get_user_model()


class MergedUserTagVisibilityTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email="staff-1692@test.com", password="pw", is_staff=True,
        )
        self.canonical = User.objects.create_user(
            email="keep-1692@test.com", password="pw",
        )
        self.secondary = User.objects.create_user(
            email="dupe-1692@test.com", password="pw",
        )
        set_tags(self.canonical, ["alpha", "beta"])
        set_tags(self.secondary, ["alpha", "gamma"])
        merge_accounts(self.canonical, self.secondary, actor_label="test-1692")
        self.secondary.refresh_from_db()
        self.client.login(email="staff-1692@test.com", password="pw")

    def test_tag_list_counts_the_surviving_account_only(self):
        response = self.client.get(reverse("studio_tag_list"))

        self.assertEqual(
            response.context["tags"],
            [
                {"name": "alpha", "user_count": 1},
                {"name": "beta", "user_count": 1},
                {"name": "gamma", "user_count": 1},
            ],
        )
        # Each of the three tags renders one carrier, not two.
        self.assertContains(
            response,
            'data-testid="studio-tag-user-count">1 user</a>',
            count=3,
        )

    def test_scrubbed_row_is_absent_from_a_tag_filtered_user_listing(self):
        self.assertTrue(self.secondary.email.endswith("@merged.invalid"))

        response = self.client.get(
            reverse("studio_user_list"), {"tag": "gamma"},
        )

        listed = [row["pk"] for row in response.context["user_rows"]]
        self.assertEqual(listed, [self.canonical.pk])
        self.assertNotContains(response, self.secondary.email)
