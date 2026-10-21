#!/usr/bin/env python3
"""Inspect and explicitly review annotations while keeping source text immutable."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

import manage


def show_note(db: Path, note_id: str) -> dict:
    with manage.connect(db) as conn:
        records = manage.exported_records(conn)
    for record in records:
        if record["id"] == note_id:
            return record
    raise manage.DataError(f"笔记不存在：{note_id}")


def update_annotation(db: Path, note_id: str, *, actor: str, origin: str,
                      reason: str, status: str, summary: str | None = None,
                      category: str | None = None, tags: list[str] | None = None) -> dict:
    if origin not in {"human", "ai_assisted"}:
        raise manage.DataError("origin 必须是 human 或 ai_assisted")
    if status not in {"pending", "verified", "rejected"}:
        raise manage.DataError("status 必须是 pending、verified 或 rejected")
    if not isinstance(actor, str) or not actor.strip():
        raise manage.DataError("必须填写实际操作者 actor，可使用组内代号")
    if not isinstance(reason, str) or not reason.strip():
        raise manage.DataError("必须填写修改或复核原因 reason")
    if status in {"verified", "rejected"} and origin != "human":
        raise manage.DataError("只有实际人工复核可确认 verified/rejected；AI 建议保持 pending")
    if summary is not None and (not isinstance(summary, str) or not summary.strip()
                                or "\x00" in summary):
        raise manage.DataError("摘要须为非空文本且不能含 NUL")
    if category is not None and category not in {"question", "idea", "note", "event"}:
        raise manage.DataError("category 无效")
    if tags is not None:
        if (not isinstance(tags, list) or not tags
                or any(not isinstance(t, str) or not t.strip() or "\x00" in t for t in tags)):
            raise manage.DataError("tags 必须是非空文本数组")
        tags = sorted(set(t.strip() for t in tags))

    with manage.connect(db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM annotations WHERE note_id=?", (note_id,)).fetchone()
        if row is None:
            raise manage.DataError(f"笔记不存在或缺少标注：{note_id}")
        old_tags = [r[0] for r in conn.execute(
            "SELECT t.name FROM tags t JOIN note_tags nt ON nt.tag_id=t.id "
            "WHERE nt.note_id=? ORDER BY t.name", (note_id,))]
        keys = ("summary_zh", "category", "summary_origin", "review_status", "reviewer")
        before = {key: row[key] for key in keys}
        before["tags"] = old_tags
        after = {
            "summary_zh": summary if summary is not None else row["summary_zh"],
            "category": category if category is not None else row["category"],
            "summary_origin": origin if summary is not None else row["summary_origin"],
            "review_status": status,
            "reviewer": actor.strip() if status in {"verified", "rejected"} else None,
            "tags": tags if tags is not None else old_tags,
        }
        if before == after:
            return {"status": "ok", "note_id": note_id, "changed": False,
                    "message": "内容与状态相同；没有新增重复日志。"}
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE annotations SET summary_zh=?,category=?,summary_origin=?,"
            "review_status=?,reviewer=?,updated_at=? WHERE note_id=?",
            tuple(after[k] for k in keys) + (now, note_id))
        if tags is not None:
            conn.execute("DELETE FROM note_tags WHERE note_id=?", (note_id,))
            for tag in tags:
                conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (tag,))
                conn.execute("INSERT INTO note_tags(note_id,tag_id) SELECT ?,id FROM tags WHERE name=?",
                             (note_id, tag))
        details = {
            "action": "review_annotation", "actor": actor.strip(), "actor_origin": origin,
            "reason": reason.strip(), "before": before, "after": after,
            "original_modified": False, "glm_executed": False,
        }
        log_id = conn.execute(
            "INSERT INTO processing_logs(note_id,process_type,status,details) "
            "VALUES(?,'annotation','succeeded',?)",
            (note_id, json.dumps(details, ensure_ascii=False))).lastrowid
    return {"status": "ok", "note_id": note_id, "changed": True, "log_id": log_id,
            "annotation": after, "glm_executed": False}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=manage.DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("show", help="查看原文、来源、标签和当前标注")
    show.add_argument("note_id")
    edit = sub.add_parser("update", help="显式修改候选标注并保留前后差异")
    edit.add_argument("note_id")
    edit.add_argument("--actor", required=True)
    edit.add_argument("--origin", choices=("human", "ai_assisted"), required=True)
    edit.add_argument("--reason", required=True)
    edit.add_argument("--status", choices=("pending", "verified", "rejected"), required=True)
    edit.add_argument("--summary")
    edit.add_argument("--category", choices=("question", "idea", "note", "event"))
    edit.add_argument("--tag", action="append", dest="tags", help="可重复；提供时替换整组标签")
    args = parser.parse_args(argv)
    try:
        if args.command == "show":
            result = show_note(args.db, args.note_id)
        else:
            result = update_annotation(args.db, args.note_id, actor=args.actor,
                origin=args.origin, reason=args.reason, status=args.status,
                summary=args.summary, category=args.category, tags=args.tags)
        manage.emit(result)
        return 0
    except (manage.DataError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
