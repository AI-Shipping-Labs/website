"""Fixture helpers for the study-group journeys in issue #1984."""

import importlib

from django.db import connection

from playwright_tests.conftest import create_user
from playwright_tests.test_pods_1918 import COURSE_SLUG


def groups_url(server):
    return f"{server}/courses/{COURSE_SLUG}/home/pods?cohort=4"


def membership_count(pod, user):
    from pods.models import PodMembership

    count = PodMembership.objects.filter(pod=pod, user=user).count()
    connection.close()
    return count


def set_theme(page, theme):
    page.evaluate(
        "theme => { localStorage.setItem('theme', theme); "
        "document.documentElement.classList.toggle('dark', theme === 'dark'); }",
        theme,
    )


def imported_members(cohort, users):
    from accounts.models import User
    from content.models import CohortEnrollment

    members = [users["anna"], users["raj"], users["mike"], users["basic"]]
    for index in range(48):
        email = f"imported-{index}@test.com"
        create_user(email, tier_slug="main", first_name=f"Imported{index}")
        user = User.objects.get(email=email)
        CohortEnrollment.objects.create(user=user, cohort=cohort)
        members.append(user)
    return members


def imported_groups(cohort, members):
    from pods.models import Pod, PodMembership

    migration = importlib.import_module("pods.migrations.0010_unlimited_self_service_groups")
    groups = []
    for number, title in enumerate(migration.REVIEWED_PURPOSES, start=1):
        groups.append(
            Pod.objects.create(
                cohort=cohort,
                name=title,
                purpose=f"Raw private formation note {number}",
                source="studio",
            )
        )
    for index, user in enumerate(members):
        PodMembership.objects.create(pod=groups[index % 23], user=user, source="staff")
    return groups
