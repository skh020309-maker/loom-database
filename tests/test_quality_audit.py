"""Regression tests for read-only auditing, candidate deduplication and leakage."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audit_quality as audit
import manage


def record(note_id="one", text="A sufficiently detailed note about collecting and reviewing reliable public research evidence.", **updates):
    value = {"id": note_id, "title": "Example note", "author": "Author One",
             "url": f"https://example.org/{note_id}", "source_kind": "public_note",
             "language": "en", "original_excerpt": text,
             "excerpt_locator": "first paragraph", "summary_zh": "待核验摘要",
             "category": "note", "license_status": "unknown", "retrieved_on": "2026-10-21",
             "evidence_note": "Test fixture only.", "split": "dev",
             "content_origin": "public_excerpt", "tags": ["research"],
             "summary_origin": "ai_assisted", "review_status": "pending",
             "pipeline_status": "not_run", "training_allowed": 0,
             "original_sha256": manage.digest(text)}
    value.update(updates)
    return value


class QualityAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def database(self):
        db = self.root / "loom.db"
        manage.initialize(db)
        seed = self.root / "seed.json"
        seed.write_text(json.dumps([record()]), encoding="utf-8")
        manage.import_records(db, seed)
        return db

    def test_exact_duplicate_is_critical_and_not_near_candidate(self):
        first = record()
        second = record("two", first["original_excerpt"])
        report = audit.audit_records([first, second])
        self.assertIn("exact_original_duplicate", {x["type"] for x in report["critical_issues"]})
        self.assertEqual(report["near_duplicate_candidates"], [])
        self.assertEqual(report["metrics"]["exact_duplicate_group_count"], 1)

    def test_near_duplicate_is_candidate_not_equality_and_short_text_excluded(self):
        text = "A sufficiently detailed note about collecting and reviewing reliable public research evidence."
        rows = [record("a", text), record("b", text.replace("reliable", "verifiable")),
                record("short1", "这是一个笔记"), record("short2", "这是一个想法")]
        report = audit.audit_records(rows)
        self.assertEqual(report["metrics"]["exact_duplicate_group_count"], 0)
        self.assertEqual([x["ids"] for x in report["near_duplicate_candidates"]], [["a", "b"]])
        self.assertEqual(report["metrics"]["near_duplicate_short_excluded_ids"], ["short1", "short2"])
        self.assertEqual(report["status"], "ok")

    def test_chinese_unicode_whitespace_inline_code_only_comparison(self):
        original = "研究　`ＡＩ`　笔记\n需要核验"
        canonical = "研究 AI 笔记 需要核验"
        self.assertEqual(audit.normalize_text(original), canonical)
        self.assertEqual(audit.normalize_text("``ＡＩ``"), "``AI``")
        self.assertEqual(audit.normalize_text(r"\`literal\`"), r"\`literal\`")
        rows = [record("a", original), record("b", canonical)]
        report = audit.audit_records(rows)
        self.assertEqual(report["metrics"]["normalized_duplicate_candidate_group_count"], 1)
        self.assertEqual(rows[0]["original_excerpt"], original)

    def test_missing_database_never_created_and_error_returns_nonzero(self):
        db, output = self.root / "absent.db", self.root / "report.json"
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertNotEqual(audit.main(["--db", str(db), "--output", str(output)]), 0)
        self.assertFalse(db.exists())
        self.assertFalse(output.exists())

    def test_author_and_source_document_leakage_missing_fields_and_gold(self):
        one = record("one", author="Ａuthor One", url="https://example.org/doc#one")
        two = record("two", "Other independent excerpt to prevent equality.",
                     author="author  one", url="https://example.org/doc#two", split="test")
        del two["excerpt_locator"]
        report = audit.audit_records([one, two])
        issues = {x["type"] for x in report["critical_issues"]}
        self.assertTrue({"author_split_leakage", "source_document_split_leakage", "missing_fields"} <= issues)
        self.assertEqual(report["metrics"]["author_count"], 1)
        self.assertEqual(report["metrics"]["human_reviewed_gold_eligible_count"], 0)
        self.assertEqual(report["metrics"]["pending_ratio"], 1)
        one.update(review_status="verified", reviewer="Human Reviewer")
        self.assertEqual(audit.audit_records([one])["metrics"]["human_reviewed_gold_eligible_count"], 1)

    def test_output_cannot_overwrite_database_and_success_leaves_db_unchanged(self):
        db = self.database()
        before = db.read_bytes()
        with self.assertRaises(manage.DataError):
            audit.write_report(db, db)
        output = self.root / "report.json"
        report = audit.write_report(db, output)
        self.assertEqual(db.read_bytes(), before)
        self.assertEqual(report["status"], "ok")
        self.assertTrue(output.is_file())
        self.assertEqual(report["metrics"]["database_note_count"], 1)

    def test_no_candidate_is_normal_and_warning_is_not_failure(self):
        report = audit.audit_records([record("one"), record("two", "中文记录讨论矩阵分解的数学性质及其在机器学习实验中的应用和局限性。")])
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["near_duplicate_candidates"], [])
        self.assertTrue(report["warnings"])
        self.assertEqual(report["metrics"]["by_license_status"], {"unknown": 2})

    def test_missing_annotation_cannot_disappear_through_export_join(self):
        db = self.database()
        with sqlite3.connect(db, factory=manage.ManagedConnection) as conn:
            conn.execute("DELETE FROM annotations")
        report = audit.audit_database(db)
        self.assertEqual(report["status"], "failed")
        issue = next(x for x in report["critical_issues"] if x["type"] == "export_join_missing_record")
        self.assertEqual(issue["ids"], ["one"])
        self.assertTrue(issue["missing_annotation"])

    def test_broken_schema_returns_nonzero_without_report(self):
        db = self.root / "broken.db"
        with sqlite3.connect(db, factory=manage.ManagedConnection) as conn:
            conn.execute("CREATE TABLE unrelated(id INTEGER)")
        output = self.root / "report.json"
        with contextlib.redirect_stderr(io.StringIO()):
            result = audit.main(["--db", str(db), "--output", str(output)])
        self.assertNotEqual(result, 0)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
