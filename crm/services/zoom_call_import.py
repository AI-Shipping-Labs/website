"""Historical Zoom 1:1 summary import (issue #1951).

Parses the dated summaries in a local ``zoom-calls`` checkout
(``1x1/YYYY-MM-DD-<name-slug>-summary.md``) and records them as CRM
calls through ``POST /api/crm/calls`` -- the same staff-token write
path the booking app uses. The command runs from a laptop against the
production API; agents have no database access, so the authenticated
API is the only production write path.

Deliberately conservative: a file with a non-matching filename or a
missing/unparseable ``Date:`` line is skipped and reported, never
guessed. Emails are never fabricated: a participant name that no
alias maps and the user search cannot uniquely resolve stays
unresolved.

The HTTP client is injectable so Django tests can stub it (no
network): pass any object with ``search_users`` / ``member_calls`` /
``post_call`` to :func:`run_import`.
"""

import datetime as dt
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

#: The H1 is ``# 1x1: <Participant> and Alexey Grigorev``; the host is
#: never the imported participant.
HOST_NAME = 'Alexey Grigorev'

#: ``2026-04-23-jane-doe-summary.md`` -> stem without the ``.md``.
SUMMARY_FILENAME_RE = re.compile(
    r'^(?P<date>\d{4}-\d{2}-\d{2})-.+-summary$'
)

H1_RE = re.compile(r'^#\s+1x1:\s*(?P<rest>.+?)\s*$', re.MULTILINE)
DATE_LINE_RE = re.compile(
    r'^Date:\s*(?P<date>\d{4}-\d{2}-\d{2})\s*$', re.MULTILINE,
)
DURATION_LINE_RE = re.compile(
    r'^Duration:\s*(?P<minutes>\d+)\s+minutes?\s*$', re.MULTILINE,
)
#: Strips parenthetical aliases: ``Kalavati Singh (Kala)`` ->
#: ``Kalavati Singh``.
ALIAS_PARENS_RE = re.compile(r'\s*\([^)]*\)\s*')

#: Resolution outcome tokens.
MATCHED = 'matched'
UNRESOLVED = 'unresolved'

UNRESOLVED_NO_MATCH = 'no_user_match'
UNRESOLVED_AMBIGUOUS = 'ambiguous'

#: Staff-token env / .env keys, mirroring ``asl_cli.config`` (issue
#: #1951 keeps the operator contract identical; inlined instead of
#: imported so the command never depends on a dev-only package).
_STAFF_ENV_KEYS = ('ASL_API_TOKEN', 'API_SHIPPING_LABS_API_TOKEN')
DEFAULT_BASE_URL = 'https://aishippinglabs.com'


@dataclass(frozen=True)
class ParsedSummary:
    """One parseable dated summary file."""

    stem: str
    filename: str
    name: str
    date: dt.date
    duration_minutes: int | None
    body: str

    def occurred_at(self):
        """The file date at 12:00 UTC (no real times exist in the files)."""
        return dt.datetime(
            self.date.year, self.date.month, self.date.day, 12, 0, 0,
            tzinfo=dt.timezone.utc,
        )

    def payload(self):
        """The ``POST /api/crm/calls`` body for this summary."""
        return {
            'email': None,  # filled by the caller after resolution
            'occurred_at': self.occurred_at().isoformat(),
            'summary': self.body,
            'source': 'zoom',
            'source_ref': self.stem,
        }


@dataclass(frozen=True)
class SkippedFile:
    filename: str
    reason: str


@dataclass(frozen=True)
class Resolution:
    status: str
    email: str | None = None
    reason: str = ''


@dataclass
class ImportResult:
    """Per-run tallies and per-file rows for the end-of-run report."""

    would_create: int = 0
    would_update: int = 0
    created: int = 0
    updated: int = 0
    skipped_unresolved: int = 0
    rows: list = field(default_factory=list)
    skipped_files: list = field(default_factory=list)


def participant_from_h1(raw):
    """Extract the participant display name from the H1 remainder.

    ``Kalavati Singh (Kala) and Alexey Grigorev`` -> ``Kalavati Singh``.
    Parenthetical aliases are stripped; the host is never the
    participant. Returns ``None`` when only the host is named.
    """
    for candidate in raw.split(' and '):
        name = ALIAS_PARENS_RE.sub(' ', candidate).strip()
        if name and name.casefold() != HOST_NAME.casefold():
            return name
    return None


