#!/usr/bin/env python3
"""Read-only, standard-library data quality audit for Loom public excerpts."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from itertools import combinations
import json
import os
from pathlib import Path
import re
import sqlite3
import statistics
import sys
import tempfile
import unicodedata
from urllib.parse import urlsplit, urlunsplit

import manage


ROOT = Path(__file__).resolve().parents[1]
INLINE_CODE = re.compile(r"(?<![\\`])`([^`\n]+?)(?<!\\)`(?!`)")
REQUIRED_TEXT = (
    "id", "title", "author", "url", "source_kind", "language",
    "original_excerpt", "excerpt_locator", "summary_zh", "category",
    "license_status", "retrieved_on", "evidence_note", "split", "content_origin",
    "summary_origin", "review_status", "pipeline_status",
)


def normalize_text(value: str) -> str:
    """Comparison key only: NFKC, unambiguous single-backtick code, whitespace."""
    value = unicodedata.normalize("NFKC", value)
    return " ".join(INLINE_CODE.sub(r"\1", value).split())


def author_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def document_key(value: str) -> str:
    """Ignore fragment locators; retain path, query, and HTTP vs HTTPS."""
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(),
                       parsed.path, parsed.query, ""))


def duplicate_groups(records: list[dict], key) -> list[dict]:
    groups = defaultdict(list)
    for row in records:
        value = key(row)
        if value:
            groups[value].append(row)
    return [{"ids": [row["id"] for row in rows],
             "splits": sorted({row.get("split", "") for row in rows})}
            for rows in groups.values() if len(rows) > 1]


def audit_records(records: list[dict], *, threshold: float = 0.65,
                  min_length: int = 30) -> dict:
    """Audit exported records without editing original excerpts or annotations."""
    if not 0 < threshold <= 1 or min_length < 3:
        raise manage.DataError("threshold 必须在 (0,1]；min-length 必须至少为 3")
    critical, warnings = [], []
    usable = []
    for index, row in enumerate(records):
        label = row.get("id") or f"<row:{index + 1}>"
        missing = [key for key in REQUIRED_TEXT if not isinstance(row.get(key), str)
                   or not row[key].strip()]
        if missing:
            critical.append({"type": "missing_fields", "ids": [label],
                             "fields": missing})
        try:
            manage.validate_record(row)
        except (manage.DataError, TypeError) as exc:
            critical.append({"type": "record_contract", "ids": [label],
                             "message": str(exc)})
        if row.get("review_status") == "verified" and not (
            isinstance(row.get("reviewer"), str) and row["reviewer"].strip()
        ):
            critical.append({"type": "verified_without_reviewer", "ids": [label]})
        if isinstance(row.get("original_excerpt"), str) and row.get("id"):
            usable.append(row)
            if row.get("original_sha256") != manage.digest(row["original_excerpt"]):
                critical.append({"type": "sha256_mismatch", "ids": [label]})

    ids = duplicate_groups(usable, lambda row: row["id"])
    exact = duplicate_groups(usable, lambda row: row["original_excerpt"])
    normalized = duplicate_groups(usable, lambda row: normalize_text(row["original_excerpt"]))
    normalized_only = [group for group in normalized if len({
        row["original_excerpt"] for row in usable if row["id"] in group["ids"]
    }) > 1]
    for kind, groups in (("duplicate_id", ids), ("exact_original_duplicate", exact)):
        for group in groups:
            critical.append({"type": kind, **group})
    for group in normalized_only:
        warnings.append({"type": "normalized_duplicate_candidate", **group,
                         "message": "仅比较键一致；需人工判定，不改写或删除原文。"})

    def grouped(field, key):
        groups = defaultdict(list)
        for row in records:
            if isinstance(row.get(field), str) and row[field].strip():
                try:
                    value = key(row[field])
                except ValueError:
                    continue
                groups[value].append(row)
        return [{"key": value, "ids": [row.get("id") for row in rows],
                 "splits": sorted({row.get("split", "") for row in rows})}
                for value, rows in sorted(groups.items())]

    author_groups = grouped("author", author_key)
    url_groups = grouped("url", lambda value: value)
    document_groups = grouped("url", document_key)
    for kind, groups in (("author_split_leakage", author_groups),
                         ("source_document_split_leakage", document_groups)):
        for group in groups:
            if {"dev", "test"}.issubset(group["splits"]):
                critical.append({"type": kind, **group})
    for group in url_groups:
        authors = {author_key(row["author"]) for row in records
                   if row.get("url") == group["key"]
                   and isinstance(row.get("author"), str)}
        if len(authors) > 1:
            critical.append({"type": "source_url_author_conflict", **group})

    grams = []
    excluded = []
    for row in usable:
        normalized_text = normalize_text(row["original_excerpt"])
        compact = "".join(normalized_text.split())
        if len(compact) < min_length:
            excluded.append(row["id"])
            continue
        grams.append((row, normalized_text, {compact[i:i + 3]
                      for i in range(len(compact) - 2)}))
    near = []
    for left, right in combinations(grams, 2):
        if left[1] == right[1]:  # Already reported as exact/normalized equality.
            continue
        score = len(left[2] & right[2]) / len(left[2] | right[2])
        if score >= threshold:
            near.append({"ids": [left[0]["id"], right[0]["id"]],
                         "jaccard": round(score, 6),
                         "splits": [left[0].get("split"), right[0].get("split")]})
    near.sort(key=lambda item: (-item["jaccard"], item["ids"]))
    if near:
        warnings.append({"type": "near_duplicate_candidates", "count": len(near),
                         "ids": sorted({note_id for item in near for note_id in item["ids"]}),
                         "message": "字符 3-gram 相似候选；不是语义去重结论。"})

    def counts(field):
        return dict(sorted(Counter(str(row.get(field) or "<missing>")
                                   for row in records).items()))

    def ids_where(field, value):
        return [row.get("id") for row in records if row.get(field) == value]

    pending = ids_where("review_status", "pending")
    unknown = ids_where("license_status", "unknown")
    if pending:
        warnings.append({"type": "pending_human_review", "ids": pending,
                         "message": "摘要、分类和标签待人工复核，不计为 gold。"})
    if unknown:
        warnings.append({"type": "unknown_license", "ids": unknown,
                         "message": "许可未确认；公开可访问不代表全文或训练授权。"})
    lengths = {row["id"]: len(row["original_excerpt"]) for row in usable}
    short = [note_id for note_id, length in lengths.items() if length < min_length]
    if short:
        warnings.append({"type": "short_excerpt", "ids": short,
                         "message": "长度是覆盖描述，不表示内容错误；短文本不适合近重复推断。"})
    verified = [row.get("id") for row in records
                if row.get("review_status") == "verified"
                and isinstance(row.get("reviewer"), str) and row["reviewer"].strip()]
    values = list(lengths.values())
    n = len(records)
    metrics = {
        "record_count": n, "unique_id_count": len(set(lengths)),
        "author_count": len(author_groups), "source_url_count": len(url_groups),
        "source_document_count": len(document_groups),
        "by_language": counts("language"), "by_category": counts("category"),
        "by_author": counts("author"), "by_split": counts("split"),
        "by_license_status": counts("license_status"),
        "by_review_status": counts("review_status"),
        "by_summary_origin": counts("summary_origin"),
        "pending_count": len(pending), "pending_ratio": len(pending) / n if n else 0,
        "unknown_license_count": len(unknown),
        "unknown_license_ratio": len(unknown) / n if n else 0,
        "human_reviewed_gold_eligible_count": len(verified),
        "human_reviewed_gold_eligible_ids": verified,
        "ai_assisted_pending_count": sum(row.get("summary_origin") == "ai_assisted"
                                        and row.get("review_status") == "pending"
                                        for row in records),
        "original_length_chars": {"min": min(values) if values else 0,
                                  "max": max(values) if values else 0,
                                  "mean": round(statistics.mean(values), 3) if values else 0,
                                  "median": statistics.median(values) if values else 0,
                                  "by_id": lengths},
        "exact_duplicate_group_count": len(exact),
        "normalized_duplicate_candidate_group_count": len(normalized_only),
        "near_duplicate_pair_count": len(near),
        "near_duplicate_short_excluded_ids": excluded,
    }
    return {"status": "failed" if critical else "ok", "metrics": metrics,
            "critical_issues": critical, "warnings": warnings,
            "near_duplicate_candidates": near,
            "groups": {"authors": author_groups, "source_urls": url_groups,
                       "source_documents": document_groups},
            "method": {"near_duplicate_threshold": threshold,
                       "minimum_nonwhitespace_chars": min_length,
                       "normalization": "NFKC + whitespace collapse + unambiguous single-backtick inline code delimiters",
                       "near_duplicates": "Jaccard on sets of character 3-grams after comparison normalization and whitespace removal; candidates only, not semantic deduplication",
                       "gold": "Only verified records with a named reviewer are eligible; eligibility is not independent proof of label correctness. Pending AI-assisted labels never count as gold.",
                       "scope": "Offline data audit; does not visit source URLs, verify author identity, infer permissions, or establish GLM/MySQL/semantic-search execution. No record is modified or removed."}}


def audit_database(db_path: Path, *, threshold: float = 0.65,
                   min_length: int = 30) -> dict:
    db_path = Path(db_path).resolve()
    if not db_path.is_file():
        raise manage.DataError(f"数据库不存在：{db_path}")
    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = manage.REQUIRED_TABLES - tables
        if missing:
            raise manage.DataError("数据库结构缺少表：" + ", ".join(sorted(missing)))
        records = manage.exported_records(conn)
        report = audit_records(records, threshold=threshold, min_length=min_length)
        for row in conn.execute(
            "SELECT n.id,s.id AS source_id,a.note_id FROM notes n "
            "LEFT JOIN sources s ON s.id=n.source_id "
            "LEFT JOIN annotations a ON a.note_id=n.id "
            "WHERE s.id IS NULL OR a.note_id IS NULL"
        ):
            report["critical_issues"].append({"type": "export_join_missing_record",
                "ids": [row["id"]], "missing_source": row["source_id"] is None,
                "missing_annotation": row["note_id"] is None})
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            report["critical_issues"].append({"type": "sqlite_integrity", "messages": integrity})
        for row in conn.execute("PRAGMA foreign_key_check"):
            report["critical_issues"].append({"type": "foreign_key_violation",
                                             "table_row_parent_fkid": list(row)})
        report["metrics"]["database_note_count"] = conn.execute("SELECT count(*) FROM notes").fetchone()[0]
        report["metrics"]["database_source_count"] = conn.execute("SELECT count(*) FROM sources").fetchone()[0]
    report["status"] = "failed" if report["critical_issues"] else "ok"
    report["database"] = str(db_path)
    report["audited_at_utc"] = datetime.now(timezone.utc).isoformat()
    return report


def write_report(db_path: Path, output: Path, *, threshold=0.65, min_length=30) -> dict:
    db_path, output = Path(db_path).resolve(), Path(output).resolve()
    if (output == db_path or str(output) in {str(db_path) + "-wal", str(db_path) + "-shm"}
            or (output.exists() and db_path.exists() and os.path.samefile(output, db_path))):
        raise manage.DataError("报告输出不能覆盖数据库及其 WAL/SHM 文件")
    report = audit_database(db_path, threshold=threshold, min_length=min_length)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent,
                                         delete=False, suffix=".tmp") as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "loom.db")
    parser.add_argument("--output", type=Path, default=ROOT / "exports" / "data_quality.json")
    parser.add_argument("--threshold", type=float, default=0.65)
    parser.add_argument("--min-length", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        report = write_report(args.db, args.output, threshold=args.threshold,
                              min_length=args.min_length)
        print(json.dumps({"status": report["status"], "output": str(args.output.resolve()),
                          "records": report["metrics"]["record_count"],
                          "critical_issues": len(report["critical_issues"]),
                          "warnings": len(report["warnings"])}, ensure_ascii=False))
        return 1 if report["critical_issues"] else 0
    except (manage.DataError, sqlite3.Error, OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
