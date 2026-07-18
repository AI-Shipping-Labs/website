"""Studio views for the bulk-contact CSV importer (issue #356).

Lives in its own file (separate from ``studio/views/users.py``) so it can land
alongside the contact-tag detail-page work without merge conflicts.

Three steps:

1. ``GET /studio/users/import/`` -- show the upload form.
2. ``POST /studio/users/import/`` -- parse the upload, stash it in the
   session, render the confirm page (header + first 5 rows + email-column
   dropdown + tag input + tier dropdown).
3. ``POST /studio/users/import/confirm`` -- read the stash, run
   ``run_import``, drop the stash, render the result page (counts +
   warnings table).

The session stash is a single key holding the decoded CSV text plus the
inferred header. If the operator hits the confirm URL without a stash (e.g.
they refreshed past the upload page), we redirect back to step 1 with a
flash.
"""

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render

from accounts.utils.tags import normalize_tag
from payments.models import Tier
from studio.decorators import staff_required
from studio.services.contacts_import import (
    MAX_UPLOAD_BYTES,
    NO_TIER_CHANGE,
    all_tiers_for_dropdown,
    decode_csv_bytes,
    default_email_column,
    is_csv_upload,
    parse_csv,
    plan_csv_import,
    run_import,
)

# Single session key holding the upload payload between step 1 and step 3.
# Cleared after a successful confirm so refreshing the result page leaves
# nothing behind.
SESSION_KEY = 'studio_user_import_payload'

# Preview slice rendered on the confirm page.
PREVIEW_ROWS = 5


def _count_phrase(count, singular, plural=None):
    plural = plural or f'{singular}s'
    return f'{count} {singular if count == 1 else plural}'


def _outcome_summary(plan):
    return (
        f'{_count_phrase(plan.created, "new user")} will be created, '
        f'{_count_phrase(plan.updated, "existing user")} will be updated, '
        f'{_count_phrase(plan.total_skipped, "row")} will be skipped '
        f'({_count_phrase(plan.malformed, "invalid email")}, '
        f'{_count_phrase(plan.skipped, "duplicate")}).'
    )


def _preview_rows(parsed):
    return [
        [row.get(col, '') for col in parsed.header]
        for row in parsed.rows[:PREVIEW_ROWS]
    ]


def _store_preview(request, stash, plan, *, email_column):
    updated_stash = dict(stash)
    updated_stash['preview'] = plan.session_metadata(email_column=email_column)
    request.session[SESSION_KEY] = updated_stash
    return updated_stash


def _confirm_context(
    parsed,
    stash,
    *,
    email_column,
    plan,
    tag='',
    tier_value=NO_TIER_CHANGE,
    error='',
):
    return {
        'header': parsed.header,
        'preview_rows': _preview_rows(parsed),
        'default_email_column': email_column,
        'tiers': all_tiers_for_dropdown(),
        'no_tier_sentinel': NO_TIER_CHANGE,
        'filename': stash.get('filename', ''),
        'total_rows': len(parsed.rows),
        'header_subtitle': (
            f"Reviewing {stash.get('filename', '')} "
            f"({_count_phrase(len(parsed.rows), 'data row')}). Pick the email "
            "column, optionally set a tag and tier, then import."
        ),
        'preview': plan.aggregate(),
        'outcome_summary': _outcome_summary(plan),
        'selected_tag': tag,
        'selected_tier_value': str(tier_value),
        'error': error,
    }


def _render_confirm(
    request,
    parsed,
    stash,
    *,
    email_column,
    plan,
    tag='',
    tier_value=NO_TIER_CHANGE,
    error='',
    status=200,
):
    return render(
        request,
        'studio/users/import_confirm.html',
        _confirm_context(
            parsed,
            stash,
            email_column=email_column,
            plan=plan,
            tag=tag,
            tier_value=tier_value,
            error=error,
        ),
        status=status,
    )


def _render_upload_form(request, *, error='', status=200):
    return render(
        request,
        'studio/users/import.html',
        {'error': error},
        status=status,
    )


@staff_required
def user_import(request):
    """Step 1+2: GET shows upload form; POST stashes parsed CSV + shows preview."""
    if request.method != 'POST':
        return _render_upload_form(request)

    uploaded_file = request.FILES.get('csv_file')
    if uploaded_file is None:
        return _render_upload_form(
            request,
            error='Please choose a CSV file to upload.',
            status=400,
        )

    if not is_csv_upload(uploaded_file):
        return _render_upload_form(
            request,
            error='Only .csv files are supported.',
            status=400,
        )

    if uploaded_file.size > MAX_UPLOAD_BYTES:
        return _render_upload_form(
            request,
            error='File too large (max 5 MB).',
            status=400,
        )

    raw_bytes = uploaded_file.read()
    raw_text = decode_csv_bytes(raw_bytes)

    parsed, error = parse_csv(raw_text)
    if error is not None:
        return _render_upload_form(request, error=error, status=400)

    # Stash the raw text + header. The confirm step re-parses on POST so a
    # tampered session can't sneak rows past validation.
    stash = {
        'raw_text': parsed.raw_text,
        'header': parsed.header,
        'filename': uploaded_file.name,
    }

    default_index = default_email_column(parsed.header)
    email_column = parsed.header[default_index]
    plan = plan_csv_import(parsed, email_column=email_column)
    stash = _store_preview(
        request,
        stash,
        plan,
        email_column=email_column,
    )

    return _render_confirm(
        request,
        parsed,
        stash,
        email_column=email_column,
        plan=plan,
    )


