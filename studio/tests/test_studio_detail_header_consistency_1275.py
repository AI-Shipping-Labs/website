"""Static regression inventory for the #1275 stacked-header migration."""

import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "templates" / "studio"

PRIMARY = (
    "plans/detail.html",
    "sprints/detail.html",
    "crm/detail.html",
    "event_series/detail.html",
    "campaigns/detail.html",
    "workshops/detail.html",
    "utm_campaigns/detail.html",
    "personas/detail.html",
    "questionnaires/detail.html",
    "projects/review.html",
    "events/form.html",
    "articles/form.html",
    "courses/form.html",
    "marketing_pages/form.html",
    "workshops/form.html",
    "downloads/form.html",
    "recordings/form.html",
    "sprints/form.html",
    "redirects/form.html",
    "personas/form.html",
    "questionnaires/form.html",
    "utm_campaigns/form.html",
    "utm_campaigns/link_form.html",
    "courses/unit_form.html",
    "courses/peer_reviews.html",
    "plans/_editor_body.html",
    "worker_task_detail.html",
    "worker_inspect.html",
    "ses_events/detail.html",
    "questionnaires/response_detail.html",
)

SECTION_ONLY = (
    "dashboard.html",
    "sync/_sync_repo_card.html",
    "tier_overrides.html",
    "imports/list.html",
    "settings/_integration_card.html",
    "settings/_auth_card.html",
    "users/list.html",
)

CARD_HEADERS = {"courses/peer_reviews.html", "plans/_editor_body.html"}
ACTIONLESS = {
    "questionnaires/form.html",
    "worker_task_detail.html",
    "worker_inspect.html",
    "ses_events/detail.html",
}
CANONICAL_FOCUS_RING = (
    "focus-visible:outline-none focus-visible:ring-2 "
    "focus-visible:ring-accent focus-visible:ring-offset-2 "
    "focus-visible:ring-offset-background"
)


def source(relative):
    return (TEMPLATES / relative).read_text()


