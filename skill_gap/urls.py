"""URL routes for the skill-gap page (issue #1716).

Mounted at the project root in ``website/urls.py``. The path has no
trailing slash per the platform convention (``RemoveTrailingSlashMiddleware``
301s ``/skill-gap/`` to ``/skill-gap``).
"""

from django.urls import path

from skill_gap.views.skill_gap import skill_gap

urlpatterns = [
    path('skill-gap', skill_gap, name='skill_gap'),
]
