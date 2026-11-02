"""Surface banner pipeline state on event create/update responses.

The API renders banners fire-and-forget: a misconfigured generator shows
up only as a ``banner_error`` key inside a large JSON body, so a silent
outage is easy to miss at exactly the moment an event is created (the
public page then falls back to the static logo image). Print a loud
notice on stderr instead.
"""

import click


def warn_banner_result(payload):
    """Print a stderr notice describing the banner result keys, if any."""
    if not isinstance(payload, dict):
        return
    error = payload.get('banner_error')
    if error:
        click.echo('WARNING: banner generation did not run: %s' % error, err=True)
        return
    task_id = payload.get('banner_task_id')
    if task_id:
        click.echo('Banner render enqueued (task %s).' % task_id, err=True)
