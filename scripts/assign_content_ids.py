#!/usr/bin/env python
"""Assign content_id UUIDs to content files missing them.

Walks a local content repo directory, finds all .md and .yaml files with
frontmatter, and for any file missing a content_id field, generates a UUID v4
and writes it back into the frontmatter.

Usage:
    python scripts/assign_content_ids.py /path/to/content-repo
"""
import os
import sys
import uuid

import frontmatter
import yaml


def assign_content_ids(repo_dir):
    """Walk repo_dir and assign content_id to files missing it.

    Returns:
        tuple: (assigned_count, already_had_count)
    """
    assigned = 0
    already_had = 0

    for root, dirs, files in os.walk(repo_dir):
        # Skip .git directory
        dirs[:] = [d for d in dirs if d != '.git']

        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in ('.md', '.yaml', '.yml'):
                continue
            if filename.upper() == 'README.MD':
                continue

            filepath = os.path.join(root, filename)

            try:
                if ext == '.md':
                    post = frontmatter.load(filepath)
                    if post.get('content_id'):
                        already_had += 1
                        continue
                    post['content_id'] = str(uuid.uuid4())
                    frontmatter.dump(post, filepath)
                    assigned += 1
                else:
                    # YAML file
                    with open(filepath, 'r') as f:
                        data = yaml.safe_load(f)
                    if not isinstance(data, dict):
                        continue
                    if data.get('content_id'):
                        already_had += 1
                        continue
                    data['content_id'] = str(uuid.uuid4())
                    with open(filepath, 'w') as f:
                        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
                    assigned += 1
            except Exception as e:
                print(f'Warning: could not process {filepath}: {e}')

    return assigned, already_had


def main():
    if len(sys.argv) < 2:
        print('Usage: python scripts/assign_content_ids.py /path/to/content-repo')
        sys.exit(1)

    repo_dir = sys.argv[1]
    if not os.path.isdir(repo_dir):
        print(f'Directory not found: {repo_dir}')
        sys.exit(1)

    assigned, already_had = assign_content_ids(repo_dir)
    print(f'Assigned content_id to {assigned} files. {already_had} files already had content_id.')


if __name__ == '__main__':
    main()
