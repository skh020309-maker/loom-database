#!/usr/bin/env python3
"""Export all eight Loom SQLite tables to a MySQL 8.0.29+ seed SQL file.

Reads SQLite in read-only mode and never connects to or installs MySQL.
Run schema/mysql.sql in a selected database first, then import the seed into
empty tables. Ordinary INSERT statements intentionally fail on duplicate keys.
Text uses UTF-8 hex expressions, independent of backslash/quote SQL modes.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "loom.db"
DEFAULT_OUTPUT = ROOT / "exports" / "mysql_seed.sql"
# Parent tables always precede children; every SQLite field is preserved.
COLUMNS = OrderedDict({
    "sources": (
        "id", "url", "external_author", "source_kind", "license_status",
        "license_name", "license_url", "created_at",
    ),
    "notes": (
        "id", "source_id", "title", "language", "original_excerpt",
        "original_sha256", "excerpt_locator", "retrieved_on", "content_origin",
        "split", "training_allowed", "owner_user_id", "created_at",
    ),
    "annotations": (
        "note_id", "summary_zh", "category", "summary_origin", "review_status",
        "pipeline_status", "reviewer", "updated_at",
    ),
    "tags": ("id", "name"),
    "note_tags": ("note_id", "tag_id"),
    "import_batches": (
        "id", "input_file", "input_sha256", "record_count",
        "inserted_count", "skipped_count", "created_at",
    ),
    "processing_logs": (
        "id", "note_id", "batch_id", "process_type", "status",
        "provider", "model", "details", "created_at",
    ),
    "source_checks": (
        "id", "source_id", "note_id", "checked_on", "checker_type", "fetch_status",
        "excerpt_status", "author_check", "license_check", "license_evidence_url",
        "notes", "method", "created_at",
    ),
})
TEXT_LIMITS = {
    ("sources", "url"): 768,
    ("notes", "id"): 191,
    ("notes", "original_sha256"): 64,
    ("annotations", "note_id"): 191,
    ("tags", "name"): 191,
    ("note_tags", "note_id"): 191,
    ("processing_logs", "note_id"): 191,
    ("source_checks", "id"): 191,
    ("source_checks", "note_id"): 191,
}
for _table, _fields in {
    "sources": ("source_kind", "license_status"),
    "notes": ("language", "content_origin", "split"),
    "annotations": ("category", "summary_origin", "review_status", "pipeline_status"),
    "processing_logs": ("process_type", "status"),
    "source_checks": ("checker_type", "fetch_status", "excerpt_status", "author_check",
                      "license_check", "method"),
}.items():
    for _field in _fields:
        TEXT_LIMITS[(_table, _field)] = 32
INTEGER_COLUMNS = {
    "sources": {"id"},
    "notes": {"source_id", "training_allowed"},
    "annotations": set(),
    "tags": {"id"},
    "note_tags": {"tag_id"},
    "import_batches": {"id", "record_count", "inserted_count", "skipped_count"},
    "processing_logs": {"id", "batch_id"},
    "source_checks": {"source_id"},
}


class ExportError(ValueError):
    """The SQLite data cannot be exported without changing its meaning."""


def identifier(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name):
        raise ExportError(f"Unsupported SQL identifier: {name!r}")
    return f"`{name}`"


def sql_value(value: object) -> str:
    if value is None:
        return "NULL"
    if type(value) is int:
        if not -(2**63) <= value < 2**63:
            raise ExportError("Integer exceeds MySQL signed BIGINT range")
        return str(value)
    if isinstance(value, str):
        # No user-controlled character appears inside the SQL string literal.
        return f"CONVERT(X'{value.encode('utf-8').hex()}' USING utf8mb4)"
    raise ExportError(f"Unsupported SQLite value type: {type(value).__name__}")


def export_database(db_path: Path, output: Path) -> dict[str, object]:
    db_path, output = Path(db_path).resolve(), Path(output).resolve()
    if not db_path.is_file():
        raise ExportError(f"SQLite database does not exist: {db_path}")
    if output == db_path:
        raise ExportError("Output must not overwrite the SQLite database")
    if output.suffix.lower() != ".sql":
        raise ExportError("Output must be an .sql file")
    if output in {(ROOT / "schema" / name).resolve() for name in ("sqlite.sql", "mysql.sql")}:
        raise ExportError("Output must not overwrite a schema file")

    conn = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
    counts: dict[str, int] = {}
    lines = [
        "-- Loom MySQL 8.0.29+ seed export. MySQL has NOT been executed or verified.",
        "-- Requires schema/mysql.sql and eight EMPTY target tables in a selected database.",
        "-- Stop on the first SQL error; do not use the mysql client's --force option.",
        "-- Every text value is UTF-8 hex; original characters are preserved.",
        "SET NAMES utf8mb4 COLLATE utf8mb4_0900_bin;",
        "SET @loom_export_previous_sql_mode = @@SESSION.sql_mode;",
        "SET SESSION sql_mode = CONCAT_WS(',', @@SESSION.sql_mode,"
        " 'STRICT_TRANS_TABLES', 'NO_AUTO_VALUE_ON_ZERO');",
        "START TRANSACTION;",
    ]
    try:
        conn.execute("BEGIN")  # One consistent snapshot for all eight tables.
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise ExportError(f"SQLite integrity check failed: {integrity}")
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise ExportError(f"SQLite foreign key check failed: {fk_errors}")
        actual_tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if actual_tables != set(COLUMNS):
            raise ExportError(
                f"Table contract differs: expected {sorted(COLUMNS)}, found {sorted(actual_tables)}"
            )
        for table, columns in COLUMNS.items():
            schema = conn.execute(f"PRAGMA table_info({identifier(table)})").fetchall()
            if tuple(column[1] for column in schema) != columns:
                raise ExportError(f"Column contract differs for {table}; update both schemas first")
            primary = [column[1] for column in sorted(schema, key=lambda col: col[5]) if column[5]]
            required = {column[1] for column in schema if column[3] or column[5]}
            ordering = ", ".join(identifier(name) for name in primary) or "rowid"
            rows = conn.execute(
                f"SELECT {', '.join(map(identifier, columns))} FROM {identifier(table)}"
                f" ORDER BY {ordering}"
            ).fetchall()
            counts[table] = len(rows)
            lines.append(f"\n-- {table}: {len(rows)} rows")
            for row in rows:
                for column, value in zip(columns, row):
                    if value is None:
                        if column in required:
                            raise ExportError(f"{table}.{column}: NULL is not portable to a MySQL key/required field")
                        continue
                    expected_type = int if column in INTEGER_COLUMNS[table] else str
                    if type(value) is not expected_type:
                        raise ExportError(f"{table}.{column}: expected {expected_type.__name__}")
                    limit = TEXT_LIMITS.get((table, column))
                    if limit is not None and len(value) > limit:
                        raise ExportError(f"{table}.{column} exceeds MySQL's {limit}-character bound")
                if table == "notes":
                    excerpt, digest = row[columns.index("original_excerpt")], row[columns.index("original_sha256")]
                    if hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != digest:
                        raise ExportError(f"Note {row[0]!r} has an incorrect original SHA-256")
                lines.append(
                    f"INSERT INTO {identifier(table)} ({', '.join(map(identifier, columns))})"
                    f" VALUES ({', '.join(sql_value(value) for value in row)});"
                )
        conn.rollback()
    finally:
        conn.close()
    lines.extend([
        "\nCOMMIT;",
        "SET SESSION sql_mode = @loom_export_previous_sql_mode;",
        "-- Confirm imported table counts before treating migration as complete.",
    ])
    for table, count in counts.items():
        lines.append(
            f"SELECT '{table}' AS table_name, COUNT(*) AS actual_rows, {count} AS expected_rows"
            f" FROM {identifier(table)};"
        )
    content = "\n".join(lines) + "\n"
    # Nothing is written until the entire source has passed conversion checks.
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8", newline="\n")
    return {
        "status": "ok",
        "source": str(db_path),
        "output": str(output),
        "row_counts": counts,
        "total_rows": sum(counts.values()),
        "sql_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "mysql_executed": False,
        "mysql_version_required": "8.0.29+",
        "limitations": (
            "SQL generated and locally checked only; import requires empty tables. "
            "Indexed URL/ID/tag length bounds are enforced; schema is not a live MySQL migration."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(export_database(args.db, args.output), ensure_ascii=False, indent=2))
        return 0
    except (ExportError, OSError, sqlite3.Error, UnicodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
