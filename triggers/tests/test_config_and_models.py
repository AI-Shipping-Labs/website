"""Config-flag registration + model behaviour tests (issue #1070)."""

from django.core.exceptions import ValidationError
from django.test import TestCase, tag

from integrations.config import clear_config_cache, is_enabled
from integrations.models import IntegrationSetting
from integrations.settings_registry import get_group_by_name
from triggers.models import TriggerSubscription


@tag("core")
class TriggersFlagConfigTest(TestCase):
    def tearDown(self):
        clear_config_cache()

    def test_flag_defaults_off(self):
        clear_config_cache()
        self.assertFalse(is_enabled("TRIGGERS_ENABLED"))

    def test_flag_reads_db_override(self):
        IntegrationSetting.objects.create(
            key="TRIGGERS_ENABLED", value="true", group="triggers",
        )
        clear_config_cache()
        self.assertTrue(is_enabled("TRIGGERS_ENABLED"))

    def test_flag_is_registered_in_settings_group(self):
        group = get_group_by_name("triggers")
        self.assertIsNotNone(group)
        keys = {k["key"] for k in group["keys"]}
        self.assertIn("TRIGGERS_ENABLED", keys)


@tag("core")
class SubscriptionMatchesTest(TestCase):
    def test_empty_filter_matches_all(self):
        sub = TriggerSubscription(property_filter={})
        self.assertTrue(sub.matches({"name": "anything"}))
        self.assertTrue(sub.matches({}))

    def test_exact_match_required(self):
        sub = TriggerSubscription(property_filter={"name": "v0_workshop"})
        self.assertTrue(sub.matches({"name": "v0_workshop", "extra": 1}))
        self.assertFalse(sub.matches({"name": "other"}))
        self.assertFalse(sub.matches({}))

    def test_non_dict_properties_do_not_match_a_filter(self):
        sub = TriggerSubscription(property_filter={"name": "x"})
        self.assertFalse(sub.matches(None))

    def test_non_object_filter_is_rejected_at_model_boundary(self):
        with self.assertRaises(ValidationError):
            TriggerSubscription.objects.create(
                property_filter=["not", "an", "object"],
                target_url="https://handler.example.com/hook",
                secret="s",
            )


@tag("core")
class EmbedShortcodeTest(TestCase):
    def test_embed_shortcode_format(self):
        from triggers.models import EventWidget

        widget = EventWidget(slug="v0-claim", event_name="v0_workshop")
        self.assertEqual(
            widget.embed_shortcode, "```eventwidget\nslug: v0-claim\n```",
        )


@tag("core")
class WidgetDefaultCopyTest(TestCase):
    """Default copy must be partner-agnostic (issue #1070 PM review).

    v0 credits is only the first partner, so the model defaults must not be
    credit/v0-flavored; operators override per widget.
    """

    def test_claim_label_default_is_neutral(self):
        from triggers.models import EventWidget

        widget = EventWidget(slug="x", event_name="x")
        self.assertEqual(widget.claim_label, "Claim")

    def test_exhausted_label_default_is_neutral(self):
        from triggers.models import EventWidget

        widget = EventWidget(slug="x", event_name="x")
        self.assertEqual(widget.exhausted_label, "No longer available")
