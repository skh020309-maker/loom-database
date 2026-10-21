#!/usr/bin/env python3
"""Evaluate two literal SQL LIKE baselines against AI-proposed, unreviewed labels."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("loom_manage_for_evaluation", ROOT / "scripts" / "manage.py")
manage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manage)
TOP_K = 5
DEFAULT_BENCHMARK = ROOT / "data" / "retrieval_benchmark.json"
DEFAULT_OUTPUT = ROOT / "exports" / "retrieval_eval.json"


class BenchmarkError(ValueError):
    """Malformed labels, unsupported IDs, or a split mismatch."""


def read_benchmark(path: Path) -> tuple[dict[str, Any], str]:
    raw = Path(path).read_bytes()
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"评测文件必须是有效 UTF-8 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise BenchmarkError("评测文件必须是含 questions 数组的对象")
    return data, hashlib.sha256(raw).hexdigest()


def corpus_snapshot(db: Path) -> tuple[list[dict[str, Any]], str]:
    with manage.connect(db) as conn:
        notes = manage.exported_records(conn)
    # Includes every searchable field and tags, so annotation edits also alter the hash.
    searchable = [{key: note[key] for key in (
        "id", "title", "author", "original_excerpt", "summary_zh", "tags", "split"
    )} for note in notes]
    canonical = json.dumps(searchable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return notes, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_benchmark(data: dict[str, Any], notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        raise BenchmarkError("questions 必须是非空数组；空评测集不能计算指标")
    reference_ids = data.get("reference_note_ids")
    if (not isinstance(reference_ids, list) or not reference_ids
            or any(not isinstance(value, str) or not value for value in reference_ids)
            or len(set(reference_ids)) != len(reference_ids)):
        raise BenchmarkError("reference_note_ids 必须是非空且无重复的 ID 数组")
    note_by_id = {note["id"]: note for note in notes}
    unknown_reference = set(reference_ids) - note_by_id.keys()
    if unknown_reference:
        raise BenchmarkError("参考样本 ID 不存在：" + ", ".join(sorted(unknown_reference)))
    author_splits: dict[str, set[str]] = {}
    for note in notes:
        author = manage.normalized_author(note["author"])
        author_splits.setdefault(author, set()).add(note["split"])
    question_ids = set()
    for index, question in enumerate(questions, 1):
        if not isinstance(question, dict):
            raise BenchmarkError(f"第 {index} 题必须是对象")
        for field in ("id", "query", "keyword_baseline"):
            if not isinstance(question.get(field), str) or not question[field].strip():
                raise BenchmarkError(f"第 {index} 题的 {field} 不能为空")
        if question["id"] in question_ids:
            raise BenchmarkError("题目 ID 重复：" + question["id"])
        question_ids.add(question["id"])
        if question.get("split") not in ("dev", "test"):
            raise BenchmarkError(f"{question['id']} split 必须为 dev/test")
        if question.get("label_origin") != "ai_assisted" or question.get("review_status") != "pending":
            raise BenchmarkError(f"{question['id']} 当前基线仅接受 ai_assisted / pending 候选标签")
        if type(question.get("no_answer")) is not bool:
            raise BenchmarkError(f"{question['id']} no_answer 必须是布尔值")
        expected = question.get("expected_ids")
        if (not isinstance(expected, list)
                or any(not isinstance(value, str) or not value for value in expected)
                or len(set(expected)) != len(expected)):
            raise BenchmarkError(f"{question['id']} expected_ids 必须是无重复字符串数组")
        if question["no_answer"] != (len(expected) == 0):
            raise BenchmarkError(f"{question['id']} 负例必须没有目标 ID，正例至少一个目标 ID")
        for target_id in expected:
            if target_id not in note_by_id:
                raise BenchmarkError(f"{question['id']} 预期目标 ID 不存在：{target_id}")
            if target_id not in reference_ids:
                raise BenchmarkError(f"{question['id']} 目标 ID 不属于固定参考集：{target_id}")
            note = note_by_id[target_id]
            author = manage.normalized_author(note["author"])
            if note["split"] != question["split"] or author_splits[author] != {question["split"]}:
                raise BenchmarkError(f"{question['id']} 预期作者 split 不一致或跨组泄漏：{note['author']}")
    return questions


def score_result(question: dict[str, Any], returned_ids: list[str]) -> dict[str, Any]:
    """Keep engine order. The first relevant result defines reciprocal rank."""
    top = returned_ids[:TOP_K]
    expected = set(question["expected_ids"])
    first_rank = next((rank for rank, note_id in enumerate(top, 1) if note_id in expected), None)
    negative = question["no_answer"]
    return {
        "returned_ids": top,
        "first_relevant_rank": first_rank,
        "hit_at_5": None if negative else int(first_rank is not None),
        "reciprocal_rank_at_5": None if negative else (1.0 / first_rank if first_rank else 0.0),
        "negative_false_return": bool(top) if negative else None,
    }


def aggregate(details: list[dict[str, Any]], track: str) -> dict[str, Any]:
    positive = [row[track] for row in details if not row["no_answer"]]
    negative = [row[track] for row in details if row["no_answer"]]
    return {
        "questions": len(details), "positive_questions": len(positive),
        "negative_questions": len(negative),
        "hit_at_5": sum(row["hit_at_5"] for row in positive) / len(positive) if positive else None,
        "mrr_at_5": sum(row["reciprocal_rank_at_5"] for row in positive) / len(positive) if positive else None,
        "negative_false_return_rate": sum(row["negative_false_return"] for row in negative) / len(negative) if negative else None,
    }


def evaluate(db: Path, benchmark: Path) -> dict[str, Any]:
    data, benchmark_hash = read_benchmark(benchmark)
    notes, corpus_hash = corpus_snapshot(db)
    questions = validate_benchmark(data, notes)
    details = []
    for question in questions:
        row = dict(question)
        for track, field in (("full_query", "query"), ("preset_keyword", "keyword_baseline")):
            # Both tracks call the shipped engine directly, without semantic rewriting.
            result = manage.search_database(db, question[field], limit=TOP_K)
            row[track] = {
                "submitted_query": question[field],
                "search_method": result["search_method"],
                **score_result(question, [hit["id"] for hit in result["results"]]),
            }
        details.append(row)
    _, final_corpus_hash = corpus_snapshot(db)
    if corpus_hash != final_corpus_hash:
        raise BenchmarkError("评测期间可检索语料发生变化；请在导入或编辑结束后重跑")
    return {
        "status": "ok", "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_version": data.get("version"), "benchmark_sha256": benchmark_hash,
        "corpus_searchable_sha256": corpus_hash, "corpus_note_count": len(notes),
        "reference_note_ids": data["reference_note_ids"], "top_k": TOP_K,
        "label_origin": "ai_assisted", "review_status": "pending",
        "gold_standard": False, "glm_executed": False,
        "ranking": "当前引擎 ORDER BY note ID；无相关性排序",
        "tracks": {
            track: {
                "overall": aggregate(details, track),
                "by_split": {split: aggregate([row for row in details if row["split"] == split], track)
                             for split in ("dev", "test")},
                "by_query_type": {
                    query_type: aggregate([
                        row for row in details if row.get("query_type", "unspecified") == query_type
                    ], track)
                    for query_type in sorted({row.get("query_type", "unspecified") for row in details})
                },
            } for track in ("full_query", "preset_keyword")
        },
        "query_type_counts": dict(Counter(row.get("query_type", "unspecified") for row in details)),
        "limitations": [
            "题目和目标均为 AI 辅助候选，尚未人工核验；结果不是人工金标准准确率。",
            "整句和预指定关键词均为真实 SQL LIKE 字面匹配；关键词命中不证明语义检索已实现。",
            "关键词在看过参考样本后预指定，是有利的诊断条件，未测试自动关键词提取能力。",
            "检索范围是当前数据库全部可检索记录；扩库或改标注会改变结果，应连同语料指纹比较。",
            "split 继承预期目标作者；负例没有目标作者，其 split 只是题目分组。",
            "负例错误返回率只衡量无答案候选题是否返回任何结果；不衡量生成答案的幻觉。",
            "少量候选题不支持泛化结论；没有执行向量检索、重排序、GLM、语音或 OCR。",
        ],
        "details": details,
    }


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=manage.DEFAULT_DB)
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        if args.output.resolve() in {args.db.resolve(), args.benchmark.resolve()}:
            raise BenchmarkError("输出不能覆盖数据库或评测题文件")
        result = evaluate(args.db, args.benchmark)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "ok", "questions": len(result["details"]),
                          "corpus_note_count": result["corpus_note_count"],
                          "tracks": result["tracks"], "output": str(args.output.resolve()),
                          "label_status": "AI 辅助候选，待人工复核，非人工金标准"},
                         ensure_ascii=False, indent=2))
        return 0
    except (BenchmarkError, manage.DataError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
