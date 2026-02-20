from django.urls import path
from content.views.home import home
from content.views.pages import (
    about, activities, blog_list, blog_detail,
    recordings_list, recording_detail,
    projects_list, project_detail,
    collection_list,
    tutorials_list, tutorial_detail,
    downloads_list,
)
from content.views.api import submit_project, download_file
from content.views.admin_api import reorder_modules, reorder_units
from content.views.tags import tags_index, tags_detail
from content.views.courses import (
    courses_list, course_detail, course_unit_detail,
    api_courses_list, api_course_detail,
    api_course_unit_detail, api_course_unit_complete,
    api_cohort_enroll, api_cohort_unenroll,
)

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
    # Tags
    path('tags', tags_index, name='tags_index'),
    path('tags/<slug:tag>', tags_detail, name='tags_detail'),
    # Courses
    path('courses', courses_list, name='courses_list'),
    path('courses/<slug:slug>', course_detail, name='course_detail'),
    path('courses/<slug:slug>/<int:module_sort>/<int:unit_sort>', course_unit_detail, name='course_unit_detail'),
    # API endpoints
    path('api/projects/submit', submit_project, name='submit_project'),
    path('api/downloads/<slug:slug>/file', download_file, name='download_file'),
    path('api/courses', api_courses_list, name='api_courses_list'),
    path('api/courses/<slug:slug>', api_course_detail, name='api_course_detail'),
    path('api/courses/<slug:slug>/units/<int:unit_id>', api_course_unit_detail, name='api_course_unit_detail'),
    path('api/courses/<slug:slug>/units/<int:unit_id>/complete', api_course_unit_complete, name='api_course_unit_complete'),
    path('api/courses/<slug:slug>/cohorts/<int:cohort_id>/enroll', api_cohort_enroll, name='api_cohort_enroll'),
    path('api/courses/<slug:slug>/cohorts/<int:cohort_id>/unenroll', api_cohort_unenroll, name='api_cohort_unenroll'),
    # Admin API endpoints
    path('api/admin/modules/reorder', reorder_modules, name='reorder_modules'),
    path('api/admin/units/reorder', reorder_units, name='reorder_units'),
]
