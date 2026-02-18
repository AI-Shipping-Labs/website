from django.urls import path
from content.views.home import home
from content.views.pages import (
    about, activities, blog_list, blog_detail,
    recordings_list, recording_detail,
    projects_list, project_detail,
    collection_list,
    tutorials_list, tutorial_detail,
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
    path('collection', collection_list, name='collection_list'),
    path('tutorials', tutorials_list, name='tutorials_list'),
    path('tutorials/<slug:slug>', tutorial_detail, name='tutorial_detail'),
]
