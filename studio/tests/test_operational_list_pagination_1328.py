"""Focused Studio pagination contracts for issue #1328."""

from urllib.parse import parse_qs, urlparse

from django.test import TestCase

from content.models import MarketingPage
from integrations.models import Redirect, UtmCampaign
from tests.fixtures import StaffUserMixin


def _query_params(url):
    return parse_qs(urlparse(url).query)


class StudioOperationalPaginationTest(StaffUserMixin, TestCase):
    def setUp(self):
        self.client.login(**self.staff_credentials)

    def _assert_page_clamping(self, url, *, context_key="page"):
        cases = (
            (None, 1),
            ("garbage", 1),
            ("0", 1),
            ("-2", 1),
            ("999", 2),
        )
        separator = "&" if "?" in url else "?"
        for raw, expected in cases:
            with self.subTest(url=url, raw=raw):
                target = url if raw is None else f"{url}{separator}page={raw}"
                response = self.client.get(target)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context[context_key].number, expected)

    def test_redirects_paginate_in_source_path_order_and_keep_edit_action(self):
        Redirect.objects.all().delete()
        redirects = [
            Redirect(source_path=f"/old-{index:02d}", target_path=f"/new-{index:02d}")
            for index in range(26)
        ]
        Redirect.objects.bulk_create(redirects)

        first = self.client.get("/studio/redirects/")
        second = self.client.get("/studio/redirects/?page=2")

        self.assertEqual(
            [item.source_path for item in first.context["redirects"]],
            [f"/old-{index:02d}" for index in range(25)],
        )
        self.assertEqual(
            [item.source_path for item in second.context["redirects"]],
            ["/old-25"],
        )
        last = second.context["redirects"][0]
        self.assertContains(second, f'/studio/redirects/{last.pk}/edit')
        self.assertContains(first, 'data-testid="redirect-list-pager"')

        self._assert_page_clamping("/studio/redirects/")

    def test_redirect_pager_is_hidden_for_25_rows_and_empty_state_is_unchanged(self):
        Redirect.objects.all().delete()
        Redirect.objects.bulk_create([
            Redirect(source_path=f"/small-{index:02d}", target_path="/target")
            for index in range(25)
        ])
        populated = self.client.get("/studio/redirects/")
        self.assertNotContains(populated, 'data-testid="redirect-list-pager"')

        Redirect.objects.all().delete()
        empty = self.client.get("/studio/redirects/")
        self.assertContains(empty, 'data-testid="studio-empty-state-fresh"')
        self.assertContains(empty, "No redirects yet")
        self.assertNotContains(empty, 'data-testid="redirect-list-pager"')

    def test_utm_active_and_archived_scopes_paginate_newest_first(self):
        UtmCampaign.objects.all().delete()
        for index in range(26):
            UtmCampaign.objects.create(
                name=f"Active {index:02d}",
                slug=f"active_{index:02d}",
                default_utm_source="newsletter",
                default_utm_medium="email",
            )
            UtmCampaign.objects.create(
                name=f"Archived {index:02d}",
                slug=f"archived_{index:02d}",
                default_utm_source="newsletter",
                default_utm_medium="email",
                is_archived=True,
            )

        active_expected = list(
            UtmCampaign.objects.filter(is_archived=False).values_list("slug", flat=True)
        )
        active_first = self.client.get("/studio/utm-campaigns/")
        active_second = self.client.get("/studio/utm-campaigns/?page=2")
        self.assertEqual(
            [item.slug for item in active_first.context["campaigns"]],
            active_expected[:25],
        )
        self.assertEqual(
            [item.slug for item in active_second.context["campaigns"]],
            active_expected[25:],
        )
        self.assertTrue(all(not item.is_archived for item in active_second.context["campaigns"]))

        archived = self.client.get("/studio/utm-campaigns/?archived=1")
        archived_second = self.client.get("/studio/utm-campaigns/?archived=1&page=2")
        self.assertEqual(len(archived.context["campaigns"]), 25)
        self.assertEqual(len(archived_second.context["campaigns"]), 1)
        self.assertTrue(all(item.is_archived for item in archived.context["campaigns"]))
        self.assertEqual(
            _query_params(archived.context["pager_next_url"]),
            {"archived": ["1"], "page": ["2"]},
        )

        self._assert_page_clamping("/studio/utm-campaigns/?archived=1")

    def test_marketing_page_search_and_status_survive_title_order_pagination(self):
        MarketingPage.objects.all().delete()
        for index in range(26):
            MarketingPage.objects.create(
                title=f"Matching launch {index:02d}",
                public_path=f"/matching-launch-{index:02d}",
                content_markdown="Body",
                status="draft",
            )
        MarketingPage.objects.create(
            title="Unrelated draft",
            public_path="/unrelated-draft",
            content_markdown="Body",
            status="draft",
        )
        MarketingPage.objects.create(
            title="Matching launch published",
            public_path="/matching-launch-published",
            content_markdown="Body",
            status="published",
        )

        url = "/studio/marketing-pages/?q=Matching+launch&status=draft"
        first = self.client.get(url)
        second = self.client.get(f"{url}&page=2")

        self.assertEqual(len(first.context["pages"]), 25)
        self.assertEqual(
            [item.title for item in second.context["pages"]],
            ["Matching launch 25"],
        )
        self.assertNotContains(second, "Unrelated draft")
        self.assertNotContains(second, "Matching launch published")
        self.assertEqual(
            _query_params(first.context["pager_next_url"]),
            {"q": ["Matching launch"], "status": ["draft"], "page": ["2"]},
        )

        self._assert_page_clamping(url)

    def test_filtered_marketing_page_empty_state_and_single_page_stay_unchanged(self):
        MarketingPage.objects.create(
            title="Only page",
            public_path="/only-page",
            content_markdown="Body",
            status="draft",
        )
        populated = self.client.get("/studio/marketing-pages/")
        self.assertNotContains(populated, 'data-testid="marketing-page-list-pager"')

        filtered = self.client.get("/studio/marketing-pages/?q=no-match")
        self.assertContains(filtered, 'data-testid="studio-empty-state-filter"')
        self.assertContains(filtered, "No marketing pages match your filters")
        self.assertNotContains(filtered, 'data-testid="marketing-page-list-pager"')
