#!/usr/bin/env python3
"""Reject rolling-deploy-unsafe NOT NULL ``AddField`` operations.

The check reads Django's on-disk migration graph and operation objects.  It
does not initialize application ``ready()`` hooks and never opens a database
connection.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MethodType
from typing import Callable, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from django.apps import apps
from django.apps.config import AppConfig
from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, connections, models
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.migration import Migration
from django.db.migrations.operations.fields import AddField
from django.db.migrations.operations.models import CreateModel, RenameModel
from django.db.migrations.operations.special import SeparateDatabaseAndState
from django.db.migrations.state import ProjectState
from django.db.models.fields import NOT_PROVIDED, AutoFieldMixin

MigrationNode = tuple[str, str]
StateFactory = Callable[[MigrationNode], ProjectState]

# This is a frozen enforcement boundary, not a list of current leaves.  Routine
# feature work must never move it forward to make a finding disappear.
FROZEN_BASELINE_NODES: tuple[MigrationNode, ...] = (
    ("accounts", "0026_deletion_request_lifecycle"),
    ("analytics", "0006_alter_useractivity_event_type"),
    ("bookclub", "0008_alter_book_required_level"),
    ("comments", "0001_initial"),
    (
        "community",
        "0019_alter_callhost_options_alter_bookedcall_host_and_more",
    ),
    ("content", "0060_article_image_manifest"),
    ("crm", "0008_slack_ingest_lease_and_refresh_count"),
    ("email_app", "0021_reconcile_emaillog_subject_default"),
    ("events", "0045_seriesoccurrenceoptout"),
    ("integrations", "0028_maven_enrollment_notification_step"),
    ("notifications", "0011_alter_notification_notification_type"),
    ("payments", "0014_stripewebhookdeliveryattempt_refund_review"),
    (
        "plans",
        "0029_sprint_audience_sprint_description_sprint_outcomes",
    ),
    ("questionnaires", "0008_response_review_queue"),
    ("studio", "0001_initial"),
    ("triggers", "0003_r1_expand_reconciliation"),
    ("voting", "0001_initial"),
)


@dataclass(frozen=True, order=True)
class Finding:
    app_label: str
    migration_name: str
    model_name: str
    field_name: str

    def render(self) -> str:
        return (
            f"{self.app_label}.{self.migration_name}: "
            f"{self.model_name}.{self.field_name} adds a physical NOT NULL "
            "column without a persistent non-None db_default. Old-image "
            "inserts can omit the unknown column; Python default, "
            "preserve_default, blank, auto_now, and auto_now_add do not "
            "protect their SQL after the migration. Supply a valid persistent "
            "db_default through the overlap window, or use an explicitly "
            "reviewed expand/contract migration that keeps the column nullable "
            "during overlap."
        )


@dataclass(frozen=True)
class CheckResult:
    findings: tuple[Finding, ...] = ()
    configuration_errors: tuple[str, ...] = ()
    checked_migrations: int = 0

    @property
    def ok(self) -> bool:
        return not self.findings and not self.configuration_errors

    def render(self) -> str:
        lines = [
            f"Migration safety configuration error: {error}"
            for error in self.configuration_errors
        ]
        lines.extend(
            f"Migration safety violation: {finding.render()}"
            for finding in self.findings
        )
        if self.configuration_errors:
            lines.append(
                "Migration safety check failed: "
                f"{len(self.configuration_errors)} configuration error(s), "
                f"{len(self.findings)} violation(s)."
            )
        elif self.findings:
            lines.append(
                f"Migration safety check failed: {len(self.findings)} violation(s)."
            )
        else:
            lines.append(
                "Migration safety check passed: 0 violations across "
                f"{self.checked_migrations} in-scope first-party migration(s)."
            )
        return "\n".join(lines)


def _noop_ready(_app_config: AppConfig) -> None:
    """Stand-in for AppConfig.ready() during the database-free bootstrap."""


def setup_django_without_ready_hooks() -> None:
    """Populate models for migration loading without running ``ready()``.

    ``django.setup()`` invokes every application ``ready()`` hook.  This
    project intentionally warms an integration-settings cache in one of those
    hooks, which performs a database query.  Migration loading needs the app
    and model registries, but none of the hooks, so pass prebuilt AppConfig
    instances whose per-instance hook is a no-op to Django's public registry
    population API.
    """

    if apps.ready:
        return
    app_configs = [AppConfig.create(entry) for entry in settings.INSTALLED_APPS]
    for app_config in app_configs:
        app_config.ready = MethodType(_noop_ready, app_config)
    apps.populate(app_configs)


def first_party_app_labels(repo_root: Path) -> frozenset[str]:
    """Return installed apps whose package lives directly in this repository."""

    root = repo_root.resolve()
    labels = {
        app_config.label
        for app_config in apps.get_app_configs()
        if Path(app_config.path).resolve()
        == root.joinpath(*app_config.name.split(".")).resolve()
    }
    return frozenset(labels)


def _in_scope_nodes(
    graph: MigrationGraph,
    migrations: Mapping[MigrationNode, Migration],
    first_party_apps: frozenset[str],
    baselines: tuple[MigrationNode, ...],
) -> tuple[tuple[MigrationNode, ...], tuple[str, ...]]:
    errors: list[str] = []

    for node in baselines:
        if node not in graph.nodes:
            errors.append(
                f"frozen baseline node {node[0]}.{node[1]} is missing from "
                "the on-disk graph; restore it instead of advancing the baseline"
            )
        if node[0] not in first_party_apps:
            errors.append(
                f"frozen baseline app {node[0]} is not an installed first-party app"
            )

    if errors:
        return (), tuple(sorted(set(errors)))

    grandfathered: set[MigrationNode] = set()
    for app_label, migration_name in baselines:
        grandfathered.update(
            node
            for node in graph.forwards_plan((app_label, migration_name))
            if node[0] == app_label
        )

    # A first-party app absent from the frozen baseline has no grandfathered
    # nodes, so its first and every subsequent migration remain in scope.
    nodes = tuple(
        sorted(
            node
            for node in migrations
            if node in graph.nodes
            and node[0] in first_party_apps
            and node not in grandfathered
        )
    )

    return nodes, ()


def _field_adds_physical_column(field: models.Field, connection) -> bool:
    if isinstance(field, (models.GeneratedField, AutoFieldMixin)):
        return False
    if getattr(field, "many_to_many", False) or not getattr(field, "concrete", True):
        return False
    try:
        return field.db_parameters(connection=connection).get("type") is not None
    except (AttributeError, LookupError, TypeError):
        # Unbound relation fields can require a rendered target model to resolve
        # their SQL type. They are ordinary physical columns, so fail closed.
        return True


def _has_persistent_database_default(field: models.Field) -> bool:
    return field.db_default is not NOT_PROVIDED and field.db_default is not None


def _model_has_writable_table(
    state: ProjectState,
    app_label: str,
    model_name: str,
) -> bool | None:
    model_state = state.models.get((app_label, model_name.lower()))
    if model_state is None:
        return None
    return bool(
        model_state.options.get("managed", True)
        and not model_state.options.get("proxy", False)
    )


def _inspect_operation_sequence(
    *,
    operations: Iterable,
    state: ProjectState,
    app_label: str,
    migration_name: str,
    connection,
    created_models: set[str],
    findings: list[Finding],
    errors: list[str],
) -> None:
    for operation in operations:
        if isinstance(operation, SeparateDatabaseAndState):
            _inspect_operation_sequence(
                operations=operation.database_operations,
                state=state.clone(),
                app_label=app_label,
                migration_name=migration_name,
                connection=connection,
                created_models=created_models,
                findings=findings,
                errors=errors,
            )
        elif isinstance(operation, CreateModel):
            created_models.add(operation.name.lower())
        elif isinstance(operation, RenameModel):
            old_name = operation.old_name.lower()
            if old_name in created_models:
                created_models.remove(old_name)
                created_models.add(operation.new_name.lower())
        elif isinstance(operation, AddField):
            model_name = operation.model_name.lower()
            field = operation.field
            if (
                model_name not in created_models
                and not field.null
                and not _has_persistent_database_default(field)
                and _field_adds_physical_column(field, connection)
            ):
                has_table = _model_has_writable_table(state, app_label, model_name)
                if has_table is None:
                    errors.append(
                        f"{app_label}.{migration_name} cannot resolve model "
                        f"state for {operation.model_name}.{operation.name}"
                    )
                elif has_table:
                    findings.append(
                        Finding(
                            app_label=app_label,
                            migration_name=migration_name,
                            model_name=operation.model_name,
                            field_name=operation.name,
                        )
                    )

        operation.state_forwards(app_label, state)


def check_migration_graph(
    *,
    graph: MigrationGraph,
    migrations: Mapping[MigrationNode, Migration],
    state_factory: StateFactory,
    first_party_apps: frozenset[str],
    connection,
    baselines: tuple[MigrationNode, ...] = FROZEN_BASELINE_NODES,
) -> CheckResult:
    nodes, configuration_errors = _in_scope_nodes(
        graph, migrations, first_party_apps, baselines
    )
    if configuration_errors:
        return CheckResult(configuration_errors=configuration_errors)

    findings: list[Finding] = []
    errors: list[str] = []
    for app_label, migration_name in nodes:
        try:
            state = state_factory((app_label, migration_name))
            _inspect_operation_sequence(
                operations=migrations[(app_label, migration_name)].operations,
                state=state,
                app_label=app_label,
                migration_name=migration_name,
                connection=connection,
                created_models=set(),
                findings=findings,
                errors=errors,
            )
        except Exception as exc:  # noqa: BLE001 - a linter must fail closed.
            errors.append(
                f"{app_label}.{migration_name} could not be inspected "
                f"({type(exc).__name__}: {exc})"
            )

    return CheckResult(
        findings=tuple(sorted(findings)),
        configuration_errors=tuple(sorted(set(errors))),
        checked_migrations=len(nodes),
    )


def check_repository() -> CheckResult:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings")
    setup_django_without_ready_hooks()
    loader = MigrationLoader(None, ignore_no_migrations=True)
    graph_migrations = {
        node: migration
        for node, migration in loader.disk_migrations.items()
        if node in loader.graph.nodes
    }
    return check_migration_graph(
        graph=loader.graph,
        migrations=graph_migrations,
        state_factory=lambda node: loader.project_state([node], at_end=False),
        first_party_apps=first_party_app_labels(Path(settings.BASE_DIR)),
        connection=connections[DEFAULT_DB_ALIAS],
    )


def main() -> int:
    result = check_repository()
    print(result.render())
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
