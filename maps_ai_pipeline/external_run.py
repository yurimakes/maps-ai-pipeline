"""External reference selection adapter for the existing recommendation flow.

Business bindings are explicit test rules, never semantic model inference.
Only matching reference projections enter snapshots; originals stay outside Git.
"""
import argparse
import json
import re
from copy import deepcopy
from pathlib import Path

from .contracts import (RequestConflict, StorageError, ValidationError, identifier,
                        keys, read_json, require, text, validate_bundle)
from .pipeline import run
from .reference_check import ReferenceDictionary


def domain_type(record):
    value = record.get("데이터타입", "").strip().upper()
    if value in {"VARCHAR", "CHAR", "NVARCHAR", "NCHAR", "TEXT"}:
        return "string"
    if value in {"INTEGER", "INT", "BIGINT"}:
        return "integer"
    if value in {"NUMERIC", "DECIMAL", "NUMBER"}:
        scale = record.get("데이터소수점길이", "")
        if scale in {"", "0"}:
            return "integer"
        if type(scale) is str and scale.isdigit():
            return "decimal"
        return None
    return {"DATE": "date", "DATETIME": "datetime", "TIMESTAMP": "datetime", "BOOLEAN": "boolean"}.get(value)


class ExternalPreparedInputs:
    def __init__(self, dictionary_path, bindings_path):
        self.document = read_json(dictionary_path)
        self.reference = ReferenceDictionary(self.document)
        self.bindings = read_json(bindings_path)
        keys(self.bindings, "version bindings", "invalid business bindings")
        text(self.bindings["version"], "invalid binding version")
        require(type(self.bindings["bindings"]) is list, "invalid binding list")
        self.concepts = {"words": {}, "terms": {}}
        for binding in self.bindings["bindings"]:
            keys(binding, "concept_id term_names word_names", "invalid business binding")
            identifier(binding["concept_id"])
            for kind, field in (("terms", "term_names"), ("words", "word_names")):
                require(type(binding[field]) is list, "invalid binding names")
                for name in binding[field]:
                    text(name, "invalid business binding name")
                    require(name not in self.concepts[kind], "duplicate business name binding")
                    self.concepts[kind][name] = binding["concept_id"]

    def load(self, request_path):
        path = Path(request_path).resolve()
        request = read_json(path)
        bundle = {"request": request}
        for name in ("metadata", "business_definition", "target"):
            ref = request[name]
            if name == "target" and ref["provided"] is False:
                bundle[name] = None
                continue
            relative = ref["path"]
            target = (path.parent / relative).resolve()
            require(not Path(relative).is_absolute() and target.is_relative_to(path.parent), "unsafe request reference")
            bundle[name] = read_json(target)
        selected = {"words": {}, "terms": {}, "domains": {}}
        for column in bundle["metadata"]["columns"]:
            for kind in ("words", "terms"):
                queries = {column["name"]}
                if kind == "words":
                    queries.update(re.split(r"[_\s]+", column["name"]))
                for query in filter(None, queries):
                    for hit in self.reference.search(kind, query):
                        identity = hit["reference_id"]
                        record = self.reference.active[kind][identity]
                        name_key, alias_key = (("공통표준단어명", "공통표준단어영문약어명") if kind == "words"
                                               else ("공통표준용어명", "공통표준용어영문약어명"))
                        name = record[name_key]
                        aliases = [v for v in (name, record.get(alias_key, "")) if v.strip()]
                        item = {"id": identity, "name": name, "aliases": sorted(set(aliases)),
                                "concept_id": self.concepts[kind].get(name)}
                        if kind == "terms":
                            domain_id = record["_record"]["active_domain_id"]
                            domain = self.reference.active["domains"][domain_id]
                            item.update(domain_id=domain_id, word_ids=[])
                            selected["domains"][domain_id] = {
                                "id": domain_id, "name": domain["공통표준도메인명"],
                                "data_type": domain_type(domain), "allowed_values": None}
                        selected[kind][identity] = item
        bundle["dictionary"] = {
            "version": request["dictionary"]["version"], "origin": "external_reference",
            "binding_version": self.bindings["version"],
            "business_bindings": deepcopy(self.bindings),
            **{kind: [records[k] for k in sorted(records)] for kind, records in selected.items()}}
        validate_bundle(bundle)
        return deepcopy(bundle)


def main():
    parser = argparse.ArgumentParser(description="External dictionary recommendation baseline")
    parser.add_argument("--dictionary", required=True)
    parser.add_argument("--bindings", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        inputs = ExternalPreparedInputs(args.dictionary, args.bindings)
        result = run(args.request, args.output, inputs=inputs)
        columns = result["envelope"]["result"]["columns"]
        print(json.dumps({"status": "reused" if result["reused"] else "saved", "columns": len(columns),
                          "term_candidate_columns": sum(c["recommendation_status"] == "candidate" for c in columns),
                          "held_columns": sum(c["recommendation_status"] == "held" for c in columns),
                          "mapping_candidate_columns": sum(bool(c["mapping_candidates"]) for c in columns),
                          "model_used": False}))
        return 0
    except RequestConflict:
        print(json.dumps({"status": "request_conflict"}))
        return 3
    except (ValidationError, StorageError, OSError, TypeError, KeyError):
        print(json.dumps({"status": "validation_or_storage_error"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
