"""Equivalence checks for the A0.2 step-2 declare conversion.

The donor snapshot is the hand-written ``INTEGRATION_GROUPS`` list as it
existed before the conversion; the derived legacy view in
``integrations.settings_registry`` must match it field for field, in
donor group and key order. Provisional by design: the A0.2 cutover
deletes the legacy view together with these checks.
"""

import importlib
import json
from pathlib import Path

from django.test import SimpleTestCase

from integrations.settings_registry import INTEGRATION_GROUPS, SETTING_VALUE_TYPES

FIXTURE = Path(__file__).parent / "fixtures" / "donor_settings_inventory_2026-09-08.json"

# Site-declared optional additions (issue #1627): keys this repo marks
# ``optional=True`` beyond the donor snapshot. The donor UI accepted a
# blank submit for every key (blank meant unset), but the package form
# only allows blanks for keys declared optional, so blankable
# display/fallback keys are declared optional even though the donor
# badge metadata counted them as required.
SITE_OPTIONAL_ADDITIONS = frozenset({
    "AWS_S3_CONTENT_BUCKET",
    "AWS_S3_CONTENT_REGION",
    "AWS_S3_DOWNLOADS_BUCKET",
    "AWS_S3_RECORDINGS_BUCKET",
    "AWS_S3_RECORDINGS_REGION",
    "BANNER_GENERATOR_FUNCTION_URL",
    "CONTENT_CDN_BASE",
    "DAPIER_AGENT_NAME",
    "DAPIER_API_TOKEN",
    "DAPIER_BASE_URL",
    "EVENT_DISPLAY_TIMEZONE",
    "GITHUB_APP_ID",
    "GITHUB_APP_INSTALLATION_ID",
    "GOOGLE_CALENDAR_ID",
    "RELAY_PROMOTIONAL_FROM_EMAIL",
    "RELAY_TRANSACTIONAL_FROM_EMAIL",
    "RELAY_WELCOME_FROM_EMAIL",
    "SITE_BASE_URL",
    "SITE_BASE_URL_ALIASES",
    "SLACK_ANNOUNCEMENTS_CHANNEL_ID",
    "SLACK_COMMUNITY_CHANNEL_IDS",
    "SLACK_DEV_ANNOUNCEMENTS_CHANNEL_ID",
    "SLACK_DEV_COMMUNITY_CHANNEL_IDS",
    "SLACK_ENVIRONMENT",
    "SLACK_INVITE_URL",
    "SLACK_TEAM_ID",
    "SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID",
    "SLACK_TEST_COMMUNITY_CHANNEL_IDS",
    "STRIPE_CUSTOMER_PORTAL_URL",
    "STRIPE_DASHBOARD_ACCOUNT_ID",
})

RELAY_KEY_RENAMES = {
    "SES_TRANSACTIONAL_FROM_EMAIL": "RELAY_TRANSACTIONAL_FROM_EMAIL",
    "SES_PROMOTIONAL_FROM_EMAIL": "RELAY_PROMOTIONAL_FROM_EMAIL",
    "SES_WELCOME_FROM_EMAIL": "RELAY_WELCOME_FROM_EMAIL",
    "SES_WELCOME_REPLY_TO_EMAIL": "RELAY_WELCOME_REPLY_TO_EMAIL",
}


def _project_email_cleanup(groups):
    """Apply the approved email-key delta to the immutable donor snapshot."""
    for group in groups:
        if group["name"] == "relay":
            _project_relay_group(group)
        _project_email_descriptions(group)
    return groups


def _project_relay_group(group):
    for key_def in group["keys"]:
        old_key = key_def["key"]
        key_def["key"] = RELAY_KEY_RENAMES.get(old_key, old_key)
        key_def["docs_url"] = f"_docs/integrations/relay.md#{key_def['key'].lower()}"
        if key_def["key"] == "RELAY_WELCOME_REPLY_TO_EMAIL":
            key_def["description"] = "Reply-To address for welcome emails. Leave blank to omit Reply-To."
    group["keys"].insert(4, {
        "key": "RELAY_UNSUBSCRIBE_EMAIL",
        "is_secret": False,
        "description": "Optional mailto address included with one-click unsubscribe headers.",
        "docs_url": "_docs/integrations/relay.md#relay_unsubscribe_email",
        "optional": True,
        "is_email": True,
    })


def _project_email_descriptions(group):
    replacements = {
        "SES_WEBHOOK_SHARED_SECRET": (
            "Optional shared secret required in the X-SES-Webhook-Secret header on the SES webhook. "
            "In production, Relay's aisl-ses-ingress reads it from Secrets Manager. Leave blank locally to allow "
            "runserver replay."
        ),
        "STAFF_SIGNUP_NOTIFY_EMAIL": (
            "Single staff mailbox used for paid-signup BCC and internal notices. Replies still route only via "
            "RELAY_WELCOME_REPLY_TO_EMAIL."
        ),
    }
    for key_def in group["keys"]:
        description = replacements.get(key_def["key"])
        if description is not None:
            key_def["description"] = description