def parse_summary_text(filename, text):
    """Parse one summary file's text.

    Returns ``(ParsedSummary, None)`` on success or ``(None, reason)``
    when the file must be skipped and reported: a non-matching
    filename, a missing ``# 1x1:`` heading, a missing/unparseable
    ``Date:`` line, or a participant that cannot be extracted.
    """
    stem = filename.removesuffix('.md')
    if not SUMMARY_FILENAME_RE.fullmatch(stem):
        return None, (
            'filename does not match YYYY-MM-DD-<name-slug>-summary.md'
        )

    h1 = H1_RE.search(text)
    if h1 is None:
        return None, 'missing "# 1x1: <name> and Alexey Grigorev" heading'
    name = participant_from_h1(h1.group('rest'))
    if name is None:
        return None, 'no participant named besides the host in the H1'

    date_match = DATE_LINE_RE.search(text)
    if date_match is None:
        return None, 'missing or unparseable "Date: YYYY-MM-DD" line'
    try:
        parsed_date = dt.date.fromisoformat(date_match.group('date'))
    except ValueError:
        return None, 'missing or unparseable "Date: YYYY-MM-DD" line'

    duration_match = DURATION_LINE_RE.search(text)
    duration = (
        int(duration_match.group('minutes')) if duration_match else None
    )

    return ParsedSummary(
        stem=stem,
        filename=filename,
        name=name,
        date=parsed_date,
        duration_minutes=duration,
        body=text,
    ), None


def parse_summary_file(path):
    """Read and parse one summary file (see :func:`parse_summary_text`)."""
    text = Path(path).read_text(encoding='utf-8')
    return parse_summary_text(Path(path).name, text)


def discover_summary_files(root):
    """List ``<root>/1x1/*.md`` sorted; the caller parses each one.

    Non-recursive on purpose: plan-note subdirectories are out of scope
    (issue #1951) and a file that does not match the summary filename
    shape is skipped and reported by the parser, never silently dropped.
    """
    return sorted((Path(root) / '1x1').glob('*.md'))


def load_aliases(path):
    """Load the optional ``{"display name or file stem": "email"}`` map."""
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('aliases file must be a JSON object')
    return {
        str(key).strip().casefold(): str(value).strip()
        for key, value in data.items()
        if str(key).strip() and str(value).strip()
    }


def _alias_email(aliases, *, stem, name):
    for key in (stem, name):
        email = aliases.get(key.casefold())
        if email:
            return email
    return None


def _full_name(user_row):
    return (
        f"{user_row.get('first_name', '')} "
        f"{user_row.get('last_name', '')}"
    ).strip().casefold()


def resolve_email(client, aliases, parsed):
    """Resolve one parsed summary to a member email.

    Order: the alias map (by file stem, then by display name) wins;
    then ``GET /api/users?q=<name>`` -- a unique hit resolves, several
    hits collapse only when exactly one matches the parsed full name,
    otherwise the name is ambiguous and stays unresolved. No hit is
    also unresolved. Emails are never guessed.
    """
    aliased = _alias_email(aliases, stem=parsed.stem, name=parsed.name)
    if aliased:
        return Resolution(MATCHED, email=aliased)

    hits = client.search_users(parsed.name)
    if len(hits) == 1:
        return Resolution(MATCHED, email=hits[0]['email'])
    if not hits:
        return Resolution(UNRESOLVED, reason=UNRESOLVED_NO_MATCH)

    exact = [row for row in hits if _full_name(row) == parsed.name.casefold()]
    if len(exact) == 1:
        return Resolution(MATCHED, email=exact[0]['email'])
    emails = ', '.join(sorted(row['email'] for row in hits))
    return Resolution(UNRESOLVED, reason=f'{UNRESOLVED_AMBIGUOUS}: {emails}')


def _zoom_refs_for_email(client, email, cache):
    """Source refs of the member's existing ``zoom`` calls (report only)."""
    if email not in cache:
        members = client.member_calls(email)
        refs = set()
        for member in members:
            for call in member.get('calls', []):
                if call.get('source') == 'zoom':
                    refs.add(call.get('source_ref'))
        cache[email] = refs
    return cache[email]


