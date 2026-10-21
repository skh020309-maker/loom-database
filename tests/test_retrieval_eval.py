"""Exercise the actual SQL LIKE engine against a temporary, synthetic corpus."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("loom_retrieval_evaluator", ROOT / "scripts" / "evaluate_retrieval.py")
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)
manage = evaluation.manage


class RetrievalEvaluationContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "test.db"
        self.benchmark = self.root / "benchmark.json"
        manage.initialize(self.db)
        self.ids = [f"fixture-{index:03d}" for index in range(1, 7)]
        seed = [{
            "id": note_id, "title": f"sharedword note {index}",
            "author": "Fixture Author", "url": f"https://example.org/{note_id}",
            "source_kind": "public_note", "language": "en",
            "original_excerpt": f"Unique synthetic fixture {index}.",
            "excerpt_locator": "Synthetic first paragraph", "summary_zh": f"测试笔记{index}",
            "tags": ["合成测试"], "category": "note", "license_status": "unknown",
            "license_name": None, "license_url": None, "retrieved_on": "2026-10-21",
            "evidence_note": "Synthetic unit-test fixture; not collected public data.",
            "split": "dev", "content_origin": "public_excerpt",
        } for index, note_id in enumerate(self.ids, 1)]
        seed_path = self.root / "seed.json"
        seed_path.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
        manage.import_records(self.db, seed_path)

    def tearDown(self):
        self.temp.cleanup()

    def question(self, **changes):
        question = {
            "id": "q-001", "split": "dev", "query_type": "chinese_natural",
            "query": "请找到那条测试笔记", "keyword_baseline": "sharedword",
            "expected_ids": [self.ids[1]], "no_answer": False,
            "label_origin": "ai_assisted", "review_status": "pending",
        }
        question.update(changes)
        return question

    def run_questions(self, questions):
        payload = {"version": "test", "reference_note_ids": self.ids, "questions": questions}
        self.benchmark.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return evaluation.evaluate(self.db, self.benchmark)

    def test_empty_benchmark_rejected_instead_of_dividing_by_zero(self):
        with self.assertRaisesRegex(evaluation.BenchmarkError, "非空数组"):
            self.run_questions([])

    def test_actual_query_and_keyword_tracks_keep_engine_order_and_top_five(self):
        report = self.run_questions([
            self.question(),
            self.question(id="q-002", query="sharedword", expected_ids=[self.ids[5]]),
        ])
        first, sixth = report["details"]
        self.assertEqual(first["full_query"]["returned_ids"], [])
        self.assertEqual(first["preset_keyword"]["returned_ids"], self.ids[:5])
        self.assertEqual(first["preset_keyword"]["first_relevant_rank"], 2)
        self.assertEqual(first["preset_keyword"]["reciprocal_rank_at_5"], 0.5)
        self.assertEqual(sixth["full_query"]["returned_ids"], self.ids[:5])
        self.assertEqual(sixth["full_query"]["hit_at_5"], 0)
        self.assertIsNone(sixth["full_query"]["first_relevant_rank"])
        metrics = report["tracks"]["preset_keyword"]["overall"]
        self.assertEqual(metrics["hit_at_5"], 0.5)
        self.assertEqual(metrics["mrr_at_5"], 0.25)
        self.assertIsNone(metrics["negative_false_return_rate"])
        self.assertEqual(report["tracks"]["preset_keyword"]["by_query_type"]["chinese_natural"]["questions"], 2)

    def test_no_answer_false_return_rate_uses_only_negative_queries(self):
        report = self.run_questions([
            self.question(query="nothing-such-as-this", keyword_baseline="nothing-such-as-this",
                          expected_ids=[], no_answer=True),
            self.question(id="q-002", query="sharedword", keyword_baseline="sharedword",
                          expected_ids=[], no_answer=True),
        ])
        for track in ("full_query", "preset_keyword"):
            metrics = report["tracks"][track]["overall"]
            self.assertEqual(metrics["negative_false_return_rate"], 0.5)
            self.assertEqual(metrics["negative_questions"], 2)
            self.assertIsNone(metrics["hit_at_5"])
            self.assertIsNone(metrics["mrr_at_5"])
            self.assertIsNone(report["tracks"][track]["by_split"]["test"]["hit_at_5"])

    def test_nonexistent_gold_id_is_an_error(self):
        with self.assertRaisesRegex(evaluation.BenchmarkError, "预期目标 ID 不存在"):
            self.run_questions([self.question(expected_ids=["invented-target"])])

    def test_expected_author_split_must_match_and_must_not_leak(self):
        with self.assertRaisesRegex(evaluation.BenchmarkError, "作者 split"):
            self.run_questions([self.question(split="test")])
        with manage.connect(self.db) as conn:
            conn.execute("UPDATE notes SET split='test' WHERE id=?", (self.ids[-1],))
        with self.assertRaisesRegex(evaluation.BenchmarkError, "跨组泄漏"):
            self.run_questions([self.question()])

    def test_empty_positive_and_nonempty_negative_labels_rejected(self):
        for question in (self.question(expected_ids=[]), self.question(no_answer=True)):
            with self.subTest(question=question):
                with self.assertRaisesRegex(evaluation.BenchmarkError, "负例必须"):
                    self.run_questions([question])

    def test_searchable_annotation_changes_change_corpus_fingerprint(self):
        before = self.run_questions([self.question()])
        with manage.connect(self.db) as conn:
            conn.execute("UPDATE annotations SET summary_zh=? WHERE note_id=?",
                         ("增加了一条可检索线索", self.ids[0]))
        after = self.run_questions([self.question()])
        self.assertNotEqual(before["corpus_searchable_sha256"], after["corpus_searchable_sha256"])
        self.assertEqual(before["benchmark_sha256"], after["benchmark_sha256"])
        self.assertFalse(after["gold_standard"])
        self.assertFalse(after["glm_executed"])

    def test_cli_invalid_benchmark_returns_nonzero_without_output(self):
        self.benchmark.write_text('{"reference_note_ids":[],"questions":[]}', encoding="utf-8")
        output = self.root / "must-not-exist.json"
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "evaluate_retrieval.py"),
             "--db", str(self.db), "--benchmark", str(self.benchmark), "--output", str(output)],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stderr)["status"], "error")
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
