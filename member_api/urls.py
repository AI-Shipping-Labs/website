"""URL routes for the member API."""

from django.urls import path

from member_api.views.docs import docs_page, openapi_json
from member_api.views.plans import (
    plan_detail,
    plan_markdown,
    plan_progress,
    plans_collection,
)

urlpatterns = [
    path("openapi.json", openapi_json, name="member_api_openapi_json"),
    path("docs", docs_page, name="member_api_docs"),
    path("v1/plans", plans_collection, name="member_api_plans_collection"),
    path("v1/plans/<int:plan_id>", plan_detail, name="member_api_plan_detail"),
    path(
        "v1/plans/<int:plan_id>/markdown",
        plan_markdown,
        name="member_api_plan_markdown",
    ),
    path(
        "v1/plans/<int:plan_id>/progress",
        plan_progress,
        name="member_api_plan_progress",
    ),
]
