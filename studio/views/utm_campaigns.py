"""Studio views for managing UTM campaigns and tracked links."""

import csv
import io
import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from django.contrib import messages
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from integrations.models import UtmCampaign, UtmCampaignLink
from studio.decorators import staff_required

SLUG_RE = re.compile(r'^[a-z0-9_]+$')


def _validate_slug(value):
    """Return (valid, cleaned_value_or_none)."""
    if not value:
        return False
    return bool(SLUG_RE.match(value))


@staff_required
def utm_campaign_list(request):
    """List UTM campaigns (active by default, or archived via ?archived=1)."""
    show_archived = request.GET.get('archived') == '1'
    campaigns = UtmCampaign.objects.filter(is_archived=show_archived)
    return render(request, 'studio/utm_campaigns/list.html', {
        'campaigns': campaigns,
        'show_archived': show_archived,
    })


@staff_required
def utm_campaign_create(request):
    """Create a new UTM campaign."""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        slug = request.POST.get('slug', '').strip()
        default_utm_source = request.POST.get('default_utm_source', '').strip()
        default_utm_medium = request.POST.get('default_utm_medium', '').strip()
        notes = request.POST.get('notes', '').strip()

        form_data = {
            'name': name,
            'slug': slug,
            'default_utm_source': default_utm_source,
            'default_utm_medium': default_utm_medium,
            'notes': notes,
        }

        if not name or not slug or not default_utm_source or not default_utm_medium:
            messages.error(request, 'Name, slug, default source and default medium are required.')
            return render(request, 'studio/utm_campaigns/form.html', {
                'campaign': None,
                'form_data': form_data,
                'form_action': 'create',
            })

        if not _validate_slug(slug):
            messages.error(request, 'Slug must contain only lowercase letters, digits, and underscores.')
            return render(request, 'studio/utm_campaigns/form.html', {
                'campaign': None,
                'form_data': form_data,
                'form_action': 'create',
            })

        if UtmCampaign.objects.filter(slug=slug).exists():
            messages.error(request, f'A campaign with slug "{slug}" already exists.')
            return render(request, 'studio/utm_campaigns/form.html', {
                'campaign': None,
                'form_data': form_data,
                'form_action': 'create',
            })

        campaign = UtmCampaign.objects.create(
            name=name,
            slug=slug,
            default_utm_source=default_utm_source,
            default_utm_medium=default_utm_medium,
            notes=notes,
            created_by=request.user,
        )
        messages.success(request, f'Campaign "{campaign.name}" created.')
        return redirect('studio_utm_campaign_detail', campaign_id=campaign.pk)

    return render(request, 'studio/utm_campaigns/form.html', {
        'campaign': None,
        'form_data': {},
        'form_action': 'create',
    })


@staff_required
def utm_campaign_detail(request, campaign_id):
    """Show campaign metadata and its links."""
    campaign = get_object_or_404(UtmCampaign, pk=campaign_id)
    links = campaign.links.filter(is_archived=False)
    archived_links = campaign.links.filter(is_archived=True)
    return render(request, 'studio/utm_campaigns/detail.html', {
        'campaign': campaign,
        'links': links,
        'archived_links': archived_links,
    })


