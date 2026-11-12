import copy
import unittest

from maps_ai_pipeline.contracts import ValidationError
from maps_ai_pipeline.reference_check import ReferenceDictionary


def fixture():
    return {
        "words": [{"공통표준단어명": "합성단어", "공통표준단어영문약어명": "SYN",
                   "_record": {"id": "w1", "search_enabled": True}},
                  {"공통표준단어명": "폐기합성", "공통표준단어영문약어명": "OLD",
                   "_record": {"id": "w2", "search_enabled": False}}],
        "terms": [{"공통표준용어명": "합성용어", "공통표준용어영문약어명": "SYN_TERM",
                   "공통표준도메인명": "합성문자",
                   "_record": {"id": "t1", "search_enabled": True, "active_domain_id": "d1"}}],
        "domains": [{"공통표준도메인명": "합성문자", "_record": {"id": "d1", "search_enabled": True}}],
    }


class ReferenceTests(unittest.TestCase):
    def test_reference_names_abbreviations_and_source_preserved(self):
        doc = fixture()
        original = copy.deepcopy(doc)
        ref = ReferenceDictionary(doc)
        self.assertEqual(ref.search("words", "syn")[0]["reference_id"], "w1")
        self.assertEqual(ref.search("terms", "合成不存在"), [])
        self.assertEqual(ref.search("terms", "합성용어")[0]["linked_domain_id"], "d1")
        self.assertEqual(ref.check()["self_lookup_checks"], 2)
        self.assertEqual(doc, original)

    def test_retired_not_searchable(self):
        self.assertEqual(ReferenceDictionary(fixture()).search("words", "OLD"), [])

    def test_alias_collision_keeps_all_held(self):
        doc = fixture()
        another = copy.deepcopy(doc["words"][0])
        another["_record"]["id"] = "w3"
        doc["words"].append(another)
        hits = ReferenceDictionary(doc).search("words", "SYN")
        self.assertEqual(len(hits), 2)
        self.assertTrue(all(h["status"] == "held" and not h["approval_eligible"] for h in hits))

    def test_broken_and_retired_domain_rejected(self):
        for retired in (False, True):
            doc = fixture()
            if retired:
                doc["domains"][0]["_record"]["search_enabled"] = False
            else:
                doc["terms"][0]["_record"]["active_domain_id"] = "absent"
            with self.assertRaises(ValidationError):
                ReferenceDictionary(doc)

    def test_domain_code_recommendation_not_allowed(self):
        for kind in ("domains", "codes"):
            with self.assertRaises(ValidationError):
                ReferenceDictionary(fixture()).search(kind, "test")

    def test_non_boolean_search_flag_rejected(self):
        doc = fixture()
        doc["words"][0]["_record"]["search_enabled"] = 1
        with self.assertRaises(ValidationError):
            ReferenceDictionary(doc)
