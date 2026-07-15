import json
import logging

from django.core import signing
from django.db import transaction
from django.db.models import F
from django.http import HttpResponseRedirect, JsonResponse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_POST

from content.access import build_gating_context, can_access
from content.models import Download, Project

logger = logging.getLogger(__name__)


def _no_store(response):
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Referrer-Policy'] = 'no-referrer'
    return response


def _private_not_found():
    """Return an enumeration-safe 404 for every delivery surface."""
    return _no_store(JsonResponse({'error': 'Not found.'}, status=404))


@require_POST
def request_download(request, slug):
    """Enumeration-safe, transactional request for one published download."""
    download = Download.objects.filter(slug=slug, published=True).first()
    if download is None:
        return _private_not_found()
    from content.services.download_delivery import normalize_download_surface
    surface = normalize_download_surface(request.GET.get('surface'))
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return _no_store(JsonResponse({'error': 'Invalid request.'}, status=400))
    if not isinstance(data, dict):
        return _no_store(JsonResponse({'error': 'Invalid request.'}, status=400))

    from content.services.download_requests import (
        GENERIC_REQUEST_MESSAGE,
        consume_download_request_rate_limit,
        normalize_download_email,
        request_download_for_email,
    )

    try:
        email = normalize_download_email(data.get('email'))
    except ValueError as exc:
        return _no_store(JsonResponse({'error': str(exc)}, status=400))

    if consume_download_request_rate_limit(request, email, slug):
        return _no_store(JsonResponse(
            {'error': 'Too many requests. Please try again later.'},
            status=429,
        ))

    try:
        request_download_for_email(
            email,
            download,
            newsletter_opt_in=data.get('newsletter_opt_in') is True,
            surface=surface,
        )
    except Exception:
        # Provider exceptions can contain the recipient, endpoint, or other
        # request secrets. Keep production logs deliberately payload-free.
        logger.error('download_request_failed reason=email_delivery_failure')
        return _no_store(JsonResponse(
            {'error': 'We could not send the email. Please try again.'},
            status=503,
        ))
    return _no_store(JsonResponse(
        {'status': 'accepted', 'message': GENERIC_REQUEST_MESSAGE},
        status=202,
    ))


@require_POST
def submit_project(request):
    """Community project submission endpoint.

    Authenticated users can submit a project for admin review.
    Creates a Project with status='pending_review' and published=False.
    """
    if not request.user.is_authenticated:
        return JsonResponse(
            {'error': 'Authentication required'},
            status=401,
        )

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse(
            {'error': 'Invalid JSON'},
            status=400,
        )

    title = data.get('title', '').strip()
    if not title:
        return JsonResponse(
            {'error': 'Title is required'},
            status=400,
        )

    description = data.get('description', '').strip()
    if not description:
        return JsonResponse(
            {'error': 'Description is required'},
            status=400,
        )

    # Generate a unique slug from the title
    base_slug = slugify(title)
    slug = base_slug
    counter = 1
    while Project.objects.filter(slug=slug).exists():
        slug = f'{base_slug}-{counter}'
        counter += 1

    difficulty = data.get('difficulty', '').strip()
    valid_difficulties = [c[0] for c in Project.DIFFICULTY_CHOICES]
    if difficulty and difficulty not in valid_difficulties:
        return JsonResponse(
            {'error': f'Invalid difficulty. Must be one of: {", ".join(valid_difficulties)}'},
            status=400,
        )

    tags = data.get('tags', [])
    if not isinstance(tags, list):
        return JsonResponse(
            {'error': 'Tags must be a list'},
            status=400,
        )

    from django.utils import timezone

    project = Project(
        title=title,
        slug=slug,
        description=description,
        content_markdown=data.get('content_markdown', ''),
        difficulty=difficulty,
        tags=tags,
        source_code_url=data.get('source_code_url', ''),
        demo_url=data.get('demo_url', ''),
        cover_image_url=data.get('cover_image_url', ''),
        author=request.user.get_full_name() or request.user.email,
        date=timezone.now().date(),
        status='pending_review',
        published=False,
        submitter=request.user,
    )
    project.save()

    return JsonResponse({
        'id': project.pk,
        'slug': project.slug,
        'status': project.status,
        'message': 'Project submitted for review',
    }, status=201)