@staff_required
def utm_campaign_edit(request, campaign_id):
    """Edit an existing campaign. Slug is locked when links exist."""
    campaign = get_object_or_404(UtmCampaign, pk=campaign_id)
    slug_locked = campaign.has_links()

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        default_utm_source = request.POST.get('default_utm_source', '').strip()
        default_utm_medium = request.POST.get('default_utm_medium', '').strip()
        notes = request.POST.get('notes', '').strip()
        posted_slug = request.POST.get('slug', '').strip()

        if not name or not default_utm_source or not default_utm_medium:
            messages.error(request, 'Name, default source, and default medium are required.')
            return render(request, 'studio/utm_campaigns/form.html', {
                'campaign': campaign,
                'form_data': {
                    'name': name,
                    'slug': campaign.slug if slug_locked else posted_slug,
                    'default_utm_source': default_utm_source,
                    'default_utm_medium': default_utm_medium,
                    'notes': notes,
                },
                'form_action': 'edit',
                'slug_locked': slug_locked,
            })

        if slug_locked:
            # ignore any posted slug; keep current
            new_slug = campaign.slug
        else:
            new_slug = posted_slug or campaign.slug
            if not _validate_slug(new_slug):
                messages.error(request, 'Slug must contain only lowercase letters, digits, and underscores.')
                return render(request, 'studio/utm_campaigns/form.html', {
                    'campaign': campaign,
                    'form_data': {
                        'name': name,
                        'slug': new_slug,
                        'default_utm_source': default_utm_source,
                        'default_utm_medium': default_utm_medium,
                        'notes': notes,
                    },
                    'form_action': 'edit',
                    'slug_locked': slug_locked,
                })
            if new_slug != campaign.slug and UtmCampaign.objects.filter(slug=new_slug).exclude(pk=campaign.pk).exists():
                messages.error(request, f'A campaign with slug "{new_slug}" already exists.')
                return render(request, 'studio/utm_campaigns/form.html', {
                    'campaign': campaign,
                    'form_data': {
                        'name': name,
                        'slug': new_slug,
                        'default_utm_source': default_utm_source,
                        'default_utm_medium': default_utm_medium,
                        'notes': notes,
                    },
                    'form_action': 'edit',
                    'slug_locked': slug_locked,
                })

        campaign.name = name
        campaign.slug = new_slug
        campaign.default_utm_source = default_utm_source
        campaign.default_utm_medium = default_utm_medium
        campaign.notes = notes
        campaign.save()
        messages.success(request, f'Campaign "{campaign.name}" updated.')
        return redirect('studio_utm_campaign_detail', campaign_id=campaign.pk)

    return render(request, 'studio/utm_campaigns/form.html', {
        'campaign': campaign,
        'form_data': {
            'name': campaign.name,
            'slug': campaign.slug,
            'default_utm_source': campaign.default_utm_source,
            'default_utm_medium': campaign.default_utm_medium,
            'notes': campaign.notes,
        },
        'form_action': 'edit',
        'slug_locked': slug_locked,
    })


@staff_required
@require_POST
def utm_campaign_archive(request, campaign_id):
    campaign = get_object_or_404(UtmCampaign, pk=campaign_id)
    campaign.is_archived = True
    campaign.save(update_fields=['is_archived', 'updated_at'])
    messages.success(request, f'Campaign "{campaign.name}" archived.')
    return redirect('studio_utm_campaign_list')


@staff_required
@require_POST
def utm_campaign_unarchive(request, campaign_id):
    campaign = get_object_or_404(UtmCampaign, pk=campaign_id)
    campaign.is_archived = False
    campaign.save(update_fields=['is_archived', 'updated_at'])
    messages.success(request, f'Campaign "{campaign.name}" unarchived.')
    return redirect('studio_utm_campaign_detail', campaign_id=campaign.pk)


@staff_required
@require_POST
def utm_link_create(request, campaign_id):
    """Create a new tracked link under a campaign."""
    campaign = get_object_or_404(UtmCampaign, pk=campaign_id)
    utm_content = request.POST.get('utm_content', '').strip()
    destination = request.POST.get('destination', '').strip()
    label = request.POST.get('label', '').strip()
    utm_term = request.POST.get('utm_term', '').strip()
    utm_source = request.POST.get('utm_source', '').strip()
    utm_medium = request.POST.get('utm_medium', '').strip()

    if not utm_content or not destination:
        messages.error(request, 'utm_content and destination are required.')
        return redirect('studio_utm_campaign_detail', campaign_id=campaign.pk)

    if not _validate_slug(utm_content):
        messages.error(request, 'utm_content must contain only lowercase letters, digits, and underscores.')
        return redirect('studio_utm_campaign_detail', campaign_id=campaign.pk)

    if campaign.links.filter(utm_content=utm_content).exists():
        messages.error(request, f'A link with utm_content "{utm_content}" already exists for this campaign.')
        return redirect('studio_utm_campaign_detail', campaign_id=campaign.pk)

    try:
        UtmCampaignLink.objects.create(
            campaign=campaign,
            utm_content=utm_content,
            destination=destination,
            label=label,
            utm_term=utm_term,
            utm_source=utm_source,
            utm_medium=utm_medium,
            created_by=request.user,
        )
    except IntegrityError:
        messages.error(request, f'A link with utm_content "{utm_content}" already exists for this campaign.')
        return redirect('studio_utm_campaign_detail', campaign_id=campaign.pk)

    messages.success(request, f'Link "{utm_content}" created.')
    return redirect('studio_utm_campaign_detail', campaign_id=campaign.pk)


