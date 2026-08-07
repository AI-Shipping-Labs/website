"""URL routes for the member API."""

from django.urls import path

from member_api.views.books import (
    book_chapter_note,
    book_chapter_read,
    book_reading,
    reader_profile,
)
from member_api.views.docs import docs_page, openapi_json
from member_api.views.plans import (
    checkpoint_collection,
    checkpoint_detail,
    deliverable_collection,
    deliverable_detail,
    next_step_collection,
    next_step_detail,
    plan_detail,
    plan_markdown,
    plan_progress,
    plans_collection,
    resource_collection,
    resource_detail,
    week_collection,
    week_detail,
    week_note_detail,
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
    path(
        "v1/plans/<int:plan_id>/weeks",
        week_collection,
        name="member_api_week_collection",
    ),
    path(
        "v1/plans/<int:plan_id>/weeks/<int:week_id>",
        week_detail,
        name="member_api_week_detail",
    ),
    path(
        "v1/plans/<int:plan_id>/weeks/<int:week_id>/checkpoints",
        checkpoint_collection,
        name="member_api_checkpoint_collection",
    ),
    path(
        "v1/plans/<int:plan_id>/weeks/<int:week_id>/note",
        week_note_detail,
        name="member_api_week_note_detail",
    ),
    path(
        "v1/plans/<int:plan_id>/checkpoints/<int:checkpoint_id>",
        checkpoint_detail,
        name="member_api_checkpoint_detail",
    ),
    path(
        "v1/plans/<int:plan_id>/deliverables",
        deliverable_collection,
        name="member_api_deliverable_collection",
    ),
    path(
        "v1/plans/<int:plan_id>/deliverables/<int:deliverable_id>",
        deliverable_detail,
        name="member_api_deliverable_detail",
    ),
    path(
        "v1/plans/<int:plan_id>/next-steps",
        next_step_collection,
        name="member_api_next_step_collection",
    ),
    path(
        "v1/plans/<int:plan_id>/next-steps/<int:next_step_id>",
        next_step_detail,
        name="member_api_next_step_detail",
    ),
    path(
        "v1/plans/<int:plan_id>/resources",
        resource_collection,
        name="member_api_resource_collection",
    ),
    path(
        "v1/plans/<int:plan_id>/resources/<int:resource_id>",
        resource_detail,
        name="member_api_resource_detail",
    ),
    path(
        "v1/books/reader-profile",
        reader_profile,
        name="member_api_reader_profile",
    ),
    path(
        "v1/books/<slug:slug>/reading",
        book_reading,
        name="member_api_book_reading",
    ),
    path(
        "v1/books/<slug:slug>/chapters/<int:number>/read",
        book_chapter_read,
        name="member_api_book_chapter_read",
    ),
    path(
        "v1/books/<slug:slug>/chapters/<int:number>/note",
        book_chapter_note,
        name="member_api_book_chapter_note",
    ),
]
