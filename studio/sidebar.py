"""Route-name driven active-state map for the Studio sidebar (issue #1435).

The Studio sidebar information architecture (eight collapsible groups, see
issues #570 / #576 / #1287) is unchanged here. What this module owns is the
*source of truth* for two derived facts:

``active_section``
    Which collapsible group contains the current page, so the group renders
    expanded server-side before any JavaScript runs.

``active_destination``
    Which single sidebar link is the current destination, so exactly one
    link carries the active class string and ``aria-current="page"``.

Before #1435 both facts were computed from URL substring checks -- once in
``studio_sidebar_state`` and again, independently, inside
``templates/studio/base.html``. The two lists drifted: deep routes such as
``/studio/users/<id>/``, ``/studio/users/import/``, ``/studio/assistant/``,
``/studio/maven-events/``, ``/studio/questionnaire-responses/`` and
``/studio/payments/stripe-webhooks/`` resolved to *no* section, so their
owning group rendered collapsed, and only the Email log link ever emitted
``aria-current``.

The authority is now the resolved Django route name
(``request.resolver_match.url_name``). Every Studio route belongs to exactly
one of:

``SIDEBAR_ROUTE_FAMILIES``
    A (section, destination, route names) family. The whole family -- list,
    detail, form, and POST action routes -- shares the destination's logical
    home in the sidebar.

``SECTION_ONLY_ROUTES``
    A route that has an owning section but no sidebar link of its own. Its
    group expands, and no link is marked current (nothing may lie about
    being the current page). Stripe webhook diagnostics are the current
    example: they are reached from the Payment mismatches cross-link and
    keep their People ownership without adding a navigation item.

``ROUTES_WITHOUT_SIDEBAR_HOME``
    JSON endpoints and impersonation redirects that never render the
    sidebar and have no honest home. They expand nothing and mark nothing.

``studio/tests/test_sidebar_routes.py`` asserts that the three sets exactly
partition the Studio URLconf, so a newly added route cannot silently fall
back to "no section" again.
"""

from django.urls import Resolver404, resolve

#: Collapsible group slugs, in sidebar order. These match the
#: ``studio-section-<slug>`` element ids in ``templates/studio/base.html``.
SECTION_SLUGS = (
    'events',
    'content',
    'people',
    'planning',
    'onboarding',
    'communication',
    'tracking',
    'operations',
)

#: Destinations nested under the Operations > Triggers disclosure.
TRIGGER_DESTINATIONS = frozenset({
    'trigger_subscriptions',
    'trigger_widgets',
    'trigger_emissions',
    'trigger_deliveries',
})

