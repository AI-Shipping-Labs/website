from django.db import migrations, models

INDEX_NAME = "emaillog_ses_msgid_idx"
TABLE_NAME = "email_app_emaillog"
SQL_COLUMNS = '"ses_message_id"'
EXPECTED_COLUMNS = ("ses_message_id",)
WHERE_SQL = "\"ses_message_id\" <> ''"


def _normalized(definition):
    return " ".join(definition.lower().replace('"', "").split())


def _excludes_blanks(definition):
    normalized = _normalized(definition or "").replace("::text", "")
    return any(
        token in normalized
        for token in (
            "ses_message_id <> ''",
            "ses_message_id > ''",
            "ses_message_id != ''",
            "not (ses_message_id = '')",
        )
    )


def _postgres_index(schema_editor, name):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT i.indisvalid,
                   i.indisunique,
                   pg_get_expr(i.indpred, i.indrelid),
                   access_method.amname,
                   i.indnkeyatts,
                   i.indnatts,
                   ARRAY(
                       SELECT pg_get_indexdef(i.indexrelid, position, true)
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
               AND table_class.relname = %s
            """,
            [name, TABLE_NAME],
        )
        return cursor.fetchone()


def _sqlite_index(schema_editor, name):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = %s",
            [name],
        )
        return cursor.fetchone()


def _matches_sqlite_expected(definition):
    normalized = _normalized(definition or "")
    return (
        " unique " not in f" {normalized} "
        and " on " in normalized
        and TABLE_NAME in normalized
        and f"({_normalized(SQL_COLUMNS)})" in normalized
        and _excludes_blanks(normalized)
    )


def create_ses_message_id_index(apps, schema_editor):
    is_postgresql = schema_editor.connection.vendor == "postgresql"
    concurrently = " CONCURRENTLY" if is_postgresql else ""
    if is_postgresql:
        existing = _postgres_index(schema_editor, INDEX_NAME)
        if existing is not None:
            (
                valid,
                unique,
                predicate,
                access_method,
                key_count,
                attribute_count,
                rendered_keys,
                _definition,
            ) = existing
            matches = (
                valid
                and not unique
                and access_method == "btree"
                and key_count == len(EXPECTED_COLUMNS)
                and attribute_count == len(EXPECTED_COLUMNS)
                and tuple(rendered_keys) == EXPECTED_COLUMNS
                and _excludes_blanks(predicate)
            )
            if matches:
                return
            schema_editor.execute(f'DROP INDEX{concurrently} IF EXISTS "{INDEX_NAME}"')
    else:
        existing = _sqlite_index(schema_editor, INDEX_NAME)
        if existing is not None:
            definition = existing[0]
            if _matches_sqlite_expected(definition):
                return
            schema_editor.execute(f'DROP INDEX{concurrently} IF EXISTS "{INDEX_NAME}"')
    schema_editor.execute(
        f'CREATE INDEX{concurrently} IF NOT EXISTS "{INDEX_NAME}" '
        f'ON "{TABLE_NAME}" ({SQL_COLUMNS}) WHERE {WHERE_SQL}',
    )


def drop_ses_message_id_index(apps, schema_editor):
    concurrently = " CONCURRENTLY" if schema_editor.connection.vendor == "postgresql" else ""
    schema_editor.execute(f'DROP INDEX{concurrently} IF EXISTS "{INDEX_NAME}"')


class Migration(migrations.Migration):
    # CREATE INDEX CONCURRENTLY is forbidden inside a transaction. SQLite
    # takes the portable IF NOT EXISTS branch used by local migration tests.
    atomic = False

    dependencies = [
        ("email_app", "0023_sesevent_match_status"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    create_ses_message_id_index,
                    drop_ses_message_id_index,
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="emaillog",
                    index=models.Index(
                        condition=models.Q(ses_message_id__gt=""),
                        fields=["ses_message_id"],
                        name="emaillog_ses_msgid_idx",
                    ),
                ),
            ],
        ),
    ]