def _normalize(groups):
    """Annotate donor value types and drop empty-string defaults.

    The donor snapshot was captured from the pre-annotation list literal,
    so the legacy ``SETTING_VALUE_TYPES`` pass is replayed here; the
    derived view has already been through it. Empty-string defaults are
    dropped so donor-absent and donor-``''`` agree. The site optional
    addendum is folded in so the comparison proves the declared optional
    set is exactly donor-optional plus the documented additions.
    """
    normalized = []
    for group in groups:
        keys = []
        for key_def in group["keys"]:
            cleaned = {field: value for field, value in key_def.items() if not (field == "default" and value == "")}
            value_type = SETTING_VALUE_TYPES.get(cleaned["key"])
            if value_type:
                cleaned["value_type"] = value_type
            if cleaned["key"] in SITE_OPTIONAL_ADDITIONS:
                cleaned["optional"] = True
            keys.append(cleaned)
        normalized.append({"name": group["name"], "label": group["label"], "keys": keys})
    return normalized


class DonorInventoryEquivalenceTest(SimpleTestCase):
    def test_derived_registry_matches_donor_inventory(self):
        donor = json.loads(FIXTURE.read_text())
        self.assertEqual(_normalize(INTEGRATION_GROUPS), _normalize(_project_email_cleanup(donor)))

    def test_group_and_key_counts(self):
        self.assertEqual(len(INTEGRATION_GROUPS), 21)
        self.assertEqual(sum(len(group["keys"]) for group in INTEGRATION_GROUPS), 175)


class DeclarationIdempotenceTest(SimpleTestCase):
    def test_reimport_does_not_conflict(self):
        from integrations import settings_keys

        # Re-executing the declares must hit the registry's identical-
        # definition path, not the conflicting-declaration error; Django
        # startup declares them twice (this app and community_base.config).
        importlib.reload(settings_keys)


class PackageMappingTest(SimpleTestCase):
    def test_value_types_are_mapped(self):
        from integrations import settings_keys

        self.assertEqual(settings_keys.S3_ENABLED.value_type, "bool")
        self.assertEqual(settings_keys.EMAIL_BATCH_SIZE.value_type, "int")
        self.assertEqual(settings_keys.STRIPE_SECRET_KEY.value_type, "str")
        self.assertEqual(settings_keys.SITE_BASE_URL.value_type, "str")

    def test_defaults_are_donor_literals(self):
        from integrations import settings_keys

        self.assertEqual(settings_keys.MAVEN_ENROLLMENT_ENABLED.default, "false")
        self.assertEqual(settings_keys.LLM_JUDGE_MODEL.default, "")
        self.assertEqual(settings_keys.USER_ACTIVITY_RETENTION_DAYS.default, "365")
        self.assertEqual(
            settings_keys.STRIPE_WEBHOOK_EXPECTED_URL.default, "https://aishippinglabs.com/api/webhooks/payments"
        )
        self.assertEqual(
            settings_keys.STRIPE_PAYMENT_LINKS.django_settings_fallback,
            "STRIPE_PAYMENT_LINKS",
        )

    def test_keys_without_donor_default_declare_zero_values(self):
        from community_base.config.registry import definition as registry_definition

        from integrations import settings_keys
        from integrations.settings_registry import _legacy_key_entry

        for key_name in settings_keys._KEYS_WITHOUT_DONOR_DEFAULT:
            declared = registry_definition(key_name)
            expected = {"bool": False, "int": 0}.get(declared.value_type, "")
            self.assertEqual(declared.default, expected, key_name)
            self.assertNotIn("default", _legacy_key_entry(key_name), key_name)

    def test_docs_url_preserved_on_every_key(self):
        from community_base.config.registry import definitions

        from integrations import settings_keys  # noqa: F401

        for declared in definitions():
            self.assertTrue(declared.docs_url, declared.key)


class SiteOptionalAdditionsTest(SimpleTestCase):
    def test_additions_are_disjoint_from_donor_optional(self):
        donor = json.loads(FIXTURE.read_text())
        donor_optional = {
            key_def["key"]
            for group in donor
            for key_def in group["keys"]
            if key_def.get("optional")
        }
        self.assertFalse(SITE_OPTIONAL_ADDITIONS & donor_optional)

    def test_additions_are_declared_optional(self):
        from integrations import settings_keys

        for key in SITE_OPTIONAL_ADDITIONS:
            self.assertTrue(getattr(settings_keys, key).optional, key)


class RelaySettingsGroupsTest(SimpleTestCase):
    def test_ses_group_contains_only_event_ingest_settings(self):
        from integrations.settings_registry import get_group_by_name

        group = get_group_by_name("ses")
        self.assertEqual({item["key"] for item in group["keys"]}, {
            "SES_WEBHOOK_VALIDATION_ENABLED", "SES_WEBHOOK_SHARED_SECRET",
        })

    def test_sender_settings_and_storage_credentials_remain_available(self):
        from integrations.settings_keys import (
            AWS_ACCESS_KEY_ID,
            AWS_SECRET_ACCESS_KEY,
            RELAY_PROMOTIONAL_FROM_EMAIL,
            RELAY_TRANSACTIONAL_FROM_EMAIL,
            RELAY_WELCOME_FROM_EMAIL,
            RELAY_WELCOME_REPLY_TO_EMAIL,
        )

        for definition in (
            RELAY_TRANSACTIONAL_FROM_EMAIL, RELAY_PROMOTIONAL_FROM_EMAIL,
            RELAY_WELCOME_FROM_EMAIL, RELAY_WELCOME_REPLY_TO_EMAIL,
        ):
            with self.subTest(key=definition.key):
                self.assertEqual(definition.group, "relay")
        self.assertEqual(AWS_ACCESS_KEY_ID.group, "s3_content")
        self.assertEqual(AWS_SECRET_ACCESS_KEY.group, "s3_content")
