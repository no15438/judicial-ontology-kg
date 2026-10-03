"""Structural and source-evidence checks, not a legal semantic judge."""

import re
from datetime import date

from judicial_kg.contracts import COLLECTIONS, Contract
from judicial_kg.sources.records import canonical_hash, sha256, verify_evidence


def validate_source(doc: dict) -> None:
    if (
        not isinstance(doc, dict)
        or type(doc.get("schema_version")) is not int
        or doc["schema_version"] != 1
        or not isinstance(doc.get("source"), dict)
        or not isinstance(doc.get("dataset"), str)
        or not doc["dataset"]
        or not isinstance(doc.get("family_id"), str)
        or not doc["family_id"]
        or not isinstance(doc.get("fields"), list)
        or not doc["fields"]
    ):
        raise ValueError("Invalid source document structure")
    if doc["document_id"] != f"{doc['dataset']}:{canonical_hash(doc['source'])}":
        raise ValueError("Document identity differs from its source fingerprint")
    if doc["reference_status"] != "not_graph_gold" or not doc.get("family_id"):
        raise ValueError("Expected source family and non-gold status")
    names = set()
    for field in doc["fields"]:
        if (
            not isinstance(field, dict)
            or any(
                not isinstance(field.get(key), str)
                for key in ("name", "source_pointer", "text", "sha256", "offset_space")
            )
            or not field["name"]
            or not field["source_pointer"].startswith("/")
            or type(field.get("characters")) is not int
        ):
            raise ValueError("Invalid source field structure")
        if field["name"] in names:
            raise ValueError("Duplicate source field")
        names.add(field["name"])
        if (
            sha256(field["text"].encode()) != field["sha256"]
            or len(field["text"]) != field["characters"]
            or field["offset_space"] != "decoded_published_field_unicode_codepoints"
        ):
            raise ValueError("Changed source field content or coordinate space")


