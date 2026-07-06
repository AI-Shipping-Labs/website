"""``asl raw`` -- generic escape-hatch for any API path."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client, json_option

groups = []


@click.command("raw")
@click.argument("method", type=click.Choice(["GET", "POST", "PATCH", "PUT", "DELETE"], case_sensitive=False))
@click.argument("path")
@json_option("data", required=False)
@click.option("-p", "--param", "params", multiple=True, help="Query params as key=value (repeatable).")
@click.option("--raw-output", is_flag=True, default=False, help="Return raw text instead of JSON.")
@format_option
def raw(method, path, data, params, raw_output, fmt):
    """Call any API path directly.

    PATH is the full path after the base URL, e.g. /api/events.
    METHOD is GET/POST/PATCH/PUT/DELETE.

    Examples:

      asl raw GET /api/events
      asl raw GET /api/users -p email=someone@example.com
      asl raw POST /api/integrations/settings --data '{"updates":[...]}'
    """
    query = {}
    for p in params:
        if "=" in p:
            key, _, value = p.partition("=")
            query[key] = value
        else:
            query[p] = ""

    result = get_client().request(
        method.upper(),
        path,
        params=query or None,
        json_body=data,
        raw=raw_output,
    )
    if raw_output:
        click.echo(result)
    else:
        emit(result, fmt)


groups.append(raw)
