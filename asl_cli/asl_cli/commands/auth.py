"""``asl login`` / ``asl logout`` / ``asl whoami`` -- device login.

Device login is the preferred way to sign in: ``asl login`` prints a code
and a verification URL, an admin approves it in the browser, and the CLI
stores the resulting staff token with mode 0600. Existing ``ASL_API_TOKEN``
and ``.env`` tokens keep working and take precedence over the stored file.
"""

from __future__ import annotations

import click
import httpx

from asl_cli import config
from asl_cli.commands._shared import emit, format_option, get_client
from asl_cli.device_flow import DeviceLoginError, default_label, device_login

groups = []


@click.command("login")
@click.option("--label", default=None, help="Machine label shown on the approval page.")
@click.option("--timeout", type=float, default=600, help="Seconds to wait for approval.")
def login(label, timeout):
    """Sign in with the browser device flow and store the credential."""
    base_url = config.resolve_base_url()
    try:
        credential = device_login(base_url, label=label or default_label(), timeout=timeout)
    except DeviceLoginError as exc:
        raise click.ClickException(str(exc))
    email = ""
    if isinstance(credential.get("user"), dict):
        email = credential["user"].get("email", "")
    path = config.write_stored_credential(base_url=base_url, token=credential["token"], email=email)
    if email:
        click.echo(f"Signed in as {email}.")
    else:
        click.echo("Signed in.")
    click.echo(f"Token stored in {path} (mode 0600).")


groups.append(login)


@click.command("logout")
def logout():
    """Remove the stored device-login credential from this machine."""
    if config.clear_stored_credential():
        click.echo("Signed out. The stored credential was removed.")
        click.echo("Revoke the server-side token in Studio if this machine is untrusted.")
    else:
        click.echo("Not signed in: no stored credential found.")


groups.append(logout)


@click.command("whoami")
@format_option
def whoami(fmt):
    """Show who the stored credential signs in as."""
    try:
        data = get_client().get("/api/whoami")
    except httpx.HTTPError as exc:
        raise click.ClickException(f"Cannot reach the API: {exc}")
    emit(data, fmt, columns=["email"])


groups.append(whoami)