def validate_record(record: dict, doc: dict, contract: Contract) -> list[dict]:
    issues = []

    def issue(code, path):
        # Do not echo source quotes, names, or rejected values into diagnostics.
        issues.append({"code": code, "path": path})

    errors = list(contract.validator.iter_errors(record))
    if errors:
        return [
            {"code": f"schema:{e.validator}", "path": "/" + "/".join(map(str, e.path))}
            for e in errors
        ]
    try:
        validate_source(doc)
    except (ValueError, KeyError, TypeError, AttributeError):
        return [{"code": "invalid_source", "path": "/"}]
    for key in ("document_id", "family_id"):
        if record[key] != doc[key]:
            issue("source_identity_mismatch", f"/{key}")
    fields = {f["name"] for f in doc["fields"]}
    processed = set(record["coverage"]["processed_fields"])
    unprocessed = set(record["coverage"]["unprocessed_fields"])
    if processed & unprocessed or processed | unprocessed != fields:
        issue("field_coverage_not_partition", "/coverage")
    tables = {key: {node["id"]: node for node in record[key]} for key in COLLECTIONS}
    all_ids = [node["id"] for key in COLLECTIONS for node in record[key]]
    if len(all_ids) != len(set(all_ids)):
        issue("duplicate_id", "/")
        return issues
    actors, mentions, contexts = (tables[k] for k in ("actors", "mentions", "contexts"))

    def reference(identifier, table, path):
        if identifier not in table:
            issue("unresolved_reference", path)
            return None
        return table[identifier]

    def contained(span, enclosing):
        return any(
            span["field"] == parent["field"]
            and parent["start"] <= span["start"] < span["end"] <= parent["end"]
            for parent in enclosing
        )

    for key in COLLECTIONS:
        for i, node in enumerate(record[key]):
            path = f"/{key}/{i}"
            if key != "actors":
                spans = [node["evidence"]] if key == "mentions" else node["evidence"]
                for j, span in enumerate(spans):
                    try:
                        verify_evidence(doc, span)
                    except (ValueError, KeyError, TypeError):
                        issue("evidence_mismatch", f"{path}/evidence/{j}")
                    if span["field"] not in processed:
                        issue("evidence_in_unprocessed_field", path)
            if key == "actors":
                for mid in node["mention_ids"]:
                    mention = reference(mid, mentions, f"{path}/mention_ids")
                    if mention and mention["actor_id"] != node["id"]:
                        issue("mention_actor_disagreement", path)
            elif key == "mentions":
                actor = reference(node["actor_id"], actors, f"{path}/actor_id")
                if actor and node["id"] not in actor["mention_ids"]:
                    issue("mention_actor_disagreement", path)
            elif key == "contexts":
                attribution = node["attributed_actor_id"]
                actor = reference(attribution, actors, path) if attribution is not None else None
                if node["kind"] in ("party_claim", "party_defense") and (
                    actor is None or actor["actor_type"] == "court"
                ):
                    issue("party_attribution_required", path)
                if node["kind"].startswith("court_") and actor and actor["actor_type"] != "court":
                    issue("court_attribution_type", path)
                parent_id = node["parent_context_id"]
                if parent_id is not None:
                    parent = reference(parent_id, contexts, f"{path}/parent_context_id")
                    if parent and not all(contained(s, parent["evidence"]) for s in spans):
                        issue("context_outside_parent", path)
                seen = {node["id"]}
                while parent_id in contexts:
                    if parent_id in seen:
                        issue("context_cycle", path)
                        break
                    seen.add(parent_id)
                    parent_id = contexts[parent_id]["parent_context_id"]
            else:
                context = reference(node["context_id"], contexts, f"{path}/context_id")
                if context and not all(contained(s, context["evidence"]) for s in spans):
                    issue("evidence_outside_context", path)
                if key == "roles":
                    reference(node["actor_id"], actors, f"{path}/actor_id")
                elif key == "events":
                    for participant in node["participants"]:
                        reference(participant["actor_id"], actors, f"{path}/participants")
                else:
                    subject = reference(
                        node["subject_id"], actors | tables["events"], f"{path}/subject_id"
                    )
                    if (
                        subject
                        and "context_id" in subject
                        and subject["context_id"] != node["context_id"]
                    ):
                        issue("event_statement_context_mismatch", path)
                    value = node["value"]
                    if not any(value["raw"] in span["quote"] for span in spans):
                        issue("value_raw_not_in_evidence", f"{path}/value/raw")
                    expected_kind = contract.ontology["predicate_value_kinds"].get(
                        node["predicate"]
                    )
                    if expected_kind and value["kind"] != expected_kind:
                        issue("predicate_value_kind", f"{path}/value")
                    normalized = value["normalized"]
                    if value["kind"] in ("money", "rate"):
                        if normalized is not None and not re.fullmatch(
                            r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", normalized
                        ):
                            issue("decimal_string_required", f"{path}/value/normalized")
                        units = (
                            (None, "CNY")
                            if value["kind"] == "money"
                            else (None, "percent", "ratio")
                        )
                        if value["unit"] not in units:
                            issue("unsupported_unit", f"{path}/value/unit")
                    elif value["kind"] == "date":
                        if value["unit"] is not None:
                            issue("unsupported_unit", f"{path}/value/unit")
                        if normalized is not None:
                            try:
                                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", normalized):
                                    raise ValueError
                                date.fromisoformat(normalized)
                            except ValueError:
                                issue("invalid_iso_date", f"{path}/value/normalized")
                            raw_date = re.fullmatch(
                                r"([0-9]{4})(?:年|[-/.])([0-9]{1,2})(?:月|[-/.])([0-9]{1,2})日?",
                                value["raw"],
                            )
                            supported = None
                            if raw_date:
                                try:
                                    supported = date(*map(int, raw_date.groups())).isoformat()
                                except ValueError:
                                    pass
                            if supported != normalized:
                                issue("date_normalization_not_supported_by_raw", f"{path}/value")
                    elif normalized is not None or value["unit"] is not None:
                        issue("text_must_remain_raw", f"{path}/value")
    return issues
