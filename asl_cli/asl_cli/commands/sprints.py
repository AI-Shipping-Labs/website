"""``asl sprints`` -- sprints, enrollments, accountability, plans."""

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


@sprints.command("enrollments")
@click.argument("slug")
@format_option
def sprints_enrollments(slug, fmt):
    """List enrollments for a sprint."""
    emit(get_client().get(f"{API}/sprints/{slug}/enrollments"), fmt)


@sprints.command("enrollment")
@click.argument("slug")
@click.argument("email")
@format_option
def sprints_enrollment(slug, email, fmt):
    """Get a single enrollment."""
    emit(get_client().get(f"{API}/sprints/{slug}/enrollments/{email}"), fmt)


@sprints.command("accountability-partners")
@click.argument("slug")
@format_option
def sprints_accountability_partners(slug, fmt):
    """List accountability partners."""
    emit(get_client().get(f"{API}/sprints/{slug}/accountability-partners"), fmt)


@sprints.command("randomize-accountability")
@click.argument("slug")
@format_option
def sprints_randomize_accountability(slug, fmt):
    """Randomize accountability partners."""
    emit(get_client().post(f"{API}/sprints/{slug}/accountability-partners/randomize"), fmt)


@sprints.command("progress-evidence")
@click.argument("slug")
@format_option
def sprints_progress_evidence(slug, fmt):
    """Get progress evidence for a sprint."""
    emit(get_client().get(f"{API}/sprints/{slug}/progress-evidence"), fmt)


@sprints.command("plans")
@click.argument("slug")
@format_option
def sprints_plans(slug, fmt):
    """List plans for a sprint."""
    emit(get_client().get(f"{API}/sprints/{slug}/plans"), fmt)


@sprints.command("import-plans")
@click.argument("slug")
@json_option("data", required=True)
@format_option
def sprints_import_plans(slug, data, fmt):
    """Bulk-import plans for a sprint."""
    emit(get_client().post(f"{API}/sprints/{slug}/plans/bulk-import", json_body=data), fmt)


@sprints.command("send-plan-emails")
@click.argument("slug")
@format_option
def sprints_send_plan_emails(slug, fmt):
    """Send ready-plan emails."""
    emit(get_client().post(f"{API}/sprints/{slug}/plans/send-ready-emails"), fmt)


@sprints.command("send-partner-intros")
@click.argument("slug")
@format_option
def sprints_send_partner_intros(slug, fmt):
    """Send accountability partner intro emails."""
    emit(get_client().post(f"{API}/sprints/{slug}/partner-intro-emails"), fmt)


@sprints.command("course-enrollments")
@click.argument("slug")
@format_option
def sprints_course_enrollments(slug, fmt):
    """List enrollments for a course."""
    emit(get_client().get(f"{API}/courses/{slug}/enrollments"), fmt)


@sprints.command("course-certificates")
@click.argument("slug")
@format_option
def sprints_course_certificates(slug, fmt):
    """List certificates for a course."""
    emit(get_client().get(f"{API}/courses/{slug}/certificates"), fmt)


groups = [sprints]
