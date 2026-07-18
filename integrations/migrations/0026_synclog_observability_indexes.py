from django.db import migrations, models

INDEXES = (
    (
        "sync_src_status_started_idx",
        '"source_id", "status", "started_at" DESC',
        ("source_id", "status", "started_at"),
        (0, 0, 3),
    ),
    (
        "sync_batch_started_idx",
        '"batch_id", "started_at" DESC',
        ("batch_id", "started_at"),
        (0, 3),
    ),
)


def _normalized(definition):
    return " ".join(definition.lower().replace('"', "").split())


def _postgres_index(schema_editor, name):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT i.indisvalid,
                   i.indisunique,
                   i.indpred IS NULL,
                   access_method.amname,
                   i.indnkeyatts,
                   i.indnatts,
                   ARRAY(
                       SELECT pg_get_indexdef(i.indexrelid, position, true)
                         FROM generate_series(1, i.indnkeyatts) AS position
                       ORDER BY position
                   ),
                   ARRAY(
                       SELECT i.indoption[position - 1]
                         FROM generate_series(1, i.indnkeyatts) AS position
                        ORDER BY position
                   ),
                   pg_get_indexdef(i.indexrelid)
              FROM pg_index AS i
              JOIN pg_class AS index_class ON index_class.oid = i.indexrelid
              JOIN pg_class AS table_class ON table_class.oid = i.indrelid
              JOIN pg_namespace AS namespace
                ON namespace.oid = index_class.relnamespace
              JOIN pg_am AS access_method ON access_method.oid = index_class.relam
             WHERE namespace.nspname = current_schema()
               AND index_class.relname = %s
               AND table_class.relname = 'integrations_synclog'
            """,
            [name],
        )
        return cursor.fetchone()


def _sqlite_index(schema_editor, name):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1, sql FROM sqlite_master WHERE type = 'index' AND name = %s",
            [name],
        )
        return cursor.fetchone()


def _matches_sqlite_expected(definition, sql_columns):
    normalized = _normalized(definition or "")
    expected_columns = _normalized(sql_columns)
    return (
        " on " in normalized
        and "integrations_synclog" in normalized
        and f"({expected_columns})" in normalized
    )


def create_observability_indexes(apps, schema_editor):
    is_postgresql = schema_editor.connection.vendor == "postgresql"
    concurrently = " CONCURRENTLY" if is_postgresql else ""
    inspect = _postgres_index if is_postgresql else _sqlite_index
    for name, sql_columns, expected_columns, expected_options in INDEXES:
        existing = inspect(schema_editor, name)
        if existing is not None:
            if is_postgresql:
                (
                    valid,
                    unique,
                    unqualified,
                    access_method,
                    key_count,
                    attribute_count,
                    rendered_keys,
                    index_options,
                    _definition,
                ) = existing
                matches = (
                    valid
                    and not unique
                    and unqualified
                    and access_method == "btree"
                    and key_count == len(expected_columns)
                    and attribute_count == len(expected_columns)
                    and tuple(rendered_keys) == expected_columns
                    and tuple(index_options) == expected_options
                )
            else:
                valid, definition = existing
                matches = valid and _matches_sqlite_expected(
                    definition,
                    sql_columns,
                )
            if matches:
                continue
            # IF NOT EXISTS alone silently preserves an interrupted invalid
            # index or a wrong-definition same-name index. Remove only that
            # unusable/mismatched object, online on PostgreSQL, then retry the
            # exact definition.
            schema_editor.execute(f'DROP INDEX{concurrently} IF EXISTS "{name}"')
        schema_editor.execute(
            f'CREATE INDEX{concurrently} IF NOT EXISTS "{name}" '
            f'ON "integrations_synclog" ({sql_columns})',
        )


def drop_observability_indexes(apps, schema_editor):
    concurrently = " CONCURRENTLY" if schema_editor.connection.vendor == "postgresql" else ""
    for name, _sql_columns, _expected_columns, _expected_options in reversed(INDEXES):
        schema_editor.execute(f'DROP INDEX{concurrently} IF EXISTS "{name}"')


class Migration(migrations.Migration):
    # CREATE INDEX CONCURRENTLY is forbidden inside a transaction.  SQLite
    # takes the portable IF NOT EXISTS branch used by local migration tests.
    atomic = False

    dependencies = [
        ('integrations', '0025_webhooklog_delivery_state'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    create_observability_indexes,
                    drop_observability_indexes,
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name='synclog',
                    index=models.Index(
                        fields=['source', 'status', '-started_at'],
                        name='sync_src_status_started_idx',
                    ),
                ),
                migrations.AddIndex(
                    model_name='synclog',
                    index=models.Index(
                        fields=['batch_id', '-started_at'],
                        name='sync_batch_started_idx',
                    ),
                ),
            ],
        ),
    ]
