"""Project candidate records into a reversible graph without merging assertions."""

from copy import deepcopy

from judicial_kg.contracts import COLLECTIONS, collections
from judicial_kg.sources.records import canonical_hash

GRAPH_VERSION = "0.1.0"
KINDS = dict(
    zip(COLLECTIONS, ("Actor", "Mention", "Context", "Role", "Event", "Statement"), strict=True)
)
KINDS.update(objects="Object", object_mentions="ObjectMention", object_links="ObjectLink")
KINDS.update(
    collectives="Collective",
    collective_mentions="CollectiveMention",
    collective_memberships="CollectiveMembership",
    collective_participations="CollectiveParticipation",
)
REFERENCES = {
    "collectives": {
        "context_id": ("in_context", False),
        "mention_ids": ("has_collective_mention", True),
    },
    "collective_mentions": {
        "collective_id": ("denotes_collective", False),
        "context_id": ("in_context", False),
    },
    "collective_memberships": {
        "collective_id": ("member_of", False),
        "actor_id": ("member_actor", False),
        "context_id": ("in_context", False),
    },
    "collective_participations": {
        "collective_id": ("collective_participant", False),
        "event_id": ("collective_of_event", False),
        "context_id": ("in_context", False),
    },
    "objects": {"mention_ids": ("has_object_mention", True)},
    "object_mentions": {
        "object_id": ("denotes_object", False),
        "context_id": ("in_context", False),
    },
    "object_links": {
        "object_mention_id": ("object_via", False),
        "event_id": ("object_of_event", False),
        "context_id": ("in_context", False),
    },
    "actors": {"mention_ids": ("has_mention", True)},
    "mentions": {"actor_id": ("denotes", False)},
    "contexts": {
        "attributed_actor_id": ("attributed_to", False),
        "parent_context_id": ("within_context", False),
    },
    "roles": {"actor_id": ("role_of", False), "context_id": ("in_context", False)},
    "events": {"context_id": ("in_context", False)},
    "statements": {"subject_id": ("about", False), "context_id": ("in_context", False)},
}


def references(record: dict, collection: str) -> dict:
    """Select reference cardinality from the explicit record contract, never guess."""
    version = record["schema_version"]
    if (
        version not in {"0.1.0", "0.2.0", "0.3.0", "0.4.0", "0.5.0"}
        or record["ontology_version"] != version
    ):
        raise ValueError("Unsupported or inconsistent record version")
    if collection == "contexts" and version in {"0.2.0", "0.3.0", "0.4.0", "0.5.0"}:
        return {
            "attributed_actor_ids": ("attributed_to", True),
            "parent_context_id": ("within_context", False),
        }
    return REFERENCES[collection]


def evidence_fields(record, collection):
    if collection in {"actors", "objects", "collectives"}:
        return {}
    if collection == "contexts" and record["schema_version"] in {"0.3.0", "0.4.0", "0.5.0"}:
        return {"evidence": "covers", "assertion_evidence": "assertion_source"}
    return {"evidence": "supported_by"}


def node_id(document_id: str, kind: str, local_id: str) -> str:
    # Hash a tuple rather than concatenating IDs with an ambiguous separator.
    return "n:" + canonical_hash([document_id, kind, local_id])


def empty_graph() -> dict:
    return {
        "graph_version": GRAPH_VERSION,
        "status": "unreviewed_candidate_graph",
        "identity_scope": "document_only",
        "reference_status": "not_graph_gold",
        "nodes": [],
        "edges": [],
    }


