"""Host sync resolver and EventHost attachment helpers."""

from dataclasses import dataclass

from django.utils.text import slugify

from integrations.services.github_sync.common import logger


@dataclass(frozen=True)
class ResolvedEventHosts:
    """Resolved host assignment from source YAML.

    ``hosts`` is ``None`` when the source should leave existing EventHost rows
    untouched. An empty list is meaningful: it is the explicit ``hosts: []``
    clear operation.
    """

    hosts: list | None


def _resolve_hosts_for_event_yaml(data, rel_path, stats, *, legacy_name_field):
    """Resolve source host metadata into existing ``events.Host`` rows.

    Canonical ``hosts:`` values win over legacy name fields. Unknown host slugs
    are warnings and skipped. Malformed values are sync errors and leave the
    existing EventHost assignment untouched.
    """
    from events.models import Host

    if 'hosts' in data:
        raw = data.get('hosts')
        if not isinstance(raw, list):
            msg = (
                f'hosts: in {rel_path} must be a list of Host.slug strings, '
                f'got {type(raw).__name__}. Ignoring field.'
            )
            logger.warning(msg)
            stats['errors'].append({'file': rel_path, 'error': msg})
            return ResolvedEventHosts(None)

        if not raw:
            return ResolvedEventHosts([])

        slugs = []
        for index, value in enumerate(raw):
            if not isinstance(value, str):
                msg = (
                    f'hosts: item {index + 1} in {rel_path} must be a '
                    f'Host.slug string, got {type(value).__name__}. '
                    f'Ignoring field.'
                )
                logger.warning(msg)
                stats['errors'].append({'file': rel_path, 'error': msg})
                return ResolvedEventHosts(None)
            slug = value.strip()
            if not slug:
                msg = (
                    f'hosts: item {index + 1} in {rel_path} must be a '
                    'non-empty Host.slug string. Ignoring field.'
                )
                logger.warning(msg)
                stats['errors'].append({'file': rel_path, 'error': msg})
                return ResolvedEventHosts(None)
            slugs.append(slug)

        if len(set(slugs)) != len(slugs):
            msg = (
                f'hosts: in {rel_path} contains duplicate Host.slug values. '
                'Ignoring field.'
            )
            logger.warning(msg)
            stats['errors'].append({'file': rel_path, 'error': msg})
            return ResolvedEventHosts(None)

        found = {host.slug: host for host in Host.objects.filter(slug__in=slugs)}
        resolved = []
        for host_slug in slugs:
            host = found.get(host_slug)
            if host is None:
                logger.warning(
                    "Unknown host slug '%s' referenced from %s. Skipped.",
                    host_slug, rel_path,
                )
                continue
            resolved.append(host)

        if not resolved:
            return ResolvedEventHosts(None)
        return ResolvedEventHosts(resolved)

    legacy_name = str(data.get(legacy_name_field, '') or '').strip()
    if not legacy_name:
        return ResolvedEventHosts(None)

    legacy_slug = slugify(legacy_name)
    if not legacy_slug:
        return ResolvedEventHosts(None)

    host = Host.objects.filter(slug=legacy_slug).first()
    if host is None:
        logger.warning(
            "Legacy host name '%s' in %s did not match Host.slug '%s'. "
            'Skipped.',
            legacy_name, rel_path, legacy_slug,
        )
        return ResolvedEventHosts(None)
    return ResolvedEventHosts([host])


def _attach_hosts_to_event(event, resolved):
    """Replace ``EventHost`` rows for GitHub-origin events only."""
    from events.models import EventHost

    if event.origin != 'github':
        return
    if resolved is None or resolved.hosts is None:
        return

    new_ids = [host.pk for host in resolved.hosts]
    current_qs = EventHost.objects.filter(event=event).order_by('position')
    current_ids = list(current_qs.values_list('host_id', flat=True))

    if current_ids == new_ids:
        return

    current_qs.delete()
    EventHost.objects.bulk_create([
        EventHost(event=event, host=host, position=position)
        for position, host in enumerate(resolved.hosts)
    ])
