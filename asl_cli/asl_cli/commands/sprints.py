"""``asl sprints`` -- sprint list/detail, enrollments, accountability, plans."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client, json_option

API = "/api"


@click.group()
def sprints():
    """Manage sprints."""


@sprints.command("list")
@format_option
def sprints_list(fmt):
    """List sprints."""
    emit(get_client().get(f"{API}/sprints"), fmt)


@sprints.command("get")
@click.argument("slug")
@format_option
def sprints_get(slug, fmt):
    """Get a single sprint."""
    emit(get_client().get(f"{API}/sprints/{slug}"), fmt)


# -- enrollments (nested) ----------------------------------------------------

@click.group(name="enrollments")
def sprints_enrollments():
    """Manage sprint enrollments."""


@sprints_enrollments.command("list")
@click.argument("slug")
@format_option
def sprints_enrollments_list(slug, fmt):
    """List enrollments for a sprint."""
    emit(get_client().get(f"{API}/sprints/{slug}/enrollments"), fmt)


@sprints_enrollments.command("get")
@click.argument("slug")
@click.argument("email")
@format_option
def sprints_enrollment_get(slug, email, fmt):
    """Get a single enrollment."""
    emit(get_client().get(f"{API}/sprints/{slug}/enrollments/{email}"), fmt)


sprints.add_command(sprints_enrollments)


# -- accountability (nested) -------------------------------------------------

@click.group(name="accountability")
def sprints_accountability():
    """Manage accountability partners."""


@sprints_accountability.command("list")
@click.argument("slug")
@format_option
def sprints_accountability_list(slug, fmt):
    """List accountability partners for a sprint."""
    emit(get_client().get(f"{API}/sprints/{slug}/accountability-partners"), fmt)


@sprints_accountability.command("randomize")
@click.argument("slug")
@format_option
def sprints_accountability_randomize(slug, fmt):
    """Randomize accountability partners."""
    emit(get_client().post(f"{API}/sprints/{slug}/accountability-partners/randomize"), fmt)


sprints.add_command(sprints_accountability)


@sprints.command("progress-evidence")
@click.argument("slug")
@format_option
def sprints_progress_evidence(slug, fmt):
    """Get progress evidence for a sprint."""
    emit(get_client().get(f"{API}/sprints/{slug}/progress-evidence"), fmt)


# -- plans (nested) ----------------------------------------------------------

@click.group(name="plans")
def sprints_plans():
    """Manage sprint plans."""


@sprints_plans.command("list")
@click.argument("slug")
@format_option
def sprint_plans_list(slug, fmt):
    """List plans for a sprint."""
    emit(get_client().get(f"{API}/sprints/{slug}/plans"), fmt)


@sprints_plans.command("bulk-import")
@click.argument("slug")
@json_option("data", required=True)
@format_option
def sprint_plans_bulk_import(slug, data, fmt):
    """Bulk-import plans for a sprint."""
    emit(get_client().post(f"{API}/sprints/{slug}/plans/bulk-import", json_body=data), fmt)


@sprints_plans.command("send-ready-emails")
@click.argument("slug")
@format_option
def sprint_plans_send_ready_emails(slug, fmt):
    """Send ready-plan emails."""
    emit(get_client().post(f"{API}/sprints/{slug}/plans/send-ready-emails"), fmt)


@sprints_plans.command("partner-intro-emails")
@click.argument("slug")
@format_option
def sprint_partner_intro_emails(slug, fmt):
    """Send accountability partner intro emails."""
    emit(get_client().post(f"{API}/sprints/{slug}/partner-intro-emails"), fmt)


sprints.add_command(sprints_plans)


# -- courses (nested) --------------------------------------------------------

@click.group(name="courses")
def sprints_courses():
    """Course enrollments and certificates."""


@sprints_courses.command("enrollments")
@click.argument("slug")
@format_option
def course_enrollments(slug, fmt):
    """List enrollments for a course."""
    emit(get_client().get(f"{API}/courses/{slug}/enrollments"), fmt)


@sprints_courses.command("certificates")
@click.argument("slug")
@format_option
def course_certificates(slug, fmt):
    """List certificates for a course."""
    emit(get_client().get(f"{API}/courses/{slug}/certificates"), fmt)


sprints.add_command(sprints_courses)


groups = [sprints]
