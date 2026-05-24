"""Async render task + per-content-type payload mapping (issue #788).

Called by django-q workers via the ``async_task`` enqueued by
:mod:`integrations.services.banner_generator.dispatch`. Builds the
template payload from the record (per the spec's mapping table), calls
the Lambda in S3-output mode, and persists the resulting CDN URL + a
sha256 title hash on the record via ``Model.objects.filter(pk=...)
.update(...)`` so we never race with the dispatcher's own ``save()``
calls or trigger ``post_save`` signals that the sync pipeline reacts to.

The task is fire-and-forget: any
:class:`integrations.services.banner_generator.BannerGeneratorError` is
logged at WARNING and swallowed so a failed render never blocks the
sync pipeline or the operator-initiated regenerate action.
"""

import logging
import re

from integrations.config import get_config
from integrations.services.banner_generator import (
    DEFAULT_FORMAT,
    DEFAULT_SIZE,
    DEFAULT_TEMPLATE,
    BannerGeneratorError,
    render_to_s3,
)
from integrations.services.banner_generator.dispatch import (
    SUPPORTED_CONTENT_TYPES,
    title_hash,
)

logger = logging.getLogger(__name__)

SUBTITLE_MAX_CHARS = 140
MAX_TAGS_IN_META = 3
META_TAG_JOINER = ' / '


# --------------------------------------------------------------------------
# Field helpers
# --------------------------------------------------------------------------


_MD_FENCE_RE = re.compile(r'```.*?```', re.DOTALL)
_MD_INLINE_CODE_RE = re.compile(r'`([^`]*)`')
_MD_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\([^)]+\)')
_MD_LINK_RE = re.compile(r'\[([^\]]+)\]\([^)]+\)')
_MD_BOLD_RE = re.compile(r'\*\*([^*]+)\*\*')
_MD_EMPH_RE = re.compile(r'(?<![*_])[*_]([^*_\n]+)[*_](?!\*|_)')
_MD_HEADING_RE = re.compile(r'^\s{0,3}#{1,6}\s+', re.MULTILINE)
_HTML_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'\s+')


def _strip_markdown(text):
    """Return ``text`` with the most common markdown noise removed.

    Used to derive the subtitle field for the OG card so a description
    with code fences, links, or images doesn't render as raw syntax on
    the rendered banner. Best-effort — we are aiming for a clean
    sentence, not a full HTML render.
    """
    if not text:
        return ''
    text = _MD_FENCE_RE.sub('', text)
    text = _MD_IMAGE_RE.sub(r'\1', text)
    text = _MD_LINK_RE.sub(r'\1', text)
    text = _MD_INLINE_CODE_RE.sub(r'\1', text)
    text = _MD_BOLD_RE.sub(r'\1', text)
    text = _MD_EMPH_RE.sub(r'\1', text)
    text = _MD_HEADING_RE.sub('', text)
    text = _HTML_TAG_RE.sub('', text)
    text = _WS_RE.sub(' ', text)
    return text.strip()


def _truncate(text, max_chars):
    """Strip markdown and clamp ``text`` to ``max_chars`` with an ellipsis.

    Returns the empty string when ``text`` is falsy. When ``text`` fits,
    returns it as-is (no trailing ellipsis). Truncation tries to break
    on a word boundary when one is available within the last 20% of the
    window — otherwise it falls back to a hard cut.
    """
    if not text:
        return ''
    cleaned = _strip_markdown(text)
    if len(cleaned) <= max_chars:
        return cleaned
    # Try to break on the last whitespace within the trailing 20% so we
    # don't truncate mid-word.
    cutoff = max_chars - 1  # leave room for the ellipsis
    window_start = max(0, int(cutoff * 0.8))
    last_space = cleaned.rfind(' ', window_start, cutoff)
    if last_space > 0:
        cutoff = last_space
    return cleaned[:cutoff].rstrip() + '…'


def _top_tags(tags, n=MAX_TAGS_IN_META):
    """Return the first ``n`` tags joined with ``" / "``.

    Accepts any iterable of strings; non-string entries are skipped. An
    empty input returns the empty string so the meta_secondary slot can
    be omitted from the rendered banner.
    """
    if not tags:
        return ''
    cleaned = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        value = tag.strip()
        if not value:
            continue
        cleaned.append(value)
        if len(cleaned) >= n:
            break
    return META_TAG_JOINER.join(cleaned)


