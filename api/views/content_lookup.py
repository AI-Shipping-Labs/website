"""Staff read-only lookup of synced content by frontmatter UUID (issue #1834).

``GET /api/content/<uuid>`` resolves any row addressable by ``/c/<uuid>``
(articles, projects, tutorials, downloads, workshops, workshop pages,
marketing pages, events, courses, course modules, course units) and returns
its metadata, source provenance, stored markdown, and stored HTML. Staff see
drafts and gated rows in full; ``is_public`` reports what ``/c/`` would do.

Lookup and the public rule come from ``content.services.content_lookup`` so
this endpoint and the share-link redirect never drift apart.
"""

from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.datetime import isoformat_or_none
from api.utils import parse_bool_query, require_methods, validation_response
from content.models import (
    Article,
    Course,
    Download,
    MarketingPage,
    Module,
    Project,
    Tutorial,
    Unit,
    Workshop,
    WorkshopPage,
)
from content.services.content_lookup import (
    content_type_name,
    find_content_matches,
    is_public_content,
)
from events.models import Event
from integrations.config import site_base_url

# Per-model (markdown field, html field). ``None`` html means no stored HTML.
_BODY_FIELDS = {
    Article: ('content_markdown', 'content_html'),
    Project: ('content_markdown', 'content_html'),
    Tutorial: ('content_markdown', 'content_html'),
    MarketingPage: ('content_markdown', 'content_html'),
    Workshop: ('description', 'description_html'),
    Event: ('description', 'description_html'),
    Course: ('description', 'description_html'),
    WorkshopPage: ('body', 'body_html'),
    Module: ('overview', 'overview_html'),
    Unit: ('body', 'body_html'),
    Download: ('description', None),
}

_EXAMPLE_UUID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
_EXAMPLE_COMMIT = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"

_CONTENT_EXAMPLE = {
    "content_id": _EXAMPLE_UUID,
    "type": "course_unit",
    "id": 412,
    "title": "Setting up the environment",
    "slug": "setup",
    "url": "https://aishippinglabs.com/courses/ai-buildcamp/week-1/setup",
    "share_url": f"https://aishippinglabs.com/c/{_EXAMPLE_UUID}",
    "is_public": True,
    "required_level": 20,
    "context": {"course_slug": "ai-buildcamp", "module_slug": "week-1"},
    "source": {
        "repo": "AI-Shipping-Labs/ai-buildcamp-course",
        "path": "week-1/01-setup.md",
        "commit": _EXAMPLE_COMMIT,
        "github_url": (
            "https://github.com/AI-Shipping-Labs/ai-buildcamp-course/blob/"
            f"{_EXAMPLE_COMMIT}/week-1/01-setup.md"
        ),
    },
    "updated_at": None,
    "markdown": "## Setup\n\nInstall uv first.\n",
    "html": '<h2 id="setup">Setup</h2>\n<p>Install uv first.</p>',
    "homework_markdown": "Push your repo.",
    "homework_html": "<p>Push your repo.</p>",
}