class StudioDetailHeaderInventoryTest(SimpleTestCase):
    def test_new_metadata_and_help_links_own_canonical_focus_ring(self):
        anchors = (
            (
                "plans/detail.html",
                r'<a[^>]+data-testid="plan-detail-member-link"[^>]*>',
            ),
            (
                "plans/detail.html",
                r'<a[^>]+studio_sprint_detail[^>]*>',
            ),
            (
                "plans/_editor_body.html",
                r'<a[^>]+studio_user_detail[^>]*>',
            ),
            (
                "utm_campaigns/link_form.html",
                r'<a[^>]+data-testid="utm-fields-help-link"[^>]*>',
            ),
        )
        for relative, pattern in anchors:
            with self.subTest(relative=relative, pattern=pattern):
                match = re.search(pattern, source(relative))
                self.assertIsNotNone(match)
                self.assertIn(CANONICAL_FOCUS_RING, match.group(0))

    def test_inventory_is_exactly_thirty_primary_and_thirty_seven_unique(self):
        self.assertEqual(len(PRIMARY), 30)
        self.assertEqual(len(set(PRIMARY) | set(SECTION_ONLY)), 37)
        for relative in (*PRIMARY, *SECTION_ONLY):
            self.assertTrue((TEMPLATES / relative).is_file(), relative)

    def test_page_headers_use_shared_tag_and_card_headers_do_not(self):
        for relative in PRIMARY:
            with self.subTest(relative=relative):
                template = source(relative)
                if relative in CARD_HEADERS:
                    self.assertNotIn("{% studio_header_actions", template)
                    self.assertIn("flex flex-wrap items-center gap-2", template)
                else:
                    self.assertIn("{% studio_header_actions", template)
                    self.assertIn("{% endstudio_header_actions %}", template)

    def test_shared_header_blocks_have_no_legacy_layout_tokens(self):
        forbidden = (
            "justify-between",
            "sm:flex-row",
            "sm:justify-end",
            "shrink-0",
            "space-x-",
        )
        for relative in set(PRIMARY) - CARD_HEADERS:
            template = source(relative)
            chunks = template.split("{% studio_header_actions")[1:]
            with self.subTest(relative=relative):
                self.assertTrue(chunks)
                for chunk in chunks:
                    header = chunk.split("{% endstudio_header_actions %}", 1)[0]
                    for token in forbidden:
                        self.assertNotIn(token, header)

    def test_actionless_headers_do_not_supply_action_markup(self):
        for relative in ACTIONLESS:
            template = source(relative)
            with self.subTest(relative=relative):
                for chunk in template.split("{% studio_header_actions")[1:]:
                    body = chunk.split("%}", 1)[1].split("{% endstudio_header_actions %}", 1)[0]
                    self.assertFalse(body.strip())

    def test_recovered_controls_and_exact_semantics_remain(self):
        plan = source("plans/detail.html")
        self.assertIn('data-testid="plan-access-card"', plan)
        self.assertIn("studio_plan_visibility_update", plan)
        self.assertIn('data-testid="studio-plan-carry-over"', plan)
        self.assertIn('data-testid="studio-plan-move-unfinished"', plan)

        editor = source("plans/_editor_body.html")
        self.assertIn('data-testid="studio-plan-carry-over"', editor)
        self.assertIn('data-testid="studio-plan-draft-next-sprint"', editor)
        self.assertIn('name="return_to"', editor)

        series = source("event_series/detail.html")
        self.assertIn('data-testid="event-series-metadata-save"', series)
        self.assertIn('data-testid="event-series-delete-form"', series)
        self.assertIn("studio_event_series_create_zoom", series)

        project = source("projects/review.html")
        self.assertIn('name="action" value="approve"', project)
        self.assertIn('name="action" value="reject"', project)
        self.assertIn("not is_synced and project.status == 'pending_review'", project)

    def test_required_title_metadata_and_existing_test_ids_are_preserved(self):
        expectations = {
            "plans/detail.html": ("title_meta=plan_header_meta", "plan-detail-title"),
            "crm/detail.html": ("title_meta=crm_header_meta", "crm-detail-email"),
            "event_series/detail.html": (
                "title_meta=series_header_meta",
                "event-series-cadence",
            ),
            "utm_campaigns/detail.html": (
                "title_meta=campaign_header_meta",
                "Archived",
            ),
            "ses_events/detail.html": ("title_meta=ses_detail_meta", "tabindex=\"0\""),
            "questionnaires/response_detail.html": (
                "title_meta=response_header_meta",
                "response-detail-status",
            ),
        }
        for relative, markers in expectations.items():
            template = source(relative)
            with self.subTest(relative=relative):
                for marker in markers:
                    self.assertIn(marker, template)

    def test_post_actions_keep_csrf_confirmations_and_destinations(self):
        expectations = {
            "plans/detail.html": (
                "studio_plan_view_as_member",
                "studio_plan_carry_over",
                "carry_over_confirmation|escapejs",
                "studio_plan_draft_next_sprint",
                "draft_next_sprint_confirmation|escapejs",
            ),
            "event_series/detail.html": (
                "studio_event_series_create_zoom",
                "studio_event_series_delete",
                "Delete this series? The member events will remain.",
            ),
            "campaigns/detail.html": (
                "studio_campaign_send",
                "studio_campaign_duplicate",
                "studio_campaign_delete",
                "This cannot be undone.",
            ),
            "utm_campaigns/detail.html": (
                "studio_utm_campaign_archive",
                "Archive this campaign?",
                "studio_utm_campaign_unarchive",
            ),
        }
        for relative, markers in expectations.items():
            template = source(relative)
            with self.subTest(relative=relative):
                self.assertIn("{% csrf_token %}", template)
                for marker in markers:
                    self.assertIn(marker, template)

        utm_header = source("utm_campaigns/detail.html").split(
            "{% studio_header_actions", 1
        )[1].split("{% endstudio_header_actions %}", 1)[0]
        edit = utm_header.split(">Edit</a>", 1)[0]
        self.assertNotIn("bg-accent", edit)

    def test_campaign_has_one_action_home_and_sentence_case_duplicate(self):
        template = source("campaigns/detail.html")
        self.assertNotIn(">Actions</h2>", template)
        self.assertNotIn("Duplicate Campaign", template)
        self.assertNotIn("bg-red-600", template)
        self.assertEqual(template.count("studio_campaign_duplicate"), 1)
        self.assertEqual(template.count("studio_campaign_send"), 1)

    def test_named_sections_are_title_first(self):
        expectations = {
            "dashboard.html": ("Worker running", "Worker dashboard"),
            "sync/_sync_repo_card.html": ("repo.repo_name", "Sync now"),
            "tier_overrides.html": ("Active overrides", "active-overrides-count"),
            "imports/list.html": ("Scheduled imports", "studio_import_schedule_toggle"),
            "settings/_integration_card.html": ("group.label", "Configured"),
            "settings/_auth_card.html": ("provider.label", "Configured"),
            "users/list.html": (
                "Membership breakdown",
                "Active Stripe subscription vs. override grant",
            ),
        }
        for relative, (title, trailing) in expectations.items():
            template = source(relative)
            with self.subTest(relative=relative):
                self.assertLess(template.index(title), template.index(trailing))