#: ``(section slug, destination key, route names)``. ``section`` is empty for
#: the top-level Dashboard link, which sits outside every collapsible group.
SIDEBAR_ROUTE_FAMILIES = (
    ('', 'dashboard', ('studio_dashboard',)),

    # --- Events -----------------------------------------------------------
    ('events', 'events', (
        'studio_event_list',
        'studio_event_list_past',
        'studio_event_new',
        'studio_event_edit',
        'studio_event_delete',
        'studio_event_duplicate',
        'studio_event_duplicates',
        'studio_event_duplicates_confirm',
        'studio_event_duplicates_preview',
        'studio_event_announce_slack',
        'studio_event_banner_status',
        'studio_event_create_zoom',
        'studio_event_notify',
        'studio_event_notify_workshop_ready',
        'studio_event_regenerate_banner',
        'studio_event_registrations_csv',
        'studio_event_remove_banner',
        'studio_event_send_followup',
        'studio_event_upload_banner',
    )),
    ('events', 'event_series', (
        'studio_event_series_list',
        'studio_event_series_detail',
        'studio_event_series_new',
        'studio_event_series_delete',
        'studio_event_series_add_occurrence',
        'studio_event_series_announce_slack',
        'studio_event_series_create_zoom',
        'studio_event_series_event_publish',
        'studio_event_series_event_unpublish',
        'studio_event_series_notify',
        'studio_event_series_publish_all',
        'studio_event_series_regenerate_banner',
        'studio_event_series_remove_banner',
        'studio_event_series_upload_banner',
    )),
    ('events', 'event_hosts', (
        'studio_host_list',
        'studio_host_new',
        'studio_host_edit',
    )),

    # --- Content ----------------------------------------------------------
    ('content', 'articles', (
        'studio_article_list',
        'studio_article_edit',
        'studio_article_notify',
        'studio_article_announce_slack',
        'studio_article_regenerate_banner',
        'studio_article_remove_banner',
        'studio_article_upload_banner',
        'studio_article_regenerate_preview_token',
    )),
    ('content', 'marketing_pages', (
        'studio_marketing_page_list',
        'studio_marketing_page_new',
        'studio_marketing_page_edit',
        'studio_marketing_page_regenerate_preview_token',
    )),
    ('content', 'courses', (
        'studio_course_list',
        'studio_course_edit',
        'studio_course_notify',
        'studio_course_announce_slack',
        'studio_course_create_stripe_product',
        'studio_course_regenerate_banner',
        'studio_course_remove_banner',
        'studio_course_upload_banner',
        'studio_course_access_list',
        'studio_course_access_grant',
        'studio_course_access_revoke',
        'studio_course_user_search',
        'studio_course_enrollment_list',
        'studio_course_enrollment_create',
        'studio_course_enrollment_unenroll',
        'studio_course_instructor_add',
        'studio_course_instructor_remove',
        'studio_course_instructor_reorder',
        'studio_module_create',
        'studio_module_reorder',
        'studio_unit_create',
        'studio_unit_edit',
        'studio_peer_review_management',
        'studio_peer_review_extend_deadline',
        'studio_peer_review_form_batch',
        'studio_peer_review_issue_certificates',
        # Certificate revoke / un-revoke are POST-only controls on the
        # course peer-reviews page and redirect straight back to it.
        'studio_certificate_revoke',
        'studio_certificate_unrevoke',
    )),
    ('content', 'projects', (
        'studio_project_list',
        'studio_project_review',
        'studio_project_regenerate_banner',
        'studio_project_remove_banner',
        'studio_project_upload_banner',
    )),
    ('content', 'workshops', (
        'studio_workshop_list',
        'studio_workshop_detail',
        'studio_workshop_edit',
        'studio_workshop_notify',
        'studio_workshop_announce_slack',
        'studio_workshop_resync',
        'studio_workshop_regenerate_banner',
        'studio_workshop_remove_banner',
        'studio_workshop_upload_banner',
        'studio_workshop_regenerate_preview_token',
    )),
    ('content', 'recordings', (
        'studio_recording_list',
        'studio_recording_edit',
        'studio_recording_notify',
        'studio_recording_announce_slack',
    )),
    ('content', 'downloads', (
        'studio_download_list',
        'studio_download_edit',
        'studio_download_notify',
        'studio_download_announce_slack',
        'studio_download_regenerate_banner',
        'studio_download_remove_banner',
        'studio_download_upload_banner',
    )),

    # --- People -----------------------------------------------------------
    ('people', 'users', (
        'studio_user_list',
        'studio_user_export',
        'studio_user_detail',
        'studio_user_alias_add',
        'studio_user_alias_remove',
        'studio_user_deliverability_action',
        'studio_user_maven_email_preference',
        'studio_user_slack_id_set',
        'studio_user_slack_membership_check',
        'studio_user_sync_from_stripe',
        'studio_user_tag_add',
        'studio_user_tag_remove',
        'studio_member_note_create',
        'studio_member_note_edit',
        'studio_member_note_delete',
        # Contacts import wizard (upload -> preview -> confirm).
        'studio_user_import',
        'studio_user_import_preview',
        'studio_user_import_confirm',
    )),
    ('people', 'call_hosts', (
        'studio_call_host_list',
        'studio_call_host_create',
        'studio_call_host_edit',
        'studio_call_host_delete',
    )),
    ('people', 'imports', (
        'studio_import_batch_list',
        'studio_import_batch_new',
        'studio_import_batch_detail',
        'studio_import_batch_fragment',
        'studio_import_batch_rerun',
        'studio_import_schedule_toggle',
    )),
    ('people', 'instructors', (
        'studio_instructor_list',
        'studio_instructor_link',
        'studio_instructor_unlink',
    )),
    ('people', 'tier_overrides', (
        'studio_tier_overrides_list',
        # The per-user override page/actions have always highlighted the
        # Tier overrides destination; that stays unchanged.
        'studio_user_tier_override_page',
        'studio_user_tier_override_create',
        'studio_user_tier_override_revoke',
    )),
    ('people', 'tags', (
        'studio_tag_list',
        'studio_tag_rename',
        'studio_tag_delete',
    )),
    ('people', 'user_merge', (
        'studio_user_merge',
        'studio_user_merge_preview',
        'studio_user_merge_confirm',
    )),
    ('people', 'payment_mismatches', (
        'studio_payment_mismatch_list',
        'studio_payment_mismatch_mark',
    )),
    ('people', 'subscription_reconciliation', (
        'studio_subscription_reconciliation',
        'studio_subscription_reconciliation_check',
    )),
    ('people', 'user_create', (
        'studio_user_create',
        'studio_user_create_done',
    )),
    ('people', 'crm', (
        'studio_crm_list',
        'studio_crm_detail',
        'studio_crm_edit',
        'studio_crm_archive',
        'studio_crm_reactivate',
        'studio_crm_markdown_download',
        'studio_crm_slack_ingest',
        'studio_crm_slack_progress_undo',
        'studio_crm_slack_progress_change_undo',
        # "Track in CRM" is posted from the user detail page and redirects
        # to the CRM record it creates, so CRM is its logical home.
        'studio_crm_track',
    )),
    ('people', 'assistant', ('studio_assistant',)),

    # --- Planning ---------------------------------------------------------
    ('planning', 'sprints', (
        'studio_sprint_list',
        'studio_sprint_detail',
        'studio_sprint_create',
        'studio_sprint_edit',
        'studio_sprint_delete',
        'studio_sprint_cancel',
        'studio_sprint_complete',
        'studio_sprint_add_member',
        'studio_sprint_bulk_enroll',
        'studio_sprint_unenroll',
        'studio_sprint_accountability_add',
        'studio_sprint_accountability_randomize',
        'studio_sprint_accountability_remove',
        'studio_sprint_feedback_attach',
        'studio_sprint_feedback_distribute',
        'studio_sprint_feedback_synthesize',
        'studio_sprint_plan_request_create_plan',
        'studio_sprint_plan_request_prepare',
        'studio_sprint_send_partner_intro_emails',
        'studio_sprint_send_plan_ready_emails',
    )),
    ('planning', 'plans', (
        'studio_plan_list',
        'studio_plan_detail',
        'studio_plan_create',
        'studio_plan_edit',
        'studio_plan_carry_over',
        'studio_plan_markdown_download',
        'studio_plan_move_unfinished',
        'studio_plan_share',
        'studio_plan_view_as_member',
        'studio_plan_visibility_update',
        'studio_plan_draft_first_sprint',
        'studio_plan_draft_first_sprint_apply',
        'studio_plan_draft_first_sprint_dismiss',
        'studio_plan_draft_next_sprint',
        'studio_plan_draft_next_sprint_dismiss',
        'studio_interview_note_create',
        'studio_interview_note_edit',
        'studio_interview_note_delete',
    )),
    ('planning', 'books', (
        'studio_book_list',
        'studio_book_detail',
        'studio_book_create',
        'studio_book_edit',
        'studio_book_delete',
        'studio_book_chapter_create',
        'studio_book_chapter_edit',
        'studio_book_chapter_delete',
        'studio_book_chapter_reorder',
        'studio_book_chapter_pull_notes',
    )),

    # --- Onboarding & intake ---------------------------------------------
    ('onboarding', 'questionnaires', (
        'studio_questionnaire_list',
        'studio_questionnaire_detail',
        'studio_questionnaire_create',
        'studio_questionnaire_edit',
        'studio_questionnaire_question_create',
        'studio_questionnaire_question_edit',
        'studio_questionnaire_question_delete',
        'studio_questionnaire_question_reorder',
        'studio_questionnaire_option_reorder',
        # Response review lives under Questionnaires, including the
        # cross-questionnaire queue at /studio/questionnaire-responses/.
        'studio_questionnaire_response_queue',
        'studio_questionnaire_responses',
        'studio_questionnaire_response_detail',
        'studio_questionnaire_response_review',
        'studio_response_question_create',
        'studio_response_question_edit',
        'studio_response_question_delete',
    )),
    ('onboarding', 'personas', (
        'studio_persona_list',
        'studio_persona_detail',
        'studio_persona_create',
        'studio_persona_edit',
        'studio_persona_reorder',
    )),

    # --- Communication ----------------------------------------------------
    ('communication', 'notifications', ('studio_notification_log',)),
    ('communication', 'campaigns', (
        'studio_campaign_list',
        'studio_campaign_detail',
        'studio_campaign_create',
        'studio_campaign_edit',
        'studio_campaign_delete',
        'studio_campaign_duplicate',
        'studio_campaign_recipients',
        'studio_campaign_delivery_resolve',
        'studio_campaign_recipient_count',
        'studio_campaign_recount',
        'studio_campaign_send',
        'studio_campaign_test_send',
    )),
    ('communication', 'email_templates', (
        'studio_email_template_list',
        'studio_email_template_edit',
        'studio_email_template_preview',
        'studio_email_template_reset',
        'studio_email_template_send_test',
    )),
    ('communication', 'announcement', ('studio_announcement_banner',)),

    # --- Tracking ---------------------------------------------------------
    ('tracking', 'utm_campaigns', (
        'studio_utm_campaign_list',
        'studio_utm_campaign_detail',
        'studio_utm_campaign_create',
        'studio_utm_campaign_edit',
        'studio_utm_campaign_import',
        'studio_utm_campaign_archive',
        'studio_utm_campaign_unarchive',
        'studio_utm_link_create',
        'studio_utm_link_edit',
        'studio_utm_link_archive',
    )),
    ('tracking', 'utm_analytics', (
        'studio_utm_dashboard',
        'studio_utm_campaign_analytics',
        'studio_utm_link_analytics',
    )),
    ('tracking', 'signup_analytics', ('studio_signup_analytics',)),

    # --- Operations -------------------------------------------------------
    ('operations', 'sync', (
        'studio_sync_dashboard',
        'studio_sync_all',
        'studio_sync_history',
        'studio_sync_status',
        'studio_sync_trigger',
        'studio_sync_repo_trigger',
        'studio_sync_object_trigger',
        'studio_content_source_create',
        'studio_content_source_refresh',
        'studio_content_sources_export',
        'studio_content_sources_import',
    )),
    ('operations', 'worker', (
        'studio_worker',
        'studio_worker_task_detail',
        'studio_worker_inspect_task',
        'studio_worker_retry_failed',
        'studio_worker_delete_failed',
        'studio_worker_bulk_retry_failed',
        'studio_worker_bulk_delete_failed',
        'studio_worker_delete_queued',
        'studio_worker_drain_queue',
        'studio_worker_test_smoke',
    )),
    ('operations', 'ses_events', (
        'studio_ses_event_list',
        'studio_ses_event_detail',
    )),
    ('operations', 'email_log', ('studio_email_log_list',)),
    ('operations', 'maven_events', (
        'studio_maven_event_list',
        'studio_maven_event_detail',
        'studio_maven_event_retry',
    )),
    ('operations', 'redirects', (
        'studio_redirect_list',
        'studio_redirect_create',
        'studio_redirect_edit',
        'studio_redirect_delete',
        'studio_redirect_toggle',
    )),
    ('operations', 'trigger_subscriptions', (
        'studio_trigger_subscription_list',
        'studio_trigger_subscription_create',
        'studio_trigger_subscription_edit',
        'studio_trigger_subscription_toggle',
    )),
    ('operations', 'trigger_widgets', (
        'studio_trigger_widget_list',
        'studio_trigger_widget_create',
        'studio_trigger_widget_edit',
        'studio_trigger_widget_toggle',
    )),
    ('operations', 'trigger_emissions', ('studio_trigger_emission_list',)),
    ('operations', 'trigger_deliveries', ('studio_trigger_delivery_list',)),
    ('operations', 'settings', (
        'studio_settings',
        'studio_settings_save',
        'studio_settings_save_auth',
        'studio_settings_export',
        'studio_settings_import',
        # Calendly OAuth connect/callback/sync are settings-owned redirects.
        'studio_calendly_connect',
        'studio_calendly_callback',
        'studio_calendly_sync',
    )),
    ('operations', 'api_tokens', (
        'studio_api_token_list',
        'studio_api_token_create',
        'studio_api_token_created',
        'studio_api_token_revoke',
        'studio_api_token_rotate',
    )),
)

