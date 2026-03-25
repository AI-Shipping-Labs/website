#!/usr/bin/env python
"""Sync all content from a local content repo clone to the database.

Usage:
    uv run python scripts/sync_content.py
    uv run python scripts/sync_content.py /path/to/content-repo
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from integrations.models import ContentSource
from integrations.services.github import sync_content_source


def main():
    repo_dir = (
        sys.argv[1] if len(sys.argv) > 1
        else os.environ.get('CONTENT_REPO_DIR')
        or os.path.expanduser('~/git/ai-shipping-labs-content')
    )

    if not os.path.isdir(repo_dir):
        print(f'Content repo not found at {repo_dir}')
        print('Clone it first: git clone git@github.com:AI-Shipping-Labs/content.git ~/git/ai-shipping-labs-content')
        sys.exit(1)

    sources = ContentSource.objects.all()
    if not sources.exists():
        print('No content sources found. Run: uv run python manage.py seed_content_sources')
        sys.exit(1)

    for source in sources:
        print(f'Syncing {source.content_type}...')
        result = sync_content_source(source, repo_dir=repo_dir)
        print(f'  {result.items_created} created, {result.items_updated} updated')
        for error in (result.errors or []):
            print(f'  ERROR: {error}')

    print('Done.')


if __name__ == '__main__':
    main()
