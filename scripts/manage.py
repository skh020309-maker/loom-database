#!/usr/bin/env python3
"""Manage Loom's small, source-attributed public excerpt research database.

Only Python's standard library is used. Search is literal SQL LIKE keyword
search. Importing a record does not invoke GLM or establish permission to train.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "loom.db"
SCHEMA = ROOT / "schema" / "sqlite.sql"
REQUIRED_TABLES = {
    "sources", "notes", "annotations", "tags", "note_tags",
    "processing_logs", "import_batches", "source_checks",
}


class DataError(ValueError):
    """A collection or database violates this project's data contract."""


class ManagedConnection(sqlite3.Connection):
    """Commit/rollback and close on context exit, including on Windows."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def connect(db_path: Path, *, create: bool = False) -> sqlite3.Connection:
    db_path = Path(db_path)
    if create:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    elif not db_path.is_file():
        raise DataError(f"数据库不存在：{db_path}。请先运行 init。")
    conn = sqlite3.connect(db_path, factory=ManagedConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not create:
        found = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = REQUIRED_TABLES - found
        if missing:
            conn.close()
            raise DataError(f"数据库缺少表：{', '.join(sorted(missing))}。请先运行 init。")
    return conn


def initialize(db_path: Path) -> dict[str, Any]:
    with connect(db_path, create=True) as conn:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return {"status": "ok", "database": str(Path(db_path).resolve()),
            "message": "研究样本库已初始化；未运行 GLM，默认禁止训练使用。"}


def required_text(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise DataError(f"{key} 必须是非空字符串，且不能含 NUL 字符")
    return value


def valid_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None
                and not any(char.isspace() for char in value))
    except ValueError:
        return False


def normalized_author(author: str) -> str:
    return " ".join(author.split()).casefold()


def validate_record(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DataError("每条记录必须是 JSON 对象")
    rec = dict(raw)
    for key in (
        "id", "title", "author", "url", "source_kind", "language",
        "original_excerpt", "excerpt_locator", "summary_zh", "category",
        "license_status", "retrieved_on", "evidence_note", "split", "content_origin",
    ):
        rec[key] = required_text(rec, key)
    choices = {
        "source_kind": {"public_note"}, "language": {"en", "zh"},
        "category": {"question", "idea", "note", "event"},
        "license_status": {"unknown", "open"}, "split": {"dev", "test"},
        "content_origin": {"public_excerpt"},
    }
    for key, values in choices.items():
        if rec[key] not in values:
            raise DataError(f"{key} 不支持值 {rec[key]!r}，允许：{', '.join(sorted(values))}")
    if len(rec["original_excerpt"]) > 500:
        raise DataError("original_excerpt 超过 500 字符")
    if not valid_url(rec["url"]):
        raise DataError("url 必须是无账号密码的 HTTP(S) 来源地址")
    try:
        if date.fromisoformat(rec["retrieved_on"]).isoformat() != rec["retrieved_on"]:
            raise ValueError
    except ValueError as exc:
        raise DataError("retrieved_on 必须使用 YYYY-MM-DD 日期") from exc
    tags = rec.get("tags")
    if not isinstance(tags, list) or not tags:
        raise DataError("tags 必须是非空字符串数组")
    if any(not isinstance(tag, str) or not tag.strip() or "\x00" in tag for tag in tags):
        raise DataError("tags 不能含空标签或非字符串")
    rec["tags"] = list(dict.fromkeys(tag.strip() for tag in tags))
    for key in ("license_name", "license_url"):
        if rec.get(key) is not None and not isinstance(rec[key], str):
            raise DataError(f"{key} 必须是字符串或 null")
        rec[key] = rec.get(key) or None
    if rec["license_url"] is not None and not valid_url(rec["license_url"]):
        raise DataError("license_url 必须是 HTTP(S) 地址或 null")
    if rec["license_status"] == "open" and not (
        rec["license_name"] and rec["license_url"]
    ):
        raise DataError("open 许可必须同时提供 license_name 和 license_url")
    # These are policy decisions, not permissions inferred from a website/license.
    if rec.get("training_allowed", 0) not in (0, False):
        raise DataError("公开样本导入不授予训练许可；training_allowed 必须为 0")
    if rec.get("owner_user_id") is not None:
        raise DataError("外部作者不能作为 Loom 内部 owner_user_id")
    rec["original_sha256"] = digest(rec["original_excerpt"])
    return rec


def read_seed(path: Path) -> tuple[list[dict[str, Any]], str]:
    contents = Path(path).read_bytes()
    try:
        raw = json.loads(contents.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataError(f"种子文件不是有效 UTF-8 JSON：{exc}") from exc
    if isinstance(raw, dict):
        raw = raw.get("notes")
    if not isinstance(raw, list) or not raw:
        raise DataError("导入文件须为非空记录数组，或含 notes 数组的对象")
    records = []
    for index, item in enumerate(raw, start=1):
        try:
            records.append(validate_record(item))
        except DataError as exc:
            raise DataError(f"第 {index} 条记录：{exc}") from exc
    return records, hashlib.sha256(contents).hexdigest()


def author_split_errors(conn: sqlite3.Connection) -> list[str]:
    author_splits: dict[str, set[str]] = {}
    display_names: dict[str, str] = {}
    for row in conn.execute(
        "SELECT s.external_author, n.split FROM notes n "
        "JOIN sources s ON s.id=n.source_id"
    ):
        key = normalized_author(row["external_author"])
        display_names[key] = row["external_author"]
        author_splits.setdefault(key, set()).add(row["split"])
    return [
        f"作者跨 dev/test 泄漏：{display_names[key]}"
        for key, splits in sorted(author_splits.items()) if len(splits) > 1
    ]


def import_records(db_path: Path, input_path: Path) -> dict[str, Any]:
    records, file_hash = read_seed(input_path)
    inserted, skipped = 0, 0
    conn = connect(db_path)
    try:
        with conn:  # Everything in a batch commits together, or nothing does.
            conn.execute("BEGIN IMMEDIATE")
            batch_id = conn.execute(
                "INSERT INTO import_batches(input_file,input_sha256,record_count,"
                "inserted_count,skipped_count) VALUES(?,?,0,0,0)",
                (Path(input_path).name, file_hash),
            ).lastrowid
            for rec in records:
                existing = conn.execute(
                    "SELECT n.*,s.url,s.external_author FROM notes n "
                    "JOIN sources s ON s.id=n.source_id WHERE n.id=?", (rec["id"],)
                ).fetchone()
                source = conn.execute(
                    "SELECT id,external_author,license_status,license_name,license_url "
                    "FROM sources WHERE url=?", (rec["url"],)
                ).fetchone()
                if source is not None:
                    changed_license_fields = [
                        key for key in ("license_status", "license_name", "license_url")
                        if source[key] != rec[key]
                    ]
                    if changed_license_fields:
                        raise DataError(
                            f"{rec['id']} 同一来源 URL 的许可信息冲突"
                            f"（{', '.join(changed_license_fields)}）；"
                            "请独立复核后更新来源许可，不通过导入静默覆盖。"
                        )
                if existing is not None:
                    if (existing["original_excerpt"] != rec["original_excerpt"]
                            or existing["original_sha256"] != rec["original_sha256"]):
                        raise DataError(f"{rec['id']} 原文不可变：相同 id 的原文已改变")
                    if (existing["url"] != rec["url"]
                            or existing["external_author"] != rec["author"]
                            or existing["excerpt_locator"] != rec["excerpt_locator"]):
                        raise DataError(f"{rec['id']} 来源或定位不可变")
                    if existing["split"] != rec["split"]:
                        raise DataError(f"{rec['id']} 的已有 split 与导入记录不同")
                    # Never overwrite a user's annotations or their tags on reimport.
                    skipped += 1
                    continue
                duplicate = conn.execute(
                    "SELECT id FROM notes WHERE original_sha256=?",
                    (rec["original_sha256"],)
                ).fetchone()
                if duplicate is not None:
                    raise DataError(f"{rec['id']} 原文重复，已存在于 {duplicate['id']}")
                if source is not None and source["external_author"] != rec["author"]:
                    raise DataError(f"{rec['id']} 同一来源 URL 的作者不一致")
                if source is None:
                    source_id = conn.execute(
                        "INSERT INTO sources(url,external_author,source_kind,"
                        "license_status,license_name,license_url) VALUES(?,?,?,?,?,?)",
                        tuple(rec[key] for key in (
                            "url", "author", "source_kind", "license_status",
                            "license_name", "license_url",
                        )),
                    ).lastrowid
                else:
                    source_id = source["id"]
                conn.execute(
                    "INSERT INTO notes(id,source_id,title,language,original_excerpt,"
                    "original_sha256,excerpt_locator,retrieved_on,content_origin,split)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (rec["id"], source_id, *(rec[key] for key in (
                        "title", "language", "original_excerpt", "original_sha256",
                        "excerpt_locator", "retrieved_on", "content_origin", "split",
                    ))),
                )
                conn.execute(
                    "INSERT INTO annotations(note_id,summary_zh,category) VALUES(?,?,?)",
                    (rec["id"], rec["summary_zh"], rec["category"]),
                )
                for tag in rec["tags"]:
                    conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (tag,))
                    conn.execute(
                        "INSERT INTO note_tags(note_id,tag_id) "
                        "SELECT ?,id FROM tags WHERE name=?", (rec["id"], tag),
                    )
                conn.execute(
                    "INSERT INTO processing_logs(note_id,batch_id,process_type,status,"
                    "details) VALUES(?,?,'import','succeeded',?)",
                    (rec["id"], batch_id, json.dumps({
                        "evidence_note": rec["evidence_note"],
                        "message": "仅完成公开摘录入库；摘要为 AI 辅助待人工复核；GLM 未运行",
                        "glm_executed": False,
                    }, ensure_ascii=False)),
                )
                inserted += 1
            errors = author_split_errors(conn)
            if errors:
                raise DataError("; ".join(errors))
            conn.execute(
                "UPDATE import_batches SET record_count=?,inserted_count=?,"
                "skipped_count=? WHERE id=?",
                (len(records), inserted, skipped, batch_id),
            )
    finally:
        conn.close()
    return {"status": "ok", "batch_id": batch_id, "records": len(records),
            "inserted": inserted, "skipped": skipped, "glm_executed": False}


def exported_records(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    records = []
    rows = conn.execute(
        "SELECT n.*,s.url,s.external_author AS author,s.source_kind,"
        "s.license_status,s.license_name,s.license_url,a.summary_zh,a.category,"
        "a.summary_origin,a.review_status,a.pipeline_status,a.reviewer,a.updated_at "
        "FROM notes n JOIN sources s ON s.id=n.source_id "
        "JOIN annotations a ON a.note_id=n.id ORDER BY n.id"
    ).fetchall()
    for row in rows:
        item = dict(row)
        check = conn.execute(
            "SELECT * FROM source_checks WHERE source_id=? AND note_id=? "
            "ORDER BY checked_on DESC,created_at DESC,id DESC LIMIT 1", (item['source_id'],item['id'])
        ).fetchone()
        item["source_check"] = dict(check) if check else None
        if item["source_check"]:
            item["source_check"].pop("source_id")
        item.pop("source_id")
        item["tags"] = [tag[0] for tag in conn.execute(
            "SELECT t.name FROM tags t JOIN note_tags nt ON t.id=nt.tag_id "
            "WHERE nt.note_id=? ORDER BY t.name", (item["id"],)
        )]
        evidence = conn.execute(
            "SELECT details FROM processing_logs WHERE note_id=? "
            "AND process_type='import' ORDER BY id LIMIT 1", (item["id"],)
        ).fetchone()
        item["evidence_note"] = (
            json.loads(evidence[0]).get("evidence_note", "") if evidence else ""
        )
        item["annotation"] = {
            key: item[key] for key in (
                "summary_zh", "category", "summary_origin", "review_status",
                "pipeline_status", "reviewer", "updated_at",
            )
        }
        records.append(item)
    return records


def check_database(db_path: Path) -> dict[str, Any]:
    conn = connect(db_path)
    errors: list[str] = []
    try:
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            errors.extend(f"SQLite integrity: {message}" for message in integrity)
        errors.extend(f"外键错误：{tuple(row)}" for row in conn.execute(
            "PRAGMA foreign_key_check"))
        rows = conn.execute(
            "SELECT n.*,s.url,s.external_author FROM notes n "
            "LEFT JOIN sources s ON n.source_id=s.id"
        ).fetchall()
        for note in rows:
            label = note["id"]
            raw = note["original_excerpt"]
            if not isinstance(raw, str) or not raw.strip() or len(raw) > 500 or "\x00" in raw:
                errors.append(f"{label}：原文为空、超长或包含非法字符")
            if not isinstance(raw, str) or digest(raw) != note["original_sha256"]:
                errors.append(f"{label}：原文 SHA-256 不一致")
            if not note["url"] or not valid_url(note["url"]):
                errors.append(f"{label}：来源缺失或 URL 无效")
            if not note["external_author"] or not note["external_author"].strip():
                errors.append(f"{label}：外部作者缺失")
            if not note["excerpt_locator"] or not note["excerpt_locator"].strip():
                errors.append(f"{label}：摘录定位缺失")
            try:
                if date.fromisoformat(note["retrieved_on"]).isoformat() != note["retrieved_on"]:
                    raise ValueError
            except (ValueError, TypeError):
                errors.append(f"{label}：采集日期无效")
            if note["content_origin"] != "public_excerpt":
                errors.append(f"{label}：内容来源类型无效")
            if note["split"] not in {"dev", "test"}:
                errors.append(f"{label}：split 无效")
            if note["owner_user_id"] is not None:
                errors.append(f"{label}：公开样本误设内部 owner_user_id")
            if note["training_allowed"] != 0:
                errors.append(f"{label}：研究样本库未授予训练许可")
            annotation = conn.execute(
                "SELECT * FROM annotations WHERE note_id=?", (label,)
            ).fetchone()
            evidence = conn.execute(
                "SELECT details FROM processing_logs WHERE note_id=? "
                "AND process_type='import' AND status='succeeded' ORDER BY id LIMIT 1",
                (label,),
            ).fetchone()
            try:
                details = json.loads(evidence["details"]) if evidence else {}
                evidence_note = details.get("evidence_note")
                if not isinstance(evidence_note, str) or not evidence_note.strip():
                    raise ValueError("missing evidence")
            except (ValueError, TypeError, AttributeError):
                errors.append(f"{label}：缺少有效采集证据记录 evidence_note")
            if annotation is None:
                errors.append(f"{label}：缺少标注")
            elif (annotation["review_status"] == "verified"
                    and not (annotation["reviewer"] or "").strip()):
                errors.append(f"{label}：人工核验未填写核验者")
            elif annotation["pipeline_status"] == "completed":
                count = conn.execute(
                    "SELECT count(*) FROM processing_logs WHERE note_id=? "
                    "AND process_type='glm' AND status='succeeded' "
                    "AND provider IS NOT NULL AND model IS NOT NULL", (label,)
                ).fetchone()[0]
                if not count:
                    errors.append(f"{label}：流水线完成状态缺少真实 GLM 执行记录")
        errors.extend(author_split_errors(conn))
        # Imports validate these records too; recheck detects direct SQL corruption.
        from source_audits import validate_audit
        source_checks = conn.execute(
            "SELECT c.*,s.url AS source_url FROM source_checks c "
            "JOIN sources s ON s.id=c.source_id "
            "JOIN notes n ON n.id=c.note_id AND n.source_id=c.source_id"
        ).fetchall()
        for source_check in source_checks:
            try:
                validate_audit(dict(source_check))
            except ValueError as exc:
                errors.append(f"{source_check['id']}：来源核查无效：{exc}")
        return {"status": "ok" if not errors else "failed",
                "notes_checked": len(rows), "errors": errors,
                "source_checks_checked": len(source_checks),
                "checks": ["integrity", "foreign_keys", "sha256", "excerpt_length",
                           "source_provenance", "content_origin", "annotation_state",
                           "evidence_note", "author_split_leakage", "training_permission",
                           "source_check_contract"],
                "scope_note": "离线结构校验；不代替网页原文比对或人工语义核验。"}
    finally:
        conn.close()


def csv_safe(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        # Spreadsheet applications otherwise interpret scraped text as formulas.
        return "'" + value
    return value


SUMMARY_ORIGIN_LABELS = {"ai_assisted": "AI 辅助", "human": "人工撰写"}
REVIEW_STATUS_LABELS = {"pending": "待人工复核", "verified": "已人工复核", "rejected": "已驳回"}


def export_database(db_path: Path, fmt: str, output: Path | None) -> dict[str, Any]:
    check = check_database(db_path)
    if check["errors"]:
        raise DataError("拒绝导出校验失败的数据库：" + "; ".join(check["errors"]))
    with connect(db_path) as conn:
        notes = exported_records(conn)
    suffix = {"json": "json", "csv": "csv", "markdown": "md"}[fmt]
    output = Path(output) if output else ROOT / "exports" / f"notes.{suffix}"
    if output.resolve() == Path(db_path).resolve():
        raise DataError("导出路径不能覆盖数据库")
    origin_counts = {key: sum(note["summary_origin"] == key for note in notes)
                     for key in SUMMARY_ORIGIN_LABELS}
    review_counts = {key: sum(note["review_status"] == key for note in notes)
                     for key in REVIEW_STATUS_LABELS}
    summary_status = (
        "摘要来源：" + "、".join(f"{label} {origin_counts[key]} 条"
                              for key, label in SUMMARY_ORIGIN_LABELS.items())
        + "；复核状态：" + "、".join(f"{label} {review_counts[key]} 条"
                                  for key, label in REVIEW_STATUS_LABELS.items())
        + "。"
    )
    metadata = {
        "project": "Loom public excerpt research seed",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(notes), "training_allowed": False,
        "summary_status": summary_status,
        "summary_origin_counts": origin_counts,
        "review_status_counts": review_counts,
        "source_checks_coverage": sum(note['source_check'] is not None for note in notes),
        "source_check_note": "网页来源核查与摘要人工复核独立；许可确认不等于训练授权。",
        "search_method": "SQL LIKE literal keyword search",
        "glm_executed_by_import": False,
        "coverage": "公开文字摘录；不是学生真实输入；未覆盖语音或 OCR 评测",
    }
    if fmt == "json":
        content = json.dumps({"metadata": metadata, "notes": notes},
                             ensure_ascii=False, indent=2) + "\n"
        encoding = "utf-8"
    elif fmt == "csv":
        fields = [
            "id", "title", "author", "url", "source_kind", "language",
            "original_excerpt", "original_sha256", "excerpt_locator", "summary_zh",
            "tags", "category", "license_status", "license_name", "license_url",
            "retrieved_on", "evidence_note", "split", "content_origin",
            "training_allowed", "owner_user_id", "summary_origin", "review_status",
            "pipeline_status", "reviewer", "source_check",
        ]
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for item in notes:
            line = {key: csv_safe(value) for key, value in item.items()}
            line["tags"] = json.dumps(item["tags"], ensure_ascii=False)
            line["source_check"] = json.dumps(item["source_check"], ensure_ascii=False)
            writer.writerow(line)
        content, encoding = buffer.getvalue(), "utf-8-sig"
    else:
        lines = [
            "# Loom 公开笔记研究样本", "",
            "公开文字摘录；不是学生真实输入；默认禁止训练使用。",
            summary_status,
            "导入过程未运行 GLM；逐条复核状态和流水线状态见下方记录。",
            "本批未覆盖语音或 OCR 评测。", "",
        ]
        for item in notes:
            title = item["title"].replace("\n", " ")
            lines.extend([
                f"## {item['id']} · {title}", "",
                f"- 外部作者：{item['author']}",
                f"- 来源：<{item['url']}>",
                f"- 原文定位：{item['excerpt_locator']}",
                f"- 采集日期：{item['retrieved_on']}",
                f"- 分类：{item['category']}；分组：{item['split']}",
                f"- 标签：{'、'.join(item['tags'])}",
                f"- 许可：{item['license_status']} / {item['license_name'] or '未确认'}",
                f"- 许可链接：{'<' + item['license_url'] + '>' if item['license_url'] else '未确认'}",
                f"- 核验状态：{item['review_status']}；流水线：{item['pipeline_status']}",
                f"- SHA-256：`{item['original_sha256']}`", "", "**原文摘录**", "",
                *["> " + part for part in item["original_excerpt"].splitlines()],
                "", f"**中文摘要（{SUMMARY_ORIGIN_LABELS[item['summary_origin']]}，"
                    f"{REVIEW_STATUS_LABELS[item['review_status']]}）**", "",
                item["summary_zh"], "",
                f"采集证据：{item['evidence_note']}", "",
            ])
            check = item['source_check']
            if check:
                lines.extend([
                    f"来源再核查：{check['checked_on']} / {check['checker_type']} / "
                    f"访问 {check['fetch_status']} / 摘录 {check['excerpt_status']} / "
                    f"作者 {check['author_check']} / 许可 {check['license_check']}。",
                    check['notes'],
                    f"许可证据：<{check['license_evidence_url']}>" if check['license_evidence_url'] else "许可证据：未确认",
                    "",
                ])
        content, encoding = "\n".join(lines) + "\n", "utf-8"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding=encoding, newline="")
    return {"status": "ok", "format": fmt, "records": len(notes),
            "output": str(output.resolve()),
            "csv_note": "CSV 使用 UTF-8 BOM；潜在公式单元格加前缀，完整原文见 JSON。"
                        if fmt == "csv" else None}


def search_database(db_path: Path, keyword: str, limit: int = 20) -> dict[str, Any]:
    if not keyword.strip() or not 1 <= limit <= 500:
        raise DataError("keyword 不能为空，limit 须为 1–500")
    # Parameters prevent SQL injection; escaping makes '%' and '_' literal.
    pattern = "%" + keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with connect(db_path) as conn:
        results = [dict(row) for row in conn.execute(
            "SELECT DISTINCT n.id,n.title,s.external_author AS author,s.url,"
            "a.summary_zh,a.category,n.split,a.review_status FROM notes n "
            "JOIN sources s ON s.id=n.source_id "
            "JOIN annotations a ON a.note_id=n.id "
            "WHERE n.title LIKE ? ESCAPE '\\' "
            "OR n.original_excerpt LIKE ? ESCAPE '\\' "
            "OR a.summary_zh LIKE ? ESCAPE '\\' "
            "OR s.external_author LIKE ? ESCAPE '\\' "
            "OR EXISTS(SELECT 1 FROM note_tags nt JOIN tags t ON t.id=nt.tag_id "
            "WHERE nt.note_id=n.id AND t.name LIKE ? ESCAPE '\\') "
            "ORDER BY n.id LIMIT ?", (pattern, pattern, pattern, pattern, pattern, limit)
        )]
    return {"search_method": "SQL LIKE literal keyword search（非向量或语义检索）",
            "keyword": keyword, "returned": len(results), "limit": limit,
            "results": results}


def stats_database(db_path: Path) -> dict[str, Any]:
    with connect(db_path) as conn:
        totals = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in sorted(REQUIRED_TABLES)
        }
        totals["external_authors"] = len({
            normalized_author(row[0]) for row in conn.execute(
                "SELECT DISTINCT external_author FROM sources")
        })
        by_split = dict(conn.execute("SELECT split,count(*) FROM notes GROUP BY split"))
        by_category = dict(conn.execute(
            "SELECT category,count(*) FROM annotations GROUP BY category"))
        by_review = dict(conn.execute(
            "SELECT review_status,count(*) FROM annotations GROUP BY review_status"))
        glm_successes = conn.execute(
            "SELECT count(*) FROM processing_logs WHERE process_type='glm' "
            "AND status='succeeded'"
        ).fetchone()[0]
    return {"database": str(Path(db_path).resolve()), "totals": totals,
            "by_split": by_split, "by_category": by_category,
            "by_review_status": by_review, "recorded_glm_successes": glm_successes,
            "scope_note": "公开摘录研究种子；外部作者不是内部用户；训练默认禁止。"}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help="SQLite 路径（默认 loom-database/loom.db）")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="创建表结构")
    importer = commands.add_parser("import", help="事务导入公开摘录 JSON")
    importer.add_argument("input", type=Path)
    exporter = commands.add_parser("export", help="导出 JSON/UTF-8 BOM CSV/Markdown")
    exporter.add_argument("--format", choices=("json", "csv", "markdown"), default="json")
    exporter.add_argument("--output", type=Path)
    commands.add_parser("check", help="校验完整性、哈希、来源和作者分组")
    searcher = commands.add_parser("search", help="字面关键词 SQL LIKE 检索")
    searcher.add_argument("keyword")
    searcher.add_argument("--limit", type=int, default=20)
    commands.add_parser("stats", help="统计样本、标签、状态与执行记录")
    return result


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        if args.command == "init":
            result = initialize(args.db)
        elif args.command == "import":
            result = import_records(args.db, args.input)
        elif args.command == "export":
            result = export_database(args.db, args.format, args.output)
        elif args.command == "check":
            result = check_database(args.db)
        elif args.command == "search":
            result = search_database(args.db, args.keyword, args.limit)
        else:
            result = stats_database(args.db)
        emit(result)
        return 0 if result.get("status") != "failed" else 1
    except (DataError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "error", "error": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
