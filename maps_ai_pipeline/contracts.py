"""Strict synthetic input contracts. Scores and approvals are never inferred."""

import hashlib
import json
import re
from pathlib import Path

SCHEMA_VERSION = "1.0"
RULE_VERSION = "exact-alias-concept-type-v1"
TYPES = {"string", "integer", "decimal", "date", "datetime", "boolean"}


class ValidationError(ValueError):
    pass


class RequestConflict(ValueError):
    pass


class StorageError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def keys(value, required, label):
    require(type(value) is dict and set(value) == set(required.split()), label)


def text(value, label):
    require(type(value) is str and bool(value.strip()) and len(value) <= 256, label)


def identifier(value):
    require(type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value),
            "invalid identifier")
    require(value.upper() not in {"CON", "PRN", "AUX", "NUL",
                                 *[f"COM{i}" for i in range(1, 10)],
                                 *[f"LPT{i}" for i in range(1, 10)]}, "reserved local identifier")


def versioned(value, fields):
    keys(value, "version " + fields, "invalid document fields")
    text(value["version"], "invalid document version")


def rows(value, allow_empty=True):
    require(type(value) is list and (allow_empty or bool(value)), "invalid rows")


def unique(items):
    require(len(items) == len(set(items)), "duplicate identifier")


def source_key(column):
    return tuple(column[k] for k in ("system_id", "table_id", "column_id"))


def digest(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def _constant(_):
    raise ValidationError("non-finite JSON number")


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"),
                          object_pairs_hook=_object, parse_constant=_constant)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValidationError("cannot read valid JSON input") from None


def validate_bundle(bundle):
    keys(bundle, "request metadata dictionary business_definition target", "invalid bundle")
    request = bundle["request"]
    keys(request, "schema_version customer_id project_id batch_id request_id metadata dictionary business_definition target",
         "invalid request fields")
    require(request["schema_version"] == SCHEMA_VERSION, "unsupported schema version")
    for field in ("customer_id", "project_id", "batch_id", "request_id"):
        identifier(request[field])
    for name in ("metadata", "dictionary", "business_definition"):
        ref = request[name]
        keys(ref, "path version", "invalid document reference")
        text(ref["path"], "invalid document path")
        text(ref["version"], "invalid reference version")
        require(type(bundle[name]) is dict and bundle[name].get("version") == ref["version"],
                "reference version mismatch")
    target_ref = request["target"]
    keys(target_ref, "provided confirmed path version", "invalid target reference")
    require(type(target_ref["provided"]) is bool and type(target_ref["confirmed"]) is bool,
            "invalid target flags")
    if target_ref["provided"]:
        text(target_ref["path"], "invalid target path")
        text(target_ref["version"], "invalid target version")
        require(type(bundle["target"]) is dict and
                bundle["target"].get("version") == target_ref["version"], "target version mismatch")
    else:
        require(target_ref == {"provided": False, "confirmed": False, "path": None, "version": None}
                and bundle["target"] is None, "absent target must have null reference")

    meta = bundle["metadata"]
    versioned(meta, "columns")
    rows(meta["columns"], allow_empty=False)
    for col in meta["columns"]:
        keys(col, "system_id table_id column_id name data_type", "invalid source column")
        for field in ("system_id", "table_id", "column_id"):
            identifier(col[field])
        text(col["name"], "invalid column name")
        require(col["data_type"] in TYPES, "unsupported source data type")
    unique([source_key(col) for col in meta["columns"]])

    dictionary = bundle["dictionary"]
    external = dictionary.get("origin") == "external_reference"
    versioned(dictionary, "origin words terms domains" + (" binding_version business_bindings" if external else ""))
    require(dictionary["origin"] in {"synthetic", "external_reference"}, "unsupported dictionary origin")
    if external:
        text(dictionary["binding_version"], "missing business binding version")
        require(type(dictionary["business_bindings"]) is dict and
                dictionary["business_bindings"].get("version") == dictionary["binding_version"], "invalid binding snapshot")
    for name in ("words", "terms", "domains"):
        rows(dictionary[name])
        unique([item.get("id") for item in dictionary[name] if type(item) is dict])
    for domain in dictionary["domains"]:
        keys(domain, "id name data_type allowed_values", "invalid domain")
        identifier(domain["id"])
        text(domain["name"], "invalid domain name")
        require(domain["data_type"] in TYPES or (external and domain["data_type"] is None), "unsupported domain data type")
        require(domain["allowed_values"] is None or
                (type(domain["allowed_values"]) is list and all(type(v) is str for v in domain["allowed_values"])),
                "invalid allowed code values")
    domain_ids = {d["id"] for d in dictionary["domains"]}
    word_ids = set()
    for word in dictionary["words"]:
        keys(word, "id name aliases concept_id", "invalid standard word")
        _named_candidate(word, external)
        word_ids.add(word["id"])
    for term in dictionary["terms"]:
        keys(term, "id name aliases concept_id word_ids domain_id", "invalid standard term")
        _named_candidate(term, external)
        rows(term["word_ids"], allow_empty=external)
        require(all(type(w) is str for w in term["word_ids"]), "invalid term word IDs")
        unique(term["word_ids"])
        require(set(term["word_ids"]) <= word_ids, "unknown word link")
        require(term["domain_id"] in domain_ids, "unknown linked domain")

    business = bundle["business_definition"]
    versioned(business, "description column_definitions")
    text(business["description"], "missing business description")
    rows(business["column_definitions"])
    source_keys = {source_key(c) for c in meta["columns"]}
    for definition in business["column_definitions"]:
        keys(definition, "system_id table_id column_id concept_id description", "invalid business definition")
        require(source_key(definition) in source_keys, "unknown business source column")
        require(definition["concept_id"] is None or type(definition["concept_id"]) is str,
                "invalid business concept")
        if definition["concept_id"] is not None:
            identifier(definition["concept_id"])
        text(definition["description"], "missing column definition")
    unique([source_key(d) for d in business["column_definitions"]])

    if target_ref["provided"]:
        target = bundle["target"]
        versioned(target, "tables")
        rows(target["tables"], allow_empty=False)
        table_ids = []
        for table in target["tables"]:
            keys(table, "table_id name columns", "invalid target table")
            identifier(table["table_id"])
            text(table["name"], "invalid target table name")
            table_ids.append(table["table_id"])
            rows(table["columns"], allow_empty=False)
            column_ids = []
            for col in table["columns"]:
                keys(col, "column_id name data_type term_id", "invalid target column")
                identifier(col["column_id"])
                text(col["name"], "invalid target column name")
                require(col["data_type"] in TYPES, "unsupported target data type")
                require(col["term_id"] in {t["id"] for t in dictionary["terms"]}, "unknown target term")
                column_ids.append(col["column_id"])
            unique(column_ids)
        unique(table_ids)


def _named_candidate(item, external=False):
    identifier(item["id"])
    if not (external and item["concept_id"] is None):
        identifier(item["concept_id"])
    text(item["name"], "invalid dictionary name")
    rows(item["aliases"], allow_empty=False)
    for alias in item["aliases"]:
        text(alias, "invalid alias")