def run_import(
    *,
    root,
    client,
    aliases=None,
    apply=False,
    parser=None,
):
    """Parse, resolve, and (optionally) record every dated summary.

    Args:
        root: Local ``zoom-calls`` checkout (reads ``<root>/1x1/*.md``).
        client: Injectable API client (``search_users``,
            ``member_calls``, ``post_call``).
        aliases: Optional normalized alias map (see
            :func:`load_aliases`).
        apply: When False (dry-run) nothing is written; every resolved
            file is only reported as would-create / would-update.
        parser: Optional ``path -> (ParsedSummary|None, reason)``
            override (tests inject malformed-file fixtures through it).

    Returns:
        :class:`ImportResult` with per-file rows and the end counts.
    """
    aliases = aliases or {}
    parse = parser or parse_summary_file
    result = ImportResult()
    refs_cache = {}

    for path in discover_summary_files(root):
        parsed, reason = parse(path)
        if parsed is None:
            result.skipped_files.append(SkippedFile(path.name, reason))
            continue

        resolution = resolve_email(client, aliases, parsed)
        if resolution.status != MATCHED:
            result.skipped_unresolved += 1
            result.rows.append({
                'parsed': parsed,
                'resolution': resolution,
                'disposition': 'skipped',
            })
            continue

        existing = _zoom_refs_for_email(client, resolution.email, refs_cache)
        if apply:
            payload = parsed.payload()
            payload['email'] = resolution.email
            client.post_call(payload)
            disposition = 'created'
            if parsed.stem in existing:
                disposition = 'updated'
                result.updated += 1
            else:
                result.created += 1
        else:
            disposition = (
                'would_update' if parsed.stem in existing
                else 'would_create'
            )
            if disposition == 'would_update':
                result.would_update += 1
            else:
                result.would_create += 1
        result.rows.append({
            'parsed': parsed,
            'resolution': resolution,
            'disposition': disposition,
        })

    return result


def resolve_staff_token(env=None):
    """Staff API token from the env, else the repo ``.env`` file.

    Mirrors ``asl_cli.config`` so operators configure the import the
    same way as the CLI (``ASL_API_TOKEN`` or
    ``API_SHIPPING_LABS_API_TOKEN``). Raises ``RuntimeError`` when
    nothing resolves -- the token is never printed.
    """
    environ = os.environ if env is None else env
    for key in _STAFF_ENV_KEYS:
        value = (environ.get(key) or '').strip()
        if value:
            return value
    value = _read_env_file_value(_STAFF_ENV_KEYS)
    if value:
        return value
    raise RuntimeError(
        'Staff API token not found; set ASL_API_TOKEN in the environment '
        'or the repo .env file.'
    )


def resolve_base_url(env=None):
    """API base URL (``ASL_BASE_URL``, default the production site)."""
    environ = os.environ if env is None else env
    return (environ.get('ASL_BASE_URL') or DEFAULT_BASE_URL).rstrip('/')


def _read_env_file_value(keys):
    """First matching key in a ``.env`` found upward from cwd."""
    cwd = Path.cwd()
    for directory in [cwd, *cwd.parents]:
        candidate = directory / '.env'
        if not candidate.is_file():
            continue
        try:
            for line in candidate.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                env_key, _, raw_value = line.partition('=')
                if env_key.strip() in keys:
                    value = raw_value.strip().strip('"').strip("'")
                    if value:
                        return value
        except OSError:
            continue
    return None


class CrmApiClient:
    """Minimal HTTP client over the staff API (injectable in tests).

    Wraps the three endpoints the import needs: ``GET /api/users`` for
    name resolution, ``GET /api/crm/export?email=`` for the
    create-vs-update classification, and ``POST /api/crm/calls`` for
    the writes. The token is never logged or included in errors.
    """

    def __init__(self, base_url, token, timeout=30, post_sleep=0.0):
        self.base_url = base_url.rstrip('/')
        self._token = token
        self.timeout = timeout
        #: Small pause between sequential POSTs; keeps 27 writes far
        #: inside the 120/min token throttle.
        self.post_sleep = post_sleep

    def _headers(self):
        return {
            'Authorization': f'Token {self._token}',
            'Content-Type': 'application/json',
        }

    def _request(self, method, path, json_body=None):
        import requests

        response = requests.request(
            method,
            f'{self.base_url}{path}',
            headers=self._headers(),
            json=json_body,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f'{method} {path} failed with HTTP '
                f'{response.status_code}',
            )
        return response

    def search_users(self, query, limit=100):
        response = self._request(
            'GET',
            f'/api/users?q={_urlquote(query)}&limit={limit}',
        )
        return response.json().get('users', [])

    def member_calls(self, email):
        response = self._request(
            'GET',
            f'/api/crm/export?scope=all&email={_urlquote(email)}',
        )
        return response.json().get('members', [])

    def post_call(self, payload):
        if self.post_sleep:
            import time

            time.sleep(self.post_sleep)
        response = self._request('POST', '/api/crm/calls', json_body=payload)
        return response.json()


def _urlquote(value):
    from urllib.parse import quote

    return quote(str(value), safe='')
