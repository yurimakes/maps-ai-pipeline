"""Deterministic dictionary baseline: candidates only, with explicit abstention."""

import re

from . import __version__
from .contracts import (RULE_VERSION, SCHEMA_VERSION, ValidationError, digest, keys,
                        require, source_key, validate_bundle)


def normalize(value):
    return value.strip().casefold()


def matches(item, name, tokens=False):
    aliases = [item["name"], *item["aliases"]]
    names = {normalize(name)}
    if tokens:
        names.update(normalize(p) for p in re.split(r"[_\s]+", name) if p)
    return sorted({a for a in aliases if normalize(a) in names})


def _score():
    return {"public_standard": None, "business_concept": None}


def recommend(bundle):
    validate_bundle(bundle)
    dictionary = bundle["dictionary"]
    domains = {d["id"]: d for d in dictionary["domains"]}
    definitions = {source_key(d): d for d in bundle["business_definition"]["column_definitions"]}
    target_ref = bundle["request"]["target"]
    results = []
    for col in bundle["metadata"]["columns"]:
        definition = definitions.get(source_key(col))
        concept = definition["concept_id"] if definition else None
        row = {"source": dict(col), "word_candidates": [], "term_candidates": [],
               "mapping_candidates": [], "recommendation_status": "held",
               "hold_reasons": [], "mapping_hold_reasons": [],
               "scores": _score(), "review_status": "not_reviewed",
               "approval_eligible": False, "execution_eligible": False,
               "code_processing": {"mode": "reference_only", "recommendations": []}}
        for kind, tokens in (("words", True), ("terms", False)):
            output_name = "word_candidates" if kind == "words" else "term_candidates"
            for item in dictionary[kind]:
                hit = matches(item, col["name"], tokens)
                if not hit:
                    continue
                evidence = {"method": "exact_alias", "matched_aliases": hit,
                            "dictionary_origin": dictionary["origin"],
                            "dictionary_version": dictionary["version"],
                            "business_definition_version": bundle["business_definition"]["version"],
                            "business_concept_id": concept, "source_concept_id": item["concept_id"],
                            "rule_version": RULE_VERSION}
                reasons = []
                if concept is None:
                    reasons.append("business_context_missing")
                elif item["concept_id"] is None:
                    reasons.append("dictionary_concept_unverified")
                elif concept != item["concept_id"]:
                    reasons.append("business_meaning_conflict")
                candidate = {"id": item["id"], "name": item["name"],
                             "registration_status": "existing_synthetic_reference" if dictionary["origin"] == "synthetic" else "existing_external_reference",
                             "evidence": evidence, "hold_reasons": reasons, "scores": _score()}
                if kind == "terms":
                    candidate["linked_domain"] = dict(domains[item["domain_id"]])
                    candidate["word_ids"] = list(item["word_ids"])
                    if domains[item["domain_id"]]["data_type"] != col["data_type"]:
                        reasons.append("source_domain_type_conflict")
                row[output_name].append(candidate)
        valid = [t for t in row["term_candidates"] if not t["hold_reasons"]]
        if len(valid) > 1:
            for item in valid:
                item["hold_reasons"].append("ambiguous_standard_term")
            valid = []
        eligible_words = [w for w in row["word_candidates"] if not w["hold_reasons"]]
        for item in eligible_words:
            alternatives = [w for w in eligible_words
                            if set(map(normalize, w["evidence"]["matched_aliases"])) &
                            set(map(normalize, item["evidence"]["matched_aliases"]))]
            if len(alternatives) > 1:
                item["hold_reasons"].append("ambiguous_standard_word")
        if valid:
            row["recommendation_status"] = "candidate"
        else:
            if not row["term_candidates"]:
                row["hold_reasons"].append("no_dictionary_term")
            row["hold_reasons"].extend(sorted({r for t in row["term_candidates"] for r in t["hold_reasons"]}))
            if concept is None and "business_context_missing" not in row["hold_reasons"]:
                row["hold_reasons"].append("business_context_missing")
        if not target_ref["provided"]:
            row["mapping_hold_reasons"].append("target_not_provided")
        elif not target_ref["confirmed"]:
            row["mapping_hold_reasons"].append("target_not_confirmed")
        elif not valid:
            row["mapping_hold_reasons"].append("standard_term_unresolved")
        else:
            term = valid[0]
            targets = [(table, target) for table in bundle["target"]["tables"]
                       for target in table["columns"] if target["term_id"] == term["id"]]
            compatible = [(table, target) for table, target in targets
                          if target["data_type"] == col["data_type"]]
            if not targets:
                row["mapping_hold_reasons"].append("no_target_term_match")
            elif not compatible:
                row["mapping_hold_reasons"].append("target_type_conflict")
            elif len(compatible) > 1:
                row["mapping_hold_reasons"].append("ambiguous_target")
            else:
                table, target = compatible[0]
                row["mapping_candidates"].append({"target_table_id": table["table_id"],
                    "target_column_id": target["column_id"], "term_id": term["id"],
                    "target_version": bundle["target"]["version"],
                    "evidence": {"method": "same_term_id_and_exact_data_type",
                                 "rule_version": RULE_VERSION}, "scores": _score()})
        results.append(row)
    used_targets = {}
    for row in results:
        for mapping in row["mapping_candidates"]:
            key = (mapping["target_table_id"], mapping["target_column_id"])
            used_targets.setdefault(key, []).append(row)
    for group in used_targets.values():
        if len(group) > 1:
            for row in group:
                row["mapping_candidates"] = []
                row["mapping_hold_reasons"].append("duplicate_target_assignment")
    return results


def _build(bundle):
    request = bundle["request"]
    return {"schema_version": SCHEMA_VERSION, "input_fingerprint": digest(bundle),
            "input": bundle, "result": {
                "request_identity": {k: request[k] for k in ("customer_id", "project_id", "batch_id", "request_id")},
                "status": "recommendation_completed", "result_version": "result-v1",
                "provenance": {
                    "input_used": True, "metadata_version": bundle["metadata"]["version"],
                    "dictionary_used": True, "dictionary_version": bundle["dictionary"]["version"],
                    "business_definition_version": bundle["business_definition"]["version"],
                    "target_version": request["target"]["version"],
                    "rules_used": True, "rules_version": RULE_VERSION,
                    "pipeline_version": __version__, "model_used": False,
                    "model_version": None, "mock_used": False},
                "columns": recommend(bundle)}}


def validate_envelope(envelope):
    """Checks both structural and semantic output integrity before storage/reuse.

    This baseline regenerates the deterministic contract and compares it exactly,
    rejecting fabricated scores, domains, mappings, provenance and approvals.
    A future model adapter needs a separately validated output contract.
    """
    try:
        keys(envelope, "schema_version input_fingerprint input result", "invalid result envelope")
        validate_bundle(envelope["input"])
        require(envelope == _build(envelope["input"]), "result fails dictionary/rule output contract")
        require(digest(envelope) == digest(_build(envelope["input"])), "result field type mismatch")
    except (TypeError, KeyError):
        raise ValidationError("invalid output field type") from None


def run(request_path, output_root=None, *, inputs=None, store=None):
    from .adapters import LocalPreparedInputs, LocalResultStore

    bundle = (inputs or LocalPreparedInputs()).load(request_path)
    envelope = _build(bundle)
    validate_envelope(envelope)
    if store is None:
        require(output_root is not None, "missing output folder")
        store = LocalResultStore(output_root)
    path, reused = store.save(envelope)
    return {"path": path, "reused": reused, "envelope": envelope}
