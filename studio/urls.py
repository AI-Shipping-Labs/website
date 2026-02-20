from django.urls import path

from studio.views.dashboard import dashboard
from studio.views.courses import (
    course_list, course_create, course_edit,
    module_create, unit_create, unit_edit,
    module_reorder,
)
from studio.views.articles import article_list, article_create, article_edit
from studio.views.events import event_list, event_create, event_edit
from studio.views.recordings import recording_list, recording_create, recording_edit
from studio.views.campaigns import campaign_list, campaign_create, campaign_detail
from studio.views.subscribers import subscriber_list, subscriber_export_csv
from studio.views.downloads import download_list, download_create, download_edit
from studio.views.projects import project_list, project_review

urlpatterns = [
    # Dashboard
    path('', dashboard, name='studio_dashboard'),

    # Courses
    path('courses/', course_list, name='studio_course_list'),
    path('courses/new', course_create, name='studio_course_create'),
    path('courses/<int:course_id>/edit', course_edit, name='studio_course_edit'),
    path('courses/<int:course_id>/modules/add', module_create, name='studio_module_create'),
    path('courses/<int:course_id>/modules/reorder', module_reorder, name='studio_module_reorder'),
    path('modules/<int:module_id>/units/add', unit_create, name='studio_unit_create'),
    path('units/<int:unit_id>/edit', unit_edit, name='studio_unit_edit'),

    # Articles
    path('articles/', article_list, name='studio_article_list'),
    path('articles/new', article_create, name='studio_article_create'),
    path('articles/<int:article_id>/edit', article_edit, name='studio_article_edit'),

    # Events
    path('events/', event_list, name='studio_event_list'),
    path('events/new', event_create, name='studio_event_create'),
    path('events/<int:event_id>/edit', event_edit, name='studio_event_edit'),

    # Recordings
    path('recordings/', recording_list, name='studio_recording_list'),
    path('recordings/new', recording_create, name='studio_recording_create'),
    path('recordings/<int:recording_id>/edit', recording_edit, name='studio_recording_edit'),

    # Campaigns
    path('campaigns/', campaign_list, name='studio_campaign_list'),
    path('campaigns/new', campaign_create, name='studio_campaign_create'),
    path('campaigns/<int:campaign_id>/', campaign_detail, name='studio_campaign_detail'),

    # Subscribers
    path('subscribers/', subscriber_list, name='studio_subscriber_list'),
    path('subscribers/export', subscriber_export_csv, name='studio_subscriber_export'),

    # Downloads
    path('downloads/', download_list, name='studio_download_list'),
    path('downloads/new', download_create, name='studio_download_create'),
    path('downloads/<int:download_id>/edit', download_edit, name='studio_download_edit'),

    # Projects
    path('projects/', project_list, name='studio_project_list'),
    path('projects/<int:project_id>/review', project_review, name='studio_project_review'),
]