@staff_required
def user_import_preview(request):
    """Recompute aggregate outcomes for a selected stashed CSV column."""
    if request.method != 'POST':
        return JsonResponse(
            {'error': 'Method not allowed.', 'code': 'method_not_allowed'},
            status=405,
        )

    unexpected = set(request.POST) - {'email_column', 'csrfmiddlewaretoken'}
    if unexpected:
        return JsonResponse(
            {
                'error': 'Only email_column may be submitted.',
                'code': 'invalid_preview_fields',
            },
            status=400,
        )

    stash = request.session.get(SESSION_KEY)
    if not stash:
        return JsonResponse(
            {
                'error': 'Upload session expired. Please choose a CSV file again.',
                'code': 'upload_session_expired',
            },
            status=400,
        )

    parsed, error = parse_csv(stash.get('raw_text', ''))
    if error is not None or parsed is None:
        return JsonResponse(
            {
                'error': error or 'Could not re-parse the uploaded CSV.',
                'code': 'invalid_upload_stash',
            },
            status=400,
        )

    email_column = (request.POST.get('email_column') or '').strip()
    if not email_column or email_column not in parsed.header:
        return JsonResponse(
            {
                'error': 'Pick which column holds the email address.',
                'code': 'invalid_email_column',
            },
            status=400,
        )

    plan = plan_csv_import(parsed, email_column=email_column)
    _store_preview(request, stash, plan, email_column=email_column)
    return JsonResponse({'email_column': email_column, **plan.aggregate()})


@staff_required
def user_import_confirm(request):
    """Step 3: pull stash, validate operator inputs, run import, render result."""
    if request.method != 'POST':
        return redirect('studio_user_import')

    stash = request.session.get(SESSION_KEY)
    if not stash:
        messages.error(
            request,
            'Upload session expired. Please choose a CSV file again.',
        )
        return redirect('studio_user_import')

    raw_text = stash.get('raw_text', '')
    parsed, error = parse_csv(raw_text)
    if error is not None or parsed is None:
        messages.error(request, error or 'Could not re-parse the uploaded CSV.')
        return redirect('studio_user_import')

    email_column = (request.POST.get('email_column') or '').strip()
    if not email_column or email_column not in parsed.header:
        reviewed_column = (stash.get('preview') or {}).get('email_column')
        if reviewed_column not in parsed.header:
            reviewed_column = parsed.header[default_email_column(parsed.header)]
        plan = plan_csv_import(parsed, email_column=reviewed_column)
        return _render_confirm(
            request,
            parsed,
            stash,
            email_column=reviewed_column,
            plan=plan,
            tag=(request.POST.get('tag') or '').strip(),
            tier_value=request.POST.get('tier_id') or NO_TIER_CHANGE,
            error='Pick which column holds the email address.',
            status=400,
        )

    plan = plan_csv_import(parsed, email_column=email_column)
    current_metadata = plan.session_metadata(email_column=email_column)

    raw_tag = (request.POST.get('tag') or '').strip()
    normalized_tag = normalize_tag(raw_tag) if raw_tag else ''
    # If the operator typed something but it normalized to nothing, surface
    # that rather than silently dropping the tag.
    if raw_tag and not normalized_tag:
        stash = _store_preview(
            request, stash, plan, email_column=email_column,
        )
        return _render_confirm(
            request,
            parsed,
            stash,
            email_column=email_column,
            plan=plan,
            tag=raw_tag,
            tier_value=request.POST.get('tier_id') or NO_TIER_CHANGE,
            error=(
                'Tag normalized to an empty string. Use letters, digits, or '
                'hyphens.'
            ),
            status=400,
        )

    tier_value = (request.POST.get('tier_id') or NO_TIER_CHANGE).strip()
    tier = None
    if tier_value and tier_value != NO_TIER_CHANGE:
        try:
            tier = Tier.objects.get(pk=tier_value)
        except (Tier.DoesNotExist, ValueError):
            stash = _store_preview(
                request, stash, plan, email_column=email_column,
            )
            return _render_confirm(
                request,
                parsed,
                stash,
                email_column=email_column,
                plan=plan,
                tag=raw_tag,
                tier_value=tier_value,
                error='Invalid tier selected.',
                status=400,
            )

    if not plan.can_apply:
        stash = _store_preview(
            request, stash, plan, email_column=email_column,
        )
        return _render_confirm(
            request,
            parsed,
            stash,
            email_column=email_column,
            plan=plan,
            tag=raw_tag,
            tier_value=tier_value,
            status=400,
        )

    if stash.get('preview') != current_metadata:
        stash = _store_preview(
            request, stash, plan, email_column=email_column,
        )
        return _render_confirm(
            request,
            parsed,
            stash,
            email_column=email_column,
            plan=plan,
            tag=raw_tag,
            tier_value=tier_value,
            error=(
                'Import outcome changed. Review the updated counts and '
                'confirm again.'
            ),
            status=409,
        )

    result = run_import(
        parsed,
        email_column=email_column,
        tag=normalized_tag,
        tier=tier,
        granted_by=request.user,
        plan=plan,
    )

    # Drop the stash now that the import has succeeded; refreshing the result
    # page must not re-import.
    request.session.pop(SESSION_KEY, None)

    return render(request, 'studio/users/import_result.html', {
        'created': result.created,
        'updated': result.updated,
        'skipped': result.skipped,
        'malformed': result.malformed,
        'warnings': result.warnings,
        'tag': normalized_tag,
        'tier_name': tier.name if tier else '',
    })
