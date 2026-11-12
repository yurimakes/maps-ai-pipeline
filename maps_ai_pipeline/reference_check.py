"""Read-only adapter for external reference dictionaries; no source redistribution."""
import argparse
import json
from collections import defaultdict
from pathlib import Path

from .contracts import ValidationError, read_json, require


class ReferenceDictionary:
    def __init__(self, document):
        require(type(document) is dict, "invalid dictionary")
        self.active = {}
        self.index = {}
        self.total = {}
        for kind, name, abbreviation in (
            ("words", "공통표준단어명", "공통표준단어영문약어명"),
            ("terms", "공통표준용어명", "공통표준용어영문약어명"),
            ("domains", "공통표준도메인명", None),
        ):
            records = document.get(kind)
            require(type(records) is list, "missing reference records")
            active = {}
            index = defaultdict(list)
            seen = set()
            for record in records:
                require(type(record) is dict, "invalid reference record")
                management = record.get("_record")
                require(type(management) is dict, "missing record metadata")
                identity = management.get("id")
                require(type(identity) is str and bool(identity) and identity not in seen,
                        "missing or duplicate reference ID")
                seen.add(identity)
                enabled = management.get("search_enabled")
                require(type(enabled) is bool, "invalid search flag")
                if not enabled:
                    continue
                require(type(record.get(name)) is str and bool(record[name].strip()), "missing reference name")
                active[identity] = record
                for field in (name, abbreviation):
                    if field is None:
                        continue
                    value = record.get(field, "")
                    require(type(value) is str, "invalid reference alias")
                    key = value.strip().casefold()
                    if key and identity not in index[key]:
                        index[key].append(identity)
            self.active[kind] = active
            self.index[kind] = index
            self.total[kind] = len(records)
        for record in self.active["terms"].values():
            domain_id = record["_record"].get("active_domain_id")
            require(type(domain_id) is str and domain_id in self.active["domains"], "broken active domain link")
            require(record.get("공통표준도메인명") == self.active["domains"][domain_id]["공통표준도메인명"],
                    "domain name/link mismatch")

    def search(self, kind, query):
        require(kind in {"words", "terms"}, "domains and codes are not recommendation targets")
        require(type(query) is str and bool(query.strip()), "empty query")
        results = []
        for identity in self.index[kind].get(query.strip().casefold(), []):
            record = self.active[kind][identity]
            item = {"reference_id": identity, "method": "exact_reference_name_or_abbreviation",
                    "status": "held", "hold_reasons": ["business_meaning_not_validated"],
                    "public_standard_score": None, "business_concept_score": None,
                    "model_used": False, "approval_eligible": False, "execution_eligible": False}
            if kind == "terms":
                item["linked_domain_id"] = record["_record"]["active_domain_id"]
            results.append(item)
        return results

    def check(self):
        checks = 0
        for kind, field in (("words", "공통표준단어명"), ("terms", "공통표준용어명")):
            for identity, record in self.active[kind].items():
                hits = self.index[kind].get(record[field].strip().casefold(), [])
                require(identity in hits, "reference self-lookup failed")
                checks += 1
        return {"status": "reference_structure_and_lookup_passed",
                "total_records": self.total,
                "active_records": {k: len(v) for k, v in self.active.items()},
                "self_lookup_checks": checks,
                "duplicate_alias_keys": {k: sum(len(v) > 1 for v in self.index[k].values())
                                         for k in ("words", "terms")},
                "business_meaning_validated": False, "mapping_accuracy_measured": False,
                "model_used": False, "code_values_generated": False}


def main():
    parser = argparse.ArgumentParser(description="Read-only external dictionary structure/search check")
    parser.add_argument("--dictionary", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        source = Path(args.dictionary)
        before = source.read_bytes()
        reference = ReferenceDictionary(read_json(source))
        report = reference.check()
        require(source.read_bytes() == before, "source changed during check")
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        print(json.dumps(report, ensure_ascii=True))
    except (ValidationError, OSError, TypeError, KeyError):
        print(json.dumps({"status": "reference_validation_or_output_error"}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
