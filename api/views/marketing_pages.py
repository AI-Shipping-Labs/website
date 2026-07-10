"""Staff token API for standalone marketing pages."""

from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import (
    body_must_be_object_response,
    delete_not_available_response,
    parse_json_body,
    require_methods,
    validation_response,
)
from content.models import MarketingPage
from content.models.marketing_page import (
    NAV_SECTION_ABOUT,
    NAV_SECTION_COMMUNITY,
    NAV_SECTION_NONE,
    NAV_SECTION_RESOURCES,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
)
from integrations.config import site_base_url
from studio.utils import is_synced

DELETE_NOT_AVAILABLE_MESSAGE = (
    "Marketing page deletion is not available through the API. "
    "Use Studio or set status=draft to unpublish this page."
)

READ_ONLY_FIELDS = {
    'id',
    'content_id',
    'content_html',
    'preview_token',
    'source_repo',
    'source_path',
    'source_commit',
    'created_at',
    'updated_at',
    'published_at',
}

WRITABLE_FIELDS = {
    'title',
    'public_path',
    'description',
    'meta_description',
    'content_markdown',
    'cover_image_url',
    'tags',
    'status',
    'show_in_sitemap',
    'nav_section',
    'nav_label',
    'nav_order',
}

VALID_STATUSES = {STATUS_DRAFT, STATUS_PUBLISHED}
VALID_NAV_SECTIONS = {
    NAV_SECTION_NONE,
    NAV_SECTION_ABOUT,
    NAV_SECTION_COMMUNITY,
    NAV_SECTION_RESOURCES,
}

_PAGE_EXAMPLE = {
    'id': 42,
    'content_id': '11111111-1111-1111-1111-111111111111',
    'title': 'AI Shipping Labs Community Story',
    'public_path': '/community-story',
    'public_url': 'https://aishippinglabs.com/community-story',
    'description': 'A standalone landing page.',
    'meta_description': 'A standalone landing page for AI Shipping Labs.',
    'content_markdown': '# Community Story\n\nShip projects with us.',
    'content_html': '<p>Ship projects with us.</p>',
    'cover_image_url': '',
    'tags': ['community'],
    'status': 'published',
    'show_in_sitemap': True,
    'nav_section': 'community',
    'nav_label': 'Community Story',
    'nav_order': 10,
    'source_repo': '',
    'source_path': '',
    'editable': True,
    'created_at': '2026-07-10T10:00:00+00:00',
    'updated_at': '2026-07-10T10:00:00+00:00',
}


def _site_url(path):
    return f'{site_base_url().rstrip("/")}{path}'


def _iso(value):
    return value.isoformat() if value is not None else None


def serialize_marketing_page(page):
    return {
        'id': page.id,
        'content_id': str(page.content_id),
        'title': page.title,
        'public_path': page.public_path,
        'public_url': _site_url(page.get_absolute_url()),
        'description': page.description,
        'meta_description': page.meta_description,
        'content_markdown': page.content_markdown,
        'content_html': page.content_html,
        'cover_image_url': page.cover_image_url,
        'tags': page.tags or [],
        'status': page.status,
        'show_in_sitemap': page.show_in_sitemap,
        'nav_section': page.nav_section,
        'nav_label': page.nav_label,
        'nav_order': page.nav_order,
        'source_repo': page.source_repo or '',
        'source_path': page.source_path or '',
        'editable': not is_synced(page),
        'created_at': _iso(page.created_at),
        'updated_at': _iso(page.updated_at),
    }


def _preview_payload(page):
    return {
        'content_id': str(page.content_id),
        'preview_url': _site_url(page.get_preview_url()),
    }


def _read_only_field_response(field):
    return error_response(
        f'{field} is read-only',
        'read_only_field',
        status=422,
        details={'field': field},
    )


def _validation_errors(exc):
    if hasattr(exc, 'message_dict'):
        return exc.message_dict
    return {'__all__': exc.messages}


def _validate_payload_fields(data):
    for field in sorted(READ_ONLY_FIELDS):
        if field in data:
            return _read_only_field_response(field)
    unknown = sorted(set(data) - WRITABLE_FIELDS)
    if unknown:
        return validation_response({
            field: 'Unknown field.'
            for field in unknown
        })
    return None