@require_GET
def download_file(request, slug):
    """File download endpoint.

    Streams the file if the user has access. Returns 403 otherwise.
    For lead magnet downloads (required_level=0), anonymous users get 401
    with requires_email=true so the frontend can show an email signup form.

    On success, increments download_count and redirects to the file_url.
    """
    download = Download.objects.filter(slug=slug, published=True).first()
    if download is None:
        return _private_not_found()
    from content.services.download_delivery import normalize_download_surface
    surface = normalize_download_surface(request.GET.get('surface'))
    grant_token = request.GET.get('grant', '')
    grant_user = None
    grant_payload = None

    if grant_token:
        from content.models import DownloadDeliveryGrant
        from content.services.download_delivery import (
            apply_explicit_newsletter_opt_in,
            grant_secret_matches,
            unpack_delivery_grant_token,
        )
        try:
            grant_id, secret = unpack_delivery_grant_token(grant_token)
            grant_payload = (grant_id, secret)
            # A valid mailbox click confirms separately requested newsletter
            # consent even when entitlement or asset delivery later fails.
            # Do not consume the delivery grant until presigning succeeds.
            with transaction.atomic():
                grant_stub = (
                    DownloadDeliveryGrant.objects
                    .select_for_update()
                    .select_related('user')
                    .get(pk=grant_id)
                )
                if (
                    grant_stub.download_id != download.pk
                    or grant_stub.redeemed_at is not None
                    or grant_stub.expires_at <= timezone.now()
                    or not grant_secret_matches(grant_stub, secret)
                    or not grant_stub.user.email_verified
                ):
                    raise ValueError('Invalid download grant')
                if grant_stub.newsletter_opt_in:
                    apply_explicit_newsletter_opt_in(grant_stub.user)
                grant_user = grant_stub.user
                surface = normalize_download_surface(grant_stub.surface)
        except (
            signing.BadSignature,
            DownloadDeliveryGrant.DoesNotExist,
            ValueError,
        ):
            logger.info(
                'download_delivery_denied slug=%s required_level=%s surface=%s reason=invalid_grant',
                slug,
                download.required_level,
                surface,
            )
            return _no_store(JsonResponse(
                {'error': 'This download link is invalid or expired.'},
                status=403,
            ))

    acting_user = grant_user or request.user

    # Lead magnet flow: required_level 0 but user is anonymous
    if download.required_level == 0 and not acting_user.is_authenticated:
        logger.info(
            'download_delivery_denied slug=%s required_level=%s surface=%s reason=email_required',
            slug,
            download.required_level,
            surface,
        )
        return _no_store(JsonResponse(
            {
                'error': 'Email signup required',
                'requires_email': True,
                'download_slug': slug,
            },
            status=401,
        ))

    # Gated download: user does not have sufficient access level
    if not can_access(acting_user, download):
        gating = build_gating_context(acting_user, download, 'download')
        if gating.get('gated_reason') == 'unverified_email':
            logger.info(
                'download_delivery_denied slug=%s required_level=%s surface=%s reason=unverified_email',
                slug,
                download.required_level,
                surface,
            )
            return _no_store(JsonResponse(
                {
                    'error': 'Email verification required',
                    'requires_email_verification': True,
                    'gated_reason': 'unverified_email',
                    'download_slug': slug,
                },
                status=403,
            ))
        if grant_token:
            logger.info(
                'download_delivery_denied slug=%s required_level=%s surface=%s reason=access',
                slug,
                download.required_level,
                surface,
            )
            return _no_store(HttpResponseRedirect(
                f'{download.get_absolute_url()}?delivery=access-required',
            ))
        logger.info(
            'download_delivery_denied slug=%s required_level=%s surface=%s reason=access',
            slug,
            download.required_level,
            surface,
        )
        return _no_store(JsonResponse(
            {'error': 'Insufficient access level'},
            status=403,
        ))

    if not download.delivery_ready:
        logger.warning(
            'download_delivery_denied slug=%s required_level=%s surface=%s reason=asset_not_ready',
            slug,
            download.required_level,
            surface,
        )
        return _no_store(JsonResponse(
            {'error': 'This download is temporarily unavailable.'},
            status=503,
        ))

    from content.services.download_delivery import (
        build_download_presigned_url,
        verify_download_object_exists,
    )

    if grant_payload:
        from content.models import DownloadDeliveryGrant
        from content.services.download_delivery import (
            grant_secret_matches,
        )
        grant_id, secret = grant_payload
        try:
            with transaction.atomic():
                grant = (
                    DownloadDeliveryGrant.objects
                    .select_for_update()
                    .select_related('user', 'download')
                    .get(pk=grant_id)
                )
                if (
                    grant.download_id != download.pk
                    or grant.redeemed_at is not None
                    or grant.expires_at <= timezone.now()
                    or not grant_secret_matches(grant, secret)
                    or not grant.user.email_verified
                    or not can_access(grant.user, download)
                ):
                    logger.info(
                        'download_delivery_denied slug=%s required_level=%s surface=%s reason=invalid_or_replayed_grant',
                        slug,
                        download.required_level,
                        surface,
                    )
                    return _no_store(JsonResponse(
                        {'error': 'This download link is invalid or expired.'},
                        status=403,
                    ))
                # Re-check at redemption time: an object that existed during
                # content sync may have been removed before this one-time
                # handoff.  Do this before consuming the grant or count.
                verify_download_object_exists(download.storage_key)
                presigned_url = build_download_presigned_url(download)
                grant.redeemed_at = timezone.now()
                grant.save(update_fields=['redeemed_at'])
                Download.objects.filter(pk=download.pk).update(
                    download_count=F('download_count') + 1,
                )
                acting_user = grant.user
        except Exception:
            # S3/presigner exceptions may echo object keys, signed URLs, or
            # credentials. Keep the safe routing dimensions, but never attach
            # the exception payload or trace.
            logger.error(
                'download_delivery_denied slug=%s required_level=%s surface=%s reason=grant_presign_failure',
                slug,
                download.required_level,
                surface,
            )
            return _no_store(JsonResponse(
                {'error': 'This download is temporarily unavailable.'},
                status=503,
            ))
    else:
        try:
            verify_download_object_exists(download.storage_key)
            presigned_url = build_download_presigned_url(download)
        except Exception:
            logger.error(
                'download_delivery_denied slug=%s required_level=%s surface=%s reason=session_presign_failure',
                slug,
                download.required_level,
                surface,
            )
            return _no_store(JsonResponse(
                {'error': 'This download is temporarily unavailable.'},
                status=503,
            ))
        Download.objects.filter(pk=download.pk).update(
            download_count=F('download_count') + 1,
        )

    # Record a `resource_view` for an authenticated member on a successful
    # authorised serve (issue #773). Anonymous lead-magnet downloads return
    # 401 above and never reach here; deduped + defensive in the helper.
    if acting_user.is_authenticated:
        from analytics.activity import record_resource_view
        record_resource_view(
            acting_user,
            object_type='download',
            object_id=download.slug,
            title=download.title,
            target_url=download.get_absolute_url(),
        )

    logger.info(
        'download_delivery_succeeded slug=%s required_level=%s surface=%s',
        slug,
        download.required_level,
        surface,
    )
    return _no_store(HttpResponseRedirect(presigned_url))
