from django.urls import path

from topics import views

app_name = 'topics'

urlpatterns = [
    path('topics/', views.topics_hub, name='topics_hub'),
    # ``str`` rather than ``slug``: the slug alphabet carries dots
    # (the file-stem alphabet from the wiki repository).
    path('topics/<str:slug>/', views.topic_page, name='topic_page'),
]