@staff_required
def utm_link_edit(request, campaign_id, link_id):
    """Edit an existing tracked link."""
    campaign = get_object_or_404(UtmCampaign, pk=campaign_id)
    link = get_object_or_404(UtmCampaignLink, pk=link_id, campaign=campaign)

    if request.method == 'POST':
        utm_content = request.POST.get('utm_content', '').strip()
        destination = request.POST.get('destination', '').strip()
        label = request.POST.get('label', '').strip()
        utm_term = request.POST.get('utm_term', '').strip()
        utm_source = request.POST.get('utm_source', '').strip()
        utm_medium = request.POST.get('utm_medium', '').strip()

        form_data = {
            'utm_content': utm_content,
            'destination': destination,
            'label': label,
            'utm_term': utm_term,
            'utm_source': utm_source,
            'utm_medium': utm_medium,
        }

        if not utm_content or not destination:
            messages.error(request, 'utm_content and destination are required.')
            return render(request, 'studio/utm_campaigns/link_form.html', {
                'campaign': campaign, 'link': link, 'form_data': form_data,
            })

        if not _validate_slug(utm_content):
            messages.error(request, 'utm_content must contain only lowercase letters, digits, and underscores.')
            return render(request, 'studio/utm_campaigns/link_form.html', {
                'campaign': campaign, 'link': link, 'form_data': form_data,
            })

        if utm_content != link.utm_content and campaign.links.filter(utm_content=utm_content).exists():
            messages.error(request, f'A link with utm_content "{utm_content}" already exists for this campaign.')
            return render(request, 'studio/utm_campaigns/link_form.html', {
                'campaign': campaign, 'link': link, 'form_data': form_data,
            })

        link.utm_content = utm_content
        link.destination = destination
        link.label = label
        link.utm_term = utm_term
        link.utm_source = utm_source
        link.utm_medium = utm_medium
        link.save()
        messages.success(request, f'Link "{utm_content}" updated.')
        return redirect('studio_utm_campaign_detail', campaign_id=campaign.pk)

    return render(request, 'studio/utm_campaigns/link_form.html', {
        'campaign': campaign,
        'link': link,
        'form_data': {
            'utm_content': link.utm_content,
            'destination': link.destination,
            'label': link.label,
            'utm_term': link.utm_term,
            'utm_source': link.utm_source,
            'utm_medium': link.utm_medium,
        },
    })


@staff_required
@require_POST
def utm_link_archive(request, campaign_id, link_id):
    campaign = get_object_or_404(UtmCampaign, pk=campaign_id)
    link = get_object_or_404(UtmCampaignLink, pk=link_id, campaign=campaign)
    link.is_archived = True
    link.save(update_fields=['is_archived', 'updated_at'])
    messages.success(request, f'Link "{link.utm_content}" archived.')
    return redirect('studio_utm_campaign_detail', campaign_id=campaign.pk)


