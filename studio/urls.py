from django.urls import path

from studio.views.dashboard import dashboard
from studio.views.courses import (
    course_list, course_edit,
    module_create, unit_create, unit_edit,
    module_reorder, course_create_stripe_product,
    course_access_list, course_access_grant, course_access_revoke,
)
from studio.views.articles import article_list, article_edit
from studio.views.events import event_list, event_create, event_edit, event_create_zoom
from studio.views.recordings import recording_list, recording_edit, recording_publish_youtube
from studio.views.campaigns import campaign_list, campaign_create, campaign_detail
from studio.views.subscribers import subscriber_list, subscriber_export_csv
from studio.views.downloads import download_list, download_edit
from studio.views.projects import project_list, project_review
from studio.views.tier_overrides import tier_override_page, tier_override_create, tier_override_revoke
from studio.views.redirects import redirect_list, redirect_create, redirect_edit, redirect_delete, redirect_toggle
from studio.views.sync import sync_dashboard, sync_history, sync_trigger, sync_all, sync_status
from studio.views.peer_reviews import (
    peer_review_management, peer_review_form_batch,
    peer_review_issue_certificates, peer_review_extend_deadline,
)
from studio.views.notifications import (
    notification_log,
    article_notify, article_announce_slack,
    recording_notify, recording_announce_slack,
    event_notify, event_announce_slack,
    download_notify, download_announce_slack,
    course_notify, course_announce_slack,
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
    path('events/new', event_create, name='studio_event_create'),
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

    # Subscribers
    path('subscribers/', subscriber_list, name='studio_subscriber_list'),
    path('subscribers/export', subscriber_export_csv, name='studio_subscriber_export'),

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

    # Redirects
    path('redirects/', redirect_list, name='studio_redirect_list'),
    path('redirects/new', redirect_create, name='studio_redirect_create'),
    path('redirects/<int:redirect_id>/edit', redirect_edit, name='studio_redirect_edit'),
    path('redirects/<int:redirect_id>/delete', redirect_delete, name='studio_redirect_delete'),
    path('redirects/<int:redirect_id>/toggle', redirect_toggle, name='studio_redirect_toggle'),

    # Content Sync
    path('sync/', sync_dashboard, name='studio_sync_dashboard'),
    path('sync/all/', sync_all, name='studio_sync_all'),
    path('sync/<uuid:source_id>/', sync_history, name='studio_sync_history'),
    path('sync/<uuid:source_id>/trigger/', sync_trigger, name='studio_sync_trigger'),
    path('sync/<uuid:source_id>/status/', sync_status, name='studio_sync_status'),
]
