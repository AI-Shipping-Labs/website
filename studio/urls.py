from django.urls import path
from django.views.generic import RedirectView

from studio.views.announcement import announcement_banner_edit
from studio.views.api_tokens import (
    studio_api_token_create,
    studio_api_token_created,
    studio_api_token_list,
    studio_api_token_revoke,
)
from studio.views.articles import article_edit, article_list
from studio.views.campaigns import (
    campaign_create,
    campaign_delete,
    campaign_detail,
    campaign_duplicate,
    campaign_edit,
    campaign_list,
    campaign_send,
    campaign_test_send,
)
from studio.views.contacts_import import user_import, user_import_confirm
from studio.views.content_sources import (
    content_source_create,
    content_source_refresh,
)
from studio.views.courses import (
    course_access_grant,
    course_access_list,
    course_access_revoke,
    course_create_stripe_product,
    course_edit,
    course_list,
    course_user_search,
    module_create,
    module_reorder,
    unit_create,
    unit_edit,
)
from studio.views.crm import (
    crm_archive,
    crm_detail,
    crm_edit,
    crm_list,
    crm_reactivate,
    crm_track,
)
from studio.views.dashboard import dashboard
from studio.views.downloads import download_edit, download_list
from studio.views.email_templates import (
    email_template_edit,
    email_template_list,
    email_template_preview,
    email_template_reset,
    email_template_send_test,
)
from studio.views.enrollments import (
    enrollment_create,
    enrollment_list,
    enrollment_unenroll,
)
from studio.views.event_series import (
    event_series_add_occurrence,
    event_series_create,
    event_series_delete,
    event_series_detail,
    event_series_list,
)
from studio.views.events import event_create, event_create_zoom, event_edit, event_list
from studio.views.impersonate import impersonate_user, stop_impersonation
from studio.views.integration_docs import integration_docs
from studio.views.member_notes import (
    member_note_create,
    member_note_delete,
    member_note_edit,
)
from studio.views.notifications import (
    article_announce_slack,
    article_notify,
    course_announce_slack,
    course_notify,
    download_announce_slack,
    download_notify,
    event_announce_slack,
    event_notify,
    notification_log,
    recording_announce_slack,
    recording_notify,
)
from studio.views.peer_reviews import (
    peer_review_extend_deadline,
    peer_review_form_batch,
    peer_review_issue_certificates,
    peer_review_management,
)
from studio.views.plans import (
    interview_note_create,
    interview_note_delete,
    interview_note_edit,
    plan_create,
    plan_detail,
    plan_edit,
    plan_list,
)
from studio.views.projects import project_list, project_review
from studio.views.recordings import recording_edit, recording_list, recording_publish_youtube
from studio.views.redirects import redirect_create, redirect_delete, redirect_edit, redirect_list, redirect_toggle
from studio.views.settings import (
    settings_dashboard,
    settings_export,
    settings_import,
    settings_save_auth_provider,
    settings_save_group,
)
from studio.views.sprints import (
    sprint_add_member,
    sprint_create,
    sprint_detail,
    sprint_edit,
    sprint_list,
)
from studio.views.sprints_enroll import sprint_bulk_enroll
from studio.views.sync import (
    content_sources_export,
    content_sources_import,
    sync_all,
    sync_dashboard,
    sync_history,
    sync_object_trigger,
    sync_repo_trigger,
    sync_status,
    sync_trigger,
)
from studio.views.tier_overrides import (
    legacy_tier_override_create_redirect,
    legacy_tier_override_page_redirect,
    legacy_tier_override_revoke_redirect,
    legacy_user_tier_override_action_redirect,
    studio_user_search,
    tier_override_page,
    user_tier_override_page,
)
from studio.views.user_imports import (
    import_batch_detail,
    import_batch_fragment,
    import_batch_list,
    import_batch_new,
    import_batch_rerun,
    import_schedule_toggle,
)
from studio.views.users import (
    user_create,
    user_create_done,
    user_detail,
    user_export_csv,
    user_list,
    user_slack_id_set,
    user_sync_from_stripe,
    user_tag_add,
    user_tag_remove,
    user_tier_override_create,
    user_tier_override_revoke,
)
from studio.views.utm_analytics import (
    utm_campaign_detail as utm_analytics_campaign_detail,
)
from studio.views.utm_analytics import (
    utm_dashboard,
)
from studio.views.utm_analytics import (
    utm_link_detail as utm_analytics_link_detail,
)
from studio.views.utm_campaigns import (
    utm_campaign_archive,
    utm_campaign_create,
    utm_campaign_detail,
    utm_campaign_edit,
    utm_campaign_import,
    utm_campaign_list,
    utm_campaign_unarchive,
    utm_link_archive,
    utm_link_create,
    utm_link_edit,
)
from studio.views.worker import (
    worker_bulk_delete_failed,
    worker_bulk_retry_failed,
    worker_delete_failed,
    worker_delete_queued,
    worker_drain_queue,
    worker_inspect_task,
    worker_retry_failed,
    worker_status,
    worker_task_detail,
)
from studio.views.workshops import (
    workshop_detail,
    workshop_edit,
    workshop_list,
    workshop_resync,
)

