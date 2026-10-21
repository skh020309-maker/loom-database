"""Review contracts: provenance, actual human status, transactions and audit."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import manage
import review_notes


class ReviewContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "notes.db"
        manage.initialize(self.db)
        record = json.loads((ROOT / "data" / "seed_public_notes.json").read_text(encoding="utf-8"))[0]
        self.note_id = record["id"]
        seed = Path(self.tmp.name) / "seed.json"
        seed.write_text(json.dumps([record], ensure_ascii=False), encoding="utf-8")
        manage.import_records(self.db, seed)

    def tearDown(self):
        self.tmp.cleanup()

    def edit(self, **changes):
        args = dict(actor="reviewer-A", origin="human", reason="Checked original source", status="verified")
        args.update(changes)
        return review_notes.update_annotation(self.db, self.note_id, **args)

    def test_review_preserves_original_and_records_before_after(self):
        before = review_notes.show_note(self.db, self.note_id)
        result = self.edit(summary="人工复核后的测试摘要", tags=["复核", "复核"])
        after = review_notes.show_note(self.db, self.note_id)
        self.assertTrue(result["changed"])
        for key in ("original_excerpt", "original_sha256", "url", "author", "excerpt_locator"):
            self.assertEqual(before[key], after[key])
        self.assertEqual(after["annotation"]["review_status"], "verified")
        self.assertEqual(after["annotation"]["pipeline_status"], "not_run")
        with manage.connect(self.db) as conn:
            log = conn.execute("SELECT details FROM processing_logs WHERE process_type='annotation'").fetchone()
        audit = json.loads(log[0])
        self.assertEqual(audit["before"]["summary_zh"], before["summary_zh"])
        self.assertEqual(audit["after"]["tags"], ["复核"])
        self.assertFalse(audit["glm_executed"])

    def test_ai_cannot_claim_human_verified_or_rejected(self):
        for status in ("verified", "rejected"):
            with self.subTest(status=status), self.assertRaises(manage.DataError):
                self.edit(origin="ai_assisted", status=status)
        self.assertEqual(review_notes.show_note(self.db, self.note_id)["review_status"], "pending")

    def test_invalid_edit_leaves_annotation_and_logs_unchanged(self):
        before = review_notes.show_note(self.db, self.note_id)
        with self.assertRaises(manage.DataError):
            self.edit(summary="", tags=["新标签"])
        self.assertEqual(review_notes.show_note(self.db, self.note_id), before)
        with manage.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM processing_logs").fetchone()[0], 1)

    def test_repeated_same_review_is_noop(self):
        self.assertTrue(self.edit()["changed"])
        self.assertFalse(self.edit()["changed"])
        with manage.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM processing_logs").fetchone()[0], 2)

    def test_pending_edit_clears_verification_but_keeps_input_origin(self):
        self.edit()
        self.edit(status="pending", origin="ai_assisted", summary="待核查的新候选摘要")
        after = review_notes.show_note(self.db, self.note_id)
        self.assertIsNone(after["annotation"]["reviewer"])
        self.assertEqual(after["annotation"]["summary_origin"], "ai_assisted")
        self.assertEqual(after["annotation"]["review_status"], "pending")

    def test_missing_note_and_actor_do_not_mutate_database(self):
        with self.assertRaises(manage.DataError):
            self.edit(actor=" ")
        with self.assertRaises(manage.DataError):
            review_notes.update_annotation(self.db, "missing", actor="A", origin="human",
                                           reason="x", status="verified")
        self.assertEqual(manage.check_database(self.db)["status"], "ok")


if __name__ == "__main__":
    unittest.main()
