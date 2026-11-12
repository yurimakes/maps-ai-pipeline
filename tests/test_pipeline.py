import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from maps_ai_pipeline.adapters import LocalPreparedInputs, LocalResultStore
from maps_ai_pipeline.contracts import RequestConflict, ValidationError, read_json
from maps_ai_pipeline.pipeline import run, validate_envelope

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "synthetic"


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs"
        shutil.copytree(EXAMPLES, self.inputs)
        self.output = self.root / "results"
        self.request = self.inputs / "request-v1.json"

    def edit(self, name, callback):
        path = self.inputs / name
        data = read_json(path)
        callback(data)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def execute(self, request=None):
        return run(request or self.request, self.output)

    def result_row(self, column_id, request=None):
        result = self.execute(request)["envelope"]["result"]
        return next(c for c in result["columns"] if c["source"]["column_id"] == column_id)

    def assert_blocked(self):
        with self.assertRaises(ValidationError):
            self.execute()
        self.assertFalse(self.output.exists())

    def test_dictionary_match_has_words_domain_and_evidence(self):
        row = self.result_row("c-name")
        self.assertEqual(row["recommendation_status"], "candidate")
        self.assertEqual({w["id"] for w in row["word_candidates"]}, {"w-customer", "w-name"})
        term = row["term_candidates"][0]
        self.assertEqual(term["linked_domain"]["id"], "d-text")
        self.assertEqual(term["evidence"]["matched_aliases"], ["CUST_NM"])
        self.assertEqual(term["evidence"]["method"], "exact_alias")
        self.assertEqual(row["mapping_candidates"][0]["target_column_id"], "customer-name")

    def test_abbreviation_conflict_not_resolved_by_spelling(self):
        self.edit("business-v1.json", lambda d: d["column_definitions"][1].update(concept_id="other-concept"))
        row = self.result_row("c-amount")
        self.assertEqual(row["recommendation_status"], "held")
        self.assertIn("business_meaning_conflict", row["hold_reasons"])
        self.assertEqual(row["mapping_candidates"], [])
        self.assertTrue(all("business_meaning_conflict" in t["hold_reasons"] for t in row["term_candidates"]))

    def test_no_business_context_holds_alias_matches(self):
        self.edit("business-v1.json", lambda d: d.update(column_definitions=[]))
        row = self.result_row("c-amount")
        self.assertEqual(row["recommendation_status"], "held")
        self.assertIn("business_context_missing", row["hold_reasons"])

    def test_unknown_column_is_held(self):
        row = self.result_row("c-unknown")
        self.assertEqual(row["term_candidates"], [])
        self.assertEqual(set(row["hold_reasons"]), {"no_dictionary_term", "business_context_missing"})

    def test_absent_target_never_approves_or_executes(self):
        result = self.execute(self.inputs / "request-no-target.json")["envelope"]["result"]
        for row in result["columns"]:
            self.assertEqual(row["mapping_candidates"], [])
            self.assertIn("target_not_provided", row["mapping_hold_reasons"])
            self.assertFalse(row["approval_eligible"])
            self.assertFalse(row["execution_eligible"])

    def test_unconfirmed_target_does_not_generate_mapping(self):
        self.edit("request-v1.json", lambda d: d["target"].update(confirmed=False))
        row = self.result_row("c-name")
        self.assertEqual(row["mapping_candidates"], [])
        self.assertEqual(row["mapping_hold_reasons"], ["target_not_confirmed"])

    def test_business_revision_preserves_metadata_and_previous_result(self):
        meta = (self.inputs / "metadata-v1.json").read_bytes()
        old = self.execute()
        old_bytes = old["path"].read_bytes()
        new = self.execute(self.inputs / "request-v2.json")
        self.assertNotEqual(old["path"], new["path"])
        self.assertEqual(old["path"].read_bytes(), old_bytes)
        self.assertEqual((self.inputs / "metadata-v1.json").read_bytes(), meta)
        self.assertEqual(old["envelope"]["input"]["metadata"], new["envelope"]["input"]["metadata"])
        old_row = old["envelope"]["result"]["columns"][1]
        new_row = new["envelope"]["result"]["columns"][1]
        self.assertEqual([t["id"] for t in old_row["term_candidates"] if not t["hold_reasons"]], ["t-cash-balance"])
        self.assertEqual([t["id"] for t in new_row["term_candidates"] if not t["hold_reasons"]], ["t-claim-benefit"])
        self.assertIn("no_target_term_match", new_row["mapping_hold_reasons"])

    def test_rerun_reuses_exact_bytes(self):
        first = self.execute()
        before = first["path"].stat().st_mtime_ns
        second = self.execute()
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(first["path"].stat().st_mtime_ns, before)
        self.assertEqual(len(list(self.output.rglob("*.json"))), 1)

    def test_changed_content_under_same_request_is_conflict(self):
        first = self.execute()
        original = first["path"].read_bytes()
        self.edit("business-v1.json", lambda d: d.update(description="変更された合成定義"))
        with self.assertRaises(RequestConflict):
            self.execute()
        self.assertEqual(first["path"].read_bytes(), original)

    def test_concurrent_same_request_publishes_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(lambda _: self.execute(), range(8)))
        self.assertEqual(sum(not r["reused"] for r in outcomes), 1)
        self.assertEqual(len(list(self.output.rglob("*.json"))), 1)
        self.assertEqual(list(self.output.rglob("*.tmp")), [])

    def test_concurrent_different_inputs_same_id_conflicts(self):
        other = self.root / "other-inputs"
        shutil.copytree(self.inputs, other)
        path = other / "business-v1.json"
        doc = read_json(path)
        doc["description"] = "different synthetic definition"
        path.write_text(json.dumps(doc), encoding="utf-8")

        def attempt(request):
            try:
                self.execute(request)
                return "saved"
            except RequestConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, [self.request, other / "request-v1.json"]))
        self.assertCountEqual(results, ["saved", "conflict"])

    def test_version_mismatch_blocked(self):
        self.edit("request-v1.json", lambda d: d["dictionary"].update(version="different"))
        self.assert_blocked()

    def test_unknown_domain_blocked(self):
        self.edit("dictionary-v1.json", lambda d: d["terms"][0].update(domain_id="unknown"))
        self.assert_blocked()

    def test_duplicate_source_identifier_blocked(self):
        self.edit("metadata-v1.json", lambda d: d["columns"].append(copy.deepcopy(d["columns"][0])))
        self.assert_blocked()

    def test_unsafe_reference_and_identifier_blocked(self):
        for field, value in (("metadata", {"path": "../outside.json", "version": "meta-v1"}),
                             ("request_id", "../escape")):
            with self.subTest(field=field):
                shutil.copy(EXAMPLES / "request-v1.json", self.request)
                self.edit("request-v1.json", lambda d: d.update({field: value}))
                self.assert_blocked()

    def test_duplicate_json_keys_and_non_finite_blocked(self):
        for content in ('{"version":"x","version":"y"}', '{"version":NaN}', '{"version":Infinity}'):
            with self.subTest(content=content):
                (self.inputs / "metadata-v1.json").write_text(content, encoding="utf-8")
                self.assert_blocked()

    def test_malformed_shapes_blocked(self):
        for filename, callback in [
            ("metadata-v1.json", lambda d: d["columns"][0].update(data_type=[])),
            ("dictionary-v1.json", lambda d: d["terms"].append(None)),
            ("request-v1.json", lambda d: d["target"].update(provided=1)),
            ("business-v1.json", lambda d: d.update(unexpected="field")),
        ]:
            with self.subTest(filename=filename):
                shutil.copy(EXAMPLES / filename, self.inputs / filename)
                self.edit(filename, callback)
                self.assert_blocked()
                shutil.copy(EXAMPLES / filename, self.inputs / filename)

    def test_domain_type_conflict_held(self):
        self.edit("metadata-v1.json", lambda d: d["columns"][0].update(data_type="integer"))
        row = self.result_row("c-name")
        self.assertEqual(row["recommendation_status"], "held")
        self.assertIn("source_domain_type_conflict", row["hold_reasons"])
        self.assertEqual(row["mapping_candidates"], [])

    def test_ambiguous_term_is_held(self):
        def add_term(d):
            item = copy.deepcopy(d["terms"][0])
            item["id"] = "t-customer-alternative"
            d["terms"].append(item)
        self.edit("dictionary-v1.json", add_term)
        row = self.result_row("c-name")
        self.assertIn("ambiguous_standard_term", row["hold_reasons"])
        self.assertEqual(row["mapping_candidates"], [])

    def test_target_type_conflict_held(self):
        self.edit("target-v1.json", lambda d: d["tables"][0]["columns"][0].update(data_type="integer"))
        self.assertEqual(self.result_row("c-name")["mapping_hold_reasons"], ["target_type_conflict"])

    def test_ambiguous_target_held(self):
        def add_target(d):
            col = copy.deepcopy(d["tables"][0]["columns"][0])
            col["column_id"] = "another-name"
            d["tables"][0]["columns"].append(col)
        self.edit("target-v1.json", add_target)
        self.assertEqual(self.result_row("c-name")["mapping_hold_reasons"], ["ambiguous_target"])

    def test_duplicate_target_assignment_held(self):
        def add_column(d):
            col = copy.deepcopy(d["columns"][0])
            col["column_id"] = "c-name-2"
            d["columns"].append(col)
        def add_definition(d):
            definition = copy.deepcopy(d["column_definitions"][0])
            definition["column_id"] = "c-name-2"
            d["column_definitions"].append(definition)
        self.edit("metadata-v1.json", add_column)
        self.edit("business-v1.json", add_definition)
        for col in self.execute()["envelope"]["result"]["columns"]:
            if col["source"]["column_id"] in {"c-name", "c-name-2"}:
                self.assertEqual(col["mapping_candidates"], [])
                self.assertEqual(col["mapping_hold_reasons"], ["duplicate_target_assignment"])

    def test_no_fabricated_model_scores_codes_or_approval(self):
        envelope = self.execute()["envelope"]
        provenance = envelope["result"]["provenance"]
        self.assertFalse(provenance["model_used"])
        self.assertFalse(provenance["mock_used"])
        self.assertIsNone(provenance["model_version"])
        for row in envelope["result"]["columns"]:
            self.assertEqual(row["scores"], {"public_standard": None, "business_concept": None})
            self.assertFalse(row["approval_eligible"])
            self.assertFalse(row["execution_eligible"])
            self.assertEqual(row["code_processing"]["recommendations"], [])
        yn = envelope["result"]["columns"][3]["term_candidates"][0]["linked_domain"]
        self.assertEqual(yn["allowed_values"], ["Y", "N"])

    def test_invalid_outputs_blocked_before_publication(self):
        bundle = LocalPreparedInputs().load(self.request)
        reference = self.execute()["envelope"]
        mutations = [
            lambda e: e["result"]["columns"][0].update(approval_eligible=True),
            lambda e: e["result"]["columns"][0]["scores"].update(public_standard=0.99),
            lambda e: e["result"]["columns"][0]["term_candidates"][0]["linked_domain"].update(id="fabricated"),
            lambda e: e["result"]["columns"][0]["mapping_candidates"][0].update(target_column_id="fabricated"),
            lambda e: e["result"]["provenance"].update(model_used=True),
            lambda e: e.update(input_fingerprint="wrong"),
            lambda e: e["result"]["columns"].pop(),
        ]
        for i, mutate in enumerate(mutations):
            with self.subTest(index=i):
                envelope = copy.deepcopy(reference)
                mutate(envelope)
                output = self.root / f"invalid-{i}"
                with self.assertRaises(ValidationError):
                    LocalResultStore(output).save(envelope)
                self.assertFalse(output.exists())
        self.assertEqual(bundle, reference["input"])

    def test_tampered_saved_result_is_rejected(self):
        first = self.execute()
        saved = read_json(first["path"])
        saved["result"]["columns"][0]["scores"]["public_standard"] = 1.0
        first["path"].write_text(json.dumps(saved), encoding="utf-8")
        with self.assertRaises(ValidationError):
            self.execute()

    def test_scoped_request_ids_do_not_collide(self):
        first = self.execute()
        self.edit("request-v1.json", lambda d: d.update(customer_id="other-demo-customer"))
        second = self.execute()
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(len(list(self.output.rglob("*.json"))), 2)

    def test_cli_exit_codes(self):
        command = [sys.executable, "-m", "maps_ai_pipeline", "--request", str(self.request),
                   "--output", str(self.output)]
        success = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(json.loads(success.stdout)["status"], "saved")
        self.edit("business-v1.json", lambda d: d.update(description="new synthetic text"))
        conflict = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(conflict.returncode, 3)
        self.assertEqual(json.loads(conflict.stdout)["status"], "request_conflict")
        self.edit("request-v1.json", lambda d: d.update(request_id="../bad"))
        invalid = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(invalid.returncode, 2)

    def test_ambiguous_word_candidates_all_held(self):
        def add_word(d):
            word = copy.deepcopy(d["words"][0])
            word["id"] = "w-customer-alternative"
            d["words"].append(word)
        self.edit("dictionary-v1.json", add_word)
        row = self.result_row("c-name")
        words = [w for w in row["word_candidates"] if w["id"] in {"w-customer", "w-customer-alternative"}]
        self.assertEqual(len(words), 2)
        self.assertTrue(all("ambiguous_standard_word" in w["hold_reasons"] for w in words))

    def test_duplicate_dictionary_id_blocked(self):
        self.edit("dictionary-v1.json", lambda d: d["words"].append(copy.deepcopy(d["words"][0])))
        self.assert_blocked()

    def test_unknown_target_term_blocked(self):
        self.edit("target-v1.json", lambda d: d["tables"][0]["columns"][0].update(term_id="unknown"))
        self.assert_blocked()

    def test_unknown_business_column_blocked(self):
        self.edit("business-v1.json", lambda d: d["column_definitions"][0].update(column_id="unknown"))
        self.assert_blocked()

    def test_failed_atomic_publication_leaves_no_completed_result(self):
        from maps_ai_pipeline.contracts import StorageError
        with patch("maps_ai_pipeline.adapters.os.link", side_effect=OSError("synthetic failure")):
            with self.assertRaises(StorageError):
                self.execute()
        self.assertEqual(list(self.output.rglob("*.json")), [])
        self.assertEqual(list(self.output.rglob("*.tmp")), [])
        self.assertFalse(self.execute()["reused"])

    def test_reserved_windows_identifier_blocked(self):
        self.edit("request-v1.json", lambda d: d.update(request_id="CON"))
        self.assert_blocked()


if __name__ == "__main__":
    unittest.main()
