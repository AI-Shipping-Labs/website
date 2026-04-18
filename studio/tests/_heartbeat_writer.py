"""Subprocess helper for ``test_worker_health_cache``.

Writes a single fake django-q ``Stat`` heartbeat into a FileBasedCache
located at the directory passed as ``--cache-dir``. Run as a standalone
script — the parent test process invokes this with ``subprocess.run`` to
prove that heartbeats written in one process are visible from another.

This mirrors what a real ``manage.py qcluster`` does on every guard cycle,
minus the actual cluster machinery.
"""

import argparse
import os
import sys

# Ensure the project root (the parent of website/) is on sys.path before we
# touch Django, in case this file was invoked from a different cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, '..', '..'))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache-dir', required=True)
    parser.add_argument('--cluster-id', default='test-cluster')
    parser.add_argument(
        '--secret-key', required=True,
        help='Must match the parent process SECRET_KEY — django-q signs '
             'every Stat payload with this and the parent will reject '
             'BadSignature entries silently.',
    )
    parser.add_argument(
        '--q-cluster-name', default='ai-shipping-labs',
        help='Must match Q_CLUSTER["name"] in the parent — used as the '
             'signing salt and as part of the cache key prefix.',
    )
    args = parser.parse_args()

    # Configure Django settings programmatically so this child process
    # uses the same FileBasedCache LOCATION as the parent test, without
    # importing the project's full settings (which may try to reach AWS,
    # GitHub, etc. in some environments).
    from django.conf import settings
    if not settings.configured:
        settings.configure(
            DEBUG=True,
            DATABASES={
                'default': {
                    'ENGINE': 'django.db.backends.sqlite3',
                    'NAME': ':memory:',
                },
            },
            INSTALLED_APPS=[
                'django.contrib.contenttypes',
                'django.contrib.auth',
                'django_q',
            ],
            CACHES={
                'default': {
                    'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                },
                'django_q': {
                    'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
                    'LOCATION': args.cache_dir,
                },
            },
            Q_CLUSTER={
                'name': args.q_cluster_name,
                'orm': 'default',
                'cache': 'django_q',
            },
            USE_TZ=True,
            SECRET_KEY=args.secret_key,
        )

    import django
    django.setup()

    # django-q imports must come after django.setup() so settings are resolved.
    # Status is Stat's parent class — we use it directly to skip the Sentinel
    # construction (Sentinel needs a full cluster process). The serialised
    # payload is byte-compatible with what a real cluster writes.
    from django.core.cache import caches
    from django_q.conf import Conf
    from django_q.signing import SignedPackage
    from django_q.status import Stat, Status

    heartbeat = Status(pid=os.getpid(), cluster_id=args.cluster_id)
    heartbeat.status = 'Idle'
    heartbeat.workers = [os.getpid()]

    cache = caches[Conf.CACHE]
    key = Stat.get_key(args.cluster_id)
    payload = SignedPackage.dumps(heartbeat, True)

    # Mirror Broker.set_stat: maintain the index list AND write the entry.
    key_list = cache.get(Conf.Q_STAT, []) or []
    if key not in key_list:
        key_list.append(key)
    cache.set(Conf.Q_STAT, key_list)
    cache.set(key, payload, 30)
    print(f'WROTE {key} -> {args.cache_dir}')


if __name__ == '__main__':
    main()
