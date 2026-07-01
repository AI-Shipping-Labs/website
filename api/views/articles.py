"""Staff token API for synced article draft preview links."""

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.utils import require_methods
from content.models import Article
from integrations.config import site_base_url


def _absolute_preview_url(article):
    return f'{site_base_url().rstrip("/")}{article.get_preview_url()}'


def _preview_payload(article):
    return {
        'content_id': str(article.content_id),
        'preview_url': _absolute_preview_url(article),
    }


@csrf_exempt
@token_required
@require_methods('GET')
@openapi_spec(
    tag='Articles',
    methods={
        'GET': {
            'summary': 'Get article draft preview link',
            'path_params': {
                'content_id': {
                    'type': 'string',
                    'format': 'uuid',
                    'required': True,
                },
            },
            'responses': {
                200: {'description': 'Preview URL for the synced article'},
                401: {'description': 'Missing or invalid staff token'},
                404: {'description': 'Article not found'},
            },
        },
    },
)
def article_preview_link(request, content_id):
    article = get_object_or_404(Article, content_id=content_id)
    return JsonResponse(_preview_payload(article), status=200)


@csrf_exempt
@token_required
@require_methods('POST')
@openapi_spec(
    tag='Articles',
    methods={
        'POST': {
            'summary': 'Regenerate article draft preview token',
            'path_params': {
                'content_id': {
                    'type': 'string',
                    'format': 'uuid',
                    'required': True,
                },
            },
            'responses': {
                200: {'description': 'New preview URL for the synced article'},
                401: {'description': 'Missing or invalid staff token'},
                404: {'description': 'Article not found'},
            },
        },
    },
)
def article_preview_token_regenerate(request, content_id):
    article = get_object_or_404(Article, content_id=content_id)
    article.regenerate_preview_token()
    return JsonResponse(_preview_payload(article), status=200)