def project_records(records: list[dict]) -> dict:
    """Internal projection; callers must validate records against original sources."""
    graph = empty_graph()
    nodes, edges = {}, {}

    def add_node(node):
        if node["id"] in nodes:
            raise ValueError("Duplicate graph node identity")
        nodes[node["id"]] = node

    def edge(source, relation, target, index=0, **properties):
        value = {
            "source": source,
            "relation": relation,
            "target": target,
            "index": index,
            "properties": properties,
        }
        value["id"] = "e:" + canonical_hash(value)
        if value["id"] in edges:
            raise ValueError("Duplicate graph edge identity")
        edges[value["id"]] = value

    for record in records:
        doc_id = record["document_id"]
        document_node = node_id(doc_id, "Document", "")
        add_node(
            {
                "id": document_node,
                "kind": "Document",
                "document_id": doc_id,
                "record_pointer": "",
                "properties": {
                    k: deepcopy(v) for k, v in record.items() if k not in collections(record)
                },
            }
        )
        local = {
            node["id"]: node_id(doc_id, KINDS[key], node["id"])
            for key in collections(record)
            for node in record[key]
        }
        for key in collections(record):
            for index, original in enumerate(record[key]):
                identifier = local[original["id"]]
                properties = deepcopy(original)
                for field, (relation, many) in references(record, key).items():
                    refs = properties.pop(field)
                    refs = refs if many else [] if refs is None else [refs]
                    for position, ref in enumerate(refs):
                        edge(identifier, relation, local[ref], position)
                if key == "events":
                    for position, participant in enumerate(properties.pop("participants")):
                        edge(
                            identifier,
                            "participant",
                            local[participant["actor_id"]],
                            position,
                            role=participant["role"],
                        )
                for field, relation in evidence_fields(record, key).items():
                    evidence = properties.pop(field)
                    spans = (
                        [evidence]
                        if key in {"mentions", "object_mentions", "collective_mentions"}
                        else evidence
                    )
                    for position, span in enumerate(spans):
                        evidence_id = node_id(doc_id, "EvidenceSpan", canonical_hash(span))
                        if evidence_id not in nodes:
                            add_node(
                                {
                                    "id": evidence_id,
                                    "kind": "EvidenceSpan",
                                    "document_id": doc_id,
                                    "record_pointer": None,
                                    "properties": deepcopy(span),
                                }
                            )
                        edge(identifier, relation, evidence_id, position)
                add_node(
                    {
                        "id": identifier,
                        "kind": KINDS[key],
                        "document_id": doc_id,
                        "record_pointer": f"/{key}/{index}",
                        "properties": properties,
                    }
                )
                edge(document_node, "contains", identifier, index, collection=key)
    graph["nodes"] = sorted(nodes.values(), key=lambda n: n["id"])
    graph["edges"] = sorted(edges.values(), key=lambda e: e["id"])
    return graph


def restore_records(graph: dict) -> list[dict]:
    """Reconstruct exact records; reject dangling, extra or cross-document structure.

    This checks graph serialization, not the source or legal meaning of a record.
    A consumer must still run validate_batch with the original source manifest.
    """
    try:
        nodes = {n["id"]: n for n in graph["nodes"]}
        if len(nodes) != len(graph["nodes"]):
            raise ValueError("Duplicate graph nodes")
        outgoing = {identifier: [] for identifier in nodes}
        for edge in graph["edges"]:
            source, target = nodes[edge["source"]], nodes[edge["target"]]
            if source["document_id"] != target["document_id"]:
                raise ValueError("Cross-document graph edge")
            outgoing[edge["source"]].append(edge)

        def links(identifier, relation):
            return sorted(
                [e for e in outgoing[identifier] if e["relation"] == relation],
                key=lambda e: e["index"],
            )

        records = []
        for doc in sorted(nodes.values(), key=lambda n: n["document_id"]):
            if doc["kind"] != "Document":
                continue
            record = deepcopy(doc["properties"])
            record.update({k: [] for k in collections(record)})
            contained = links(doc["id"], "contains")
            for key in collections(record):
                for connection in (e for e in contained if e["properties"]["collection"] == key):
                    node = nodes[connection["target"]]
                    item = deepcopy(node["properties"])
                    for field, (relation, many) in references(record, key).items():
                        refs = [
                            nodes[e["target"]]["properties"]["id"]
                            for e in links(node["id"], relation)
                        ]
                        if not many and len(refs) > 1:
                            raise ValueError("Multiple values for a singular reference")
                        item[field] = refs if many else refs[0] if refs else None
                    if key == "events":
                        item["participants"] = [
                            {
                                "actor_id": nodes[e["target"]]["properties"]["id"],
                                "role": e["properties"]["role"],
                            }
                            for e in links(node["id"], "participant")
                        ]
                    for field, relation in evidence_fields(record, key).items():
                        spans = [
                            deepcopy(nodes[e["target"]]["properties"])
                            for e in links(node["id"], relation)
                        ]
                        if (
                            key in {"mentions", "object_mentions", "collective_mentions"}
                            and len(spans) != 1
                        ):
                            raise ValueError("Mention must have exactly one evidence span")
                        item[field] = (
                            spans[0]
                            if key in {"mentions", "object_mentions", "collective_mentions"}
                            else spans
                        )
                    record[key].append(item)
            records.append(record)
        # Reprojection catches omitted/unused nodes, unsupported edges, wrong types,
        # indices, IDs, pointers and graph metadata, not just dangling references.
        if canonical_hash(project_records(records)) != canonical_hash(graph):
            raise ValueError("Graph is not a canonical lossless candidate projection")
        return records
    except (KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValueError("Invalid candidate graph structure") from error
