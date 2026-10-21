#!/usr/bin/env python3
"""Append-only source/excerpt checks; no changes to collected data or labels."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

from manage import connect, valid_url, required_text, DataError, ROOT, DEFAULT_DB


CONTENT_FIELDS = (
    "checked_on", "checker_type", "fetch_status", "excerpt_status",
    "author_check", "license_check", "license_evidence_url", "notes", "method",
)
CHOICES = {
    "checker_type": {"ai_assisted", "human"},
    "fetch_status": {"accessible", "blocked", "unavailable"},
    "excerpt_status": {"exact", "normalized_match", "not_found", "not_checked"},
    "author_check": {"confirmed", "conflict", "not_checked"},
    "license_check": {"confirmed", "not_confirmed", "conflict", "not_checked"},
    "method": {"web_reader", "human_browser"},
}


def today_shanghai() -> date:
    return datetime.now(timezone(timedelta(hours=8))).date()


def validate_audit(raw: Any) -> dict[str, Any]:
    """Validate one check independently of SQLite and discard export-only fields."""
    if not isinstance(raw, dict):
        raise DataError("每条来源核查必须是 JSON 对象")
    rec = {key: required_text(raw, key) for key in (
        "id", "source_url", "checked_on", "checker_type", "fetch_status",
        "excerpt_status", "author_check", "license_check", "notes", "method",
    )}
    if not valid_url(rec["source_url"]):
        raise DataError("source_url 必须是无账号密码的 HTTP(S) 地址")
    try:
        checked = date.fromisoformat(rec["checked_on"])
        if checked.isoformat() != rec["checked_on"] or checked > today_shanghai():
            raise ValueError
    except ValueError as exc:
        raise DataError("checked_on 必须是合法 YYYY-MM-DD 日期且不能晚于上海今天") from exc
    for key, allowed in CHOICES.items():
        if rec[key] not in allowed:
            raise DataError(f"{key} 不支持值 {rec[key]!r}")
    if rec["fetch_status"] != "accessible" and (
        rec["excerpt_status"] != "not_checked" or rec["author_check"] != "not_checked"
    ):
        raise DataError("blocked/unavailable 页面的摘录和作者必须为 not_checked")
    evidence = raw.get("license_evidence_url")
    if evidence is not None and (not isinstance(evidence, str) or not valid_url(evidence)):
        raise DataError("license_evidence_url 必须是有效 HTTP(S) 地址或 null")
    if rec["license_check"] in {"confirmed", "conflict"} and not evidence:
        raise DataError("confirmed/conflict 许可必须提供 license_evidence_url")
    rec["license_evidence_url"] = evidence
    if "note_id" in raw:
        rec["note_id"] = required_text(raw, "note_id")
    if "source_id" in raw:
        source_id = raw["source_id"]
        if isinstance(source_id, bool) or not isinstance(source_id, int) or source_id <= 0:
            raise DataError("source_id 必须是正整数")
        rec["source_id"] = source_id
    if raw.get("created_at") is not None:
        stamp = required_text(raw, "created_at")
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
        except ValueError as exc:
            raise DataError("created_at 必须是带时区的 ISO 日期时间") from exc
        rec["created_at"] = stamp
    return rec


def read_audits(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DataError(f"来源核查文件不是有效 UTF-8 JSON：{exc}") from exc
    if isinstance(raw, dict):
        raw = raw.get("checks")
    if not isinstance(raw, list) or not raw:
        raise DataError("来源核查文件须为非空数组或含 checks 数组的对象")
    records = []
    for index, item in enumerate(raw, 1):
        try:
            records.append(validate_audit(item))
        except DataError as exc:
            raise DataError(f"第 {index} 条来源核查：{exc}") from exc
    return records


def import_audits(db: Path, path: Path) -> dict[str, Any]:
    records = read_audits(path)
    inserted = skipped = 0
    try:
        with connect(db) as conn:
            conn.execute("BEGIN IMMEDIATE")
            for rec in records:
                source = conn.execute("SELECT id FROM sources WHERE url=?",
                                      (rec["source_url"],)).fetchone()
                if source is None:
                    raise DataError(f"未知 source_url：{rec['source_url']}")
                source_id = source["id"]
                if rec.get("source_id", source_id) != source_id:
                    raise DataError(f"{rec['id']} 的 source_id 与 source_url 不一致")
                if "note_id" in rec:
                    note = conn.execute("SELECT source_id FROM notes WHERE id=?",
                                        (rec["note_id"],)).fetchone()
                    if note is None or note["source_id"] != source_id:
                        raise DataError(f"{rec['id']} 的 note_id 与来源不一致")
                    note_id = rec["note_id"]
                else:
                    candidates = conn.execute(
                        "SELECT id FROM notes WHERE source_id=? ORDER BY id LIMIT 2", (source_id,)
                    ).fetchall()
                    if len(candidates) != 1:
                        raise DataError(f"{rec['id']} 来源关联零条或多条笔记，必须提供 note_id")
                    note_id = candidates[0]["id"]
                existing = conn.execute("SELECT * FROM source_checks WHERE id=?",
                                        (rec["id"],)).fetchone()
                if existing is not None:
                    if (existing["source_id"] != source_id or existing["note_id"] != note_id) or any(
                        existing[key] != rec[key] for key in CONTENT_FIELDS
                    ):
                        raise DataError(f"{rec['id']} 同 ID 核查内容冲突；请使用新的核查 ID")
                    skipped += 1
                    continue
                columns = ("id", "source_id", "note_id", *CONTENT_FIELDS)
                values = (rec["id"], source_id, note_id, *(rec[key] for key in CONTENT_FIELDS))
                conn.execute(
                    f"INSERT INTO source_checks({','.join(columns)}) "
                    f"VALUES({','.join('?' for _ in columns)})", values,
                )
                inserted += 1
    except sqlite3.Error as exc:
        raise DataError(f"来源核查写入失败：{exc}") from exc
    return {"status": "ok", "inserted": inserted, "skipped": skipped,
            "record_count": len(records), "annotations_modified": False,
            "licenses_modified": False}


def audit_database(db: Path) -> dict[str, Any]:
    with connect(db) as conn:
        checks = []
        for row in conn.execute(
            "SELECT c.*,s.url AS source_url,s.external_author AS author "
            "FROM source_checks c JOIN sources s ON s.id=c.source_id "
            "ORDER BY c.checked_on,c.id"
        ):
            checks.append(dict(row))
    return {"metadata": {
        "project": "Loom source checks", "record_count": len(checks),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "annotation_status_changed": False,
        "scope": "指定笔记摘录的网页来源比对；不传播到同源其他摘录，不是人工语义金标准，不授予训练许可",
    }, "checks": checks}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import")
    importer.add_argument("file", type=Path)
    exporter = commands.add_parser("export")
    exporter.add_argument("--output", type=Path, default=ROOT / "exports" / "source_audit.json")
    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            result = import_audits(args.db, args.file)
        else:
            # Also reject symlinks/hard links to the database.
            same_file = args.output.exists() and args.db.exists() and args.output.samefile(args.db)
            if same_file or args.output.resolve() == args.db.resolve():
                raise DataError("导出文件不能覆盖数据库")
            result = audit_database(args.db)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (DataError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
