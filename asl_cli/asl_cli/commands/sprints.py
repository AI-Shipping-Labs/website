"""``asl sprints`` -- sprint list/detail, enrollments, accountability."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"

commands = []


@click.command("sprints-list")
@format_option
def sprints_list(fmt):
    """List sprints."""
    data = get_client().get(f"{API}/sprints")
    emit(data, fmt)


commands.append(sprints_list)


@click.command("sprints-get")
@click.argument("slug")
@format_option
def sprints_get(slug, fmt):
    """Get a single sprint."""
    data = get_client().get(f"{API}/sprints/{slug}")
    emit(data, fmt)


commands.append(sprints_get)


@click.command("sprints-enrollments")
@click.argument("slug")
@format_option
def sprints_enrollments(slug, fmt):
    """List enrollments for a sprint."""
    data = get_client().get(f"{API}/sprints/{slug}/enrollments")
    emit(data, fmt)


commands.append(sprints_enrollments)


@click.command("sprints-enrollment-get")
@click.argument("slug")
@click.argument("email")
@format_option
def sprints_enrollment_get(slug, email, fmt):
    """Get a single enrollment."""
    data = get_client().get(f"{API}/sprints/{slug}/enrollments/{email}")
    emit(data, fmt)


commands.append(sprints_enrollment_get)


@click.command("sprints-accountability-partners")
@click.argument("slug")
@format_option
def sprints_accountability_partners(slug, fmt):
    """List accountability partners for a sprint."""
    data = get_client().get(f"{API}/sprints/{slug}/accountability-partners")
    emit(data, fmt)


commands.append(sprints_accountability_partners)


@click.command("sprints-accountability-randomize")
@click.argument("slug")
@format_option
def sprints_accountability_randomize(slug, fmt):
    """Randomize accountability partners for a sprint."""
    data = get_client().post(f"{API}/sprints/{slug}/accountability-partners/randomize")
    emit(data, fmt)


commands.append(sprints_accountability_randomize)


@click.command("sprints-progress-evidence")
@click.argument("slug")
@format_option
def sprints_progress_evidence(slug, fmt):
    """Get progress evidence for a sprint."""
    data = get_client().get(f"{API}/sprints/{slug}/progress-evidence")
    emit(data, fmt)


commands.append(sprints_progress_evidence)


# -- course enrollments / certificates ---------------------------------------

@click.command("course-enrollments")
@click.argument("slug")
@format_option
def course_enrollments(slug, fmt):
    """List enrollments for a course."""
    data = get_client().get(f"{API}/courses/{slug}/enrollments")
    emit(data, fmt)


commands.append(course_enrollments)


@click.command("course-certificates")
@click.argument("slug")
@format_option
def course_certificates(slug, fmt):
    """List certificates for a course."""
    data = get_client().get(f"{API}/courses/{slug}/certificates")
    emit(data, fmt)


commands.append(course_certificates)