def _parse_utm_url(raw_url):
    """Parse a URL string and extract UTM params plus a stripped destination.

    Returns dict with keys:
        ok (bool)
        missing (list[str])  — missing UTM param names
        utm_source, utm_medium, utm_campaign, utm_content, utm_term
        destination (str)    — URL without any utm_* params but with fragment preserved
    """
    parsed = urlparse(raw_url)
    if not parsed.scheme or not parsed.netloc:
        return {'ok': False, 'missing': ['invalid_url'], 'raw': raw_url}

    qs = parse_qs(parsed.query, keep_blank_values=False)
    get_one = lambda k: qs.get(k, [''])[0].strip()

    utm_source = get_one('utm_source')
    utm_medium = get_one('utm_medium')
    utm_campaign = get_one('utm_campaign')
    utm_content = get_one('utm_content')
    utm_term = get_one('utm_term')

    missing = []
    if not utm_source:
        missing.append('utm_source')
    if not utm_medium:
        missing.append('utm_medium')
    if not utm_campaign:
        missing.append('utm_campaign')
    if not utm_content:
        missing.append('utm_content')

    if missing:
        return {'ok': False, 'missing': missing, 'raw': raw_url}

    # rebuild destination minus utm params
    non_utm = [(k, v) for k, vs in qs.items() for v in vs if not k.startswith('utm_')]
    new_query = urlencode(non_utm)
    destination = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment,
    ))

    return {
        'ok': True,
        'missing': [],
        'utm_source': utm_source,
        'utm_medium': utm_medium,
        'utm_campaign': utm_campaign,
        'utm_content': utm_content,
        'utm_term': utm_term,
        'destination': destination,
        'raw': raw_url,
    }


@staff_required
def utm_campaign_import(request):
    """Paste-box / CSV importer. Idempotent on (campaign slug, utm_content)."""
    if request.method == 'POST':
        textarea_urls = request.POST.get('urls', '') or ''
        lines = [ln.strip() for ln in textarea_urls.splitlines() if ln.strip()]

        csv_file = request.FILES.get('csv_file')
        if csv_file is not None:
            try:
                raw = csv_file.read().decode('utf-8')
            except UnicodeDecodeError:
                raw = csv_file.read().decode('latin-1', errors='ignore') if hasattr(csv_file, 'read') else ''
            reader = csv.DictReader(io.StringIO(raw))
            for row in reader:
                value = (row.get('url') or '').strip()
                if value:
                    lines.append(value)

        campaigns_created = 0
        campaigns_matched = 0
        links_created = 0
        links_skipped = 0
        errors = []  # list of (raw_url, reason)

        seen_campaign_slugs = set()

        for raw_url in lines:
            parsed = _parse_utm_url(raw_url)
            if not parsed['ok']:
                if parsed['missing'] == ['invalid_url']:
                    errors.append((raw_url, 'URL is not a valid http(s) URL'))
                else:
                    reason = 'Missing UTM parameter(s): ' + ', '.join(parsed['missing'])
                    errors.append((raw_url, reason))
                continue

            slug = parsed['utm_campaign']
            if not _validate_slug(slug):
                errors.append((raw_url, f'Campaign slug "{slug}" is not valid (lowercase letters, digits, underscores only)'))
                continue
            if not _validate_slug(parsed['utm_content']):
                errors.append((raw_url, f'utm_content "{parsed["utm_content"]}" is not valid (lowercase letters, digits, underscores only)'))
                continue

            campaign, created = UtmCampaign.objects.get_or_create(
                slug=slug,
                defaults={
                    'name': slug,
                    'default_utm_source': parsed['utm_source'],
                    'default_utm_medium': parsed['utm_medium'],
                    'created_by': request.user if request.user.is_authenticated else None,
                },
            )
            if created:
                campaigns_created += 1
                seen_campaign_slugs.add(slug)
            else:
                if slug not in seen_campaign_slugs:
                    campaigns_matched += 1
                    seen_campaign_slugs.add(slug)

            link_defaults = {
                'destination': parsed['destination'],
                'utm_term': parsed['utm_term'],
                # only store source/medium as overrides when different from campaign defaults
                'utm_source': parsed['utm_source'] if parsed['utm_source'] != campaign.default_utm_source else '',
                'utm_medium': parsed['utm_medium'] if parsed['utm_medium'] != campaign.default_utm_medium else '',
                'created_by': request.user if request.user.is_authenticated else None,
            }
            link, link_created = UtmCampaignLink.objects.get_or_create(
                campaign=campaign,
                utm_content=parsed['utm_content'],
                defaults=link_defaults,
            )
            if link_created:
                links_created += 1
            else:
                links_skipped += 1

        return render(request, 'studio/utm_campaigns/import_result.html', {
            'campaigns_created': campaigns_created,
            'campaigns_matched': campaigns_matched,
            'links_created': links_created,
            'links_skipped': links_skipped,
            'errors': errors,
        })

    return render(request, 'studio/utm_campaigns/import.html', {})
