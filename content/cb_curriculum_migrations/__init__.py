"""Discover installed curriculum migrations after the site-owned history."""

from community_base.curriculum import migrations as package_migrations

for package_path in package_migrations.__path__:
    if package_path not in __path__:
        __path__.append(package_path)