def _apply_payload(page, data, *, partial):
    if not partial:
        required = ['title', 'public_path']
        missing = [field for field in required if not data.get(field)]
        if missing:
            raise ValidationError({
                field: 'This field is required.'
                for field in missing
            })

    for field in [
        'title',
        'public_path',
        'description',
        'meta_description',
        'content_markdown',
        'cover_image_url',
        'nav_label',
    ]:
        if field in data:
            setattr(page, field, '' if data[field] is None else str(data[field]))

    if 'tags' in data:
        if not isinstance(data['tags'], list) or not all(
            isinstance(item, str) for item in data['tags']
        ):
            raise ValidationError({'tags': 'Tags must be an array of strings.'})
        page.tags = data['tags']

    if 'status' in data:
        status = str(data['status']).strip().lower()
        if status not in VALID_STATUSES:
            raise ValidationError({'status': 'Unknown status.'})
        page.status = status

    if 'show_in_sitemap' in data:
        if not isinstance(data['show_in_sitemap'], bool):
            raise ValidationError({'show_in_sitemap': 'Must be a boolean.'})
        page.show_in_sitemap = data['show_in_sitemap']

    if 'nav_section' in data:
        section = str(data['nav_section']).strip().lower()
        if section not in VALID_NAV_SECTIONS:
            raise ValidationError({'nav_section': 'Unknown navigation section.'})
        page.nav_section = section

    if 'nav_order' in data:
        try:
            page.nav_order = int(data['nav_order'])
        except (TypeError, ValueError) as exc:
            raise ValidationError({'nav_order': 'Must be an integer.'}) from exc


@token_required
@csrf_exempt
@require_methods('GET', 'POST', 'DELETE')
@openapi_spec(
    tag='Marketing Pages',
    summary='List, create, or attempt to delete marketing pages',
    methods={
        'GET': {
            'summary': 'List marketing pages',
            'query': {
                'status': {'type': 'string', 'enum': sorted(VALID_STATUSES)},
                'nav_section': {
                    'type': 'string',
                    'enum': sorted(VALID_NAV_SECTIONS),
                },
                'q': {'type': 'string'},
            },
            'responses': {
                200: {
                    'description': 'List of marketing pages.',
                    'example': {'marketing_pages': [_PAGE_EXAMPLE]},
                },
                422: {'description': 'Unknown filter value.'},
            },
        },
        'POST': {
            'summary': 'Create a marketing page',
            'request_body': {
                'required': ['title', 'public_path'],
                'properties': {
                    'title': {'type': 'string'},
                    'public_path': {'type': 'string'},
                    'description': {'type': 'string'},
                    'meta_description': {'type': 'string'},
                    'content_markdown': {'type': 'string'},
                    'cover_image_url': {'type': 'string'},
                    'tags': {'type': 'array', 'items': {'type': 'string'}},
                    'status': {'type': 'string', 'enum': sorted(VALID_STATUSES)},
                    'show_in_sitemap': {'type': 'boolean'},
                    'nav_section': {
                        'type': 'string',
                        'enum': sorted(VALID_NAV_SECTIONS),
                    },
                    'nav_label': {'type': 'string'},
                    'nav_order': {'type': 'integer'},
                },
                'example': {
                    'title': 'Campaign Overview',
                    'public_path': '/campaign-overview',
                    'content_markdown': '# Campaign Overview\n\nDetails.',
                    'status': 'published',
                    'nav_section': 'none',
                },
            },
            'responses': {
                201: {'description': 'Marketing page created.', 'example': _PAGE_EXAMPLE},
                400: {'description': 'Invalid JSON body.'},
                422: {'description': 'Validation error.'},
            },
        },
        'DELETE': {
            'summary': 'DELETE is not available on this route',
            'responses': {
                405: {
                    'description': 'Marketing page deletion is not available.',
                    'example': {
                        'error': DELETE_NOT_AVAILABLE_MESSAGE,
                        'code': 'marketing_page_delete_not_available',
                    },
                },
            },
        },
    },
)
def marketing_pages_collection(request):
    if request.method == 'DELETE':
        return delete_not_available_response(
            DELETE_NOT_AVAILABLE_MESSAGE,
            'marketing_page_delete_not_available',
        )

    if request.method == 'GET':
        qs = MarketingPage.objects.all().order_by('title')
        status_filter = request.GET.get('status')
        if status_filter:
            if status_filter not in VALID_STATUSES:
                return validation_response({'status': 'Unknown status.'})
            qs = qs.filter(status=status_filter)
        nav_section = request.GET.get('nav_section')
        if nav_section:
            if nav_section not in VALID_NAV_SECTIONS:
                return validation_response({'nav_section': 'Unknown navigation section.'})
            qs = qs.filter(nav_section=nav_section)
        query = request.GET.get('q')
        if query:
            qs = qs.filter(title__icontains=query)
        return JsonResponse({
            'marketing_pages': [serialize_marketing_page(page) for page in qs],
        })

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()

    field_error = _validate_payload_fields(data)
    if field_error is not None:
        return field_error

    page = MarketingPage()
    try:
        _apply_payload(page, data, partial=False)
        page.save()
    except ValidationError as exc:
        return validation_response(_validation_errors(exc))

    return JsonResponse(serialize_marketing_page(page), status=201)