def _none_if_blank(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _source_for(target):
    if isinstance(target, Tutorial):
        repo = path = commit = None
    elif isinstance(target, (Module, Unit)):
        course = target.course if isinstance(target, Module) else target.module.course
        repo = _none_if_blank(course.source_repo)
        path = _none_if_blank(target.source_path)
        commit = _none_if_blank(target.source_commit_sha)
    elif isinstance(target, Course):
        repo = _none_if_blank(target.source_repo)
        path = _none_if_blank(target.source_path)
        commit = _none_if_blank(target.source_commit_sha)
    else:
        repo = _none_if_blank(target.source_repo)
        path = _none_if_blank(target.source_path)
        commit = _none_if_blank(target.source_commit)

    github_url = None
    if repo and path and commit and '/' in repo:
        github_url = (
            f"https://github.com/{repo}/blob/{commit}/{path.lstrip('/')}"
        )
    return {
        "repo": repo,
        "path": path,
        "commit": commit,
        "github_url": github_url,
    }


def _context_for(target):
    if isinstance(target, Unit):
        module = target.module
        context = {
            "course_slug": module.course.slug,
            "module_slug": module.slug,
        }
        if module.parent_id is not None:
            context["parent_module_slug"] = module.parent.slug
        return context
    if isinstance(target, Module):
        return {
            "course_slug": target.course.slug,
            "parent_module_slug": (
                target.parent.slug if target.parent_id is not None else None
            ),
        }
    if isinstance(target, WorkshopPage):
        return {"workshop_slug": target.workshop.slug}
    return {}


def _required_level_for(target):
    if hasattr(target, 'effective_required_level'):
        return target.effective_required_level
    return getattr(target, 'required_level', None)


def _slug_for(target):
    if isinstance(target, MarketingPage):
        # Marketing pages have no slug column; their public path is the key.
        return target.public_path
    return target.slug


def _absolute(path):
    return f"{site_base_url().rstrip('/')}{path}"


def _serialize_content(target, content_id, *, include_body):
    markdown_field, html_field = _BODY_FIELDS[type(target)]
    markdown = getattr(target, markdown_field) or ''
    html = (getattr(target, html_field) or '') if html_field else None

    payload = {
        "content_id": str(content_id),
        "type": content_type_name(target),
        "id": target.pk,
        "title": target.title,
        "slug": _slug_for(target),
        "url": _absolute(target.get_absolute_url()),
        "share_url": _absolute(
            reverse('content_share_link', kwargs={'content_id': content_id})
        ),
        "is_public": bool(is_public_content(target)),
        "required_level": _required_level_for(target),
        "context": _context_for(target),
        "source": _source_for(target),
        "updated_at": isoformat_or_none(getattr(target, 'updated_at', None)),
    }

    if include_body:
        payload["markdown"] = markdown
        payload["html"] = html
        if isinstance(target, Unit):
            payload["homework_markdown"] = target.homework or ''
            payload["homework_html"] = target.homework_html or ''
    else:
        payload["markdown_length"] = len(markdown)
        payload["html_length"] = len(html) if html is not None else None
    return payload


def _match_summary(target):
    return {
        "type": content_type_name(target),
        "id": target.pk,
        "title": target.title,
        "url": _absolute(target.get_absolute_url()),
    }


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Content",
    summary="Get synced content by content_id UUID",
    methods={
        "GET": {
            "summary": "Get synced content by content_id UUID",
            "description": (
                "Staff-only read of any synced content row addressable by "
                "``/c/<uuid>``: metadata, source provenance, stored markdown, "
                "and stored HTML. Drafts, unpublished, and tier-gated rows "
                "are returned in full; ``is_public`` reports whether "
                "``/c/<uuid>`` would redirect anonymous visitors. "
                "``source.commit`` is the row's own last-sync commit, not the "
                "content source's latest synced commit."
            ),
            "path_params": {
                "content_id": {
                    "type": "string",
                    "format": "uuid",
                    "required": True,
                },
            },
            "query": {
                "include_body": {
                    "type": "string",
                    "enum": ["true", "false", "1", "0"],
                    "default": "true",
                    "required": False,
                    "description": (
                        "``false``/``0`` omits ``markdown``, ``html``, "
                        "``homework_markdown``, ``homework_html`` and returns "
                        "``markdown_length`` / ``html_length`` instead."
                    ),
                },
            },
            "responses": {
                200: {
                    "description": "The content row (course_unit example).",
                    "example": _CONTENT_EXAMPLE,
                },
                401: {
                    "description": "Missing, invalid, or non-staff token.",
                    "example": {"error": "Authentication token required"},
                },
                404: {
                    "description": "No row uses this content_id.",
                    "example": {
                        "error": "Content not found",
                        "code": "content_not_found",
                    },
                },
                409: {
                    "description": "More than one row uses this content_id.",
                    "example": {
                        "error": "content_id matches more than one row",
                        "code": "content_id_ambiguous",
                        "matches": [
                            {
                                "type": "article",
                                "id": 12,
                                "title": "Intro",
                                "url": "https://aishippinglabs.com/blog/intro",
                            },
                            {
                                "type": "workshop_page",
                                "id": 34,
                                "title": "Intro",
                                "url": "https://aishippinglabs.com/workshops/w/intro",
                            },
                        ],
                    },
                },
                422: {
                    "description": "Invalid include_body value.",
                    "example": {
                        "error": "include_body must be true or false",
                        "code": "validation_error",
                        "details": {"field": "include_body"},
                    },
                },
            },
        },
    },
)
def content_lookup_detail(request, content_id):
    """GET ``/api/content/<uuid>``."""
    raw_include_body = request.GET.get('include_body')
    include_body = True
    if raw_include_body is not None:
        normalized = raw_include_body.strip().lower()
        if normalized not in {'true', 'false', '1', '0'}:
            return validation_response(
                {"field": "include_body"},
                message="include_body must be true or false",
            )
        include_body = parse_bool_query(normalized)

    matches = find_content_matches(content_id)
    if not matches:
        return error_response(
            "Content not found", "content_not_found", status=404,
        )
    if len(matches) > 1:
        return JsonResponse(
            {
                "error": "content_id matches more than one row",
                "code": "content_id_ambiguous",
                "matches": [_match_summary(row) for row in matches],
            },
            status=409,
        )
    return JsonResponse(
        _serialize_content(matches[0], content_id, include_body=include_body),
    )
