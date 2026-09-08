"""Staff operator API for shared first-party comments."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.utils.display import display_name
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import parse_json_body, require_methods, validation_response
from comments import services as comment_services
from comments.models import ApiReplyOperation, Comment

CONTENT_TYPES = (
    'course_unit',
    'workshop_page',
    'sprint_plan',
    'book_club_note',
    'unknown',
)
KINDS = ('top_level', 'reply')
OWNER_FILTERS = {
    'course_unit': ('course_slug', 'module_slug', 'unit_slug'),
    'workshop_page': ('workshop_key', 'page_slug'),
    'book_club_note': ('book_slug', 'chapter_number'),
    'sprint_plan': ('plan_id',),
}


@dataclass(frozen=True)
class ResolvedThread:
    content_type: str
    owner: object
    context: dict


def _validation(field, message, **details):
    return validation_response({'field': field, **details}, message)


def _empty_filter(qs):
    return qs.none()


def _parse_int(raw, *, field, minimum, maximum=None):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, _validation(field, f'{field} must be an integer')
    if value < minimum or (maximum is not None and value > maximum):
        bound = f'{minimum}..{maximum}' if maximum is not None else f'>= {minimum}'
        return None, _validation(field, f'{field} must be {bound}')
    return value, None


def _parse_bool(raw, *, field):
    if raw == 'true':
        return True, None
    if raw == 'false':
        return False, None
    return None, _validation(field, f'{field} must be true or false')


def _parse_timestamp(raw, *, field):
    value = parse_datetime(raw)
    if value is None or value.tzinfo is None:
        return None, _validation(
            field,
            f'{field} must be an RFC 3339 datetime with an offset',
        )
    return value, None


def _owner_subquery(content_type, params):
    if content_type == 'course_unit':
        from content.models import Unit  # noqa: PLC0415

        qs = Unit.objects.all()
        if 'course_slug' in params:
            qs = qs.filter(module__course__slug=params['course_slug'])
        if 'module_slug' in params:
            qs = qs.filter(module__slug=params['module_slug'])
        if 'unit_slug' in params:
            qs = qs.filter(slug=params['unit_slug'])
        return qs.values('content_id')
    if content_type == 'workshop_page':
        from content.models import WorkshopPage  # noqa: PLC0415

        qs = WorkshopPage.objects.all()
        if 'workshop_key' in params:
            qs = qs.filter(workshop__slug=params['workshop_key'])
        if 'page_slug' in params:
            qs = qs.filter(slug=params['page_slug'])
        return qs.values('content_id')
    if content_type == 'sprint_plan':
        from plans.models import Plan  # noqa: PLC0415

        qs = Plan.objects.all()
        if 'plan_id' in params:
            qs = qs.filter(pk=params['plan_id'])
        return qs.values('comment_content_id')
    if content_type == 'book_club_note':
        from bookclub.models import Note  # noqa: PLC0415

        qs = Note.objects.all()
        if 'book_slug' in params:
            qs = qs.filter(chapter__book__slug=params['book_slug'])
        if 'chapter_number' in params:
            qs = qs.filter(chapter__number=params['chapter_number'])
        return qs.values('comment_content_id')
    raise ValueError(f'Unsupported content type: {content_type}')


def _apply_owner_filter(qs, content_type, params):
    field = 'content_id'
    if content_type == 'unknown':
        for known_type in CONTENT_TYPES[:-1]:
            qs = qs.exclude(**{f'{field}__in': _owner_subquery(known_type, {})})
        return qs
    return qs.filter(**{f'{field}__in': _owner_subquery(content_type, params)})


def _parse_list_filters(request, qs):
    params = request.GET
    try:
        limit_raw = params.get('limit', '50')
        limit, error = _parse_int(limit_raw, field='limit', minimum=1)
        if error:
            return None, None, None, error
        limit = min(limit, 200)
        offset, error = _parse_int(
            params.get('offset', '0'), field='offset', minimum=0,
        )
        if error:
            return None, None, None, error

        content_type = None
        if 'content_type' in params:
            content_type = params.get('content_type')
            if content_type not in CONTENT_TYPES:
                if content_type == '':
                    qs = _empty_filter(qs)
                else:
                    return None, None, None, _validation(
                        'content_type', 'Invalid content_type',
                        allowed=list(CONTENT_TYPES),
                    )

        kind = None
        if 'kind' in params:
            kind = params.get('kind')
            if kind not in KINDS:
                if kind == '':
                    qs = _empty_filter(qs)
                else:
                    return None, None, None, _validation(
                        'kind', 'Invalid kind', allowed=list(KINDS),
                    )
            elif kind == 'top_level':
                qs = qs.filter(parent__isnull=True)
            else:
                qs = qs.filter(parent__isnull=False)

        if 'content_id' in params:
            from uuid import UUID  # noqa: PLC0415

            raw = params.get('content_id')
            if raw == '':
                qs = _empty_filter(qs)
            else:
                try:
                    content_id = UUID(raw)
                except (ValueError, TypeError, AttributeError):
                    return None, None, None, _validation(
                        'content_id', 'content_id must be a UUID',
                    )
                qs = qs.filter(content_id=content_id)

        if 'parent_id' in params:
            raw = params.get('parent_id')
            parent_id, error = _parse_int(
                raw, field='parent_id', minimum=1,
            )
            if error:
                return None, None, None, error
            if kind == 'top_level':
                return None, None, None, _validation(
                    'parent_id', 'parent_id cannot be combined with kind=top_level',
                )
            qs = qs.filter(parent_id=parent_id)

        if 'author_email' in params:
            author_email = params.get('author_email')
            if not author_email:
                qs = _empty_filter(qs)
            else:
                qs = qs.filter(
                    Q(user__email__iexact=author_email)
                    | Q(user__email_aliases__email__iexact=author_email)
                ).distinct()

        since = until = None
        for field in ('since', 'until'):
            if field not in params:
                continue
            raw = params.get(field)
            if not raw:
                qs = _empty_filter(qs)
                continue
            value, error = _parse_timestamp(raw, field=field)
            if error:
                return None, None, None, error
            if field == 'since':
                since = value
                qs = qs.filter(created_at__gte=value)
            else:
                until = value
                qs = qs.filter(created_at__lt=value)
        if since is not None and until is not None and since >= until:
            return None, None, None, _validation(
                'since', 'since must be earlier than until',
            )

        unanswered = None
        if 'unanswered' in params:
            unanswered, error = _parse_bool(
                params.get('unanswered'), field='unanswered',
            )
            if error:
                return None, None, None, error
            if kind == 'reply' or 'parent_id' in params:
                return None, None, None, _validation(
                    'unanswered',
                    'unanswered cannot be combined with kind=reply or parent_id',
                )
            qs = qs.filter(parent__isnull=True)
            if unanswered:
                qs = qs.filter(reply_count=0)
            else:
                qs = qs.filter(reply_count__gt=0)

        owner_groups = []
        owner_values = {}
        for owner_type, fields in OWNER_FILTERS.items():
            values = {field: params.get(field) for field in fields if field in params}
            if values:
                owner_groups.append(owner_type)
                owner_values.update(values)
        if len(owner_groups) > 1:
            return None, None, None, _validation(
                'content_type', 'Owner-specific filters cannot be mixed',
            )
        inferred_type = owner_groups[0] if owner_groups else None
        if inferred_type == 'course_unit':
            if 'module_slug' in owner_values and 'course_slug' not in owner_values:
                return None, None, None, _validation(
                    'module_slug', 'module_slug requires course_slug',
                )
            if 'unit_slug' in owner_values and not {
                'course_slug', 'module_slug'
            }.issubset(owner_values):
                return None, None, None, _validation(
                    'unit_slug', 'unit_slug requires course_slug and module_slug',
                )
        elif inferred_type == 'workshop_page':
            if 'page_slug' in owner_values and 'workshop_key' not in owner_values:
                return None, None, None, _validation(
                    'page_slug', 'page_slug requires workshop_key',
                )
        elif inferred_type == 'book_club_note':
            if 'chapter_number' in owner_values and 'book_slug' not in owner_values:
                return None, None, None, _validation(
                    'chapter_number', 'chapter_number requires book_slug',
                )
            if 'chapter_number' in owner_values:
                value, error = _parse_int(
                    owner_values['chapter_number'],
                    field='chapter_number',
                    minimum=0,
                )
                if error:
                    return None, None, None, error
                owner_values['chapter_number'] = value
        elif inferred_type == 'sprint_plan':
            value, error = _parse_int(
                owner_values['plan_id'], field='plan_id', minimum=1,
            )
            if error:
                return None, None, None, error
            owner_values['plan_id'] = value

        if inferred_type and content_type and content_type != inferred_type:
            return None, None, None, _validation(
                'content_type', 'content_type contradicts owner-specific filters',
            )
        effective_type = inferred_type or content_type
        if effective_type:
            qs = _apply_owner_filter(qs, effective_type, owner_values)
        return qs, limit, offset, None
    except ValueError:
        return None, None, None, _validation('filters', 'Invalid filter value')


def _course_context(unit):
    module = unit.module
    course = module.course
    return {
        'course_title': course.title,
        'course_slug': course.slug,
        'module_title': module.title,
        'module_slug': module.slug,
        'unit_title': unit.title,
        'unit_slug': unit.slug,
        'title': f'{course.title} — {module.title} — {unit.title}',
        'url': f'{unit.get_absolute_url()}#qa-section',
    }


def _workshop_context(page):
    return {
        'workshop_title': page.workshop.title,
        'workshop_key': page.workshop.url_key,
        'page_title': page.title,
        'page_slug': page.slug,
        'title': f'{page.workshop.title} — {page.title}',
        'url': f'{page.get_absolute_url()}#qa-section',
    }


def _plan_context(plan):
    return {
        'plan_id': plan.pk,
        'plan_title': plan.display_title,
        'sprint_id': plan.sprint_id,
        'sprint_name': plan.sprint.name,
        'sprint_slug': plan.sprint.slug,
        'member_email': plan.member.email,
        'visibility': plan.visibility,
        'title': plan.display_title,
        'url': f'/studio/plans/{plan.pk}/',
    }


def _book_context(note):
    chapter = note.chapter
    book = chapter.book
    return {
        'note_id': note.pk,
        'book_title': book.title,
        'book_slug': book.slug,
        'chapter_number': chapter.number,
        'chapter_title': chapter.title,
        'note_owner_email': note.user.email,
        'title': f'{book.title} — Chapter {chapter.number}: {chapter.title}',
        'url': f'{note.get_absolute_url()}#qa-section-{note.pk}',
    }


def _resolve_threads(content_ids):
    """Resolve one page of UUIDs in four fixed bulk queries."""
    from bookclub.models import Note  # noqa: PLC0415
    from content.models import Unit, WorkshopPage  # noqa: PLC0415
    from plans.models import Plan  # noqa: PLC0415

    resolved = {}
    for unit in Unit.objects.filter(content_id__in=content_ids).select_related(
        'module__course'
    ):
        resolved[unit.content_id] = ResolvedThread(
            'course_unit', unit, _course_context(unit),
        )
    for page in WorkshopPage.objects.filter(content_id__in=content_ids).select_related(
        'workshop'
    ):
        resolved[page.content_id] = ResolvedThread(
            'workshop_page', page, _workshop_context(page),
        )
    for plan in Plan.objects.filter(
        comment_content_id__in=content_ids
    ).select_related('sprint', 'member'):
        resolved[plan.comment_content_id] = ResolvedThread(
            'sprint_plan', plan, _plan_context(plan),
        )
    for note in Note.objects.filter(
        comment_content_id__in=content_ids
    ).select_related('chapter__book', 'user'):
        resolved[note.comment_content_id] = ResolvedThread(
            'book_club_note', note, _book_context(note),
        )
    return resolved


def _serialize_comment(comment, resolved, *, idempotent_replay=None):
    thread = resolved.get(comment.content_id)
    row = {
        'id': comment.pk,
        'content_id': str(comment.content_id),
        'content_type': thread.content_type if thread else 'unknown',
        'kind': 'reply' if comment.parent_id else 'top_level',
        'parent_id': comment.parent_id,
        'thread_root_id': comment.parent_id or comment.pk,
        'body': comment.body,
        'author': {
            'id': comment.user_id,
            'email': comment.user.email,
            'display_name': display_name(comment.user),
        },
        'created_at': comment.created_at.isoformat(),
        'updated_at': comment.updated_at.isoformat(),
        'reply_count': comment.reply_count if comment.parent_id is None else 0,
        'context': thread.context if thread else None,
    }
    if idempotent_replay is not None:
        row['idempotent_replay'] = idempotent_replay
    return row


COMMENTS_QUERY = {
    'content_type': {'type': 'string', 'enum': list(CONTENT_TYPES), 'required': False},
    'content_id': {'type': 'string', 'format': 'uuid', 'required': False},
    'kind': {'type': 'string', 'enum': list(KINDS), 'required': False},
    'parent_id': {'type': 'integer', 'required': False},
    'author_email': {'type': 'string', 'format': 'email', 'required': False},
    'since': {'type': 'string', 'format': 'date-time', 'required': False},
    'until': {'type': 'string', 'format': 'date-time', 'required': False},
    'unanswered': {'type': 'boolean', 'required': False},
    'course_slug': {'type': 'string', 'required': False},
    'module_slug': {'type': 'string', 'required': False},
    'unit_slug': {'type': 'string', 'required': False},
    'workshop_key': {'type': 'string', 'required': False},
    'page_slug': {'type': 'string', 'required': False},
    'book_slug': {'type': 'string', 'required': False},
    'chapter_number': {'type': 'integer', 'required': False},
    'plan_id': {'type': 'integer', 'required': False},
    'limit': {'type': 'integer', 'default': 50, 'maximum': 200, 'required': False},
    'offset': {'type': 'integer', 'default': 0, 'required': False},
}

COMMENT_ROW_SCHEMA = {
    'type': 'object',
    'required': [
        'id', 'content_id', 'content_type', 'kind', 'parent_id',
        'thread_root_id', 'body', 'author', 'created_at', 'updated_at',
        'reply_count', 'context',
    ],
    'properties': {
        'id': {'type': 'integer'},
        'content_id': {'type': 'string', 'format': 'uuid'},
        'content_type': {'type': 'string', 'enum': list(CONTENT_TYPES)},
        'kind': {'type': 'string', 'enum': list(KINDS)},
        'parent_id': {'type': ['integer', 'null']},
        'thread_root_id': {'type': 'integer'},
        'body': {'type': 'string', 'description': 'Verbatim, unrendered plain text.'},
        'author': {
            'type': 'object',
            'required': ['id', 'email', 'display_name'],
            'properties': {
                'id': {'type': 'integer'},
                'email': {'type': 'string', 'format': 'email'},
                'display_name': {'type': 'string'},
            },
        },
        'created_at': {'type': 'string', 'format': 'date-time'},
        'updated_at': {'type': 'string', 'format': 'date-time'},
        'reply_count': {'type': 'integer'},
        'context': {
            'type': ['object', 'null'],
            'description': 'Owner-specific safe context; null for unknown UUIDs.',
            'additionalProperties': True,
        },
    },
}

COMMENTS_LIST_SCHEMA = {
    'type': 'object',
    'required': ['comments', 'count', 'limit', 'offset'],
    'properties': {
        'comments': {'type': 'array', 'items': COMMENT_ROW_SCHEMA},
        'count': {'type': 'integer'},
        'limit': {'type': 'integer'},
        'offset': {'type': 'integer'},
    },
}


@token_required(structured_errors=True)
@require_methods('GET')
@openapi_spec(
    tag='Comments',
    summary='List shared first-party comments',
    methods={'GET': {
        'description': (
            'Returns top-level comments and replies as a flat newest-first '
            'collection. All filters combine with AND. Bodies are unrendered '
            'plain text. Unknown owner UUIDs remain visible with null context.'
        ),
        'query': COMMENTS_QUERY,
        'responses': {
            200: {
                'description': 'Filtered comments with deterministic pagination.',
                'schema': COMMENTS_LIST_SCHEMA,
                'example': {
                    'comments': [], 'count': 0, 'limit': 50, 'offset': 0,
                },
            },
            401: {'description': 'Missing or invalid staff-owned operator token.'},
            422: {'description': 'Invalid or contradictory filter.'},
        },
    }},
)
def comments_collection(request):
    qs = Comment.objects.select_related('user', 'parent').annotate(
        reply_count=Count('replies', distinct=True),
    )
    qs, limit, offset, error = _parse_list_filters(request, qs)
    if error:
        return error
    qs = qs.order_by('-created_at', '-id')
    count = qs.count()
    comments = list(qs[offset:offset + limit])
    resolved = _resolve_threads({comment.content_id for comment in comments})
    return JsonResponse({
        'comments': [_serialize_comment(comment, resolved) for comment in comments],
        'count': count,
        'limit': limit,
        'offset': offset,
    })


def _normalize_reply_request(request):
    key_header = request.headers.get('Idempotency-Key')
    if key_header is None or not key_header.strip():
        return None, None, error_response(
            'Idempotency-Key header is required',
            'idempotency_key_required',
            status=422,
            details={'field': 'Idempotency-Key'},
        )
    key = key_header.strip()
    if len(key) > 255 or any(ord(char) < 33 or ord(char) > 126 for char in key):
        return None, None, _validation(
            'Idempotency-Key',
            'Idempotency-Key must be 1..255 visible ASCII characters',
        )

    data, parse_error = parse_json_body(request)
    if parse_error:
        return None, None, parse_error
    if not isinstance(data, dict):
        return None, None, _validation('body', 'Body must be a JSON object')
    unknown = sorted(set(data) - {'body'})
    if unknown:
        return None, None, _validation(
            unknown[0], f'Unknown field: {unknown[0]}',
        )
    body = data.get('body')
    if not isinstance(body, str):
        return None, None, _validation('body', 'body must be a string')
    body = body.strip()
    if not body:
        return None, None, _validation('body', 'body must not be blank')
    if len(body) > 10_000:
        return None, None, _validation(
            'body', 'body must be at most 10000 Unicode code points',
        )
    return key, body, None


def _request_digest(comment_id, body):
    payload = json.dumps(
        {'parent_id': comment_id, 'body': body},
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _replay_response(operation, digest):
    if operation.request_digest != digest:
        return error_response(
            'Idempotency key was already used for a different request',
            'idempotency_key_reused',
            status=409,
        )
    reply = operation.resulting_comment
    if reply is None:
        return error_response(
            'The idempotent result is no longer available',
            'idempotent_result_unavailable',
            status=409,
        )
    reply = Comment.objects.select_related('user', 'parent').annotate(
        reply_count=Count('replies', distinct=True),
    ).get(pk=reply.pk)
    resolved = _resolve_threads({reply.content_id})
    return JsonResponse(
        _serialize_comment(reply, resolved, idempotent_replay=True),
        status=200,
    )


def _resolve_one_thread(content_id):
    return _resolve_threads({content_id}).get(content_id)


def _can_write_thread(thread, actor):
    if thread.content_type == 'sprint_plan':
        from plans.comments_permissions import (  # noqa: PLC0415
            viewer_can_write_plan_thread,
        )

        return viewer_can_write_plan_thread(thread.owner, actor)
    if thread.content_type == 'book_club_note':
        from bookclub.comments_permissions import (  # noqa: PLC0415
            viewer_can_write_book_note_thread,
        )

        return viewer_can_write_book_note_thread(thread.owner, actor)
    return True


@token_required(structured_errors=True)
@csrf_exempt
@require_methods('POST')
@openapi_spec(
    tag='Comments',
    summary='Reply to a shared first-party comment',
    methods={'POST': {
        'description': (
            'Creates one direct plain-text reply as the staff token owner '
            'through the shared comment service. Idempotency-Key is required; '
            'an identical replay returns the original reply with 200 and never '
            'repeats notifications or activation.'
        ),
        'headers': {
            'Idempotency-Key': {
                'type': 'string', 'minLength': 1, 'maxLength': 255,
                'required': True,
                'description': 'Visible-ASCII key scoped to this operator token.',
            },
        },
        'request_body': {
            'required': ['body'],
            'properties': {
                'body': {
                    'type': 'string', 'minLength': 1, 'maxLength': 10000,
                    'description': 'Plain text; HTML and Markdown are not rendered.',
                },
            },
            'example': {'body': 'Here is the answer.'},
        },
        'responses': {
            201: {
                'description': 'Reply created; idempotent_replay is false.',
                'schema': {
                    **COMMENT_ROW_SCHEMA,
                    'required': [
                        *COMMENT_ROW_SCHEMA['required'], 'idempotent_replay',
                    ],
                    'properties': {
                        **COMMENT_ROW_SCHEMA['properties'],
                        'idempotent_replay': {'type': 'boolean', 'const': False},
                    },
                },
            },
            200: {
                'description': 'Original reply replayed; idempotent_replay is true.',
                'schema': {
                    **COMMENT_ROW_SCHEMA,
                    'required': [
                        *COMMENT_ROW_SCHEMA['required'], 'idempotent_replay',
                    ],
                    'properties': {
                        **COMMENT_ROW_SCHEMA['properties'],
                        'idempotent_replay': {'type': 'boolean', 'const': True},
                    },
                },
            },
            401: {'description': 'Missing or invalid staff-owned operator token.'},
            403: {'description': 'Actor cannot write this resolved thread.'},
            404: {'description': 'Comment or registered thread owner not found.'},
            409: {'description': 'Key reused for a different request.'},
            422: {'description': 'Invalid parent, body, JSON fields, or idempotency key.'},
        },
    }},
)
def comment_reply(request, comment_id):
    key, body, error = _normalize_reply_request(request)
    if error:
        return error
    token = request.auth_token
    digest = _request_digest(comment_id, body)
    existing = ApiReplyOperation.objects.select_related(
        'resulting_comment__user', 'resulting_comment__parent'
    ).filter(token_identity=token.pk, idempotency_key=key).first()
    if existing:
        return _replay_response(existing, digest)

    try:
        with transaction.atomic():
            parent = Comment.objects.select_for_update().filter(pk=comment_id).first()
            if parent is None:
                return error_response(
                    'Comment not found', 'comment_not_found', status=404,
                )
            if parent.parent_id is not None:
                return error_response(
                    'Replies cannot receive replies', 'invalid_parent', status=422,
                )
            thread = _resolve_one_thread(parent.content_id)
            if thread is None:
                return error_response(
                    'Comment thread owner not found', 'thread_not_found', status=404,
                )
            if not _can_write_thread(thread, request.user):
                return error_response(
                    'The operator cannot reply to this thread',
                    'comment_reply_forbidden',
                    status=403,
                )

            # Re-check under the parent lock. This serializes identical-parent
            # submissions before any activation/notification side effect.
            existing = ApiReplyOperation.objects.select_related(
                'resulting_comment__user', 'resulting_comment__parent'
            ).filter(token_identity=token.pk, idempotency_key=key).first()
            if existing:
                return _replay_response(existing, digest)

            reply = comment_services.create_comment(
                content_id=parent.content_id,
                user=request.user,
                parent=parent,
                body=body,
            )
            ApiReplyOperation.objects.create(
                token_identity=token.pk,
                token_name_snapshot=token.name,
                token_prefix_snapshot=token.key_prefix,
                idempotency_key=key,
                request_digest=digest,
                resulting_comment=reply,
                resulting_comment_id_snapshot=reply.pk,
                actor=request.user,
                actor_id_snapshot=request.user.pk,
                actor_email_snapshot=request.user.email,
            )
    except IntegrityError:
        # A same-token/same-key request on another parent can race the unique
        # constraint. Its reply and side effects rolled back with the failed
        # transaction; the committed winner is now the only operation.
        existing = ApiReplyOperation.objects.select_related(
            'resulting_comment__user', 'resulting_comment__parent'
        ).get(token_identity=token.pk, idempotency_key=key)
        return _replay_response(existing, digest)

    reply = Comment.objects.select_related('user', 'parent').annotate(
        reply_count=Count('replies', distinct=True),
    ).get(pk=reply.pk)
    return JsonResponse(
        _serialize_comment(reply, {reply.content_id: thread}, idempotent_replay=False),
        status=201,
    )
