#!/usr/bin/env python3
"""Import historical 1:1 calls into the CRM via the staff API (issue #1951).

Reads a local ``zoom-calls`` checkout: every ``1x1/*-summary.md`` becomes
a ``zoom``-source Call, and dated call-note sections in
``1x1/plans/*.md`` become ``telegram``-source Calls. Writes go through
``POST /api/crm/calls`` strictly sequentially (the ``(source,
source_ref)`` upsert makes re-runs idempotent); dry-run is the default
and writes nothing.

Name to email resolution: ``--map NAME=EMAIL`` entries win, otherwise a
single ``GET /api/users?q=<name>`` match resolves; zero or several
matches are skipped and reported. Emails are never fabricated. The API
token comes from the environment (``ASL_API_TOKEN``) or the repo
``.env`` and is never printed or logged.

Usage:

    uv run python scripts/import_crm_calls.py --source-dir ~/git/zoom-calls \
        [--base-url https://aishippinglabs.com] [--dry-run] \
        [--map "Name=email@example.com" ...] [--apply]
"""

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from crm.services import zoom_call_import as zci  # noqa: E402

_TOKEN_KEYS = ('ASL_API_TOKEN', 'API_SHIPPING_LABS_API_TOKEN')


def _read_token():
    """Staff API token: environment first, then the repo ``.env``."""
    for key in _TOKEN_KEYS:
        value = os.environ.get(key, '').strip()
        if value:
            return value
    for directory in (Path.cwd(), REPO_ROOT):
        candidate = directory / '.env'
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            env_key, _, raw = line.partition('=')
            if env_key.strip() in _TOKEN_KEYS:
                value = raw.strip().strip('"').strip("'")
                if value:
                    return value
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Import historical 1:1 calls into the CRM via the staff API.',
    )
    parser.add_argument(
        '--source-dir',
        required=True,
        help='zoom-calls repo checkout (reads <dir>/1x1/)',
    )
    parser.add_argument(
        '--base-url',
        default=zci.DEFAULT_BASE_URL,
        help='production API base URL (default: %(default)s)',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='print the full plan without writing (the default)',
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='perform the writes (default is a dry-run)',
    )
    parser.add_argument(
        '--map',
        dest='name_map',
        action='append',
        default=[],
        metavar='NAME=EMAIL',
        help=(
            'operator-supplied resolution for a name the lookup cannot '
            'match, e.g. Zoom labels (repeatable; wins over the lookup)'
        ),
    )
    args = parser.parse_args(argv)

    token = _read_token()
    if not token:
        parser.error(
            'no staff API token found: set ASL_API_TOKEN in the '
            'environment or the repo .env',
        )

    aliases = {}
    for entry in args.name_map:
        name, sep, email = entry.partition('=')
        if not sep or not name.strip() or not email.strip():
            parser.error(f'--map expects NAME=EMAIL, got {entry!r}')
        aliases[name.strip()] = email.strip()

    apply = args.apply and not args.dry_run
    client = zci.CrmApiClient(
        base_url=args.base_url.rstrip('/'),
        token=token,
    )
    result = zci.run_import(
        root=Path(args.source_dir).expanduser(),
        client=client,
        aliases=aliases,
        apply=apply,
    )

    for row in result.rows:
        parsed = row['parsed']
        resolution = row['resolution']
        email = getattr(resolution, 'email', None) or ''
        print(
            f"{getattr(parsed, 'stem', '?')}: "
            f"{getattr(parsed, 'name', '?')} -> "
            f"{email or 'UNRESOLVED'} [{row['disposition']}]",
        )
    for skipped in result.skipped_files:
        print(f'SKIPPED {skipped.filename}: {skipped.reason}')
    if apply:
        print(
            f"applied: {result.created} created, {result.updated} updated, "
            f"{result.skipped_unresolved} skipped-unresolved",
        )
    else:
        print(
            f"dry-run: {result.would_create} would create, "
            f"{result.would_update} would update, "
            f"{result.skipped_unresolved} skipped-unresolved",
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
