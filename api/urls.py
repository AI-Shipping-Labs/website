"""URL routes for the JSON API.

Routes mounted under ``/api/`` from ``website/urls.py``. This module
hosts the contacts endpoints (issue #431) and the plans-API surface
(issue #433). Every route is JSON-in / JSON-out and gated by
``token_required``.

Path style: no trailing slash. The site-wide
``RemoveTrailingSlashMiddleware`` strips trailing slashes from any
request that doesn't go to admin/accounts/studio, so ``/api/sprints/``
gets 301-redirected to ``/api/sprints``. We register the slashless form
to match the contacts endpoints and skip that redirect on every API
call.
"""

from django.urls import path

from api.views.aliases import user_aliases_add, user_aliases_remove
from api.views.articles import (
    article_preview_link,
    article_preview_token_regenerate,
)
from api.views.boot_timing import boot_timing_diagnostics
from api.views.campaigns import campaign_detail, campaign_recipients, campaigns_collection
from api.views.checkpoints import (
    checkpoint_detail,
    checkpoint_move,
    week_checkpoints_create,
)
from api.views.cleanup_gates import cleanup_gates_diagnostics
from api.views.contacts import (
    contacts_export,
    contacts_import,
    contacts_set_tags,
)
from api.views.course_certificates import (
    course_certificate_detail,
    course_certificates_collection,
)
from api.views.course_enrollments import (
    course_enrollment_detail,
    course_enrollments_collection,
)
from api.views.crm_export import crm_export
from api.views.docs import docs_page, openapi_json
from api.views.enrollments import (
    sprint_enrollment_detail,
    sprint_enrollments_collection,
)
from api.views.event_series import (
    event_series_collection,
    event_series_detail,
    event_series_occurrence_detail,
    event_series_occurrences_bulk,
    event_series_occurrences_reconcile,
    event_series_zoom_meetings,
)
from api.views.events import (
    event_detail,
    event_notify_workshop_ready,
    event_regenerate_banner,
    events_collection,
)
from api.views.hosts import host_detail, hosts_collection
from api.views.integration_settings import integration_settings
from api.views.interview_notes import (
    interview_note_detail,
    interview_notes_create,
    plan_interview_notes,
    user_interview_notes,
)
from api.views.marketing_pages import (
    marketing_page_detail,
    marketing_page_preview_link,
    marketing_page_preview_token_regenerate,
    marketing_pages_collection,
)
from api.views.onboarding import (
    onboarding_personas,
    onboarding_questionnaires,
    onboarding_response_detail,
    onboarding_responses_collection,
)
from api.views.payment_mismatches import (
    payment_mismatch_detail,
    payment_mismatches_collection,
)
from api.views.plan_items import (
    deliverable_detail,
    next_step_detail,
    plan_deliverables,
    plan_next_steps,
    plan_resources,
    resource_detail,
)
from api.views.plan_sprints_ingest import plan_sprints_ingest
from api.views.plans import (
    plan_detail,
    plan_draft_first_sprint,
    plan_draft_first_sprint_apply,
    plan_draft_next_sprint,
    plan_move_unfinished,
    sprint_partner_intro_emails,
    sprint_plans_bulk_import,
    sprint_plans_collection,
    sprint_plans_send_ready_emails,
)
from api.views.redirects import (
    redirect_detail,
    redirects_bulk_upsert,
    redirects_collection,
)
from api.views.ses_events_list import ses_events_dispatch
from api.views.signup_analytics import signup_analytics_report
from api.views.sprints import (
    sprint_accountability_partners,
    sprint_accountability_randomize,
    sprint_detail,
    sprint_progress_evidence,
    sprint_roster_activity,
    sprints_collection,
)
from api.views.sync_sources import sync_source_trigger, sync_sources_collection
from api.views.tier_overrides import tier_overrides_grant
from api.views.tier_reconcile import (
    tier_reconcile_apply,
    tier_reconcile_diagnostics,
)
from api.views.triggers import (
    deliveries_collection,
    emissions_collection,
    subscription_detail,
    subscriptions_collection,
    widget_detail,
    widgets_collection,
)
from api.views.user_merge import merge_users
from api.views.users import (
    user_activity,
    user_clear_bounce,
    user_crm_record,
    user_detail,
    user_email_log,
    user_mark_bounced,
    user_ses_events,
    user_tags_add,
    user_tags_remove,
    users_collection,
)
from api.views.utm_campaigns import (
    utm_campaign_detail,
    utm_campaign_link_detail,
    utm_campaign_links_collection,
    utm_campaigns_collection,
)
from api.views.weeks import plan_weeks_collection, week_detail, week_note_detail
from api.views.worker import (
    worker_task_detail,
    worker_tasks_collection,
    worker_tasks_failed,
)