# --------------------------------------------------------------------------
# Per-type payload builders
# --------------------------------------------------------------------------


def _course_meta_primary(course):
    """Return the Course ``meta_primary`` slot value.

    Free courses (``required_level == LEVEL_OPEN``) show "Free". Paid
    courses show the tier label (Basic / Main / Premium) so the card
    immediately communicates the access requirement.
    """
    from content.access import LEVEL_OPEN, LEVEL_TO_TIER_NAME

    level = getattr(course, 'required_level', LEVEL_OPEN) or LEVEL_OPEN
    return LEVEL_TO_TIER_NAME.get(level, 'Free')


def _course_kicker(course):
    """Return the Course ``kicker`` slot value.

    Courses with at least one ``Cohort`` get the cohort-based label so
    the OG card reads "Cohort-based course". Otherwise we surface the
    self-paced label.
    """
    has_cohort = False
    try:
        has_cohort = course.cohorts.exists()
    except Exception:  # noqa: BLE001 — best-effort; missing relation
        # ``cohorts`` reverse relation may not exist in some test
        # fixtures. Default to the self-paced label so the render never
        # fails on a missing manager.
        has_cohort = False
    return 'Cohort-based course' if has_cohort else 'Self-paced course'


_DOWNLOAD_FILE_TYPE_LABELS = {
    'pdf': 'PDF',
    'zip': 'ZIP',
    'slides': 'Slides',
    'notebook': 'Notebook',
    'csv': 'CSV',
    'other': 'File',
}


def _download_meta_primary(download):
    """Return the Download ``meta_primary`` slot value.

    Uses the ``file_type`` field's display label (e.g. "PDF",
    "Notebook"). Falls back to "File" when the field is empty or set to
    the catch-all ``other`` choice.
    """
    file_type = (getattr(download, 'file_type', '') or '').strip().lower()
    return _DOWNLOAD_FILE_TYPE_LABELS.get(file_type, 'File')


def build_payload(content_type, record):
    """Return the Lambda ``data`` payload for a content record.

    Mirrors the per-type mapping table in issue #788. Returns a dict
    with the seven OG-card slots (``kind``, ``kicker``, ``title``,
    ``subtitle``, ``meta_primary``, ``meta_secondary``, ``footer``).
    Empty strings are kept rather than ``None`` so the Lambda template
    can decide whether to render or hide each slot.
    """
    title = (getattr(record, 'title', '') or '').strip()
    description = getattr(record, 'description', '') or ''
    tags = getattr(record, 'tags', []) or []
    subtitle = _truncate(description, SUBTITLE_MAX_CHARS)
    meta_secondary = _top_tags(tags)

    if content_type == 'article':
        kicker = ''
        if tags and isinstance(tags[0], str):
            kicker = tags[0].strip().title()
        return {
            'kind': 'Article',
            'kicker': kicker,
            'title': title,
            'subtitle': subtitle,
            'meta_primary': 'Blog',
            'meta_secondary': meta_secondary,
            'footer': 'aishippinglabs.com/blog',
        }

    if content_type == 'course':
        slug = (getattr(record, 'slug', '') or '').strip()
        return {
            'kind': 'Course',
            'kicker': _course_kicker(record),
            'title': title,
            'subtitle': subtitle,
            'meta_primary': _course_meta_primary(record),
            'meta_secondary': meta_secondary,
            'footer': f'aishippinglabs.com/courses/{slug}',
        }

    if content_type == 'project':
        difficulty = (getattr(record, 'difficulty', '') or '').strip()
        if difficulty:
            kicker = f'{difficulty.title()} build'
        else:
            kicker = 'Project'
        first_tag = ''
        if tags and isinstance(tags[0], str):
            first_tag = tags[0].strip()
        return {
            'kind': 'Project',
            'kicker': kicker,
            'title': title,
            'subtitle': subtitle,
            'meta_primary': first_tag or 'Project',
            'meta_secondary': meta_secondary,
            'footer': 'AI Shipping Labs Projects',
        }

    if content_type == 'download':
        return {
            'kind': 'Resource',
            'kicker': 'Download',
            'title': title,
            'subtitle': subtitle,
            'meta_primary': _download_meta_primary(record),
            'meta_secondary': meta_secondary,
            'footer': 'AI Shipping Labs Downloads',
        }

    if content_type == 'workshop':
        return {
            'kind': 'Workshop',
            'kicker': 'Hands-on workshop',
            'title': title,
            'subtitle': subtitle,
            'meta_primary': 'Live online',
            'meta_secondary': meta_secondary,
            'footer': 'AI Shipping Labs Workshops',
        }

    raise ValueError(f'unsupported content_type: {content_type!r}')