@token_required
@csrf_exempt
@require_methods('GET', 'PATCH', 'DELETE')
@openapi_spec(
    tag='Marketing Pages',
    summary='Retrieve, update, or attempt to delete a marketing page',
    methods={
        'GET': {
            'summary': 'Retrieve a marketing page by content_id',
            'responses': {
                200: {'description': 'Marketing page detail.', 'example': _PAGE_EXAMPLE},
                404: {'description': 'Marketing page not found.'},
            },
        },
        'PATCH': {
            'summary': 'Update a manual marketing page',
            'responses': {
                200: {'description': 'Marketing page updated.', 'example': _PAGE_EXAMPLE},
                409: {'description': 'Synced page is read-only.'},
                422: {'description': 'Validation error.'},
            },
        },
        'DELETE': {
            'summary': 'DELETE is not available on this route',
            'responses': {
                405: {
                    'description': 'Marketing page deletion is not available.',
                    'example': {
                        'error': DELETE_NOT_AVAILABLE_MESSAGE,
                        'code': 'marketing_page_delete_not_available',
                    },
                },
            },
        },
    },
)
def marketing_page_detail(request, content_id):
    if request.method == 'DELETE':
        return delete_not_available_response(
            DELETE_NOT_AVAILABLE_MESSAGE,
            'marketing_page_delete_not_available',
        )
    page = get_object_or_404(MarketingPage, content_id=content_id)
    if request.method == 'GET':
        return JsonResponse(serialize_marketing_page(page))
    if is_synced(page):
        return error_response(
            'Synced marketing pages are read-only through the API.',
            'synced_marketing_page_read_only',
            status=409,
        )

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return body_must_be_object_response()

    field_error = _validate_payload_fields(data)
    if field_error is not None:
        return field_error
    try:
        _apply_payload(page, data, partial=True)
        page.save()
    except ValidationError as exc:
        return validation_response(_validation_errors(exc))
    return JsonResponse(serialize_marketing_page(page))


@token_required
@csrf_exempt
@require_methods('GET')
@openapi_spec(
    tag='Marketing Pages',
    methods={
        'GET': {
            'summary': 'Get marketing page draft preview link',
            'responses': {
                200: {'description': 'Preview URL for the marketing page.'},
                404: {'description': 'Marketing page not found.'},
            },
        },
    },
)
def marketing_page_preview_link(request, content_id):
    page = get_object_or_404(MarketingPage, content_id=content_id)
    return JsonResponse(_preview_payload(page), status=200)


@token_required
@csrf_exempt
@require_methods('POST')
@openapi_spec(
    tag='Marketing Pages',
    methods={
        'POST': {
            'summary': 'Regenerate marketing page draft preview token',
            'responses': {
                200: {'description': 'New preview URL for the marketing page.'},
                404: {'description': 'Marketing page not found.'},
            },
        },
    },
)
def marketing_page_preview_token_regenerate(request, content_id):
    page = get_object_or_404(MarketingPage, content_id=content_id)
    page.regenerate_preview_token()
    return JsonResponse(_preview_payload(page), status=200)
