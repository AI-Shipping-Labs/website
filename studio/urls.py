from django.urls import path

from studio.views.announcement import announcement_banner_edit
from studio.views.articles import article_edit, article_list
from studio.views.campaigns import campaign_create, campaign_detail, campaign_list
from studio.views.content_sources import (
    content_source_create,
    content_source_created,
    content_source_refresh,
)
from studio.views.courses import (
    course_access_grant,
    course_access_list,
    course_access_revoke,
    course_create_stripe_product,
    course_edit,
    course_list,
    module_create,
    module_reorder,
    unit_create,
    unit_edit,
)
from studio.views.dashboard import dashboard
from studio.views.downloads import download_edit, download_list
from studio.views.events import event_create_zoom, event_edit, event_list
from studio.views.impersonate import impersonate_user, stop_impersonation
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
from studio.views.projects import project_list, project_review
from studio.views.recordings import recording_edit, recording_list, recording_publish_youtube
from studio.views.redirects import redirect_create, redirect_delete, redirect_edit, redirect_list, redirect_toggle
from studio.views.settings import settings_dashboard, settings_save_group
from studio.views.subscribers import subscriber_export_csv, subscriber_list
from studio.views.sync import sync_all, sync_dashboard, sync_history, sync_status, sync_trigger
from studio.views.tier_overrides import tier_override_create, tier_override_page, tier_override_revoke
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
    worker_run_sync_now,
    worker_status,
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

    # Articles
    path('articles/', article_list, name='studio_article_list'),
    path('articles/<int:article_id>/edit', article_edit, name='studio_article_edit'),
    path('articles/<int:article_id>/notify', article_notify, name='studio_article_notify'),
    path('articles/<int:article_id>/announce-slack', article_announce_slack, name='studio_article_announce_slack'),

    # Events
    path('events/', event_list, name='studio_event_list'),
    path('events/<int:event_id>/edit', event_edit, name='studio_event_edit'),
    path('events/<int:event_id>/create-zoom', event_create_zoom, name='studio_event_create_zoom'),
    path('events/<int:event_id>/notify', event_notify, name='studio_event_notify'),
    path('events/<int:event_id>/announce-slack', event_announce_slack, name='studio_event_announce_slack'),

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

    # Subscribers
    path('subscribers/', subscriber_list, name='studio_subscriber_list'),
    path('subscribers/export', subscriber_export_csv, name='studio_subscriber_export'),

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
    path('users/tier-override/', tier_override_page, name='studio_tier_override'),
    path('users/tier-override/create', tier_override_create, name='studio_tier_override_create'),
    path('users/tier-override/revoke', tier_override_revoke, name='studio_tier_override_revoke'),

    # Announcement banner
    path('announcement/', announcement_banner_edit, name='studio_announcement_banner'),

    # Redirects
    path('redirects/', redirect_list, name='studio_redirect_list'),
    path('redirects/new', redirect_create, name='studio_redirect_create'),
    path('redirects/<int:redirect_id>/edit', redirect_edit, name='studio_redirect_edit'),
    path('redirects/<int:redirect_id>/delete', redirect_delete, name='studio_redirect_delete'),
    path('redirects/<int:redirect_id>/toggle', redirect_toggle, name='studio_redirect_toggle'),

    # Settings
    path('settings/', settings_dashboard, name='studio_settings'),
    path('settings/<str:group_name>/save/', settings_save_group, name='studio_settings_save'),

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
    path(
        'worker/run-sync-now/',
        worker_run_sync_now,
        name='studio_worker_run_sync_now',
    ),

    # Content Sync
    path('sync/', sync_dashboard, name='studio_sync_dashboard'),
    path('sync/all/', sync_all, name='studio_sync_all'),
    path('sync/history/', sync_history, name='studio_sync_history'),
    path('sync/<uuid:source_id>/trigger/', sync_trigger, name='studio_sync_trigger'),
    path('sync/<uuid:source_id>/status/', sync_status, name='studio_sync_status'),

    # Content Sources (register a new repo)
    path(
        'content-sources/new/',
        content_source_create,
        name='studio_content_source_create',
    ),
    path(
        'content-sources/created/',
        content_source_created,
        name='studio_content_source_created',
    ),
    path(
        'content-sources/refresh/',
        content_source_refresh,
        name='studio_content_source_refresh',
    ),
]
