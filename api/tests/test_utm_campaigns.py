"""Tests for the UTM campaign + tracked-link API (issue #875).

Token-authenticated CRUD over ``UtmCampaign`` / ``UtmCampaignLink``. No hard
delete is exposed (policy #864): a DELETE request returns 405 and archiving is
``PATCH {"is_archived": true}``. Every serialized link must carry the
generated ``url`` plus ``effective_source`` / ``effective_medium``.
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from integrations.models import UtmCampaign, UtmCampaignLink

User = get_user_model()

COLLECTION = "/api/utm-campaigns"


class UtmApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-utm@test.com",
            password="pw",
            is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="utm")

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.staff_token.key}"}

    def _get(self, path, **extra):
        return self.client.get(path, **self._auth(), **extra)

    def _post(self, path, payload):
        return self.client.post(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(),
        )

    def _patch(self, path, payload):
        return self.client.patch(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(),
        )


class CampaignAuthTest(UtmApiTestBase):
    def test_list_requires_token(self):
        response = self.client.get(COLLECTION)
        self.assertEqual(response.status_code, 401)

    def test_list_rejects_invalid_token(self):
        response = self.client.get(
            COLLECTION,
            HTTP_AUTHORIZATION="Token not-a-real-token",
        )
        self.assertEqual(response.status_code, 401)

    def test_create_requires_token(self):
        response = self.client.post(
            COLLECTION,
            data=json.dumps({"name": "x", "slug": "x"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)


class CampaignListTest(UtmApiTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.active = UtmCampaign.objects.create(
            name="Spring Launch",
            slug="spring_launch",
            default_utm_source="newsletter",
            default_utm_medium="email",
        )
        cls.archived = UtmCampaign.objects.create(
            name="Winter Recap",
            slug="winter_recap",
            default_utm_source="slack",
            default_utm_medium="social",
            is_archived=True,
        )

    def test_list_returns_all_campaigns(self):
        response = self._get(COLLECTION)
        self.assertEqual(response.status_code, 200)
        slugs = {c["slug"] for c in response.json()["campaigns"]}
        self.assertEqual(slugs, {"spring_launch", "winter_recap"})

    def test_filter_is_archived_true(self):
        response = self._get(COLLECTION + "?is_archived=true")
        slugs = [c["slug"] for c in response.json()["campaigns"]]
        self.assertEqual(slugs, ["winter_recap"])

    def test_filter_is_archived_false(self):
        response = self._get(COLLECTION + "?is_archived=false")
        slugs = [c["slug"] for c in response.json()["campaigns"]]
        self.assertEqual(slugs, ["spring_launch"])

    def test_filter_q_name_icontains(self):
        response = self._get(COLLECTION + "?q=spring")
        slugs = [c["slug"] for c in response.json()["campaigns"]]
        self.assertEqual(slugs, ["spring_launch"])

    def test_invalid_is_archived_value_returns_422(self):
        response = self._get(COLLECTION + "?is_archived=maybe")
        self.assertEqual(response.status_code, 422)


class CampaignCreateTest(UtmApiTestBase):
    def _payload(self, **overrides):
        payload = {
            "name": "May Newsletter",
            "slug": "may_newsletter",
            "default_utm_source": "newsletter",
            "default_utm_medium": "email",
        }
        payload.update(overrides)
        return payload

    def test_create_returns_201_and_persists(self):
        response = self._post(COLLECTION, self._payload(notes="hi"))
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["slug"], "may_newsletter")
        self.assertEqual(body["notes"], "hi")
        self.assertFalse(body["is_archived"])
        self.assertTrue(
            UtmCampaign.objects.filter(slug="may_newsletter").exists()
        )

    def test_missing_required_field_returns_422(self):
        payload = self._payload()
        del payload["default_utm_source"]
        response = self._post(COLLECTION, payload)
        self.assertEqual(response.status_code, 422)
        self.assertIn("default_utm_source", response.json()["details"])

    def test_invalid_slug_returns_422(self):
        response = self._post(COLLECTION, self._payload(slug="Not Valid!"))
        self.assertEqual(response.status_code, 422)
        self.assertIn("slug", response.json()["details"])
        self.assertFalse(UtmCampaign.objects.filter(name="May Newsletter").exists())

    def test_duplicate_slug_returns_422(self):
        UtmCampaign.objects.create(
            name="Existing",
            slug="may_newsletter",
            default_utm_source="newsletter",
            default_utm_medium="email",
        )
        response = self._post(COLLECTION, self._payload())
        self.assertEqual(response.status_code, 422)
        self.assertIn("slug", response.json()["details"])

    def test_custom_non_preset_source_and_medium_accepted(self):
        response = self._post(
            COLLECTION,
            self._payload(
                default_utm_source="my_custom_source",
                default_utm_medium="my_custom_medium",
            ),
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["default_utm_source"], "my_custom_source")
        self.assertEqual(body["default_utm_medium"], "my_custom_medium")

    def test_invalid_json_returns_400(self):
        response = self.client.post(
            COLLECTION,
            data="{not json",
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)


class CampaignDetailTest(UtmApiTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.campaign = UtmCampaign.objects.create(
            name="Detail Campaign",
            slug="detail_campaign",
            default_utm_source="newsletter",
            default_utm_medium="email",
        )
        cls.active_link = UtmCampaignLink.objects.create(
            campaign=cls.campaign,
            utm_content="hero",
            destination="/events/demo",
        )
        cls.archived_link = UtmCampaignLink.objects.create(
            campaign=cls.campaign,
            utm_content="footer",
            destination="/events/demo",
            is_archived=True,
        )

    def test_detail_includes_active_links_only_by_default(self):
        response = self._get(f"{COLLECTION}/{self.campaign.pk}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        contents = {link["utm_content"] for link in body["links"]}
        self.assertEqual(contents, {"hero"})

    def test_detail_include_archived_links(self):
        response = self._get(
            f"{COLLECTION}/{self.campaign.pk}?include_archived=true"
        )
        contents = {link["utm_content"] for link in response.json()["links"]}
        self.assertEqual(contents, {"hero", "footer"})

    def test_unknown_campaign_returns_404(self):
        response = self._get(f"{COLLECTION}/999999")
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertEqual(body["code"], "unknown_campaign")
        self.assertIn("error", body)


class CampaignPatchTest(UtmApiTestBase):
    def setUp(self):
        self.campaign = UtmCampaign.objects.create(
            name="Patch Campaign",
            slug="patch_campaign",
            default_utm_source="newsletter",
            default_utm_medium="email",
        )

    def test_patch_updates_writable_fields(self):
        response = self._patch(
            f"{COLLECTION}/{self.campaign.pk}",
            {"name": "Renamed", "default_utm_source": "slack", "notes": "n"},
        )
        self.assertEqual(response.status_code, 200)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.name, "Renamed")
        self.assertEqual(self.campaign.default_utm_source, "slack")
        self.assertEqual(self.campaign.notes, "n")

    def test_patch_archive_then_unarchive(self):
        response = self._patch(
            f"{COLLECTION}/{self.campaign.pk}", {"is_archived": True}
        )
        self.assertEqual(response.status_code, 200)
        self.campaign.refresh_from_db()
        self.assertTrue(self.campaign.is_archived)

        response = self._patch(
            f"{COLLECTION}/{self.campaign.pk}", {"is_archived": False}
        )
        self.campaign.refresh_from_db()
        self.assertFalse(self.campaign.is_archived)

    def test_slug_change_allowed_without_links(self):
        response = self._patch(
            f"{COLLECTION}/{self.campaign.pk}", {"slug": "new_slug"}
        )
        self.assertEqual(response.status_code, 200)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.slug, "new_slug")

    def test_slug_change_with_links_returns_422(self):
        UtmCampaignLink.objects.create(
            campaign=self.campaign,
            utm_content="hero",
            destination="/x",
        )
        response = self._patch(
            f"{COLLECTION}/{self.campaign.pk}", {"slug": "locked_slug"}
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("slug", response.json()["details"])
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.slug, "patch_campaign")

    def test_same_slug_with_links_is_noop_not_error(self):
        UtmCampaignLink.objects.create(
            campaign=self.campaign,
            utm_content="hero",
            destination="/x",
        )
        response = self._patch(
            f"{COLLECTION}/{self.campaign.pk}",
            {"slug": "patch_campaign", "name": "Still Fine"},
        )
        self.assertEqual(response.status_code, 200)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.name, "Still Fine")


class LinkCollectionTest(UtmApiTestBase):
    def setUp(self):
        self.campaign = UtmCampaign.objects.create(
            name="Link Campaign",
            slug="link_campaign",
            default_utm_source="newsletter",
            default_utm_medium="email",
        )

    def _links_path(self):
        return f"{COLLECTION}/{self.campaign.pk}/links"

    def test_create_link_returns_201_with_url(self):
        response = self._post(
            self._links_path(),
            {"utm_content": "homepage", "destination": "/pricing"},
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["utm_content"], "homepage")
        self.assertEqual(body["campaign"], self.campaign.pk)
        self.assertIn("utm_source=newsletter", body["url"])
        self.assertIn("utm_medium=email", body["url"])
        self.assertIn("utm_campaign=link_campaign", body["url"])
        self.assertIn("utm_content=homepage", body["url"])

    def test_create_missing_utm_content_returns_422(self):
        response = self._post(
            self._links_path(), {"destination": "/pricing"}
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("utm_content", response.json()["details"])

    def test_create_missing_destination_returns_422(self):
        response = self._post(
            self._links_path(), {"utm_content": "homepage"}
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("destination", response.json()["details"])

    def test_create_invalid_utm_content_slug_returns_422(self):
        response = self._post(
            self._links_path(),
            {"utm_content": "Bad Slug!", "destination": "/x"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("utm_content", response.json()["details"])

    def test_duplicate_utm_content_in_campaign_returns_422(self):
        UtmCampaignLink.objects.create(
            campaign=self.campaign,
            utm_content="homepage",
            destination="/x",
        )
        response = self._post(
            self._links_path(),
            {"utm_content": "homepage", "destination": "/y"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("utm_content", response.json()["details"])

    def test_custom_source_medium_override_on_link(self):
        response = self._post(
            self._links_path(),
            {
                "utm_content": "homepage",
                "destination": "/x",
                "utm_source": "podcast",
                "utm_medium": "audio",
            },
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["effective_source"], "podcast")
        self.assertEqual(body["effective_medium"], "audio")

    def test_list_links_filter_is_archived(self):
        UtmCampaignLink.objects.create(
            campaign=self.campaign, utm_content="active_one", destination="/a"
        )
        UtmCampaignLink.objects.create(
            campaign=self.campaign,
            utm_content="archived_one",
            destination="/b",
            is_archived=True,
        )
        response = self._get(self._links_path() + "?is_archived=false")
        contents = {link["utm_content"] for link in response.json()["links"]}
        self.assertEqual(contents, {"active_one"})

    def test_list_links_unknown_campaign_returns_404(self):
        response = self._get(f"{COLLECTION}/999999/links")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_campaign")


class LinkDetailTest(UtmApiTestBase):
    def setUp(self):
        self.campaign = UtmCampaign.objects.create(
            name="LD Campaign",
            slug="ld_campaign",
            default_utm_source="newsletter",
            default_utm_medium="email",
        )
        self.link = UtmCampaignLink.objects.create(
            campaign=self.campaign,
            utm_content="hero",
            destination="/events/demo",
        )

    def _path(self):
        return f"{COLLECTION}/{self.campaign.pk}/links/{self.link.pk}"

    def test_get_link_includes_generated_url_and_effective(self):
        response = self._get(self._path())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["url"], self.link.build_url())
        self.assertEqual(body["effective_source"], "newsletter")
        self.assertEqual(body["effective_medium"], "email")

    def test_unknown_link_returns_404(self):
        response = self._get(f"{COLLECTION}/{self.campaign.pk}/links/999999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_link")

    def test_patch_updates_writable_fields(self):
        response = self._patch(
            self._path(),
            {"destination": "/new-dest", "label": "New Label"},
        )
        self.assertEqual(response.status_code, 200)
        self.link.refresh_from_db()
        self.assertEqual(self.link.destination, "/new-dest")
        self.assertEqual(self.link.label, "New Label")

    def test_patch_archive_removes_from_default_list(self):
        response = self._patch(self._path(), {"is_archived": True})
        self.assertEqual(response.status_code, 200)
        self.link.refresh_from_db()
        self.assertTrue(self.link.is_archived)

        list_response = self._get(
            f"{COLLECTION}/{self.campaign.pk}/links?is_archived=false"
        )
        contents = [
            link["utm_content"] for link in list_response.json()["links"]
        ]
        self.assertNotIn("hero", contents)

    def test_patch_duplicate_utm_content_returns_422(self):
        UtmCampaignLink.objects.create(
            campaign=self.campaign, utm_content="other", destination="/o"
        )
        response = self._patch(self._path(), {"utm_content": "other"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("utm_content", response.json()["details"])


class NoHardDeleteTest(UtmApiTestBase):
    def setUp(self):
        self.campaign = UtmCampaign.objects.create(
            name="Del Campaign",
            slug="del_campaign",
            default_utm_source="newsletter",
            default_utm_medium="email",
        )
        self.link = UtmCampaignLink.objects.create(
            campaign=self.campaign,
            utm_content="hero",
            destination="/x",
        )

    def test_delete_campaign_collection_returns_405(self):
        response = self.client.delete(COLLECTION, **self._auth())
        self.assertEqual(response.status_code, 405)

    def test_delete_campaign_detail_returns_405(self):
        response = self.client.delete(
            f"{COLLECTION}/{self.campaign.pk}", **self._auth()
        )
        self.assertEqual(response.status_code, 405)

    def test_delete_link_detail_returns_405(self):
        response = self.client.delete(
            f"{COLLECTION}/{self.campaign.pk}/links/{self.link.pk}",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 405)
        self.assertTrue(
            UtmCampaignLink.objects.filter(pk=self.link.pk).exists()
        )


class UtmOpenApiDocsTest(UtmApiTestBase):
    def test_utm_endpoints_appear_in_openapi_spec(self):
        response = self._get("/api/openapi.json")
        self.assertEqual(response.status_code, 200)
        spec = response.json()
        paths = spec["paths"]
        self.assertIn("/api/utm-campaigns", paths)
        self.assertIn("/api/utm-campaigns/{campaign_id}", paths)
        self.assertIn("/api/utm-campaigns/{campaign_id}/links", paths)
        self.assertIn(
            "/api/utm-campaigns/{campaign_id}/links/{link_id}", paths
        )
        # The UTM tag is attached to the collection operation.
        tags = paths["/api/utm-campaigns"]["get"]["tags"]
        self.assertIn("UTM", tags)
