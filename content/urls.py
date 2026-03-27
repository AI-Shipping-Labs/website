from django.urls import path
from django.views.generic import RedirectView

from content.views.admin_api import reorder_modules, reorder_units
from content.views.api import download_file, submit_project
from content.views.courses import (
    api_cohort_enroll,
    api_cohort_unenroll,
    api_course_detail,
    api_course_purchase,
    api_course_unit_complete,
    api_course_unit_detail,
    api_courses_list,
    course_detail,
    course_unit_detail,
    courses_list,
)
from content.views.home import home
from content.views.interview import interview_detail, interview_hub
from content.views.pages import (
    about,
    activities,
    blog_detail,
    blog_list,
    collection_list,
    downloads_list,
    project_detail,
    projects_list,
    recording_detail,
    recordings_list,
    tutorial_detail,
    tutorials_list,
)
from content.views.peer_review import (
    api_review_dashboard,
    api_submit_project,
    api_submit_review,
    certificate_page,
    project_submit,
    review_dashboard,
    review_form,
)
from content.views.tags import tags_detail, tags_index

urlpatterns = [
    path('', home, name='home'),
    path('about', about, name='about'),
    path('activities', activities, name='activities'),
    path('blog', blog_list, name='blog_list'),
    path('blog/<slug:slug>', blog_detail, name='blog_detail'),
    path('event-recordings', recordings_list, name='recordings_list'),
    path('event-recordings/<slug:slug>', recording_detail, name='recording_detail'),
    path('projects', projects_list, name='projects_list'),
    path('projects/<slug:slug>', project_detail, name='project_detail'),
    path('resources', collection_list, name='collection_list'),
    path('collection', collection_list),  # backward compat redirect
    path('tutorials', tutorials_list, name='tutorials_list'),
    path('tutorials/<slug:slug>', tutorial_detail, name='tutorial_detail'),
    path('downloads', downloads_list, name='downloads_list'),
    # Interview questions
    path('interview', interview_hub, name='interview_hub'),
    path('interview/<slug:slug>', interview_detail, name='interview_detail'),
    # Learning path (redirect old URL to article)
    path('learning-path/ai-engineer', RedirectView.as_view(url='/blog/ai-engineer-learning-path', permanent=True), name='learning_path_ai_engineer_redirect'),
    # Tags
    path('tags', tags_index, name='tags_index'),
    path('tags/<slug:tag>', tags_detail, name='tags_detail'),
    # Courses
    path('courses', courses_list, name='courses_list'),
    path('courses/<slug:slug>', course_detail, name='course_detail'),
    # Peer review (must be before the catch-all slug-based unit URL)
    path('courses/<slug:slug>/submit', project_submit, name='project_submit'),
    path('courses/<slug:slug>/reviews', review_dashboard, name='peer_review_dashboard'),
    path('courses/<slug:slug>/reviews/<int:submission_id>', review_form, name='peer_review_form'),
    # Course unit detail (three slug segments - must be after more specific patterns)
    path('courses/<slug:course_slug>/<slug:module_slug>/<slug:unit_slug>', course_unit_detail, name='course_unit_detail'),
    # Certificates
    path('certificates/<uuid:certificate_id>', certificate_page, name='certificate_page'),
    # API endpoints
    path('api/projects/submit', submit_project, name='submit_project'),
    path('api/downloads/<slug:slug>/file', download_file, name='download_file'),
    path('api/courses', api_courses_list, name='api_courses_list'),
    path('api/courses/<slug:slug>', api_course_detail, name='api_course_detail'),
    path('api/courses/<slug:slug>/units/<int:unit_id>', api_course_unit_detail, name='api_course_unit_detail'),
    path('api/courses/<slug:slug>/units/<int:unit_id>/complete', api_course_unit_complete, name='api_course_unit_complete'),
    path('api/courses/<slug:slug>/cohorts/<int:cohort_id>/enroll', api_cohort_enroll, name='api_cohort_enroll'),
    path('api/courses/<slug:slug>/cohorts/<int:cohort_id>/unenroll', api_cohort_unenroll, name='api_cohort_unenroll'),
    path('api/courses/<slug:slug>/purchase', api_course_purchase, name='api_course_purchase'),
    path('api/courses/<slug:slug>/submit', api_submit_project, name='api_submit_project'),
    path('api/courses/<slug:slug>/reviews', api_review_dashboard, name='api_review_dashboard'),
    path('api/courses/<slug:slug>/reviews/<int:submission_id>', api_submit_review, name='api_submit_review'),
    # Admin API endpoints
    path('api/admin/modules/reorder', reorder_modules, name='reorder_modules'),
    path('api/admin/units/reorder', reorder_units, name='reorder_units'),
]
