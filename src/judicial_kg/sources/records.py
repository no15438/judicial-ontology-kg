"""Lossless published-field records and evidence offsets, not semantic annotations."""

import hashlib
import json
import re


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_hash(value) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def pointer_get(value, pointer: str):
    if not pointer.startswith("/"):
        raise ValueError("Expected absolute JSON pointer")
    for part in pointer[1:].split("/"):
        if re.search(r"~(?![01])", part):
            raise ValueError("Invalid JSON pointer escape")
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", key):
                raise ValueError("Invalid JSON pointer array index")
            value = value[int(key)]
        else:
            value = value[key]
    return value


def document(dataset: str, source: dict, fields: dict[str, tuple[str, str]], **metadata) -> dict:
    reserved = {"schema_version", "document_id", "dataset", "source", "fields", "reference_status"}
    if reserved & metadata.keys():
        raise ValueError("Metadata cannot override provenance or reference status")
    if not fields or any(not isinstance(text, str) for _, text in fields.values()):
        raise ValueError("Expected nonempty field mapping of unchanged source strings")
    return {
        "schema_version": 1,
        "document_id": f"{dataset}:{canonical_hash(source)}",
        "dataset": dataset,
        "source": json.loads(json.dumps(source)),
        "fields": [
            {
                "name": name,
                "source_pointer": pointer,
                "text": text,
                "sha256": sha256(text.encode()),
                "characters": len(text),
                "offset_space": "decoded_published_field_unicode_codepoints",
            }
            for name, (pointer, text) in fields.items()
        ],
        "reference_status": "not_graph_gold",
        **metadata,
    }


def evidence_span(doc: dict, field_name: str, start: int, end: int) -> dict:
    fields = [f for f in doc["fields"] if f["name"] == field_name]
    if len(fields) != 1:
        raise ValueError("Evidence field must resolve exactly once")
    field = fields[0]
    text = field["text"]
    if sha256(text.encode()) != field["sha256"] or len(text) != field["characters"]:
        raise ValueError("Field content changed after provenance registration")
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
        raise ValueError("Invalid evidence range; requires nonempty half-open character span")
    return {
        "document_id": doc["document_id"],
        "field": field_name,
        "field_sha256": field["sha256"],
        "source_pointer": field["source_pointer"],
        "start": start,
        "end": end,
        "quote": text[start:end],
        "offset_space": field["offset_space"],
    }


def verify_evidence(doc: dict, span: dict) -> None:
    expected = evidence_span(doc, span["field"], span["start"], span["end"])
    if span != expected:
        raise ValueError("Evidence does not match the named document and source field")