# --------------------------------------------------------------------------
# Storage key + URL helpers
# --------------------------------------------------------------------------


def s3_key_for(content_type, content_id):
    """Return the stable S3 object key for a content record's banner.

    Always lowercase, always ``.png``, always under the ``banners/``
    prefix so the bucket policy can scope the Lambda's PutObject IAM
    grant tightly. ``content_id`` is the model's primary key (an int);
    we cast to str so call sites don't need to.
    """
    return f'banners/{content_type}/{content_id}.png'


def cdn_url_for(content_type, content_id):
    """Return the public CDN URL for a banner, or ``''`` when CDN unset.

    The URL is stable across re-renders (the S3 key is keyed on the
    model PK, not a content hash), so browsers can cache it
    aggressively. When ``CONTENT_CDN_BASE`` is not configured we return
    an empty string — auto-banner persistence then no-ops.
    """
    cdn_base = (get_config('CONTENT_CDN_BASE', '') or '').rstrip('/')
    if not cdn_base:
        return ''
    return f'{cdn_base}/{s3_key_for(content_type, content_id)}'


# --------------------------------------------------------------------------
# Worker entrypoint
# --------------------------------------------------------------------------


def _resolve_model(content_type):
    """Map a content_type slug to its model class (deferred import).

    Duplicates the table in ``dispatch`` so the worker doesn't pull in
    the dispatcher module (which depends on ``jobs.tasks.helpers``).
    """
    from content.models import Article, Course, Download, Project, Workshop

    return {
        'article': Article,
        'course': Course,
        'project': Project,
        'download': Download,
        'workshop': Workshop,
    }.get(content_type)


def render_banner_for_content(content_type, content_pk):
    """Worker task: render and persist a banner for one content record.

    Returns the ``auto_banner_url`` on success, ``None`` on any failure
    or short-circuit (unsupported type, missing record, missing CDN
    config, Lambda error). All exceptions from the Lambda client are
    caught and logged at WARNING — this is a best-effort fire-and-forget
    job that must never bubble up to the worker process.
    """
    if content_type not in SUPPORTED_CONTENT_TYPES:
        logger.warning(
            'render_banner_for_content: unsupported content_type=%r',
            content_type,
        )
        return None

    model = _resolve_model(content_type)
    if model is None:
        logger.warning(
            'render_banner_for_content: unknown content_type=%r',
            content_type,
        )
        return None

    record = model.objects.filter(pk=content_pk).first()
    if record is None:
        logger.warning(
            'render_banner_for_content: %s pk=%s not found',
            content_type, content_pk,
        )
        return None

    payload = build_payload(content_type, record)
    s3_key = s3_key_for(content_type, content_pk)

    try:
        render_to_s3(
            template=DEFAULT_TEMPLATE,
            size=DEFAULT_SIZE,
            fmt=DEFAULT_FORMAT,
            data=payload,
            s3_key=s3_key,
        )
    except BannerGeneratorError as exc:
        logger.warning(
            'render_banner_for_content: %s pk=%s failed: %s',
            content_type, content_pk, exc,
        )
        return None

    banner_url = cdn_url_for(content_type, content_pk)
    if not banner_url:
        # CONTENT_CDN_BASE missing — we still rendered to S3 but can't
        # persist a usable URL. Log so the operator notices the misconfig
        # and skip the DB update.
        logger.warning(
            'render_banner_for_content: %s pk=%s rendered but '
            'CONTENT_CDN_BASE is unset; skipping URL persistence',
            content_type, content_pk,
        )
        return None

    # Persist via ``.update()`` so we don't trigger ``save()``-time side
    # effects (Article.save re-renders markdown; Workshop.save re-runs
    # the gate-ordering invariant). This is also the safer pattern when
    # the dispatcher's own save() raced with the worker — ``update()``
    # is a single atomic UPDATE.
    new_hash = title_hash(getattr(record, 'title', '') or '')
    model.objects.filter(pk=content_pk).update(
        auto_banner_url=banner_url,
        auto_banner_title_hash=new_hash,
    )
    return banner_url
