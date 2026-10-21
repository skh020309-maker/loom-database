#!/usr/bin/env python3
"""Atomically revise pending AI-assisted summaries with immutable-source guards."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any

import manage


FIELDS = {"note_id", "original_sha256", "before_summary", "after_summary", "reason"}
ACTION = "apply_annotation_revision"
HASH_METHOD = ("SHA-256 of validated records sorted by note_id, canonical JSON "
               "sort_keys=True/separators=(',',':')/ensure_ascii=False, UTF-8 without BOM; "
               "formatting, LF/CRLF outside strings and record order do not affect the hash")


def read_revisions(path: Path) -> tuple[list[dict[str, str]], str, str]:
    """Canonical batch identity plus original-byte evidence; text stays exact."""
    contents = Path(path).read_bytes()
    try:
        raw = json.loads(contents.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise manage.DataError(f"修订文件不是有效 UTF-8 JSON：{exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise manage.DataError("修订文件必须是非空 JSON 数组")
    records, seen = [], set()
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict) or set(item) != FIELDS:
            raise manage.DataError(f"第 {index} 条仅允许且必须包含：{', '.join(sorted(FIELDS))}")
        rec = {key: manage.required_text(item, key) for key in sorted(FIELDS)}
        if rec["before_summary"] == rec["after_summary"]:
            raise manage.DataError(f"第 {index} 条 before_summary 与 after_summary 相同，没有实际修订")
        if not re.fullmatch(r"[0-9a-f]{64}", rec["original_sha256"]):
            raise manage.DataError(f"第 {index} 条 original_sha256 须为 64 位小写十六进制")
        if rec["note_id"] in seen:
            raise manage.DataError(f"同一修订文件重复 note_id：{rec['note_id']}")
        seen.add(rec["note_id"])
        records.append(rec)
    canonical = json.dumps(sorted(records, key=lambda rec: rec["note_id"]),
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return (records, hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            hashlib.sha256(contents).hexdigest())


def matching_success(conn: sqlite3.Connection, rec: dict[str, str], file_hash: str) -> int | None:
    """A matching summary alone is not proof that this revision was applied."""
    rows = conn.execute(
        "SELECT id,details FROM processing_logs WHERE note_id=? "
        "AND process_type='annotation' AND status='succeeded' ORDER BY id DESC",
        (rec["note_id"],),
    )
    for row in rows:
        try:
            detail = json.loads(row["details"])
        except (TypeError, ValueError):
            continue
        if not isinstance(detail, dict):
            continue
        before, after = detail.get("before"), detail.get("after")
        if (detail.get("action") == ACTION and detail.get("actor") == "Codex"
                and detail.get("actor_origin") == "ai_assisted"
                and detail.get("source_revision_sha256") == file_hash
                and isinstance(before, dict) and before.get("summary_zh") == rec["before_summary"]
                and isinstance(after, dict) and after.get("summary_zh") == rec["after_summary"]
                and detail.get("original_sha256") == rec["original_sha256"]
                and detail.get("original_modified") is False
                and detail.get("glm_executed") is False):
            return row["id"]
    return None


def apply_revisions(db: Path, revision_file: Path, *, dry_run: bool = False) -> dict[str, Any]:
    """Validate the complete batch under one write lock, then update summaries only."""
    revisions, file_hash, input_hash = read_revisions(revision_file)
    planned, skipped = [], []
    log_ids = []
    with manage.connect(db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        # Complete validation precedes all writes; the transaction also rolls back
        # if any later update/log insert fails unexpectedly.
        for rec in revisions:
            row = conn.execute(
                "SELECT a.*,n.original_excerpt,n.original_sha256 FROM notes n "
                "JOIN annotations a ON a.note_id=n.id WHERE n.id=?", (rec["note_id"],),
            ).fetchone()
            if row is None:
                raise manage.DataError(f"笔记不存在或缺少标注：{rec['note_id']}")
            if (row["original_sha256"] != rec["original_sha256"]
                    or manage.digest(row["original_excerpt"]) != rec["original_sha256"]):
                raise manage.DataError(f"{rec['note_id']} 原文哈希不一致，拒绝修订")
            # Run protection before the idempotence branch too: a later human
            # decision must never be erased by a replay of an older AI batch.
            if (row["summary_origin"] != "ai_assisted" or row["review_status"] != "pending"
                    or row["reviewer"] not in (None, "")):
                raise manage.DataError(f"{rec['note_id']} 含人工来源、人工复核状态或核验者，拒绝覆盖")
            prior_log = matching_success(conn, rec, file_hash)
            if row["summary_zh"] == rec["after_summary"] and prior_log is not None:
                skipped.append({"note_id": rec["note_id"], "existing_log_id": prior_log})
                continue
            if row["summary_zh"] != rec["before_summary"]:
                raise manage.DataError(f"{rec['note_id']} 当前摘要不等于 before_summary，整批取消")
            planned.append(rec)

        if not dry_run:
            now = datetime.now(timezone.utc).isoformat()
            for rec in planned:
                # Category, tags, original/source fields and pipeline_status are
                # deliberately absent from this UPDATE statement.
                conn.execute(
                    "UPDATE annotations SET summary_zh=?,summary_origin='ai_assisted',"
                    "review_status='pending',reviewer=NULL,updated_at=? WHERE note_id=?",
                    (rec["after_summary"], now, rec["note_id"]),
                )
                detail = {
                    "action": ACTION, "actor": "Codex", "actor_origin": "ai_assisted",
                    "reason": rec["reason"],
                    "before": {"summary_zh": rec["before_summary"]},
                    "after": {"summary_zh": rec["after_summary"]},
                    "source_revision_file": Path(revision_file).name,
                    "source_revision_sha256": file_hash,
                    "input_file_sha256": input_hash,
                    "source_revision_hash_method": HASH_METHOD,
                    "original_sha256": rec["original_sha256"],
                    "original_modified": False, "glm_executed": False,
                }
                log_ids.append(conn.execute(
                    "INSERT INTO processing_logs(note_id,process_type,status,details) "
                    "VALUES(?,'annotation','succeeded',?)",
                    (rec["note_id"], json.dumps(detail, ensure_ascii=False)),
                ).lastrowid)
        else:
            conn.rollback()
    return {"status": "ok", "dry_run": dry_run, "records": len(revisions),
            "planned": len(planned), "applied": 0 if dry_run else len(planned),
            "skipped": len(skipped), "revised_note_ids": [rec["note_id"] for rec in planned],
            "skipped_notes": skipped, "log_ids": log_ids,
            "source_revision_file": Path(revision_file).name,
            "source_revision_sha256": file_hash, "input_file_sha256": input_hash,
            "source_revision_hash_method": HASH_METHOD,
            "original_modified": False, "glm_executed": False,
            "scope": "仅 AI 辅助摘要修订，保持待人工复核；不改变分类、标签或 GLM 管线状态。"}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=manage.DEFAULT_DB)
    parser.add_argument("file", type=Path, help="JSON 修订数组")
    parser.add_argument("--dry-run", action="store_true", help="完整校验但不写摘要或日志")
    args = parser.parse_args(argv)
    try:
        manage.emit(apply_revisions(args.db, args.file, dry_run=args.dry_run))
        return 0
    except (manage.DataError, OSError, sqlite3.Error, TypeError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
