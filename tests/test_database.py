"""Meaningful contracts for the public-excerpt database; no network calls."""
import csv
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("loom_manage", ROOT / "scripts" / "manage.py")
manage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manage)


def sample(**changes):
    item = {
        "id": "pub-test-001", "title": "Atomic notes", "author": "Example Author",
        "url": "https://example.org/note/one", "source_kind": "public_note",
        "language": "en", "original_excerpt": "Keep a note focused on one idea.",
        "excerpt_locator": "First paragraph", "summary_zh": "每条笔记围绕一个想法。",
        "tags": ["笔记", "原子化"], "category": "idea", "license_status": "unknown",
        "license_name": None, "license_url": None, "retrieved_on": "2026-10-20",
        "evidence_note": "Synthetic test fixture, not collected material.",
        "split": "dev", "content_origin": "public_excerpt",
    }
    item.update(changes)
    return item


class DatabaseContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "test.sqlite3"
        manage.initialize(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def load(self, records):
        path = self.root / "seed.json"
        path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        return manage.import_records(self.db, path)

    def count(self, table):
        with manage.connect(self.db) as conn:
            return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def test_import_is_idempotent_and_preserves_human_annotations(self):
        first = self.load([sample()])
        self.assertEqual(first["inserted"], 1)
        with manage.connect(self.db) as conn:
            conn.execute(
                "UPDATE annotations SET summary_zh=?,summary_origin='human',"
                "review_status='verified',reviewer='tester' WHERE note_id=?",
                ("人工修订的摘要", "pub-test-001"),
            )
            conn.execute("DELETE FROM note_tags WHERE note_id=?", ("pub-test-001",))
        second = self.load([sample(summary_zh="不应覆盖人工修订")])
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["skipped"], 1)
        self.assertEqual(self.count("notes"), 1)
        self.assertEqual(self.count("processing_logs"), 1)
        with manage.connect(self.db) as conn:
            row = conn.execute("SELECT * FROM annotations").fetchone()
            self.assertEqual(row["summary_zh"], "人工修订的摘要")
            self.assertEqual(row["review_status"], "verified")
        self.assertEqual(self.count("note_tags"), 0)

    def test_invalid_batch_rolls_back_after_a_valid_insert(self):
        self.load([sample()])
        second = sample(id="pub-test-002", url="https://example.org/note/two",
                        original_excerpt="A genuinely distinct test excerpt.")
        conflicting = sample(original_excerpt="Changed original.")
        with self.assertRaisesRegex(manage.DataError, "原文不可变"):
            self.load([second, conflicting])
        self.assertEqual(self.count("notes"), 1)
        self.assertEqual(self.count("sources"), 1)
        self.assertEqual(self.count("import_batches"), 1)
        self.assertEqual(self.count("processing_logs"), 1)

    def test_invalid_length_does_not_import_any_record(self):
        with self.assertRaisesRegex(manage.DataError, "500"):
            self.load([sample(), sample(id="too-long", original_excerpt="字" * 501)])
        self.assertEqual(self.count("notes"), 0)
        self.assertEqual(self.count("import_batches"), 0)

    def test_original_and_attribution_immutable_even_via_sql(self):
        self.load([sample()])
        with manage.connect(self.db) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE notes SET original_excerpt='changed'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE sources SET external_author='Someone else'")
        changed = sample(url="https://example.org/another-source")
        with self.assertRaisesRegex(manage.DataError, "来源或定位"):
            self.load([changed])
        with manage.connect(self.db) as conn:
            row = conn.execute("SELECT original_sha256 FROM notes").fetchone()
            self.assertEqual(row[0], manage.digest(sample()["original_excerpt"]))
        self.assertEqual(manage.check_database(self.db)["errors"], [])

    def test_duplicate_hash_is_rejected_and_batch_rolled_back(self):
        with self.assertRaisesRegex(manage.DataError, "原文重复"):
            self.load([sample(), sample(id="pub-other", url="https://example.org/other")])
        self.assertEqual(self.count("notes"), 0)
        self.assertEqual(self.count("sources"), 0)

    def test_parameterized_search_handles_injection_and_literal_wildcards(self):
        self.load([sample(title="100% coverage")])
        self.assertEqual(manage.search_database(self.db, "' OR 1=1 --")["returned"], 0)
        self.assertEqual(manage.search_database(self.db, "%")["returned"], 1)
        self.assertEqual(manage.search_database(self.db, "_")["returned"], 0)
        self.assertEqual(manage.search_database(self.db, "笔记")["returned"], 1)
        self.assertEqual(self.count("notes"), 1)

    def test_same_author_cannot_leak_between_dev_and_test(self):
        first = sample(author=" Example Author ")
        second = sample(id="pub-test-002", author="example   author", split="test",
                        url="https://example.org/second", original_excerpt="Second note.")
        with self.assertRaisesRegex(manage.DataError, "作者跨 dev/test"):
            self.load([first, second])
        self.assertEqual(self.count("notes"), 0)
        self.load([sample()])
        self.load([sample(id="pub-test-003", author="Another Author",
                          url="https://example.org/third", split="test",
                          original_excerpt="Third note.")])
        self.assertEqual(manage.check_database(self.db)["errors"], [])

    def test_check_detects_tampered_hash_and_split(self):
        self.load([sample(), sample(id="pub-test-002", url="https://example.org/second",
                                   original_excerpt="Second note.")])
        with manage.connect(self.db) as conn:
            conn.execute("DROP TRIGGER notes_immutable_original")
            conn.execute("UPDATE notes SET original_sha256=? WHERE id=?",
                         ("0" * 64, "pub-test-001"))
            conn.execute("UPDATE notes SET split='test' WHERE id='pub-test-002'")
        errors = manage.check_database(self.db)["errors"]
        self.assertTrue(any("SHA-256" in error for error in errors))
        self.assertTrue(any("作者跨 dev/test" in error for error in errors))

    def test_foreign_keys_and_pipeline_claims(self):
        self.load([sample()])
        with manage.connect(self.db) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO note_tags(note_id,tag_id) VALUES('missing',9)")
            conn.execute("UPDATE annotations SET pipeline_status='completed'")
        self.assertTrue(any("真实 GLM" in error
                            for error in manage.check_database(self.db)["errors"]))

    def test_export_preserves_provenance_and_has_utf8_bom_csv(self):
        self.load([sample(title="=1+1")])
        json_path, csv_path = self.root / "notes.json", self.root / "notes.csv"
        manage.export_database(self.db, "json", json_path)
        exported = json.loads(json_path.read_text(encoding="utf-8"))
        row = exported["notes"][0]
        self.assertEqual(row["original_excerpt"], sample()["original_excerpt"])
        self.assertEqual(row["evidence_note"], sample()["evidence_note"])
        self.assertEqual(row["annotation"]["pipeline_status"], "not_run")
        self.assertEqual(row["training_allowed"], 0)
        self.assertIsNone(row["owner_user_id"])
        manage.export_database(self.db, "csv", csv_path)
        self.assertTrue(csv_path.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertIn("'=1+1", csv_path.read_text(encoding="utf-8-sig"))

    def test_markdown_export_preserves_license_url(self):
        license_url = "https://creativecommons.org/licenses/by/4.0/"
        self.load([sample(
            license_status="open", license_name="CC BY 4.0", license_url=license_url,
        )])
        output = self.root / "notes.md"
        manage.export_database(self.db, "markdown", output)
        content = output.read_text(encoding="utf-8")
        self.assertIn("- 许可：open / CC BY 4.0", content)
        self.assertIn(f"- 许可链接：<{license_url}>", content)

    def mixed_review_fixture(self):
        self.load([
            sample(id="review-human", url="https://example.org/review/human",
                   original_excerpt="First unique review fixture."),
            sample(id="review-pending", url="https://example.org/review/pending",
                   original_excerpt="Second unique review fixture."),
            sample(id="review-rejected", url="https://example.org/review/rejected",
                   original_excerpt="Third unique review fixture."),
        ])
        with manage.connect(self.db) as conn:
            conn.execute(
                "UPDATE annotations SET summary_origin='human',review_status='verified',"
                "reviewer='reviewer-A',summary_zh='人工撰写且已核验的摘要' "
                "WHERE note_id='review-human'")
            conn.execute(
                "UPDATE annotations SET review_status='rejected',reviewer='reviewer-A' "
                "WHERE note_id='review-rejected'")

    def test_json_export_summarizes_mixed_review_states_without_relabeling(self):
        self.mixed_review_fixture()
        output = self.root / "mixed.json"
        manage.export_database(self.db, "json", output)
        payload = json.loads(output.read_text(encoding="utf-8"))
        metadata = payload["metadata"]
        self.assertEqual(metadata["summary_origin_counts"], {"ai_assisted": 2, "human": 1})
        self.assertEqual(metadata["review_status_counts"], {"pending": 1, "verified": 1, "rejected": 1})
        for phrase in ("AI 辅助 2 条", "人工撰写 1 条", "待人工复核 1 条", "已人工复核 1 条", "已驳回 1 条"):
            self.assertIn(phrase, metadata["summary_status"])
        records = {note["id"]: note for note in payload["notes"]}
        self.assertEqual(records["review-human"]["annotation"]["summary_origin"], "human")
        self.assertEqual(records["review-human"]["annotation"]["review_status"], "verified")
        self.assertEqual(records["review-rejected"]["annotation"]["summary_origin"], "ai_assisted")

    def test_markdown_mixed_review_headings_reflect_each_annotation(self):
        self.mixed_review_fixture()
        output = self.root / "mixed.md"
        manage.export_database(self.db, "markdown", output)
        content = output.read_text(encoding="utf-8")
        sections = {section.split(" · ", 1)[0]: section for section in content.split("\n## ")[1:]}
        expected = {
            "review-human": "**中文摘要（人工撰写，已人工复核）**",
            "review-pending": "**中文摘要（AI 辅助，待人工复核）**",
            "review-rejected": "**中文摘要（AI 辅助，已驳回）**",
        }
        for note_id, heading in expected.items():
            self.assertIn(heading, sections[note_id])
        self.assertNotIn("AI 辅助，待人工复核", sections["review-human"])
        self.assertIn("已驳回 1 条", content.split("\n## ")[0])

    def test_source_license_conflicts_rejected_for_same_and_new_note_ids(self):
        baseline = sample(
            license_status="open", license_name="CC BY 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
        )
        self.load([baseline])
        changes = {
            "license_status": "unknown",
            "license_name": "CC BY-SA 4.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        }
        for same_id in (True, False):
            for field, value in changes.items():
                with self.subTest(same_id=same_id, field=field):
                    changed = dict(baseline, **{field: value})
                    if not same_id:
                        changed.update(id="pub-new", original_excerpt="A second excerpt.")
                    with self.assertRaisesRegex(manage.DataError, "许可信息冲突.*独立复核"):
                        self.load([changed])
                    self.assertEqual(self.count("notes"), 1)
                    self.assertEqual(self.count("import_batches"), 1)
                    with manage.connect(self.db) as conn:
                        source = conn.execute("SELECT * FROM sources").fetchone()
                        for license_field in changes:
                            self.assertEqual(source[license_field], baseline[license_field])

    def test_csv_escapes_formula_prefixes_and_json_keeps_exact_text(self):
        for index, prefix in enumerate(("=", "+", "-", "@")):
            self.load([sample(
                id=f"formula-{index}", title=prefix + "Example",
                url=f"https://example.org/formula/{index}",
                original_excerpt=f"  {prefix}SUM(A1:A2)",
                summary_zh=prefix + "中文摘要",
                evidence_note=prefix + "external evidence",
            )])
        csv_path, json_path = self.root / "escape.csv", self.root / "exact.json"
        manage.export_database(self.db, "csv", csv_path)
        manage.export_database(self.db, "json", json_path)
        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        originals = json.loads(json_path.read_text(encoding="utf-8"))["notes"]
        for row, original in zip(rows, originals):
            for key in ("title", "original_excerpt", "summary_zh", "evidence_note"):
                self.assertEqual(row[key], "'" + original[key])

    def test_check_requires_preserved_evidence_note(self):
        self.load([sample()])
        with manage.connect(self.db) as conn:
            conn.execute("UPDATE processing_logs SET details='{}'")
        self.assertTrue(any("evidence_note" in error
                            for error in manage.check_database(self.db)["errors"]))

    def test_training_permission_or_internal_ownership_cannot_be_inferred(self):
        with self.assertRaises(manage.DataError):
            self.load([sample(training_allowed=1)])
        with self.assertRaises(manage.DataError):
            self.load([sample(owner_user_id="external-author-is-not-user")])
        with self.assertRaises(manage.DataError):
            self.load([sample(license_status="open")])

    def test_cli_failures_return_nonzero_without_creating_missing_database(self):
        missing = self.root / "missing.sqlite3"
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "manage.py"),
             "--db", str(missing), "check"],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stderr)["status"], "error")
        self.assertFalse(missing.exists())
        self.load([sample()])
        with manage.connect(self.db) as conn:
            conn.execute("UPDATE annotations SET pipeline_status='completed'")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "manage.py"),
             "--db", str(self.db), "check"],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["status"], "failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