urlpatterns = [
    # ---- API documentation (issue #722) -------------------------------
    # Staff-only Swagger UI page + raw OpenAPI JSON. These two routes
    # are deliberately EXCLUDED from the generated spec itself (see
    # ``api.openapi.builder._DOCS_ROUTE_NAMES``) -- the docs do not
    # document themselves. They also sit outside the ``token_required``
    # surface that the rest of the API uses, because an operator
    # pulling up ``/api/docs`` in a browser doesn't yet have a token
    # to authorize with; the Swagger UI page prompts for one.
    path(
        "openapi.json",
        openapi_json,
        name="api_openapi_json",
    ),
    path(
        "docs",
        docs_page,
        name="api_docs",
    ),
    # ---- Worker (issue #714) ------------------------------------------
    # Read-only worker task observability. Register the ``failed`` literal
    # and the bare collection BEFORE the ``<task_id>`` capture so neither
    # is swallowed by the 32-char hex id matcher. No write endpoints --
    # retry/delete/drain stay on Studio HTML (``/studio/worker/``).
    path(
        "worker/tasks/failed",
        worker_tasks_failed,
        name="api_worker_tasks_failed",
    ),
    path(
        "worker/tasks",
        worker_tasks_collection,
        name="api_worker_tasks_collection",
    ),
    path(
        "worker/tasks/<str:task_id>",
        worker_task_detail,
        name="api_worker_task_detail",
    ),
    # ---- Email campaigns (issue #676) ---------------------------------
    # Draft authoring only: GET/POST collection, GET/PATCH detail. No
    # DELETE route is registered (archive via PATCH is_archived=true).
    # No POST /send, /test-send, or /duplicate — sending stays in Studio.
    path(
        "campaigns",
        campaigns_collection,
        name="api_campaigns_collection",
    ),
    path(
        "campaigns/<int:campaign_id>",
        campaign_detail,
        name="api_campaign_detail",
    ),
    path(
        "campaigns/<int:campaign_id>/recipients",
        campaign_recipients,
        name="api_campaign_recipients",
    ),
    # ---- Articles ------------------------------------------------------
    path(
        "articles/<uuid:content_id>/preview-link",
        article_preview_link,
        name="api_article_preview_link",
    ),
    path(
        "articles/<uuid:content_id>/preview-token/regenerate",
        article_preview_token_regenerate,
        name="api_article_preview_token_regenerate",
    ),
    # ---- Marketing pages ----------------------------------------------
    path(
        "marketing-pages",
        marketing_pages_collection,
        name="api_marketing_pages_collection",
    ),
    path(
        "marketing-pages/<uuid:content_id>/preview-link",
        marketing_page_preview_link,
        name="api_marketing_page_preview_link",
    ),
    path(
        "marketing-pages/<uuid:content_id>/preview-token/regenerate",
        marketing_page_preview_token_regenerate,
        name="api_marketing_page_preview_token_regenerate",
    ),
    path(
        "marketing-pages/<uuid:content_id>",
        marketing_page_detail,
        name="api_marketing_page_detail",
    ),
    # ---- Events (issue #627) ------------------------------------------
    path(
        "events",
        events_collection,
        name="api_events_collection",
    ),
    # Register the ``regenerate-banner`` action BEFORE the bare ``<slug>``
    # detail so the slug converter never swallows the literal suffix (issue
    # #995). Force-enqueues a banner render; allowed for synced events.
    path(
        "events/<slug:slug>/regenerate-banner",
        event_regenerate_banner,
        name="api_event_regenerate_banner",
    ),
    path(
        "events/<slug:slug>/notify-workshop-ready",
        event_notify_workshop_ready,
        name="api_event_notify_workshop_ready",
    ),
    path(
        "events/<slug:slug>",
        event_detail,
        name="api_event_detail",
    ),
    # ---- Host profiles (issue #1031) ----------------------------------
    path(
        "hosts",
        hosts_collection,
        name="api_hosts_collection",
    ),
    path(
        "hosts/<slug:slug>",
        host_detail,
        name="api_host_detail",
    ),
    # ---- Event series + bulk occurrences (issue #678) -----------------
    # Register the bulk literal BEFORE the per-occurrence id capture so
    # the ``occurrences/bulk`` literal is not swallowed by ``<int>``.
    # Matches the ``sprints/<slug>/plans/bulk-import`` precedent.
    path(
        "event-series",
        event_series_collection,
        name="api_event_series_collection",
    ),
    path(
        "event-series/<int:series_id>",
        event_series_detail,
        name="api_event_series_detail",
    ),
    path(
        "event-series/<int:series_id>/occurrences/bulk",
        event_series_occurrences_bulk,
        name="api_event_series_occurrences_bulk",
    ),
    # ---- Idempotent schedule-replace (issue #878) ---------------------
    # PUT-only. The bare ``occurrences`` literal must stay BEFORE the
    # ``occurrences/<int:occurrence_id>`` capture so the ``<int>`` converter
    # never swallows it (same ordering discipline as ``occurrences/bulk``).
    path(
        "event-series/<int:series_id>/occurrences",
        event_series_occurrences_reconcile,
        name="api_event_series_occurrences_reconcile",
    ),
    # ---- Bulk Zoom-meeting creation for a series (issue #932) ----------
    # POST-only. The ``zoom-meetings`` literal must stay BEFORE the
    # ``occurrences/<int:occurrence_id>`` capture so an ``<int>`` converter
    # never swallows it (same ordering discipline as ``occurrences/bulk``).
    path(
        "event-series/<int:series_id>/zoom-meetings",
        event_series_zoom_meetings,
        name="api_event_series_zoom_meetings",
    ),
    path(
        "event-series/<int:series_id>/occurrences/<int:occurrence_id>",
        event_series_occurrence_detail,
        name="api_event_series_occurrence_detail",
    ),
    # ---- Integration settings (issues #633, #640) ---------------------
    # GET lists registered keys with metadata + source enum but never the
    # value itself; POST mutates rows; everything else returns 405.
    path(
        "integrations/settings",
        integration_settings,
        name="api_integration_settings",
    ),
    # ---- Plan-sprints ingest / backfill trigger (issue #904) ----------
    # Staff-token POST that enqueues the same capture + parse + auto-apply
    # path the daily schedule runs, with an optional ``since`` for a
    # retroactive backfill and ``dry_run`` for a no-write preview.
    path(
        "integrations/slack/plan-sprints/ingest",
        plan_sprints_ingest,
        name="api_plan_sprints_ingest",
    ),
    # ---- Content sync sources (issue #634) ----------------------------
    path(
        "sync/sources",
        sync_sources_collection,
        name="api_sync_sources_collection",
    ),
    path(
        "sync/sources/<uuid:source_id>/trigger",
        sync_source_trigger,
        name="api_sync_source_trigger",
    ),
    # ---- URL redirects (issue #674) -----------------------------------
    # Register the ``bulk`` literal BEFORE the ``<int:id>`` capture so the
    # literal does not collide with the integer converter.
    path(
        "redirects/bulk",
        redirects_bulk_upsert,
        name="api_redirects_bulk_upsert",
    ),
    path(
        "redirects",
        redirects_collection,
        name="api_redirects_collection",
    ),
    path(
        "redirects/<int:redirect_id>",
        redirect_detail,
        name="api_redirect_detail",
    ),
    # ---- UTM campaigns + tracked links (issue #875) -------------------
    # Token-authenticated CRUD over UtmCampaign / UtmCampaignLink. No DELETE
    # route is registered (archive via PATCH is_archived=true), so a DELETE
    # request falls through to the require_methods 405. Register the
    # ``<id>/links`` sub-routes (more segments) BEFORE the bare ``<id>``
    # detail so the precedence is explicit.
    path(
        "utm-campaigns",
        utm_campaigns_collection,
        name="api_utm_campaigns_collection",
    ),
    path(
        "utm-campaigns/<int:campaign_id>/links",
        utm_campaign_links_collection,
        name="api_utm_campaign_links_collection",
    ),
    path(
        "utm-campaigns/<int:campaign_id>/links/<int:link_id>",
        utm_campaign_link_detail,
        name="api_utm_campaign_link_detail",
    ),
    path(
        "utm-campaigns/<int:campaign_id>",
        utm_campaign_detail,
        name="api_utm_campaign_detail",
    ),
    # ---- Signup analytics (issue #1175) --------------------------------
    path(
        "signup-analytics",
        signup_analytics_report,
        name="api_signup_analytics",
    ),
    # ---- Contacts (issue #431) ----------------------------------------
    path(
        "contacts/import",
        contacts_import,
        name="api_contacts_import",
    ),
    path(
        "contacts/export",
        contacts_export,
        name="api_contacts_export",
    ),
    # Email contains '@' and '.' which the slug converter doesn't match;
    # use the path converter so the address is captured intact.
    path(
        "contacts/<path:email>/tags",
        contacts_set_tags,
        name="api_contacts_set_tags",
    ),
    # ---- Tier overrides (issue #833) ----------------------------------
    # Staff-token grant of a 10-year ``main`` (or higher) TierOverride for
    # non-paying members. Mirrors the Studio contact-import override but is
    # an explicitly-named endpoint so the privileged action is discoverable
    # and impossible to trigger by accident from the contacts surface.
    path(
        "tier-overrides",
        tier_overrides_grant,
        name="api_tier_overrides_grant",
    ),
    # ---- Sprints (issue #433) -----------------------------------------
    path(
        "sprints",
        sprints_collection,
        name="api_sprints_collection",
    ),
    path(
        "sprints/<slug:slug>/progress-evidence",
        sprint_progress_evidence,
        name="api_sprint_progress_evidence",
    ),
    path(
        "sprints/<slug:slug>/roster-activity",
        sprint_roster_activity,
        name="api_sprint_roster_activity",
    ),
    path(
        "sprints/<slug:slug>/accountability-partners/randomize",
        sprint_accountability_randomize,
        name="api_sprint_accountability_randomize",
    ),
    path(
        "sprints/<slug:slug>/accountability-partners",
        sprint_accountability_partners,
        name="api_sprint_accountability_partners",
    ),
    path(
        "sprints/<slug:slug>",
        sprint_detail,
        name="api_sprint_detail",
    ),
    # ---- Sprint enrollments (issue #443) ------------------------------
    # Register the collection BEFORE the per-email detail so the
    # ``enrollments`` literal isn't swallowed by the path converter
    # capturing an email like ``foo@bar.com/extra``.
    path(
        "sprints/<slug:slug>/enrollments",
        sprint_enrollments_collection,
        name="api_sprint_enrollments_collection",
    ),
    path(
        "sprints/<slug:slug>/enrollments/<path:email>",
        sprint_enrollment_detail,
        name="api_sprint_enrollment_detail",
    ),
    # ---- Course enrollments (issue #445) ------------------------------
    # Register the collection BEFORE the per-email detail so the
    # ``enrollments`` literal isn't swallowed by the path converter
    # capturing an email like ``alice@example.com/extra``.
    path(
        "courses/<slug:slug>/enrollments",
        course_enrollments_collection,
        name="api_course_enrollments_collection",
    ),
    path(
        "courses/<slug:slug>/enrollments/<path:email>",
        course_enrollment_detail,
        name="api_course_enrollment_detail",
    ),
    # ---- Course certificates (issue #445) -----------------------------
    path(
        "courses/<slug:slug>/certificates",
        course_certificates_collection,
        name="api_course_certificates_collection",
    ),
    path(
        "courses/<slug:slug>/certificates/<path:email>",
        course_certificate_detail,
        name="api_course_certificate_detail",
    ),
    # ---- Plans (issue #433) -------------------------------------------
    # Plan action literals come BEFORE the generic plans collection so
    # they do not collide with the slug captures.
    path(
        "sprints/<slug:slug>/plans/bulk-import",
        sprint_plans_bulk_import,
        name="api_sprint_plans_bulk_import",
    ),
    path(
        "sprints/<slug:slug>/plans/send-ready-emails",
        sprint_plans_send_ready_emails,
        name="api_sprint_plans_send_ready_emails",
    ),
    path(
        "sprints/<slug:slug>/partner-intro-emails",
        sprint_partner_intro_emails,
        name="api_sprint_partner_intro_emails",
    ),
    path(
        "sprints/<slug:slug>/plans",
        sprint_plans_collection,
        name="api_sprint_plans_collection",
    ),
    path(
        "plans/<int:plan_id>",
        plan_detail,
        name="api_plan_detail",
    ),
    path(
        "plans/<int:plan_id>/move-unfinished",
        plan_move_unfinished,
        name="api_plan_move_unfinished",
    ),
    # Carry-over + AI next-sprint draft (issue #891, Phase 3). Staff-only;
    # same shared service as the Studio "Draft next sprint plan" button.
    path(
        "plans/<int:plan_id>/draft-next-sprint",
        plan_draft_next_sprint,
        name="api_plan_draft_next_sprint",
    ),
    path(
        "plans/<int:plan_id>/draft-first-sprint",
        plan_draft_first_sprint,
        name="api_plan_draft_first_sprint",
    ),
    path(
        "plans/<int:plan_id>/draft-first-sprint/apply",
        plan_draft_first_sprint_apply,
        name="api_plan_draft_first_sprint_apply",
    ),
    # ---- Weeks (issue #433) -------------------------------------------
    path(
        "plans/<int:plan_id>/weeks",
        plan_weeks_collection,
        name="api_plan_weeks_collection",
    ),
    path(
        "weeks/<int:week_id>",
        week_detail,
        name="api_week_detail",
    ),
    path(
        "weeks/<int:week_id>/note",
        week_note_detail,
        name="api_week_note_detail",
    ),
    # ---- Checkpoints (issue #433) -------------------------------------
    path(
        "weeks/<int:week_id>/checkpoints",
        week_checkpoints_create,
        name="api_week_checkpoints_create",
    ),
    path(
        "checkpoints/<int:checkpoint_id>/move",
        checkpoint_move,
        name="api_checkpoint_move",
    ),
    path(
        "checkpoints/<int:checkpoint_id>",
        checkpoint_detail,
        name="api_checkpoint_detail",
    ),
    # ---- Resources / Deliverables / NextSteps (issue #433) ------------
    path(
        "plans/<int:plan_id>/resources",
        plan_resources,
        name="api_plan_resources",
    ),
    path(
        "resources/<int:item_id>",
        resource_detail,
        name="api_resource_detail",
    ),
    path(
        "plans/<int:plan_id>/deliverables",
        plan_deliverables,
        name="api_plan_deliverables",
    ),
    path(
        "deliverables/<int:item_id>",
        deliverable_detail,
        name="api_deliverable_detail",
    ),
    path(
        "plans/<int:plan_id>/next-steps",
        plan_next_steps,
        name="api_plan_next_steps",
    ),
    path(
        "next-steps/<int:item_id>",
        next_step_detail,
        name="api_next_step_detail",
    ),
    # ---- Interview notes (issue #433) ---------------------------------
    path(
        "plans/<int:plan_id>/interview-notes",
        plan_interview_notes,
        name="api_plan_interview_notes",
    ),
    path(
        "users/<path:email>/interview-notes",
        user_interview_notes,
        name="api_user_interview_notes",
    ),
    path(
        "users/<path:email>/notes",
        user_interview_notes,
        name="api_user_member_notes",
    ),
    path(
        "users/<path:email>/notes/",
        user_interview_notes,
    ),
    # ---- User Management API (issue #764) -----------------------------
    # Read state + read SES history + safe writes (unsubscribe, tags,
    # verify). The tag-DELETE route is registered BEFORE the tag-POST
    # collection so the ``<str:tag>`` capture lands. The single-user
    # detail route is registered AFTER the more-specific sub-resources
    # (``ses-events``, ``email-log``, ``tags``) so the ``<path:email>``
    # converter does not swallow the literal suffix.
    path(
        "users/<path:email>/ses-events",
        user_ses_events,
        name="api_user_ses_events",
    ),
    path(
        "users/<path:email>/email-log",
        user_email_log,
        name="api_user_email_log",
    ),
    path(
        "users/<path:email>/activity",
        user_activity,
        name="api_user_activity",
    ),
    path(
        "users/<path:email>/tags/<str:tag>",
        user_tags_remove,
        name="api_user_tag_remove",
    ),
    path(
        "users/<path:email>/tags",
        user_tags_add,
        name="api_user_tags_add",
    ),
    # Mark-bounced (issue #784). Registered BEFORE the ``users/<path:email>``
    # catch-all so the literal ``mark-bounced`` suffix is not swallowed by
    # the path converter.
    path(
        "users/<path:email>/mark-bounced",
        user_mark_bounced,
        name="api_user_mark_bounced",
    ),
    path(
        "users/<path:email>/clear-bounce",
        user_clear_bounce,
        name="api_user_clear_bounce",
    ),
    # Email aliases (issue #840a). Staff-token add/remove of an alias that
    # routes a billing/relay email to a canonical account so future Stripe
    # webhooks resolve correctly. Register the DELETE (with the trailing
    # ``<path:alias_email>`` segment, which carries ``@``/``.``) BEFORE the
    # POST collection, and both BEFORE the ``users/<path:email>`` catch-all,
    # mirroring the tag-remove-before-tag-add ordering above.
    path(
        "users/<path:email>/aliases/<path:alias_email>",
        user_aliases_remove,
        name="api_user_aliases_remove",
    ),
    path(
        "users/<path:email>/aliases",
        user_aliases_add,
        name="api_user_aliases_add",
    ),
    # Account merge (issue #841). Register the literal ``users/merge`` BEFORE
    # the ``users/<path:email>`` catch-all -- otherwise the path converter
    # swallows ``merge`` as an email. Same ordering discipline as the
    # mark-bounced / aliases routes above.
    path(
        "users/merge",
        merge_users,
        name="api_user_merge",
    ),
    path(
        "users/payment-mismatches",
        payment_mismatches_collection,
        name="api_payment_mismatches_collection",
    ),
    path(
        "users/payment-mismatches/<int:mismatch_id>",
        payment_mismatch_detail,
        name="api_payment_mismatch_detail",
    ),
    path(
        "users",
        users_collection,
        name="api_users_collection",
    ),
    path(
        "users/<path:email>/crm-record",
        user_crm_record,
        name="api_user_crm_record",
    ),
    path(
        "users/<path:email>",
        user_detail,
        name="api_user_detail",
    ),
    path(
        "interview-notes",
        interview_notes_create,
        name="api_interview_notes_create",
    ),
    path(
        "member-notes",
        interview_notes_create,
        name="api_member_notes_create",
    ),
    path(
        "member-notes/",
        interview_notes_create,
    ),
    path(
        "interview-notes/<int:note_id>",
        interview_note_detail,
        name="api_interview_note_detail",
    ),
    path(
        "member-notes/<int:note_id>",
        interview_note_detail,
        name="api_member_note_detail",
    ),
    path(
        "member-notes/<int:note_id>/",
        interview_note_detail,
    ),
    # ---- Tier reconciliation (issue #621) -----------------------------
    # Diagnostics is registered BEFORE the apply route so the
    # ``diagnostics`` literal does not collide with the apply path.
    path(
        "payments/tier-reconcile/diagnostics",
        tier_reconcile_diagnostics,
        name="api_tier_reconcile_diagnostics",
    ),
    path(
        "payments/tier-reconcile",
        tier_reconcile_apply,
        name="api_tier_reconcile_apply",
    ),
    # ---- Cleanup-gate diagnostics (issue #1087) -----------------------
    # Staff-token read-only counts the blocked cleanups (#1016 / #1018 /
    # #1017) gate on. Integer counts only -- no PII. No captures, so no
    # converter/literal collision concerns. Slashless like every /api route.
    path(
        "diagnostics/cleanup-gates",
        cleanup_gates_diagnostics,
        name="api_cleanup_gates_diagnostics",
    ),
    # ---- Boot-timing diagnostics (issue #1142) ------------------------
    # Staff-token read-only per-phase container cold-start timings for the
    # web and worker tiers, persisted to the shared ``django_q`` cache by
    # ``scripts/entrypoint_init.py`` on every boot. No captures, so no
    # converter/literal collision concerns. Slashless like every /api route.
    path(
        "diagnostics/boot-timing",
        boot_timing_diagnostics,
        name="api_boot_timing_diagnostics",
    ),
    # ---- Onboarding read API (issue #837) -----------------------------
    # Staff-token read-only feed over the questionnaires app: survey shape
    # (questionnaires/personas) + member responses for plan generation.
    # Register the bulk ``responses`` collection literal BEFORE the
    # per-email ``responses/<path:email>`` detail so the path converter
    # (which matches an email's ``@``/``.``) does not swallow the literal,
    # mirroring the enrollments collection-before-detail precedent above.
    path(
        "onboarding/questionnaires",
        onboarding_questionnaires,
        name="api_onboarding_questionnaires",
    ),
    path(
        "onboarding/personas",
        onboarding_personas,
        name="api_onboarding_personas",
    ),
    path(
        "onboarding/responses",
        onboarding_responses_collection,
        name="api_onboarding_responses_collection",
    ),
    path(
        "onboarding/responses/<path:email>",
        onboarding_response_detail,
        name="api_onboarding_response_detail",
    ),
    # ---- SES events: webhook (POST) + aggregate list (GET) -------------
    # SNS POSTs bounce/complaint notifications here (auth = SNS signature,
    # not a token). Issue #829 adds a token-gated GET aggregate list on the
    # same canonical path via ``ses_events_dispatch``: GET -> list view,
    # any other method -> the unchanged webhook. The slashless form is
    # canonical so the trailing-slash middleware doesn't 301 the SNS POST.
    path(
        "ses-events",
        ses_events_dispatch,
        name="api_ses_events",
    ),
    # ---- CRM export (issue #1079) -------------------------------------
    # Staff-token read-only aggregate: the full per-user CRM record (state +
    # crm_record + notes + nested plans + enrollments + onboarding
    # responses) in one call. Slashless form like every other /api route.
    path(
        "crm/export",
        crm_export,
        name="api_crm_export",
    ),
    # ---- Event triggers (issue #1070) ---------------------------------
    # Staff-token-gated management + observability. No DELETE: deactivate
    # via ``is_active``. Subscription/widget secrets are never returned.
    path(
        "triggers/subscriptions",
        subscriptions_collection,
        name="api_trigger_subscriptions",
    ),
    path(
        "triggers/subscriptions/<int:subscription_id>",
        subscription_detail,
        name="api_trigger_subscription_detail",
    ),
    path(
        "triggers/widgets",
        widgets_collection,
        name="api_trigger_widgets",
    ),
    path(
        "triggers/widgets/<int:widget_id>",
        widget_detail,
        name="api_trigger_widget_detail",
    ),
    path(
        "triggers/emissions",
        emissions_collection,
        name="api_trigger_emissions",
    ),
    path(
        "triggers/deliveries",
        deliveries_collection,
        name="api_trigger_deliveries",
    ),
]