#: Routes that belong to a section but have no sidebar link of their own.
#: The section expands; nothing is marked current.
SECTION_ONLY_ROUTES = {
    # Reached from the Payment mismatches cross-link; People owns payment
    # troubleshooting today and #1435 does not add a navigation item.
    'studio_stripe_webhooks': 'people',
    'studio_stripe_webhooks_verify': 'people',
}

#: Routes with no honest sidebar home: JSON endpoints that never render the
#: sidebar, and impersonation redirects that leave Studio entirely.
ROUTES_WITHOUT_SIDEBAR_HOME = frozenset({
    'studio_global_search',
    'studio_user_search',
    'studio_impersonate',
    'studio_stop_impersonate',
})


def _build_route_index():
    index = {}
    for section, destination, route_names in SIDEBAR_ROUTE_FAMILIES:
        for route_name in route_names:
            index[route_name] = (section, destination)
    for route_name, section in SECTION_ONLY_ROUTES.items():
        index[route_name] = (section, '')
    return index


#: ``route name -> (section slug, destination key)``.
ROUTE_INDEX = _build_route_index()


def route_name_for(target):
    """Return the resolved route name for a request (or path), or ``''``.

    Prefers ``request.resolver_match.url_name`` -- the metadata Django has
    already computed for the current request. Falls back to resolving a raw
    path so synthetic requests and direct callers still work, and returns
    ``''`` when nothing resolves. Never raises: a missing or unresolvable
    route must degrade to "no active section, no current link", not a 500.
    """
    resolver_match = getattr(target, 'resolver_match', None)
    if resolver_match is not None:
        return getattr(resolver_match, 'url_name', '') or ''

    path = target if isinstance(target, str) else getattr(target, 'path', '')
    if not path:
        return ''
    try:
        return resolve(path).url_name or ''
    except Resolver404:
        return ''


def sidebar_state(target):
    """Compute the sidebar active state for a request (or path).

    Returns the section-expansion booleans consumed by
    ``templates/studio/base.html`` plus ``active_section`` (the group that
    renders expanded server-side and is force-opened by the #1287 client
    merge) and ``active_destination`` (the single link that gets the active
    class string and ``aria-current="page"``).
    """
    section, destination = ROUTE_INDEX.get(route_name_for(target), ('', ''))

    state = {
        f'{slug}_active': slug == section for slug in SECTION_SLUGS
    }
    # Events is the dashboard default (#576): with no other section active
    # it renders expanded so the operator lands on the primary surface.
    state['events_expanded'] = state['events_active'] or not section
    state['triggers_active'] = destination in TRIGGER_DESTINATIONS
    state['active_section'] = section
    state['active_destination'] = destination
    return state
