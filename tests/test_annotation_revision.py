"""Guard AI-assisted summary revisions against source edits and human overwrites."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import apply_annotation_revision as revision
import manage


class AnnotationRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "loom.db"
        manage.initialize(self.db)
        self.notes = []
        for note_id in ("one", "two"):
            self.notes.append({
                "id": note_id, "title": "A public example", "author": "Example Author",
                "url": f"https://example.org/{note_id}", "source_kind": "public_note",
                "language": "en", "original_excerpt": f"A brief original excerpt about research notes: {note_id}.",
                "excerpt_locator": "first paragraph", "summary_zh": f"原摘要 {note_id}",
                "category": "note", "license_status": "unknown", "retrieved_on": "2026-10-21",
                "evidence_note": "Regression fixture, not production material.",
                "split": "dev", "content_origin": "public_excerpt", "tags": ["研究", "notes"],
            })
        seed = self.root / "seed.json"
        seed.write_text(json.dumps(self.notes, ensure_ascii=False), encoding="utf-8")
        manage.import_records(self.db, seed)
        self.path = self.root / "revision.json"

    def tearDown(self):
        self.temp.cleanup()

    def rec(self, index=0, **changes):
        note = self.notes[index]
        result = {"note_id": note["id"], "original_sha256": manage.digest(note["original_excerpt"]),
                  "before_summary": note["summary_zh"], "after_summary": f"更准确的候选摘要 {note['id']}",
                  "reason": "根据原文修正摘要范围，待人工复核。"}
        result.update(changes)
        return result

    def write(self, records):
        self.path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.path

    def annotation(self, note_id="one"):
        with manage.connect(self.db) as conn:
            return dict(conn.execute("SELECT * FROM annotations WHERE note_id=?", (note_id,)).fetchone())

    def annotation_logs(self):
        with manage.connect(self.db) as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM processing_logs WHERE process_type='annotation' ORDER BY id")]

    def test_revision_preserves_original_category_tags_and_pipeline_and_logs_evidence(self):
        with manage.connect(self.db) as conn:
            conn.execute("UPDATE annotations SET pipeline_status='failed' WHERE note_id='one'")
            before_note = dict(conn.execute("SELECT * FROM notes WHERE id='one'").fetchone())
            before_tags = conn.execute("SELECT * FROM note_tags ORDER BY note_id,tag_id").fetchall()
        rec = self.rec()
        result = revision.apply_revisions(self.db, self.write([rec]))
        after = self.annotation()
        self.assertEqual((result["applied"], result["skipped"]), (1, 0))
        self.assertEqual(after["summary_zh"], rec["after_summary"])
        self.assertEqual((after["category"], after["pipeline_status"]), ("note", "failed"))
        self.assertEqual((after["summary_origin"], after["review_status"], after["reviewer"]),
                         ("ai_assisted", "pending", None))
        with manage.connect(self.db) as conn:
            self.assertEqual(dict(conn.execute("SELECT * FROM notes WHERE id='one'").fetchone()), before_note)
            self.assertEqual(conn.execute("SELECT * FROM note_tags ORDER BY note_id,tag_id").fetchall(), before_tags)
        detail = json.loads(self.annotation_logs()[0]["details"])
        self.assertEqual((detail["actor"], detail["actor_origin"]), ("Codex", "ai_assisted"))
        self.assertEqual(detail["before"]["summary_zh"], rec["before_summary"])
        self.assertEqual(detail["after"]["summary_zh"], rec["after_summary"])
        self.assertEqual(detail["reason"], rec["reason"])
        self.assertEqual(detail["source_revision_file"], self.path.name)
        canonical = json.dumps([rec], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.assertEqual(detail["source_revision_sha256"], hashlib.sha256(canonical.encode("utf-8")).hexdigest())
        self.assertEqual(detail["input_file_sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertIs(detail["original_modified"], False)
        self.assertIs(detail["glm_executed"], False)

    def test_before_mismatch_rolls_back_complete_batch_and_all_logs(self):
        before = [self.annotation(note_id) for note_id in ("one", "two")]
        self.write([self.rec(), self.rec(1, before_summary="过期内容")])
        with self.assertRaises(manage.DataError):
            revision.apply_revisions(self.db, self.path)
        self.assertEqual([self.annotation(note_id) for note_id in ("one", "two")], before)
        self.assertEqual(self.annotation_logs(), [])

    def test_second_sql_update_failure_rolls_back_first_summary_and_its_log(self):
        # Test-only trigger belongs to the disposable fixture database. It fails
        # after the first summary UPDATE and its successful log INSERT execute.
        with manage.connect(self.db) as conn:
            conn.execute("CREATE TRIGGER fail_second_summary BEFORE UPDATE OF summary_zh "
                         "ON annotations WHEN OLD.note_id='two' BEGIN "
                         "SELECT RAISE(ABORT, 'deliberate second update failure'); END")
        before = [self.annotation(note_id) for note_id in ("one", "two")]
        self.write([self.rec(), self.rec(1)])
        with self.assertRaisesRegex(sqlite3.IntegrityError, "second update failure"):
            revision.apply_revisions(self.db, self.path)
        self.assertEqual([self.annotation(note_id) for note_id in ("one", "two")], before)
        self.assertEqual(self.annotation_logs(), [])

    def test_unchanged_before_after_summary_is_rejected_without_applied_log(self):
        self.write([self.rec(after_summary=self.notes[0]["summary_zh"])])
        before = self.annotation()
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                with self.assertRaisesRegex(manage.DataError, "没有实际修订"):
                    revision.apply_revisions(self.db, self.path, dry_run=dry_run)
        self.assertEqual(self.annotation(), before)
        self.assertEqual(self.annotation_logs(), [])

    def test_each_human_protection_state_refuses_overwrite(self):
        protections = [
            ("ai_assisted", "verified", "Actual Human"),
            ("ai_assisted", "rejected", "Actual Human"),
            ("human", "pending", None),
            ("ai_assisted", "pending", "Actual Human"),
            ("ai_assisted", "pending", " "),
        ]
        self.write([self.rec()])
        for origin, status, reviewer in protections:
            with self.subTest(origin=origin, status=status, reviewer=reviewer):
                with manage.connect(self.db) as conn:
                    conn.execute("UPDATE annotations SET summary_origin=?,review_status=?,reviewer=? "
                                 "WHERE note_id='one'", (origin, status, reviewer))
                before = self.annotation()
                with self.assertRaises(manage.DataError):
                    revision.apply_revisions(self.db, self.path)
                self.assertEqual(self.annotation(), before)
        self.assertEqual(self.annotation_logs(), [])

    def test_reapplying_same_file_hash_is_idempotent_even_after_file_rename(self):
        self.write([self.rec()])
        revision.apply_revisions(self.db, self.path)
        before = self.annotation()
        renamed = self.root / "renamed_revision.json"
        renamed.write_bytes(self.path.read_bytes())
        result = revision.apply_revisions(self.db, renamed)
        self.assertEqual((result["applied"], result["skipped"]), (0, 1))
        self.assertEqual(self.annotation(), before)
        self.assertEqual(len(self.annotation_logs()), 1)

    def test_canonical_idempotence_survives_lf_crlf_bom_formatting_and_record_order(self):
        self.write([self.rec(), self.rec(1)])
        original_result = revision.apply_revisions(self.db, self.path)
        content = self.path.read_bytes()
        self.path.write_bytes(b"\xef\xbb\xbf" + content.replace(b"\n", b"\r\n"))
        result = revision.apply_revisions(self.db, self.path)
        self.assertEqual((result["applied"], result["skipped"]), (0, 2))
        self.assertEqual(result["source_revision_sha256"], original_result["source_revision_sha256"])
        self.assertNotEqual(result["input_file_sha256"], original_result["input_file_sha256"])
        self.path.write_text(json.dumps([self.rec(1), self.rec()], ensure_ascii=True,
                                       sort_keys=True), encoding="utf-8")
        reordered = revision.apply_revisions(self.db, self.path)
        self.assertEqual(reordered["source_revision_sha256"], original_result["source_revision_sha256"])
        self.assertEqual((reordered["applied"], reordered["skipped"]), (0, 2))
        self.assertEqual(len(self.annotation_logs()), 2)

    def test_hash_mismatch_rejected_without_changes(self):
        self.write([self.rec(original_sha256="0" * 64)])
        before = self.annotation()
        with self.assertRaises(manage.DataError):
            revision.apply_revisions(self.db, self.path)
        self.assertEqual(self.annotation(), before)
        self.assertEqual(self.annotation_logs(), [])

    def test_duplicate_note_ids_rejected_before_any_write(self):
        self.write([self.rec(), self.rec(after_summary="另一份摘要")])
        with self.assertRaises(manage.DataError):
            revision.apply_revisions(self.db, self.path)
        self.assertEqual(self.annotation()["summary_zh"], self.notes[0]["summary_zh"])
        self.assertEqual(self.annotation_logs(), [])

    def test_dry_run_validates_complete_batch_without_summary_or_log_writes(self):
        self.write([self.rec(), self.rec(1)])
        before = self.db.read_bytes()
        result = revision.apply_revisions(self.db, self.path, dry_run=True)
        self.assertEqual((result["planned"], result["applied"], result["log_ids"]), (2, 0, []))
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(self.annotation_logs(), [])
        self.write([self.rec(), self.rec(1, before_summary="过期内容")])
        with self.assertRaises(manage.DataError):
            revision.apply_revisions(self.db, self.path, dry_run=True)
        self.assertEqual(self.db.read_bytes(), before)

    def test_after_summary_without_matching_tool_log_does_not_bypass_before_guard(self):
        rec = self.rec()
        with manage.connect(self.db) as conn:
            conn.execute("UPDATE annotations SET summary_zh=? WHERE note_id='one'", (rec["after_summary"],))
        self.write([rec])
        with self.assertRaises(manage.DataError):
            revision.apply_revisions(self.db, self.path)
        self.assertEqual(self.annotation_logs(), [])

    def test_idempotence_never_downgrades_a_later_human_decision(self):
        self.write([self.rec()])
        revision.apply_revisions(self.db, self.path)
        with manage.connect(self.db) as conn:
            conn.execute("UPDATE annotations SET review_status='verified',reviewer='Human Reviewer' "
                         "WHERE note_id='one'")
        before = self.annotation()
        with self.assertRaises(manage.DataError):
            revision.apply_revisions(self.db, self.path)
        self.assertEqual(self.annotation(), before)
        self.assertEqual(len(self.annotation_logs()), 1)

    def test_unknown_human_field_empty_or_nul_text_and_unknown_note_are_rejected(self):
        for updates in ({"actor_origin": "human"}, {"after_summary": " "},
                        {"before_summary": ""}, {"reason": "bad\x00text"},
                        {"note_id": "missing"}):
            with self.subTest(updates=updates):
                self.write([self.rec(**updates)])
                with self.assertRaises(manage.DataError):
                    revision.apply_revisions(self.db, self.path)
                self.assertEqual(self.annotation_logs(), [])

    def test_missing_db_not_created_and_cli_reports_error(self):
        missing = self.root / "missing.db"
        self.write([self.rec()])
        with contextlib.redirect_stderr(io.StringIO()):
            result = revision.main(["--db", str(missing), str(self.path), "--dry-run"])
        self.assertNotEqual(result, 0)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
