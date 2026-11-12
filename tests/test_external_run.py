import copy
import json
import tempfile
import unittest
from pathlib import Path

from maps_ai_pipeline.external_run import ExternalPreparedInputs
from maps_ai_pipeline.contracts import RequestConflict, ValidationError, read_json
from maps_ai_pipeline.pipeline import run


class ExternalFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.doc = {
            "words": [{"공통표준단어명":"합성단어","공통표준단어영문약어명":"SYN", "_record":{"id":"w1","search_enabled":True}}],
            "terms": [{"공통표준용어명":"합성용어","공통표준용어영문약어명":"SYN_TERM","공통표준도메인명":"합성문자",
                       "_record":{"id":"t1","search_enabled":True,"active_domain_id":"d1"}}],
            "domains": [{"공통표준도메인명":"합성문자","데이터타입":"VARCHAR", "_record":{"id":"d1","search_enabled":True}}]}
        self.write("reference.json", self.doc)
        self.write("bindings.json", {"version":"binding-v1","bindings":[{"concept_id":"synthetic-concept","term_names":["合成用語不存在","합성용어"],"word_names":[]}]})
        self.metadata = {"version":"meta-v1","columns":[{"system_id":"s1","table_id":"t1","column_id":"c1","name":"SYN_TERM","data_type":"string"}]}
        self.write("meta.json", self.metadata)
        self.business = {"version":"business-v1","description":"합성 업무 규칙","column_definitions":[
            {"system_id":"s1","table_id":"t1","column_id":"c1","concept_id":"synthetic-concept","description":"합성 의미"}]}
        self.write("business.json", self.business)
        self.request = {"schema_version":"1.0","customer_id":"demo","project_id":"p1","batch_id":"b1","request_id":"r1",
                        "metadata":{"path":"meta.json","version":"meta-v1"},"dictionary":{"path":"in-memory.json","version":"ref-v1"},
                        "business_definition":{"path":"business.json","version":"business-v1"},
                        "target":{"provided":False,"confirmed":False,"path":None,"version":None}}
        self.write("request.json", self.request)

    def write(self, name, data):
        (self.root/name).write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")

    def execute(self):
        inputs=ExternalPreparedInputs(self.root/"reference.json",self.root/"bindings.json")
        return run(self.root/"request.json",self.root/"results",inputs=inputs)

    def test_external_dictionary_flows_through_existing_validator_store(self):
        before=(self.root/"reference.json").read_bytes()
        first=self.execute()
        row=first["envelope"]["result"]["columns"][0]
        self.assertEqual(row["recommendation_status"],"candidate")
        self.assertEqual(row["term_candidates"][0]["linked_domain"]["id"],"d1")
        self.assertEqual(row["term_candidates"][0]["word_ids"],[])
        self.assertEqual(row["mapping_hold_reasons"],["target_not_provided"])
        self.assertEqual(before,(self.root/"reference.json").read_bytes())
        self.assertTrue(self.execute()["reused"])

    def test_business_conflict_is_held(self):
        self.business["column_definitions"][0]["concept_id"]="different-meaning"
        self.write("business.json",self.business)
        row=self.execute()["envelope"]["result"]["columns"][0]
        self.assertIn("business_meaning_conflict",row["hold_reasons"])

    def test_no_binding_does_not_invent_concept(self):
        self.write("bindings.json",{"version":"v1","bindings":[]})
        row=self.execute()["envelope"]["result"]["columns"][0]
        self.assertIn("dictionary_concept_unverified",row["hold_reasons"])

    def test_unknown_domain_type_held(self):
        self.doc["domains"][0]["데이터타입"]="UNKNOWN"
        self.write("reference.json",self.doc)
        row=self.execute()["envelope"]["result"]["columns"][0]
        self.assertIn("source_domain_type_conflict",row["hold_reasons"])

    def test_changed_reference_same_request_conflicts(self):
        first=self.execute()
        before=first["path"].read_bytes()
        self.doc["domains"][0]["데이터타입"]="INTEGER"
        self.write("reference.json",self.doc)
        with self.assertRaises(RequestConflict): self.execute()
        self.assertEqual(before,first["path"].read_bytes())

    def test_confirmed_target_generates_candidate(self):
        self.request["target"]={"provided":True,"confirmed":True,"path":"target.json","version":"target-v1"}
        self.write("request.json",self.request)
        self.write("target.json",{"version":"target-v1","tables":[{"table_id":"target","name":"합성대상","columns":[
            {"column_id":"col","name":"합성컬럼","data_type":"string","term_id":"t1"}]}]})
        row=self.execute()["envelope"]["result"]["columns"][0]
        self.assertEqual(row["mapping_candidates"][0]["target_column_id"],"col")
        self.assertFalse(row["execution_eligible"])

    def test_new_business_request_preserves_previous_result(self):
        first=self.execute()
        before=first["path"].read_bytes()
        self.request["request_id"]="r2"
        self.request["business_definition"]["version"]="business-v2"
        self.business["version"]="business-v2"
        self.business["column_definitions"][0]["concept_id"]="different"
        self.write("business.json",self.business)
        self.write("request.json",self.request)
        second=self.execute()
        self.assertEqual(before,first["path"].read_bytes())
        self.assertEqual(first["envelope"]["input"]["metadata"],second["envelope"]["input"]["metadata"])
        self.assertEqual(second["envelope"]["result"]["columns"][0]["recommendation_status"],"held")

    def test_changed_unused_binding_same_request_conflicts(self):
        self.execute()
        bindings=read_json(self.root/"bindings.json")
        bindings["bindings"].append({"concept_id":"unrelated","term_names":["다른합성용어"],"word_names":[]})
        self.write("bindings.json",bindings)
        with self.assertRaises(RequestConflict): self.execute()
