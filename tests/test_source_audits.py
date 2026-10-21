"""Source-check contracts using isolated databases and synthetic fixtures only."""
import json
import sqlite3
from datetime import timedelta
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import manage
import source_audits as audits


def sample():
    return {
        "id": "note-fixture", "title": "Synthetic fixture", "author": "Fixture Author",
        "url": "https://example.org/note", "source_kind": "public_note", "language": "en",
        "original_excerpt": "This is a synthetic test fixture.", "excerpt_locator": "paragraph one",
        "summary_zh": "测试数据，不计入真实语料。", "tags": ["测试"], "category": "note",
        "license_status": "unknown", "license_name": None, "license_url": None,
        "retrieved_on": audits.today_shanghai().isoformat(), "evidence_note": "Synthetic fixture.",
        "split": "dev", "content_origin": "public_excerpt",
    }


def check(**changes):
    item = {
        "id": "check-fixture", "note_id": "note-fixture", "source_url": "https://example.org/note",
        "checked_on": audits.today_shanghai().isoformat(), "checker_type": "ai_assisted",
        "fetch_status": "accessible", "excerpt_status": "exact", "author_check": "confirmed",
        "license_check": "not_confirmed", "license_evidence_url": None,
        "notes": "测试核查记录。", "method": "web_reader",
    }
    item.update(changes)
    return item


class SourceAuditContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "test.db"
        manage.initialize(self.db)
        seed = self.root / "seed.json"
        seed.write_text(json.dumps([sample()]), encoding="utf-8")
        manage.import_records(self.db, seed)

    def tearDown(self):
        self.temp.cleanup()

    def load(self, records):
        path = self.root / "checks.json"
        path.write_text(json.dumps(records), encoding="utf-8")
        return audits.import_audits(self.db, path)

    def test_import_is_idempotent(self):
        self.assertEqual(self.load([check()])["inserted"], 1)
        self.assertEqual(self.load([check()])["skipped"], 1)
        self.assertEqual(audits.audit_database(self.db)["metadata"]["record_count"], 1)

    def test_conflicting_id_rolls_back_entire_batch(self):
        self.load([check()])
        with self.assertRaisesRegex(manage.DataError, "冲突"):
            self.load([check(id="another"), check(notes="changed")])
        self.assertEqual(audits.audit_database(self.db)["metadata"]["record_count"], 1)

    def test_unknown_url_rolls_back_prior_insert(self):
        with self.assertRaisesRegex(manage.DataError, "未知"):
            self.load([check(), check(id="unknown", source_url="https://example.org/missing")])
        self.assertEqual(audits.audit_database(self.db)["checks"], [])

    def test_note_and_source_ids_must_match_url(self):
        for changes in ({"note_id": "missing"}, {"source_id": 9999}):
            with self.subTest(changes=changes), self.assertRaises(manage.DataError):
                self.load([check(**changes)])
        self.assertEqual(audits.audit_database(self.db)["checks"], [])

    def test_source_checks_never_upgrade_annotations_or_license(self):
        self.load([check(license_check="confirmed", license_evidence_url="https://example.org/license")])
        with manage.connect(self.db) as conn:
            label = conn.execute("SELECT * FROM annotations").fetchone()
            source = conn.execute("SELECT * FROM sources").fetchone()
            self.assertEqual(label["review_status"], "pending")
            self.assertEqual(label["summary_origin"], "ai_assisted")
            self.assertEqual(label["pipeline_status"], "not_run")
            self.assertEqual(source["license_status"], "unknown")
            self.assertIsNone(source["license_url"])

    def test_blocked_page_cannot_confirm_excerpt_or_author(self):
        for changes in (
            {"fetch_status": "blocked"},
            {"fetch_status": "unavailable", "excerpt_status": "not_checked"},
            {"fetch_status": "blocked", "author_check": "not_checked"},
        ):
            with self.subTest(changes=changes), self.assertRaises(manage.DataError):
                audits.validate_audit(check(**changes))
        accepted = audits.validate_audit(check(fetch_status="blocked", excerpt_status="not_checked",
                                               author_check="not_checked", license_check="not_checked"))
        self.assertEqual(accepted["fetch_status"], "blocked")

    def test_invalid_dates_urls_and_enums_rejected(self):
        tomorrow = (audits.today_shanghai() + timedelta(days=1)).isoformat()
        changes = [
            {"checked_on": "2026-02-30"}, {"checked_on": "20261021"}, {"checked_on": tomorrow},
            {"source_url": "javascript:alert(1)"}, {"source_url": "https://user:pw@example.org/"},
            {"license_check": "confirmed"}, {"license_check": "conflict"},
            {"license_evidence_url": "file:///tmp/license"}, {"checker_type": "robot"},
            {"method": "search_snippet"}, {"author_check": "maybe"}, {"notes": ""},
            {"source_id": True}, {"created_at": "today"},
            {"note_id": ""}, {"note_id": None},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(manage.DataError):
                audits.validate_audit(check(**change))

    def test_original_excerpt_and_hash_unchanged(self):
        with manage.connect(self.db) as conn:
            before = dict(conn.execute("SELECT * FROM notes").fetchone())
        self.load([check(excerpt_status="not_found", author_check="conflict")])
        with manage.connect(self.db) as conn:
            after = dict(conn.execute("SELECT * FROM notes").fetchone())
        self.assertEqual(before, after)

    def test_export_joins_source_and_notes_and_roundtrips(self):
        self.load([check()])
        result = audits.audit_database(self.db)
        row = result["checks"][0]
        self.assertEqual(row["source_url"], sample()["url"])
        self.assertEqual(row["author"], sample()["author"])
        self.assertEqual(row["note_id"], sample()["id"])
        self.assertNotIn("note_ids", row)
        self.assertIn("created_at", row)
        self.assertEqual(self.load(result)["skipped"], 1)

    def test_validate_preserves_note_id_and_strips_export_metadata(self):
        validated = audits.validate_audit({**check(), "author": "display", "note_ids": ["display"]})
        self.assertEqual(validated["note_id"], "note-fixture")
        self.assertNotIn("author", validated)
        self.assertNotIn("note_ids", validated)

    def test_unique_source_resolves_missing_note_id(self):
        item = check()
        del item["note_id"]
        self.load([item])
        self.assertEqual(audits.audit_database(self.db)["checks"][0]["note_id"], "note-fixture")

    def test_database_rejects_update_and_delete_of_existing_checks(self):
        self.load([check()])
        with manage.connect(self.db) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE source_checks SET notes='changed'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM source_checks")
        self.assertEqual(audits.audit_database(self.db)["checks"][0]["notes"], check()["notes"])

    def test_two_excerpts_same_url_require_explicit_id_and_never_propagate(self):
        second = sample()
        second.update(id="note-second", original_excerpt="Another synthetic excerpt from this URL.")
        seed = self.root / "second.json"
        seed.write_text(json.dumps([second]), encoding="utf-8")
        manage.import_records(self.db, seed)
        ambiguous = check()
        del ambiguous["note_id"]
        with self.assertRaisesRegex(manage.DataError, "必须提供 note_id"):
            self.load([ambiguous])
        self.assertEqual(audits.audit_database(self.db)["checks"], [])
        self.load([check()])
        exported = audits.audit_database(self.db)["checks"]
        self.assertEqual([row["note_id"] for row in exported], ["note-fixture"])
        with manage.connect(self.db) as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM source_checks WHERE note_id='note-second'"
            ).fetchone()[0], 0)
        with self.assertRaisesRegex(manage.DataError, "冲突"):
            self.load([check(note_id="note-second")])
        self.assertEqual(audits.audit_database(self.db)["checks"][0]["note_id"], "note-fixture")

    def test_export_cannot_overwrite_database(self):
        before = self.db.read_bytes()
        self.assertEqual(audits.main(["--db", str(self.db), "export", "--output", str(self.db)]), 1)
        self.assertEqual(self.db.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
