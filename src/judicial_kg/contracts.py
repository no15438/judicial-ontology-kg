"""Versioned development contract; deliberately no automatic gold promotion."""

import json
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator

from judicial_kg.sources.records import canonical_hash

ONTOLOGY_FILE = "ontology/private-lending-v0.1.json"
SCHEMA_FILE = "schemas/extraction-v0.1.schema.json"
CONTRACT_FILES = {
    "0.1.0": (ONTOLOGY_FILE, SCHEMA_FILE),
    "0.2.0": ("ontology/private-lending-v0.2.json", "schemas/extraction-v0.2.schema.json"),
    "0.3.0": ("ontology/private-lending-v0.3.json", "schemas/extraction-v0.3.schema.json"),
    "0.4.0": ("ontology/private-lending-v0.4.json", "schemas/extraction-v0.4.schema.json"),
}
COLLECTIONS = ("actors", "mentions", "contexts", "roles", "events", "statements")

OBJECT_COLLECTIONS = ("objects", "object_mentions", "object_links")


def collections(record):
    return COLLECTIONS + (OBJECT_COLLECTIONS if record["schema_version"] == "0.4.0" else ())


def build_schema(ontology: dict) -> dict:
    """Generate the checked-in schema from the versioned vocabulary."""
    text = {"type": "string", "minLength": 1}
    nullable = {"anyOf": [text, {"type": "null"}]}
    identifier = {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$"}

    def obj(**properties):
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    def array(items, minimum=0):
        return {"type": "array", "items": items, "minItems": minimum, "uniqueItems": True}

    def enum(key):
        return {"enum": ontology[key]}

    span = obj(
        document_id=text,
        field=text,
        field_sha256={"type": "string", "pattern": "^[a-f0-9]{64}$"},
        source_pointer={"type": "string", "pattern": "^/"},
        start={"type": "integer", "minimum": 0},
        end={"type": "integer", "minimum": 1},
        quote=text,
        offset_space={"const": "decoded_published_field_unicode_codepoints"},
    )
    evidence = array({"$ref": "#/$defs/span"}, 1)
    contextual = {"context_id": identifier, "polarity": enum("polarities"), "evidence": evidence}
    definitions = {
        "span": span,
        "actor": obj(
            id=identifier, actor_type=enum("actor_types"), mention_ids=array(identifier, 1)
        ),
        "mention": obj(
            id=identifier,
            actor_id=identifier,
            kind=enum("mention_kinds"),
            evidence={"$ref": "#/$defs/span"},
        ),
        "context": obj(
            id=identifier,
            kind=enum("context_kinds"),
            attributed_actor_id={"anyOf": [identifier, {"type": "null"}]},
            parent_context_id={"anyOf": [identifier, {"type": "null"}]},
            evidence=evidence,
        ),
        "role": obj(id=identifier, actor_id=identifier, role=enum("roles"), **contextual),
        "event": obj(
            id=identifier,
            event_type=enum("event_types"),
            participants=array(obj(actor_id=identifier, role=enum("participant_roles")), 1),
            **contextual,
        ),
        "statement": obj(
            id=identifier,
            subject_id=identifier,
            predicate=enum("predicates"),
            value=obj(
                kind={"enum": ["money", "rate", "date", "text"]},
                raw=text,
                normalized=nullable,
                unit=nullable,
            ),
            **contextual,
        ),
    }
    if ontology["version"] in {"0.2.0", "0.3.0", "0.4.0"}:
        context = definitions["context"]
        del context["properties"]["attributed_actor_id"]
        context["required"].remove("attributed_actor_id")
        context["properties"]["attributed_actor_ids"] = array(identifier)
        context["required"].append("attributed_actor_ids")
    if ontology["version"] in {"0.3.0", "0.4.0"}:
        context = definitions["context"]
        context["properties"]["assertion_evidence"] = array({"$ref": "#/$defs/span"})
        context["required"].append("assertion_evidence")
    schema = obj(
        schema_version={"const": ontology["version"]},
        ontology_version={"const": ontology["version"]},
        document_id=text,
        family_id=text,
        reference_status={"const": "not_graph_gold"},
        provenance=obj(method=enum("provenance_methods"), producer=text, version=text, run_id=text),
        coverage=obj(
            processed_fields=array(text),
            unprocessed_fields=array(text),
            semantic_completeness={"const": "not_assessed"},
        ),
        **{key: array({"$ref": f"#/$defs/{key[:-1]}"}) for key in COLLECTIONS},
    )
    if ontology["version"] == "0.4.0":
        definitions.update(
            object=obj(
                id=identifier, object_type=enum("object_types"), mention_ids=array(identifier, 1)
            ),
            object_mention=obj(
                id=identifier,
                object_id=identifier,
                context_id=identifier,
                evidence={"$ref": "#/$defs/span"},
            ),
            object_link=obj(
                id=identifier,
                object_mention_id=identifier,
                event_id=identifier,
                relation={"const": "involves_object"},
                **contextual,
            ),
        )
        for key in OBJECT_COLLECTIONS:
            schema["properties"][key] = array({"$ref": f"#/$defs/{key[:-1]}"})
            schema["required"].append(key)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Judicial KG development extraction contract",
        **schema,
        "$defs": definitions,
    }


@dataclass(frozen=True)
class Contract:
    ontology: dict
    schema: dict
    validator: Draft202012Validator

    @classmethod
    def load(cls, root: Path, version: str = "0.1.0"):
        if version not in CONTRACT_FILES:
            raise ValueError("Unsupported extraction contract version")
        ontology_file, schema_file = CONTRACT_FILES[version]
        ontology = json.loads((root / ontology_file).read_text())
        schema = json.loads((root / schema_file).read_text())
        if ontology["version"] != version:
            raise ValueError("Ontology version differs from requested contract")
        if schema != build_schema(ontology):
            raise ValueError("Schema and ontology differ; regenerate the schema explicitly")
        Draft202012Validator.check_schema(schema)
        return cls(ontology, schema, Draft202012Validator(schema))

    def versions(self) -> dict:
        return {
            "ontology_version": self.ontology["version"],
            "ontology_sha256": canonical_hash(self.ontology),
            "schema_sha256": canonical_hash(self.schema),
        }


def candidate_record(
    doc: dict, provenance: dict, processed_fields: list[str], version: str = "0.1.0"
) -> dict:
    """An empty candidate envelope, never evidence that extraction found no facts."""
    return {
        "schema_version": version,
        "ontology_version": version,
        "document_id": doc["document_id"],
        "family_id": doc["family_id"],
        "reference_status": "not_graph_gold",
        "provenance": provenance.copy(),
        "coverage": {
            "processed_fields": processed_fields.copy(),
            "unprocessed_fields": [
                f["name"] for f in doc["fields"] if f["name"] not in processed_fields
            ],
            "semantic_completeness": "not_assessed",
        },
        **{key: [] for key in collections({"schema_version": version})},
    }