urlpatterns = [
    # Dashboard
    path('', dashboard, name='studio_dashboard'),

    # Courses
    path('courses/', course_list, name='studio_course_list'),
    path('courses/<int:course_id>/edit', course_edit, name='studio_course_edit'),
    path('courses/<int:course_id>/modules/add', module_create, name='studio_module_create'),
    path('courses/<int:course_id>/modules/reorder', module_reorder, name='studio_module_reorder'),
    path('modules/<int:module_id>/units/add', unit_create, name='studio_unit_create'),
    path('units/<int:unit_id>/edit', unit_edit, name='studio_unit_edit'),
    path('courses/<int:course_id>/notify', course_notify, name='studio_course_notify'),
    path('courses/<int:course_id>/announce-slack', course_announce_slack, name='studio_course_announce_slack'),
    path('courses/<int:course_id>/create-stripe-product', course_create_stripe_product, name='studio_course_create_stripe_product'),
    path('courses/<int:course_id>/peer-reviews', peer_review_management, name='studio_peer_review_management'),
    path('courses/<int:course_id>/peer-reviews/form-batch', peer_review_form_batch, name='studio_peer_review_form_batch'),
    path('courses/<int:course_id>/peer-reviews/issue-certificates', peer_review_issue_certificates, name='studio_peer_review_issue_certificates'),
    path('courses/<int:course_id>/peer-reviews/extend-deadline', peer_review_extend_deadline, name='studio_peer_review_extend_deadline'),
    path('courses/<int:course_id>/access/', course_access_list, name='studio_course_access_list'),
    path('courses/<int:course_id>/access/grant/', course_access_grant, name='studio_course_access_grant'),
    path('courses/<int:course_id>/access/<int:access_id>/revoke/', course_access_revoke, name='studio_course_access_revoke'),
    path('courses/<int:course_id>/access/users/search/', course_user_search, name='studio_course_user_search'),

    # Enrollments scoped to a course (issue #293; superseding #236)
    path(
        'courses/<int:course_id>/enrollments/',
        enrollment_list,
        name='studio_course_enrollment_list',
    ),
    path(
        'courses/<int:course_id>/enrollments/create',
        enrollment_create,
        name='studio_course_enrollment_create',
    ),
    path(
        'courses/<int:course_id>/enrollments/<int:enrollment_id>/unenroll',
        enrollment_unenroll,
        name='studio_course_enrollment_unenroll',
    ),

    # Articles
    path('articles/', article_list, name='studio_article_list'),
    path('articles/<int:article_id>/edit', article_edit, name='studio_article_edit'),
    path('articles/<int:article_id>/notify', article_notify, name='studio_article_notify'),
    path('articles/<int:article_id>/announce-slack', article_announce_slack, name='studio_article_announce_slack'),

    # Events. The literal ``new`` route is registered before the
    # ``<int:event_id>`` routes so the slug is not swallowed (issue #574).
    path('events/', event_list, name='studio_event_list'),
    path('events/new', event_create, name='studio_event_new'),
    path('events/<int:event_id>/edit', event_edit, name='studio_event_edit'),
    path('events/<int:event_id>/create-zoom', event_create_zoom, name='studio_event_create_zoom'),
    path('events/<int:event_id>/notify', event_notify, name='studio_event_notify'),
    path('events/<int:event_id>/announce-slack', event_announce_slack, name='studio_event_announce_slack'),

    # Event series (issue #564, renamed from event-groups in #575). The
    # literal ``new`` route is registered before the ``<int:series_id>``
    # routes so the slug is not swallowed.
    path('event-series/', event_series_list, name='studio_event_series_list'),
    path('event-series/new', event_series_create, name='studio_event_series_new'),
    path(
        'event-series/<int:series_id>/',
        event_series_detail,
        name='studio_event_series_detail',
    ),
    path(
        'event-series/<int:series_id>/add-occurrence',
        event_series_add_occurrence,
        name='studio_event_series_add_occurrence',
    ),
    path(
        'event-series/<int:series_id>/delete',
        event_series_delete,
        name='studio_event_series_delete',
    ),

    # Backward-compat 301 redirects from the old ``/studio/event-groups/``
    # URLs to the new ``/studio/event-series/`` URLs (issue #575). External
    # Studio bookmarks keep working through the rename. These five
    # patterns mirror the new routes above one-to-one.
    path(
        'event-groups/',
        RedirectView.as_view(url='/studio/event-series/', permanent=True),
    ),
    path(
        'event-groups/new',
        RedirectView.as_view(url='/studio/event-series/new', permanent=True),
    ),
    path(
        'event-groups/<int:series_id>/',
        RedirectView.as_view(
            url='/studio/event-series/%(series_id)s/', permanent=True,
        ),
    ),
    path(
        'event-groups/<int:series_id>/add-occurrence',
        RedirectView.as_view(
            url='/studio/event-series/%(series_id)s/add-occurrence',
            permanent=True,
        ),
    ),
    path(
        'event-groups/<int:series_id>/delete',
        RedirectView.as_view(
            url='/studio/event-series/%(series_id)s/delete', permanent=True,
        ),
    ),

    # Workshops (issue #297)
    # ``resync/`` is registered before ``<int:workshop_id>/`` so the
    # literal string isn't swallowed by the int converter.
    path('workshops/', workshop_list, name='studio_workshop_list'),
    path('workshops/resync/', workshop_resync, name='studio_workshop_resync'),
    path('workshops/<int:workshop_id>/', workshop_detail, name='studio_workshop_detail'),
    path('workshops/<int:workshop_id>/edit', workshop_edit, name='studio_workshop_edit'),

    # Recordings
    path('recordings/', recording_list, name='studio_recording_list'),
    path('recordings/<int:recording_id>/edit', recording_edit, name='studio_recording_edit'),
    path('recordings/<int:recording_id>/publish-youtube', recording_publish_youtube, name='studio_recording_publish_youtube'),
    path('recordings/<int:recording_id>/notify', recording_notify, name='studio_recording_notify'),
    path('recordings/<int:recording_id>/announce-slack', recording_announce_slack, name='studio_recording_announce_slack'),

    # Campaigns
    path('campaigns/', campaign_list, name='studio_campaign_list'),
    path('campaigns/new', campaign_create, name='studio_campaign_create'),
    path('campaigns/<int:campaign_id>/', campaign_detail, name='studio_campaign_detail'),
    path(
        'campaigns/<int:campaign_id>/edit',
        campaign_edit,
        name='studio_campaign_edit',
    ),
    path(
        'campaigns/<int:campaign_id>/delete',
        campaign_delete,
        name='studio_campaign_delete',
    ),
    path(
        'campaigns/<int:campaign_id>/duplicate',
        campaign_duplicate,
        name='studio_campaign_duplicate',
    ),
    path(
        'campaigns/<int:campaign_id>/send',
        campaign_send,
        name='studio_campaign_send',
    ),
    path(
        'campaigns/<int:campaign_id>/test-send',
        campaign_test_send,
        name='studio_campaign_test_send',
    ),

    # UTM Campaigns
    path('utm-campaigns/', utm_campaign_list, name='studio_utm_campaign_list'),
    path('utm-campaigns/new', utm_campaign_create, name='studio_utm_campaign_create'),
    path('utm-campaigns/import', utm_campaign_import, name='studio_utm_campaign_import'),
    path('utm-campaigns/<int:campaign_id>/', utm_campaign_detail, name='studio_utm_campaign_detail'),
    path('utm-campaigns/<int:campaign_id>/edit', utm_campaign_edit, name='studio_utm_campaign_edit'),
    path('utm-campaigns/<int:campaign_id>/archive', utm_campaign_archive, name='studio_utm_campaign_archive'),
    path('utm-campaigns/<int:campaign_id>/unarchive', utm_campaign_unarchive, name='studio_utm_campaign_unarchive'),
    path('utm-campaigns/<int:campaign_id>/links/add', utm_link_create, name='studio_utm_link_create'),
    path('utm-campaigns/<int:campaign_id>/links/<int:link_id>/edit', utm_link_edit, name='studio_utm_link_edit'),
    path('utm-campaigns/<int:campaign_id>/links/<int:link_id>/archive', utm_link_archive, name='studio_utm_link_archive'),

    # UTM Analytics
    path('utm-analytics/', utm_dashboard, name='studio_utm_dashboard'),
    path(
        'utm-analytics/campaign/<slug:campaign_slug>/',
        utm_analytics_campaign_detail,
        name='studio_utm_campaign_analytics',
    ),
    path(
        'utm-analytics/campaign/<slug:campaign_slug>/link/<int:link_id>/',
        utm_analytics_link_detail,
        name='studio_utm_link_analytics',
    ),

    # Impersonate
    path('impersonate/<int:user_id>/', impersonate_user, name='studio_impersonate'),
    path('impersonate/stop/', stop_impersonation, name='studio_stop_impersonate'),

    # Notifications
    path('notifications/', notification_log, name='studio_notification_log'),

    # Downloads
    path('downloads/', download_list, name='studio_download_list'),
    path('downloads/<int:download_id>/edit', download_edit, name='studio_download_edit'),
    path('downloads/<int:download_id>/notify', download_notify, name='studio_download_notify'),
    path('downloads/<int:download_id>/announce-slack', download_announce_slack, name='studio_download_announce_slack'),

    # Projects
    path('projects/', project_list, name='studio_project_list'),
    path('projects/<int:project_id>/review', project_review, name='studio_project_review'),

    # Tier Overrides
    path('tier_overrides/', tier_override_page, name='studio_tier_overrides_list'),
    path('tier_overrides/', tier_override_page, name='studio_tier_override'),
    path('api/users/search/', studio_user_search, name='studio_user_search'),
    path(
        'users/tier-override/',
        legacy_tier_override_page_redirect,
        name='studio_legacy_tier_override',
    ),
    path(
        'users/tier-override/create',
        legacy_tier_override_create_redirect,
        name='studio_tier_override_create',
    ),
    path(
        'users/tier-override/revoke',
        legacy_tier_override_revoke_redirect,
        name='studio_tier_override_revoke',
    ),

    # Users list + CSV export (issue #271)
    path('users/', user_list, name='studio_user_list'),
    path('users/export', user_export_csv, name='studio_user_export'),

    # External user-import pipeline (issue #317)
    path('imports/', import_batch_list, name='studio_import_batch_list'),
    path('imports/new/', import_batch_new, name='studio_import_batch_new'),
    path('imports/<int:batch_id>/', import_batch_detail, name='studio_import_batch_detail'),
    path('imports/<int:batch_id>/fragment/', import_batch_fragment, name='studio_import_batch_fragment'),
    path('imports/<int:batch_id>/rerun/', import_batch_rerun, name='studio_import_batch_rerun'),
    path('imports/schedules/<slug:source>/toggle/', import_schedule_toggle, name='studio_import_schedule_toggle'),

    # Manually create user (issue #234)
    path('users/new/', user_create, name='studio_user_create'),
    path('users/created/', user_create_done, name='studio_user_create_done'),

    # Bulk import contacts from CSV (issue #356). Registered before the
    # ``<int:user_id>/`` route so the literal ``import/`` prefix is not
    # swallowed by the int converter.
    path('users/import/', user_import, name='studio_user_import'),
    path('users/import/confirm', user_import_confirm, name='studio_user_import_confirm'),

    # User detail page + contact tags (issue #354). The literal ``new/``,
    # ``created/``, ``export``, and ``tier-override/`` prefixes are
    # registered above so the ``<int:user_id>`` route does not swallow them.
    path('users/<int:user_id>/', user_detail, name='studio_user_detail'),
    path(
        'users/<int:user_id>/sync-from-stripe/',
        user_sync_from_stripe,
        name='studio_user_sync_from_stripe',
    ),
    path(
        'users/<int:user_id>/notes/new',
        member_note_create,
        name='studio_member_note_create',
    ),
    path(
        'users/<int:user_id>/notes/<int:note_id>/edit',
        member_note_edit,
        name='studio_member_note_edit',
    ),
    path(
        'users/<int:user_id>/notes/<int:note_id>/delete',
        member_note_delete,
        name='studio_member_note_delete',
    ),
    path('users/<int:user_id>/tags/add', user_tag_add, name='studio_user_tag_add'),
    path('users/<int:user_id>/tags/remove', user_tag_remove, name='studio_user_tag_remove'),
    # Manual Slack ID edit (issue #561). POST-only; the GET surface lives
    # on /studio/users/<id>/ where the inline form is rendered.
    path(
        'users/<int:user_id>/slack-id/',
        user_slack_id_set,
        name='studio_user_slack_id_set',
    ),
    # Inline and per-user tier-override controls.
    path(
        'users/<int:user_id>/tier_override/',
        user_tier_override_page,
        name='studio_user_tier_override_page',
    ),
    path(
        'users/<int:user_id>/tier_override/create',
        user_tier_override_create,
        name='studio_user_tier_override_create',
    ),
    path(
        'users/<int:user_id>/tier_override/revoke',
        user_tier_override_revoke,
        name='studio_user_tier_override_revoke',
    ),
    path(
        'users/<int:user_id>/tier-override/create',
        legacy_user_tier_override_action_redirect,
        {'action': 'create'},
        name='studio_legacy_user_tier_override_create',
    ),
    path(
        'users/<int:user_id>/tier-override/revoke',
        legacy_user_tier_override_action_redirect,
        {'action': 'revoke'},
        name='studio_legacy_user_tier_override_revoke',
    ),

    # CRM (issue #560). The ``Track in CRM`` CTA on the user profile is
    # the only POST entry; everything else lives under ``/studio/crm/``.
    path(
        'users/<int:user_id>/crm/track',
        crm_track,
        name='studio_crm_track',
    ),
    path('crm/', crm_list, name='studio_crm_list'),
    path('crm/<int:crm_id>/', crm_detail, name='studio_crm_detail'),
    path('crm/<int:crm_id>/edit', crm_edit, name='studio_crm_edit'),
    path(
        'crm/<int:crm_id>/archive',
        crm_archive,
        name='studio_crm_archive',
    ),
    path(
        'crm/<int:crm_id>/reactivate',
        crm_reactivate,
        name='studio_crm_reactivate',
    ),

    # Sprints (issue #432). Members section.
    path('sprints/', sprint_list, name='studio_sprint_list'),
    path('sprints/new', sprint_create, name='studio_sprint_create'),
    path('sprints/<int:sprint_id>/', sprint_detail, name='studio_sprint_detail'),
    path('sprints/<int:sprint_id>/edit', sprint_edit, name='studio_sprint_edit'),
    # Bulk enroll (issue #443). Staff-only; enrolls members from a
    # textarea of emails. Allows under-tier with a warning per the spec.
    path(
        'sprints/<int:sprint_id>/enroll',
        sprint_bulk_enroll,
        name='studio_sprint_bulk_enroll',
    ),
    # Add a single member + create their plan (issue #444). Staff-only.
    # Reuses ``templates/studio/plans/form.html`` with the sprint locked.
    path(
        'sprints/<int:sprint_id>/add-member',
        sprint_add_member,
        name='studio_sprint_add_member',
    ),

    # Plans (issue #432, drag-drop editor #434). Members section.
    # The ``/edit/`` route is the drag-drop authoring shell (#434); the
    # trailing slash is part of the contract documented in that issue.
    # Studio's other plan/sprint edit URLs do NOT have a trailing slash
    # (they are still server-rendered POST forms); only this one is the
    # thin client-side editor.
    path('plans/', plan_list, name='studio_plan_list'),
    path('plans/new', plan_create, name='studio_plan_create'),
    path('plans/<int:plan_id>/', plan_detail, name='studio_plan_detail'),
    path('plans/<int:plan_id>/edit/', plan_edit, name='studio_plan_edit'),
    path(
        'plans/<int:plan_id>/notes/new',
        interview_note_create,
        name='studio_interview_note_create',
    ),
    path(
        'plans/<int:plan_id>/notes/<int:note_id>/edit',
        interview_note_edit,
        name='studio_interview_note_edit',
    ),
    path(
        'plans/<int:plan_id>/notes/<int:note_id>/delete',
        interview_note_delete,
        name='studio_interview_note_delete',
    ),

    # Announcement banner
    path('announcement/', announcement_banner_edit, name='studio_announcement_banner'),

    # Transactional email templates (issue #455)
    path(
        'email-templates/',
        email_template_list,
        name='studio_email_template_list',
    ),
    path(
        'email-templates/<slug:template_name>/edit/',
        email_template_edit,
        name='studio_email_template_edit',
    ),
    path(
        'email-templates/<slug:template_name>/reset/',
        email_template_reset,
        name='studio_email_template_reset',
    ),
    path(
        'email-templates/<slug:template_name>/preview/',
        email_template_preview,
        name='studio_email_template_preview',
    ),
    path(
        'email-templates/<slug:template_name>/send-test/',
        email_template_send_test,
        name='studio_email_template_send_test',
    ),

    # API tokens (issue #431). Superuser-only; the plaintext key is shown
    # exactly once on the ``created/`` page via a session stash.
    path('api-tokens/', studio_api_token_list, name='studio_api_token_list'),
    path('api-tokens/new/', studio_api_token_create, name='studio_api_token_create'),
    path('api-tokens/created/', studio_api_token_created, name='studio_api_token_created'),
    # ``<path:key>`` because the urlsafe key contains '-' / '_' which
    # ``<slug:>`` allows but the path converter is the conservative match.
    path('api-tokens/<path:key>/revoke/', studio_api_token_revoke, name='studio_api_token_revoke'),

    # Redirects
    path('redirects/', redirect_list, name='studio_redirect_list'),
    path('redirects/new', redirect_create, name='studio_redirect_create'),
    path('redirects/<int:redirect_id>/edit', redirect_edit, name='studio_redirect_edit'),
    path('redirects/<int:redirect_id>/delete', redirect_delete, name='studio_redirect_delete'),
    path('redirects/<int:redirect_id>/toggle', redirect_toggle, name='studio_redirect_toggle'),

    # Settings
    path('settings/', settings_dashboard, name='studio_settings'),
    # Export / import (issue #323) registered BEFORE the generic
    # ``<group_name>/save/`` route so the literal ``export/`` and
    # ``import/`` prefixes aren't swallowed by the str converter.
    path('settings/export/', settings_export, name='studio_settings_export'),
    path('settings/import/', settings_import, name='studio_settings_import'),
    # Auth provider save URL is registered BEFORE the generic
    # ``<group_name>/save/`` route so the literal ``auth/`` prefix isn't
    # swallowed by the str converter (it would otherwise treat ``auth``
    # as the integration group name).
    path(
        'settings/auth/<str:provider>/save/',
        settings_save_auth_provider,
        name='studio_settings_save_auth',
    ),
    path('settings/<str:group_name>/save/', settings_save_group, name='studio_settings_save'),

    # Integration setup docs (issue #641). Each integration setting may
    # carry a ``docs_url`` pointing at ``_docs/integrations/<group>.md``;
    # the Studio template rewrites that to this route and the (?) icon
    # links here in a new tab.
    path(
        'docs/integrations/<str:group>',
        integration_docs,
        name='studio_integration_docs',
    ),

    # Worker Status
    path('worker/', worker_status, name='studio_worker'),
    path(
        'worker/queue/drain/',
        worker_drain_queue,
        name='studio_worker_drain_queue',
    ),
    path(
        'worker/queue/<int:ormq_id>/inspect/',
        worker_inspect_task,
        name='studio_worker_inspect_task',
    ),
    path(
        'worker/queue/<int:ormq_id>/delete/',
        worker_delete_queued,
        name='studio_worker_delete_queued',
    ),
    path(
        'worker/task/<str:task_id>/',
        worker_task_detail,
        name='studio_worker_task_detail',
    ),
    path(
        'worker/failed/<str:task_id>/retry/',
        worker_retry_failed,
        name='studio_worker_retry_failed',
    ),
    path(
        'worker/failed/<str:task_id>/delete/',
        worker_delete_failed,
        name='studio_worker_delete_failed',
    ),
    path(
        'worker/failed/bulk-retry/',
        worker_bulk_retry_failed,
        name='studio_worker_bulk_retry_failed',
    ),
    path(
        'worker/failed/bulk-delete/',
        worker_bulk_delete_failed,
        name='studio_worker_bulk_delete_failed',
    ),

    # Content Sync
    path('sync/', sync_dashboard, name='studio_sync_dashboard'),
    path('sync/all/', sync_all, name='studio_sync_all'),
    path('sync/history/', sync_history, name='studio_sync_history'),
    # Export / import (issue #436) registered BEFORE the generic
    # ``<uuid:source_id>/...`` and ``<path:repo_name>/...`` routes so the
    # literal ``export/`` and ``import/`` prefixes aren't swallowed.
    path(
        'sync/export/',
        content_sources_export,
        name='studio_content_sources_export',
    ),
    path(
        'sync/import/',
        content_sources_import,
        name='studio_content_sources_import',
    ),
    path('sync/<uuid:source_id>/trigger/', sync_trigger, name='studio_sync_trigger'),
    path('sync/<uuid:source_id>/status/', sync_status, name='studio_sync_status'),
    # Per-object Re-sync source button (issue #281). Registered before the
    # ``<path:repo_name>`` route so the literal ``object/`` prefix isn't
    # swallowed by the path converter (which would treat ``object`` as the
    # first segment of a repo name).
    path(
        'sync/object/<str:model_name>/<int:object_id>/trigger/',
        sync_object_trigger,
        name='studio_sync_object_trigger',
    ),
    # Per-repo sync uses ``<path:repo_name>`` because repo names contain a
    # slash (e.g. ``AI-Shipping-Labs/content``). See issue #232.
    path(
        'sync/<path:repo_name>/trigger-repo/',
        sync_repo_trigger,
        name='studio_sync_repo_trigger',
    ),

    # Content Sources (register a new repo)
    path(
        'content-sources/new/',
        content_source_create,
        name='studio_content_source_create',
    ),
    path(
        'content-sources/refresh/',
        content_source_refresh,
        name='studio_content_source_refresh',
    ),
]
